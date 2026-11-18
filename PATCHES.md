# Patches registry

`core/nxnative` is a vendored snapshot; every deviation lives as a file in
`patches/`, applied in `patches/series` order by `patches/patch_nxnative.sh`
on each install (see `docs/nxnative_patches.md`). Status values:
**promoted** (install-pinned for BOTW) vs **experimental** (default off,
opt-in via `ab_test.py`). “Install-pinned” describes the managed BOTW
installation environment, not the vendored core's compiled default.

| # | Patch | Flag | Status | Effect + metric | Evidence |
|---|-------|------|--------|-----------------|----------|
| 1 | `swx_keepalive_gpu.patch` | `SWX_GPU_KEEPALIVE` | promoted (on macOS) | 0 vs 8 hold transitions, ~4x fewer longframes in half the frames | Gate 2, `ab_results.md` |
| 2 | `dm_save_async.patch` | `DM_SAVE_ASYNC` | promoted (default ON) | clone+swap 995/656 µs vs 2–4 ms stock; 0 attributable longframes over 26k frames | Gate save-async S2, `ab_results.md` |
| 3 | `dm_pso_user_async.patch` | `DM_PSO_USER_ASYNC` | promoted (default ON) | batched `pso_user.tsv` writes, one fflush per flush on a background queue | header ("default ON since Gate 1 pass") |
| 4 | `dm_gpu_texture_pool.patch` | `SWX_GPU_TEXTURE_POOL` | promoted for BOTW install env (`=1`); core compiled default remains off | Pool-only passed two gameplay runs with zero misses and a 64 MiB resident pool; matched burst evidence is noisy and does not establish a general performance gain | `instrumentation/ab_results.md`, `docs/texture_burst_measurement_and_dispatch_plan.md` |
| 5 | `dm_gpu_bind_once.patch` | `SWX_GPU_BIND_ONCE` | experimental (compiled default off; absent from managed BOTW env) | suppresses repeated deswizzle binds; remains explicitly selectable for A/B; combined pool+bind runs had recurring hitches in 2/3 runs, without isolating cause | `instrumentation/ab_results.md`, A/B only |
| 6 | `dm_vfr_framepace.patch` | `SWX_VFR` | install default `relaxed`; 30 fps movie + return-to-gameplay approved (60→30→60, p99 33.33/16.67 ms); steady-rate A/B still open | display-divisor lock, monotonic fallback, real-time delta measured at game present | `docs/vfr_framepace.md`, `ab_results.md` |
| 7 | `dm_gpu_keepalive_yield.patch` | `SWX_GPU_KEEPALIVE_YIELD` | promoted (install pins `=1`) | luneta frame p95 16.73–16.89 ms vs 17.41–20.48 (`yield=0`) vs 19.56–21.31 (off); GPU p95 9.95–15.17 vs 15.27–18.66 vs 16.81–19.84 | triple A/B, `ab_results.md` |
| 8 | `dm_residency_ub_ring.patch` | `SWX_RESIDENCY_UB_RING` | promoted (install pins `=1`) | hot UB rotation 0 vs 34–41 MB/window; residency commits 0.1–1.3 vs 3.1–5.2 ms; frame p95 unchanged; not a fix for the 20–24 ms luneta GPU cost | 2×2 luneta A/B, `ab_results.md` + `docs/residency_ub_ring.md` |
| 9 | `dm_rt_heap_reserve.patch` | `SWX_RT_HEAP_RESERVE` | experimental, in main, default off | automatic-MTLHeap arenas (16 MB min, 64 MB/arena, 256 MB total) for private RenderTarget heaps; mechanism pass (87–100% stock RT creations eliminated hot, 0 fallback); perf gate open | `ab_results.md` |
| 10 | `dm_vfr_ratio_load_fix.patch` | `SWX_VFR` | VFR guest timing fix; movie + resume path approved (ratio held across 60→30→60); rest pending | BOTW delta-time and UI-step VFR stubs load the published ratio at state `+0xA0` | `docs/vfr_framepace.md` |
| 11 | `dm_present_worker.patch` | `SWX_VFR` | experimental (active whenever VFR armed; no new flags); movie + resume approved via ready FIFO (150/150 per 300 refreshes, 0 empty/0 drops); full Gate 2 still open | worker waits for source-safe completion, keeps cancellation observable without advancing `g_pq_done` over unfinished reads, and blocks promotion after purge; ready FIFO of 2; `SWX_VFR=0` stock untouched | `docs/vfr_framepace.md`, `ab_results.md` |
| 12 | `dm_vfr_queue_policy.patch` | `SWX_VFR` | experimental (Etapa 2; movie + resume approved with zero empty/drops in transition; steady 60/60 production gate still open) | four physical snapshots, two-ready FIFO, oldest drop on overflow, bounded one-period priming at startup/resume, acquire cadence credits, and per-window queue/simulation telemetry using the cached display-link rate; no new environment key | `docs/vfr_framepace.md`, `ab_results.md` |
| 13 | `dm_vfr_quick_menu_alpha.patch` | `SWX_VFR` | experimental (two player-reported VFR runs passed; broader selector matrix and stock control unrecorded) | Runs BOTW 1.9.0's existing capture-alpha correction at every positive VFR animation frame, while preserving the stock equality branch and movie/scripted-override paths | `docs/vfr_quick_menu_alpha.md` |
| 14 | `dm_gpu_texture_pool_fix.patch` | `SWX_GPU_TEXTURE_POOL` | correctness repair for the promoted install-pinned pool | Preserves upload-block ownership through GPU feedback, checks batch budget before pool take/fallback allocation, and reports materialized private pool capacity separately from useful reserved bytes; two pool-only gameplay runs had zero misses and 64 MiB materialized | `docs/texture_burst_measurement_and_dispatch_plan.md`, `instrumentation/ab_results.md` |

## Out of tree (frozen, not in `series`)

| Branch | Patch | Status |
|--------|-------|--------|
| `feat/attachment-store-lookahead` (frozen at `b2a52bd`) | `dm_store_lookahead.patch` (`DM_NVN_STORE_LOOKAHEAD`) | experimental, correct but no gain: exercised (~299–600 indexed/window, 0 invalidations, ~25–111k examined) yet only 32 warm-up stores (~0.5 MB) dropped, zero on the hot luneta stretch; flag cleared from player env |

## Notes

- Order in `series` is application order; a later patch may depend on an
  earlier one's context. New patches go last unless rebased.
- Each patch declares `# verify:` sentinels; `patch_nxnative.sh --force`
  reverts the whole stack and re-applies it. Direct core edits are not
  tracked — always express deviations as patch files.
- Verification is by real gameplay gates (`instrumentation/ab_test.py` +
  `log_report.py`), never synthetic unit tests; numbers land in
  `instrumentation/ab_results.md`.
