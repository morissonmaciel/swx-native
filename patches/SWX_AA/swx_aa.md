# SWX_AA — game AA control for BOTW (off / FXAA / SMAA 1x)

Toggle `SWX_AA` (BOTW only, harness-only, never pinned): the anchored
fullscreen composite draw (scene → canvas) is either left to the game
(`fxaa`, the default) or replaced by an inline copy pass — plain scene
(`off`) or SMAA-filtered scene (`smaa`) — while the game draw is skipped.

## Toggle

- absent or `fxaa` = the game's FXAA draws (default, no-op). Invalid values
  fall back here with one `[RED]` warning.
- `off` = AA off: the scene is copied into the canvas texel-for-texel
  (bilinear when sizes differ), RGB-only, honouring the canvas's pending
  clear. The game draw is skipped.
- `smaa` = SMAA only: the 3-pass filter runs on the scene, the copy pass
  draws the filtered result into the canvas, and the game draw is skipped.

Read once at launch. Harness-only: the installer leaves it absent and the
harness sets it per run. A saved `"0"` (the value `off` once had) reads as
`"off"` on both launchers.

## Anchor (PASSDUMP-confirmed 2026-11-20, F3115-3117)

The FXAA/upscale composite draw of BOTW 1.9.0: fs `bd7ffdd435c6c3bc` +
vs `5e9f57168d89a543` — a fullscreen triangle (prim 4, 3 verts, indexed)
into a single RGB10A2Unorm 2D canvas, sampling a same-format 2D scene
texture, no blending, full viewport. Exactly one such draw per gameplay
frame; none with the inventory open, so menus never trigger.

## Behavior

- Validation mirrors the game's own finish path (2D source, source serial
  != canvas serial, no blend, RGB-only mask `0x7`, full viewport/scissor;
  anything else keeps the game draw, logged once per cause).
- Order with `SWX_FIDELITYFX_CAS`: CAS sharpens first (`fxcas_try` at draw
  entry); SMAA/copy consume its output (`fxcas_tex`) and disarm the
  one-shot swap the skipped draw would have taken.
- Reference LUTs (AreaTex RG8Unorm 160x560 + SearchTex R8Unorm 64x16,
  original Smaa bytes, MIT) uploaded once into immutable textures, reused
  across frames and scales. Copy shaders (`swxaa_vs/copy/scale`) live in
  `smaa.metal`, loaded by name. No MetalFX replacement path for this draw.

## Gate history

- 2026-11-20 (then-current core): install + boot smoke `SWX_AA=smaa`
  (log `filtered … into the canvas`, zero `[RED]`, exit 0) and combo
  `SWX_AA=smaa` × `SWX_FIDELITYFX_CAS=1`; see `patches/PATCHES.md` #29 and
  `docs/smaa.md`. A/B formal and cost p50/p95 SKIPPED per player order.
- 2026-11-23 (current core): re-split rebased as `SWX_AA/*` (9 patches, 1
  per file) + structural review (APROVADO COM AJUSTES; `META needs`
  widening carried, duplicate verify removed). Re-gate (G0/G1/G2) pending.

## Files

- `nvn_mtl.m.patch` — hook `dm/gfx/nvn/nvn_mtl.m`: `swx_aa_mode()`,
  anchor match, `swx_smaa_filter()` 3-pass + `swx_aa_copy()`, `swx_aa_hook()`
  call after `fb_prepare`, LUT tables.
- `smaa.metal.patch` — new `dm/gfx/msl/smaa.metal`: edge/weights/blend +
  copy/scale shaders (colhido pelo wildcard `../gfx/msl/*.metal`).
- `settings.tsv.patch` — `SWX_AA` choice row (`off/fxaa/smaa`, `fxaa`).
- `settings.py.patch` — Graphics order + `META needs` widening.
- `launcher.schema.json.patch` — `env`/`needs` widened to `(DM|SWX)_`.
- `test_launcher_settings.py.patch` — catalog asserts.
- `launcher.c.patch` / `LauncherSettings.swift.patch` — `"0"`→`"off"`.
- `settings.md.patch` — `### Anti-aliasing` doc row.
