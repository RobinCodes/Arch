#!/usr/bin/env bash
# One-shot: let noctalia push the wallpaper and colours to its greeter without
# asking for a password every time.
#
# Run:  sudo bash install-greeter-sync.sh
#   or: sudo bash install-greeter-sync.sh --undo
#
# This is half of the fix. The other half is one line in your own noctalia
# settings and needs no root — `artwork refresh` writes it. Read on.
#
#
# WHAT WAS FAILING, AND WHY THE FIRST DIAGNOSIS WAS WRONG
#
# noctalia syncs the current wallpaper and palette to its own login greeter, so
# the screen you log in on matches the one you log in to. The greeter's files
# live in /var/lib/noctalia-greeter, owned by the `greeter` user, so the copy
# runs through a root helper behind a polkit action. Shipped policy:
#
#     <action id="org.noctalia.greeter.apply-appearance">
#       <defaults>
#         <allow_any>auth_admin</allow_any>
#         <allow_inactive>auth_admin</allow_inactive>
#         <allow_active>auth_admin</allow_active>
#
# auth_admin on all three, so the obvious conclusion was that every sync wants
# the admin password, and the obvious fix was a rule granting that action. This
# file wrote that rule, and the password prompts carried on.
#
# They carried on because that action was never the one being asked for. noctalia
# does not call the helper directly — it runs it through whichever privilege
# wrapper it finds, and it looks for `run0` before `pkexec`. run0 does its work
# by asking systemd for a transient unit, so the action that actually reaches
# polkit is org.freedesktop.systemd1.manage-units. From polkitd's own log, at
# exactly the 1800-second wallpaper rotation interval:
#
#     16:35:58  FAILED to authenticate ... org.freedesktop.systemd1.manage-units
#     17:05:58  FAILED to authenticate ... org.freedesktop.systemd1.manage-units
#
# A prompt nobody was looking at, half an hour apart, failing into a notification.
#
# So there are two halves:
#
#   this file    grants org.noctalia.greeter.apply-appearance to you. Pinned to
#                the one action id and the one user.
#
#   artwork      sets [shell.greeter_sync] privilege_command = "pkexec" in your
#                noctalia settings, which sends the call through the action this
#                rule is about instead of through run0. No root, no prompt.
#
# The rule without the setting does nothing, which is what happened the first
# time. The setting without the rule turns a run0 prompt into a pkexec prompt.
#
# Granting manage-units instead would have "worked" too, and would have meant
# handing the account permission to start, stop and restart every unit on the
# system — the whole of root — for the sake of a picture on the login screen.
# That is why it is not what this does.
#
#
# WHAT THIS CHANGES, AND WHAT IT COSTS
#
# One polkit rule: the named user may run that one action without
# authenticating. Not a blanket rule, not the wheel group — the action id and
# the user are both pinned.
#
# The privilege it hands over is "can change the picture and the colours on the
# login screen". The helper behind the action is a fixed path named in the
# policy, so it is not a way to run something else as root. And the user this is
# granted to is already in wheel, which is a far larger grant than this one —
# anyone who could abuse this rule could already run sudo.
#
# The honest cost: the login screen's appearance can now be changed without a
# password by anything running as you. On a single-user laptop that is the same
# trust boundary as the session itself. On a shared machine, do not install this
# — press Cancel on the prompt instead and let the greeter keep its own look,
# or turn the sync off entirely with
# [shell.greeter_sync] auto_sync = false.
set -euo pipefail

RULE=/etc/polkit-1/rules.d/49-noctalia-greeter-sync.rules
ACTION=org.noctalia.greeter.apply-appearance
POLICY="/usr/share/polkit-1/actions/$ACTION.policy"

if [[ $EUID -ne 0 ]]; then
    echo "Run as root:  sudo bash $0" >&2
    exit 1
fi

if [[ ${1:-} == --undo ]]; then
    rm -f "$RULE"
    echo ">> Removed $RULE"
    echo "   The greeter sync will ask for a password again."
    exit 0
fi

USER_NAME=${1:-${SUDO_USER:-}}
if [[ -z $USER_NAME && -n ${PKEXEC_UID:-} ]]; then
    USER_NAME=$(getent passwd "$PKEXEC_UID" | cut -d: -f1)
fi
if [[ -z $USER_NAME || $USER_NAME == root ]]; then
    echo "Can't tell who to grant this to. Name the account:  bash $0 <user>" >&2
    exit 1
fi
getent passwd "$USER_NAME" >/dev/null || { echo "No such user: $USER_NAME" >&2; exit 1; }

[[ -f $POLICY ]] || {
    echo "No $POLICY — is noctalia's greeter installed?" >&2
    exit 1
}

echo ">> Granting $ACTION to $USER_NAME"
install -dm755 /etc/polkit-1/rules.d
cat >"$RULE" <<EOF
// Managed by Scripts/Arch/art/install-greeter-sync.sh — re-run that rather
// than editing this.
//
// noctalia's greeter sync runs by itself whenever the wallpaper changes, and
// the shipped policy asks for the admin password every time. That turns an
// automatic background copy into an unprompted password dialog that times out
// into a failure notification.
//
// This is only half of it: noctalia reaches this action only when
// [shell.greeter_sync] privilege_command is "pkexec". Left to itself it picks
// run0, which asks for org.freedesktop.systemd1.manage-units instead and is
// not covered by this rule on purpose — that action is every unit on the box.
// \`artwork refresh\` writes the setting.
//
// Pinned to one action id and one user. polkit evaluates rules in name order
// and the first Result wins, so the 49- prefix keeps this ahead of the 50-
// defaults without outranking anything an administrator adds below it.
polkit.addRule(function (action, subject) {
    if (action.id === "$ACTION" && subject.user === "$USER_NAME") {
        return polkit.Result.YES;
    }
});
EOF
chmod 644 "$RULE"

# polkitd watches rules.d and reloads on its own, but only if it parses. A
# syntax error there is silent and leaves every rule in the file inert, so the
# reload is asked for explicitly and the result checked.
systemctl reload polkit 2>/dev/null || systemctl restart polkit 2>/dev/null || true
sleep 1
if journalctl -u polkit --since "10 seconds ago" --no-pager 2>/dev/null \
        | grep -qi "error\|failed to"; then
    echo "   ! polkit reported a problem loading the rule:" >&2
    journalctl -u polkit --since "10 seconds ago" --no-pager | tail -5 >&2
    exit 1
fi
echo "   $RULE"

# Did it take? pkcheck without --allow-user-interaction answers without opening
# anything, so this can be asked rather than found out at the next rotation.
# $$ has to expand inside the runuser shell, not out here: out here it is this
# script's PID, which root owns, and the answer would be about root.
if command -v pkcheck >/dev/null \
   && ! runuser -u "$USER_NAME" -- bash -c \
        'pkcheck --action-id "$1" --process $$' _ "$ACTION" >/dev/null 2>&1; then
    echo "   ! polkit still will not grant it to $USER_NAME. Check for another"
    echo "   ! rule in /etc/polkit-1/rules.d that sorts before 49- and returns"
    echo "   ! a Result for this action." >&2
fi

# The other half, which is not root's to write.
STATE_HOME=$(getent passwd "$USER_NAME" | cut -d: -f6)/.local/state
SETTINGS="$STATE_HOME/noctalia/settings.toml"
echo
if grep -q 'privilege_command *= *"pkexec"' "$SETTINGS" 2>/dev/null; then
    echo "The wrapper is already pinned to pkexec. Nothing else to do."
else
    echo "Now the other half, as $USER_NAME — without it noctalia keeps using"
    echo "run0 and this rule never comes up:"
    echo
    echo "    artwork refresh"
fi
echo
echo "Test it:   noctalia msg greeter-sync     (no prompt should appear)"
echo "Undo:  sudo bash $0 --undo"
