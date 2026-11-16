#!/usr/bin/env python3
"""swx-native wrapper (backend).

Collects the minimal inputs of a game install, validates them, then hands the
resolved config to the nxnative core. Designed as a backend: a future graphical
UI can import resolve/validate or drive this module as a subprocess
(--print-config emits the resolved config as JSON).

Minimal inputs:
  --base         base game file (.nsp)
  --update       update file, BOTW 1.9.0 (.nsp)
  --keys         prod.keys
  --install-dir  Install Dir (<I>): folder the game is installed/built into

When any of them is missing and stdin is a TTY, the wrapper asks interactively.
Without a TTY it fails with usage instead (pipelines must pass every flag).
"""
import argparse
import json
import os
import sys

TITLE_ID = "01007EF00011E000"
EXPECTED_UPDATE_HINTS = ("1.9.0", "v1114112", "1114112")
GAME_EXTS = (".nsp", ".xci", ".nsz", ".xcz")

ENV = {
    "base": "SWX_BASE_NSP",
    "update": "SWX_UPDATE_NSP",
    "keys": "SWX_PROD_KEYS",
    "install_dir": "SWX_INSTALL_DIR",
}


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
    p.add_argument("--print-config", action="store_true",
                   help="print the resolved config as JSON and exit")
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


def main(argv=None):
    args = parse_args(argv)
    cfg = {
        "base": args.base,
        "update": args.update,
        "keys": args.keys,
        "install_dir": args.install_dir,
        "title_id": args.title_id,
        "platform": args.platform,
    }
    missing = [k for k in ("base", "update", "keys", "install_dir") if not cfg[k]]
    if missing:
        if not sys.stdin.isatty():
            print(f"missing required inputs: {', '.join(missing)} (no TTY: pass every flag)",
                  file=sys.stderr)
            return 2
        cfg = prompt_missing(cfg)

    errors = validate(cfg)
    if errors:
        for e in errors:
            print(f"error: {e}", file=sys.stderr)
        return 2
    for note in warnings(cfg):
        print(f"warning: {note}", file=sys.stderr)

    # Resolve for display/backend use (absolute paths; key contents never shown).
    resolved = {**cfg}
    for k in ("base", "update", "keys", "install_dir"):
        resolved[k] = os.path.abspath(os.path.expanduser(resolved[k]))

    if args.print_config:
        print(json.dumps(resolved, indent=2))
        return 0

    print(f"game:        Breath of the Wild ({resolved['title_id']})")
    print(f"base:        {resolved['base']}")
    print(f"update:      {resolved['update']}")
    print(f"keys:        {resolved['keys']}")
    print(f"Install Dir: {resolved['install_dir']} (<I>)")
    print(f"platform:    {resolved['platform']}")
    # TODO: invoke core/nxnative build (scripts/build_ipa_from_nsp.py
    # --platform mac-app) with this config and track progress.
    print("TODO: build step not implemented yet (skeleton validates inputs only).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
