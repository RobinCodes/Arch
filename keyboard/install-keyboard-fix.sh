#!/usr/bin/env bash
# One-shot: stop xfce4-screensaver from wedging the keyboard on suspend, and
# leave a mouse-clickable way out for the times it still does.
#
# Run:  sudo bash install-keyboard-fix.sh
#
# Re-runnable — every file is overwritten from this folder.
#
# Two halves, and only the first is a fix:
#
#   1. logind waits 30s instead of 5s for the screensaver to finish locking
#      before it suspends anyway. This is what stops the session being frozen
#      mid-grab in the first place.
#   2. `unstick-keyboard` plus a launcher for it, because a 30s budget is a
#      bigger budget and not a guarantee. A machine deep in the swapfile can
#      still miss it, and when it does the recovery has to be reachable with
#      the pointer alone.
set -euo pipefail

SRC="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
LOGIND_DIR=/etc/systemd/logind.conf.d
LOGIND_CONF="$LOGIND_DIR/10-inhibit-delay.conf"
BIN=/usr/local/bin/unstick-keyboard
DESKTOP=/usr/share/applications/unstick-keyboard.desktop

if [[ $EUID -ne 0 ]]; then
    echo "Run as root:  sudo bash $0" >&2
    exit 1
fi

for f in unstick-keyboard unstick-keyboard.desktop 10-inhibit-delay.conf; do
    [[ -f "$SRC/$f" ]] || { echo "missing $SRC/$f" >&2; exit 1; }
done

echo "==> installing unstick-keyboard to $BIN"
install -Dm755 "$SRC/unstick-keyboard" "$BIN"

echo "==> installing launcher to $DESKTOP"
install -Dm644 "$SRC/unstick-keyboard.desktop" "$DESKTOP"

echo "==> installing $LOGIND_CONF"
install -d -m755 "$LOGIND_DIR"
install -Dm644 "$SRC/10-inhibit-delay.conf" "$LOGIND_CONF"

# A drop-in is only read at logind startup. Restarting logind is deliberately
# not done here: it drops the seat and can take the X session with it, which is
# a strange thing for a fix to this particular bug to do. The value applies at
# the next boot, and there is no hurry — nothing is worse in the meantime.
echo "==> reloading systemd"
systemctl daemon-reload

echo
echo "Installed. Effective InhibitDelayMaxSec after the next reboot:"
printf '    %s\n' "$(grep -E '^InhibitDelayMaxSec' "$LOGIND_CONF")"
echo
echo "Recovery, when the keyboard dies before then:"
echo "    click Applications > System > Unstick Keyboard   (mouse only)"
echo "    or run  unstick-keyboard  from a TTY or over ssh"
echo
echo "Add it to the panel: right-click the panel > Panel > Add New Items"
echo "  > Launcher > Add, then drag in \"Unstick Keyboard\"."
