# Texture burst: measurement, pool evaluation, and grouped-dispatch plan

Reference for the completed parser and pool/bind-cache A/B, the current
upload attribution format, and the deferred grouped-dispatch prototype.
The managed BOTW install enables the repaired staging pool. Bind-once
remains experimental and off in the managed environment.

## 1. What the current evidence says

Archived area-entry captures show that upload bytes alone do not predict a
hitch. One run uploaded 56.7 MB without an eligible empty display tick; another
had a 28.5 MB burst (146 first uploads) associated with a user-felt hitch and
the only empty tick/drop in its window. An older capture recorded 222 first
uploads / 41.3 MB, 5.2 ms of measured upload work, but a 55.8 ms frame and
55.7 ms submit. These are window/frame correlations, not proof that a
particular upload caused a missed refresh. Counts of texture objects, mip
levels, command encoding, residency work, and unrelated submit work all need
separate measurements.

Prior instrumentation attributed much of an earlier 237-texture burst to
GPU-deswizzle dispatch encoding and its records, while allocator, residency
commit and waits were smaller. The count-split experiment increased batches
and submit cost (8 batches / 44.2 ms versus 6 / 31.1 ms on its matched burst),
so splitting more often is not a default remedy. See
`docs/upload_overlap_plan.md`, `docs/texture_streaming_hitches.md`,
`docs/io_paths_nxnative_vs_mng.md`, and `instrumentation/ab_results.md`.

## 2. Upload measurement format

The parser accepts both historical upload lines: `oldq` followed by `]`
and `oldq ... + path ...` before `]`. Path durations may be absent even
when counts are present. Unavailable measurements are `null` in JSON and
`n/a` in text.

### Structured burst records and collection

The host writes these records to the `DM_NVN_TSV` timing file only; they do
not appear in `game.log`. The schema 1 events are `encode` (one per upload
batch), `publish` (publication boundary), `gpu` (GPU completion feedback),
and `overflow` (records dropped because the bounded capture table was full).
The encode, publish, and GPU events carry producer-supplied `batch`, `frame`,
and `submission` IDs. `slot_waits`, texture/mip/layer/dispatch/blit/byte
counts, and `dropped` are integers. Timing fields currently include encode
durations `dst_wall_ns`, `protect_wall_ns`, `origin_wall_ns`,
`deswizzle_wall_ns`, `flush_wall_ns`, `commit_wall_ns`, and `wait_wall_ns`,
publish `queue_ns`, and GPU `gpu_ns`. They are independent measured intervals;
do not sum them into a synthetic total. `complete_ns` is an absolute monotonic
completion timestamp, not a duration. Absent timing is omitted while a
measured zero remains zero. Unknown fields are preserved for forward
compatibility.

In the current host patch, `dst_wall_ns` brackets destination texture
descriptor/allocation and residency registration, and `protect_wall_ns`
brackets dirty/source protection. `origin_wall_ns` covers obtaining the
upload source address or sparse page table, and `deswizzle_wall_ns` covers
deswizzle dispatch encoding (or CPU deswizzle work on its fallback).
`flush_wall_ns` brackets ending upload encoders and preparing the upload
commit; `commit_wall_ns` brackets the commit path through resource/command
buffer publication. `wait_wall_ns` measures the uploader's wait for a reused
staging slot, and `slot_waits` records whether that slot was observed busy.
`queue_ns` starts immediately before `rec_commit_upload` queues the upload
record and ends immediately before the real Metal commit; in encode-thread
mode it includes the record-to-real-commit delay. `gpu_ns` is the GPU command
buffer interval when counter timestamps are available. These spans may
overlap or include nested work; keep each reported field separate.

Joins require an exact match of all three IDs (`batch`, `frame`,
`submission`); batch-only or nearby-line matching is not valid. The report
publishes per-key presence for encode/publish/GPU and explicitly lists missing
event types and events without a complete key. A missing GPU record is
reported as missing data, never inferred as zero GPU cost. `log_report.py`
accepts `--tsv <path>` for direct analysis; `ab_test.py collect` passes its
copied `timing.tsv` into report generation so `report.json` includes these
events and joins. Legacy game-log parsing remains unchanged.

`log_report.py` retains each event and summarizes each duration field by
event kind (sample count, sum, p50, p95); it reports encode/publish/GPU joins
only when all three IDs match. Missing or mismatched IDs remain unmatched.
It does not add phase durations into a synthetic total. The phase names and
boundaries above are those emitted by the host patch; GPU duration is reported
separately from host intervals. Do not treat `complete_ns` or wall-frame time
as CPU encode cost.

For a capture, keep the installed pool setting on and leave bind-once absent
from `env.txt`. Set a unique `DM_NVN_TSV` path for that run, with
`DM_NVN_TSV_CALLS=0` and `DM_NVN_WAITS=1`. After the game exits, collect using
`ab_test.py collect <label> --tsv <same-path>`; if `DM_NVN_TSV` was set in the
application env.txt or collection process environment, the harness reads that
path automatically. The collection stores `timing.tsv`
beside `game.log`, `env.txt`, and `report.json`. Missing an explicitly named
TSV is a collection error rather than a silently incomplete capture. Preserve
the source TSV and all raw IDs when comparing logs.

## 3. Pool and bind-cache results

The four-arm comparison and combined follow-up are in the
[A/B results ledger](../instrumentation/ab_results.md#texture-bursts--22-staging-pool-and-bind-cache-2026-11-18).
The repaired pool was enabled for BOTW after two clean pool-only runs.
The results do not establish a general performance gain or support promoting
bind-once.

The staging pool reports cumulative reuse, cold allocations and misses.
Compare deltas across reports. Reserved bytes are useful staging demand,
while resident bytes are materialized private pool capacity. Neither
measures destination texture memory.

## 4. Reuse assessment: existing experimental work

| Candidate | Reusable part | Why it does or does not address texture bursts |
|---|---|---|
| `SWX_GPU_TEXTURE_POOL` / repaired pool patch | resident private **staging** buffers only, with reuse/retire accounting | Enabled in the managed BOTW environment after two pool-only runs; this is not a core-wide compiled default or proof of frame-time gain. It may reduce repeated staging allocation and `res_add`/`res_commit` churn. It does not pool destination `MTLTexture` objects, remove per-texture deswizzle dispatches, or guarantee fewer encoded commands. |
| `SWX_GPU_BIND_ONCE` | cache stable Metal bindings across consecutive eligible jobs | Can reduce repeated CPU command-encoder state setup; it does not reduce bytes, texture creation, dispatch count, or GPU decode cost. Must invalidate whenever encoder or resource state changes. |
| RT heap reserve (`SWX_RT_HEAP_RESERVE`) | heap-backed transient private render targets | Not a texture-upload pool. Render-target attachment heaps are a different resource lifetime and usage class; staging buffers and sampled destination textures are excluded. Do not include in the 2×2 burst A/B. |
| UB ring (`residency-ub-ring`) | uniform-buffer writes and residency ring reuse | Not a texture staging or sampled texture cache. It may alter unrelated frame pressure but confounds this experiment; leave at a fixed state, preferably off. |
| Atomic upload reservation / MNG `up_pair_alloc` | reserve a texture's dependent staging offsets together and flush before splitting the dependency pair | Relevant only if measurements show lazy split/packing or cross-batch dependency overhead. Reuse the invariant and accounting model, not a blind code transplant: NX record/commit ownership differs. It does not reduce per-texture dispatch encoding by itself. |
| GPU decode / transcode | GPU path for formats whose destination representation requires decode/transcode | Do not assume all BOTW burst textures are CPU-decoded: the current path already GPU-deswizzles supported formats. First classify formats and prove CPU fallback or conversion cost. MNG's BC decompression/transcode is useful only for formats/output paths that actually match. |

“One texture catalog” should mean a CPU-side job table that describes many
independent texture jobs, not one combined destination texture. Texture IDs,
dimensions, mip levels, formats, views, sampling state, and lifetimes remain
per texture. A catalog can help reserve all staging offsets and prepare one
ordered dispatch sequence, but it does not make heterogeneous destinations
alias-compatible.

### Bind-cache attribution and diagnostic isolation

Read-only review of `patches/dm_gpu_bind_once.patch` shows one cache suppresses
both `setArgumentTable:` and `setComputePipelineState:` together. Its key is
the upload encoder identity, a small `which` value for the two deswizzle
pipelines, and argument-table identity. Per-slice addresses/textures are
still written for each job. Explicit invalidations cover upload encoder
creation/end, blit encoder boundaries, sparse gather state changes, and the
depth/stencil detour. The cache is global and weakly references encoder/table;
its correctness therefore depends on all external state mutations continuing
to pass through those invalidation sites. Current user evidence does not prove
that this cache caused the combined-run hitches.

The recorder (`nvn_rec`) path is relevant because Metal state-setting calls
can become deferred recorded operations; eliding a call can reduce CPU record
traffic as well as encoder work. It also makes a timing-only comparison
ambiguous: the current flag bundles table and PSO suppression. If the new
records show binding/recording cost is material, isolate the two effects using
diagnostic builds with the same existing `SWX_GPU_BIND_ONCE` control: one
temporary build suppresses only the argument-table setter, another only the
PSO setter, with counters for attempted/emitted/skipped/invalidation events.
Do not introduce a user-facing flag or change the production patch for this
experiment. Keep the pool and route fixed, compare the same burst IDs, and
discard any arm that changes resource or dispatch behavior. Promote neither
sub-effect until repeatable frame and stage attribution supports it.

In the available `refs/mac-nativegames/games/la/runtime/gfx/nvn/nvn_rec.h`
recorder reference, compute argument-table and pipeline setters become
separate `RO_C_TABLE` and `RO_C_PSO` records; replay later performs the Metal
setter calls. Thus skipping either proxy call also removes its recorder entry
in a recorder-enabled build. The burst experiment must record whether
`nvn_rec` is active and hold that mode constant, or it would mix two effects.

## 5. Stage C — one grouped-dispatch prototype, only after Stage B data

Entry condition: Stage A attribution is complete and Stage B says pool/bind
changes leave meaningful CPU dispatch/setup cost, or show a repeatable burst
regression not explained by allocation, residency, GPU execution, or waits.
Then design one isolated prototype that builds a bounded job table for one
upload submission and encodes compatible deswizzle jobs through fewer encoder
setup transitions. Keep existing per-texture destination resources and
per-texture output ranges.

Required invariants:

1. Preserve replay submission order and all producer dependencies. A texture
   must be fully initialized, including required mip work, before any draw,
   copy, readback, or present samples it.
2. Preserve per-texture format, dimensions, swizzle parameters, mip offsets,
   row/array layout, and sparse residency behavior. Group only jobs with
   compatible pipeline/binding/encoder state; incompatible jobs remain on the
   original path.
3. Reserve each job's source and all dependent outputs atomically. Never split
   a texture's deswizzle output and subsequent decode/transcode input across
   batches without an explicit fence/order edge. On overflow, flush before
   the job and retry it intact.
4. Retain staging and destination objects until the GPU completion fence that
   consumes them. Pool reuse and texture destruction must not race the grouped
   command buffer.
5. Keep a correctness fallback to the existing per-texture path for unsupported
   formats, allocation failure, pool miss, or uncertain compatibility.

Prototype gate: compare baseline and prototype under the same two existing
flag states selected by Stage B; do not add a third control switch. Require
zero `[RED]`, no wrong/missing texture or mip, no new frame-step transition,
no lifetime/residency errors, and no increased missed-present/drop rate.
Performance evidence must show reduced CPU encoding/submit time on matched
bursts, not merely fewer log rows or a change in GPU duration. Also check
non-burst frames for no regression, then repeat on a second route/session.
Only after those results should the prototype be considered for a permanent
patch or package promotion.

## 6. Parser validation

Parser validation used archived `ab-new-save-early/game.log` and
`run-bindonce-long-20261116-225156.log`: the former's count-only `+ path`
suffix is now parsed while path durations remain unavailable; the latter's
count-plus-duration suffix populates all path totals, including `oldq`. A
recent VFR capture without the upload suffix reports those buckets as
unavailable. No synthetic core test or core build was run.
