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
only sub-detector ripples). (Key since renamed `SWX_SAVE_ASYNC=1`,
`DM_SAVE_ASYNC` kept as legacy alias; see the save cleanup worker A/B below.)

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

## BOTW render scale — macOS gameplay validation (2026-11-18)

Patch #19 raises the `DM_RENDER_SCALE` ceiling to `1.2` on the supported
BOTW 1.9.0 build. The player approved promotion as an opt-in, with `1.0`
as the launcher and host default. A short `1.0` run confirmed normal
launch and gameplay on the candidate. Prior `1.0` measurements serve as
historical context, and no new matched-route long run was collected.

| Scale | Measured refreshes | Eligible empty refreshes | On-screen p99 per window | Player observation |
|---|---:|---:|---|---|
| `1.1` | 30,900 | 2 | 16.67 ms in 103/103 | No felt hitches or visual glitches |
| `1.2` run 1 | 12,000 | 3 | 16.67 ms in 40/40 | Three felt hitches |
| `1.2` run 2 | 18,300 | 5 | 16.67 ms in 61/61 | Nearly hitch-free through teleports and combat, with one suspected upload burst |

The launcher and host confirmed the selected factors. The upload staging
pool reported zero misses in both `1.2` runs. The logs do not record the
dimensions of the scene render targets, so this acceptance covers launch,
playability and observed presentation cadence. A controlled performance
delta against `1.0` and the exact internal dimensions remain unverified.

## BOTW disable-DRS flag — compatibility gate (2026-11-19)

Patch #20 adds `SWX_DISABLE_DRS`: with the flag set, both render-scale
stubs take the apply path unconditionally, fixing the 3D views at
`DM_RENDER_SCALE` — including `1.0` (1600×900 docked). Flag off keeps the
game's dynamic resolution. Experimental opt-in, default off, never pinned
by the installer (armed per run via `ab_test.py set disable-drs` or the
launcher toggle, gated by the `RENDER_SCALE_DRS` fixups capability).

M4 Pro, same route, `DM_RENDER_SCALE=1.0`, `DM_NVN_PASSDUMP=1200`:
- Control (`SWX_DISABLE_DRS=0`): scene RTs at 1600×900 in the F1200 dump
  (P 117776 c0/c1/c3/c5); GPU p50 7.01 / p95 8.77 / max 12.85 ms over
  frames 600–900. Nothing near the ~29 ms average the game needs before
  lowering scale — this route cannot demonstrate the override under load
  on this machine.
- Flag on (`SWX_DISABLE_DRS=1`): loader logs
  `3D views fixed at DM_RENDER_SCALE 1.00 (SWX_DISABLE_DRS)`; scene RTs
  at 1600×900 in the F1200 dump (49 P-lines at 1600×900); GPU max
  11.18 ms (frames 600–900), 9.29 ms (900–1200). No cadence regression
  vs control; HUD and luneta clean (player-reported).
- `DM_RENDER_SCALE=1.2` with the flag on still renders the scene at
  1920×1080 (player-confirmed; that run's log rotated away).

Verdict: compatibility approved — the flag is inert on unloaded scenes
at 1.0 and preserves 1.2. Preventing a real DRS dip stays unproven in
gameplay; revisit when a load or a machine makes the control dip. Player
env keeps `SWX_DISABLE_DRS=1`; the installer default stays off.

## BOTW native 1080p bypass — Gates 2 and 3 (2026-11-19)

Patch #21 adds `SWX_RES_BYPASS=1080p`: the scene stub produces a 1920
render base (site `main+0x12660c8`), the frontend confirms a 1920x1080
drawable on a fitting screen and only then forces `+0x10 = 1.0f` /
`+0x14 = 1`, before the guest thread. Experimental opt-in, default
absent, never pinned by the installer.

Summary of the three gates collected:

| Gate | What was checked | Result |
|---|---|---|
| 1 — Installation | `install.sh` end-to-end, incremental migrate + idempotent 2nd apply, `main` fixups regenerated, build green, default env absent | PASS (rewrite ok, `# SWX_NATIVE_1080P 1` in fixups, `env.txt` without `SWX_RES_BYPASS`) |
| 2 — Real resolution, compatibility | 1080p active at `DM_RENDER_SCALE=1.0`, docked, drawable confirmed | PASS (bypass active at scale 2 = 960×540 pt, drawable 1920×1080, p99 16.67 ms, 0 dropped, no `[RED]`) |
| 3 — Precedence + 1:1 present | `0.95` + DRS + `metalfx` with the preset; PASSDUMP | PASS (scene 1920×1080 despite 0.95, canvas/snapshot/drawable 1920×1080, zero MetalFX-upscaled frames, no `[RED]`) |

Bottom line: the bypass activates only when the drawable is exactly
1920×1080, overrides `DM_RENDER_SCALE`/`SWX_DISABLE_DRS`, and presents
1:1 (MetalFX refused at equal sizes) with stock cadence. Handheld,
incompatible-build and small-screen fallbacks are not gameplay-tested;
they fall back to the stock path with a warning by construction.

Gate 2 — compatibility (`SWX_RES_BYPASS=1080p`, `SWX_DISABLE_DRS=1`,
`DM_RENDER_SCALE=1.0`, `.prev.log`): loader `3D views fixed at
DM_RENDER_SCALE 1.00`, frontend `1080p bypass active: content 960x540 pt
on scale 2, drawable 1920x1080`. Frames 1200–1500: p99 16.67 ms
on-screen, 0 dropped, GPU max 10.80 ms. No `[RED]`. Approved.

Gate 3 — precedence and 1:1 present (`SWX_RES_BYPASS=1080p`,
`SWX_DISABLE_DRS=1`, `DM_RENDER_SCALE=0.95`,
`DM_PRESENT_UPSCALE=metalfx`, `DM_NVN_PASSDUMP=1200`, current `.log`):
loader `rendered at 0.95` + `fixed at 0.95`; frontend bypass active as
above. F1200 dump: scene attachments at **1920x1080** (42 P-lines),
half-res at 960x540; canvas/swapchain 1920x1080, VFR snapshot ring
1920x1080, `picture shape 1920x1080`. MetalFX armed but every present
went `through the bilinear pass` — zero upscaled frames, as `fx_wanted`
refuses equal sizes. Frames 900–1200: p99 16.67 ms, 0 dropped; one
108 ms frame at 1500 with GPU max 8.90 ms (load/teleport, not render
cost). Zero `[RED]`.
Precedence proven: with 0.95 in env, the scene stays 1920 (not 1824 or
1520). Open: player-observable HUD/luneta/teleport/combat pass and the
fallback runs (handheld, incompatible build, small screen).

### Fullscreen-capable v2 revalidation (2026-11-19)

Rebuild, packaging and signing completed. The installed host contains the
v2 fullscreen path and the installer preserved `SWX_RES_BYPASS=1080p`.
The player reported clean gameplay in windowed and fullscreen sessions,
with `DM_RENDER_SCALE` at `1.0` and `1.2`, and with MetalFX enabled and
absent. The two retained logs both have `DM_RENDER_SCALE=1.2` and MetalFX
enabled. The other settings combinations are player-reported.

| Retained session | Evidence |
|---|---|
| Fullscreen boot | Internal resolution and VFR snapshots at 1920×1080. Drawable at 3840×2160. MetalFX created a 1920×1080 → 3840×2160 scaler and ran for displayed frames. Normal exit. |
| Window boot and transition | Starts with a 1920×1080 drawable. During the reported maximize interaction, the recorded drawable was 1920×1144, which keeps the fitted 16:9 viewport at 1920×1080. The presenter used its standard pass and the drawable returned to 1920×1080. Normal exit. |

The current behavior of centering the picture when maximizing the window
is accepted for this release. Making maximize behave like fullscreen is
deferred. No new `PASSDUMP` or matched fullscreen capture with MetalFX
disabled was retained for v2.

Local captures: `/tmp/swx-native-1080p-gates.tyffPJ/fullscreen-metalfx.log`
and `/tmp/swx-native-1080p-gates.tyffPJ/window-and-transition.log`.

## BOTW native 1440p scene — Gate 2 (2B approved 2026-11-19, 2C partial, Gate 3 open)

Patch #23 flips the docked stub's 1440p path from `{2560.0f, 1920.0f}` to
`{2560.0f, 2560.0f}` (stage 2: native 1440p scene), names the effective
preset in the frontend activation log, offers `1440p` in the launcher on
macOS builds declaring `SWX_NATIVE_1440P`, and teaches the harness
`ab_test set res-bypass 1440p` (`on` stays `1080p`, `off` is `0`).
Heaps, pool headroom (+512 MiB) and the reserve-before-publish contract
are unchanged from #22. Gate 2 measures correctness and regressions;
sustaining 60 fps is Gate 3.

| Gate | Execution | Approval |
|---|---|---|
| 2A · Installation | fixture stacks (DRS-only, 1080p, installed stage 1) re-applied; real `main` rewritten; host compiled and linked | same marker on all three routes; 2nd apply idempotent; `main.fixups.tsv` declares `SWX_NATIVE_1440P=1`; launcher offers 1440p only on macOS with the capability; reinstall preserves an explicit choice, fresh install leaves the key absent |
| 2B · Native 1440p + precedence | same save, windowed boot, `SWX_RES_BYPASS=1440p`, `DM_FULLSCREEN=0`, `DM_RENDER_SCALE=0.95`, `SWX_DISABLE_DRS=0`, `DM_PRESENT_UPSCALE=metalfx`; 1–3 gameplay frames via `DM_CTL` `passdump 3`; menu, spyglass, teleport, quit | main color/depth attachments at **2560×1440** despite conflicting flags; canvas, swapchain, crop, snapshots and drawable at **2560×1440**; 3632 MiB heap; image and HUD intact; zero `[RED]`; exit 0; MetalFX armed, no scaler for the 1:1 present |
| 2C · Regressions + fallback | same binary: `1080p` smoke, preset `0`, `1440p` with `DM_OPMODE=0`, `1440p` with `DM_FULLSCREEN=1`; scale 1.0 on the controls | `1080p` keeps scene/swapchain **1920×1080**; `0` keeps stock docked sizes; handheld refuses before the extra reserve; fullscreen keeps scene/swapchain **2560×1440**, logs the real drawable, returns to the fixed 1440p window on exit |

Prerequisite (Gate 1 evidence): capture a short gameplay `PASSDUMP` at
1920×1080 on the stage-1 build before replacing it — main color/depth
targets at 1920×1080 are the missing direct evidence.

### Gate 2B run (fullscreen, approved 2026-11-19)

Deviation from the protocol above: the run booted fullscreen, not
windowed, with `DM_PRESENT_UPSCALE=metalfx`. Swapchain, crop and VFR
snapshots at **2560×1440**; MetalFX created a 2560×1440 → 3840×2160
scaler for the drawable and ran for displayed frames; total present cost
with MetalFX 1.23–1.26 ms p95. VFR: average present 16.67 ms over
255.0 s sampled, 0 of last 300 dropped. Zero `[RED]`, exit 0.
2B approved on this evidence; the windowed leg was not run.

### Remainder (deferred, not regressions)

2C: fullscreen smoke PASS (same binary). Deferred to a later round:
return to the fixed 1440p window on exit, the `1080p` / `0` / handheld
(`DM_OPMODE=0`) controls. Gate 3: formal 1080p-vs-1440p A/B still open
(the run above sustained 16.67 ms mean present, not a comparison).

### Gate 1 — stage-1 output and memory (approved 2026-11-19)

`SWX_RES_BYPASS=1440p` on the stage-1 build (scene held at 1920×1080):
canvas, swapchain, crop, VFR snapshots and drawable at 2560×1440, pool
3797 MiB, `SetHeapSize` 3632 MiB accepted, zero allocation errors, normal
exit. `DM_CTL` `passdump 3` on frame 1421, pass 143733: four color targets
and one depth target at **1920×1080** — the missing direct scene evidence.
Capture: `/tmp/swx-1440p-gate1.bc7EkF/stage1-passdump.log:3058`.

## BOTW native 4K — approved functional (2026-11-19)

Patch #24 adds `SWX_RES_BYPASS=4k` (mode 3, `SWX_NATIVE_4K`): docked stub
emits `{3840.0f, 3840.0f}`, canvas lows / TV / viewport take 3840, integer
heights take 2160 (`0xf2c10e09`/`0xf2c10e08`, viewport `0x52810e04`), heaps
display 112 / GPU 448 / memory 4080 / RootHeap 4048 MiB (+960 MiB heap/pool
→ 4288/4245 MiB). Mode 3 is split from mode 2 by `tbz` bit tests (no flag
touch); one stub per address. Windowless fallback logs without writing the
`+0xB4` slot (the loader already zeroes it on capable builds).

| Gate | Execution | Approval |
|---|---|---|
| 1 · Installation | `#23` → `#24` live migrate + `--force` 0→24 + idempotent reapply; `main` rewritten; host compiled/linked | new tip `991ae800f1cb6b3c`, prefix-23 `8ba3d581d4b32ad6` recomputed, sentinels 17/17 (`0xf2c10e09/08` lock the 2160 heights), `test_launcher_settings` 17 ok, `clang -fsyntax-only` clean (pre-existing `activity` warning only), `main.fixups.tsv` declares `SWX_NATIVE_4K=1`, launcher offers 4k only on macOS with the capability, harness `4k/1440p/1080p/on/off/clear` + invalid rejected |
| 2 · 4K native | fullscreen drawable 3840×2160, `SWX_RES_BYPASS=4k`, `DM_RENDER_SCALE=0.95`, `SWX_DISABLE_DRS=0`, `DM_PRESENT_UPSCALE=metalfx`; save load, `passdump 3`, spyglass, teleport | `4k bypass active: internal 3840x2160`, guest AS heap 4.19 GB / pool 4.15 GB, heap 3.98 GB accepted, swapchain first 3840×2160, drawable 3840×2160, snapshots/crop 3840×2160, frame 1150 pass 107186: 4 color + 1 depth at **3840×2160**, viewport 0,0,3840,2160, 1:1 present through the bilinear pass (MetalFX armed, no scaler), zero `[RED]`. Capture: `/tmp/swx-native-4k-gate.4c3vLG/4k-passdump.log:2358` |
| 3 · Short regression | same binary: `1440p` fullscreen + MetalFX | `1440p bypass active: internal 2560x1440`, heap 3.55 GB (3632 MiB) / pool 3.71 GB (3797 MiB), swapchain/crop/snapshots 2560×1440, MetalFX scaler 2560×1440 → 3840×2160, exit 0, zero `[RED]`. Current rotation log: `/Users/morissonmaciel/Library/Logs/nxnative/01007EF00011E000.log:215` |

Performance note (not a gate): the first 4K run shows GPU near or above
16.67 ms in heavy stretches (frames to 1200: GPU p50 14.66 / p95 16.18 /
max 18.63 ms; to 1500: p50 14.35 / p95 16.19 / max 17.29 ms), consistent
with the MNG limit the reviewer noted. A 1440p-vs-4K performance
comparison would need the same route and configuration. 4K development
ends here with no sustained-60 fps certification.

## BOTW Graphics section + render-scale select — Gate 2 (validated 2026-11-19)

Patch #25 Etapa 2 keeps the #25 host behavior and reorganizes the Mac
window: a single **Graphics** section right after **Game** with Display
mode → Native resolution → Frame rate → Render scale → Upscaling → Fixed
internal resolution → Picture shape, every row basic, Console mode staying
in Game. Render scale on macOS is a percent select (50/75/100/110/120 %
writing `0.5`–`1.2`) filtered by the stub ceiling (Breath of the Wild
offers all five; a ceiling-`1.0` build offers up to 100 %). A saved `1.00`
reads as `1.0`, an off-list saved choice falls back to 100 %, and an
env-fixed `0.95` still applies and shows **95 %** on its greyed-out row.
The iPhone keeps the slider and its sections. Code gates green
(`test_launcher_settings` 19, Swift launcher storage check, `clang
-fsyntax-only`, installer `--force` + idempotent); the window layout,
`120%` arriving as `1.2`, the locked `95 %` row, the ceiling-`1.0`
omission of 110/120 % and the unchanged iPhone catalog confirmed on
the gameplay run. Review corrections (validated on the corrected tip
`b127aa64e21e45ac`): the locked percent uses `Int(exactly:)` so a
gigantic env-fixed value keeps the picker's control instead of
trapping, and saved-choice matching uses exact numeric equality on
both the Swift and the no-window boot paths. A `v3` compat patch +
migration covers installs of the first Graphics tip (`304324774d5b604a`).

## BOTW keep dynamic resolution under the preset — gameplay gate (validated 2026-11-19)

Patch #26 (`swx_botw_native_keep_drs.patch`) separates the preset from
the DRS policy: `bp_force_internal()` publishes only the base (`+0x10 =
1.0f`, `+0xB4 = mode`, reservation check kept) and no longer writes
`+0x14`, which stays under the loader's `SWX_DISABLE_DRS` handling (`0`
keeps the game's dynamic resolution around the new base, `1` fixes the
scene for A/B control). `DM_RENDER_SCALE` stays ignored under the
preset; heaps, HUD, VFR snapshots and present keep using the preset
size. Catalog and player docs now call the preset the base and point to
Fixed internal resolution for the reduction policy. No new flag, no
ARM64 stub change. Code gates green (sentinels + forward/reverse
round-trip identical, `sh -n`, `#25→#26` migration `b127aa64e21e45ac` →
`2ada8887b947ac31` verified on an isolated core copy, full installer run
with the pairs overlay: prewarm `17,818` rows → `13` files, `787.7 MB`).
Gameplay (player report, same intense-combat region as the earlier runs,
`SWX_NATIVE_RES=1440p` with `SWX_DISABLE_DRS=0`): holds stable 60 fps.

## BOTW FidelityFX CAS sharpen-only — gates (patch #27, validated 2026-11-20)

`swx_botw_fxcas.patch` + `docs/fxcas.md`: opt-in `SWX_FXCAS=1` (absent/`0`
off). Before the composite draw (fs `bd7ffdd435c6c3bc` + vs
`5e9f57168d89a543`, fullscreen triangle into a single RGB10A2Unorm 2D
canvas, same-format scene, full viewport), the scene runs through AMD CAS
sharpen-only (fixed 0.25, `dm/gfx/msl/fxcas.metal`) into a private
same-format scratch; only that draw's scene binding is swapped (one-shot
in `encode_args_`, cache invalidated on entry/exit). Code review: APPROVE
with two MEDIUM fast-follows applied (registry `g_rmu` lock, arm scoped to
the arming draw); format contract corrected to RGB10A2Unorm 10-bit after
the first build proved `RGBA16Float` never matches (`fmt=90`).

Installation: stack 26→27 reapplies idempotently (`patch_nxnative.sh`
exit 0, marker `7203f912…`), host compiles/links, `vs_cas`/`fs_cas` in the
MSL header; `SWX_FXCAS=0` disables by construction (zero passes).

Image/order (PASSDUMP F1953–1955, 1440p, `SWX_FXCAS=1`): exactly one
`B … st1 fxcas scene ser=57277 sharpened` per frame on the confirmed
anchor (same canvas `ser=2792` fmt=90 2560×1440, scene 2560×1440 swizzle
identity, blend off); HUD follows on the same canvas with unchanged
shaders; zero `[RED]`. Player visual: 3D sharper, no hitch felt. Long
run (25,200+ frames): exit 0, zero `[RED]`, hitch pattern unchanged
(boot/uploads/pipeline compiles only).

Cost/compat (formal A/B p95 ≤ +0.5 ms, 1080p/2160p smoke + recorder):
open — indirect evidence only (present-path GPU p50/p95 unchanged).
