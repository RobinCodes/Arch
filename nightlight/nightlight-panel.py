#!/usr/bin/env python3
"""The night light panel: a slider that overrides the schedule, and the switch
that hands control back to it.

WHY A WINDOW AND NOT A MENU ITEM

DBusMenu carries labels, checkmarks, radio items and separators. It has no
slider, and no way to send one — a Gtk.Scale packed into a Gtk.MenuItem does
not survive being flattened to data and arrives in the bar as an empty row. So
the tray menu carries coarse steps for the common case and this window is where
fine adjustment happens.

It is an ordinary toplevel. A Wayland client cannot make an override-redirect
window, cannot place itself, and cannot grab the seat, so the X11 trick of a
POPUP positioned against the tray icon is not available in any form. niri floats
it by rule instead (see nightlight-tray.py), and it closes on Escape, on Close,
or when it loses focus — which a managed window is actually told about.

WHY IT PAINTS ITSELF

GTK has never heard of noctalia, so left alone this comes up in default Adwaita
light: a white rectangle under a dark bar. ../lib/noctalia.py reads the palette
noctalia writes for its GTK template, and the stylesheet below is built from
those colours, so the window matches the bar and follows a theme change without
being reopened. With the template not enabled the palette is empty, every
lookup falls back, and this renders in stock GTK — worse-looking, not broken.

DRAGGING

Every value the slider passes is applied live, because a night light you cannot
see while choosing it is not adjustable. What is *not* done per value is the
state write and the mode switch: those land once, when the drag ends. Otherwise
a single drag from 0 to 70 would run gamma-apply seventy times, and on the
gammastep backend each of those is a process launch.
"""

import os
import subprocess
import sys
import warnings

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import noctalia  # noqa: E402

warnings.filterwarnings("ignore", category=DeprecationWarning)

HERE = os.path.dirname(os.path.abspath(__file__))
NIGHTLIGHT = os.path.join(HERE, "nightlight")
NIGHTLIGHT_AUTO = os.path.join(HERE, "nightlight-auto")
GAMMA_APPLY = os.path.join(HERE, "gamma-apply")

APP_ID = "nightlight-panel"
WINDOW_TITLE = "Night light"

NEUTRAL_KELVIN = 6500
KELVIN_PER_PERCENT = 40

# While a drag is in flight the temperature is pushed straight at gamma-apply,
# skipping the nightlight CLI: one process instead of two, and no state write
# per value. 40ms is below the rate a slider can be dragged meaningfully but
# high enough that the backend is not relaunched on every pixel.
DRAG_APPLY_MS = 40


def run(argv, **kwargs):
    """A repo script, with its output captured and failure turned into None.

    Nothing here is worth taking the window down for: a night light that will
    not apply should leave the slider where the user put it and say nothing,
    because the alternative is a traceback over the top of everything.
    """
    try:
        out = subprocess.run(
            argv, capture_output=True, text=True, timeout=10, **kwargs
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def get_percent():
    value = run([NIGHTLIGHT, "--print"])
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return 0


def get_mode():
    return "manual" if run([NIGHTLIGHT, "--mode"]) == "manual" else "auto"


def scheduled_percent():
    value = run([NIGHTLIGHT_AUTO, "--print"])
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return None


def kelvin_for(percent):
    return NEUTRAL_KELVIN - percent * KELVIN_PER_PERCENT


def stylesheet(palette):
    """The panel's CSS, in noctalia's colours.

    Only the parts that would otherwise clash are named: background, text, and
    the slider's own trough and handle. Everything else is left to GTK, because
    a stylesheet that restates the whole widget set is one that breaks on the
    next GTK release for no benefit here.

    The fallbacks are GTK's own theme colours rather than invented hex, so with
    no noctalia palette this is a no-op instead of a second, worse theme.
    """
    bg = palette.get("window_bg_color", "@theme_bg_color")
    fg = palette.get("window_fg_color", "@theme_fg_color")
    card = palette.get("card_bg_color", "@theme_base_color")
    accent = palette.get("accent_bg_color", "@theme_selected_bg_color")
    on_accent = palette.get("accent_fg_color", "@theme_selected_fg_color")
    # surface_variant is the palette's "slightly off the background" step, which
    # is exactly what an unfilled trough wants. Falling back to the card colour
    # keeps it distinguishable from the window when that name is missing.
    trough = palette.get("surface_variant_color", card)

    # `background-image: none` on everything that takes a background is not
    # decoration: Adwaita paints its controls with gradient images, and a rule
    # that only sets background-color leaves the image on top of it. That is
    # what a switch styled this way looks like with the line missing — stock
    # Adwaita red, with the accent colour underneath where it cannot be seen.
    return f"""
    window.nightlight-panel {{
        background-color: {bg};
        color: {fg};
        border: 1px solid {card};
        border-radius: 10px;
    }}
    window.nightlight-panel label {{
        color: {fg};
    }}
    window.nightlight-panel .dim-label {{
        opacity: 0.65;
    }}
    window.nightlight-panel .value-label {{
        font-size: 15pt;
        font-weight: bold;
    }}
    window.nightlight-panel scale value,
    window.nightlight-panel scale marks label {{
        color: {fg};
        opacity: 0.65;
    }}
    window.nightlight-panel scale trough {{
        background-image: none;
        background-color: {trough};
        border: none;
        min-height: 6px;
        border-radius: 3px;
    }}
    window.nightlight-panel scale highlight {{
        background-image: none;
        background-color: {accent};
        border: none;
        border-radius: 3px;
    }}
    window.nightlight-panel scale slider {{
        background-image: none;
        background-color: {accent};
        border: 2px solid {bg};
        border-radius: 50%;
        min-width: 16px;
        min-height: 16px;
    }}
    window.nightlight-panel switch {{
        background-image: none;
        background-color: {trough};
        border: none;
        border-radius: 12px;
    }}
    window.nightlight-panel switch:checked {{
        background-image: none;
        background-color: {accent};
    }}
    window.nightlight-panel switch slider {{
        background-image: none;
        background-color: {fg};
        border: none;
        border-radius: 50%;
    }}
    window.nightlight-panel switch:checked slider {{
        background-image: none;
        background-color: {on_accent};
    }}
    window.nightlight-panel button {{
        background-image: none;
        background-color: {card};
        color: {fg};
        border: none;
        border-radius: 6px;
        padding: 6px 14px;
    }}
    window.nightlight-panel button:hover {{
        background-image: none;
        background-color: {accent};
        color: {on_accent};
    }}
    """


class Panel(Gtk.Window):
    """The slider window. One at a time — the tray keeps the reference."""

    def __init__(self, on_change=None):
        super().__init__(title=WINDOW_TITLE)
        # Called after anything that changes intensity or mode, so a tray
        # holding this window repaints its icon without polling for it.
        self._on_change = on_change
        self._dragging = False
        self._pending_apply = 0
        self._pending_percent = None

        self.set_border_width(16)
        self.set_resizable(False)
        # No titlebar. GTK3 draws client-side decorations on Wayland whether or
        # not the compositor would have drawn its own, and the result here was a
        # header with a maximise and a close button on a panel that is 330px
        # wide, cannot be resized, and already closes three other ways. Dropping
        # it also stops those two buttons inheriting the button rule below,
        # which left them as unlabelled dark squares.
        self.set_decorated(False)
        self.get_style_context().add_class("nightlight-panel")

        self._palette = noctalia.Palette(on_change=lambda _p: self._restyle())
        self._css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), self._css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
        self._restyle()

        percent = get_percent()
        # Seeds the no-op guard in _commit: the value the panel opened at has
        # already been committed, by whatever set it.
        self._last_committed = percent

        self._value_label = Gtk.Label()
        self._value_label.set_xalign(0.0)
        self._value_label.get_style_context().add_class("value-label")

        self._detail_label = Gtk.Label()
        self._detail_label.set_xalign(0.0)
        self._detail_label.get_style_context().add_class("dim-label")

        self._scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self._scale.set_size_request(300, -1)
        self._scale.set_draw_value(False)
        self._scale.set_value(percent)
        # Marks at the schedule's own two ends, so the slider says where the
        # automatic curve would have put it without needing a second readout.
        self._scale.add_mark(0, Gtk.PositionType.BOTTOM, "off")
        self._scale.add_mark(70, Gtk.PositionType.BOTTOM, "night")
        # Connected after set_value, so seeding the slider does not apply
        # anything or knock the mode over to manual on open.
        self._scale.connect("value-changed", self._on_value_changed)
        self._scale.connect("button-press-event", self._on_button)
        self._scale.connect("button-release-event", self._on_button)
        self._scale.connect("key-press-event", self._on_scale_key)

        self._auto_switch = Gtk.Switch()
        self._auto_switch.set_active(get_mode() == "auto")
        self._auto_switch.set_tooltip_text(
            "On: the intensity follows the time of day.\n"
            "Off: it stays exactly where you put it."
        )
        self._auto_switch.connect("state-set", self._on_auto_toggled)

        auto_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        auto_label = Gtk.Label(label="Automatic schedule")
        auto_label.set_xalign(0.0)
        auto_row.pack_start(auto_label, True, True, 0)
        auto_row.pack_end(self._auto_switch, False, False, 0)

        close = Gtk.Button(label="Close")
        close.connect("clicked", lambda _b: self.destroy())
        close_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        close_row.pack_end(close, False, False, 0)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.pack_start(self._value_label, False, False, 0)
        box.pack_start(self._detail_label, False, False, 0)
        box.pack_start(self._scale, False, False, 0)
        box.pack_start(
            Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 4
        )
        box.pack_start(auto_row, False, False, 0)
        box.pack_start(close_row, False, False, 0)
        self.add(box)

        self.connect("key-press-event", self._on_key_press)
        self.connect("focus-out-event", lambda *_: self.destroy())

        self._refresh_labels()

    # --- appearance ---------------------------------------------------------

    def _restyle(self):
        try:
            self._css.load_from_data(stylesheet(self._palette).encode())
        except GLib.Error:
            pass

    def _refresh_labels(self):
        percent = int(self._scale.get_value())
        self._value_label.set_text(f"{percent}%  ·  {kelvin_for(percent)}K")

        if self._auto_switch.get_active():
            wanted = scheduled_percent()
            detail = (
                f"Following the schedule — it wants {wanted}% now"
                if wanted is not None else "Following the schedule"
            )
        else:
            detail = "Manual — the schedule is not running"
        self._detail_label.set_text(detail)

    # --- the slider ---------------------------------------------------------

    def _on_button(self, _scale, event):
        """Track the drag so the expensive half can wait for the end of it."""
        self._dragging = event.type == Gdk.EventType.BUTTON_PRESS
        if not self._dragging:
            self._commit(int(self._scale.get_value()))
        return False

    def _on_scale_key(self, _scale, _event):
        """Arrow keys move the slider without ever pressing a button.

        Without this the value-changed handler would apply the new temperature
        live and nothing would ever commit it, because the commit hangs off
        button-release. Committing on idle after the keystroke keeps one path
        for both ways of moving the slider.
        """
        GLib.idle_add(lambda: self._commit(int(self._scale.get_value())) or False)
        return False

    def _on_value_changed(self, scale):
        percent = int(scale.get_value())
        self._refresh_labels()
        if self._dragging:
            self._apply_live(percent)
        # A value change that is not a drag — a click on the trough, a mark, or
        # a scroll — is already final, so it commits on the next idle rather
        # than waiting for a button-release that may never come.
        elif not self._pending_apply:
            GLib.idle_add(lambda: self._commit(percent) or False)

    def _apply_live(self, percent):
        """Push the temperature at the backend, coalesced, without saving it.

        Deliberately not through the nightlight CLI: that writes state and flips
        the mode, neither of which should happen once per value the slider
        passes through on its way somewhere.
        """
        self._pending_percent = percent
        if self._pending_apply:
            return
        self._pending_apply = GLib.timeout_add(DRAG_APPLY_MS, self._flush_live)

    def _flush_live(self):
        self._pending_apply = 0
        percent = self._pending_percent
        self._pending_percent = None
        if percent is None:
            return False
        try:
            subprocess.Popen(
                [GAMMA_APPLY, str(kelvin_for(percent))],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except OSError:
            pass
        return False

    def _commit(self, percent):
        """End of the interaction: save the value, which switches to manual.

        This is the one that makes the override stick, and the switch below
        follows it so the window never claims to be on a schedule it has just
        left.

        A value equal to the last committed one is not an interaction, and this
        is where that has to be caught: button-release fires for a click on the
        handle that moved nothing, and the arrow-key path commits on every
        keystroke. Without the guard, opening the panel and clicking the handle
        would switch the schedule off while changing no colour at all — the
        worst kind of surprise, because there is nothing on screen to notice.
        """
        if percent == self._last_committed:
            return
        self._last_committed = percent
        if self._pending_apply:
            GLib.source_remove(self._pending_apply)
            self._pending_apply = 0
            self._pending_percent = None
        run([NIGHTLIGHT, str(percent)])
        if self._auto_switch.get_active():
            self._auto_switch.handler_block_by_func(self._on_auto_toggled)
            self._auto_switch.set_active(False)
            self._auto_switch.handler_unblock_by_func(self._on_auto_toggled)
        self._refresh_labels()
        if self._on_change:
            self._on_change()

    # --- the switch ---------------------------------------------------------

    def _on_auto_toggled(self, _switch, state):
        if state:
            # `--mode auto` applies the scheduled value itself, so the slider is
            # moved to match rather than left showing the manual one.
            run([NIGHTLIGHT, "--mode", "auto"])
            scheduled = get_percent()
            # Re-seed the guard too: the slider has just been moved to the
            # scheduled value, and without this the next release would read that
            # as a hand-set one and switch straight back to manual.
            self._last_committed = scheduled
            self._scale.handler_block_by_func(self._on_value_changed)
            self._scale.set_value(scheduled)
            self._scale.handler_unblock_by_func(self._on_value_changed)
        else:
            run([NIGHTLIGHT, "--mode", "manual"])
        # GTK sets the visual state from the return value; False means "I did
        # not handle it, use `state`", which is what is wanted here.
        GLib.idle_add(lambda: self._refresh_labels() or False)
        if self._on_change:
            GLib.idle_add(lambda: self._on_change() or False)
        return False

    # --- dismissal ----------------------------------------------------------

    def _on_key_press(self, _window, event):
        if event.keyval == Gdk.KEY_Escape:
            self.destroy()
            return True
        return False


NIRI_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "niri"
)
PANEL_KDL = os.path.join(NIRI_DIR, "nightlight-panel.kdl")
NIRI_CONF = os.path.join(NIRI_DIR, "config.kdl")

PANEL_RULE = f"""\
// Managed by Scripts/Arch/nightlight/nightlight-panel.py — rewritten in full,
// so anything added here by hand is lost.
//
// The panel is a normal toplevel because a Wayland client cannot make an
// override-redirect one. Floating it and pinning it to the same corner as the
// tray is what puts it where the icon that opened it is — top-left, since the
// tray widget sits in the bar's `start` section. Move it back to top-right if
// the tray ever moves back to `end`; lib/menu.py carries the same note.
window-rule {{
    match app-id=r#"^{APP_ID}$"# title="^{WINDOW_TITLE}$"
    open-floating true
    default-floating-position x=12 y=12 relative-to="top-left"
}}
"""


def ensure_window_rules():
    """Keep the panel's window rule current — but never add the include.

    Same rule as zen's and the brightness panel's: the `include` line in
    config.kdl is the opt-in switch, added once by install-autostart.sh. If it
    is there this refreshes the rule it points at; if it is not, this does
    nothing. A config edited to drop this panel should stay edited, and a script
    that grows its own include back on next launch is overruling a decision the
    user made about their own config.

    Without the rule the panel still opens — it is just tiled into the column
    like any other window instead of floating by the bar.
    """
    if not os.environ.get("NIRI_SOCKET") or not os.path.isdir(NIRI_DIR):
        return
    try:
        with open(NIRI_CONF, encoding="utf-8") as fh:
            if "nightlight-panel.kdl" not in fh.read():
                return
    except OSError:
        return
    try:
        if not os.path.exists(PANEL_KDL) or open(PANEL_KDL).read() != PANEL_RULE:
            tmp = PANEL_KDL + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(PANEL_RULE)
            os.replace(tmp, PANEL_KDL)
            subprocess.Popen(
                ["niri", "msg", "action", "load-config-file"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
    except OSError:
        pass


def main():
    # Before any window exists: GTK takes the Wayland app_id from the prgname,
    # and niri's window rule matches on it.
    GLib.set_prgname(APP_ID)
    ensure_window_rules()
    panel = Panel()
    panel.connect("destroy", Gtk.main_quit)
    panel.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
