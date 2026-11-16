#!/usr/bin/env python3
"""Deterministic game-log report for texture-upload A/B testing.

Parses an nxnative game log (default: the BOTW macOS log) and prints one
JSON/text summary: session span, longframe classification (pipeline builds vs
first-upload bursts vs GPU-bound/other), totals (new pipelines, first-upload
count and MB, hybrid draws, draws that waited), frame-step transitions,
prewarm/prebuild lines, keep-alive presence, and [RED] lines.

Usage: log_report.py [log path] [--json]
"""
import json
import os
import re
import sys

DEFAULT_LOG = os.path.expanduser("~/Library/Logs/nxnative/01007EF00011E000.log")


def classify(line):
    m_np = re.search(r"new pipelines (\d+)", line)
    m_up = re.search(r"texture uploads (\d+) first", line)
    m_mb = re.search(r"([\d.]+) MB", line)
    m_ms = re.search(r"whole frame ([\d.]+) ms", line)
    m_hy = re.search(r"draws with a hybrid pipeline (\d+)", line)
    m_gpu = re.search(r"GPU ([\d.]+) ms", line)
    m_fr = re.search(r"frame (\d+)", line)
    m_ba = re.search(r"in (\d+) batches \((\d+) split\)", line)
    np_ = int(m_np.group(1)) if m_np else 0
    up = int(m_up.group(1)) if m_up else 0
    return {
        "frame": int(m_fr.group(1)) if m_fr else -1,
        "ms": float(m_ms.group(1)) if m_ms else 0.0,
        "new_pipelines": np_,
        "first_uploads": up,
        "upload_mb": float(m_mb.group(1)) if (m_mb and up) else 0.0,
        "hybrid": int(m_hy.group(1)) if m_hy else 0,
        "gpu_ms": float(m_gpu.group(1)) if m_gpu else 0.0,
        "batches": int(m_ba.group(1)) if m_ba else 0,
        "split_batches": int(m_ba.group(2)) if m_ba else 0,
    }


def report(path):
    rep = {"log": path, "longframes": [], "steps": [], "red": [],
           "keepalive": False, "synclag": False, "prewarm": {}, "frames": 0}
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if "[swx-keepalive]" in line:
                rep["keepalive"] = True
            if "DM_NVN_SYNC_LAG=1: newest-fence" in line:
                rep["synclag"] = True
            if "[RED]" in line and "swx" not in line and "keepalive" not in line:
                rep["red"].append(line.strip()[:200])
            m = re.search(r"frame step: hold (\d) → (\d)", line)
            if m:
                rep["steps"].append(f"{m.group(1)}->{m.group(2)}")
            m = re.search(r"pipeline prewarm done:.*?render pipelines (\d+)", line)
            if m:
                rep["prewarm"]["render_pipelines"] = int(m.group(1))
            m = re.search(r"pipeline prebuild done: (\d+) shader pairs", line)
            if m:
                rep["prewarm"]["prebuild_pairs"] = int(m.group(1))
            m = re.search(r"on the submit thread pipelines ([\d.]+) ms", line)
            if m:
                rep.setdefault("submit_pipeline_ms", []).append(float(m.group(1)))
            if "[longframe]" in line:
                rep["longframes"].append(classify(line))
            m = re.search(r"frame (\d+) \(", line)
            if m:
                rep["frames"] = max(rep["frames"], int(m.group(1)))
    lfs = rep.pop("longframes")
    by_cause = {"pipelines": [], "uploads": [], "gpu_other": []}
    for lf in lfs:
        if lf["new_pipelines"] > 0:
            by_cause["pipelines"].append(lf)
        elif lf["first_uploads"] > 0:
            by_cause["uploads"].append(lf)
        else:
            by_cause["gpu_other"].append(lf)
    rep["longframe_count"] = len(lfs)
    rep["worst_ms"] = max([l["ms"] for l in lfs], default=0.0)
    for k, v in by_cause.items():
        rep[f"lf_{k}"] = len(v)
        rep[f"lf_{k}_worst_ms"] = max([x["ms"] for x in v], default=0.0)
    rep["new_pipelines_total"] = sum(l["new_pipelines"] for l in lfs)
    rep["first_uploads_total"] = sum(l["first_uploads"] for l in lfs)
    rep["first_uploads_mb"] = round(sum(l["upload_mb"] for l in lfs), 1)
    rep["hybrid_draws_max"] = max([l["hybrid"] for l in lfs], default=0)
    rep["batches_total"] = sum(l["batches"] for l in lfs)
    rep["split_batches_total"] = sum(l["split_batches"] for l in lfs)
    up_lfs = [l for l in lfs if l["first_uploads"] > 0 and l["batches"] > 0]
    rep["upload_frames_with_batches"] = len(up_lfs)
    rep["upload_batch_rate"] = round(sum(l["first_uploads"] for l in up_lfs) / max(sum(l["batches"] for l in up_lfs), 1), 1) if up_lfs else 0.0
    return rep


def main(argv=None):
    args = [a for a in (argv or sys.argv[1:]) if not a.startswith("-")]
    as_json = "--json" in (argv or sys.argv[1:])
    path = args[0] if args else DEFAULT_LOG
    if not os.path.isfile(path):
        print(f"error: log not found: {path}", file=sys.stderr)
        return 2
    rep = report(path)
    if as_json:
        print(json.dumps(rep, indent=2))
        return 0
    print(f"log: {rep['log']}")
    print(f"frames seen: {rep['frames']}, keep-alive: {rep['keepalive']}, sync-lag: {rep.get('synclag', False)}, [RED]: {len(rep['red'])}")
    print(f"prewarm: {rep.get('prewarm', {})}")
    print(f"longframes: {rep['longframe_count']} (worst {rep['worst_ms']:.1f} ms)")
    print(f"  with new pipelines : {rep['lf_pipelines']} (worst {rep['lf_pipelines_worst_ms']:.1f} ms)")
    print(f"  upload-only          : {rep['lf_uploads']} (worst {rep['lf_uploads_worst_ms']:.1f} ms)")
    print(f"  gpu-bound/other      : {rep['lf_gpu_other']} (worst {rep['lf_gpu_other_worst_ms']:.1f} ms)")
    print(f"new pipelines total: {rep['new_pipelines_total']}, "
          f"first uploads: {rep['first_uploads_total']} ({rep['first_uploads_mb']} MB), "
          f"max hybrid draws in a frame: {rep['hybrid_draws_max']}")
    print(f"upload batches: {rep['batches_total']} total ({rep['split_batches_total']} by count-split) "
          f"over {rep['upload_frames_with_batches']} frames with batch data, "
          f"{rep['upload_batch_rate']} uploads/batch")
    print(f"frame-step transitions: {', '.join(rep['steps']) if rep['steps'] else 'none'}")
    for r in rep["red"][:10]:
        print(f"RED: {r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
