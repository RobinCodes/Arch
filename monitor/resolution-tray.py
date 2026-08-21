#!/usr/bin/env python3
"""Resolution tray icon: click for the three modes worth running the primary
monitor at, pick one, done.

  Highest   the best mode the monitor reports — 3840x2160 @ 60 Hz on the LG
  Full HD   1920x1080, or the nearest thing to it the monitor has
  Lowest    the smallest mode it reports — 640x480 here

The sibling `resolution` script owns all of it: resolving what those three mean
for whatever is currently primary, writing the XFCE display profile, calling
xrandr, and moving font DPI and panel sizes to match. This only reads that
script and calls it, so the icon, the CLI and xfce4-display-settings can't
disagree about what's set.

The preset list is cached rather than re-read per click. It only changes when
the hardware does, and RandR says so — so the menu is seeded from a cache that
a screen change invalidates, and opening it costs nothing."""

import os
import subprocess
import warnings

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

warnings.filterwarnings("ignore", category=DeprecationWarning)

RESOLUTION_BIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resolution")

ICON_NAME = "preferences-desktop-display-symbolic"
FALLBACK_ICON_NAME = "video-display-symbolic"
DISPLAY_SETTINGS = "xfce4-display-settings"

LABELS = {"highest": "Highest", "fullhd": "Full HD", "lowest": "Lowest"}
ORDER = ("highest", "fullhd", "lowest")

# A mode switch emits several RandR events in a row — one per CRTC touched, and
# more once xfsettingsd has had its say. Coalesce them into one reload.
RELOAD_DEBOUNCE_MS = 400


def run_resolution(*args):
    return subprocess.run(
        [RESOLUTION_BIN, *args], capture_output=True, text=True, check=True
    ).stdout


def read_presets():
    """Parse `resolution --list` into ({key: (mode, rate)}, active key).

    Lines are "<key> <WxH> <rate>", plus a final "current <key> <WxH> <rate>"
    whose key is "custom" when the active mode is none of the three.
    """
    presets, current = {}, None
    for line in run_resolution("--list").splitlines():
        fields = line.split()
        if len(fields) == 4 and fields[0] == "current":
            current = fields[1]
        elif len(fields) == 3:
            presets[fields[0]] = (fields[1], fields[2])
    return presets, current


def pretty(mode, rate):
    """3840x2160 60.00 -> 3840x2160 @ 60 Hz, with the trailing zeros gone."""
    return f"{mode.replace('x', '×')} @ {float(rate):g} Hz"


class ResolutionTray:
    def __init__(self):
        self.presets = {}
        self.current = None
        self.error = None
        self.menu = None
        self._updating = False
        self._reload_timer = None

        self.icon = Gtk.StatusIcon()
        self.icon.set_title("Screen resolution")
        self.icon.set_from_icon_name(
            ICON_NAME
            if Gtk.IconTheme.get_default().has_icon(ICON_NAME)
            else FALLBACK_ICON_NAME
        )
        self.icon.connect("activate", self.on_click)
        self.icon.connect("popup-menu", self.on_popup_menu)

        self.reload()
        self.icon.set_visible(True)

        # Catches a mode set from the CLI or the settings dialog as well as our
        # own, so the tick is on the right item however the change was made.
        screen = Gdk.Screen.get_default()
        if screen is not None:
            screen.connect("size-changed", self.on_screen_changed)
            screen.connect("monitors-changed", self.on_screen_changed)

    def reload(self):
        try:
            self.presets, self.current = read_presets()
            self.error = None
        except (subprocess.CalledProcessError, OSError) as err:
            self.presets, self.current = {}, None
            self.error = err.stderr.strip() if hasattr(err, "stderr") else str(err)
        self.refresh_tooltip()

    def refresh_tooltip(self):
        if self.error:
            self.icon.set_tooltip_text(f"Screen resolution: unavailable\n{self.error}")
            return
        active = self.presets.get(self.current)
        if active is None:
            self.icon.set_tooltip_text("Screen resolution: custom")
            return
        self.icon.set_tooltip_text(
            f"Screen resolution: {pretty(*active)} ({LABELS[self.current]})"
        )

    def on_screen_changed(self, _screen):
        if self._reload_timer is not None:
            GLib.source_remove(self._reload_timer)
        self._reload_timer = GLib.timeout_add(RELOAD_DEBOUNCE_MS, self.on_reload_timer)

    def on_reload_timer(self):
        self._reload_timer = None
        self.reload()
        return GLib.SOURCE_REMOVE

    def apply(self, key):
        try:
            subprocess.Popen([RESOLUTION_BIN, key])
        except OSError as err:
            dialog = Gtk.MessageDialog(
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.CLOSE,
                text=f"Could not run {RESOLUTION_BIN}",
            )
            dialog.format_secondary_text(str(err))
            dialog.run()
            dialog.destroy()
        # No reload here: the mode change raises a screen event on its own and
        # refreshes once, rather than racing this against an xrandr call that
        # hasn't landed yet.

    def on_preset_toggled(self, item, key):
        # Selecting one radio item deactivates another, so this fires twice.
        if self._updating or not item.get_active() or key == self.current:
            return
        self.apply(key)

    def build_menu(self):
        menu = Gtk.Menu()

        if self.error:
            broken = Gtk.MenuItem(label="Resolutions unavailable")
            broken.set_sensitive(False)
            menu.append(broken)
        else:
            group, items = None, []
            for key in ORDER:
                if key not in self.presets:
                    continue
                item = Gtk.RadioMenuItem(
                    label=f"{LABELS[key]} — {pretty(*self.presets[key])}"
                )
                if group is None:
                    group = item
                else:
                    item.join_group(group)
                menu.append(item)
                items.append((item, key))

            # Seed every item before connecting anything, so putting the tick
            # on the active preset doesn't fire a handler that reapplies it.
            self._updating = True
            for item, key in items:
                item.set_active(key == self.current)
            self._updating = False
            for item, key in items:
                item.connect("toggled", self.on_preset_toggled, key)

        menu.append(Gtk.SeparatorMenuItem())
        settings = Gtk.MenuItem(label="Display Settings…")
        settings.connect("activate", self.on_settings)
        menu.append(settings)

        menu.append(Gtk.SeparatorMenuItem())
        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", lambda _item: Gtk.main_quit())
        menu.append(quit_item)

        menu.show_all()
        return menu

    def on_settings(self, _item):
        try:
            subprocess.Popen([DISPLAY_SETTINGS])
        except OSError:
            pass

    def show_menu(self, button, time):
        # Rebuilt per click so it can't outlive the icon's position, and so the
        # tick is seeded from the current mode.
        self.menu = self.build_menu()
        self.menu.popup(
            None, None, Gtk.StatusIcon.position_menu, self.icon, button, time
        )

    def on_click(self, _icon):
        self.show_menu(0, Gtk.get_current_event_time())

    def on_popup_menu(self, _icon, button, time):
        self.show_menu(button, time)


def main():
    GLib.set_prgname("resolution-tray")
    if not os.access(RESOLUTION_BIN, os.X_OK):
        raise SystemExit(f"{RESOLUTION_BIN} is missing or not executable")
    ResolutionTray()
    Gtk.main()


if __name__ == "__main__":
    main()
