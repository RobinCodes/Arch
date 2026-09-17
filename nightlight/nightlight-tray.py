#!/usr/bin/env python3
"""Night light tray icon: what the screen is at, and the two controls for it.

The menu is served over DBusMenu and drawn by noctalia, so it is in noctalia's
theme without this file knowing anything about themes — see ../lib/sni.py. That
covers everything a menu can express:

  Automatic schedule   a checkmark. On, the clock drives the intensity; off, it
                       stays where it was put.
  the steps            radio items at 0/10/.../100, for picking a value without
                       opening anything.
  Fine adjustment      opens ./nightlight-panel.py, because DBusMenu has no
                       slider and a Gtk.Scale does not survive being flattened
                       into menu data.

Picking a step or moving the slider switches to manual, which is what "override
it completely" means: the value stays until the schedule is switched back on.
Turning the checkmark back on hands control to the curve and applies whatever it
wants right now.

THE ICON IS A REAL ICON, NOT A DRAWN ONE

Unlike the battery tray — whose icon is a percentage rendered with cairo,
something no icon theme can contain — this has two states worth showing and
Adwaita already has both: `night-light-symbolic` and
`night-light-disabled-symbolic`. A named icon is what a symbolic-icon bar wants,
because the bar recolours it to match itself; a pixmap arrives as fixed pixels
and stays the colour it was drawn.

The number goes in the tooltip instead, where it does not need to be legible at
16 pixels.

POLLING

Every 30 seconds, which is only there to notice what this process did not do:
nightlight-auto.timer moving the value on its schedule, or a `nightlight` call
from a terminal. Anything done through this tray refreshes immediately and does
not wait for a tick.
"""

import os
import subprocess
import sys
import warnings

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import tray  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# The panel is a sibling script, not a module name Python would find on its own;
# it is imported rather than spawned so the window can tell this process what it
# changed instead of the tray having to poll for it.
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "nightlight_panel", os.path.join(HERE, "nightlight-panel.py")
)
nightlight_panel = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nightlight_panel)

warnings.filterwarnings("ignore", category=DeprecationWarning)

NIGHTLIGHT = os.path.join(HERE, "nightlight")
NIGHTLIGHT_AUTO = os.path.join(HERE, "nightlight-auto")

APP_ID = "nightlight-tray"
ICON_ON = "night-light-symbolic"
ICON_OFF = "night-light-disabled-symbolic"

POLL_INTERVAL_MS = 30000


def steps(peak):
    """The steps the menu offers: every ten, plus the schedule's own peak,
    labelled as such, so the one value most likely to be wanted by hand is also
    the one that does not need reading off a slider. The peak comes from
    nightlight-auto (see nightlight_panel.schedule), and is added as its own
    step if it is not already a multiple of ten."""
    values = set(range(0, 101, 10))
    if peak is not None:
        values.add(peak)
    labelled = []
    for value in sorted(values):
        if value == 0:
            label = "Off — 6500K"
        elif value == peak:
            label = "{}% — night".format(value)
        elif value == 100:
            label = "100% — 2500K"
        else:
            label = "{}%".format(value)
        labelled.append((value, label))
    return labelled

# Scrolling over the icon moves by this much. Coarser than the slider on
# purpose: a scroll is a nudge, and every value it can reach is also in the menu.
SCROLL_STEP = 5


def run(argv):
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


class NightlightTray:
    def __init__(self):
        self.percent = 0
        self.mode = "auto"
        self.panel = None
        self._menu_state = None
        self.schedule = {}

        self.tray = tray.Tray(APP_ID, ICON_ON, "Night light")
        self.tray.connect_scroll(self.on_scroll)

        self.refresh()
        GLib.timeout_add(POLL_INTERVAL_MS, self.on_poll)

    # --- state --------------------------------------------------------------

    def read_state(self):
        value = run([NIGHTLIGHT, "--print"])
        try:
            self.percent = max(0, min(100, int(value)))
        except (TypeError, ValueError):
            # Keep the last known value rather than falling to 0. `run` returns
            # None for a timeout, a non-zero exit, or a state file caught
            # between truncate and write, and none of those mean the night
            # light was turned off — but 0 is the one number that says it was,
            # in the icon as well as the tooltip. Holding the previous reading
            # is wrong for at most one poll; claiming "off" is wrong in a way
            # that looks like a bug in something else.
            pass
        self.mode = "manual" if run([NIGHTLIGHT, "--mode"]) == "manual" else "auto"

    def refresh(self):
        self.read_state()

        self.tray.set_icon(ICON_ON if self.percent else ICON_OFF)

        kelvin = 6500 - self.percent * 40
        if self.mode == "auto":
            state = "following the schedule"
        else:
            state = "manual — the schedule is not running"
        self.tray.set_tooltip(f"Night light: {self.percent}% ({kelvin}K)\n{state}")

        # Rebuilding a menu is not free: every rebuild bumps the DBusMenu
        # revision and emits LayoutUpdated, and a menu replaced while the host
        # is trying to open it does not open. That is how the battery tray
        # presented as "the icon is there and clicking does nothing" — it was
        # rebuilding on every poll. So the menu is rebuilt only when something
        # it actually shows has changed.
        # The schedule is in the key so that editing nightlight-auto's peak or
        # times reaches the menu at the next poll, not at the next restart.
        self.schedule = nightlight_panel.schedule()
        state_key = (self.percent, self.mode, tuple(sorted(self.schedule.items())))
        if state_key != self._menu_state:
            self._menu_state = state_key
            self.tray.set_menu(self.build_menu())

    def on_poll(self):
        self.refresh()
        return True

    # --- menu ---------------------------------------------------------------

    def build_menu(self):
        menu = Gtk.Menu()

        auto = tray.check_item(
            "Automatic schedule", self.mode == "auto", self.on_auto_toggled
        )
        sched = self.schedule
        if all(k in sched for k in ("PEAK", "RISE_START", "RISE_END", "FALL_END")):
            auto.set_tooltip_text(
                "0% through the day, rising from {} to {}% at {}, back to 0% "
                "by {}".format(
                    nightlight_panel.clock(sched["RISE_START"]), sched["PEAK"],
                    nightlight_panel.clock(sched["RISE_END"]),
                    nightlight_panel.clock(sched["FALL_END"]))
            )
        menu.append(auto)
        menu.append(tray.separator())

        # Radio rather than plain items: the menu then shows which value is on
        # without a heading that restates the tooltip. An exact match is only
        # marked when the value came from a step — a scheduled 43% leaves all
        # ten unmarked, which is honest, and the tooltip carries the number.
        group = []
        for value, label in steps(self.schedule.get("PEAK")):
            item = Gtk.RadioMenuItem.new_with_label(group, label)
            group = item.get_group()
            item.set_active(value == self.percent)
            item.connect("toggled", self.on_step_toggled, value)
            menu.append(item)

        menu.append(tray.separator())
        menu.append(tray.item("Fine adjustment…", self.on_adjust))
        menu.append(tray.separator())
        menu.append(tray.item("Quit", lambda _item: Gtk.main_quit()))
        return menu

    def on_auto_toggled(self, item):
        mode = "auto" if item.get_active() else "manual"
        # --mode auto applies the scheduled value itself, so there is nothing to
        # apply here — only a state read to pick the new colour up.
        run([NIGHTLIGHT, "--mode", mode])
        self.refresh()

    def on_step_toggled(self, item, value):
        # Every switch toggles twice — once off the old item, once onto the new
        # one — and only the second is a request to do anything.
        if item.get_active():
            self.set_percent(value)

    def on_adjust(self, _item):
        """Open the slider, or focus the one already open.

        In-process rather than spawned: the window is GTK and this is already a
        GTK main loop, so opening it costs nothing, and it can hand back what it
        changed instead of the tray waiting for its next poll to find out.
        """
        if self.panel is not None:
            self.panel.present()
            return
        self.panel = nightlight_panel.Panel(on_change=self.refresh)
        self.panel.connect("destroy", self.on_panel_destroyed)
        self.panel.show_all()

    def on_panel_destroyed(self, _panel):
        self.panel = None
        self.refresh()

    # --- scroll -------------------------------------------------------------

    def on_scroll(self, _source, delta, _direction):
        target = self.percent + (SCROLL_STEP if delta > 0 else -SCROLL_STEP)
        self.set_percent(max(0, min(100, target)))

    def set_percent(self, value):
        """Set a value by hand, which is what switches the schedule off.

        Run in the foreground rather than spawned: gamma-apply returns as soon
        as the new holder has the ramp, and refreshing before it has taken it
        would read back the old value and redraw the icon wrong.
        """
        run([NIGHTLIGHT, str(value)])
        self.refresh()


def main():
    # The prgname is the panel's, not this tray's, and that is deliberate: GTK
    # takes the Wayland app_id from it, the only window this process ever opens
    # is the panel, and niri's floating rule matches that app_id. The tray's own
    # identity is the SNI Id above, which has nothing to do with prgname.
    GLib.set_prgname(nightlight_panel.APP_ID)
    nightlight_panel.ensure_window_rules()
    NightlightTray()
    Gtk.main()


if __name__ == "__main__":
    main()
