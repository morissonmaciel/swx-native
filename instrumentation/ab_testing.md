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
python3 instrumentation/ab_test.py set keepalive|snap|save|pso|pool|bind_once|vfr on|off|clear
python3 instrumentation/ab_test.py collect <label> [--install-dir I] [--log PATH]
python3 instrumentation/ab_test.py compare <labelA> <labelB> [--install-dir I]
```

`on` writes an explicit `=1` (visible as `launcher: env K=1` at boot);
`off` writes `=0` (forces stock behavior); `clear` removes the line so the
compiled default applies. Every install pins the steady-beat baseline
(`SWX_VFR=relaxed` at auto Hz, `DM_PRESENT_SNAP=2` stock snapshot ring);
`set vfr off` restores the legacy Switch-interval frame step for A/B
(`ab_results.md` keeps the stock-baseline numbers for comparison).
`snap 4` is an optional smoothness experiment
(Gate 3a recipe, preserved in `ab_results.md`), not the default.
Keep-alive on on macOS (the texture-upload count split was removed after its
Gate 1 showed +13 ms; numbers preserved in `instrumentation/ab_results.md`).
Do not combine relaxed with `DM_FPS_TARGET=0`: target 0 hands the interval
back to the game's dynamic logic while the host ratio would still apply,
so the host suspends the beat and runs legacy (logged) — keep the target
at 60 (or unset) with relaxed.

`collect` snapshots the finished run's game log (default: the current
log; `--log` for the rotated `.prev.log`), the `env.txt` state, and the
`log_report.py` JSON into `<I>/.swx-history/ab-<label>/`.
`compare` prints both runs side by side: longframes, worst frame,
upload-only vs gpu-bound splits, `uploads/batch`, step transitions.

## Bind-once gate (experimental, default off)

`bind_once on` sets `SWX_GPU_BIND_ONCE=1`. It suppresses only redundant
Metal 4 deswizzle binds while upload-encoder state is known; sparse gather
and encoder recreation invalidate the cache. Compare three cold runs and
three enter-leave-enter runs against `bind_once off`. Promotion requires zero
`[RED]`, no new frame-step transition, a median upload-frame p95 submit
improvement of at least 1 ms, and no individual regression above 1 ms.

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
