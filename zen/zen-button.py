#!/usr/bin/env python3
"""zen-button — the leave-zen affordance: one button, and nothing around it.

This replaces a yad dialog. yad always builds a dialog: a content area, a
button box, and its own borders, so the smallest honest "just a button" still
came out as a panel with a button floating in it. Here the window *is* the
button, which on a 1366x768 screen is the difference between a discreet tab in
the corner and a widget.

Exits 0 when the button is pressed, 1 if the window goes away any other way,
so the caller can tell "user asked to leave" from "helper died".
"""

import argparse
import sys

import gi

gi.require_version("Gtk", "3.0")
# Gdk is pinned explicitly: gtk4 is installed alongside gtk3 on this machine, so
# an unversioned `from gi.repository import Gdk` loads Gdk 4.0 and then clashes
# with the Gtk 3.0 pulled in beside it.
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

# Matched by zen's --skip-class so the fullscreen watcher leaves this window
# alone; without it the way out of zen gets stretched over the app it sits on.
WM_CLASS = "ZenExitButton"

CSS = """
window { background-color: transparent; }
button {
    background-image: none;
    background-color: rgba(24, 24, 27, 0.90);
    color: #f2f2f2;
    border: 1px solid rgba(255, 255, 255, 0.20);
    border-radius: 5px;
    padding: 0;
    margin: 0;
    font-size: 11px;
    text-shadow: none;
}
button:hover  { background-color: rgba(178, 45, 45, 0.95); }
button:active { background-color: rgba(148, 32, 32, 0.98); }
"""

# Without a compositor an RGBA visual is unavailable and the alpha in the CSS
# above renders as black around the rounded corners, so the button goes opaque
# and square instead.
CSS_OPAQUE = """
window { background-color: #18181b; }
button {
    background-image: none;
    background-color: #18181b;
    color: #f2f2f2;
    border: 1px solid #4a4a4f;
    border-radius: 0;
    padding: 0;
    margin: 0;
    font-size: 11px;
    text-shadow: none;
}
button:hover  { background-color: #b22d2d; }
button:active { background-color: #942020; }
"""


def corner(width, height, margin):
    """Bottom-right of the primary monitor.

    Geometry, not workarea: zen has already quit the panel, so the strut that
    reserves space for it is gone by the time this runs, and on the run where
    it is not gone yet the workarea would park the button 27px too high.
    """
    display = Gdk.Display.get_default()
    monitor = display.get_primary_monitor() or display.get_monitor(0)
    geo = monitor.get_geometry()
    return (
        geo.x + geo.width - width - margin,
        geo.y + geo.height - height - margin,
    )


def build(label="Exit Zen", width=88, height=28, margin=10):
    """The window, its button, and the mutable exit status they share.

    Split out from main() so the click can be exercised without a real pointer:
    XTest events do not reliably reach a GTK button from outside the process,
    and "the button actually reports that it was pressed" is the one thing in
    zen that has no second way in besides the <Super>Z hotkey.
    """
    GLib.set_prgname("zen-button")
    try:
        Gtk.init([])
    except TypeError:
        Gtk.init()
    # res_class in WM_CLASS, which is what Wnck reports as the class group name.
    Gdk.set_program_class(WM_CLASS)

    win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
    win.set_title("Exit Zen")
    win.set_decorated(False)
    win.set_resizable(False)
    win.set_keep_above(True)
    win.set_skip_taskbar_hint(True)
    win.set_skip_pager_hint(True)
    win.stick()
    # UTILITY is a second, independent reason the fullscreen watcher skips this
    # window: it only ever fullscreens NORMAL ones.
    win.set_type_hint(Gdk.WindowTypeHint.UTILITY)
    win.set_size_request(width, height)
    win.set_default_size(width, height)

    screen = Gdk.Screen.get_default()
    composited = screen.is_composited()
    if composited:
        visual = screen.get_rgba_visual()
        if visual:
            win.set_visual(visual)
        else:
            composited = False
    win.set_app_paintable(composited)

    provider = Gtk.CssProvider()
    provider.load_from_data((CSS if composited else CSS_OPAQUE).encode())
    Gtk.StyleContext.add_provider_for_screen(
        screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )

    button = Gtk.Button(label=label)
    button.set_relief(Gtk.ReliefStyle.NONE)
    button.set_can_focus(False)
    win.add(button)

    status = {"code": 1}

    def leave(*_):
        status["code"] = 0
        Gtk.main_quit()

    button.connect("clicked", leave)
    win.connect("destroy", Gtk.main_quit)
    # Moving before show avoids the frame appearing centred and then jumping.
    win.move(*corner(width, height, margin))
    return win, button, status


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--label", default="Exit Zen")
    ap.add_argument("--width", type=int, default=88)
    ap.add_argument("--height", type=int, default=28)
    ap.add_argument("--margin", type=int, default=10)
    args = ap.parse_args()

    win, _button, status = build(args.label, args.width, args.height, args.margin)
    win.show_all()
    win.move(*corner(args.width, args.height, args.margin))

    Gtk.main()
    return status["code"]


if __name__ == "__main__":
    sys.exit(main())
