#!/usr/bin/env bash
# Re-assert touchpad settings that XFCE's xfsettingsd resets at each login
# (scroll direction + tap mapping). Auto-detects the active driver, so it works
# whether the touchpad is on synaptics OR libinput -- no breakage during or
# after a driver switch. Runs from ~/.config/autostart/touchpad-tweaks.desktop.
DEV="ELAN0647:00 04F3:31B2 Touchpad"

apply_synaptics() {
    /usr/bin/synclient \
        VertScrollDelta=-100 HorizScrollDelta=-100 \
        VertTwoFingerScroll=1 HorizTwoFingerScroll=1 \
        TapButton1=1 TapButton2=3 TapButton3=2 \
        PalmDetect=1 LockedDrags=1 LockedDragTimeout=165 2>/dev/null
}

apply_libinput() {
    /usr/bin/xinput set-prop "$DEV" "libinput Natural Scrolling Enabled" 1 2>/dev/null
    /usr/bin/xinput set-prop "$DEV" "libinput Tapping Enabled" 1 2>/dev/null
    /usr/bin/xinput set-prop "$DEV" "libinput Tapping Drag Lock Enabled" 1 2>/dev/null
}

apply() {
    # synclient -l succeeds only when a synaptics device is present.
    if /usr/bin/synclient -l >/dev/null 2>&1; then
        apply_synaptics
    else
        apply_libinput
    fi
}

# Apply now, then again after xfsettingsd's startup pass, so our values win.
apply
sleep 2
apply
