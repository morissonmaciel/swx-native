#!/bin/sh
# swx-native — setup.sh
# Minimal venv bootstrap for nxnative work. Only creates/updates .venv via uv.
set -eu

trap 'echo "" >&2; echo "interrupted." >&2; exit 130' INT

ROOT="$(cd "$(dirname "$0")" && pwd)"
VENV="$ROOT/.venv"
TOOLS_REQ="$ROOT/wrapper/requirements.txt"
PYTHON_WANT="${SWX_PYTHON:-3.14}"
RECREATE=0
BASE=""
UPDATE=""
KEYS=""
INSTALL_DIR=""

while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help)
      echo "usage: ./setup.sh [--recreate] [--python 3.14] [--base <file>] [--update <file>] [--keys <file>] [--install-dir <dir>]"
      echo "  Creates .venv with uv + Python >= 3.14, installs wrapper/requirements.txt,"
      echo "  prepares <I>/{saves,shaders,mods,game} + <I>/env.txt, then builds via nxnative (--platform mac, no .app yet)."
      exit 0 ;;
    --recreate) RECREATE=1; shift ;;
    --python) PYTHON_WANT="${2:-}"; shift 2 ;;
    --base) BASE="${2:-}"; shift 2 ;;
    --base=*) BASE="${1#--base=}"; shift ;;
    --update) UPDATE="${2:-}"; shift 2 ;;
    --update=*) UPDATE="${1#--update=}"; shift ;;
    --keys) KEYS="${2:-}"; shift 2 ;;
    --keys=*) KEYS="${1#--keys=}"; shift ;;
    --install-dir) INSTALL_DIR="${2:-}"; shift 2 ;;
    --install-dir=*) INSTALL_DIR="${1#--install-dir=}"; shift ;;
    --) shift; break ;;
    *) echo "[ERROR] unknown arg: $1 (see ./setup.sh --help)" >&2; exit 2 ;;
  esac
done

say() { printf '%s\n' "$*"; }
fail() { printf '[ERROR] %s\n' "$*" >&2; exit 1; }
ok() { printf '\033[32m✔\033[0m %s: %s\n' "$1" "$2"; }
bad() { printf '\033[31m✘\033[0m %s: %s\n' "$1" "$2" >&2; }

# ---- uv ----
if ! command -v uv >/dev/null 2>&1; then
  echo "[WARNING] uv not found; installing..." >&2
  if command -v brew >/dev/null 2>&1; then brew install uv || fail "failed to install uv via brew.";
  else curl -LsSf https://astral.sh/uv/install.sh | sh || fail "failed to install uv.";
  fi
  export PATH="$HOME/.local/bin:$PATH"
fi
command -v uv >/dev/null 2>&1 || fail "uv unavailable after install. Add ~/.local/bin to PATH."
say "uv: $(uv --version)"

# ---- local venv with Python >= 3.14 ----
if [ "$RECREATE" = "1" ] && [ -d "$VENV" ]; then say "removing .venv (--recreate)..."; rm -rf "$VENV"; fi
if [ ! -x "$VENV/bin/python" ]; then
  say "creating .venv (python $PYTHON_WANT)..."
  uv venv --python "$PYTHON_WANT" "$VENV" || fail "failed to create .venv (uv venv --python $PYTHON_WANT)."
fi
PYV="$("$VENV/bin/python" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}")')"
say "venv python: $PYV ($VENV/bin/python)"
"$VENV/bin/python" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 14) else 1)' \
  || fail "venv needs Python >= 3.14 (current: $PYV). Run: ./setup.sh --recreate --python 3.14"
[ "$("$VENV/bin/python" -c 'import platform; print(platform.machine())')" = "arm64" ] \
  || fail "venv python is not arm64 (Rosetta). Recreate with an arm64 uv/python: ./setup.sh --recreate"

# ---- Python deps of the tool set (pinned) ----
[ -f "$TOOLS_REQ" ] || fail "requirements not found: $TOOLS_REQ"
say "installing Python deps ($TOOLS_REQ)..."
uv pip install --python "$VENV/bin/python" -r "$TOOLS_REQ" || fail "failed to install requirements."
"$VENV/bin/python" -c 'import lz4.block, capstone, fontTools, zbic; import capstone as c; assert c.__version__.startswith("5."), c.__version__' \
  || fail "Python modules missing after install (lz4/capstone==5.x/fonttools/zbic)."
say "Python deps ok."

# ---- game inputs (<I> layout) ----
if [ -n "$BASE" ] || [ -n "$UPDATE" ] || [ -n "$KEYS" ] || [ -n "$INSTALL_DIR" ]; then
  FAILED=0
  if [ -n "$BASE" ]; then
    if [ -f "$BASE" ]; then ok "base" "$BASE"; else bad "base not found" "$BASE"; FAILED=1; fi
  fi
  if [ -n "$UPDATE" ]; then
    if [ -f "$UPDATE" ]; then ok "update" "$UPDATE"; else bad "update not found" "$UPDATE"; FAILED=1; fi
  fi
  if [ -n "$KEYS" ]; then
    if [ -f "$KEYS" ]; then ok "keys" "$KEYS"; else bad "keys not found" "$KEYS"; FAILED=1; fi
  fi
  if [ -n "$INSTALL_DIR" ]; then
    if [ -e "$INSTALL_DIR" ] && [ ! -d "$INSTALL_DIR" ]; then
      bad "install-dir not a directory" "$INSTALL_DIR"; FAILED=1
    else
      if [ -d "$INSTALL_DIR" ]; then ok "install-dir (kept)" "$INSTALL_DIR";
      else mkdir -p "$INSTALL_DIR" || { bad "cannot create install-dir" "$INSTALL_DIR"; FAILED=1; }; [ -d "$INSTALL_DIR" ] && ok "install-dir (created)" "$INSTALL_DIR"; fi
      if [ -d "$INSTALL_DIR" ]; then
        for sub in saves shaders mods game; do
          if [ -d "$INSTALL_DIR/$sub" ]; then ok "$sub (kept)" "$INSTALL_DIR/$sub";
          else mkdir -p "$INSTALL_DIR/$sub" && ok "$sub (created)" "$INSTALL_DIR/$sub" || { bad "cannot create $sub" "$INSTALL_DIR/$sub"; FAILED=1; }; fi
        done
      fi
    fi
  fi
  [ "$FAILED" = "0" ] || fail "one or more inputs missing (see above)."
fi

# ---- <I>/env.txt (created once, never overwritten) ----
# Feeds both the build (--env-file: where the prebuild writes pso_archive)
# and the run (.command applies it last, so it wins over baked defaults).
# User edits must survive reinstalls: only create when missing.
if [ -n "$INSTALL_DIR" ]; then
  ENV_FILE="$INSTALL_DIR/env.txt"
  if [ -f "$ENV_FILE" ]; then
    ok "env.txt (kept, user-managed)" "$ENV_FILE"
  else
    # Template only: no active keys. Baked defaults live in the .command
    # ($HERE-based, self-contained); the build uses its own managed file
    # below. Anything set here wins at game launch.
    {
      printf '# SWX runtime overrides (optional).\n'
      printf '# Baked defaults live in "Breath of the Wild.command"; any KEY=VALUE here wins.\n'
      printf '# Examples (current baked values shown commented out):\n'
      printf '# DM_SAVE=%s/saves\n' "$INSTALL_DIR"
      printf '# DM_PSO_USER=%s/shaders/pso_user.tsv\n' "$INSTALL_DIR"
      printf '# DM_PSO_ARCHIVE=%s/shaders/pso_archive\n' "$INSTALL_DIR"
      printf '# DM_PSO_PAIRS=%s/shaders/swx_pairs.tsv\n' "$INSTALL_DIR"
      printf '# DM_NVN_SHMAP=%s/shaders/shader_map.tsv\n' "$INSTALL_DIR"
      printf '# DM_PSO_MANIFEST=%s/shaders/pso_manifest.tsv\n' "$INSTALL_DIR"
      printf '# DM_LANG=pt-BR\n'
    } > "$ENV_FILE" || fail "cannot write $ENV_FILE"
    ok "env.txt (template created)" "$ENV_FILE"
  fi
fi

# ---- nxnative build (extract -> rewrite -> shaders/host, no .app yet) ----
# <I>/game is the NSP folder (base + update staged as symlinks); the build
# folder is <I>/game/build so extraction stays under game.
# --platform mac stops before packing: it builds the Mac shaders + host and
# prints the command that runs the game. Packing the .app comes later.
if [ -n "$BASE" ] && [ -n "$UPDATE" ] && [ -n "$KEYS" ] && [ -n "$INSTALL_DIR" ]; then
  TITLE_ID="01007EF00011E000"
  # Community pipelines (append_shaders.py): re-enabled — required to
  # (re)materialize the derived overlay, which is the build's active profile
  # (DM_GAMES_DIR) and the trio/manifest source. The MNG list adds exact R
  # rows (and S shader rows) the seed lacks; its pairs are already covered
  # by the profile. Additive only, validated in /tmp before install.
  "$VENV/bin/python" "$ROOT/pipelines/append_shaders.py" --install-dir "$INSTALL_DIR" \
    || fail "append_shaders failed (see output above)."
  # Patches first: clone the pristine core to /tmp and patch the copy, so the
  # build below compiles the modified files. Prints SWX_PATCHED_CORE=<dir> last.
  PATCHED_LINE="$("$VENV/bin/python" "$ROOT/patches/apply_patches.py" --core "$ROOT/core/nxnative" --platform mac --game "$TITLE_ID" | tail -1)" \
    || fail "apply_patches.py failed (see output above)."
  NX="$(printf '%s' "$PATCHED_LINE" | sed -n 's/^SWX_PATCHED_CORE=//p')"
  [ -n "$NX" ] && [ -d "$NX" ] || fail "apply_patches.py did not return a core dir (got: $PATCHED_LINE)"
  ok "patched core" "$NX"
  NX_SCRIPT="$NX/scripts/build_ipa_from_nsp.py"
  [ -f "$NX_SCRIPT" ] || fail "nxnative script missing: $NX_SCRIPT (populate core/nxnative)"
  # A download via browser deixa quarentena nos binários do nxnative e o
  # Gatekeeper mata eles com SIGKILL (exit 137) ou mostra o alerta de malware.
  # Mesmo tratamento do scripts/install.sh: limpa o flag antes de rodar.
  for b in "$NX/dm/gfx/tools/dmsh_xlate_bin/dmsh_xlate" "$NX/dm/tools/nx_extract_bin/nx_extract" "$NX/dm/gfx/tools/spirv_cross_bin/spirv-cross"; do
    [ -x "$b" ] || fail "binary missing/not executable: $b (re-fetch core/nxnative)."
    if xattr -l "$b" 2>/dev/null | grep -q com.apple.quarantine; then
      say "stripping quarantine: $b"
      xattr -d com.apple.quarantine "$b" 2>/dev/null || fail "could not strip quarantine from $b (run: xattr -d com.apple.quarantine '$b')."
    fi
  done
  GAME_DIR="$INSTALL_DIR/game"
  # Managed build env (setup-owned, rewritten every run): the structural
  # SAVE/PSO_* values the prebuild needs. The user's <I>/env.txt stays purely
  # a runtime override file and is never read here.
  BUILD_ENV_FILE="$GAME_DIR/build/build.env"
  mkdir -p "$GAME_DIR/build" || fail "cannot create $GAME_DIR/build"
  {
    printf 'DM_SAVE=%s\n' "$INSTALL_DIR/saves"
    printf 'DM_PSO_USER=%s\n' "$INSTALL_DIR/shaders/pso_user.tsv"
    printf 'DM_PSO_ARCHIVE=%s\n' "$INSTALL_DIR/shaders/pso_archive"
    printf 'DM_PSO_PAIRS=%s\n' "$INSTALL_DIR/shaders/swx_pairs.tsv"
  } > "$BUILD_ENV_FILE" || fail "cannot write $BUILD_ENV_FILE"
  ok "build.env" "$BUILD_ENV_FILE"
  for src in "$BASE" "$UPDATE"; do
    dst="$GAME_DIR/$(basename "$src")"
    if [ -e "$dst" ] || [ -L "$dst" ]; then rm -f "$dst"; fi
    ln -s "$src" "$dst" || fail "cannot stage $(basename "$src") into $GAME_DIR"
    ok "staged" "$dst"
  done
  say "building via nxnative (--platform mac, no .app yet)..."
  DM_GAME="$TITLE_ID" DM_GAMES_DIR="$INSTALL_DIR/shaders/derived" DM_GAMES_FIRST=1 \
    "$VENV/bin/python" "$NX_SCRIPT" "$GAME_DIR" \
    -k "$KEYS" \
    --platform mac \
    --work-dir "$GAME_DIR/build" \
    --env-file "$BUILD_ENV_FILE" \
    || fail "nxnative build failed (see output above)."
  ok "nxnative" "--platform mac ok (no .app yet)"

  # ---- shader tables into <I>/shaders (self-contained <I>) ----
  # All six come from the derived overlay (<I>/shaders/derived/<id>/):
  # shader_map.tsv is the static profile input (copied there byte-identical);
  # c1_full.tsv/cb_extent.tsv are (re)made there by THIS build's Mac shader
  # step (chain_tables writes beside the active profile); pso_manifest.tsv is
  # the extended manifest (profile + community R rows); shader_pairs.tsv is
  # the pair list the runtime prebuild walk needs (without it the walk logs
  # "nothing to build" and its pair-archive bases get zero takes and are
  # swept to obsolete/); pso_pairs.tsv rides along for consistency.
  # Copy only after the build: the set stays consistent (stamp-named tables
  # match the just-built libraries). The pristine core profile must NOT be
  # used as source: those tables are git-ignored build products, absent from
  # a fresh checkout. The .command then points DM_NVN_SHMAP at <I>.
  DERIVED_PROFILE="$INSTALL_DIR/shaders/derived/$TITLE_ID"
  for t in shader_map.tsv c1_full.tsv cb_extent.tsv pso_manifest.tsv shader_pairs.tsv pso_pairs.tsv; do
    src="$DERIVED_PROFILE/$t"
    [ -f "$src" ] || fail "shader table not found after build: $src"
    cp -f "$src" "$INSTALL_DIR/shaders/$t" || fail "cannot copy $t to $INSTALL_DIR/shaders"
    ok "shader table" "$INSTALL_DIR/shaders/$t"
  done

  # ---- <I>/Breath of the Wild.command (double-click to play) ----
  # Replicates build_ipa_from_nsp.py mac_run for --platform mac:
  #   cd <work>/run && DM_ROOT=<work> DM_ROMFS=<extract>/RomFS[.img] ...
  #   <work>/tools/dmhost <work>/patched_<ver>
  # <I>/env.txt wins over the baked defaults (same precedence the script uses:
  # the environment file is applied last).
  WORK="$GAME_DIR/build"
  HOST_BIN="$WORK/tools/dmhost"
  [ -x "$HOST_BIN" ] || fail "dmhost not found: $HOST_BIN (build did not produce it?)"
  PATCHED_ARG=""
  for cand in "$WORK"/patched_*; do
    if [ -d "$cand" ]; then
      [ -n "$PATCHED_ARG" ] && fail "more than one $WORK/patched_* folder."
      PATCHED_ARG="$cand"
    fi
  done
  [ -n "$PATCHED_ARG" ] || fail "no $WORK/patched_* folder (build did not produce one?)."
  ROMFS_ARG=""
  for cand in "$WORK"/extract/*/RomFS.img "$WORK"/extract/*/RomFS; do
    if [ -e "$cand" ]; then ROMFS_ARG="$cand"; break; fi
  done
  [ -n "$ROMFS_ARG" ] || fail "no RomFS found under $WORK/extract (RomFS.img or RomFS)."
  MSL_DIR="$WORK/shader/cache"
  [ -d "$MSL_DIR" ] || fail "Mac shader libraries not found: $MSL_DIR"
  FONT_DIR="$WORK/font"
  SHMAP="$INSTALL_DIR/shaders/shader_map.tsv"
  # NOTE: copied from the pristine profile above (no patch touches the tables);
  # <I>-local so the .command survives /tmp eviction and repo moves.
  [ -f "$SHMAP" ] || fail "shader map not found: $SHMAP"
  RUN_DIR="$WORK/run"
  mkdir -p "$RUN_DIR/save" "$RUN_DIR/temp"
  # Escape for safe embedding inside double quotes in the .command.
  esc() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g; s/\$/\\$/g; s/`/\\`/g'; }
  COMMAND_FILE="$INSTALL_DIR/Breath of the Wild.command"
  {
    printf '#!/bin/sh\n'
    printf '# Breath of the Wild — double-click to play. Generated by setup.sh; re-run setup.sh to regenerate.\n'
    printf 'set -eu\n'
    printf 'HERE="$(cd "$(dirname "$0")" && pwd)"\n'
    printf '# Play log: always appended here (tail -f it to watch live).\n'
    printf 'LOG_DIR="$HERE/run/logs"\n'
    printf 'mkdir -p "$LOG_DIR" || exit 1\n'
    printf 'LOG_FILE="$LOG_DIR/game.log"\n'
    printf 'printf "===== run %%s\\n" "$(date "+%%Y-%%m-%%d %%H:%%M:%%S")" >> "$LOG_FILE"\n'
    printf 'exec >> "$LOG_FILE" 2>&1\n'
    printf 'RUN="%s"\n' "$(esc "$RUN_DIR")"
    printf 'mkdir -p "$RUN/save" "$RUN/temp"\n'
    printf 'export DM_ROOT="%s"\n' "$(esc "$WORK")"
    printf 'export DM_ROMFS="%s"\n' "$(esc "$ROMFS_ARG")"
    printf 'export DM_SAVE="$HERE/saves"\n'
    printf 'export DM_TEMP="$RUN/temp"\n'
    printf 'export DM_PSO_USER="$HERE/shaders/pso_user.tsv"\n'
    printf 'export DM_PSO_ARCHIVE="$HERE/shaders/pso_archive"\n'
    printf 'export DM_PSO_PAIRS="$HERE/shaders/swx_pairs.tsv"\n'
    printf 'export DM_MSL_DIR="%s"\n' "$(esc "$MSL_DIR")"
    printf 'export DM_FONT_DIR="%s"\n' "$(esc "$FONT_DIR")"
    printf 'export DM_OBSOLETE="%s"\n' "$(esc "$WORK/obsolete")"
    printf 'export DM_NVN_SHMAP="$HERE/shaders/shader_map.tsv"\n'
    printf '# NOTE: no DM_PSO_MANIFEST here on purpose. warm_paths() treats a set\n'
    printf '# DM_PSO_MANIFEST as the ONLY prewarm list ("alone") and then the\n'
    printf '# device pso_user.tsv is never read. Unset, prewarm reads the seed\n'
    printf '# beside SHMAP plus <I>/shaders/pso_user.tsv (see warm_paths).\n'
    printf 'export DM_WINDOW=1 DM_AUDIO=1\n'
    printf 'export DM_FPS=60 DM_FPS_TARGET=60 DM_FPS_HOLD=off\n'
    printf '# install defaults on macOS (managed env; override in <I>/env.txt to opt out).\n'
    printf 'export SWX_GPU_KEEPALIVE=1 SWX_GPU_KEEPALIVE_YIELD=1\n'
    printf '# <I>/env.txt wins over the baked defaults (handles values with spaces).\n'
    printf 'ENV_FILE="$HERE/env.txt"\n'
    printf 'if [ -f "$ENV_FILE" ]; then\n'
    printf '  while IFS= read -r line || [ -n "$line" ]; do\n'
    printf '    case "$line" in ""|"#"*) continue;; *?=*) k=${line%%%%=*}; v=${line#*=}; export "$k=$v";; esac\n'
    printf '  done < "$ENV_FILE"\n'
    printf 'fi\n'
    printf 'cd "$RUN"\n'
    printf 'exec "%s" "%s"\n' "$(esc "$HOST_BIN")" "$(esc "$PATCHED_ARG")"
  } > "$COMMAND_FILE" || fail "cannot write $COMMAND_FILE"
  chmod +x "$COMMAND_FILE"
  ok "command" "$COMMAND_FILE"
fi

say "ready: $VENV/bin/python"
