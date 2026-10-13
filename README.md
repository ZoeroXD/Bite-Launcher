# Bite Launcher

A black, touch-first launcher for the Raspberry Pi 4 DJ rig. It hides the Raspberry Pi desktop, waits until a controller supported by BiteDJ is plugged in, and then shows a purple **Launch** button that starts BiteDJ with `~/bitedj/build/mixxx --fullScreen`, the same command as the BiteDJ desktop entry.

![Waiting for a controller](screenshots/1-waiting.png)
![Controller recognized](screenshots/2-recognized.png)
![Network details](screenshots/3-network.png)

## What's on screen

- **Black screen only.** No panel, menu, wallpaper or cursor. A black background starts first, so the Raspberry Pi desktop never shows.
- **Small tray, top right:** Ethernet icon, Wi-Fi signal and the clock. Tap the tray to see the Wi-Fi network, Ethernet status, IP address and hostname (handy for SSH or VNC), plus a **Switch to Pi Desktop** button.
- **Centre, no controller:** **No Controller Recognized** with "Plug in a DJ controller supported by BiteDJ". There is no button, so nothing can be launched.
- **Centre, controller plugged in:** **Controller Recognized**, the controller's name, and the purple **Launch** button (`#9d55e2`, the purple from the logo). Unplug the controller and the button goes away again.
- **Shark fin:** a dorsal fin in the logo's stripe order (charcoal, gray, red, orange, blue, purple, gray, charcoal) shaped from the logo's "D". The front edge uses the D's measured curve: a superellipse with exponent 2.9, 1.41 times as wide as it is tall. It also has the D's sharp top corner, its three-lobed bite on the back edge, and the logo's outline: a 2 px black keyline with about 2.5 px of white around it. It sits just above the bottom edge and glides slowly and steadily, Jaws-style, with no bobbing. It takes about 30 seconds to cross from left to right, pauses off screen, then turns around and comes back.

![Fin preview, sped up about 8 times](screenshots/fin-preview.gif)

## How controller detection works

The launcher reads every mapping installed with BiteDJ (`/opt/bitedj/share/mixxx/controllers/*.midi.xml`, `*.hid.xml`). That's 162 mappings in the current BiteDJ source. Every 1.5 seconds it compares them with:

- USB vendor and product IDs (`/sys/bus/usb/devices`)
- ALSA sound cards (`/proc/asound/cards`)
- ALSA MIDI clients (`/proc/asound/seq/clients`)

The Pi's own HDMI audio, headphone jack, USB hubs, "Midi Through" and mappings named only after a brand (such as "Numark") are ignored. The most specific match wins, so a DDJ-4000 is never mistaken for a DDJ-400. If you set `DJRIG_APP=mixxx`, stock Mixxx's mappings are checked as well.

Check what it sees over SSH:

```bash
python3 /opt/bitedj-launcher/bitedj_launcher.py --list-controllers
```

## Install on a running Pi

```bash
git clone https://github.com/ZoeroXD/BiteDJ-Launcher.git && cd BiteDJ-Launcher
./install.sh
sudo reboot
```

The installer:

1. Installs `python3-pyqt6` and `swaybg`. Qt is already on the Pi because BiteDJ uses it.
2. Copies the launcher to `/opt/bitedj-launcher` and adds `/usr/local/bin/bitedj-launcher`, which restarts the launcher if it ever crashes.
3. Adds the **Bite Launcher** shortcut to the app menu (`/usr/share/applications`) and to the desktop (`~/Desktop`), marked as trusted so it opens without a prompt.
4. Adds the launcher settings to `/etc/djrig.conf`.
5. In `~/.config/labwc/autostart`, comments out `djrig-launch` (which started BiteDJ straight away), adds a black `swaybg` background and starts `bitedj-launcher`. A backup is saved as `autostart.pre-launcher`.

To undo everything, run `./uninstall.sh`.

## Switch between Bite Launcher and the Pi desktop

Switching uses `bite-mode` from [BiteDJ-On-Off-Macro-with-FLX4](https://github.com/ZoeroXD/BiteDJ-On-Off-Macro-with-FLX4), so install that too. Once it's installed, you can switch any of these ways:

| From | To | How |
|---|---|---|
| Bite Launcher | Pi desktop | Tap the clock, then **Switch to Pi Desktop** |
| Bite Launcher | Pi desktop | **Ctrl+Alt+D** on a keyboard |
| Either | The other | Hold **both 4 BEAT/EXIT** buttons on the FLX4 for 8 seconds |
| Pi desktop | Bite Launcher | Double-click the **Bite Launcher** desktop icon, or open it from the app menu |
| Either | Either | Over SSH: `~/bin/bite-mode desktop`, `~/bin/bite-mode launcher` or `~/bin/bite-mode status` |

Going to the desktop closes the launcher and BiteDJ (BiteDJ gets 15 seconds to save and quit), then brings back the taskbar and desktop icons. Going back to the launcher stops the taskbar and desktop and opens the launcher full screen on black. The Pi always starts in launcher mode at boot.

If `bite-mode` isn't installed, **Switch to Pi Desktop** shows a short note saying so, and the **Bite Launcher** icon just opens the launcher.

## Bite Launcher shortcut

`bite-launcher.desktop` shows as **Bite Launcher** with the striped-fin icon, on the desktop and in the app menu. It runs `bite-launcher-open`, which switches back to launcher mode through `bite-mode` when it's installed, or simply opens the launcher when it isn't. Only one copy runs at a time, so opening it while the launcher is already on screen does nothing.

## Build it into the image

`mixxxpi64-ng-bitedj-launcher.patch` applies to [mixxxpi64-ng-bitedj](https://github.com/ZoeroXD/mixxxpi64-ng-bitedj):

```bash
cd mixxxpi64-ng-bitedj
git apply ../BiteDJ-Launcher/mixxxpi64-ng-bitedj-launcher.patch
```

It adds `python3-pyqt6` to the base packages, puts the launcher, icon and shortcut into the `03-kiosk` stage, starts the launcher instead of `djrig-launch` in labwc autostart, and adds the settings to `djrig.conf`.

## Settings (`/etc/djrig.conf`)

| Setting | Default | Meaning |
|---|---|---|
| `BITEDJ_APP` | `~/bitedj/build/mixxx` | What **Launch** runs (same as the BiteDJ desktop entry and the FLX4 hold macro). Use a full path to change it |
| `BITEDJ_APP_ARGS` | `--fullScreen` | Arguments passed to it |
| `DJRIG_APP` | (unset) | Used only when `~/bitedj/build/mixxx` doesn't exist, e.g. `bitedj` or `mixxx` on the image build |
| `DJRIG_BITEDJ_SCALE` | (unset) | Optional `QT_SCALE_FACTOR` for BiteDJ. Not set means the same scaling as the desktop entry |
| `DJRIG_QPA` | (unset) | Optional Qt platform for BiteDJ |
| `BITEDJ_LAUNCHER_AUTOLAUNCH` | `0` | `1` = start BiteDJ by itself about a second after a controller is recognized |
| `BITEDJ_FIN_SECONDS` | `30` | Seconds for the fin to cross the screen |
| `BITEDJ_CLOCK_FORMAT` | `%-I:%M %p` | Clock format (`%H:%M` for 24-hour) |
| `BITEDJ_CONTROLLER_DIRS` | (auto) | Mapping folders to search, separated by colons |

## Behaviour details

- **While BiteDJ runs,** the launcher stays behind it, stops its animation and network checks, and only does a light check every 1.5 s to see whether BiteDJ is still running, so it uses almost no CPU. When BiteDJ quits, the launcher screen is already there underneath, with no flash of the desktop.
- **If BiteDJ closes within 20 seconds with an error,** an orange note says so and Launch stays available.
- **BiteDJ started some other way** (the FLX4 hold-button macro, or SSH) is detected, and the launcher stays out of the way. The launcher, the hold macro and `bite-mode` all use `~/bitedj/build/mixxx`, so they always agree on whether BiteDJ is running.
- **Touch** works as mouse clicks. **Enter or Space** presses Launch. **Ctrl+Alt+D** switches to the Pi desktop. **Ctrl+Alt+Q** quits the launcher (the wrapper restarts it).
- **Fullscreen:** if `~/.config/labwc/rc.xml` has the kiosk rule that makes every window fullscreen, the launcher leaves it to that rule (asking again would switch it back out). Otherwise, as on the regular Pi desktop, it goes fullscreen by itself.

## Testing on a PC

```bash
pip install PyQt6
python3 bitedj_launcher.py --windowed --demo-controller     # pretend an FLX4 is plugged in
python3 bitedj_launcher.py --snapshot out.png --at 6         # render one frame
```

## Files

| File | Purpose |
|---|---|
| `bitedj_launcher.py` | The launcher (Python 3 + PyQt6, one file) |
| `bitedj-launcher` | Wrapper that restarts it if it exits, and stops when its labwc session ends (used by labwc autostart) |
| `bite-launcher-open` | What the Bite Launcher shortcut runs: switches back via `bite-mode`, or opens the launcher |
| `bite-launcher.desktop`, `bite-launcher.png` | Bite Launcher shortcut and icon |
| `djrig-launcher.conf` | Settings added to `/etc/djrig.conf` |
| `install.sh`, `uninstall.sh` | Install and remove on a running Pi |
| `mixxxpi64-ng-bitedj-launcher.patch` | Adds the launcher to the image build |
