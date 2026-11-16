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

## Legal

Use only your own legally obtained copy of the game, dumped from your own
Nintendo Switch. This repository holds no game files, keys, firmware, or
extracted content. Builds made from your files are for your own use only.
