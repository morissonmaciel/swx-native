# SWX_AA — game AA control for BOTW (experimental, harness-only)

Never pin it in the player's `env.txt`: the installer strips `SWX_AA` on
reinstall (keep-out list in `wrapper/main.py`) and the harness sets it per
run. Launcher Graphics → Anti-aliasing (macOS): Off / FXAA / SMAA.

| Value | Behavior |
|---|---|
| absent or `fxaa` | The game's FXAA draws (default). Invalid values fall back here with one `[RED]` warning. |
| `0` | AA off: the anchored FXAA draw is not encoded; a copy pass draws the scene texel-for-texel (bilinear when sizes differ), RGB-only, honoring the canvas's pending clear. |
| `smaa` | SMAA only: the 3-pass filter runs on the scene, the copy pass draws the filtered result into the canvas, and the game draw is skipped. |

Read once at launch. `fxaa` is the public name (the launcher, the parser
and `settings.tsv` agree on `0|fxaa|smaa`); `txaa` was never a name and is
rejected into the fxaa fallback with the one `[RED]` warning. `ab_test.py`
has no `SWX_AA` knob, so SMAA runs go through the harness env directly
(`SWX_AA=smaa` per run), never through the installed `env.txt`. Order with `SWX_FXCAS`: CAS sharpens first; SMAA/copy consume its output (the skipped draw's one-shot swap is disarmed in the hook).

## What it does

At the anchored draw, the hook validates MNG-style (2D source,
source != canvas, no blend, RGB-only mask 0x7, full viewport/scissor) and,
unless the mode is `fxaa`, runs the copy pass instead of encoding the game
draw. In `smaa` mode the 3-pass filter (edge detection into `RG8Unorm`,
blend-weight solve into `RGBA8Unorm`, neighbourhood blend) runs first into
a temporary compatible with the source (same format, dimensions and colour
interpretation); the copy pass draws that temporary. The weights pass is a
port of the reference H/V path (line search with SearchTex refinement,
AreaTex lookup by distance and crossing edges, corner rounding, 16 steps,
1x; no dedicated diagonal search — stair-step pixels on diagonals still go
through the H/V path, and where no weight is found the blend returns the
scene colour, so residual shimmer can remain on diagonal edges). The blend pass
gathers the four neighbours' weights, picks the dominant axis and mixes the
two taps on it, as the reference does. The sampler,
coordinates and format stay the draw's own. Any failure (library, LUTs,
pipeline, no room, validation) leaves the original draw with the original
source (sharpened when FXCAS is armed).

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
  its own `stack_verified`), all thirteen `# verify:` sentinels resolve,
  `smaa.metal` + anchor keys + copy pass in tree.
- Harness smokes: PASS — `=smaa` log-confirmed at 1440p + shadow High
  (`filtered 2560x1440 (format 90)`, `filtered scene ... drawn into the
  canvas ... texel for texel; the game draw skipped`, zero `[RED]`,
  exit 0) and at 2160p + shadow High (`filtered 3840x2160 (format 90)`,
  texel-for-texel into the canvas, draw skipped, zero `[RED]`, exit 0;
  the 4K anchor holds with the same fs key). The 1440p retained run had
  `SWX_FXCAS=0`; the 2160p anchor runs cover both orders — CAS off and
  the combo `SWX_FXCAS=1` (`BOTW scene 3840x2160 sharpened before its
  composite draw`, then filtered + copied + skipped, zero `[RED]`,
  exit 0). The `=0`/`fxaa` permutations ran per player report
  (screenshots taken); their structural lines are not in the retained
  logs. Unknown-value warning path unchanged (warns once, keeps the
  game draw).
- Visual/order: PASS per player report 2026-11-20 (new binary, skip-draw
  path) — screenshots compared with zoom across `fxaa`/`=0`/`=smaa` ×
  `SWX_FXCAS=0/1`: AA differences perceptible under zoom, CAS sharpening
  differences perceptible with `=1`; no blur/flicker/glitch reported.
  Prior binding-swap runs (F1674-76 solo, F2065-67 combo) validated the
  anchor only, not this skip-draw path.
- Cost (pressure PASS 2026-11-20, formal A/B open): `=smaa` + 2160p +
  shadow High + CAS off, 2100 frames: GPU p50 17.11 / p95 20.03 /
  max 21.88 ms; frame p50 19.80 / p95 34.06 ms (VFR 60 Hz lock, exit 0,
  zero `[RED]`). Alternating A/B vs `=0` not run.
