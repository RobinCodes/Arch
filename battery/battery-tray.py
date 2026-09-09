#!/usr/bin/env python3
"""Battery tray icon for XFCE: the charge percentage drawn as text in the
panel, with the charging state carried by a lightning bolt and by colour.

The panel gives a tray icon a small square (16px here), so the digits are
fitted to that box rather than drawn at a fixed size — "100" simply renders
smaller than "72" instead of spilling out of the icon.

Colour encodes the state at a glance:

  green   charging
  amber   on battery, at or below LOW_PERCENT
  red     on battery, at or below CRITICAL_PERCENT
  panel   anything else (discharging normally, full, or held by a
          charge threshold)

The menu also carries the four power profiles — performance, default, battery
saver and extreme battery saver — which are handed off to the sibling
power-profile script, the single owner of that state. Anything other than
default shows as a bar along the bottom of the icon.

The same menu carries the AC follow: with it on, plugging in switches to
performance and unplugging puts back whatever was on before. This file only
spots the transition — the AC line is already read every poll for the charging
colour — and hands it to `power-profile --sync`, which owns the decision and
the memory of what to hand back.

The battery side is read straight from sysfs, so it needs nothing beyond
PyGObject."""

import glob
import os
import subprocess
import sys
import warnings

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import tray  # noqa: E402

warnings.filterwarnings("ignore", category=DeprecationWarning)

POLL_INTERVAL_MS = 5000
LOW_PERCENT = 20
CRITICAL_PERCENT = 10
# The panel used to hand its icon size over through size-changed. A
# StatusNotifierItem has no such conversation — the bar takes the picture and
# scales it — so the icon is drawn once at a size big enough to survive being
# scaled down to whatever the bar is using. 32 rather than 16 because a bar at
# scale 2, or simply a taller bar, would otherwise show a blurry upscale of a
# 16px bitmap.
ICON_SIZE = 32
FALLBACK_ICON_SIZE = ICON_SIZE

# Share of the icon's height the digits get. Charging gives up the top of the
# box to the bolt and an active profile the bottom to its bar; all three must
# sum to no more than 1 to stay disjoint.
TEXT_FRACTION = 0.94
CHARGING_TEXT_FRACTION = 0.60
BOLT_FRACTION = 0.40
MARKER_FRACTION = 0.22

CHARGING_RGB = (0.55, 0.80, 0.35)
LOW_RGB = (0.96, 0.71, 0.20)
CRITICAL_RGB = (0.93, 0.35, 0.32)
SAVER_RGB = (0.35, 0.74, 0.86)
PERFORMANCE_RGB = (0.78, 0.55, 0.95)
# Used when the panel is in dark mode, where the GTK theme's foreground
# colour isn't necessarily the one the panel actually paints against.
DARK_PANEL_RGB = (0.93, 0.93, 0.93)

POWER_SUPPLY_DIR = "/sys/class/power_supply"
# noctalia's settings window. The XFCE power manager it used to open does not
# exist here, and nothing in this session owns the same set of knobs — the four
# profiles in this menu are this repo's own, written straight to sysfs.
SETTINGS_ARGV = ("noctalia", "msg", "settings-open")

# Resolved from this file so the Scripts folder stays movable.
POWER_PROFILE_BIN = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "power-profile"
)
STATE_DIR = os.path.join(
    os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")),
    "battery",
)
PROFILE_FILE = os.path.join(STATE_DIR, "profile")
AUTO_FILE = os.path.join(STATE_DIR, "auto")
# Written by power-profile while AC is the reason performance is on; it holds
# the profile that will be put back on unplug.
AC_RESTORE_FILE = os.path.join(STATE_DIR, "ac-restore")
DEFAULT_PROFILE = "default"
# name, menu label, bar width as a share of the icon's, bar colour, tooltip
PROFILES = (
    ("performance", "Performance", 0.90, PERFORMANCE_RGB,
     "Every ceiling off, CPU and GPU biased hard toward speed. Holds on "
     "battery too — picking it here is a pin, not a preference AC gets to "
     "undo"),
    ("default", "Default", 0.0, None,
     "The machine as it was before any profile was applied — on this laptop "
     "that already means turbo on and no frequency caps"),
    ("saver", "Battery Saver", 0.50, SAVER_RGB,
     "Turbo off, deeper idle states, batched disk writeback"),
    ("extreme", "Extreme Battery Saver", 0.90, SAVER_RGB,
     "Battery saver plus hard CPU and GPU frequency ceilings — "
     "noticeably slower"),
)
PROFILE_LABELS = {name: label for name, label, _w, _rgb, _tip in PROFILES}
PROFILE_BARS = {name: (width, rgb) for name, _label, width, rgb, _tip in PROFILES}
# Long enough to cover a polkit prompt if the sudoers rule isn't installed.
PROFILE_POLL_MS = 200
PROFILE_TIMEOUT_MS = 120_000

READ_ERRORS = (OSError, ValueError, IndexError)


def read_profile():
    """The saved power profile. Anything unset or unrecognised is default, so
    a missing or corrupt state file reports the machine as untouched rather
    than claiming a profile that was never applied."""
    try:
        with open(PROFILE_FILE) as state:
            name = state.read().strip()
    except OSError:
        return DEFAULT_PROFILE
    return name if name in PROFILE_LABELS else DEFAULT_PROFILE


def read_auto():
    """Whether the profile follows the AC line. Unset means on, matching
    power-profile — a state file that hasn't been written yet is not the same
    as one that says off."""
    try:
        with open(AUTO_FILE) as state:
            return state.read().strip() != "off"
    except OSError:
        return True


def ac_engaged():
    """True while AC is the reason the machine is in performance, which is
    exactly when there's a profile waiting to be handed back."""
    return os.path.exists(AC_RESTORE_FILE)


def read_attr(device, name):
    """A sysfs attribute as a stripped string, or None if it isn't there."""
    try:
        with open(os.path.join(device, name)) as attr:
            return attr.read().strip()
    except OSError:
        return None


def read_int(device, name):
    value = read_attr(device, name)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def find_battery():
    """The laptop's own battery, or None on a desktop.

    Peripherals (this machine's touchpad reports one) also show up under
    /sys/class/power_supply and are excluded by their scope=Device.
    """
    for device in sorted(glob.glob(os.path.join(POWER_SUPPLY_DIR, "*"))):
        if read_attr(device, "type") != "Battery":
            continue
        if read_attr(device, "scope") == "Device":
            continue
        return device
    return None


def on_ac_power():
    for device in sorted(glob.glob(os.path.join(POWER_SUPPLY_DIR, "*"))):
        if read_attr(device, "type") != "Mains":
            continue
        if read_attr(device, "online") == "1":
            return True
    return False


def seconds_left(device, status):
    """Time to full or empty, from the current draw. None if idle or unknown.

    Batteries report either energy (µWh/µW) or charge (µAh/µA); the ratio
    works out to hours either way, so only the pair has to match.
    """
    for now_attr, full_attr, rate_attr in (
        ("energy_now", "energy_full", "power_now"),
        ("charge_now", "charge_full", "current_now"),
    ):
        now = read_int(device, now_attr)
        full = read_int(device, full_attr)
        rate = read_int(device, rate_attr)
        if now is None or full is None or not rate:
            continue
        remaining = full - now if status == "Charging" else now
        return remaining / abs(rate) * 3600
    return None


def health_percent(device):
    """Full charge as a share of the design capacity, or None."""
    for full_attr, design_attr in (
        ("energy_full", "energy_full_design"),
        ("charge_full", "charge_full_design"),
    ):
        full = read_int(device, full_attr)
        design = read_int(device, design_attr)
        if full is None or not design:
            continue
        return round(full / design * 100)
    return None


def format_duration(seconds):
    minutes = round(seconds / 60)
    return f"{minutes // 60}:{minutes % 60:02d}"


def panel_is_dark():
    """Whether the bar is painting itself dark.

    Asked of noctalia rather than of xfconf, which is the same question to a
    different owner. The GTK theme is still not a reliable answer: the bar
    picks its own palette and a GTK app under Wayland has no way to read it.
    """
    try:
        out = subprocess.run(
            ["noctalia", "msg", "theme-mode-get"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip().lower()
    except (subprocess.SubprocessError, OSError):
        return True  # noctalia ships dark; guessing dark is the safer miss
    return out == "dark"


def default_fg_rgb():
    """The colour the bar most likely paints its own text in."""
    if panel_is_dark():
        return DARK_PANEL_RGB
    colour = Gtk.Label().get_style_context().get_color(Gtk.StateFlags.NORMAL)
    return colour.red, colour.green, colour.blue


class BatteryState:
    def __init__(self, device):
        self.percent = read_int(device, "capacity")
        self.status = read_attr(device, "status") or "Unknown"
        self.on_ac = on_ac_power()
        self.seconds = seconds_left(device, self.status)
        self.health = health_percent(device)

    @property
    def charging(self):
        return self.status == "Charging"

    @property
    def draining(self):
        # "Unknown" on AC is how a battery held at its charge threshold often
        # reports; only treat a clear Discharging as running down.
        return self.status == "Discharging"

    def colour(self, default_rgb):
        if self.charging:
            return CHARGING_RGB
        if self.draining and self.percent is not None:
            if self.percent <= CRITICAL_PERCENT:
                return CRITICAL_RGB
            if self.percent <= LOW_PERCENT:
                return LOW_RGB
        return default_rgb

    def describe(self):
        if self.percent is None:
            return "Battery: unknown"

        if self.charging:
            state = "charging"
        elif self.status == "Full":
            state = "full"
        elif self.draining:
            state = "on battery"
        elif self.on_ac:
            # Plugged in but not taking charge: a charge threshold holding it.
            state = "plugged in, not charging"
        else:
            state = self.status.lower()

        lines = [f"Battery: {self.percent}% — {state}"]
        if self.seconds and self.charging:
            lines.append(f"{format_duration(self.seconds)} until full")
        elif self.seconds and self.draining:
            lines.append(f"{format_duration(self.seconds)} remaining")
        if self.health is not None:
            lines.append(f"Health: {self.health}% of design capacity")
        return "\n".join(lines)


def draw_bolt(ctx, size):
    """A lightning bolt in the reserved top-right corner. Outlined so it keeps
    its shape against a light panel as well as a dark one."""
    bolt = ((0.62, 0.00), (0.16, 0.56), (0.44, 0.56), (0.34, 1.00), (0.84, 0.40),
            (0.54, 0.40))
    span = size * BOLT_FRACTION
    x0, y0 = size - span, 0.0

    ctx.new_path()
    for index, (bx, by) in enumerate(bolt):
        point = (x0 + bx * span, y0 + by * span)
        ctx.line_to(*point) if index else ctx.move_to(*point)
    ctx.close_path()

    # Scale the outline off the bolt, not the icon: a width picked for the icon
    # is a sixth of the bolt's own size and rounds it off into a blob.
    ctx.set_source_rgba(0, 0, 0, 0.45)
    ctx.set_line_width(max(0.7, span * 0.10))
    ctx.set_line_join(cairo.LINE_JOIN_MITER)
    ctx.stroke_preserve()
    ctx.set_source_rgb(*CHARGING_RGB)
    ctx.fill()


def draw_profile_bar(ctx, size, profile):
    """A bar along the bottom edge marking an active profile: teal for the
    saving ones — half the icon's width for saver, near full for extreme —
    and violet across the full width for performance.

    Direction is the reason the two families differ by hue rather than by
    length alone: a longer teal bar means slower, so a longer bar can't also
    mean faster. Neither hue is one the digits use, so the bar never reads as
    a charge state.
    """
    width, rgb = PROFILE_BARS[profile]
    span = width * size
    height = max(1.5, size * 0.10)
    ctx.set_source_rgb(*rgb)
    ctx.rectangle((size - span) / 2, size - height - max(0.5, size * 0.02),
                  span, height)
    ctx.fill()


def draw_text(ctx, text, box):
    """Fit `text` to `box` (x, y, w, h) and draw it centred inside it."""
    x, y, width, height = box
    ctx.set_font_size(height)
    extents = ctx.text_extents(text)
    if not extents.width or not extents.height:
        return
    ctx.set_font_size(
        height * min(width / extents.width, height / extents.height)
    )
    extents = ctx.text_extents(text)
    # Centre on the ink extents rather than the font's line box: digits carry
    # no ascender or descender, so the line box would sit them too high.
    ctx.move_to(
        x + (width - extents.width) / 2 - extents.x_bearing,
        y + height - (height - extents.height) / 2 - (extents.height + extents.y_bearing),
    )
    ctx.show_text(text)


def render_icon(size, state, default_rgb, profile=DEFAULT_PROFILE):
    """The percentage as a pixbuf sized for the tray's current icon slot.

    The tray allocates a square (xfce4-panel squashes anything wider), so
    neither marker can sit beside the digits — the bolt takes the top-right
    corner while charging, the saver bar the bottom edge, and the digits give
    up the height to keep clear of both rather than overlapping them into an
    unreadable smudge.
    """
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    ctx = cairo.Context(surface)
    ctx.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)

    text = "?" if state.percent is None else str(state.percent)
    if state.charging:
        top, height = (size * (1 - CHARGING_TEXT_FRACTION),
                       size * CHARGING_TEXT_FRACTION)
    else:
        top, height = 0.0, size * TEXT_FRACTION
    marked = profile != DEFAULT_PROFILE
    if marked:
        height -= size * MARKER_FRACTION

    ctx.set_source_rgb(*state.colour(default_rgb))
    draw_text(ctx, text, (0.0, top, size, height))

    if state.charging:
        draw_bolt(ctx, size)
    if marked:
        draw_profile_bar(ctx, size, profile)

    surface.flush()
    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)


class BatteryTray:
    def __init__(self):
        self.device = find_battery()
        self.size = FALLBACK_ICON_SIZE
        self.default_rgb = default_fg_rgb()
        self.profile = read_profile()
        self.auto = read_auto()
        self._switching = None
        self._queued = []
        self._waited_ms = 0
        # None rather than the current reading, so the first check always syncs:
        # the AC line can have moved while nothing was running to notice.
        self._on_ac = None

        self.tray = tray.Tray(
            "battery-tray", "battery-symbolic", "Battery",
            category=tray.CATEGORY.HARDWARE,
        )
        self.refresh()
        GLib.timeout_add(POLL_INTERVAL_MS, self.on_poll)
        # sysfs doesn't survive a reboot but the state file does, so at login
        # the saved profile has to be written back out — then settled against
        # the AC line, which may well have changed while this machine was off.
        self.reapply_profile()
        self.check_ac()

    def refresh(self):
        # Picked up from the files rather than held in memory, so a profile or
        # follow setting changed from the CLI shows up here too.
        self.profile = read_profile()
        self.auto = read_auto()

        if self.device is None or not os.path.isdir(self.device):
            # A battery can appear late at boot, or be removed on a docked
            # machine; look again rather than staying blank forever.
            self.device = find_battery()
        if self.device is None:
            self.tray.set_visible(False)
            return
        self.tray.set_visible(True)

        try:
            state = BatteryState(self.device)
        except READ_ERRORS:
            return
        self.tray.set_pixbuf(
            render_icon(self.size, state, self.default_rgb, self.profile),
            state.describe(),
        )
        lines = [state.describe()]
        if self.profile != DEFAULT_PROFILE:
            # Say when the profile isn't yours, so a machine sitting in
            # performance nobody selected explains itself on hover.
            reason = " — automatic on AC" if ac_engaged() else ""
            lines.append(f"Power: {PROFILE_LABELS[self.profile]}{reason}")
        self.tray.set_tooltip(" · ".join(lines))
        # The menu carries the live profile state, so it is rebuilt with the
        # icon rather than on click: a StatusNotifierItem menu is sent over
        # D-Bus ahead of being shown, and there is no open event to rebuild on.
        self.tray.set_menu(self.build_menu())

    def check_ac(self):
        """Hand every plug and unplug to power-profile, which decides what it
        means.

        Polled off the same sysfs read the icon already does rather than pushed
        from udev or UPower: it costs a file read every five seconds and keeps
        this file's only dependency the panel itself. The delay is bounded by
        the poll interval, which is well inside the time it takes a laptop to
        notice it's on mains anyway.
        """
        on_ac = on_ac_power()
        if on_ac == self._on_ac:
            return
        self._on_ac = on_ac
        self.spawn_profile("--sync")

    def on_poll(self):
        self.check_ac()
        self.refresh()
        return True

    def build_menu(self):
        menu = Gtk.Menu()

        group = []
        for name, label, _width, _rgb, tooltip in PROFILES:
            item = Gtk.RadioMenuItem.new_with_label(group, label)
            group = item.get_group()
            item.set_tooltip_text(tooltip)
            item.set_active(name == self.profile)
            item.set_sensitive(self._switching is None)
            # Connect after set_active so seeding the group doesn't apply
            # anything.
            item.connect("toggled", self.on_profile_toggled, name)
            menu.append(item)
        menu.append(Gtk.SeparatorMenuItem())

        follow = Gtk.CheckMenuItem(label="Performance on AC")
        follow.set_tooltip_text(
            "Switch to performance when the charger goes in, and back to the "
            "profile that was on before when it comes out. Picking a profile "
            "above while plugged in replaces that — your choice is what stays "
            "on when you unplug"
        )
        follow.set_active(self.auto)
        follow.set_sensitive(self._switching is None)
        follow.connect("toggled", self.on_auto_toggled)
        menu.append(follow)
        menu.append(Gtk.SeparatorMenuItem())

        settings = Gtk.MenuItem(label="noctalia Settings")
        settings.connect("activate", self.on_settings)
        menu.append(settings)
        menu.append(Gtk.SeparatorMenuItem())

        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", lambda _item: Gtk.main_quit())
        menu.append(quit_item)

        return menu

    def on_profile_toggled(self, item, name):
        # Every switch toggles twice — once off the old item, once onto the
        # new one — and only the second is a request to do anything.
        if item.get_active():
            self.set_profile(name)

    def on_auto_toggled(self, item):
        self.auto = item.get_active()
        # --auto on applies straight away if we're already plugged in, so this
        # is a profile switch like any other and goes through the same queue.
        self.spawn_profile("--auto", "on" if self.auto else "off")

    def spawn_profile(self, *args):
        """power-profile in the background, with the radio items held
        insensitive until it exits.

        Not run inline: without the sudoers rule the script falls back to a
        polkit prompt, and a synchronous call would stall the tray — icon,
        tooltip and menu — for as long as the password dialog is up.

        A second call while one is in flight is queued rather than dropped: at
        login the reapply and the AC sync are issued back to back, and losing
        the sync would leave the machine on whatever profile it shut down with.
        Identical calls collapse, so a run held up behind a password prompt
        can't accumulate a poll's worth of syncs to replay afterwards.
        """
        if self._switching is not None:
            if args not in self._queued:
                self._queued.append(args)
            return
        try:
            proc = subprocess.Popen(
                [POWER_PROFILE_BIN, *args],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except OSError as error:
            self.report_failure(str(error))
            return
        self._switching = proc
        self._waited_ms = 0
        GLib.timeout_add(PROFILE_POLL_MS, self.on_profile_wait)

    def set_profile(self, name):
        self.spawn_profile(name)

    def reapply_profile(self):
        if self.profile != DEFAULT_PROFILE:
            self.spawn_profile("--reapply")

    def on_profile_wait(self):
        proc = self._switching
        if proc is None:
            return GLib.SOURCE_REMOVE

        self._waited_ms += PROFILE_POLL_MS
        if proc.poll() is None:
            if self._waited_ms < PROFILE_TIMEOUT_MS:
                return GLib.SOURCE_CONTINUE
            # A prompt nobody is ever going to answer; don't leave the menu
            # wedged behind it.
            proc.kill()

        self._switching = None
        _out, err = proc.communicate()
        if proc.returncode:
            self.report_failure((err or b"").decode(errors="replace").strip())
        self.refresh()
        if self._queued:
            self.spawn_profile(*self._queued.pop(0))
        return GLib.SOURCE_REMOVE

    def report_failure(self, message):
        dialog = Gtk.MessageDialog(
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.CLOSE,
            text="Couldn't switch the power profile",
        )
        dialog.format_secondary_text(message or "power-profile failed.")
        dialog.run()
        dialog.destroy()

    def on_settings(self, _item):
        try:
            subprocess.Popen(SETTINGS_ARGV)
        except OSError:
            pass


def main():
    GLib.set_prgname("battery-tray")
    if find_battery() is None:
        raise SystemExit("no system battery found under /sys/class/power_supply")
    BatteryTray()
    Gtk.main()


if __name__ == "__main__":
    main()
