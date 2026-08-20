#!/usr/bin/env bash
# One-shot: let the battery tray switch power profiles without a password.
#
# Run:  sudo bash install-power-profile.sh
#   or: pkexec bash install-power-profile.sh   (GUI prompt, no TTY needed)
#   or: bash install-power-profile.sh <user>   (already root; name the account)
#
# Two steps, and they belong together:
#
#   1. Copy power-profile-apply to /usr/local/libexec, root-owned. The sudoers
#      rule below names that path, and a NOPASSWD rule pointing at a script in
#      the home directory would be a root shell for anyone who can edit it —
#      which is the whole point of not running it from where it's developed.
#   2. Install the sudoers rule for the invoking user.
#
# Re-run it after editing power-profile-apply; the installed copy is what runs.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"

HELPER=/usr/local/libexec/battery-power-profile
SUDOERS=/etc/sudoers.d/battery-power-profile

if [[ $EUID -ne 0 ]]; then
    echo "Run as root:  sudo bash $0" >&2
    echo "          or: pkexec bash $0" >&2
    exit 1
fi

# The sudoers rule has to name the account the tray runs as, and root isn't it.
# sudo and pkexec each record the caller differently, and neither is set when
# the script is run from an already-root shell — hence the argument.
USER_NAME=${1:-${SUDO_USER:-}}
if [[ -z $USER_NAME && -n ${PKEXEC_UID:-} ]]; then
    USER_NAME=$(getent passwd "$PKEXEC_UID" | cut -d: -f1)
fi
if [[ -z $USER_NAME || $USER_NAME == root ]]; then
    echo "Can't tell which account to install the rule for." >&2
    echo "Name it explicitly:  bash $0 <user>" >&2
    exit 1
fi
if ! getent passwd "$USER_NAME" >/dev/null; then
    echo "No such user: $USER_NAME" >&2
    exit 1
fi

echo ">> Installing the privileged helper to $HELPER"
install -Dm755 -o root -g root "$SRC/power-profile-apply" "$HELPER"
install -dm755 -o root -g root /var/lib/battery-power-profile

echo ">> Installing the sudoers rule for $USER_NAME"
# Validate before moving it into place: a syntactically bad file in
# /etc/sudoers.d makes sudo refuse to run at all, which is not a state you want
# to discover the next time you need root.
TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT
cat >"$TMP" <<EOF
# Lets the battery tray apply power profiles. Installed by
# $SRC/install-power-profile.sh
$USER_NAME ALL=(root) NOPASSWD: $HELPER performance, $HELPER default, $HELPER saver, $HELPER extreme
EOF

visudo -cqf "$TMP"
install -Dm440 -o root -g root "$TMP" "$SUDOERS"

if ! grep -Eq '^[[:space:]]*[@#]includedir[[:space:]]+/etc/sudoers.d' /etc/sudoers; then
    echo
    echo "WARNING: /etc/sudoers does not include /etc/sudoers.d — the rule was" >&2
    echo "installed but will be ignored, and the tray will fall back to a" >&2
    echo "polkit password prompt on every switch." >&2
fi

echo
echo "============================================================"
echo "Done. Check it without a password prompt:"
echo "  sudo -n $HELPER default"
echo "The tray's Power submenu should now switch profiles directly."
echo "============================================================"
