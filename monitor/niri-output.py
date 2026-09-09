#!/usr/bin/env python3
"""niri-output — the persistence half of `resolution`, against niri's config.

WHY A SEPARATE FILE FOR THIS

`niri msg output ... mode ...` is explicitly temporary: niri forgets it the
next time the config is re-read, which a config file save or a `load-config-
file` action both cause. So the same split the XFCE version had — call the
compositor for *now*, write the profile for *later* — still applies. Only the
profile moved, from xfconf's displays.xml to niri's own config language.

It does NOT write config.kdl itself. That file is hand-maintained, KDL has
comments and `/-` node-commenting this would have to round-trip, and a script
that reformats a config every five minutes is a script that eventually eats
one. Instead niri's `include` is used: everything here goes in outputs.kdl,
which is machine-owned and safe to rewrite wholesale, and config.kdl gets one
`include` line added once. That line is the only edit ever made to it.

Subcommands:
  ensure-include        add the include to config.kdl if it is not there
  get <output>          print "<mode> <scale>" as recorded, or nothing
  set <output> <mode> <scale>    record them and rewrite outputs.kdl
"""

import os
import re
import sys

CONFIG_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "niri"
)
CONFIG = os.path.join(CONFIG_DIR, "config.kdl")
OUTPUTS = os.path.join(CONFIG_DIR, "outputs.kdl")
INCLUDE_LINE = 'include "outputs.kdl"'

HEADER = """\
// Managed by Scripts/Arch/monitor/resolution — rewritten in full on every
// change, so anything you add here by hand is lost. Put hand-written output
// settings in config.kdl instead; niri applies the last definition it reads,
// and config.kdl's include of this file is what decides which that is.
//
// `resolution retier` recomputes scale from the monitor's EDID millimetres
// and its pixel budget. See the header of ./resolution for why both matter.
"""

# One `output "NAME" { ... }` block. Non-greedy to the first closing brace at
# the start of a line, which is all this file's own writer ever produces --
# and this only ever parses what it wrote.
BLOCK = re.compile(
    r'^output\s+"(?P<name>[^"]+)"\s*\{(?P<body>.*?)^\}\s*$',
    re.MULTILINE | re.DOTALL,
)


def read_blocks():
    """{output name: {"mode": str|None, "scale": str|None}} from outputs.kdl."""
    try:
        with open(OUTPUTS, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return {}

    out = {}
    for match in BLOCK.finditer(text):
        body = match.group("body")
        entry = {}
        mode = re.search(r'^\s*mode\s+"([^"]+)"', body, re.MULTILINE)
        scale = re.search(r"^\s*scale\s+([0-9.]+)", body, re.MULTILINE)
        if mode:
            entry["mode"] = mode.group(1)
        if scale:
            entry["scale"] = scale.group(1)
        out[match.group("name")] = entry
    return out


def write_blocks(blocks):
    parts = [HEADER]
    for name in sorted(blocks):
        entry = blocks[name]
        if not entry.get("mode") and not entry.get("scale"):
            continue
        parts.append(f'\noutput "{name}" {{')
        if entry.get("mode"):
            parts.append(f'    mode "{entry["mode"]}"')
        if entry.get("scale"):
            parts.append(f'    scale {entry["scale"]}')
        parts.append("}\n")

    os.makedirs(CONFIG_DIR, exist_ok=True)
    # Written through a temp file in the same directory and renamed, because
    # niri watches this path: a partial write is a config it will try to parse
    # and reject, and a rejected config is a desktop that stops applying any of
    # this until the next save.
    tmp = OUTPUTS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(parts))
    os.replace(tmp, OUTPUTS)


def ensure_include():
    """Add the include to config.kdl, once. Returns True if it changed."""
    try:
        with open(CONFIG, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return False

    # Any include of this file counts, however it is quoted or spaced, so a
    # line the user wrote themselves is not duplicated.
    if re.search(r'^\s*include\s+"[^"]*outputs\.kdl"', text, re.MULTILINE):
        return False

    if not text.endswith("\n"):
        text += "\n"
    text += (
        "\n// Output modes and scales picked by Scripts/Arch/monitor/resolution.\n"
        "// niri applies the last definition it reads, so this include sits at the\n"
        "// end on purpose: it is meant to win over anything above it.\n"
        f"{INCLUDE_LINE}\n"
    )
    tmp = CONFIG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, CONFIG)
    return True


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__, file=sys.stderr)
        return 2

    cmd = args[0]

    if cmd == "ensure-include":
        print("added" if ensure_include() else "present")
        return 0

    if cmd == "get" and len(args) == 2:
        entry = read_blocks().get(args[1], {})
        if entry:
            print(f"{entry.get('mode', '-')} {entry.get('scale', '-')}")
        return 0

    if cmd == "set" and len(args) == 4:
        _, name, mode, scale = args
        blocks = read_blocks()
        entry = blocks.setdefault(name, {})
        # "-" means leave this field as it was, so `retier` can change the
        # scale without having to restate a mode it was not asked about.
        if mode != "-":
            entry["mode"] = mode
        if scale != "-":
            entry["scale"] = scale
        write_blocks(blocks)
        ensure_include()
        return 0

    print(f"niri-output: bad arguments: {' '.join(args)}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
