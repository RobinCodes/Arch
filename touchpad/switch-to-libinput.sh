#!/usr/bin/env bash
# One-shot: switch the touchpad from synaptics to libinput.
# Does BOTH root steps together so a partial migration is impossible.
# Run:  sudo bash switch-to-libinput.sh   (then log out and back in)
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"

if [[ $EUID -ne 0 ]]; then
    echo "Run with sudo:  sudo bash $0" >&2
    exit 1
fi

echo ">> Installing libinput touchpad config"
install -Dm644 "$SRC/72-touchpad-robin.libinput.conf" \
    /etc/X11/xorg.conf.d/72-touchpad-robin.conf

echo ">> Installing xorg-xinput (needed by the login re-apply script)"
pacman -S --needed --noconfirm xorg-xinput

echo
echo "============================================================"
echo "Config installed and xorg-xinput present."
echo ">>> NOW LOG OUT of XFCE and log back in <<<"
echo "That restarts Xorg and binds the touchpad to libinput."
echo "============================================================"
