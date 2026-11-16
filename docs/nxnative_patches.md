# nxnative core patches

`core/nxnative` is a vendored snapshot: we read it, never fork it. Upstream
fixes and features are imported by replacing the directory; our deviations
live as patch files in `patches/`, re-applied by `patches/patch_nxnative.sh`
on every `install.sh` run. A transparent core update = fresh snapshot +
re-run; conflicts surface loudly instead of silently rotting.

## Layout

| Path | Role |
|---|---|
| `patches/series` | ordered list of patch files (`#` comments allowed); empty = nothing to apply |
| `patches/*.patch` | unified diffs, `-p1`, paths relative to `core/nxnative`, with `# verify:` sentinel headers (see below) |
| `core/nxnative/.swx-patches-applied` | marker with the hash of series + patch contents (state lives with the patched tree; `core/` itself is not versioned here) |

## When patches apply

`install.sh` calls `patches/patch_nxnative.sh <core dir>` after verifying the
core exists and before creating the venv. Rationale: everything downstream
(venv deps, wrapper, build) runs against the patched tree, so patching is a
precondition, not an afterthought. `SWX_SKIP_PATCHES=1` skips it for
debugging (the wrapper then runs against a pristine core).

## Semantics

The series is an ordered stack: a later patch may depend on an earlier
one's context (e.g. batch instrumentation on top of the count split), so
application walks in order and `--force` reverts walk in reverse.

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
4. `--force` normalizes first: best-effort revert in reverse order (a later
   patch's context depends on the earlier ones, so forward-order revert
   fails), then a normal run. The way out of half-applied or hand-edited
   trees.
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
the script applies onto a fresh tree with `-p1`. Every patch must declare
`# verify:` sentinel headers — one `grep -F` literal per hunk region (plus a
bare path for any new file) — chosen so they are absent on the pristine tree
and present only when the patch landed; the script trusts nothing else.
