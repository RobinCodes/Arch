#!/usr/bin/env bash
# One-shot: let noctalia push the wallpaper and colours to its greeter without
# asking for a password every time.
#
# Run:  sudo bash install-greeter-sync.sh
#   or: sudo bash install-greeter-sync.sh --undo
#
#
# WHAT WAS FAILING
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
# auth_admin on all three, so every sync wants the admin password. The sync runs
# on its own whenever the wallpaper changes, which means a password prompt fires
# at moments nobody asked for anything — at session start, and on every rotation
# if a wallpaper folder is set. From the log:
#
#     22:55:25.659  panel manager: opened "polkit"
#     22:55:50.579  greeter sync failed: Connection timed out
#     22:55:50.728  panel manager: closing "polkit"
#
# A prompt nobody was looking at, timing out 25 seconds later into a failure
# notification. That is the notification, and this is why it appeared.
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
# — press Cancel on the prompt instead and let the greeter keep its own look.
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
// Pinned to one action id and one user on purpose. polkit evaluates rules in
// name order and the first Result wins, so the 49- prefix keeps this ahead of
// the 50- defaults without outranking anything an administrator adds below it.
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
echo
echo "Done. The next wallpaper change syncs to the greeter without a prompt."
echo "Test it now, as $USER_NAME:   noctalia msg greeter-sync"
echo "Undo:  sudo bash $0 --undo"
