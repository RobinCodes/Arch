#!/usr/bin/env python3
"""zen-pick — choose what to open in zen: a set from apps.conf, or any app.

Reads candidate rows on stdin as `kind<TAB>label<TAB>runs<TAB>token`, one per
line, and prints the chosen tokens on stdout, one per line. Exits 1 if the user
cancels.

Written against GTK directly rather than as a `yad --list --checklist` because
the picker needs three things yad's list does not do together: a type-to-filter
box over ~80 rows, checkboxes for the multi-app case, and an unambiguous way to
get a launch command back out — yad prints columns joined by a separator, and
the commands here legitimately contain the characters yad separates on.
"""

import argparse
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk  # noqa: E402

COL_CHECK, COL_NAME, COL_RUNS, COL_TOKEN, COL_KIND = range(5)

CSS = """
.zen-hint { font-size: 11px; opacity: 0.65; padding: 2px 4px; }
.zen-kind { font-size: 11px; opacity: 0.75; }
"""


def read_rows(stream):
    rows = []
    for line in stream:
        parts = line.rstrip("\n").split("\t")
        if len(parts) >= 4 and parts[3]:
            rows.append(parts[:4])
    return rows


class Picker(Gtk.Window):
    def __init__(self, rows, multi_hint):
        super().__init__(title="Enter Zen")
        self.chosen = []

        self.set_default_size(560, 470)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_keep_above(True)
        self.set_type_hint(Gdk.WindowTypeHint.DIALOG)

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS.encode())
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self.store = Gtk.ListStore(bool, str, str, str, str)
        for kind, label, runs, token in rows:
            self.store.append([False, label, runs, token, kind])

        self.filtered = self.store.filter_new()
        self.filtered.set_visible_func(self.visible)
        self.query = ""

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_border_width(8)
        self.add(box)

        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("Type to filter…")
        self.search.connect("search-changed", self.on_search)
        self.search.connect("activate", self.on_search_activate)
        # Down out of the box lands on the list without touching the mouse.
        self.search.connect("key-press-event", self.on_search_key)
        box.pack_start(self.search, False, False, 0)

        self.view = Gtk.TreeView(model=self.filtered)
        self.view.set_headers_visible(True)
        self.view.set_enable_search(False)
        self.view.connect("row-activated", self.on_row_activated)

        toggle = Gtk.CellRendererToggle()
        toggle.connect("toggled", self.on_toggled)
        self.view.append_column(Gtk.TreeViewColumn("", toggle, active=COL_CHECK))

        name = Gtk.CellRendererText()
        name.set_property("ellipsize", 3)  # PANGO_ELLIPSIZE_END
        col = Gtk.TreeViewColumn("Application", name, text=COL_NAME)
        col.set_expand(True)
        col.set_min_width(200)
        self.view.append_column(col)

        runs = Gtk.CellRendererText()
        runs.set_property("ellipsize", 3)
        runs.set_property("scale", 0.85)
        col = Gtk.TreeViewColumn("Runs", runs, text=COL_RUNS)
        col.set_expand(True)
        self.view.append_column(col)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_shadow_type(Gtk.ShadowType.IN)
        scroll.add(self.view)
        box.pack_start(scroll, True, True, 0)

        hint = Gtk.Label(label=multi_hint, xalign=0)
        hint.get_style_context().add_class("zen-hint")
        hint.set_line_wrap(True)
        box.pack_start(hint, False, False, 0)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        buttons.set_halign(Gtk.Align.END)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.destroy())
        enter = Gtk.Button(label="Enter Zen")
        enter.get_style_context().add_class("suggested-action")
        enter.connect("clicked", lambda *_: self.accept())
        buttons.pack_start(cancel, False, False, 0)
        buttons.pack_start(enter, False, False, 0)
        box.pack_start(buttons, False, False, 0)

        self.connect("destroy", Gtk.main_quit)
        self.connect("key-press-event", self.on_key)
        self.search.grab_focus()

    # ---------------------------------------------------------- filtering --
    def visible(self, model, it, _data):
        if not self.query:
            return True
        haystack = f"{model[it][COL_NAME]} {model[it][COL_RUNS]}".lower()
        # Every word must appear somewhere, so "term xf" finds "Xfce Terminal"
        # regardless of the order the words were typed in.
        return all(word in haystack for word in self.query.split())

    def on_search(self, entry):
        self.query = entry.get_text().strip().lower()
        self.filtered.refilter()

    def on_search_key(self, _entry, event):
        if event.keyval in (Gdk.KEY_Down, Gdk.KEY_Up):
            self.view.grab_focus()
            if not self.view.get_selection().get_selected()[1]:
                first = self.filtered.get_iter_first()
                if first:
                    self.view.get_selection().select_iter(first)
            return True
        return False

    # ------------------------------------------------------------ actions --
    def on_toggled(self, _cell, path):
        it = self.filtered.convert_iter_to_child_iter(self.filtered.get_iter(path))
        self.store[it][COL_CHECK] = not self.store[it][COL_CHECK]

    def on_row_activated(self, _view, path, _column):
        """Double-click or Enter on a row: that one app, straight into zen.

        Deliberately ignores the checkboxes — activating a row is an unambiguous
        "this one", and having it silently drag along boxes ticked earlier is
        how you end up in zen with an app you forgot you had selected.
        """
        self.chosen = [self.filtered[path][COL_TOKEN]]
        self.destroy()

    def on_search_activate(self, _entry):
        """Enter in the search box takes the obvious match, if there is one.

        An exact name wins even when the filter left several rows showing,
        because the substring match is noisier than it looks: typing the set
        name "work" also matches "Advanced Network Configuration", and Enter
        should still open the set.
        """
        rows = [row[COL_TOKEN] for row in self.filtered]
        exact = [r[COL_TOKEN] for r in self.filtered if r[COL_NAME].lower() == self.query]
        if len(exact) == 1:
            self.chosen = exact
            self.destroy()
        elif len(rows) == 1:
            self.chosen = rows
            self.destroy()
        elif rows:
            self.view.grab_focus()
            first = self.filtered.get_iter_first()
            if first:
                self.view.get_selection().select_iter(first)

    def accept(self):
        # Base-store order, so a multi-app zen launches in the order the list
        # showed rather than the order the boxes happened to be ticked.
        self.chosen = [row[COL_TOKEN] for row in self.store if row[COL_CHECK]]
        if not self.chosen:
            model, it = self.view.get_selection().get_selected()
            if it:
                self.chosen = [model[it][COL_TOKEN]]
        if self.chosen:
            self.destroy()

    def on_key(self, _widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self.destroy()
            return True
        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and \
                self.view.has_focus():
            self.accept()
            return True
        return False


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--hint",
        default="Tick several boxes for a multi-app zen  ·  "
                "double-click or Enter for just one  ·  Esc cancels",
    )
    args = ap.parse_args()

    rows = read_rows(sys.stdin)
    if not rows:
        print("zen-pick: nothing to choose from", file=sys.stderr)
        return 1

    win = Picker(rows, args.hint)
    win.show_all()
    win.present()
    Gtk.main()

    for token in win.chosen:
        print(token)
    return 0 if win.chosen else 1


if __name__ == "__main__":
    sys.exit(main())
