# Performance cores, Game Mode, and Game Hub

What actually gives the game Apple's performance cores and Game Mode, how the
two reference projects differ, and what our wrapper must (and must not) copy.
Decision on the Game Mode default is **deferred** (see end).

## Performance cores come from QoS, not pinning

Darwin does not let an app pin threads to P-cores. Core affinity is virtual
bookkeeping the Horizon kernel sees; the scheduler decides (mac-nativegames
`threads.c`: "Core affinity is virtual bookkeeping; Darwin schedules
pthreads"; nxnative `hle_thread.c`: `SetThreadCoreMask` only stores the mask).
The real mechanism is **QoS**: `QOS_CLASS_USER_INTERACTIVE` threads get
P-cores and high clocks. Both projects do the same — game threads at
`USER_INTERACTIVE` by default (mac-nativegames `threads.c`,
`audio_renderer.c`; nxnative `hle_thread.c` with `DM_QOS_MAP=0`), helpers and
background work at `UTILITY`. No folder convention is involved; our wrapper
only needs the host to keep game threads at high QoS (already the default).

## Game Mode: the two projects diverge

- mac-nativegames opts **in**: `LSSupportsGameMode=True` plus
  `GCSupportsControllerUserInteraction=True` (`build_app.py`), category
  `action-games`.
- nxnative opts **out**: `pack_app.py` writes `LSSupportsGameMode=false`
  (Apple's documented off switch) while keeping the games category. Reason,
  measured in `docs/reference/frame-rate.md` ("macOS Game Mode holds the GPU
  at its lowest clock"): in fullscreen, after 45 s–3 min, the GPU falls to its
  lowest clock (338 MHz on an M4 Pro at 72–98% busy), GPU time doubles
  (10.5→19.7 ms), and the frame step drops to 30 fps — reproduced in a ~100
  line Metal app with no host code, with `gamepolicyd` logging `Set DPS to 2`.
  No public API keeps the clock up while Game Mode is on. Cost of opting out
  (unmeasured): 2x Bluetooth sampling for controllers/AirPods and lower
  background-task priority.
- Caveat: macOS remembers an app as a game per bundle ID; an app that already
  ran with Game Mode needs one manual off switch (fullscreen, Cmd-Esc →
  Game Overlay → Settings → Game Mode), even after repacking with `false`.
- iOS still declares the key `true` in nxnative (collapse only suspected
  there).

## Game Hub listing

Neither project integrates GameKit/Game Center (no `game-center`
entitlements, no `GKGame`). Listing needs only the games category
(`LSApplicationCategoryType = public.app-category.*`) plus game recognition.
Minimal recipe (mac-nativegames `games/odyssey/PLAYBOOK.md`): games category
+ `LSSupportsGameMode` (macOS 26) or `GCSupportsGameMode`.

## Verdict

Only the built `.app`'s `Info.plist` matters here: `LSApplicationCategoryType`,
`LSSupportsGameMode`, `GCSupportsControllerUserInteraction`. The Application
Support layout is orthogonal (it serves signing, updates, and size — see
`app_layout.md`). Our wrapper generates that plist when packing into
`<I>/host`, so Game Mode becomes a build preset (`game_mode on|off`).

Deferred decision: default to `false` per nxnative's measurement on this exact
BOTW profile (frame-step 60 fps + Metal), unless our own runs show otherwise.
Empirical note: P-core affinity forced via command line ran with Game Mode on
and no error in a local nxnative install (custom hack, to be re-validated in
our wrapper).
