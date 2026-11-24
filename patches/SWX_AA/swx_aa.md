# SWX_AA — game AA control for BOTW (off / FXAA / SMAA 1x)

`SWX_AA` is a BOTW-only, harness-selected mode. The install leaves it absent.

## Toggle behavior

- Absent or `fxaa`: keep the game's native FXAA composite unchanged.
- `off`: keep the game's original composite draw, including its upscale, mask and clear behavior. For four verified fragment shader variants, copy the effective fragment NVN slot 0 CBUF into the frame submission arena and set only `c[3].x/y` at bytes `0x30/0x34` to `1e30`. This neutralizes the native FXAA thresholds without replacing or skipping the game's draw.
- `smaa`: filter the scene before HUD, copy the filtered result into the canvas, and skip the verified game composite. If the hook cannot safely replace the draw, it keeps the game's draw and attempts the same threshold neutralization for the verified variants.

Unknown values fall back to `fxaa` with a warning. A previously saved `"0"` is migrated to `"off"` by the launchers.

## Effective uniform handling

The override is restricted to the gameplay composite fragment key `bd7ffdd435c6c3bc` and pause Quality variants `a2406ffb6621954d`, `754271a4c8677a08`, and `fcc8d0ea42d4d941`, all at fragment stage, NVN slot 0. Other shaders and bindings remain untouched.

The code preserves the CBUF's original read extent: it copies the mapped binding and rounded readable tail, overlays current inline updates, then zeros only bytes beyond the readable extent. If current deferred updates cover every readable byte, that complete overlay is sufficient. If a pending GPU writer or partial update makes the effective bytes uncertain, the encode thread does not wait for the upload queue; it keeps the original mapped binding and reports `AA F ... fallback=stock-binding`. Unmapped or unbound slots retain the stock zero-bank/error behavior and report `fallback=stock-slot`. A game-bound empty slot is handled separately because the core already defines its reads as zero; it may use the neutral CBUF only when the fixed read extent fits the bounded arena allocation.

Successful neutralization reports `AA N ... source=effective` or, only for a confirmed game-bound empty slot, `source=empty`. The arena owns the copied bytes through submission commit and the GPU completion fence; the override is never cached, so later effective-buffer generations are re-evaluated. Any applicable `AA F` means that draw kept native threshold behavior and the `off` result was not established for that draw.

## Anchor and order

The FXAA/upscale composite draw in BOTW 1.9.0 is fragment key `bd7ffdd435c6c3bc` plus vertex key `5e9f57168d89a543`: a fullscreen triangle into the RGB10A2Unorm canvas, sampling the scene texture, no blending and full viewport. The SMAA hook validates the source and destination before replacement; other draws are not modified.

With `SWX_FIDELITYFX_CAS`, CAS sharpens first at draw entry. SMAA and its copy consume that output. `fxaa` leaves the original composite path unchanged. There is no MetalFX replacement path for this draw.

## Gate status

The earlier visual A/B for SMAA passed on its then-current implementation. This rebased renderer patch changes `off` semantics and CBUF fallback safety, so installation/build and the focused gameplay gate must be repeated. For `off`, require the mode marker and `AA N source=effective` for the gameplay draw, with no `AA F` for that draw; for `smaa`, require the matching `AA Z` decision and same-frame capture evidence. An `AA F` is a safe fallback, not a passing result for neutralization.

## Patch files

- `nvn_mtl.m.patch`: CBUF handling, the anchored hook, SMAA passes and capture markers.
- `smaa.metal.patch`: SMAA edge, weights, blend and copy shaders.
- `settings.tsv.patch` / `settings.py.patch`: setting catalog and Graphics order.
- `launcher.schema.json.patch`: accepts the `SWX_` namespace.
- `launcher.c.patch` / `LauncherSettings.swift.patch`: saved-choice migration.
- `settings.md.patch`: launcher setting description.
