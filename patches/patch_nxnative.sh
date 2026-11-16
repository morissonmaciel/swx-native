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
#   # verify: <relpath>             — the file itself must exist
# A patch counts as applied only when every sentinel holds.
#
# Guarantees:
# - sequential check-then-apply: each patch dry-runs just before it applies,
#   so a dependent patch sees the tree its context was written against.
#   Already-applied prefixes are skipped. A failure stops before later
#   patches; earlier ones may already be applied (--force normalizes).
# - a marker is a *verified* claim: it is only written after every sentinel
#   of every patch holds on the just-patched tree, and an existing marker is
#   only trusted after the same check passes.
# - --batch everywhere and exactly one stdin redirect (the patch file).
#   NEVER add a second stdin redirect (e.g. </dev/null after the file):
#   the last redirect wins, patch reads empty stdin, exits 0 doing nothing,
#   and only the post-apply sentinel check catches it (this exact bug
#   happened once, 2026-11-16).
# - -V none: no .orig/.rej litter on success paths.
# - quiet no-op when series is missing or empty.
# Usage: patch_nxnative.sh <core dir> [--force]
# --force normalizes first (best-effort revert in reverse order, then
# a normal run): the way out of half-applied trees.
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
                [ -e "$CORE/$spec" ] || rc=1
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

WANT="$(hash_state)"
if [ "$FORCE" -eq 0 ] && [ -f "$MARKER" ] && [ "$(cat "$MARKER")" = "$WANT" ]; then
    if stack_verified; then
        echo "patches: already applied and verified ($WANT)."
        exit 0
    fi
    echo "warning: marker matches but the tree does not verify (manual edits? partial apply?). Re-checking from scratch; use --force to normalize first." >&2
    rm -f "$MARKER"
fi

# 0. --force normalizes first: revert in reverse order (a later patch's
# context depends on the earlier ones, so forward-order revert fails),
# each revert independently best-effort, then drop any stale marker.
if [ "$FORCE" -eq 1 ]; then
    for p in $(echo "$PATCHES" | tac 2>/dev/null || echo "$PATCHES" | tail -r); do
        if is_applied "$p"; then
            patch --batch -V none -p1 -R -d "$CORE" < "$HERE/$p" >/dev/null 2>&1 || true
            if is_applied "$p"; then
                echo "warning: revert did not clear sentinels: $p" >&2
            else
                echo "patches: reverted $p (forced clean slate)"
            fi
        fi
    done
    rm -f "$MARKER"
fi

# Adopt a fully-patched tree that lost its marker instead of erroring on
# per-patch context overlap.
if stack_verified; then
    echo "$WANT" > "$MARKER"
    echo "patches: already applied and verified ($WANT)."
    exit 0
fi

# 1-2. check-then-apply in series order: each patch dry-runs just before it
# applies, so a dependent patch sees the tree its context was written
# against. A failure stops before later patches (--force normalizes).
for p in $PATCHES; do
    [ -f "$HERE/$p" ] || { echo "[ERROR] patch listed in series but missing: $p" >&2; exit 1; }
    if is_applied "$p"; then
        echo "patches: already applied, skipping $p"
        continue
    fi
    if ! patch --batch -V none -p1 --dry-run -d "$CORE" < "$HERE/$p" >/dev/null 2>&1; then
        echo "[ERROR] patch does not apply cleanly (rebase it): $p" >&2
        echo "        resolve inside core/nxnative, then refresh patches/$p -- never edit core directly." >&2
        echo "        (if the tree itself diverged from a marked state, re-run with --force to normalize it first.)" >&2
        exit 1
    fi
    patch --batch -V none -p1 -d "$CORE" < "$HERE/$p" >/dev/null || { echo "[ERROR] failed applying: $p" >&2; exit 1; }
    if ! is_applied "$p"; then
        echo "[ERROR] patch applied but its sentinels are missing (silent skip?): $p" >&2
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
