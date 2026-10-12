# Arch desktop integration

Scripts that embed themselves into the Arch desktop — night light, brightness
and battery tray icons, display modes and scaling, power profiles, an OOM
watchdog, and a focus mode.

**If the tray icons are missing**, there are two separate causes and one
command tells them apart:

```sh
systemctl --user list-units 'app-*'   # inactive dead -> run install-autostart.sh
busctl --user get-property org.kde.StatusNotifierWatcher \
    /StatusNotifierWatcher org.kde.StatusNotifierWatcher \
    RegisteredStatusNotifierItems     # active but empty -> the backend race
```

See *They register and never start* and *They start, and publish nothing*. Unlike the other folders here, these aren't
standalone programs you run once: they're wired into systemd, XDG autostart,
the compositor config, and keyboard shortcuts, so **the paths below are
referenced from outside this folder**.

Scripts resolve their siblings relative to their own location, so moving this
folder only requires updating the external references in the last table.

---

## Two sessions, one set of scripts

This folder runs under **XFCE on X11** and under **niri with noctalia** as the
shell. Neither is the fallback. Every script picks a backend at startup and the
command means the same thing in both:

```sh
session_kind()   # niri | x11 | unknown      — lib/niri.sh
```

It asks the niri IPC socket first, then `$DISPLAY`. The socket rather than
`$XDG_CURRENT_DESKTOP`, because that variable is inherited by anything the
session spawns — including a shell on a TTY with no way to reach the
compositor — while the socket is the thing the IPC actually needs.

### Where the two genuinely differ

These are not two spellings of one mechanism. Five assumptions were load-bearing
in the X11 code and none of them survive on Wayland, so both implementations are
kept side by side rather than one being ported over the other:

| Assumption | Why it fails on Wayland | The two paths |
|---|---|---|
| A gamma ramp is server state that outlives the process that set it | The ramp belongs to the client connection and is restored the moment it drops, so `redshift -P -O` is a no-op with a flicker in it | `redshift` one-shot / `gamma-apply` keeps a holder alive |
| The tray is XEmbed, so `Gtk.StatusIcon` appears in the panel | There is no reparenting; a StatusIcon draws **nothing** and does not error | `Gtk.StatusIcon` / a StatusNotifierItem served by `lib/sni.py`, chosen by `lib/tray.py` |
| A client can place its own window and grab the seat | Both are compositor-owned by design | `gtk move()` + seat grab / niri window rules in their own `.kdl` files |
| `xrandr` and EWMH enumerate outputs and windows | No root window, no `_NET_CLIENT_LIST`, no RandR | `xrandr` + libwnck / `niri msg` |
| The desktop's settings live in xfconf | There is no xfconf | `xfconf-query` / `noctalia msg` and its `settings.toml` |

### What each script does about it

| Script | X11 | niri |
|---|---|---|
| `nightlight` | `redshift -P -O` | `gamma-apply` → `wl-gammarelay-rs`, `gammastep` or `wlsunset` |
| `resolution` | `xrandr` + `displays.xml` + 5 xfconf values | `niri msg outputs` + `outputs.kdl` + one `scale` |
| `nolock` | 5 xfconf keys across 2 daemons | 3 noctalia settings |
| `zen` | `xfce4-panel --quit`, xfconf, 1 workspace | `noctalia msg bar-hide`, `caffeine`, `systemd-inhibit` |
| `zen-wm.py` | libwnck | `niri msg` |
| `unstick-keyboard` | kill the wedged `xfce4-screensaver` X grab | resume SIGSTOP'd procs, restart a wedged shell |
| trays ×4 | `Gtk.StatusIcon` | StatusNotifierItem |
| `oom-defender` | `xprop -root _NET_ACTIVE_WINDOW` | `niri msg focused-window` |
| `power-profile` | *(same — pure sysfs, no desktop involved)* | |

Two of these are improvements rather than translations, and only work on the
niri side:

- **`zen` hides the shell instead of killing it.** xfce4-panel has no hide
  command, so the X11 path must quit it — taking every tray plugin with it and
  bringing them back in whatever order the panel restores them. noctalia has
  `bar-hide`/`bar-show`.
- **`resolution` sets one number instead of five.** Panel height, tray icon
  size, dock size, cursor size and Xft DPI are five independent pixel counts
  under XFCE. Wayland has one knob — the output's `scale` — and every client
  that respects it gets all five right at once. The density-capped-by-pixel-
  budget reasoning that computes it is shared.

### Which trays run here

Only two, and the reason is overlap rather than anything being broken:
noctalia's bar already carries network, bluetooth, volume, brightness and a
battery percentage as native widgets, so running this repo's versions beside
them was two icons for one job.

| Tray | Status | Why |
|---|---|---|
| `battery-tray.py` | **running** | noctalia shows a battery percentage but has no equivalent of the four sysfs power profiles or the AC-follow — that menu is the reason this one stays |
| `resolution-tray.py` | **running** | noctalia has no display-mode widget at all |
| `nightlight-tray.py` | **running** | noctalia has `nightlight-toggle`, but its night light is its own schedule with its own times; this one is the front end for the curve in `nightlight-auto` |
| `brightness-tray.py` | retired | noctalia's `brightness` widget covers it |
| `nolock-tray.py` | retired | no noctalia equivalent, but `nolock` from the CLI does the same job |
| `nm-applet`, `blueman` | retired | noctalia's `network` and bluetooth widgets cover both |

Which of them autostart is decided by `install-autostart.sh`, not by hand-edited
`.desktop` files — see *They register and never start* below.

The retired ones are disabled, not deleted — the scripts still work and still
run under XFCE. `nm-applet` and `blueman` autostart from `/etc/xdg/autostart`,
which needs root to change, so they are suppressed the XDG-spec way instead: a
same-named `.desktop` in `~/.config/autostart` carrying `Hidden=true`, which
the systemd generator honours.

### They register and never start

The failure that took all the tray icons off the bar had nothing to do with the
tray protocol, which is why restarting noctalia never helped and why the icons
came back the moment they were launched by hand.

The autostart entries were written under XFCE and carried `OnlyShowIn=XFCE;`.
systemd's xdg-autostart generator turns that into an `ExecCondition`:

```
ExecCondition=/usr/lib/systemd/systemd-xdg-autostart-condition "XFCE" ""
```

`$XDG_CURRENT_DESKTOP` is `niri` here, so the condition fails. The unit is
generated, it is wanted by `xdg-desktop-autostart.target`, and it is skipped —
every login, silently apart from one line each in the journal:

```
app-battery\x2dtray@autostart.service: Skipped due to 'exec-condition'.
Condition check resulted in Battery Tray being skipped.
```

`install-autostart.sh` regenerates the entries with no `OnlyShowIn` and no
`NotShowIn`. Naming a desktop in a `.desktop` file is the thing that broke this,
and it was never needed: `lib/tray.py` asks the session bus whether anything
owns `org.kde.StatusNotifierWatcher` and picks its backend from the answer, so
the scripts already answer "which session is this" better than the file can.

```bash
bash install-autostart.sh     # no root; everything it touches is under $HOME
```

It also enables noctalia's GTK template (see `nightlight/`) and adds the
`include` line for the night light panel's floating rule.

### They start, and publish nothing

With the `OnlyShowIn` gate gone the units ran — and the icons were still not
there. The units were `active`, the three processes were up, and the watcher had
**zero** items registered. They had picked the wrong backend.

`lib/tray.py` used to probe the bus once, at construction, and read "nobody owns
`org.kde.StatusNotifierWatcher`" as "use `Gtk.StatusIcon`". That is a race, and
it loses it on every cold boot:

```
12:19:22.053  Starting Battery Tray...
12:19:22.117  Started Night Light Tray.
12:19:22.122  Started app-niri-noctalia-829.scope     <- the host, last
```

niri activates `xdg-desktop-autostart.target` as soon as the session is up and
spawns noctalia from its own config at about the same moment, so the trays ask
whether an SNI host exists some tens of milliseconds before the only thing that
could answer has been executed. They fall back to XEmbed, which under a Wayland
compositor draws nothing and does not error. Started by hand later they work
perfectly — which is exactly what made this look like the autostart bug twice.

**`_pick_backend` no longer asks the bus on Wayland.** SNI is the only backend
that *can* work there, host or no host, and `sni.Item` already watches the
watcher name and registers the moment it appears — so a bar that starts late,
or restarts, picks the item up on its own.

The two mistakes are not symmetric, and that decides the default. Choosing SNI
wrongly costs nothing XEmbed would have saved: with no host the item waits, and
if one never arrives XEmbed had nowhere to draw either. Choosing XEmbed wrongly
is silent and permanent. So only a positively identified X11 session gets
XEmbed; anything unrecognised gets the backend that can still come good.

That last clause is not hypothetical either. A unit that starts before niri's
`systemctl --user import-environment` has no `WAYLAND_DISPLAY`, and GTK then
quietly connects to **XWayland** — so even asking Gdk which backend it got
returns `X11Display` in a session with no X11 system tray in it. `DISPLAY` is
set under niri for the same reason. Neither is evidence of anything.

| Session | Watcher | Backend |
|---|---|---|
| `WAYLAND_DISPLAY` or `NIRI_SOCKET` set | either | `sni` |
| environment missed the import | present | `sni` |
| environment missed the import | absent | `sni` — it can still recover |
| `XDG_SESSION_TYPE=x11` | absent | `statusicon` — XFCE's XEmbed tray |

> **Three failures, one symptom, nothing in common.** Icons that *appear and do
> nothing* are the libappindicator problem below. Icons that *are not there at
> all* are one of the two above, and `systemctl --user list-units 'app-*'` tells
> them apart: `inactive dead` is the `OnlyShowIn` gate, `active` with an empty
> `RegisteredStatusNotifierItems` is the backend race.
>
> ```sh
> busctl --user get-property org.kde.StatusNotifierWatcher \
>     /StatusNotifierWatcher org.kde.StatusNotifierWatcher \
>     RegisteredStatusNotifierItems
> ```

### If a tray icon does not appear

`lib/tray.py` asks the session bus whether anything owns
`org.kde.StatusNotifierWatcher` and picks its backend from the answer. That
catches a missing host. It **cannot** catch a host that is present but not
displaying, and that is a real failure mode worth knowing:

> noctalia registers a watcher whether or not its bar has a tray widget. With
> `bar.default.end` set to `["network", "volume", "brightness", "battery",
> "session"]` and no `"tray"` entry, all four of this repo's trays registered
> correctly and none of them were visible — and neither were `nm-applet` or
> `blueman`, which had been invisible the whole time for the same reason.

Check the bar layout before debugging the tray:

```sh
noctalia config export full | grep -E '^\s+(start|center|end) ='
# "tray" must appear in one of start / center / end
```

**Where it sits here.** The tray is in `start`, at the left of the bar:

```toml
# ~/.local/state/noctalia/settings.toml
[bar.default]
start  = [ "launcher", "wallpaper", "workspaces", "tray" ]
center = [ "control-center", "clock", "keybinds" ]
end    = [ "network", "volume", "brightness", "battery", "session" ]
```

It was in `end`, which put this repo's three icons plus noctalia's own five
widgets into one crowded right-hand cluster. Moving the whole `tray` widget
across splits it by owner — this repo's icons on the left, noctalia's own on
the right — rather than interleaving two sets of things that are configured in
different places.

`settings.toml` is noctalia's, not this repo's: edit it and run
`noctalia msg config-reload`, which applies the change without a restart. **Two
window rules follow the tray's side of the bar** — `tray-menu.kdl` and
`nightlight-panel.kdl` both pin to `top-left` so a menu opens under the icon
that opened it. Move the tray back to `end` and both want `top-right` again;
each file says so in a comment.

### Why the tray is hand-written and not libappindicator

The first version of this used `libappindicator`, which registered fine and was
completely inert: the icons appeared in noctalia's bar and clicking them did
nothing. Introspecting the published item explains it in one line:

```
methods:    ['Scroll', 'SecondaryActivate', 'XAyatanaSecondaryActivate']
properties: []
```

No `Activate`, no `ContextMenu`, and no `ItemIsMenu`. The SNI spec says a host
should open the item's menu on left click when `ItemIsMenu` is true and call
`Activate()` otherwise — so with the property absent, noctalia called
`Activate()`, the method did not exist, and nothing happened. This is
libappindicator's long-standing gap; hosts that work with it do so by
special-casing it.

`lib/sni.py` serves the item and its DBusMenu directly instead, which fixes it
at the source and drops the dependency. Two things fall out of that:

- **Every method a host might call exists.** `Activate`, `ContextMenu`,
  `SecondaryActivate` and `Scroll`, plus the full property set.
- **A drawn icon goes over the wire as pixels**, in `IconPixmap` (ARGB32,
  network byte order — the channel reorder is the difference between a correct
  icon and one with red and blue swapped). No icon theme can contain a picture
  this repo draws with cairo.

Callers still build a `Gtk.Menu`; `sni.menu_from_gtk` flattens it, preserving
labels, separators, checkmarks, radio state and sensitivity.

**Rebuilding a menu is not free.** Every rebuild bumps the DBusMenu revision
and emits `LayoutUpdated`. The battery tray rebuilt on every poll and had
reached revision **10,255** before anyone looked; it now rebuilds only when the
profile, the AC-follow or the in-flight switch actually changes.

### `ItemIsMenu`, and the second time the clicks died

Serving the item properly was not the end of it. The obvious next step was to
set **`ItemIsMenu` true** — the spec says a host seeing it "should prefer
showing the menu or sending `ContextMenu()` instead of `Activate()`", which is
exactly what was wanted. The icons came back and were inert again.

What noctalia 5.1.0 does with the property true is neither of the two things
the spec offers. It stops calling `Activate()` and does not start doing
anything else:

| Measured with `ItemIsMenu` true | Result |
|---|---|
| two days of noctalia's own log | no `GetLayout`, no `AboutToShow`, no popup, no menu of any kind |
| D-Bus capture across a full tray restart | 0 calls on `com.canonical.dbusmenu` |
| D-Bus capture, 156s, icons being clicked | 0 inbound method calls, on any interface |

It reads the item's properties happily — 28 `GetAll` round trips in one
25-second capture — and renders the icon from them. It simply never asks for
the menu, and never has. So the click had nowhere left to go: `Activate()`
suppressed by the property, and the served DBusMenu never once read.

**`ItemIsMenu` is false**, therefore, which puts `Activate()` back — the one
call noctalia is known to make — and `lib/menu.py` answers it by drawing the
menu itself.

### `lib/menu.py`, the menu this repo draws when the bar will not

An ordinary GTK toplevel, floated by a niri rule, painted from noctalia's
palette through `lib/noctalia.py`. Same shape as the night light panel, and for
the same reason: a Wayland client cannot make an override-redirect window,
cannot place itself, and cannot grab the seat, so `Gtk.Menu.popup_at_pointer`
has no surface to anchor to — there is no toplevel behind a tray icon.

It is a fallback, and it knows it. `sni.Item.menu_fetched` latches on the first
`GetLayout` any host ever makes, and `Tray._on_sni_activate` draws nothing when
it is set, so a bar that renders the served menu gets to keep rendering it and
the two can never both appear. On noctalia the latch never sets and this is the
only menu there is.

Rows are buttons, not list rows, so they hover and focus like the menu entries
they stand in for. Clicking one fires the original `Gtk.MenuItem` through
`menu.activate`, which `lib/sni.py` also calls for a DBusMenu `Event` — one set
of semantics for both routes, including the rule that is not obvious: a radio
item is *selected*, never toggled, because toggling the one already active
would ask GTK to leave its group with nothing in it.

The menu closes on Escape, on focus-out, on picking something, and on a second
click of the icon that opened it.

### Files niri owns

`resolution`, `zen`, `brightness-tray`, `nightlight-panel` and `lib/menu.py`
each write a small `.kdl` file next to
`config.kdl` and add one `include` line to it. That line is the only edit ever
made to `config.kdl`, which stays hand-maintained; the included files are
machine-owned and rewritten wholesale.

| File | Written by | Holds |
|---|---|---|
| `~/.config/niri/outputs.kdl` | `monitor/resolution` | the chosen mode and scale per output |
| `~/.config/niri/zen.kdl` | `zen/zen` | the exit button's floating placement |
| `~/.config/niri/brightness-panel.kdl` | `brightness/brightness-tray.py` | the slider panel's floating placement |
| `~/.config/niri/nightlight-panel.kdl` | `nightlight/nightlight-panel.py` | the night light panel's floating placement |
| `~/.config/niri/tray-menu.kdl` | `lib/menu.py` | the tray menus' floating placement — one rule for all three, matched on the window title |

The touchpad is configured directly in `config.kdl`'s
`input { touchpad { ... } }`, which is the niri-side equivalent of
`/etc/X11/xorg.conf.d/72-touchpad-robin.conf` — including `dwt`
(disable-while-typing) and `disabled-on-external-mouse`, the latter replacing
the `touchpad-auto.sh` poller with a compositor feature.

### One dependency, on the niri side only

Nothing here ships a Wayland gamma tool and Arch installs none by default.
Until one is present `nightlight` reports the problem and refuses rather than
lying about what is on screen:

```sh
sudo pacman -S gammastep          # or wlsunset
yay -S wl-gammarelay-rs           # best: retunes over D-Bus, no respawn
```

`gamma-apply` auto-detects whichever is installed, preferring the one that does
not need a restart per change. The X11 path needs `redshift`, which is already
installed.

**What did not come across:** the two LightDM greeter hooks. greetd runs
noctalia's own greeter, which reads nothing in `/etc/lightdm`, so both
installers refuse under greetd (`FORCE_LIGHTDM=1` overrides) and say so rather
than writing config nothing reads. The niri-side replacement is
`noctalia msg greeter-sync`, which `monitor/resolution` calls on every mode or
scale change. Under LightDM the old hooks still work.

---

## `art/`

Where the wallpaper and the boot splash get their pictures — one choice, two
consumers.

| File | Role |
|---|---|
| `artwork` | The selector. `artwork <folder>` rotates through that folder, `artwork <file>` holds one picture, `artwork none` hands the desktop back to noctalia and makes the boot black, `artwork pick` opens a chooser, `artwork refresh` re-renders after you add images, `artwork next` / `prev` / `random` move the rotation along by hand and `artwork which` prints the picture on screen. Run as yourself. |
| `install-plymouth.sh` | The boot half: builds the plymouth theme from whatever `artwork` staged, adds the mkinitcpio hook, and rebuilds the UKI. Needs `sudo`. `--undo` removes it. |
| `install-greeter-sync.sh` | One polkit rule, so noctalia can push the wallpaper to its greeter without a password prompt. Half of that fix; `artwork` writes the other half. `--undo` removes it. |
| `fetch-ukiyoe.py` | One optional source: public-domain Japanese woodblock prints from the Met's open-access API. Nothing depends on it — it just fills a folder that `artwork` can then be pointed at. |

```bash
artwork ~/Pictures/wallpapers     # rotate through a folder
artwork ~/Pictures/one.jpg        # hold one picture
artwork none                      # noctalia's default, black boot
sudo bash art/install-plymouth.sh # apply the boot half

artwork next                      # the next picture, now
artwork prev                      # the one before
artwork random                    # any of them
artwork which                     # the path on screen, bare
xdg-open "$(artwork which)"       # …so it composes
```

`next`, `prev` and `random` check noctalia's `wallpaper.directory`, not
artwork's own record, because that is what noctalia steps through: a held file
and `none` both leave it empty and get told so, while a folder chosen from
noctalia's own settings window steps just as well as one chosen here. They
nudge the rotation without resetting its timer, and print where they landed —
including when a folder of one picture means they landed where they started, so
a no-op does not look like a failure.

`which` prints the path and nothing else on stdout, and exits 1 with its
complaint on stderr, so `$(artwork which)` is either a file or empty. With more
than one screen, `--output eDP-1` (any connector name niri knows) narrows any of
the four to one of them; without it they act on all screens, and `which` reports
noctalia's default.

### Three settings, and where they live

| Flag | What it sets | Values |
|---|---|---|
| `--fit MODE` | how a picture meets a screen it does not match | `center` `crop` `fit` `stretch` `repeat` `span` |
| `--background COLOUR` | what shows where the picture does not reach | `#rrggbb`, `#rrggbbaa`, `#rgb`, a noctalia palette role such as `surface`, or `''` for none |
| `--every SECONDS` | how long each picture stays up | whole seconds (5 or more), or `off` to hold one |

They can ride along with a source, or be given on their own to change just that
and leave the source alone:

```bash
artwork ~/Pictures/art --fit fit --background '#101010' --every 600
artwork --every 30                # just the interval
artwork --fit stretch             # just the fit
artwork                           # print everything that is set
```

**There is no artwork config file.** All three live in noctalia's
`settings.toml` — `wallpaper.fill_mode`, `wallpaper.fill_color` and
`wallpaper.automation.interval_seconds` — because a second copy of `fill_mode`
is a second thing to disagree. `artwork` edits that file **one key at a time**,
never by block, and refuses the write unless it still parses, nothing outside
the keys it named has moved, every change actually landed, and
`noctalia config validate` accepts the result. The previous file is kept beside
it as `settings.toml.artwork-bak`.

The boot splash honours the same two: `--fit` picks how each picture is fitted
to 1366x768, and `--background` fills what is left over. A palette role has no
meaning outside noctalia, so when the background is one of those the splash
keeps its own treatment — a blurred, darkened copy of the picture behind itself,
which beats flat black for a portrait picture on a landscape screen.

> **The block rewrite that ate noctalia's own tables.** Until now `artwork`
> replaced the whole `[wallpaper]` block, scanning to "the next line starting
> with `[`" to find the end. noctalia indents its sub-tables by four spaces, so
> that scan stopped at none of them and `[wallpaper.default]`,
> `[wallpaper.last]` and `[wallpaper.monitors.*]` went every single time. That
> is what the note below used to blame on noctalia. It was this.

### One choice, two consumers

The wallpaper and the boot splash want the same pictures and take them in
completely different ways:

- **Wallpaper** — noctalia already does folders, rotation and transitions, so it
  is pointed **straight at your folder**, not at a copy. Adding a file to the
  folder is the whole of adding a wallpaper; nothing here watches anything.
- **Boot splash** — plymouth reads its images out of the initramfs, which is
  built once and then frozen, so it cannot point at anything. Pictures are
  rendered to 1366x768 PNG8 and baked in. That is why the wallpaper half is
  instant and the boot half needs root and a rebuild.

**One file is not a folder of one.** `artwork <file>` clears
`wallpaper.directory`, turns `wallpaper.automation` off and sets that one
picture through `noctalia msg wallpaper-set`, then reads it back with
`wallpaper-get` to check it took. Pointing the rotation at the file's *parent*
would have been the lazy version and would have quietly picked up every other
picture in it.

`none` is not "no wallpaper". It hands the desktop back to noctalia's own
default and makes the boot splash plain black.

> **`artwork none` also sets the default wallpaper explicitly**, with
> `noctalia msg wallpaper-set /usr/share/noctalia/assets/noctalia-wallpaper.png`,
> rather than only clearing `wallpaper.directory` — so the desktop changes when
> you run the command instead of at some later reload. This used to be here for
> a different and wrong reason: `[wallpaper.default]` and `[wallpaper.last]` did
> disappear whenever the directory was cleared, and noctalia was blamed for it.
> The block rewrite above was deleting them. It no longer does, and they survive
> `artwork none` now.

### The sequence

```
Visor          its own background
black          kernel load and early initramfs
Arch Linux     the UKI's embedded splash, drawn by systemd-stub
black          or your artwork — plymouth, until the greeter draws
greetd
```

Both black stretches used to be console text. They are black rather than another
picture because neither can show one: the first is before any userspace exists,
and the second *is* plymouth, which is where an artwork goes if you want one.

**The Arch logo stays.** An earlier version replaced it with an artwork; the UKI
splash is baked into the `.efi` at build time and cannot rotate, so it was the
one fixed picture in a scheme otherwise about variety — and it is the one moment
in the boot that says which distribution this is.

### Two things that were still printing

**`systemd.show_status=auto` was making it worse, not better.** It was chosen so
status would come back by itself on a failed or slow boot. But systemd already
treats `quiet` as `show_status=no`, so naming the option explicitly *overrode*
what `quiet` had given and switched part of the output back on. It is `false`
now. `auto` is a louder setting than saying nothing at all, which is the
opposite of how it reads.

**plymouth was clearing the screen a second early.** `plymouth-quit.service` is
ordered after `multi-user.target` and before nothing in particular, so it tore
the splash down the moment the target was reached — measured at 22:46:09.438,
with greetd starting 15ms later and its compositor taking about a second more to
draw. That gap was a bare VT. `install-plymouth.sh` now drops in

```ini
[Service]
ExecStart=
ExecStart=-/usr/bin/plymouth quit --retain-splash
```

which leaves the final frame on the framebuffer instead of clearing it, so it
holds until the greeter's compositor takes the display.

### The greeter sync, and why it kept asking for a password

noctalia copies the current wallpaper and palette to its own greeter, through a
root helper behind a polkit action. The sync runs by itself whenever the
wallpaper changes, so a password dialog fired at moments nobody asked for
anything:

```
22:55:25.659  panel manager: opened "polkit"
22:55:50.579  greeter sync failed: Connection timed out
22:55:50.728  panel manager: closing "polkit"
```

A prompt nobody was looking at, timing out 25 seconds later into the failure
notification.

The shipped policy for `org.noctalia.greeter.apply-appearance` is `auth_admin`
on all three of `allow_any`, `allow_inactive` and `allow_active`, so that looked
like the whole answer, and `install-greeter-sync.sh` granted exactly that action
to exactly this user. **The prompts carried on**, because that action was never
the one being asked for.

noctalia does not run the helper directly. It runs it through whichever
privilege wrapper it finds, and it looks for `run0` before `pkexec`. run0 does
its work by asking systemd for a transient unit, so the action that actually
reaches polkit is `org.freedesktop.systemd1.manage-units`. polkitd's own log,
at exactly the 1800-second rotation interval:

```
16:35:58  FAILED to authenticate ... org.freedesktop.systemd1.manage-units
17:05:58  FAILED to authenticate ... org.freedesktop.systemd1.manage-units
```

Half an hour apart, to the second. That is the wallpaper rotating.

So the fix is two halves, and either alone does nothing useful:

| Half | What | Root? |
|---|---|---|
| `install-greeter-sync.sh` | one polkit rule granting `org.noctalia.greeter.apply-appearance` to one named user | yes, once |
| `artwork` | `[shell.greeter_sync] privilege_command = "pkexec"`, written by any `artwork` command that touches settings — the smallest is `artwork refresh` | no |

`pkexec` sends the call through the action the policy — and that rule — is
about. With both in place the sync is silent:

```
22:24:58  pkexec[35604]: pam_unix(polkit-1:session): session opened for user root by robin
```

pkexec ran the helper, `/var/lib/noctalia-greeter/` was written, and polkitd
logged nothing at all. No agent, no dialog, no password.

> **Why not just grant `manage-units`?** It would have worked, and it would have
> meant granting the account permission to start, stop and restart every unit on
> the system — the whole of root — for the sake of a picture on the login
> screen. The narrow action is the one worth having.

> **What the narrow one costs.** The login screen's appearance can then be
> changed without a password by anything running as you. The helper is a fixed
> path named in the policy, so it is not a route to running something else as
> root, and the user granted it is already in `wheel` — a far larger grant. On a
> shared machine, skip it: set `[shell.greeter_sync] auto_sync = false` and let
> the greeter keep its own look. `artwork` leaves the wrapper alone when the
> sync is off, because there is then nothing to make silent.

`artwork` with no arguments says which of the two are in place, asking polkit
with `pkcheck` rather than looking for the rule file — `/etc/polkit-1/rules.d`
is not readable as a user, and `pkcheck` without `--allow-user-interaction`
answers without opening a dialog of its own.

---

## `boot/`

| File | Role |
|---|---|
| `install-quiet-boot.sh` | One-shot `sudo` install of five kernel command-line flags, plus the `mkinitcpio` run that bakes them into the UKI. `--undo` takes them back out. |
| `install-quiet-session.sh` | The two things that still print after the kernel goes quiet: `pulsar.service`'s restart loop, and the deprecation warning `niri-session` provokes. `--undo` removes the session shadow. |

The boot chain here is **firmware → Visor → UKI → greetd on VT 1 → niri**.
Visor is a third-party graphical boot manager built from a GitHub checkout in
`~/.cache/visor/source`, not an Arch package; its config lives on the ESP at
`\EFI\visor\boot.conf` and nothing in this repo touches it.

### The console writes over the middle of that

`/etc/kernel/cmdline` had no `quiet` and no `loglevel`, so the console log level
was the compiled-in default and every kernel message at warning or below went to
the screen — **129 of them** on the boot this was measured on. systemd added its
`[ OK ]` column on top, because `ShowStatus` defaults to yes.

So Visor drew its background, handed over, and the kernel painted text across it
until greetd started a compositor. That text *was* the gap between Visor and
niri. The five flags:

| Flag | Stops |
|---|---|
| `quiet` | informational kernel chatter. Errors still print. |
| `loglevel=3` | the level `quiet` implies, stated so it survives `quiet` being removed: err and worse still reach the screen, warnings no longer do |
| `udev.log_level=3` | udev's coldplug chatter, which `quiet` does not cover because it is userspace |
| `vt.global_cursor_default=0` | the blinking block cursor sitting there until greetd draws |
| `systemd.show_status=false` | the `[ OK ]` column, and every status line systemd would print |
| `splash` | **the one that mattered.** See below |

### `splash` was missing, and that was the whole thing

Three rounds went into silencing sources — kernel `loglevel`, udev, systemd
status, and finally `console=tty3` to move the console off the screen entirely.
The screen looked the same every time, because it was the wrong question.

plymouth was installed, hooked, running and themed. From `plymouth(8)`:

> In order for the configured default plymouth theme to be loaded during boot,
> the option `splash` … must be provided at the kernel command line. **Without
> this command line option, plymouth will default to showing detailed boot
> output.**

There was no `splash`, so plymouth was in its **details** mode — the mode whose
entire job is to render the boot log as scrolling text. *The text on screen was
plymouth drawing it.* That is why quieting the sources kept not working:
plymouth reads the log, not the console, so moving the console away from the
screen changed nothing about what plymouth was painting.

Two smaller errors came out of the same round:

- **`console=tty3` made it worse, not better.** `/sys/class/tty/console/active`
  came back as `tty3`, so the kernel had moved the console to a VT of its own
  and the boot log was being *shown* there rather than hidden. It is stripped on
  every run now, by the `REMOVE` list.
- **`udev.log_level=3` is the wrong prefix for the initramfs.** `rd.udev.log_level=3`
  is the one that applies there; the bare form only covers udev after the
  switch-root. Both are set.

The lesson worth keeping: when a splash screen is installed and text still
appears, check whether the splash is *rendering the text itself* before
silencing anything else.

### The two [ERROR] lines### The two [ERROR] lines — `pulsar.service`

```
pulsar.service: Failed to load environment files: No such file or directory
pulsar.service: Failed to spawn 'start' task: No such file or directory
Failed to start Pulsar.
pulsar.service: Scheduled restart job, restart counter is at 1.
```

An enabled system unit for a Node server in `/srv/pulsar` that is not there any
more, with `Restart=always` — which is why *two* ERROR lines go past rather than
one. `install-quiet-session.sh` stops it, disables it, clears its failed state
and moves the unit aside to `pulsar.service.removed-<stamp>`. Nothing under
`/srv` is touched: the thing that tries to start it goes, the project does not.

### The orange deprecation, take two

The first one was `vm.laptop_mode`, fixed above. What was left says:

```
Calling import-environment without a list of variable names is deprecated.
```

From `/usr/bin/niri-session`, line 35:

```sh
# Import the login manager environment.
systemctl --user import-environment
```

Bare, no variable list, which systemd now warns about. It is upstream niri's
script, not anything configured here, and it runs in greetd's session context —
whose stderr goes to the VT and **not** to the journal, which is why the line is
visible on screen and `journalctl -b` has never held a copy of it.

Editing `/usr/bin/niri-session` would last until the next `pacman -Syu`. So the
session entry is shadowed instead: XDG data dirs are searched in order and
`/usr/local/share` comes before `/usr/share`, so a `niri.desktop` of the same
name there is the one the greeter reads. It runs the same script with
`SYSTEMD_LOG_LEVEL=err` in front.

The variable is set for the session process only, so `systemctl` still warns at
you normally in a terminal — which is the half of that behaviour worth keeping.

> If the greeter ends up listing niri **twice**, it is not de-duplicating the two
> session directories by filename the way the XDG spec says to. `--undo` removes
> the shadow.

### What this does not do

- **Visor's menu and its timeout** are a preference, not a bug. `timeout=0`
  boots the default entry without drawing the menu at all, and `autoboot=1`
  skips it while still letting a held key interrupt — both in `boot.conf`. Left
  alone: a bootloader is the one thing here that can make the machine unbootable
  if it is edited on a guess.
- **The boot splash itself** belongs to `art/`, which installs plymouth and the
  artwork it shows. This folder only stops the console writing over it.

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

**The holder needs its own systemd unit, and that is not a detail.**
`nightlight-auto.timer` runs `nightlight-auto` as a `Type=oneshot` service, and
systemd's default `KillMode=control-group` means that when the main process of
a oneshot exits, everything still in that unit's cgroup is killed. A
`setsid gammastep &` from inside it is in that cgroup. From the journal:

```
22:46:55.484  Starting Set night light intensity based on time of day...
22:46:55.872  Finished Set night light intensity based on time of day.
```

388 ms, and then the holder was shot. The compositor restores the original ramp
the moment the client holding `wlr-gamma-control` goes away, so the screen went
warm for about half a second on every tick and snapped back to neutral. It
looks exactly like something else fighting over gamma, and nothing was.

`setsid` does not help — it changes the session and process group, neither of
which is the cgroup. `gamma-apply` launches the holder with
`systemd-run --user --collect --unit=...` instead, which puts it in a unit of
its own that nobody cleans up. Each launch gets a unique unit name so the two
can be told apart during a swap.

### Why the swap is old-first, and not new-first

The obvious swap order is to start the replacement before killing the old
holder, so the compositor never sees a moment with nobody holding the ramp.
That was the order here, and it is not possible: **`wlr-gamma-control` is
exclusive**, one client per output. Ask for a control another client already
holds and the compositor answers `failed`, which `gammastep` reports as

```
Warning: Zero outputs support gamma adjustment.
Warning: 1/1 output(s) do not support gamma adjustment.
```

— and then never retries. Not when the old holder exits, not ever. Measured: a
second `gammastep` was still silent six seconds after the first was stopped.

So the old order produced exactly the flash it was written to prevent, except
permanent — new holder refused, old holder killed, nobody holding the ramp,
screen at neutral until some later call happened to find the control free. It
worked on alternate calls, which is what made it look intermittent rather than
broken:

| Call | New holder | Old holder | Screen |
|---|---|---|---|
| 1 | refused (2 already holds) | killed | **neutral** |
| 2 | takes it (1 holds nothing) | killed | correct |
| 3 | refused | killed | **neutral** |

Stopping first costs a real gap, for as long as the replacement takes to start
— about a tenth of a second, once — instead of a coin flip between correct and
neutral. `systemctl stop` is synchronous, so the control really is free by the
time the new one asks.

**The way to have no gap at all is `wl-gammarelay-rs`**, which retunes over
D-Bus and never restarts anything: no holder swap, so no gap to close. That is
why it is first in the backend list.

Installing it is the whole of the change — `gamma-apply` re-picks the backend on
every call. Three things had to be right for that to be true, and only the
first two were:

- **The old `gammastep` holder is retired when the backend changes.** The gamma
  control is exclusive, so a holder left over from the previous backend would
  refuse the new one and the install would look like it had done nothing.
  `gamma-apply` compares the backend against `gamma.backend` and stops the
  recorded holder when it differs.
- **The relay daemon gets its own systemd unit.** `relay_start` used
  `setsid … &`, which is the exact pattern the cgroup note above explains does
  not survive a `Type=oneshot` caller — and `nightlight-auto` is one. That bug
  could only ever have shown up once a relay backend was installed: started from
  a terminal or the tray it works, started by the five-minute tick it dies
  ~400 ms later. It now goes through `systemd-run --user` like the respawn
  holders, with a fixed unit name since there is only ever one relay.
- **The interface name is not the bus name.** This one shipped broken and was
  found the only way it could be — by installing the backend and watching the
  screen stay neutral.

  `rs.wl-gammarelay` is the *bus* name. D-Bus interface names may not contain a
  hyphen, so the same string is an illegal *interface* name, and the call was
  passing it as both:

  ```sh
  busctl --user set-property rs.wl-gammarelay / rs.wl-gammarelay Temperature q 3700
  #                          ^ bus name, fine   ^ illegal as an interface
  # Invalid interface name: rs.wl-gammarelay
  ```

  The interface is `rs.wl.gammarelay`, with dots. Worse than the typo was the
  `2>/dev/null` on that line, which threw away the one sentence that explained
  it: what reached the user was a backend that reported failure for no stated
  reason, and a screen sitting at 6500 K. The redirect is gone — a D-Bus error
  from the only call that sets the temperature is worth reading.

  Verify with `busctl --user get-property rs.wl-gammarelay / rs.wl.gammarelay
  Temperature`, which should echo whatever `nightlight` last asked for.

### One swap at a time, newest value wins

A respawn is a process launch, a settle and a synchronous `systemctl stop`, so
it costs the better part of a second. The panel applies live while the slider
is dragged, coalesced to one call per 40 ms — right for `wl-gammarelay-rs`,
where an apply is a D-Bus property write, and hopeless for a respawn backend: a
two-second drag asks for fifty of them, each killing the holder the last one
started, all interleaving.

`gamma-apply` therefore takes an `flock` around the swap. The first caller
holds it and everyone behind simply writes the temperature they want to
`gamma.want` and exits; the holder re-reads that file after each apply and goes
round again if it changed. The last value asked for is always the one that ends
up on screen, and the values nobody is looking at any more are skipped rather
than queued. Measured on a simulated drag — 30 applies, 40 ms apart:

| | Before | After |
|---|---|---|
| `gammastep` launches | 30, overlapping | 8, sequential |
| refusals | ~15 | 0 |
| holders left running | several | 1 |
| final temperature | wherever the race ended | the last one requested |

None of that applies on `wl-gammarelay-rs`, which is the point of preferring
it: the same 30-apply drag spawns nothing at all, restarts nothing, and lands
on the last value asked for. A single change costs **11 ms**, against ~180 ms
for a `gammastep` swap.

> **This needs a package.** Nothing here ships a gamma tool and Arch installs
> none by default, so until one is present `nightlight` reports the problem and
> refuses rather than lying about what is on screen:
> `sudo pacman -S gammastep`, or `yay -S wl-gammarelay-rs` for the one that
> retunes over D-Bus without respawning anything — see *Why the swap is
> old-first* for what that buys and why installing it needs nothing else.
>
> Symptom before the port was noticed: `nightlight-auto` ran every five minutes
> and logged ``RANDR Query Version' returned error -1 / Initialization of randr
> failed`` each time, having silently done nothing since the move to Wayland.

| Script | Role |
|---|---|
| `nightlight` | The CLI and the single owner of state. `nightlight` prints the current value, `nightlight 60` sets it, `nightlight --mode [auto\|manual]` switches scheduling on or off, `nightlight --print` gives the bare percentage for scripts. |
| `nightlight-auto` | Drives intensity from the clock — the curve below. Run every 5 minutes by a systemd timer. `nightlight-auto --print` writes the intensity that should be on screen to stdout and changes nothing. |
| `nightlight-tray.py` | The tray icon. Automatic on/off, ten steps, and the door to the slider. |
| `nightlight-panel.py` | The slider, as a floating window in noctalia's colours. |
| `nightlight-gui` | Now a one-line launcher for the panel, kept because the keybinding and the `.desktop` point at the name. |
| `gamma-apply` | Owns "the screen is at K right now". Picks a backend — `wl-gammarelay-rs`, `gammastep`, `wlsunset`, or `redshift` on X11 — and keeps it alive. Swaps holders old-first, because `wlr-gamma-control` is exclusive, and serialises swaps behind an `flock` so a slider drag cannot interleave them. |
| `nightlight-login` | Applies whatever should be on screen as the session comes up, from XDG autostart — the scheduled value in auto mode, the recorded one in manual, both via `nightlight-auto --print`. Twice, three seconds apart — the culprit changed from xfsettingsd's display pass to noctalia, which implements `wlr-gamma-control` itself and can take the ramp while starting up. It used to run `nightlight-auto` itself, which left manual mode neutral after a reboot: the Wayland ramp dies with the session, and in manual mode `nightlight-auto` applies nothing by design, so nothing ever put the hand-set value back. |
| `nightlight-greeter` | **Inert.** Was the same for the LightDM login screen. greetd is the display manager now. |
| `install-nightlight-greeter.sh` | Refuses and explains, unless `FORCE_LIGHTDM=1`. |

### The curve

```
80% ┤        ╭─────────────────╮
    │       ╱                   ╲
    │      ╱                     ╲
40% ┤     ╱                       ╲
    │    ╱                         ╲
 0% ┼───╯                           ╰────
    16:00  19:00          05:00  06:30
```

| Window | Intensity |
|---|---|
| 06:30 → 16:00 | 0% — neutral. The screen is not tinted at all during the day. |
| 16:00 → 19:00 | rising, 0% → 80% |
| 19:00 → 05:00 | 80% (3300K), across midnight |
| 05:00 → 06:30 | falling, 80% → 0% |

Four times and one ceiling, all constants at the top of `nightlight-auto`, and
that file is the only place the curve is written down — the login screen asks it
for a number rather than carrying a copy, and the tray and the panel read the
five `NAME=number` lines straight out of it (`schedule()` in
`nightlight-panel.py`) for the "night" step, the slider's "night" mark and the
schedule tooltip.

> **That used to be untrue.** The tray's tooltip and step list and the panel's
> mark each carried their own 70, so raising the peak meant four edits and
> missing one meant a menu labelling the wrong value "night". Now changing
> `PEAK` is the whole change: a peak that is not a multiple of ten gets a step of
> its own, and a running tray picks it up at its next 30-second poll.

`PEAK` is 80, landing at 3300K. It was 70 (3700K) and was raised for a warmer
night; the last 20%, down to the 2500K deep amber at 100, stays out of the
schedule. It caps the *schedule*, not the control: `nightlight 100` by hand
still goes all the way.

**The two ramps are eased, not straight.** The endpoints are exactly the times
above — "0% at 06:30" and "80% at 19:00" hold literally — and the easing only
decides the shape between them. A straight ramp has a corner at each end, where
the rate of change goes from nothing to its full value in a single tick, and the
corner is the part the eye catches: the screen looks still, then visibly starts
moving. Smoothstep leaves and arrives at zero rate, so there is no moment to
point at.

### Auto vs. manual

Two modes, and setting a value by hand picks one:

- **auto** — `nightlight-auto` drives the intensity from the clock.
- **manual** — the schedule does not run; the value stays exactly where it was
  put, until automatic is switched back on.

Setting any value by hand — the slider, a menu step, `nightlight 60`, a scroll
over the icon — switches to manual. That is the whole of the override mechanism.

It replaces one that parked a hold until the next schedule boundary and then
handed control back on its own. The hold went because it answered a question
nobody was asking: a slider gets moved because the schedule's idea of the right
colour is wrong for what is on screen, and that reason does not expire at 19:00.
What the hold actually did was put the value back some hours later, usually
unnoticed — which is the one behaviour a manual override exists to prevent.

Switching automatic back on applies the scheduled value immediately rather than
leaving the screen on the manual one until the next five-minute tick; a switch
you flip that visibly does nothing reads as broken.

### The tray and the panel

The menu is served over DBusMenu and drawn by **noctalia**, so it is in
noctalia's theme without this repo knowing anything about themes. It carries
everything a menu can express: the Automatic checkmark, ten steps, and an item
that opens the panel.

The panel is the part a menu cannot be. DBusMenu has no slider — a `Gtk.Scale`
packed into a `Gtk.MenuItem` does not survive being flattened into menu data and
arrives as an empty row — so fine adjustment is a window, floated top-right by a
niri rule and dismissed on Escape, on Close, or on focus-out.

Being a window means GTK draws it, and **GTK has never heard of noctalia**: left
alone it comes up in default Adwaita light, a white rectangle under a dark bar.
noctalia ships a GTK template that writes the active palette to
`~/.config/gtk-3.0/noctalia.css` as `@define-color` lines; `install-autostart.sh`
enables it, and `lib/noctalia.py` reads the colours out of that file and watches
it, so the panel matches the bar and follows a scheme change without being
reopened. The template covers libadwaita's colour names, which GTK3's own
stylesheet mostly ignores — so importing it is not by itself enough to theme the
window. What it is enough for is *telling us the colours*, which the panel then
uses in a stylesheet of its own. noctalia owns the palette; this repo owns the
widgets it paints with it. With the template not enabled the panel renders in
stock GTK, which is a cosmetic miss rather than a failure.

Dragging the slider applies every value it passes, because a night light you
cannot see while choosing it is not adjustable. What waits for the end of the
drag is the state write and the mode switch — otherwise one drag from 0 to 70
would run `gamma-apply` seventy times, and on the `gammastep` backend each of
those is a process launch.

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

Requires a gamma backend (`gammastep` here) — and `redshift` only for the X11
path. **`yad` is no longer needed**: the slider it used to draw is now
`nightlight-panel.py`.

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
   login screen, replacing the 309-line `monitor-greeter`. It goes through the
   same privilege wrapper as the wallpaper sync, so it is silent for the same
   reason and needs the same two halves — see
   [the greeter sync](#the-greeter-sync-and-why-it-kept-asking-for-a-password).

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
| `battery-tray.py` | The tray icon — which power profile is on — and the menu they are switched from. |
| `power-profile` | The CLI and the single owner of profile state. `power-profile` prints the current profile, `power-profile performance` sets it, `power-profile --auto [on\|off]` gets or sets the AC follow, `power-profile --sync` applies whatever the AC state currently calls for. Runs as you; hands the sysfs writes to the helper below. |
| `power-profile-apply` | The privileged half — every write that needs root. Installed to `/usr/local/libexec/`, not run from here. |
| `install-power-profile.sh` | One-shot `sudo` install of the helper and its sudoers rule. |

`battery-tray.py` draws the **active power profile** as a level marker:

| Marker | Profile | Colour |
|---|---|---|
| `++` | Performance | violet |
| `+` | Default | the bar's own foreground |
| `-` | Battery Saver | teal |
| `--` | Extreme Battery Saver | teal |

Colour carries direction — violet for faster than stock, teal for slower — so
the two saving profiles read as a pair and neither reads as an alarm. Count
carries degree, at a fixed symbol size: one mark and two are the same size as
each other, so what changes is how many there are.

The marks are drawn as rectangles, not typeset. Scaling `+` and `-` as glyphs
to a common box cannot produce a set: a hyphen's ink is about a third as wide
and a tenth as tall as a plus sign's, so fitting either to the square makes the
other absurd — fit by width and the dashes come out as solid blocks, fit by
height and they disappear. Two rectangles with a shared length and thickness
have no such problem, and are crisper at 32px than hinted glyphs anyway.

**It used to draw the charge percentage**, with a bolt for charging, colour for
low and critical, and a bar along the bottom for the profile — four things
fighting over a 32px square, and the profile, the only one of them this tray
can change, got the smallest share. noctalia's own `battery` widget is already
on the bar and already shows charge, state and time, so the number here was a
second, worse copy of something two centimetres away. Hovering still gives the
state, time to full or empty, the battery's health against its design capacity,
and the profile — marked *automatic on AC* when the charger is what put it
there, so a machine sitting in performance nobody chose explains itself.

Either mouse button opens the menu.

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
| `noctalia-setting.py` | Reads and writes one boolean in noctalia's `settings.toml`, which is how the niri-side switches are reached — noctalia has no IPC command for them. Unused under XFCE, where xfconf is the store. |
| `20-lid-lock.conf` | `HandleLidSwitch=lock` plus the external-power twin. Installed to `/etc/systemd/logind.conf.d/`. The one lock trigger that is root-owned rather than a `nolock` switch. |
| `install-lid-lock.sh` | One-shot `sudo` install of that drop-in. Applies it with `systemctl reload systemd-logind`, which re-reads the config in place without dropping the seat. |

**The lock triggers are different objects in the two sessions**, so `nolock`
carries a table per backend and the generic on/off/snapshot/restore logic runs
over whichever is selected.

Under XFCE, five switches across two daemons — `xfce4-power-manager`'s
`lock-screen-suspend-hibernate` and the lid pair, plus `xfce4-screensaver`'s
`sleep-activation` and `saver-activation/enabled`. The lid pair is the one that
took longest to find, because closing the lid never slept the machine: it
locked directly, so none of the sleep-related switches ever got a say.

Under niri there are three, all noctalia's — niri does not lock, and logind
decides what the lid means.

| Setting | Default | Locks? |
|---|---|---|
| `lockscreen.lock_before_suspend` | `true` | **Yes — this is the one that catches you.** noctalia holds a logind sleep *delay* inhibitor (visible as "Lock before sleep" in `systemd-inhibit --list`) and raises the lock screen on the way down. Direct heir to xfce4-screensaver's `/lock/sleep-activation`. |
| `idle.behavior.lock.enabled` | `false` | Lock after an idle timeout. Ships off, so it costs nothing today — managed so that turning it on later cannot silently defeat nolock. |
| `idle.behavior.lock-and-suspend.enabled` | `false` | Same, with a suspend attached. |

`lockscreen.enabled` is deliberately left alone. It is the master switch, and
with it on `Super+Alt+L` still locks on demand while nolock is on — exactly the
reasoning that kept `/lock/enabled` untouched under XFCE.

**The lid pair has no equivalent and is root-owned rather than a `nolock`
switch.** `xfce4-power-manager` held a logind `handle-lid-switch` inhibitor and
decided for itself what a lid close meant, which is why it could be told "blank
the screen" instead of "lock" — and why it took so long to find, since closing
the lid never slept the machine and none of the sleep switches ever got a say.
Nothing in this session takes that inhibitor, so the lid belongs to logind again
and `HandleLidSwitch` is system configuration, not a per-user toggle. `nolock
status` prints the current lid action so the whole picture stays in one place.

> **Changed 2026-10-09: the lid locks instead of suspending.** logind's default
> is `suspend`, and `systemd-sleep` freezes the whole of `user.slice` on the way
> down — "Successfully froze unit 'user.slice'" lands in the journal immediately
> before "Performing sleep operation 'suspend'". That made the lid the **only**
> thing in this session that stopped a running process: `Super+Alt+L` just
> raises a lock screen and `Mod+Shift+P` just powers the output off, and neither
> touches a PID. So the rule now enforced is that locking and blanking leave
> work running and only an explicit sleep stops it. `nolock/20-lid-lock.conf`
> sets `HandleLidSwitch=lock` and `HandleLidSwitchExternalPower=lock`, so a lid
> close locks, blanks, and leaves the download running. `lock` is reachable
> because noctalia registers a logind session lock listener at startup and
> reports back through `SetLockedHint`; niri powers the internal output down on
> lid close by itself, so the screen-off half needed nothing.
>
> **The cost, accepted deliberately:** nothing sleeps this laptop on its own any
> more. `IdleAction` is logind's `ignore` default and all three noctalia idle
> behaviours are `enabled = false`, so there is no timeout left to fall back on
> — a machine shut in a bag stays awake, gets warm and flattens the battery.
> Suspending is a manual act now: the session menu, or `systemctl suspend`.
> `HandleLidSwitchDocked` stays at `ignore`, because docked means an external
> screen is attached and locking would interrupt work that is still visible.
>
> **And it costs `nolock` the lid.** "nolock on, shut the lid, open it straight
> back into the session" no longer works. `nolock` never could change the lid
> action — only the switches on the *sleep* path — and the lid is not on that
> path any more, so `lock_before_suspend` never gets consulted. The prompt now
> comes from logind's own `lock`, which reaches noctalia through the session
> `Lock` signal and is gated only by `lockscreen.enabled`, the master switch
> `nolock` deliberately leaves alone. `nolock on` still works as advertised for
> a real suspend. If waking without a password matters more than the lid
> leaving work running, the lid action is the thing to put back.

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

**The original bug is live under XFCE and impossible under niri**, and that is
worth being precise about, because the recovery script has to cover both.
On X11, `xfce4-screensaver` held a logind delay inhibitor on sleep so it could
raise the lock screen first, and locking meant `XGrabKeyboard` on the root
window. If `InhibitDelayMaxSec` elapsed first, logind suspended anyway and froze
`user.slice` mid-lock — and what came back was a screensaver stuck partway
through locking, still holding the keyboard grab, with no lock window on screen
to type into. Every keystroke went to that grab and vanished.

Under niri none of that is possible. There is no `XGrabKeyboard` and no root
window to grab: input routing belongs to the compositor, and a client that
wedges *loses* input rather than capturing everyone else's. niri keeps taking
keys no matter what any client does.

So `unstick-keyboard` does three things, the first only under X11:

0. **Break the screensaver's grab** by killing and restarting
   `xfce4-screensaver`, since X11 releases every grab a client holds the moment
   its connection drops. Skipped entirely under Wayland.

**And two that apply in both sessions:**

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

The X11 files in this folder configure the touchpad under XFCE and are still
what runs there. Under niri the compositor owns input directly, so the
equivalent is a block in `~/.config/niri/config.kdl` rather than anything here:

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

`72-touchpad-robin.conf`, `72-touchpad-robin.libinput.conf`,
`apply-touchpad.sh`, `switch-to-libinput.sh` and `touchpad-auto.sh` are the X11
implementation and still the right thing there. Under niri none of them run.
`touchpad-auto.service` has been disabled — it was restart-looping every 5
seconds against an X server that does not exist, 447 restarts deep by the time
it was found. `grab-watchdog.service`, which is not in this repo but was
looping for the same reason, was disabled with it.

### Smooth scrolling, and the half that is X11-only

The touchpad half is the config above. The other half is `~/.xprofile`
exporting `MOZ_USE_XINPUT2=1`, because on X11 pixel-precise scroll deltas only
reach a client that asked for XInput2 events, and Firefox does not ask unless
that is set — so scrolling feels stepped in the browser and smooth everywhere
else. It also switches on the rest of what APZ does with a real touchpad:
kinetic flicks, pinch zoom, swipe to navigate.

**Under Wayland the variable does nothing, and needs to do nothing.** There is
no core-protocol fallback to opt out of — `wl_pointer.axis` carries the delta
natively, and Firefox gets all of the above without being told to. So the file
matters in one session and is inert in the other, which is fine; it is only
read on the X11 path.

One catch on this machine: `~/.xprofile` was sourced by
`/etc/lightdm/Xsession`, and the display manager is greetd now. Whether it is
still read on an XFCE login depends on how greetd starts that session — check
with `env | grep MOZ_USE_XINPUT2` inside XFCE before assuming it took.

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

**The exit button is placed by the compositor, if you opt in.** A Wayland
client cannot position its own toplevel, so the corner the X11 build asked for
with `move()` is a niri window rule instead, in `~/.config/niri/zen.kdl`.

That used to install itself on every enter, which was wrong in one direction: a
config edited to remove zen grew the include back the next time zen ran, which
is a script overruling a decision about a file it does not own. **The include is
now the switch.** If `config.kdl` already includes `zen.kdl` the rule is kept
current; if it does not, zen writes nothing. Adding it is explicit:

```sh
zen --install-niri-rules
```

Without the rule zen still works — the exit button is tiled like any other
window rather than tucked in the corner, and `<Super>Z` still leaves zen.
`brightness-tray.py` follows the same rule for its slider panel.
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
| `~/.config/autostart/nightlight-tray.desktop` | `nightlight/nightlight-tray.py` |
| `~/.config/autostart/battery-tray.desktop` | `battery/battery-tray.py` |
| `~/.config/autostart/resolution-tray.desktop` | `monitor/resolution-tray.py` |
| `~/.local/share/applications/nightlight-gui.desktop` | `nightlight/nightlight-gui` |

The four `autostart` entries are **generated by `install-autostart.sh`**, which
is where the list of what starts with the session lives. Editing them by hand is
what broke them last time. `brightness-tray` and `nolock-tray` have no entry —
both are retired, see *Which trays run here*.
| `~/.config/niri/config.kdl` → `binds` | `nightlight-gui`, `nolock`, `zen`, `unstick-keyboard` — see the keybinding table below |
| `~/.bashrc` | puts `nightlight/` on `PATH` |

**XDG autostart works under niri** — `niri --session` activates
`xdg-desktop-autostart.target` and systemd generates an `app-*@autostart.service`
per `.desktop` file, so the trays start with the session exactly as they did
under XFCE, *provided the entry does not name a desktop*.

- Every entry carried `OnlyShowIn=XFCE;`, so the generator created each unit and
  then skipped it every boot (`Condition check resulted in ... being skipped`).
  That is the reason the icons were not on the bar. `install-autostart.sh`
  regenerates them without it — see *They register and never start* above.
- `touchpad-tweaks.desktop` was deleted outright — see `touchpad/`.

### Keybindings

Under XFCE these are xfconf custom shortcuts (`Super+N`, `Super+U`). niri has
no xfconf, so the niri session gets plain binds in `config.kdl` instead —
`Super+U` could not be kept there because niri already uses it for
`focus-workspace-down`.

| Chord | Runs | Was |
|---|---|---|
| `Mod+N` | `nightlight/nightlight-gui` → the night light panel | `Super+N` (unchanged) |
| `Mod+Alt+U` | `nolock/nolock toggle` | `Super+U` |
| `Mod+Shift+Z` | `zen/zen --pick` | `Super+Shift+Z` (unchanged) |
| `Mod+Z` | `zen/zen --exit` | `Super+Z` (unchanged) |
| `Mod+Ctrl+Shift+U` | `keyboard/unstick-keyboard --quiet` | a panel launcher |

> **None of these five are in `config.kdl` right now.** The table is the
> intended set, not the installed one — checked with
> `grep -nE '^\s*Mod\+' ~/.config/niri/config.kdl`, which finds none of them.
> Nothing in this repo adds them, and nothing should: `config.kdl` is
> hand-maintained, and the one edit the scripts make to it is the `include`
> line for a machine-owned file. Add them by hand from the table above; niri
> reloads on save.
>
> The same check says `zen.kdl` and `brightness-panel.kdl` are not included
> either, so `zen`'s exit button and the brightness panel open tiled rather than
> floating. That is the documented fallback, not a break.

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
| `~/.config/niri/nightlight-panel.kdl` | `nightlight/nightlight-panel.py` |
| `~/.config/niri/tray-menu.kdl` | `lib/menu.py` |

One file outside niri is edited by hand rather than by a script:
`~/.local/state/noctalia/settings.toml`, whose `[bar.default]` widget lists say
where the tray sits. Nothing here writes it — noctalia owns it and has no CLI
for widget order — so the change is applied with an edit plus
`noctalia msg config-reload`, and the two `.kdl` placements above follow which
side of the bar it names. See *If a tray icon does not appear*.

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
| `/etc/systemd/logind.conf.d/20-lid-lock.conf` | a copy of `nolock/20-lid-lock.conf` |
| `/usr/local/libexec/battery-power-profile` | a copy of `battery/power-profile-apply` |
| `/etc/sudoers.d/battery-power-profile` | the NOPASSWD rule naming that path |

> **Three copies are stale** and need `sudo` to refresh:
> `/usr/local/bin/oom-defender` (still the X11 focus-detection build),
> `/usr/local/bin/unstick-keyboard` (still the screensaver-grab build), and
> `/usr/local/libexec/battery-power-profile`, which is the one that actually
> runs and still contains the four `vm.laptop_mode` writes. Re-run
> `oom/install-oom-defender.sh`, `keyboard/install-keyboard-fix.sh` and
> `battery/install-power-profile.sh`.

### Inert leftovers

Harmless, and not doing anything in a niri session. Some of them are live
again under XFCE — the column says which:

| File | Why it is dead |
|---|---|
| `/usr/local/libexec/nightlight-greeter`, `/etc/lightdm/lightdm.conf.d/50-nightlight.conf` | LightDM is not the display manager; greetd is |
| `/usr/local/libexec/monitor-greeter`, `/etc/lightdm/lightdm.conf.d/60-monitor.conf`, `/etc/lightdm/lightdm-gtk-greeter.conf.d/60-monitor.conf` | same |
| `/etc/X11/xorg.conf.d/72-touchpad-robin.conf` | not read under niri; still the touchpad config for an XFCE session |
| `~/.xprofile` | inert under niri (`MOZ_USE_XINPUT2` is meaningless on Wayland); under XFCE it still matters, but was sourced by `/etc/lightdm/Xsession` and the DM is greetd now — verify it is read |
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
