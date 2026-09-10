#!/usr/bin/env python3
"""zen-wm — window listing and control for zen mode, via libwnck.

Built on libwnck3 + python-gobject because both are already installed (the
brightness tray uses the same stack), so zen adds no packages to a machine
where RAM is tight. wmctrl and xdotool would each be a new dependency for
work Wnck already does natively.

Subcommands:
  list                      TSV of closable windows: pid, class, title
  close-all [--keep-pid P]  politely close every closable window
  fullscreen-new            fullscreen windows that appear from now on
  unfullscreen-all          drop every window back out of fullscreen
"""

import argparse
import sys
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Wnck", "3.0")
from gi.repository import Gtk, Wnck  # noqa: E402

# Windows zen will close. Docks, desktop backgrounds, splash screens and menus
# are either infrastructure or transient: the panel and xfdesktop are quit
# through their own --quit, and closing a menu is meaningless.
CLOSABLE = (Wnck.WindowType.NORMAL, Wnck.WindowType.DIALOG)
# Only real top-level windows get fullscreened; a dialog stretched to the whole
# screen is unusable.
FULLSCREENABLE = (Wnck.WindowType.NORMAL,)


def get_screen():
    """A Wnck screen with its window list already populated.

    force_update() is what makes Wnck usable from a short-lived script: the
    normal contract is that the window list only fills in once a GTK main loop
    has been running.
    """
    try:
        Gtk.init([])
    except TypeError:
        Gtk.init()
    screen = Wnck.Screen.get_default()
    screen.force_update()
    return screen


def pump(seconds):
    """Run the GTK loop briefly so Wnck sees the window manager's replies.

    force_update() re-reads state but does not drain the event queue, so
    without this the window list goes stale mid-wait.
    """
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        time.sleep(0.05)


def describe(win):
    """One TSV row: pid, application class, title."""
    klass = win.get_class_group_name() or win.get_name() or "?"
    return f"{win.get_pid()}\t{klass}\t{win.get_name() or ''}"


def closable(screen, keep):
    return [
        w
        for w in screen.get_windows()
        if w.get_window_type() in CLOSABLE and w.get_pid() not in keep
    ]


def cmd_list(args):
    screen = get_screen()
    for win in closable(screen, set(args.keep_pid or [])):
        print(describe(win))
    return 0


def cmd_close_all(args):
    screen = get_screen()
    keep = set(args.keep_pid or [])
    now = int(time.time())

    for win in closable(screen, keep):
        # close() sends WM_DELETE_WINDOW, the same thing the titlebar X does,
        # so an app with unsaved work gets to put up its save prompt instead
        # of losing the file. Anything still alive at the deadline is reported
        # rather than killed.
        win.close(now)

    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        pump(0.3)
        screen.force_update()
        if not closable(screen, keep):
            break

    left = closable(screen, keep)
    for win in left:
        print(describe(win))
    return 1 if left else 0


def cmd_fullscreen_new(args):
    """Fullscreen every new top-level window for a while.

    Matching on "new since we started" rather than on the PID we spawned is
    deliberate: launchers like firefox and many .desktop wrappers fork, so the
    window's PID often has nothing to do with the process zen started.
    """
    screen = get_screen()
    baseline = {w.get_xid() for w in screen.get_windows()}
    skip = set(args.skip_class or [])
    done = set()

    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        pump(0.4)
        screen.force_update()
        for win in screen.get_windows():
            xid = win.get_xid()
            if xid in baseline or xid in done:
                continue
            if win.get_window_type() not in FULLSCREENABLE:
                continue
            # zen's own exit button is a new top-level window like any other,
            # and blowing it up to fill the screen would hide the app behind
            # the way out of zen.
            if win.get_class_group_name() in skip:
                continue
            win.set_fullscreen(True)
            # Remembered so a window the user deliberately un-fullscreens is
            # not immediately grabbed back.
            done.add(xid)
            print(describe(win))
            sys.stdout.flush()
        if args.expect and len(done) >= args.expect:
            break

    return 0 if done else 1


def cmd_unfullscreen_all(args):
    screen = get_screen()
    for win in screen.get_windows():
        if win.is_fullscreen():
            win.set_fullscreen(False)
    pump(0.3)
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
    p.add_argument("--skip-class", action="append")
    p.set_defaults(func=cmd_fullscreen_new)

    p = sub.add_parser("unfullscreen-all")
    p.set_defaults(func=cmd_unfullscreen_all)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
