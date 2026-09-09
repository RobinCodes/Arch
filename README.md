# Arch desktop integration

Scripts that embed themselves into the Arch desktop — night light, brightness
and battery tray icons, display modes and scaling, power profiles, an OOM
watchdog, and a focus mode. Unlike the other folders here, these aren't
standalone programs you run once: they're wired into systemd, XDG autostart,
the compositor config, and keyboard shortcuts, so **the paths below are
referenced from outside this folder**.

Scripts resolve their siblings relative to their own location, so moving this
folder only requires updating the external references in the last table.

---

## Ported from XFCE/X11 to niri/Wayland

This folder was written against XFCE on X11 and now runs under **niri** with
**noctalia** as the shell. Everything works; several things work better. The
sections below describe the current behaviour, and each one says what it used
to do where the reason is worth keeping.

The port was not a search-and-replace. Five assumptions were load-bearing
across the whole folder, and each one had to be answered differently:

| Assumption | Why it broke | What replaced it |
|---|---|---|
| A gamma ramp is server state that survives the process that set it | Wayland ties the ramp to the client connection and restores it the moment that client exits, so `redshift -P -O` is a no-op with a flicker in it | `nightlight/gamma-apply`, which keeps a holder alive and swaps holders new-first so the screen never flashes |
| The tray is XEmbed, so a `Gtk.StatusIcon` appears in the panel | There is no reparenting on Wayland; a StatusIcon draws **nothing** and does not error | `lib/tray.py` publishes StatusNotifierItems over D-Bus, which noctalia's bar hosts |
| A client can place its own window and grab the seat | Both are compositor-owned by design; `move()` is dropped and there is no grab to take | niri window rules, written to their own `.kdl` files and pulled in with `include` |
| `xrandr`/EWMH can enumerate outputs and windows | No root window, no `_NET_CLIENT_LIST`, no RandR | `niri msg`, via `lib/niri.sh` and `niri msg --json` |
| The desktop's settings live in xfconf | There is no xfconf | noctalia's IPC (`noctalia msg`) where one exists, its `settings.toml` where one does not |

Two changes are improvements rather than substitutions, and worth calling out:

- **`zen` hides the shell instead of killing it.** The XFCE version had to run
  `xfce4-panel --quit` and restart it afterwards, which took every tray plugin
  down with it and brought them back in a different order. noctalia has
  `bar-hide`/`bar-show`, so nothing is quit and nothing has to come back.
- **`resolution` sets one number instead of five.** Panel height, tray icon
  size, dock size, cursor size and Xft DPI were five independent pixel counts
  that all had to be moved together. Wayland has one knob — the output's
  `scale` — and every client that respects it gets all five right at once. The
  density-capped-by-pixel-budget reasoning that computes it is unchanged.

**What did not come across:** the two LightDM greeter hooks. This machine uses
greetd with noctalia's own greeter, which reads nothing in `/etc/lightdm`, so
both installers now refuse and say so rather than writing config nothing reads.
The replacement is `noctalia msg greeter-sync`, which `monitor/resolution`
calls on every mode or scale change — same "one source of truth" argument the
old `monitor-greeter` header makes at length, in one line instead of 309.

### Files niri now owns

`resolution`, `zen` and `brightness-tray` each write a small `.kdl` file next
to `config.kdl` and add one `include` line to it. That line is the only edit
ever made to `config.kdl`, which stays hand-maintained; the included files are
machine-owned and rewritten wholesale.

| File | Written by | Holds |
|---|---|---|
| `~/.config/niri/outputs.kdl` | `monitor/resolution` | the chosen mode and scale per output |
| `~/.config/niri/zen.kdl` | `zen/zen` | the exit button's floating placement |
| `~/.config/niri/brightness-panel.kdl` | `brightness/brightness-tray.py` | the slider panel's floating placement |

The touchpad is configured directly in `config.kdl`'s `input { touchpad { ... } }`
block, which replaces `/etc/X11/xorg.conf.d/72-touchpad-robin.conf` entirely —
including `dwt` (disable-while-typing) and `disabled-on-external-mouse`, the
latter replacing the `touchpad-auto.sh` poller with a compositor feature.

### One dependency to install

Nothing in this folder ships a Wayland gamma tool, and Arch does not install one
by default. Until one is present, `nightlight` reports the problem and refuses
rather than pretending:

```sh
sudo pacman -S gammastep          # or wlsunset
yay -S wl-gammarelay-rs           # best: retunes over D-Bus, no respawn
```

`gamma-apply` auto-detects whichever is installed, preferring the one that does
not need a restart per change. See its header for the ordering and why.

---

## `nightlight/`

Night light (colour temperature) control. Intensity runs 0–100%, mapping
linearly to 6500K (neutral) → 2500K (very warm). All state lives in
`~/.local/state/nightlight/`, including `kelvin`, which holds the current
temperature. (It used to live in `~/.config/redshift/redshift.conf`; that file
is still read once as a fallback so an existing install carries its value
across instead of snapping to neutral.)

**The gamma layer is the one part of this folder that had to be redesigned
rather than translated.** On X11 a gamma ramp is server state, so
`redshift -P -O 4000` could set it and exit and the screen stayed warm — the
old `nightlight` did exactly that, once per call, with no daemon anywhere.
Wayland ties the ramp to the client that set it through `wlr-gamma-control` and
restores the original the moment that client disconnects, so a one-shot set is a
no-op with a flicker in it. `gamma-apply` is the piece that had to be invented:
something stays alive holding the control for as long as the screen is meant to
be warm, and `nightlight` just asks it for a number. The schedule, the override
and the two modes are unchanged.

> **This needs a package.** Nothing here ships a gamma tool and Arch installs
> none by default, so until one is present `nightlight` reports the problem and
> refuses rather than lying about what is on screen:
> `sudo pacman -S gammastep`, or `yay -S wl-gammarelay-rs` for the one that
> retunes over D-Bus without respawning anything.
>
> Symptom before the port was noticed: `nightlight-auto` ran every five minutes
> and logged ``RANDR Query Version' returned error -1 / Initialization of randr
> failed`` each time, having silently done nothing since the move to Wayland.

| Script | Role |
|---|---|
| `nightlight` | The CLI and the single owner of state. `nightlight` prints the current value, `nightlight 60` sets it, `nightlight --mode [auto\|manual]` switches scheduling on or off. |
| `nightlight-auto` | Drives intensity from the clock: held at 100% from 19:00–07:00, easing to a 35% minimum at 13:00. Run every 5 minutes by a systemd timer. `nightlight-auto --print` writes the intensity that should be on screen to stdout and changes nothing. |
| `nightlight-gui` | A `yad` slider popup for live adjustment. Bound to `Mod+N`. Runs under XWayland, which is fine — it only shells out to `nightlight`. |
| `gamma-apply` | Owns "the screen is at K right now". Picks a backend — `wl-gammarelay-rs`, `gammastep`, `wlsunset`, or `redshift` on X11 — and keeps it alive. Swaps holders new-first, so a schedule tick never flashes the screen back to cold. |
| `nightlight-login` | Applies the schedule as the session comes up, from XDG autostart. Twice, three seconds apart — the culprit changed from xfsettingsd's display pass to noctalia, which implements `wlr-gamma-control` itself and can take the ramp while starting up. |
| `nightlight-greeter` | **Inert.** Was the same for the LightDM login screen. greetd is the display manager now. |
| `install-nightlight-greeter.sh` | Refuses and explains, unless `FORCE_LIGHTDM=1`. |

**Auto vs. manual.** In auto mode, setting a value by hand parks an override
until the next 07:00/19:00 boundary, then the schedule takes back over. In
manual mode the schedule never runs and your value stays put.

**Before you log in.** An X server starts with neutral gamma ramps and nothing
in the greeter sets them, so the login screen used to be a white rectangle at
2am and the first colour change of the day landed a second or so into the
session. `install-nightlight-greeter.sh` fixes that:

> **Historical, from the LightDM era.** greetd runs noctalia's own greeter now,
> which reads nothing under `/etc/lightdm`, so none of what follows in this
> subsection is live. It is kept because the reasoning is the reasoning, and
> because switching back to LightDM would make it true again. The current
> answer is `noctalia msg greeter-sync`, called by `monitor/resolution`.

```bash
sudo bash nightlight/install-nightlight-greeter.sh   # refuses unless FORCE_LIGHTDM=1
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
| `resolution` | The CLI and the single owner of resolution changes. `resolution` prints the current mode, `resolution highest\|fullhd\|lowest` sets one, `resolution retier` re-fits the UI scale without touching the mode, `resolution --list` prints all three plus the active one in a parseable form. |
| `resolution-tray.py` | The tray icon those are picked from. |
| `niri-output.py` | The persistence half: writes `~/.config/niri/outputs.kdl` and adds the one `include` line to `config.kdl`. |
| `monitor-greeter` | **Inert.** Was the LightDM greeter's display layout. |
| `install-monitor-greeter.sh` | Refuses and explains, unless `FORCE_LIGHTDM=1`. |

**The greeter problem solved itself.** `monitor-greeter` was 309 lines that ran
as root out of a LightDM display-setup hook, dropped privileges to read the
user's `displays.xml`, re-validated every field against a regex, and rebuilt the
layout with `xrandr` before the greeter drew — all to work around a greeter that
started X with no configuration and put the login box on whichever monitor
happened to be first. noctalia's greeter is told directly:

```bash
noctalia msg greeter-sync
```

`resolution` calls that on every mode or scale change, so the login screen
follows the session for the same "one source of truth" reason the old script's
header argues for at length, in one line instead of several hundred.

**Applying is now two writes, in the opposite order from before.**
`niri msg output ... mode ...` is explicitly temporary — niri forgets it the
next time the config is read — so the mode is also written to `outputs.kdl`.
Under XFCE the profile had to be written *first*, to beat `xfsettingsd`
re-applying its saved copy on the RandR event. niri has no daemon racing us, and
writing the config first would make niri reload and re-apply mid-change, so the
compositor is told first and the file second.

> **Historical, from the LightDM era.** greetd runs noctalia's own greeter now,
> which reads nothing under `/etc/lightdm`, so none of what follows in this
> subsection is live. It is kept because the reasoning is the reasoning, and
> because switching back to LightDM would make it true again. The current
> answer is `noctalia msg greeter-sync`, called by `monitor/resolution`.

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

Requires `niri` (for `niri msg`) and `python3`.

### The resolution tray

`resolution-tray.py` puts a display icon in the bar. Its menu offers three
presets for whichever output the focused window is on — niri has no `primary`
flag, being a scrolling WM with no "main" screen, and the focused output is the
closest honest equivalent:

| Preset | On the LG | How it's chosen |
|---|---|---|
| Highest | 3840x2160 @ 60 Hz | most pixels, then fastest rate |
| Full HD | 1920x1080 @ 60 Hz | nearest mode to 1920x1080 |
| Lowest | 640x480 @ 60 Hz | fewest pixels, then fastest rate |

Nothing is hard-coded to this monitor. The presets are resolved from
`niri msg outputs` for the focused output on each read, so they still mean
something on a
different screen, and Full HD degrades to the closest available mode rather than
failing on a monitor with no exact 1920x1080. The LG's own ceiling is 3840x2160
@ 60: its EDID caps the pixel clock at 600 MHz, hsync at 135 kHz and vsync at
61 Hz, and that mode sits on all three limits at once, so there is no higher
resolution *and* no faster refresh at any lower one.

**Three things move together on every switch**, which is why this is a script
and not three `niri msg` aliases:

1. **The niri output config, written second.** `niri msg output ... mode ...` is
   temporary and forgotten on the next config read, so the mode also goes to
   `outputs.kdl`. The *order* is the opposite of the XFCE version, and for a
   symmetrical reason: there, `xfsettingsd` re-applied its saved layout on RandR
   events, so `displays.xml` had to be written first or the change had a timer
   on it. Here nothing races us, and writing the file first would make niri
   reload and re-apply mid-change.
2. **The scale, recomputed.** Under XFCE this was five independent pixel counts
   — panel height, tray icon size, dock size, cursor size and Xft DPI — none of
   which followed resolution on their own, so all five had to be moved together
   or 640x480 left a panel eating a sixth of the screen and 4K left everything
   half-size. Wayland has one knob, the output's `scale`, and every client that
   respects it gets all five right at once. There is no Xft DPI here and nothing
   sets one: a scale of 1.5 *is* how you say "144 dpi" to a Wayland client.
3. **The greeter, told.** `noctalia msg greeter-sync` pushes the layout to the
   login screen, replacing the 309-line `monitor-greeter`.

The scale is quantised to `SCALE_STEP` (0.25 by default). niri accepts arbitrary
fractional scales, but the quarter steps are the ones clients and icon themes
are actually tested against, and 1.5 looks better than 1.49 for reasons that
have nothing to do with arithmetic.

| Screen | Density asks for | Pixel budget allows | Scale |
|---|---|---|---|
| 27" 4K (3840 px / 600 mm) | 143 dpi | 270 dpi | **1.5** |
| 11.6" laptop (1366 px / 260 mm) | 117 dpi | 96 dpi *(binds)* | **1.0** |

`TIERS_ENABLED=0 resolution fullhd` changes the mode and nothing else.

**XWayland clients are scaled by the compositor rather than re-rendered**, so
they land at the right physical size and a slightly soft one. That is niri's
behaviour, not something this can fix.

**4K is not free on this machine.** Moving from the old upscaled desktop to a
native 3840x2160 framebuffer took `shared` memory from ~247 MB to ~617 MB and
`available` from ~1.17 GB to ~660 MB — worth reading next to the pre-launch
memory rule in `oom/` below, because it comes straight off that cushion. Full HD
hands most of it back, which is a real use for the middle preset beyond taste.

Requires PyGObject (`python-gobject`) and `libappindicator`; `niri msg` and
`noctalia msg` come with the session.

---

## `brightness/`

`brightness-tray.py` — backlight and night light warmth from the bar. The menu
carries a few brightness steps, a few warmth steps and the Auto switch;
*Adjust...* opens a slider panel with both. Warmth and the Auto switch delegate
to `../nightlight/nightlight`.

**This tray had the most XFCE-shaped interface of the four and is the one
StatusNotifierItem could not reproduce as-is.** Three things had to move:

- **A left click can no longer open a popup.** SNI gives the bar one gesture and
  it opens the menu; there is no activate event to hook. So the common cases are
  menu items now.
- **Sliders cannot go in a menu.** DBusMenu carries labels, checkboxes, radio
  items and separators — a `Gtk.Scale` packed into a menu item arrives as an
  empty row. That is the whole reason for the steps-in-menu plus panel split.
- **The panel is a real window.** The X11 build used an override-redirect
  `Gtk.WindowType.POPUP`, positioned by hand against the tray icon and dismissed
  by a seat grab that caught the click landing outside it. A Wayland client can
  do none of those three: it cannot read its icon's position, place its own
  toplevel, or grab the seat. It is an ordinary window that niri floats by rule,
  and it closes on Escape, its Close button, or focus-out — which the compositor
  now actually reports, because this is a window it manages.

Scrolling over the icon still steps brightness where the bar forwards a scroll
(SNI has a `Scroll` method and libappindicator forwards it, but a bar is not
obliged to send one). Every step is also in the menu, so nothing is reachable
only by scrolling.

Requires `brightnessctl`, PyGObject (`python-gobject`) and `libappindicator`.

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

**On icon size.** The layout constraint is unchanged — the icon is square, so
the bolt can't sit beside the digits and while charging the digits give up the
top 40% of the box to it. What changed is how the size is decided. XEmbed let
the panel hand the tray its exact pixel size through `size-changed`, and the
icon was redrawn at that resolution. A StatusNotifierItem has no such
conversation: the bar takes the picture and scales it. So the icon is now drawn
once at 32px — big enough to survive being scaled down to whatever the bar uses,
where a 16px bitmap would visibly blur on a taller bar or at scale 2.

**On publishing a drawn icon at all.** SNI can carry pixel data inline, but
libappindicator only exposes the by-name path, so `lib/tray.py` writes the
pixbuf to a PNG in a private directory registered as an icon theme path. The
filename carries a serial that changes on every write — icon lookups are cached
aggressively by every toolkit involved, and writing new bytes to the same name
reliably leaves the bar showing the old picture, which for this tray would mean
a percentage that never changes. Two files are kept and the rest deleted, or a
write per poll would be thousands of files an hour.

## `nolock/`

Skip the password prompt when the laptop wakes — a tray toggle and `Mod+Alt+U`,
for when you're somewhere you'd rather just open the lid.

| File | Role |
|---|---|
| `nolock` | The CLI and the single owner of the setting. `nolock` prints `on` or `off` plus what each switch currently reads; `nolock on\|off\|toggle` changes it. |
| `nolock-tray.py` | The tray icon: **open padlock** = nolock on (wake straight into the session), **closed padlock** = a password on wake. The menu carries the toggle, *Lock Now*, and noctalia's settings. |
| `noctalia-setting.py` | Reads and writes one boolean in noctalia's `settings.toml`, which is how the three switches below are reached — noctalia has no IPC command for them. |

**The lock triggers changed owner, and there are three of them now, not five.**
niri does not lock; noctalia does, and logind decides what the lid means.

| Setting | Default | Locks? |
|---|---|---|
| `lockscreen.lock_before_suspend` | `true` | **Yes — this is the one that catches you.** noctalia holds a logind sleep *delay* inhibitor (visible as "Lock before sleep" in `systemd-inhibit --list`) and raises the lock screen on the way down. Direct heir to xfce4-screensaver's `/lock/sleep-activation`. |
| `idle.behavior.lock.enabled` | `false` | Lock after an idle timeout. Ships off, so it costs nothing today — managed so that turning it on later cannot silently defeat nolock. |
| `idle.behavior.lock-and-suspend.enabled` | `false` | Same, with a suspend attached. |

`lockscreen.enabled` is deliberately left alone. It is the master switch, and
with it on `Super+Alt+L` still locks on demand while nolock is on — exactly the
reasoning that kept `/lock/enabled` untouched under XFCE.

**The lid pair has no equivalent and is reported rather than changed.**
`xfce4-power-manager` held a logind `handle-lid-switch` inhibitor and decided
for itself what a lid close meant, which is why it could be told "blank the
screen" instead of "lock" — and why it took so long to find, since closing the
lid never slept the machine and none of the sleep switches ever got a say.
Nothing in this session takes that inhibitor, so the lid belongs to logind
again and `HandleLidSwitch` lives in `/etc/systemd/logind.conf`: root-owned
system configuration, not a per-user toggle. With `lock_before_suspend` off, a
lid close still suspends and still comes back without asking, which is the
outcome that was wanted. `nolock status` prints the current lid action so the
whole picture is in one place.

**Why the table grew a column.** The XFCE version got away with one value doing
double duty: for all five of its keys, the value meaning "this would lock" was
*also* the compiled-in default, so an unset key read as locking and restored to
the value it would have had anyway. That coincidence does not hold here.
`lock_before_suspend` defaults to `true` (locking) and both idle behaviours
default to `false` (not locking), so "what does unset mean" and "what do I
write to make it lock again" became different questions.

Getting that wrong is not theoretical: the first version of the port restored
an unset key by writing the locking value, and `nolock off` duly switched **on**
two idle-lock behaviours that had never been on. Restoring now means removing
the key, which is what `noctalia-setting.py del` is for.

## `keyboard/`

Recovery for a session that has stopped taking input, and the logind tuning
that makes the worst case rarer.

| File | Role |
|---|---|
| `unstick-keyboard` | Recovery. Resumes SIGSTOP'd processes and restarts the shell if it has stopped answering its IPC. Installed to `/usr/local/bin/`. |
| `unstick-keyboard.desktop` | Launcher for it, because the recovery has to be reachable without a keyboard. Installed to `/usr/share/applications/`. |
| `10-inhibit-delay.conf` | `InhibitDelayMaxSec=30`. Installed to `/etc/systemd/logind.conf.d/`. |
| `install-keyboard-fix.sh` | One-shot `sudo` install of all three. |

**The original bug cannot happen here, and that is worth being precise about.**
On X11, `xfce4-screensaver` held a logind delay inhibitor on sleep so it could
raise the lock screen first, and locking meant `XGrabKeyboard` on the root
window. If `InhibitDelayMaxSec` elapsed first, logind suspended anyway and froze
`user.slice` mid-lock — and what came back was a screensaver stuck partway
through locking, still holding the keyboard grab, with no lock window on screen
to type into. Every keystroke went to that grab and vanished.

There is no `XGrabKeyboard` on Wayland and no root window to grab. Input routing
belongs to the compositor, and a client that wedges *loses* input rather than
capturing everyone else's. niri keeps taking keys no matter what any client does.

**Two things survived the port, which is why the folder still exists:**

1. **Suspended processes.** `oom-defender` freezes with SIGSTOP and resumes on
   its own — but only while it is alive to remember what it froze. Killed or
   crashed mid-episode, its victims stay stopped forever. A stopped process
   owning the window you are typing into is indistinguishable from a dead
   keyboard, and this is now the *most* likely cause rather than the least.

2. **A wedged lock screen.** noctalia locks through `ext-session-lock`, which is
   deliberately fail-closed: if the lock client dies without unlocking, the
   compositor keeps the session locked and shows a blank screen. That is the
   right security choice and a terrible place to be stuck, because the thing
   that could unlock it is the thing that died. `unstick-keyboard` detects a
   shell that has stopped answering `noctalia msg status` and restarts it, which
   puts a password prompt back on screen.

**`10-inhibit-delay.conf` is still doing real work**, for the same reason as
before with a different daemon: noctalia takes a sleep *delay* inhibitor to lock
before suspend, and on a 3.4 GB machine that has been paged out to swap, the
shipped 5s budget is tight. 30s is the cost ceiling, not the cost — the
inhibitor is released as soon as the lock is up, so a suspend that took 0.3s
still takes 0.3s.

> **Installed 2026-08-17.** `systemd-analyze cat-config systemd/logind.conf`
> confirms the drop-in still overrides the stock 5s under the current session.
> `unstick-keyboard` itself was rewritten for Wayland and needs reinstalling
> (`sudo bash install-keyboard-fix.sh`) — the copy in `/usr/local/bin` is the
> X11 one until you do.

## `touchpad/`

**Superseded by the compositor.** Everything this folder did is now three lines
in `~/.config/niri/config.kdl`:

```kdl
input {
    touchpad {
        tap
        dwt                             // disable while typing
        drag-lock
        natural-scroll
        tap-button-map "left-right-middle"
        click-method "button-areas"     // bottom-right corner = right click
        disabled-on-external-mouse
    }
}
```

That is full parity with the old `72-touchpad-robin.libinput.conf`, plus the
two things that used to need help:

| Old mechanism | Why it existed | Replaced by |
|---|---|---|
| `apply-touchpad.sh`, run twice at login from XDG autostart | `xfsettingsd` reset scroll direction and tap mapping at every login, so the config had to be re-asserted after it had had its say | Nothing. niri applies its own input config and there is no second daemon racing it. |
| `touchpad-auto.sh` + `touchpad-auto.service`, a 2s poller | The ELAN0647 emits ghost touches (32 phantom button events in 30s, measured 2026-08-20, with `kernel bug: Touch jump detected and discarded` in the Xorg log). The poller disabled the touchpad whenever a USB mouse was present. It had to be a poller because udev `RUN+=` has no session to reach `xinput` from. | `disabled-on-external-mouse`, a libinput feature niri exposes directly. No polling, no X connection, no service. |

The X11 files (`72-touchpad-robin.conf`, `72-touchpad-robin.libinput.conf`,
`apply-touchpad.sh`, `switch-to-libinput.sh`, `touchpad-auto.sh`) are kept for
reference and for an X session; none of them run here.
`touchpad-auto.service` has been disabled — it was restart-looping every 5
seconds against an X server that does not exist, 447 restarts deep by the time
it was found. `grab-watchdog.service`, which is not in this repo but was
looping for the same reason, was disabled with it.

### Smooth scrolling, and the half that stopped being necessary

The touchpad half is the config above. The other half was `~/.xprofile`
exporting `MOZ_USE_XINPUT2=1`, because on X11 pixel-precise scroll deltas only
reached a client that asked for XInput2 events, and Firefox did not ask unless
that was set — so scrolling felt stepped in the browser and smooth everywhere
else.

**That variable does nothing under Wayland and is no longer needed.** There is
no core-protocol fallback to opt out of: `wl_pointer.axis` carries the delta
natively, and Firefox on Wayland gets kinetic flicks, pinch zoom and swipe
navigation without being told to. `~/.xprofile` is also no longer sourced —
it was read by `/etc/lightdm/Xsession`, which nothing runs now.

## `zen/`

Strip the desktop down to one app (or a few) and freeze the screen.

| File | Role |
|---|---|
| `zen` | The CLI and the owner of the session state. `zen NAME...` enters, `zen --pick` chooses from a dialog, `zen --exit` leaves, `zen --status`, `zen --dry-run`. |
| `zen-wm.py` | Window listing and control, via niri's IPC. |
| `zen-pick.py` | The chooser: type-to-filter over sets and every installed app, with checkboxes for multi-app zen. |
| `zen-apps.py` | Lists every installed application as `name<TAB>command`, straight from the XDG desktop-entry path. |
| `zen-button.py` | The leave-zen affordance: one button, and nothing around it. |

**Zen stays inside the running compositor session, and the reason got stronger
after the port.** The night light is a gamma ramp held by a live client, and the
compositor takes it back the instant that client goes away — so a kiosk session
on another VT, or restarting niri, would snap the screen to a cold 6500K the
moment zen started. On X11 the same conclusion came from the ramp being
per-X-server state; here it is per-*connection*, which is a shorter fuse for the
same mistake. Zen tears down the visible layer and stops the automation, and
never touches the display pipeline.

**The desktop is asked, not killed — and this is the port's clearest win.**

| What zen does | XFCE | niri + noctalia |
|---|---|---|
| Hide the panel | `xfce4-panel --quit`, then restart it on exit | `noctalia msg bar-hide` / `bar-show` |
| Hide the desktop | `xfdesktop --quit` | `desktop-widgets-hide` / `-show` |
| Silence notifications | xfconf `xfce4-notifyd /do-not-disturb` | `notification-dnd-set on\|off` |
| Stop idle/blank | xfconf presentation-mode + `xfce4-screensaver-command --inhibit` | `caffeine-enable`, plus a `systemd-inhibit` that dies with zen |
| Hide OSD popups | *(not possible)* | `osd-disable` / `osd-enable` |

Quitting the panel took every tray plugin down with it — clipman, nm-applet,
blueman, and this repo's own four — and brought them back in a different order,
or not at all if one had crashed meanwhile. Hiding is reversible in a way
quitting never was.

**What was lost: the single workspace.** xfwm4 had a fixed workspace count that
could be set to 1, so there was literally nowhere to wander off to. niri creates
and destroys workspaces on demand and has no count to clamp. Faking it would
mean rewriting the user's keymap on the way in and restoring it on the way out —
a much bigger promise than zen should make about a config file it does not own.

**Windows are handled by id, not by pid.** `zen-wm.py` was built on libwnck,
which reads EWMH properties off the X root window; there is no root window and
no `_NET_CLIENT_LIST` here, so niri is now the only authority on what exists.
Closing still sends a polite close request, so an app with unsaved work gets to
put up its save prompt — anything still alive at the deadline is reported rather
than killed.

The one thing that got *harder*: niri reports `is_floating`, `is_focused` and
`is_urgent` for a window but **not** whether it is fullscreen, and
`fullscreen-window` is a toggle rather than a set. So "put everything back"
cannot be answered by asking. The ids zen fullscreened are written to
`~/.local/state/zen/fullscreened` as they happen, and exit toggles exactly those
back — which also makes it correct across a zen that died and was restarted,
something the libwnck version got for free by reading live state.

**The exit button is placed by the compositor.** A Wayland client cannot
position its own toplevel, so the corner the X11 build asked for with `move()`
is a niri window rule now, written to `~/.config/niri/zen.kdl` on every enter.
It opens unfocused on purpose: it appears while the app you asked for is still
mapping, and stealing focus at that moment would put your first keystrokes into
a button.

Requires PyGObject, `yad`, and noctalia for the hide/show half (zen still runs
without it, there is just less to hide).

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

1. **Attention.** The focused window's PID comes from the compositor —
   `niri msg focused-window`, which hands the pid over directly, or the old
   two-step `xprop -root _NET_ACTIVE_WINDOW` → `_NET_WM_PID` walk under X11.
   Wayland has no root window and no `_NET_ACTIVE_WINDOW` to read, so there is
   no protocol-level way to ask this question: every compositor answers on its
   own IPC or not at all. That is why it is a branch rather than one command.

   The group around it grows *down* through children and *up* through parents,
   stopping at the first name in `protect`. That bound is the whole trick: a
   browser's parent is the shell and *its* parent is the session, so an
   unbounded walk would declare the entire desktop off limits, while stopping at
   the first protected name keeps a browser's content processes with the browser
   and nothing more.
2. **In use.** Burning CPU without growing is what a video decoding or a build
   running looks like. Burning CPU *and* growing is the runaway, which stays a
   candidate — otherwise the one process worth catching would be the one
   process protected.
3. **Size**, projected `growth_lookahead` seconds forward rather than measured
   now, so a process taking 40 MB/s is caught while it is still small enough
   for freezing it to be worth something.

No graphical session, no answer from the compositor, or nothing focused, and
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
| `~/.config/autostart/brightness-tray.desktop` | `brightness/brightness-tray.py` |
| `~/.config/autostart/battery-tray.desktop` | `battery/battery-tray.py` |
| `~/.config/autostart/nolock-tray.desktop` | `nolock/nolock-tray.py` |
| `~/.config/autostart/resolution-tray.desktop` | `monitor/resolution-tray.py` |
| `~/.local/share/applications/nightlight-gui.desktop` | `nightlight/nightlight-gui` |
| `~/.config/niri/config.kdl` → `binds` | `nightlight-gui`, `nolock`, `zen`, `unstick-keyboard` — see the keybinding table below |
| `~/.bashrc` | puts `nightlight/` on `PATH` |

**XDG autostart works under niri** — `niri --session` activates
`xdg-desktop-autostart.target` and systemd generates an `app-*@autostart.service`
per `.desktop` file, so the four trays start with the session exactly as they
did under XFCE. Two entries needed attention during the port:

- `nightlight-login.desktop` carried `OnlyShowIn=XFCE;`, so the generator
  created the unit and then skipped it every boot (`Condition check resulted in
  Night light being skipped`). The line is gone.
- `touchpad-tweaks.desktop` was deleted outright — see `touchpad/`.

### Keybindings

These used to live in xfconf, which does not exist here; they are now plain
niri binds in `config.kdl`. `Super+U` could not be kept because niri already
uses it for `focus-workspace-down`.

| Chord | Runs | Was |
|---|---|---|
| `Mod+N` | `nightlight/nightlight-gui` | `Super+N` (unchanged) |
| `Mod+Alt+U` | `nolock/nolock toggle` | `Super+U` |
| `Mod+Shift+Z` | `zen/zen --pick` | `Super+Shift+Z` (unchanged) |
| `Mod+Z` | `zen/zen --exit` | `Super+Z` (unchanged) |
| `Mod+Ctrl+Shift+U` | `keyboard/unstick-keyboard --quiet` | a panel launcher |

Because they are in the config file rather than in a daemon's memory, editing
`config.kdl` is now the right way to change them — niri reloads on save, and
`niri msg action load-config-file` forces it. The old warning about `xfconfd`
overwriting a hand-edited XML file no longer applies to anything here.

### Machine-owned niri files

Written by the scripts, safe to delete (they are regenerated), and pulled into
`config.kdl` by a single `include` line each:

| File | Written by |
|---|---|
| `~/.config/niri/outputs.kdl` | `monitor/resolution` |
| `~/.config/niri/zen.kdl` | `zen/zen` |
| `~/.config/niri/brightness-panel.kdl` | `brightness/brightness-tray.py` |

### Copies, not references

These live outside the folder as *copies*, so moving it doesn't break them —
re-running the matching `install-*.sh` is what updates them:

| File | Holds |
|---|---|
| `/usr/local/bin/oom-defender` | a copy of `oom/oom-defender` |
| `/etc/systemd/system/oom-defender.service` | a copy of `oom/oom-defender.service` |
| `/etc/oom-defender.conf` | a copy of `oom/oom-defender.conf`, then hand-tuned |
| `/etc/sysctl.d/99-oom-defender.conf` | written by `oom/install-oom-defender.sh` |
| `/etc/systemd/zram-generator.conf` | written by `oom/install-oom-defender.sh` |
| `/etc/fstab` | the `/swapfile` line, appended by the same script |
| `/usr/local/bin/unstick-keyboard` | a copy of `keyboard/unstick-keyboard` |
| `/etc/systemd/logind.conf.d/10-inhibit-delay.conf` | a copy of `keyboard/10-inhibit-delay.conf` |
| `/usr/local/libexec/battery-power-profile` | a copy of `battery/power-profile-apply` |
| `/etc/sudoers.d/battery-power-profile` | the NOPASSWD rule naming that path |

> **Two copies are stale** after the port and need `sudo` to refresh:
> `/usr/local/bin/oom-defender` (still the X11 focus-detection build) and
> `/usr/local/bin/unstick-keyboard` (still the screensaver-grab build). Re-run
> `oom/install-oom-defender.sh` and `keyboard/install-keyboard-fix.sh`.

### Inert leftovers

Harmless, but they are not doing anything and will not:

| File | Why it is dead |
|---|---|
| `/usr/local/libexec/nightlight-greeter`, `/etc/lightdm/lightdm.conf.d/50-nightlight.conf` | LightDM is not the display manager; greetd is |
| `/usr/local/libexec/monitor-greeter`, `/etc/lightdm/lightdm.conf.d/60-monitor.conf`, `/etc/lightdm/lightdm-gtk-greeter.conf.d/60-monitor.conf` | same |
| `/etc/X11/xorg.conf.d/72-touchpad-robin.conf` | no X server reads it |
| `~/.xprofile` | sourced by `/etc/lightdm/Xsession`, which nothing runs; `MOZ_USE_XINPUT2` is meaningless on Wayland |
| `~/.config/redshift/redshift.conf` | read once as a fallback by `nightlight`, then superseded by `~/.local/state/nightlight/kelvin` |
| `~/.config/systemd/user/grab-watchdog.service`, `touchpad-auto.service` | disabled during the port; both were restart-looping against an absent X server |

`~/.config/mozilla/firefox/vfgut37p.default-release/user.js` is hand-maintained
rather than installed by anything here, but belongs to the same memory strategy
— see *Firefox unloads its own tabs first* above. Delete the file to revert
every pref in it.

`nightlight-auto.timer` uses `OnCalendar` wall-clock ticks rather than
`OnUnitActiveSec`, because monotonic time stops during suspend — a laptop
resumed hours later kept its pre-suspend colour until the next tick.
After editing the service or timer: `systemctl --user daemon-reload`.
