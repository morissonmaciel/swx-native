# Shader knowledge ingest: design (slices 3+)

How the community shader/pipeline knowledge (mac-nativegames' shared list)
becomes tables the nxnative host reads, maps one-to-one, and warms up on
every launch. Slices 1+2 (coverage measurement) are done
(`wrapper/shader_list.py`); this doc designs slices 3+.

## Piece 1 — Shader identity dictionary

Join MNG `V`-row keys (`SHA-256(control + code)`, full 64 hex) against the
profile's `dm_ctl_sha16` by `key[:16]`. Measured: 138/467 unique BOTW keys
covered. The `bound_control` subset (agl-embedded `0x848` sections, whose
bytes MNG rewrites before hashing) never joins directly; re-derive both key
styles locally from the player's extracted files (both algorithms are
documented, one per repo) and join the recomputed keys. Output: the covered
shader set for this exact game version.

## Piece 2 — Variant dictionary (the real DE-PARA)

MNG names a variant `<shader key>-<suffix>_b`, where the suffix is the first
12 hex of the bind-state answers' SHA-256; the runtime picks by matching deps.
nxnative enumerates `#var0, #var1, …` per shader entry. Open verification:
how `varN` is assigned — if it also derives from bind state, the mapping is
deterministic per (shader, deps). Fallback v1 (recommended): shader-level
coverage — force every nxnative variant of each covered shader into the
prebuild. More packaging compile time, always correct, no variant semantics
needed. `A` rows (392, bind states without joinable keys) resolve here, via
the seed's `.variants` index or via the v1 fallback.

## Piece 3 — Pipeline dictionary (BUILT: `wrapper/shader_convert.py`)

An MNG `R/B/T` row carries the runtime's 544-byte `pso_skey`, packed as
`<vs variant>:<fs variant>:<other 384 bytes hex>` with zero-runs compressed
(`z<hexcount>.`). The converter unpacks it and translates field-by-field
into nxnative's 512-byte `pso_skey` (C alignment explains both layouts: the
2-byte pad after `fz_desc` puts `k` at 88/200, matching their `SKEY_CFMT=216`
and our live 512-byte rows). Identical structs copy verbatim
(stages/merged/fz/`fz_desc`, formats, `va_st`/`vs_st`, `lmap`); names become
`<key16>_b`; `a2c` keeps only bit 0 (rows with layered/MetalFX/border/HDR
bits are skipped as another pipeline family); upper blend bits must be zero;
the nxnative-only tail (`atest/logic/attr_hi/border`) is zeroed in the
safe-miss direction. Hard skips (counted, never forced): unparsable packing,
unknown naming, nonzero recorded pointers, either shader missing from the
profile map (this is also the version filter).

## Piece 4 — Version filter

Only rows whose shaders exist in the player's extracted files enter the
derived profile; every other row is dropped after local key recomputation.
Both upstreams filter this way. Open verification: how nxnative reacts to a
manifest row with no shader — MNG skips quietly and counts
(`warm_available`); if nxnative aborts, this filter is mandatory, not
optional.

## Piece 5 — Community coverage: pairs file + manifest overlay (NOT a profile copy)

Two failed builds taught the exact shape. The overlay must NOT be a profile
copy with edited validated tables: `patch_nso.py` loads `patches.py` from
every profile (repo + `DM_GAMES_DIR`) and aborts on a duplicate build ID, and
the split step aborts on any drift in `shader_map/facts/pairs` vs freshly-
split tables. So the final delivery splits in two, each through a sanctioned
hook:

1. `swx_pairs.tsv` (profile `pso_pairs.tsv` rows + 336 new community pairs)
   in `<I>/shaders/`, consumed via `DM_PSO_PAIRS` (prebuild concatenates it:
   `nvn_mtl.m`). No profile table touched.
2. Derived overlay = full profile copy MINUS `patches.py`, with ONLY
   `pso_manifest.tsv` extended (converted community R rows; the manifest is
   not split-validated). Consumed via `DM_GAMES_DIR` + `DM_GAMES_FIRST`.

Provenance (`SWX_DERIVED.txt`) sits next to each artifact. S-row additions
were nil (already covered) — recorded, not shipped.

## Piece 6 — Build hookup (both overlays, no ordering problem)

V1 needs no extracted files (pairs + R rows join against the static profile
map), so there is no ordering problem: before invoking nxnative, the wrapper
writes `<I>/shaders/swx_pairs.tsv` and the derived overlay
`<I>/game/derived/<id>/`, then passes `DM_PSO_PAIRS` (prebuild + first start)
and `DM_GAMES_DIR=<I>/game/derived` + `DM_GAMES_FIRST=1` (warm-up manifest).
`env.txt` carries `DM_PSO_PAIRS` for runs; the manifest overlay is picked up
through the profile. Implemented in `run_nxnative` via
`derive_community_profile` (missing list = stock + warning).

## Piece 7 — The every-launch loop

No new mechanism needed: `pso_user.tsv` (already redirected to `<I>/shaders`)
accumulates each session's needs, the derived manifest is the base, and
`DM_PSO_MANIFEST` points at the union. Reopening warms base plus local
learnings. The only periodic addition: re-fetch the community list so other
players' new rows flow in.

## V1 scope (implemented: pairs via `wrapper/shader_derive.py`, exact R rows via `wrapper/shader_convert.py`)

- Pair extraction from all R/B/T rows: 95.4% both-shaders-known; genuinely
  new pairs vs the profile's tables were only 336. Delivered via
  `swx_pairs.tsv` + `DM_PSO_PAIRS` (no profile-table edits, so the split
  check passes): first-seen draws of those pairs get hybrid stand-ins while
  the exact pipeline builds once in the background.
- S rows for matched shaders: all 16 were already in the profile manifest
  (0 added).
- Exact R rows: CONVERTED (16,858 rows, 0 parse failures, 7,667 duplicates
  collapsed, 1,179 shader-not-in-map, 222 specialized-a2c skipped). Semantic
  validation: stage bytes match the profile map's own stage column on all
  25,815 rows (0 mismatches); all outputs are 1024 hex chars with sane
  names/stages/samples.

## Empirical gate: PASSED (live gameplay, keep-alive build)

Warm-up: 18,172 render pipelines, 0 stale keys, 0 pipelines without a base,
zero `[RED]`; prebuild union 16,103 pairs including `swx_pairs.tsv`. Session:
84 longframes, of which only 9 involve new pipelines (40–126 ms,
hybrid-covered); the rest are texture first-uploads on area entry and
GPU-bound steady frames. Cross-check: all 633 runtime-built R rows reference
known shaders but none matches a converted row (genuinely new states, not
conversion misses), and 633/633 of their pairs were prebuild-covered — zero
full stalls by design, with learnings accumulating into `pso_user.tsv`.

## Residual backlog (explicitly deferred)

Validated in production logs (22,947-line warm-up, 0 stale keys, 0 new
pipelines on the common paths). Left open, in priority order:

1. **Bound_control re-derivation (slice 3).** ~1k MNG-referenced shaders are
   absent from the profile map. Most are other-version shaders (correctly
   skipped); the bound_control subset (same bytes, different key) would need
   local RomFS parsing to confirm. Payoff is bounded: those shaders already
   resolve at runtime through nxnative's own keying (hybrid-covered first
   build, then `pso_user`), so this only converts some on-demand builds into
   prewarmed ones. Do it only with local splits as ground truth, and only if
   `pso_user` growth stalls while hitches persist in new areas.
2. **B-row border variants / A-row bind states.** Both resolve at runtime
   through nxnative's own mechanisms (border path, per-shader libraries +
   on-demand specialization). No action unless evidence of border/variant
   stalls appears in logs.
3. **Community list refresh.** Re-running install picks up a newer `refs/`
   snapshot and re-derives. Automate only when the list moves fast enough to
   matter (check list version/date before deriving).
4. **Title gating.** nxnative warms everything before the first frame
   (5.5 s measured). A title/rest split only pays off if prepare time grows
   painful.

## Open verification points (resolved during v1)

1. nxnative `varN` assignment: positional per container (`#BNSH@base#var{v}`,
   `#BAHS#prog{p}#var{seen}` in `shader_split.py`) — never bind-state
   derived. Shader-level (control+code) join is the right granularity.
2. Manifest rows with no shader in the map: dropped quietly (`warm_stale`,
   `nvn_mtl.m`), counted in the prewarm-done line. The version filter is
   recommended, not mandatory.
3. Pulling-base semantics: R rows group by pair (`vkey|fkey`); "the pipelines
   of a pair share their bases, so one worker builds them one after another"
   (`nvn_mtl.m`). Covering the pair is what eliminates submit-thread stalls
   (hybrid draws use the pair's pulling pipeline meanwhile).
