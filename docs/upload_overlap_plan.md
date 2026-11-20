# Upload overlap: implementation record and deferred work

The managed BOTW install enables the repaired staging pool after two clean
pool-only gameplay runs. The core defaults stay off for pooling and
bind-once, and the managed install leaves bind-once off.

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
In that capture, submit-thread encoding and recording dominated. The
matched count-split experiment increased submit cost. Later pooling runs
supported the rollout for BOTW, with a general frame-time gain still
unproven.

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

## 3. Pooled staging and bind-once

The staging pool reuses resident private buffers across submissions and
returns them through the GPU completion path. These buffers are transient
and the pool does not change save contents. The bind-once experiment caches
argument-table and pipeline binds across compatible consecutive dispatches.
Its invalidation covers encoder changes and sparse gather.

The [A/B ledger](../instrumentation/ab_results.md#texture-bursts--22-staging-pool-and-bind-cache-2026-11-18)
records the correctness runs and rollout decision. A general improvement in
burst frame time was not established.

## 4. Parked follow-ups: slice 2 (pre-reserve offsets) and slice 3 (helper copies + GPU decompress)

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

## 5. Standing rules

- Measurement entries served their gates and were removed: the
  `../instrumentation/swx_*_diag.patch` entries (batches, times, paths,
  per-texture, vconv) plus the unlisted residual diag were reverted and
  deleted once every open question they instrumented was decided
  (2026-11-16 final commit). `instrumentation/ab_test.py` and
  `instrumentation/log_report.py` keep parsing enriched historical lines;
  absent upload timing/path suffixes are unavailable, not zero.
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
