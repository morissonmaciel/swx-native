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
    m_submit = re.search(r"submit\+present ([\d.]+) ms", line)
    m_hy = re.search(r"draws with a hybrid pipeline (\d+)", line)
    m_gpu = re.search(r"GPU ([\d.]+) ms", line)
    m_fr = re.search(r"frame (\d+)", line)
    m_ba = re.search(r"in (\d+) batches \((\d+) split\)", line)
    m_tm = re.search(r"\[upload alloc ([\d.]+) ms \((\d+) bufs\) \+ rescommit ([\d.]+) ms \+ commit ([\d.]+) ms \+ drain ([\d.]+) ms \((\d+)\) \+ slotwait ([\d.]+) ms \+ oldq ([\d.]+) ms\]", line)
    m_pa = re.search(r"\+ path (\d+)/(\d+)/(\d+) \(([\d.]+)/([\d.]+)/([\d.]+) ms\)\]", line)
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
        "up_alloc_ms": float(m_tm.group(1)) if m_tm else 0.0,
        "up_alloc_n": int(m_tm.group(2)) if m_tm else 0,
        "up_rescommit_ms": float(m_tm.group(3)) if m_tm else 0.0,
        "up_cbend_ms": float(m_tm.group(4)) if m_tm else 0.0,
        "up_drain_ms": float(m_tm.group(5)) if m_tm else 0.0,
        "up_drain_n": int(m_tm.group(6)) if m_tm else 0,
        "up_slotwait_ms": float(m_tm.group(7)) if m_tm else 0.0,
        "up_oldq_ms": float(m_tm.group(8)) if m_tm else 0.0,
        "up_direct": int(m_pa.group(1)) if m_pa else 0,
        "up_enc": int(m_pa.group(2)) if m_pa else 0,
        "up_cpu": int(m_pa.group(3)) if m_pa else 0,
        "up_pdi_ms": float(m_pa.group(4)) if m_pa else 0.0,
        "up_pen_ms": float(m_pa.group(5)) if m_pa else 0.0,
        "up_pcp_ms": float(m_pa.group(6)) if m_pa else 0.0,
    }


def _parse_vfr_window(line):
    """Janela VFR estruturada; tolera linhas legadas sem todos os campos."""
    raw = line.strip()
    m = re.search(
        r"VFR up to present (\d+): (\S+) (\S+) (\d+) Hz, (\S+) clock, display (\d+) Hz, "
        r"average present ([\d.]+) ms, applied ([\d.]+) ms, sampled time ([\d.]+) s, "
        r"ratio ([\d.]+) \((\d+) VFR steps\), ring waits ([\d.]+) / ticket waits ([\d.]+) ms, "
        r"queued (\d+), (\d+) of last (\d+) dropped, guest interval (\d+)", raw)
    if not m:
        return {"raw": raw}
    return {
        "raw": raw,
        "present": int(m.group(1)),
        "policy": m.group(2),
        "clock": m.group(3),
        "hz": int(m.group(4)),
        "clock_source": m.group(5),
        "display_hz": int(m.group(6)),
        "avg_present_ms": float(m.group(7)),
        "applied_ms": float(m.group(8)),
        "sampled_s": float(m.group(9)),
        "ratio": float(m.group(10)),
        "steps": int(m.group(11)),
        "ring_wait_ms": float(m.group(12)),
        "ticket_wait_ms": float(m.group(13)),
        "queued": int(m.group(14)),
        "dropped": int(m.group(15)),
        "window": int(m.group(16)),
        "guest_interval": int(m.group(17)),
    }


def report(path):
    rep = {"log": path, "longframes": [], "steps": [], "red": [],
           "display_windows": [], "display_link": {}, "vfr_windows": [], "timing_windows": [],
           "present_windows": [],
           # Etapa 0: contadores futuros ainda nao emitidos pelo host.
           # None = indisponivel (nunca 0, que pareceria medicao).
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
                # Contadores futuros da Etapa 1+: opcionais; ausentes = None (indisponivel).
                for key, pat in (("ready_depth", r"ready[ _-]depth (\d+)"),
                                  ("drop_oldest", r"drop[ _-]oldest (\d+)"),
                                  ("credit", r"credit ([\d.]+)"),
                                  ("promote_ms", r"promote[ _-]ms ([\d.]+)")):
                    m = re.search(pat, line)
                    if m:
                        rep[key] = float(m.group(1)) if "." in m.group(1) else int(m.group(1))
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
    for k in ("up_alloc_ms", "up_rescommit_ms", "up_cbend_ms", "up_drain_ms", "up_slotwait_ms", "up_oldq_ms"):
        rep[f"{k}_total"] = round(sum(l[k] for l in lfs), 1)
    rep["up_alloc_n_total"] = sum(l["up_alloc_n"] for l in lfs)
    rep["up_drain_n_total"] = sum(l["up_drain_n"] for l in lfs)
    rep["up_direct_total"] = sum(l["up_direct"] for l in lfs)
    rep["up_enc_total"] = sum(l["up_enc"] for l in lfs)
    rep["up_cpu_total"] = sum(l["up_cpu"] for l in lfs)
    for k in ("up_pdi_ms", "up_pen_ms", "up_pcp_ms"):
        rep[k + "_total"] = round(sum(l[k] for l in lfs), 1)
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
    print(f"  upload submit p95    : {rep['lf_upload_submit_p95_ms']:.1f} ms")
    print(f"  gpu-bound/other      : {rep['lf_gpu_other']} (worst {rep['lf_gpu_other_worst_ms']:.1f} ms)")
    print(f"new pipelines total: {rep['new_pipelines_total']}, "
          f"first uploads: {rep['first_uploads_total']} ({rep['first_uploads_mb']} MB), "
          f"max hybrid draws in a frame: {rep['hybrid_draws_max']}")
    print(f"upload batches: {rep['batches_total']} total ({rep['split_batches_total']} by count-split) "
          f"over {rep['upload_frames_with_batches']} frames with batch data, "
          f"{rep['upload_batch_rate']} uploads/batch")
    print(f"upload cost split: alloc {rep.get('up_alloc_ms_total', 0.0):.1f} ms ({rep.get('up_alloc_n_total', 0)} bufs) + "
          f"rescommit {rep.get('up_rescommit_ms_total', 0.0):.1f} ms + commit {rep.get('up_cbend_ms_total', 0.0):.1f} ms + "
          f"drain {rep.get('up_drain_ms_total', 0.0):.1f} ms ({rep.get('up_drain_n_total', 0)} waits) + "
          f"slotwait {rep.get('up_slotwait_ms_total', 0.0):.1f} ms + oldq {rep.get('up_oldq_ms_total', 0.0):.1f} ms")
    print(f"upload paths: {rep.get('up_direct_total', 0)} direct + {rep.get('up_enc_total', 0)} GPU-encode + {rep.get('up_cpu_total', 0)} CPU")
    print(f"upload path ms: direct {rep.get('up_pdi_ms_total', 0.0):.1f} + enc {rep.get('up_pen_ms_total', 0.0):.1f} + cpu {rep.get('up_pcp_ms_total', 0.0):.1f}")
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
        else:
            print(f"vfr: {len(vw)} legacy window(s)")
    print(f"future counters (Etapa 0, indisponiveis): ready_depth={rep.get('ready_depth', None) if rep.get('ready_depth', None) is not None else 'n/a'}, "
          f"drop_oldest={rep.get('drop_oldest', None) if rep.get('drop_oldest', None) is not None else 'n/a'}, "
          f"credit={rep.get('credit', None) if rep.get('credit', None) is not None else 'n/a'}, "
          f"promote_ms={rep.get('promote_ms', None) if rep.get('promote_ms', None) is not None else 'n/a'}")
    print(f"frame-step transitions: {', '.join(rep['steps']) if rep['steps'] else 'none'}")
    for r in rep["red"][:10]:
        print(f"RED: {r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
