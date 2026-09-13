#!/usr/bin/env bash
# Shared niri/Wayland helpers, sourced by the scripts in this repo.
#
# These exist because the ports off XFCE all needed the same three answers —
# "is this session niri", "what output is primary", "what has focus" — and each
# had grown its own slightly different guess. One copy, one behaviour.
#
# Source it, don't run it:   . "$(dirname "$0")/../lib/niri.sh"

# Which session is this running under?
#
#   niri     a live niri Wayland session (the IPC socket is reachable)
#   x11      an X11 session with xrandr available — XFCE, or anything else
#   unknown  neither: a TTY, an ssh login, a systemd unit that started too
#            early, or a Wayland compositor that is not niri
#
# This is the branch point for every script in this repo that touches the
# desktop. The two backends are kept side by side rather than one being
# ported over the other, because the machine dual-boots between the two
# sessions and a script that only works in the session you are not in is a
# script you cannot use.
session_kind() {
    if niri_running; then
        echo niri
    elif [[ -n ${DISPLAY:-} ]] && command -v xrandr >/dev/null 2>&1; then
        echo x11
    else
        echo unknown
    fi
}

# True in a live niri session. Checks the socket rather than $XDG_CURRENT_DESKTOP
# because the variable is inherited by anything the session spawns, including a
# shell on a TTY that has no way to talk to the compositor, while the socket is
# the thing the IPC actually needs.
niri_running() {
    [[ -n ${NIRI_SOCKET:-} ]] && [[ -S ${NIRI_SOCKET:-} ]]
}

# niri has no concept of a primary output — it is a scrolling WM with no
# "main" screen. So primary means: the output the focused window is on, and
# failing that the first output niri lists. That is the closest honest
# equivalent to xrandr's `primary` flag, and it is what the user is looking at.
niri_primary_output() {
    local out
    out=$(niri msg --json focused-output 2>/dev/null \
          | python3 -c 'import json,sys
try: print(json.load(sys.stdin)["name"])
except Exception: pass' 2>/dev/null)
    if [[ -n $out ]]; then
        printf '%s\n' "$out"
        return 0
    fi
    niri msg --json outputs 2>/dev/null \
        | python3 -c 'import json,sys
try:
    d = json.load(sys.stdin)
    print(sorted(d)[0])
except Exception: pass' 2>/dev/null
}

# PID of the focused window, or nothing. This is what replaced the xprop
# _NET_ACTIVE_WINDOW walk: Wayland has no such root property, and niri hands
# the pid over directly rather than making us map a window id back to a client.
niri_focused_pid() {
    niri msg --json focused-window 2>/dev/null \
        | python3 -c 'import json,sys
try:
    w = json.load(sys.stdin)
    if w and w.get("pid"): print(w["pid"])
except Exception: pass' 2>/dev/null
}

# Physical width of an output in millimetres, from its EDID. Empty when the
# panel reports nothing usable — a 0mm EDID, a KVM in the way — which callers
# have to fall back for rather than divide by.
niri_physical_width_mm() {
    niri msg --json outputs 2>/dev/null \
        | python3 -c 'import json,sys
try:
    d = json.load(sys.stdin)
    o = d.get(sys.argv[1])
    mm = (o or {}).get("physical_size") or [0, 0]
    if mm[0] and mm[0] > 0: print(mm[0])
except Exception: pass' "$1" 2>/dev/null
}
