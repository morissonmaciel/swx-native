#!/usr/bin/env python3
"""patches/apply_patches.py — copy core/nxnative to /tmp and patch the copy.

Self-contained (stdlib only): no dependencies outside this folder.
The pristine checkout is never touched: every run deletes the destination,
clones the source fresh (APFS copy-on-write when available) and applies
patches/series in order (real apply + `# verify:` sentinels per patch).
No dry-run gate: patch(1) dry-run checks each hunk against the pristine
file without prior hunks' effects, so it can never validate hunks that
depend on earlier hunks of the same file; the disposable copy makes the
dry-run redundant — apply straight, verify with sentinels.
patches/patch_nxnative.sh is kept as historical reference only.

Usage: apply_patches.py [--core <dir>] [--dest <dir>] [--check]
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
        description="Copy <core> to a fresh /tmp dir and apply patches/series in order there.",
    )
    ap.add_argument("--core", default=str(ROOT / "core" / "nxnative"),
                    help="pristine nxnative checkout")
    ap.add_argument("--dest", default="",
                    help="where to build the patched copy "
                         "(default: /tmp/swx-nxnative-<hash>)")
    ap.add_argument("--check", action="store_true",
                    help="same full pipeline; only differs in intent")
    return ap.parse_args(argv)


def series_entries(series_path):
    entries = []
    with open(series_path, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if s and not s.startswith("#"):
                entries.append(s)
    return entries


def inputs_hash(series_path, entries):
    h = hashlib.sha256()
    h.update(series_path.read_bytes())
    for e in entries:
        p = HERE / e
        if not p.is_file():
            die(f"patch file not found: {p} (series entry: {e})")
        h.update(e.encode())
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
    patch_path = HERE / entry
    data = patch_path.read_bytes()
    patch_bin = shutil.which("patch")
    if patch_bin is None:
        die("patch(1) not found")
    r = subprocess.run([patch_bin, "-p1", "--batch", "--forward"],
                       input=data, cwd=str(dest),
                       stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        die(f"apply failed: {entry}")
    if not check_sentinels(patch_path, dest):
        die(f"sentinels do not hold after applying: {entry}")
    ok(f"applied {entry}")


def main(argv=None):
    args = parse_args(argv)
    series_path = HERE / "series"
    core = Path(args.core)
    if not series_path.is_file():
        die(f"series not found: {series_path}")
    if not core.is_dir():
        die(f"core dir not found: {core} (pass --core <dir>)")
    entries = series_entries(series_path)
    dest = Path(args.dest) if args.dest else Path(
        f"/tmp/swx-nxnative-{inputs_hash(series_path, entries)}")
    clone_tree(core, dest)
    if not entries:
        print("nothing to do (series empty or all commented).")
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
