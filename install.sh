#!/bin/bash
# Unified launcher + FLX4 macros. Run as the normal Pi user, not root.
set -euo pipefail
[ "$(id -u)" -ne 0 ] || { echo "Run as your normal Pi user, not root."; exit 1; }
cd "$(dirname "$0")"
CHECK_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --check-only) CHECK_ONLY=1 ;;
    *) echo "Usage: ./install.sh [--check-only]"; exit 2 ;;
  esac
done
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}
export DBUS_SESSION_BUS_ADDRESS=${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}
echo "Checking launcher compatibility before installation."
python3 -B install_preflight.py
if [ "$CHECK_ONLY" = 1 ]; then
  exit 0
fi

echo "Installing Bite Launcher $(cat VERSION) and bundled FLX4 controls"
sudo apt-get update -q
sudo apt-get install -y --no-remove --no-install-recommends python3-pyqt6 qt6-wayland swaybg alsa-utils
# Fail before replacing installed files if the system interpreter cannot find its
# Wayland plugin. PyQt6 alone does not guarantee that plugin is installed.
/usr/bin/python3 -c 'from pathlib import Path; from PyQt6.QtCore import QLibraryInfo; p=Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath))/"platforms"; assert list(p.glob("libqwayland*.so")), f"Qt Wayland platform plugin missing from {p}"'
sudo install -d -o root -g root -m 755 /opt/bitedj-launcher
for file in bitedj_launcher.py bite_runtime.py bite_session.py bitedj-hold.py configure_session.py configure_autostart.py install_preflight.py launcher_palette.py theme_sync.py; do
  sudo install -m 755 "$file" "/opt/bitedj-launcher/$file"
done
# Files from earlier versions that are no longer used.
sudo rm -f /opt/bitedj-launcher/configure_boot.py /opt/bitedj-launcher/splash_manager.py /opt/bitedj-launcher/BOOT-BRANDING-STATUS
for file in bitedj-launcher bite-launcher-open bite-mode; do
  sudo install -m 755 "$file" "/usr/local/bin/$file"
done
sudo install -m 644 bite-launcher.png /opt/bitedj-launcher/bite-launcher.png
sudo touch /etc/djrig.conf
if ! grep -q "BiteDJ Launcher" /etc/djrig.conf; then
  sudo tee -a /etc/djrig.conf < djrig-launcher.conf >/dev/null
fi
# Read as the installing user, so ~/bitedj refers to their BiteDJ installation.
# Startup repeats the same detection; no stale color is cached or hardcoded.
/usr/bin/python3 -B /opt/bitedj-launcher/theme_sync.py

# Replace the old macro service instead of starting a second listener.
systemctl --user stop bitedj-hold.service 2>/dev/null || true
python3 -c 'import bite_runtime as r; r.stop("legacy-listener"); r.stop("flx4-reader")'
mkdir -p "$HOME/.config/systemd/user" "$HOME/bin"
for old in "$HOME/bin/bite-mode" "$HOME/bin/bitedj-hold.py" "$HOME/.config/systemd/user/bitedj-hold.service"; do
  if [ -f "$old" ] && [ ! -f "$old.pre-bite-unified" ]; then cp "$old" "$old.pre-bite-unified"; fi
done
install -m 755 bite-mode "$HOME/bin/bite-mode"
install -m 755 compat/bitedj-hold.py "$HOME/bin/bitedj-hold.py"
install -m 644 systemd/bitedj-hold.service "$HOME/.config/systemd/user/bitedj-hold.service"
python3 configure_session.py
# Pi's labwc merge session also runs system autostart: guard that source too.
# Guards are inactive for other users and when saved mode is desktop.
sudo /usr/bin/python3 configure_autostart.py

sudo install -m 644 bite-launcher.desktop /usr/share/applications/bite-launcher.desktop
DESKTOP_DIR=$(xdg-user-dir DESKTOP 2>/dev/null || true)
if [ -z "$DESKTOP_DIR" ] || [ "$DESKTOP_DIR" = "$HOME" ]; then DESKTOP_DIR="$HOME/Desktop"; fi
mkdir -p "$DESKTOP_DIR"
# A link to the menu entry, like the Pi menu's "Add to Desktop". The Pi desktop
# opens these directly instead of asking "Execute / Execute in Terminal / Open".
install -m 644 bite-launcher-shortcut.desktop "$DESKTOP_DIR/bite-launcher.desktop"

systemctl --user daemon-reload
systemctl --user enable --now bitedj-hold.service
# Record completion only after the installation steps above succeed.
sudo install -m 644 VERSION /opt/bitedj-launcher/VERSION
# Do not live-reload a half-migrated graphical session during an update.
echo "Installed. Reboot to use the new launcher, keyboard binding, and bundled FLX4 macros."
echo "The existing saved mode was preserved. No separate macro installation is needed."
echo "Boot settings were not changed; the Pi starts up as stock Raspberry Pi OS."
