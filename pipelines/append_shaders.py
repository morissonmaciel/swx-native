#!/usr/bin/env python3
"""pipelines/append_shaders.py — merge community pipelines into the install.

Self-contained: the Python toolset lives in this dir (shader_derive.py,
shader_convert.py, shader_list.py — moved from wrapper/, which is retired)
plus the vendored mac-nativegames shader list (shader_list.tsv).
Derives community coverage for the core profile
(core/nxnative/dm/games/<title id>):
  swx_pairs.tsv (profile pairs + new community pairs) -> DM_PSO_PAIRS
  derived/<id>/ overlay (profile minus patches.py, manifest extended)
    -> DM_GAMES_DIR=<parent> + DM_GAMES_FIRST=1
Work happens in /tmp/swx-pipelines-<hash> (hash over the inputs, same
inputs reuse the same path; deleted and rebuilt every run, like patches/).
Validated there (pair keys resolve, split tables byte-identical,
no patches.py, manifest only gains checked R/S rows); then installed into <I>.

Usage: append_shaders.py --install-dir <I> [--core <dir>] [--title-id <id>] [--mng-list <file>] [--check]
  --check validates everything in tmp and discards it; touches nothing in <I>.
"""
import argparse
import hashlib
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

sys.path.insert(0, str(HERE))
from shader_derive import build_derived, read_profile, write_pairs_file  # noqa: E402

GREEN = "\033[32m✔\033[0m"

TITLE_ID = "01007EF00011E000"
TABLE_FILES = ("shader_map.tsv", "pso_pairs.tsv", "shader_pairs.tsv",
               "pso_manifest.tsv", "shader_facts.tsv")
VALIDATED_TABLES = ("shader_map.tsv", "shader_facts.tsv",
                    "shader_pairs.tsv", "pso_pairs.tsv")


def die(msg):
    print(f"[ERROR] {msg}", file=sys.stderr)
    sys.exit(1)


def ok(msg):
    print(f"{GREEN} {msg}")


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        prog="append_shaders.py",
        description="Derive community shader coverage in a tmp dir, validate it, "
                    "install swx_pairs.tsv + derived/<id>/ into <I>/shaders.",
    )
    ap.add_argument("--install-dir", default="",
                    help="Install Dir (<I>); swx_pairs.tsv + derived/<id>/ land in <I>/shaders")
    ap.add_argument("--core", default=str(ROOT / "core" / "nxnative"),
                    help="pristine nxnative checkout")
    ap.add_argument("--title-id", default=TITLE_ID, help="game profile")
    ap.add_argument("--mng-list", default=str(HERE / "shader_list.tsv"),
                    help="community shader list")
    ap.add_argument("--check", action="store_true",
                    help="validate everything in tmp and discard; touches nothing in <I>")
    return ap.parse_args(argv)


def is_hex16(s):
    return len(s) == 16 and all(c in "0123456789abcdef" for c in s)


def inputs_hash(mng_list, profile_dir):
    h = hashlib.sha256()
    h.update(Path(mng_list).read_bytes())
    for t in TABLE_FILES:
        p = profile_dir / t
        if p.is_file():
            h.update(t.encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:12]


def validate(out_dir, profile_dir, title_id):
    """Validate the derived artifacts. Raises SystemExit via die() on failure."""
    keys, _, _ = read_profile(str(profile_dir))
    # 1. every pair row resolves to known map keys
    n = 0
    with open(out_dir / "swx_pairs.tsv", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            p = line.split("\t")
            if len(p) < 2:
                die(f"bad pair row: {line!r}")
            if not (is_hex16(p[0]) and is_hex16(p[1])):
                die(f"bad pair keys: {line!r}")
            if p[0] not in keys or p[1] not in keys:
                die(f"unknown pair keys: {line!r}")
            n += 1
    if n == 0:
        die("pairs file has no data rows")
    print(f"pairs ok: {n} rows, all keys resolve")
    # 2. overlay: split-validated tables byte-identical, no patches.py
    derived = out_dir / "derived" / title_id
    if not derived.is_dir():
        die("derived overlay missing")
    for t in VALIDATED_TABLES:
        a = profile_dir / t
        if not a.is_file():
            continue
        b = derived / t
        if not b.is_file():
            die(f"overlay lost {t}")
        if a.read_bytes() != b.read_bytes():
            die(f"overlay drifted: {t}")
    if (derived / "patches.py").exists():
        die("overlay must exclude patches.py")
    print("overlay ok: validated tables identical, no patches.py")
    # 3. manifest only gains R rows and checked S rows (plus comments)
    base_rows = set()
    mpath = profile_dir / "pso_manifest.tsv"
    if mpath.is_file():
        with open(mpath, encoding="utf-8") as f:
            base_rows = {ln for ln in (l.rstrip("\n") for l in f)
                         if ln and not ln.startswith("#")}
    new_r = new_s = 0
    with open(derived / "pso_manifest.tsv", encoding="utf-8") as f:
        for ln in (l.rstrip("\n") for l in f):
            if not ln or ln.startswith("#") or ln in base_rows:
                continue
            if ln.startswith("R\t"):
                new_r += 1
            elif ln.startswith("S\t"):
                p = ln.split("\t")
                if (len(p) != 3 or not p[1].endswith("_b")
                        or p[1][:-2] not in keys
                        or p[2] not in ("0", "1", "2", "3", "4", "5")):
                    die(f"bad S manifest row added: {ln!r}")
                new_s += 1
            else:
                die(f"non-R/S manifest row added: {ln!r}")
    print(f"manifest ok: {new_r} R rows + {new_s} S rows added, nothing else changed")


def main(argv=None):
    args = parse_args(argv)
    mng_list = Path(args.mng_list)
    profile_dir = Path(args.core) / "dm" / "games" / args.title_id
    if not mng_list.is_file():
        die(f"mng list not found: {mng_list} (pass --mng-list)")
    if not profile_dir.is_dir():
        die(f"profile not found: {profile_dir} (pass --core/--title-id)")
    if not (profile_dir / "shader_map.tsv").is_file():
        die(f"profile has no shader_map.tsv: {profile_dir}")
    install_dir = None
    if args.check:
        pass
    else:
        if not args.install_dir:
            die("--install-dir is required (or use --check)")
        install_dir = Path(args.install_dir)
        if install_dir.exists() and not install_dir.is_dir():
            die(f"install-dir is not a directory: {install_dir}")
        (install_dir / "shaders").mkdir(parents=True, exist_ok=True)

    tmp = Path(f"/tmp/swx-pipelines-{inputs_hash(mng_list, profile_dir)}")
    if os.path.lexists(tmp):
        print(f"removing previous tmp: {tmp}")
        shutil.rmtree(tmp)
    out = tmp / "out"
    (out / "derived").mkdir(parents=True)

    print(f"deriving from {mng_list} + {profile_dir}")
    pairs_stats = write_pairs_file(str(profile_dir), str(mng_list),
                                   str(out / "swx_pairs.tsv"))
    mani_stats = build_derived(str(profile_dir), str(mng_list),
                               str(out / "derived" / args.title_id))
    print(f"pairs profile={pairs_stats['profile_pairs']} "
          f"added={pairs_stats['added']} total={pairs_stats['total']}")
    print(f"manifest rows_added={mani_stats['manifest_rows_added']} "
          f"converted={mani_stats.get('converted_rows')}")

    print(f"validating in {tmp}")
    validate(out, profile_dir, args.title_id)

    if args.check:
        ok("check passed; tmp discarded (nothing installed)")
        shutil.rmtree(tmp)
        print("done (check)")
        return

    assert install_dir is not None
    shaders = install_dir / "shaders"
    shutil.move(str(out / "swx_pairs.tsv"), str(shaders / "swx_pairs.tsv"))
    ok(f"swx_pairs.tsv {shaders / 'swx_pairs.tsv'}")
    (shaders / "derived").mkdir(exist_ok=True)
    old_overlay = shaders / "derived" / args.title_id
    if old_overlay.exists() or os.path.lexists(old_overlay):
        shutil.rmtree(old_overlay)
    shutil.move(str(out / "derived" / args.title_id), str(old_overlay))
    ok(f"derived overlay {old_overlay}")
    shutil.rmtree(tmp)
    print(f"done (install): build consumes DM_PSO_PAIRS={shaders / 'swx_pairs.tsv'}")
    print(f"done (install): build consumes DM_GAMES_DIR={shaders / 'derived'} DM_GAMES_FIRST=1")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\ninterrupted.", file=sys.stderr)
        sys.exit(130)
