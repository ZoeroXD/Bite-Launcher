#!/bin/bash
# Bite Launcher installer for the Raspberry Pi DJ rig.
# Run as the DJ user (not root):  ./install.sh
#   --keep-rainbow   leave the Raspberry Pi rainbow boot screen as it is
set -e
[ "$(id -u)" -eq 0 ] && { echo "Run as your normal user; sudo is used where needed."; exit 1; }
cd "$(dirname "$0")"
KEEP_RAINBOW=0
for a in "$@"; do [ "$a" = "--keep-rainbow" ] && KEEP_RAINBOW=1; done
AUTOSTART="$HOME/.config/labwc/autostart"
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}
[ -z "$DBUS_SESSION_BUS_ADDRESS" ] && [ -S "$XDG_RUNTIME_DIR/bus" ] && export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"

echo ">> Installing packages"
sudo apt-get update -q
sudo apt-get install -y --no-install-recommends python3-pyqt6 swaybg

echo ">> Installing launcher"
sudo install -d /opt/bitedj-launcher
sudo install -m 755 bitedj_launcher.py /opt/bitedj-launcher/bitedj_launcher.py
sudo install -m 755 bitedj-launcher /usr/local/bin/bitedj-launcher
sudo install -m 755 bite-launcher-open /usr/local/bin/bite-launcher-open
sudo install -m 644 bite-launcher.png /opt/bitedj-launcher/bite-launcher.png
sudo touch /etc/djrig.conf
grep -q "BiteDJ Launcher" /etc/djrig.conf || sudo tee -a /etc/djrig.conf < djrig-launcher.conf >/dev/null

echo ">> Adding the Bite Launcher shortcut (desktop + app menu)"
sudo install -m 644 bite-launcher.desktop /usr/share/applications/bite-launcher.desktop
DESKTOP_DIR=$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")
mkdir -p "$DESKTOP_DIR"
install -m 755 bite-launcher.desktop "$DESKTOP_DIR/bite-launcher.desktop"
# Trusted = double-click runs it instead of asking or opening it as text
gio set "$DESKTOP_DIR/bite-launcher.desktop" metadata::trust true 2>/dev/null || true

echo ">> Updating labwc autostart"
mkdir -p "$(dirname "$AUTOSTART")"
touch "$AUTOSTART"
[ -f "$AUTOSTART.pre-launcher" ] || cp "$AUTOSTART" "$AUTOSTART.pre-launcher"
# The launcher replaces djrig-launch, which started BiteDJ without waiting for you
sed -i 's|^\s*/usr/local/bin/djrig-launch.*|# /usr/local/bin/djrig-launch \&   # replaced by bitedj-launcher|' "$AUTOSTART"
# Both lines skip when ~/.config/bite-launcher/autostart-off exists (set by Ctrl+Alt+D or the FLX4 macro,
# cleared by the Bite Launcher shortcut), so the Pi boots into whichever mode was used last.
sed -i '/# bitedj-launcher/d' "$AUTOSTART"      # replace lines from an older install
OFF='"$HOME/.config/bite-launcher/autostart-off"'
{ echo "[ -e $OFF ] || swaybg -c \"#000000\" &   # bitedj-launcher: black backdrop"
  cat "$AUTOSTART"
  echo "[ -e $OFF ] || /usr/local/bin/bitedj-launcher &   # bitedj-launcher"
} > "$AUTOSTART.new" && mv "$AUTOSTART.new" "$AUTOSTART"

# The rainbow square at power-on is the GPU firmware's splash screen; disable_splash=1 hides it.
# It's only a picture: hiding it skips no hardware checks and doesn't slow or change booting.
echo ">> Rainbow boot screen"
CONFIG=/boot/firmware/config.txt
[ -f "$CONFIG" ] || CONFIG=/boot/config.txt
RAINBOW="kept (--keep-rainbow)"
if [ "$KEEP_RAINBOW" = 0 ]; then
  if [ ! -f "$CONFIG" ]; then
    RAINBOW="config.txt not found, left as is"
  elif grep -qE '^\s*disable_splash\s*=\s*1' "$CONFIG"; then
    RAINBOW="already off ($CONFIG)"
  else
    [ -f "$CONFIG.pre-bite-launcher" ] || sudo cp "$CONFIG" "$CONFIG.pre-bite-launcher"
    # An existing "disable_splash=0" is parked (so uninstall can restore it), then ours goes under [all]
    sudo sed -i 's/^\(\s*disable_splash\s*=.*\)$/#bite-launcher# \1/' "$CONFIG"
    printf '\n# Bite Launcher: hide the rainbow boot screen\n[all]\ndisable_splash=1\n# end Bite Launcher\n' | sudo tee -a "$CONFIG" >/dev/null
    RAINBOW="turned off ($CONFIG, backup: $CONFIG.pre-bite-launcher)"
  fi
fi

echo
echo ">> Done. Reboot (sudo reboot) to start the Bite Launcher."
