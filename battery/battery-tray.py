#!/usr/bin/env python3
"""Power-profile tray icon: which of the four profiles is on, drawn as a
level marker in the bar.

    ++   performance          every ceiling off
    +    default             the machine as it came
    -    battery saver       turbo off, deeper idle states
    --   extreme saver       saver plus hard frequency ceilings

WHY IT IS NOT THE CHARGE PERCENTAGE ANY MORE

It used to draw the battery percentage, with a bolt for charging, colour for
low and critical, and a bar along the bottom for the active profile — four
things fighting over a 32px square, and the profile, the only one of them this
tray can actually change, got the smallest share.

noctalia's own battery widget is already on the bar and already shows charge,
state and time. So the number here was a second, worse copy of something two
centimetres away, and everything drawn around it existed to work around how
little room the number left. Dropping it leaves the icon saying the one thing
nothing else on the bar says, in the space that was being split four ways.

Charge has not gone anywhere — it is in the tooltip, in full, along with time
remaining and battery health.

Colour still carries the direction the profile points: violet for faster than
stock, teal for slower, and the bar's own foreground for stock itself, so the
two saving profiles read as a pair and neither reads as an alarm.

The menu carries the four profiles, handed off to the sibling power-profile
script, the single owner of that state. It also carries the AC follow: with it
on, plugging in switches to performance and unplugging puts back whatever was
on before. This file only spots the transition — the AC line is already read
every poll — and hands it to `power-profile --sync`, which owns the decision
and the memory of what to hand back.

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
# The panel used to hand its icon size over through size-changed. A
# StatusNotifierItem has no such conversation — the bar takes the picture and
# scales it — so the icon is drawn once at a size big enough to survive being
# scaled down to whatever the bar is using. 32 rather than 16 because a bar at
# scale 2, or simply a taller bar, would otherwise show a blurry upscale of a
# 16px bitmap.
ICON_SIZE = 32
FALLBACK_ICON_SIZE = ICON_SIZE

# The marks are drawn, not typeset. Scaling "+" and "-" as glyphs to a common
# box cannot produce a set: a hyphen's ink is a third as wide and a tenth as
# tall as a plus sign's, so fitting either one to the square makes the other
# absurd — fit by width and the dashes come out as fat blocks, fit by height
# and they vanish. Two rectangles with a shared length and thickness have no
# such problem, and are crisper at 32px than hinted glyphs would be anyway.
#
# One symbol is the same size as each of two, so the count is what carries the
# level: + then ++, - then --.
MARK_LENGTH_FRACTION = 0.38
MARK_THICKNESS_FRACTION = 0.105
MARK_GAP_FRACTION = 0.13

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
# name, menu label, icon marker, marker colour (None = the bar's own
# foreground), tooltip
PROFILES = (
    ("performance", "Performance", "++", PERFORMANCE_RGB,
     "Every ceiling off, CPU and GPU biased hard toward speed. Holds on "
     "battery too — picking it here is a pin, not a preference AC gets to "
     "undo"),
    ("default", "Default", "+", None,
     "The machine as it was before any profile was applied — on this laptop "
     "that already means turbo on and no frequency caps"),
    ("saver", "Battery Saver", "-", SAVER_RGB,
     "Turbo off, deeper idle states, batched disk writeback"),
    ("extreme", "Extreme Battery Saver", "--", SAVER_RGB,
     "Battery saver plus hard CPU and GPU frequency ceilings — "
     "noticeably slower"),
)
PROFILE_LABELS = {name: label for name, label, _m, _rgb, _tip in PROFILES}
PROFILE_MARKS = {name: (mark, rgb) for name, _label, mark, rgb, _tip in PROFILES}
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


def draw_marks(ctx, size, count, plus):
    """`count` plus or minus marks in a row, centred in a `size` square.

    A plus is the minus with a second bar across it, so the two families share
    a stroke weight exactly and the eye reads the difference as the extra bar
    rather than as a change of size.
    """
    length = size * MARK_LENGTH_FRACTION
    thick = max(2.0, size * MARK_THICKNESS_FRACTION)
    gap = size * MARK_GAP_FRACTION

    total = count * length + (count - 1) * gap
    left = (size - total) / 2
    middle = size / 2

    for index in range(count):
        centre = left + index * (length + gap) + length / 2
        ctx.rectangle(centre - length / 2, middle - thick / 2, length, thick)
        if plus:
            ctx.rectangle(centre - thick / 2, middle - length / 2, thick, length)
    ctx.fill()


def render_icon(size, default_rgb, profile=DEFAULT_PROFILE):
    """The active power profile as a pixbuf, sized for the bar's icon slot.

    Nothing else is drawn here. The charge state this used to carry lives in
    the tooltip and in noctalia's own battery widget — see the module
    docstring.
    """
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    ctx = cairo.Context(surface)

    mark, rgb = PROFILE_MARKS.get(profile, PROFILE_MARKS[DEFAULT_PROFILE])
    ctx.set_source_rgb(*(rgb or default_rgb))
    # The table spells the marker out — "++", "+", "-", "--" — because that is
    # what it looks like on screen and what it is called everywhere else. The
    # two things the drawing needs fall straight out of it.
    draw_marks(ctx, size, len(mark), mark.startswith("+"))

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
        # The icon depends on the profile and nothing else now, so it is redrawn
        # when the profile changes and not once a poll. sni.set_icon_pixbuf
        # would drop the duplicate anyway; this skips the cairo pass that
        # produces it.
        if self.profile != getattr(self, "_drawn_profile", None):
            self._drawn_profile = self.profile
            self.tray.set_pixbuf(
                render_icon(self.size, self.default_rgb, self.profile)
            )

        lines = [state.describe()]
        if self.profile != DEFAULT_PROFILE:
            # Say when the profile isn't yours, so a machine sitting in
            # performance nobody selected explains itself on hover.
            reason = " — automatic on AC" if ac_engaged() else ""
            lines.append(f"Power: {PROFILE_LABELS[self.profile]}{reason}")
        self.tray.set_tooltip(" · ".join(lines))

        # Rebuild the menu only when something in it actually changed.
        #
        # This used to run on every refresh, i.e. every POLL_INTERVAL_MS. A
        # StatusNotifierItem menu is served over DBusMenu and every rebuild
        # bumps its revision, so the host was being told the menu had changed
        # five times a second-and-a-bit forever — 10,255 revisions by the time
        # it was measured.
        #
        # That was worth stopping on its own, but it was not why clicking did
        # nothing: noctalia never reads the menu at all, so there was nothing
        # to interrupt. That turned out to be ItemIsMenu — see lib/sni.py.
        #
        # The tooltip still updates every poll, because it genuinely changes
        # every poll.
        menu_state = (self.profile, self.auto, self._switching is None)
        if menu_state != getattr(self, "_menu_state", None):
            self._menu_state = menu_state
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
