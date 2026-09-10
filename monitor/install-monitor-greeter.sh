#!/usr/bin/env bash
# One-shot: give the login screen the display layout the session already has.
#
# Run:  sudo bash install-monitor-greeter.sh
#   or: pkexec bash install-monitor-greeter.sh   (GUI prompt, no TTY needed)
#   or: bash install-monitor-greeter.sh <user>   (already root; name the account)
#
# Three steps:
#
#   1. Copy monitor-greeter to /usr/local/libexec, root-owned. LightDM runs it
#      as root before anyone has authenticated, and a root-run script living in
#      the home directory would be a root shell for whoever could edit it — the
#      same reason install-nightlight-greeter.sh copies its helper out.
#   2. Point LightDM's display-setup-script at it, chaining to whatever already
#      held that hook — nightlight, on this machine. LightDM allows one script
#      per seat, and the order matters in one direction only: a modeset wipes
#      the gamma ramp, so the layout has to run before the night light, never
#      after.
#   3. Tell lightdm-gtk-greeter which monitor to draw the login box on, as a
#      belt-and-braces for the case where step 2 runs but xrandr refuses the
#      arrangement. Cheap, independent, and it covers exactly the failure this
#      whole folder exists to remove.
#
# The drop-in names the account whose saved XFCE display profile the greeter
# should copy, and it names this folder nowhere — monitor-greeter reads
# displays.xml directly. So re-run this after editing monitor-greeter, and after
# re-running install-nightlight-greeter.sh (which rewrites the drop-in this one
# chains to, and could move it out from under us). Moving this folder is fine.
set -euo pipefail
SRC="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"

HELPER=/usr/local/libexec/monitor-greeter
DROPIN=/etc/lightdm/lightdm.conf.d/60-monitor.conf
GREETER_DROPIN=/etc/lightdm/lightdm-gtk-greeter.conf.d/60-monitor.conf
NIGHTLIGHT_DROPIN=/etc/lightdm/lightdm.conf.d/50-nightlight.conf

if [[ $EUID -ne 0 ]]; then
    echo "Run as root:  sudo bash $0" >&2
    echo "          or: pkexec bash $0" >&2
    exit 1
fi

# The drop-in has to name the account whose display profile the greeter follows,
# and root isn't it. sudo and pkexec each record the caller differently, and
# neither is set when this is run from an already-root shell — hence the
# argument. Lifted verbatim from install-nightlight-greeter.sh, on purpose.
USER_NAME=${1:-${SUDO_USER:-}}
if [[ -z $USER_NAME && -n ${PKEXEC_UID:-} ]]; then
    USER_NAME=$(getent passwd "$PKEXEC_UID" | cut -d: -f1)
fi
if [[ -z $USER_NAME || $USER_NAME == root ]]; then
    echo "Can't tell whose display profile the login screen should follow." >&2
    echo "Name the account explicitly:  bash $0 <user>" >&2
    exit 1
fi
if ! getent passwd "$USER_NAME" >/dev/null; then
    echo "No such user: $USER_NAME" >&2
    exit 1
fi

[[ -f "$SRC/monitor-greeter" ]] || { echo "missing $SRC/monitor-greeter" >&2; exit 1; }
command -v xrandr >/dev/null || { echo "xrandr is not installed (xorg-xrandr)" >&2; exit 1; }

bash -n "$SRC/monitor-greeter"

echo ">> Installing the layout helper to $HELPER"
install -Dm755 -o root -g root "$SRC/monitor-greeter" "$HELPER"

# ---------------------------------------------------------------------------
# Work out what to run after the layout.
#
# Deliberately read out of the nightlight drop-in by name rather than out of
# "whatever currently holds display-setup-script": the thing currently holding
# it, on the second run of this script, is this script's own drop-in, and
# chaining that to itself would fork-bomb the login screen. The cost of naming
# it is that a display-setup-script set anywhere other than 50-nightlight.conf
# — in lightdm.conf itself, say — is overridden rather than chained. Nothing on
# this machine does that; check `grep -r display-setup-script /etc/lightdm` if
# something that used to run at the greeter has stopped.
# ---------------------------------------------------------------------------
CHAIN=""
if [[ -f $NIGHTLIGHT_DROPIN ]]; then
    CHAIN=$(sed -n 's/^[[:space:]]*display-setup-script[[:space:]]*=[[:space:]]*//p' \
                "$NIGHTLIGHT_DROPIN" | tail -n1)
fi
if [[ -n $CHAIN ]]; then
    echo ">> Chaining to the existing display-setup-script: $CHAIN"
else
    echo ">> No existing display-setup-script to chain to"
fi

echo ">> Pointing LightDM at the layout helper for $USER_NAME"
# A drop-in rather than an edit to /etc/lightdm/lightdm.conf: that file belongs
# to the package, and editing it earns a .pacnew to reconcile on every update.
# 60- rather than 50- so this wins the display-setup-script slot even if
# install-nightlight-greeter.sh is re-run afterwards — conf.d is read in name
# order and the last assignment of a key is the one that sticks. Re-run this
# script after that happens anyway, so the chain below points at current paths.
install -dm755 -o root -g root /etc/lightdm/lightdm.conf.d
{
    cat <<EOF
# Managed by $SRC/install-monitor-greeter.sh — re-run that rather than
# editing this, or the next run will overwrite what you wrote here.
#
# LightDM starts X with no display configuration, so the modesetting driver
# lights up every connected output left to right with none of them primary, and
# the greeter draws its login box on whatever ends up at the origin — the
# laptop panel, which on a docked boot is shut and unlit. monitor-greeter
# applies the session's own saved layout instead, before the greeter draws.
[Seat:*]
EOF
    if [[ -n $CHAIN ]]; then
        cat <<EOF
# The tail after -- is the previous owner of this hook. It runs second because a
# modeset wipes the gamma ramp: night light after layout, never before.
display-setup-script=$HELPER $USER_NAME -- $CHAIN
EOF
    else
        echo "display-setup-script=$HELPER $USER_NAME"
    fi
} >"$DROPIN"
chmod 644 "$DROPIN"

# ---------------------------------------------------------------------------
# Ask the helper, in dry-run, what it would do — and use its answer to name the
# monitor for the greeter's own fallback.
# ---------------------------------------------------------------------------
echo ">> Asking the helper what it would arrange on the current hardware"
PLAN=""
if [[ -n ${DISPLAY:-} ]]; then
    PLAN=$(MONITOR_GREETER_DRYRUN=1 "$HELPER" "$USER_NAME" 2>/dev/null || true)
fi

if [[ -n $PLAN ]]; then
    echo "   $PLAN"
else
    echo "   (no X display reachable from here — skipped; this is only a preview)"
fi

# The output carrying --primary, plus every other one it would light up: the
# greeter walks this list and takes the first monitor that exists, so naming a
# monitor that is not plugged in on some other boot costs nothing.
MONITORS=$(printf '%s\n' "$PLAN" | awk '
    { for (i = 1; i <= NF; i++) {
        if ($i == "--output") {
            name = $(i+1)
            if (!(name in seen)) { seen[name] = 1; ord[++n] = name }
        } else if ($i == "--primary") primary = name
    } }
    END {
        out = primary
        for (i = 1; i <= n; i++) {
            if (ord[i] == primary) continue
            out = (out == "" ? ord[i] : out ";" ord[i])
        }
        printf "%s", out
    }')

if [[ -n $MONITORS ]]; then
    echo ">> Telling lightdm-gtk-greeter to prefer: $MONITORS"
    install -dm755 -o root -g root /etc/lightdm/lightdm-gtk-greeter.conf.d
    cat >"$GREETER_DROPIN" <<EOF
# Managed by $SRC/install-monitor-greeter.sh — re-run that rather than
# editing this, or the next run will overwrite what you wrote here.
#
# Fallback, not the fix. When monitor-greeter has done its job there is only one
# monitor left for the greeter to choose from and this changes nothing. It earns
# its place on the boot where xrandr refuses the arrangement: the greeter then
# still puts the login box somewhere you can see it instead of on the shut lid.
# Semicolon-separated, first one that exists wins, so an undocked boot skips
# straight past the external monitor.
[greeter]
active-monitor=$MONITORS
EOF
    chmod 644 "$GREETER_DROPIN"
else
    echo ">> Skipping the greeter fallback: nothing to name without a preview"
    rm -f "$GREETER_DROPIN"
fi

echo
echo "Done. Nothing changes in the running session — LightDM reads this when it"
echo "next starts an X server, so the login screen is fixed from the next reboot."
echo
echo "  drop-in:  $DROPIN"
[[ -f $GREETER_DROPIN ]] && echo "  fallback: $GREETER_DROPIN"
echo "  helper:   $HELPER"
