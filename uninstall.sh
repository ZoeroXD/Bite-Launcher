#!/bin/bash
set -euo pipefail
[ "$(id -u)" -ne 0 ] || { echo "Run as your normal Pi user, not root."; exit 1; }
cd "$(dirname "$0")"
[ "$#" -eq 0 ] || { echo "Usage: ./uninstall.sh"; exit 2; }
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}
export DBUS_SESSION_BUS_ADDRESS=${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}
python3 -B install_preflight.py --uninstall
SESSION_KIND=$(python3 -c 'import bite_runtime as r; print("lite" if r.lite_mode() else "desktop")')
sudo /usr/bin/python3 -I -S configure_lite.py --check --remove --user "$(id -un)"
sudo /usr/bin/python3 -I -S configure_rainbow.py --check
# Refuse to overwrite later firmware edits; do this before removing the launcher.
sudo /usr/bin/python3 -I -S configure_rainbow.py --restore
sudo /usr/bin/python3 -I -S configure_lite.py --remove --user "$(id -un)"
# Latest specification: selecting desktop closes BiteDJ as well as the launcher.
if [ "$SESSION_KIND" = desktop ] && [ -x /usr/local/bin/bite-mode ]; then
  /usr/local/bin/bite-mode desktop
fi
systemctl --user disable --now bitedj-hold.service 2>/dev/null || true
if [ "$SESSION_KIND" = desktop ]; then
  python3 configure_session.py --remove
  sudo /usr/bin/python3 configure_autostart.py --remove
fi
if cmp -s bite-mode "$HOME/bin/bite-mode"; then rm "$HOME/bin/bite-mode"; fi
if cmp -s compat/bitedj-hold.py "$HOME/bin/bitedj-hold.py"; then rm "$HOME/bin/bitedj-hold.py"; fi
if cmp -s systemd/bitedj-hold.service "$HOME/.config/systemd/user/bitedj-hold.service"; then
  rm "$HOME/.config/systemd/user/bitedj-hold.service"
fi
systemctl --user daemon-reload
DESKTOP_DIR=$(xdg-user-dir DESKTOP 2>/dev/null || true)
[ -n "$DESKTOP_DIR" ] && [ "$DESKTOP_DIR" != "$HOME" ] || DESKTOP_DIR="$HOME/Desktop"
rm -f "$DESKTOP_DIR/bite-launcher.desktop" "$HOME/Desktop/bite-launcher.desktop"
rm -f "$HOME/.config/bite-launcher/autostart-off"
sudo rm -rf /opt/bitedj-launcher
sudo rm -f /usr/local/bin/bitedj-launcher /usr/local/bin/bite-launcher-open /usr/local/bin/bite-mode /usr/share/applications/bite-launcher.desktop
echo "Removed. Reboot to reload keyboard settings. Backups and /etc/djrig.conf were retained."
echo "Any unchanged managed rainbow setting was restored; other boot settings were not changed."
