# App layout: mac-nativegames convention vs our Install Dir

How mac-nativegames splits game content between the signed `.app` and
`~/Library/Application Support`, why, and how that maps to our self-contained
Install Dir (`<I>`: `/game`, `/save`, `/shaders`; the built `.app` sits at
`<I>` root, and `/game` keeps nxnative's standard structure).

## Where mac-nativegames puts things

Inside `Breath of the Wild.app` (signed, read-only): the executable (rewritten
game code + host), prebuilt `.metallib` shader libraries, and by default the
~16 GB RomFS image at `Contents/Helpers/GameData-*.bundle` (`build_app.py:
place_data_bundle`). The seal deliberately excludes hashing the image
(`app_seal_valid`, `build_app.py`: "not --deep: that hashes the 16 GB image").

Outside, in `~/Library/Application Support/MacNativeGames/<TOTK|BotW>/`
(`docs/install.md`: "Where things are"):

| Path | Content |
|---|---|
| `profile/` | saves + settings |
| `shader-cache/` | compiled Metal pipelines (regenerable) |
| `shader-list-contribution.txt` | shaders missing from the shared list |
| `mods/<version>/` | RomFS mods + manifests |
| `controls.txt`, `rumble.txt`, `gyro.txt`, … | input/haptics choices |
| `GameData/<version>/romfs.bin` + `romfs.sha256` | game image, **only** with `--mac-game-data library\|folder` |

Logs go to `~/Library/Logs/MacNativeGames/`; builder state to `.../Builder/`.
The player's original dumps and `prod.keys` are read in place, never copied.
The work folder (`BotW_build`) lives wherever the user chose.

## Why

1. **A signed bundle is read-only.** Everything written at runtime (saves,
   settings, shader cache, mods, logs) must live outside, or the seal breaks.
   Code patches are baked into the executable at build time; RomFS mods live
   in `mods/`.
2. **Apple conventions.** `Application Support` = app data the user does not
   open directly (survives updates, covered by Time Machine); `~/Library/Logs`
   for logs.
3. **Lossless updates.** A rebuild replaces only the `.app`; saves, settings,
   mods and shader cache persist, with a `profile-before-<version>` backup
   before the first start on a new version.
4. **Size and speed.** With `library`, the app is a few hundred MB (the image
   is APFS-cloned once); the launcher resolves remembered folder → `GameData`
   → asks the user (`MNGGameDataFolder`).
5. **Per-version isolation.** `mods/<v0|v121|v143>`, `GameData/<version>`.
6. **iOS parallel.** The same split (`Documents/GameData` outside the signed
   payload).

## Mapping to our Install Dir

Our `<I>` is self-contained instead of scattered, which matches nxnative's
model — every location is already env-directed (see its Mac run command:
`DM_ROOT`, `DM_ROMFS`, `DM_SAVE`, `DM_TEMP`, `DM_MSL_DIR`, `DM_FONT_DIR`):

| `<I>` dir | Holds | nxnative env |
|---|---|---|
| `/game` | nxnative's standard structure (`<id>_build`: extract, rewritten modules, shader cache, `tools/dmhost`, logs) | `DM_ROOT`, `DM_ROMFS` |
| `/save` | saves + settings, nested per game (`<id>/`, work clone `<id>.work` beside it; the wrapper points the app here via `DM_SAVE` in the launcher's `env.txt`) | `DM_SAVE` |
| `/shaders` | Metal shader cache + `pso_user.tsv` (per-machine warm list) + `pso_archive/` (pipeline bases); the wrapper points the app here via `DM_PSO_USER` + `DM_PSO_ARCHIVE` | `DM_MSL_DIR`, `DM_PSO_USER`, `DM_PSO_ARCHIVE` |
| `<I>/<App>.app` | signed, read-only bundle (host inside `Contents/MacOS`) | — |

Constraint (from the read-only rule above): saves and shaders must sit
*beside* the `.app` inside `<I>`, never inside the bundle.
