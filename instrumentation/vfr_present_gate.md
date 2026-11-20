# VFR present queue — Gate 2

Gate 2 was closed by player acceptance on 2026-11-20. The protocol below
records the original coverage and explains the telemetry.

## Coverage

Capture each supported lock/display pair: 30/30, 30/60, 30/120, 60/60,
60/120, 120/120, and 120/60 (game rate / display refresh). Also capture a
non-divisor lock, uncapped play, a 30 fps movie, title/opening sequence, an
isolated stall, and recovery after the display link becomes silent. Keep
startup/loading, steady gameplay, movies, and stall/recovery segments labeled
separately. Repeat runs with the same route, save, display, and settings.

## Log contract and interpretation

The host reports one rolling window using this prefix and field contract:

```text
VFR present queue up to tick <tick>: window <n> refreshes, ready_depth max <n> frames (sampled before eligible consumption), drop_oldest <n> frames, credit <n> acquires, queue_ms p95 <x> ms from <n> pops (ready→eligible pop), promote_ms p95 <x> ms from <n> promotions (enqueue→ready), held <n> intentional refreshes, empty <n> eligible refreshes
```

`credit` may be absent if the acquisition counter is unavailable. Simulation
and wall-time deltas are reported separately by the existing `[nvn] VFR up to
present` line as `window game <seconds> s / wall <seconds> s`. These are deltas
since the previous VFR report, normally about 300 game presents; the first
`0/0` sample is parsed as unavailable because it has no preceding endpoint.
`sampled time` on that line remains the cumulative VFR game time. Do not infer
VFR time from the stock frame-step ratio. Older logs retain `null`/`n/a` for
fields they never recorded. The parser stores queue windows in
`vfr_queue_windows` and cadence/game-time windows in `vfr_windows`, displaying
the latest of each; raw lines remain available.

The p95 sample counts distinguish an empty measurement window from a measured
zero-latency window. When `pops` or `promotions` is zero, its p95 is unavailable
and the parser returns `null`/`n/a`; older lines without sample counts remain
parseable, with the counts themselves unavailable.

Interpret counts by regime. `drop_oldest` should be approximately zero only
when production does not exceed consumption; drops under deliberate excess or
uncapped load are not an automatic failure. In steady eligible regimes,
`empty` should be zero. On idle rate pairs where a physical refresh occurs
without a new game frame, `held` is expected and intentional. Startup prime
may also contribute intentional holds; separate that interval from steady
gameplay when evaluating the count. Review
`queue_ms` as ready-to-eligible-pop latency and `promote_ms` as enqueue-to-ready
latency; assess their p95 against the available per-pair frame budget, not one
universal threshold. `ready_depth` is a maximum sampled before consumption,
not an average occupancy.

For an active gameplay segment, compare the game and wall deltas from each
`VFR up to present` window and confirm the simulation ratio/step behavior did
not change. `sampled time` is cumulative and can be used to bound a longer
segment; the stock ratio line is not a substitute. Inspect title/opening,
movie, stall, link-silent recovery, frame-step transitions, visible pacing,
and `[RED]` messages separately. The gate is not approved by parser output
alone.

## Current result

Accepted by the player, including steady 60/60 and the remaining protocol
coverage. The archived gameplay includes repeated steady 60/60 runs and
the 30 fps movie with recovery to 60 fps. The player waived additional
captures for the rest of the matrix and recovery cases. See the
[A/B results](ab_results.md).
