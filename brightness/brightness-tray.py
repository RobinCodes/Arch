#!/usr/bin/env python3
"""Brightness tray icon for XFCE, mirroring the pulseaudio panel plugin's UX:
click the icon for a slider popup, scroll over it to step brightness.

The popup carries two sliders — backlight brightness and night light warmth —
plus an Auto switch for the night light:

  Auto on   nightlight-auto drives warmth from the time of day; moving the
            slider parks an override until the next 07:00/19:00 boundary.
  Auto off  the slider value stays put indefinitely.

Both are handed off to the sibling nightlight script, which owns the state."""

import os
import subprocess
import time
import warnings

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk

warnings.filterwarnings("ignore", category=DeprecationWarning)

STEP = 5
POLL_INTERVAL_MS = 2000
ICON_NAME = "display-brightness-symbolic"
NIGHTLIGHT_ICON_NAME = "night-light-symbolic"
# Clicking the icon while the popup is grabbing input closes the popup and can
# still deliver an "activate" afterwards; ignore one that lands immediately.
REOPEN_GUARD_US = 250_000

# ../nightlight/, resolved from this file so the Scripts folder stays movable.
_NIGHTLIGHT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "nightlight")
)
NIGHTLIGHT_BIN = os.path.join(_NIGHTLIGHT_DIR, "nightlight")
NIGHTLIGHT_AUTO_BIN = os.path.join(_NIGHTLIGHT_DIR, "nightlight-auto")
REDSHIFT_CONF = os.path.expanduser("~/.config/redshift/redshift.conf")
STATE_DIR = os.path.join(
    os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")),
    "nightlight",
)
OVERRIDE_FILE = os.path.join(STATE_DIR, "override-until")
MODE_FILE = os.path.join(STATE_DIR, "mode")
# Mirrors the scale in the nightlight script: 0% = 6500K, 100% = 2500K.
NEUTRAL_KELVIN = 6500
KELVIN_PER_PERCENT = 40
# Each nightlight call spawns redshift (~30ms), so coalesce a drag instead of
# launching a process per pixel of slider travel.
APPLY_DEBOUNCE_MS = 120

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
    with open(REDSHIFT_CONF) as conf:
        for line in conf:
            if line.startswith("temp-day="):
                kelvin = int(line.split("=", 1)[1])
                return round((NEUTRAL_KELVIN - kelvin) / KELVIN_PER_PERCENT)
    raise ValueError(f"no temp-day in {REDSHIFT_CONF}")


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
        self.icon = Gtk.StatusIcon.new_from_icon_name(ICON_NAME)
        self.icon.set_tooltip_text("Brightness")
        self.icon.connect("activate", self.on_click)
        self.icon.connect("scroll-event", self.on_scroll)
        self.popup = None
        self.brightness_scale = None
        self.nightlight_scale = None
        self.auto_switch = None
        self.seat = None
        self._updating = False
        self._dragging = set()
        self._closed_at = 0
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
            self.icon.set_tooltip_text("\n".join(lines))
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

    def on_scroll(self, _icon, event):
        if event.direction == Gdk.ScrollDirection.UP:
            delta = STEP
        elif event.direction == Gdk.ScrollDirection.DOWN:
            delta = -STEP
        elif event.direction == Gdk.ScrollDirection.SMOOTH:
            # Touchpads and high-resolution wheels send SMOOTH instead of
            # UP/DOWN; without this branch scrolling does nothing at all.
            _ok, _dx, dy = event.get_scroll_deltas()
            if not dy:
                return
            delta = -STEP if dy > 0 else STEP
        else:
            return

        try:
            percent = set_brightness(get_brightness() + delta)
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

    def on_click(self, _icon):
        if self.popup is not None:
            self.close_popup()
            return
        if GLib.get_monotonic_time() - self._closed_at < REOPEN_GUARD_US:
            return

        try:
            brightness = get_brightness()
        except CTL_ERRORS:
            brightness = 50
        try:
            nightlight = get_nightlight()
        except CTL_ERRORS:
            nightlight = 0

        popup = Gtk.Window(type=Gtk.WindowType.POPUP)
        popup.set_decorated(False)
        popup.set_skip_taskbar_hint(True)
        popup.set_skip_pager_hint(True)
        popup.set_border_width(6)

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

        sliders = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
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

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.pack_start(sliders, True, True, 0)
        box.pack_start(
            Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0
        )
        box.pack_start(mode_row, False, False, 0)
        popup.add(box)

        popup.connect("button-press-event", self.on_popup_button_press)
        popup.connect("key-press-event", self.on_popup_key_press)

        popup.move(*self.popup_position(popup))
        popup.show_all()

        # A POPUP window is override-redirect, so the WM never focuses it and
        # focus-out-event never fires. Grab the seat instead: that both routes
        # scroll events here and lets us see the click that dismisses us.
        seat = popup.get_window().get_display().get_default_seat()
        seat.grab(
            popup.get_window(),
            Gdk.SeatCapabilities.ALL_POINTING | Gdk.SeatCapabilities.KEYBOARD,
            True,
            None,
            None,
            None,
            None,
        )

        self.popup = popup
        self.seat = seat

    def popup_position(self, popup):
        """Place the popup against the tray icon, kept inside the work area."""
        natural = popup.get_preferred_size()[1]
        width, height = natural.width, natural.height
        display = Gdk.Display.get_default()

        ok, _screen, area, _orientation = self.icon.get_geometry()
        if ok:
            x, below, above = area.x, area.y + area.height, area.y - height
            monitor = display.get_monitor_at_point(area.x, area.y)
        else:
            # Some trays refuse to report geometry; fall back to the pointer.
            seat = display.get_default_seat()
            _screen, px, py = seat.get_pointer().get_position()
            x, below, above = px, py, py - height
            monitor = display.get_monitor_at_point(px, py)

        work = (monitor or display.get_monitor(0)).get_workarea()
        # Below the icon for a top panel, above it for a bottom one.
        y = below if below + height <= work.y + work.height else above
        x = max(work.x, min(x, work.x + work.width - width))
        y = max(work.y, min(y, work.y + work.height - height))
        return x, y

    def on_popup_button_press(self, popup, event):
        # owner_events=True means clicks on a slider arrive at the slider;
        # anything that reaches the window itself landed outside it.
        alloc = popup.get_allocation()
        if not (0 <= event.x <= alloc.width and 0 <= event.y <= alloc.height):
            self.close_popup()
            return True
        return False

    def on_popup_key_press(self, _popup, event):
        if event.keyval == Gdk.KEY_Escape:
            self.close_popup()
            return True
        return False

    def close_popup(self):
        if self.popup is None:
            return
        if self.seat is not None:
            self.seat.ungrab()
            self.seat = None
        self.popup.destroy()
        self.popup = None
        self.brightness_scale = None
        self.nightlight_scale = None
        self.auto_switch = None
        self._dragging.clear()
        self._closed_at = GLib.get_monotonic_time()


def main():
    BrightnessTray()
    Gtk.main()


if __name__ == "__main__":
    main()
