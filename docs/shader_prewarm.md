# Shader and pipeline prewarm: mac-nativegames vs nxnative

Raw technical record of the two prewarm approaches and why the
mac-nativegames one holds the frame rate where nxnative's does not.

## mac-nativegames: pay everything before play

1. **Union list as build contract.** `shader_list.tsv` (18,244 variants) is
   what every build manufactures: each shader's default bind-state variant
   plus every listed non-default state and pipeline (`V/A/L/S/C` shader and
   compute rows; `R/B/T` render pipeline rows, including border-color-emulated
   and tile-read variants). Before `R/B/T` rows existed, every fresh install
   built ~58 pipelines inside a 5-minute route (`docs/shader-list.md`).
2. **Packaging-time compile.** Seed (`prepare_shader_seed.py`) → `totk_xlate`
   → SPIRV-Cross → one `.metallib` per shader → `warm_metal_cache` compiles
   the seed once from the game binary's folder, so the system Metal cache is
   already warm at first start (`tools/warm_metal_cache.m`,
   `nvn_mtl.m:11220`). Measured cost: a cold library 48–92 ms (almost all in
   the system Metal compiler), a cold pipeline 9.0 ms on one thread, 2.7 ms
   on 8 (`nvn_mtl.m:11210-11224`).
3. **Two-tier boot warm-up** (`warm_start`, `nvn_mtl.m`). `pso_title.tsv`
   (title screen, menus, save list) is the *only* list the first frame waits
   for (`games/totk/Makefile:106-107`); `pso_manifest.tsv` (everything else)
   builds on background threads — 8 on Mac (cores minus 2), dropping to at
   most 2 once the player is in the world so warm-up never takes the game's
   cores (`warm_threads`, `warm_threads_now`). The loading screen stretches
   via `mtl_warm_wait` until the background pass catches up. On iOS the
   device pipeline archive is preloaded before the title, then dropped
   (`warm_fill_archive`); keeping 6,813 render pipelines resident put an
   iPhone 14 at 3.82–3.89 GB with p99 117–200 ms, so they are not kept.
4. **Self-feeding loop.** Anything the submit thread still has to build in
   play is appended to per-profile `pso_user.tsv` (plus shared
   `pso_collected.tsv`) and warmed at every later start
   (`pso_user_record`, `nvn_mtl.m:7456-7494`). Player contributions flow to
   the shader inbox and into everyone's next build. Coverage converges toward
   100%: no one stutters where anyone already played.
5. **Observed cost.** 10–20 s of preparation on every game load (first frame
   gate + background pass), before/without gameplay. Reference scale: 23,226
   pipelines took 235 s single-threaded on first start; 9,268 libraries took
   856 s on one thread vs 188 s on 8 through the compiler service.

## nxnative: prebuild plus on-demand fallback

1. **Route-scoped coverage.** BOTW ships 15,767 prebuilt bases, a 3,012-row
   `pso_pairs.tsv`, a 1,789-line `pso_manifest.tsv`, and a per-GPU offline
   pipeline archive (`metal-tt` `.mtl4a`) plus a first-start prepare step
   (5.2 s on 12 workers, or background prewarm). Coverage follows the
   author's routes, not the union of all players' play.
2. **Hybrid stand-ins or submit-thread waits.** A pipeline missing from the
   archive draws with a hybrid stand-in while the real one compiles, or the
   draw waits: first-seen pipelines compile on the submit thread in frames
   of 100–550 ms (`docs/reference/shaders.md:2446`). Donkey Kong Bananza
   before its seed list: 154 frames over 100 ms in 13,200 frames, 87 s of
   compiling on the submit thread, worst single stall 7.1 s; after: 68 such
   frames, 12 s of compiling (`:2627`).
3. **Failure modes that recompile forever.** A Mac app without
   `Resources/dm/shader_pairs.tsv` logs `pipeline prebuild: nothing to
   build`, draws no hybrid stand-ins, and compiles every first-seen pipeline
   on the submit thread (`:2446`). Archive misses (uncovered GPU, chain
   mismatch), a missing `pso_user.tsv`, or a purged system Metal cache
   (keyed by source, options, *and the length of the run folder's path* —
   moving/rebuilding the app invalidates it, per the MNG comment at
   `nvn_mtl.m:11215-11220`) all silently return to runtime compiling.

## Measured difference, in mac-nativegames' favor

| | mac-nativegames | nxnative |
|---|---|---|
| When compile cost is paid | packaging (one-time) + boot (every start, off the gameplay path) | first start prepare + whenever a draw meets an uncovered pipeline |
| Coverage source | union of all players' contributions, converges | author's routes + local `pso_user.tsv` |
| First-frame gate | title list only | full prepare step |
| In-play miss cost | recorded for next start; rare by construction | 100–550 ms submit-thread stall or hybrid stand-in |
| Behavior on a low budget | same 10–20 s load, then stable | each stall costs proportionally more frames |

Design rule for our wrapper (see `app_layout.md` for where it lives):
compile before play, never during. Build carries the full seed plus lists;
boot warms title-gated then background; runtime appends to the per-profile
user list. All of it fits in `<I>/shaders`.
