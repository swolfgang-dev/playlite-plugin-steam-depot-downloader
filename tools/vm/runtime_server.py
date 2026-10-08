"""Persistent allowlisted VM control. Steam state and asynchronous exports survive RPCs."""
import base64
import json
import os
from pathlib import Path
import re
import socketserver
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

ASSETS = Path(__file__).resolve().parent
HOME = Path.home()
CONTROL = HOME / '.local/state/playlite-vm/control'
CFG = json.loads(Path('/etc/playlite-vm.json').read_text())
STAGING = CFG['staging']
if not isinstance(STAGING, str) or not re.fullmatch(r'\.steam-vm-[0-9a-f]{12}', STAGING):
    raise RuntimeError('Invalid VM staging folder.')
LIBRARY = HOME / '.steam/debian-installation'
os.environ.update(PLAYLITE_VM='1', PLAYLITE_CONTROL=str(CONTROL), PLAYLITE_LIBRARY=str(LIBRARY),
                  DISPLAY=':0', XAUTHORITY=str(HOME / '.Xauthority'),
                  DBUS_SESSION_BUS_ADDRESS='unix:path=/run/user/1000/bus')
os.environ.pop('LD_AUDIT', None)
sys.path.insert(0, str(ASSETS / 'steam'))
import bridge
from setup_moon import install_bridge
from guest_control import vpn_ready, run, launch, set_nord_option

steam_lock = threading.Lock()
last_verified = float('-inf')
setup_thread = None
setup_error = None
setup_phase = None


def vpn_check():
    global last_verified
    if 'Status: Connected' not in run(['nordvpn', 'status']).stdout:
        last_verified = 0
        raise RuntimeError('NordVPN disconnected in the VM. Downloads are blocked.')
    if 'kill switch: enabled' not in run(['nordvpn', 'settings']).stdout.lower():
        last_verified = 0
        raise RuntimeError('Enable the VM VPN kill switch before downloading.')
    if time.monotonic() - last_verified > 15:
        vpn_ready()
        last_verified = time.monotonic()
    return {'connected': True, 'kill_switch': True, 'protected': True}


def http(request):
    url = request.get('url')
    headers = request.get('headers', {})
    body = request.get('body')
    if not isinstance(url, str) or len(url) > 8192 or urllib.parse.urlsplit(url).scheme != 'https':
        raise ValueError('VM HTTP requires a bounded HTTPS URL.')
    if not isinstance(headers, dict) or len(headers) > 32 or any(not isinstance(k, str) or not isinstance(v, str) or len(k) + len(v) > 8192 or any(c in k + v for c in '\r\n\0') for k, v in headers.items()):
        raise ValueError('Invalid HTTP headers.')
    data = base64.b64decode(body, validate=True) if body else None
    if data is not None and len(data) > 65536:
        raise ValueError('HTTP request exceeds limit.')
    class Redirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, hdrs, newurl):
            if headers or data:
                raise ValueError('Authenticated redirects are not accepted.')
            if not newurl.startswith('https://'):
                raise ValueError('Insecure redirect.')
            return super().redirect_request(req, fp, code, msg, hdrs, newurl)
    try:
        req = urllib.request.Request(url, data=data, headers={
            'User-Agent': 'Playlite-Steam-Downloader/VM', 'Accept-Encoding': 'identity', **headers})
        with urllib.request.build_opener(Redirect()).open(req, timeout=30) as response:
            content = response.read(64 * 1024 * 1024 + 1)
            if len(content) > 64 * 1024 * 1024:
                raise ValueError('HTTP response exceeds limit.')
            return {'status': response.status, 'body': base64.b64encode(content).decode()}
    except urllib.error.HTTPError as error:
        content = error.read(4096).decode('utf-8', errors='replace').lower()
        reason = ('invalid_api_key' if 'invalid api key' in content else 'expired_code' if 'expired' in content else
                  'invalid_code' if 'invalid' in content and 'code' in content else
                  'browser_challenge' if 'cloudflare' in content or 'just a moment' in content else '')
        return {'status': error.code, 'body': '', 'reason': reason}
    except (OSError, urllib.error.URLError):
        return {'status': 0, 'body': ''}


def setup_user():
    global setup_error,setup_phase
    try:
        vpn_check()
        setup_phase='Downloading verified Steam bootstrap'
        from bootstrap_steam import install as install_bootstrap
        install_bootstrap(HOME)
        setup_phase='Updating Steam'
        dispatch({'command':'start'})
        deadline=time.monotonic()+900
        while not dispatch({'command':'status'}).get('steam_ready'):
            if time.monotonic()>=deadline:raise RuntimeError('Steam update did not finish. Check the VM desktop.')
            vpn_check()
            try:
                path=LIBRARY/'logs/bootstrap_log.txt'
                with path.open('rb') as log:
                    log.seek(0,2);size=log.tell();log.seek(max(0,size-16384))
                    lines=log.read().decode('utf-8',errors='replace').splitlines()
                for line in reversed(lines):
                    match=re.search(r'Downloading update \(([0-9,]+) of ([0-9,]+) KB\)',line)
                    if match:
                        done,total=(int(value.replace(',','')) for value in match.groups())
                        setup_phase=f'Updating Steam · {done//1024} / {total//1024} MB'
                        break
                    if any(text in line for text in ('Applying update','Extracting package','Installing update')):
                        setup_phase='Applying Steam update';break
            except OSError:
                pass
            time.sleep(2)
        if not dispatch({'command':'status'}).get('moon_installed'):
            setup_phase='Installing LuaTools and LuaMoon'
            dispatch({'command':'install_moon'})
            deadline=time.monotonic()+900
            while True:
                state=dispatch({'command':'status'})
                if not state.get('moon_installing'):
                    if not state.get('moon_installed') or state.get('moon_install_exit')!=0:
                        raise RuntimeError('LuaMoon installation failed. Check the VM control/moon-setup.log.')
                    break
                if time.monotonic()>=deadline:raise RuntimeError('LuaMoon installation timed out. Check the VM desktop.')
                time.sleep(2)
        setup_phase='Starting Steam with LuaTools'
        dispatch({'command':'start'})
        setup_phase='Setup complete'
    except Exception as error:
        setup_error=str(error)
        setup_phase='Setup needs attention'


def dispatch(request):
    global last_verified,setup_thread,setup_error,setup_phase
    command = request.get('command')
    if command == 'vpn_check':
        return vpn_check()
    if command == 'http':
        vpn_check()
        return http(request)
    with steam_lock:
        if command == 'setup':
            vpn_check()
            if setup_thread is None or not setup_thread.is_alive():
                setup_error=None;setup_phase='Starting setup'
                setup_thread=threading.Thread(target=setup_user,daemon=True);setup_thread.start()
            return {'setup_running':True}
        if command == 'status':
            state = bridge.dispatch(request)
            state['moon_installed']=(HOME/'.local/share/Lumen/luatools/backend/main.lua').is_file() and (HOME/'.local/share/Lumen/lumen').is_file() and (HOME/'.local/share/SLSsteam/SLSsteam.so').is_file()
            state.update(setup_complete=Path('/var/lib/playlite-vm/setup-complete').exists(),
                         share_mounted=os.path.ismount('/mnt/standalone'),
                         bridge_installed=(HOME / '.local/share/Lumen/luatools/backend/main.lua').is_file())
            state.update(setup_running=setup_thread is not None and setup_thread.is_alive(),setup_error=setup_error,setup_phase=setup_phase)
            return state
        if command == 'open_nord':
            launch(['nordvpn-gui'])
            return {'message': 'Sign into NordVPN in the VM desktop.'}
        if command == 'nord_login_token':
            token=request.get('token')
            if not isinstance(token,str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,256}',token):
                raise ValueError('Enter a valid Nord Account access token.')
            try:
                result=run(['nordvpn','login','--token',token],60)
                if result.returncode:raise RuntimeError()
            except Exception:
                raise RuntimeError('NordVPN token sign-in failed. Check the token in Nord Account and retry.') from None
            return {'message':'NordVPN signed in. Continuing setup…'}
        if command == 'vpn_connect':
            country = request.get('country', '')
            protocol = request.get('protocol', 'tcp')
            if not isinstance(country, str) or len(country) > 80 or any(c in country for c in '\r\n\0') or country.startswith('-'):
                raise ValueError('Invalid VPN country.')
            if protocol not in ('tcp', 'udp'):
                raise ValueError('Invalid VPN protocol.')
            for key, value in [('firewall', 'on'), ('routing', 'on'), ('technology', 'OpenVPN'),
                               ('protocol', protocol), ('autoconnect', 'on'), ('killswitch', 'on')]:
                set_nord_option(key,value)
            result = run(['nordvpn', 'connect', *([country] if country else [])], 60)
            if result.returncode:
                raise RuntimeError('NordVPN connection failed in the VM. Check its desktop.')
            last_verified = 0
            return vpn_check()
        if command in ('disconnect', 'vpn_disconnect'):
            bridge.dispatch({'command': 'stop'})
            deadline = time.monotonic() + 30
            while bridge.steam_running():
                if time.monotonic() >= deadline:
                    raise RuntimeError('Steam has not stopped; VPN was retained.')
                time.sleep(.5)
            result = run(['nordvpn', 'disconnect'], 30)
            if result.returncode:
                raise RuntimeError('VM VPN could not disconnect.')
            last_verified = 0
            return {'disconnected': True}
        if command == 'steam_login_view':
            vpn_check()
            from steam_sign_in import view
            return view(request.get('events',[]),bridge.dispatch({'command':'status'}).get('steam_session_saved',False))
        permitted = {'provider_login', 'authentication_status', 'start', 'stop', 'install_moon', 'add', 'add_status',
                     'cancel_add', 'install', 'download_status', 'pause', 'eula_status', 'eula_accept',
                     'installed_games', 'uninstall', 'retail_selection', 'has_game', 'finish_export', 'workshop'}
        if command not in permitted:
            raise ValueError('Unsupported VM command.')
        if command not in ('stop', 'pause', 'cancel_add', 'download_status', 'has_game'):
            vpn_check()
        if command in ('start', 'install', 'workshop'):
            if not os.path.ismount('/mnt/standalone'):
                raise RuntimeError('Shared games folder is unavailable; Steam operation was blocked.')
            common=LIBRARY/'steamapps/common'
            if not common.is_symlink() or common.resolve()!=Path('/mnt/standalone'):
                raise RuntimeError('Steam game storage is not linked to the shared folder. Run VM setup first.')
        if command == 'start':
            main = HOME / '.local/share/Lumen/luatools/backend/main.lua'
            if main.is_file():
                install_bridge(HOME, CONTROL)
            # Desktop launchers may already have started Steam without debugging.
            # The setup launcher includes the same flags as the plugin.
        if command == 'finish_export':
            bridge.app_id(request.get('appid'))
            # The installed game is the user's final shared copy. Completion
            # never uninstalls or removes it.
            return {'removed': False, 'retained': True, 'direct_install': True}
        if command == 'workshop':
            app = bridge.app_id(request.get('appid'))
            item = request.get('item')
            if item is not None and (type(item) is not int or not 0 < item < 2**64):
                raise ValueError('Invalid Workshop item ID.')
            uri = 'steam://url/CommunityFilePage/' + str(item) if item else 'steam://url/SteamWorkshopPage/' + str(app)
            launch([bridge.steam_binary(), uri])
            return {'opened': True}
        result = bridge.dispatch(request)
        return result


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(180)
        try:
            line = self.rfile.readline(131073)
            if len(line) > 131072:
                raise ValueError('Request exceeds limit.')
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError('Invalid request.')
            response = {'ok': True, 'result': dispatch(request)}
        except (ValueError, RuntimeError) as error:
            response = {'ok': False, 'error': str(error)}
        except Exception:
            response = {'ok': False, 'error': 'VM control failed; check its service log.'}
        self.wfile.write(json.dumps(response).encode() + b'\n')


if __name__ == '__main__':
    os.umask(0o077)
    CONTROL.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = CONTROL / 'runtime.sock'
    path.unlink(missing_ok=True)
    # The daemon belongs to the VM, independently of a Playlite process.
    with socketserver.ThreadingUnixStreamServer(str(path), Handler) as server:
        path.chmod(0o600)
        server.serve_forever()
