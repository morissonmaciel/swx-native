# Reading the shared shader list (player as reader, no upload)

How our wrapper consumes mac-nativegames' community shader list to prebuild
and warm pipelines, without running any contribution/upload path.

## Key compatibility

Both sides key a shader by `SHA-256(control section + code area)`:

- mac-nativegames (`prepare_shader_seed.py:476-516`): full 64-hex digest.
  Control cut at the length the control word at `+0x20` declares (`0x880` /
  `0x88c` TOTK, `0x7d1` / `0x848` BOTW). For BOTW, embedded agl `0x848`
  sections go through `bound_control` — the bytes as the game hands them to
  NVN (`:375-381`) — before hashing.
- nxnative (`shader_split.py:55`, `shader_map.tsv` column `dm_ctl_sha16`):
  the same digest **truncated to 16 hex chars**, with the same
  shorter-when-declared control cut.

So an MNG `V`-row key's first 16 chars join directly against nxnative's
`dm_ctl_sha16` wherever the hashed bytes are identical. The one systematic
mismatch is the BOTW `bound_control` subset (agl-embedded shaders), where the
preimages — hence the keys — differ by construction.

## Reader path

1. **Parse** `games/botw/assets/shader_list.tsv` (identifiers and pipeline
   descriptors only, no game data — same legal property as nxnative's own
   tables, MIT-licensed source).
2. **Join** `V/A/L/S/C` row keys (`[:16]`) against the profile's
   `shader_map.tsv`. Matches are forced into the prebuild seed.
3. **Resolve the rest locally.** We hold the player's own game files, so the
   `bound_control` subset and any genuinely new rows are re-derived from the
   local RomFS in both key styles and joined the same way. Whatever still
   misses is left to on-demand build plus the per-profile user list, exactly
   as a first-seen shader today.
4. **Pipeline rows** (`R/B/T`, with bind state and tile-read info) map onto
   the profile's `pso_pairs.tsv` / `pso_manifest.tsv` equivalents; rows whose
   state nxnative normalizes away (e.g. unused colour state) collapse onto the
   same pair.
5. **Feed the build without touching the core.** The augmented tables ship as
   a *derived profile* (`DM_GAMES_DIR`, the mechanism nxnative already uses
   for per-version tables), so `core/nxnative` stays pristine and our wrapper
   owns the overlay.

## When the read happens

Not "at game load" as a single moment, but at the same two moments as
mac-nativegames itself (see `shader_prewarm.md`):

- **Packaging**: the ingested list extends what gets translated and compiled
  into `.metallib`s — this is where the compile cost is paid, once.
- **Boot**: the extended warm lists (`pso_manifest.tsv` equivalent) are
  warmed title-gated plus background — this is the 10–20 s the player sees.

A pure reader needs no inbox, no upload switch, and no network: ship the
list, compile at packaging, warm at boot. Contribution stays a later,
independent opt-in.
