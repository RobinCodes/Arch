"""Shared tray plumbing for this repo's four panel icons.

WHY THIS EXISTS

All four trays were written against Gtk.StatusIcon, which is XEmbed: the icon
is a real X window reparented into the panel's own window. There is no
reparenting on Wayland and no XEmbed, so a StatusIcon under niri creates
nothing anybody can see — it does not fail, it just never appears, which is
the worst way for this to break.

The replacement is StatusNotifierItem, the D-Bus protocol every Wayland bar
implements, reached here through libappindicator. noctalia registers an
org.kde.StatusNotifierWatcher, so an indicator published this way shows up in
its bar with no cooperation needed from either side.

WHAT CHANGES FOR THE CALLER, AND WHY

  The menu is the whole interface. SNI has no "left click did something"
  event to hook: a left click opens the menu and that is all it does. Anything
  the StatusIcon version did on activate has to become a menu item.

  Menus travel as data, not widgets. DBusMenu serialises labels, checkboxes,
  radio items and separators — a Gtk.Scale packed into a Gtk.MenuItem does not
  survive the trip and arrives as an empty row. So the slider popups became
  two things: coarse steps in the menu for the common case, and a real window
  for fine adjustment, opened from a menu item.

  Icons are named, not drawn. set_icon_full takes an icon name looked up in a
  theme. For an icon this repo draws itself (the battery percentage) that
  means writing a PNG into a private directory and pointing the indicator's
  theme path at it — see set_pixbuf.

Scroll is wired where the StatusIcon version had it, but treat it as a bonus:
the SNI spec has a Scroll method and libappindicator forwards it, yet whether
a given bar sends one is up to the bar. Every scroll action here is also
reachable from the menu, so nothing is only reachable by scrolling.
"""

import os
import tempfile
import warnings

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AppIndicator3", "0.1")
from gi.repository import AppIndicator3, Gtk  # noqa: E402

warnings.filterwarnings("ignore", category=DeprecationWarning)

CATEGORY = AppIndicator3.IndicatorCategory


class Tray:
    """One StatusNotifierItem, with the bits these four trays actually use."""

    def __init__(self, app_id, icon_name, title=None,
                 category=CATEGORY.HARDWARE):
        self._id = app_id
        self._icon_dir = None
        self._icon_serial = 0

        self.indicator = AppIndicator3.Indicator.new(app_id, icon_name, category)
        self.indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
        if title:
            self.indicator.set_title(title)

        # An indicator with no menu is inert in most bars — some will not even
        # draw it. Every caller replaces this, but the default keeps a
        # half-built tray visible rather than invisible.
        self._menu = Gtk.Menu()
        self.indicator.set_menu(self._menu)

    def set_visible(self, visible):
        """Show or hide the icon without exiting.

        The SNI equivalent of Gtk.StatusIcon.set_visible: PASSIVE tells the bar
        to drop the item and ACTIVE brings it back, so a tray with nothing to
        report (no battery present, say) can go quiet and return on its own
        rather than being respawned.
        """
        self.indicator.set_status(
            AppIndicator3.IndicatorStatus.ACTIVE if visible
            else AppIndicator3.IndicatorStatus.PASSIVE
        )

    # --- icon ---------------------------------------------------------------

    def set_icon(self, name, description=""):
        self.indicator.set_icon_full(name, description)

    def set_pixbuf(self, pixbuf, description=""):
        """Publish a drawn icon by writing it where the theme lookup can find it.

        SNI can carry pixel data inline, but libappindicator's binding only
        offers the by-name path, so the pixbuf goes to a PNG in a private
        directory that is registered as an icon theme path.

        The filename carries a serial that changes on every write. Icon
        lookups are cached aggressively by every toolkit involved, and writing
        new bytes to the same name is the reliable way to have a bar keep
        showing the old picture — which for the battery tray would mean a
        percentage that never changes.
        """
        if self._icon_dir is None:
            self._icon_dir = tempfile.mkdtemp(prefix=f"{self._id}-icons-")
            self.indicator.set_icon_theme_path(self._icon_dir)

        self._icon_serial += 1
        name = f"{self._id}-{self._icon_serial}"
        path = os.path.join(self._icon_dir, f"{name}.png")
        try:
            pixbuf.savev(path, "png", [], [])
        except Exception:
            return
        self.indicator.set_icon_full(name, description)

        # Keep the directory from growing for the life of the session: at one
        # write per poll this is thousands of files an hour otherwise.
        stale = os.path.join(self._icon_dir, f"{self._id}-{self._icon_serial - 2}.png")
        try:
            os.remove(stale)
        except OSError:
            pass

    def set_label(self, text, guide=""):
        """Text beside the icon. `guide` is the widest string it will hold, so
        the bar can reserve a stable width instead of jittering per update."""
        self.indicator.set_label(text or "", guide)

    def set_tooltip(self, text):
        # SNI has a tooltip field, but no bar in common use renders it for an
        # app indicator, so this lands on the title instead — which noctalia
        # does show.
        self.indicator.set_title(text)

    # --- menu ---------------------------------------------------------------

    def set_menu(self, menu):
        self._menu = menu
        menu.show_all()
        self.indicator.set_menu(menu)

    def connect_scroll(self, callback):
        """callback(delta, direction) on a scroll over the icon, if the bar
        sends one. Never fatal: an older libappindicator without the signal
        just means scrolling does nothing."""
        try:
            self.indicator.connect("scroll-event", callback)
        except TypeError:
            pass


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
