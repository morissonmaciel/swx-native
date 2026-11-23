#!/usr/bin/env python3
"""Convert mac-nativegames pipeline rows (R/B/T) to nxnative R rows.

Reads the shared shader_list.tsv, unpacks each pipeline key, translates the
544-byte MNG pso_key state into nxnative's 512-byte layout, and emits plain
nxnative `R <hex>` rows. Only safe rows convert; anything else is skipped
with a counted reason (never forced).

Layout basis (verified):
- MNG: vs[80] fs[80] stages[4] fz_desc[34] pad[2] k.vs[8] k.fs[8] cfmt[32]
  dfmt[4] sfmt[4] samples[4] blend_en[4] chmask[4] a2c[4] blend[64] attr[64]
  stream[128] lmap[8] fsfn[8] = 544. Cross-checked against their SKEY_*
  offsets (MERGED=162, CFMT=216, BLEND_EN=260, CHMASK=264, BLEND=272).
- nxnative: vs[24] fs[24] stages[4] fz_desc[34] pad[2] k.vs[8] k.fs[8]
  cfmt[32] dfmt[4] sfmt[4] samples[4] blend_en[4] chmask[4] a2c[4] blend[64]
  attr[64] stream[128] lmap[8] fsfn[8] atest[4] logic[4] attr_hi[64]
  border[4] spare[4] = 512. Cross-checked against live native rows
  (ASCII names, stages, zero pointers) and the 512-80=432 old length.

Field rules (see docs/shader_ingest_design.md piece 3):
- vs/fs names: `<key64>[-<suffix>]_b` -> `<key16>_b`, NUL-padded to 24.
- stages/merged/fz/fz_desc: identical structs, copied verbatim.
- k.vs/k.fs/k.fsfn: must be zero in source (recorded rows); else skip.
- cfmt/dfmt/sfmt/samples/blend_en/chmask/lmap: copied verbatim (nxnative
  normalizes on load via pso_key_norm).
- a2c: only bit 0 (alpha-to-coverage, boolean on both sides) may be set;
  any layered/MetalFX/border/HDR/host bit -> skip (other pipeline family).
- blend u64s: low 37 bits kept, upper (advanced-blend) bits must be zero.
- attr/stream: identical layouts, copied verbatim.
- atest/logic/attr_hi/border/spare: zeroed (safe-miss direction: a draw
  needing them builds its own at runtime instead of using a wrong one).
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

DEFAULT_MNG_LIST = os.path.join(
    ROOT, "refs", "mac-nativegames", "games", "botw", "assets", "shader_list.tsv")
DEFAULT_PROFILE_MAP = os.path.join(
    ROOT, "core", "nxnative", "dm", "games", "01007EF00011E000", "shader_map.tsv")

# MNG 544-byte layout, cross-checked against shader_list.py SKEY_* constants.
MNG_VS, MNG_FS = 0, 80
MNG_STAGES = 160          # vs_stage, fs_stage, merged, fz (4 bytes)
MNG_FZ = 164              # kind, fbmask, fx[32] (34 bytes)
MNG_KVS = 200             # k.vs, k.fs pointers (2x8, must be zero)
MNG_CFMT = 216            # == SKEY_CFMT
MNG_BLEND_EN = 260        # == SKEY_BLEND_EN
MNG_CHMASK = 264          # == SKEY_CHMASK
MNG_A2C = 268
MNG_BLEND = 272           # == SKEY_BLEND, 8x u64
MNG_ATTR = 336            # 16x u32
MNG_STREAM = 400          # 16x {stride, divisor} u32
MNG_LMAP = 528            # 8 bytes
MNG_FSFN = 536            # pointer (must be zero)
MNG_SIZE = 544

# nxnative 512-byte layout (C natural alignment; verified against live rows).
NX_VS, NX_FS = 0, 24
NX_STAGES = 48
NX_FZ = 52
NX_KVS = 88
NX_CFMT = 104
NX_BLEND_EN = 148
NX_CHMASK = 152
NX_A2C = 156
NX_BLEND = 160
NX_ATTR = 224
NX_STREAM = 288
NX_LMAP = 416
NX_FSFN = 424
NX_ATEST = 432
NX_SIZE = 512

PACKED = re.compile(r"^([0-9a-f_\-]{0,80}):([0-9a-f_\-]{0,80}):((?:[0-9a-f]{2}|z[0-9a-f]+\.)*)$")


def unpack_key(text):
    """MNG pipeline key -> 544-byte blob, or None. Two wire formats:
    packed `vs:fs:zhex.` (mirror MNG unpack_key) and the raw hex of the
    544-byte key itself (shader_list.tsv v1 R rows carry this one)."""
    m = PACKED.match(text)
    if m:
        vs, fs, rest = m.groups()
        try:
            key = (vs.encode().hex().ljust(2 * 80, "0") + fs.encode().hex().ljust(2 * 80, "0")
                   + re.sub(r"z([0-9a-f]+)\.", lambda z: "00" * int(z.group(1), 16), rest))
        except (ValueError, UnicodeDecodeError):
            return None
        if len(key) != 2 * MNG_SIZE:
            return None
        return bytes.fromhex(key)
    if len(text) == 2 * MNG_SIZE and all(c in "0123456789abcdef" for c in text):
        return bytes.fromhex(text)
    return None


def short_name(variant):
    """`<key64>[-<suffix>]_b` -> `<key16>_b`, or None."""
    if not variant.endswith("_b"):
        return None
    core = variant[:-2].split("-")[0]
    if len(core) < 16 or any(c not in "0123456789abcdef" for c in core[:16]):
        return None
    return core[:16] + "_b"


def convert_row(kind, packed, profile_keys, stats):
    """Convert one MNG pipeline row -> nxnative R hex, or (None, reason)."""
    blob = unpack_key(packed)
    if blob is None:
        return None, "unparsable"
    # names live in the blob's vs[80]/fs[80] ASCII fields in both wire formats
    vs_name = short_name(blob[MNG_VS:MNG_VS + 80].split(b"\0")[0].decode("ascii", errors="replace"))
    fs_raw = blob[MNG_FS:MNG_FS + 80].split(b"\0")[0].decode("ascii", errors="replace")
    fs_name = short_name(fs_raw) if fs_raw else None
    if not vs_name:
        return None, "bad-vs-name"
    if vs_name[:-2] not in profile_keys or (fs_name and fs_name[:-2] not in profile_keys):
        return None, "shader-not-in-map"
    if blob[MNG_KVS:MNG_KVS + 16] != bytes(16) or blob[MNG_FSFN:MNG_FSFN + 8] != bytes(8):
        return None, "nonzero-pointers"
    a2c = int.from_bytes(blob[MNG_A2C:MNG_A2C + 4], "little")
    if a2c & ~1:
        stats["specialized-a2c"] = stats.get("specialized-a2c", 0) + 1
        return None, "specialized-a2c"
    out = bytearray(NX_SIZE)
    out[NX_VS:NX_VS + 24] = vs_name.encode()[:24].ljust(24, b"\0")
    if fs_name:
        out[NX_FS:NX_FS + 24] = fs_name.encode()[:24].ljust(24, b"\0")
    out[NX_STAGES:NX_STAGES + 4] = blob[MNG_STAGES:MNG_STAGES + 4]
    # fz stage sanity: 0=vertex 1=pixel 4=mesh 5=compute-ish (nxnative warm kinds)
    if out[NX_STAGES] not in (0, 1, 4) or (fs_name and out[NX_STAGES + 1] not in (1,)):
        return None, "bad-stage"
    out[NX_FZ:NX_FZ + 34] = blob[MNG_FZ:MNG_FZ + 34]
    for table, src, dst, size in (("cfmt", MNG_CFMT, NX_CFMT, 32),
                                  ("dstate", MNG_BLEND_EN - 8, NX_BLEND_EN - 8, 12),
                                  ("blend", MNG_BLEND, NX_BLEND, 64),
                                  ("attr", MNG_ATTR, NX_ATTR, 64),
                                  ("stream", MNG_STREAM, NX_STREAM, 128),
                                  ("lmap", MNG_LMAP, NX_LMAP, 8)):
        _ = table
        out[dst:dst + size] = blob[src:src + size]
    for i in range(8):
        v = int.from_bytes(out[NX_BLEND + 8 * i:NX_BLEND + 8 * i + 8], "little")
        if v >> 37:
            return None, "adv-blend-bits"
    # a2c already filtered to bit 0; atest/logic/attr_hi/border stay zeroed.
    out[NX_A2C:NX_A2C + 4] = blob[MNG_A2C:MNG_A2C + 4]
    return out.hex(), None


def convert_list(mng_list, profile_keys):
    """Convert all R/B/T rows. Returns (rows, stats) with deduped R hex rows."""
    stats, converted, seen = {"rows_seen": 0}, [], set()
    with open(mng_list, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            p = line.split("\t")
            if p[0] not in ("R", "B", "T") or len(p) < 2:
                continue
            stats["rows_seen"] += 1
            hexrow, reason = convert_row(p[0], p[1], profile_keys, stats)
            if hexrow is None:
                stats[reason] = stats.get(reason, 0) + 1
                continue
            if hexrow in seen:
                stats["duplicate"] = stats.get("duplicate", 0) + 1
                continue
            seen.add(hexrow)
            converted.append(hexrow)
    stats["converted"] = len(converted)
    return converted, stats


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mng-list", default=DEFAULT_MNG_LIST)
    ap.add_argument("--profile-map", default=DEFAULT_PROFILE_MAP)
    ap.add_argument("--out", help="write converted R rows here (default: stdout stats only)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    keys = set()
    with open(args.profile_map, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            for part in line.split("\t"):
                if len(part) == 16 and all(c in "0123456789abcdef" for c in part):
                    keys.add(part)
                    break

    stats, converted, seen = {"rows_seen": 0}, [], set()
    converted, stats = convert_list(args.mng_list, keys)
    stats["profile_keys"] = len(keys)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write("# SWX converted pipelines (mac-nativegames R/B/T rows -> nxnative R rows)\n")
            for h in converted:
                f.write(f"R\t{h}\n")
        stats["out"] = args.out
    if args.json:
        print(json.dumps(stats, indent=2))
    else:
        for k, v in stats.items():
            print(f"{k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
