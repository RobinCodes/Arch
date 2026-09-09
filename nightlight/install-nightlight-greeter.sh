#!/usr/bin/env bash
# One-shot: warm the login screen too, not just the session.
#
# Run:  sudo bash install-nightlight-greeter.sh
#   or: pkexec bash install-nightlight-greeter.sh   (GUI prompt, no TTY needed)
#   or: bash install-nightlight-greeter.sh <user>   (already root; name the account)
#
# Two steps, and they belong together:
#
#   1. Copy nightlight-greeter to /usr/local/libexec, root-owned. LightDM runs
#      it as root before anyone has authenticated, and a root-run script living
#      in the home directory would be a root shell for whoever could edit it —
#      the same reason install-power-profile.sh copies its helper out.
#   2. Point LightDM's display-setup-script and greeter-setup-script at it, for
#      the account whose schedule the greeter should follow.
#
# The drop-in names this folder, because the installed copy asks nightlight-auto
# here what the schedule wants rather than carrying its own copy of the curve.
# So: re-run this after editing nightlight-greeter *or* after moving this folder.
# Moving it without re-running is not fatal — the greeter falls back to the last
# temperature in redshift.conf — but the login screen stops tracking the clock.
set -euo pipefail
SRC="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"

# --- Is LightDM even the display manager any more? -------------------------
#
# This machine moved to greetd + noctalia-greeter, and under that stack nothing
# reads /etc/lightdm at all: the drop-in below would be written, be valid, and
# be ignored forever. Refusing is the honest outcome, because a silent no-op is
# indistinguishable from a fix that did not work.
#
# The replacement is one IPC call, not a script. `noctalia msg greeter-sync`
# pushes the monitor layout and the colour scheme to the greeter, and
# monitor/resolution now calls it on every mode change — so the login screen
# follows the session for the same "one source of truth" reason this script's
# header argues for, with none of the privilege-dropping this needed.
DM=$(readlink -f /etc/systemd/system/display-manager.service 2>/dev/null || true)
if [[ $DM != */lightdm.service ]]; then
    echo "LightDM is not the display manager here (found: ${DM##*/})." >&2
    echo >&2
    echo "Nothing reads /etc/lightdm on this system, so installing these hooks" >&2
    echo "would change nothing. Under greetd + noctalia-greeter the equivalent is:" >&2
    echo >&2
    echo "    noctalia msg greeter-sync" >&2
    echo >&2
    echo "which monitor/resolution already runs whenever the mode or scale changes." >&2
    echo "Re-run this only if you switch back to LightDM." >&2
    echo >&2
    echo "Pass FORCE_LIGHTDM=1 to install anyway." >&2
    [[ ${FORCE_LIGHTDM:-} == 1 ]] || exit 1
fi

HELPER=/usr/local/libexec/nightlight-greeter
DROPIN=/etc/lightdm/lightdm.conf.d/50-nightlight.conf

if [[ $EUID -ne 0 ]]; then
    echo "Run as root:  sudo bash $0" >&2
    echo "          or: pkexec bash $0" >&2
    exit 1
fi

# The drop-in has to name the account whose night light schedule the greeter
# follows, and root isn't it. sudo and pkexec each record the caller
# differently, and neither is set when this is run from an already-root shell —
# hence the argument.
USER_NAME=${1:-${SUDO_USER:-}}
if [[ -z $USER_NAME && -n ${PKEXEC_UID:-} ]]; then
    USER_NAME=$(getent passwd "$PKEXEC_UID" | cut -d: -f1)
fi
if [[ -z $USER_NAME || $USER_NAME == root ]]; then
    echo "Can't tell whose schedule the login screen should follow." >&2
    echo "Name the account explicitly:  bash $0 <user>" >&2
    exit 1
fi
if ! getent passwd "$USER_NAME" >/dev/null; then
    echo "No such user: $USER_NAME" >&2
    exit 1
fi

[[ -f "$SRC/nightlight-greeter" ]] || { echo "missing $SRC/nightlight-greeter" >&2; exit 1; }
[[ -x "$SRC/nightlight-auto" ]]    || { echo "missing $SRC/nightlight-auto" >&2; exit 1; }
command -v redshift >/dev/null || { echo "redshift is not installed" >&2; exit 1; }

bash -n "$SRC/nightlight-greeter"

echo ">> Installing the greeter helper to $HELPER"
install -Dm755 -o root -g root "$SRC/nightlight-greeter" "$HELPER"

echo ">> Pointing LightDM at it for $USER_NAME"
# A drop-in rather than an edit to /etc/lightdm/lightdm.conf: that file belongs
# to the package, and editing it earns a .pacnew to reconcile on every update.
# conf.d is read before it, so this only holds because the packaged file leaves
# both of these keys commented out — check there first if the colour never lands.
install -dm755 -o root -g root /etc/lightdm/lightdm.conf.d
cat >"$DROPIN" <<EOF
# Managed by $SRC/install-nightlight-greeter.sh — re-run that rather than
# editing this, or the next run will overwrite what you wrote here.
#
# Twice on purpose: the first sets the ramp before the greeter draws, the second
# puts it back if the greeter's own display pass wiped it.
[Seat:*]
display-setup-script=$HELPER $USER_NAME $SRC/nightlight-auto
greeter-setup-script=$HELPER $USER_NAME $SRC/nightlight-auto
EOF
chmod 644 "$DROPIN"

# Prove the half that can silently do nothing: the privilege drop and the path
# to the schedule. If this prints a number, the greeter will get one too.
echo ">> Checking the schedule answers as $USER_NAME"
read -r uid gid < <(getent passwd "$USER_NAME" | cut -d: -f3,4 | tr : ' ')
home_dir=$(getent passwd "$USER_NAME" | cut -d: -f6)
pct=$(setpriv --reuid "$uid" --regid "$gid" --init-groups --inh-caps=-all -- \
          env -i HOME="$home_dir" PATH=/usr/local/bin:/usr/bin \
          "$SRC/nightlight-auto" --print 2>/dev/null || true)
if [[ "$pct" =~ ^[0-9]+$ ]]; then
    echo "   schedule wants ${pct}% right now ($((6500 - pct * 40))K)"
else
    echo "   WARNING: nightlight-auto --print gave nothing usable." >&2
    echo "   The login screen will fall back to the last value in redshift.conf." >&2
fi

if [[ "$(readlink -f /etc/systemd/system/display-manager.service 2>/dev/null)" != */lightdm.service ]]; then
    echo
    echo "WARNING: LightDM is not the active display manager, so nothing will" >&2
    echo "run these hooks. The helper is installed and harmless either way." >&2
fi

echo
echo "============================================================"
echo "Done. Test it without logging out:"
echo "  sudo $HELPER $USER_NAME $SRC/nightlight-auto"
echo "(that sets this session's screen to the same value it would set at the"
echo "login screen). Then log out — the greeter should already be warm."
echo "============================================================"
