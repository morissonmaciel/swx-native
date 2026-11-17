# A/B results ledger (real gameplay, BOTW 01007EF00011E000)

Snapshots live in `<I>/.swx-history/ab-<label>/` (game log + env + report).
Same region/route every run unless noted; window as-is (log-verified
drawable 2560x1440 from the 1080p series on; the first snapshot is 5120x2880
and compares with nothing). Protocol: `instrumentation/ab_testing.md`.

## Gate 0 — baseline reproducibility: PASS

`split-on-baseline` (5K drawable): 2306 frames, 35 longframes, worst
201.0 ms. `split-on-baseline-1080p` (1440p drawable): 1724 frames, 16
longframes, worst 50.4 ms. Same burst signature both runs (237–254 first
uploads, ~30–38 MB). Conclusion: the area-entry burst reproduces with
batch data; the 5K snapshot is reference-only.

## Gate 1 — texture count split: FAIL as improvement, PASS as measurement

Identical burst (237 first / 29.5 MB), same route:
- split ON: `in 8 batches (3 split)`, submit 44.2 ms, whole 50.4 ms.
- split OFF (stock): `in 6 batches (0 split)`, submit 31.1 ms, whole 36.4 ms.
Run totals: ON 16 LF / worst 50.4; OFF 30 LF / worst 36.4 (back-to-back
thermal confound on counts; the burst-frame comparison is the clean one).

Verdict: fragmenting batches adds ~13 ms of commit overhead without moving
encode off the submit thread (also: a 2-texture frame flushed 6 batches —
fragmentation has its own cost). Default flipped to OFF
(`patches/texture_upload_split.patch`, opt-in `SWX_UPLOAD_SPLIT=1`);
instrumentation kept. Follow-up is overlap/pooling, not splitting
(`docs/texture_streaming_hitches.md` §1a/§5, `docs/io_paths_nxnative_vs_mng.md` §2).

## Gate 2 — GPU keep-alive: PASS, effect isolated

- ON: 4116 frames, 33 longframes, **zero hold transitions**.
- OFF (`SWX_GPU_KEEPALIVE=0` verified at boot): 1944 frames, 129 longframes,
  8 transitions in 4+ episodes; worst GPU-bound frames 19–29 ms at
  hy 244–273 (vs ~19 ms ceiling with ON).
Confounds: different run lengths/routes, back-to-back thermals. Magnitude
(0 vs 8 transitions, ~4x LF in half the frames) exceeds noise. Mechanism:
no idle gaps → no downclock → fewer late frames → fewer holds. Keep-alive
stays ON (macOS default). The user's felt benefit is quantified.

## Gate 3a — sync-lag prototype correctness: PASS with recipe, FAIL stock

`dm_nvn_sync_lag.patch` (relax newest-fence wait to previous submission in
`mtl_wait()`; acquire/finish/drain paths untouched):
- Ring 2 (profile default): repeated black blinks with spikes. Mechanism:
  game 1 frame ahead of screen (snapshots) + CPU 1 submission ahead of GPU
  = 2 frames of latency in a 2-slot ring → slot overwrite on spikes.
- Ring 4 (`DM_PRESENT_SNAP=4`): 4724 frames, 0 RED, 0 blinks, 0 gameplay
  holds (startup 12→42 only). Cost: +1 frame display latency.
Recipe (historical Gate 3a pass, now an optional smoothness experiment, not
the install default): `DM_NVN_SYNC_LAG=1` + `DM_PRESENT_SNAP=4`
pinned by `wrapper/main.py` at the time; stock baseline (`=0` / `snap 2`)
is pinned for current texture-hitch testing.

## Gate 3b — sync-lag numbers: MIXED

- Holds: 0 gameplay episodes in 4724 lag frames (vs 1 in the 1080p
  baseline; keepalive-on already had 0 — lag adds nothing beyond keep-alive
  here).
- Burst submit: 34.8 ms (lag+stock) vs 31.1 (stock) vs 44.2 (split on) —
  within run-to-run noise; the overlap did not shrink encode.
- New: one pipeline freeze (frame 3416: 4 new pipelines + 151.3 ms
  libraries, whole 156.9 ms) — first in all runs; rows self-healed into
  `pso_user.tsv`. Follow-up: prewarm coverage gap (fatia 3 territory).
Win confirmed: flatness (no plateaus), not faster bursts — half the MNG
promise. The prototype stays env-gated (lag=1/snap=4 available as an
optional smoothness experiment), stock baseline pinned for current testing,
not promoted to a profile key.

## Gate 1 (save-async S2) — PASS, promoted to default ON

Slice: `patches/dm_save_async.patch` (single file, `hle_fs.c`): only the
post-swap `rm_tree` moved to a joinable background thread; the next
commit's pre-clear joins it; spawn failure falls back to sync; refused
path and cloud sender untouched. 3 runs (`save-on`, `save-on-settings`,
`save-on-long`, up to 26254 frames), zero `[RED]`, saves commit
(`DM_SAVE_ASYNC=1` logged once at boot), no new longframe class;
clone+swap timings fell to sub-ms (995/656 us vs 2–4 ms stock).
Commit-path coverage verified: manual save, autosave and in-game config
save all funnel into `commit_mount_locked` (4 entry points,
`hle_fs.c:1034-1073`; one mount in BOTW, no `_device`/system save).
Promoted: code default ON + install pins `DM_SAVE_ASYNC=1`
(`=0` restores stock). Gate 2 left open: no save-attributed ≥20 ms spike
occurred in either condition to compare (settings/manual writes showed
only sub-detector ripples).

## Gate — keep-alive yield (luneta): PASS, promoted to install default

Slice: `patches/dm_gpu_keepalive_yield.patch` — shared stop word polled
every 1024 iterations, set before each main commit (normal + `sub_cut`);
idle semaphore replaces 2 ms polling; start moved to first `mtl_sub_begin`.
Triple A/B, same luneta route, warm boots, VFR relaxed/auto, pool off:

| Arm | Hot frame p95 | Hot GPU p95 |
|---|---:|---:|
| `keepalive=1,yield=1` | 16.73–16.89 ms | 9.95–15.17 ms, stable |
| `keepalive=1,yield=0` | 17.41–20.48 ms | 15.27–18.66 ms, recurring hitches |
| `keepalive=0` | 19.56–21.31 ms | 16.81–19.84 ms, worst (45 GPU-bound longframes) |

e.g. frame 1500: yield=1 16.73/12.74 ms; yield=0 20.48/18.55 ms; off
21.09/19.38 ms. Mechanism confirmed by counters: thousands of kernels per
window while gameplay runs, hundreds stopped by arriving game work; early
windows show calibration overshoot (max 5.0→2.4 ms cold) settling to the
~1 ms target — gate reads the post-warmup max. Zero `[RED]`, no frame-step
transitions, VFR debt 0.00 in all arms.

Promoted: install pins `SWX_GPU_KEEPALIVE=1` + `SWX_GPU_KEEPALIVE_YIELD=1`
(macOS; code defaults already on). `=0` on either restores stock for A/B.
`log_report` marks keepalive only on the `on:` line; the stop-callback lag
is wall clock (includes CPU scheduling), not a kernel-exit proof.

## Gate — ub-ring R1 (luneta): PASS, promoted to install default

Slice: `patches/dm_residency_ub_ring.patch` (`SWX_RESIDENCY_UB_RING=1`,
default off during the gate) — up to 4 settled shared buffers stay
registered in the residency set; reuse only after the retire fence
completed (submission fences assigned once in `mtl_sub_commit`, shared by
`sub_cut` segments); pressure falls back to the legacy one-shot path.
Same luneta route, 2 boots per side, keepalive+yield on, pool off.

Verdict: causally removes the recurring `g_ubs_buf` alloc + add/remove-set
churn with no regression in any scenario — promoted as a submission
stability / resource-availability optimization, explicitly not a fix for
the luneta's 20–24 ms GPU cost (that stays with passes/attachments).

2×2 gate (same route, 2 boots/side, keepalive+yield on, pool off):
off-side line 2373 created 34–41 1 MB buffers per hot window (+34–41 MB
to the set, 3.1–5.2 ms in commits); on-side the source is gone after
warm-up (stable reuse, zero fallback, zero new allocations hot).
Hot UB rotation 0 vs 34–41 MB/window; residency commits 0.1–1.3 ms vs
3.1–5.2 ms; frame p95 16.72–16.74 vs 16.73–16.75 ms; zero `[RED]`, VFR
stable both sides. Two GPU-bound longframes on the off-side (frames 1924,
2014, ~17 ms GPU, no uploads) are not used as standalone GPU-benefit proof.
Details in `docs/residency_ub_ring.md`.

Promoted: install pins `SWX_RESIDENCY_UB_RING=1`. `=0` restores the legacy
path for A/B.
