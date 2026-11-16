#!/bin/sh
# patches/patch_nxnative.sh — apply patches/series onto core/nxnative.
# Idempotent (marker .swx-patches-applied), check-before-apply (no half-patched
# tree), quiet no-op when series is missing or empty.
# Usage: patch_nxnative.sh <core dir> [--force]
set -eu

CORE=""; FORCE=0
for a in "$@"; do
  case "$a" in
    --force) FORCE=1 ;;
    -h|--help) echo "usage: patch_nxnative.sh <core dir> [--force]"; exit 0 ;;
    *) [ -n "$CORE" ] && { echo "[ERROR] usage: patch_nxnative.sh <core dir> [--force]" >&2; exit 2; }; CORE="$a" ;;
  esac
done
[ -n "$CORE" ] || { echo '[ERROR] usage: patch_nxnative.sh <core dir> [--force]' >&2; exit 2; }
[ -d "$CORE" ] || { echo "[ERROR] core dir missing: $CORE" >&2; exit 1; }
command -v patch >/dev/null 2>&1 || { echo '[ERROR] patch(1) not found (install Xcode command line tools).' >&2; exit 1; }

HERE="$(cd "$(dirname "$0")" && pwd)"
SERIES="$HERE/series"
MARKER="$CORE/.swx-patches-applied"

hash_state() {   # hash of series + every listed patch's content
    if [ ! -f "$SERIES" ]; then echo "empty"; return; fi
    { cat "$SERIES"; for p in $(grep -v '^[[:space:]]*#' "$SERIES" | grep -v '^[[:space:]]*$'); do cat "$HERE/$p"; done; } 2>/dev/null | shasum -a 256 | cut -c1-16
}

PATCHES=""
if [ -f "$SERIES" ]; then
    # shellcheck disable=SC2016
    PATCHES="$(grep -v '^[[:space:]]*#' "$SERIES" | grep -v '^[[:space:]]*$' || true)"
fi
if [ -z "$PATCHES" ]; then
    echo "patches: nothing to apply (no series)."
    exit 0
fi

WANT="$(hash_state)"
if [ "$FORCE" -eq 0 ] && [ -f "$MARKER" ] && [ "$(cat "$MARKER")" = "$WANT" ]; then
    echo "patches: already applied ($WANT)."
    exit 0
fi

# 1. check everything first
for p in $PATCHES; do
    [ -f "$HERE/$p" ] || { echo "[ERROR] patch listed in series but missing: $p" >&2; exit 1; }
    if ! patch -p1 --dry-run -d "$CORE" < "$HERE/$p" >/dev/null 2>&1; then
        echo "[ERROR] patch does not apply cleanly (rebase it): $p" >&2
        echo "        resolve inside core/nxnative, then refresh patches/$p -- never edit core directly." >&2
        exit 1
    fi
done
# 2. then apply everything
for p in $PATCHES; do
    patch -p1 -d "$CORE" < "$HERE/$p" >/dev/null || { echo "[ERROR] failed applying: $p" >&2; exit 1; }
    echo "patches: applied $p"
done
echo "$WANT" > "$MARKER"
echo "patches: done ($WANT)."
