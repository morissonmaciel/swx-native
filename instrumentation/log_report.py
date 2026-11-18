#!/usr/bin/env python3
"""Deterministic game-log report for texture-upload A/B testing.

Parses an nxnative game log (default: the BOTW macOS log) and prints one
JSON/text summary: session span, longframe classification (pipeline builds vs
first-upload bursts vs GPU-bound/other), totals (new pipelines, first-upload
count and MB, hybrid draws, draws that waited), frame-step transitions,
prewarm/prebuild lines, keep-alive presence, and [RED] lines.

Usage: log_report.py [log path] [--tsv timing.tsv] [--json]
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
    m_submit = re.search(r"submit\+present ([\d.]+) ms", line)
    m_hy = re.search(r"draws with a hybrid pipeline (\d+)", line)
    m_gpu = re.search(r"GPU ([\d.]+) ms", line)
    m_fr = re.search(r"frame (\d+)", line)
    m_ba = re.search(r"in (\d+) batches \((\d+) split\)", line)
    # `+ path ...` may be appended inside the upload bracket, after oldq.
    m_tm = re.search(r"\[upload alloc ([\d.]+) ms \((\d+) bufs\) \+ rescommit ([\d.]+) ms \+ commit ([\d.]+) ms \+ drain ([\d.]+) ms \((\d+)\) \+ slotwait ([\d.]+) ms \+ oldq ([\d.]+) ms(?: \+ path \d+/\d+/\d+(?: \([\d.]+/[\d.]+/[\d.]+ ms\))?)?\]", line)
    m_pa = re.search(r"\+ path (\d+)/(\d+)/(\d+)(?: \(([\d.]+)/([\d.]+)/([\d.]+) ms\))?", line)
    np_ = int(m_np.group(1)) if m_np else 0
    up = int(m_up.group(1)) if m_up else 0
    return {
        "frame": int(m_fr.group(1)) if m_fr else -1,
        "ms": float(m_ms.group(1)) if m_ms else 0.0,
        "submit_ms": float(m_submit.group(1)) if m_submit else 0.0,
        "new_pipelines": np_,
        "first_uploads": up,
        "upload_mb": float(m_mb.group(1)) if (m_mb and up) else 0.0,
        "hybrid": int(m_hy.group(1)) if m_hy else 0,
        "gpu_ms": float(m_gpu.group(1)) if m_gpu else 0.0,
        "batches": int(m_ba.group(1)) if m_ba else 0,
        "split_batches": int(m_ba.group(2)) if m_ba else 0,
        "up_alloc_ms": float(m_tm.group(1)) if m_tm else None,
        "up_alloc_n": int(m_tm.group(2)) if m_tm else None,
        "up_rescommit_ms": float(m_tm.group(3)) if m_tm else None,
        "up_cbend_ms": float(m_tm.group(4)) if m_tm else None,
        "up_drain_ms": float(m_tm.group(5)) if m_tm else None,
        "up_drain_n": int(m_tm.group(6)) if m_tm else None,
        "up_slotwait_ms": float(m_tm.group(7)) if m_tm else None,
        "up_oldq_ms": float(m_tm.group(8)) if m_tm else None,
        "up_direct": int(m_pa.group(1)) if m_pa else None,
        "up_enc": int(m_pa.group(2)) if m_pa else None,
        "up_cpu": int(m_pa.group(3)) if m_pa else None,
        "up_pdi_ms": float(m_pa.group(4)) if m_pa and m_pa.group(4) else None,
        "up_pen_ms": float(m_pa.group(5)) if m_pa and m_pa.group(5) else None,
        "up_pcp_ms": float(m_pa.group(6)) if m_pa and m_pa.group(6) else None,
    }


def _parse_vfr_window(line):
    """Janela VFR estruturada; tolera linhas legadas sem todos os campos."""
    raw = line.strip()
    m = re.search(
        r"VFR up to present (?P<present>\d+): (?P<policy>\S+) (?P<clock>\S+) (?P<hz>\d+) Hz, "
        r"(?P<clock_source>\S+) clock, display (?P<display_hz>\d+) Hz, "
        r"average present (?P<avg_present_ms>[\d.]+) ms, applied (?P<applied_ms>[\d.]+) ms, "
        r"sampled time (?P<sampled_s>[\d.]+) s, "
        r"(?:window game (?P<game_delta_s>[\d.]+) s / wall (?P<wall_delta_s>[\d.]+) s, )?"
        r"ratio (?P<ratio>[\d.]+) \((?P<steps>\d+) VFR steps\), "
        r"ring waits (?P<ring_wait_ms>[\d.]+) / ticket waits (?P<ticket_wait_ms>[\d.]+) ms, "
        r"queued (?P<queued>\d+), (?P<dropped>\d+) of last (?P<window>\d+) dropped, "
        r"guest interval (?P<guest_interval>\d+)", raw)
    if not m:
        return {"raw": raw}
    fields = m.groupdict()
    for key in ("present", "hz", "display_hz", "steps", "queued", "dropped", "window", "guest_interval"):
        fields[key] = int(fields[key])
    for key in ("avg_present_ms", "applied_ms", "sampled_s", "ratio", "ring_wait_ms", "ticket_wait_ms"):
        fields[key] = float(fields[key])
    game_s, wall_s = fields.pop("game_delta_s"), fields.pop("wall_delta_s")
    fields["game_delta_s"] = float(game_s) if game_s is not None else None
    fields["wall_delta_s"] = float(wall_s) if wall_s is not None else None
    # The initial window has no prior endpoint; a logged 0/0 is unavailable.
    fields["window_delta_available"] = game_s is not None and wall_s is not None
    if fields["game_delta_s"] == 0 and fields["wall_delta_s"] == 0:
        fields["game_delta_s"] = fields["wall_delta_s"] = None
        fields["window_delta_available"] = False
    fields["raw"] = raw
    return fields


def _parse_present_queue_window(line):
    """Parse the worker/ready-FIFO gate line without inventing old data."""
    raw = line.strip()
    m = re.search(r"VFR present queue up to tick (\d+):", raw, re.I)
    if not m:
        return None
    out = {
        "raw": raw, "tick": int(m.group(1)), "ready_depth": None,
        "drop_oldest": None, "credit": None, "queue_ms_p95": None,
        "queue_pops": None, "promote_ms_p95": None, "promote_count": None,
        "window": None, "held": None,
        "empty": None,
    }
    tail = raw[m.end():]
    # Accept both `name value` and `name=value`; p95 labels are emitted as
    # `queue_ms p95 X` / `promote_ms p95 X` in the current contract.
    patterns = {
        "ready_depth": r"ready[ _-]depth(?:\s+max)?\s*(?:=|:)?\s*(\d+)",
        "drop_oldest": r"drop[ _-]oldest\s*(?:=|:)?\s*(\d+)",
        "credit": r"credit\s*(?:=|:)?\s*(\d+)",
        "window": r"window\s*(?:=|:)?\s*(\d+)",
        "held": r"held\s*(?:=|:)?\s*(\d+)",
        "empty": r"empty\s*(?:=|:)?\s*(\d+)",
    }
    for key, pattern in patterns.items():
        mm = re.search(pattern, tail, re.I)
        if mm:
            val = float(mm.group(1))
            out[key] = int(val) if val.is_integer() else val
    for value_key, count_key, name, unit in (
            ("queue_ms_p95", "queue_pops", r"queue[ _-]ms", "pops"),
            ("promote_ms_p95", "promote_count", r"promote[ _-]ms", "promotions")):
        mm = re.search(
            rf"(?:{name})\s*(?:p95\s*)?(?:=|:)?\s*([\d.]+|n/a|null)\s*ms"
            rf"(?:\s+from\s+(\d+)\s+{unit})?", tail, re.I)
        if mm:
            value, count = mm.groups()
            if count is not None:
                out[count_key] = int(count)
            if value.lower() not in ("n/a", "null") and count != "0":
                out[value_key] = float(value)
    return out


def _parse_texture_pool_line(line):
    """Parse cumulative low-frequency staging-pool counters when present."""
    m = re.search(
        r"texture staging pool(?: up to frame\s+(\d+))?:\s*reuse\s+(\d+)\s*/\s*cold\s+(\d+)\s*/\s*miss\s+(\d+);\s*"
        r"reserved\s+(\d+)\s+bytes;\s*resident\s+(\d+)\s+bytes", line, re.I)
    if not m:
        return None
    return {
        "frame": int(m.group(1)) if m.group(1) else None,
        "reuse": int(m.group(2)), "cold": int(m.group(3)),
        "miss": int(m.group(4)), "reserved_bytes": int(m.group(5)),
        "resident_bytes": int(m.group(6)),
    }


_TEXTURE_BURST_INT_FIELDS = {
    "schema", "batch", "frame", "submission", "textures", "mips",
    "layers", "dispatches", "blits", "bytes", "slot_waits", "dropped",
}
# These fields are intervals/durations from the host schema. complete_ns is
# deliberately excluded: it is an absolute monotonic timestamp, not a cost.
_TEXTURE_BURST_DURATION_FIELDS = {
    "dst_wall_ns", "protect_wall_ns", "origin_wall_ns", "deswizzle_wall_ns",
    "flush_wall_ns", "commit_wall_ns", "wait_wall_ns", "queue_ns", "gpu_ns",
}


def _parse_texture_burst_line(line):
    """Parse one structured texture-burst event without inventing joins.

    Unknown keys remain strings so schema extensions survive collection.
    Missing IDs and measurements remain absent; in particular, nearby log
    lines are never used to fill frame/submission/batch fields.
    """
    marker = "[texture-burst] "
    start = line.find(marker)
    if start < 0:
        return None
    fields = {}
    for token in line[start + len(marker):].strip().split():
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        if not key or not value:
            continue
        if key in _TEXTURE_BURST_INT_FIELDS:
            try:
                fields[key] = int(value, 10)
            except ValueError:
                fields[key] = value
        elif key.endswith("_ns"):
            try:
                fields[key] = int(value, 10)
            except ValueError:
                fields[key] = value
        else:
            fields[key] = value
    if fields.get("schema") != 1 or not isinstance(fields.get("event"), str):
        return None
    fields["raw"] = line.strip()
    return fields


def _texture_burst_summary(events):
    """Summarize durations separately and join only identical full IDs."""
    by_event = {}
    by_batch = {}
    by_full_id = {}
    for item in events:
        kind = item["event"]
        bucket = by_event.setdefault(kind, {"count": 0, "duration_ns": {}})
        bucket["count"] += 1
        for key, value in item.items():
            if key in _TEXTURE_BURST_DURATION_FIELDS and isinstance(value, int):
                samples = bucket["duration_ns"].setdefault(key, [])
                samples.append(value)
        batch = item.get("batch")
        if isinstance(batch, int):
            # Exact producer-supplied batch IDs are safe joins. Do not fill
            # missing frame/submission metadata from another event.
            by_batch.setdefault(str(batch), []).append(item)
        if all(isinstance(item.get(key), int)
               for key in ("batch", "frame", "submission")):
            by_full_id.setdefault((item["batch"], item["frame"],
                                   item["submission"]), []).append(item)
    for bucket in by_event.values():
        for key, values in bucket["duration_ns"].items():
            ordered = sorted(values)
            bucket["duration_ns"][key] = {
                "samples": len(ordered), "sum": sum(ordered),
                "p50": ordered[(len(ordered) - 1) // 2],
                "p95": ordered[(95 * len(ordered) - 1) // 100],
            }
    joins = []
    counts = {"encode_publish_pairs": 0, "encode_gpu_pairs": 0,
              "publish_without_encode": 0, "gpu_without_encode": 0,
              "encode_without_publish": 0, "encode_without_gpu": 0,
              "missing_join_id": 0, "slot_waits_total": 0,
              "overflow_dropped_total": 0}
    counts["slot_waits_total"] = sum(
        item.get("slot_waits", 0) for item in events
        if item.get("event") == "encode" and isinstance(item.get("slot_waits", 0), int))
    counts["overflow_dropped_total"] = sum(
        item.get("dropped", 0) for item in events
        if item.get("event") == "overflow" and isinstance(item.get("dropped", 0), int))
    for key, group in sorted(by_full_id.items()):
        kinds = {kind: sum(item.get("event") == kind for item in group)
                 for kind in ("encode", "publish", "gpu")}
        missing = [kind for kind, count in kinds.items() if count == 0]
        counts["encode_publish_pairs"] += bool(kinds["encode"] and kinds["publish"])
        counts["encode_gpu_pairs"] += bool(kinds["encode"] and kinds["gpu"])
        counts["publish_without_encode"] += bool(kinds["publish"] and not kinds["encode"])
        counts["gpu_without_encode"] += bool(kinds["gpu"] and not kinds["encode"])
        counts["encode_without_publish"] += bool(kinds["encode"] and not kinds["publish"])
        counts["encode_without_gpu"] += bool(kinds["encode"] and not kinds["gpu"])
        joins.append({"batch": key[0], "frame": key[1], "submission": key[2],
                      "events": kinds, "missing": missing,
                      "status": "complete" if not missing else "partial"})
    missing_ids = [item for item in events if not all(
        isinstance(item.get(key), int) for key in ("batch", "frame", "submission"))]
    counts["missing_join_id"] = len(missing_ids)
    return {
        "by_event": by_event, "by_batch": by_batch,
        "joins": counts, "by_full_id": joins,
        "unmatched_missing_id": missing_ids,
    }


def report(path, tsv_path=None):
    rep = {"log": path, "longframes": [], "steps": [], "red": [],
           "display_windows": [], "display_link": {}, "vfr_windows": [],
           "vfr_queue_windows": [], "timing_windows": [],
           "present_windows": [], "texture_pool_windows": [],
           "texture_burst_events": [], "texture_burst_tsv": tsv_path,
           # Optional queue metrics. None means absent from this log, not 0.
           "ready_depth": None, "drop_oldest": None,
           "credit": None, "promote_ms": None,
           "keepalive": False, "synclag": False, "prewarm": {}, "frames": 0,
           "ka_kernels": 0, "ka_gpu_ms": 0.0, "ka_max_ms": 0.0,
           "ka_stopped": 0, "ka_stop_cb_max_ms": 0.0,
           "ubr_reuse": 0, "ubr_new": 0, "ubr_peak": 0,
           "ubr_fallback": 0, "ubr_reused_kb": 0.0,
           "rth_new": 0, "rth_hits": 0, "rth_full": 0, "rth_evicted": 0,
           "rth_fallback": 0, "rth_backing_mb_max": 0, "rth_req": 0,
           "rth_stock_reused": 0, "rth_stock_made": 0, "rth_top": ""}
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            burst = _parse_texture_burst_line(line)
            if burst:
                rep["texture_burst_events"].append(burst)
            pool = _parse_texture_pool_line(line)
            if pool:
                previous = rep["texture_pool_windows"][-1] if rep["texture_pool_windows"] else None
                for key in ("reuse", "cold", "miss", "reserved_bytes"):
                    pool[f"delta_{key}"] = (pool[key] - previous[key]) if previous and pool[key] >= previous[key] else None
                rep["texture_pool_windows"].append(pool)
            if "[swx-keepalive] on:" in line:
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
            shown = re.search(
                r"on-screen up to frame (\d+): (\d+) frames, dropped (\d+) "
                r"\(discarded (\d+)\), p99 ([\d.]+) ms", line)
            if shown:
                present, count, dropped, discarded = map(int, shown.groups()[:4])
                rep["display_windows"].append({
                    "present": present, "shown": count - discarded, "late": dropped - discarded,
                    "discarded": discarded, "p99_ms": float(shown[5]),
                })
            link = re.search(
                r"display link up to tick (\d+): ticks (\d+), presents (\d+), empty ticks (\d+) "
                r"\(held for the present interval (\d+)\), old frames dropped by merging (\d+)", line)
            if link:
                fields = ("up_to_tick", "ticks", "presents", "empty", "held", "merged")
                rep["display_link"] = dict(zip(fields, map(int, link.groups())))
                # Sufixo opcional (logs legados podem nao te-lo): latencias e drenos.
                m = re.search(
                    r"submission ahead of target display p50 ([\d.]+) ms / min ([\d.]+) ms", line)
                rep["display_link"]["submit_ahead_p50_ms"] = float(m.group(1)) if m else None
                rep["display_link"]["submit_ahead_min_ms"] = float(m.group(2)) if m else None
                m = re.search(
                    r"game enqueue to target display p50 ([\d.]+) ms / min ([\d.]+) / max ([\d.]+) ms", line)
                if m:
                    rep["display_link"]["enqueue_to_target_p50_ms"] = float(m.group(1))
                    rep["display_link"]["enqueue_to_target_min_ms"] = float(m.group(2))
                    rep["display_link"]["enqueue_to_target_max_ms"] = float(m.group(3))
                else:
                    rep["display_link"]["enqueue_to_target_p50_ms"] = None
                    rep["display_link"]["enqueue_to_target_min_ms"] = None
                    rep["display_link"]["enqueue_to_target_max_ms"] = None
                m = re.search(r"standing frames drained (\d+) times", line)
                rep["display_link"]["drained"] = int(m.group(1)) if m else None
            pres = re.search(
                r"present: game enqueue to screen p50 ([\d.]+) ms, p95 ([\d.]+), max ([\d.]+) "
                r"over 300 frames shown \(ticket (\d+)\)", line)
            if pres:
                rep["present_windows"].append({
                    "ticket": int(pres.group(4)),
                    "enqueue_to_screen_p50_ms": float(pres.group(1)),
                    "enqueue_to_screen_p95_ms": float(pres.group(2)),
                    "enqueue_to_screen_max_ms": float(pres.group(3)),
                })
            if ("[nvn] vfr frame pace" in line.lower() or "[nvn] vfr cadence" in line.lower()
                    or "[nvn] vfr up to present" in line.lower()):
                rep["vfr_windows"].append(_parse_vfr_window(line))
                # Optional fields in older VFR formats; absent values stay None.
                for key, pat in (("ready_depth", r"ready[ _-]depth (\d+)"),
                                  ("drop_oldest", r"drop[ _-]oldest (\d+)"),
                                  ("credit", r"credit ([\d.]+)"),
                                  ("promote_ms", r"promote[ _-]ms ([\d.]+)")):
                    m = re.search(pat, line)
                    if m:
                        rep[key] = float(m.group(1)) if "." in m.group(1) else int(m.group(1))
            queue_window = _parse_present_queue_window(line)
            if queue_window:
                rep["vfr_queue_windows"].append(queue_window)
                for key in ("ready_depth", "drop_oldest", "credit"):
                    if queue_window[key] is not None:
                        rep[key] = queue_window[key]
                if queue_window["promote_ms_p95"] is not None:
                    rep["promote_ms"] = queue_window["promote_ms_p95"]
            timing = re.search(
                r"timing up to frame (\d+): frame time p50 [\d.]+ / p95 ([\d.]+).*?;"
                r" GPU p50 ([\d.]+) / p95 ([\d.]+)", line)
            if timing:
                rep["timing_windows"].append({
                    "present": int(timing[1]), "frame_p95_ms": float(timing[2]),
                    "gpu_p50_ms": float(timing[3]), "gpu_p95_ms": float(timing[4]),
                })

            m = re.search(r"keep-alive up to frame \d+: (\d+) kernels, ([\d.]+) GPU ms, max ([\d.]+) ms, (\d+) stopped, stop-cb max ([\d.]+) ms", line)
            if m:
                rep["ka_kernels"] += int(m.group(1)); rep["ka_gpu_ms"] += float(m.group(2));
                rep["ka_max_ms"] = max(rep["ka_max_ms"], float(m.group(3)));
                rep["ka_stopped"] += int(m.group(4));
                rep["ka_stop_cb_max_ms"] = max(rep["ka_stop_cb_max_ms"], float(m.group(5)))
            m = re.search(r"ub-ring up to frame \d+: (\d+) reuse, (\d+) new, \d+ inflight \(peak (\d+)\), (\d+) fallback, ([\d.]+) KB reused", line)
            if m:
                rep["ubr_reuse"] += int(m.group(1)); rep["ubr_new"] += int(m.group(2));
                rep["ubr_peak"] = max(rep["ubr_peak"], int(m.group(3)));
                rep["ubr_fallback"] += int(m.group(4)); rep["ubr_reused_kb"] += float(m.group(5))
            m = re.search(r"rt-heap up to frame \d+: arenas new (\d+), hits (\d+), full (\d+), active \d+, idle \d+, live \d+ targets, backing (\d+) MB, fallback (\d+), evicted (\d+); eligible (\d+), stock reused (\d+), stock made (\d+)", line)
            if m:
                rep["rth_new"] += int(m.group(1)); rep["rth_hits"] += int(m.group(2));
                rep["rth_full"] += int(m.group(3));
                rep["rth_backing_mb_max"] = max(rep["rth_backing_mb_max"], int(m.group(4)));
                rep["rth_fallback"] += int(m.group(5)); rep["rth_evicted"] += int(m.group(6));
                rep["rth_req"] += int(m.group(7));
                rep["rth_stock_reused"] += int(m.group(8)); rep["rth_stock_made"] += int(m.group(9))
            m = re.search(r"rt-heap top up to frame \d+: (.*)", line)
            if m:
                rep["rth_top"] = m.group(1).strip()[:300]
            m = re.search(r"frame (\d+) \(", line)
            if m:
                rep["frames"] = max(rep["frames"], int(m.group(1)))
    # The host writes texture-burst records to DM_NVN_TSV, never game.log.
    # Parse only structured markers; ignore TSV headers and unrelated rows.
    if tsv_path and os.path.abspath(tsv_path) != os.path.abspath(path):
        with open(tsv_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                burst = _parse_texture_burst_line(line)
                if burst:
                    rep["texture_burst_events"].append(burst)
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
    upload_submit = sorted(x["submit_ms"] for x in by_cause["uploads"])
    if upload_submit:
        # Nearest-rank p95: stable and interpretable for the small A/B corpus.
        rep["lf_upload_submit_p95_ms"] = upload_submit[(95 * len(upload_submit) - 1) // 100]
    else:
        rep["lf_upload_submit_p95_ms"] = 0.0
    rep["new_pipelines_total"] = sum(l["new_pipelines"] for l in lfs)
    rep["first_uploads_total"] = sum(l["first_uploads"] for l in lfs)
    rep["first_uploads_mb"] = round(sum(l["upload_mb"] for l in lfs), 1)
    rep["hybrid_draws_max"] = max([l["hybrid"] for l in lfs], default=0)
    rep["batches_total"] = sum(l["batches"] for l in lfs)
    rep["split_batches_total"] = sum(l["split_batches"] for l in lfs)
    up_lfs = [l for l in lfs if l["first_uploads"] > 0 and l["batches"] > 0]
    rep["upload_frames_with_batches"] = len(up_lfs)
    rep["upload_batch_rate"] = round(sum(l["first_uploads"] for l in up_lfs) / max(sum(l["batches"] for l in up_lfs), 1), 1) if up_lfs else 0.0
    for k in ("up_alloc_ms", "up_rescommit_ms", "up_cbend_ms", "up_drain_ms", "up_slotwait_ms", "up_oldq_ms",
              "up_alloc_n", "up_drain_n", "up_direct", "up_enc", "up_cpu",
              "up_pdi_ms", "up_pen_ms", "up_pcp_ms"):
        samples = [l[k] for l in lfs if l[k] is not None]
        total = sum(samples)
        rep[f"{k}_total"] = (round(total, 1) if k.endswith("_ms") else total) if samples else None
        rep[f"{k}_samples"] = len(samples)
    rep["texture_burst"] = _texture_burst_summary(rep["texture_burst_events"])
    return rep


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in argv
    tsv_path = None
    if "--tsv" in argv:
        i = argv.index("--tsv")
        if i + 1 >= len(argv):
            print("error: --tsv requires a path", file=sys.stderr)
            return 2
        tsv_path = argv[i + 1]
        del argv[i:i + 2]
    args = [a for a in argv if not a.startswith("-")]
    path = args[0] if args else DEFAULT_LOG
    if not os.path.isfile(path):
        print(f"error: log not found: {path}", file=sys.stderr)
        return 2
    if tsv_path and not os.path.isfile(tsv_path):
        print(f"error: TSV not found: {tsv_path}", file=sys.stderr)
        return 2
    rep = report(path, tsv_path)
    if as_json:
        print(json.dumps(rep, indent=2))
        return 0
    print(f"log: {rep['log']}")
    print(f"frames seen: {rep['frames']}, keep-alive: {rep['keepalive']}, sync-lag: {rep.get('synclag', False)}, [RED]: {len(rep['red'])}")
    print(f"prewarm: {rep.get('prewarm', {})}")
    print(f"longframes: {rep['longframe_count']} (worst {rep['worst_ms']:.1f} ms)")
    print(f"  with new pipelines : {rep['lf_pipelines']} (worst {rep['lf_pipelines_worst_ms']:.1f} ms)")
    print(f"  upload-only          : {rep['lf_uploads']} (worst {rep['lf_uploads_worst_ms']:.1f} ms)")
    print(f"  upload submit p95    : {rep['lf_upload_submit_p95_ms']:.1f} ms")
    print(f"  gpu-bound/other      : {rep['lf_gpu_other']} (worst {rep['lf_gpu_other_worst_ms']:.1f} ms)")
    print(f"new pipelines total: {rep['new_pipelines_total']}, "
          f"first uploads: {rep['first_uploads_total']} ({rep['first_uploads_mb']} MB), "
          f"max hybrid draws in a frame: {rep['hybrid_draws_max']}")
    print(f"upload batches: {rep['batches_total']} total ({rep['split_batches_total']} by count-split) "
          f"over {rep['upload_frames_with_batches']} frames with batch data, "
          f"{rep['upload_batch_rate']} uploads/batch")
    fmt = lambda value, places=1: "n/a" if value is None else f"{value:.{places}f}"
    print(f"upload cost split: alloc {fmt(rep.get('up_alloc_ms_total'))} ms ({fmt(rep.get('up_alloc_n_total'), 0)} bufs) + "
          f"rescommit {fmt(rep.get('up_rescommit_ms_total'))} ms + commit {fmt(rep.get('up_cbend_ms_total'))} ms + "
          f"drain {fmt(rep.get('up_drain_ms_total'))} ms ({fmt(rep.get('up_drain_n_total'), 0)} waits) + "
          f"slotwait {fmt(rep.get('up_slotwait_ms_total'))} ms + oldq {fmt(rep.get('up_oldq_ms_total'))} ms")
    print(f"upload paths: {fmt(rep.get('up_direct_total'), 0)} direct + {fmt(rep.get('up_enc_total'), 0)} GPU-encode + {fmt(rep.get('up_cpu_total'), 0)} CPU")
    print(f"upload path ms: direct {fmt(rep.get('up_pdi_ms_total'))} + enc {fmt(rep.get('up_pen_ms_total'))} + cpu {fmt(rep.get('up_pcp_ms_total'))}")
    if windows := rep["display_windows"]:
        print(f"screen: {sum(w['shown'] for w in windows)} shown, "
              f"{sum(w['late'] for w in windows)} late, "
              f"{sum(w['discarded'] for w in windows)} discarded, "
              f"worst window p99 {max(w['p99_ms'] for w in windows):.2f} ms")
    if rep["display_link"]:
        dl = rep["display_link"]
        print(f"display link (last report, up to tick {dl.get('up_to_tick')}, window of {dl.get('ticks')} ticks): presents {dl.get('presents')}, "
              f"empty {dl.get('empty')}, held {dl.get('held')}, merged {dl.get('merged')}")
        print(f"  enqueue to target display p50 "
              f"{dl.get('enqueue_to_target_p50_ms') if dl.get('enqueue_to_target_p50_ms') is not None else 'n/a'} ms, "
              f"submit ahead p50 "
              f"{dl.get('submit_ahead_p50_ms') if dl.get('submit_ahead_p50_ms') is not None else 'n/a'} ms, "
              f"drained {dl.get('drained') if dl.get('drained') is not None else 'n/a'}")
    if pw := rep.get("present_windows"):
        print(f"enqueue to screen: p50 {pw[-1]['enqueue_to_screen_p50_ms']:.1f} ms "
              f"(p95 {pw[-1]['enqueue_to_screen_p95_ms']:.1f}, max {pw[-1]['enqueue_to_screen_max_ms']:.1f}, "
              f"{len(pw)} windows)")
    if vw := rep.get("vfr_windows"):
        last = vw[-1]
        if "hz" in last:
            print(f"vfr: {last.get('policy')} {last.get('hz')} Hz, display {last.get('display_hz')} Hz, "
                  f"avg present {last.get('avg_present_ms'):.2f} ms, ratio {last.get('ratio'):.3f} "
                  f"({last.get('steps')} steps), queued {last.get('queued')}, "
                  f"dropped {last.get('dropped')}/{last.get('window')}")
            gd, wd = last.get("game_delta_s"), last.get("wall_delta_s")
            print(f"  VFR window delta: game {gd if gd is not None else 'n/a'} s / "
                  f"wall {wd if wd is not None else 'n/a'} s")
        else:
            print(f"vfr: {len(vw)} legacy window(s)")
    if qw := rep.get("vfr_queue_windows"):
        last = qw[-1]
        def show(value):
            return "n/a" if value is None else value
        print(f"vfr present queue (last tick {last['tick']}): ready_depth={show(last['ready_depth'])}, "
              f"drop_oldest={show(last['drop_oldest'])}, credit={show(last['credit'])}, "
              f"queue_ms p95={show(last['queue_ms_p95'])} from {show(last['queue_pops'])} pops, "
              f"promote_ms p95={show(last['promote_ms_p95'])} from {show(last['promote_count'])} promotions, "
              f"window={show(last['window'])}, held={show(last['held'])}, empty={show(last['empty'])}")
    elif rep.get("ready_depth") is not None or rep.get("drop_oldest") is not None or rep.get("credit") is not None:
        print(f"optional queue counters: ready_depth={rep.get('ready_depth') if rep.get('ready_depth') is not None else 'n/a'}, "
              f"drop_oldest={rep.get('drop_oldest') if rep.get('drop_oldest') is not None else 'n/a'}, "
              f"credit={rep.get('credit') if rep.get('credit') is not None else 'n/a'}, "
              f"promote_ms={rep.get('promote_ms') if rep.get('promote_ms') is not None else 'n/a'}")
    if burst_events := rep.get("texture_burst_events"):
        burst = rep["texture_burst"]
        kinds = ", ".join(f"{kind}={info['count']}" for kind, info in sorted(burst["by_event"].items()))
        joins = burst["joins"]
        print(f"texture-burst schema 1: {kinds}; encode/publish joined={joins['encode_publish_pairs']}, "
              f"encode/GPU joined={joins['encode_gpu_pairs']}, missing full IDs={joins['missing_join_id']}, "
              f"slot waits={joins['slot_waits_total']}, overflow dropped={joins['overflow_dropped_total']}")
        for kind, info in sorted(burst["by_event"].items()):
            costs = info["duration_ns"]
            if costs:
                dominant = max(costs, key=lambda key: costs[key]["p50"])
                value = costs[dominant]
                print(f"  {kind} highest p50 field: {dominant} p50={value['p50']} ns "
                      f"p95={value['p95']} ns (n={value['samples']}); fields remain separate")
    print(f"frame-step transitions: {', '.join(rep['steps']) if rep['steps'] else 'none'}")
    for r in rep["red"][:10]:
        print(f"RED: {r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
