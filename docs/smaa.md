# SWX_AA=smaa — SMAA 1x on BOTW's fullscreen copy/resolve (experimental)

Opt-in, off by default, harness-only. Never pin it in the player's
`env.txt`: the installer strips `SWX_AA` on reinstall (keep-out list in
`wrapper/main.py`) and the harness sets it per run:

```sh
SWX_AA=smaa
```

Absent or `0` keeps the original draws. Any other value keeps the original
draws with a single warning (`[RED] SWX_AA=<v> unknown (want smaa or 0)`);
the variable is read once at launch.

## What it does

The anchored fullscreen copy/resolve draw keeps running: the hook filters
its source texture (fragment stage texture slot 0,
`bound_tex(1,0)`/`tex_by_id`; never a snapshot of the destination) through
three fullscreen passes — edge detection into `RG8Unorm`, blend-weight
solve into `RGBA8Unorm`, neighbourhood blend — into a temporary compatible
with the source (same format, dimensions and colour interpretation), then
registers it as this draw's binding only (`g_fb_src`/`g_fb_use`, the
one-draw substitution `fb_prepare` uses; `encode_args` honours it on the
direct path and on the recorder's replay). The sampler, coordinates,
format and the game's RGB-only channel mask stay the draw's own. The hook
sits right after `fb_prepare` in the per-draw path. Any failure (library,
LUTs, pipeline, no room) leaves the original draw with the original
source.

Kernels live in `dm/gfx/msl/smaa.metal`, built with the host's shaders
(`host_msl.py` wildcard) and loaded by name; until the anchor is set the
hook keeps the original draws and the kernels never encode.

Ordering goes through the core Metal 4 primitives (`enc_r_begin`/`end`
barriers) and is recorder-safe (`enc_r_*` + `rec_table`, as
`copy_t2t_draw`). Intermediates are reused only on confirmed GPU
completion (slot ring vs `g_done`).

## Anchor (Etapa 0 done 2026-11-20)

`fs=bd7ffdd435c6c3bc` + `vs=5e9f57168d89a543` — the fullscreen 2560x1440
composite (scene → canvas, `blend=0`, `wm=0x7` RGB-only, integral viewport),
1×/frame with a unique VS pairing (PASSDUMP F3115–3117,
`SWX_NATIVE_RES=1440p`). Keys match the first 16 chars (`strncmp`, as
FXCAS does — runtime keys carry a `_b`-style suffix). The other
fullscreen no-blend draws in the frame are depth-only (`wm=0xffffff0f` on
a depth target, `wm=0` depth write) and are excluded. Source is the 2D
scene (FXCAS-sharpened when `SWX_FXCAS=1`), never a snapshot of the
destination — so SMAA runs after CAS on the same draw (sharpen, then AA
last).

## LUT provenance

`AreaTex` `RG8Unorm` 160x560 + `SearchTex` `R8Unorm` 64x16, bytes copied
verbatim from `refs/mac-nativegames/games/totk/third_party/smaa/AreaTex.h`
and `SearchTex.h` at `71c806a838bdd7d` (MIT License, preserved in
`dm/gfx/msl/smaa.metal`). Uploaded once into immutable Shared textures,
reused across frames and scales.

## Cost

Unknown — no measurement yet. The filter adds three fullscreen passes at
source resolution plus two same-size scratch targets (edge `RG8`, weights
`RGBA8`); the cost gate below defines the evidence required before any
default-on discussion.

## Known limitation / follow-up

The anchor is filled (Etapa 0): the hook filters the composite draw above.
Before changing the hook's match policy, cite
`refs/mac-nativegames/games/totk/third_party/d2r_gfx/gfx/nvn/botw_finish.h:70-100`,
which validates a full draw (2D source, source != destination, no blend,
RGB-only mask, integral viewport/scissor) and composes texel-for-texel or
bilinear RGB-only. FXAA replacement before the HUD stays independent work.

## Gates

- Installation/rewrite + host build: PASS (script) — stack 1..29 applies
  cleanly from the series (`patch_nxnative.sh` exit 0, marker written after
  its own `stack_verified`), all six `# verify:` sentinels resolve,
  `smaa.metal` + anchor keys in tree.
- Harness smokes: PASS (structural) — `SWX_AA=smaa` + `SWX_FXCAS=0`:
  `filtered 2560x1440 (format 90) for one draw`, anchor 1×/frame
  (F1674–1676), zero `[RED]`; `=smaa` + `SWX_FXCAS=1`: CAS sharpens
  (`ser=57398 sharpened`) then SMAA filters the same composite draw
  (F2065–2067, 1×/frame each), zero `[RED]`. Unset/`=0`/unknown paths
  unchanged from the inert-anchor runs (original draws kept).
- Visual/order: PASS per player report 2026-11-20 — no HUD/text blur, no
  flicker, no glitches; slight AA improvement on edges (SMAA 1x = single
  full filter per anchored draw per frame, as designed); with `SWX_FXCAS=1`
  the combo holds (CAS sharpness + slight pleasant color pop, no
  over-sharpen/ringing reported). Formal p95 A/B + 2160p pressure smoke
  still open (waived like FXCAS unless you want them).
- Cost (OPEN): formal A/B (GPU p95 + recorder) at the anchored
  resolution(s) before any wider rollout.
