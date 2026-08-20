#!/usr/bin/env bash
# Disable the built-in touchpad whenever a USB mouse is plugged in; re-enable it
# when the last one is unplugged.  Runs from touchpad-auto.service (user unit).
#
# WHY THIS EXISTS: the ELAN0647 touchpad on this machine emits ghost touches.
# Measured 2026-08-20 with `xinput test-xi2 --root`, nobody touching the laptop:
# 32 phantom button events in 30s -- left, middle, and (because TapButton2=3 /
# TappingButtonMap "lrm" maps a two-finger tap to the right button) a stream of
# spurious RIGHT-CLICKS.  Xorg agrees, on event13:
#     (EE) ELAN0647:00 04F3:31B2 Touchpad: kernel bug: Touch jump detected and discarded.
# Phantom presses also outnumber releases, i.e. the button sticks down.  A stuck
# button holds an implicit pointer grab, which is very likely the source of the
# session wedges that grab-watchdog was written to break.
#
# WHY A POLLER AND NOT A UDEV RULE: udev RUN+= executes as root in a short-lived,
# session-less context with no DISPLAY or XAUTHORITY, and udev kills long or
# blocking RUN commands.  xinput is an X client and needs the user's session, so
# driving it from udev is unreliable by construction.  This runs inside the XFCE
# session where the X connection already exists, and needs no root at all.
#
# WHY IT RE-ASSERTS EVERY TICK instead of only on plug/unplug: xfsettingsd and
# apply-touchpad.sh both write to this device at login and can flip it back on.
# Comparing against the *actual* X property each tick means we correct that drift
# instead of trusting a cached idea of the state.

set -uo pipefail

TOUCHPAD="ELAN0647:00 04F3:31B2 Touchpad"
POLL=2

# A USB pointer is any input device that exposes a mouseN handler and whose Phys
# is a usb- path.  Matching the class rather than one VID:PID means a different
# mouse also works.  The internal ELAN devices are i2c-, so they never match.
usb_mouse_present() {
    awk '
        /^P: Phys=/     { phys = substr($0, 9) }
        /^H: Handlers=/ { if ($0 ~ /mouse[0-9]/ && phys ~ /^usb-/) { found = 1 } }
        END             { exit(found ? 0 : 1) }
    ' /proc/bus/input/devices
}

# 0 = disabled, 1 = enabled, empty = device not present to X (yet).
touchpad_state() {
    xinput list-props "$TOUCHPAD" 2>/dev/null \
        | awk -F':' '/Device Enabled/ { gsub(/[^0-9]/, "", $2); print $2; exit }'
}

# Fail fast if X is not up yet; systemd restarts us until it is (same contract
# grab-watchdog.service uses).
xinput list >/dev/null 2>&1 || {
    echo "touchpad-auto: cannot reach X display '${DISPLAY:-unset}'" >&2
    exit 1
}

echo "touchpad-auto: watching '$TOUCHPAD', poll=${POLL}s"

while true; do
    if usb_mouse_present; then want=0; else want=1; fi

    have=$(touchpad_state)
    if [[ -n $have && $have -ne $want ]]; then
        if (( want == 0 )); then
            xinput disable "$TOUCHPAD" 2>/dev/null \
                && echo "touchpad-auto: USB mouse present -> touchpad disabled"
        else
            xinput enable "$TOUCHPAD" 2>/dev/null \
                && echo "touchpad-auto: no USB mouse -> touchpad enabled"
        fi
    fi

    sleep "$POLL"
done
