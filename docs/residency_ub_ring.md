# Uniform-update residency ring

Submission stability, not a GPU fix: `mtl_sub_update()` bump-allocates
uniform data into shared buffers. Every exhaust used to allocate a new
buffer, add it to the `MTLResidencySet`, and defer-release (+remove) the
old one. On the BOTW luneta route that was 34–41 fresh 1 MB buffers per
hot window (`nvn_mtl.m:2373`), +34–41 MB to the set and 3.1–5.2 ms in
residency commits per window.

## Mechanism (`patches/dm_residency_ub_ring.patch`)

`SWX_RESIDENCY_UB_RING=1` (install-pinned default; `=0` restores the
legacy path for A/B) keeps up to 4 settled shared buffers registered in
the residency set. An exhausted bump reuses an entry only after its retire
fence completed; a buffer referenced by an in-flight command buffer never
returns. Submission fences are assigned once in `mtl_sub_commit`
(`++g_fence`; `sub_cut` segments share it and complete before it on the
same queue): the rotated-away entry is stamped with `g_fence+1`, the
current one at commit. Under pressure (ring full, entry over 4 MB) the
write falls back to the legacy one-shot path instead of retaining
unboundedly. No queue-level residency changes, no staging/texture-pool
changes, no altered lifetimes.

## Gate (2×2, luneta)

Same route (pan 10 s, open 5 s, close 5 s + save/load, shrine, teleport),
keepalive+yield on, pool off, VFR relaxed/auto, 2 boots per side.

| Resultado | Ring ligado | Ring desligado |
|---|---:|---:|
| Rotação UB quente | 0 | 34–41 MB/janela |
| Commits residency | 0,1–1,3 ms | 3,1–5,2 ms |
| Frame p95 | 16,72–16,74 ms | 16,73–16,75 ms |
| [RED] / VFR | 0 / estável | 0 / estável |

After warm-up the on-side shows stable reuse with zero fallback and zero
new allocations on the hot stretch. Two GPU-bound longframes appeared on
the off-side (frames 1924 and 2014, ~17 ms GPU, no uploads); they are not
used as standalone proof of GPU benefit.

## Status

Promoted as a submission-stability / resource-availability optimization.
Explicitly not a fix for the luneta's 20–24 ms GPU cost, which stays with
passes and attachments. Counters on the power line every 300 frames
(`reuse, new, inflight, peak, fallback, KB reused`); no per-write logging.
