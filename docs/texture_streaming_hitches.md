# Texture streaming hitches: nxnative vs mac-nativegames

Deep-dive investigation (subagent, file:line verified on both trees) into why
area-entry texture streaming hitches more on nxnative, plus the empirical
confirmation from a local BOTW 1.9.0 run.

Roots: `NX=core/nxnative`, `MNG=refs/mac-nativegames/games/totk/third_party/d2r_gfx`.
Log context: BOTW 1.9.0 long frames are ~75 ms / 304 first-time uploads /
32 MB with 0 new pipelines on 133 of 135 long frames — synchronous,
submit-thread, first-upload deswizzle+blit+commit work landing inside one
frame, not shader compile.

## 1. Texture upload batching / scheduling

### 1a. Same 128 MB cap, different split, residency and commit integration

Both batch all uploads of one replay submission into one upload
`MTL4CommandBuffer` (compute deswizzle + unified blit, barrier, then blit),
flushed before the main command buffer commits so the same Metal queue orders
uploads before first sample. Both cap a batch at 128 MB. The differences are
*what counts toward the cap, when a batch splits, what staging stays
resident, and which thread/commit path the batch joins.* nxnative splits
lazily inside `up_alloc()` on `g_up_staging_bytes`; keeps 3 slot blocks + 1
overflow each resident; commits via the encode-thread record path
(`rec_commit_upload`) and wakes waiters with a condvar. MNG reserves up front
(`up_reserve`/`up_pair_alloc`/`up_cpu_reserve`), pools 6 private + 4 shared
blocks that stay resident, forbids splitting inside one texture's dependent
buffers, commits directly with `q4_commit`, and wakes waiters by polling /
`retire_idle_kick`.

Anchors:

* NX batch contract + sizes:
  * `NX/dm/gfx/nvn/nvn_mtl.m:3204-3209` packaged-upload comment (stamp→protect→read→deswizzle→blit, one upload CB per replay submission, 32 MB private blocks).
  * `NX/dm/gfx/nvn/nvn_mtl.m:3228` `#define UP_BLOCK_SIZE (32u*1024*1024)`.
  * `NX/dm/gfx/nvn/nvn_mtl.m:3255` `#define UP_BATCH_BYTES (128u*1024*1024)`.
  * `NX/dm/gfx/nvn/nvn_mtl.m:3229-3234` slot retention: 3 slots, `g_up_slot_blk/over`, `UP_SLOT_BLOCK 4MB`, grow to previous batch demand, max 64 MB/slot, 192 MB total, `DM_NVN_UPLOAD_REUSE`.
  * `NX/dm/gfx/nvn/nvn_mtl.m:3331-3350` `up_begin()`; `3338-3339` `rec_publish()` then `up_wait_zero(&g_up_alloc_busy[slot],WS_UPALLOC)`.
  * `NX/dm/gfx/nvn/nvn_mtl.m:3219-3226` `up_wait_zero()` 10 ms cond-timedwait + `WS_*` ledger.
  * `NX/dm/gfx/nvn/nvn_mtl.m:3368-3410` `up_alloc()`; `3373-3377` split test `g_up_staging_bytes && extra > UP_BATCH_BYTES-MIN(...)` → `up_flush(); up_begin();`; `3379-3386` first-block from slot, `3388-3406` block-switch to `g_up_keep` (leak fix + `g_up_swaps` diag).
  * `NX/dm/gfx/nvn/nvn_mtl.m:3267-3329` `up_flush()`; `3273-3283` second compute encoder for blits + queue/device barriers; `3297-3317` `g_up_pending` + feedback retires `staging`/`views`, `res_commit()`, clears `g_up_alloc_busy[slot]`; `3320` `rec_commit_upload(cb,o)`; `3323` `if(!g_cb) rec_publish()`.
  * `NX/dm/gfx/nvn/nvn_mtl.m:1588` `q_cb() { up_flush(); rec_drain(); up_wait_zero(&g_up_pending,WS_OLDQ); }`.
* MNG batch contract + sizes:
  * `MNG/gfx/nvn/nvn_mtl.m:4937-4939` same batch comment.
  * `MNG/gfx/nvn/nvn_mtl.m:4949` `UP_BLOCK_SIZE 32MB`; `MNG/gfx/nvn/nvn_mtl.m:5067` `g_up_batch_bytes, g_up_batch_limit=128u<<20`; `MNG/gfx/nvn/nvn_mtl.m:5047` `UP_SBLOCK_SIZE 8MB`.
  * `MNG/gfx/nvn/nvn_mtl.m:5033-5048` pooled-resident comment + `#define UP_POOL 6 / UP_SPOOL 4`, `UP_POOL_USE/SPOOL_USE` (2 on iOS), `g_up_pool/spool`, `g_up_pool_busy/spool_busy`.
  * `MNG/gfx/nvn/nvn_mtl.m:5054-5062` `up_pool_take()`; `5236-5261` `up_alloc_block()`; `5212-5217` `up_reserve()`; `5193-5211` `up_begin()` with `g_up_slot_mu/cv` wait loop + `g_up_slot_waits/ns`.
  * `MNG/gfx/nvn/nvn_mtl.m:5264-5292` `up_pair_alloc()` atomic reservation of `source+first+second` via simulated packing (`up_pair_extra`), `5287-5289` flush-before-split so a texture's deswizzle output + transcoder input never split across batches; `5293-5299` `up_cpu_reserve()`.
  * `MNG/gfx/nvn/nvn_mtl.m:5115-5190` `up_flush()`; `5121-5146` blit + `bcd`/`bc1astc`/`bctc` dispatches with encoder barriers; `5152-5158` staging snapshot; `5159-5178` feedback clears `pool_mask/spool_mask`, `g_up_alloc_busy[slot]`, `g_up_pending`, calls `retire_idle_kick()`; `5181-5183` `res_commit(); cb_submit_label(...,"upload"...); [cb end]; q4_commit(cb,o)`.
  * `MNG/gfx/nvn/nvn_mtl.m:1679` `q_cb() { up_flush(); while(g_up_pending) usleep(200); }` — spin-poll, no `rec_drain`, no encode thread.
  * `MNG/gfx/nvn/nvn_mtl.m:3557-3560` `mtl_finish_gpu(){ mtl_wait(g_fence); while(g_up_pending) usleep(1000); }` vs `NX/dm/gfx/nvn/nvn_mtl.m:2804-2808` `mtl_finish_gpu_at(){ mtl_wait_at(g_fence); rec_publish(); up_wait_zero(&g_up_pending); }`.

**Finding: explains area-entry upload hitches.** Both allow a single submission to synchronously encode ~32 MB / hundreds of textures on the submit thread — the `304 / 32 MB / 75 ms` signature. Three nxnative-specific aggravations: (i) split accounting uses `g_up_staging_bytes` (allocated staging length) not batch bytes, so a burst of small textures reusing one 32 MB block can pack far more *work* into one batch before splitting; MNG counts per reservation and splits atomically per texture pair; (ii) nxnative's `rec_commit_upload` + `rec_drain` path keeps the batch as a record in the encode thread's block until drain; MNG commits straight to its queue and only polls; (iii) MNG's `up_pair_alloc` guarantee avoids the mid-texture split nxnative explicitly permits. MNG's larger pool (6+4 vs 3+1) also avoids per-frame `newBuffer` + `res_add` churn that shows as `res_commit` kernel time.

### 1b. Staging buffer kinds, zero-copy, direct/partial paths

nxnative added two submit-thread CPU savers MNG lacks — zero-copy source buffers (`up_big_src`) and partial re-uploads (`pkg_written` / `DM_NVN_PARTIAL_UPLOAD`) plus a GPU-direct deswizzle path (`DM_NVN_DIRECT_UPLOAD`) — but first uploads still go through private staging + compute deswizzle on the submit thread. MNG instead has a second *shared* staging pool for CPU-staged textures and does BC1-5 GPU decompression (`bcd`) and BC→ASTC/EAC transcoding (`bc1astc`/`bctc`) inside the same upload batch.

Anchors: `NX/dm/gfx/nvn/nvn_mtl.m:3625-3637` (`up_big_src`), `:3642-3657` (`pkg_written`), `:3442-3443,3664,3698,3705,3713` (`DM_NVN_DIRECT_UPLOAD`), `:3210-3211` (submit-thread accounting); `MNG/gfx/nvn/nvn_mtl.m:5048-5052,5249,5679` (two pooled block kinds), `:4958-5030` (`bcd`/`bc1astc`/`bctc` MSL + job dispatch in `up_flush`).

**Finding: neutral for first-upload bursts, favors nxnative for re-uploads.** First uploads are whole in both, so neither avoids the 304-texture burst.

## 2. Texture pre-warm and first-upload triggers

### 2a. No texture pre-warm on either side; both resolve lazily at bind/draw

Both pre-warm *shaders/pipelines/libraries* only. Textures are created on first resolution during replay (draw, copy/clear source, present, or `ReadTexels`): entering a new area means hundreds of `pkg_upload` deswizzles back-to-back on the submit thread. No `tex_*warm`, no upload-batch warm-up, no boot-time texture walk exists in either tree.

Anchors: NX `nvn_mtl.m:6078` (`warm_start` shaders only), `:10233-10240` (prebuild pulling bases), first-upload path `:4552` (`tex_from_storage`), `:4939-4952` (`pkg_upload` first fill); MNG `nvn_mtl.m:11169` (`warm_shader_load`), `:11258-11369` (`warm_build_rows`), first-upload `:5788-5858` (`tex_mtl_p`, `pkg_upload`).

**Finding: neutral — both hitch-prone by design.** Area entry necessarily faults hundreds of cold textures on the frame that first binds them. The differentiator is per-use checking cost (next), not pre-upload.

### 2b. What triggers the check, and how often

MNG checks once per submission per texture (`csub==g_sub_serial` early-out, dirty-tracker shortcut) and skips re-lookup when no guest write occurred. nxnative checks per use but memoizes within the submission (`pk->sub/pk->own` vs `g_sub_serial`, `g_pkg_kept`, edge-byte guards).

Anchors: `MNG/gfx/nvn/nvn_mtl.m:5809-5822`; `NX/dm/gfx/nvn/nvn_mtl.m:4892-4913`.

**Finding: favors MNG slightly on steady-state, neutral on cold bursts.** Cold first-upload still pays full deswizzle in both.

## 3. Vertex / uniform data paths

### 3a. nxnative: shadow inputs + CPU conversion + pulling pipelines (hybrid)

Three tiers: (1) shadow-input variants (`dmsh_vin.py`), (2) CPU conversion at draw time (`vconv_bind` → `arena_alloc` + `vconv_fill`, per-submission 64-entry memo), (3) GPU pulling pipelines (no vertex descriptor, per-draw `pull_bind` layout block). Pipeline-compile stalls are hidden via hybrid stand-ins + background builds (`pso_pend_demand`, `USER_INITIATED` prediction, `UTILITY` background queue).

Anchors: `NX/dm/gfx/tools/dmsh_pull.py:2`, `dm/gfx/tools/dmsh_pull.metal:1`, `NX/dm/gfx/nvn/nvn_mtl.m:8067-8148` (tier selection), `:8338-8375` (`vconv_bind`), `:10150-10188` (hybrid), `:10067-10124` (background queues), `dm/gfx/nvn/nvn_rec.h:1-16` (encode thread).

### 3b. MNG: direct Metal vertex descriptors + MSL retyping, no pulling, no CPU conversion

NVN formats map straight to `MTLVertexFormat` (incl. integer formats read as float inputs); type mismatches recompile a variant MSL with redeclared inputs (`vs_vin` → `msl_retype_inputs` → `vpatch` cache), never per-draw CPU copies. Unmapped formats fall back to a constant buffer-30 value. No pulling stand-ins: background work is parallel pipeline precompile with scan-ahead (`pso_pend`, `TOTK_PSO_RESCANS`).

Anchors: `MNG/gfx/nvn/nvn_mtl.m:9642-9673` (`vfmt`), `:10109-10139` (retype), `:10795-10804` (constant fallback), `:10846-10859` (scan-ahead).

**Finding: favors MNG for submit-thread smoothness, favors nxnative for correctness/completeness.** MNG never stalls the submit thread converting vertex data; nxnative's hybrid means a pipeline miss never blocks the draw. Second-order to texture uploads for the reported symptom.

## 4. Synchronization / readback stalls

### 4a. Fence waits: nxnative ledger + fast-wake vs MNG sync-lag relaxation

nxnative waits precisely for the fence the game asked for (wait ledger `DM_NVN_WAITS`, GPU-signal fast path). MNG by default (always for BOTW via `SYNC_LAG_DEFAULT=1`) relaxes a wait on the newest fence to the previous submission's fence, trading one frame of CPU/GPU overlap for lower blocking.

Anchors: `NX/dm/gfx/nvn/nvn_mtl.m:280-317` (ledger), `:2760-2799` (`mtl_wait_at`), `NX/dm/gfx/nvn/hle_nvn.c:876-887` (`nvnSyncWait`); `MNG/gfx/nvn/hle_nvn.c:860-917` (`SYNC_LAG` relaxation), `MNG/gfx/nvn/nvn_mtl.m:3545-3555` (plain timedwait, no fast-wake).

**Finding: favors MNG for BOTW area-entry hitches.** BOTW's main thread does `SyncWait(newest)` per frame; MNG lets the CPU run one submission ahead, overlapping the next frame's upload encoding with the previous frame's GPU execution — directly shrinking the observed 75 ms stall even with identical total upload work. nxnative's `DM_NVN_CUT` segmentation splits *one* submission rather than overlapping frames.

### 4b. GPU-written texture readback: nxnative inline `gsb_store` vs MNG deferred writeback + late landing

Largest architectural fork. nxnative has NO writeback tables: GPU→CPU visibility is done inline in the submission (`gsb_store`/`gsb_store_buf`/`rt_mirror_*` after each pass, on the submit thread's blit encoder). MNG notes rendered textures and copies them out at `sub_commit` end, landing bytes in the completion handler (`late_land`) after a dirty check, with fence/eager knobs.

Anchors: NX `nvn_mtl.m:4344-4354` (`gsb` tracking), `:5552-5576` (`gsb_store`/`gsb_store_buf`), `:5578-5599` (`rt_mirror_*`); MNG `nvn_obj.h:238-257` (writeback staging), `nvn_mtl.m:3906-3932` (`wb_note`), `:24590-24711` (`g_late_*`, `late_land`, `botw_late_commit`), `:4363` (back after game fence waits).

**Finding: each side pays differently.** nxnative's inline stores extend the critical path of the frame that first renders new targets; MNG moves that work to completion handlers. For pure first-sample area textures neither path runs (second-order); for GPU-written mirrors/shadows learned on entry, first-order.

### 4c. Present snapshots

Both copy the presented texture into a ring slot so the game can reuse its texture immediately. nxnative's `snap_drain`/`snap_take` can block present/acquire (`g_snap_ev`, bounded); MNG's retire is fire-and-forget.

Anchors: `NX/dm/gfx/nvn/nvn_mtl.m:17395-17472`; `MNG/gfx/nvn/nvn_mtl.m:3538`.

**Finding: neutral-to-MNG.** nxnative's snapshot waits are bounded and rarely hit, but another place a frame can block on GPU completion.

## 5. Throttling, batching knobs, background threads, residency

nxnative parallelizes *encoding* (encode thread) and *pipeline builds* (prediction + hybrid + prebuild) but keeps uploads, vertex conversion and residency commits on the submit thread. MNG parallelizes pipelines/variants and retirement, pools upload/argument/table/readback buffers, and keeps the GPU clock up with keep-alive spin kernels. Neither throttles uploads by time (128 MB cap only) — a 32 MB/304-texture burst always fits one batch and always stalls the submitting frame.

Anchors: NX `nvn_rec.h:1-16` (encode thread QoS), `nvn_mtl.m:10042-10147` (`pso_pend` bounds), knobs `DM_NVN_UPLOAD_REUSE/DIRECT_UPLOAD/PARTIAL_UPLOAD/PSO_HYBRID/ENC_THREAD/PS0_PREBUILD`; MNG pool/reserve knobs (`TOTK_UPLOAD_MEMORY_FILE` accounting), `retire_idle_kick`, buffer caches, `TOTK_GPU_KEEPALIVE`, QoS map.

**Finding: favors MNG for upload-heavy frames.** MNG's pooled-resident staging (explicitly blames per-batch residency commits for ~1.5 ms/frame and fixes with pooling) + retire-idle batching + buffer caches + keep-alive directly attack the non-pipeline frame cost that dominates the log.

## Prioritized takeaways for the 304-texture / 32 MB stall

1. **Batch-all-in-one-submission on the submit thread — primary.** No texture prefetch on either side; a new area faults hundreds of cold textures in one submission; 32 MB fits the cap so it never splits. nxnative's staging-byte accounting + record/commit path make the stall likelier to land fully inside the frame's submit call.
2. **No texture pre-warm on either side — necessary condition.** Shader-only warm-ups leave area textures cold on both.
3. **MNG's SYNC_LAG + deferred writeback hide the same work — why MNG hitches less.** One-submission-ahead overlap plus completion-handler copies: same bytes, less blocked time.
4. **Vertex CPU work is nxnative-only — secondary.** Per-draw `vconv_fill` + arena alloc vs MNG's descriptor+variant approach.
5. **Residency/retirement batching — tertiary but measurable.** Pooled buffers + idle retire vs per-batch allocate/commit on the critical path.

## Empirical confirmation (local BOTW 1.9.0 run, this workspace)

133 of 135 longframes show `new pipelines 0` (max observed: one frame with 6): the remaining hitches are texture uploads (e.g. frame 621: 99 first uploads, 24.3 MB) and GPU-bound frames (17–21 ms GPU vs 16.7 ms budget), not pipeline compiles. Prewarm covered 18,224 pipelines (community overlay) with 0 stale keys and 0 pipelines without a base; `draws that waited` empty; hybrid stand-ins covered first-seen draws. The shader side is converged — the residue is streaming + GPU budget.

Gate 1 verdict (2026-11-16, numbers in `instrumentation/ab_results.md`): a
texture-count split (`SWX_UPLOAD_SPLIT`) was built and measured +13 ms submit
on the identical burst with no encode moved off the submit thread — so the
patch and its docs were removed (only pooled resident staging remains as the
batching answer). Takeaway 1 stands, sharpened: byte-count *or*
texture-count splitting cannot fix a submit-thread encode bottleneck —
only overlap (CPU ahead of GPU) or pooled resident staging removes it.

## Open: unattributed submit blocks (frame 755 family, 2026-11-16)

Long run (19,854 frames, `SWX_TEXTURE_POOL=1`): frame 755 whole 26.3 ms with
submit+present 24.9 ms against GPU 0.3 ms, 0 pipelines, 4 uploads / 0.8 MB /
0.1 ms encode — ~24 ms of submit with no owner in the log. Same family as
the earlier 77/69 ms freeze with no attributed cause. Not reproducible on
demand; next step when it recurs is a `DM_NVN_WAITS=1` run to name the
waiter. Upload, pipeline and GPU causes excluded by the longframe
decomposition above.
