#!/bin/sh
# patches/patch_nxnative.sh — apply patches/series onto core/nxnative.
#
# The series is an ordered stack: a later patch may depend on an earlier
# one's context (e.g. instrumentation on top of the split). So application
# walks in order, and reverts walk in reverse.
#
# Verification is by content sentinels, never by reverse dry-run: Apple's
# patch(1) answers its own "Unreversed patch detected! Ignore -R?" with yes
# under --batch, so a reverse dry-run exits 0 even on a pristine tree and
# proves nothing (found 2026-11-16 while stacking a third patch). Each patch
# file declares its sentinels in `# verify:` header lines:
#   # verify: <relpath>:<literal>  — grep -F must find <literal> in the file
#   # verify: <relpath>             — the file itself must exist and be non-empty
# A patch counts as applied only when every sentinel holds. Sentinels prove
# applied-ness, not applied-version: after a patch file's *content* changes,
# a revert with the old version (e.g. `git show HEAD:patches/<p>`)
# followed by a re-run converges — the script errors out loudly instead of
# blessing stale code (see the apply loop).
#
# Guarantees:
# - sequential check-then-apply: each patch dry-runs just before it applies,
#   so a dependent patch sees the tree its context was written against.
# - self-normalizing: any applied state that is not the verified marked
#   state is reverted (reverse order) and applied fresh — no silent
#   adoption, no resume-and-hope. A failure stops before later patches.
# - a marker is a *verified* claim: it is only written after every sentinel
#   of every patch holds on the just-patched tree, and an existing marker is
#   only trusted (same hash) after the same check passes.
# - reverses never run blind: Apple's patch(1) forward-applies on non-matching
#   content with exit 0 under --batch (proven 2026-11-16), so -R runs only
#   when the sentinels hold, and a revert that leaves them holding is a hard
#   stop (duplicate/partial application needs exact-string surgery, never
#   another -R).
# - --batch everywhere and exactly one stdin redirect (the patch file).
#   NEVER add a second stdin redirect (e.g. </dev/null after the file):
#   the last redirect wins, patch reads empty stdin, exits 0 doing nothing,
#   and only the post-apply sentinel check catches it (this exact bug
#   happened once, 2026-11-16).
# - -V none: no .orig/.rej litter on success paths.
# - quiet no-op when series is missing or empty.
# Usage: patch_nxnative.sh <core dir> [--force]
# --force additionally drops stray files at patch-owned new-file paths, then
# runs the same revert-if-applied + check-then-apply as a normal run.
set -eu

CORE=""; FORCE=0
for a in "$@"; do
  case "$a" in
    --force) FORCE=1 ;;
    -h|--help) echo "usage: patch_nxnative.sh <core dir> [--force]" >&2; exit 0 ;;
    *) [ -n "$CORE" ] && { echo "[ERROR] usage: patch_nxnative.sh <core dir> [--force]" >&2; exit 2; }; CORE="$a" ;;
  esac
done
[ -n "$CORE" ] || { echo "[ERROR] usage: patch_nxnative.sh <core dir> [--force]" >&2; exit 2; }
[ -d "$CORE" ] || { echo "[ERROR] core dir missing: $CORE" >&2; exit 1; }
command -v patch >/dev/null 2>&1 || { echo "[ERROR] patch(1) not found (install Xcode command line tools)." >&2; exit 1; }

HERE="$(cd "$(dirname "$0")" && pwd)"
SERIES="$HERE/series"
MARKER="$CORE/.swx-patches-applied"

hash_state() {   # hash of series + every listed patch's content
    if [ ! -f "$SERIES" ]; then echo "empty"; return; fi
    { cat "$SERIES"; for p in $(grep -v '^[[:space:]]*#' "$SERIES" | grep -v '^[[:space:]]*$'); do cat "$HERE/$p"; done; } 2>/dev/null | shasum -a 256 | cut -c1-16
}

PATCHES=""
if [ -f "$SERIES" ]; then
    # shellcheck disable=SC2016
    PATCHES="$(grep -v '^[[:space:]]*#' "$SERIES" | grep -v '^[[:space:]]*$' || true)"
fi
if [ -z "$PATCHES" ]; then
    echo "patches: nothing to apply (no series)."
    exit 0
fi

is_applied() {   # $1 = patch file name: every # verify: sentinel holds
    vf="$HERE/$1"
    tmp="${TMPDIR:-/tmp}/swx-verify-$$"
    grep '^# verify: ' "$vf" 2>/dev/null > "$tmp" || true
    [ -s "$tmp" ] || { rm -f "$tmp"; return 1; }   # no sentinels declared: never "applied"
    rc=0
    while IFS= read -r line; do
        spec="${line#"# verify: "}"
        case "$spec" in
            *:*)
                f="${spec%%:*}"; lit="${spec#*:}"
                grep -qF "$lit" "$CORE/$f" 2>/dev/null || rc=1
                ;;
            *)
                [ -s "$CORE/$spec" ] || rc=1
                ;;
        esac
    done < "$tmp"
    rm -f "$tmp"
    return $rc
}

stack_verified() {   # every patch's sentinels hold
    for p in $PATCHES; do
        is_applied "$p" || return 1
    done
    return 0
}

newfile_targets() {   # $1 = patch file name: relpaths the patch creates (--- /dev/null -> +++ b/<path>)
    grep -A1 '^--- /dev/null' "$HERE/$1" 2>/dev/null | grep '^+++ b/' | sed 's|^+++ b/||; s/[[:space:]].*$//'
}

drop_empty_newfiles() {   # remove 0-byte leftovers for add-file hunks so patch(1) can recreate them
    for p in $PATCHES; do
        newfile_targets "$p" | while IFS= read -r f; do
            [ -n "$f" ] || continue
            if [ -e "$CORE/$f" ] && [ ! -s "$CORE/$f" ]; then
                rm -f "$CORE/$f"
                echo "patches: removed empty new-file leftover $f ($p)"
            fi
        done
    done
}

drop_stale_newfiles() {   # --force only: remove add-file targets of unapplied patches (any size)
    # Forward patch(1) over an existing file truncates it to 0 bytes with
    # exit 0 (proven 2026-11-16: swx_keepalive.m), so a stray file at a
    # patch-owned path blocks re-creation. Only paths this series creates,
    # only when their patch is not applied (never broad deletion).
    for p in $PATCHES; do
        if ! is_applied "$p"; then
            newfile_targets "$p" | while IFS= read -r f; do
                [ -n "$f" ] || continue
                if [ -e "$CORE/$f" ]; then
                    rm -f "$CORE/$f"
                    echo "patches: removed stale new-file $f ($p)"
                fi
            done
        fi
    done
}

WANT="$(hash_state)"

# Exact append-only upgrades for known installed prefixes. The ordered stack
# currently ends with ratio (#10), worker (#11), queue policy (#12), alpha
# guard (#13), texture-pool correctness repair (#14), burst attribution
# (#15), burst host lifetime (#16), upload batch blit prototype (#17), save
# cleanup worker (#18), BOTW render scale ceiling (#19), and BOTW
# disable-DRS flag (#20).
# Historical markers are retained for append-only upgrades.
VFR_V2_PREFIX_COUNT=9
VFR_V2_PREFIX_HASH="f64de0f41d4008b6"
VFR_RATIO_PREFIX_COUNT=10
VFR_RATIO_PREFIX_HASH="08aa37472c9ef5e5"
VFR_RATIO_FIX="dm_vfr_ratio_load_fix.patch"
PRESENT_WORKER="dm_present_worker.patch"
PRESENT_WORKER_OLD="compat/dm_present_worker-v1.patch"
PRESENT_QUEUE_POLICY="dm_vfr_queue_policy.patch"
QUICK_MENU_ALPHA="dm_vfr_quick_menu_alpha.patch"
POOL_REPAIR="dm_gpu_texture_pool_fix.patch"
POOL_REPAIR_PREFIX_COUNT=13
POOL_REPAIR_PREFIX_HASH="ea9b59b9c90f9d50"
POOL_REPAIR_SCOPE_V1_HASH="053f4709d36ee757"
POOL_REPAIR_SCOPE_UPGRADE="compat/dm_gpu_texture_pool_fix-scope-v1.patch"
TEXTURE_BURST_ATTRIBUTION="dm_texture_burst_attribution.patch"
TEXTURE_BURST_PREFIX_COUNT=14
TEXTURE_BURST_PREFIX_HASH="f56148a5c5d18b4a"
TEXTURE_BURST_A2_UPGRADE="compat/dm_texture_burst_attribution-rec_a2-v1.patch"
TEXTURE_BURST_A2_V1_HASH="05e12967a955adcb"
TEXTURE_BURST_A2_V1_WANT="844febe76ff28fd9"
TEXTURE_BURST_HOST_LIFETIME="dm_texture_burst_attribution_host_lifetime.patch"
TEXTURE_BURST_HOST_PREFIX_COUNT=15
TEXTURE_BURST_HOST_PREFIX_HASH="e65ca40b937099e0"
UP_BLIT_REC="dm_up_blit_rec.patch"
UP_BLIT_PREFIX_COUNT=16
UP_BLIT_PREFIX_HASH="95af732262d69275"
SAVE_WORKER="swx_save_cleanup_worker.patch"
SAVE_WORKER_PREFIX_COUNT=17
SAVE_WORKER_PREFIX_HASH="2e8ec3cfb8de1dfb"
BOTW_RENDER_SCALE="swx_botw_render_scale.patch"
BOTW_RENDER_SCALE_PREFIX_COUNT=18
BOTW_RENDER_SCALE_PREFIX_HASH="e0ca96f9fb6d2422"
DISABLE_DRS="swx_botw_disable_drs.patch"
DISABLE_DRS_PREFIX_COUNT=19
DISABLE_DRS_PREFIX_HASH="75e74f5d4d5f73a6"
PRESENT_WORKER_PREFIX_COUNT=11
PRESENT_WORKER_PREFIX_HASH="d15dd3c0e4745a4d"
PRESENT_QUEUE_PREFIX_COUNT=12
PRESENT_QUEUE_POLICY_PRE_RATE_HASH="b188e4d88f1c537e"
PRESENT_QUEUE_POLICY_PRE_RATE="compat/dm_vfr_queue_policy-display-rate-v1.patch"
hash_series_prefix() {
    n="$1"
    { sed -n "1,${n}p" "$SERIES"
      for p in $(sed -n "1,${n}p" "$SERIES"); do cat "$HERE/$p"; done
    } | shasum -a 256 | cut -c1-16
}
verify_series_prefix() {
    n="$1"; i=0
    for p in $PATCHES; do
        i=$((i + 1)); [ "$i" -le "$n" ] || break
        is_applied "$p" || return 1
    done
    [ "$i" -eq "$((n + 1))" ]
}
apply_pool_repair() (
    [ "$(sed -n '14p' "$SERIES")" = "$POOL_REPAIR" ] || exit 1
    if ! is_applied "$POOL_REPAIR"; then
        patch --batch -V none -p1 --dry-run -d "$CORE" < "$HERE/$POOL_REPAIR" >/dev/null || exit 1
        patch --batch -V none -p1 -d "$CORE" < "$HERE/$POOL_REPAIR" >/dev/null || exit 1
        is_applied "$POOL_REPAIR" || exit 1
    fi
    if ! is_applied "$TEXTURE_BURST_ATTRIBUTION"; then
        patch --batch -V none -p1 --dry-run -d "$CORE" < "$HERE/$TEXTURE_BURST_ATTRIBUTION" >/dev/null || exit 1
        patch --batch -V none -p1 -d "$CORE" < "$HERE/$TEXTURE_BURST_ATTRIBUTION" >/dev/null || exit 1
        is_applied "$TEXTURE_BURST_ATTRIBUTION" || exit 1
    fi
    if ! is_applied "$TEXTURE_BURST_HOST_LIFETIME"; then
        patch --batch -V none -p1 --dry-run -d "$CORE" < "$HERE/$TEXTURE_BURST_HOST_LIFETIME" >/dev/null || exit 1
        patch --batch -V none -p1 -d "$CORE" < "$HERE/$TEXTURE_BURST_HOST_LIFETIME" >/dev/null || exit 1
        is_applied "$TEXTURE_BURST_HOST_LIFETIME" || exit 1
    fi
    if ! is_applied "$UP_BLIT_REC"; then
        patch --batch -V none -p1 --dry-run -d "$CORE" < "$HERE/$UP_BLIT_REC" >/dev/null || exit 1
        patch --batch -V none -p1 -d "$CORE" < "$HERE/$UP_BLIT_REC" >/dev/null || exit 1
        is_applied "$UP_BLIT_REC" || exit 1
    fi
)
append_texture_burst_attribution() (
    [ "$(hash_series_prefix "$TEXTURE_BURST_PREFIX_COUNT")" = "$TEXTURE_BURST_PREFIX_HASH" ] || exit 1
    [ "$(cat "$MARKER")" = "$TEXTURE_BURST_PREFIX_HASH" ] || exit 1
    verify_series_prefix "$TEXTURE_BURST_PREFIX_COUNT" || exit 1
    if ! is_applied "$TEXTURE_BURST_ATTRIBUTION"; then
        patch --batch -V none -p1 --dry-run -d "$CORE" < "$HERE/$TEXTURE_BURST_ATTRIBUTION" >/dev/null || exit 1
        patch --batch -V none -p1 -d "$CORE" < "$HERE/$TEXTURE_BURST_ATTRIBUTION" >/dev/null || exit 1
        is_applied "$TEXTURE_BURST_ATTRIBUTION" || exit 1
    fi
    if ! is_applied "$TEXTURE_BURST_HOST_LIFETIME"; then
        patch --batch -V none -p1 --dry-run -d "$CORE" < "$HERE/$TEXTURE_BURST_HOST_LIFETIME" >/dev/null || exit 1
        patch --batch -V none -p1 -d "$CORE" < "$HERE/$TEXTURE_BURST_HOST_LIFETIME" >/dev/null || exit 1
        is_applied "$TEXTURE_BURST_HOST_LIFETIME" || exit 1
    fi
    if ! is_applied "$UP_BLIT_REC"; then
        patch --batch -V none -p1 --dry-run -d "$CORE" < "$HERE/$UP_BLIT_REC" >/dev/null || exit 1
        patch --batch -V none -p1 -d "$CORE" < "$HERE/$UP_BLIT_REC" >/dev/null || exit 1
        is_applied "$UP_BLIT_REC" || exit 1
    fi
    stack_verified || exit 1
    echo "$WANT" > "$MARKER"
)
append_texture_burst_host_lifetime() (
    [ "$(hash_series_prefix "$TEXTURE_BURST_HOST_PREFIX_COUNT")" = "$TEXTURE_BURST_HOST_PREFIX_HASH" ] || exit 1
    [ "$(cat "$MARKER")" = "$TEXTURE_BURST_HOST_PREFIX_HASH" ] || exit 1
    verify_series_prefix "$TEXTURE_BURST_HOST_PREFIX_COUNT" || exit 1
    if ! is_applied "$TEXTURE_BURST_HOST_LIFETIME"; then
        patch --batch -V none -p1 --dry-run -d "$CORE" < "$HERE/$TEXTURE_BURST_HOST_LIFETIME" >/dev/null || exit 1
        patch --batch -V none -p1 -d "$CORE" < "$HERE/$TEXTURE_BURST_HOST_LIFETIME" >/dev/null || exit 1
        is_applied "$TEXTURE_BURST_HOST_LIFETIME" || exit 1
    fi
    if ! is_applied "$UP_BLIT_REC"; then
        patch --batch -V none -p1 --dry-run -d "$CORE" < "$HERE/$UP_BLIT_REC" >/dev/null || exit 1
        patch --batch -V none -p1 -d "$CORE" < "$HERE/$UP_BLIT_REC" >/dev/null || exit 1
        is_applied "$UP_BLIT_REC" || exit 1
    fi
    stack_verified || exit 1
    echo "$WANT" > "$MARKER"
)
append_up_blit_rec() (
    [ "$(hash_series_prefix "$UP_BLIT_PREFIX_COUNT")" = "$UP_BLIT_PREFIX_HASH" ] || exit 1
    [ "$(cat "$MARKER")" = "$UP_BLIT_PREFIX_HASH" ] || exit 1
    verify_series_prefix "$UP_BLIT_PREFIX_COUNT" || exit 1
    if ! is_applied "$UP_BLIT_REC"; then
        patch --batch -V none -p1 --dry-run -d "$CORE" < "$HERE/$UP_BLIT_REC" >/dev/null || exit 1
        patch --batch -V none -p1 -d "$CORE" < "$HERE/$UP_BLIT_REC" >/dev/null || exit 1
        is_applied "$UP_BLIT_REC" || exit 1
    fi
    stack_verified || exit 1
    echo "$WANT" > "$MARKER"
)
append_save_worker() (
    [ "$(hash_series_prefix "$SAVE_WORKER_PREFIX_COUNT")" = "$SAVE_WORKER_PREFIX_HASH" ] || exit 1
    [ "$(cat "$MARKER")" = "$SAVE_WORKER_PREFIX_HASH" ] || exit 1
    verify_series_prefix "$SAVE_WORKER_PREFIX_COUNT" || exit 1
    if ! is_applied "$SAVE_WORKER"; then
        patch --batch -V none -p1 --dry-run -d "$CORE" < "$HERE/$SAVE_WORKER" >/dev/null || exit 1
        patch --batch -V none -p1 -d "$CORE" < "$HERE/$SAVE_WORKER" >/dev/null || exit 1
        is_applied "$SAVE_WORKER" || exit 1
    fi
    stack_verified || exit 1
    echo "$WANT" > "$MARKER"
)
append_botw_render_scale() (
    [ "$(hash_series_prefix "$BOTW_RENDER_SCALE_PREFIX_COUNT")" = "$BOTW_RENDER_SCALE_PREFIX_HASH" ] || exit 1
    [ "$(cat "$MARKER")" = "$BOTW_RENDER_SCALE_PREFIX_HASH" ] || exit 1
    verify_series_prefix "$BOTW_RENDER_SCALE_PREFIX_COUNT" || exit 1
    if ! is_applied "$BOTW_RENDER_SCALE"; then
        patch --batch -V none -p1 --dry-run -d "$CORE" < "$HERE/$BOTW_RENDER_SCALE" >/dev/null || exit 1
        patch --batch -V none -p1 -d "$CORE" < "$HERE/$BOTW_RENDER_SCALE" >/dev/null || exit 1
        is_applied "$BOTW_RENDER_SCALE" || exit 1
    fi
    stack_verified || exit 1
    echo "$WANT" > "$MARKER"
)
append_disable_drs() (
    [ "$(hash_series_prefix "$DISABLE_DRS_PREFIX_COUNT")" = "$DISABLE_DRS_PREFIX_HASH" ] || exit 1
    [ "$(cat "$MARKER")" = "$DISABLE_DRS_PREFIX_HASH" ] || exit 1
    verify_series_prefix "$DISABLE_DRS_PREFIX_COUNT" || exit 1
    if ! is_applied "$DISABLE_DRS"; then
        patch --batch -V none -p1 --dry-run -d "$CORE" < "$HERE/$DISABLE_DRS" >/dev/null || exit 1
        patch --batch -V none -p1 -d "$CORE" < "$HERE/$DISABLE_DRS" >/dev/null || exit 1
        is_applied "$DISABLE_DRS" || exit 1
    fi
    stack_verified || exit 1
    echo "$WANT" > "$MARKER"
)
apply_texture_burst_a2_upgrade() (
    [ "$(hash_series_prefix "$TEXTURE_BURST_HOST_PREFIX_COUNT")" = "$TEXTURE_BURST_HOST_PREFIX_HASH" ] || exit 1
    verify_series_prefix "$TEXTURE_BURST_PREFIX_COUNT" || exit 1
    if ! is_applied "$TEXTURE_BURST_ATTRIBUTION"; then
        grep -Fq 'rec_own_put(b, RO_COMMIT_REAL, 2); r->a[0] = 0; r->a[1] = rec_obj(cb);' "$CORE/dm/gfx/nvn/nvn_mtl.m" || { echo "[ERROR] snapshot commit snippet diverges; refusing punctual a[2] repair." >&2; exit 1; }
        if ! patch --batch -V none -p1 --dry-run -d "$CORE" < "$HERE/$TEXTURE_BURST_A2_UPGRADE" >/dev/null; then
            echo "[ERROR] texture-burst snapshot a[2] repair preflight failed; core left untouched." >&2
            exit 1
        fi
        patch --batch -V none -p1 -d "$CORE" < "$HERE/$TEXTURE_BURST_A2_UPGRADE" >/dev/null || exit 1
        is_applied "$TEXTURE_BURST_ATTRIBUTION" || exit 1
    fi
    if ! is_applied "$TEXTURE_BURST_HOST_LIFETIME"; then
        patch --batch -V none -p1 --dry-run -d "$CORE" < "$HERE/$TEXTURE_BURST_HOST_LIFETIME" >/dev/null || exit 1
        patch --batch -V none -p1 -d "$CORE" < "$HERE/$TEXTURE_BURST_HOST_LIFETIME" >/dev/null || exit 1
        is_applied "$TEXTURE_BURST_HOST_LIFETIME" || exit 1
    fi
    stack_verified || exit 1
    echo "$WANT" > "$MARKER"
)
append_texture_pool_repair() (
    [ "$(hash_series_prefix "$POOL_REPAIR_PREFIX_COUNT")" = "$POOL_REPAIR_PREFIX_HASH" ] || exit 1
    [ "$(cat "$MARKER")" = "$POOL_REPAIR_PREFIX_HASH" ] || exit 1
    verify_series_prefix "$POOL_REPAIR_PREFIX_COUNT" || exit 1
    apply_pool_repair || exit 1
    stack_verified || exit 1
    echo "$WANT" > "$MARKER"
)
apply_queue_and_alpha() (
    [ "$(sed -n '12p' "$SERIES")" = "$PRESENT_QUEUE_POLICY" ] || exit 1
    [ "$(sed -n '13p' "$SERIES")" = "$QUICK_MENU_ALPHA" ] || exit 1
    if ! is_applied "$PRESENT_QUEUE_POLICY"; then
        patch --batch -V none -p1 --dry-run -d "$CORE" < "$HERE/$PRESENT_QUEUE_POLICY" >/dev/null || exit 1
        patch --batch -V none -p1 -d "$CORE" < "$HERE/$PRESENT_QUEUE_POLICY" >/dev/null || exit 1
        is_applied "$PRESENT_QUEUE_POLICY" || exit 1
    fi
    is_applied "$PRESENT_WORKER" || exit 1
    is_applied "$PRESENT_QUEUE_POLICY" || exit 1
    if ! is_applied "$QUICK_MENU_ALPHA"; then
        patch --batch -V none -p1 --dry-run -d "$CORE" < "$HERE/$QUICK_MENU_ALPHA" >/dev/null || exit 1
        patch --batch -V none -p1 -d "$CORE" < "$HERE/$QUICK_MENU_ALPHA" >/dev/null || exit 1
        is_applied "$QUICK_MENU_ALPHA" || exit 1
    fi
)
append_vfr_ratio_worker_queue_alpha() (
    [ "$(hash_series_prefix "$VFR_V2_PREFIX_COUNT")" = "$VFR_V2_PREFIX_HASH" ] || exit 1
    [ "$(hash_series_prefix "$VFR_RATIO_PREFIX_COUNT")" = "$VFR_RATIO_PREFIX_HASH" ] || exit 1
    [ "$(sed -n '11p' "$SERIES")" = "$PRESENT_WORKER" ] || exit 1
    [ "$(sed -n '12p' "$SERIES")" = "$PRESENT_QUEUE_POLICY" ] || exit 1
    [ "$(sed -n '13p' "$SERIES")" = "$QUICK_MENU_ALPHA" ] || exit 1
    if [ "$(cat "$MARKER")" = "$VFR_V2_PREFIX_HASH" ]; then
        verify_series_prefix "$VFR_V2_PREFIX_COUNT" || exit 1
        if ! is_applied "$VFR_RATIO_FIX"; then
            patch --batch -V none -p1 --dry-run -d "$CORE" < "$HERE/$VFR_RATIO_FIX" >/dev/null || exit 1
            patch --batch -V none -p1 -d "$CORE" < "$HERE/$VFR_RATIO_FIX" >/dev/null || exit 1
            is_applied "$VFR_RATIO_FIX" || exit 1
        fi
    elif [ "$(cat "$MARKER")" = "$VFR_RATIO_PREFIX_HASH" ]; then
        verify_series_prefix "$VFR_RATIO_PREFIX_COUNT" || exit 1
    else exit 1
    fi
    if ! is_applied "$PRESENT_WORKER"; then
        patch --batch -V none -p1 --dry-run -d "$CORE" < "$HERE/$PRESENT_WORKER" >/dev/null || exit 1
        patch --batch -V none -p1 -d "$CORE" < "$HERE/$PRESENT_WORKER" >/dev/null || exit 1
        is_applied "$PRESENT_WORKER" || exit 1
    fi
    verify_series_prefix "$PRESENT_WORKER_PREFIX_COUNT" || exit 1
    apply_queue_and_alpha
    apply_pool_repair
    stack_verified || exit 1
    echo "$WANT" > "$MARKER"
)
append_vfr_worker_to_full_stack() (
    [ "$(hash_series_prefix "$PRESENT_WORKER_PREFIX_COUNT")" = "$PRESENT_WORKER_PREFIX_HASH" ] || exit 1
    [ "$(cat "$MARKER")" = "$PRESENT_WORKER_PREFIX_HASH" ] || exit 1
    verify_series_prefix "$PRESENT_WORKER_PREFIX_COUNT" || exit 1
    apply_queue_and_alpha
    apply_pool_repair
    stack_verified || exit 1
    echo "$WANT" > "$MARKER"
)
upgrade_vfr_queue_policy_display_rate() (
    [ "$(hash_series_prefix "$PRESENT_WORKER_PREFIX_COUNT")" = "$PRESENT_WORKER_PREFIX_HASH" ] || exit 1
    [ "$(sed -n '12p' "$SERIES")" = "$PRESENT_QUEUE_POLICY" ] || exit 1
    [ "$(sed -n '13p' "$SERIES")" = "$QUICK_MENU_ALPHA" ] || exit 1
    [ "$(cat "$MARKER")" = "$PRESENT_QUEUE_POLICY_PRE_RATE_HASH" ] || exit 1
    verify_series_prefix "$PRESENT_WORKER_PREFIX_COUNT" || exit 1
    grep -Fq 'int rate = swx_link_target(), link_rate = mtl_display_hz();' "$CORE/dm/gfx/nvn/nvn_mtl.m" || exit 1
    # Refuse migration if the installed Etapa 2 credit path is the old one.
    grep -Fq 'Signal-only: always 0' "$CORE/dm/gfx/nvn/nvn_mtl.m" || { echo "[ERROR] upgrade needs the Etapa 2 credit fix in core first (restore a clean core at the current series and run the normalizer, then re-run the upgrade)." >&2; exit 1; }
    grep -Fq 'Punctual-recovery poll, once per present' "$CORE/dm/gfx/nvn/hle_nvn.c" || { echo "[ERROR] upgrade needs the Etapa 2 credit fix in core first (restore a clean core at the current series and run the normalizer, then re-run the upgrade)." >&2; exit 1; }
    patch --batch -V none -p1 --dry-run -d "$CORE" < "$HERE/$PRESENT_QUEUE_POLICY_PRE_RATE" >/dev/null || exit 1
    patch --batch -V none -p1 -d "$CORE" < "$HERE/$PRESENT_QUEUE_POLICY_PRE_RATE" >/dev/null || exit 1
    grep -Fq 'int rate = swx_link_target(), link_rate = atomic_load(&g_swx_link_hz);' "$CORE/dm/gfx/nvn/nvn_mtl.m" || exit 1
    apply_queue_and_alpha
    apply_pool_repair
    stack_verified || exit 1
    echo "$WANT" > "$MARKER"
)
hash_present_worker_state() {
    old_patch="$1"
    { sed -n "1,${PRESENT_WORKER_PREFIX_COUNT}p" "$SERIES"
      for p in $(sed -n "1,${PRESENT_WORKER_PREFIX_COUNT}p" "$SERIES"); do
          if [ "$p" = "$PRESENT_WORKER" ]; then cat "$HERE/$old_patch"; else cat "$HERE/$p"; fi
      done
    } | shasum -a 256 | cut -c1-16
}
apply_present_worker_migration() (
    CORE="$1"; old_patch="$2"
    patch --batch -V none -p1 -R --dry-run -d "$CORE" < "$HERE/$old_patch" >/dev/null || exit 1
    patch --batch -V none -p1 -R -d "$CORE" < "$HERE/$old_patch" >/dev/null || exit 1
    ! is_applied "$old_patch" || exit 1
    patch --batch -V none -p1 --dry-run -d "$CORE" < "$HERE/$PRESENT_WORKER" >/dev/null || exit 1
    patch --batch -V none -p1 -d "$CORE" < "$HERE/$PRESENT_WORKER" >/dev/null || exit 1
    is_applied "$PRESENT_WORKER" || exit 1
    apply_queue_and_alpha
    apply_pool_repair
)
migrate_present_worker() (
    old_patch="$1"
    stage="$(mktemp -d "${TMPDIR:-/tmp}/swx-present-worker.XXXXXX")"
    trap 'rm -rf "$stage"' EXIT
    sed -n -e 's|^--- a/||p' -e 's|^+++ b/||p' "$HERE/$old_patch" "$HERE/$PRESENT_WORKER" "$HERE/$PRESENT_QUEUE_POLICY" "$HERE/$QUICK_MENU_ALPHA" "$HERE/$POOL_REPAIR" | sort -u |
        while IFS= read -r f; do
            if [ -f "$CORE/$f" ]; then mkdir -p "$stage/$(dirname "$f")"; cp -p "$CORE/$f" "$stage/$f"; fi
        done
    apply_present_worker_migration "$stage" "$old_patch"
)
OLD_WORKER_PATCH=""
if [ -f "$MARKER" ] && [ "$(cat "$MARKER")" = "$(hash_present_worker_state "$PRESENT_WORKER_OLD")" ] && is_applied "$PRESENT_WORKER_OLD"; then
    OLD_WORKER_PATCH="$PRESENT_WORKER_OLD"
fi
if [ "$FORCE" -eq 0 ] && [ -n "$OLD_WORKER_PATCH" ] &&
   [ "$(hash_series_prefix "$VFR_RATIO_PREFIX_COUNT")" = "$VFR_RATIO_PREFIX_HASH" ] && verify_series_prefix "$VFR_RATIO_PREFIX_COUNT"; then
    if ! migrate_present_worker "$OLD_WORKER_PATCH" || ! apply_present_worker_migration "$CORE" "$OLD_WORKER_PATCH" || ! stack_verified; then
        echo "[ERROR] present-worker patch upgrade failed preflight/apply; core left unchanged where possible." >&2
        exit 1
    fi
    echo "$WANT" > "$MARKER"
    echo "patches: upgraded present worker ($WANT)."
    exit 0
fi
if [ "$FORCE" -eq 0 ] && [ -f "$MARKER" ] &&
   { [ "$(cat "$MARKER")" = "$VFR_V2_PREFIX_HASH" ] || [ "$(cat "$MARKER")" = "$VFR_RATIO_PREFIX_HASH" ]; }; then
    if ! append_vfr_ratio_worker_queue_alpha; then
        echo "[ERROR] append-only VFR ratio/worker/queue/alpha and texture-pool update failed preflight/apply; core left otherwise untouched." >&2
        exit 1
    fi
    echo "patches: appended VFR ratio, worker, queue policy, alpha guard, and texture-pool repair ($WANT)."
    exit 0
fi
if [ "$FORCE" -eq 0 ] && [ -f "$MARKER" ] && [ "$(cat "$MARKER")" = "$PRESENT_WORKER_PREFIX_HASH" ]; then
    if ! append_vfr_worker_to_full_stack; then
        echo "[ERROR] append-only VFR queue/alpha and texture-pool update failed preflight/apply; core left otherwise untouched." >&2
        exit 1
    fi
    echo "patches: appended VFR queue policy, alpha guard, and texture-pool repair ($WANT)."
    exit 0
fi
if [ "$FORCE" -eq 0 ] && [ -f "$MARKER" ] && [ "$(cat "$MARKER")" = "$PRESENT_QUEUE_POLICY_PRE_RATE_HASH" ]; then
    if ! upgrade_vfr_queue_policy_display_rate; then
        echo "[ERROR] cached display-rate correction, alpha guard, and texture-pool upgrade failed preflight/apply; core left unchanged where possible." >&2
        exit 1
    fi
    echo "patches: upgraded VFR queue policy display rate, alpha guard, and texture-pool repair ($WANT)."
    exit 0
fi
if [ "$FORCE" -eq 0 ] && [ -f "$MARKER" ] && [ "$(cat "$MARKER")" = "$(hash_series_prefix "$PRESENT_QUEUE_PREFIX_COUNT")" ]; then
    if ! verify_series_prefix "$PRESENT_QUEUE_PREFIX_COUNT"; then
        echo "[ERROR] installed queue-policy prefix does not verify; refusing alpha-guard upgrade." >&2; exit 1
    fi
    if ! is_applied "$QUICK_MENU_ALPHA"; then
        patch --batch -V none -p1 --dry-run -d "$CORE" < "$HERE/$QUICK_MENU_ALPHA" >/dev/null || { echo "[ERROR] alpha-guard preflight failed; core left untouched." >&2; exit 1; }
        patch --batch -V none -p1 -d "$CORE" < "$HERE/$QUICK_MENU_ALPHA" >/dev/null || exit 1
    fi
    if ! apply_pool_repair; then echo "[ERROR] texture-pool repair preflight failed; core left untouched." >&2; exit 1; fi
    stack_verified || { echo "[ERROR] alpha-guard stack verification failed." >&2; exit 1; }
    echo "$WANT" > "$MARKER"
    echo "patches: appended VFR quick-menu alpha guard and texture-pool repair ($WANT)."
    exit 0
fi
if [ "$FORCE" -eq 0 ] && [ -f "$MARKER" ] && [ "$(cat "$MARKER")" = "$POOL_REPAIR_SCOPE_V1_HASH" ]; then
    verify_series_prefix "$POOL_REPAIR_PREFIX_COUNT" || exit 1
    if ! is_applied "$POOL_REPAIR"; then
        if ! patch --batch -F 0 -V none -p1 --dry-run -d "$CORE" < "$HERE/$POOL_REPAIR_SCOPE_UPGRADE" >/dev/null; then
            echo "[ERROR] texture-pool scope repair preflight failed; core left untouched." >&2
            exit 1
        fi
        patch --batch -F 0 -V none -p1 -d "$CORE" < "$HERE/$POOL_REPAIR_SCOPE_UPGRADE" >/dev/null || exit 1
    fi
    if ! is_applied "$TEXTURE_BURST_ATTRIBUTION"; then
        patch --batch -V none -p1 --dry-run -d "$CORE" < "$HERE/$TEXTURE_BURST_ATTRIBUTION" >/dev/null || exit 1
        patch --batch -V none -p1 -d "$CORE" < "$HERE/$TEXTURE_BURST_ATTRIBUTION" >/dev/null || exit 1
    fi
    if ! is_applied "$TEXTURE_BURST_HOST_LIFETIME"; then
        patch --batch -V none -p1 --dry-run -d "$CORE" < "$HERE/$TEXTURE_BURST_HOST_LIFETIME" >/dev/null || exit 1
        patch --batch -V none -p1 -d "$CORE" < "$HERE/$TEXTURE_BURST_HOST_LIFETIME" >/dev/null || exit 1
    fi
    stack_verified || exit 1
    echo "$WANT" > "$MARKER"
    echo "patches: corrected texture-pool allocator scope ($WANT)."
    exit 0
fi
if [ "$FORCE" -eq 0 ] && [ -f "$MARKER" ] && { [ "$(cat "$MARKER")" = "$TEXTURE_BURST_A2_V1_HASH" ] || [ "$(cat "$MARKER")" = "$TEXTURE_BURST_A2_V1_WANT" ]; }; then
    if ! apply_texture_burst_a2_upgrade; then
        echo "[ERROR] texture-burst snapshot a[2] repair failed preflight/apply; core left unchanged where possible." >&2
        exit 1
    fi
    echo "patches: repaired texture-burst snapshot commit tag ($WANT)."
    exit 0
fi
if [ "$FORCE" -eq 0 ] && [ -f "$MARKER" ] && [ "$(cat "$MARKER")" = "$DISABLE_DRS_PREFIX_HASH" ]; then
    if ! append_disable_drs; then
        echo "[ERROR] append-only BOTW disable-DRS flag failed preflight/apply; core left unchanged where possible." >&2
        exit 1
    fi
    echo "patches: appended BOTW disable-DRS flag ($WANT)."
    exit 0
fi
if [ "$FORCE" -eq 0 ] && [ -f "$MARKER" ] && [ "$(cat "$MARKER")" = "$BOTW_RENDER_SCALE_PREFIX_HASH" ]; then
    if ! append_botw_render_scale; then
        echo "[ERROR] append-only BOTW render scale ceiling failed preflight/apply; core left unchanged where possible." >&2
        exit 1
    fi
    echo "patches: appended BOTW render scale ceiling ($WANT)."
    exit 0
fi
if [ "$FORCE" -eq 0 ] && [ -f "$MARKER" ] && [ "$(cat "$MARKER")" = "$SAVE_WORKER_PREFIX_HASH" ]; then
    if ! append_save_worker; then
        echo "[ERROR] append-only save cleanup worker failed preflight/apply; core left unchanged where possible." >&2
        exit 1
    fi
    echo "patches: appended save cleanup worker ($WANT)."
    exit 0
fi
if [ "$FORCE" -eq 0 ] && [ -f "$MARKER" ] && [ "$(cat "$MARKER")" = "$UP_BLIT_PREFIX_HASH" ]; then
    if ! append_up_blit_rec; then
        echo "[ERROR] append-only upload batch blit prototype failed preflight/apply; core left unchanged where possible." >&2
        exit 1
    fi
    echo "patches: appended upload batch blit prototype ($WANT)."
    exit 0
fi
if [ "$FORCE" -eq 0 ] && [ -f "$MARKER" ] && [ "$(cat "$MARKER")" = "$TEXTURE_BURST_HOST_PREFIX_HASH" ]; then
    if ! append_texture_burst_host_lifetime; then
        echo "[ERROR] append-only texture burst host lifetime repair failed preflight/apply; core left unchanged where possible." >&2
        exit 1
    fi
    echo "patches: repaired texture burst host lifetime handoff ($WANT)."
    exit 0
fi
if [ "$FORCE" -eq 0 ] && [ -f "$MARKER" ] && [ "$(cat "$MARKER")" = "$TEXTURE_BURST_PREFIX_HASH" ]; then
    if ! append_texture_burst_attribution; then
        echo "[ERROR] append-only texture burst attribution failed preflight/apply; core left unchanged where possible." >&2
        exit 1
    fi
    echo "patches: appended texture burst attribution ($WANT)."
    exit 0
fi
if [ "$FORCE" -eq 0 ] && [ -f "$MARKER" ] && [ "$(cat "$MARKER")" = "$POOL_REPAIR_PREFIX_HASH" ]; then
    if ! append_texture_pool_repair; then
        echo "[ERROR] append-only texture-pool correctness update failed preflight/apply; core left unchanged where possible." >&2
        exit 1
    fi
    echo "patches: appended texture-pool correctness repair ($WANT)."
    exit 0
fi
if [ "$FORCE" -eq 0 ] && [ -f "$MARKER" ] && [ "$(cat "$MARKER")" = "$WANT" ]; then
    if stack_verified; then
        echo "patches: already applied and verified ($WANT)."
        exit 0
    fi
    echo "warning: marker matches but the tree does not verify (manual edits? partial apply?). Re-checking from scratch; use --force to normalize first." >&2
    rm -f "$MARKER"
fi

VFR_CURRENT="dm_vfr_framepace.patch"
VFR_OLD="compat/dm_vfr_framepace-v1.patch"

migrate_vfr() (
    # Preflight both diffs on copies of the patch's files before touching
    # the installed stack. Later patches may remain applied in this upgrade.
    stage="$(mktemp -d "${TMPDIR:-/tmp}/swx-vfr.XXXXXX")"
    trap 'rm -rf "$stage"' EXIT
    sed -n -e 's|^--- a/||p' -e 's|^+++ b/||p' "$HERE/$VFR_OLD" "$HERE/$VFR_CURRENT" |
        sort -u | while IFS= read -r f; do
            if [ -f "$CORE/$f" ]; then
                mkdir -p "$stage/$(dirname "$f")"
                cp -p "$CORE/$f" "$stage/$f"
            fi
        done
    apply_vfr_migration "$stage"
)

apply_vfr_migration() (
    CORE="$1"
    patch --batch -V none -p1 -R --dry-run -d "$CORE" < "$HERE/$VFR_OLD" >/dev/null || exit 1
    patch --batch -V none -p1 -R -d "$CORE" < "$HERE/$VFR_OLD" >/dev/null || exit 1
    ! is_applied "$VFR_OLD" || exit 1
    newfile_targets "$VFR_OLD" | while IFS= read -r f; do
        if [ -e "$CORE/$f" ] && [ ! -s "$CORE/$f" ]; then rm -f "$CORE/$f"; fi
    done
    patch --batch -V none -p1 --dry-run -d "$CORE" < "$HERE/$VFR_CURRENT" >/dev/null || exit 1
    patch --batch -V none -p1 -d "$CORE" < "$HERE/$VFR_CURRENT" >/dev/null || exit 1
    is_applied "$VFR_CURRENT"
)

if [ "$FORCE" -eq 0 ] && ! is_applied "$VFR_CURRENT" && is_applied "$VFR_OLD"; then
    complete=1
    for p in $PATCHES; do
        if [ "$p" != "$VFR_CURRENT" ] && ! is_applied "$p"; then complete=0; fi
    done
    if [ "$complete" -eq 1 ]; then
        if ! migrate_vfr; then
            echo "[ERROR] cannot upgrade the old VFR patch over this core; the stack was left as-is." >&2
            exit 1
        fi
        rm -f "$MARKER"
        if ! apply_vfr_migration "$CORE" || ! stack_verified; then
            echo "[ERROR] VFR upgrade did not verify." >&2
            exit 1
        fi
        echo "$WANT" > "$MARKER"
        echo "patches: upgraded VFR ($WANT)."
        exit 0
    fi
fi

# 0. normalize first when the tree is not the verified marked state:
# revert whatever the sentinels show as applied, in reverse order (a later
# patch's context depends on the earlier ones, so forward-order revert
# fails), then drop any stale marker. --force asks for this explicitly;
# otherwise it happens automatically whenever sentinels show applied state
# outside the fast path above. Reverts never run on sentinels-absent
# patches (see the -R warning above); a duplicate/partial application that
# a single revert does not clear stops loudly instead of being reverted
# again and again.
any_applied() {
    for p in $PATCHES; do
        is_applied "$p" && return 0
        if [ "$p" = "$VFR_CURRENT" ] && is_applied "$VFR_OLD"; then return 0; fi
    done
    return 1
}
if { [ "$FORCE" -eq 1 ] || any_applied; } then
    if [ "$FORCE" -eq 1 ]; then
        echo "patches: normalizing (forced clean slate)"
    else
        echo "patches: tree shows applied state outside the marked stack; normalizing"
    fi
    for p in $(echo "$PATCHES" | tac 2>/dev/null || echo "$PATCHES" | tail -r); do
        # NEVER reverse unless the sentinels hold: Apple's patch(1) answers
        # its own "assume -R?" with yes under --batch, so -R on non-matching
        # content forward-applies with exit 0 (proven 2026-11-16: tripled
        # split hunks, phantom singles). A revert that does not clear the
        # sentinels is a hard stop, never a warning to run past.
        if [ "$p" = "$VFR_CURRENT" ] && ! is_applied "$p" && is_applied "$VFR_OLD"; then
            p="$VFR_OLD"
        fi
        if is_applied "$p"; then
            patch --batch -V none -p1 -R -d "$CORE" < "$HERE/$p" >/dev/null 2>&1 || true
            if is_applied "$p"; then
                echo "[ERROR] revert did not clear sentinels (duplicate or partial application?): $p" >&2
                echo "        normalize manually with exact-string surgery (see docs/nxnative_patches.md), then re-run." >&2
                exit 1
            fi
            echo "patches: reverted $p"
        fi
    done
    # Apple's patch(1) reverse of an add-file hunk truncates instead of
    # deleting, leaving a 0-byte file that blocks re-creation (found
    # 2026-11-16: swx_keepalive.m). Drop those empties; only empty
    # new-file targets, never broad deletion.
    drop_empty_newfiles
    # A stray full file at a patch-owned new-file path truncates to 0 on
    # forward apply (exit 0), so --force drops those too; a plain run only
    # drops empties and fails loudly otherwise (never delete real content
    # unasked).
    if [ "$FORCE" -eq 1 ]; then drop_stale_newfiles; fi
    rm -f "$MARKER"
fi

# A 0-byte new-file leftover also blocks a plain (non-force) re-run's
# dry-run/apply, so sweep them before the check-then-apply loop too.
drop_empty_newfiles

# 1-2. check-then-apply in series order on a clean tree: each patch dry-runs
# just before it applies, so a dependent patch sees the tree its context
# was written against. A failure stops before later patches.
for p in $PATCHES; do
    [ -f "$HERE/$p" ] || { echo "[ERROR] patch listed in series but missing: $p" >&2; exit 1; }
    if ! patch --batch -V none -p1 --dry-run -d "$CORE" < "$HERE/$p" >/dev/null 2>&1; then
        if is_applied "$p"; then
            echo "[ERROR] patch content changed but the tree still holds the old application: $p" >&2
            echo "        revert it with the old version first, e.g." >&2
            echo "          git show HEAD:patches/$p > /tmp/old.patch && patch -p1 -R -d <core> < /tmp/old.patch" >&2
            echo "        then re-run $0." >&2
        else
            echo "[ERROR] patch does not apply cleanly (rebase it): $p" >&2
            echo "        resolve inside core/nxnative, then refresh patches/$p -- never edit core directly." >&2
        fi
        exit 1
    fi
    patch --batch -V none -p1 -d "$CORE" < "$HERE/$p" >/dev/null || { echo "[ERROR] failed applying: $p" >&2; exit 1; }
    if ! is_applied "$p"; then
        echo "[ERROR] patch applied but its sentinels are missing (silent skip or truncated new file?): $p" >&2
        echo "        a stray file at one of its add-file paths truncates on apply; remove it or re-run with --force." >&2
        exit 1
    fi
    echo "patches: applied $p"
done
# 3. verify the whole stack before writing the marker.
if ! stack_verified; then
    echo "[ERROR] patch run finished but the tree does not verify (sentinels missing) -- tree left unmarked, investigate before retrying." >&2
    exit 1
fi
echo "$WANT" > "$MARKER"
echo "patches: done ($WANT)."
