# Patches registry

`core/nxnative` is a vendored snapshot; every deviation lives as a file in
`patches/`, applied in `patches/series` order by `patches/patch_nxnative.sh`
on each install (see `docs/nxnative_patches.md`). Status values:
**promoted** (default on / install-pinned) vs **experimental** (default off,
opt-in via `ab_test.py`).

| # | Patch | Flag | Status | Effect + metric | Evidence |
|---|-------|------|--------|-----------------|----------|
| 1 | `swx_keepalive_gpu.patch` | `SWX_GPU_KEEPALIVE` | promoted (on macOS) | 0 vs 8 hold transitions, ~4x fewer longframes in half the frames | Gate 2, `ab_results.md` |
| 2 | `dm_save_async.patch` | `DM_SAVE_ASYNC` | promoted (default ON) | clone+swap 995/656 µs vs 2–4 ms stock; 0 attributable longframes over 26k frames | Gate save-async S2, `ab_results.md` |
| 3 | `dm_pso_user_async.patch` | `DM_PSO_USER_ASYNC` | promoted (default ON) | batched `pso_user.tsv` writes, one fflush per flush on a background queue | header ("default ON since Gate 1 pass") |
| 4 | `dm_gpu_texture_pool.patch` | `SWX_GPU_TEXTURE_POOL` | experimental (stays `=0`) | Gate failed on numbers: pool p95 worse than baseline, frames >33 ms only with pool on | phase-1 gate verdict |
| 5 | `dm_gpu_bind_once.patch` | `SWX_GPU_BIND_ONCE` | experimental (default off) | suppresses repeated deswizzle binds; counters only, no gate numbers recorded | A/B only |
| 6 | `dm_vfr_framepace.patch` | `SWX_VFR` | install default `relaxed`; new cadence A/B pending | display-divisor lock, monotonic fallback, real-time delta measured at game present | `docs/vfr_framepace.md` |
| 7 | `dm_gpu_keepalive_yield.patch` | `SWX_GPU_KEEPALIVE_YIELD` | promoted (install pins `=1`) | luneta frame p95 16.73–16.89 ms vs 17.41–20.48 (`yield=0`) vs 19.56–21.31 (off); GPU p95 9.95–15.17 vs 15.27–18.66 vs 16.81–19.84 | triple A/B, `ab_results.md` |
| 8 | `dm_residency_ub_ring.patch` | `SWX_RESIDENCY_UB_RING` | promoted (install pins `=1`) | hot UB rotation 0 vs 34–41 MB/window; residency commits 0.1–1.3 vs 3.1–5.2 ms; frame p95 unchanged; not a fix for the 20–24 ms luneta GPU cost | 2×2 luneta A/B, `ab_results.md` + `docs/residency_ub_ring.md` |
| 9 | `dm_rt_heap_reserve.patch` | `SWX_RT_HEAP_RESERVE` | experimental, in main, default off | automatic-MTLHeap arenas (16 MB min, 64 MB/arena, 256 MB total) for private RenderTarget heaps; mechanism pass (87–100% stock RT creations eliminated hot, 0 fallback); perf gate open | `ab_results.md` |
| 10 | `dm_vfr_ratio_load_fix.patch` | `SWX_VFR` | VFR guest timing fix | BOTW delta-time and UI-step VFR stubs load the published ratio at state `+0xA0`; gameplay A/B pending | `docs/vfr_framepace.md` |
| 11 | `dm_present_worker.patch` | `SWX_VFR` | experimental (active whenever VFR armed; no new flags) | worker waits for source-safe completion, keeps cancellation observable without advancing `g_pq_done` over unfinished reads, and blocks promotion after purge; ready FIFO of 2; `SWX_VFR=0` stock untouched; gameplay A/B pending | `docs/vfr_framepace.md` |

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
