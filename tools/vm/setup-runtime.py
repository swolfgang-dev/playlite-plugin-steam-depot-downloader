#!/usr/bin/env python3
"""Register the VM's persistent bridge; run as root during install or upgrade."""
from pathlib import Path
import hashlib
import shutil
import subprocess

ASSETS = Path(__file__).resolve().parent
# Allow only the precise tunnel DNS recovery commands used by the VPN gate.
dns_policy = Path('/etc/sudoers.d/playlite-vm-dns')
dns_policy.write_text('ubuntu ALL=(root) NOPASSWD: /usr/bin/resolvectl dnssec nordtun no, /usr/bin/resolvectl dnssec nordlynx no, /usr/bin/resolvectl flush-caches\n')
dns_policy.chmod(0o440)
installer = ASSETS / 'steam/moon-install.sh'
if hashlib.sha256(installer.read_bytes()).hexdigest() != '1c6896832d32d6d17c5635daf95b539763955f7ea1d9bd4446e24c2e8a5806c2':
    raise SystemExit('LuaMoon installer checksum mismatch.')
for folder in (Path('/home/ubuntu/.config'), Path('/home/ubuntu/.local'),
               Path('/home/ubuntu/.local/share'), Path('/home/ubuntu/.local/state'),
               Path('/home/ubuntu/.config/systemd'), Path('/home/ubuntu/.config/systemd/user'),
               Path('/home/ubuntu/.config/autostart')):
    folder.mkdir(parents=True, exist_ok=True)
    shutil.chown(folder,user='ubuntu',group='ubuntu')
# A process spawned through the root guest agent is outside the user's logind
# session. spice-vdagentd rejects it; run the desktop agent in the user manager.
clipboard_unit = Path('/home/ubuntu/.config/systemd/user/playlite-spice-agent.service')
clipboard_unit.write_text('''[Unit]
Description=Playlite VM desktop clipboard agent

[Service]
Environment=DISPLAY=:0
Environment=XAUTHORITY=/home/ubuntu/.Xauthority
ExecStart=/usr/bin/spice-vdagent -x
Restart=on-failure
RestartSec=2
''')
clipboard_start = Path('/home/ubuntu/.config/autostart/spice-vdagent.desktop')
clipboard_start.write_text('''[Desktop Entry]
Type=Application
Name=SPICE clipboard agent
Exec=systemctl --user start playlite-spice-agent.service
Terminal=false
''')
for file in (clipboard_unit, clipboard_start):
    file.chmod(0o644)
    shutil.chown(file, user='ubuntu', group='ubuntu')
if Path('/run/user/1000/bus').exists() and Path('/tmp/.X11-unix/X0').exists():
    user_systemctl = ['runuser', '-u', 'ubuntu', '--', 'env',
                     'XDG_RUNTIME_DIR=/run/user/1000',
                     'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus',
                     'systemctl', '--user']
    subprocess.run(user_systemctl + ['daemon-reload'], check=True)
    subprocess.run(user_systemctl + ['start', clipboard_unit.name], check=True)
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
