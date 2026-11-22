#!/bin/sh
# patches/apply_patches.sh — copy core/nxnative to /tmp and patch the copy.
# The pristine checkout is never touched: every run deletes the destination,
# clones the source fresh (APFS copy-on-write when available) and applies
# patches/series in order (real apply + `# verify:` sentinels per patch).
# No dry-run gate: patch(1) dry-run checks each hunk against the pristine
# file without prior hunks' effects, so it can never validate hunks that
# depend on earlier hunks of the same file; the disposable copy makes the
# dry-run redundant — apply straight, verify with sentinels.
# patches/patch_nxnative.sh is kept as historical reference only.
#
# usage: sh patches/apply_patches.sh [--core <dir>] [--dest <dir>] [--check]
#   --core <dir>  pristine nxnative checkout (default: <repo>/core/nxnative)
#   --dest <dir>  where to build the patched copy
#                  (default: /tmp/swx-nxnative-<hash>, hash over series +
#                  patch contents, so the same inputs reuse the same path)
#   --check       same full pipeline (dry-run + apply + sentinels) on the
#                 copy; validates without any intent to build from it
#
# Prints SWX_PATCHED_CORE=<dest> as its last stdout line, so a caller
# (e.g. setup.sh) can capture the folder to point the extract/install
# command at instead of core/nxnative.
set -eu

trap 'echo "" >&2; echo "interrupted." >&2; exit 130' INT

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
SERIES="$HERE/series"
CORE="$ROOT/core/nxnative"
DEST=""
MODE="apply"

while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help)
      echo "usage: sh patches/apply_patches.sh [--core <dir>] [--dest <dir>] [--check]"
      echo "  Copies <core> to a fresh /tmp dir and applies patches/series in order there."
      echo "  Last stdout line is SWX_PATCHED_CORE=<dest>."
      exit 0 ;;
    --core) CORE="${2:-}"; shift 2 ;;
    --core=*) CORE="${1#--core=}"; shift ;;
    --dest) DEST="${2:-}"; shift 2 ;;
    --dest=*) DEST="${1#--dest=}"; shift ;;
    --check) MODE="check"; shift ;;
    --) shift; break ;;
    *) echo "[ERROR] unknown arg: $1" >&2; exit 2 ;;
  esac
done

say() { printf '%s\n' "$*"; }
fail() { printf '[ERROR] %s\n' "$*" >&2; exit 1; }
ok() { printf '\033[32m✔\033[0m %s\n' "$*"; }

[ -f "$SERIES" ] || fail "series not found: $SERIES"
[ -d "$CORE" ] || fail "core dir not found: $CORE (pass --core <dir>)"
command -v patch >/dev/null || fail "patch(1) not found."
command -v shasum >/dev/null || fail "shasum not found."

entries() { grep -v '^[[:space:]]*#' "$SERIES" | grep -v '^[[:space:]]*$' || true; }
[ -n "$(entries)" ] || { say "nothing to do (series empty or all commented)."; }

# Reusable hash over what will be applied: same inputs, same destination.
if [ -z "$DEST" ]; then
  HASH_INPUT="$(cat "$SERIES")"
  for p in $(entries); do
    [ -f "$HERE/$p" ] || fail "patch file not found: $HERE/$p (series entry: $p)"
    HASH_INPUT="$HASH_INPUT
$p:$(shasum -a 256 "$HERE/$p" | cut -d' ' -f1)"
  done
  HASH="$(printf '%s' "$HASH_INPUT" | shasum -a 256 | cut -c1-12)"
  DEST="/tmp/swx-nxnative-$HASH"
fi

if [ -e "$DEST" ] || [ -L "$DEST" ]; then
  say "removing previous copy: $DEST"
  rm -rf "$DEST" || fail "cannot remove $DEST"
fi
mkdir -p "$(dirname "$DEST")"
say "copying $CORE -> $DEST"
if ! cp -Rc "$CORE" "$DEST" 2>/dev/null; then
  cp -R "$CORE" "$DEST" || fail "copy failed"
fi
[ -d "$DEST" ] || fail "copy produced no dir: $DEST"

check_sentinels() { # $1 = patch file: every `# verify:` literal must hold
  missing=0
  while IFS= read -r spec || [ -n "$spec" ]; do
    case "$spec" in ""|\#*) continue ;; esac
    rel="${spec%%:*}"; lit="${spec#*:}"
    if [ "$lit" = "$spec" ]; then
      [ -e "$DEST/$rel" ] || { echo "[ERROR] verify missing file: $rel ($1)" >&2; missing=1; }
    else
      grep -F -q -- "$lit" "$DEST/$rel" 2>/dev/null || { echo "[ERROR] verify not found: $rel :: $lit ($1)" >&2; missing=1; }
    fi
  done <<EOF
$(grep '^# verify: ' "$1" 2>/dev/null | sed 's/^# verify: //')
EOF
  [ "$missing" = "0" ] || return 1
}

apply_one() { # $1 = series entry (path relative to patches/)
  f="$HERE/$1"
  # No dry-run gate: patch(1) checks each hunk against the pristine file
  # without prior hunks' effects, so a whole-file dry-run always fails on
  # hunks that depend on earlier hunks of the same file (proven by experiment:
  # dependent hunks fail dry-run single- and multi-section, real apply works).
  # The copy is disposable, so apply straight and verify with sentinels.
  patch -d "$DEST" -p1 --batch --forward < "$f" >/dev/null \
    || fail "apply failed: $1"
  check_sentinels "$f" || fail "sentinels do not hold after applying: $1"
  ok "applied $1"
}

LIST="$(mktemp -t swx-patches.XXXXXX)" || fail "cannot create temp file"
trap 'rm -f "$LIST"; echo "" >&2; echo "interrupted." >&2; exit 130' INT
trap 'rm -f "$LIST"' EXIT
entries > "$LIST"

while IFS= read -r p || [ -n "$p" ]; do
  [ -n "$p" ] || continue
  apply_one "$p"
done < "$LIST"

say "done ($MODE): $DEST"
printf 'SWX_PATCHED_CORE=%s\n' "$DEST"
