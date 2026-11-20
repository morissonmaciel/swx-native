# SWX_FXCAS — FidelityFX CAS sharpen-only on BOTW's 3D scene (experimental)

Opt-in, off by default. Add to the player's `env.txt`:

```ini
SWX_FXCAS=1
```

Absent or `0` disables every CAS pass (verified gate: zero passes with
`SWX_FXCAS=0`).

## What it does

Right before the draw that composites the 3D scene onto the canvas, the
scene runs through AMD CAS sharpen-only (fixed intensity 0.25, MIT,
`dm/gfx/msl/fxcas.metal`) into a private scratch texture of the same format
and size, and only that draw's scene binding is swapped to the sharpened
copy (one-shot in `encode_args_`, cache invalidated on entry and exit).
UVs, sampler, other readers of the scene and any FXAA of the draw itself
are preserved. A reused scratch serves while the configuration holds;
residency tracked, retired on change. Any incompatible condition (another
format, DRS region, HUD layouts) or a preparation failure leaves the draw
on the original texture.

`DM_PRESENT_UPSCALE=metalfx` keeps operating afterwards, on the composed
present of the frame.

## Anchor (PASSDUMP-confirmed)

Exactly one draw per gameplay frame with fs `bd7ffdd435c6c3bc` +
vs `5e9f57168d89a543`: fullscreen triangle (prim 4, 3 verts, indexed) into
a single RGBA16Float 2D canvas (2560×1440 ms1), sampling a same-size float
2D scene texture, no blending, full viewport; the scene was written by the
immediately preceding pass. With the inventory open there is no such draw
(canvas cleared + UI only), so menus never trigger. HUD draws follow on the
same canvas and read it unchanged.

The quick-menu blur quad (`HoldShortCutBg`, fs `d4fa32dabc35066c`) uses a
different fragment shader and cannot match the anchor; it stays on the
smoke gate (spyglass, quick menu, movie: no flicker or black screen).

## Gates

Installation (stack from master, idempotent reapply, host compiles/links;
zero passes at `SWX_FXCAS=0`), image/order (3 frames, same save/framing at
1440p: exactly one CAS pass on the confirmed scene before the HUD; 3D
sharper, HUD glyphs identical), cost/compat (A/B same binary, 1440p
fullscreen + MetalFX + DRS, no PASSDUMP during measurement; target ≤ +0.5 ms
GPU p95 at 60 fps, no new hitches or `[RED]`; short smoke 1080p/2160p + recorder).
