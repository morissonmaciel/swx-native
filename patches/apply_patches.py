#!/usr/bin/env python3
"""patches/apply_patches.py — copy core/nxnative to /tmp and patch the copy.

Self-contained (stdlib only: no dependencies outside this folder).
The pristine checkout is never touched: every run deletes the destination,
clones the source fresh (APFS copy-on-write when available) and applies
patches/patches.toml in order — only entries with enabled = true whose
games/platforms match --game/--platform (real apply + `# verify:` sentinels
per patch, no dry-run gate: patch(1) dry-run checks each hunk against the
pristine file without prior hunks' effects, so it can never validate hunks
that depend on earlier hunks of the same file; the disposable copy makes
the dry-run redundant — apply straight, verify with sentinels).
patches/patch_nxnative.sh + the old `series` file are historical only.

Usage: apply_patches.py [--core <dir>] [--dest <dir>] [--platform mac|ios] [--game <id>] [--check]
Prints SWX_PATCHED_CORE=<dest> as its last stdout line, so a caller
(e.g. setup.sh) can capture the folder to point the extract/install
command at instead of core/nxnative.
"""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ImportError:  # Python < 3.11: use the venv (3.14) instead
    tomllib = None

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

GREEN = "\033[32m✔\033[0m"
RED = "\033[31m✘\033[0m"


def die(msg):
    print(f"[ERROR] {msg}", file=sys.stderr)
    sys.exit(1)


def ok(msg):
    print(f"{GREEN} {msg}")


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        prog="apply_patches.py",
        description="Copy <core> to a fresh /tmp dir and apply in-scope patches/patches.toml entries in order there.",
    )
    ap.add_argument("--core", default=str(ROOT / "core" / "nxnative"),
                    help="pristine nxnative checkout")
    ap.add_argument("--dest", default="",
                    help="where to build the patched copy "
                         "(default: /tmp/swx-nxnative-<hash>)")
    ap.add_argument("--platform", default="mac",
                    help="target platform family (default: mac); only matching entries apply")
    ap.add_argument("--game", default="*",
                    help="title id (default: * = any game); only matching entries apply")
    ap.add_argument("--check", action="store_true",
                    help="same full pipeline; only differs in intent")
    return ap.parse_args(argv)


def load_stack(toml_path):
    if tomllib is None:
        die("tomllib missing (need Python >= 3.11): use the repo .venv")
    with open(toml_path, "rb") as f:
        data = tomllib.load(f)
    patches = data.get("patch", [])
    if not isinstance(patches, list):
        die(f"bad patches file (no [[patch]] list): {toml_path}")
    return patches


def matches(entry, platform, game):
    games = entry.get("games", ["*"])
    platforms = entry.get("platforms", ["mac", "ios"])
    return (game == "*" or "*" in games or game in games) and platform in platforms


def select_entries(stack, platform, game):
    """(to-apply, skipped): enabled + scope match, in file order."""
    apply, skipped = [], []
    for entry in stack:
        path = entry.get("path", "")
        if not entry.get("enabled", False):
            skipped.append((path, "disabled"))
        elif not matches(entry, platform, game):
            skipped.append((path, "out of scope"))
        else:
            apply.append(entry)
    return apply, skipped


def inputs_hash(toml_path, entries, platform, game):
    """Identity of this filtered stack: scope + registry + patch contents."""
    h = hashlib.sha256()
    h.update(f"{platform}\n{game}\n".encode())
    h.update(toml_path.read_bytes())
    for e in entries:
        p = HERE / e["path"]
        if not p.is_file():
            die(f"patch file not found: {p} (entry: {e['path']})")
        h.update(e["path"].encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:12]


def clone_tree(src, dest):
    if os.path.lexists(dest):
        print(f"removing previous copy: {dest}")
        shutil.rmtree(dest, ignore_errors=False)
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"copying {src} -> {dest}")
    try:
        subprocess.run(["cp", "-Rc", str(src), str(dest)],
                       check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        shutil.copytree(src, dest, symlinks=True)
    if not dest.is_dir():
        die(f"copy produced no dir: {dest}")


def patch_sentinels(patch_path):
    specs = []
    with open(patch_path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("# verify: "):
                specs.append(line[len("# verify: "):].rstrip("\n"))
    return specs


def check_sentinels(patch_path, dest):
    """Every `# verify:` literal must hold. Returns True, else False."""
    bad = False
    for spec in patch_sentinels(patch_path):
        if not spec or spec.startswith("#"):
            continue
        rel, sep, lit = spec.partition(":")
        target = dest / rel
        if not sep:
            if not target.exists():
                print(f"[ERROR] verify missing file: {rel} ({patch_path.name})",
                      file=sys.stderr)
                bad = True
        else:
            try:
                data = target.read_bytes()
            except OSError:
                data = b""
            if lit.encode() not in data:
                print(f"[ERROR] verify not found: {rel} :: {lit} ({patch_path.name})",
                      file=sys.stderr)
                bad = True
    return not bad


def apply_one(entry, dest):
    patch_path = HERE / entry["path"]
    data = patch_path.read_bytes()
    patch_bin = shutil.which("patch")
    if patch_bin is None:
        die("patch(1) not found")
    r = subprocess.run([patch_bin, "-p1", "--batch", "--forward"],
                       input=data, cwd=str(dest),
                       stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        die(f"apply failed: {entry['path']}")
    if not check_sentinels(patch_path, dest):
        die(f"sentinels do not hold after applying: {entry['path']}")
    flag = entry.get("flag", "")
    ok(f"applied {entry['path']}" + (f" [{flag}]" if flag else ""))


def main(argv=None):
    args = parse_args(argv)
    toml_path = HERE / "patches.toml"
    core = Path(args.core)
    if not toml_path.is_file():
        die(f"patches.toml not found: {toml_path}")
    if not core.is_dir():
        die(f"core dir not found: {core} (pass --core <dir>)")
    stack = load_stack(toml_path)
    entries, skipped = select_entries(stack, args.platform, args.game)
    for path, why in skipped:
        print(f"skip {path} ({why})")
    # The destination covers the filtered set: different scope, different tree.
    dest = Path(args.dest) if args.dest else Path(
        f"/tmp/swx-nxnative-{inputs_hash(toml_path, entries, args.platform, args.game)}")
    clone_tree(core, dest)
    if not entries:
        print("nothing to do (no enabled in-scope entries).")
    for entry in entries:
        apply_one(entry, dest)
    mode = "check" if args.check else "apply"
    print(f"done ({mode}): {dest}")
    print(f"SWX_PATCHED_CORE={dest}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\ninterrupted.", file=sys.stderr)
        sys.exit(130)
