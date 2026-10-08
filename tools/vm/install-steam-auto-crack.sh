#!/usr/bin/env bash
# Run inside the guest as root; Wine and the application run as ubuntu.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
dpkg --add-architecture i386
install -d -m 0755 /etc/apt/keyrings
curl --fail --location --retry 3 https://dl.winehq.org/wine-builds/winehq.key \
  --output /etc/apt/keyrings/winehq-archive.key
curl --fail --location --retry 3 \
  https://dl.winehq.org/wine-builds/ubuntu/dists/noble/winehq-noble.sources \
  --output /etc/apt/sources.list.d/winehq-noble.sources
apt-get update
# Ubuntu's Wine 9 displays the .NET 10 WPF GUI but drops text input.
# WineHQ stable (11.0 when verified) supports typing into its fields.
apt-get install -y --no-install-recommends \
  winehq-stable fonts-wine fonts-liberation fonts-noto-cjk \
  python3-fonttools xvfb xauth curl ca-certificates unzip

# Latest stable upstream release at recipe update time (2026-10-08).
# Pin both URL and published digest so a retry installs the same payload.
app_version=3.5.1.0
app_sha256=86853fe90605a4be3adf370a5cdee4c246f724e17c6dc9e2faf7ecff997de3d9
runtime_version=10.0.12
runtime_sha512=e3581e55c5dd345df242af978bd5bd22871975af7cb3d7664567fbfc774e1809e2cd4ee36c42c47c928cbf11dac5f6cf475fdf6ed3b6aed45867ba93421cb6de
work_dir=$(mktemp -d)
trap 'rm -rf -- "$work_dir"' EXIT
curl --fail --location --retry 3 \
  "https://github.com/SteamAutoCracks/Steam-auto-crack/releases/download/$app_version/SteamAutoCrack.zip" \
  --output "$work_dir/app.zip"
printf '%s  %s\n' "$app_sha256" "$work_dir/app.zip" | sha256sum --check
curl --fail --location --retry 3 \
  "https://builds.dotnet.microsoft.com/dotnet/WindowsDesktop/$runtime_version/windowsdesktop-runtime-$runtime_version-win-x86.exe" \
  --output "$work_dir/runtime.exe"
printf '%s  %s\n' "$runtime_sha512" "$work_dir/runtime.exe" | sha512sum --check

prefix=/home/ubuntu/.local/share/playlite/wine-steam-auto-crack
app_dir="$prefix/drive_c/SteamAutoCrack"
install -d -o ubuntu -g ubuntu "$prefix" /home/ubuntu/.cache/playlite
install -o ubuntu -g ubuntu -m 0644 "$work_dir/runtime.exe" \
  /home/ubuntu/.cache/playlite/windowsdesktop-runtime.exe
# Keep the virtual display alive until Wine finishes all child processes.
runuser -u ubuntu -- env HOME=/home/ubuntu WINEPREFIX="$prefix" WINEARCH=win64 \
  WINEDLLOVERRIDES='mscoree,mshtml=' xvfb-run -a bash -euo pipefail -c '
    wineboot --init
    wineserver -w
    wine reg add "HKCU\Software\Wine" /v Version /t REG_SZ /d win10 /f
    ln -sfnT /mnt/standalone "$WINEPREFIX/dosdevices/s:"
    # The Wine folder picker defaults to its virtual Desktop. Show the games
    # there by setting the Desktop folder for this dedicated prefix.
    wine reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders" \
      /v Desktop /t REG_EXPAND_SZ /d "S:\\" /f
    wine reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders" \
      /v Desktop /t REG_SZ /d "S:\\" /f
    python3 /usr/local/lib/playlite-vm/prepare-wine-fonts.py
    wineserver -w
    result=0
    wine "$HOME/.cache/playlite/windowsdesktop-runtime.exe" /install /quiet /norestart || result=$?
    wineserver -w
    # Windows ERROR_SUCCESS_REBOOT_REQUIRED becomes 194 as a Unix exit code.
    if [[ "$result" != 0 && "$result" != 194 ]]; then exit "$result"; fi
    wine "$WINEPREFIX/drive_c/Program Files (x86)/dotnet/dotnet.exe" --list-runtimes \
      | grep -F "Microsoft.WindowsDesktop.App 10.0."
  '
rm -f /home/ubuntu/.cache/playlite/windowsdesktop-runtime.exe
mkdir -p "$app_dir"
unzip -q -o "$work_dir/app.zip" -d "$app_dir"
test -f "$app_dir/SteamAutoCrack.exe"
chown -R ubuntu:ubuntu "$prefix"
printf '%s\n' "$app_version" > "$app_dir/playlite-release-version.txt"
chown ubuntu:ubuntu "$app_dir/playlite-release-version.txt"
runuser -u ubuntu -- ln -sfnT /mnt/standalone "$prefix/dosdevices/s:"

cat > /usr/local/bin/playlite-steam-auto-crack <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
export WINEPREFIX="$HOME/.local/share/playlite/wine-steam-auto-crack"
export WINEARCH=win64
export WINEDLLOVERRIDES='mshtml='
if ! mountpoint -q /mnt/standalone; then
  printf 'The shared Steam library is not mounted at /mnt/standalone.\n' >&2
  exit 1
fi
mkdir -p "$WINEPREFIX/dosdevices"
ln -sfnT /mnt/standalone "$WINEPREFIX/dosdevices/s:"
cd "$WINEPREFIX/drive_c/SteamAutoCrack"
exec wine SteamAutoCrack.exe "$@"
EOF
chmod 0755 /usr/local/bin/playlite-steam-auto-crack
install -d -o ubuntu -g ubuntu /home/ubuntu/Desktop /home/ubuntu/.local/share/applications
cat > /home/ubuntu/.local/share/applications/playlite-steam-auto-crack.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=Steam Auto Crack (Wine)
Exec=/usr/local/bin/playlite-steam-auto-crack
Icon=applications-games
Terminal=true
Categories=Utility;
EOF
install -o ubuntu -g ubuntu -m 0755 \
  /home/ubuntu/.local/share/applications/playlite-steam-auto-crack.desktop \
  /home/ubuntu/Desktop/playlite-steam-auto-crack.desktop
chown ubuntu:ubuntu /home/ubuntu/.local/share/applications/playlite-steam-auto-crack.desktop
printf '\nInstalled Steam Auto Crack %s with Windows .NET Desktop Runtime %s.\n' \
  "$app_version" "$runtime_version"
