# SWX_DRAW_DISTANCE — draw-distance presets for BOTW (experimental, harness-only)

Never pin it in the player's `env.txt`: the installer leaves `SWX_DRAW_DISTANCE`
absent on fresh installs (a saved choice is preserved across reinstalls) and the
harness sets it per run. Launcher Graphics → Draw distance (macOS, when the
build's fixups declare `SWX_BOTW_DRAW_DISTANCE`): Normal / Medium / High.

| Value | Preset | Nominal factor | Additional texture bias |
|---|---|---:|---:|
| absent or `1.0` | Normal | 1.00× | `0` |
| `1.15` | Medium | 1.15× | `−0.202` |
| `1.2` | High | 1.20× | `−0.263` |

Absent or invalid uses Normal (invalid warns once). The bias follows
`−log₂(factor)`, added to the game's bias on eligible materials. Reinstall
preserves an explicit saved choice; a saved `0` from no era migrates to
Normal. Grass and terrain LOD are out of scope; the camera far plane
(~25000 m) is untouched.

## What it does

Three effects, resolved once at boot by the loader (state `+0xBC` + a direct
renderer handoff, so the renderer never re-parses the env):

- **Placed actors.** The anchored traverse scale (`fmul s0,s0,s1` at
  `main+0x10a4688`, resume `+0x10a468c`) runs after the shared 100 m
  fallback. Under 2000 m of original distance: `max(original,
  min(original×factor, 1000 m))` (never shrinks a longer original). From
  2000 m: the preset factor (1.5× defensive cap). Normal reproduces the
  original multiply bit-for-bit.
- **Background trees.** Range/fade/detail fields scale at consumption (per-frame
  setup + update around `main+0x13c94ac` / `main+0x13c8898`, fields at
  `tree+0x14cb0`); the two input fields stay the game's, so no accumulation
  across frames.
- **Mipmap bias.** Fragment-stage SAMPT combined-handle sampler variants carry
  the additional bias on eligible opaque 2D linear materials (2+ usable mips,
  recorder-safe, cached by sampler ID + bias epoch). A blend-state change
  re-decodes the fragment argument block, so the bias is re-decided per encode.
  Separate SAMP slots keep the game's sampler (slot coincidence does not prove
  co-use). Normal creates no variants.

## Ceiling

Approved ceiling is **1.20×** (current Savage Wind High). The reference
records a bridge and an NPC near Rito Village disappearing at **1.30×**;
the Rito bridge crossing is the gameplay test route for that reason.

## Gates

- Installation/rewrite + host build: OPEN — stack 1..30 must apply cleanly
  (`patch_nxnative.sh` exit 0, marker written after its own
  `stack_verified`), all thirteen `# verify:` sentinels resolve, fixups
  capability present, disassembly confirms the `fmul` anchor and the tree
  consumers, host links.
- Launcher catalog: OPEN — macOS Graphics exposes Normal/Medium/High in
  order with Normal default; Medium and invalid-fallback verified by
  configuration (no gameplay run per value).
- Gameplay (2 runs): PASS per player runs 2026-11-22, Rito bridge route
  (crossing + camera sweep, no scope). **Normal via `env.txt`**
  (`factor 1.00`, stock path): exit 0, zero `[RED]`, shutdown summary
  `0 examined / 0 biased / 0 variants`, distances unchanged
  (`actor 56.0->56.0`, f2/f4 unchanged, 20.7M stub fires), cadence locked
  (frame p95 16.72 ms). **High via launcher** (`factor 1.20`,
  bias `-0.263`, no override): exit 0, zero `[RED]`, 18-min run, shutdown
  summary with original/effective pairs (`actor 2000.0->2400.0`,
  `f2 4000.0->4800.0`, later `105.0->126.0`, `f2 4954.9->5945.8`),
  cadence locked (p99 16.67 ms, GPU p95 ~9-10 ms). Config: 1440p, shadow
  Console (isolates the unrelated scope+shadow-High crash, separate front),
  FXCAS=1 + SMAA on. No streaming/allocation failure, no sustained cadence
  drop in either arm.
