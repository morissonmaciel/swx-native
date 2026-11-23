# FidelityFX CAS — sharpen-only on BOTW's 3D scene

Toggle `SWX_FIDELITYFX_CAS` (BOTW only, macOS default on): right before the
draw that composites the 3D scene onto the canvas, the scene runs through AMD
CAS sharpen-only (strength 0.25 by default, overridable via `SWX_FIDELITYFX_CAS_SHARPNESS`, MIT, `dm/gfx/msl/fxcas.metal`) into a
private same-format scratch texture, and only that draw's scene binding is
swapped to the sharpened copy (one-shot in `encode_args_`, cache invalidated
on entry and exit).

## Toggle

- `SWX_FIDELITYFX_CAS=1` (the macOS install default; BOTW only): sharpen the
  3D scene before its composite draw.
- `SWX_FIDELITYFX_CAS=0` or absent: zero CAS passes by construction (the gate
  is opt-out — the promotion pins it on, the gate itself reads absent/0 as
  off).
- Nothing else changes: UVs, sampler and every other reader of the scene
  keep the original texture.

## Sharpness

- `SWX_FIDELITYFX_CAS_SHARPNESS` 0..1, default 0.25 when omitted or
  invalid (invalid logs `[RED]`); only relevant with the toggle on.
- Gate limited validation: omitted+on = 0.25, 0.15, 0.75.

## Anchor (PASSDUMP-confirmed)

The FXAA/upscale composite draw of BOTW 1.9.0: fs `bd7ffdd435c6c3bc` +
vs `5e9f57168d89a543` — a fullscreen triangle (prim 4, 3 verts, indexed) into
a single RGB10A2Unorm 2D canvas, sampling a same-format 2D scene texture at
or under canvas size, no blending, full viewport. Exactly one such draw per
gameplay frame; none with the inventory open, so menus never trigger. HUD
draws follow on the same canvas and read it unchanged.

## Behavior

- Sharpen-only, default 0.25 (AMD FidelityFX CAS, MIT): 3x3 taps clamped to the
  texture (valid under DRS), alpha passes through. Under DRS the scene may be
  smaller than the canvas — CAS runs at scene resolution into a same-size
  scratch, so sharpening happens before the composite upscale.
- Private scratch: the sharpened copy lands in an `MTLStorageModePrivate`
  same-format, same-size texture, reused while the configuration holds
  (residency tracked, retired on change).
- One-shot binding swap: the arm is scoped to the anchored draw and consumed
  by its `encode_args_` scene binding; a draw that never encodes (discard,
  cull, empty scissor) discards the arm. Order with `SWX_AA=smaa`: CAS
  sharpens first, SMAA/copy consume its output.
- Any incompatible condition (format mismatch, a scene larger than the canvas
  in either dimension, HUD layouts) or preparation failure leaves the draw on
  the original texture.

## Gate history

The historical gate row (#27: install + image/order PASS — 3/3 PASSDUMP
frames one CAS pass on the confirmed anchor, HUD unchanged, 25k-frame run
zero `[RED]`, `=0` off-smoke zero passes; formal cost A/B + recorder SKIPPED
per player order 2026-11-20) is preserved verbatim in `patches/PATCHES.md`;
its evidence lives in `instrumentation/ab_results.md`.

## Files

- `nvn_mtl.m.patch` — hook `dm/gfx/nvn/nvn_mtl.m`: `fxcas_on()` toggle,
  `fxcas_run()` sharpen pass into the scratch, `fxcas_try()` anchor match +
  arm, one-shot binding swap in `encode_args_`, call at `draw_` entry.
- `fxcas.metal.patch` — new `dm/gfx/msl/fxcas.metal`: `vs_cas` fullscreen
  triangle + `fs_cas` (AMD CAS sharpen-only, default 0.25, overridable via `SWX_FIDELITYFX_CAS_SHARPNESS` 0..1, MIT).
