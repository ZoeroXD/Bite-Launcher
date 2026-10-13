#!/bin/bash
# Removes the Bite Launcher and restores the previous labwc autostart and rainbow boot screen setting.
#   --keep-rainbow-off   leave the rainbow boot screen hidden
set -e
KEEP_OFF=0
for a in "$@"; do [ "$a" = "--keep-rainbow-off" ] && KEEP_OFF=1; done
AUTOSTART="$HOME/.config/labwc/autostart"
if [ -f "$AUTOSTART.pre-launcher" ]; then
  mv "$AUTOSTART.pre-launcher" "$AUTOSTART"
else
  sed -i 's|^# \(/usr/local/bin/djrig-launch &\).*|\1|; /bitedj-launcher/d' "$AUTOSTART"
fi
pkill -f /usr/local/bin/bitedj-launcher || true
pkill -f bitedj_launcher.py || true
sudo rm -rf /opt/bitedj-launcher /usr/local/bin/bitedj-launcher /usr/local/bin/bite-launcher-open /usr/share/applications/bite-launcher.desktop
DESKTOP_DIR=$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")
rm -f "$DESKTOP_DIR/bite-launcher.desktop" "$HOME/Desktop/bite-launcher.desktop"

if [ "$KEEP_OFF" = 0 ]; then
  for CONFIG in /boot/firmware/config.txt /boot/config.txt; do
    [ -f "$CONFIG" ] && grep -q '^# Bite Launcher: hide the rainbow boot screen' "$CONFIG" || continue
    # Drop the block the installer added and bring back any line it parked
    sudo sed -i '/^# Bite Launcher: hide the rainbow boot screen$/,/^# end Bite Launcher$/d; s/^#bite-launcher# //' "$CONFIG"
    sudo sed -i -e :a -e '/^\n*$/{$d;N;ba' -e '}' "$CONFIG"   # trim the blank line left at the end
    echo "Rainbow boot screen restored in $CONFIG"
  done
fi
echo "Removed. Settings in /etc/djrig.conf were left in place. Reboot to return to the old startup."
