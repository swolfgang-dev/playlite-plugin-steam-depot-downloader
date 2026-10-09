"""Dedicated OpenVPN namespace. No game download implementation in this milestone."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import threading
import sys
from .constants import IMAGE, LABEL, WORKER_LABEL, PROFILE_SUFFIX
from .guardian import process_token, safe_directory
from .settings import Preferences as Preferences
from .recommendations import recommended_servers
from .credentials import validate_credentials

GUARD = '''set -eu
iptables -N PLAYLITE_WORKER
iptables -A PLAYLITE_WORKER -o lo -j RETURN
iptables -A PLAYLITE_WORKER -o tun0 -j RETURN
iptables -A PLAYLITE_WORKER -j REJECT
iptables -I OUTPUT 1 -m owner --uid-owner 65534 -j PLAYLITE_WORKER
'''

class ConnectionAttemptFailed(RuntimeError):
    pass


class AuthenticationRejected(ConnectionAttemptFailed):
    pass


class Network:
    def __init__(self):
        self.name = 'playlite-steam-downloader-vpn-' + str(os.getuid()) + PROFILE_SUFFIX
        self.directory = None
        self.container = None
        self.cancelled = threading.Event()
        self.guardian = None

    def start_guardian(self):
        self.guardian = subprocess.Popen([sys.executable, str(Path(__file__).with_name('guardian.py')),
            str(os.getpid()), process_token(os.getpid()), self.container, self.directory],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)

    def cancel(self):
        self.cancelled.set()

    def check_cancelled(self):
        if self.cancelled.is_set():
            raise RuntimeError('VPN connection cancelled. Downloads remain disabled.')

    def docker(self, *args, timeout=30):
        # Only repeat read-only checks: a timed-out mutation may have completed.
        readonly = args[0] in ('inspect','ps','logs') or (args[0]=='exec' and (
            args[2:4] in (('iptables','-S'),('iptables','-C'),('ip','route')) or args[2]=='cat')) or (
            args[0]=='run' and args[-1]=='wget -q -T 10 -O - https://api.ipify.org')
        for attempt in range(3 if readonly else 1):
            try:
                result = subprocess.run(['docker', *args], capture_output=True, text=True, timeout=timeout)
                break
            except subprocess.TimeoutExpired as error:
                if not readonly or attempt==2:
                    raise RuntimeError('Docker '+args[0]+' timed out'+(' after 3 attempts.' if readonly else '.')) from error
                import logging
                logging.info('Docker %s check timed out; retrying in 2 seconds (%s/3).',args[0],attempt+2)
                self.cancelled.wait(2)
                self.check_cancelled()
            except OSError as error:
                raise RuntimeError('Docker is unavailable. Check that Docker is installed and running.') from error
        if result.returncode:
            # Docker errors can include mount paths or user input; never report raw output.
            raise RuntimeError('Docker could not complete the isolated network operation.')
        return (result.stdout + result.stderr).strip() if args[0] == 'logs' else result.stdout.strip()

    def inspect(self):
        data = json.loads(self.docker('inspect', self.container or self.name))[0]
        if data['Config'].get('Labels', {}).get(LABEL) != str(os.getuid()):
            raise RuntimeError('Refusing to control a container that does not belong to this plugin.')
        host = data['HostConfig']
        if host['NetworkMode'] != 'bridge' or host.get('Privileged') or host.get('PortBindings'):
            raise RuntimeError('The VPN container has an unsafe network configuration.')
        if data['Config']['Image'] != IMAGE:
            raise RuntimeError('The VPN container image does not match the configured version.')
        return data

    def disconnect(self):
        relay=vars(self).pop('_steam_desktop_relay',None)
        if relay and relay[0].poll() is None:relay[0].terminate()
        if self.container:
            self.inspect()
            workers = self.docker('ps', '-aq', '--filter', 'label=' + WORKER_LABEL + '=' + self.container)
            for worker in workers.split():
                self.docker('rm', '-f', worker)
            self.docker('rm', '-f', self.container)
            self.container = None
        if self.directory:
            shutil.rmtree(self.directory)
            self.directory = None
        if self.guardian:
            try:
                self.guardian.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            self.guardian = None
        return 'Disconnected. Downloads remain disabled.'

    def connect(self, preferences, username, password, deadline=90, progress=lambda text: None):
        self.attempted_servers = set()
        delays = (5, 10, 20, 30)
        for attempt in range(5):
            self.check_cancelled()
            progress(f'NordVPN connection attempt {attempt + 1}/5…')
            try:
                return self._connect_once(preferences, username, password, deadline, progress)
            except ConnectionAttemptFailed:
                if attempt == 4:
                    raise ConnectionAttemptFailed('NordVPN failed all 5 connection attempts. This can be temporary; saved credentials have not been changed. Try again later, and check the service credentials if rejection persists.') from None
                delay = delays[attempt]
                progress(f'NordVPN attempt {attempt + 1}/5 failed. Waiting {delay}s before attempt {attempt + 2}/5… Cancel is available.')
                self.cancelled.wait(delay)
                self.check_cancelled()

    def choose_server(self, preferences, progress):
        progress('Asking NordVPN for recommended OpenVPN servers…')
        try:
            ranked = recommended_servers(preferences)
            if not hasattr(self, 'compatible_servers'):
                listing = self.docker('run', '--rm', '--network', 'none', IMAGE, 'format-servers', '-nordvpn')
                import re
                self.compatible_servers = set(re.findall(r'`([a-z]{2}\d+\.nordvpn\.com)` \| openvpn', listing))
            attempted = getattr(self, 'attempted_servers', set())
            server = next((host for host in ranked if host in self.compatible_servers and host not in attempted), None)
            if server:
                attempted.add(server)
                self.attempted_servers = attempted
                progress(f'NordVPN recommends {server}. Connecting…')
                return server
        except (OSError, ValueError, RuntimeError):
            pass
        progress('Recommendations unavailable or exhausted. Using Gluetun compatible-server selection…')
        return None

    def _connect_once(self, preferences, username, password, deadline=90, progress=lambda text: None):
        progress('Preparing the isolated OpenVPN container…')
        preferences.validate()
        username, password = validate_credentials(username, password)
        if self.container:
            self.disconnect()
        # An explicit Connect replaces only this user's validated plugin container.
        existing = self.docker('ps', '-aq', '--filter', 'name=^/' + self.name + '$')
        if existing:
            data = self.inspect()
            secret = next((mount for mount in data['Mounts'] if mount['Destination'] == '/run/secrets'), None)
            if not secret or secret['RW'] or not safe_directory(secret['Source']):
                raise RuntimeError('The previous VPN container has an unsafe credential mount. Refusing to remove it.')
            self.container, self.directory = data['Id'], secret['Source']
            progress('Removing the previous plugin connection before reconnecting…')
            self.disconnect()
        self.directory = tempfile.mkdtemp(prefix='playlite-vpn-', dir=os.environ.get('XDG_RUNTIME_DIR') or None)
        os.chmod(self.directory, 0o700)
        try:
            for name, value in [('openvpn_user', username), ('openvpn_password', password)]:
                path = Path(self.directory) / name
                descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(descriptor, 'w') as stream:
                    stream.write(value + '\n')
            args = ['run', '-d', '--name', self.name, '--label', LABEL + '=' + str(os.getuid()),
                    '--network', 'bridge', '--cap-add', 'NET_ADMIN', '--device', '/dev/net/tun',
                    '--sysctl', 'net.ipv6.conf.all.disable_ipv6=1',
                    '--mount', f'type=bind,src={self.directory},dst=/run/secrets,readonly',
                    '-e', 'VPN_SERVICE_PROVIDER=nordvpn', '-e', 'VPN_TYPE=openvpn',
                    '-e', 'OPENVPN_PROTOCOL=' + preferences.protocol,
                    '-e', 'FIREWALL=on', '-e', 'FIREWALL_OUTBOUND_SUBNETS=',
                    '-e', 'DNS_ADDRESS=127.0.0.1']
            server = self.choose_server(preferences, progress)
            self.check_cancelled()
            if server:
                args += ['-e', 'SERVER_HOSTNAMES=' + server]
            if preferences.country:
                args += ['-e', 'SERVER_COUNTRIES=' + preferences.country]
            self.container = self.docker(*args, IMAGE, timeout=180)
            self.check_cancelled()
            self.inspect()
            progress('Installing the tunnel-only worker firewall…')
            # No worker exists until the UID guard is installed successfully.
            self.docker('exec', self.container, '/bin/sh', '-c', GUARD)
            self.start_guardian()
            started = time.monotonic()
            limit = started + deadline
            while time.monotonic() < limit:
                self.check_cancelled()
                elapsed = int(time.monotonic() - started)
                progress(f'Connecting to NordVPN ({elapsed}s / {deadline:.0f}s)… Cancel is available.')
                data = self.inspect()
                if not data['State']['Running']:
                    raise ConnectionAttemptFailed('The VPN stopped before establishing a tunnel.')
                logs = self.docker('logs', '--tail', '80', self.container)
                if data['State'].get('Health', {}).get('Status') == 'healthy':
                    progress('Tunnel connected. Verifying isolation and public IP…')
                    try:
                        return self.check()
                    except RuntimeError:
                        progress('Waiting for the tunnel route and isolation checks…')
                if 'AUTH_FAILED' in logs:
                    raise AuthenticationRejected('NordVPN rejected this connection attempt.')
                self.cancelled.wait(1)
            raise ConnectionAttemptFailed('VPN connection timed out. Check the service credentials and country selection.')
        except Exception:
            self.disconnect()
            raise

    def probe_args(self, command):
        if not self.container or not self.directory:
            raise RuntimeError('Connect the VPN first.')
        path = Path(self.directory) / 'resolv.conf'
        path.write_text('nameserver 103.86.96.100\nnameserver 103.86.99.100\n')
        path.chmod(0o644)
        return ['run', '--rm', '--network', 'container:' + self.container,
                '--label', WORKER_LABEL + '=' + self.container,
                '--user', '65534:65534', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                '--read-only', '--pids-limit', '32', '--memory', '64m',
                '--mount', f'type=bind,src={path},dst=/etc/resolv.conf,readonly',
                '--entrypoint', '/bin/sh', IMAGE, '-c', command]

    def connected_server(self):
        """Read the server identified by the latest established OpenVPN peer."""
        import re
        logs = self.docker('logs', '--tail', '200', self.container)
        peers = re.findall(r'\[([a-z]{2}\d+\.nordvpn\.com)\] Peer Connection Initiated', logs)
        return peers[-1] if peers else None

    def check(self):
        if not self.container or not self.directory:
            raise RuntimeError('Connect the VPN from this settings window before signing into Moon or downloading. A previous connection must be reconnected after restarting or reloading the plugin.')
        data = self.inspect()
        if not data['State']['Running'] or data['State'].get('Health', {}).get('Status') != 'healthy':
            raise RuntimeError('The VPN is not healthy. Downloads are blocked.')
        output = self.docker('exec', self.container, 'iptables', '-S', 'OUTPUT').splitlines()
        first = next((line for line in output if line.startswith('-A ')), '')
        if first != '-A OUTPUT -m owner --uid-owner 65534 -j PLAYLITE_WORKER':
            raise RuntimeError('The worker firewall is not the first output rule.')
        if self.docker('exec', self.container, 'cat', '/proc/sys/net/ipv6/conf/all/disable_ipv6') != '1':
            raise RuntimeError('IPv6 isolation is not active.')
        rules = self.docker('exec', self.container, 'iptables', '-S', 'PLAYLITE_WORKER').splitlines()
        expected=['-N PLAYLITE_WORKER', '-A PLAYLITE_WORKER -o tun0 -j RETURN',
                  '-A PLAYLITE_WORKER -j REJECT --reject-with icmp-port-unreachable']
        with_loopback=[expected[0],'-A PLAYLITE_WORKER -o lo -j RETURN',*expected[1:]]
        if rules not in (expected,with_loopback):
            raise RuntimeError('The worker firewall is not intact.')
        route = self.docker('exec', self.container, 'ip', 'route', 'get', '1.1.1.1')
        if 'dev tun0' not in route:
            raise RuntimeError('The default route does not use the VPN tunnel.')
        address = self.docker(*self.probe_args('wget -q -T 10 -O - https://api.ipify.org'), timeout=20)
        import ipaddress
        address = str(ipaddress.ip_address(address))
        server = self.connected_server()
        return ('VPN connected to ' + server if server else 'VPN connected (server name unavailable)') + '. Isolated public IP: ' + address + '. Isolated download workers are ready.'
