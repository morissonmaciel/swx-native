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
| `patches/*.patch` | unified diffs, `-p1`, paths relative to `core/nxnative` |
| `core/nxnative/.swx-patches-applied` | marker with the hash of series + patch contents (state lives with the patched tree; `core/` itself is not versioned here) |

## When patches apply

`install.sh` calls `patches/patch_nxnative.sh <core dir>` after verifying the
core exists and before creating the venv. Rationale: everything downstream
(venv deps, wrapper, build) runs against the patched tree, so patching is a
precondition, not an afterthought. `SWX_SKIP_PATCHES=1` skips it for
debugging (the wrapper then runs against a pristine core).

## Semantics

1. Check-then-apply: every patch in series order is verified first
   (`patch -p1 --dry-run`); any failure stops the run before anything is
   modified — no half-patched tree.
2. Idempotent: when the marker hash matches the current series + contents,
   the script reports "already applied" and exits 0.
3. `--force` re-applies from scratch (useful after a manual core edit).
4. A conflict (upstream changed the same lines) fails the check with the
   `.rej` hunks left for manual rebase: update the `.patch` file, never the
   core directly. Direct core edits are invisible to this mechanism and will
   be silently kept or lost on the next snapshot — always express deviations
   as patch files.

## Writing a patch

Make the change in a scratch copy of the core, then
`diff -u` (or `git diff --no-index`) old vs new from inside `core/nxnative`
so paths are relative to it. Keep patches minimal (one concern each),
headered with why (not just what), and reproducible from a pristine snapshot:
the script applies onto a fresh tree with `-p1`.
