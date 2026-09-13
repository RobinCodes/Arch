"""The tray menu as a window, for a bar that will not draw one itself.

WHY THIS EXISTS

The intended path is DBusMenu: lib/sni.py serves the menu as data, the bar
renders it, and it matches the bar's styling for free. That is still what
happens on a host that uses it, and this file stays out of the way there.

noctalia does not use it. Measured on 5.1.0, across two days of its own logs
and two D-Bus captures — one of them spanning a full tray restart and
re-registration:

    GetLayout      0 calls
    AboutToShow    0 calls
    anything at all on com.canonical.dbusmenu    0 calls

It reads the item's properties happily (28 GetAll round trips in one 25-second
capture) and renders the icon from them, so the item is registered and live. It
simply never asks for the menu, and never has.

That leaves the SNI item's own methods as the only channel a click can arrive
on, and Activate() is the one noctalia calls — which is what this repo saw
before ItemIsMenu was added, and why sni.Item sets ItemIsMenu false again. See
the comment there; the property is the whole reason the last fix did not take.

So: the host says "clicked", and this draws the menu.

WHY A WINDOW AND NOT A Gtk.Menu

A Wayland client cannot make an override-redirect window, cannot place itself,
and cannot grab the seat. Gtk.Menu.popup_at_pointer needs a parent surface the
tray process does not have — there is no toplevel behind a tray icon. So this
is an ordinary toplevel that niri floats by rule, exactly like
nightlight-panel.py, which hit the same wall for the same reason.

It paints itself from noctalia's palette for the same reason that panel does:
GTK has never heard of noctalia, and left alone this would be a white rectangle
under a dark bar.
"""

import os
import subprocess

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

import noctalia  # noqa: E402

# One rule covers all three trays, so it matches on the title rather than the
# app-id: these windows are opened from inside the tray processes, whose app-id
# is already their own name, and there is no second process here to give a
# distinct one to.
WINDOW_TITLE = "Tray menu"

NIRI_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "niri"
)
MENU_KDL = os.path.join(NIRI_DIR, "tray-menu.kdl")
NIRI_CONF = os.path.join(NIRI_DIR, "config.kdl")

# Top-left because that is where the tray is. If the tray widget moves back to
# the right of the bar, change `relative-to` here to match — a menu that opens
# at the opposite end of the screen from the icon that opened it is worse than
# one that is merely tiled.
MENU_RULE = f"""\
// Managed by Scripts/Arch/lib/menu.py — rewritten in full, so anything added
// here by hand is lost.
//
// The tray menu is a normal toplevel because a Wayland client cannot make an
// override-redirect one, and a tray icon has no surface to anchor a Gtk.Menu
// to. Floating it and pinning it under the tray is what puts it where the icon
// that opened it is.
window-rule {{
    match title="^{WINDOW_TITLE}$"
    open-floating true
    default-floating-position x=12 y=12 relative-to="top-left"
}}
"""


def ensure_window_rules():
    """Keep the menu's window rule current — but never add the include.

    Same rule as the night light panel's: the `include` line in config.kdl is
    the opt-in switch, added once by install-autostart.sh. If it is there this
    refreshes the rule it points at; if it is not, this does nothing. A config
    edited to drop this should stay edited.

    Without the rule the menu still opens and still works — it is just tiled
    into the column like any other window instead of floating under the bar.
    """
    if not os.environ.get("NIRI_SOCKET") or not os.path.isdir(NIRI_DIR):
        return
    try:
        with open(NIRI_CONF, encoding="utf-8") as fh:
            if "tray-menu.kdl" not in fh.read():
                return
    except OSError:
        return
    try:
        if not os.path.exists(MENU_KDL) or open(MENU_KDL).read() != MENU_RULE:
            tmp = MENU_KDL + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(MENU_RULE)
            os.replace(tmp, MENU_KDL)
            subprocess.Popen(
                ["niri", "msg", "action", "load-config-file"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
    except OSError:
        pass


def stylesheet(palette):
    """The menu's CSS, in noctalia's colours.

    Rows are buttons rather than list rows so they take focus and hover like
    the menu entries they stand in for. The fallbacks are GTK's own theme
    colours rather than invented hex, so with no noctalia palette this is a
    no-op instead of a second, worse theme.
    """
    bg = palette.get("window_bg_color", "@theme_bg_color")
    fg = palette.get("window_fg_color", "@theme_fg_color")
    card = palette.get("card_bg_color", "@theme_base_color")
    accent = palette.get("accent_bg_color", "@theme_selected_bg_color")
    on_accent = palette.get("accent_fg_color", "@theme_selected_fg_color")

    # `background-image: none` is not decoration: Adwaita paints its buttons
    # with gradient images, and a rule that only sets background-color leaves
    # the image sitting on top of it.
    return f"""
    window.tray-menu {{
        background-color: {bg};
        color: {fg};
        border: 1px solid {card};
        border-radius: 10px;
    }}
    window.tray-menu label {{
        color: {fg};
    }}
    window.tray-menu .menu-heading {{
        opacity: 0.65;
        font-size: 9pt;
    }}
    window.tray-menu .menu-mark {{
        opacity: 0.9;
    }}
    window.tray-menu button.menu-row {{
        background-image: none;
        background-color: transparent;
        color: {fg};
        border: none;
        border-radius: 6px;
        padding: 5px 10px;
    }}
    window.tray-menu button.menu-row:hover {{
        background-image: none;
        background-color: {accent};
        color: {on_accent};
    }}
    window.tray-menu button.menu-row:disabled {{
        background-image: none;
        background-color: transparent;
        opacity: 0.45;
    }}
    window.tray-menu separator {{
        background-color: {card};
        min-height: 1px;
        margin: 3px 6px;
    }}
    """


def activate(widget):
    """Fire a Gtk menu item the way a real click on it would.

    Shared with lib/sni.py so a host that *does* render the DBusMenu and one
    that makes us draw it ourselves put the trays through exactly the same
    code. Going through the widget rather than a stored callback keeps one set
    of semantics: the trays connect to "activate" and "toggled" in the ordinary
    GTK way, and a check item's state has to flip before its handler runs or
    the handler reads the old value.

    Radio items are not toggled, they are selected. Toggling one that is
    already active would ask GTK to leave its group with nothing selected,
    which is not what clicking the current choice in a menu means — it means
    nothing at all.
    """
    if isinstance(widget, Gtk.RadioMenuItem):
        if not widget.get_active():
            widget.set_active(True)
    elif isinstance(widget, Gtk.CheckMenuItem):
        widget.set_active(not widget.get_active())
    else:
        widget.emit("activate")


class Popup(Gtk.Window):
    """One open menu. The tray keeps the reference and closes the old one."""

    def __init__(self, items, on_closed=None):
        super().__init__(title=WINDOW_TITLE)
        self._on_closed = on_closed

        self.set_border_width(6)
        self.set_resizable(False)
        # No titlebar: GTK3 draws client-side decorations on Wayland whether or
        # not the compositor would, and a header bar with a close button on top
        # of a menu that already closes four other ways is just wrong.
        self.set_decorated(False)
        self.get_style_context().add_class("tray-menu")

        self._palette = noctalia.Palette(on_change=lambda _p: self._restyle())
        self._css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), self._css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
        self._restyle()

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._build(box, items, depth=0)
        self.add(box)

        self.connect("key-press-event", self._on_key)
        # A managed toplevel is actually told when it loses focus, which is the
        # closest thing to a menu's implicit grab that Wayland offers.
        self.connect("focus-out-event", lambda *_: self.close())
        self.connect("destroy", self._on_destroy)

    # --- building -----------------------------------------------------------

    def _build(self, box, items, depth):
        for entry in items:
            if entry.get("type") == "separator":
                box.pack_start(
                    Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL),
                    False, False, 0,
                )
                continue

            label = entry.get("label", "")
            widget = entry.get("widget")
            children = entry.get("children")

            # A submenu arrives flattened: its label becomes a heading and its
            # entries are indented under it. Nested popups would need a second
            # toplevel positioned against the first, which is the problem this
            # file exists to avoid — and no menu in this repo has one anyway.
            if children:
                box.pack_start(self._heading(label, depth), False, False, 0)
                self._build(box, children, depth + 1)
                continue

            row = Gtk.Button()
            row.get_style_context().add_class("menu-row")
            row.set_relief(Gtk.ReliefStyle.NONE)
            row.set_sensitive(bool(entry.get("enabled", True)))
            row.add(self._row_content(entry, label, depth))
            if widget is not None:
                tip = widget.get_tooltip_text()
                if tip:
                    row.set_tooltip_text(tip)
                row.connect("clicked", self._on_row, widget)
            box.pack_start(row, False, False, 0)

    def _heading(self, text, depth):
        heading = Gtk.Label(label=text)
        heading.set_xalign(0.0)
        heading.get_style_context().add_class("menu-heading")
        heading.set_margin_start(10 + depth * 14)
        heading.set_margin_top(6)
        heading.set_margin_bottom(2)
        return heading

    def _row_content(self, entry, label, depth):
        """A row: a fixed-width mark column, then the label.

        The mark column is present on every row, checked or not, so the labels
        line up instead of stepping in and out as items toggle.
        """
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.set_margin_start(depth * 14)

        toggle = entry.get("toggle-type")
        mark = ""
        if toggle == "radio":
            mark = "●" if entry.get("toggle-state") else "○"
        elif toggle == "checkmark":
            mark = "✔" if entry.get("toggle-state") else ""

        mark_label = Gtk.Label(label=mark)
        mark_label.get_style_context().add_class("menu-mark")
        mark_label.set_xalign(0.0)
        # Sized in characters so it holds the column open when the mark is
        # blank, without depending on the font's pixel metrics.
        mark_label.set_width_chars(1)
        row.pack_start(mark_label, False, False, 0)

        text = Gtk.Label(label=label)
        text.set_xalign(0.0)
        row.pack_start(text, True, True, 0)
        return row

    # --- behaviour ----------------------------------------------------------

    def _restyle(self):
        try:
            self._css.load_from_data(stylesheet(self._palette).encode())
        except GLib.Error:
            pass

    def _on_row(self, _button, widget):
        # Close first. The handler may put up a dialog or spawn a helper, and a
        # menu still sitting open behind that is a menu that looks stuck.
        self.close()
        activate(widget)

    def _on_key(self, _window, event):
        if event.keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False

    def _on_destroy(self, _window):
        # Drop the provider with the window. These are added per screen, and a
        # tray that opens its menu fifty times would otherwise be carrying
        # fifty stylesheets, each one restyled on every palette change.
        Gtk.StyleContext.remove_provider_for_screen(
            Gdk.Screen.get_default(), self._css
        )
        if self._on_closed:
            self._on_closed()
