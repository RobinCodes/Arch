#!/usr/bin/env bash
# One-shot: the graphical half of the boot, from what `artwork` selected.
#
# Run:  sudo bash install-plymouth.sh
#   or: sudo bash install-plymouth.sh --undo
#
# Set the source first, as yourself:  art/artwork <folder>   (or: artwork none)
#
#
# THE SEQUENCE THIS PRODUCES
#
#   Visor            its own background
#   black            kernel load and early initramfs
#   Arch Linux       the UKI's embedded splash, drawn by systemd-stub
#   black            or your artwork, if a folder is set — plymouth, from here
#                    until the greeter draws
#   greetd
#
# The two black stretches are the point. They used to be console text, and the
# reason it is black rather than another picture is that neither stretch can
# show one: the first is before any userspace exists, and the second is
# plymouth, which is where the artwork goes if you want one there.
#
#
# WHY PLYMOUTH QUITS WITH --retain-splash
#
# plymouth-quit.service is ordered after multi-user.target and before nothing
# in particular, so it tears the splash down the moment the target is reached —
# measured here at 22:46:09.438, with greetd starting 15ms later and its
# compositor taking a second or so more to actually draw. That gap is a bare VT,
# and a bare VT is where the last of the text was coming from.
#
# `--retain-splash` leaves the final frame on the framebuffer instead of
# clearing it, so whatever plymouth was showing stays until the greeter's
# compositor takes the display. Nothing is drawn over it and nothing is cleared,
# which is what makes the hand-off look like one continuous screen.
#
#
# WHY THE ARCH LOGO IS BACK
#
# It was replaced with an artwork in the previous version of this file. The UKI
# splash is baked into the .efi at build time and cannot rotate, so it was the
# one fixed picture in a scheme that was otherwise about variety — and it is the
# one moment in the boot that says which distribution this is. It stays.
set -euo pipefail

THEME=artwork
THEME_DIR="/usr/share/plymouth/themes/$THEME"
MKINITCPIO_CONF=/etc/mkinitcpio.conf
PRESET=/etc/mkinitcpio.d/linux.preset
ARCH_SPLASH=/usr/share/systemd/bootctl/splash-arch.bmp
QUIT_DROPIN=/etc/systemd/system/plymouth-quit.service.d/retain-splash.conf

USER_HOME="${SUDO_USER:+/home/$SUDO_USER}"
USER_HOME="${USER_HOME:-$HOME}"
SPLASH_SRC="$USER_HOME/.local/state/artwork/splash"

# Everything in the theme is carried inside the UKI on the ESP, and mkinitcpio
# builds a fallback image with the same hooks, so it lands there twice. 24 PNG8s
# is about 9MB per image — and still 24 boots before one repeats.
THEME_IMAGES=24

STAMP=$(date +%Y%m%d-%H%M%S)

if [[ $EUID -ne 0 ]]; then
    echo "Run as root:  sudo bash $0" >&2
    exit 1
fi

backup() { [[ -f $1 ]] && cp -a "$1" "$1.bak-$STAMP"; }

# --- undo -------------------------------------------------------------------
if [[ ${1:-} == --undo ]]; then
    echo ">> Removing the plymouth hook"
    backup "$MKINITCPIO_CONF"
    sed -i -E 's/^(HOOKS=\(.*)\bplymouth\b[[:space:]]*(.*\))$/\1\2/' "$MKINITCPIO_CONF"
    sed -i -E 's/^(HOOKS=\(.*[^ ]) +\)$/\1)/' "$MKINITCPIO_CONF"
    echo "   $(grep '^HOOKS=' "$MKINITCPIO_CONF")"

    echo ">> Restoring the stock UKI splash"
    backup "$PRESET"
    sed -i -E "s#--splash [^\" ]*#--splash $ARCH_SPLASH#" "$PRESET"

    echo ">> Dropping the --retain-splash override"
    rm -f "$QUIT_DROPIN"
    rmdir --ignore-fail-on-non-empty "$(dirname "$QUIT_DROPIN")" 2>/dev/null || true
    systemctl daemon-reload

    echo ">> Rebuilding"
    mkinitcpio -P
    echo
    echo "Done. $THEME_DIR is left in place; remove it by hand if you want the"
    echo "disk back. plymouth itself is still installed."
    exit 0
fi

command -v mkinitcpio >/dev/null || { echo "mkinitcpio missing" >&2; exit 1; }
[[ -f $PRESET ]] || { echo "no $PRESET" >&2; exit 1; }

echo ">> Installing plymouth"
pacman -S --needed --noconfirm plymouth

# --- the theme --------------------------------------------------------------
shopt -s nullglob
staged=("$SPLASH_SRC"/*.jpg)

rm -rf "$THEME_DIR"
install -dm755 "$THEME_DIR"

count=0
if (( ${#staged[@]} > 0 )); then
    # A random subset, so re-running reshuffles which ones can come up.
    mapfile -t chosen < <(printf '%s\n' "${staged[@]}" | shuf -n "$THEME_IMAGES")
    echo ">> Building the theme from ${#chosen[@]} of ${#staged[@]} staged images"
    # PNG8 rather than PNG24, because plymouth reads PNG and the ESP is small:
    # measured at 380KB against 1.4MB for the same 1366x768 picture, and at
    # splash size the two are indistinguishable.
    for image in "${chosen[@]}"; do
        magick "$image" -strip -colors 256 -dither FloydSteinberg \
            "PNG8:$THEME_DIR/art-$count.png"
        count=$((count + 1))
    done
    echo "   $count images ($(du -sh "$THEME_DIR" | cut -f1))"
else
    echo ">> No staged images — the splash will be black"
    echo "   (set one with:  art/artwork <folder>)"
fi

cat >"$THEME_DIR/$THEME.plymouth" <<EOF
[Plymouth Theme]
Name=Artwork
Description=Black, or one of your pictures at random
ModuleName=script

[script]
ImageDir=$THEME_DIR
ScriptFile=$THEME_DIR/$THEME.script
EOF

# COUNT is written in because the script language cannot list a directory. At
# zero the theme draws nothing at all, which on plymouth's black background is
# exactly the "completely black" case and needs no special theme of its own.
cat >"$THEME_DIR/$THEME.script" <<EOF
# Managed by Scripts/Arch/art/install-plymouth.sh — rewritten in full.

COUNT = $count;

if (COUNT > 0)
  {
    index = Math.Int(Math.Random() * COUNT);
    if (index >= COUNT) index = COUNT - 1;

    art = Image("art-" + index + ".png");
    # Scaled to the panel rather than trusting the rendition to match it: the
    # framebuffer plymouth gets is whatever KMS came up in, which is not always
    # the mode the desktop ends up using.
    art = art.Scale(Window.GetWidth(), Window.GetHeight());

    sprite = Sprite(art);
    sprite.SetX(0);
    sprite.SetY(0);
    sprite.SetZ(-100);
  }

# Required whether or not the theme uses them: plymouth calls each one, and a
# theme that has not defined it logs an error per call into the boot log.
fun refresh_callback() { }
Plymouth.SetRefreshFunction(refresh_callback);

fun message_callback(text) { }
Plymouth.SetMessageFunction(message_callback);

fun quit_callback() { }
Plymouth.SetQuitFunction(quit_callback);
EOF

echo ">> Making it the default theme"
plymouth-set-default-theme "$THEME"

# --- mkinitcpio hook --------------------------------------------------------
echo ">> Adding the plymouth hook"
if grep -qE '^HOOKS=\(.*\bplymouth\b' "$MKINITCPIO_CONF"; then
    echo "   already there"
else
    backup "$MKINITCPIO_CONF"
    # Immediately after `udev`: plymouth needs udev to have brought the KMS
    # device up, and has to be running before anything that can prompt.
    sed -i -E 's/^(HOOKS=\(base udev)( |\))/\1 plymouth\2/' "$MKINITCPIO_CONF"
    grep -qE '^HOOKS=\(.*\bplymouth\b' "$MKINITCPIO_CONF" || {
        echo "   ! could not place the hook; add 'plymouth' after 'udev' in" >&2
        echo "   ! $MKINITCPIO_CONF by hand." >&2
        exit 1
    }
fi
echo "   $(grep '^HOOKS=' "$MKINITCPIO_CONF")"

# --- hold the last frame through the hand-off -------------------------------
echo ">> Making plymouth quit with --retain-splash"
install -dm755 "$(dirname "$QUIT_DROPIN")"
cat >"$QUIT_DROPIN" <<'EOF'
# Managed by Scripts/Arch/art/install-plymouth.sh.
#
# plymouth-quit runs when multi-user.target is reached, which is about a second
# before greetd's compositor has anything on screen. Without --retain-splash it
# clears the framebuffer into that gap and a bare VT shows through.
#
# The empty ExecStart= is not a typo: a drop-in adds to the list, so the
# original has to be cleared before the replacement is given.
[Service]
ExecStart=
ExecStart=-/usr/bin/plymouth quit --retain-splash
EOF
systemctl daemon-reload
echo "   $QUIT_DROPIN"

# --- the UKI splash ---------------------------------------------------------
echo ">> Making sure the UKI splash is the Arch logo"
if [[ -f $ARCH_SPLASH ]]; then
    if ! grep -q -- "--splash $ARCH_SPLASH" "$PRESET"; then
        backup "$PRESET"
        sed -i -E "s#--splash [^\" ]*#--splash $ARCH_SPLASH#" "$PRESET"
    fi
    echo "   $(grep '^default_options' "$PRESET")"
else
    echo "   ! $ARCH_SPLASH is missing; leaving $PRESET alone" >&2
fi

# --- build ------------------------------------------------------------------
echo ">> Rebuilding the UKI (the slow bit)"
if ! mkinitcpio -P; then
    echo >&2
    echo "mkinitcpio failed — rolling back." >&2
    for f in "$MKINITCPIO_CONF" "$PRESET"; do
        [[ -f $f.bak-$STAMP ]] && cp -a "$f.bak-$STAMP" "$f"
    done
    mkinitcpio -P || true
    echo "Rolled back; nothing on the ESP was left half-written." >&2
    exit 1
fi

echo
if (( count > 0 )); then
    echo "Done. $count artworks, one at random per boot."
else
    echo "Done. Black from the kernel through to the greeter."
fi
echo "Reboot to see it.   Undo:  sudo bash $0 --undo"
