# SYNC_LAG equivalence evaluation (nxnative vs mac-nativegames)

Question: does `core/nxnative` have an equivalent of mac-nativegames'
`TOTK_SYNC_LAG=1`, and if not, what is the minimal patch that brings the
same one-submission-ahead CPU/GPU overlap to BOTW (01007EF00011E000)?

Verdict: **no equivalent exists.** Every nxnative frame strictly waits for
its newest GPU fence. The closest mechanisms (`DM_NVN_CUT`, `DM_PRESENT_SNAP`,
encode thread) hide translation/encode time or screen latency, never GPU
execution. This doc records the mechanism comparison, the effective settings
on the local install, the empirical motivation, the correctness risks of a
port, and a minimal gated action plan. Step 2 of the texture investigation;
kept out of `feat/texture_uploads` on purpose.

Roots: `NX=core/nxnative`, `MNG=refs/mac-nativegames/games/totk/third_party/d2r_gfx`.
Log context: BOTW 1.9.0 local runs, `instrumentation/log_report.py` output.

## 1. What MNG SYNC_LAG does

`MNG/gfx/nvn/hle_nvn.c:853-879,893-905`: the game's main thread waits every
frame for the GPU to finish the just-submitted frame (`SyncWait` on the
newest fence, ~7 ms), so CPU and GPU never overlap. With the switch on, such
a wait settles for the fence of the *previous* submission instead: the main
thread computes frame N+1 while the GPU draws frame N. Waits on older fences
are unchanged, and translation of the current submission must still finish
before the wait returns (`:858-863`).

Control surface: `SYNC_LAG_DEFAULT 1` under `GAME_BOTW`, else `0`
(`:865-871`); runtime `TOTK_SYNC_LAG=0/1` plus a `=2` per-frame-lag variant,
default off (`:872-879,900-905`), and a runtime flag via
`nvn_set_sync_lag()`. State tracked as `g_sub_fence_last/prev`; when
`sync_lag_on()` and `want >= last`, `want = prev`, with a further decrement
when `want == mtl_last_fence()` (`:893-899`).

Net effect for BOTW area entry: identical total upload/GPU work, less blocked
time — the next frame's upload encoding overlaps the previous frame's GPU
execution. This is the mechanism behind "MNG hitches less with the same
work" (`docs/texture_streaming_hitches.md` §4a).

## 2. nxnative fence/wait architecture (strict newest-fence)

- `NX/dm/gfx/nvn/hle_nvn.c:876-886` (`nvnSyncWait`): `if (!o->fenced)
  ALREADY_SIGNALED; if (w(o->fence, 0)) ALREADY_SIGNALED; else
  w(o->fence, timeout)`. No `last/prev` tracking, no lag switch, no fence
  substitution. A `grep` for `SYNC_LAG|sync_lag|g_sub_fence|LAG_DEFAULT`
  under `dm/gfx/nvn` returns no host mechanism — only `os_sync_*`,
  `OP_WAIT_SYNC`, `SYNC_CONDITION` constants.
- `NX/dm/gfx/nvn/nvn_mtl.m:2763-2801` (`mtl_wait_at`): `if (g_done >= fence)
  return 1`; otherwise fast path `waitUntilSignaledValue:fence`, then
  `pthread_cond_timedwait` until `g_done >= fence`. The ledger split at
  `:2804-2805` (`fence >= g_fence ? WS_SYNC_NEWEST : WS_SYNC`) is accounting
  only — the same `fence` is waited on.
- `DM_NVN_FAST_WAKE` (`:2757-2762`): selects the wake *source* (signal after
  each submission vs commit-feedback handler), ~0.26 ms. `post_early`
  (`:2760-2762`) runs CPU writes early; neither releases the wait early.
  Documented in `docs/reference/nvn-objects.md:552-556`.
- `DM_NVN_WAITS` (`:280-286,308-316`, `docs/env_switches.tsv:268`): wait
  ledger — per-frame counts plus "newest-fence wake ms after GPU end".
  Diagnostic only, no behavior change. It is, however, the right
  measurement tool for the baseline below.
- Opposite direction: `DM_NVN_PRESENT_FINISH=1` makes each present return
  only once the GPU ran the frame, so the game *never* runs ahead of the GPU
  (`docs/env_switches.tsv:162`, `hle_nvn.c:2644-2646`).

## 3. Candidates examined — why none is equivalent

### 3a. DM_NVN_CUT segmentation (closest, still not overlap)

`nvn_mtl.m:15725-15752`: hands a submission to the GPU *in segments*
(`DM_NVN_CUT=<draw count>`, default 64, `0`=off). Motivating case is stated
in the comment: BOTW's main thread holds the next submit in `nvnSyncWait`
on the just-submitted fence, so committing between passes lets the GPU run
earlier passes while the CPU translates later ones. `sub_cut_ready()` only
commits when earlier work reported back (`g_seg_inflight > 0` holds,
`:15741-15752`); `DM_NVN_CUT_BUSY=0` commits at every cut. `DM_NVN_PASS_SPLIT`
(default 256, `:15786-15797`) ends a long pass while the GPU is idle — same
category. Measured in `docs/reference/frame-rate.md:588-594` (BOTW shrine:
main in `nvnSyncWait` 59% + render in submit 40%, 7.7 ms idle → 3.8 ms;
`DM_NVN_CUT=0` vs 64: newest-fence wait 12.5 ms → 5.6 ms — *still waits for
newest*). Difference vs `SYNC_LAG=1`: reduces the bubble *within* one frame
without relaxing correctness; the CPU never runs a frame ahead.

### 3b. DM_PRESENT_SNAP / DEPTH / DRAIN (ahead of screen, not GPU)

`nvn_mtl.m:17401-17446,17548-17555,17600-17604`: with snapshots, each present
is copied on the game's queue right after frame work, and the game may run
up to one frame ahead of the *screen* (ring length from
`DM_PRESENT_SNAP=<n>`, `:17418-17424`, profile `present_snap on` for BOTW).
But snapshot present still trails frame work (`rec_own_push` behind
submissions, `snap_commit_wait`/`sub_commit_wait` at `:17202,:17346`), and
acquire waits for ring capacity (`mtl_snap_acquire_wait`). `DM_PRESENT_DEPTH`
is disabled with snapshots (`:17573`,
`docs/reference/frame-rate.md:308`). Difference: MNG relaxes the *GPU
fence*; this relaxes *display release*. BOTW's profile note confirms the
serial CPU/GPU model: no N+1 submit until N's GPU work is done
(`dm/games/01007EF00011E000/README.md:31,64-65`, just-submitted fence wait
`@sync_ms 9.7 ms`).

### 3c. Encode thread (overlap of translation with encoding)

`dm/gfx/nvn/nvn_rec.h:1-16,101-113,359-370,486-498`: replay Metal encoding
runs on a worker while the submit thread keeps translating; the submit
*waits for encode to finish* (`rec_drain`) at the end of each submission.
`nvn_mtl.m:2733-2752`: CPU-wait-for-GPU waits for the fence signal behind
the commit; `DM_NVN_SUB_DRAIN=1` restores the old wait
(`docs/env_switches.tsv:227`, default off). `SUB_MERGE`/`SUB_IDLE_FB`
(`nvn_rec.h:159-170`) hold-while-GPU-busy then commit in order — still the
same fence (`docs/reference/rendering.md:1342-1347`). Difference: hides
*encode*, not GPU execution. Note this path (`rec_commit_upload` +
`rec_drain`, kept on by the local install's saved choice, §5) is one of the
upload-stall aggravators in `docs/texture_streaming_hitches.md` §1a — the
batch sits as a record in the encode thread's block until drain, where MNG
commits straight to its queue.

### 3d. Profile keys (no lag key exists)

BOTW `dm/games/01007EF00011E000/game.tsv:24,28-29`: `fps_target 60`,
`present_snap on`, `pso_mtl4 on`; no `enc_thread` (hence off by default),
no `sync_lag`. `dm/games/game.py:159-269,176-184` offers only
`present_snap on/off` and `enc_thread on/off`; `dm/games/settings.tsv:34,37`
and `dm/games/README.md:255,258` document exactly those two as tested
defaults. TOTK's own profile
(`dm/games/0100F2C0115B6000/game.tsv:41,46`) has neither `present_snap` nor
`enc_thread` (defaults off). A lag knob would be a new key
(`sync_lag on/off` → `DM_GAME_SYNC_LAG`) plus a new host switch.

## 4. Effective performance settings on this install

Launcher precedence (`dm/mac/launcher.c:6-9`): launch environment >
player `env.txt` > bundled `dm/env.txt` > saved choices > tested defaults.
For `/Volumes/Home Data/Games/Breath of The Wild/Breath of the Wild.app`
(bundle id `org.nxnative.01007ef00011e000`):

| Settings-window toggle | Variable | Effective value | Source |
|---|---|---|---|
| Frame buffering | `DM_PRESENT_SNAP` | 1 | default (`launcher.json:246`, profile `present_snap on`) |
| Metal 4 pipelines | `DM_PSO_MTL4` | 1 | default |
| Pipeline prebuild | `DM_PSO_PREBUILD` | 1 | default (needs `DM_PSO_MTL4=1`) |
| Encode thread | `DM_NVN_ENC_THREAD` | 1, user-saved | default is 0 (`launcher.json:282`); prefs hold `launcher.DM_NVN_ENC_THREAD=1` |
| Hold frames when late | `DM_FPS_HOLD` | on | default |
| Performance HUD | `DM_METAL_HUD` | 1, user-saved | default 0; prefs hold `=1` (plus `DM_LANG=pt-BR`, `DM_FULLSCREEN=1`) |

Player `env.txt`
(`~/Library/Application Support/nxnative/01007EF00011E000/env.txt`) holds
only `DM_SAVE`/`DM_PSO_USER`/`DM_PSO_ARCHIVE`/`DM_PSO_PAIRS`; the bundle
ships no `dm/env.txt`. Historical Gate 3a pass pinned the decided
defaults in player `env.txt`: `DM_NVN_SYNC_LAG=1` (one submission ahead;
host default is strict) and `DM_PRESENT_SNAP=4` (ring fits CPU-ahead +
screen-ahead; the game profile only promises 2). Current installs pin the
strict stock measurement baseline instead (`DM_NVN_SYNC_LAG=0` +
`DM_PRESENT_SNAP=2`) for texture-hitch testing; `=1` / `snap 4` remains
available as an optional smoothness experiment. `SWX_*` default in code
(keep-alive on on macOS; upload split opt-in with `SWX_UPLOAD_SPLIT=1`
since Gate 1). Stock baseline (`=0` / `snap 2`) is pinned by
`wrapper/main.py:redirect_saves`; `=1` / `snap 4` opts into the smoothness
experiment for A/B.

## 5. Empirical motivation (why overlap is the remaining lever)

- Upload bursts are now *measured*, not inferred. With the batch
  instrumentation (`instrumentation/swx_upload_batches_diag.patch`): frame 622 —
  `254 first / 38.4 MB / 8.3 ms in 8 batches (3 split)`,
  `submit+present 52.7 ms`, whole 60.9 ms. The count split fires (3
  split-triggered flushes) yet submit time is unchanged: fragmenting batches
  does not move encode off the submit thread. Total: 17 batches, 3 split,
  16.7 uploads/batch over 3 burst frames (`log_report.py`).
- Everything else is GPU-bound hybrid work the host's hold logic paces:
  worst frames whole 199–201 ms at GPU 15–19 ms, `submit+present` 5–8 ms,
  195–307 hybrid draws, 0 uploads, 0 new pipelines
  (`01007EF00011E000.log`, e.g. frames 2276/2277, 1141). Present upscale to
  the 5K drawable costs p50 0.41 ms — innocent.
- Frame-step oscillation (holds 1→2 at presents 1621/1704/2272, probes back)
  with `CPU 99% on performance cores` at the failed probe: placement is
  correct; the limit is overlapped work, not cores or clocks (keep-alive
  covers idle gaps only).
- The MNG comparison (`docs/texture_streaming_hitches.md` §4a) predicts
  exactly this residue: same bytes, less blocked time, via one-submission
  overlap plus deferred writeback.

## 6. Correctness risks of a port (read before coding)

1. **Guest-memory visibility.** Running frame N+1's CPU while the GPU still
   reads frame N's inputs is safe in MNG only because their dirty-tracking
   and residency keep both generations alive. nxnative's tracker is
   `nvn_dirty` (`nvn_mtl.m`, `nvn_dirty_stats`); the lag point must not let
   the game (or the rewriter) recycle a pool/buffer the GPU still reads.
   The `TOTK_SYNC_LAG=2` experiment notes (`hle_nvn.c:872-878`) show upstream
   itself treats deeper lag as experimental.
2. **Translation must finish.** MNG's own comment (`:858`): the relaxed wait
   still guarantees translation completion. An nxnative port must order the
   substitution *after* `rec_drain`/submit completion, never before.
3. **Writeback interaction.** nxnative's GPU→CPU visibility is inline
   (`gsb_store`/`rt_mirror_*` on the submit thread, §4b of the hitch doc);
   MNG defers to completion handlers (`late_land`). Lagging the wait extends
   the window in which a deferred/overlapped store could race a re-upload —
   the `up_lost` re-upload path (`nvn_mtl.m:3281-3325`) is the safety net to
   watch in testing.
4. **Snapshot ring.** `DM_PRESENT_SNAP` already lets the game run ahead of
   the *screen*; adding GPU lag stacks two kinds of "ahead". Test with
   snapshots on (this install's state). — Confirmed 2026-11-16: ring 2
   blinked repeatedly under lag, ring 4 holds flat with zero blinks
   (Gate 3a, `instrumentation/ab_results.md`). Ring size is load-bearing for any lag design.
5. **Scope discipline.** Per `core/nxnative/AGENTS.md`: prefer a general
   mechanism with a per-game default over a BOTW-only branch; gate fixed
   addresses/build IDs if anydos traces are used (none expected here —
   fence values are dynamic). Mirror MNG: default on for BOTW, off
   elsewhere. Keep the switch (`DM_NVN_SYNC_LAG`) so A/B never needs a
   rebuild.

## 7. Minimal action plan — status 2026-11-16 (see `instrumentation/ab_results.md`)

- **Gate 0 — baseline: PASS.** Reproducible burst with batch data
  (frames 612/622/609, 237–254 first uploads).
- **Slice 1 — prototype: DONE.** `patches/dm_nvn_sync_lag.patch`: single
  choke point `mtl_wait()` (covers `nvnSyncWait` fenced==1 and
  `nvn_sync_wait` fenced==3 paths; acquire/finish/drain call
  `mtl_wait_at` directly and are unchanged). No fence-history state
  needed: `g_fence` increments by 1 per `mtl_sub_commit`, so newest−1 is
  always a committed submission.
- **Gate 1 — correctness: PASS with recipe, FAIL stock.** Stock ring (2):
  repeated black blinks — game ahead of screen + CPU ahead of GPU = 2
  frames of latency in a 2-slot ring (hypothesis confirmed by the fix).
  With `DM_PRESENT_SNAP=4`: 4724 frames, 0 RED, 0 blinks, 0 gameplay
  holds. Historical decided install defaults were `DM_NVN_SYNC_LAG=1` +
  `DM_PRESENT_SNAP=4` pinned by the wrapper; current installs pin the
  strict stock baseline (`=0` / `snap 2`), with `=1` / `snap 4` kept as
  an optional smoothness experiment.
- **Slice 2 / Gate 2 — quantify/decision: MIXED, parked.** Holds suppressed
  (0 episodes) but burst submit unchanged (within noise) — flatness win,
  not faster bursts. Prototype stays env-gated (stock baseline pinned for
  current testing); NOT
  promoted to a profile key. Revisit only with the writeback/pooling
  follow-ups (`docs/io_paths_nxnative_vs_mng.md`).
- **Original Gate 2 sketch (superseded):** promote to profile key
  (`sync_lag on` for BOTW only) — declined for now; the ring-size coupling
  makes a per-game default premature.
- **Non-goals (unchanged).** Pooled upload staging, deferred writeback
  (`late_land` equivalent), vertex-descriptor rework, async save commit —
  each a separate evaluation with its own gates.

## 8. Open questions

- Exact MNG guard for "translation must finish" at `:858` — read the
  surrounding lines before mirroring the ordering.
- Whether `DM_NVN_CUT_BUSY=0` (commit at every cut) already recovers part
  of the overlap on BOTW, and how it composes with a lag switch.
- `SWX_` (experiment, may be dropped) vs `DM_` (upstream-shaped switch):
  slice 1 uses `DM_NVN_SYNC_LAG` from the start so a successful experiment
  is already shaped for upstreaming.
