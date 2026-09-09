#!/usr/bin/env python3
"""nolock tray icon: one click to decide whether waking the laptop asks for a
password.

  open padlock    nolock is on  — open the lid or wake straight into the session
  closed padlock  nolock is off — waking asks for the password

The sibling nolock script owns the state; this only reads it and calls that
script to change it, so the icon, the keybind and the CLI can't disagree about
what's set. That contract is unchanged from the XFCE version; what changed is
everything underneath it.

TWO THINGS THE PORT HAD TO GIVE UP, AND WHAT REPLACED THEM

  The icon. Gtk.StatusIcon is XEmbed and draws nothing at all under a Wayland
  compositor, so this is a StatusNotifierItem now (../lib/tray.py). The one
  behavioural consequence: a left click can only open the menu, so the toggle
  that used to be a click on the icon is the first item in that menu.

  The updates. Xfconf emits property-changed, so a toggle from the keybind
  redrew this instantly and an idle tray cost nothing. noctalia's settings are
  a TOML file with no change signal, so the file is watched directly instead —
  same result, same "no polling", and it also catches somebody editing the
  file by hand or changing the setting in noctalia's own UI.
"""

import os
import subprocess
import sys
import warnings

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import tray  # noqa: E402

warnings.filterwarnings("ignore", category=DeprecationWarning)

HERE = os.path.dirname(os.path.abspath(__file__))
NOLOCK_BIN = os.path.join(HERE, "nolock")

ON_ICON = "changes-allow-symbolic"
OFF_ICON = "changes-prevent-symbolic"

SETTINGS_FILE = os.path.join(
    os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")),
    "noctalia",
    "settings.toml",
)

# noctalia's settings window, at the section that holds these switches. The
# XFCE version pointed at two separate control panels; there is one here.
NOCTALIA_SETTINGS = ("noctalia", "msg", "settings-open")

# A write lands as several filesystem events (create temp, rename, attribute
# change); coalesce them into one status read.
RELOAD_DEBOUNCE_MS = 250


class NoLockTray:
    def __init__(self):
        self.state = self.read_state()
        self.pending = 0

        self.tray = tray.Tray(
            "nolock-tray",
            ON_ICON if self.state else OFF_ICON,
            "Wake login",
            category=tray.CATEGORY.SYSTEM_SERVICES,
        )
        self.refresh()

        # Watching the directory rather than the file: the settings are written
        # through a temp file and renamed, which replaces the inode, and a
        # monitor bound to the old inode stops reporting after the first write.
        self.monitor = Gio.File.new_for_path(
            os.path.dirname(SETTINGS_FILE)
        ).monitor_directory(Gio.FileMonitorFlags.NONE, None)
        self.monitor.connect("changed", self.on_settings_changed)

    # --- state --------------------------------------------------------------

    def read_state(self):
        """True when nolock is on. Asks the script rather than reading the
        settings here: the rule for what counts as "would lock" lives in one
        place, and it is not this file."""
        try:
            out = subprocess.run(
                [NOLOCK_BIN, "status", "--quiet"],
                capture_output=True, text=True, timeout=5, check=True,
            ).stdout.strip()
        except (subprocess.SubprocessError, OSError):
            return False
        return out == "on"

    def refresh(self):
        on = self.state
        self.tray.set_icon(ON_ICON if on else OFF_ICON)
        # Phrased as skipped/required rather than on/off: the menu item next to
        # it is a checkbox called "Skip login on wake", and a tooltip reading
        # "off" beside a ticked box invites exactly the wrong reading.
        self.tray.set_tooltip(
            "Wake login: skipped — the lid or a wake won't ask for your password"
            if on
            else "Wake login: required — the lid or a wake asks for your password"
        )
        self.tray.set_menu(self.build_menu())

    def on_settings_changed(self, _monitor, gfile, _other, _event):
        if gfile.get_path() != SETTINGS_FILE:
            return
        if self.pending:
            GLib.source_remove(self.pending)
        self.pending = GLib.timeout_add(RELOAD_DEBOUNCE_MS, self.reload)

    def reload(self):
        self.pending = 0
        state = self.read_state()
        if state != self.state:
            self.state = state
            self.refresh()
        return GLib.SOURCE_REMOVE

    # --- actions ------------------------------------------------------------

    def toggle(self, *_):
        try:
            subprocess.Popen([NOLOCK_BIN, "toggle"])
        except OSError as err:
            dialog = Gtk.MessageDialog(
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.CLOSE,
                text=f"Could not run {NOLOCK_BIN}",
            )
            dialog.format_secondary_text(str(err))
            dialog.run()
            dialog.destroy()
        # No refresh here: the settings write lands on its own and the file
        # monitor redraws once, rather than racing this against a process that
        # hasn't written yet.

    def on_settings(self, _item):
        try:
            subprocess.Popen(NOCTALIA_SETTINGS)
        except OSError:
            pass

    def build_menu(self):
        menu = Gtk.Menu()

        entry = Gtk.CheckMenuItem(label="Skip login on wake")
        entry.set_active(self.state)
        # The handler is attached after set_active so seeding the box does not
        # fire "toggled" and immediately undo the state it is displaying.
        entry.connect("toggled", self.toggle)
        menu.append(entry)

        menu.append(tray.separator())
        menu.append(tray.item("Lock Now", self.lock_now))
        menu.append(tray.item("noctalia Settings", self.on_settings))

        menu.append(tray.separator())
        menu.append(tray.item("Quit", lambda _item: Gtk.main_quit()))
        return menu

    def lock_now(self, _item):
        """The manual path, which nolock never touches — lockscreen.enabled is
        deliberately left alone so this still works while nolock is on."""
        try:
            subprocess.Popen(["noctalia", "msg", "session", "lock"])
        except OSError:
            pass


def main():
    GLib.set_prgname("nolock-tray")
    if not os.access(NOLOCK_BIN, os.X_OK):
        raise SystemExit(f"{NOLOCK_BIN} is missing or not executable")
    NoLockTray()
    Gtk.main()


if __name__ == "__main__":
    main()
