#!/usr/bin/env bash
# One-shot: make closing the lid lock the session instead of suspending it, so
# that locking and blanking the screen stop nothing and only an explicit sleep
# freezes user.slice.
#
# Run:  sudo bash install-lid-lock.sh
#
# Re-runnable — the drop-in is overwritten from this folder every time.
#
# Unlike install-keyboard-fix.sh, this one applies the value immediately.
# That note next door ("restarting logind drops the seat and can take the X
# session with it") is about `restart`, and it still holds. `reload` is a
# different operation: logind re-reads its configuration in place without
# touching seats or sessions, and systemd-logind has advertised CanReload=yes
# since well before the 261 on this machine. Checked rather than assumed below,
# because falling back to "next boot" is fine and losing the session is not.
set -euo pipefail

SRC="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
LOGIND_DIR=/etc/systemd/logind.conf.d
LOGIND_CONF="$LOGIND_DIR/20-lid-lock.conf"

if [[ $EUID -ne 0 ]]; then
    echo "Run as root:  sudo bash $0" >&2
    exit 1
fi

[[ -f "$SRC/20-lid-lock.conf" ]] || { echo "missing $SRC/20-lid-lock.conf" >&2; exit 1; }

echo "==> installing $LOGIND_CONF"
install -d -m755 "$LOGIND_DIR"
install -Dm644 "$SRC/20-lid-lock.conf" "$LOGIND_CONF"

echo "==> reloading systemd"
systemctl daemon-reload

if [[ "$(systemctl show systemd-logind.service -p CanReload --value)" == "yes" ]]; then
    echo "==> reloading systemd-logind (in place; seats and sessions untouched)"
    systemctl reload systemd-logind.service
    applied="now"
else
    echo "==> systemd-logind does not support reload here; leaving it alone"
    echo "    (a restart would drop the seat, which is not worth it for this)"
    applied="after the next reboot"
fi

# Read the value back off the bus rather than trusting the file. logind does
# not fail on an unparseable Handle* value, it warns and keeps its default, so
# the file saying `lock` is not evidence that `lock` is in force. This is also
# the check that would catch `lock` not being accepted by this systemd at all.
prop() {
    busctl get-property org.freedesktop.login1 /org/freedesktop/login1 \
        org.freedesktop.login1.Manager "$1" 2>/dev/null | awk '{print $2}' | tr -d '"'
}

echo
echo "Installed. Effective settings, $applied:"
lid="$(prop HandleLidSwitch)"
printf '    HandleLidSwitch = %s\n' "${lid:-<unreadable>}"
printf '    IdleAction      = %s\n' "$(prop IdleAction)"

echo
if [[ "$applied" == "now" && "$lid" != "lock" ]]; then
    echo "WARNING: logind still reports '${lid:-unknown}', not 'lock'."
    echo "  The drop-in is in place but is not in force. Check for a parse"
    echo "  warning with:  journalctl -u systemd-logind -n 20"
    echo "  A lid close will still SUSPEND, and still freeze user.slice."
    exit 1
fi

echo "Closing the lid now locks and blanks, and leaves every process running."
echo "Nothing will sleep this laptop on its own — suspend from the session menu"
echo "or with 'systemctl suspend' when you actually want work to stop."
