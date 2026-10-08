#!/usr/bin/env bash
set -euo pipefail
exec >> /var/log/playlite-nordvpn-install.log 2>&1
if ! command -v nordvpn >/dev/null; then
  curl --fail --location --retry 3 https://downloads.nordcdn.com/apps/linux/install.sh -o /var/tmp/playlite-install-nordvpn.sh
  bash /var/tmp/playlite-install-nordvpn.sh -n -p nordvpn-gui
fi
usermod -aG nordvpn ubuntu
systemctl enable --now nordvpnd
# Restart the bridge so its process picks up the new group membership.
systemctl restart playlite-steam-bridge.service
mkdir -p /var/lib/playlite-vm
touch /var/lib/playlite-vm/nordvpn-installed
