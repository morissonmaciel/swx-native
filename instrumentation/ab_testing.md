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
python3 instrumentation/ab_test.py set split|keepalive|lag|snap|save|pso|pool|bind_once on|off|clear
python3 instrumentation/ab_test.py collect <label> [--install-dir I] [--log PATH]
python3 instrumentation/ab_test.py compare <labelA> <labelB> [--install-dir I]
```

`on` writes an explicit `=1` (visible as `launcher: env K=1` at boot);
`off` writes `=0` (forces stock behavior); `clear` removes the line so the
compiled default applies. Strict stock measurement baseline is pinned by
every install (`DM_NVN_SYNC_LAG=0`, `DM_PRESENT_SNAP=2`) for current
texture-hitch testing; `=1` / `snap 4` is an optional smoothness experiment
(Gate 3a recipe, preserved in `ab_results.md`), not the default.
Keep-alive on on macOS, upload split OFF unless `SWX_UPLOAD_SPLIT=1`
(Gate 1, 2026-11-16). `lag` arms the host switch; verify it took effect
via `sync-lag: True` in the report.

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

1. `set split clear` + `set keepalive clear` (clean = defaults), play the
   route → `collect split-on-baseline-1080p` (stored). `compare` prints a
   `sync-lag:` line too — must read False on every pre-Phase-3 run.
2. `set split off`, same route → `collect split-off` → `compare` (Gate 1).
   Then `set split clear`.
3. `set keepalive off`, same route → `collect keepalive-off` →
   `compare split-on-baseline keepalive-off` (Gate 2). Then
   `set keepalive clear`.
4. Phase 3 (done — `patches/dm_nvn_sync_lag.patch`, default ON in code when
   env is absent): the historical Gate 3a-pass recipe was
   `DM_NVN_SYNC_LAG=1` + `DM_PRESENT_SNAP=4` in player `env.txt`.
   Current installs pin the strict stock baseline (`DM_NVN_SYNC_LAG=0` +
   `DM_PRESENT_SNAP=2`); use `set lag on` / `set snap on` for the optional
   smoothness experiment. A/B against stock: `set lag off` (plus
   `set snap 2` for the pure-stock ring) vs defaults, `collect`, compare.
   Gate 3a (correctness) before Gate 3b (numbers).
