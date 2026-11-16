#!/usr/bin/env python3
"""swx-native wrapper (backend).

Collects the minimal inputs of a game install, validates them, prepares the
Install Dir (<I>), then drives core/nxnative through extract, binary rewrite,
shader build, host build and Mac app packing, finishing with a signed
read-only .app (Game Mode on, controller support on).

Designed as a backend: a future graphical UI can import resolve/validate or
drive this module as a subprocess (--print-config emits the resolved config
as JSON). Progress comes from nxnative's own `--progress json` events; each
phase is logged and, on a TTY, shown under a braille spinner.

Minimal inputs:
  --base         base game file (.nsp)
  --update       update file, BOTW 1.9.0 (.nsp)
  --keys         prod.keys file (its path is logged, never its contents)
  --install-dir  Install Dir (<I>): folder the game is installed/built into

Without a TTY, every flag must be passed (pipelines fail with usage instead
of prompting).
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime

TITLE_ID = "01007EF00011E000"
EXPECTED_UPDATE_HINTS = ("1.9.0", "v1114112", "1114112")
GAME_EXTS = (".nsp", ".xci", ".nsz", ".xcz")
LAYOUT_VERSION = 1
LAYOUT_DIRS = ("game", "save", "host", "shaders")
SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

ENV = {
    "base": "SWX_BASE_NSP",
    "update": "SWX_UPDATE_NSP",
    "keys": "SWX_PROD_KEYS",
    "install_dir": "SWX_INSTALL_DIR",
}


class InstallError(Exception):
    """A phase failed. Carries what the user needs: phase, log, tail."""

    def __init__(self, phase, log_path, tail):
        super().__init__(f"{phase} failed (log: {log_path})")
        self.phase = phase
        self.log_path = log_path
        self.tail = tail


def here_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def parse_args(argv):
    p = argparse.ArgumentParser(
        prog="main.py",
        description="swx-native backend: resolve and validate install inputs, then build the game via core/nxnative.",
    )
    p.add_argument("--base", default=os.environ.get(ENV["base"]),
                   help="base game file (.nsp)")
    p.add_argument("--update", default=os.environ.get(ENV["update"]),
                   help="update file, BOTW 1.9.0 (.nsp)")
    p.add_argument("--keys", default=os.environ.get(ENV["keys"]),
                   help="prod.keys file (its path is logged, never its contents)")
    p.add_argument("--install-dir", "-I", default=os.environ.get(ENV["install_dir"]),
                   help="Install Dir (<I>): folder the game is installed/built into")
    p.add_argument("--title-id", default=TITLE_ID,
                   help=f"game title id (default: {TITLE_ID})")
    p.add_argument("--platform", default="mac-app", choices=("mac", "mac-app"),
                   help="nxnative platform (default: mac-app)")
    p.add_argument("--app-name", default="Breath of the Wild",
                   help="name of the built .app (default: Breath of the Wild)")
    p.add_argument("--print-config", action="store_true",
                   help="print the resolved config as JSON and exit")
    p.add_argument("--validate-only", action="store_true",
                   help="validate inputs and prepare <I>, then exit before calling nxnative")
    p.add_argument("--no-spinner", action="store_true",
                   help="never show the braille spinner (one line per event instead)")
    return p.parse_args(argv)


def ask(prompt, current):
    """Prompt for one value, keeping `current` on empty input."""
    suffix = f" [{current}]" if current else ""
    try:
        value = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        return current
    return value or current


def prompt_missing(cfg):
    """Interactively fill every missing input. Only call on a TTY."""
    print("Interactive setup — minimal install inputs (empty keeps current):")
    if not cfg["base"]:
        cfg["base"] = ask("base game .nsp path", cfg["base"])
    if not cfg["update"]:
        cfg["update"] = ask("update 1.9.0 .nsp path", cfg["update"])
    if not cfg["keys"]:
        cfg["keys"] = ask("prod.keys path", cfg["keys"])
    if not cfg["install_dir"]:
        cfg["install_dir"] = ask("Install Dir (<I>) path", cfg["install_dir"])
    return cfg


def validate(cfg):
    """Return a list of error strings; empty means the config is usable."""
    errors = []
    for key in ("base", "update"):
        path = cfg[key] or ""
        if not path:
            errors.append(f"--{key.replace('_', '-')} is required")
        elif not os.path.isfile(path):
            errors.append(f"--{key.replace('_', '-')} not found: {path}")
    keys = cfg["keys"] or ""
    if not keys:
        errors.append("--keys is required")
    elif not os.path.isfile(keys):
        errors.append(f"--keys not found: {keys}")
    elif os.path.getsize(keys) == 0:
        errors.append(f"--keys is empty: {keys}")
    install_dir = cfg["install_dir"] or ""
    if not install_dir:
        errors.append("--install-dir is required")
    else:
        try:
            os.makedirs(install_dir, exist_ok=True)
        except OSError as e:
            errors.append(f"cannot create Install Dir {install_dir}: {e}")
        if os.path.isdir(install_dir) and not os.access(install_dir, os.W_OK):
            errors.append(f"Install Dir not writable: {install_dir}")
    return errors


def warnings(cfg):
    """Non-fatal notes about suspicious but usable inputs."""
    notes = []
    for key in ("base", "update"):
        path = (cfg[key] or "")
        if path and not path.lower().endswith(GAME_EXTS):
            notes.append(f"{key} has an unusual game extension: {path}")
    update = cfg["update"] or ""
    if update and not any(h in os.path.basename(update) for h in EXPECTED_UPDATE_HINTS):
        notes.append(f"update filename does not mention 1.9.0/v1114112: {update}")
    return notes


# ---- logging (system tmp, auto-cleaned; history receipt stays in <I>) ----

def stamp():
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def logs_dir():
    base = os.path.join(tempfile.gettempdir(), "swx-native", "logs")
    os.makedirs(base, exist_ok=True)
    return base


def log_line(master, msg):
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}\n"
    with open(master, "a", encoding="utf-8") as f:
        f.write(line)


def file_tail(path, n=30):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-n:])
    except OSError:
        return ""


def file_info(path):
    try:
        st = os.stat(path)
        return {"name": os.path.basename(path), "size": st.st_size,
                "mtime": int(st.st_mtime)}
    except OSError:
        return {"name": os.path.basename(path or ""), "size": None, "mtime": None}


def write_receipt(install_dir, record):
    hist = os.path.join(install_dir, ".swx-history")
    os.makedirs(hist, exist_ok=True)
    path = os.path.join(hist, f"install-{record['stamp']}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)
    return path


def nxnative_commit(nx_root):
    try:
        r = subprocess.run(["git", "-C", nx_root, "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, timeout=30)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"


# ---- terminal spinner (TTY only) ----

class Spinner:
    """Braille spinner with a mutable stage title. No-op when disabled."""

    def __init__(self, enabled):
        self.enabled = enabled
        self.title = ""
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()

    def set_title(self, title):
        with self._lock:
            self.title = title

    def start(self):
        if not self.enabled or self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        i = 0
        while not self._stop.is_set():
            with self._lock:
                title = self.title
            frame = SPINNER_FRAMES[i % len(SPINNER_FRAMES)]
            sys.stdout.write(f"\r{frame} {title}   ")
            sys.stdout.flush()
            i += 1
            self._stop.wait(0.08)
        sys.stdout.write("\r" + " " * 80 + "\r")
        sys.stdout.flush()

    def stop(self):
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join()
        self._thread = None


# ---- install phases ----

def phase_prepare_dirs(cfg, master, phase_log):
    """Fase 1: create <I>/{game,save,host,shaders} + layout marker."""
    t0 = time.monotonic()
    install_dir = cfg["install_dir"]
    for d in LAYOUT_DIRS:
        os.makedirs(os.path.join(install_dir, d), exist_ok=True)
    marker = os.path.join(install_dir, ".swx-layout")
    with open(marker, "w", encoding="utf-8") as f:
        f.write(f"layout_version={LAYOUT_VERSION}\n")
    secs = time.monotonic() - t0
    log_line(master, f"phase prepare-dirs ok ({secs:.1f}s): {', '.join(LAYOUT_DIRS)}")
    with open(phase_log, "a", encoding="utf-8") as f:
        f.write(f"created {', '.join(LAYOUT_DIRS)} under {install_dir}\n")
    return {"name": "prepare-dirs", "seconds": round(secs, 1), "status": "ok"}


def stage_game_files(cfg, master):
    """Symlink base + update into system tmp (transient staging)."""
    stage = os.path.join(tempfile.gettempdir(), "swx-native", f"stage-{stamp()}")
    os.makedirs(stage, exist_ok=True)
    for key in ("base", "update"):
        src = os.path.abspath(os.path.expanduser(cfg[key]))
        dst = os.path.join(stage, os.path.basename(src))
        if os.path.islink(dst) or os.path.exists(dst):
            os.remove(dst)
        os.symlink(src, dst)
    log_line(master, f"staged game files in {stage}")
    return stage


def run_nxnative(cfg, stage, master, phase_log, spinner):
    """Fase 2: extract, rewrite binaries, build shaders/host, pack the .app."""
    t0 = time.monotonic()
    nx_root = os.path.join(here_root(), "core", "nxnative")
    script = os.path.join(nx_root, "scripts", "build_ipa_from_nsp.py")
    if not os.path.isfile(script):
        raise InstallError("nxnative", phase_log,
                           f"nxnative script missing: {script} (see README: populate core/nxnative)")
    work_dir = os.path.join(cfg["install_dir"], "game", "build")
    app_path = os.path.join(cfg["install_dir"], "host", cfg["app_name"] + ".app")
    cmd = [sys.executable, script, stage,
           "-k", cfg["keys"],
           "--platform", cfg["platform"],
           "--work-dir", work_dir,
           "-o", app_path,
           "--progress", "json"]
    env = dict(os.environ)
    env["DM_GAME"] = cfg["title_id"]
    log_line(master, f"phase nxnative start: {' '.join(cmd)} (DM_GAME={cfg['title_id']})")
    with open(phase_log, "a", encoding="utf-8") as f:
        f.write(f"$ {' '.join(cmd)}\n")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, errors="replace", env=env, bufsize=1)
    with open(phase_log, "a", encoding="utf-8", errors="replace") as f:
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            f.write(line + "\n")
            try:
                ev = json.loads(line)
            except (ValueError, TypeError):
                log_line(master, f"nxnative: {line[:200]}")
                continue
            kind = ev.get("ev")
            if kind == "stage":
                title = f"[{ev.get('k')}/{ev.get('n')}] {ev.get('name')} — {ev.get('step', '')}"
                spinner.set_title(title.strip(" —"))
                log_line(master, f"nxnative stage: {title}")
            elif kind == "stage_end":
                log_line(master, f"nxnative stage_end ok={ev.get('ok')}: {ev.get('summary', '')}")
            elif kind == "say":
                log_line(master, f"nxnative: {str(ev.get('msg', ''))[:300]}")
    rc = proc.wait()
    secs = time.monotonic() - t0
    if rc != 0:
        tail = file_tail(phase_log)
        log_line(master, f"phase nxnative FAILED ({secs:.1f}s, exit {rc})")
        raise InstallError("nxnative", phase_log, tail)
    log_line(master, f"phase nxnative ok ({secs:.1f}s): {app_path}")
    return ({"name": "nxnative", "seconds": round(secs, 1), "status": "ok"},
            app_path)


def sign_app(app_path, master, phase_log, spinner):
    """Fase 3: plist (Game Mode on, controller support, games category),
    ad-hoc re-sign, verify. The bundle stays sealed and read-only."""
    t0 = time.monotonic()
    info = os.path.join(app_path, "Contents", "Info.plist")
    if not os.path.isfile(info):
        raise InstallError("sign-app", phase_log, f"Info.plist missing: {info}")
    if shutil.which("plutil") is None or shutil.which("codesign") is None:
        raise InstallError("sign-app", phase_log,
                           "plutil/codesign not found (install Xcode command line tools)")
    spinner.set_title("signing .app (Game Mode, controller support)")
    edits = [
        (["plutil", "-replace", "LSSupportsGameMode", "-bool", "YES", info], "Game Mode on"),
        (["plutil", "-replace", "GCSupportsControllerUserInteraction", "-bool", "YES", info],
         "controller support on"),
        (["plutil", "-replace", "LSApplicationCategoryType", "-string",
          "public.app-category.adventure-games", info], "games category"),
    ]
    with open(phase_log, "a", encoding="utf-8", errors="replace") as f:
        for cmd, what in edits:
            log_line(master, f"sign-app: {what}")
            r = subprocess.run(cmd, capture_output=True, text=True)
            f.write(f"$ {' '.join(cmd)}\n{r.stdout}{r.stderr}")
            if r.returncode != 0:
                raise InstallError("sign-app", phase_log, f"{what} failed: {r.stderr[-2000:]}")
        for cmd in (["codesign", "-f", "-s", "-", app_path],
                    ["codesign", "--verify", "--strict", app_path]):
            r = subprocess.run(cmd, capture_output=True, text=True)
            f.write(f"$ {' '.join(cmd)}\n{r.stdout}{r.stderr}")
            if r.returncode != 0:
                raise InstallError("sign-app", phase_log,
                                   f"{' '.join(cmd[:3])} failed: {r.stderr[-2000:]}")
    secs = time.monotonic() - t0
    log_line(master, f"phase sign-app ok ({secs:.1f}s)")
    return {"name": "sign-app", "seconds": round(secs, 1), "status": "ok"}


def run_install(cfg, spinner_enabled):
    """Validate -> prepare <I> -> nxnative -> sign. Returns exit code."""
    run_stamp = stamp()
    logs = logs_dir()
    master = os.path.join(logs, f"install-{run_stamp}.log")
    phase_log = lambda name: os.path.join(logs, f"install-{run_stamp}-{name}.log")  # noqa: E731

    record = {"stamp": run_stamp, "title_id": cfg["title_id"], "platform": cfg["platform"],
              "app_name": cfg["app_name"], "install_dir": cfg["install_dir"],
              "inputs": {k: file_info(cfg[k]) for k in ("base", "update", "keys")},
              "nxnative_commit": nxnative_commit(os.path.join(here_root(), "core", "nxnative")),
              "master_log": master, "phases": [], "result": "error", "error": None,
              "app_path": None}
    log_line(master, f"install start: title {cfg['title_id']} platform {cfg['platform']} "
                     f"inputs base={cfg['base']} update={cfg['update']} keys={cfg['keys']} "
                     f"install_dir={cfg['install_dir']}")

    spinner = Spinner(spinner_enabled)
    errors = validate(cfg)
    for e in errors:
        log_line(master, f"validate error: {e}")
    if errors:
        record["error"] = "; ".join(errors)
        record["receipt"] = write_receipt(cfg["install_dir"], record)
        for e in errors:
            print(f"error: {e}", file=sys.stderr)
        print(f"master log: {master}", file=sys.stderr)
        return 2
    for note in warnings(cfg):
        log_line(master, f"validate warning: {note}")
        print(f"warning: {note}", file=sys.stderr)

    try:
        record["phases"].append(phase_prepare_dirs(cfg, master, phase_log("prepare")))

        stage = stage_game_files(cfg, master)
        spinner.set_title("starting nxnative")
        spinner.start()
        try:
            info, app_path = run_nxnative(cfg, stage, master, phase_log("nxnative"), spinner)
        finally:
            spinner.stop()
        record["phases"].append(info)
        record["app_path"] = app_path
        print(f"packed: {app_path}")

        spinner.set_title("signing .app")
        spinner.start()
        try:
            record["phases"].append(sign_app(app_path, master, phase_log("sign"), spinner))
        finally:
            spinner.stop()
        print("signed: Game Mode on, controller support on, bundle sealed")

        record["result"] = "ok"
        record["receipt"] = write_receipt(cfg["install_dir"], record)
        log_line(master, f"install ok: receipt {record['receipt']}")
        print(f"done: {app_path}")
        print(f"master log: {master}")
        print(f"receipt: {record['receipt']}")
        return 0
    except InstallError as e:
        try:
            spinner.stop()
        except Exception:
            pass
        record["error"] = f"{e.phase}: see {e.log_path}"
        try:
            record["receipt"] = write_receipt(cfg["install_dir"], record)
        except OSError:
            pass
        log_line(master, f"install FAILED at {e.phase}")
        print(f"error: {e.phase} failed.", file=sys.stderr)
        if e.tail:
            print(e.tail, file=sys.stderr)
        print(f"phase log: {e.log_path}", file=sys.stderr)
        print(f"master log: {master}", file=sys.stderr)
        return 1


def main(argv=None):
    args = parse_args(argv)
    cfg = {
        "base": args.base,
        "update": args.update,
        "keys": args.keys,
        "install_dir": args.install_dir,
        "title_id": args.title_id,
        "platform": args.platform,
        "app_name": args.app_name,
    }
    missing = [k for k in ("base", "update", "keys", "install_dir") if not cfg[k]]
    if missing:
        if not sys.stdin.isatty():
            print(f"missing required inputs: {', '.join(missing)} (no TTY: pass every flag)",
                  file=sys.stderr)
            return 2
        cfg = prompt_missing(cfg)

    for k in ("base", "update", "keys", "install_dir"):
        if cfg[k]:
            cfg[k] = os.path.abspath(os.path.expanduser(cfg[k]))

    if args.print_config:
        errors = validate(cfg)
        if errors:
            for e in errors:
                print(f"error: {e}", file=sys.stderr)
            return 2
        print(json.dumps(cfg, indent=2))
        return 0

    if args.validate_only:
        errors = validate(cfg)
        for e in errors:
            print(f"error: {e}", file=sys.stderr)
        for note in warnings(cfg):
            print(f"warning: {note}", file=sys.stderr)
        if errors:
            return 2
        print(f"game:        Breath of the Wild ({cfg['title_id']})")
        print(f"base:        {cfg['base']}")
        print(f"update:      {cfg['update']}")
        print(f"keys:        {cfg['keys']}")
        print(f"Install Dir: {cfg['install_dir']} (<I>)")
        print(f"platform:    {cfg['platform']}")
        print("inputs valid.")
        return 0

    return run_install(cfg, spinner_enabled=sys.stdout.isatty() and not args.no_spinner)


if __name__ == "__main__":
    raise SystemExit(main())
