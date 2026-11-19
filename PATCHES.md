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
| 2 | `dm_save_async.patch` | `SWX_SAVE_ASYNC` (`DM_SAVE_ASYNC` legacy alias) | promoted (default ON) | clone+swap 995/656 µs vs 2–4 ms stock; 0 attributable longframes over 26k frames | Gate save-async S2, `ab_results.md` |
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
| 15 | `dm_texture_burst_attribution.patch` | (opt-in via `DM_NVN_TSV`, no new flag) | batch-correlated texture-upload attribution (encode/publish/gpu events with stable full-ID joins; missing fields unavailable, never zero) | per-batch wall-clock buckets for area-entry bursts; 554/159 fully joined batches, zero overflow/slot-waits | `docs/texture_burst_measurement_and_dispatch_plan.md`, `instrumentation/ab_results.md` |
| 16 | `dm_texture_burst_attribution_host_lifetime.patch` | (same opt-in) | host-side lifetime handoff for burst records (TSV drained on the game thread, GPU feedback publishes timestamps) | supports the #15 attribution path | `docs/texture_burst_measurement_and_dispatch_plan.md` |
| 17 | `dm_up_blit_rec.patch` | `SWX_NVN_UP_BLIT_REC` | experimental (default 0, never pinned by installer) | defers whole buffer→texture upload batches to the encode thread as one `RO_COMMIT_UBATCH`; GPU executes the same copies in the same order; A/B recorded | `instrumentation/ab_results.md` |
| 18 | `swx_save_cleanup_worker.patch` | `SWX_SAVE_ASYNC` | promoted (default ON) | persistent `QOS_CLASS_BACKGROUND` cleanup worker, FIFO depth 4, per-generation `mkdtemp` staging, orphan recovery on mount; single-install A/B: 5.47–5.97 ms/commit off vs 1.10–1.18 ms on (≈4–5 ms sync-path saving); isolated 74.6 ms frame shows no commit link | `instrumentation/ab_results.md` |
| 19 | `swx_botw_render_scale.patch` | `DM_RENDER_SCALE` | promoted as opt-in on macOS for BOTW 1.9.0; default `1.0` | raises the internal render-scale ceiling to `1.2`; both `1.2` gameplay runs held 16.67 ms on-screen p99 in every reported window | `instrumentation/ab_results.md` |
| 20 | `swx_botw_disable_drs.patch` | `SWX_DISABLE_DRS` | experimental opt-in (default off; never pinned by the installer) | fixes the 3D views at `DM_RENDER_SCALE`, including `1.0` (1600×900 docked); compatibility gate passed 2026-11-19 (M4 Pro): 1.0+flag → 1600×900 scene, 1.2 → 1920×1080, no cadence regression; effect under load unproven | `instrumentation/ab_results.md` |
| 21 | `swx_botw_native_1080p.patch` | `SWX_RES_BYPASS` | experimental opt-in (default absent; never pinned by the installer) | scene, canvas and VFR snapshots at 1920×1080 docked. v2 gameplay accepted on M4 Pro, with windowed 1:1 and fullscreen MetalFX verified in retained logs. Fullscreen without MetalFX passed by player report. | `instrumentation/ab_results.md` |
| 22 | `swx_botw_native_1440p.patch` | `SWX_RES_BYPASS=1440p` | experimental stage 1, not exposed (no launcher option; env-armed only) | canvas, swapchain, TV and viewport at 2560×1440 with scene held at 1920×1080; +512 MiB heap headroom gated on confirmed boot. Boot verified (canvas/swapchain/drawable 2560×1440, pool 3797 MiB). **Gate 1 PASS** (frame 1421, pass 143733: 4 color + 1 depth targets at 1920×1080; output chain at 2560×1440). | `instrumentation/ab_results.md` |
| 23 | `swx_botw_native_1440p_scene.patch` | `SWX_RES_BYPASS=1440p` | experimental opt-in (launcher offers 1440p on macOS with SWX_NATIVE_1440P; never pinned) | native 1440p scene: docked stub emits {2560.0f, 2560.0f}, canvas/swapchain/crop/snapshots/drawable 2560×1440, heaps as in #22. **2B PASS** (fullscreen run: output chain 2560×1440, MetalFX → 3840×2160, present p95 1.23–1.26 ms, 16.67 ms avg over 255 s, zero `[RED]`, exit 0). 2C partial (fullscreen smoke pass; window-return + remaining controls deferred). Gate 3 (formal 1080p-vs-1440p A/B) open. | `instrumentation/ab_results.md` |
| 24 | `swx_botw_native_4k.patch` | `SWX_RES_BYPASS=4k` | experimental opt-in (launcher offers 4k on macOS with SWX_NATIVE_4K; never pinned) | **native 4K functional (approved 2026-11-19)**: docked stub emits {3840.0f, 3840.0f}, canvas/swapchain/crop/snapshots/drawable 3840×2160, display 112 / GPU 448 / memory 4080 / RootHeap 4048 MiB (+960 MiB heap/pool → 4288/4245 MiB). PASSDUMP frame 1150 pass 107186: 4 color + 1 depth at 3840×2160, viewport/crop/snapshots 3840×2160, heap 3.98 GB, 1:1 present (bilinear, MetalFX armed idle), zero `[RED]`. Sustained 60 fps not claimed. Superseded by #25 rename (canonical `SWX_NATIVE_RES=2160p`). | `instrumentation/ab_results.md` |
| 25 | `swx_botw_native_res.patch` | `SWX_NATIVE_RES` | experimental opt-in Etapa 1 (launcher Native resolution 1080p/1440p/2160p on macOS with SWX_NATIVE_*; never pinned): canonical key rename, legacy fallback, saved-choice migration, reinstall canonicalization | canonical key rename: `SWX_NATIVE_RES=1080p/1440p/2160p`, `0` off, absent by default; legacy `SWX_RES_BYPASS=1080p/1440p/4k` reads when canonical absent (`4k` is `2160p`), canonical wins including `0`; saved `launcher.SWX_RES_BYPASS` migrates once to `launcher.SWX_NATIVE_RES`; reinstall pins only canonical. Etapa 2 (same patch): macOS Graphics section (Display mode → Picture shape, after Game) with render scale as a 50–120 % select filtered by the stub ceiling; saved `1.00` normalizes to `1.0`, off-list falls back to 100 %, env-fixed `0.95` applies and shows 95 %. Gate 2 pending gameplay. | `instrumentation/ab_results.md` |

## Out of tree (frozen, not in `series`)

| Branch | Patch | Status |
|--------|-------|--------|
| `feat/attachment-store-lookahead` (frozen at `b2a52bd`) | `dm_store_lookahead.patch` (`DM_NVN_STORE_LOOKAHEAD`) | experimental, correct but no gain: exercised (~299–600 indexed/window, 0 invalidations, ~25–111k examined) yet only 32 warm-up stores (~0.5 MB) dropped, zero on the hot luneta stretch; flag cleared from player env |

## Managed BOTW install env (production, 2026-11-18)

`wrapper/main.py` pins these into
`~/Library/Application Support/nxnative/01007EF00011E000/env.txt`; the
launcher logs each line at boot (`launcher: env K=V`). Experimental keys
(`DM_NVN_ENC_THREAD`, `SWX_NVN_UP_BLIT_REC`, `SWX_GPU_BIND_ONCE`,
`DM_NVN_TSV*`, `DM_NVN_WAITS*`, `SWX_RT_HEAP_RESERVE`,
`SWX_DISABLE_DRS`, `SWX_NATIVE_RES`) stay absent:
compiled defaults apply. Legacy `SWX_RES_BYPASS` is never pinned (rewritten to canonical on reinstall).

| Key | Value | Status |
|-----|-------|--------|
| `SWX_VFR` | `relaxed` | install default (display-divisor lock) |
| `SWX_VFR_HZ` | `auto` | panel-derived display-link rate |
| `DM_SAVE` / `DM_PSO_USER` / `DM_PSO_ARCHIVE` / `DM_PSO_PAIRS` | paths | save + shader cache locations |
| `SWX_GPU_TEXTURE_POOL` | `1` | promoted: resident staging pool + repair (#14) |
| `SWX_SAVE_ASYNC` | `1` | promoted (default ON; `DM_SAVE_ASYNC` legacy alias only, `SWX_SAVE_ASYNC` wins when both are set) |
| `DM_PSO_USER_ASYNC` | `1` | promoted (default ON) |
| `SWX_GPU_KEEPALIVE` | `1` | promoted (on macOS) |
| `SWX_GPU_KEEPALIVE_YIELD` | `1` | promoted (install pins `=1`) |
| `SWX_RESIDENCY_UB_RING` | `1` | promoted (install pins `=1`) |

## Notes

- Order in `series` is application order; a later patch may depend on an
  earlier one's context. New patches go last unless rebased.
- Each patch declares `# verify:` sentinels; `patch_nxnative.sh --force`
  reverts the whole stack and re-applies it. Direct core edits are not
  tracked — always express deviations as patch files.
- Verification is by real gameplay gates (`instrumentation/ab_test.py` +
  `log_report.py`), never synthetic unit tests; numbers land in
  `instrumentation/ab_results.md`.
