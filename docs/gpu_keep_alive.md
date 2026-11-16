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

## Contrast with nxnative

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
