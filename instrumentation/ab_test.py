#!/usr/bin/env python3
"""A/B test harness for real gameplay runs (BOTW, 01007EF00011E000).

The user plays a fixed route; this tool manages the player env.txt overrides
and snapshots each finished run (game log + env state + report) so launcher
log rotation can never lose data. Comparison via instrumentation/log_report.py.

Usage:
  ab_test.py status [--install-dir I]
  ab_test.py set keepalive|snap|save|pso|pool|bind_once|vfr|yield|residency-ub-ring|rt-heap-reserve|up-blit-rec|disable-drs|res-bypass on|off|clear
                   (res-bypass also takes 1080p|1440p|4k explicitly; on stays 1080p)
  ab_test.py set vfr uncapped
  ab_test.py set vfr-hz auto|30..240|clear
  ab_test.py collect <label> [--install-dir I] [--log PATH]
                       [--tsv PATH]
  ab_test.py compare <labelA> <labelB> [--install-dir I]

"on" writes an explicit value (visible as `launcher: env K=V` at boot;
=1, except snap=4 and vfr=relaxed); "off" forces stock (=0, except snap=2);
"clear" removes the line so the compiled/profile default applies. Every
new install defaults to the VFR 60 fps lock: `SWX_VFR=relaxed` at auto Hz (the
snapshot ring is not pinned, so the compiled default applies unless set
for A/B); `set vfr off` restores the legacy
Switch-interval frame step for A/B. `snap 4` is an optional smoothness
experiment that can conceal the visible hitch; keep-alive on on macOS
(texture-upload count split was removed after Gate 1 showed +13 ms).
"""
import json
import os
import shutil
import sys
import time

TITLE_ID = "01007EF00011E000"
DEFAULT_LOG = os.path.expanduser(
    f"~/Library/Logs/nxnative/{TITLE_ID}.log")
APP_SUPPORT_ENV = os.path.expanduser(
    f"~/Library/Application Support/nxnative/{TITLE_ID}/env.txt")

KEYS = {
    "keepalive": "SWX_GPU_KEEPALIVE",
    "snap": "DM_PRESENT_SNAP",
    "save": "SWX_SAVE_ASYNC",
    "pso": "DM_PSO_USER_ASYNC",
    "pool": "SWX_GPU_TEXTURE_POOL",
    "bind_once": "SWX_GPU_BIND_ONCE",
    "vfr": "SWX_VFR",
    "vfr-hz": "SWX_VFR_HZ",
    "yield": "SWX_GPU_KEEPALIVE_YIELD",
    "residency-ub-ring": "SWX_RESIDENCY_UB_RING",
    "rt-heap-reserve": "SWX_RT_HEAP_RESERVE",
    "up-blit-rec": "SWX_NVN_UP_BLIT_REC",
    "disable-drs": "SWX_DISABLE_DRS",
    "res-bypass": "SWX_RES_BYPASS",
}
# Explicit values for on/off (defaults live in code/profile/install env).
ON_VALUES = {"snap": "4", "vfr": "relaxed", "res-bypass": "1080p"}
OFF_VALUES = {"snap": "2", "vfr": "0", "res-bypass": "0"}

ENV_ALWAYS_KEEP = ("DM_SAVE=", "DM_PSO_USER=", "DM_PSO_ARCHIVE=",
                   "DM_PSO_PAIRS=")


def install_dir(args):
    if "--install-dir" in args:
        return args[args.index("--install-dir") + 1]
    d = os.environ.get("SWX_INSTALL_DIR", "")
    if not d:
        print("error: pass --install-dir <I> or set SWX_INSTALL_DIR",
              file=sys.stderr)
        raise SystemExit(2)
    return d


def read_env_lines():
    if not os.path.isfile(APP_SUPPORT_ENV):
        return []
    with open(APP_SUPPORT_ENV, "r", encoding="utf-8", errors="replace") as f:
        return f.read().splitlines()


def write_env_lines(lines):
    with open(APP_SUPPORT_ENV, "w", encoding="utf-8") as f:
        for ln in lines:
            f.write(ln + "\n")


def cmd_status(args):
    _ = args
    print(f"env.txt: {APP_SUPPORT_ENV}")
    for ln in read_env_lines():
        if ln.strip().startswith(("SWX_", "DM_NVN_WAITS")) \
                or ln.strip().startswith(ENV_ALWAYS_KEEP):
            print(f"  {ln}")
    for p in (DEFAULT_LOG, DEFAULT_LOG.replace(".log", ".prev.log")):
        if os.path.isfile(p):
            st = os.stat(p)
            print(f"log: {p} ({st.st_size} B, "
                  f"mtime {time.strftime('%H:%M:%S', time.localtime(st.st_mtime))})")
        else:
            print(f"log: {p} (missing)")


def cmd_set(args):
    if len(args) != 2 or args[0] not in KEYS:
        raise SystemExit("usage: ab_test.py set <feature> on|off|clear")
    feature, mode = args
    if feature == "res-bypass" and mode in ("1080p", "1440p", "4k"):
        value = mode
    elif feature == "vfr-hz":
        if mode not in ("auto", "clear") and mode not in map(str, range(30, 241)):
            raise SystemExit("usage: ab_test.py set vfr-hz auto|30..240|clear")
        value = mode
    elif feature == "vfr" and mode == "uncapped":
        value = mode
    elif mode in ("on", "off", "clear"):
        values = ON_VALUES if mode == "on" else OFF_VALUES
        value = values.get(feature, "1" if mode == "on" else "0")
    else:
        raise SystemExit("usage: ab_test.py set <feature> on|off|clear (res-bypass also takes 1080p|1440p|4k)")
    key = KEYS[feature]
    drop = (key + "=", "DM_SAVE_ASYNC=") if feature == "save" else (key + "=",)
    lines = [ln for ln in read_env_lines() if not ln.strip().startswith(drop)]
    if mode != "clear":
        lines.append(f"{key}={value}")
    write_env_lines(lines)
    print(f"{key}: {'removed (compiled default applies)' if mode == 'clear' else value}")


def cmd_collect(args):
    if not args:
        print("usage: ab_test.py collect <label> [--install-dir I]",
              file=sys.stderr)
        raise SystemExit(2)
    label = args[0]
    log = DEFAULT_LOG
    if "--log" in args:
        log = args[args.index("--log") + 1]
    tsv = args[args.index("--tsv") + 1] if "--tsv" in args else os.environ.get("DM_NVN_TSV")
    if not tsv:
        tsv = next((ln.split("=", 1)[1] for ln in read_env_lines()
                    if ln.strip().startswith("DM_NVN_TSV=")), None)
    dest = os.path.join(install_dir(args), ".swx-history", f"ab-{label}")
    os.makedirs(dest, exist_ok=True)
    if not os.path.isfile(log):
        print(f"error: no finished run log yet: {log}", file=sys.stderr)
        raise SystemExit(2)
    shutil.copy2(log, os.path.join(dest, "game.log"))
    stored_tsv = None
    if tsv:
        if not os.path.isfile(tsv):
            print(f"error: DM_NVN_TSV was set but the timing TSV is missing: {tsv}",
                  file=sys.stderr)
            raise SystemExit(2)
        stored_tsv = os.path.join(dest, "timing.tsv")
        shutil.copy2(tsv, stored_tsv)
    with open(os.path.join(dest, "env.txt"), "w", encoding="utf-8") as f:
        for ln in read_env_lines():
            f.write(ln + "\n")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from log_report import report
    rep = report(log, stored_tsv)
    rep["timing_tsv"] = "timing.tsv" if stored_tsv else None
    with open(os.path.join(dest, "report.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=2)
    print(f"collected '{label}': {rep['frames']} frames, "
          f"{rep['longframe_count']} longframes (worst {rep['worst_ms']:.1f} ms), "
          f"uploads {rep['first_uploads_total']} in {rep.get('batches_total', 0)} batches "
          f"-> {dest}")


def one_line(path):
    with open(path, encoding="utf-8") as f:
        rep = json.load(f)
    return {
        "label": os.path.basename(os.path.dirname(path))[3:],
        "frames": rep["frames"],
        "longframes": rep["longframe_count"],
        "worst": rep["worst_ms"],
        "screen_p99_max": max((w["p99_ms"] for w in rep.get("display_windows", [])), default="-"),
        "screen_late": sum(w["late"] for w in rep["display_windows"]) if rep.get("display_windows") else "-",
        "screen_discarded": sum(w["discarded"] for w in rep["display_windows"]) if rep.get("display_windows") else "-",
        "lf_up": rep["lf_uploads"],
        "lf_up_worst": rep["lf_uploads_worst_ms"],
        "lf_up_submit_p95": rep.get("lf_upload_submit_p95_ms", 0.0),
        "lf_gpu": rep["lf_gpu_other"],
        "lf_gpu_worst": rep["lf_gpu_other_worst_ms"],
        "uploads": rep["first_uploads_total"],
        "mb": rep["first_uploads_mb"],
        "batches": rep.get("batches_total", 0),
        "up_per_batch": rep.get("upload_batch_rate", 0.0),
        "u_alloc": rep.get("up_alloc_ms_total", 0.0),
        "u_rescommit": rep.get("up_rescommit_ms_total", 0.0),
        "u_commit": rep.get("up_cbend_ms_total", 0.0),
        "u_drain": rep.get("up_drain_ms_total", 0.0),
        "u_slotwait": rep.get("up_slotwait_ms_total", 0.0),
        "u_oldq": rep.get("up_oldq_ms_total", 0.0),
        "p_direct": rep.get("up_direct_total", 0),
        "p_enc": rep.get("up_enc_total", 0),
        "p_cpu": rep.get("up_cpu_total", 0),
        "p_di_ms": rep.get("up_pdi_ms_total", 0.0),
        "p_en_ms": rep.get("up_pen_ms_total", 0.0),
        "p_cp_ms": rep.get("up_pcp_ms_total", 0.0),
        "steps": ",".join(rep["steps"]) or "-",
        "keepalive": rep["keepalive"],
        "ka_kernels": rep.get("ka_kernels", 0),
        "ka_gpu_ms": rep.get("ka_gpu_ms", 0.0),
        "ka_max_ms": rep.get("ka_max_ms", 0.0),
        "ka_stopped": rep.get("ka_stopped", 0),
        "ka_stop_cb_max_ms": rep.get("ka_stop_cb_max_ms", 0.0),
        "ubr_reuse": rep.get("ubr_reuse", 0),
        "ubr_new": rep.get("ubr_new", 0),
        "ubr_peak": rep.get("ubr_peak", 0),
        "ubr_fallback": rep.get("ubr_fallback", 0),
        "ubr_reused_kb": rep.get("ubr_reused_kb", 0.0),
        "rth_new": rep.get("rth_new", 0),
        "rth_hits": rep.get("rth_hits", 0),
        "rth_full": rep.get("rth_full", 0),
        "rth_fallback": rep.get("rth_fallback", 0),
        "rth_backing_mb": rep.get("rth_backing_mb_max", 0),
        "rth_req": rep.get("rth_req", 0),
        "rth_stock_reused": rep.get("rth_stock_reused", 0),
        "rth_stock_made": rep.get("rth_stock_made", 0),
    }


def cmd_compare(args):
    if len(args) < 2:
        print("usage: ab_test.py compare <labelA> <labelB> [--install-dir I]",
              file=sys.stderr)
        raise SystemExit(2)
    rows = []
    for lab in args[:2]:
        p = os.path.join(install_dir(args), ".swx-history", f"ab-{lab}", "report.json")
        if not os.path.isfile(p):
            print(f"error: run '{lab}' not collected yet", file=sys.stderr)
            raise SystemExit(2)
        rows.append(one_line(p))
    cols = ["label", "frames", "longframes", "worst", "screen_p99_max", "screen_late", "screen_discarded", "lf_up", "lf_up_worst", "lf_up_submit_p95",
            "lf_gpu", "lf_gpu_worst", "uploads", "mb", "batches",
            "up_per_batch", "u_alloc", "u_rescommit", "u_commit", "u_drain",
            "u_slotwait", "u_oldq", "p_direct", "p_enc", "p_cpu",
            "p_di_ms", "p_en_ms", "p_cp_ms", "steps", "keepalive",
            "ka_kernels", "ka_gpu_ms", "ka_max_ms", "ka_stopped", "ka_stop_cb_max_ms",
            "ubr_reuse", "ubr_new", "ubr_peak", "ubr_fallback", "ubr_reused_kb",
            "rth_new", "rth_hits", "rth_full", "rth_fallback", "rth_backing_mb",
            "rth_req", "rth_stock_reused", "rth_stock_made"]
    width = [max(len(c), max(len(str(r[c])) for r in rows)) for c in cols]
    print("  ".join(c.ljust(w) for c, w in zip(cols, width)))
    for r in rows:
        print("  ".join(str(r[c]).ljust(w) for c, w in zip(cols, width)))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip().splitlines()[0])
        print("See usage in the module docstring: status | set | collect | compare")
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "status":
        cmd_status(rest)
    elif cmd == "set":
        cmd_set(rest)
    elif cmd == "collect":
        cmd_collect(rest)
    elif cmd == "compare":
        cmd_compare(rest)
    else:
        print(f"error: unknown command: {cmd}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
