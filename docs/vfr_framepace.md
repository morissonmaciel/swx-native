# Breath of the Wild VFR and display pacing

The macOS BOTW 1.9.0 VFR patch uses one pacing policy for the game and
the Metal presentation layer. It measures simulation time at game
present, independently of whether a frame reaches the screen.

| Environment | Behavior |
|---|---|
| `SWX_VFR=relaxed`, `SWX_VFR_HZ=auto` | Lock the game to 60 fps. |
| `SWX_VFR=relaxed`, `SWX_VFR_HZ=30..240` | Lock at the requested game rate. Use display-link ticks where the rate divides the link rate. Use a monotonic deadline otherwise. |
| `SWX_VFR=uncapped` | Use the monotonic clock with a ceiling of 144 fps at `auto`, or the configured rate. |
| `SWX_VFR=0` | Restore the stock timing path and its existing controls. |

The install supplies `relaxed` and `auto` where no player override exists.
An existing numeric `SWX_VFR_HZ` now sets game rate in lock mode or the
ceiling in uncapped mode. The physical link separately requests the panel
rate, up to 240 Hz, and returns the newest eligible frame on each callback.
At 60 fps on a 120 Hz link, two ticks pace one game frame. VI still signals
physical retraces. A silent or unavailable link hands pacing to the
monotonic clock. Texture tickets, snapshot capacity and GPU fences
retain their existing lifetime rules.

The game delta uses the moving average of the last five monotonic
intervals between `QueuePresentTexture` calls. Intervals outside
1–250 ms clear the sample history. The average is capped at 1/15 s.
The guest receives seconds and the 30 fps-normalized ratio in one atomic
publication. Movies use a 30 fps host policy and keep their native game
step. Scripted overrides keep the guest's step. Deterministic clock,
unsupported builds and a disabled VFR use the stock path.

`DM_FPS`, `DM_FPS_TARGET` (including 0) and `DM_FPS_HOLD` belong to
the stock timing path. The VFR link owns its range and queue selection.
The existing render-scale, dynamic resolution and Metal safety mechanisms
continue to apply.

See [the presentation A/B protocol](../instrumentation/ab_testing.md#vfr-presentation-gate)
for the measurements required before judging the new cadence against the
previous implementation.

BOTW's VFR delta-time and UI-step stubs read the published ratio at state
`+0xA0`; the stock FPS target at `+0` must not feed either VFR consumer.
