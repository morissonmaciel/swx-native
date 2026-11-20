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
rate, up to 240 Hz. VFR presents from a two-ready FIFO in ticket order,
consuming the oldest ready frame at each eligible callback; if a third frame
becomes ready while both slots are occupied, it drops the oldest queued frame
and records that drop. At 60 fps on a 120 Hz link, two ticks pace one game
frame. VI still signals
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

See the [Gate 2 present-queue protocol](../instrumentation/vfr_present_gate.md)
for coverage, interpretation and the final player acceptance.

BOTW's VFR delta-time and UI-step stubs read the published ratio at state
`+0xA0`; the stock FPS target at `+0` must not feed either VFR consumer.

## Etapa 2 — four snapshots and two ready frames (`dm_vfr_queue_policy.patch`)

With VFR active, the host uses four physical snapshot slots and targets two
GPU-safe ready frames. The display callback consumes the oldest ready ticket;
when the ready FIFO is full, a newly promoted frame replaces the oldest queued
one and increments `drop_oldest`. Priming after start or resume can wait up to
one target period for the second ready frame. The game cadence remains governed
by `SWX_VFR_HZ`; the acquire signal is consumed only as a punctual recovery
poll and does not alter the simulation ratio or pacing deadline. The callback
does not wait for GPU completion. `SWX_VFR=0` remains on the stock path.

The queue-policy patch also records queue depth, enqueue-to-display timing,
drop/hold/empty counts, and game-time versus wall-time windows. These values
support the pair-by-pair Gate 2 protocol in
[`vfr_present_gate.md`](../instrumentation/vfr_present_gate.md). The player
accepted steady 60/60 and the remaining Gate 2 coverage on 2026-11-20.
The 30 fps movie and return-to-gameplay have separate passing evidence below.

The VFR-only quick-menu capture alpha guard is documented in
[`vfr_quick_menu_alpha.md`](vfr_quick_menu_alpha.md). It preserves the stock
frame-1 comparison and uses BOTW's existing alpha correction on positive
fractional UI frames when VFR owns the clock. Its visual A/B remains pending.

## Etapa 1 — present worker + ready FIFO (`dm_present_worker.patch`)

Active whenever VFR is armed (`SWX_VFR=relaxed`/`uncapped` with a resolved
rate); `SWX_VFR=0` keeps the stock take/wait/present path byte-for-byte.
No new flags. Gate 2 observability is documented in the linked protocol and
the parser keeps those counters nullable for older logs.

- A worker thread (`nvn-present-worker`) takes `g_pq[]` tickets in order
  through `g_pq_wk`, separate from `g_pq_read`, which still means taken for
  presentation. Per ticket, it waits until the GPU finished the producer
  work it depends on: snapshot copy via `g_snap_ev` (after
  `snap_commit_wait`), external producer via `g_ext_ev`, and the game's own
  draws via `mtl_wait` on the submission fence. It promotes only when that
  wait is source-safe and discard is off. The ready FIFO is capped at 2;
  the worker retains its pin while waiting for space.
  A nonzero producer token without its completion event is a fatal invariant
  failure; the worker never treats a missing event as proof that the source is
  safe to reuse.
- The display-link callback loses `ext_wait`/`snap_commit_wait`/
  `sub_commit_wait`: it pops the oldest ready ticket without blocking and
  calls `present_now()`; an empty FIFO keeps the last image on screen
  (`g_link_empty++`, as before). The classic present thread pops ready
  tickets the same way while the link is silent, rescued, or absent, and
  otherwise idles exactly as in stock.
- Ordering across VFR arm/disarm is preserved: ready tickets drain first,
  stock takes wait for `g_wk_inflight == 0`, and stall/suspend purges mark
  tickets canceled under `g_pq_mu` before any condition wait releases it.
  The worker checks cancellation before promotion and during FIFO backpressure;
  the callback checks it before presentation. Canceled producer work is drained
  to a source-safe point before `g_pq_done` can cross it.
- The `g_snap` ticket/ev checks remain in place: a copy is readable only
  after it completes, and snapshot slots stay busy until their source-safe
  frontier advances. A canceled ticket is recorded as dropped in presentation
  history and wakes screen observers, but does not leap `g_pq_done` over an
  earlier in-flight present or producer read. `RED_ONCE(111)` is kept; the
  defensive `RED_ONCE(173)` still catches a ready ticket at or below
  `g_pq_done`. Each enqueue pin has one owner through producer and FIFO waits,
  then is released once on presentation or cancellation/drop.

## Gameplay gate protocol (user-run; decisive case: link stall)

Triple A/B with the same panel, drawable, save, route, refresh setting
and input sequence, each condition run twice with a warm cache, steady
gameplay captured separately from startup/loading/occlusion:

- (a) candidate: this build, `SWX_VFR=relaxed` (then `uncapped`);
- (b) base: previous host build (VFR without the worker);
- (c) stock: `SWX_VFR=0`.

Cover 60/60 and 60/120, a lock that does not divide the panel rate,
uncapped play, a movie, a scripted scene, and recovery from a paused link
(the stall case decides: drop accounting must match, the picture must
resume without a hang, and no `RED` may fire). Exercise conflicting stock
FPS overrides with VFR enabled and disabled.

For each pair run `collect <label>` on both arms then `compare`, and judge
on on-screen interval p99 and frames held longer than 1.5× their expected
period, `late`/discarded counts, display-link `empty`/`held`/`merged`,
enqueue→screen p50/p95, TSV `@link_target_ms` vs `@pres_at_ms`, GPU vs
present timings, game-time drift in a steady segment, and
UI/input/physics/cutscene behavior.

## Gate evidence (approved 2026-11-18: 30 fps movie + return to gameplay)

One long `relaxed/auto` session on a 60 Hz link confirmed the 60 → 30 →
60 fps sequence (`instrumentation/ab_results.md`, preserved capture
`stage2-fixed-new-save-121839.log`):

- 30 fps movie on the 60 Hz panel: 150 new frames + 150 intentional
  repeats per 300-refresh window, zero eligible empty ticks and zero
  full-queue drops; on-screen p99 33.33 ms.
- Return to gameplay: target back to 60 fps as soon as the decoder
  stopped; the transition window had zero eligible empty ticks and zero
  full-queue drops; the first full 60 fps window returned to p99
  16.67 ms. No route repetition needed for these two cases.
- The player closed Gate 2 on 2026-11-20, accepting steady 60/60
  production and waiving the remaining matrix and recovery captures. The
  archived 60/60 measurements and observed empty ticks remain in the
  [A/B results](../instrumentation/ab_results.md).
