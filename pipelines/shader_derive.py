#!/usr/bin/env python3
"""Emit a derived nxnative profile with community pair + shader coverage.

Reads mac-nativegames' shared shader list and extends the profile's
prebuild/warm tables WITHOUT touching the core or the game's own tables:

- pso_pairs.tsv += (vs, fs) pairs extracted from the list's R/B/T rows
  (pulling-base prebuild + hybrid draws then cover first-seen pipelines).
- pso_manifest.tsv += S rows (<key16>_b + stage) for matched shaders.

Exact R-row conversion is deliberately NOT done: the two hosts' pipeline
state structs differ (544 vs 432 bytes), so byte-mapping state would risk
silent wrong pipelines. Pair-level coverage kills the submit-thread stalls;
exact-pipeline warm stays a follow-up.

Writes a full derived profile dir (copy of the input profile + replaced
tables + SWX_DERIVED.txt provenance) for DM_GAMES_DIR overlay use.

The copy deliberately EXCLUDES patches.py: patch_nso.py loads patches.py
from every profile (repo + DM_GAMES_DIR) and aborts on a duplicate build ID.
Code cuts keep coming from the stock profile (keyed by build ID, not by
profile dir); the overlay only extends data tables.
"""
import argparse
import json
import os
import shutil
import sys

from shader_convert import convert_list, unpack_key, short_name, MNG_VS, MNG_FS

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

DEFAULT_MNG_LIST = os.path.join(
    ROOT, "refs", "mac-nativegames", "games", "botw", "assets", "shader_list.tsv")
DEFAULT_PROFILE_DIR = os.path.join(
    ROOT, "core", "nxnative", "dm", "games", "01007EF00011E000")


def read_profile(profile_dir):
    """Return (map_keys, pair_set, manifest_s_keys, header_lines)."""
    with open(os.path.join(profile_dir, "shader_map.tsv"), encoding="utf-8") as f:
        text = f.read()
    keys = set()
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        for part in line.split("\t"):
            if len(part) == 16 and all(c in "0123456789abcdef" for c in part):
                keys.add(part)
                break
    pairs = set()
    for name in ("shader_pairs.tsv", "pso_pairs.tsv"):
        path = os.path.join(profile_dir, name)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                p = line.split("\t")
                if len(p) >= 2:
                    pairs.add((p[0], p[1]))
    manifest_s = set()
    mpath = os.path.join(profile_dir, "pso_manifest.tsv")
    if os.path.isfile(mpath):
        with open(mpath, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("S\t"):
                    p = line.split("\t")
                    if len(p) >= 2:
                        manifest_s.add(p[1])
    return keys, pairs, manifest_s


def read_mng_list(path):
    """Return (s_rows, pair_list) with 16-hex keys.

    s_rows: [(key16_b, stage)]. pair_list: [(vs16, fs16)] from R/B/T rows
    whose variants parse (variant = <key64>-<suffix>_b). R/B/T keys come in
    two wire formats (packed vs:fs:state or the raw hex of the 544-byte
    key); unpack_key handles both, the names are read from the blob.
    """
    s_rows, pairs = [], []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            p = line.split("\t")
            kind = p[0]
            if kind == "S" and len(p) > 2 and len(p[1]) >= 16:
                s_rows.append((p[1][:16] + "_b", p[2]))
            elif kind in ("R", "B", "T") and len(p) > 1:
                blob = unpack_key(p[1])
                if blob is not None:
                    vs = short_name(blob[MNG_VS:MNG_VS + 80].split(b"\0")[0].decode("ascii", errors="replace"))
                    fs_raw = blob[MNG_FS:MNG_FS + 80].split(b"\0")[0].decode("ascii", errors="replace")
                    fs = short_name(fs_raw) if fs_raw else None
                    if vs and fs:
                        pairs.append((vs[:-2], fs[:-2]))
                    continue
                fields = p[1].split(":")
                if len(fields) >= 2:
                    vs, fs = fields[0][:16], fields[1][:16]
                    ok = lambda s: len(s) == 16 and all(
                        c in "0123456789abcdef" for c in s)
                    if ok(vs) and ok(fs):
                        pairs.append((vs, fs))
    return s_rows, pairs


def write_pairs_file(profile_dir, mng_list, out_path):
    """Community pairs as a standalone prebuild table (v1 delivery).

    pso_prebuild concatenates shader_pairs.tsv with DM_PSO_PAIRS (nvn_mtl.m),
    so this file extends prebuild coverage WITHOUT modifying the profile's
    validated tables (the split aborts on any profile-table drift). Content:
    the profile's own pso_pairs.tsv rows plus new community pairs, deduped.
    """
    keys, pairs, _ = read_profile(profile_dir)
    s_seen, mng_pairs = read_mng_list(mng_list)
    new_pairs = sorted({(a, b) for a, b in mng_pairs if a in keys and b in keys} - pairs)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    existing = []
    ppath = os.path.join(profile_dir, "pso_pairs.tsv")
    if os.path.isfile(ppath):
        with open(ppath, encoding="utf-8") as f:
            existing = [ln.rstrip("\n") for ln in f if ln.strip() and not ln.startswith("#")]
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("# SWX community pairs: profile pso_pairs.tsv + mac-nativegames list (identifiers only)\n")
        for ln in existing:
            f.write(ln + "\n")
        for a, b in new_pairs:
            f.write(f"{a}\t{b}\n")
    return {"profile_pairs": len(pairs), "added": len(new_pairs),
            "total": len(existing) + len(new_pairs)}


def build_derived(profile_dir, mng_list, out_dir):
    """Derived profile: full copy minus patches.py, manifest extended.

    Excludes patches.py (patch_nso.py aborts on duplicate build IDs across
    repo + DM_GAMES_DIR overlays) and leaves the split-validated tables
    (shader_map/facts/pairs) byte-identical, so the split check passes.
    Only pso_manifest.tsv grows: converted community R rows and S shader
    rows the profile lacks. Returns stats.
    """
    keys, pairs, _ = read_profile(profile_dir)
    converted, conv_stats = convert_list(mng_list, keys)

    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    shutil.copytree(profile_dir, out_dir,
                    ignore=shutil.ignore_patterns("__pycache__", ".DS_Store", "patches.py"))

    have, have_s = set(), set()
    mpath = os.path.join(out_dir, "pso_manifest.tsv")
    if os.path.isfile(mpath):
        with open(mpath, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("R\t"):
                    have.add(line.split("\t", 1)[1])
                elif line.startswith("S\t"):
                    have_s.add(line.split("\t", 2)[1])
    new_rows = [h for h in converted if h not in have]
    with open(mpath, "a", encoding="utf-8") as f:
        f.write("# SWX: converted community pipelines below (mac-nativegames R/B/T rows)\n")
        for h in new_rows:
            f.write(f"R\t{h}\n")
    # S rows: community shaders the seed lacks (key in the map, numeric stage)
    s_rows, _ = read_mng_list(mng_list)
    s_add, seen_s = [], set()
    for key_b, stage in s_rows:
        if key_b in have_s or key_b in seen_s or key_b[:-2] not in keys:
            continue
        if stage not in ("0", "1", "2", "3", "4", "5"):
            continue
        seen_s.add(key_b)
        s_add.append((key_b, stage))
    if s_add:
        with open(mpath, "a", encoding="utf-8") as f:
            f.write("# SWX: community shaders the seed lacks (mac-nativegames S rows)\n")
            for key_b, stage in s_add:
                f.write(f"S\t{key_b}\t{stage}\n")

    stats = {
        "profile_keys": len(keys),
        "converted_rows": len(converted),
        "manifest_rows_added": len(new_rows),
        "manifest_s_added": len(s_add),
        "skipped": {k: v for k, v in conv_stats.items()
                    if k not in ("rows_seen", "converted")},
    }
    with open(os.path.join(out_dir, "SWX_DERIVED.txt"), "w", encoding="utf-8") as f:
        f.write("# swx derived profile: community R rows over stock tables (identifiers only)\n")
        f.write(f"source_list\t{mng_list}\n")
        f.write(f"base_profile\t{profile_dir}\n")
        for k, v in stats.items():
            f.write(f"{k}\t{v}\n")
    return stats


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mng-list", default=DEFAULT_MNG_LIST)
    ap.add_argument("--profile-dir", default=DEFAULT_PROFILE_DIR)
    ap.add_argument("--out", required=True,
                    help="output derived profile dir (created fresh)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    for label, path in (("mng list", args.mng_list), ("profile", args.profile_dir)):
        if not os.path.exists(path):
            print(f"error: {label} not found: {path}", file=sys.stderr)
            return 2
    stats = build_derived(args.profile_dir, args.mng_list, args.out)
    if args.json:
        print(json.dumps(stats, indent=2))
    else:
        for k, v in stats.items():
            print(f"{k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
