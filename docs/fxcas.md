> **Superseded (2026-11-23):** this experimental opt-in (`SWX_FXCAS=1`) was promoted to the `SWX_FIDELITYFX_CAS` patch group (`patches/SWX_FIDELITYFX_CAS/`, enabled in `patches.toml`, macOS default on, BOTW only), with configurable sharpness via `SWX_FIDELITYFX_CAS_SHARPNESS`. Kept as design/provenance record for the 2026-11-20 gates; see `patches/SWX_FIDELITYFX_CAS/fidelityfx_cas.md` for the current doc.

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
(RGB10A2Unorm) and size, and only that draw's scene binding is swapped to the sharpened
copy (one-shot in `encode_args_`, cache invalidated on entry and exit).
UVs, sampler, other readers of the scene and any FXAA of the draw itself
are preserved. A reused scratch serves while the configuration holds;
residency tracked, retired on change. Under DRS the scene may be smaller
than the canvas: CAS still runs at scene resolution into a same-size scratch
(UVs preserved), so sharpening happens before the composite upscale. Any
incompatible condition (a format mismatch, a scene larger than the canvas in
either dimension, HUD layouts) or a preparation failure leaves the draw on
the original texture. Taps are clamped to the scene texture bounds; this
assumes DRS reallocates the scene (a smaller-viewport-into-the-same-allocation
case would smear one pixel of edge ringing — bounded, since the composite's
own filtering already crosses that boundary).

`DM_PRESENT_UPSCALE=metalfx` keeps operating afterwards, on the composed
present of the frame.

## Anchor (PASSDUMP-confirmed)

Exactly one draw per gameplay frame with fs `bd7ffdd435c6c3bc` +
vs `5e9f57168d89a543`: fullscreen triangle (prim 4, 3 verts, indexed) into
a single RGB10A2Unorm (10-bit) 2D canvas (2560×1440 ms1), sampling a same-size RGB10A2Unorm
2D scene texture, no blending, full viewport; the scene was written by the
immediately preceding pass. With the inventory open there is no such draw
(canvas cleared + UI only), so menus never trigger. HUD draws follow on the
same canvas and read it unchanged.

The quick-menu blur quad (`HoldShortCutBg`, fs `d4fa32dabc35066c`) uses a
different fragment shader and cannot match the anchor; it stays on the
smoke gate (spyglass, quick menu, movie: no flicker or black screen).

## Gates

- Installation: PASS — stack from master reapplies idempotently, host
  compiles/links, `SWX_FXCAS=0` yields zero passes (master `1c9461b`
  reinstall, `SWX_NATIVE_RES=1440p` windowed: zero `sharpened`, zero
  `[RED]`, exit 0).
- Image/order: PASS — 3/3 PASSDUMP frames (F1953–1955, 1440p,
  `SWX_FXCAS=1`): exactly one CAS pass per frame on the confirmed anchor
  (scene `ser=57277`, canvas `ser=2792`), HUD draws follow on the same
  canvas with unchanged shaders, zero `[RED]`; 25,200+ frame run exit 0,
  zero `[RED]`; player confirms 3D sharper, no hitch felt.
- Cost/compat: SKIPPED — formal A/B (GPU p95 ≤ +0.5 ms + recorder) waived
  per player order 2026-11-20; compat covered by the smokes below, cost by
  indirect evidence only (present-path GPU unchanged, hitch pattern
  unchanged). 1080p smoke on master `1c9461b`
  (`SWX_FXCAS=1`, `SWX_NATIVE_RES=1080p` windowed): 10,800 sharpened passes
  at 1920×1080 over a ~200 s gameplay session, zero `[RED]`, exit 0.
  `2160p` fullscreen smoke on master `1c9461b` (`SWX_FXCAS=1`,
  `SWX_NATIVE_RES=2160p`, `2160p bypass active: internal 3840x2160`,
  drawable 3840×2160): 3,600 sharpened passes at 3840×2160 over a ~98 s
  session, zero `[RED]`, exit 0. (An earlier windowed `2160p`-requested run
  fell back to the stock path and sharpened a DRS-driven 1600×900 scene —
  CAS fires there too, but that run did not exercise 4K.)
