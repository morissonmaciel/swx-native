# VFR frame pace: steady host beat for BOTW 1.9.0 (default on)

`SWX_VFR=relaxed` (install-pinned, `SWX_VFR_HZ=auto`) routes BOTW through a
steady host beat instead of the Switch's 1/2/3-vsync intervals. The setter
stub forces present interval 1 and the VFR ratio stub consumes the host's
real-time ratio (5-sample average of delivered acquire slots, 1/15 s step
cap, debt bounded at 0.25 s, published as a 64-bit atomic pair at state
+0xA0). Ticket, snapshot-ring and fence waits still run; movies, the VFR
override, DETCLOCK and `DM_FPS_TARGET=0` suspend the beat back to legacy.
Mechanism: `patches/dm_vfr_framepace.patch` (`nvn_vfr_pace.h`,
`hle_nvn.c` `swx_vfr_*`, BOTW `patches.py` frame-step stubs,
`RELAXED`→`VFR_FRAMEPACE` capability); stub semantics emulated in
`dm/tools/tests/test_vfr_stub.py`, pure beat logic in `t_vfr_pace.c`.

## Recommended combination

```
SWX_VFR=relaxed
SWX_VFR_HZ=60   # or auto (window refresh up to 120 Hz, 60 with no window)
# DM_FPS absent
DM_FPS_TARGET=60
```

`SWX_VFR=relaxed` with `DM_FPS` absent is the most coherent combination: no
test pin is applied, so `mtl_fps_target()` falls back to the profile/game
FPS (60 Hz on BOTW) for the display-link/Metal policy, while VFR controls
the game's real cadence via `SWX_VFR_HZ`. With `SWX_VFR_HZ=60` both sides
align naturally (VFR beat at 60 Hz, display-link cap at the game's 60).

## Conflicts with DM_FPS / DM_FPS_TARGET (verified against the code)

| Variable | Effect with `SWX_VFR=relaxed` active | Verdict |
|---|---|---|
| `DM_FPS` absent | Display-link follows normal policy; VFR controls the game beat | Recommended |
| `DM_FPS=60` + `SWX_VFR_HZ=60` | Coherent but redundant | Safe |
| `DM_FPS=30` + VFR 60 | Game advances at 60, presentation can cap at 30 | Avoid |
| `DM_FPS=60` + VFR 30 | Display wakes at 60 for frames produced at 30 | Safe but wasteful |
| `DM_FPS_TARGET=60` / unset | Passes through the setter, VFR holds interval/ratio | Safe |
| `DM_FPS_TARGET=30` | VFR keeps driving; the target does not limit VFR Hz | Safe but confusing |
| `DM_FPS_TARGET=0` | Game runs its dynamic interval while the host ratio would still apply | Real conflict — the host suspends the beat (logged); keep target at 60 |

No full bypass of the old machinery exists: `fps_step_cap()` and
`dm_fps_lock_tick()` still run, but the legacy cadence control
(`fstep_tick`, including `DM_FPS_HOLD`) stays out while VFR is armed. The
`DM_FPS_TARGET=0` case is guarded in `swx_vfr_suspended()` (no frame-step
target ⇒ the game runs its own interval logic, so the host ratio must not
mix with it); the ratio stub additionally keeps the original compare when
the VFR override is set or the published bits fall outside
0 < bits <= 0x40000000.

## Gate evidence (gameplay, install-pinned default)

Two sessions (current + previous log), BOTW 1.9.0, teleports, 2 shrines
in/out, 2 busy villages, cutscene, save/load across save versions:

- VFR ratio locked `0.500` (single-window excursions ≤ 0.602, recovered);
  raw = applied = 16.67 ms in every 300-present window; debt `0.00` at
  every report; present queue depth steady 2; dropped 0–5/300.
- Worst longframes: 82–86 ms whole (pipeline builds on teleport),
  76–79 ms submit (321-upload bursts); relaxed `applied` peaked at 19.26
  ms, never near the 1/15 s cap; GPU-bound village frames (~20 ms) did
  not move the cadence.
- Movie cutscene: clean suspend (`frame step cap 60 → 30`, beat off),
  30 fps playback with no slow-mo, resume to 0.500 after.
- Zero `[RED]` in both sessions; no black frames, no deadlock, saves intact.
- Game-time tracking: ~40.8k steps in one session ≈ 680 s at 16.67 ms
  against the same wall clock (ratio ≈ 1.000, inside the 0.99–1.01 gate).
