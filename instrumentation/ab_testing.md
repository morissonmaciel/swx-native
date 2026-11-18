# A/B testing protocol (real gameplay runs)

The user plays a fixed route; `instrumentation/ab_test.py` manages player-env
overrides and snapshots each finished run before launcher log rotation can
lose it. Analysis via `instrumentation/log_report.py`; gates in the plan discussion.

## Fixed variables (do not change mid A/B)

- Same save, same start point, same route (the area-entry that produces the
  ~250-first-upload burst, e.g. the frames 612/622 region).
- Same window/drawable across compared runs: the as-is window, which the
  log reports as drawable 2560x1440 with 1920x1080 snapshots (measured
  2026-11-16 12:31 run; the user does not control the window size — just
  keep the same non-fullscreen window setup every run). The older
  `ab-split-on-baseline` snapshot is 5120x2880 and stays as a 5K reference
  only.
- Game Mode on, nothing else heavy running, one run per condition
  (two if the numbers come back ambiguous).

## Commands

```
python3 instrumentation/ab_test.py status
python3 instrumentation/ab_test.py set keepalive|snap|save|pso|pool|bind_once|vfr|yield|residency-ub-ring|rt-heap-reserve on|off|clear
python3 instrumentation/ab_test.py set vfr uncapped
python3 instrumentation/ab_test.py set vfr-hz auto|30..240|clear
python3 instrumentation/ab_test.py collect <label> [--install-dir I] [--log PATH]
python3 instrumentation/ab_test.py compare <labelA> <labelB> [--install-dir I]
```

`on` writes an explicit value (`=1`, except `snap 4` and `vfr relaxed`,
visible as `launcher: env K=V` at boot); `off` writes `=0` (forces stock
behavior, except `snap 2`); `clear` removes the line so the
compiled default applies. New installs default to the VFR 60 fps lock
(`SWX_VFR=relaxed` at auto Hz). The snapshot ring is not pinned, so the
compiled default (stock ring) applies unless set for A/B
(`ab_results.md` keeps the stock-baseline numbers for comparison).
`snap 4` is an optional smoothness experiment
(Gate 3a recipe, preserved in `ab_results.md`), not the default.
Keep-alive on on macOS (install default `SWX_GPU_KEEPALIVE=1` with
`SWX_GPU_KEEPALIVE_YIELD=1`; the texture-upload count split was removed after its
Gate 1 showed +13 ms; numbers preserved in `instrumentation/ab_results.md`).
`set yield off` restores the old run-to-end spinner for A/B within keepalive=1.
`collect` snapshots the finished run's game log (default: the current
log; `--log` for the rotated `.prev.log`), the `env.txt` state, and the
`log_report.py` JSON into `<I>/.swx-history/ab-<label>/`.
`compare` prints both runs side by side: longframes, worst frame,
upload-only vs gpu-bound splits, `uploads/batch`, step transitions.

## VFR presentation gate

Compare the previous host build and the candidate with the same panel,
drawable, save, route, refresh setting and input sequence. Run each
condition twice with a warm cache. Capture steady gameplay separately
from startup, loading and occlusion. Cover 60/60 and 60/120, a lock that
does not divide the panel rate, uncapped play, a movie, a scripted scene
and recovery from a paused link. Exercise conflicting stock FPS overrides
with VFR enabled and disabled.

Compare on-screen interval p99 and frames held longer than 1.5× their
expected period. The report gives per-window p99, late intervals and
discarded presents separately. Display-link tick and merge counters are
cumulative at the last report. The per-frame TSV (`DM_NVN_TSV=/path/to/timing.tsv`) can
distinguish interval spikes caused by skipped callbacks from a late
system present (`@link_target_ms` to `@pres_at_ms`). Compare both with
GPU work and game present timings. Assess game-time drift separately in
a steady segment and check UI, input, physics and cutscene transitions.
Gameplay validation is pending.

### Etapa 0 — measurement protocol (instrumentation ready, no host change)

Scope: `log_report.py` only; no new flags, no host/log-format change.
The VFR path (`SWX_VFR=relaxed`/`uncapped`, rate in `SWX_VFR_HZ`) already
emits everything Etapa 0 needs; this step only completes the parser and
marks what does not exist yet.

Observables and where they come from:

- Ticks with no new frame: `empty ticks` in `display link up to tick`.
- Intermediate ticks holding the frame: `held for the present interval`.
- Merges dropping old frames: `old frames dropped by merging`.
- Enqueue→screen time: `game enqueue to target display p50/min/max`
  (display-link suffix) and `present: game enqueue to screen
  p50/p95/max over 300 frames` (one entry per 300-frame window in
  `present_windows`).
- On-screen health per 300-frame window: `shown` (= frames − discarded),
  `late` (= dropped − discarded), `discarded`, `p99_ms`.
- VFR/timing windows: `VFR up to present` parsed to
  `{present, policy, hz, display_hz, avg_present_ms, applied_ms, ratio,
  steps, ring/ticket waits, queued, dropped/window, guest_interval}`
  (legacy `vfr frame pace`/`cadence` lines kept as `{raw}`); `timing up
  to frame` keeps `frame_p95_ms`, `gpu_p50/p95_ms`.

Legacy compatibility: the display-link latency/drain suffix and the
`present:` line are optional — a legacy line with only the five base
counters still parses, missing fields report `null` (text: `n/a`), never
a fabricated number.

Future counters (`ready_depth`, `drop_oldest`, `credit`, `promote_ms`)
are NOT emitted by the host in Etapa 0. The report carries them as
`null`/`n/a` — never `0`, which would look like a measurement. If a
future log ever contains them, the parser picks them up; otherwise they
stay unavailable.

User-run gameplay gates (out of scope for this agent — real runs by the
user): fixed save/route from the fixed-variables section, two runs per
condition with a warm cache, covering 60/60, 60/120, a lock that does
not divide the panel rate, uncapped play, a movie, a scripted scene and
recovery from a paused link, plus conflicting stock FPS overrides with
VFR on/off. For each pair run `collect <label>` on both arms then
`compare`, and judge on on-screen p99, `late` counts, display-link
`empty`/`held`/`merged`, enqueue→screen p50/p95, TSV
`@link_target_ms` vs `@pres_at_ms`, GPU vs present timings, game-time
drift in a steady segment, and UI/input/physics/cutscene behavior.

## Bind-once gate (experimental, default off)

`bind_once on` sets `SWX_GPU_BIND_ONCE=1`. It suppresses only redundant
Metal 4 deswizzle binds while upload-encoder state is known; sparse gather
and encoder recreation invalidate the cache. Compare three cold runs and
three enter-leave-enter runs against `bind_once off`. Promotion requires zero
`[RED]`, no new frame-step transition, a median upload-frame p95 submit
improvement of at least 1 ms, and no individual regression above 1 ms.

## RT heap-arena gate H1 (experimental, default off)

`rt-heap-reserve on` sets `SWX_RT_HEAP_RESERVE=1`. It keeps automatic-MTLHeap
arenas for private transient render targets: Metal packs several live targets
into each resident heap and reuses freed space. Arenas are keyed only by heap
compatibility (storage/cache/hazard modes, 16 MB minimum, 64 MB per arena,
256 MB total over active and idle arenas); past either cap the stock path runs
(`fallback`). Eligible requests and their stock outcomes (`eligible, stock
reused, stock made`) are counted in both arms, so the gate compares measured
sides. Two boots per side with ring UB + yield active and texture pool off.
Each boot: load, 10 s luneta with pan, close 5 s, reopen the same luneta
10 s, shrine, teleport, dense area, save/load. Passes iff zero `[RED]`/
flicker/step change, backing ≤256 MB stable after warm-up, second luneta with
arena `hits` > 0 and arenas `new` down ≥70% vs the first, and equivalent stock
creations and/or residency-commit/submit cost in the repeated stretch down
≥20% with no individual regression >1 ms. The expected win is no backing-heap
creation/growth on re-entry and transitions, not the luneta's continuous GPU
cost.

## Run order (phases 0–2, no rebuilds needed)

1. Historical Gate 1 (done; split patch since removed — the recipe below
   no longer runs, kept for the record): split clear/off around the same
   route, `collect split-on-baseline-1080p` vs `collect split-off` →
   `compare` showed +13 ms with the split on, so the patch was dropped
   (`instrumentation/ab_results.md` Gate 1).
2. `set keepalive off`, same route → `collect keepalive-off` →
   `compare split-on-baseline keepalive-off` (Gate 2, historical label).
   Then `set keepalive clear`. `compare` prints a `sync-lag:` line too —
   must read False on every stock-baseline run.
3. Historical Gate 3 (done; frame-ahead patch since removed — the recipe
   below no longer runs, kept for the record): `DM_NVN_SYNC_LAG=1` +
   `DM_PRESENT_SNAP=4` in player `env.txt` passed Gate 3a (correctness) with
   mixed Gate 3b (numbers), so the patch, its env switch and its dedicated
   doc were deleted (`instrumentation/ab_results.md` Gate 3).
