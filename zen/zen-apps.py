#!/usr/bin/env python3
"""zen-apps — list every installed application, as `name<TAB>command` rows.

This is what makes "zen anything" work from the picker. `zen firefox` always
worked from a terminal, but the picker only knew the sets in apps.conf, and the
whole point of the picker is that it is reachable from a hotkey when there is no
terminal left to type in.

Desktop entries are read straight from the XDG search path rather than via a
menu library, because gnome-menus / garcon bindings are not installed and the
spec's parsing rules are short enough to implement exactly.
"""

import os
import shlex
import sys

# Field codes a launcher is supposed to substitute (files, urls, icon, ...).
# Nothing is being opened here, so they are dropped. %% is a literal percent.
FIELD_CODES = set("fFuUdDnNickvm")

# Flatpak's --file-forwarding brackets the file arguments with @@u ... @@.
# With the field codes gone the markers are left behind as stray argv entries,
# and flatpak treats a dangling @@ as an error.
FORWARD_MARKERS = {"@@", "@@u", "@@U"}

# Terminal=true entries have no window of their own; they need one wrapped
# around them or zen would launch a process nobody can see.
TERMINALS = (
    ("xfce4-terminal", "--command="),
    ("alacritty", "-e "),
    ("kitty", ""),
    ("xterm", "-e "),
)


def data_dirs():
    """XDG application directories, most specific first (spec order)."""
    home = os.environ.get(
        "XDG_DATA_HOME", os.path.join(os.path.expanduser("~"), ".local/share")
    )
    rest = os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share")
    out = []
    for base in [home] + [d for d in rest.split(":") if d]:
        path = os.path.join(base, "applications")
        if os.path.isdir(path) and path not in out:
            out.append(path)
    return out


def parse(path):
    """The [Desktop Entry] group as a dict, or None if the file has none.

    Later groups ([Desktop Action new-window] and friends) are deliberately
    ignored: they carry their own Name and Exec and would show up as duplicate
    entries for the same application.
    """
    entry = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            in_entry = False
            for line in fh:
                line = line.strip()
                if line.startswith("["):
                    if in_entry:
                        break
                    in_entry = line == "[Desktop Entry]"
                    continue
                if not in_entry or "=" not in line or line.startswith("#"):
                    continue
                key, _, value = line.partition("=")
                entry.setdefault(key.strip(), value.strip())
    except OSError:
        return None
    return entry or None


def clean_exec(value):
    """An Exec= line reduced to something bash can run.

    shlex is used rather than a regex so that a field code inside a quoted
    argument is still recognised, and so the result can be re-quoted without
    losing arguments that contain spaces.
    """
    try:
        parts = shlex.split(value)
    except ValueError:
        return None
    out = []
    for part in parts:
        if part in FORWARD_MARKERS:
            continue
        rebuilt, i, dropped = "", 0, False
        while i < len(part):
            if part[i] == "%" and i + 1 < len(part):
                nxt = part[i + 1]
                if nxt == "%":
                    rebuilt += "%"
                    i += 2
                    continue
                if nxt in FIELD_CODES:
                    dropped = True
                    i += 2
                    continue
            rebuilt += part[i]
            i += 1
        # An argument that was nothing but a field code disappears entirely;
        # keeping it as "" would pass an empty argv entry to the program.
        if rebuilt or not dropped:
            out.append(rebuilt)
    return shlex.join(out) if out else None


def which(cmd):
    for d in os.environ.get("PATH", "").split(":"):
        if d and os.access(os.path.join(d, cmd), os.X_OK):
            return True
    return False


def terminal_wrap(command):
    for term, flag in TERMINALS:
        if which(term):
            if flag.endswith("="):
                return f"{term} {flag}{shlex.quote(command)}"
            if flag:
                return f"{term} {flag}{command}"
            return f"{term} {command}"
    return None


def main():
    seen = set()
    apps = []

    for directory in data_dirs():
        for root, _, files in os.walk(directory):
            for name in sorted(files):
                if not name.endswith(".desktop"):
                    continue
                path = os.path.join(root, name)
                # The desktop file ID, so a user override in ~/.local/share
                # hides the system copy instead of listing both.
                app_id = os.path.relpath(path, directory).replace(os.sep, "-")
                if app_id in seen:
                    continue
                seen.add(app_id)

                entry = parse(path)
                if not entry or entry.get("Type", "Application") != "Application":
                    continue
                if entry.get("NoDisplay", "").lower() == "true":
                    continue
                if entry.get("Hidden", "").lower() == "true":
                    continue
                try_exec = entry.get("TryExec")
                if try_exec and not (os.path.isabs(try_exec) and os.access(try_exec, os.X_OK)) \
                        and not which(try_exec):
                    continue

                command = clean_exec(entry.get("Exec", ""))
                if not command:
                    continue
                if entry.get("Terminal", "").lower() == "true":
                    command = terminal_wrap(command)
                    if not command:
                        continue

                label = entry.get("Name") or os.path.splitext(name)[0]
                apps.append((label, command))

    apps.sort(key=lambda a: a[0].lower())
    for label, command in apps:
        # Tabs and newlines in a Name would corrupt the row the caller builds.
        label = label.replace("\t", " ").replace("\n", " ")
        print(f"{label}\t{command}")
    return 0 if apps else 1


if __name__ == "__main__":
    sys.exit(main())
