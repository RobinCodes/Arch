#!/usr/bin/env python3
"""noctalia-setting — read and write one boolean in noctalia's settings.toml.

nolock needs to reach three settings that noctalia has no IPC command for, and
this is the smallest thing that can do it safely.

WHY TEXT EDITING AND NOT A TOML LIBRARY

Python reads TOML in the standard library and does not write it, and the two
third-party writers both reformat the whole document — which would mean
handing noctalia's own settings file back to it with every comment gone and
every table reordered. This only ever flips a bare `key = true/false` inside a
known table, so a targeted edit keeps the rest of the file byte-identical and
cannot corrupt a section it does not understand.

A key that is absent is genuinely absent, not false: noctalia writes only what
differs from its defaults, so an unset key means "whatever the default is".
`get` reports that as `unset` and lets the caller decide, which is the same
distinction the XFCE version drew between an unset xfconf property and one
explicitly set to its locking value.

Usage:
  noctalia-setting.py get <table> <key>            -> true | false | unset
  noctalia-setting.py set <table> <key> <true|false>
  noctalia-setting.py del <table> <key>            -> back to noctalia's default
"""

import os
import re
import sys

SETTINGS = os.path.join(
    os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")),
    "noctalia",
    "settings.toml",
)


def read():
    try:
        with open(SETTINGS, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def table_span(text, table):
    """(start, end) character offsets of a table's body, or None.

    A table runs from the line after its header to the next header at the same
    or any level. noctalia indents sub-tables, so the header pattern allows
    leading whitespace.
    """
    header = re.compile(
        r"^[ \t]*\[" + re.escape(table) + r"\][ \t]*$", re.MULTILINE
    )
    match = header.search(text)
    if not match:
        return None
    start = match.end()
    nxt = re.compile(r"^[ \t]*\[", re.MULTILINE).search(text, start)
    return (start, nxt.start() if nxt else len(text))


def get(table, key):
    text = read()
    span = table_span(text, table)
    if span is None:
        return "unset"
    body = text[span[0]:span[1]]
    match = re.search(
        r"^[ \t]*" + re.escape(key) + r"[ \t]*=[ \t]*(true|false)[ \t]*$",
        body,
        re.MULTILINE,
    )
    return match.group(1) if match else "unset"


def set_(table, key, value):
    text = read()
    span = table_span(text, table)

    if span is None:
        # New table, appended. noctalia merges what it reads over its defaults,
        # so a table holding one key is a complete and valid override.
        if text and not text.endswith("\n"):
            text += "\n"
        if text:
            text += "\n"
        text += f"[{table}]\n{key} = {value}\n"
    else:
        body = text[span[0]:span[1]]
        pattern = re.compile(
            r"^([ \t]*" + re.escape(key) + r"[ \t]*=[ \t]*)(true|false)([ \t]*)$",
            re.MULTILINE,
        )
        if pattern.search(body):
            body = pattern.sub(lambda m: m.group(1) + value + m.group(3), body, count=1)
        else:
            # Inserted at the top of the table rather than the bottom: the last
            # line of a table body is often a blank separator before the next
            # header, and appending after it would land the key outside.
            body = f"{key} = {value}\n" + body.lstrip("\n")
            body = "\n" + body if not body.startswith("\n") else body
        text = text[:span[0]] + body + text[span[1]:]

    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    tmp = SETTINGS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, SETTINGS)


def del_(table, key):
    """Remove a key, putting it back to whatever noctalia defaults it to.

    This is how nolock restores a key it found unset. Writing the "would lock"
    value instead looks equivalent and is not: two of the three keys nolock
    manages default to *not* locking, so writing the locking value over an
    unset key arms a lock that was never armed.

    An emptied table is left in place. It is valid TOML, noctalia merges it
    over its defaults to no effect, and removing it would mean deciding whether
    a table this script did not create is safe to delete.
    """
    text = read()
    span = table_span(text, table)
    if span is None:
        return
    body = text[span[0]:span[1]]
    pattern = re.compile(
        r"^[ \t]*" + re.escape(key) + r"[ \t]*=[ \t]*(?:true|false)[ \t]*\n?",
        re.MULTILINE,
    )
    new_body = pattern.sub("", body, count=1)
    if new_body == body:
        return
    text = text[:span[0]] + new_body + text[span[1]:]

    tmp = SETTINGS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, SETTINGS)


def main():
    args = sys.argv[1:]
    if len(args) == 3 and args[0] == "del":
        del_(args[1], args[2])
        return 0
    if len(args) == 3 and args[0] == "get":
        print(get(args[1], args[2]))
        return 0
    if len(args) == 4 and args[0] == "set" and args[3] in ("true", "false"):
        set_(args[1], args[2], args[3])
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
