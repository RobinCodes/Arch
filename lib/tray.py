"""Shared tray plumbing for this repo's four panel icons.

WHY THIS EXISTS

All four trays were written against Gtk.StatusIcon, which is XEmbed: the icon
is a real X window reparented into the panel's own window. There is no
reparenting on Wayland and no XEmbed, so a StatusIcon under niri creates
nothing anybody can see — it does not fail, it just never appears, which is
the worst way for this to break.

The replacement is StatusNotifierItem, the D-Bus protocol every Wayland bar
implements, served directly by lib/sni.py. noctalia registers an
org.kde.StatusNotifierWatcher, so an item published this way shows up in its
bar with no cooperation needed from either side.

WHAT CHANGES FOR THE CALLER, AND WHY

  The menu is the whole interface. A left click opens the menu and that is
  all it does, so anything the StatusIcon version did on activate has to
  become a menu item.

  Who *draws* that menu depends on the bar. The served DBusMenu is the
  preferred path and the bar styles it; noctalia never reads it, so a click
  arrives as Activate() and lib/menu.py draws the menu here instead. The two
  are mutually exclusive — see _on_sni_activate — and the trays cannot tell
  which one they got.

  Menus travel as data, not widgets. DBusMenu serialises labels, checkboxes,
  radio items and separators — a Gtk.Scale packed into a Gtk.MenuItem does not
  survive the trip and arrives as an empty row. So the slider popups became
  two things: coarse steps in the menu for the common case, and a real window
  for fine adjustment, opened from a menu item.

  Icons are usually named, looked up by the bar in a theme. An icon this repo
  draws itself travels as pixels instead, in the spec's own IconPixmap — see
  set_pixbuf, and the paragraph there about the PNG-in-a-temp-directory
  arrangement that this replaced.

Scroll is wired where the StatusIcon version had it, but treat it as a bonus:
the SNI spec has a Scroll method and lib/sni.py serves it, yet whether a given
bar sends one is up to the bar. Every scroll action here is also reachable
from the menu, so nothing is only reachable by scrolling.
"""

import os
import warnings

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

import menu  # noqa: E402
import sni  # noqa: E402

warnings.filterwarnings("ignore", category=DeprecationWarning)

# Kept as strings rather than an enum: these go straight into the SNI
# Category property, which is a string in the spec.
class CATEGORY:
    HARDWARE = "Hardware"
    APPLICATION_STATUS = "ApplicationStatus"
    SYSTEM_SERVICES = "SystemServices"
    COMMUNICATIONS = "Communications"
    OTHER = "Other"

WATCHER = "org.kde.StatusNotifierWatcher"


def _have_sni_host():
    """Whether anything on the session bus owns the watcher name *right now*.

    Only meaningful on X11, where both backends genuinely work and the answer
    decides which is better. On Wayland it is the wrong question — see
    _pick_backend.
    """
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = bus.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", "NameHasOwner",
            GLib.Variant("(s)", (WATCHER,)),
            GLib.VariantType("(b)"), Gio.DBusCallFlags.NONE, 500, None,
        )
        return bool(reply.unpack()[0])
    except Exception:
        return False


def _pick_backend():
    """Which backend to publish through.

    ON WAYLAND THE BUS IS NOT ASKED, AND THAT IS THE WHOLE POINT

    This used to probe the bus in every session and take "no watcher" for "use
    Gtk.StatusIcon". That is a race, and it lost it on every cold boot. From the
    journal of one:

        12:19:22.053  Starting Battery Tray...
        12:19:22.117  Started Night Light Tray.
        12:19:22.122  Started app-niri-noctalia-829.scope

    niri activates xdg-desktop-autostart.target as soon as the session is up,
    and spawns noctalia from its own config at about the same moment. The trays
    therefore ask "is there an SNI host" some tens of milliseconds before the
    only thing that could answer has been executed, get False, and fall back to
    XEmbed — which under a Wayland compositor draws nothing and does not error.
    Three processes, running, publishing nothing, forever. Started by hand later
    they worked perfectly, which is what made it look like an autostart problem
    twice over.

    So on Wayland the answer is SNI unconditionally. Not because a host is
    guaranteed, but because it is the only backend that *can* work: XEmbed needs
    an X server to reparent into and there isn't one. With no host the item
    simply waits — sni.Item watches the watcher name and registers whenever it
    appears, so a bar that starts late, or restarts, picks the item up on its
    own. There is nothing to lose by choosing it and a whole failure mode to
    lose by not.

    On X11 both backends really do work, so the probe still decides: xfce4-panel
    speaks SNI perfectly well, and an X11 session with no SNI host at all still
    has a working XEmbed tray.

    WHY "DON'T KNOW" MEANS SNI

    The two mistakes are not symmetric, so the default is not a coin toss.

    Choosing SNI wrongly costs nothing that XEmbed would have saved: with no
    host the item sits on the bus until one appears, and if one never does,
    XEmbed had nowhere to draw either. Choosing XEmbed wrongly is unrecoverable
    and silent — no error, no icon, forever.

    That matters because the environment can lie by omission. A unit that
    started before niri's `systemctl --user import-environment` has no
    WAYLAND_DISPLAY, and GTK then quietly connects to XWayland instead, so even
    asking Gdk which backend it got returns "X11" in a session that has no X11
    system tray in it. DISPLAY is set under niri too, for the same reason. So
    only a positively-identified X11 session gets XEmbed; anything unrecognised
    gets the backend that can still come good.

    Neither branch can tell whether a host that exists is actually *displaying*
    the item. noctalia registers a watcher whether or not its bar has a "tray"
    widget configured, and with that entry missing every item registers
    correctly and none appear. That is a bar configuration problem; the README
    says where to look.
    """
    if os.environ.get("WAYLAND_DISPLAY") or os.environ.get("NIRI_SOCKET"):
        return "sni"
    if _have_sni_host():
        return "sni"
    if os.environ.get("XDG_SESSION_TYPE") == "x11":
        return "statusicon"
    return "sni"


class Tray:
    """One tray icon, published whichever way this session can actually show.

    Two backends, chosen at construction:

      sni         StatusNotifierItem over D-Bus, served by lib/sni.py. The
                  only thing that works on Wayland, and works on X11 too.
      statusicon  Gtk.StatusIcon, i.e. XEmbed. X11 only — under a Wayland
                  compositor it draws nothing and does not error, which is the
                  worst way for a tray to fail and the reason the SNI path
                  exists at all.

    The callers do not branch. Everything below normalises the two APIs,
    including the scroll signature, which genuinely differs: SNI hands over a
    delta and an orientation, Gtk.StatusIcon hands over a Gdk event to
    interrogate.
    """

    def __init__(self, app_id, icon_name, title=None,
                 category=CATEGORY.HARDWARE):
        self._id = app_id
        self._menu = Gtk.Menu()
        self._scroll_cb = None
        self._popup = None

        self.backend = _pick_backend()

        if self.backend == "sni":
            self.item = sni.Item(
                app_id, icon_name, title or app_id, category,
                on_activate=self._on_sni_activate,
                on_scroll=self._on_sni_scroll,
            )
            self.indicator = None
            # Cheap, idempotent, and it has to happen before the first menu is
            # opened rather than at the first click, because niri reloads its
            # config asynchronously and the rule would miss the window it was
            # written for.
            menu.ensure_window_rules()
        else:
            self.indicator = None
            self.icon = Gtk.StatusIcon.new_from_icon_name(icon_name)
            if title:
                self.icon.set_title(title)
            self.icon.connect("popup-menu", self._on_popup)
            # Left click opens the same menu. SNI has no activate event, so
            # the menu is the whole interface there; doing the same here keeps
            # one set of behaviour to reason about rather than two.
            self.icon.connect("activate", lambda _i: self._popup_now())
            self.icon.set_visible(True)

    # --- visibility ---------------------------------------------------------

    def set_visible(self, visible):
        """Show or hide without exiting.

        PASSIVE tells an SNI host to drop the item and ACTIVE brings it back,
        so a tray with nothing to report (no battery present, say) can go quiet
        and return on its own rather than being respawned.
        """
        if self.backend == "sni":
            self.item.set_status("Active" if visible else "Passive")
        else:
            self.icon.set_visible(visible)

    # --- icon ---------------------------------------------------------------

    def set_icon(self, name, description=""):
        if self.backend == "sni":
            self.item.set_icon(name)
        else:
            self.icon.set_from_icon_name(name)

    def set_pixbuf(self, pixbuf, description=""):
        """Publish a drawn icon, as pixels.

        Gtk.StatusIcon takes a pixbuf directly. SNI takes IconPixmap, which is
        the spec's own answer for an icon the application draws itself — and
        the only one that can carry a picture no icon theme contains.

        THE PNG-IN-A-TEMP-DIRECTORY PATH IS GONE, ON PURPOSE

        This used to also write each frame to a numbered PNG and point
        IconThemePath at the directory, as an Ayatana-flavoured fallback for a
        host that prefers a name. It did not work and could not have: the
        filename carried a serial so the bar would not cache it, only the last
        two files were kept, and noctalia resolves the name once and then holds
        it. The result, every five seconds for as long as the tray ran:

            [tray] tray widget icon id=… source=file
                   path=/tmp/battery-tray-icons-…/battery-tray-3.png
                   failed-to-load

        — serial 3, long deleted, while the item was publishing serial 101. The
        icon on screen was the pixmap the whole time; the file half was pure
        churn and a log line a second. Pixels only now, which is also what lets
        sni.set_icon_pixbuf skip the redraw when nothing moved.
        """
        if self.backend == "statusicon":
            self.icon.set_from_pixbuf(pixbuf)
            return
        self.item.set_icon_pixbuf(pixbuf)

    def set_label(self, text, guide=""):
        """Text beside the icon.

        Not served: the SNI spec has no label field — libappindicator carried
        one as an Ayatana extension that only its own hosts read. The value is
        folded into the tooltip instead, which every bar does show.
        """
        if self.backend == "sni" and text:
            self.item.set_tooltip(text)

    def set_tooltip(self, text):
        if self.backend == "sni":
            self.item.set_tooltip(text)
        else:
            self.icon.set_tooltip_text(text)

    # --- menu ---------------------------------------------------------------

    def set_menu(self, widget):
        self._menu = widget
        widget.show_all()
        if self.backend == "sni":
            # Flattened to data and served over DBusMenu, so the *bar* draws
            # it. That is what makes it match noctalia's styling: a GTK menu
            # drawn by this process never could.
            self.item.set_menu(sni.menu_from_gtk(widget))

    def _popup_now(self):
        self._menu.popup(None, None, Gtk.StatusIcon.position_menu,
                         self.icon, 1, Gtk.get_current_event_time())

    def _on_popup(self, _icon, button, time):
        self._menu.popup(None, None, Gtk.StatusIcon.position_menu,
                         self.icon, button, time)

    def _on_sni_activate(self):
        """A host said the icon was clicked.

        Only draws anything if nothing has ever read the served DBusMenu. On a
        bar that renders it — which is the better outcome, since the bar styles
        it — this returns without doing a thing and the bar's own menu is the
        one you get. On noctalia, which never reads it, this is the only menu
        there is.

        A second click closes the open menu rather than stacking another one on
        top of it, so the icon toggles the way a tray icon should.
        """
        if self.backend != "sni" or self.item.menu_fetched:
            return
        if self._popup is not None:
            self._popup.close()
            return
        self._popup = menu.Popup(
            sni.menu_from_gtk(self._menu),
            on_closed=self._on_popup_closed,
        )
        self._popup.show_all()
        self._popup.present()

    def _on_popup_closed(self):
        self._popup = None

    # --- scroll -------------------------------------------------------------

    def connect_scroll(self, callback):
        """callback(source, delta, direction) on a scroll over the icon.

        Normalised to the SNI Scroll method's shape, because that is the one
        that carries the least information: a Gdk scroll event can be turned
        into a delta and a direction, but not the other way round. Never fatal
        — a bar that does not forward scrolls just means scrolling does
        nothing, and every scroll action in this repo is also reachable from
        the menu.
        """
        self._scroll_cb = callback
        if self.backend == "statusicon":
            self.icon.connect("scroll-event", self._on_scroll_event)
        # The SNI path is wired in the constructor, via _on_sni_scroll.

    def _on_sni_scroll(self, delta, orientation):
        """A host called Scroll() on the item."""
        if self._scroll_cb is None:
            return
        self._scroll_cb(self, 1 if delta > 0 else -1, orientation)

    def _on_scroll_event(self, source, event):
        """Turn a Gdk scroll event into (delta, direction).

        SMOOTH is its own branch because touchpads and high-resolution wheels
        send it instead of UP/DOWN; without it, scrolling on this laptop's
        touchpad would do nothing at all.
        """
        if self._scroll_cb is None:
            return False
        if event.direction == Gdk.ScrollDirection.UP:
            delta = 1
        elif event.direction == Gdk.ScrollDirection.DOWN:
            delta = -1
        elif event.direction == Gdk.ScrollDirection.SMOOTH:
            _ok, _dx, dy = event.get_scroll_deltas()
            if not dy:
                return False
            delta = -1 if dy > 0 else 1
        else:
            return False
        self._scroll_cb(source, delta, Gdk.ScrollDirection.VERTICAL)
        return True


def item(label, callback=None, *args):
    entry = Gtk.MenuItem(label=label)
    if callback is not None:
        entry.connect("activate", callback, *args)
    return entry


def check_item(label, active, callback=None, *args):
    entry = Gtk.CheckMenuItem(label=label)
    entry.set_active(active)
    if callback is not None:
        entry.connect("toggled", callback, *args)
    return entry


def radio_item(label, active, group=None):
    entry = Gtk.RadioMenuItem(label=label)
    if group is not None:
        entry.join_group(group)
    entry.set_active(active)
    return entry


def separator():
    return Gtk.SeparatorMenuItem()


def heading(text):
    """A non-interactive label row, for state a menu needs to show.

    Insensitive rather than a custom widget: DBusMenu has no widget to send,
    but it does carry the enabled flag, so this arrives as a greyed-out row in
    every bar rather than an empty one.
    """
    entry = Gtk.MenuItem(label=text)
    entry.set_sensitive(False)
    return entry
