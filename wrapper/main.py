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
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime

TITLE_ID = "01007EF00011E000"
SHORT_NAME = "Breath of The Wild"
EXPECTED_UPDATE_HINTS = ("1.9.0", "v1114112", "1114112")
GAME_EXTS = (".nsp", ".xci", ".nsz", ".xcz")
LAYOUT_VERSION = 1
LAYOUT_DIRS = ("game", "save", "shaders")
SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

ENV = {
    "base": "SWX_BASE_NSP",
    "update": "SWX_UPDATE_NSP",
    "keys": "SWX_PROD_KEYS",
    "install_dir": "SWX_INSTALL_DIR",
    "icloud_drive_save": "SWX_ICLOUD_DRIVE_SAVE",
}


class InstallError(Exception):
    """A phase failed. Carries what the user needs: phase, log, tail."""

    def __init__(self, phase, log_path, tail):
        super().__init__(f"{phase} failed (log: {log_path})")
        self.phase = phase
        self.log_path = log_path
        self.tail = tail


class _Interrupted(Exception):
    """User pressed Ctrl+C; the child (if any) was already terminated."""


def _terminate(proc):
    """Terminate a running child quietly; escalate to kill if it lingers."""
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
    except Exception:
        pass
    try:
        if proc.stdout:
            proc.stdout.close()
    except Exception:
        pass


def here_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _env_flag(name):
    """Env tri-state: true/false/None (unset)."""
    v = os.environ.get(name, "").lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return None


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
    p.add_argument("--mng-list", default=os.environ.get("SWX_MNG_LIST", ""),
                   help="shared shader list for the derived profile (default: refs checkout; missing = stock profile)")
    p.add_argument("--icloud-drive-save", dest="icloud_drive_save", action="store_true",
                   default=_env_flag(ENV["icloud_drive_save"]),
                   help="keep saves on iCloud Drive (<I>/save becomes a symlink)")
    p.add_argument("--no-icloud-drive-save", dest="icloud_drive_save", action="store_false",
                   help="keep saves local, never ask (pairs with --open for unattended runs)")
    p.add_argument("--open", dest="open_game", action="store_true",
                   default=_env_flag("SWX_OPEN"),
                   help="open the built game when the install finishes (no prompt)")
    p.add_argument("--no-open", dest="open_game", action="store_false",
                   help="do not open the game at the end (no prompt)")
    p.add_argument("--print-config", action="store_true",
                   help="print the resolved config as JSON and exit")
    p.add_argument("--validate-only", action="store_true",
                   help="validate inputs, then exit before calling nxnative")
    p.add_argument("--no-spinner", action="store_true",
                   help="never show the braille spinner (one line per event instead)")
    return p.parse_args(argv)


def normalize_path(raw):
    """Clean a user-supplied path.

    Strips whitespace and surrounding quotes (users often paste quoted
    paths), and un-escapes shell-style drag-and-drop paths (``Home\\ Data``)
    when — and only when — the raw form does not exist but the unescaped one
    does, so genuine backslashes in filenames are never mangled.
    """
    if not raw:
        return raw
    p = raw.strip()
    if len(p) >= 2 and p[0] == p[-1] and p[0] in ("'", '"'):
        p = p[1:-1].strip()
    p = os.path.expanduser(p)
    if not os.path.exists(p) and "\\" in p:
        unescaped = re.sub(r"\\(.)", r"\1", p)
        if os.path.exists(unescaped):
            p = unescaped
    return p


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
            # Clear the whole line first: a shorter title must not leave the
            # tail of a longer one behind (\r alone only rewinds the cursor).
            sys.stdout.write(f"\r\x1b[2K{frame} {title} ")
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
    """Fase 1: create <I> layout (game/save/shaders + marker)."""
    t0 = time.monotonic()
    install_dir = cfg["install_dir"]
    for d in ("game", "shaders"):
        os.makedirs(os.path.join(install_dir, d), exist_ok=True)
    save_target = prepare_save_dir(cfg, master, phase_log)
    marker = os.path.join(install_dir, ".swx-layout")
    with open(marker, "w", encoding="utf-8") as f:
        f.write(f"layout_version={LAYOUT_VERSION}\n")
    secs = time.monotonic() - t0
    log_line(master, f"phase prepare-dirs ok ({secs:.1f}s): game, shaders, "
                     f"save ({'iCloud Drive' if cfg.get('icloud_drive_save') and save_target else 'local'})")
    with open(phase_log, "a", encoding="utf-8") as f:
        f.write(f"created game, shaders under {install_dir}\n")
    return ({"name": "prepare-dirs", "seconds": round(secs, 1), "status": "ok"},
            save_target)


def icloud_saves_root():
    """iCloud Drive root for saves: ~/.../Games/<Short Name>/Saves."""
    base = os.path.expanduser("~/Library/Mobile Documents")
    return (os.path.join(base, "com~apple~CloudDocs", "Games", SHORT_NAME, "Saves")
            if os.path.isdir(base) else None)


def icloud_saves_dir():
    """Backward-compatible alias (root without game id)."""
    return icloud_saves_root()


def save_root_for(cfg):
    """Where game-id save folders live: iCloud root or <I>/save."""
    if bool(cfg.get("icloud_drive_save")):
        root = icloud_saves_root()
        if root is not None:
            return root
    return os.path.join(cfg["install_dir"], "save")


def dm_save_for(cfg):
    """This game's save folder: <root>/<title id> (never <I> root itself,
    so the <id>.work clone stays out of sight one level down)."""
    return os.path.join(save_root_for(cfg), cfg["title_id"])


def prepare_save_dir(cfg, master, phase_log):
    """<I>/save as a real folder, or as a symlink to iCloud Drive.

    Returns the resolved target (or None for a local folder). Migrates
    existing local saves into the iCloud target instead of dropping them.
    """
    save = os.path.join(cfg["install_dir"], "save")
    target = None
    use_icloud = bool(cfg.get("icloud_drive_save"))
    if use_icloud:
        target = icloud_saves_root()
        if target is None:
            msg = "iCloud Drive not available locally; using local saves."
            log_line(master, f"prepare-dirs: {msg}")
            print(f"warning: {msg}", file=sys.stderr)
            use_icloud = False
        else:
            os.makedirs(target, exist_ok=True)
    with open(phase_log, "a", encoding="utf-8", errors="replace") as f:
        if use_icloud:
            if os.path.islink(save):
                if os.path.realpath(save) == os.path.realpath(target):
                    f.write(f"save already linked to iCloud: {save} -> {target}\n")
                else:
                    os.remove(save)
                    os.symlink(target, save)
                    f.write(f"save re-pointed to iCloud: {save} -> {target}\n")
            elif os.path.isdir(save):
                moved = 0
                for entry in sorted(os.listdir(save)):
                    shutil.move(os.path.join(save, entry), os.path.join(target, entry))
                    moved += 1
                os.rmdir(save)
                os.symlink(target, save)
                f.write(f"save migrated {moved} entries to iCloud, linked: {save} -> {target}\n")
            else:
                os.symlink(target, save)
                f.write(f"save linked to iCloud: {save} -> {target}\n")
            log_line(master, f"prepare-dirs: saves on iCloud Drive ({target})")
        else:
            os.makedirs(save, exist_ok=True)
            f.write(f"save is a local folder: {save}\n")
            log_line(master, "prepare-dirs: saves are local")
    return target


def app_support_dir(title_id):
    """The launcher's per-game folder (created by the app on first launch)."""
    return os.path.expanduser(
        os.path.join("~/Library/Application Support/nxnative", title_id))


def _nest_flat_saves(cfg, dm_save, master, phase_log):
    """Move a previous flat layout into the nested <root>/<game id>/ standard.

    The old DM_SAVE pointed at the root itself, so committed saves lived
    directly in it and the work clone sat beside it as <rootname>.work. Move
    everything under the game-id folder (and the old work clone next to it);
    colliding names stay in place with a warning.
    """
    moved, kept = [], []
    save_root = os.path.dirname(dm_save)
    idname = os.path.basename(dm_save)
    candidates = []
    if os.path.isdir(save_root):
        candidates = [(e, os.path.join(dm_save, e))
                      for e in sorted(os.listdir(save_root)) if e != idname]
    parent, workname = os.path.dirname(save_root), os.path.basename(save_root) + ".work"
    if os.path.isdir(os.path.join(parent, workname)):
        candidates.append((workname, dm_save + ".work"))
    for name, dst in candidates:
        src = (os.path.join(save_root, name) if name != workname
               else os.path.join(parent, name))
        if os.path.lexists(dst):
            kept.append(name)
            continue
        shutil.move(src, dst)
        moved.append(name)
    if moved or kept:
        with open(phase_log, "a", encoding="utf-8", errors="replace") as f:
            f.write(f"nested {len(moved)} flat entries under {dm_save}\n")
            for name in kept:
                f.write(f"kept (name collision, left in place): {name}\n")
        log_line(master, f"save-redirect: nested {len(moved)} entries under {dm_save}")
    return moved, kept


def redirect_saves(cfg, master, phase_log):
    """Point the built app at <I>/save via its env.txt override, then move any
    existing saves there and remove the old folder from Application Support.

    Supported hook (launcher.c): the launcher's own DM_SAVE never overwrites
    one already set, and <app support>/env.txt wins. Cache (pso_*), temp and
    env.txt itself stay in Application Support.
    """
    t0 = time.monotonic()
    as_dir = app_support_dir(cfg["title_id"])
    os.makedirs(as_dir, exist_ok=True)
    dm_save = dm_save_for(cfg)
    os.makedirs(dm_save, exist_ok=True)
    nest_moved, nest_kept = _nest_flat_saves(cfg, dm_save, master, phase_log)

    env_path = os.path.join(as_dir, "env.txt")
    kept = []
    if os.path.isfile(env_path):
        with open(env_path, "r", encoding="utf-8", errors="replace") as f:
            kept = [ln for ln in f.read().splitlines()
                    if not ln.strip().startswith(("DM_SAVE=", "DM_PSO_USER=", "DM_PSO_ARCHIVE=", "DM_PSO_PAIRS=",
                                                 "DM_SAVE_ASYNC=", "DM_PSO_USER_ASYNC=",
                                                 "SWX_VFR=", "SWX_VFR_HZ=",
                                                 "SWX_GPU_KEEPALIVE=", "SWX_GPU_KEEPALIVE_YIELD="))]
    # Steady-beat frame pace baseline, pinned every install: SWX_VFR=relaxed
    # (the game's clock follows the host beat; =0 restores the legacy
    # Switch-interval frame step), SWX_VFR_HZ=auto (window refresh up to
    # 120 Hz; 60 with no window). DM_PRESENT_SNAP is not pinned: the compiled
    # default (stock ring) applies unless set by hand for A/B (snap 2/4).
    # DM_NVN_SYNC_LAG is not pinned either and must stay out of the env.
    # DM_SAVE_ASYNC=1 (swapped-out save deleted on
    # a background thread; clone+swap stay synchronous). SWX switches
    # default in code (keep-alive on on macOS; upload split opt-in);
    # DM tuning the user set by hand (MAX_TEX, KEEPALIVE_US, …) passes
    # through untouched via kept.
    shaders_dir = os.path.join(cfg["install_dir"], "shaders")
    os.makedirs(shaders_dir, exist_ok=True)
    pso_user = os.path.join(shaders_dir, "pso_user.tsv")
    # Pipeline archive: nxnative defaults to pso_archive/ beside pso_user.tsv
    # (nvn_mtl.m). Pinned explicitly so the layout holds whatever the default.
    pso_archive = os.path.join(shaders_dir, "pso_archive")
    pso_pairs = os.path.join(shaders_dir, "swx_pairs.tsv")
    with open(env_path, "w", encoding="utf-8") as f:
        for ln in kept:
            f.write(ln + "\n")
        f.write(f"DM_SAVE={dm_save}\n")
        f.write(f"DM_PSO_USER={pso_user}\n")
        f.write(f"DM_PSO_ARCHIVE={pso_archive}\n")
        if os.path.isfile(pso_pairs):
            f.write(f"DM_PSO_PAIRS={pso_pairs}\n")
        f.write("SWX_VFR=relaxed\n")
        f.write("SWX_VFR_HZ=auto\n")
        f.write("DM_SAVE_ASYNC=1\n")
        f.write("DM_PSO_USER_ASYNC=1\n")
        f.write("SWX_GPU_KEEPALIVE=1\n")
        f.write("SWX_GPU_KEEPALIVE_YIELD=1\n")
    log_line(master, f"save-redirect: wrote DM_SAVE, DM_PSO_USER, DM_PSO_ARCHIVE"
                     f"{', DM_PSO_PAIRS' if os.path.isfile(pso_pairs) else ''}, "
                     f"SWX_VFR=relaxed, SWX_VFR_HZ=auto, DM_SAVE_ASYNC=1, DM_PSO_USER_ASYNC=1, SWX_GPU_KEEPALIVE=1, SWX_GPU_KEEPALIVE_YIELD=1 to {env_path}")

    moved, kept_back = [], []
    old_save = os.path.join(as_dir, "save")
    if os.path.isdir(old_save) and not os.path.islink(old_save):
        for entry in sorted(os.listdir(old_save)):
            src, dst = os.path.join(old_save, entry), os.path.join(dm_save, entry)
            if os.path.lexists(dst):
                kept_back.append(entry)
                continue
            shutil.move(src, dst)
            moved.append(entry)
        with open(phase_log, "a", encoding="utf-8", errors="replace") as f:
            f.write(f"moved {len(moved)} entries from {old_save} to {dm_save}\n")
            for entry in kept_back:
                f.write(f"kept (name collision, left in place): {entry}\n")
    removed_work = False
    if os.path.isdir(old_save) and not os.listdir(old_save):
        os.rmdir(old_save)
        work = os.path.join(as_dir, "save.work")
        if os.path.isdir(work) and not os.path.islink(work):
            shutil.rmtree(work)
            removed_work = True
        log_line(master, "save-redirect: removed old save folder from Application Support")
    pso_moved = pso_merged = 0
    stray = os.path.join(cfg["install_dir"], "pso_user.tsv")
    if os.path.isfile(stray) and os.path.realpath(stray) != os.path.realpath(pso_user):
        seen = set()
        if os.path.isfile(pso_user):
            with open(pso_user, "r", encoding="utf-8", errors="replace") as f:
                seen = {ln for ln in f.read().splitlines() if ln and not ln.startswith("#")}
        with open(stray, "r", encoding="utf-8", errors="replace") as f:
            new_rows = [ln for ln in f.read().splitlines()
                        if ln and not ln.startswith("#") and ln not in seen]
        with open(pso_user, "a", encoding="utf-8") as f:
            for ln in new_rows:
                f.write(ln + "\n")
        pso_moved, pso_merged = len(new_rows), len(new_rows)
        os.remove(stray)
        log_line(master, f"save-redirect: merged {pso_moved} rows into {pso_user}")
    arch_moved, arch_kept = [], []
    stray_arch = os.path.join(cfg["install_dir"], "pso_archive")
    if os.path.isdir(stray_arch) and os.path.realpath(stray_arch) != os.path.realpath(pso_archive):
        os.makedirs(pso_archive, exist_ok=True)
        for entry in sorted(os.listdir(stray_arch)):
            src, dst = os.path.join(stray_arch, entry), os.path.join(pso_archive, entry)
            if os.path.lexists(dst):
                arch_kept.append(entry)
                continue
            shutil.move(src, dst)
            arch_moved.append(entry)
        with open(phase_log, "a", encoding="utf-8", errors="replace") as f:
            f.write(f"moved {len(arch_moved)} archive files to {pso_archive}\n")
            for entry in arch_kept:
                f.write(f"kept (name collision, left in place): {entry}\n")
        if not os.listdir(stray_arch):
            os.rmdir(stray_arch)
            log_line(master, "save-redirect: removed stray pso_archive from <I> root")
    secs = time.monotonic() - t0
    info = {"name": "save-redirect", "seconds": round(secs, 1), "status": "ok",
            "dm_save": dm_save, "moved": moved, "kept": kept_back,
            "removed_work_clone": removed_work,
            "dm_pso_user": pso_user, "pso_rows_merged": pso_merged,
            "dm_pso_archive": pso_archive, "archive_moved": arch_moved,
            "archive_kept": arch_kept,
            "dm_pso_pairs": pso_pairs if os.path.isfile(pso_pairs) else None,
            "nested_moved": nest_moved, "nested_kept": nest_kept}
    record_note = f"phase save-redirect ok ({secs:.1f}s): {len(moved)} moved"
    log_line(master, record_note)
    return info


def derive_community_profile(cfg, master, phase_log):
    """Community coverage into the build (pairs file + derived manifest).

    - swx_pairs.tsv (profile pairs + new community pairs) -> DM_PSO_PAIRS:
      prebuild/hybrid coverage without touching validated tables.
    - Derived profile overlay (full copy minus patches.py, manifest extended
      with converted R rows) -> DM_GAMES_DIR + DM_GAMES_FIRST: warm-up
      coverage. Split-validated tables stay byte-identical, so checks pass.
    Returns (pairs_path, out_id), or None when the shared list is unavailable.
    """
    try:
        from shader_derive import write_pairs_file, build_derived, DEFAULT_MNG_LIST
    except ImportError as e:
        log_line(master, f"derive-profile: skipped ({e})")
        return None
    mng_list = cfg.get("mng_list") or DEFAULT_MNG_LIST
    if not os.path.isfile(mng_list):
        msg = f"shared shader list not found: {mng_list}; continuing stock"
        log_line(master, f"derive-profile: {msg}")
        print(f"warning: {msg}", file=sys.stderr)
        return None
    profile_dir = os.path.join(here_root(), "core", "nxnative", "dm", "games", cfg["title_id"])
    if not os.path.isdir(profile_dir):
        log_line(master, f"derive-profile: no profile for {cfg['title_id']}; continuing stock")
        return None
    pairs_path = os.path.join(cfg["install_dir"], "shaders", "swx_pairs.tsv")
    pairs_stats = write_pairs_file(profile_dir, mng_list, pairs_path)
    out_id = os.path.join(cfg["install_dir"], "game", "derived", cfg["title_id"])
    mani_stats = build_derived(profile_dir, mng_list, out_id)
    log_line(master, f"derive-profile: pairs_added={pairs_stats['added']} "
                     f"manifest_rows_added={mani_stats['manifest_rows_added']} -> {out_id}")
    with open(phase_log, "a", encoding="utf-8", errors="replace") as f:
        f.write(f"community coverage: pairs {pairs_stats} manifest {mani_stats}\n")
    return pairs_path, out_id


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
    app_path = os.path.join(cfg["install_dir"], cfg["app_name"] + ".app")
    cmd = [sys.executable, script, stage,
           "-k", cfg["keys"],
           "--platform", cfg["platform"],
           "--work-dir", work_dir,
           "-o", app_path,
           "--progress", "json"]
    env = dict(os.environ)
    env["DM_GAME"] = cfg["title_id"]
    derived = derive_community_profile(cfg, master, phase_log)
    if derived is not None:
        pairs_path, out_id = derived
        env["DM_PSO_PAIRS"] = pairs_path
        env["DM_GAMES_DIR"] = os.path.dirname(out_id)
        env["DM_GAMES_FIRST"] = "1"
        log_line(master, f"nxnative: prebuild pairs overlay DM_PSO_PAIRS={pairs_path}")
        log_line(master, f"nxnative: warm-up overlay DM_GAMES_DIR={env['DM_GAMES_DIR']}")
    log_line(master, f"phase nxnative start: {' '.join(cmd)} (DM_GAME={cfg['title_id']})")
    with open(phase_log, "a", encoding="utf-8") as f:
        f.write(f"$ {' '.join(cmd)}\n")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, errors="replace", env=env, bufsize=1)
    try:
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
    except KeyboardInterrupt:
        _terminate(proc)
        raise _Interrupted()
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
              "icloud_drive_save": bool(cfg.get("icloud_drive_save")),
              "save_target": None, "save_redirect": None, "dm_save": None,
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
        prepare_info, save_target = phase_prepare_dirs(cfg, master, phase_log("prepare"))
        record["phases"].append(prepare_info)
        record["save_target"] = save_target

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

        spinner.set_title("redirecting saves to <I>/save")
        spinner.start()
        try:
            record["phases"].append(redirect_saves(cfg, master, phase_log("saves")))
            record["save_redirect"] = record["phases"][-1]
            record["dm_save"] = record["save_redirect"]["dm_save"]
        finally:
            spinner.stop()
        save_disp = (record.get("dm_save")
                     or record["save_target"]
                     or os.path.join(cfg["install_dir"], "save"))
        print(f"saves: {save_disp}")

        record["result"] = "ok"
        record["receipt"] = write_receipt(cfg["install_dir"], record)
        log_line(master, f"install ok: receipt {record['receipt']}")
        print(f"done: {app_path}")
        print(f"master log: {master}")
        print(f"receipt: {record['receipt']}")
        if maybe_open_app(app_path, cfg.get("open_game")):
            print(f"opened: {app_path}")
        return 0
    except (KeyboardInterrupt, _Interrupted):
        try:
            spinner.stop()
        except Exception:
            pass
        record["error"] = "interrupted by user"
        record["result"] = "interrupted"
        try:
            record["receipt"] = write_receipt(cfg["install_dir"], record)
        except OSError:
            pass
        log_line(master, "install INTERRUPTED by user")
        print("interrupted by user; partial outputs kept.", file=sys.stderr)
        print(f"master log: {master}", file=sys.stderr)
        return 130
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


def maybe_open_app(app_path, mode):
    """Open the built game, per mode: True = always, False = never,
    None = ask on a TTY (never without one).

    Never fails the install: a refused open only warns. Skipped without a TTY.
    """
    if mode is False:
        return False
    if mode is None:
        if not sys.stdin.isatty():
            return False
        ans = ask("Open the game now? [Y/n]", "Y")
        if ans.lower() not in ("", "y", "yes"):
            return False
    r = subprocess.run(["open", app_path])
    if r.returncode != 0:
        print(f"warning: could not open {app_path}", file=sys.stderr)
        return False
    return True


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
        "mng_list": args.mng_list,
        "icloud_drive_save": args.icloud_drive_save,
        "open_game": args.open_game,
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
            cfg[k] = os.path.abspath(normalize_path(cfg[k]))

    if (cfg["icloud_drive_save"] is None
            and not args.print_config and not args.validate_only
            and sys.stdin.isatty()):
        ans = ask("Use iCloud Drive for saves? [y/N]", "")
        cfg["icloud_drive_save"] = ans.lower() in ("y", "yes")
    elif cfg["icloud_drive_save"] is None:
        cfg["icloud_drive_save"] = False

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
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        # Soft exit on Ctrl+C (e.g. during interactive prompts): no traceback.
        print("\ninterrupted.", file=sys.stderr)
        raise SystemExit(130)
