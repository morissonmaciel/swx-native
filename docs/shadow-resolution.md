# SWX_SHADOW_RESOLUTION — BOTW sun shadow cascades at 2048 (experimental)

Opt-in, off by default. Add to the player's `env.txt`, or pick in the
launcher (Graphics → Shadow resolution, macOS only, shown when the build
declares `SWX_BOTW_SHADOW_RES`):

```ini
SWX_SHADOW_RESOLUTION=2048
```

`896` (Console) uses the game's own cascade table (docked: 896×896 per
cascade). `2048` (High) writes 2048 in all four table entries, so the
three sun cascades render at 2048×2048 with the half-size copy at
1024×1024. The three table moves after the entry branch are replaced with
NOPs (never executed; text writes there are refused by the cheat
engine). Absent, `0`, `896`, or anything invalid falls back to Console;
an explicit invalid value is normalized to `896` by the launcher and
warned about once by the host. A carried `896` on a build without the
stub stays silent (only `2048`/garbage warn there). High is not gated on
docked mode.

## Cost

About +24 MiB of render-target heap (map 4.59 → 24 MiB, copy 1.31 →
6 MiB). The heap headroom and the game's filter are unchanged in this
round.

## Known limitation

The deferred sun shadow passes offset their taps by a texel-size pair
(`1/896` for the console map). Left as is, the taps stay a console texel
apart on the bigger map and the penumbra can break into steps. If a
capture shows the PCF still at `1/896` with stepped penumbra, record it
as the visual-gate failure; the follow-up targets the deferred pass's
confirmed bindings (Savage Wind's texel-size correction is the
reference).

## Validation results

- Installation and rewrite: stack applies idempotently, no `.rej`,
  capability in `main.fixups.tsv`, both stub branches + return to
  `0x124ef08` in disassembly, host compiles/links.
- Launcher and fallback: PASS per player report 2026-11-20 — High persists
  across close/reopen; with High saved, explicit `896` in `env.txt` locks
  the row on Console; saves open both directions (High save on Console
  and vice-versa, no issue). Row present with Console/High options
  (macOS, capable build); iOS = Console only (code-covered, not run).
- Maps and quality: PASS (structural) — same save/camera/light docked, sun + foliage; three
  `Depth16Unorm` layers 896×896 → 2048×2048 and `R16Unorm` copy
  448×448 → 1024×1024. Console: depth `ser=37623 fmt=250 896x896 array=3`,
  copy `ser=37625 fmt=20 448x448 array=3` (F1713–1715). High: depth
  `ser=39261 fmt=250 2048x2048` (clear/load writes, `array=3` sampling),
  copy `ser=39263 fmt=20 1024x1024` (sl0/sl2 writes) (F1572–1574).
  Zero cross-sizes in either dump, zero `[RED]`, exit 0. Visual: PASS per
  player report 2026-11-20 — non-nearby shadows visibly sharper on High;
  Ultra (Savage Wind levels) noted as future work, out of scope here.
- Filter and budget: PASS (gameplay, no formal A/B) — High session ~75 s
  (1440p fullscreen, MetalFX, DRS): average present 16.67 ms, 0 dropped in
  last 300 throughout, frame p50 16.67 / p95 16.75, GPU p50 ~9 / p95 ~10.1;
  13 longframes = 1 shader-library compile + VFR pacing steps (submit
  ~6 ms, GPU ~9), no GPU-driven slowdown. Formal p95 A/B vs Console +
  2160p memory smoke not run (player waives: felt performance holds). PCF
  pair still unverified in-capture (PASSDUMP lacks uniform values) — no
  stepped penumbra reported visually.
