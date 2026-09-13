"""noctalia's live colour palette, for the windows this repo draws itself.

WHY A WINDOW NEEDS THIS AND A MENU DOES NOT

A tray menu is rendered by the bar, from the data lib/sni.py serves over
DBusMenu, so it picks up noctalia's styling without anyone asking. A window is
the opposite case: it is drawn by GTK, in this process, and GTK has never heard
of noctalia. Left alone it comes up in default Adwaita light — a white
rectangle beside a dark bar.

WHERE THE COLOURS COME FROM

noctalia ships a GTK template. Enabled, it writes the active palette to
`~/.config/gtk-3.0/noctalia.css` as a list of `@define-color` lines and adds an
`@import` for it to `gtk.css`:

    @define-color window_bg_color #070722;
    @define-color accent_bg_color #a9aefe;
    @define-color theme_selected_bg_color @accent_bg_color;

That file is the palette, already resolved to hex by the only thing that knows
what "the active scheme" means, and rewritten whenever the scheme changes. So
this reads it rather than keeping a second copy of noctalia's colours that
would be wrong the first time the user picked a different theme.

The template covers the libadwaita colour names, which GTK3's own stylesheet
mostly ignores — importing it is not by itself enough to theme a GTK3 window.
What it is enough for is *telling us the colours*, which the caller then uses in
a stylesheet of its own. That is the split: noctalia owns the palette, this repo
owns the widgets it paints with it.

The file is watched, so a palette change repaints an open window instead of
waiting for it to be reopened.

If the template is not enabled the file is absent, `Palette.available` is False
and every lookup returns the caller's fallback. A window that renders in stock
Adwaita is a cosmetic miss, not a failure, so nothing here raises.
"""

import os
import re

from gi.repository import Gio, GLib

CSS_PATH = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
    "gtk-3.0", "noctalia.css",
)

# `@define-color name value;` where value is either a literal or @another-name.
_DEFINE = re.compile(r"@define-color\s+([A-Za-z0-9_-]+)\s+([^;]+);")


class Palette:
    """noctalia's colours by name, reread whenever the file changes.

    Names are noctalia's, i.e. libadwaita's: window_bg_color, window_fg_color,
    accent_bg_color, accent_fg_color, card_bg_color, popover_bg_color,
    error_bg_color, theme_selected_bg_color, and the rest of that set.
    """

    def __init__(self, on_change=None):
        self._colors = {}
        self._on_change = on_change
        self._monitor = None
        self.reload()
        self._watch()

    @property
    def available(self):
        return bool(self._colors)

    def get(self, name, fallback=None):
        return self._colors.get(name, fallback)

    def reload(self):
        raw = {}
        try:
            with open(CSS_PATH, encoding="utf-8") as fh:
                for name, value in _DEFINE.findall(fh.read()):
                    raw[name] = value.strip()
        except OSError:
            self._colors = {}
            return

        # `@define-color theme_selected_bg_color @accent_bg_color;` is an alias,
        # and the template leans on them. Resolving here means a caller asking
        # for a name always gets something it can put in a stylesheet, rather
        # than a string starting with @ that GTK would then fail to parse in a
        # context where the original definition is not in scope.
        #
        # The chain is followed with a seen-set rather than a depth limit: a
        # cycle in a generated file is not expected, but an unbounded loop while
        # parsing a config is worse than a missing colour.
        resolved = {}
        for name in raw:
            value, seen = raw[name], {name}
            while value.startswith("@"):
                target = value[1:]
                if target in seen or target not in raw:
                    value = None
                    break
                seen.add(target)
                value = raw[target]
            if value:
                resolved[name] = value
        self._colors = resolved

    def _watch(self):
        """Reload on rewrite, so a theme change repaints what is already open.

        noctalia writes the file through a temporary and renames over it, which
        arrives as CREATED or MOVED_IN rather than CHANGED — hence watching the
        directory-level events too. The callback is debounced because a rename
        can deliver two of them for one write, and repainting twice flickers.
        """
        try:
            gfile = Gio.File.new_for_path(CSS_PATH)
            self._monitor = gfile.monitor_file(Gio.FileMonitorFlags.NONE, None)
            self._monitor.connect("changed", self._on_file_event)
        except GLib.Error:
            self._monitor = None

    def _on_file_event(self, _monitor, _f, _other, event):
        if event not in (
            Gio.FileMonitorEvent.CHANGES_DONE_HINT,
            Gio.FileMonitorEvent.CREATED,
            Gio.FileMonitorEvent.MOVED_IN,
            Gio.FileMonitorEvent.RENAMED,
        ):
            return
        if getattr(self, "_pending", 0):
            return
        self._pending = GLib.timeout_add(150, self._settled)

    def _settled(self):
        self._pending = 0
        before = dict(self._colors)
        self.reload()
        if self._colors != before and self._on_change:
            self._on_change(self)
        return False
