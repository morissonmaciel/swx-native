# nxnative core patches

`core/nxnative` is a vendored snapshot: we read it, never fork it. Upstream
fixes and features are imported by replacing the directory; our deviations
live as patch files in `patches/`, applied by `patches/apply_patches.py`
(a /tmp copy per `patches/patches.toml`, which also declares each patch's
games/platforms scope) before every `setup.sh` build. A transparent core
update = fresh snapshot + re-run; conflicts surface loudly instead of
silently rotting. (`patches/patch_nxnative.sh` + `series` are historical.)

## Layout

| Path | Role |
|---|---|
| `patches/patches.toml` | ordered `[[patch]]` stack with scope (`enabled`, `games`, `platforms`, `flag`); order is application order |
| `patches/*.patch` | unified diffs, `-p1`, paths relative to `core/nxnative`, with `# verify:` sentinel headers (see below) |

## When patches apply

`setup.sh` calls `patches/apply_patches.py --platform mac --game <title id>`
after the venv is ready and before staging the game. Rationale: everything
downstream (nxnative build) runs against the patched copy, so patching is a
precondition, not an afterthought. The pristine checkout is never touched.

## Semantics (apply_patches.py; patch_nxnative.sh semantics below are historical)

The toml stack is an ordered stack: a later patch may depend on an earlier
one's context, so application walks in order (enabled + in-scope entries
only) and stops at the first failure. Points 1–4 below describe the
historical `patch_nxnative.sh` (kept for the record: dry-run gates, marker
trust, `--force` normalization); `apply_patches.py` instead always starts
from a fresh /tmp clone, applies for real, and verifies `# verify:`
sentinels — no dry-run gate (proven unable to validate dependent hunks),
no marker, no revert (delete the copy and re-run).

1. Check-then-apply, in order: each patch dry-runs just before it applies
   (`patch --batch -p1 --dry-run`; `--batch`, and a single stdin redirect —
   on 2026-11-16 a stray `</dev/null` after the file redirect fed patch
   empty stdin, which exits 0 doing nothing). Already-applied prefixes are
   skipped. A failure stops before later patches; earlier ones may already
   be applied (`--force` normalizes).
2. Verified by content sentinels, never by reverse dry-run: each patch file
   declares `# verify: <relpath>:<literal>` lines (or `# verify: <relpath>`
   for a file that must exist), and a patch counts as applied only when
   every sentinel holds (`grep -F`). Rationale: Apple's patch(1) answers
   its own "Unreversed patch detected! Ignore -R?" with yes under `--batch`,
   so a reverse dry-run exits 0 even on a pristine tree and proves nothing
   (found 2026-11-16 while stacking a third patch). The post-apply sentinel
   check is also what catches silent skips (empty stdin, missing files).
3. Idempotent, but verified — never trusted blindly: when the marker hash
   matches the current series + contents, every sentinel must still hold.
   Only then the script reports "already applied and verified". A matching
   marker with a diverged tree falls through to the normal check/apply flow
   instead of lying (this exact blind-trust failure happened once,
   2026-11-16). A fully-patched tree that lost its marker is adopted, not
   rebuilt.
   The VFR v1 upgrade keeps an exact copy of the previous diff under
   `patches/compat/`. If the rest of the stack verifies, the installer
   preflights the old reverse and the new apply on scratch copies,
   then upgrades VFR in place. The next build fingerprints the changed
   game rewrite rules and regenerates the executable's stubs.
4. `--force` normalizes first: best-effort revert in reverse order (a later
   patch's context depends on the earlier ones, so forward-order revert
   fails), then a normal run. The way out of half-applied or hand-edited
   trees.

   During the present-worker review, a local `swb_stats` edit shifted the
   context for patch #7 and the forced pass failed. Targeted reapplication
   restored the stack, and a normal invocation verified the marker and all
   sentinels. The incident concerns forced normalization of a locally edited
   core. Use a clean copy to verify a full reverse and reapply.
5. A conflict (upstream changed the same lines) fails the check with the
   `.rej` hunks left for manual rebase: update the `.patch` file, never the
   core directly. Direct core edits are invisible to this mechanism and will
   be silently kept or lost on the next snapshot — always express deviations
   as patch files. A marker must never bless a tree the patches did not change.

## Writing a patch

Make the change in a scratch copy of the core, then
`diff -u` (or `git diff --no-index`) old vs new from inside `core/nxnative`
so paths are relative to it. Keep patches minimal (one concern each),
headered with why (not just what), and reproducible from a pristine snapshot:
the script applies onto a fresh tree with `-p1`. Order in `series` is
application order: a patch whose context depends on another patch's lines
goes after it (new patches go last unless rebased). A patch that fails only
because it runs before its context lands is a series-order bug — move it,
don't rewrite it.
Every patch must declare
`# verify:` sentinel headers — one `grep -F` literal per hunk region (plus a
bare path for any new file) — chosen so they are absent on the pristine tree
and present only when the patch landed; the script trusts nothing else.

To change an existing patch's *content* (not just add a file): the tree
holding the old application cannot be forward-patched, and sentinels cannot
tell versions apart — so normalize first with the *current* files, then
edit, then run. Either `sh patches/patch_nxnative.sh <core> --force`
(reverts the whole stack in reverse order), or revert that patch and
everything above it in reverse order with the current files; never revert a
single stacked patch with the old file (its context is gone under the
patches above it — learned 2026-11-16). To add a new file at the top of the
stack, no revert is needed: append to `series` and run.
