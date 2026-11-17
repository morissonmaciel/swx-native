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

- `SWX_VFR=relaxed` + `SWX_VFR_HZ=auto` (pinned by the installer;
  `=0` restores the legacy Switch-interval frame step): the game's clock
  follows the host's steady beat at the display rate (VFR ratio published
  per frame, 1/15 s step cap, bounded debt) instead of the 1/2/3-vsync
  intervals — movies and VFR overrides suspend it back to legacy. See
  `docs/vfr_framepace.md`, `instrumentation/ab_results.md`.
- `DM_PRESENT_SNAP=2` (stock snapshot ring; the installer leaves the
  compiled default unless set for A/B, `snap 4` is an optional smoothness
  experiment, not the default): the frame-ahead overlap (`DM_NVN_SYNC_LAG`)
  was removed (not approved as default — subjective call, Gates 3a/3b
  mixed); numbers preserved in `instrumentation/ab_results.md`.
- `DM_SAVE_ASYNC=1` (pinned by the installer; `=0` restores stock):
  swapped-out save versions delete on a background thread while
  clone+swap stay synchronous — commits measure sub-ms, no save-attributed
  longframe in the corpus. `DM_PSO_USER_ASYNC=1` (pinned; `=0` restores
  stock): `pso_user.tsv` rows batch in memory, one fflush per flush.
- To re-measure after the I/O work lands: same fixed route, `instrumentation/ab_test.py`
  (`collect`/`compare` labels). Expected to move: burst `submit+present`
  (still 30–55 ms stock — untouched by save-async and pso batch) once pooled staging / deferred
  writeback land (`docs/io_paths_nxnative_vs_mng.md`, `docs/async_writes_evaluation.md`).
  The texture-upload count split was removed after Gate 1 measured +13 ms
  submit with it on (`instrumentation/ab_results.md`).

## Known issues (measured — evidence in `instrumentation/ab_results.md`)

- Texture bursts still cost 30–55 ms submit (stock batching): splitting by
  count adds commit overhead without moving encode off the submit thread.
  Pools/overlap are future work, not this split.
- Single white-frame display drops happen rarely in stock too; ring 4
  absorbs them.
- Autosave commits are background-cleaned by default (`DM_SAVE_ASYNC=1`):
  clone+swap stay synchronous (~1 ms measured), the swapped-out tree is
  deleted off-thread. Forced writes (manual/config save) still show as
  sub-detector ripples in flat regions — the remaining floor is clone+swap
  + locks, which only a durability-changing redesign would remove.
- First-seen pipeline builds can stall the submit thread once (rows
  self-heal into `pso_user.tsv`).
- One-off ~77 ms freeze with no submit/GPU/upload/pipeline footprint and
  zero acquire timeouts (frame 14638 class) — outside NVN accounting
  (game-side, present path, or OS). To hunt it: a focused run with
  `DM_NVN_WAITS=1` (wait-ledger timings) plus per-thread CPU; no code
  change until attributed.

## Legal

Use only your own legally obtained copy of the game, dumped from your own
Nintendo Switch. This repository holds no game files, keys, firmware, or
extracted content. Builds made from your files are for your own use only.
