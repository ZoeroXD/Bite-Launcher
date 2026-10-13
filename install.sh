#!/bin/bash
# BiteDJ Launcher installer for the Raspberry Pi DJ rig (labwc kiosk session).
# Run as the DJ user (not root):  ./install.sh
set -e
[ "$(id -u)" -eq 0 ] && { echo "Run as your normal user; sudo is used where needed."; exit 1; }
cd "$(dirname "$0")"
AUTOSTART="$HOME/.config/labwc/autostart"

echo ">> Installing packages"
sudo apt-get update -q
sudo apt-get install -y --no-install-recommends python3-pyqt6 swaybg

echo ">> Installing launcher"
sudo install -d /opt/bitedj-launcher
sudo install -m 755 bitedj_launcher.py /opt/bitedj-launcher/bitedj_launcher.py
sudo install -m 755 bitedj-launcher /usr/local/bin/bitedj-launcher
sudo install -m 755 bite-launcher-open /usr/local/bin/bite-launcher-open
sudo install -m 644 bite-launcher.png /opt/bitedj-launcher/bite-launcher.png

echo ">> Adding the Bite Launcher shortcut (app menu + desktop)"
sudo install -m 644 bite-launcher.desktop /usr/share/applications/bite-launcher.desktop
DESKTOP_DIR=$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")
mkdir -p "$DESKTOP_DIR"
install -m 755 bite-launcher.desktop "$DESKTOP_DIR/bite-launcher.desktop"
# Mark it trusted so the file manager opens it without an "Execute?" prompt
gio set "$DESKTOP_DIR/bite-launcher.desktop" metadata::trusted true 2>/dev/null || true
sudo touch /etc/djrig.conf
grep -q "BiteDJ Launcher" /etc/djrig.conf || sudo tee -a /etc/djrig.conf < djrig-launcher.conf >/dev/null

echo ">> Updating labwc autostart"
mkdir -p "$(dirname "$AUTOSTART")"
touch "$AUTOSTART"
[ -f "$AUTOSTART.pre-launcher" ] || cp "$AUTOSTART" "$AUTOSTART.pre-launcher"
# The launcher replaces djrig-launch, which started BiteDJ without waiting for you
sed -i 's|^\s*/usr/local/bin/djrig-launch.*|# /usr/local/bin/djrig-launch \&   # replaced by bitedj-launcher|' "$AUTOSTART"
if ! grep -q bitedj-launcher "$AUTOSTART"; then
  # Black background first so nothing but black is ever visible between boot and the launcher
  sed -i '1i swaybg -c "#000000" &   # bitedj-launcher: black backdrop' "$AUTOSTART"
  echo '/usr/local/bin/bitedj-launcher &   # bitedj-launcher' >> "$AUTOSTART"
fi
python3 /opt/bitedj-launcher/bitedj_launcher.py --list-controllers | head -1
if [ -x "$HOME/bin/bite-mode" ]; then
  echo ">> Mode switch: found ~/bin/bite-mode (Switch to Pi Desktop button, Ctrl+Alt+D and FLX4 4 BEAT/EXIT hold are active)"
else
  echo ">> Mode switch: install BiteDJ-On-Off-Macro-with-FLX4 to enable switching to the Pi Desktop"
fi
echo ">> Done. Reboot (sudo reboot) to start the Bite Launcher."
