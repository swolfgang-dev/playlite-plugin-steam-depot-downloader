#!/usr/bin/env python3
"""Small, allowlisted guest control surface for the standard Steam desktop."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import shutil


def run(args, timeout=15):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def set_nord_option(option, value):
    result=run(['nordvpn','set',option,value],30)
    if result.returncode:
        labels={'firewall':'firewall','routing':'routing','technology':'technology',
                'protocol':'protocol','autoconnect':'auto-connect','killswitch':'kill switch'}
        expected={'on':'enabled','off':'disabled'}.get(value,value).lower()
        settings=run(['nordvpn','settings'],30)
        current=dict(line.lower().split(':',1) for line in settings.stdout.splitlines() if ':' in line)
        if settings.returncode or current.get(labels.get(option,option),'').strip()!=expected:
            raise RuntimeError('NordVPN could not apply the '+option+' setting. Check the VPN service and retry.')
    return result


def vpn_ready(connect=False):
    state = run(['nordvpn', 'status']).stdout
    if 'Status: Connected' not in state and connect:
        run(['nordvpn', 'connect'], 45)
        for _ in range(30):
            state = run(['nordvpn', 'status']).stdout
            if 'Status: Connected' in state:
                break
            time.sleep(1)
    if 'Status: Connected' not in state:
        raise RuntimeError('Sign into NordVPN in the VM, then connect it first.')
    settings = run(['nordvpn', 'settings']).stdout.lower()
    if 'kill switch: enabled' not in settings:
        raise RuntimeError('Use Configure NordVPN first to enable the kill switch.')
    response = run(['curl', '--fail', '--silent', '--show-error', '--max-time', '15',
                    'https://api.nordvpn.com/v1/helpers/ips/insights'], 20)
    if response.returncode or not json.loads(response.stdout).get('protected'):
        raise RuntimeError('VPN protection could not be verified; Steam was not started.')


def configure_nord():
    # Sign-in stays in Nord's GUI, never in host arguments or generated files.
    for option, value in [('firewall', 'on'), ('routing', 'on'),
                          ('technology', 'OpenVPN'), ('protocol', 'tcp'),
                          ('autoconnect', 'on'), ('killswitch', 'on')]:
        set_nord_option(option,value)
    vpn_ready(connect=True)
    return {'message': 'NordVPN connected and verified; auto-connect and kill switch enabled.'}


def launch(args):
    environment = os.environ.copy()
    environment.update(DISPLAY=':0', XAUTHORITY='/home/ubuntu/.Xauthority',
                       DBUS_SESSION_BUS_ADDRESS='unix:path=/run/user/1000/bus')
    environment.pop('LD_AUDIT', None)
    directory = Path.home() / '.local/state/playlite-vm'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(directory / 'desktop.log', 'ab') as log:
        subprocess.Popen(args, env=environment, stdin=subprocess.DEVNULL,
                         stdout=log, stderr=log, start_new_session=True)


def fix_steam_icon():
    icons = Path.home() / '.local/share/icons/hicolor'
    if not (icons / '48x48/apps/steam.png').is_file():
        return
    shutil.copy2('/usr/share/icons/hicolor/index.theme', icons / 'index.theme')
    for size in (16, 24, 32, 48, 256):
        source = icons / f'{size}x{size}/apps/steam.png'
        if source.is_file():
            shutil.copy2(source, source.with_name('steam_tray_mono.png'))
    run(['gtk-update-icon-cache', '--force', str(icons)])
    tray = Path.home() / '.steam/steam/public/steam_tray_mono.png'
    if tray.is_file():
        shutil.copy2(icons / '48x48/apps/steam.png', tray)


def dispatch(command, argument=None):
    if command == 'status':
        vpn = run(['nordvpn', 'status'])
        return {'steam_running': run(['pgrep', '-x', 'steam']).returncode == 0,
                'vpn_connected': 'Status: Connected' in vpn.stdout,
                'share_mounted': os.path.ismount('/mnt/standalone'),
                'setup_complete': Path('/var/lib/playlite-vm/setup-complete').exists()}
    if command == 'configure-nord':
        configure_nord()
        result=subprocess.run(
            ['/usr/bin/python3','/usr/local/lib/playlite-vm/rpc.py'],input=json.dumps({'command':'setup'}),
            text=True,capture_output=True,timeout=180)
        response=json.loads(result.stdout)
        if result.returncode or not response.get('ok'):raise RuntimeError(response.get('error','VM setup failed.'))
        return {'message':'VPN verified. Steam and LuaTools setup is running; Steam will open when ready.'}
    if command == 'open-nord':
        launch(['nordvpn-gui'])
        return {'message': 'NordVPN opened in the VM. Sign in there.'}
    if command == 'start-steam':
        if not os.path.ismount('/mnt/standalone'):
            raise RuntimeError('The shared games filesystem is unavailable; Steam was not started.')
        vpn_ready(connect=True)
        fix_steam_icon()
        # Use the same allowlisted persistent bridge as Playlite.
        from subprocess import run as execute
        result = execute(['/usr/bin/python3', '/usr/local/lib/playlite-vm/rpc.py'],
                         input=json.dumps({'command': 'start'}), text=True, capture_output=True, timeout=180)
        response = json.loads(result.stdout)
        if result.returncode or not response.get('ok'):
            raise RuntimeError(response.get('error', 'Steam VM control failed.'))
        return {'message': 'Steam started in the VM.'}
    raise ValueError('Unknown VM command.')


def main():
    if os.getuid() == 0:
        os.execv('/usr/sbin/runuser', ['runuser', '-u', 'ubuntu', '--',
                 '/usr/bin/python3', str(Path(__file__).resolve()), *sys.argv[1:]])
    try:
        result = dispatch(*sys.argv[1:])
        print(json.dumps({'ok': True, 'result': result}))
    except Exception as error:
        print(json.dumps({'ok': False, 'error': str(error)}))
        if sys.argv[1:] and sys.argv[1] != 'status' and shutil.which('notify-send'):
            launch(['notify-send', '--urgency=critical', 'Playlite Steam VM', str(error)])
        raise SystemExit(1)


if __name__ == '__main__':
    main()
