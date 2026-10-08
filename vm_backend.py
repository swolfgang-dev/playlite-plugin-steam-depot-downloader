"""Local VM transport shared by Steam, store metadata, and VPN controls."""
import base64
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import threading
import time
import uuid
import shutil
import xml.etree.ElementTree as ET

OWNER = 'SteamDepotDownloader/steam-vm/v1'
GUEST = '/usr/local/lib/playlite-vm'
REPLIES = '/home/ubuntu/.local/state/playlite-vm/replies/'


def vm_root():
    data = Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share')))
    # An explicit profile can be selected without copying credentials to the host.
    return Path(os.environ.get('PLAYLITE_STEAM_VM_ROOT', data / 'playlite/plugin-data/SteamDepotDownloader/steam-vm')).expanduser()


def enabled():
    return (vm_root() / 'vm.json').is_file()


def configuration(root=None):
    root = Path(root or vm_root()).resolve()
    try:
        cfg = json.loads((root / 'vm.json').read_text())
    except (OSError, ValueError):
        raise RuntimeError('Steam VM is not installed. Open Steam setup first.') from None
    if cfg.get('owner') != OWNER or cfg.get('uid') != os.getuid() or Path(cfg.get('root', '')).resolve() != root:
        raise RuntimeError('Refusing to control an unowned or moved Steam VM.')
    for field in ('domain', 'service', 'staging'):
        if not isinstance(cfg.get(field), str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', cfg[field]):
            raise RuntimeError('Invalid Steam VM configuration.')
    if not Path(cfg.get('shared', '')).is_absolute():
        raise RuntimeError('Invalid Steam VM shared folder.')
    return cfg


def installer_module():
    path = Path(__file__).parent / 'tools/vm/install.py'
    spec = importlib.util.spec_from_file_location('playlite_vm_installer', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Agent:
    def __init__(self, cfg):
        self.cfg = cfg

    def call(self, payload):
        # libvirt receives JSON on stdin, keeping provider tokens and oversized
        # installer assets out of process arguments and shell command strings.
        script = "import libvirt,libvirt_qemu,sys; c=libvirt.open('qemu:///session'); d=c.lookupByName(sys.argv[1]); print(libvirt_qemu.qemuAgentCommand(d,sys.stdin.read(),15,0))"
        result = subprocess.run(['/usr/bin/python3', '-c', script, self.cfg['domain']],
                                input=json.dumps(payload), capture_output=True, text=True, timeout=20,
                                env=installer_module().host_environment())
        if result.returncode:
            raise RuntimeError('Steam VM guest agent is unavailable. Start the VM or check its setup log.')
        try:
            response = json.loads(result.stdout)
            return response['return']
        except (KeyError, ValueError):
            raise RuntimeError('Invalid VM guest-agent response.') from None

    def execute(self, args, payload=None, timeout=180):
        arguments = {'path': args[0], 'arg': args[1:], 'capture-output': True}
        if payload is not None:
            arguments['input-data'] = base64.b64encode(payload).decode()
        pid = self.call({'execute': 'guest-exec', 'arguments': arguments})['pid']
        deadline = time.monotonic() + timeout
        while True:
            result = self.call({'execute': 'guest-exec-status', 'arguments': {'pid': pid}})
            if result.get('exited'):
                if result.get('out-truncated') or result.get('err-truncated'):
                    raise RuntimeError('VM response was truncated.')
                return result.get('exitcode', 1), base64.b64decode(result.get('out-data', '')), base64.b64decode(result.get('err-data', ''))
            if time.monotonic() >= deadline:
                # The guest RPC client also has a timeout; it will not hold the
                # single bridge mailbox indefinitely after a host timeout.
                raise RuntimeError('Steam VM command timed out; check the VM before retrying.')
            time.sleep(.2)

    def write_file(self, path, data):
        handle=self.call({'execute':'guest-file-open','arguments':{'path':path,'mode':'w'}})
        try:
            for offset in range(0,len(data),32768):
                chunk=data[offset:offset+32768]
                result=self.call({'execute':'guest-file-write','arguments':{
                    'handle':handle,'buf-b64':base64.b64encode(chunk).decode()}})
                if result.get('count')!=len(chunk):
                    raise RuntimeError('Incomplete VM asset upload.')
        finally:
            self.call({'execute':'guest-file-close','arguments':{'handle':handle}})

    def rpc(self, request):
        code, output, _ = self.execute(['/usr/bin/python3', GUEST + '/rpc.py'],
                                       json.dumps(request).encode(), timeout=200)
        try:
            envelope = json.loads(output)
        except ValueError:
            raise RuntimeError('VM control is not installed or returned an invalid response. Run Steam setup.') from None
        if 'reply' in envelope:
            identifier = envelope['reply']
            if not isinstance(identifier, str) or not re.fullmatch(r'[0-9a-f]{32}', identifier):
                raise RuntimeError('Invalid VM response file.')
            handle = None
            try:
                handle = self.call({'execute': 'guest-file-open', 'arguments': {'path': REPLIES + identifier + '.json'}})
                data = bytearray()
                while True:
                    result = self.call({'execute': 'guest-file-read', 'arguments': {'handle': handle, 'count': 262144}})
                    data.extend(base64.b64decode(result['buf-b64'], validate=True))
                    if len(data) > 96 * 1024 * 1024:
                        raise RuntimeError('VM response exceeds limit.')
                    if result['eof']:
                        break
                envelope = json.loads(data)
            finally:
                if handle is not None:
                    self.call({'execute': 'guest-file-close', 'arguments': {'handle': handle}})
                self.execute(['/usr/bin/python3', GUEST + '/rpc.py', '--remove-reply', identifier], timeout=15)
        if code or not envelope.get('ok'):
            raise RuntimeError(envelope.get('error', 'Steam VM request failed.'))
        return envelope['result']


class VMNetwork:
    is_vm = True

    def __init__(self, root=None):
        self.profile = Path(root or vm_root())
        self.cancelled = threading.Event()
        self.container = None  # Compatibility token: verified VM session, never a Docker container.
        self.directory = None
        self.moon_confirmed = self.steam_confirmed = self.hubcap_confirmed = False

    @property
    def cfg(self):
        return configuration(self.profile)

    @property
    def agent(self):
        return Agent(self.cfg)

    def check_cancelled(self):
        if self.cancelled.is_set():
            raise RuntimeError('Cancelled')

    def cancel(self):
        self.cancelled.set()

    def boot(self, progress=lambda text: None, desktop=False):
        cfg = self.cfg
        self.resume()
        progress('Starting the Steam VM…')
        installer_module().open_vm(cfg, desktop=desktop)
        deadline = time.monotonic() + 180
        while True:
            self.check_cancelled()
            try:
                self.agent.call({'execute': 'guest-ping'})
                return
            except RuntimeError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('VM did not finish booting. Open its desktop and check setup.')
                time.sleep(1)

    def connect(self, preferences, username=None, password=None, deadline=90, progress=lambda text: None):
        # Reuse a verified connection; allow the guest VPN daemon to finish starting.
        self.boot(progress)
        self.check_cancelled()
        try:state=self.agent.rpc({'command':'vpn_check'})
        except RuntimeError:state={}
        if state.get('connected') is True and state.get('protected') is True:
            return self.check()
        progress('Connecting and checking NordVPN inside the VM…')
        for attempt in range(3):
            self.check_cancelled()
            try:
                self.agent.rpc({'command': 'vpn_connect', 'country': preferences.country, 'protocol': preferences.protocol})
                return self.check()
            except RuntimeError:
                if attempt == 2:raise
                progress('Waiting for NordVPN to finish starting; retrying…')
                if self.cancelled.wait(3):self.check_cancelled()
                try:return self.check()
                except RuntimeError:pass

    def check(self):
        self.resume()
        state = self.agent.rpc({'command': 'vpn_check'})
        if state.get('connected') is not True:
            self.container = None
            raise RuntimeError('VM VPN is unavailable. Open the desktop and sign into NordVPN.')
        self.container = self.cfg['domain']
        return 'VPN connected inside Steam VM. Steam and metadata traffic are protected.'

    def disconnect(self):
        if self.container:
            from .vpn_lifecycle import has_downloads
            if has_downloads(self):
                raise RuntimeError('Pause or cancel queued downloads before disconnecting the VM VPN.')
            self.agent.rpc({'command': 'disconnect'})
        self.container = None
        return 'Steam stopped and VM VPN disconnected. Games and logins retained.'

    def open_desktop(self):
        self.resume()
        installer_module().open_vm(self.cfg)
        return 'Steam VM desktop opened.'

    def suspend(self):
        from .vpn_lifecycle import has_downloads
        if has_downloads(self):return False
        installer=installer_module()
        state=installer.execute(['virsh','-c','qemu:///session','domstate',self.cfg['domain']]).stdout.strip()
        if state!='running':return False
        installer.execute(['virsh','-c','qemu:///session','suspend',self.cfg['domain']])
        self.session_suspended=True
        return True

    def resume(self):
        if not getattr(self,'session_suspended',False):return
        installer_module().execute(['virsh','-c','qemu:///session','resume',self.cfg['domain']])
        self.session_suspended=False

    def close_session_background(self):
        """Request normal guest shutdown without blocking Qt's quit handler."""
        if not (self.profile/'vm.json').exists():return
        cfg=self.cfg
        installer=installer_module()
        script="""
import subprocess,sys
base=[sys.argv[1],'-c','qemu:///session']
def run(*args):
    return subprocess.run(base+list(args),capture_output=True,text=True,timeout=20)
try:
    state=run('domstate',sys.argv[2])
    if state.returncode:raise RuntimeError(state.stderr.strip())
    if state.stdout.strip()=='paused':run('resume',sys.argv[2])
    if state.stdout.strip() in ('running','paused'):
        result=run('shutdown',sys.argv[2])
        if result.returncode:raise RuntimeError(result.stderr.strip())
    print('Normal VM shutdown requested.',flush=True)
except Exception as error:
    print('VM shutdown failed: '+str(error),flush=True)
"""
        binary=shutil.which('virsh')
        if not binary:raise RuntimeError('virsh is unavailable for VM shutdown.')
        log_path=self.profile/'shutdown.log'
        descriptor=os.open(log_path,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
        with os.fdopen(descriptor,'ab') as log:
            subprocess.Popen(['/usr/bin/python3','-c',script,binary,cfg['domain']],
                             env=installer.host_environment(),stdin=subprocess.DEVNULL,
                             stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        self.container=None

    def close_session(self):
        if not (self.profile/'vm.json').exists():return
        from .vpn_lifecycle import has_downloads
        if has_downloads(self):return
        installer=installer_module()
        cfg=self.cfg
        state=installer.execute(['virsh','-c','qemu:///session','domstate',cfg['domain']]).stdout.strip()
        if state not in ('running','paused'):
            self.container=None
            return
        self.resume()
        try:self.agent.rpc({'command':'disconnect'})
        except RuntimeError:pass  # First-boot setup may not have installed the bridge yet.
        installer.execute(['virsh','-c','qemu:///session','shutdown',cfg['domain']])
        self.container=None

    def set_shared_folder(self, folder):
        folder=Path(folder).expanduser().resolve()
        cfg=self.cfg
        if folder==Path(cfg['shared']).resolve():return
        from .vpn_lifecycle import has_downloads
        if has_downloads(self):raise RuntimeError('Pause or cancel downloads before changing the shared folder.')
        installer=installer_module()
        folder.mkdir(parents=True,exist_ok=True)
        # Validate isolation and mount identity before changing the running VM.
        candidate=installer.configuration(self.profile,folder)
        cfg.update(shared=candidate['shared'],shared_mount=candidate['shared_mount'])
        for relative in (Path(cfg['staging'])/'downloading',Path(cfg['staging'])/'temp',Path('Workshop')):
            (folder/relative).mkdir(parents=True,exist_ok=True)
        state=installer.execute(['virsh','-c','qemu:///session','domstate',cfg['domain']]).stdout.strip()
        restart=state in ('running','paused')
        if restart:
            self.close_session()
            deadline=time.monotonic()+45
            while installer.execute(['virsh','-c','qemu:///session','domstate',cfg['domain']]).stdout.strip()!='shut off':
                if time.monotonic()>deadline:raise RuntimeError('VM is still shutting down. Retry saving the folder shortly.')
                time.sleep(1)
        import pwd
        identity=pwd.getpwuid(os.getuid()).pw_name
        text=installer.service_text(cfg,installer.ensure_daemon(self.profile),installer.subordinate_id('/etc/subuid',identity),installer.subordinate_id('/etc/subgid',identity))
        installer.execute(['systemctl','--user','stop',cfg['service']])
        (self.profile/'virtiofs.service').write_text(text)
        installer.register_service(cfg)
        path=self.profile/'vm.json'
        temporary=self.profile/'vm.json.tmp'
        temporary.write_text(json.dumps(cfg,indent=2));temporary.chmod(0o600);temporary.replace(path)
        self.container=None
        if restart:self.boot()

    def delete_vm(self,progress=lambda text:None):
        from .vpn_lifecycle import has_downloads
        if has_downloads(self):raise RuntimeError('Pause or cancel downloads before deleting the VM.')
        cfg=self.cfg;root=self.profile.resolve();shared=Path(cfg['shared']).resolve()
        if self.profile.is_symlink() or root==Path.home().resolve() or root==Path('/') or shared==root or shared.is_relative_to(root):
            raise RuntimeError('Refusing to delete an unsafe VM profile.')
        installer=installer_module()
        xml=installer.execute(['virsh','-c','qemu:///session','dumpxml',cfg['domain']]).stdout
        domain=ET.fromstring(xml)
        disks=domain.findall('./devices/disk[@device="disk"]/source')
        if len(disks)!=1 or Path(disks[0].get('file','')).resolve()!=root/'disk.qcow2':
            raise RuntimeError('VM disk does not belong to this profile; deletion refused.')
        fragment=installer.execute(['systemctl','--user','show',cfg['service']+'.service','--property=FragmentPath','--value']).stdout.strip()
        if not fragment or Path(fragment).resolve()!=root/(cfg['service']+'.service'):
            raise RuntimeError('Shared-folder service does not belong to this profile; deletion refused.')
        state=installer.execute(['virsh','-c','qemu:///session','domstate',cfg['domain']]).stdout.strip()
        if state=='paused':
            installer.execute(['virsh','-c','qemu:///session','resume',cfg['domain']])
            self.session_suspended=False
        if state in ('running','paused') and self.agent.rpc({'command':'status'}).get('setup_running'):
            raise RuntimeError('Wait for VM setup to finish before deleting it.')
        progress('Stopping the owned Steam VM…')
        self.close_session()
        deadline=time.monotonic()+45
        while installer.execute(['virsh','-c','qemu:///session','domstate',cfg['domain']]).stdout.strip()!='shut off':
            if time.monotonic()>deadline:raise RuntimeError('VM did not shut down. Its disk was retained; retry after shutdown.')
            time.sleep(.5)
        installer.execute(['systemctl','--user','disable','--now',cfg['service']+'.service'])
        installer.execute(['virsh','-c','qemu:///session','undefine',cfg['domain'],'--managed-save'])
        progress('Removing private VM files; retaining shared games…')
        shutil.rmtree(root)
        installer.execute(['systemctl','--user','daemon-reload'])
        self.container=None;self.session_suspended=False
        return 'Steam VM deleted. Shared games and Workshop files were kept.'

    def http(self, url, headers=None, data=None):
        self.resume()
        self.check_cancelled()
        payload = {'command': 'http', 'url': url, 'headers': headers or {},
                   'body': base64.b64encode(json.dumps(data).encode()).decode() if data is not None else None}
        return self.agent.rpc(payload)


class VMSteamRuntime:
    is_vm = True
    direct_install = True

    def __init__(self, network, root=None):
        self.network = network
        cfg = network.cfg
        self.root = Path(cfg['shared']) / cfg['staging']
        self.name = cfg['domain']
        self.volume = None

    def installation_path(self, state):
        relative=PurePosixPath(state.get('directory') or '')
        if len(relative.parts)!=3 or relative.parts[:2]!=('steamapps','common') or relative.parts[2] in ('.','..'):
            raise ValueError('Invalid Steam installation path.')
        shared=Path(self.network.cfg['shared']).resolve()
        source=(shared/relative.parts[2]).resolve()
        if source.parent!=shared or not source.is_dir():
            raise ValueError('Steam installation files are unavailable or outside the shared folder.')
        return source

    def start(self):
        self.network.boot()
        self.network.check()
        return 'Steam VM is running.'

    def stop(self):
        from .vpn_lifecycle import has_downloads
        if has_downloads(self.network):
            raise RuntimeError('Pause or cancel queued downloads before stopping Steam.')
        self.request('stop')
        return 'Steam stopped in the VM. VPN, games, and logins retained.'

    def request(self, command, appid=None, **choices):
        allowed = ('steam_login_view', 'provider_login', 'authentication_status', 'start', 'status', 'stop', 'install_moon', 'add',
                   'add_status', 'cancel_add', 'install', 'download_status', 'pause', 'eula_status',
                   'eula_accept', 'installed_games', 'uninstall', 'retail_selection', 'has_game',
                   'finish_export', 'workshop')
        if command not in allowed:
            raise ValueError('Unknown runtime action.')
        if command == 'install' and choices.get('platform') not in ('windows', 'linux'):
            raise ValueError('Select Windows or Linux for isolated Steam. macOS is unavailable.')
        return self.network.agent.rpc({'command': command, 'appid': appid, **choices})


def update_runtime(network, progress=lambda text: None):
    """Install this plugin's bridge assets without copying host or other VM logins."""
    try:
        if network.agent.rpc({'command':'status'}).get('setup_running') is True:
            progress('Steam VM setup is already running; retaining its active bridge.');return
    except RuntimeError:
        pass  # The first bridge installation has no RPC endpoint yet.
    installer = installer_module()
    files = {name: installer.asset_path(name).read_text() for name in installer.ASSET_FILES
             if name in ('runtime_server.py', 'rpc.py', 'setup-runtime.py', 'guest_control.py', 'steam_sign_in.py') or name.startswith('steam/')}
    payload = json.dumps({'files': files, 'staging': network.cfg['staging']}).encode()
    # Fixed installer code; asset content travels on stdin, never shell argv.
    script = r'''
import json,os,pathlib,subprocess,sys
path=pathlib.Path(sys.argv[1])
payload=json.loads(path.read_text());path.unlink()
root=pathlib.Path('/usr/local/lib/playlite-vm')
root.mkdir(parents=True,exist_ok=True)
for name,content in payload['files'].items():
    path=root/name
    if not path.resolve().is_relative_to(root.resolve()):raise ValueError('Invalid asset path')
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(content);tmp.chmod(0o644);tmp.replace(path)
config=pathlib.Path('/etc/playlite-vm.json')
if config.exists() and json.loads(config.read_text()).get('staging')!=payload['staging']:
    raise ValueError('VM staging configuration mismatch')
config.write_text(json.dumps({'staging':payload['staging']}))
subprocess.run(['/usr/bin/python3',str(root/'setup-runtime.py')],check=True)
subprocess.run(['systemctl','restart','playlite-steam-bridge.service'],check=True)
'''
    progress('Installing the Steam VM command bridge…')
    upload='/var/tmp/playlite-vm-update-'+uuid.uuid4().hex+'.json'
    network.agent.write_file(upload,payload)
    code, _, _ = network.agent.execute(['/usr/bin/python3', '-c', script,upload], timeout=90)
    if code:
        raise RuntimeError('VM bridge installation failed. Check the VM service log.')
    progress('Steam VM bridge is ready.')
