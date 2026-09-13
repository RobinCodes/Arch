"""A StatusNotifierItem and its DBusMenu, served directly over D-Bus.

WHY THIS EXISTS INSTEAD OF libappindicator

libappindicator publishes an SNI item that is missing the half the host needs
to open a menu. Introspecting one of ours showed:

    methods:    ['Scroll', 'SecondaryActivate', 'XAyatanaSecondaryActivate']
    properties: []

No `Activate`, no `ContextMenu`, and no `ItemIsMenu`. The spec says a host
should show the item's menu on left click when `ItemIsMenu` is true and call
`Activate()` otherwise — so with the property absent, noctalia called
`Activate()`, the method did not exist, and the click did nothing. The icons
appeared and were inert, on every tray in this repo.

Serving the item ourselves fixes that at the source and drops the
libappindicator dependency. It also answers a second question for free: the
menu is rendered by the *shell*, from data, so it matches noctalia's own
styling without this file knowing anything about themes. A GTK menu drawn by
the application could never match a QML bar; a DBusMenu is not drawn by the
application at all.

AND THEN NOCTALIA DID NOT READ IT

That last paragraph is the design, and it is still right for any host that
uses the menu. noctalia does not. It never calls GetLayout — not on click, not
at registration, not once in two days of its own logs — so the item was left
with ItemIsMenu telling it not to call Activate() either, and the clicks went
nowhere. ItemIsMenu is false again for that reason; the long comment on the
property is the evidence. lib/menu.py draws the menu when nothing else will,
and `menu_fetched` below is how the two stay out of each other's way.

WHAT IS IMPLEMENTED

Enough of both specs for a bar to drive an item, and no more:

  org.kde.StatusNotifierItem   Category Id Title Status IconName IconThemePath
                               IconPixmap ToolTip ItemIsMenu Menu
                               ContextMenu Activate SecondaryActivate Scroll
                               NewIcon NewTitle NewToolTip NewStatus

  com.canonical.dbusmenu       Version TextDirection Status IconThemePath
                               GetLayout GetGroupProperties GetProperty
                               Event EventGroup AboutToShow AboutToShowGroup
                               LayoutUpdated ItemsPropertiesUpdated

Menus are trees of plain dicts, built by the caller. Rebuilding one bumps a
revision and emits LayoutUpdated, which is how a host knows to re-read — so
rebuilding on a timer is expensive and visible, and callers should only do it
when something actually changed.
"""

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

import menu  # noqa: E402

WATCHER_NAME = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"

ITEM_IFACE = "org.kde.StatusNotifierItem"
MENU_IFACE = "com.canonical.dbusmenu"

ITEM_PATH = "/StatusNotifierItem"
MENU_PATH = "/StatusNotifierItem/Menu"

ITEM_XML = f"""
<node>
  <interface name="{ITEM_IFACE}">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="WindowId" type="i" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconThemePath" type="s" access="read"/>
    <property name="IconPixmap" type="a(iiay)" access="read"/>
    <property name="OverlayIconName" type="s" access="read"/>
    <property name="AttentionIconName" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <method name="ContextMenu">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="Activate">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="SecondaryActivate">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="Scroll">
      <arg name="delta" type="i" direction="in"/>
      <arg name="orientation" type="s" direction="in"/>
    </method>
    <signal name="NewIcon"/>
    <signal name="NewTitle"/>
    <signal name="NewToolTip"/>
    <signal name="NewStatus">
      <arg name="status" type="s"/>
    </signal>
  </interface>
</node>
"""

MENU_XML = f"""
<node>
  <interface name="{MENU_IFACE}">
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <method name="GetLayout">
      <arg name="parentId" type="i" direction="in"/>
      <arg name="recursionDepth" type="i" direction="in"/>
      <arg name="propertyNames" type="as" direction="in"/>
      <arg name="revision" type="u" direction="out"/>
      <arg name="layout" type="(ia{{sv}}av)" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg name="ids" type="ai" direction="in"/>
      <arg name="propertyNames" type="as" direction="in"/>
      <arg name="properties" type="a(ia{{sv}})" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg name="id" type="i" direction="in"/>
      <arg name="name" type="s" direction="in"/>
      <arg name="value" type="v" direction="out"/>
    </method>
    <method name="Event">
      <arg name="id" type="i" direction="in"/>
      <arg name="eventId" type="s" direction="in"/>
      <arg name="data" type="v" direction="in"/>
      <arg name="timestamp" type="u" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg name="events" type="a(isvu)" direction="in"/>
      <arg name="idErrors" type="ai" direction="out"/>
    </method>
    <method name="AboutToShow">
      <arg name="id" type="i" direction="in"/>
      <arg name="needUpdate" type="b" direction="out"/>
    </method>
    <method name="AboutToShowGroup">
      <arg name="ids" type="ai" direction="in"/>
      <arg name="updatesNeeded" type="ai" direction="out"/>
      <arg name="idErrors" type="ai" direction="out"/>
    </method>
    <signal name="ItemsPropertiesUpdated">
      <arg name="updatedProps" type="a(ia{{sv}})"/>
      <arg name="removedProps" type="a(ias)"/>
    </signal>
    <signal name="LayoutUpdated">
      <arg name="revision" type="u"/>
      <arg name="parent" type="i"/>
    </signal>
    <signal name="ItemActivationRequested">
      <arg name="id" type="i"/>
      <arg name="timestamp" type="u"/>
    </signal>
  </interface>
</node>
"""


def menu_from_gtk(widget):
    """Flatten a Gtk.Menu into the dict tree this module serves.

    The four trays in this repo were written against Gtk.Menu and there is no
    reason to rewrite them: a menu is a list of labels, types and callbacks,
    and Gtk.Menu is a perfectly good way to say that. This reads it back out
    rather than making every caller learn a second vocabulary.

    Submenus recurse. Anything without a label that is not a separator is
    skipped, because DBusMenu has no way to render it and a blank row in the
    bar is worse than a missing one.
    """
    items = []
    for child in widget.get_children():
        if isinstance(child, Gtk.SeparatorMenuItem):
            items.append({"type": "separator"})
            continue

        label = child.get_label() if hasattr(child, "get_label") else None
        if not label:
            # Some items carry their text in a child Gtk.Label instead.
            inner = child.get_child()
            label = inner.get_text() if isinstance(inner, Gtk.Label) else None
        if not label:
            continue

        entry = {
            "label": label,
            "enabled": child.get_sensitive(),
            "widget": child,
        }

        if isinstance(child, Gtk.RadioMenuItem):
            entry["toggle-type"] = "radio"
            entry["toggle-state"] = 1 if child.get_active() else 0
        elif isinstance(child, Gtk.CheckMenuItem):
            entry["toggle-type"] = "checkmark"
            entry["toggle-state"] = 1 if child.get_active() else 0

        sub = child.get_submenu()
        if sub is not None:
            entry["children"] = menu_from_gtk(sub)

        items.append(entry)
    return items


class Item:
    """One StatusNotifierItem, owned for the life of the process."""

    def __init__(self, app_id, icon_name, title="", category="Hardware",
                 on_activate=None, on_scroll=None):
        self.app_id = app_id
        self.title = title or app_id
        self.category = category
        self.status = "Active"
        self.icon_name = icon_name
        self.icon_theme_path = ""
        self.icon_pixmap = []   # [(w, h, ARGB32-big-endian bytes)]
        self.tooltip = title or app_id
        self.on_activate = on_activate
        self.on_scroll = on_scroll

        self._menu = []
        self._by_id = {}
        self._revision = 1
        self._registered = False
        # Set by the first GetLayout. See the comment there, and menu_fetched.
        self._menu_fetched = False

        self.conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self._item_info = Gio.DBusNodeInfo.new_for_xml(ITEM_XML).interfaces[0]
        self._menu_info = Gio.DBusNodeInfo.new_for_xml(MENU_XML).interfaces[0]

        self.conn.register_object(
            ITEM_PATH, self._item_info,
            self._item_call, self._item_get, None)
        self.conn.register_object(
            MENU_PATH, self._menu_info,
            self._menu_call, self._menu_get, None)

        # The bus name must be unique per process. The spec allows registering
        # by unique connection name too, and that is what is used here — one
        # less well-known name to collide over, and the watcher accepts it.
        self._own_name = self.conn.get_unique_name()

        # Register now, and again whenever a watcher appears. A bar that
        # restarts — or that starts after this tray did, which is the normal
        # case at login — takes the watcher name with it, and an item that only
        # registered once would be invisible until the tray was restarted by
        # hand.
        #
        # This is load-bearing, not a nicety. lib/tray.py picks the SNI backend
        # on Wayland without waiting to see whether a host exists, precisely
        # because this watch means it does not have to: at a cold boot the trays
        # start before noctalia does, the _register() below fails, and the item
        # goes live the moment the watcher name is taken.
        #
        # The watcher id is kept because dropping the only reference to it is
        # how the callback stops being called — and a tray that silently stops
        # re-registering looks exactly like the bug this exists to prevent.
        self._watch_id = Gio.bus_watch_name_on_connection(
            self.conn, WATCHER_NAME, Gio.BusNameWatcherFlags.NONE,
            lambda *_a: self._register(), None)
        self._register()

    @property
    def menu_fetched(self):
        """Whether any host has ever read the DBusMenu off this item.

        lib/tray.py asks before drawing a menu of its own, so the two can never
        both appear: a bar that renders the served menu has already called
        GetLayout by the time it delivers a click, and one that never calls it
        is one that was never going to draw anything.
        """
        return self._menu_fetched

    # --- registration -------------------------------------------------------

    def _register(self):
        try:
            self.conn.call_sync(
                WATCHER_NAME, WATCHER_PATH, WATCHER_NAME,
                "RegisterStatusNotifierItem",
                GLib.Variant("(s)", (self._own_name,)),
                None, Gio.DBusCallFlags.NONE, 3000, None)
            self._registered = True
        except GLib.Error:
            self._registered = False

    # --- state, with the signals the spec wants ----------------------------

    def set_icon(self, name, theme_path=None):
        self.icon_name = name
        if theme_path is not None:
            self.icon_theme_path = theme_path
        self.icon_pixmap = []
        self._emit(ITEM_PATH, ITEM_IFACE, "NewIcon", None)

    def set_icon_pixbuf(self, pixbuf):
        """Serve a drawn icon as pixel data rather than as a named file.

        IconPixmap is the spec's own answer for an icon the application draws
        itself, and it is what the battery tray needs: its icon is a number
        rendered with cairo, which is not a thing any icon theme can contain.

        IconThemePath — writing a PNG somewhere and pointing the host at the
        directory — is an Ayatana extension that a host is free to ignore, so
        it is kept as a fallback and this is the real path.

        The wire format is ARGB32 in *network* byte order. GdkPixbuf hands back
        RGBA in machine order, so the channels are reordered here; skipping
        that step is the classic way to get a tray icon with swapped red and
        blue, or one that renders correctly on a big-endian machine only.
        """
        if pixbuf is None:
            return
        # A name and a pixmap are alternatives, not layers, and a host picks
        # whichever it likes. noctalia prefers the name, so leaving the one the
        # item was constructed with in place means a drawn icon is never shown:
        # the battery tray would publish "++" and display the stock
        # battery-symbolic. Clearing it is what makes the drawing the answer.
        self.icon_name = ""
        self.icon_theme_path = ""

        width, height = pixbuf.get_width(), pixbuf.get_height()
        rowstride = pixbuf.get_rowstride()
        channels = pixbuf.get_n_channels()
        data = pixbuf.get_pixels()

        out = bytearray(width * height * 4)
        i = 0
        for y in range(height):
            row = y * rowstride
            for x in range(width):
                px = row + x * channels
                r = data[px]
                g = data[px + 1]
                b = data[px + 2]
                a = data[px + 3] if channels == 4 else 255
                out[i] = a
                out[i + 1] = r
                out[i + 2] = g
                out[i + 3] = b
                i += 4

        pixmap = [(width, height, bytes(out))]
        if pixmap == self.icon_pixmap:
            # The trays redraw on a timer whether or not the picture moved, and
            # every NewIcon costs the host a GetAll that carries this array
            # back. Comparing 4KB of bytes is cheaper than sending it.
            return
        self.icon_pixmap = pixmap
        self._emit(ITEM_PATH, ITEM_IFACE, "NewIcon", None)

    # The three below say nothing when nothing changed.
    #
    # Every one of these signals costs the host a GetAll round trip on the
    # whole interface, IconPixmap included. The trays poll on a timer and call
    # all three from the same refresh, so an unguarded setter is a property
    # fetch every few seconds forever, whether or not a pixel moved: a capture
    # of 156 idle seconds held 30 NewIcon and 15 NewStatus, all of them
    # restating what the host already had.

    def set_title(self, title):
        if title == self.title:
            return
        self.title = title
        self._emit(ITEM_PATH, ITEM_IFACE, "NewTitle", None)

    def set_tooltip(self, text):
        if text == self.tooltip:
            return
        self.tooltip = text
        self._emit(ITEM_PATH, ITEM_IFACE, "NewToolTip", None)

    def set_status(self, status):
        if status == self.status:
            return
        self.status = status
        self._emit(ITEM_PATH, ITEM_IFACE, "NewStatus", GLib.Variant("(s)", (status,)))

    def set_menu(self, items):
        """Replace the menu. Bumps the revision and tells the host to re-read.

        Every call is a round trip for the host, so this should be driven by an
        actual change rather than by a poll — see the battery tray, which was
        rebuilding five times a second and had reached revision 10,255 by the
        time anyone looked.
        """
        self._menu = items
        self._by_id = {}
        self._assign_ids(self._menu, counter=[1])
        self._revision += 1
        self._emit(MENU_PATH, MENU_IFACE, "LayoutUpdated",
                   GLib.Variant("(ui)", (self._revision, 0)))

    def _assign_ids(self, items, counter):
        for entry in items:
            entry["_id"] = counter[0]
            self._by_id[counter[0]] = entry
            counter[0] += 1
            if entry.get("children"):
                self._assign_ids(entry["children"], counter)

    def _emit(self, path, iface, signal, body):
        try:
            self.conn.emit_signal(None, path, iface, signal, body)
        except GLib.Error:
            pass

    # --- StatusNotifierItem -------------------------------------------------

    def _item_get(self, _conn, _sender, _path, _iface, prop):
        if prop == "Category":
            return GLib.Variant("s", self.category)
        if prop == "Id":
            return GLib.Variant("s", self.app_id)
        if prop == "Title":
            return GLib.Variant("s", self.title)
        if prop == "Status":
            return GLib.Variant("s", self.status)
        if prop == "WindowId":
            return GLib.Variant("i", 0)
        if prop == "IconName":
            return GLib.Variant("s", self.icon_name)
        if prop == "IconThemePath":
            return GLib.Variant("s", self.icon_theme_path)
        if prop == "IconPixmap":
            return GLib.Variant("a(iiay)", self.icon_pixmap)
        if prop in ("OverlayIconName", "AttentionIconName"):
            return GLib.Variant("s", "")
        if prop == "ToolTip":
            # (icon name, icon pixmaps, title, body). Bars show the title.
            return GLib.Variant("(sa(iiay)ss)", ("", [], self.tooltip, ""))
        if prop == "ItemIsMenu":
            # FALSE, DELIBERATELY, AND THIS IS THE LINE THAT MAKES CLICKS WORK
            #
            # The spec says a host seeing true "should prefer showing the menu
            # or sending ContextMenu() instead of Activate()". This was set
            # true on exactly that reading, to stop noctalia calling an
            # Activate() that libappindicator had never implemented.
            #
            # What noctalia actually does with true is neither of the two. It
            # stops calling Activate() and does not start doing anything else.
            # Measured on 5.1.0, with the property true:
            #
            #   two days of its own logs      no GetLayout, no AboutToShow,
            #                                 no popup, no menu of any kind
            #   two D-Bus captures, one of    0 method calls inbound, on any
            #   them 156s with the icons      interface, while the icons were
            #   being clicked                 being clicked
            #
            # So the click had nowhere to go: Activate() suppressed by the
            # property, and the DBusMenu this file serves never once read. The
            # icons appeared and were inert — the same symptom the property was
            # added to fix, arrived at from the other side.
            #
            # False puts Activate() back, which is the one call noctalia is
            # known to make, and lib/tray.py now answers it by drawing the menu
            # itself. The DBusMenu below is still served in full and is still
            # the preferred path: a host that reads it renders it, and
            # _menu_fetched keeps the drawn-here fallback out of its way.
            return GLib.Variant("b", False)
        if prop == "Menu":
            return GLib.Variant("o", MENU_PATH)
        return None

    def _item_call(self, _conn, _sender, _path, _iface, method, params, inv):
        if method in ("Activate", "SecondaryActivate", "ContextMenu"):
            # All three mean "the user clicked me" and all three get the menu.
            # Which one a host sends is not worth distinguishing when the item
            # has exactly one thing to show: noctalia sends Activate, the spec
            # points a host with ItemIsMenu set at ContextMenu, and middle
            # click arrives as SecondaryActivate on both.
            #
            # Replying before acting, because the menu is a window: the reply
            # is what lets the host finish its click handler, and holding it
            # until a toplevel has been mapped is how a bar ends up briefly
            # frozen on every tray click.
            inv.return_value(None)
            if self.on_activate:
                self.on_activate()
            return
        if method == "Scroll":
            delta, orientation = params.unpack()
            if self.on_scroll:
                self.on_scroll(delta, orientation)
            inv.return_value(None)
            return
        inv.return_error_literal(
            Gio.dbus_error_quark(), Gio.DBusError.UNKNOWN_METHOD, method)

    # --- DBusMenu -----------------------------------------------------------

    def _menu_get(self, _conn, _sender, _path, _iface, prop):
        if prop == "Version":
            return GLib.Variant("u", 3)
        if prop == "TextDirection":
            return GLib.Variant("s", "ltr")
        if prop == "Status":
            return GLib.Variant("s", "normal")
        if prop == "IconThemePath":
            return GLib.Variant("as", [])
        return None

    def _props_for(self, entry):
        props = {}
        if entry.get("type") == "separator":
            props["type"] = GLib.Variant("s", "separator")
            return props
        props["label"] = GLib.Variant("s", entry.get("label", ""))
        props["enabled"] = GLib.Variant("b", bool(entry.get("enabled", True)))
        props["visible"] = GLib.Variant("b", True)
        if "toggle-type" in entry:
            props["toggle-type"] = GLib.Variant("s", entry["toggle-type"])
            props["toggle-state"] = GLib.Variant("i", int(entry.get("toggle-state", 0)))
        if entry.get("children"):
            props["children-display"] = GLib.Variant("s", "submenu")
        return props

    def _layout_node(self, entry, depth):
        """One layout node as a PLAIN tuple: (id, props, children).

        Plain on purpose. GLib.Variant construction from a format string walks the
        value and builds each leaf itself, so handing it an already-built
        Variant where it expects a tuple raises "Expected GLib.Variant, but got
        tuple". Only the two places the signature actually says `v` — the
        children array, and the property dict's values — get wrapped.
        """
        children = []
        if depth != 0 and entry.get("children"):
            children = [
                GLib.Variant("(ia{sv}av)", self._layout_node(c, depth - 1))
                for c in entry["children"]
            ]
        return (entry["_id"], self._props_for(entry), children)

    def _menu_call(self, _conn, _sender, _path, _iface, method, params, inv):
        if method == "GetLayout":
            parent_id, depth, _names = params.unpack()
            # Somebody is reading the menu, so it is being rendered by a host
            # and lib/tray.py must not also draw its own. Latched rather than
            # asked once at startup: a bar can be replaced mid-session by one
            # that behaves differently, and the first GetLayout is the only
            # reliable evidence either way.
            self._menu_fetched = True
            if parent_id == 0:
                kids = [
                    GLib.Variant("(ia{sv}av)", self._layout_node(e, depth - 1))
                    for e in self._menu
                ]
                # children-display on the root is not optional. It is how a
                # host is told the root has a submenu to open at all, and a
                # root sent with empty properties reads as an item with
                # nothing under it — an empty menu, from a tree that is fully
                # populated one level down.
                root = (0, {"children-display": GLib.Variant("s", "submenu")}, kids)
            else:
                entry = self._by_id.get(parent_id)
                root = (self._layout_node(entry, depth) if entry
                        else (parent_id, {}, []))
            inv.return_value(GLib.Variant("(u(ia{sv}av))", (self._revision, root)))
            return

        if method == "GetGroupProperties":
            ids, _names = params.unpack()
            out = []
            wanted = ids or list(self._by_id)
            for i in wanted:
                entry = self._by_id.get(i)
                if entry:
                    out.append((i, self._props_for(entry)))
            inv.return_value(GLib.Variant("(a(ia{sv}))", (out,)))
            return

        if method == "GetProperty":
            item_id, name = params.unpack()
            entry = self._by_id.get(item_id) or {}
            value = self._props_for(entry).get(name, GLib.Variant("s", ""))
            inv.return_value(GLib.Variant("(v)", (value,)))
            return

        if method == "Event":
            item_id, event_id, _data, _ts = params.unpack()
            self._dispatch(item_id, event_id)
            inv.return_value(None)
            return

        if method == "EventGroup":
            for item_id, event_id, _data, _ts in params.unpack()[0]:
                self._dispatch(item_id, event_id)
            inv.return_value(GLib.Variant("(ai)", ([],)))
            return

        if method == "AboutToShow":
            inv.return_value(GLib.Variant("(b)", (False,)))
            return

        if method == "AboutToShowGroup":
            inv.return_value(GLib.Variant("(aiai)", ([], [])))
            return

        inv.return_error_literal(
            Gio.dbus_error_quark(), Gio.DBusError.UNKNOWN_METHOD, method)

    def _dispatch(self, item_id, event_id):
        """Turn a host's "clicked" into the Gtk widget's own activation.

        Shared with lib/menu.py, which fires the same widgets when this repo
        has to draw the menu itself, so both routes into a tray run identical
        code — including the radio-item rule, which is not the obvious one.
        """
        if event_id != "clicked":
            return
        entry = self._by_id.get(item_id)
        if not entry:
            return
        widget = entry.get("widget")
        if widget is not None:
            menu.activate(widget)
