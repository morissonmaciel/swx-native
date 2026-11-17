# GPU keep-alive

How mac-nativegames keeps the GPU clock up between frames, how nxnative
addresses the same problem differently, and what we should adopt.

## The problem (measured)

An M4 GPU lowers its clock when idle for part of each frame. Standalone test:
the same compute took 10.9 ms back-to-back, 16.8 ms with 10 ms gaps, and
10.9 ms again with a spinner filling the gaps. The game idles the GPU between
frames (it waits for each frame before submitting the next), so a frame's GPU
work ran ~25% slower (world scene p50 12.1→9.1 ms with the spinner).

## The mechanism (`nvn_mtl.m:928-1018`)

A dedicated thread keeps up to two short spin kernels (~1 ms `fma` loop, one
threadgroup) in flight on their own command queue, **only in real gaps**: a
kernel launches only while `g_q4_inflight == 0` (no game command buffer on the
GPU). Duration self-calibrates from measured GPU time (`g_ka_target_us`,
default 1000 µs, tunable 100–1000 via `TOTK_GPU_KEEPALIVE_US` / `keepalive_us`
flag). Since OOT, the kernel exits early (~25 µs) as soon as the game commits
(`ka_stop_now` stop word set by `q4_commit`), so a game submission never waits
for the spinner (`OOT_KEEPALIVE_YIELD=0` restores the old behavior). It goes
quiet when nothing was submitted for 100 ms. Stated cost: one GPU core plus
power/heat.

## Controls

Default **on on Mac, off on iOS** (spinning heats the phone; the gain was
measured on desktop). Precedence: `TOTK_GPU_KEEPALIVE=0/1` env
(`dm_gfx_keepalive_locked`) wins; otherwise the settings panel Display page
switch (`gpu-keepalive.txt` via `dm_gfx_keepalive_start`, live toggle via
`dm_gfx_keepalive_set`). Off in play only sleeps the thread. Stats (kernels,
summed/longest GPU time) surface in the overlay (`mtl_keepalive_stats`).

## Adapted implementation (swx, `patches/dm_gpu_keepalive_yield.patch`)

Status: promoted to install default (macOS) after a triple A/B on the BOTW
luneta route (VFR relaxed/auto, texture pool off): `keepalive=1,yield=1`
frame p95 16.73–16.89 ms vs `yield=0` 17.41–20.48 ms vs `keepalive=0`
19.56–21.31 ms, zero `[RED]`, no frame-step transitions. The spinner returns
real budget to the game, and the immediate yield is what keeps the spinner
itself from contending.

- `dm/gfx/nvn/swx_keepalive.m`: own Metal device reference and classic
  command queue, runtime-compiled spin kernel, thread on `USER_INITIATED`,
  self-calibrating ~1 ms kernels, `SWX_GPU_KEEPALIVE=0` to disable (default
  on macOS, off on iOS), `SWX_GPU_KEEPALIVE_US=100..1000`.
  `SWX_GPU_KEEPALIVE_YIELD=1` (default on; `=0` restores the old run-to-end
  behavior for A/B): the kernel polls a shared stop word every 1024
  iterations (~25 us); `swx_keepalive_game_commit()` sets it before each
  main command-buffer commit (normal path and `sub_cut`), and the last
  completion signals a semaphore the spinner waits on instead of polling.
  Stopped kernels never recalibrate the length.
- Hooks in `nvn_mtl.m`: `swx_gpu_inflight()` reader next to `g_seg_inflight`,
  `swx_keepalive_submit()` (start + activity stamp) in `mtl_sub_begin`,
  `swx_keepalive_game_commit()` before both main commits, idle signal in both
  feedback handlers when inflight reaches 0. No hot-path changes.
- Why it cannot disturb the frame step: the step's GPU cost comes from the
  game's own per-fence `GPUStartTime/GPUEndTime` (`hle_nvn.c fstep_tick` via
  `mtl_gpu_ms`); the spinner's command buffers have their own fences and
  never touch those numbers. The spinner thread mostly sleeps, so the
  process-level CPU share check is unaffected.
- Metrics: `swx_keepalive_stats` (kernels, summed/longest GPU time, stopped,
  stop-callback max) on the power line every 300 frames; no per-kernel
  logging. The stop-callback lag is wall clock (includes CPU scheduling):
  observability only, not a kernel-exit proof. Gate approval uses kernels,
  GPU ms, max ms and stopped.
- Why it cannot disturb the frame step: the step's GPU cost comes from the
  game's own per-fence `GPUStartTime/GPUEndTime` (`hle_nvn.c fstep_tick` via
  `mtl_gpu_ms`); the spinner's command buffers have their own fences and
  never touch those numbers. The spinner thread mostly sleeps, so the
  process-level CPU share check is unaffected.
- Thermal note: sustained spinning holds the GPU out of its floor state by
  design; on thermal pressure macOS still governs (probes gate on
  `dm_thermal_now()`, unchanged).

## Contrast with nxnative (reference design)

nxnative has no spinner. Its answer to the same idle-downclock problem is
**present snapshots** (keep CPU/GPU flowing through presentation; BOTW note:
64 long frames with neither → 4 with snapshots alone). Same disease, two
therapies: filler compute vs continuous presentation.

## Recommendation (adopted)

Ship keep-alive **on by default on Mac, paired with Game Mode on** (see
`game_mode.md`): the spinner neutralizes the idle→downclock path behind
nxnative's Game Mode collapse finding, which is what makes opting in viable.
Their collapse was measured without any spinner; their doc leaves the mechanism
open. Present snapshots complement the spinner (pacing) but cannot replace it
(no GPU load in the gaps). iOS stays off by default (heat).
Field evidence (local, uncontrolled — hardware to record): hours of gameplay
with P-core affinity + Game Mode on + keep-alive held the frame rate where the
snapshot-only setup did not.
