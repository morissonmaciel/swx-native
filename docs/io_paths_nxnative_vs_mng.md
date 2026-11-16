# Bulk I/O: nxnative vs mac-nativegames (BOTW spikes)

Question: why does every bulk read/write on nxnative — area-entry texture
bursts, game saves to disk, new-pipeline archive writes — spike frametime,
and does mac-nativegames do I/O fundamentally better?

Verdict: yes, on all three moments. nxnative performs bulk I/O synchronously
on the frame's critical threads (submit game thread, commit thread, compile
threads); mac-nativegames never blocks a frame on bulk copies — CoW-free
commits, pooled resident staging, thread-local archive batches, deferred
retire on idle. Roots: `NX=core/nxnative`,
`MNG=refs/mac-nativegames/games/totk/third_party/d2r_gfx`,
`MRT=refs/mac-nativegames/games/totk/runtime`. Log context: BOTW 1.9.0,
`instrumentation/log_report.py` A/B runs (`instrumentation/ab_results.md`).

## 1. Save commit: synchronous whole-tree clone+swap vs FULLFSYNC + async copy

NX commits on the calling game/service thread, blocked throughout
(`dm_thread_service_request`, `dm/host/hle_thread.c:199`), under
`PATH+g+txn` locks (`dm/host/hle_fs.c:1006,956-958`):
`clonefile(work→.new)` + `renamex_np(RENAME_SWAP)` + `rm_tree(old)`
(`:994-996`), timed in the log (`:998-1000` — the `clone + swap 3171 us`
line). The cloud sender re-clones the committed tree under the same locks
(`:944-950`) after 3 s of quiet, and mount/fetch can block up to 10 s
(`dm/host/save_sync.c:32,397-418,611-681`). `FlushFile` is a no-op
(`:1178`); durability exists only at Commit, via APFS clone+swap (`:207-208`).

MNG `commit()` only runs `fcntl(fd,F_FULLFSYNC)` per open save fd and
returns (`MRT/fs_host.c:231-240`); the copy to the sync folder happens 3 s
later on a serial queue, gated by `save_busy`
(`MRT/cloud_save.m:390-402,56-64`), with quit/background flush (`:355-386`).

Net: NX stalls the committing frame on a whole-tree CoW clone+swap plus a
contended re-clone; MNG never clones on Commit and never blocks a frame on
cloud I/O. This is the leading hypothesis for the user's save-to-disk
frametime spike. No switch tunes it on either side — it is structural
(`DM_SAVE*` only selects folders, `save_sync.h:1-12`).

## 2. Texture upload reads: per-batch alloc/commit on submit vs pooled + GPU work

NX, per area-entry burst on the submit thread (`nvn_mtl.m:1575-1577,1588`):
guest reads via `pread` RomFS (`hle_fs.c:1092-1127`, no readahead/mmap),
stamp+protect (`nvn_mtl.m:3728-3730`), then per texture
`newBuffer(Shared)+res_add` (`:3824-3825`) plus CPU deswizzle/copy
(`:3836-3838`), per batch `newBuffer(Private)+res_add` (`:3436,3452-3453`)
and `res_commit` in `up_flush` (`:3367`). Residency commit costs 10–25 ms
for hundreds of MB; queued `res_add` measured 42–56 ms (`:154-157`).
`DM_NVN_DIRECT_UPLOAD` (`:3495`, on) and `DM_NVN_PARTIAL_UPLOAD` (`:3696`,
on) only reduce copies. The count split (`patches/texture_upload_split.patch`,
default OFF since Gate 1) reorders commits without removing any
allocate/register/commit — which is why 8 batches still cost 44.2 ms submit
(`instrumentation/ab_results.md`, Gate 1).

MNG: 6×32 MiB private + 4×8 MiB shared staging kept resident and recycled
(`MNG/.../nvn_mtl.m:5033-5062`, `res_add` once at `:5059`), BC→ASTC/EAC and
BC1-5 decompression on the GPU inside the same upload batch
(`:5123-5145`), table/readback fenced caches
(`nvn_buffer_cache.h:24-79`), retirement deferred to an idle UTILITY kick
(`nvn_mtl.m:737-765`), content-hash fast path and re-upload recycle
(`:5325-5346,5443-5492`), RomFS via `mmap` (`MRT/romfs.c:308-310`).

## 3. Pipeline persistence: serialize-behind-compile vs thread-local batches

NX: `ser_begin` is writer-first and holds back new compiles
(`nvn_mtl.m:9404-9424`, 2 s timeout), archive write on serial `g_arch_q`
(`:9636,9881`) with `serialize+rename` (`:9811-9835`, e.g. 0.8 MB/29 ms
with "compiles held back" counters), 10 s coalesce (`:9907-9911`), and
`fputs+fflush` per `pso_user.tsv` record (`:6310-6323`). A new-pipeline
frame measured 151.3 ms in libraries on the submit thread
(`instrumentation/ab_results.md`, frame 3416).

MNG: per-thread `ParBatch` flushed every 8 archives
(`totk_pipeline_archive.h:22-23,72-83,325-355`); play-time only records the
key for the next warm (`:404-408`) and never serializes.

## 4. Thread-block table

| Moment | NX blocks | MNG blocks |
|---|---|---|
| Area-entry textures | submit: pread + CPU deswizzle + alloc/commit per batch | encode with pooled staging; no per-batch commit; GPU decompress |
| Save to disk | commit thread: whole-tree clone+swap + locks (+ cloud re-clone) | nobody: FULLFSYNC, copy 3 s later on serial queue |
| New pipelines | compile threads on `ser_begin`; per-record fflush | play never serializes; thread-local batches of 8 |
| Boot lists | first submission waits prepare (`:10881-10884`) | parallel warm-fill threads, prebuilt archives taken as-is |

## 5. What is tunable today vs structural

Tunable now (no code): `DM_NVN_DIRECT_UPLOAD`, `DM_NVN_PARTIAL_UPLOAD`
(both already default on), `DM_NVN_UPLOAD_REUSE`, `DM_PSO_PREPARE=0`
(background the first-submit wait), `DM_ROMFS_OVERLAY`, `DM_FSLOG`
(measure the burst read pattern), `DM_NVN_WAITS=1` (quantify newest-fence
waits). Structural (future slices, each with its own gates): async save
commit (FULLFSYNC + deferred copy), pooled resident upload staging with
idle retire, thread-local archive batching. Order by evidence: save path
first (user-observed spike, smallest blast radius — no GPU semantics
involved), then staging pools, then archive batching.
