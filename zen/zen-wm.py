#!/usr/bin/env python3
"""zen-wm — window listing and control for zen mode, via niri's IPC.

This replaces a libwnck implementation. libwnck reads EWMH properties off the
X root window, and Wayland has neither: there is no root window, no
_NET_CLIENT_LIST to enumerate and no _NET_WM_STATE to set. Every compositor
answers these questions on its own IPC or not at all, so the port is not a
change of library but a change of authority — niri is now the only thing that
knows what windows exist.

What that costs, and the one place it shows:

  niri reports is_floating, is_focused and is_urgent for a window, but not
  whether it is fullscreen, and `fullscreen-window` is a toggle rather than a
  set. So "put everything back" cannot be answered by asking. The ids this
  fullscreened are therefore written to a state file as they happen, and
  unfullscreen-all toggles exactly those back. That also makes it correct
  across a zen that died and was restarted, which the libwnck version got for
  free by reading the live state and this one has to earn.

What it gains: closing is by window id rather than by a pid we hope maps to
one window, and niri's ids are stable for the life of the window.

Subcommands:
  list                      TSV of closable windows: pid, app id, title, id
  close-all [--keep-pid P]  politely close every closable window
  fullscreen-new            fullscreen windows that appear from now on
  unfullscreen-all          drop every window this fullscreened back out
"""

import argparse
import json
import os
import subprocess
import sys
import time

STATE_DIR = os.path.join(
    os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")), "zen"
)
FULLSCREENED = os.path.join(STATE_DIR, "fullscreened")


def niri(*args, parse=False):
    """Run `niri msg ...`, returning stdout or parsed JSON.

    Never raises on a failed call: zen runs this while it is tearing the
    desktop down, and a compositor that declines one request is not a reason
    to abandon the session half-stripped.
    """
    argv = ["niri", "msg"]
    if parse:
        argv.append("--json")
    argv.extend(args)
    try:
        out = subprocess.run(
            argv, capture_output=True, text=True, timeout=5, check=True
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return None if not parse else []
    if not parse:
        return out
    try:
        return json.loads(out)
    except ValueError:
        return []


def windows():
    return niri("windows", parse=True) or []


def closable(keep_pids, skip_app_ids=()):
    """Windows zen will close.

    niri has no window *type*: layer-shell surfaces (the bar, notifications)
    are a separate list it does not return here, so the dock/desktop/splash
    filtering the libwnck version needed is done for us. Everything `windows`
    returns is a real toplevel and a fair target.
    """
    out = []
    for win in windows():
        if win.get("pid") in keep_pids:
            continue
        if win.get("app_id") in skip_app_ids:
            continue
        out.append(win)
    return out


def describe(win):
    """One TSV row: pid, app id, title, window id."""
    app = win.get("app_id") or win.get("title") or "?"
    return f"{win.get('pid')}\t{app}\t{win.get('title') or ''}\t{win.get('id')}"


def read_ids(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return [int(line) for line in fh if line.strip().isdigit()]
    except (OSError, ValueError):
        return []


def append_id(path, wid):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"{wid}\n")


def cmd_list(args):
    for win in closable(set(args.keep_pid or [])):
        print(describe(win))
    return 0


def cmd_close_all(args):
    keep = set(args.keep_pid or [])

    for win in closable(keep):
        # close-window asks the client to close, the same thing the titlebar X
        # does, so an app with unsaved work gets to put up its save prompt
        # instead of losing the file. Anything still alive at the deadline is
        # reported rather than killed.
        niri("action", "close-window", "--id", str(win["id"]))

    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        time.sleep(0.3)
        if not closable(keep):
            break

    left = closable(keep)
    for win in left:
        print(describe(win))
    return 1 if left else 0


def cmd_fullscreen_new(args):
    """Fullscreen every new window for a while.

    Matching on "new since we started" rather than on the pid we spawned is
    deliberate and survives the port unchanged: launchers like firefox and
    many .desktop wrappers fork, so the window's pid often has nothing to do
    with the process zen started.
    """
    baseline = {w["id"] for w in windows()}
    skip = set(args.skip_class or [])
    done = set()

    # Truncated rather than appended to: this is a fresh zen session, and ids
    # left over from a previous one would be toggled back on a window that has
    # since been reused.
    os.makedirs(STATE_DIR, exist_ok=True)
    open(FULLSCREENED, "w", encoding="utf-8").close()

    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        time.sleep(0.4)
        for win in windows():
            wid = win["id"]
            if wid in baseline or wid in done:
                continue
            # zen's own exit button is a new window like any other, and blowing
            # it up to fill the screen would hide the way out of zen.
            if win.get("app_id") in skip:
                continue
            niri("action", "fullscreen-window", "--id", str(wid))
            # Remembered so a window the user deliberately un-fullscreens is
            # not immediately grabbed back, and so exit knows what to undo.
            done.add(wid)
            append_id(FULLSCREENED, wid)
            print(describe(win))
            sys.stdout.flush()
        if args.expect and len(done) >= args.expect:
            break

    return 0 if done else 1


def cmd_unfullscreen_all(args):
    """Toggle back exactly the windows this fullscreened.

    Ids that no longer exist are skipped rather than sent anyway: niri would
    ignore them, but a window id can in principle be reused, and toggling a
    window the user fullscreened themselves is the one wrong outcome here.
    """
    live = {w["id"] for w in windows()}
    for wid in read_ids(FULLSCREENED):
        if wid in live:
            niri("action", "fullscreen-window", "--id", str(wid))
    try:
        os.remove(FULLSCREENED)
    except OSError:
        pass
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list")
    p.add_argument("--keep-pid", type=int, action="append")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("close-all")
    p.add_argument("--keep-pid", type=int, action="append")
    p.add_argument("--timeout", type=float, default=8.0)
    p.set_defaults(func=cmd_close_all)

    p = sub.add_parser("fullscreen-new")
    p.add_argument("--timeout", type=float, default=30.0)
    p.add_argument("--expect", type=int, default=0)
    # Still --skip-class, matched against app_id: the Wayland name for what
    # X11 called WM_CLASS. Kept under the old flag so zen's call site did not
    # have to change.
    p.add_argument("--skip-class", action="append")
    p.set_defaults(func=cmd_fullscreen_new)

    p = sub.add_parser("unfullscreen-all")
    p.set_defaults(func=cmd_unfullscreen_all)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
