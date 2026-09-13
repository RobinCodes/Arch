#!/usr/bin/env python3
"""Resolution tray icon: click for the modes worth running the monitor at,
pick one, done.

  Highest   the best mode the monitor reports
  Full HD   1920x1080, or the nearest thing to it the monitor has
  Lowest    the smallest mode it reports

The sibling `resolution` script owns all of it: resolving what those three mean
for whatever output is focused, writing niri's output config, calling the
compositor, and moving the scale to match. This only reads that script and
calls it, so the icon, the CLI and niri's own config can't disagree.

It also calls `resolution retier` on every layout change, which is the half of
the job no menu click covers. Unplugging a monitor is a change nobody ran
`resolution` for, so the scale chosen for the screen that just left stays
applied to the one still here — and between a 27" 4K and an 11.6" laptop panel
that is a factor of two in how big everything lands on the glass.

WHAT THE PORT CHANGED

  The icon is a StatusNotifierItem (../lib/tray.py) rather than a
  Gtk.StatusIcon, which draws nothing under Wayland.

  The change signal moved from RandR to niri's event stream. There is no
  screen-changed signal to connect to on a Gdk.Screen here, and niri does not
  emit an outputs-changed event of its own — but workspaces belong to outputs,
  so plugging or unplugging a monitor always moves some and always produces a
  WorkspacesChanged. That plus ConfigLoaded is the same coverage the RandR
  handler had, from a socket that is already open rather than a poll.
"""

import json
import os
import subprocess
import sys
import warnings

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import tray  # noqa: E402

warnings.filterwarnings("ignore", category=DeprecationWarning)

HERE = os.path.dirname(os.path.abspath(__file__))
RESOLUTION_BIN = os.path.join(HERE, "resolution")

ICON_NAME = "preferences-desktop-display-symbolic"
FALLBACK_ICON_NAME = "video-display-symbolic"

LABELS = {"highest": "Highest", "fullhd": "Full HD", "lowest": "Lowest"}
ORDER = ("highest", "fullhd", "lowest")

# A layout change emits several events in a row — one per workspace touched,
# and more once niri has re-read its config. Coalesce them into one reload.
RELOAD_DEBOUNCE_MS = 400

# Events that can mean the set of outputs changed.
LAYOUT_EVENTS = ("WorkspacesChanged", "ConfigLoaded")


def run_resolution(*args):
    return subprocess.run(
        [RESOLUTION_BIN, *args], capture_output=True, text=True,
        timeout=15, check=True,
    ).stdout


def read_presets():
    """Parse `resolution --list` into ({key: (mode, rate)}, active key).

    Lines are "<key> <WxH> <rate>", plus a final "current <key> <WxH> <rate>".
    """
    presets, active = {}, None
    try:
        out = run_resolution("--list")
    except (subprocess.SubprocessError, OSError):
        return presets, active

    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[0] in LABELS:
            presets[parts[0]] = (parts[1], parts[2])
        elif len(parts) == 4 and parts[0] == "current":
            active = parts[1]
    return presets, active


class ResolutionTray:
    def __init__(self):
        self.presets = {}
        self.active = None
        self.pending = 0

        self.tray = tray.Tray(
            "resolution-tray", ICON_NAME, "Resolution",
            category=tray.CATEGORY.HARDWARE,
        )
        self.reload()
        self.watch_events()

    # --- state --------------------------------------------------------------

    def reload(self):
        """Re-read the presets and rebuild the menu.

        The preset list only changes when the hardware does, so this is called
        on a layout change rather than per click — opening the menu costs
        nothing.
        """
        self.pending = 0
        self.presets, self.active = read_presets()

        label = LABELS.get(self.active, "Custom") if self.presets else "unavailable"
        mode = self.presets.get(self.active)
        self.tray.set_tooltip(
            f"Resolution: {mode[0]} @ {mode[1]} Hz ({label})" if mode
            else "Resolution: unavailable"
        )
        self.tray.set_icon(ICON_NAME if self.presets else FALLBACK_ICON_NAME)
        self.tray.set_menu(self.build_menu())
        return GLib.SOURCE_REMOVE

    def build_menu(self):
        menu = Gtk.Menu()

        if not self.presets:
            menu.append(tray.heading("niri is not answering"))
        else:
            group = None
            for key in ORDER:
                if key not in self.presets:
                    continue
                mode, rate = self.presets[key]
                entry = Gtk.RadioMenuItem(label=f"{LABELS[key]} — {mode} @ {rate} Hz")
                if group is None:
                    group = entry
                else:
                    entry.join_group(group)
                entry.set_active(key == self.active)
                # Connected after set_active, so seeding the radio group does
                # not fire a mode switch for the mode already in use.
                entry.connect("toggled", self.on_pick, key)
                menu.append(entry)

        menu.append(tray.separator())
        menu.append(tray.item("Re-fit UI to this screen", self.on_retier))
        menu.append(tray.separator())
        menu.append(tray.item("Quit", lambda _item: Gtk.main_quit()))
        return menu

    # --- actions ------------------------------------------------------------

    def on_pick(self, item, key):
        # RadioMenuItem fires twice per change — once for the item losing the
        # selection and once for the one gaining it. Only the winner is a
        # request to switch mode.
        if not item.get_active() or key == self.active:
            return
        try:
            run_resolution(key, "--quiet")
        except (subprocess.SubprocessError, OSError):
            pass
        # niri emits nothing for a mode change on an output that was already
        # on, so unlike the layout case this has to schedule its own reload.
        self.schedule_reload()

    def on_retier(self, _item):
        try:
            run_resolution("retier", "--quiet")
        except (subprocess.SubprocessError, OSError):
            pass

    # --- niri event stream --------------------------------------------------

    def watch_events(self):
        """Follow `niri msg event-stream` for layout changes.

        A long-lived subprocess read through a GLib IO watch rather than a
        timer: the stream is line-delimited JSON and the socket is already
        there, so this costs nothing while the layout is stable.
        """
        try:
            self.proc = subprocess.Popen(
                ["niri", "msg", "--json", "event-stream"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
        except OSError:
            self.proc = None
            return

        channel = GLib.IOChannel.unix_new(self.proc.stdout.fileno())
        channel.set_flags(GLib.IOFlags.NONBLOCK)
        GLib.io_add_watch(
            channel,
            GLib.PRIORITY_DEFAULT,
            GLib.IOCondition.IN | GLib.IOCondition.HUP,
            self.on_event,
        )

    def on_event(self, channel, condition):
        if condition & GLib.IOCondition.HUP:
            # niri went away, or the stream was cut. Nothing useful left to
            # watch, and respawning in a loop against a dead compositor would
            # be worse than going quiet.
            return False
        try:
            line, _length, _end = channel.read_line()
        except (GLib.Error, ValueError):
            return True
        if not line:
            return True
        try:
            event = json.loads(line)
        except ValueError:
            return True
        if any(name in event for name in LAYOUT_EVENTS):
            self.schedule_reload()
            # The scale that suits the monitor that just arrived is not the one
            # that suited the one that left. Idempotent, so the extra call
            # after a preset switch rewrites the same value.
            self.schedule_retier()
        return True

    def schedule_reload(self):
        if self.pending:
            GLib.source_remove(self.pending)
        self.pending = GLib.timeout_add(RELOAD_DEBOUNCE_MS, self.reload)

    def schedule_retier(self):
        GLib.timeout_add(RELOAD_DEBOUNCE_MS + 100, self._retier_once)

    def _retier_once(self):
        self.on_retier(None)
        return GLib.SOURCE_REMOVE


def main():
    GLib.set_prgname("resolution-tray")
    if not os.access(RESOLUTION_BIN, os.X_OK):
        raise SystemExit(f"{RESOLUTION_BIN} is missing or not executable")
    ResolutionTray()
    Gtk.main()


if __name__ == "__main__":
    main()
