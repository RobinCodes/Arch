#!/usr/bin/env python3
"""Brightness tray icon: backlight and night light warmth from the bar.

  Auto on   nightlight-auto drives warmth from the time of day; setting a
            value by hand parks an override until the next 07:00/19:00 boundary.
  Auto off  the value stays put indefinitely.

Both are handed off to the sibling nightlight script, which owns the state.

WHAT THE PORT COST, AND HOW IT WAS PAID

This tray had the most XFCE-shaped interface of the four, and it is the one
StatusNotifierItem could not reproduce as-is. Three things had to move:

  A left click can no longer open a popup. SNI gives the bar one gesture and
  it opens the menu; there is no activate event to hook. So the common cases —
  a few brightness steps, a few warmth steps, the Auto switch — are menu items
  now, and the sliders live behind an "Adjust..." item.

  The slider panel is a real window rather than an override-redirect popup.
  The old one placed itself against the icon and took a seat grab to catch the
  click that dismissed it. A Wayland client can do neither: it cannot read the
  icon's position, cannot place its own toplevel, and cannot grab the seat.
  It is an ordinary window that niri floats by rule, closed with Escape or its
  own Close button instead of by clicking away.

  Sliders cannot go in the menu. DBusMenu carries labels, checkboxes, radio
  items and separators — a Gtk.Scale packed into a menu item arrives as an
  empty row. That is the whole reason for the steps-in-menu plus window split
  rather than simply moving the existing panel into the menu.

Scrolling over the icon still steps brightness where the bar forwards a scroll,
and every step is in the menu too, so nothing is reachable only that way."""

import os
import subprocess
import sys
import time
import warnings

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import tray  # noqa: E402

warnings.filterwarnings("ignore", category=DeprecationWarning)

STEP = 5
POLL_INTERVAL_MS = 2000
ICON_NAME = "display-brightness-symbolic"
NIGHTLIGHT_ICON_NAME = "night-light-symbolic"

# ../nightlight/, resolved from this file so the Scripts folder stays movable.
_NIGHTLIGHT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "nightlight")
)
NIGHTLIGHT_BIN = os.path.join(_NIGHTLIGHT_DIR, "nightlight")
NIGHTLIGHT_AUTO_BIN = os.path.join(_NIGHTLIGHT_DIR, "nightlight-auto")
STATE_DIR = os.path.join(
    os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")),
    "nightlight",
)
OVERRIDE_FILE = os.path.join(STATE_DIR, "override-until")
MODE_FILE = os.path.join(STATE_DIR, "mode")
# Where the current temperature lives now. It used to be read out of
# redshift.conf, which the nightlight script kept it in; with redshift gone
# under Wayland that file is a config for a program nothing runs, so the value
# moved to plain state. The old path is still read as a fallback, exactly as
# nightlight itself does, so an install that predates the move still shows the
# right number instead of jumping to neutral.
KELVIN_FILE = os.path.join(STATE_DIR, "kelvin")
LEGACY_CONF = os.path.expanduser("~/.config/redshift/redshift.conf")
# Mirrors the scale in the nightlight script: 0% = 6500K, 100% = 2500K.
NEUTRAL_KELVIN = 6500
KELVIN_PER_PERCENT = 40
# Each nightlight call reaches a gamma backend, and for the respawn backends
# that means starting a process and waiting for it to take the ramp — far more
# than redshift's old ~30ms. Coalescing a drag matters more here, not less.
APPLY_DEBOUNCE_MS = 200

CTL_ERRORS = (subprocess.CalledProcessError, OSError, ValueError, IndexError)


def get_brightness():
    out = subprocess.run(
        ["brightnessctl", "-m"], capture_output=True, text=True, check=True
    ).stdout.strip()
    # -m is one CSV line per device: name,class,current,percent,max
    fields = out.splitlines()[0].split(",")
    current, max_ = int(fields[2]), int(fields[4])
    return round(current / max_ * 100) if max_ else 0


def set_brightness(percent):
    percent = max(1, min(100, percent))
    subprocess.run(["brightnessctl", "set", f"{percent}%"], check=True)
    return percent


def get_nightlight():
    try:
        with open(KELVIN_FILE) as fh:
            kelvin = int(fh.read().strip())
        return round((NEUTRAL_KELVIN - kelvin) / KELVIN_PER_PERCENT)
    except (OSError, ValueError):
        pass
    with open(LEGACY_CONF) as conf:
        for line in conf:
            if line.startswith("temp-day="):
                kelvin = int(line.split("=", 1)[1])
                return round((NEUTRAL_KELVIN - kelvin) / KELVIN_PER_PERCENT)
    raise ValueError(f"no night light temperature in {KELVIN_FILE}")


def set_nightlight(percent):
    percent = max(0, min(100, percent))
    subprocess.run(
        [NIGHTLIGHT_BIN, str(percent)], check=True, stdout=subprocess.DEVNULL
    )
    return percent


def override_until():
    """Epoch a manual change holds the schedule off until, or None."""
    try:
        with open(OVERRIDE_FILE) as state:
            expiry = int(state.read().strip())
    except (OSError, ValueError):
        return None
    return expiry if time.time() < expiry else None


def get_mode():
    """"auto" unless the state file explicitly says otherwise."""
    try:
        with open(MODE_FILE) as state:
            return "manual" if state.read().strip() == "manual" else "auto"
    except OSError:
        return "auto"


def set_mode(mode):
    subprocess.run(
        [NIGHTLIGHT_BIN, "--mode", mode], check=True, stdout=subprocess.DEVNULL
    )


def apply_schedule():
    """Pull the scheduled value in now rather than waiting for the next tick."""
    subprocess.run([NIGHTLIGHT_AUTO_BIN], check=True, stdout=subprocess.DEVNULL)


class BrightnessTray:
    def __init__(self):
        self.tray = tray.Tray(
            "brightness-tray", ICON_NAME, "Brightness",
            category=tray.CATEGORY.HARDWARE,
        )
        self.tray.connect_scroll(self.on_scroll)
        self.popup = None
        self.brightness_scale = None
        self.nightlight_scale = None
        self.auto_switch = None
        self._updating = False
        self._dragging = set()
        self._pending_nightlight = None
        self._nightlight_timer = None
        self.refresh_tooltip()
        GLib.timeout_add(POLL_INTERVAL_MS, self.on_poll)

    def refresh_tooltip(self):
        try:
            brightness = get_brightness()
        except CTL_ERRORS:
            brightness = None
        try:
            nightlight = get_nightlight()
        except CTL_ERRORS:
            nightlight = None

        lines = []
        if brightness is not None:
            lines.append(f"Brightness: {brightness}%")
        if nightlight is not None:
            if get_mode() == "manual":
                state = "manual"
            else:
                expiry = override_until()
                state = (
                    f"held until {time.strftime('%H:%M', time.localtime(expiry))}"
                    if expiry
                    else "auto"
                )
            lines.append(f"Night light: {nightlight}% ({state})")
        if lines:
            self.tray.set_tooltip(" · ".join(lines))
        # The menu shows the current values and the Auto state, so it is
        # rebuilt whenever they are re-read: a StatusNotifierItem menu goes
        # over D-Bus before it is shown, and there is no open event to hook.
        self.tray.set_menu(self.build_menu(brightness, nightlight))
        return brightness, nightlight

    def sync_scale(self, scale, value):
        if scale is None or value is None or scale in self._dragging:
            return
        self._updating = True
        scale.set_value(value)
        self._updating = False

    def sync_switch(self):
        """Reflect a mode changed elsewhere (CLI, the yad GUI) in the switch."""
        if self.auto_switch is None:
            return
        active = get_mode() == "auto"
        if self.auto_switch.get_active() != active:
            self._updating = True
            self.auto_switch.set_active(active)
            self._updating = False

    def on_poll(self):
        brightness, nightlight = self.refresh_tooltip()
        self.sync_scale(self.brightness_scale, brightness)
        # Don't clobber a debounced write that hasn't landed yet.
        if self._pending_nightlight is None:
            self.sync_scale(self.nightlight_scale, nightlight)
        self.sync_switch()
        return True

    def on_scroll(self, _indicator, delta, direction):
        """Step brightness on a scroll over the icon.

        The signature is libappindicator's, not Gdk's: SNI sends a delta and an
        orientation rather than a Gdk event, so there is no event to inspect
        and the direction comes through as an enum value. Bars are not obliged
        to send this at all, which is why every step here is also in the menu.
        """
        try:
            current = get_brightness()
        except CTL_ERRORS:
            return
        # A positive delta is a scroll up. There is no SMOOTH-vs-discrete
        # distinction to unpick here the way there was with Gdk events: the
        # bar has already resolved the gesture into one number.
        step = STEP if delta > 0 else -STEP
        try:
            percent = set_brightness(current + step)
        except CTL_ERRORS:
            return
        self.refresh_tooltip()
        self.sync_scale(self.brightness_scale, percent)

    def on_brightness_changed(self, scale):
        if self._updating:
            return
        try:
            set_brightness(round(scale.get_value()))
        except CTL_ERRORS:
            return
        self.refresh_tooltip()

    def on_nightlight_changed(self, scale):
        if self._updating:
            return
        self._pending_nightlight = round(scale.get_value())
        if self._nightlight_timer is None:
            self._nightlight_timer = GLib.timeout_add(
                APPLY_DEBOUNCE_MS, self.flush_nightlight
            )

    def flush_nightlight(self):
        self._nightlight_timer = None
        percent, self._pending_nightlight = self._pending_nightlight, None
        if percent is None:
            return GLib.SOURCE_REMOVE
        try:
            set_nightlight(percent)
        except CTL_ERRORS:
            return GLib.SOURCE_REMOVE
        self.refresh_tooltip()
        return GLib.SOURCE_REMOVE

    def on_scale_button(self, scale, event):
        if event.type == Gdk.EventType.BUTTON_PRESS:
            self._dragging.add(scale)
            return False
        self._dragging.discard(scale)
        # Land the final value on release rather than waiting out the debounce.
        if scale is self.nightlight_scale and self._nightlight_timer is not None:
            GLib.source_remove(self._nightlight_timer)
            self._nightlight_timer = None
            self.flush_nightlight()
        return False

    def on_mode_toggled(self, _switch, active):
        if self._updating:
            return False
        try:
            set_mode("auto" if active else "manual")
            # Switching to auto should land the scheduled value immediately
            # instead of leaving the old one up for as long as a tick.
            if active:
                apply_schedule()
        except CTL_ERRORS:
            return False

        try:
            current = get_nightlight()
        except CTL_ERRORS:
            current = None
        self.sync_scale(self.nightlight_scale, current)
        self.refresh_tooltip()
        return False

    def build_menu(self, brightness, nightlight):
        """The everyday interface: a few steps, the Auto switch, and a way in
        to the sliders.

        Steps rather than a slider because DBusMenu has no slider to send. The
        values are the ones worth one click — the fine control is the window
        behind "Adjust...".
        """
        menu = Gtk.Menu()

        menu.append(tray.heading(
            f"Brightness  {brightness}%" if brightness is not None
            else "Brightness unavailable"
        ))
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        for step in (10, 25, 50, 75, 100):
            menu.append(tray.item(f"   {step}%", self.on_brightness_step, step))
        del row

        menu.append(tray.separator())
        if nightlight is not None:
            menu.append(tray.heading(f"Night light  {nightlight}%"))
            for label, value in (
                ("   Off", 0), ("   Light", 25), ("   Medium", 50),
                ("   Warm", 75), ("   Warmest", 100),
            ):
                menu.append(tray.item(label, self.on_nightlight_step, value))

            auto = Gtk.CheckMenuItem(label="Auto (follow time of day)")
            auto.set_active(get_mode() == "auto")
            # Connected after set_active so seeding the box does not write mode.
            auto.connect("toggled", self.on_auto_menu_toggled)
            menu.append(auto)
        else:
            menu.append(tray.heading("Night light unavailable"))

        menu.append(tray.separator())
        menu.append(tray.item("Adjust...", self.on_adjust))
        menu.append(tray.separator())
        menu.append(tray.item("Quit", lambda _item: Gtk.main_quit()))
        return menu

    def on_brightness_step(self, _item, value):
        try:
            set_brightness(value)
        except CTL_ERRORS:
            return
        self.refresh_tooltip()

    def on_nightlight_step(self, _item, value):
        try:
            set_nightlight(value)
        except CTL_ERRORS:
            return
        self.refresh_tooltip()

    def on_auto_menu_toggled(self, item):
        set_mode("auto" if item.get_active() else "manual")
        if item.get_active():
            apply_schedule()
        self.refresh_tooltip()

    def on_adjust(self, _item):
        if self.popup is not None:
            self.close_popup()
        self.open_panel()

    def build_column(self, icon_name, tooltip, lower, value, on_changed):
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.VERTICAL, lower, 100, 1)
        scale.set_inverted(True)
        scale.set_value(value)
        scale.set_size_request(-1, 130)
        scale.set_draw_value(True)
        scale.set_value_pos(Gtk.PositionType.BOTTOM)
        scale.set_tooltip_text(tooltip)
        # Connect after set_value so seeding the slider doesn't apply anything.
        scale.connect("value-changed", on_changed)
        scale.connect("button-press-event", self.on_scale_button)
        scale.connect("button-release-event", self.on_scale_button)

        image = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.MENU)
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        column.pack_start(image, False, False, 0)
        column.pack_start(scale, True, True, 0)
        return column, scale

    def open_panel(self):
        """The slider panel, as an ordinary toplevel window.

        The X11 build made this a Gtk.WindowType.POPUP: override-redirect, so
        the window manager never touched it, positioned by hand against the
        tray icon and dismissed by a seat grab that caught the click landing
        outside it.

        None of those three moves exist for a Wayland client. It cannot ask
        where its tray icon is, cannot place its own toplevel, and cannot grab
        the seat — all deliberate, and all things a compositor owns. So this is
        a normal window that niri is told to float by rule (see
        ensure_window_rules), and it closes on Escape, on its Close button, or
        when it loses focus, which the window manager will now actually report
        because this is a window it manages.
        """
        try:
            brightness = get_brightness()
        except CTL_ERRORS:
            brightness = 50
        try:
            nightlight = get_nightlight()
        except CTL_ERRORS:
            nightlight = 0

        popup = Gtk.Window(title="Brightness")
        popup.set_border_width(10)
        popup.set_resizable(False)

        brightness_column, self.brightness_scale = self.build_column(
            ICON_NAME, "Screen brightness", 1, brightness, self.on_brightness_changed
        )
        nightlight_column, self.nightlight_scale = self.build_column(
            NIGHTLIGHT_ICON_NAME,
            "Night light warmth (holds until 07:00/19:00)",
            0,
            nightlight,
            self.on_nightlight_changed,
        )

        sliders = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        sliders.pack_start(brightness_column, True, True, 0)
        sliders.pack_start(nightlight_column, True, True, 0)

        self.auto_switch = Gtk.Switch()
        self.auto_switch.set_active(get_mode() == "auto")
        self.auto_switch.set_tooltip_text(
            "Night light follows the time of day; off holds the slider value"
        )
        # Connect after set_active so seeding the switch doesn't write state.
        self.auto_switch.connect("state-set", self.on_mode_toggled)

        mode_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        mode_row.pack_start(Gtk.Label(label="Auto"), False, False, 0)
        mode_row.pack_end(self.auto_switch, False, False, 0)

        close = Gtk.Button(label="Close")
        close.connect("clicked", lambda _b: self.close_popup())

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.pack_start(sliders, True, True, 0)
        box.pack_start(
            Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0
        )
        box.pack_start(mode_row, False, False, 0)
        box.pack_start(close, False, False, 0)
        popup.add(box)

        popup.connect("key-press-event", self.on_popup_key_press)
        # A managed window does get told when it loses focus, which the
        # override-redirect popup never was — so the dismiss-on-click-away
        # behaviour survives the port without needing a grab to implement it.
        popup.connect("focus-out-event", lambda *_: self.close_popup())
        popup.connect("destroy", self.on_popup_destroyed)
        popup.show_all()

        self.popup = popup

    def on_popup_destroyed(self, _popup):
        self.popup = None
        self.brightness_scale = None
        self.nightlight_scale = None
        self.auto_switch = None
        self._dragging.clear()

    def on_popup_key_press(self, _popup, event):
        if event.keyval == Gdk.KEY_Escape:
            self.close_popup()
            return True
        return False

    def close_popup(self):
        # destroy() fires the handler that clears the references, so there is
        # one place that knows what closing means and it is not duplicated
        # between the button, Escape, focus-out and the window's own X.
        if self.popup is not None:
            self.popup.destroy()


NIRI_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "niri"
)
PANEL_KDL = os.path.join(NIRI_DIR, "brightness-panel.kdl")
NIRI_CONF = os.path.join(NIRI_DIR, "config.kdl")

PANEL_RULE = """\
// Managed by Scripts/Arch/brightness/brightness-tray.py.
//
// The slider panel is a normal toplevel because a Wayland client cannot make
// an override-redirect one. Floating it and pinning it to the top-right is
// what puts it back where the X11 popup placed itself, next to the bar.
window-rule {
    match app-id=r#"^brightness-tray$"# title="^Brightness$"
    open-floating true
    default-floating-position x=12 y=12 relative-to="top-right"
}
"""


def ensure_window_rules():
    """Write the panel's window rule and make sure niri reads it.

    Idempotent and cheap, so it runs at startup rather than needing an install
    step. Its own file plus an include, for the reason the rest of this repo
    uses that pattern: config.kdl is hand-maintained and nothing here should be
    reformatting it.
    """
    if not os.environ.get("NIRI_SOCKET") or not os.path.isdir(NIRI_DIR):
        return
    try:
        if not os.path.exists(PANEL_KDL) or open(PANEL_KDL).read() != PANEL_RULE:
            tmp = PANEL_KDL + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(PANEL_RULE)
            os.replace(tmp, PANEL_KDL)

        with open(NIRI_CONF, encoding="utf-8") as fh:
            conf = fh.read()
        if "brightness-panel.kdl" not in conf:
            with open(NIRI_CONF, "a", encoding="utf-8") as fh:
                fh.write(
                    "\n// Window rule for Scripts/Arch/brightness slider panel.\n"
                    'include "brightness-panel.kdl"\n'
                )
            subprocess.Popen(["niri", "msg", "action", "load-config-file"])
    except OSError:
        pass


def main():
    # Before any window exists: GTK takes the Wayland app_id from the prgname,
    # and the window rule above matches on it.
    GLib.set_prgname("brightness-tray")
    ensure_window_rules()
    BrightnessTray()
    Gtk.main()


if __name__ == "__main__":
    main()
