#!/usr/bin/env bash
# One-shot: the last two things that still print on the console, both after the
# kernel has gone quiet.
#
# Run:  sudo bash install-quiet-session.sh
#   or: sudo bash install-quiet-session.sh --undo
#
#
# 1. THE TWO [ERROR] LINES — pulsar.service
#
#     pulsar.service: Failed to load environment files: No such file or directory
#     pulsar.service: Failed to spawn 'start' task: No such file or directory
#     Failed to start Pulsar.
#     pulsar.service: Scheduled restart job, restart counter is at 1.
#
# An enabled system unit for a Node server in /srv/pulsar that is not there any
# more, with Restart=always, so systemd tries it, fails, and tries again —
# which is why two ERROR lines go past rather than one.
#
# Only the unit is removed. Nothing under /srv is touched: the request was to
# stop the thing that tries to *start* it, not to delete the project.
#
#
# 2. THE ORANGE DEPRECATION — niri-session
#
#     Calling import-environment without a list of variable names is deprecated.
#
# /usr/bin/niri-session, line 35:
#
#     # Import the login manager environment.
#     systemctl --user import-environment
#
# Bare, no variable list, which systemd now warns about. It is upstream niri's
# script, not anything configured here, and it runs in greetd's session context
# — whose stderr goes to the VT and not to the journal, which is why the line is
# visible on screen and absent from `journalctl -b`.
#
# Editing /usr/bin/niri-session would work until the next `pacman -Syu` puts it
# back. So instead the session entry is shadowed: XDG data dirs are searched in
# order and /usr/local/share comes before /usr/share, so a niri.desktop of the
# same name there is the one the greeter reads. It runs the same script with
# SYSTEMD_LOG_LEVEL=err in front, which is systemd's own switch for "only tell
# me about errors" and silences that warning at the source that prints it.
#
# The variable is set for the session process only. It is not exported to
# terminals, so `systemctl` still warns at you normally when you are the one
# running it — which is the half of that behaviour worth keeping.
set -euo pipefail

PULSAR_UNIT=pulsar.service
PULSAR_PATH=/etc/systemd/system/pulsar.service
SESSION_DIR=/usr/local/share/wayland-sessions
SESSION_FILE="$SESSION_DIR/niri.desktop"
UPSTREAM_SESSION=/usr/share/wayland-sessions/niri.desktop
STAMP=$(date +%Y%m%d-%H%M%S)

if [[ $EUID -ne 0 ]]; then
    echo "Run as root:  sudo bash $0" >&2
    exit 1
fi

# --- undo -------------------------------------------------------------------
if [[ ${1:-} == --undo ]]; then
    echo ">> Removing the session shadow"
    rm -f "$SESSION_FILE"
    rmdir --ignore-fail-on-non-empty "$SESSION_DIR" 2>/dev/null || true
    echo ">> pulsar.service is NOT restored — the unit file was archived to"
    echo "   $PULSAR_PATH.removed-*, move it back and 'systemctl enable pulsar'"
    echo "   if you actually want it."
    exit 0
fi

# --- 1. pulsar --------------------------------------------------------------
echo ">> Stopping and disabling $PULSAR_UNIT"
systemctl disable --now "$PULSAR_UNIT" 2>/dev/null || true
# The restart loop can leave it in a failed state that keeps showing up in
# `systemctl --failed` long after the unit is gone.
systemctl reset-failed "$PULSAR_UNIT" 2>/dev/null || true

if [[ -f $PULSAR_PATH ]]; then
    # Moved rather than deleted. It is three lines of ExecStart and a
    # WorkingDirectory, and it is the only record of how that server was run.
    mv "$PULSAR_PATH" "$PULSAR_PATH.removed-$STAMP"
    echo "   unit archived to $PULSAR_PATH.removed-$STAMP"
else
    echo "   no unit file at $PULSAR_PATH (already gone)"
fi
systemctl daemon-reload

# --- 2. the niri session shadow ---------------------------------------------
if [[ ! -f $UPSTREAM_SESSION ]]; then
    echo ">> No $UPSTREAM_SESSION — skipping the session shadow" >&2
else
    echo ">> Shadowing the niri session entry"
    install -dm755 "$SESSION_DIR"
    # Copied from upstream rather than written from scratch, so the name, icon
    # and any keys niri adds later come across untouched; only Exec changes.
    sed -E 's#^Exec=(.*)$#Exec=env SYSTEMD_LOG_LEVEL=err \1#' \
        "$UPSTREAM_SESSION" >"$SESSION_FILE"
    {
        echo
        echo "# Shadows $UPSTREAM_SESSION."
        echo "# Written by Scripts/Arch/boot/install-quiet-session.sh."
        echo "# Only Exec differs: SYSTEMD_LOG_LEVEL=err silences the"
        echo "# import-environment deprecation warning niri-session provokes."
    } >>"$SESSION_FILE"
    chmod 644 "$SESSION_FILE"
    echo "   $(grep '^Exec=' "$SESSION_FILE")"
fi

echo
echo "Done. Both take effect on the next boot."
echo
echo "If the greeter ends up listing niri TWICE, it is not de-duplicating the"
echo "two session directories by filename the way the XDG spec says to. In that"
echo "case run:  sudo bash $0 --undo"
