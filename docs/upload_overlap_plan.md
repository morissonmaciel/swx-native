# Upload overlap plan: pooled staging first, reserve and helper copies parked

Approved multi-slice plan for cutting area-entry texture-upload cost.
Branch: `feat/async_writes`. Docs only — no code, no builds.

## 1. Objective and non-goal

Objective: cut the per-texture submit-thread upload cost of area-entry
bursts. Per-texture-diag reading on the 237-first burst: ~52 ms of
GPU-deswizzle encode plus its records out of ~56 ms submit; alloc,
rescommit, commit, drain and oldq queue each measure single-digit ms;
the GPU-encode path carries 100% of first uploads with zero CPU fallback
and zero drain waits. Fields: `instrumentation/swx_upload_pertexture_diag.patch`
(path counts plus per-path ms), `instrumentation/swx_upload_times_diag.patch`
(alloc/rescommit/commit/drain/slotwait/oldq), parsed by
`instrumentation/log_report.py` (`m_tm`, `m_pa`, `up_pdi/pen/pcp`).
Conclusion: the burst bottleneck is submit-thread encode + records, not
allocation or commit — so splitting batches cannot fix it (Gate 1 proved
this: `instrumentation/ab_results.md`), and the fix is pooled resident
staging with overlap, not reordering.

Non-goals (each covered elsewhere, not in these slices):

- Vertex path: per-draw CPU conversion vs descriptors is second-order to
  uploads — `docs/texture_streaming_hitches.md` §3.
- Present snapshots: `snap_drain`/`snap_take` waits are bounded and rarely
  hit — `docs/texture_streaming_hitches.md` §4c.
- Archive/writeback: `pso_user` batching is done
  (`patches/dm_pso_user_async.patch`); archive batch-of-N and the 151 ms
  creation-path audit stay parked — `docs/async_writes_evaluation.md` §6.
- Save: done via S2 async delete — `docs/async_writes_evaluation.md` §5,
  `patches/dm_save_async.patch`.

## 2. Standing decisions log

Each dated 2026-11-16 with a one-line rationale:

- Instrumentation first, done and permanent: batch/times/paths/per-texture
  diag patches plus `ab_test.py`/`log_report.py` stay in the tree as
  measurement infrastructure — because every slice's gates depend on them
  (`patches/series` lines 3, 7–9).
- Pool/overlap evaluation before code, done via subagent: `docs/io_paths_nxnative_vs_mng.md`
  §§2/4 plus `docs/texture_streaming_hitches.md` §1a — because the numbers
  (not theory) decide whether the fix is pooling, reserving, or copying.
- S1-full no-clone save DROPPED in favor of S2 async delete with clone
  kept: APFS CoW clone+swap is metadata-cheap and preserving it keeps the
  exact durability/atomicity contract — `docs/async_writes_evaluation.md` §4.
- Gate-1 count split REMOVED (2026-11-16): on the identical 237-first /
  29.5 MB burst, same route, split ON cost 8 batches / 44.2 ms submit vs OFF
  6 batches / 31.1 ms (`instrumentation/ab_results.md` Gate 1). Fragmentation
  adds overhead without moving encode off the submit thread, so the patch,
  its env switch and its docs were deleted; numbers preserved in
  `instrumentation/ab_results.md`.
- Async pso batch implemented default ON: `pso_user_record` keeps dedup,
  appends under one leaf lock, flushes every 64 rows on a UTILITY serial
  queue — crash loses only unflushed rows, re-learned next run
  (`patches/dm_pso_user_async.patch`, `docs/async_writes_evaluation.md` §6).
- Frame-ahead overlap REMOVED (2026-11-17): the `DM_NVN_SYNC_LAG` port of
  MNG's `SYNC_LAG` (Gate 3a recipe `=1` + `DM_PRESENT_SNAP=4`, ring 4 runs
  4724 frames with 0 RED/0 blinks) was not approved as default — subjective
  call, Gates 3a/3b mixed. Patch, env switch and dedicated doc deleted;
  numbers preserved in `instrumentation/ab_results.md`. Installs no longer pin
  the snapshot ring; the compiled default (stock ring) applies unless set
  for A/B testing.

## 3. Slice 0 — bind-once state cache (env-gated, default off: `SWX_GPU_BIND_ONCE`)

`patches/dm_gpu_bind_once.patch` suppresses repeated argument-table and
pipeline binds only for consecutive deswizzle dispatches on the same upload
encoder. It invalidates the cache before sparse gather and whenever the
upload encoder ends or is recreated; `setAddress` stays per slice. This is
not an MNG parity claim: its reference deswizzle path also binds per slice.
Gate it with cold and enter-leave-enter A/B runs: zero `[RED]`, no new
frame-step transition, median p95 submit reduction of at least 1 ms, and no
individual regression above 1 ms.

## 4. Slice 1 — pooled resident staging (env-gated, default off: `SWX_GPU_TEXTURE_POOL`)

Shape (MNG mirror, NX-sized): keep a small pool of private + shared staging
blocks resident across submissions — cf. MNG 6x32 MB private + 4x8 MB shared,
`res_add` once (`docs/io_paths_nxnative_vs_mng.md` §2; literal anchors
`up_pool_take` / `up_reserve` / `up_pair_alloc` / `up_cpu_reserve`,
`bcd`/`bc1astc`/`bctc` dispatches, `retire_idle_kick`, `q4_commit` with no
`rec_drain` and no encode thread live in `docs/texture_streaming_hitches.md`
§1a — none of these tokens exist in `io_paths`, which describes the same
mechanism in prose). NOTE: `reup_take` names nothing in either tree or doc
and is dropped from this plan.

What changes:

- Pooled take instead of per-batch `newBuffer` + `res_add`: NX today does
  per-texture `newBuffer(Shared)` + `res_add` plus per-batch
  `newBuffer(Private)` + `res_add` with `res_commit` in `up_flush`
  (`docs/io_paths_nxnative_vs_mng.md` §2); slice 1 takes blocks from the
  pool and returns them on retire instead.
- Busy-bit retire instead of `res_remove`: retire on the existing feedback
  path (`g_up_pending` drain in `up_flush`) with a busy bit per block,
  MNG-style (`retire_idle_kick`, `docs/texture_streaming_hitches.md` §1a).
- Dirty-gated `res_commit` skip: blocks that stay resident and unmodified
  skip re-commit; only newly taken or dirtied blocks commit.
- Pool hit/miss counters in the existing diag line: extend the
  `[upload alloc ... + path ...]` longframe line
  (`instrumentation/swx_upload_times_diag.patch`,
  `instrumentation/swx_upload_paths_diag.patch`,
  parsed by `instrumentation/log_report.py`) with `pool hit/miss`;
  measurement only, no behavior change.

What stays identical:

- Split triggers, batch contract (128 MB cap, one upload CB per replay
  submission, flush-before-sample ordering), fence / sample / present
  ordering, and the encode-thread record path (`rec_commit_upload` +
  `rec_drain`).
- Refused, writeback (`gsb_store` inline path,
  `docs/texture_streaming_hitches.md` §4b) and save paths
  (`docs/async_writes_evaluation.md`).

Crash/kill semantics: staging copies only, no persistent state. A kill
mid-burst loses at most the in-flight staging contents of one submission;
there is no torn persistent tail by construction (nothing is written back
from staging), so no `kill -9` recovery proof is needed beyond the existing
Gate 1 save/load/shrine/teleport run.

Gate 1 (correctness) — house style, cloned from
`docs/async_writes_evaluation.md` §5:

- Zero `[RED]`, save/load + shrine + teleport clean, no new longframe
  class vs the same-route baseline.
- Kill mid-burst defined behavior: relaunch loads clean (staging is
  transient; committed textures re-upload from RomFS on next entry).
- PASS requires 2+ clean runs on the fixed area-entry route
  (`instrumentation/ab_testing.md` fixed variables; `ab_test.py collect`).

Gate 2 (numbers) — same fixed route, `collect`/`compare`:

- Submit p50/p95 on burst frames falls; `uploads/batch` unchanged;
  holds unchanged-or-better; no new longframe class.
- Success = alloc + rescommit + commit frame-attributed cost shrinks toward
  zero while encode-ms is untouched (encode moves in later slices, not this
  one) — a pooling win, not an fps number.
- Promote to default only with a second corpus (different area, longer
  session), same rule as `docs/async_writes_evaluation.md` §5 Gate 2.

## 5. Parked follow-ups: slice 2 (pre-reserve offsets) and slice 3 (helper copies + GPU decompress)

Slice 2 — pre-reserve offsets, only if slice 1 is insufficient. MNG shape:
`up_reserve` / `up_pair_alloc` atomic reservation of
source+first+second with flush-before-split so one texture's dependent
buffers never split across batches (`docs/texture_streaming_hitches.md`
§1a). NX work: reserve offsets up front per texture pair instead of
`g_up_staging_bytes`-gated lazy split in `up_alloc`. Entry criterion:
slice-1 runs show pool hits ~100% yet burst submit p95 still ≥ ~30 ms —
i.e. the residue is reservation/encode granularity, not alloc/commit.
Own design + Gate 1/2 clone required before coding.

Slice 3 — CPU copies to helpers with mandatory join + GPU decompress,
largest, needs its own design and correctness gate. MNG shape: shared
staging pool for CPU-staged textures plus BC1-5 GPU decompression (`bcd`)
and BC→ASTC/EAC transcoding (`bc1astc`/`bctc`) inside the same upload batch
(`docs/texture_streaming_hitches.md` §1a/§1b); NX keeps copies + compute
deswizzle on the submit thread (`docs/io_paths_nxnative_vs_mng.md` §2).
Entry criterion: after slices 1–2, per-texture `up_pen_ms` still dominates
burst submit (encode-bound, pool/reserve exhausted their share). Gate
addition: wrong-picture abort condition — any helper/GPU-decompress mismatch
must fail loudly (`[RED]`) and fall back to the synchronous path, never
ship a wrong texel. Parked until slices 1–2 numbers demand it.

## 6. Standing rules

- Measurement entries served their gates and were removed: the
  `../instrumentation/swx_*_diag.patch` entries (batches, times, paths,
  per-texture, vconv) plus the unlisted residual diag were reverted and
  deleted once every open question they instrumented was decided
  (2026-11-16 final commit). `instrumentation/ab_test.py` and
  `instrumentation/log_report.py` keep parsing the enriched lines
  (backward compatible: absent suffixes read as zero).
- One concern per patch (`docs/nxnative_patches.md`): each slice is its own
  single-file patch with a why-header.
- Verify-before-marker: every patch declares `# verify:` sentinel headers
  (absent pristine, present only when landed) and is written scratch-tested
  via `diff -u` old-vs-new from inside `core/nxnative`
  (`docs/nxnative_patches.md`); the marker file never blesses an unverified
  tree.
- A/B via `instrumentation/ab_test.py set` keys (source of truth is
  `ab_test.py:10,38-40`): `keepalive`, `snap`, `save`,
  `pso`, `pool`, `bind_once` — `on` writes `=1` (snap `=4`), `off` writes
  `=0` (snap `=2`), `clear` restores the compiled default. (The prose list in
  `instrumentation/ab_testing.md` shows only the first four; the tool
  supports all six.)
- Rollback = revert patch + rebuild: `sh patches/patch_nxnative.sh <core>
  --force` normalizes the stack in reverse order
  (`docs/nxnative_patches.md`); no runtime flag removal is needed since
  behavior switches default off (`=0` restores stock).
- Commits only when the user explicitly asks.
