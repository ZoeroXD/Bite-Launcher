# Bite Launcher

A black, touch-first launcher for the Raspberry Pi 4 DJ rig. It hides the Raspberry Pi desktop, waits until a controller supported by BiteDJ is plugged in, and then shows a purple **Launch** button that starts BiteDJ (or stock Mixxx).

![Waiting for a controller](screenshots/1-waiting.png)
![Controller recognized](screenshots/2-recognized.png)
![Network details](screenshots/3-network.png)

## What's on screen

- **Black screen only.** No panel, menu, wallpaper or cursor. A black background starts first, so the Raspberry Pi desktop never shows.
- **Small tray, top right:** Ethernet icon, Wi-Fi signal and the clock. Tap the tray to see the Wi-Fi network, Ethernet status, IP address and hostname (handy for SSH or VNC).
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
git clone <this repo> && cd BiteDJ-Launcher
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

## Bite Launcher shortcut

`bite-launcher.desktop` shows as **Bite Launcher** with the striped-fin icon. Use it when the Pi is on a normal desktop session, or over VNC or Pi Connect. Only one copy runs at a time, so opening the shortcut while the launcher is already on screen does nothing.

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
| `DJRIG_APP` | `bitedj` | App started by Launch: `bitedj` or `mixxx` |
| `DJRIG_BITEDJ_SCALE` | `1.6` | `QT_SCALE_FACTOR` for BiteDJ (same setting as `djrig-launch`) |
| `DJRIG_QPA` | `wayland` | Qt platform for the DJ app |
| `BITEDJ_LAUNCHER_AUTOLAUNCH` | `0` | `1` = start BiteDJ by itself about a second after a controller is recognized |
| `BITEDJ_FIN_SECONDS` | `30` | Seconds for the fin to cross the screen |
| `BITEDJ_CLOCK_FORMAT` | `%-I:%M %p` | Clock format (`%H:%M` for 24-hour) |
| `BITEDJ_CONTROLLER_DIRS` | (auto) | Mapping folders to search, separated by colons |

## Behaviour details

- **While BiteDJ runs,** the launcher stays behind it, stops its animation and network checks, and only does a light check every 1.5 s to see whether BiteDJ is still running, so it uses almost no CPU. When BiteDJ quits, the launcher screen is already there underneath, with no flash of the desktop.
- **If BiteDJ closes within 20 seconds with an error,** an orange note says so and Launch stays available.
- **BiteDJ started some other way** (the FLX4 hold-button macro, or SSH) is detected, and the launcher stays out of the way. The detection matches the binary set by `DJRIG_APP` or `/usr/local/bin/bitedj`. If your hold macro still points at `~/bitedj/build/mixxx`, set `DJRIG_APP` to that path so both use the same binary.
- **Touch** works as mouse clicks. **Enter or Space** presses Launch. **Ctrl+Alt+Q** quits the launcher (the wrapper restarts it).
- **Fullscreen:** the kiosk `rc.xml` already makes every window fullscreen, so the launcher doesn't request fullscreen itself, because that would switch it back out. On a normal desktop it goes fullscreen by itself after 1.5 seconds.

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
| `bitedj-launcher` | Wrapper that restarts it if it exits (used by labwc autostart) |
| `bite-launcher.desktop`, `bite-launcher.png` | Bite Launcher shortcut and icon |
| `djrig-launcher.conf` | Settings added to `/etc/djrig.conf` |
| `install.sh`, `uninstall.sh` | Install and remove on a running Pi |
| `mixxxpi64-ng-bitedj-launcher.patch` | Adds the launcher to the image build |
