#!/bin/bash
# Restores the previous labwc autostart (djrig-launch starts BiteDJ directly again).
set -e
AUTOSTART="$HOME/.config/labwc/autostart"
if [ -f "$AUTOSTART.pre-launcher" ]; then
  mv "$AUTOSTART.pre-launcher" "$AUTOSTART"
else
  sed -i 's|^# \(/usr/local/bin/djrig-launch &\).*|\1|; /bitedj-launcher/d' "$AUTOSTART"
fi
pkill -f bitedj_launcher.py || true
sudo rm -rf /opt/bitedj-launcher /usr/local/bin/bitedj-launcher /usr/share/applications/bite-launcher.desktop
rm -f "$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")/bite-launcher.desktop"
echo "Removed. Settings in /etc/djrig.conf were left in place. Reboot to return to the old startup."
