# Arch desktop integration

Scripts that embed themselves into the Arch/XFCE desktop — night light,
brightness and battery tray icons, the login screen's display layout, and
touchpad fixes. Unlike the other folders
here, these aren't standalone programs you run once: they're wired into systemd,
XFCE autostart, and a keyboard shortcut, so **the paths below are referenced from
outside this folder**.

Scripts resolve their siblings relative to their own location, so moving this
folder only requires updating the external references in the last table.

---

## `nightlight/`

Night light (colour temperature) control, layered on `redshift`. Intensity runs
0–100%, mapping linearly to 6500K (neutral) → 2500K (very warm). All state lives
in `~/.local/state/nightlight/`; `~/.config/redshift/redshift.conf` holds the
current temperature.

| Script | Role |
|---|---|
| `nightlight` | The CLI and the single owner of state. `nightlight` prints the current value, `nightlight 60` sets it, `nightlight --mode [auto\|manual]` switches scheduling on or off. |
| `nightlight-auto` | Drives intensity from the clock: held at 100% from 19:00–07:00, easing to a 35% minimum at 13:00. Run every 5 minutes by a systemd timer. `nightlight-auto --print` writes the intensity that should be on screen to stdout and changes nothing. |
| `nightlight-gui` | A `yad` slider popup for live adjustment. Bound to `Super+N`. |
| `nightlight-login` | Applies the schedule as the XFCE session comes up, from XDG autostart. Twice, three seconds apart: xfsettingsd's startup display pass reconfigures the CRTC and wipes the gamma ramp. |
| `nightlight-greeter` | The same for the LightDM login screen, before anyone has logged in. Root-owned copy in `/usr/local/libexec`, run by LightDM. |
| `install-nightlight-greeter.sh` | One-shot `sudo` install of that copy and the LightDM drop-in that calls it. |

**Auto vs. manual.** In auto mode, setting a value by hand parks an override
until the next 07:00/19:00 boundary, then the schedule takes back over. In
manual mode the schedule never runs and your value stays put.

**Before you log in.** An X server starts with neutral gamma ramps and nothing
in the greeter sets them, so the login screen used to be a white rectangle at
2am and the first colour change of the day landed a second or so into the
session. `install-nightlight-greeter.sh` fixes that:

```bash
sudo bash nightlight/install-nightlight-greeter.sh
```

That copies `nightlight-greeter` to `/usr/local/libexec` and points LightDM's
`display-setup-script` and `greeter-setup-script` at it, via a drop-in in
`/etc/lightdm/lightdm.conf.d/`. Twice on purpose, for the same reason
`nightlight-login` applies twice — the first call beats the greeter to the
screen, the second puts the ramp back if the greeter's own display pass wiped
it — but with no `sleep` between them, because both hooks block what comes after
them and a slow one is a slow login screen. The session takes over the greeter's
X server rather than starting its own, so the ramp survives the login.

`monitor/` now owns the `display-setup-script` half of that: LightDM allows one
per seat, and a modeset wipes the gamma ramp, so the display layout has to be
applied before the night light rather than after it. `60-monitor.conf` holds the
hook and runs `nightlight-greeter` immediately afterwards — the first call still
beats the greeter to the screen. Re-running this installer doesn't take the hook
back (`50-` sorts before `60-`), but it does rewrite the paths that the other
drop-in copied, so re-run `install-monitor-greeter.sh` after it.

The greeter half runs as root, and it never calls `nightlight`: root has no
business writing to your config, and a `sed -i` from root would leave the file
root-owned. Instead it drops to your uid with `setpriv`, asks `nightlight-auto
--print` what the schedule wants, checks what comes back is a number in range,
and sets the ramp itself. So the curve stays defined in exactly one place, and
the worst a tampered `nightlight-auto` can do to a root process that never
executes a line of `$HOME` is choose the colour of the login screen. `setpriv`
rather than `runuser` because `runuser`'s PAM stack includes `system-login`,
which would register a logind session for someone who has not logged in yet.

Both hooks fire once per X server, so the login screen samples the schedule when
it appears and then holds that value: sit at the greeter from 18:00 to 20:00 and
it will still be at the 18:00 colour. Nothing ticks there, and nothing should —
a timer running against a screen nobody has logged into buys a shade of orange.
The session's own timer takes over the moment you log in.

Re-run the installer after editing `nightlight-greeter` or after moving this
folder — the drop-in names both paths. Moving it without re-running is not
fatal: unable to reach `nightlight-auto`, the greeter falls back to the
temperature already in `redshift.conf`, so the login screen keeps the last
applied value instead of tracking the clock.

Requires `redshift` and `yad`.

## `monitor/`

Which size the screen runs at, and a login screen that agrees with it.

| Script | What it does |
|---|---|
| `resolution` | The CLI and the single owner of resolution changes. `resolution` prints the current mode, `resolution highest\|fullhd\|lowest` sets one, `resolution --list` prints all three plus the active one in a parseable form. |
| `resolution-tray.py` | The tray icon those are picked from. |
| `monitor-greeter` | Applies the session's saved XFCE display layout to the greeter's X server, before the greeter draws. Root-owned copy in `/usr/local/libexec`, run by LightDM. |
| `install-monitor-greeter.sh` | One-shot `sudo` install of that copy, the LightDM drop-in that calls it, and the greeter-side fallback. |

```bash
sudo bash monitor/install-monitor-greeter.sh
```

Nothing changes in the running session — LightDM reads the drop-in when it next
starts an X server, so this lands on the next reboot.

### The symptom

Boot docked with the lid shut, and the external monitor comes up blank: greeter
background, no login box. Opening the lid shows a blank laptop panel too. The
only way in was to physically pull the HDMI cable, log in on the laptop panel,
and plug it back — after which everything worked normally.

### The cause

LightDM starts X with no display configuration at all, so the `modesetting`
driver falls back to *enable every connected output, left to right, none of them
primary*. From `/var/log/Xorg.0.log`, identically on every boot:

```
(II) modeset(0): Output eDP-1  using initial mode 1366x768  +0+0
(II) modeset(0): Output HDMI-1 using initial mode 3840x2160 +1366+0
```

That is a 5206×2160 desktop whose origin — and whose monitor 0, which is where
`lightdm-gtk-greeter` puts the login window when nothing is marked primary — is
the laptop panel. The 4K monitor is parked off to the right at x=1366 and gets
nothing but background.

And the panel it *did* draw on was switched off. Docked boots happen with the
lid shut, and opening it doesn't rescue anything: at greeter time there is no
`xfce4-power-manager` and no session, so nothing re-drives eDP-1 and its
backlight stays down — `/sys/class/backlight/intel_backlight/bl_power` reads
`4`, blanked. So the login box was being drawn, correctly, onto a dark panel.

Unplugging HDMI worked because the hotplug forces a fresh modeset onto the only
output left, which both moves the login box and wakes the panel.

None of this ever reached the session, which is why everything after login was
fine: `xfsettingsd` applies `displays.xml` the moment it starts. The greeter
simply never had that profile. This gives it to it, about two seconds earlier.

### What it applies

The user's own saved XFCE layout, read straight out of

```
~/.config/xfce4/xfconf/xfce-perchannel-xml/displays.xml
```

— the same file `xfsettingsd` reads, so there is one source of truth and no way
for the two to drift. Rearrange things in `xfce4-display-settings` and the login
screen follows on the next boot, with nothing here to update. It also means
logging in is no longer a mode change: same outputs, same resolution, same
scale, so no flicker and no resize, and the 4K panel is scaled to a readable
size at the greeter instead of rendering the password box at native 3840×2160.

The file belongs to the user and this runs as root before anyone has
authenticated, so it is read under `setpriv` as that user, and every field that
comes back is re-checked against a regex before it reaches `xrandr` — the same
reasoning as `nightlight-greeter`, which drops privileges for the same kind of
question. The worst a tampered `displays.xml` can do is pick a resolution for
the login screen.

Everything goes on in a single `xrandr` invocation, so the screen never passes
through a state with no output enabled:

```
xrandr --output eDP-1 --off \
       --output HDMI-1 --mode 3840x2160 --rate 60 \
                       --scale 0.606048583984375x0.606048583984375 \
                       --pos 0x0 --primary
```

### When the profile doesn't fit the hardware

The connected outputs are the authority, not the file. If the profile names an
output that isn't plugged in — undocked, or a different monitor — the whole
profile is discarded rather than half-applied, and the fallback runs instead:
**the first external output if there is one, the built-in panel if there isn't**,
at its preferred mode, primary, alone. That fallback is the bug fix on its own;
the profile is what additionally makes the greeter and the session identical.

Undocked boots therefore light up the laptop panel, and a layout that lights
nothing up is rejected before it can be applied. Anything plugged in that the
profile has never heard of gets switched off rather than left wherever X guessed,
so a stray third output can't recreate the same bug on a different screen.

If `xrandr` refuses the arrangement outright, `--rate` and `--scale` are dropped
and the same arrangement is retried — the login screen on the right monitor at
the wrong refresh rate is still fixed. If *that* fails, X's auto-configuration is
left exactly as it was: it's the state this exists to improve on, but it is a
working screen somewhere, and half an arrangement is worse than none.

### Sharing `display-setup-script` with nightlight

LightDM allows exactly one `display-setup-script` per seat, and `nightlight/`
already wanted it. So `60-monitor.conf` takes the hook and runs the previous
owner afterwards, as everything after `--`:

```
display-setup-script=/usr/local/libexec/monitor-greeter robin -- /usr/local/libexec/nightlight-greeter robin …/nightlight-auto
```

The order matters in one direction only: **a modeset wipes the gamma ramp**, so
the layout has to run before the night light, never after. This is the same
reason `nightlight-login` applies twice around `xfsettingsd`'s startup display
pass. `50-nightlight.conf` keeps `greeter-setup-script`, so the night light
still lands twice, and the `60-` prefix means this keeps the hook even if
`install-nightlight-greeter.sh` is re-run.

Re-run `install-monitor-greeter.sh` after that happens anyway — it copies the
chain out of the nightlight drop-in at install time, so a nightlight reinstall
that changes those paths leaves the chain here pointing at the old ones.

### The greeter-side fallback

`install-monitor-greeter.sh` also writes
`/etc/lightdm/lightdm-gtk-greeter.conf.d/60-monitor.conf`:

```ini
[greeter]
active-monitor=HDMI-1;eDP-1
```

This is a fallback, not the fix. When `monitor-greeter` has done its job there is
only one monitor left to choose from and this changes nothing. It earns its place
on the boot where `xrandr` refuses the arrangement: the greeter then still puts
the login box somewhere visible rather than on the shut lid. The list is
semicolon-separated and the first monitor that exists wins, so an undocked boot
skips straight past the external one.

The installer derives that list by asking the helper, in dry run, what it would
arrange on the current hardware — the same code path that runs at boot, so the
two can't disagree:

```bash
MONITOR_GREETER_DRYRUN=1 /usr/local/libexec/monitor-greeter robin
```

That prints the `xrandr` command instead of running it, and is the way to check
what the login screen will do while you're still looking at a working desktop.

Requires `xorg-xrandr`.

### The resolution tray

`resolution-tray.py` puts a display icon in the top panel's systray. Clicking it
offers three presets for whichever output is currently primary:

| Preset | On the LG | How it's chosen |
|---|---|---|
| Highest | 3840x2160 @ 60 Hz | most pixels, then fastest rate |
| Full HD | 1920x1080 @ 60 Hz | nearest mode to 1920x1080 |
| Lowest | 640x480 @ 60 Hz | fewest pixels, then fastest rate |

Nothing is hard-coded to this monitor. The presets are resolved from `xrandr`'s
mode list for the primary output on each read, so they still mean something on a
different screen, and Full HD degrades to the closest available mode rather than
failing on a monitor with no exact 1920x1080. The LG's own ceiling is 3840x2160
@ 60: its EDID caps the pixel clock at 600 MHz, hsync at 135 kHz and vsync at
61 Hz, and that mode sits on all three limits at once, so there is no higher
resolution *and* no faster refresh at any lower one.

**Three things move together on every switch**, which is why this is a script
and not three `xrandr` aliases:

1. **The XFCE profile, written first.** xfsettingsd re-applies its saved layout
   on RandR events, so an `xrandr` call it doesn't know about is a change with a
   timer on it. Writing `displays.xml` before calling xrandr means the daemon's
   copy already agrees before it can react, and the mode survives a logout
   instead of lasting until the next hotplug. `monitor-greeter` reads that same
   file, so the login screen follows on the next boot.
2. **The `--scale` transform, always reset to 1.** This display spent a while
   running a 2328x1310 desktop bilinear-upscaled to 4K; switching modes without
   clearing the transform composes the two and lands somewhere neither asked
   for.
3. **A UI tier.** Panel heights, icon sizes and the cursor are pixel counts and
   don't follow resolution — left alone, 640x480 leaves a panel eating a sixth
   of the screen and 4K leaves everything half-size. Font DPI moves with them
   and is the one doing the visible work: it holds text at roughly the same
   *physical* size on the glass across all three presets rather than the same
   pixel size.

| Band | DPI | Bar | Bar icons | Dock | Cursor |
|---|---|---|---|---|---|
| height >= 1800 | 144 | 43 | 26 | 79 | 26 |
| height >= 1080 | 96 | 26 | 16 | 48 | 16 |
| below | 72 | 20 | 12 | 36 | 12 |

`TIERS_ENABLED=0 resolution fullhd` changes resolution and nothing else.

**4K is not free on this machine.** Moving from the old upscaled desktop to a
native 3840x2160 framebuffer took `shared` memory from ~247 MB to ~617 MB and
`available` from ~1.17 GB to ~660 MB — worth reading next to the pre-launch
memory rule in `oom/` below, because it comes straight off that cushion. Full HD
hands most of it back, which is a real use for the middle preset beyond taste.

Requires PyGObject (`python-gobject`); `xrandr` and `xfconf-query` are already
part of the desktop.

---

## `brightness/`

`brightness-tray.py` — a GTK tray icon for the XFCE panel, mirroring the
pulseaudio plugin's UX: click for a slider popup, scroll over the icon to step
backlight brightness. The popup also carries a night light slider and an Auto
switch, both of which delegate to `../nightlight/nightlight`.

Requires `brightnessctl` and PyGObject (`python-gobject`).

## `battery/`

| File | Role |
|---|---|
| `battery-tray.py` | The tray icon, and the menu the profiles are switched from. |
| `power-profile` | The CLI and the single owner of profile state. `power-profile` prints the current profile, `power-profile performance` sets it, `power-profile --auto [on\|off]` gets or sets the AC follow, `power-profile --sync` applies whatever the AC state currently calls for. Runs as you; hands the sysfs writes to the helper below. |
| `power-profile-apply` | The privileged half — every write that needs root. Installed to `/usr/local/libexec/`, not run from here. |
| `install-power-profile.sh` | One-shot `sudo` install of the helper and its sudoers rule. |

`battery-tray.py` draws the charge percentage as text in the panel. Charging is
shown by a lightning bolt in the icon's top-right corner and by colour: green
charging, amber at or below 20%, red at or below 10%, panel foreground
otherwise. A profile other than default adds a bar along the bottom edge: teal
for the saving ones — half the icon's width for saver, near-full for extreme —
and violet across the full width for performance. The two families differ by
hue rather than by length alone because a longer teal bar means *slower*, so a
longer bar can't also mean faster. Hovering gives the state, time to full or
empty, the battery's health against its design capacity, and the profile if one
is on — marked *automatic on AC* when the charger is what put it there, so a
machine sitting in performance nobody chose explains itself. Either mouse button
opens the menu.

The battery side comes entirely from `/sys/class/power_supply`, so it needs
nothing beyond PyGObject. The battery is found by scanning for `type=Battery`,
skipping `scope=Device` entries — the touchpad reports its own battery there.
Mains is the same scan for `type=Mains`, which is what makes the AC follow
below possible without a second source of truth: the touchpad and the USB-C
port publish an `online` too, and only the type tells them apart.

### Power profiles

| | Performance | Battery Saver | Extreme Battery Saver |
|---|---|---|---|
| CPU governor | `performance` | `powersave` | `powersave` |
| CPU energy preference | `performance` | `balance_power` | `power` |
| Turbo | on | off | off |
| CPU frequency ceiling | — | — | 50% of max turbo |
| GPU frequency ceiling | — | — | RP1, the efficient clock |
| PCIe ASPM | `performance` | `powersave` | `powersupersave` |
| Disk writeback | stock (5s) | batched (1.5s) | batched (60s) |
| Audio codec idle | never | 1s | 1s |
| NMI watchdog | on | on | off |

**Plugging in switches to performance; unplugging puts back what was on
before.** This is the *Performance on AC* item in the tray menu, on unless you
turn it off (`power-profile --auto off`, or unchecking it). Nothing else on this
machine will fight it: there's no TLP, no power-profiles-daemon, no tuned, and
no local power udev rules.

The profile it displaced is written to `~/.local/state/battery/ac-restore`, and
that file existing is exactly the claim *AC is the reason this machine is in
performance*. It's written when the charger goes in, consumed when it comes
out, and the rest follows from keeping it honest:

| | |
|---|---|
| Plug in | Save the current profile, switch to performance. |
| Unplug | Put the saved profile back and forget it. |
| Pick a profile while plugged in | Your pick *replaces* the saved one, so it's still there when you unplug. |
| Pick a profile on battery | Nothing saved, nothing to undo — the pick is just a pick. |
| Turn the follow off | Whatever is on stays on, and the saved profile is dropped: nothing is pending. |
| Turn it back on while plugged in | Engages straight away rather than waiting for a plug event that could be days off. |

The third row is the one that took a rewrite to get right. Deleting the saved
profile on a hand-picked switch looks equivalent — the same profile is on screen
either way — but while plugged in, *no saved profile* means *not engaged*, and
the next `--sync` reads that as licence to force performance back over the
choice just made. Overwriting keeps `--sync` idempotent, which is what lets it
be called on every transition and again at login without tracking what it
already did.

**Picking performance by hand is still a pin.** Nothing drops it when you
unplug except this follow, and only when it was the one that turned it on.

**The transition is spotted by the tray**, off the AC line it already reads
every five seconds for the charging colour, and handed to `power-profile
--sync`, which owns the decision. So the follow needs the tray running; udev or
UPower would push instead of poll, at the cost of a dependency the tray doesn't
otherwise have and a root-side path that couldn't update this account's state
files. At login the tray re-asserts the saved profile and *then* syncs, which
is what settles a machine unplugged while it was powered off.

Performance leaves the clock *floor* (`min_perf_pct`, 20 here) alone. Pinning it
to 100 keeps all four cores at max even while idle, which on a part this small
just heats it into thermal throttling and gives back the speed it was supposed
to buy — the governor already jumps to max the moment there's work. The knob is
one line in `apply_performance()` if you want it anyway.

**Default is a snapshot, not a preset — and not a compromise.** The first switch
away from it records every knob's current value to
`/var/lib/battery-power-profile/baseline`, and default writes those back, so it
returns the machine to what it was doing before rather than to values this repo
picked. On this laptop that snapshot is `powersave` governor with EPP
`performance`, turbo on, no caps and the GPU at 750 MHz — already full speed,
which is why a hardcoded "balanced" preset would have been a downgrade.

**With no snapshot, `default` writes nothing at all.** It used to fall back to a
hardcoded set of stock values, which was a live bug: the guess for EPP
(`balance_performance`) isn't what this machine boots at (`performance`), so
running `default` on an untouched machine quietly downgraded it. The invariant
that makes the no-op safe is that the baseline is written on every departure
from default and removed only on the way back, so "no baseline" means "already
at default". If you ever delete the baseline by hand while a profile is active,
enter a profile and leave it again to rebuild it.

So the useful distinction: **Default** lets the CPU idle down between bursts;
**Performance** additionally pins the governor and stops the audio codec and
PCIe links from sleeping. Default is the right everyday setting on battery;
Performance is for when you want latency gone and don't care what it costs —
which, plugged into a wall, is the whole time, and is why the follow above hands
it over automatically.

**Screen brightness is deliberately untouched**, even though it's the biggest
draw on the machine. The backlight belongs to the brightness tray and to
whoever is looking at the screen; dim it yourself if you want the rest of the
win. Wi-Fi power save is out too — `iw` isn't installed, and the `nmcli` route
writes into the saved connection profile, which is not state this script should
be mutating. USB autosuspend is out for the usual reason: it makes peripherals
drop out.

Nothing here is a hard requirement — a knob the kernel doesn't expose is skipped
rather than failing the profile, so the same script survives a chipset change.

**Setup.** The sysfs writes need root, so run once:

```bash
sudo bash battery/install-power-profile.sh
# or, with no TTY (the polkit agent prompts in a window):
pkexec bash battery/install-power-profile.sh
```

That copies `power-profile-apply` to `/usr/local/libexec/battery-power-profile`
and installs a sudoers rule for it. The copy is the point: a NOPASSWD rule
naming a script inside the home directory is a root shell for anyone who can
edit that script. **Re-run the installer after editing `power-profile-apply`** —
the installed copy is what actually runs. Without the rule everything still
works, it just asks for a password via polkit on every switch.

sysfs doesn't survive a reboot but `~/.local/state/battery/` does — `profile`,
`auto` and `ac-restore` — so the tray re-asserts the saved profile at login and
then settles it against the AC line. Delete all three and the machine reads as
untouched, at default with the follow on.

**On icon size.** The panel hands the tray a *square* (22px at the current panel
size) and `xfce4-panel`'s systray squashes anything wider, so the bolt can't sit
beside the digits — while charging, the digits give up the top 40% of the icon
to it and render smaller. Clearing the systray's **square icons** option lets a
wide icon through, which would fit both at full height, but it changes how every
other tray icon is laid out too, so it's left alone.

## `nolock/`

Skip the password prompt when the laptop wakes — a tray toggle and `Super+U`,
for when you're somewhere you'd rather just open the lid.

| File | Role |
|---|---|
| `nolock` | The CLI and the single owner of the setting. `nolock` prints `on` or `off`, `nolock on\|off\|toggle` changes it. Bound to `Super+U` as `nolock toggle`. |
| `nolock-tray.py` | The panel icon: **open padlock** = nolock on (wake straight into the session), **closed padlock** = the XFCE default. Left-click toggles; right-click has the same toggle as a checkbox plus the two settings dialogs it fronts. |

**Why five settings and not one.** XFCE has five separate switches that can
each put a lock screen in front of the session, and every one of them defaults
to locking, so flipping any one alone still leaves you typing a password:

| Channel | Property | Locks when |
|---|---|---|
| `xfce4-power-manager` | `lock-screen-suspend-hibernate` | just before it starts a suspend |
| `xfce4-screensaver` | `/lock/sleep-activation` | logind announces sleep |
| `xfce4-screensaver` | `/lock/saver-activation/enabled` | the idle screensaver kicks in — the one that actually catches you on an idle suspend, since the saver activates on the way down |
| `xfce4-power-manager` | `lid-action-on-ac` | the lid closes on mains, if the action is `3` |
| `xfce4-power-manager` | `lid-action-on-battery` | the lid closes on battery, if the action is `3` |

`/lock/enabled`, the master lock switch, is deliberately left alone, so
`Ctrl+Alt+L` still locks on demand even while nolock is on.

### The lid pair, and why the first three weren't enough

For a while this locked on every lid close while reporting itself on, which
looked like the settings not sticking and wasn't. The first three keys are all
about locking *around a sleep*, and **closing the lid on this machine never
sleeps**: `journalctl -kb | grep 'PM: suspend'` came back empty across three
days of uptime and a dozen lid closes.

`xfce4-power-manager` holds a logind `handle-lid-switch` **block** inhibitor —
visible in `systemd-inhibit --list` — so it, not logind, decides what a lid
close means. Its two lid keys were unset, and unset is not "do the sensible
thing": the compiled-in default is `3`, and 3 is *lock screen*. So the lid was
locking directly, with no suspend, no `PrepareForSleep` and no screensaver
activation anywhere in the sequence for the other three keys to veto. They were
all working exactly as documented, on an event that wasn't happening.

The action is a seven-member enum, and it's worth writing down because
`xfconf-query` will take any integer and XFCE gives no hint which is which:

| | 0 | 1 | 2 | 3 | 4 | 5 | 6 |
|---|---|---|---|---|---|---|---|
| | DPMS | suspend | hibernate | **lock screen** | nothing | hybrid sleep | shutdown |

Read out of the installed binary's `GEnum` table rather than trusted to
memory, along with the `g_param_spec_uint` default of 3, since both keys being
absent is precisely the case where the default is the whole story.

**On sets the pair to 0, DPMS**: the display goes off, the session keeps
running, and opening the lid comes straight back to it. That is what the lid
already did, minus the lock. Off restores 3.

**A key that already isn't locking is left alone.** `on` writes only to keys
that would lock, so a lid action deliberately set to suspend (1) survives it
rather than being flattened to DPMS — nolock's job there is done by the three
sleep keys, and it has no business overriding the choice.

**The setting *is* the state.** There's no state file saying "nolock is on" —
the five xfconf values are read back directly, which is why it survives a
reboot and can't drift from what XFCE is doing, and why changing any of them in
the settings dialogs shows up in the tray icon immediately. The one file it
does keep, `~/.local/state/nolock/saved`, is a snapshot of the values from
before nolock was switched on, so `off` restores what was there rather than
assuming the defaults.

**One rule covers both kinds of key**: a key locks when its effective value
equals its locking value, where "effective" means the compiled-in default while
it's unset. That reads a boolean defaulting to `true` and a seven-member lid
enum with the same line, and it's why the locking value doubles as what `off`
writes back — for all five it's also the default, so restoring lands on the
value the key would have had anyway.

**The snapshot is taken per key, not per file.** It used to skip the whole
thing if `saved` already existed, which was right when the table never changed
and wrong the moment the lid pair was added: with nolock already on, the file
was there, the two new keys would have been switched to DPMS with nothing
recording that they'd started at 3, and `off` would have quietly left the lid
not locking. Keys already in the file are still never re-recorded — that would
overwrite the originals with the values nolock itself just wrote.

**Restoring writes the value instead of removing the key.** Deleting the
property would be the tidier way back to a shipped default, but
`xfce4-power-manager` binds its keys both ways: remove one and the daemon
writes its in-memory copy — still the nolock value — straight back, silently,
in the direction that leaves a laptop no longer locking. Every write is read
back and mismatches are fatal.

Requires PyGObject (`python-gobject`) and the `Xfconf` typelib (`xfconf`).

## `keyboard/`

The keyboard dying session-wide — every key doing nothing while the mouse still
works, the lid close no longer blanking the screen, and any lock attempt ending
in xfce4-session's *"Failed to lock the screen"*.

| File | Role |
|---|---|
| `unstick-keyboard` | Recovery. Releases the stuck grab and restarts the screensaver. Installed to `/usr/local/bin/`. |
| `unstick-keyboard.desktop` | Launcher for it, because the recovery has to be reachable without a keyboard. Installed to `/usr/share/applications/`. |
| `10-inhibit-delay.conf` | The actual fix. Installed to `/etc/systemd/logind.conf.d/`. |
| `install-keyboard-fix.sh` | One-shot `sudo` install of all three. |

> **Installed 2026-08-17**, having sat uninstalled for some time before that —
> this section described the machine it was written for, not the one it was on.
> All three files are in place and `systemd-analyze cat-config systemd/logind.conf`
> shows the drop-in overriding the stock 5s.
>
> `InhibitDelayMaxSec=30` **takes effect at the next reboot**: the installer
> reloads systemd but deliberately doesn't restart logind, which would drop the
> seat and could take the X session with it. Until then logind still reports
> `InhibitDelayMaxUSec=5s`. Nothing is worse in the meantime, and the lid can't
> trigger the wedge anyway now that it's on DPMS rather than a suspend.

### What happens

`xfce4-screensaver` takes a logind **delay inhibitor** on every sleep so it can
get the lock screen up before the machine goes down, and locking means
`XGrabKeyboard` + `XGrabPointer` on the root window. logind waits
`InhibitDelayMaxSec` — **5 seconds** by default — and then suspends regardless,
at which point systemd freezes all of `user.slice`, screensaver included,
partway through the grab.

What resumes is a screensaver still holding the keyboard grab, with no lock
window on screen to type into. Every keystroke goes to that grab and is
discarded. The pointer grab is usually not taken, or is dropped, which is why
the mouse survives and the keyboard doesn't.

Five seconds is generous on most machines and very tight on this one: 3.4 GB of
RAM, `vm.swappiness = 150`, and a desktop that has normally been paged out to
the swapfile by the time the lid closes. The screensaver has to be read back
from disk before it can answer at all.

It is in the journal as:

```
systemd-logind: Delay lock is active (UID 1000/robin, PID 1051/xfce4-screensav)
                but inhibitor timeout is reached.
```

Every occurrence of that line so far has been followed by a reboot, because
there was no other way out.

### The fix, and why there is also a recovery

`InhibitDelayMaxSec=30` is a ceiling rather than a cost — the inhibitor is
released the moment the lock is up, so a suspend that took 0.3s still takes
0.3s. It only spends the extra in the case that used to wedge.

That makes the failure rare, not impossible; deep enough in the swapfile, 30s
can be missed too. So `unstick-keyboard` exists for the times it still happens,
and it is a **launcher rather than a keybinding on purpose** — a shortcut is
useless when the thing that's broken is the keyboard. Add it to the panel via
*right-click the panel > Panel > Add New Items > Launcher*, or find it under
*Applications > System > Unstick Keyboard*.

X11 drops every grab a client holds as soon as its connection closes, so the
recovery is really just "kill the screensaver and start a clean one". Around
that it also `SIGCONT`s anything left suspended: `oom-defender` freezes with
`SIGSTOP` and only resumes what it remembers freezing, so if it is killed
mid-episode its victims stay stopped indefinitely, and a stopped process
holding a grab presents identically. A shell job suspended with `Ctrl+Z` gets
resumed along with them — the intended trade for a machine that is already
unusable.

`unstick-keyboard --dry-run` prints what it would do and touches nothing.

### If it happens before this is installed

`xfce4-screensaver` is 17 characters and the kernel keeps 15, so it only ever
appears as `xfce4-screensav` — `pkill -x xfce4-screensaver` silently matches
nothing. From a TTY (`Ctrl+Alt+F2` still switches under an X grab) or over ssh:

```sh
pkill -f xfce4-screensav && DISPLAY=:0 setsid xfce4-screensaver &
```

## `touchpad/`

| File | Role |
|---|---|
| `apply-touchpad.sh` | Re-asserts scroll direction and tap mapping, which `xfsettingsd` resets at every login. Auto-detects synaptics vs. libinput, so it survives a driver switch. Runs at login, applies twice (once immediately, once after 2s) so its values win the race with `xfsettingsd`. |
| `switch-to-libinput.sh` | One-shot `sudo` migration from synaptics to libinput. Installs the libinput config and `xorg-xinput` together, so a partial migration is impossible. Requires a logout to take effect. |
| `72-touchpad-robin.conf` | Xorg config, synaptics variant. |
| `72-touchpad-robin.libinput.conf` | Xorg config, libinput variant. Installed by `switch-to-libinput.sh` to `/etc/X11/xorg.conf.d/72-touchpad-robin.conf`. |

The device is currently on **libinput**. The header comment in the libinput
config refers to an "edge-motion systemd service" — no such service is
installed; treat that line as stale.

### Smooth scrolling, and the half of it that isn't here

The touchpad half is the config above: libinput, two-finger, pixel deltas. The
other half is `~/.xprofile`, which is outside this folder and exports one
variable:

```sh
export MOZ_USE_XINPUT2=1
```

On X11 those pixel deltas only reach a client that asked for XInput2 events,
and Firefox doesn't ask unless that is set — it falls back to the core
protocol, where the only way to say "scrolled" is a button 4/5 click, one per
notch. Which is why scrolling felt stepped in the browser and smooth
everywhere else: GTK takes XI2 unconditionally, so every other app on the
machine was already getting what the touchpad was sending.

It also switches on the rest of what APZ does with a real touchpad — kinetic
flicks that coast after your fingers lift, two-finger pinch zoom, swipe to
navigate. Those prefs (`apz.gtk.kinetic_scroll.enabled`,
`apz.gtk.pangesture.enabled`) were on the whole time with nothing to feed them.

`~/.xprofile` is sourced by `/etc/lightdm/Xsession` before `xfce4-session`
starts, so it needs a fresh login, not just a Firefox restart.

## `codecs/`

| File | Role |
|---|---|
| `install-codecs.sh` | One-shot `sudo` install of the media packages a stock Arch + XFCE leaves out. |

**MOV was never the problem.** `qtdemux` ships in `gst-plugins-good` and
`gst-libav` decodes what's inside, so H.264, HEVC, ProRes and AC-3 in a `.mov`
all decoded before this existed — checked by muxing one of each with `ffmpeg`
and running it through `gst-launch-1.0 ! decodebin ! fakesink`, which is worth
doing before installing anything, because "install codecs" is the reflex and
on this machine it would have fixed nothing.

What was actually missing:

| Package | Gap |
|---|---|
| `gst-plugin-va` | **Hardware decode.** The `iHD` driver and the kernel side were already present with nothing exposing them to GStreamer — not one hardware decoder registered, every frame on the CPU. On an N-series part this is the difference between a 4K phone `.mov` playing and a slideshow that empties the battery. |
| `gst-plugins-bad` | Only `gst-plugins-bad-`***libs*** was installed, which is why `h264parse` and `h265parse` existed while the decoders shipping alongside them didn't. |
| `gst-plugins-ugly` | The older and more awkward formats a download can still be in. |
| `ffmpegthumbnailer` | Video thumbnails in Thunar. `tumbler` was installed with no video thumbnailer to call, so every `.mov` in a folder drew a generic icon. |
| `libva-utils` | `vainfo`, so "is hardware decode actually on" has an answer. Diagnostics only. |

Nothing here replaces a working codec — every package adds elements alongside
what's registered, and `decodebin` keeps the software path as the fallback it
always was.

**Installed 2026-08-17.** `decodebin` now autoplugs `vah264dec` and `vah265dec`
for H.264 and HEVC in a `.mov`, on rank: the VA elements sit at primary+1 (257)
against `avdec_*` at primary (256).

One thing worth knowing before reading a fallback as a fault: **the hardware
path is per profile, not per codec.** A test clip encoded High 4:4:4 — which is
what `ffmpeg -c:v libx264` gives you by default with no `-pix_fmt` — decodes in
software, because Intel VA-API only does 4:2:0, and `vainfo` lists
`VAProfileH264High` without ever saying so. That is `decodebin` correctly
declining an unsupported profile, and it briefly looked like the install hadn't
worked. Re-checked with `-profile:v high -pix_fmt yuv420p`, which is what
cameras and phones actually produce, and it takes the hardware path.

## `oom/`

Memory. Two halves that only make sense together: as much swap as the machine
can usefully hold, and a watchdog that stops you thrashing your way through it.

| File | Role |
|---|---|
| `oom-defender` | The daemon. Watches memory and memory PSI, freezes the biggest process you *aren't* using, kills something if freezing didn't help, and tells you either way. Installed to `/usr/local/bin/`, not run from here. |
| `oom-defender.service` | The unit. Installed to `/etc/systemd/system/`. |
| `oom-defender.conf` | Thresholds and the protected-process list. Installed to `/etc/oom-defender.conf`, and **not** overwritten by a re-run of the installer. |
| `install-oom-defender.sh` | One-shot `sudo` install of all three, plus the swap and `vm` changes below. |

### The swap layout

| Tier | Size | Priority | What it's for |
|---|---|---|---|
| `/dev/zram0` | = RAM | 100 | Compressed pages in RAM. zstd averages about 3:1 on anonymous memory, so it costs roughly a third of its size and hands back the rest. |
| `/swapfile` | 16 GB | 10 | The slow tier underneath, on the NVMe. What stops an allocation wall. |

`zram-size = ram` looks reckless on a 3.4 GB machine and isn't, for the
compression reason above — the device holds compressed pages, so a full 3.4 GB
zram occupies a bit over a gigabyte of real memory.

The `vm` settings in `/etc/sysctl.d/99-oom-defender.conf` are chosen against
that layout: `swappiness = 150` (above 100 the kernel prefers evicting
anonymous pages to dropping the page cache, which is the right trade when the
target is compressed RAM), `page-cluster = 0` (zram reads are a single page, so
readahead just decompresses neighbours for nothing), a higher
`watermark_scale_factor` so reclaim starts before your allocation stalls on it,
and 64 MB held back in `min_free_kbytes`.

### The defender

**Two tiers, cheap one first.** The hog gets `SIGSTOP` — it stops executing, so
it stops allocating, and the kernel reclaims without racing a process that is
still growing. If memory recovers it gets `SIGCONT` and the episode cost a
stutter. If it doesn't recover within `freeze_grace`, or memory falls off a
cliff outright, something gets `SIGKILL`ed. Both tiers raise a desktop
notification; a process that silently freezes or vanishes is worse than one you
were told about.

**The kill victim is re-picked, not inherited.** Over the twenty seconds a
freeze has to work in, the process holding the machine down can change — the
freeze may well have hit the wrong one.

**The biggest process is the wrong answer.** Watching a video with twenty idle
tabs behind it, the video is probably the largest thing on the machine, and
freezing it to save tabs nobody is looking at gets the trade backwards.
Candidates are ordered by three keys:

1. **Attention.** The focused window's PID comes from the window manager via
   `xprop -root _NET_ACTIVE_WINDOW`, then `_NET_WM_PID`. The group around it
   grows *down* through children and *up* through parents, stopping at the
   first name in `protect`. That bound is the whole trick: Firefox's parent is
   `xfce4-panel` and *its* parent is the session, so an unbounded walk would
   declare the entire desktop off limits, while stopping at the first protected
   name keeps a browser's content processes with the browser and nothing more.
2. **In use.** Burning CPU without growing is what a video decoding or a build
   running looks like. Burning CPU *and* growing is the runaway, which stays a
   candidate — otherwise the one process worth catching would be the one
   process protected.
3. **Size**, projected `growth_lookahead` seconds forward rather than measured
   now, so a process taking 40 MB/s is caught while it is still small enough
   for freezing it to be worth something.

No X session, no `xprop`, or a focused window that sets no `_NET_WM_PID`, and
the group is empty — which returns victim choice to size alone, exactly the
behaviour this had before.

**Freezing happens more than once; killing is ordered differently.** Up to
`max_frozen` processes are suspended, one every `freeze_interval`, before the
grace period expires — four idle things suspended in sequence is a gentler way
out than one big kill. But a freeze is reversible and costs a stutter, so it is
always worth spending on something you aren't using however small, whereas a
kill throws work away and has to land on something big enough to actually end
the episode. There is no sense destroying a 400 MB background job to rescue a
machine held down by 2 GB elsewhere. So focus is an absolute at the freeze tier
and a *bias* at the kill tier: `kill_focus_bias = 3` means a process in the
focused app has to be four times the size before it is preferred.

`oom-defender --status` prints the ordering with the focused app marked (`~`
for its group, `*` for the window itself), along with each candidate's CPU and
growth rate, so the decision is inspectable before it ever has to be made.

**Two independent triggers**, because with 19 GB of swap the two ways this
machine dies look nothing alike. *Exhaustion* is free memory and free swap both
nearly gone — a real allocation wall. *Thrashing* is memory PSI `full` staying
high: every task stalled on paging, swap nowhere near full, machine unusable
anyway. The second is the one that matters now that the swapfile exists.

**Victims are `(pid, start time)`, never a bare PID.** PIDs get recycled, and a
stale one plus a `SIGKILL` is how a watchdog kills the wrong process. A zombie
counts as dead: its pages are already gone.

**Nothing under `min_victim_mb` (96 MB) is ever touched**, whatever it scores.
The scoring follows the kernel's own badness — share of RAM per mille, plus
`oom_score_adj` — and two dozen D-Bus session services here ship with
`oom_score_adj=200` at 3–8 MB each, which that formula ranks *above* a 500 MB
browser. Freeing 5 MB doesn't end an OOM; it breaks the trash icon and leaves
the hog running.

**Everything frozen is resumed if the daemon exits for any reason.** A process
left under `SIGSTOP` is indistinguishable from a hung one.

**A freeze is a stutter, not a sentence** — `freeze_max_secs` (90s). Release
otherwise needs `release_mem_pct` (20%) *and* calm PSI, and this machine spends
much of its life between 10% and 20% free: nothing firing any more, nothing
recovered enough to release either. The journal for 2026-08-11 has a browser
process suspended for eleven straight minutes in exactly that gap. Past the
limit, with neither trigger active, it goes free regardless — a second stutter
later is much cheaper than a hang you're sitting in front of.

The daemon `mlockall()`s itself and runs at `OOMScoreAdjust=-1000` with
`MemorySwapMax=0`: a watchdog that has been paged out arrives late to the
emergency it exists for. That needs `LimitMEMLOCK=infinity` in the unit —
systemd's 8 MB default isn't enough for a Python process.

### Firefox unloads its own tabs first

Not in this folder — it lives in
`~/.config/mozilla/firefox/vfgut37p.default-release/user.js` — but it is the
layer above the defender and the two are tuned against each other.

Firefox has **no timer-based tab unloading**. The only trigger is its
low-memory watcher, which on Linux polls `MemAvailable` every 5s (1s once it
thinks memory is low) and unloads the least-recently-used tab when

```
MemAvailable <= browser.low_commit_space_threshold_percent % of MemTotal
             OR
MemAvailable <  browser.low_commit_space_threshold_mb
```

It reads `MemAvailable` **only and ignores swap**, which is the thing worth
knowing here: the 19 GB swapfile above does not blind it. Stock is 5% / 200 MB,
so on 3447 MB of RAM unloading would begin at 200 MB free — and the session
that died on 2026-08-09 was at 135 MB, making the factory setting 65 MB of
warning. Set to **1300 MB (38%)**, which puts the layers in order:

| Free RAM | What happens |
|---|---|
| 1300 MB | Firefox unloads its least-recently-used idle tab, once a second, reversibly |
| 345 MB (10%) | the defender freezes a process you aren't using |
| 172 MB (5%) | the defender kills something |

1300 MB is above where this machine normally sits (400–700 MB free), and that
is the point rather than a mistake: Firefox stays permanently inside its own
low-memory regime, polling once a second and shedding one idle tab per tick
until nothing eligible is left. There is no idle-tab timer anywhere in the
browser, so "unload tabs I'm not using" only exists in this form.

With the threshold effectively always met,
`browser.tabs.min_inactive_duration_before_unload` (2 min here, stock 10) stops
being a detail and becomes **the** control — it is the only thing between you
and every background tab unloading. Raise it if tabs vanish while you're still
working across them.

The rest of the guards are structural. The unloader never touches the **active
tab, pinned tabs, or a tab playing media, in picture-in-picture, or using
WebRTC** — they carry `NEVER_DISCARD` weight — so the video you're watching
survives at the tab level for the same reason it survives at the process level.
Note that `MIN_TABS_COUNT = 10` in `TabUnloader.sys.mjs` is *not* a floor on
how many tabs stay loaded, despite reading like one: those ten are spliced out
to skip memory reweighting and then concatenated back onto the list, so they
are unloaded last rather than not at all. `about:unloads` shows the live
ordering.

One caveat specific to this profile: `dom.ipc.processCount = 2` means all tabs
share two content processes, so unloading a tab frees memory *within* a shared
process rather than retiring a whole one, and the unloader's per-tab
process-count reweighting does nothing. That also makes process-level freezing
far too blunt an instrument for tabs — which is precisely the division of
labour here: **tab granularity is Firefox's job, process granularity is the
defender's.**

**Setup.**

```bash
sudo bash oom/install-oom-defender.sh
sudo SWAPFILE_GB=8 bash oom/install-oom-defender.sh   # smaller swapfile
sudo SWAPFILE_GB=0 bash oom/install-oom-defender.sh   # defender only
```

Re-runnable; the daemon, unit and sysctl file are refreshed from this folder
each time, `/etc/oom-defender.conf` is not. **Re-run it after editing
`oom-defender`** — the installed copy is what runs.

The installer resizes zram live only if what is currently swapped out fits in
free memory. `swapoff` has to fault every page back into RAM, and doing that
without room is the exact failure this whole folder is against; if it declines,
the config is already written and a reboot picks it up.

**Day to day.**

```bash
oom-defender --status                # where you stand, and who is first in line
sudo oom-defender --test-notify      # check notifications reach the desktop
sudo oom-defender --dry-run          # decide and log, signal nothing
journalctl -u oom-defender -f
sudo systemctl reload oom-defender   # after editing /etc/oom-defender.conf
```

Per-process opt-out works without touching the config, since anything at or
below `oom_score_adj = -900` is skipped: `sudo choom -p <pid> -n -900` to
protect something running, `choom -n 500 -- some-command` to volunteer one.

---

## External references

Everything that points into this folder. Update these if the folder moves:

| File | Points at |
|---|---|
| `~/.config/systemd/user/nightlight-auto.service` | `nightlight/nightlight-auto` |
| `~/.config/autostart/nightlight-login.desktop` | `nightlight/nightlight-login` |
| `/etc/lightdm/lightdm.conf.d/50-nightlight.conf` | `nightlight/nightlight-auto`, for the schedule the greeter follows — rewrite it by re-running `install-nightlight-greeter.sh`, not by hand. Its `display-setup-script` is overridden by the `60-` drop-in below, which chains to it |
| `~/.config/autostart/brightness-tray.desktop` | `brightness/brightness-tray.py` |
| `~/.config/autostart/battery-tray.desktop` | `battery/battery-tray.py` |
| `~/.config/autostart/touchpad-tweaks.desktop` | `touchpad/apply-touchpad.sh` |
| `~/.config/autostart/nolock-tray.desktop` | `nolock/nolock-tray.py` |
| `~/.config/autostart/resolution-tray.desktop` | `monitor/resolution-tray.py` |
| `~/.local/share/applications/nightlight-gui.desktop` | `nightlight/nightlight-gui` |
| xfconf `xfce4-keyboard-shortcuts` → `/commands/custom/<Super>n` | `nightlight/nightlight-gui` |
| xfconf `xfce4-keyboard-shortcuts` → `/commands/custom/<Super>u` | `nolock/nolock` |
| `~/.bashrc` | puts `nightlight/` on `PATH` |

A few more live outside this folder but are *copies* rather than references, so
moving it doesn't break them — re-running the matching `install-*.sh` is what
updates them:

| File | Holds |
|---|---|
| `/usr/local/libexec/nightlight-greeter` | a copy of `nightlight/nightlight-greeter` |
| `/usr/local/libexec/monitor-greeter` | a copy of `monitor/monitor-greeter` |
| `/etc/lightdm/lightdm.conf.d/60-monitor.conf` | written by `monitor/install-monitor-greeter.sh`; names that copy, and chains to whatever held `display-setup-script` before it |
| `/etc/lightdm/lightdm-gtk-greeter.conf.d/60-monitor.conf` | written by the same script — the `active-monitor` fallback |
| `/usr/local/libexec/battery-power-profile` | a copy of `battery/power-profile-apply` |
| `/etc/sudoers.d/battery-power-profile` | the NOPASSWD rule naming that path |
| `/usr/local/bin/oom-defender` | a copy of `oom/oom-defender` |
| `/etc/systemd/system/oom-defender.service` | a copy of `oom/oom-defender.service` |
| `/etc/oom-defender.conf` | a copy of `oom/oom-defender.conf`, then hand-tuned |
| `/etc/sysctl.d/99-oom-defender.conf` | written by `oom/install-oom-defender.sh` |
| `/etc/systemd/zram-generator.conf` | written by `oom/install-oom-defender.sh` |
| `/etc/fstab` | the `/swapfile` line, appended by the same script |

`~/.config/mozilla/firefox/vfgut37p.default-release/user.js` is hand-maintained
rather than installed by anything here, but belongs to the same memory strategy
— see *Firefox unloads its own tabs first* above. Delete the file to revert
every pref in it.

`~/.xprofile` is the same kind of thing for the touchpad: hand-maintained, not
installed by anything here, and the other half of *Smooth scrolling* above.
`/etc/lightdm/Xsession` sources it at line 44, before `xfce4-session` starts.
Delete the file to put Firefox back on core-pointer scroll events.

The `Super+N` and `Super+U` bindings live in xfconf, not just on disk. Change
them with `xfconf-query`, not by editing `xfce4-keyboard-shortcuts.xml` —
`xfconfd` keeps the value in memory and will overwrite a hand-edited file:

```bash
xfconf-query -c xfce4-keyboard-shortcuts -p '/commands/custom/<Super>n' -s /new/path
```

Rebinding means creating the new key and removing the old one, since the
keystroke *is* the property name:

```bash
xfconf-query -c xfce4-keyboard-shortcuts -p '/commands/custom/<Super>i' --create --type string \
    --set "$PWD/nolock/nolock toggle"
xfconf-query -c xfce4-keyboard-shortcuts -p '/commands/custom/<Super>u' --reset
```

`nightlight-auto.timer` uses `OnCalendar` wall-clock ticks rather than
`OnUnitActiveSec`, because monotonic time stops during suspend — a laptop
resumed hours later kept its pre-suspend colour until the next tick.
After editing the service or timer: `systemctl --user daemon-reload`.
