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
Recipe adopted as install defaults: `DM_NVN_SYNC_LAG=1` + `DM_PRESENT_SNAP=4`
pinned by `wrapper/main.py`; `=0` / `snap 2` restores stock for A/B.

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
promise. The prototype stays env-gated, default-pinned, not promoted to a
profile key.

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
