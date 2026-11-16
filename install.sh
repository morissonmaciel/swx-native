#!/bin/sh
# swx-native — install.sh
# Terminal entry point: prepares the environment, then delegates to the Python
# wrapper (a backend reusable by a future graphical UI).
# The wrapper (wrapper/main.py) does not exist yet: this script leaves the env
# ready and exits with a clear message when it is missing.
set -eu

# Soft exit on Ctrl+C: no stack traces, standard 130 status.
trap 'echo "" >&2; echo "interrupted." >&2; exit 130' INT

ROOT="$(cd "$(dirname "$0")" && pwd)"
NX="$ROOT/core/nxnative"
VENV="$ROOT/.venv"
WRAPPER="$ROOT/wrapper/main.py"
TOOLS_REQ="$ROOT/wrapper/requirements.txt"
PYTHON_WANT="${SWX_PYTHON:-3.14}"
RECREATE=0

for a in "$@"; do
  case "$a" in
    -h|--help)
      echo "usage: ./install.sh [--recreate] [--python 3.14] [-- <wrapper args>]"
      echo "  Prepares .venv with uv + Python >= 3.14, checks nxnative deps (mac-app), then calls wrapper/main.py."
      exit 0 ;;
    --recreate) RECREATE=1; shift ;;
    --python) PYTHON_WANT="${2:-}"; shift 2 ;;
  esac
done
# Args after "--" go to the wrapper.
if [ "${1:-}" = "--" ]; then shift; fi

say() { printf '%s\n' "$*"; }
fail() { printf '[ERROR] %s\n' "$*" >&2; exit 1; }
warn() { printf '[WARNING] %s\n' "$*" >&2; }

# ---- 0. System: macOS on arm64 only ----
[ "$(uname -s)" = "Darwin" ] || fail "macOS only (uname: $(uname -s))."
[ "$(uname -m)" = "arm64" ] || fail "Apple Silicon (arm64) only (uname -m: $(uname -m))."
if [ "$(sysctl -n sysctl.proc_translated 2>/dev/null || echo 0)" = "1" ]; then
  fail "terminal running under Rosetta (Intel). Use a native arm64 terminal."
fi
OSV="$(sw_vers -productVersion)"
say "macOS $OSV"
# require 26.6 <= OSV
if [ "$(printf '26.6\n%s\n' "$OSV" | sort -V | head -1)" != "26.6" ]; then
  fail "macOS >= 26.6 required (nxnative: MACOS_MIN 26.6, Xcode 27.2). Current: $OSV."
fi

# ---- Xcode 27.2 + MetalToolchain (macosx only for mac-app; no iOS SDK) ----
command -v xcodebuild >/dev/null || fail "Xcode not found. Install Xcode 27.2: https://developer.apple.com/download/all/?q=Xcode%2027.2"
command -v xcrun >/dev/null || fail "xcrun not found (incomplete Xcode)."
DEVDIR="$(xcode-select -p 2>/dev/null || true)"
say "Xcode: $DEVDIR"
case "$DEVDIR" in *CommandLineTools*) fail "xcode-select points to Command Line Tools ($DEVDIR). Select Xcode: sudo xcode-select -s /Applications/Xcode.app (or Xcode-27.2.app)." ;; esac
if ! xcodebuild -version 2>/dev/null | head -1 | grep -q "27.2"; then
  warn "Xcode 27.2 expected (shader chain lock: metal 32023.922). Current: $(xcodebuild -version 2>/dev/null | head -1)."
fi
xcrun -sdk macosx metal --version >/dev/null 2>&1 \
  || fail "Metal compiler does not run. Install it: xcodebuild -downloadComponent MetalToolchain"
say "Metal (macosx): $(xcrun -sdk macosx metal --version 2>/dev/null | head -1)"

# ---- Build tools (mac-app: no xcodegen, no iOS SDK) ----
need_brew="zstd glslang"
for t in make zstd glslangValidator; do
  command -v "$t" >/dev/null || fail "tool '$t' not found. Install: brew install $need_brew (make ships with Xcode)."
done
command -v xcodegen >/dev/null || warn "xcodegen missing (only required for iOS IPAs; mac-app does not need it). Optional: brew install xcodegen."
say "build tools ok (make, zstd, glslangValidator)."

# ---- Native binaries committed to nxnative (run, never rebuild; rebuilding needs .NET SDK 10) ----
# A browser download leaves quarantine on the binaries and Gatekeeper kills them with SIGKILL (exit 137).
# Strip the flag from this workspace's own 3 binaries before running them.
for b in "$NX/dm/gfx/tools/dmsh_xlate_bin/dmsh_xlate" "$NX/dm/tools/nx_extract_bin/nx_extract" "$NX/dm/gfx/tools/spirv_cross_bin/spirv-cross"; do
  [ -x "$b" ] || fail "binary missing/not executable: $b (re-fetch core/nxnative)."
  if xattr -l "$b" 2>/dev/null | grep -q com.apple.quarantine; then
    say "stripping quarantine: $b"
    xattr -d com.apple.quarantine "$b" 2>/dev/null || warn "could not strip quarantine from $b (run: xattr -d com.apple.quarantine '$b')."
  fi
done
"$NX/dm/tools/nx_extract_bin/nx_extract" --source-sha >/dev/null 2>&1 \
  || warn "nx_extract answers no --source-sha (continuing; nxnative validates it at extraction time)."
# dmsh_xlate is only required when core/nxnative uses it (currently: NVN shader translation).
# build_ipa_from_nsp.py already validates it via check_prereqs/check_translator at build time,
# so here it is a warning, never a fatal error.
if grep -rq "dmsh_xlate" "$NX/scripts" "$NX/dm/gfx/tools" 2>/dev/null; then
  if "$NX/dm/gfx/tools/dmsh_xlate_bin/dmsh_xlate" --source-sha >/dev/null 2>&1; then
    say "dmsh_xlate ok."
  else
    warn "dmsh_xlate does not start (--source-sha failed); nxnative will require it at the shader step. Re-fetch core/nxnative if it persists."
  fi
else
  say "dmsh_xlate not referenced by core/nxnative; check skipped."
fi
say "nx_extract + spirv-cross present and executable."

# ---- uv ----
if ! command -v uv >/dev/null 2>&1; then
  warn "uv not found; installing..."
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
  || fail "venv needs Python >= 3.14 (current: $PYV). Run: ./install.sh --recreate --python 3.14"
[ "$("$VENV/bin/python" -c 'import platform; print(platform.machine())')" = "arm64" ] \
  || fail "venv python is not arm64 (Rosetta). Recreate with an arm64 uv/python: ./install.sh --recreate"

# ---- Python deps of the tool set (pinned) ----
[ -f "$TOOLS_REQ" ] || fail "requirements not found: $TOOLS_REQ"
say "installing Python deps ($TOOLS_REQ)..."
uv pip install --python "$VENV/bin/python" -r "$TOOLS_REQ" || fail "failed to install requirements."
# backports.zstd only matters before 3.14 (nxnative's check_prereqs); covered in case the venv is ever < 3.14.
"$VENV/bin/python" -c 'import compression.zstd' 2>/dev/null || uv pip install --python "$VENV/bin/python" backports.zstd || fail "failed to install backports.zstd."
"$VENV/bin/python" -c 'import lz4.block, capstone, fontTools, zbic; import capstone as c; assert c.__version__.startswith("5."), c.__version__' \
  || fail "Python modules missing after install (lz4/capstone==5.x/fonttools/zbic)."
say "Python deps ok."

# ---- disk space (BOTW is a ~16 GB app; a build wants ~2x the game + 10 GB) ----
FREEG="$(df -g "$ROOT" | awk 'NR==2 {print $4}')"
[ "${FREEG:-0}" -ge 45 ] || warn "low free space (${FREEG:-?} GB; >= 45 GB recommended for BOTW 1.9.0)."

say "environment ready."
if [ -f "$WRAPPER" ]; then
  say "calling wrapper: $WRAPPER $*"
  exec "$VENV/bin/python" "$WRAPPER" "$@"
else
  warn "wrapper missing ($WRAPPER not implemented yet). Environment ready at $VENV."
  warn "next step: implement wrapper/main.py (backend), then run: ./.venv/bin/python wrapper/main.py --game-dir <game folder> -k prod.keys --platform mac-app"
  exit 0
fi
