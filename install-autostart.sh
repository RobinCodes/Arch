#!/usr/bin/env bash
# Put this repo's tray icons back on the bar, and keep them there.
#
# Run:  bash install-autostart.sh
#
# No root. Everything it touches is under $HOME.
#
#
# WHY THIS EXISTS
#
# The autostart entries were written by hand under XFCE and carried
# `OnlyShowIn=XFCE;`. systemd's xdg-autostart generator turns that into
#
#     ExecCondition=/usr/lib/systemd/systemd-xdg-autostart-condition "XFCE" ""
#
# and $XDG_CURRENT_DESKTOP is `niri` here, so the condition fails and the unit
# is skipped. Every login, in the journal, for every one of them:
#
#     app-battery\x2dtray@autostart.service: Skipped due to 'exec-condition'.
#     Condition check resulted in Battery Tray being skipped.
#
# The units exist, they are wanted by xdg-desktop-autostart.target, and they
# never run. That is the whole reason the icons stopped appearing — nothing to
# do with the tray protocol, which is why restarting noctalia never helped.
#
# The entries are generated here rather than edited in place so the fix is
# reproducible and so the repo, not ~/.config, is where the list of what
# autostarts lives.
#
#
# WHAT IT INSTALLS
#
#   battery-tray      power profiles and the AC-follow; noctalia's own battery
#                     widget has neither
#   resolution-tray   noctalia has no display-mode widget at all
#   nightlight-tray   the schedule, the manual override and the slider
#   nightlight-login  applies the scheduled colour as the session comes up,
#                     instead of waiting up to five minutes for the timer
#
# brightness-tray and nolock-tray are deliberately absent: noctalia's own
# brightness widget covers the first, and `nolock` from the CLI covers the
# second. Both scripts still work and still run under XFCE.
set -euo pipefail

HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
AUTOSTART="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
NIRI_CONF="${XDG_CONFIG_HOME:-$HOME/.config}/niri/config.kdl"
NOCTALIA_SETTINGS="${XDG_STATE_HOME:-$HOME/.local/state}/noctalia/settings.toml"

mkdir -p "$AUTOSTART"

# --- the autostart entries --------------------------------------------------

# No OnlyShowIn and no NotShowIn. The scripts pick their own backend at startup
# — lib/tray.py asks the session bus whether anything owns the
# StatusNotifierWatcher name — so "which desktop is this" is a question they
# already answer better than a .desktop file can. An entry that names a desktop
# is an entry that is wrong the next time the session changes, which is exactly
# how this broke.
write_entry() {  # write_entry <basename> <Name> <Comment> <Exec> <Icon>
    local file="$AUTOSTART/$1.desktop"
    cat >"$file" <<EOF
[Desktop Entry]
# Managed by $HERE/install-autostart.sh — re-run that rather than editing this.
#
# No OnlyShowIn: this ran under XFCE and under niri, and naming either one is
# what stopped it running at all. See that script's header.
Type=Application
Version=1.0
Name=$2
Comment=$3
Exec=$4
Icon=$5
Terminal=false
StartupNotify=false
X-GNOME-Autostart-enabled=true
EOF
    chmod 644 "$file"
    echo "   $file"
}

echo ">> Writing autostart entries"
write_entry battery-tray "Battery Tray" \
    "Battery percentage, charging state and power profiles in the bar" \
    "$HERE/battery/battery-tray.py" battery-symbolic
write_entry resolution-tray "Screen Resolution Tray" \
    "Switch the monitor between its highest mode, Full HD and its lowest" \
    "$HERE/monitor/resolution-tray.py" preferences-desktop-display-symbolic
write_entry nightlight-tray "Night Light Tray" \
    "Night light schedule, manual override and slider" \
    "$HERE/nightlight/nightlight-tray.py" night-light-symbolic
write_entry nightlight-login "Night light at login" \
    "Apply the scheduled night light intensity at session start" \
    "$HERE/nightlight/nightlight-login" night-light-symbolic

# The old hand-written entries are the ones that carried OnlyShowIn. Same names,
# so the writes above have already replaced them — this only proves it.
#
# Anchored to the start of the line, because the entries written above explain
# in a comment why there is no OnlyShowIn and an unanchored match finds that
# instead: a check that cannot pass is worse than no check.
echo ">> Checking none of them still name a desktop"
if grep -lE '^(OnlyShowIn|NotShowIn)=' \
     "$AUTOSTART"/{battery-tray,resolution-tray,nightlight-tray,nightlight-login}.desktop 2>/dev/null; then
    echo "   WARNING: the files above still carry a ShowIn line" >&2
else
    echo "   clean"
fi

# --- noctalia's GTK palette -------------------------------------------------
#
# The tray menus are drawn by noctalia from the data lib/sni.py serves, so they
# are already in its theme. The slider panel is not: it is a GTK window drawn in
# our own process, and GTK has never heard of noctalia.
#
# noctalia ships a GTK template that writes the active palette to
# ~/.config/gtk-3.0/noctalia.css. Enabling it is what lets lib/noctalia.py read
# the colours and paint the panel to match — and it keeps matching, because
# noctalia rewrites that file whenever the scheme changes.
#
# Edited as text rather than through a TOML writer: Python reads TOML in the
# standard library and does not write it, and both third-party writers reformat
# the whole document, which would mean handing noctalia its own settings back
# with every comment gone. Same reasoning as nolock/noctalia-setting.py.
echo ">> Enabling noctalia's GTK template (for the slider panel's colours)"
if [[ ! -f $NOCTALIA_SETTINGS ]]; then
    echo "   noctalia settings not found at $NOCTALIA_SETTINGS — skipping."
    echo "   The panel will render in stock GTK instead of noctalia's colours."
elif grep -q '"gtk3"' "$NOCTALIA_SETTINGS"; then
    echo "   already enabled"
else
    python3 - "$NOCTALIA_SETTINGS" <<'PY'
import re, sys

path = sys.argv[1]
with open(path, encoding="utf-8") as fh:
    text = fh.read()

# Only ever touches builtin_ids inside [theme.templates], and only by appending
# to the list it finds. A missing table or key means noctalia is on its defaults
# and there is nothing here to extend safely, so it says so and changes nothing.
pattern = re.compile(
    r'(\[theme\.templates\][^\[]*?builtin_ids\s*=\s*\[)([^\]]*)(\])', re.S
)
match = pattern.search(text)
if not match:
    print("   no [theme.templates] builtin_ids to extend — skipping.")
    sys.exit(0)

body = match.group(2)
if '"gtk3"' in body:
    sys.exit(0)

stripped = body.strip()
new = f'{body.rstrip()}, "gtk3" ' if stripped else ' "gtk3" '
with open(path, "w", encoding="utf-8") as fh:
    fh.write(text[:match.start(2)] + new + text[match.end(2):])
print('   added "gtk3" to theme.templates.builtin_ids')
PY
    if command -v noctalia >/dev/null 2>&1; then
        noctalia msg config-reload >/dev/null 2>&1 || true
        noctalia msg templates-apply >/dev/null 2>&1 || true
    fi
fi

# --- the panel's floating rule ----------------------------------------------
#
# One `include` line in config.kdl, which is the opt-in switch: the panel script
# refreshes the rule it points at but never adds the include itself, so removing
# it here is a decision that sticks. Same arrangement as zen.kdl and
# brightness-panel.kdl.
echo ">> Adding the panel's window rule to niri's config"
if [[ ! -f $NIRI_CONF ]]; then
    echo "   no config.kdl — skipping (the panel will open tiled)"
elif grep -q "nightlight-panel.kdl" "$NIRI_CONF"; then
    echo "   already included"
else
    printf '\n// Floating rule for the night light slider, written by\n// %s/nightlight/nightlight-panel.py\ninclude "nightlight-panel.kdl"\n' \
        "$HERE" >>"$NIRI_CONF"
    echo "   included"
fi

# --- artwork on PATH --------------------------------------------------------
#
# `artwork` is a command you type, so it has to be findable. A symlink rather
# than a copy: it stays in the repo, and editing it does not need a reinstall.
# ~/.local/bin is already on PATH here.
echo ">> Linking artwork into ~/.local/bin"
mkdir -p "$HOME/.local/bin"
ln -sfn "$HERE/art/artwork" "$HOME/.local/bin/artwork"
echo "   $HOME/.local/bin/artwork -> $HERE/art/artwork"

# --- the tray menus' floating rule ------------------------------------------
#
# Same opt-in switch, for the window lib/menu.py opens when a bar will not draw
# the served DBusMenu itself — which is every tray menu under noctalia. One rule
# covers all three trays: it matches on the window title, because these windows
# are opened from inside the tray processes and share no app-id of their own.
#
# Without it the menus still open and still work, they are just tiled into the
# column instead of floating under the tray.
echo ">> Adding the tray menu's window rule to niri's config"
if [[ ! -f $NIRI_CONF ]]; then
    echo "   no config.kdl — skipping (tray menus will open tiled)"
elif grep -q "tray-menu.kdl" "$NIRI_CONF"; then
    echo "   already included"
else
    printf '\n// Floating rule for the tray menus, written by\n// %s/lib/menu.py\ninclude "tray-menu.kdl"\n' \
        "$HERE" >>"$NIRI_CONF"
    echo "   included"
fi

# --- start what is not running ----------------------------------------------

echo ">> Reloading the user manager"
systemctl --user daemon-reload

echo ">> Starting them now"
for unit in battery-tray resolution-tray nightlight-tray nightlight-login; do
    # The generator escapes the dash in the unit name; systemctl takes the
    # escaped form.
    escaped="app-${unit//-/\\x2d}@autostart.service"
    if systemctl --user start "$escaped" 2>/dev/null; then
        echo "   started $unit"
    else
        echo "   $unit did not start — see: journalctl --user -b -u '$escaped'" >&2
    fi
done

echo
echo "============================================================"
echo "Done. The icons should be on the bar now."
echo
echo "If they are not, the bar itself is the next thing to check —"
echo "\"tray\" has to be in one of bar.default's start/center/end:"
echo
echo "    grep -A2 '^\\[bar.default\\]' $NOCTALIA_SETTINGS"
echo
echo "noctalia registers a StatusNotifierWatcher whether or not its bar has a"
echo "tray widget, so an item can register correctly and still be invisible."
echo "============================================================"
