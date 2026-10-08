#!/usr/bin/env bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
exec > >(tee -a /var/log/playlite-vm-setup.log) 2>&1
dpkg --add-architecture i386
add-apt-repository -n -y universe
add-apt-repository -n -y multiverse
apt-get update
printf 'lightdm shared/default-x-display-manager select lightdm\n' | debconf-set-selections
apt-get install -y --no-install-recommends \
  xorg xfce4 xfce4-terminal lightdm dbus-x11 spice-vdagent qemu-guest-agent \
  curl ca-certificates jq unzip xdg-utils epiphany-browser libnotify-bin \
  libgl1-mesa-dri:i386 libssl3t64:i386 libgl1:i386 libglx-mesa0:i386 \
  libvulkan1:i386 mesa-vulkan-drivers:i386 libgl1-mesa-dri mesa-vulkan-drivers \
  fonts-dejavu-core librsvg2-common xauth libva-drm2 libva-x11-2 \
  libva-drm2:i386 libva-x11-2:i386 libibus-1.0-5
mkdir -p /etc/lightdm/lightdm.conf.d
printf '[Seat:*]\nautologin-user=ubuntu\nautologin-user-timeout=0\nuser-session=xfce\n' \
  > /etc/lightdm/lightdm.conf.d/50-playlite-vm.conf
systemctl enable --now qemu-guest-agent
python3 /usr/local/lib/playlite-vm/setup-runtime.py
runuser -u ubuntu -- python3 /usr/local/lib/playlite-vm/prepare-storage.py
mkdir -p /home/ubuntu/.config/xfce4/xfconf/xfce-perchannel-xml
cat > /home/ubuntu/.config/xfce4/xfconf/xfce-perchannel-xml/xsettings.xml <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<channel name="xsettings" version="1.0"><property name="Net" type="empty"><property name="IconThemeName" type="string" value="Adwaita"/></property></channel>
EOF
chown -R ubuntu:ubuntu /home/ubuntu/.config
mkdir -p /home/ubuntu/Desktop
cat > /home/ubuntu/Desktop/steam-vm.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=Steam (VPN)
Exec=/usr/local/bin/playlite-vm-control start-steam
Icon=steam
Terminal=false
EOF

cat > /home/ubuntu/Desktop/configure-nordvpn.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=Configure NordVPN after sign-in
Exec=/usr/local/bin/playlite-vm-control configure-nord
Icon=nordvpn
Terminal=false
EOF
chmod +x /home/ubuntu/Desktop/*.desktop
chown -R ubuntu:ubuntu /home/ubuntu/Desktop
runuser -u ubuntu -- xdg-settings set default-web-browser org.gnome.Epiphany.desktop || true
mkdir -p /var/lib/playlite-vm
touch /var/lib/playlite-vm/setup-complete
systemctl set-default graphical.target
systemctl enable --now lightdm
printf '\nSetup complete. Sign into NordVPN in the VM, then configure it from Playlite.\n'
