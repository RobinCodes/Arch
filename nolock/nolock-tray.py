#!/usr/bin/env python3
"""nolock tray icon: one click to decide whether waking the laptop asks for a
password.

  open padlock    nolock is on  — open the lid or wake straight into the session
  closed padlock  nolock is off — waking asks for the password, as XFCE ships

The sibling nolock script owns the state; this only reads it and calls that
script to change it, so the icon, the Super+U shortcut and the CLI can't
disagree about what's set.

Reading goes through the Xfconf bindings rather than by shelling out on a
timer: xfconf emits property-changed, so a toggle from the shortcut redraws
the icon immediately and an idle tray costs nothing."""

import os
import subprocess
import warnings

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Xfconf", "0")
from gi.repository import GLib, Gtk, Xfconf  # noqa: E402

warnings.filterwarnings("ignore", category=DeprecationWarning)

NOLOCK_BIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nolock")

ON_ICON = "changes-allow-symbolic"
OFF_ICON = "changes-prevent-symbolic"

# Mirrors the KEYS table in the nolock script: (channel, property, kind,
# locking value). Any single key left at its locking value is enough to put a
# lock screen in the way, so "on" means none of them are.
#
# The locking value is also the compiled-in default each key carries while
# unset, which is what lets the same one-line test cover both the booleans and
# the lid actions — the latter being a seven-member enum where only 3, "lock
# screen", locks. Reading them the way the script does is the point: the icon
# claiming "no password" while the lid pair still locked is the bug this table
# grew to fix.
WATCHED = (
    ("xfce4-power-manager", "/xfce4-power-manager/lock-screen-suspend-hibernate", bool, True),
    ("xfce4-screensaver", "/lock/sleep-activation", bool, True),
    ("xfce4-screensaver", "/lock/saver-activation/enabled", bool, True),
    ("xfce4-power-manager", "/xfce4-power-manager/lid-action-on-ac", int, 3),
    ("xfce4-power-manager", "/xfce4-power-manager/lid-action-on-battery", int, 3),
)

WATCHED_PROPS = {prop for _channel, prop, _kind, _lock in WATCHED}

SCREENSAVER_SETTINGS = "xfce4-screensaver-preferences"
POWER_SETTINGS = "xfce4-power-manager-settings"


class NoLockTray:
    def __init__(self):
        Xfconf.init()
        # Held on the instance because the signal subscription dies with the
        # channel object, and a GC'd channel takes the live updates with it.
        self.channels = {
            name: Xfconf.Channel.get(name)
            for name in {channel for channel, _prop, _kind, _lock in WATCHED}
        }
        for channel in self.channels.values():
            channel.connect("property-changed", self.on_property_changed)

        self.menu = None
        self.icon = Gtk.StatusIcon()
        self.icon.set_title("Wake login")
        self.icon.connect("activate", self.on_click)
        self.icon.connect("popup-menu", self.on_popup_menu)
        self.refresh()
        self.icon.set_visible(True)

    def locks(self, channel, prop, kind, lock_value):
        """Whether this one key would put a lock screen in the way."""
        channel = self.channels[channel]
        # The default handed to the getter is the locking value, so an unset
        # key reads as locking without a separate "is it set" round trip.
        if kind is bool:
            return channel.get_bool(prop, lock_value) == lock_value
        return channel.get_uint(prop, lock_value) == lock_value

    def is_on(self):
        return not any(self.locks(*key) for key in WATCHED)

    def refresh(self):
        on = self.is_on()
        self.icon.set_from_icon_name(ON_ICON if on else OFF_ICON)
        # Phrased as skipped/required rather than on/off: the menu item next to
        # it is a checkbox called "Skip login on wake", and a tooltip reading
        # "off" beside a ticked box invites exactly the wrong reading.
        self.icon.set_tooltip_text(
            "Wake login: skipped — the lid or a wake won't ask for your password"
            if on
            else "Wake login: required — the lid or a wake asks for your password"
        )

    def on_property_changed(self, _channel, prop, _value):
        # The channels carry unrelated settings too (brightness steps, saver
        # delays); only the five that gate the lock screen mean a redraw.
        if prop in WATCHED_PROPS:
            self.refresh()

    def toggle(self):
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
        # No refresh here: the xfconf change lands on its own and redraws once,
        # rather than racing this against a process that hasn't written yet.

    def build_menu(self):
        menu = Gtk.Menu()

        item = Gtk.CheckMenuItem(label="Skip login on wake")
        item.set_active(self.is_on())
        item.connect("toggled", lambda _item: self.toggle())
        menu.append(item)

        menu.append(Gtk.SeparatorMenuItem())
        for label, binary in (
            ("Screensaver Settings", SCREENSAVER_SETTINGS),
            ("Power Manager Settings", POWER_SETTINGS),
        ):
            entry = Gtk.MenuItem(label=label)
            entry.connect("activate", self.on_settings, binary)
            menu.append(entry)

        menu.append(Gtk.SeparatorMenuItem())
        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", lambda _item: Gtk.main_quit())
        menu.append(quit_item)

        menu.show_all()
        return menu

    def on_settings(self, _item, binary):
        try:
            subprocess.Popen([binary])
        except OSError:
            pass

    def show_menu(self, button, time):
        # Rebuilt per click so it can't outlive the icon's position, and so the
        # check item is seeded from the current state.
        self.menu = self.build_menu()
        self.menu.popup(None, None, Gtk.StatusIcon.position_menu, self.icon,
                        button, time)

    def on_click(self, _icon):
        self.toggle()

    def on_popup_menu(self, _icon, button, time):
        self.show_menu(button, time)


def main():
    GLib.set_prgname("nolock-tray")
    if not os.access(NOLOCK_BIN, os.X_OK):
        raise SystemExit(f"{NOLOCK_BIN} is missing or not executable")
    NoLockTray()
    Gtk.main()


if __name__ == "__main__":
    main()
