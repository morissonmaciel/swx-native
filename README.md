# swx-native

Unpack The Legend of Zelda: Breath of the Wild and build it as a native macOS
app (Apple Silicon) via the nxnative core plus our own patches.

- Full name: The Legend of Zelda: Breath of the Wild
- Short name: Breath of the Wild
- Title ID: `01007EF00011E000` (target version: 1.9.0 / v1114112)

The game's own ARM64 code runs natively (rewritten where macOS requires it) on
a host that answers Horizon's services, with NVN graphics rebuilt on Metal.
Nothing from the game ships in this repository.

## Layout

| Path | What |
|---|---|
| `install.sh` | Terminal entry point: prepares `.venv` (uv + Python >= 3.14), checks nxnative deps, then calls `wrapper/main.py` |
| `wrapper/` | Python backend (reusable by a future graphical UI); `requirements.txt` is the tool set's pinned deps (at least nxnative's) |
| `patches/` | Our extra game patches (e.g. Atmosphère cheats applied on top of the core profile) |
| `core/nxnative` | Upstream core that does the heavy lifting (extract, rewrite, host, shaders, `mac-app` pack). Local checkout only, not versioned here |
| `refs/` | Local-only reference checkouts (savage-wind, mac-nativegames). Not versioned here |

## Requirements

- Apple Silicon Mac, macOS 26.6+
- Xcode 27.2 selected (`sudo xcode-select -s /Applications/Xcode.app`) + MetalToolchain (`xcodebuild -downloadComponent MetalToolchain`)
- Homebrew with `zstd` and `glslang`
- `uv` (installed by `install.sh` when missing)
- Your own game dump (NSP/XCI/NSZ/XCZ + update 1.9.0, optional DLC) and your own `prod.keys`

## Usage

```sh
./install.sh [--recreate] [--python 3.14] [-- <wrapper args>]
```

`install.sh` leaves a ready `.venv` and delegates to `wrapper/main.py`
(not implemented yet). Planned backend invocation:

```sh
./.venv/bin/python wrapper/main.py --game-dir <game folder> -k prod.keys --platform mac-app
```

Populate `core/nxnative` with the upstream checkout before building.

## Status

Working: full mac-app install through the wrapper (input validation, `<I>`
layout, nxnative extract/rewrite/shaders/host/pack, signed `.app` with Game
Mode on, `DM_SAVE`/`DM_PSO_USER`/`DM_PSO_ARCHIVE` redirected into `<I>`).

Needs testing: iCloud Drive saves (`--icloud-drive-save`).

## On Tests (experimental, installed by default)

- `DM_PRESENT_SNAP=4` + `DM_NVN_SYNC_LAG=1` (pinned by the installer;
  `=0` / `snap 2` restores stock for A/B): flat frametime in the heavy
  area-entry region, zero 30 fps holds over thousands of frames, zero
  repeated present blinks (ring 2 blinked under lag; ring 4 does not).
  See `docs/sync_lag_evaluation.md`, `instrumentation/ab_results.md`.
- To re-measure after the I/O work lands: same fixed route, `instrumentation/ab_test.py`
  (`collect`/`compare` labels). Expected to move: burst `submit+present`
  (still 30–55 ms stock — untouched by lag) once pooled staging / deferred
  writeback / async save land (`docs/io_paths_nxnative_vs_mng.md`).
  Upload split stays OFF by default (`SWX_UPLOAD_SPLIT=1` opts in; Gate 1
  measured +13 ms submit with it on).

## Known issues (measured — evidence in `instrumentation/ab_results.md`)

- Texture bursts still cost 30–55 ms submit (stock batching): splitting by
  count adds commit overhead without moving encode off the submit thread.
  Pools/overlap are future work, not this split.
- Frame-ahead + snapshot ring: `DM_NVN_SYNC_LAG=1` needs `DM_PRESENT_SNAP=4`
  (both pinned by the installer); ring 2 blinks under lag.
- Single white-frame display drops happen rarely in stock too; ring 4
  absorbs them.
- Autosave commits block the committing thread (whole-tree clone+swap,
  ~2–4 ms measured) — enough to tip an already-heavy frame. Async save is
  future work.
- First-seen pipeline builds can stall the submit thread once (rows
  self-heal into `pso_user.tsv`).

## Legal

Use only your own legally obtained copy of the game, dumped from your own
Nintendo Switch. This repository holds no game files, keys, firmware, or
extracted content. Builds made from your files are for your own use only.
