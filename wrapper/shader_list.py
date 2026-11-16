#!/usr/bin/env python3
"""Shared shader list coverage (slices 1+2 of shader_list_reading).

Read-only diagnostic: parses mac-nativegames' botw shader_list.tsv and joins
its keys against the nxnative profile's shader_map.tsv, reporting how much of
the community list our build would already prebuild.

No game files are read or changed; identifiers only, both sides.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

DEFAULT_MNG_LIST = os.path.join(
    ROOT, "refs", "mac-nativegames", "games", "botw", "assets", "shader_list.tsv")
DEFAULT_PROFILE_MAP = os.path.join(
    ROOT, "core", "nxnative", "dm", "games", "01007EF00011E000", "shader_map.tsv")

# Row kinds in the shared list; V/A/L/S/C name shader variants, R/B/T pipelines.
SHADER_ROWS = ("V", "A", "L", "S", "C")
PIPELINE_ROWS = ("R", "B", "T")


def parse_mng_list(path):
    """Parse the shared list.

    Returns (shader_keys, pipeline_rows) where shader_keys maps each join key
    to its row kind: V/S rows join by key[:16], A rows (12-hex bind-state
    suffixes) join by prefix. Pipeline rows (R/B/T) are only counted here;
    they map onto pair tables in slice 4.
    """
    shaders, pipelines = {}, 0
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            kind = parts[0] if parts else ""
            if kind in ("V", "S") and len(parts) > 1 and len(parts[1]) >= 16:
                shaders.setdefault(parts[1][:16], kind)
            elif kind == "A" and len(parts) > 1 and len(parts[1]) == 12:
                shaders.setdefault(parts[1], kind)
            elif kind in PIPELINE_ROWS:
                pipelines += 1
    return shaders, pipelines


def parse_profile_map(path):
    """Return the set of dm_ctl_sha16 keys (16 hex chars) in shader_map.tsv."""
    keys = set()
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            for part in parts:
                if len(part) == 16 and all(c in "0123456789abcdef" for c in part):
                    keys.add(part)
                    break
    return keys


def coverage(mng_shaders, profile_keys):
    # V/S rows join by key[:16]. A rows (bind-state suffixes, the first 12 hex
    # of the answers' SHA-256) are NOT key prefixes: a variant is picked by
    # matching deps through the seed's .variants index, which is slice 4 work.
    # They are reported separately, never as missed coverage.
    by_kind = {}
    missing = []
    pending = 0
    for join_key, kind in mng_shaders.items():
        if kind == "A":
            pending += 1
            by_kind.setdefault(kind, {"total": 0, "matched": 0, "pending": 0})
            by_kind[kind]["total"] += 1
            by_kind[kind]["pending"] += 1
            continue
        hit = join_key in profile_keys
        by_kind.setdefault(kind, {"total": 0, "matched": 0, "pending": 0})
        by_kind[kind]["total"] += 1
        if hit:
            by_kind[kind]["matched"] += 1
        else:
            missing.append(join_key)
    joinable = sum(b["total"] for k, b in by_kind.items() if k != "A")
    matched = sum(b["matched"] for b in by_kind.values())
    return {"total": joinable, "matched": matched,
            "missing": len(missing), "by_kind": by_kind,
            "pending_variant_resolution": pending,
            "missing_keys": sorted(missing)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mng-list", default=DEFAULT_MNG_LIST,
                    help="mac-nativegames botw shader_list.tsv")
    ap.add_argument("--profile-map", default=DEFAULT_PROFILE_MAP,
                    help="nxnative profile shader_map.tsv")
    ap.add_argument("--json", action="store_true",
                    help="print the coverage as JSON (backend use)")
    args = ap.parse_args(argv)
    for label, path in (("mng list", args.mng_list), ("profile map", args.profile_map)):
        if not os.path.isfile(path):
            print(f"error: {label} not found: {path}", file=sys.stderr)
            return 2
    shaders, pipelines = parse_mng_list(args.mng_list)
    profile_keys = parse_profile_map(args.profile_map)
    rep = coverage(shaders, profile_keys)
    rep["pipeline_rows"] = pipelines
    rep["profile_keys"] = len(profile_keys)
    if args.json:
        print(json.dumps({k: v for k, v in rep.items() if k != "missing_keys"}, indent=2))
        return 0
    total = rep["total"] or 1
    print(f"shared shader rows (joinable V/S): {rep['total']}")
    print(f"matched in profile               : {rep['matched']} ({100.0 * rep['matched'] / total:.1f}%)")
    print(f"missing from profile             : {rep['missing']}")
    print(f"A rows (bind states, slice 4)    : {rep['pending_variant_resolution']} (not joinable here)")
    print(f"shared pipeline rows             : {pipelines} (mapped in slice 4)")
    print(f"profile map keys                 : {rep['profile_keys']}")
    print("by kind (total/matched/pending):")
    for kind in sorted(rep["by_kind"]):
        b = rep["by_kind"][kind]
        print(f"  {kind}: {b['total']}/{b['matched']}/{b.get('pending', 0)}")
    print("note: V/S matches are [:16] key joins; missing V keys in the")
    print("      bound_control subset need local re-derivation (slice 3).")
    print("      A rows resolve via the seed's .variants index (slice 4).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
