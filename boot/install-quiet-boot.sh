#!/usr/bin/env bash
# One-shot: stop the kernel writing over Visor's screen on the way to greetd.
#
# Run:  sudo bash install-quiet-boot.sh
#   or: sudo bash install-quiet-boot.sh --undo
#
# WHAT IS ACTUALLY WRONG
#
# Nothing, exactly — this machine boots fine. It just narrates it. The kernel
# command line, from /etc/kernel/cmdline, is
#
#     root=PARTUUID=… zswap.enabled=0 rw rootfstype=ext4
#
# with no `quiet` and no `loglevel`, so the console log level is the compiled-in
# default and every message at KERN_WARNING or below goes to the screen — 129 of
# them on the boot this was written against. systemd then adds its own [ OK ]
# lines on top, because ShowStatus defaults to yes.
#
# The effect is that Visor draws its background, hands over, and the kernel
# immediately paints text across it until greetd starts a compositor on VT 1.
# That text is the gap between "Visor" and "niri", and the orange line flashing
# past in it was
#
#     bash: vm.laptop_mode is deprecated. Ignoring setting.
#
# which battery/power-profile-apply no longer provokes — that one is fixed at
# the source rather than hidden here, which matters, because the whole point of
# loglevel=3 is that a *real* warning still gets through.
#
# WHAT THIS CHANGES, AND WHAT IT DELIBERATELY DOES NOT
#
# Only /etc/kernel/cmdline, plus the mkinitcpio run that bakes it into the UKI.
# It does not touch:
#
#   Visor's own config (\EFI\visor\boot.conf)   its menu and its timeout are a
#                                               preference, not a bug, and a
#                                               bootloader is the one thing here
#                                               that can make the machine
#                                               unbootable if it is edited on a
#                                               guess. The README says which key
#                                               to change if you want the menu
#                                               gone.
#   mkinitcpio's HOOKS                          adding plymouth for a graphical
#                                               hand-off is a bigger change with
#                                               a real failure mode, and is a
#                                               separate decision.
set -euo pipefail

CMDLINE=/etc/kernel/cmdline
BACKUP_DIR=/etc/kernel
STAMP=$(date +%Y%m%d-%H%M%S)

# THE ONE THAT MATTERS IS `splash`, AND IT WAS MISSING
#
# Three rounds of this went into silencing sources — kernel loglevel, udev,
# systemd status, and finally console=tty3 to move the destination entirely —
# and the screen looked the same every time. It was the wrong question.
#
# plymouth was installed, hooked, running and themed. But from plymouth(8):
#
#     In order for the configured default plymouth theme to be loaded during
#     boot, the option `splash' ... must be provided at the kernel command
#     line. Without this command line option, plymouth will default to
#     showing detailed boot output.
#
# There was no `splash`, so plymouth was in its *details* mode — the mode whose
# entire job is to render the boot log as scrolling text. The text on screen was
# plymouth drawing it. That is why quieting the sources kept not working:
# plymouth reads the log, not the console, so moving the console away from the
# screen changed nothing it was drawing.
#
#   quiet                      kernel chatter. Pairs with splash; plymouth wants
#                              both, and the wiki says so.
#   splash                     load the configured theme instead of the details
#                              view. THE fix.
#   loglevel=3                 err and worse still reach the console; warnings
#                              no longer do.
#   rd.udev.log_level=3        udev inside the initramfs. The `rd.` prefix is
#                              the one that applies there — the bare form below
#                              only covers udev on the real root, which is why
#                              having only it left the early messages coming.
#   udev.log_level=3           udev after the switch-root.
#   vt.global_cursor_default=0 the blinking block cursor.
#   systemd.show_status=false  the [ OK ] column.
FLAGS=(
    quiet
    splash
    loglevel=3
    rd.udev.log_level=3
    udev.log_level=3
    vt.global_cursor_default=0
    systemd.show_status=false
)

# Stripped on every run, by key. console=tty3 was an earlier attempt at this and
# it made things worse, not better: /sys/class/tty/console/active came back as
# `tty3`, so the kernel had moved the console to a VT of its own — and the boot
# log was then being shown there instead of hidden. It has no place next to a
# working plymouth, which covers the screen properly.
REMOVE=(
    console
)

if [[ $EUID -ne 0 ]]; then
    echo "Run as root:  sudo bash $0" >&2
    exit 1
fi

[[ -f $CMDLINE ]] || { echo "no $CMDLINE — is this a UKI setup?" >&2; exit 1; }
command -v mkinitcpio >/dev/null || { echo "mkinitcpio is not installed" >&2; exit 1; }

current=$(<"$CMDLINE")
current=${current//$'\n'/ }

# --- undo -------------------------------------------------------------------
if [[ ${1:-} == --undo ]]; then
    rebuilt=$current
    for flag in "${FLAGS[@]}" "${REMOVE[@]}"; do
        # Strip by key, so loglevel=3 comes out even if it has been hand-edited
        # to loglevel=4 since.
        key=${flag%%=*}
        rebuilt=$(printf '%s\n' "$rebuilt" | sed -E "s/(^| )${key}(=[^ ]*)?( |$)/\1/g")
    done
    rebuilt=$(printf '%s\n' "$rebuilt" | tr -s ' ' | sed -E 's/^ +| +$//g')
    cp -a "$CMDLINE" "$BACKUP_DIR/cmdline.bak-$STAMP"
    printf '%s\n' "$rebuilt" >"$CMDLINE"
    echo ">> Reverted to: $rebuilt"
    echo ">> Rebuilding the UKI"
    mkinitcpio -P
    echo
    echo "Done. The console narrates the next boot again."
    exit 0
fi

# --- apply ------------------------------------------------------------------
added=()
changed=()
removed=()
rebuilt=$current

for key in "${REMOVE[@]}"; do
    if [[ " $rebuilt " == *" $key "* || " $rebuilt " == *" $key="* ]]; then
        was=$(printf '%s\n' "$rebuilt" | grep -oE "(^| )${key}(=[^ ]*)?( |$)" | head -1 | tr -d ' ')
        rebuilt=$(printf '%s\n' "$rebuilt" | sed -E "s/(^| )${key}(=[^ ]*)?( |$)/\1/g")
        rebuilt=$(printf '%s\n' "$rebuilt" | tr -s ' ' | sed -E 's/^ +| +$//g')
        removed+=("$was")
    fi
done

for flag in "${FLAGS[@]}"; do
    key=${flag%%=*}
    if [[ " $rebuilt " == *" $flag "* || $rebuilt == *" $flag" ]]; then
        continue                       # already exactly this
    fi
    if [[ " $rebuilt " == *" $key "* || " $rebuilt " == *" $key="* ]]; then
        # Present with a different value. These five keys belong to this
        # script, so it updates them rather than leaving a stale value behind —
        # that is how show_status=auto would otherwise have survived forever.
        # The change is printed, so it is never silent.
        was=$(printf '%s\n' "$rebuilt" | grep -oE "(^| )${key}(=[^ ]*)?( |$)" | head -1 | tr -d ' ')
        rebuilt=$(printf '%s\n' "$rebuilt" | sed -E "s/(^| )${key}(=[^ ]*)?( |$)/\1${flag}\3/")
        changed+=("$was -> $flag")
        continue
    fi
    rebuilt="$rebuilt $flag"
    added+=("$flag")
done

if [[ ${#added[@]} -eq 0 && ${#changed[@]} -eq 0 && ${#removed[@]} -eq 0 ]]; then
    echo ">> Nothing to do — the command line is already right:"
    echo "   $current"
    exit 0
fi
[[ ${#changed[@]} -gt 0 ]] && printf '>> Updating: %s\n' "${changed[@]}"
[[ ${#removed[@]} -gt 0 ]] && printf '>> Removing: %s\n' "${removed[@]}"

echo ">> Backing up  $BACKUP_DIR/cmdline.bak-$STAMP"
cp -a "$CMDLINE" "$BACKUP_DIR/cmdline.bak-$STAMP"

echo ">> Adding: ${added[*]}"
echo "   before: $current"
echo "   after:  $rebuilt"
printf '%s\n' "$rebuilt" >"$CMDLINE"

echo ">> Rebuilding the UKI (both default and fallback)"
if ! mkinitcpio -P; then
    echo
    echo "mkinitcpio failed — putting the old command line back." >&2
    cp -a "$BACKUP_DIR/cmdline.bak-$STAMP" "$CMDLINE"
    echo "Restored. Nothing was left half-applied; the UKI on the ESP is" >&2
    echo "whatever the last successful build wrote." >&2
    exit 1
fi

echo
echo "Done. Reboot to see it."
echo
echo "Check it took, after rebooting:"
echo "    cat /proc/cmdline          # should show all six flags"
echo "    sudo cat /dev/vcs3         # the boot text, now on tty3"
echo
echo "If text STILL appears on screen after this, it is not coming from Linux —"
echo "everything Linux prints is on tty3 now. That would leave Visor itself, or"
echo "the firmware, both of which are above the kernel and out of reach here."
echo "To see exactly what it was:  sudo cat /dev/vcs1   (tty1, straight after a"
echo "boot, from another terminal) — if that is empty, nothing Linux wrote was"
echo "ever on the visible screen."
echo
echo "If it does NOT, Visor is passing its own cmdline to the UKI and winning."
echo "That would be a  cmdline =  line in an  entry { }  block in"
echo "\\EFI\\visor\\boot.conf on the ESP; add the same flags there instead."
echo
echo "To put it back:  sudo bash $0 --undo"
