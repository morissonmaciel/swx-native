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

## Gate — rt-heap arenas H1 (luneta): mechanism PASS, merged to main, default off

Slice: `patches/dm_rt_heap_reserve.patch` (`SWX_RT_HEAP_RESERVE`, default off)
— automatic-MTLHeap arenas holding several live transient render targets
each; keyed only by heap compatibility (storage/cache/hazard modes); explicit
retire-fence stamps per submit-thread use; rigid 256 MB budget over active
and idle arenas; both-arm eligible/stock counters. Replaces the failed
one-heap-per-texture class design under the same flag (32-class table and
per-texture high-water removed).

Verdict: correctness + mechanism pass — zero `[RED]`, VFR intact (debt 0),
136 MB effective backing (< 256 MB), zero fallback in both on-runs, real reuse
eliminating 87–100% of stock RT creations after warm-up (hot: 8 arenas,
~178 live targets, 136 MB backing; Metal packs several targets per heap).
Slot cap raised 8 → 16 during the gate (budget unchanged). Not a fix for the
luneta's continuous GPU cost; the ≥20% residency-commit perf gate is not
formally closed and an anomalous off-run is not used as quantitative proof,
so the flag stays default off. Extra margin before flicker observed, not
claimed as a flicker cure.

## Present worker FIFO — two VFR-on gameplay runs (2026-11-18)

Player observation on the same route and gameplay: frame pacing felt almost
perfect, with a flat frame-time line. The player also reported that the
flickering textures in the scope (luneta) UI, a recurring glitch on the stock
path when using the graphics- and texture-heavy scope overlay, became nearly
absent with VFR and the present worker active.

Both runs used `SWX_VFR=relaxed` and `SWX_VFR_HZ=auto` on a 60 Hz link with
a 2560×1440 drawable. Each logged 2,700 presents in 45 s and an on-screen
p99 of 16.67 ms in every window. Late intervals and discarded presents were
3 / 6 in run 1 and 2 / 5 in run 2, all by the frame-900 window. Both runs
logged zero empty link ticks, zero merged frames and zero `[RED]` events.

The flicker observation is qualitative. These logs do not count flicker or
show how many frames were ready in the FIFO at each refresh.

The available VFR-off run used the same 2560×1440 drawable. Both VFR entries
were absent from `env.txt`, and boot selected the stock 60 fps path. It logged
5,400 presents, with an on-screen p99 of 16.67 ms in every window, three late
intervals, five discarded frames and no `[RED]`. All late and discarded counts
were in the first 2,700 presents. In the common gameplay windows through frame
2,700, the per-window median enqueue-to-screen time was 59.4–59.8 ms stock and
60.5–61.1 ms with the worker. This comparison shows similar display cadence at
60 Hz. Visual flicker and the comparison with the previous VFR build remain open.

The second stock run also used a 2560×1440 drawable and logged 4,800
presents. The player reported very good frame pacing, two noticeable
hitches and a few episodes of scope UI texture flicker. The log showed an
on-screen p99 of 16.67 ms in every window, four late intervals and five
discarded presents by frame 1,500, zero `[RED]`, and no `[longframe]`
entries. Enqueue-to-screen p50 rose from 59.8 ms at ticket 1,506 to
76.3 ms at ticket 1,806, and remained between 76.1 and 76.6 ms through the
end. That is roughly one additional refresh of latency during otherwise
steady presentation. The logs do not identify the exact moments of the
two hitches or the flicker.

### Extra VFR-on run and Gate 1 decision (2026-11-18)

The extra `relaxed/auto` run logged 4,500 presents in 75 s at 60/60 and
2560×1440. On-screen p99 was 16.67 ms in all 15 windows, with zero empty
link ticks, zero merges and zero `[RED]`. All on-screen drops and discards
were in the windows through frame 1,200. The sole `[longframe]` was 24.6 ms
at startup. The player reported one micro-hitch and one episode of scope UI
flicker. From frame 1,500 onward, enqueue-to-screen p50 stayed at
77.4–78.0 ms, about one refresh higher than the earlier VFR-on runs over
the shared windows, while the screen cadence stayed steady.

The player also reports that under sustained drops, stock can settle at
30 fps on a 60 Hz panel and take time to recover. Both archived stock
runs from 2026-11-17 show this fallback at startup, entering the two-vsync
hold at present 12 and returning to one vsync at present 43. Neither of
the two stock runs on 2026-11-18 entered that hold.

Gate 1 is accepted for advancing to Stage 2 based on the reproducible
60/60 result and player approval. Full protocol validation remains open,
including the matched previous VFR baseline, an isolated producer stall
with one ready frame in reserve, and display-link pause and recovery.

## Present queue — corrected Stage 2, first 60/60 run (2026-11-18)

`SWX_VFR=relaxed`, `SWX_VFR_HZ=auto`, 60 Hz display-link clock,
2560×1440 drawable and four physical snapshot slots. The host reported
18,900 game presents over 315 s. After the first VFR report, each batch of
300 game presents took 4.970–5.030 s of wall time. In the first 4,500 link
ticks the fixed build dropped 3 oldest ready frames, versus 2,684 before
the cadence fix. Across all 18,900 reported link ticks the fixed build
recorded 7 oldest drops, 7 eligible empty ticks, 18,893 ready pops, zero
credits applied and zero intentional holds. Five eligible empty ticks
occurred after tick 900, across three windows.

All 63 on-screen windows had p99 of 16.67 ms. There were no `[RED]` events.
Excluding windows through game present 2,100, the VFR time deltas sum to
280.016 s of game time and 280.017 s of wall time. Per-window ready-to-pop
p95 ranged from 20.194 to 32.464 ms. The median of the per-window
enqueue-to-screen p50 values was 60.5 ms, compared with 54.2 ms in the
uncorrected run. These are separate latency metrics.

The 60/60 production-rate and oldest-drop checks pass in this capture.
The full Gate 2 remains open, including attribution of the isolated empty
ticks and coverage of the other refresh/rate and recovery cases. Preserved
capture: `/tmp/swx-vfr-gameplay-20261118/stage2-fixed-on-115748.log`.

The second `relaxed/auto` 60/60 run used the same drawable and four slots.
It logged 9,000 game presents over 150 s, with 5.000–5.017 s per complete
300-present window after the first report. It recorded two oldest drops,
zero applied credits, one intentional hold and two eligible empty ticks.
Both empty ticks were in the window ending at tick 7,200, which also
contains three consecutive long frames (GPU 20.0, 24.0 and 16.9 ms).
The window-level log cannot assign individual empty ticks to those frames.
All 30 on-screen windows had p99 of 16.67 ms, and no `[RED]` was logged.
After present 2,100 the game and wall deltas both sum to 115.017 s.
The 60/60 cadence fix is reproducible across two runs. The strict
zero-eligible-empty check and the full Gate 2 remain open. Preserved
capture: `/tmp/swx-vfr-gameplay-20261118/stage2-fixed-on-120211.log`.

### New save — engine cutscenes and 30 fps movie recovery (2026-11-18)

In one long `relaxed/auto` session on a 60 Hz link, the player exercised
engine-rendered cutscenes and video playback, reported satisfactory behavior,
and accepted this single pass for advancing. The trace has no markers for
individual engine cutscenes. It confirms that the VP9 movie switched the
target from 60 to 30 fps at present 32,165. At a steady 30/60, each
300-refresh window had 150 ready pops, 150 intentional holds, zero eligible
empty ticks and zero oldest drops. On-screen p99 was 33.33 ms during playback.
After the decoder stopped, the target returned to 60 fps at present 33,377.
The transition window had zero eligible empty ticks and zero oldest drops;
the next full 60 fps on-screen window returned to 16.67 ms p99. The host
exited normally. The player attributed the earlier audio callback timeout
to switching from AirPods to speakers. This capture verifies playback recovery, while
display-link silence recovery and other untested rates remain separate cases.
Preserved capture: `/tmp/swx-vfr-gameplay-20261118/stage2-fixed-new-save-121839.log`.

The player felt one hitch. Frame 3,085 (54.33 s) is the strongest matching
texture burst: 146 first uploads, 28.5 MB and 3.7 ms of upload time in a
36.9 ms frame (33.3 ms submit+present, 8.5 ms GPU). Its reporting window
contains the only eligible empty tick and oldest drop in the capture.
Window aggregation prevents exact attribution to that frame. An earlier
56.7 MB burst at frame 463 had zero eligible empty ticks and no on-screen
losses in its reporting window.

## Texture bursts — 2×2 staging pool and bind cache (2026-11-18)

Nine captures at 2560×1440, VFR `relaxed/auto` on a 60 Hz link and
`DM_NVN_WAITS=1`. No per-frame timing TSV was enabled. Counts below cover
link ticks through 3,900, common to all captures. Burst comparisons use
reported first-upload count and volume as matching proxies. Mip/format and
per-batch work equivalence were not captured.

| Arm, run | First uploads / MB | Whole frame / submit+present / upload scope (ms) | Eligible empty / dropped oldest |
|---|---:|---:|---:|
| Baseline 1 | 188 / 27.7 | 45.3 / 39.6 / 4.3 | 2 / 2 |
| Baseline 2 | 215 / 39.0 | 46.9 / 44.2 / 5.1 | 3 / 2 |
| Pool only 1 | 220 / 39.8 | 43.3 / 38.5 / 4.5 | 1 / 1 |
| Pool only 2 | 215 / 39.0 | 41.2 / 34.4 / 4.6 | 1 / 1 |
| Bind only 1 | 181 / 27.5 | 39.8 / 34.8 / 3.7 | 1 / 1 |
| Bind only 2 | 188 / 27.7 | 38.6 / 35.0 / 3.6 | 1 / 1 |
| Combined 1 | 188 / 27.7 | 35.9 / 32.1 / 3.6 | 17 / 2 |
| Combined 2 | 188 / 27.7 | 44.2 / 39.6 / 4.0 | 1 / 2 |
| Combined 3 | 188 / 27.7 | 35.9 / 33.9 / 3.9 | 8 / 2 |

The pool materialized two 32 MiB blocks (64 MiB resident) and reported zero
misses in all five pool-enabled captures. All nine hosts exited normally
with no `[RED]`.
The final resize in bind-only 1 followed the last included window. Combined
1 had 15 eligible empty ticks across two post-burst windows. Both windows
had 33.33 ms on-screen p99 and newest-fence SyncWait of 3.56 and 6.75 ms per
frame, respectively. Upload-slot blocking was 0.000 ms per frame in both
windows. These aggregates show a contemporaneous wait, with the cause of
the difference between runs still unresolved.

The most closely matched count-and-volume pair is Baseline 2 versus Pool only
2 (215 first uploads / 39.0 MB each): whole frame 46.9 vs 41.2 ms,
submit+present 44.2 vs 34.4 ms, and upload scope 5.1 vs 4.6 ms. This is one
pair, with other runs varying in burst size and metrics; it is suggestive,
not causal proof or a broad performance claim. The scoped upload timer
accounts for only 3.6–5.1 ms across these captures, and CPU dispatch/setup
remains unattributed.

Rollout decision: enable the repaired pool in the managed BOTW installation
environment (`SWX_GPU_TEXTURE_POOL=1`) after two clean pool-only gameplay
runs, both with zero misses and 64 MiB resident. This is scoped to that
installation and does not change the core compiled default. Keep
`SWX_GPU_BIND_ONCE` experimental, compiled default off, and absent from the
managed environment; it remains explicitly available for A/B.
The combined pool+bind configuration is not promoted: the player reported
hitches in runs 1 and 3 of 3, while run 2 did not reproduce them. Logs show
recurrent eligible-empty clusters in runs 1 and 3, but do not isolate bind
cache as the cause. Do not describe the combined package as successful.
Grouped-dispatch remains a separate follow-up whose entry criterion is open.
Captures are saved under `.swx-history/ab-texture-burst-20261118-*` in the
BOTW installation directory.

### Combined run 3 — presentation regression reproduced

The player reported visible hitches in combined runs 1 and 3, with no
similar episode in run 2. Run 3 logged 188 uploads / 27.7 MB at frame 739
(35.9 ms whole, 33.9 ms submit+present, 3.9 ms upload scope). Of its eight
eligible empty ticks, seven were in the window ending at tick 2,700, after
the burst. That window had 33.33 ms on-screen p99 and 2.991 ms/frame of
newest-fence SyncWait, with zero measured upload-slot blocking. Combined
run 1 had ten eligible empty ticks in the same numbered window. Runs with
either switch alone had zero eligible empty ticks in that window. Last
pool report for run 3: 237 reuses, two cold allocations, zero misses.

The tested configuration with both switches enabled remains unsuitable for
promotion. The repeated pattern warrants auditing the bind cache and its
interaction with the pool. It does not isolate the bind cache as the cause.
Capture: `ab-texture-burst-20261118-combined-03` in the installation history.

### Instrumented bursts on entry to the same area (2026-11-18)

The player identified the same area in attribution runs 1 and 2. Run 2
started closer to that area. Both used the promoted pool configuration and
timing TSV. The saved captures contain 554 and 159 fully joined upload
batches, respectively, with no overflow or upload-slot waits. Run 2 reused
the live TSV path; it was matched to the 55.139 ms row at frame 692 and
archived separately from run 1.

| Metric | Run 1, frame 3,351 | Run 2, frame 692 |
|---|---:|---:|
| First uploads / reported MB | 134 / 28.3 | 277 / 40.3 |
| Whole frame / submit+present (ms) | 42.9 / 41.1 | 55.1 / 51.3 |
| Submit-thread CPU / rendering GPU (ms) | 37.2 / 5.1 | 48.9 / 5.6 |
| Draws / upload dispatches / blit records | 2,319 / 3,602 / 3,670 | 3,139 / 4,718 / 4,786 |
| Deswizzle encode / flush (ms, each summed across matching batches) | 1.54 / 4.11 | 2.21 / 5.27 |

The larger burst includes more texture operations and more draws. CPU time
grows alongside both, so this establishes the CPU-side bottleneck without
isolating the cost of recording individual uploads. Flush is the largest
upload bucket in both submissions, but it explains only a small share of
submit time. In a separate run-1 burst, 11 uploads totaling 56.7 MB took
30.1 ms submit+present, and source preparation was the largest upload
bucket (8.03 ms). Run 2 logged two eligible empty ticks in the burst window
and nine in two later windows. The TSV gives frame 692 a 16.667 ms presented
interval; the empty ticks describe its surrounding window.

## Upload blit recorder — same-build A/B (2026-11-18)

Both arms used the same installed build with `DM_NVN_ENC_THREAD=1`,
`SWX_VFR=relaxed`, `SWX_VFR_HZ=auto`, and
`SWX_GPU_TEXTURE_POOL=1`; only `SWX_NVN_UP_BLIT_REC` changed. The OFF
measurements below were verified in the earlier A/B review, but their source
logs are no longer available in the current launcher log rotation. They are
therefore recorded as verified results, not represented as newly archived
captures. ON logs are preserved under the BOTW install's
`.swx-history/ab-up-blit-rec-on-01` and `ab-up-blit-rec-on-02`; each includes
the original log, parser report, and provenance derived from launcher lines.
No current `env.txt` snapshot was taken while the environment was being
restored in parallel.

| Arm / run | uploads / MB | whole / submit+present (ms) | on-screen p99 | User observation |
|---|---:|---:|---:|---|
| OFF 1 | 127 / 28.4 | 29.5 / 22.7 | 16.67 ms in all windows | Control |
| OFF 2 | 127 / 28.4 | 23.8 / 19.7 | 16.67 ms in all windows | Control |
| ON 1, frame 623 | 149 / 39.6 | 30.2 / 17.5 | 16.67 ms in all windows | Seemed steadier; luneta texture flicker not observed |
| ON 2, frame 584 | 137 / 29.3 | 25.2 / 12.4 | 16.67 ms in all windows | One spike; luneta texture flicker returned |

The recorder reduced submit+present time in both ON bursts, including the
closely matched OFF 1 / ON 2 upload volumes. Whole-frame time did not improve
consistently: ON 1 was slower than either OFF run, while ON 2 was close to OFF
2. The perceived hitch and luneta result also did not repeat consistently,
and every arm retained 16.67 ms on-screen p99. This is evidence that the
recorder can shorten the measured submit path, but not that it reliably
improves the end-to-end hitch or presentation experience.

Gate 1 (correctness / launch with recorder enabled): **PASS**. Gate 2
(promotion for burst hitch reduction): **NOT PASSED**; keep
`SWX_NVN_UP_BLIT_REC` experimental and off by default. Patch #17 remains
available for further work, but these runs do not justify promotion. Capture
provenance is per-run launcher output; the OFF source logs were unavailable
to re-archive, and no env snapshot is claimed for those runs.

### Post-A/B run with the restored environment (2026-11-18)

The player reported no visible texture glitches or spikes. The launcher
confirmed effective `DM_NVN_ENC_THREAD=0`, the blit recorder opt-in absent,
and `SWX_GPU_TEXTURE_POOL=1`. The host exited normally with no `[RED]`.
It reported 3,600 game presents in 60 s, with 16.67 ms on-screen p99 in all
12 windows and one eligible empty tick in the initial 300-refresh window.
Frame 595 logged 137 first uploads / 29.3 MB, 27.2 ms whole frame and
24.5 ms submit+present, without an eligible empty tick in its window. The
counts and volume match ON 2, which logged 25.2 / 12.4 ms while the player
reported a hitch and flicker. This follow-up changes the recorder setting
as well as the prototype setting and does not isolate either one's effect.
Preserved capture: `/tmp/swx-texture-burst-20261118/post-blit-ab-170259.log`.

## Save cleanup worker — single-install A/B (2026-11-18)

One BOTW install, same save/route, `SWX_SAVE_ASYNC=0` (run 2, six commits)
vs `SWX_SAVE_ASYNC=1` (two commits of one write). Log-verified: commit #1
clone+swap 5525 µs, commit #6 5965 µs with the flag off (sync cleanup in the
measured path); 1100–1180 µs per commit with the flag on. Sync-path saving
≈ 4–5 ms per commit. Player-reported numbers for the remaining commits fall
in the same ranges.

The last save's window (tick 5700, flag off) logged 2 eligible empty
refreshes and one 74.6 ms frame with 0.4 ms GPU, recorded before the commit
and with no upload/GPU footprint linking it to the commit. The first five
commits' windows had zero empty refreshes. Verdict: keep the async cleanup
on (worker promoted, default on); the isolated hitch's cause is undetermined
and shows no link to the commit path in this log.
