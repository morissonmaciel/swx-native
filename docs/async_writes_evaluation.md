# Async writes evaluation: saves first, pipelines next

Scope decision (2026-11-16): saves first — user-felt spike, smaller blast
radius (no GPU semantics), simple integrity test (`kill -9` → save must open
valid). Pipelines sketched in §6 as slice 2. Branch: `feat/async_writes`.

## 1. Measured baseline (no code — 10 runs, `instrumentation/ab-*` + live log)

| Corpus | save commits | clone+swap p50/max | txn span max | ser held-back | archive writes |
|---|---|---|---|---|---|
| 10 BOTW runs, same region | 2–4 per run | 2.2–3.1 ms / 3.8 ms | 52 ms | never observed | 0–1 per run, max 23–46 ms |

Read: the commit itself costs 2–4 ms of `clonefile`+`RENAME_SWAP` on the
committing game thread, under `PATH+g+txn` locks — small alone, enough to
tip an already-heavy frame over 16.7 ms. No longframe isolates a commit
alone in this corpus, consistent with "tip-over, not sole cause". The
`ser_begin` compile-stall path never fired here (0 held-back everywhere);
archive serialize hit at most 46 ms once per run. The 151.3 ms libraries
frame (`ab-lag-on-snap4`, frame 3416) is synchronous pipeline *creation*,
not archive I/O — separate mechanism, noted for slice 2 triage.

## 2. Current NX save path (anchors)

- HLE: `dm/host/hle_fs.c:1005-1013` `h_fs_Commit`, `:1019-1040`
  `h_fs_CommitList` → `dm_thread_service_request` (calling thread blocked).
- Engine `commit_mount_locked` (`:953-1003`): caller holds `PATH+g`;
  `g_txn_lock` write-lock (`:956-958`); `clonefile(work→.new)` (`:994`) +
  `renamex_np(RENAME_SWAP)` (`:995`) + `rm_tree(old)` (`:996`); refused path
  clones+swaps `.re` under the same locks (`:982-983`).
- Cloud sender re-clones the committed tree under the same locks
  (`:944-950`, comment `:944` admits the coupling) 3 s after quiet
  (`dm/host/save_sync.c:586-606`, `QUIET_MS=3000`), then packs with `fsync`
  (`:188`) and up to 10 s fetch (`:555`). Mount path can block on fetch too
  (`:611-681`).
- `FlushFile` is a no-op (`hle_fs.c:1178`); durability exists only at Commit,
  via APFS clone+swap (`:207-208`).

## 3. MNG mirror (what "good" looks like)

`commit()` = `fcntl FULLFSYNC` per open fd + return
(`runtime/fs_host.c:231-240`); copy 3 s later on serial queue gated by
`save_busy` (`runtime/cloud_save.m:390-402`); quit/BG flush (`:355-386`).
No clone on Commit, no frame blocked on cloud I/O, no lock coupling between
commit and sender beyond a 3 s quiet gate.

## 4. Options, with trade-offs

**S1 — full MNG mirror** (`commit` = FULLFSYNC + dirty flag; background
serial queue does clone+pack+cloud after quiet): removes the whole 2–4 ms +
`rm_tree` + contention from the frame. Cost: durability semantics change —
on Switch `Commit` is synchronous by API, and the game may (must verify, not
assume) depend on read-after-commit durability. Crash window: committed but
not yet copied data lives only in `.work`. Biggest win, biggest proof burden.

**S2 — sync swap, async rest (recommended first)**: keep `clonefile` +
atomic swap on the committing thread (cheap metadata ops, preserves the
exact durability/atomicity contract the game sees today), move `rm_tree(old)`
+ the cloud sender's re-clone off the critical path (background queue after
quiet, same locks but never while a frame waits on them). Blast radius:
cleanup ordering only; commit semantics unchanged. Expected residue: ~2 ms
swap still on-thread — re-measure; escalate to S1 only if frames still tip.

**S3 — debounce** (only if measurement shows commit bursts): coalesce rapid
re-commits. Not indicated by current data (2–4 commits/run, seconds apart).
Parked.

Recommendation: S2 under `SWX_SAVE_ASYNC=1` (`DM_SAVE_ASYNC` legacy alias;
`SWX_SAVE_ASYNC` wins when both are set, both absent means on), documented
as step 1 of the switch's meaning; S1 stays a documented escalation, not this slice.

## 5. Slice 1 plan + gates — IMPLEMENTED 2026-11-16, pending Gate 1

- Prototype: `patches/dm_save_async.patch` (single file, `hle_fs.c`,
  switch `SWX_SAVE_ASYNC` with `DM_SAVE_ASYNC` as legacy alias, on when
  both absent, `=0` restores stock) — S2 as designed, superseded by
  `patches/swx_save_cleanup_worker.patch`: a persistent
  `QOS_CLASS_BACKGROUND` cleanup worker (FIFO depth 4, per-generation
  `mkdtemp` staging, orphan recovery on mount) replaces the plain
  joinable pthread (no queue lifecycle; pre-clear join bounds reuse).
  Only the post-swap `rm_tree` leaves the frame; clone+swap stay
  synchronous, so the durability contract is unchanged and no
  read-after-commit audit was needed for this slice (S1 would need it).
  Refused-commit path and cloud sender untouched. A/B via
  `instrumentation/ab_test.py set save on/off` on a single install
  (same save, same route): `on`/`off` write only `SWX_SAVE_ASYNC=1`/`=0`
  and drop the alias line, `clear` removes both.
- Prerequisite audit (done 2026-11-16, no code needed): S2 keeps the swap
  synchronous, so durability is identical — no read-after-commit proof
  required. Commit-path coverage, verified by code + log: all four commit
  entry points (`h_fs_Commit`, `h_fs_CommitList`, `h_fs_CommitOpt`,
  `h_fs_CommitSaveData`, `hle_fs.c:1034-1073`) funnel into
  `commit_mount_locked` (`:969`), where the patch lives — manual save,
  autosave and in-game config save all commit the same transaction
  (log shows 1, 2, 7, 12, 17 writes across 5 commits on one mount;
  BOTW mounts no `_device` save and no SystemSaveData handler exists).
- Gate 1 (correctness): zero `[RED]`, save/load + shrine + teleport clean,
  no new longframe class. Kill -9 timing is impractical by hand;
  construction argument instead (swap atomic unchanged, fallback sync,
  orphans cleaned by pre-clears) + refused paths covered by the upstream
  `tests/t_fs.c` suite. **PASS 2026-11-16** (3 runs, 3046–26254 frames).
- Gate 2 (numbers): same fixed route, `collect`/`compare` — commit lines
  off the frame path (clone+swap timings move to the background queue's
  log context or vanish from frame-attributed cost), zero new longframe
  class, holds unchanged-or-better. Success = save cost no longer
  frame-attributed, not a fps number. Pass = latest save loads, zero FS
  errors, no new reproducible hitch; cadence benefit inconclusive if
  back-to-back saves never overlap an in-flight cleanup.
- Decision: keep env-gated; promote to default only with a second corpus
  (different area, longer session).

## 6. Pipelines sketch — slice 2a IMPLEMENTED 2026-11-16, pending Gate 1 (brought forward per user call; archive batching stays parked)

`patches/dm_pso_user_async.patch` (single file, `nvn_mtl.m`, switch
`DM_PSO_USER_ASYNC`, default off): `pso_user_record` keeps dedup, appends
the line to an in-memory array under one leaf lock (submit, prefetch and
warm paths alike), flushes async every 64 rows on a dedicated serial
UTILITY queue, drains inline at `atexit`. Crash loses only unflushed rows
— re-learned next run (same self-healing as a torn tail). A/B via
`instrumentation/ab_test.py set pso on/off`.
Remaining parked: (b) archive batch-of-N off-thread with play-never-blocks
invariant; (c) audit the 151 ms creation path separately (may be variant
coverage, fatia 3 territory). Each with kill -9 + A/B gates cloned from §5.

Stall points: `ser_begin` writer-first (`nvn_mtl.m:9404-9424`, 2 s timeout;
never fired in corpus), archive serialize+rename (`:9811-9835`, ≤46 ms
observed), `fflush` per `pso_user.tsv` record (`:6310-6323`), synchronous
library creation on submit (151.3 ms once). MNG shape: thread-local batches
of 8 serialized off-thread, play records the key only
(`totk_pipeline_archive.h:22-27,325-355,404-408`). Slice 2 order:
(a) batch `pso_user` in memory, flush on interval/quit (crash loses learned
rows only — self-healing, proven by frame 3416's rows landing post-hoc);
(b) archive batch-of-N off-thread with play-never-blocks invariant;
(c) audit the 151 ms creation path separately (may be variant coverage,
fatia 3 territory). Each with kill -9 + A/B gates cloned from §5.
