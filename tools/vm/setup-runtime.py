#!/usr/bin/env python3
"""Register the VM's persistent bridge; run as root during install or upgrade."""
from pathlib import Path
import hashlib
import shutil
import subprocess

ASSETS = Path(__file__).resolve().parent
installer = ASSETS / 'steam/moon-install.sh'
if hashlib.sha256(installer.read_bytes()).hexdigest() != '1c6896832d32d6d17c5635daf95b539763955f7ea1d9bd4446e24c2e8a5806c2':
    raise SystemExit('LuaMoon installer checksum mismatch.')
for folder in (Path('/home/ubuntu/.config'),Path('/home/ubuntu/.local')):
    folder.mkdir(exist_ok=True)
    shutil.chown(folder,user='ubuntu',group='ubuntu')
(ASSETS / 'guest_control.py').chmod(0o755)
launcher = Path('/usr/local/bin/playlite-vm-control')
if launcher.exists() and not launcher.is_symlink():
    launcher.unlink()
if not launcher.exists():
    launcher.symlink_to(ASSETS / 'guest_control.py')
service = Path('/etc/systemd/system/playlite-steam-bridge.service')
service.write_text('''[Unit]
Description=Playlite Steam VM control
After=network.target
RequiresMountsFor=/mnt/standalone

[Service]
Type=simple
User=ubuntu
Group=ubuntu
Environment=HOME=/home/ubuntu
WorkingDirectory=/home/ubuntu
ExecStart=/usr/bin/python3 /usr/local/lib/playlite-vm/runtime_server.py
Restart=on-failure
RestartSec=2
UMask=0077

[Install]
WantedBy=multi-user.target
''')
subprocess.run(['systemctl', 'daemon-reload'], check=True)
subprocess.run(['systemctl', 'enable', '--now', service.name], check=True)
print('VM bridge installed. Steam setup installs LuaMoon after VPN sign-in and Steam bootstrap.')
