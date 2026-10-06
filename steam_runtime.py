"""Dedicated GUI Steam runtime, sharing only the verified VPN network."""
import json
import os
from pathlib import Path
import socket
import subprocess
from .constants import IMAGE,WORKER_LABEL,PROFILE_SUFFIX,OWNER_LABEL,OWNER

RUNTIME_IMAGE='playlite-steam-runtime'+PROFILE_SUFFIX+':test'

class SteamRuntime:
    def __init__(self,network,root=None):
        self.network=network
        self.root=Path(root or Path.home()/'.local/share/playlite/steam-runtime')
        self.name=f'playlite-steam-runtime-{os.getuid()}'+PROFILE_SUFFIX
        self.volume=f'playlite-steam-home-{os.getuid()}'+PROFILE_SUFFIX

    def stop(self):
        """Release the owned desktop container, retaining its VPN and saved volume."""
        from .vpn_lifecycle import has_downloads
        if has_downloads(self.network):raise RuntimeError('Pause or cancel queued and active downloads before stopping Steam.')
        result=subprocess.run(['docker','inspect',self.name],capture_output=True,text=True)
        if result.returncode:return 'Steam environment is already stopped.'
        entry=json.loads(result.stdout)[0]
        gateway=self.network.container
        if (not gateway or entry['Config'].get('Labels',{}).get(WORKER_LABEL)!=gateway
                or entry['HostConfig']['NetworkMode']!='container:'+gateway):
            raise RuntimeError('Refusing to stop a Steam environment outside this isolated VPN session.')
        try:self.request('stop')
        except (OSError,RuntimeError):pass
        if has_downloads(self.network):raise RuntimeError('A download was queued; Steam environment was retained.')
        subprocess.run(['docker','stop','--time','10',self.name],capture_output=True,text=True,check=True)
        relay=vars(self.network).pop('_steam_desktop_relay',None)
        if relay and relay[0].poll() is None:relay[0].terminate()
        return 'Steam environment stopped. VPN remains connected; saved games and login retained.'

    def arguments(self):
        self.network.check()
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700);self.root.chmod(0o700)
        for child in ('control','library'):
            path=self.root/child
            created=not path.exists()
            path.mkdir(exist_ok=True,mode=0o777);path.chmod(0o777)
            if created:
                from playlite.storage import atomic_json
                marker=self.root/'environment-owner.json'
                document=json.loads(marker.read_text()) if marker.exists() else {'owner':OWNER,'directories':[]}
                if document.get('owner')!=OWNER:raise RuntimeError('Steam directory belongs to another installation.')
                if child not in document['directories']:document['directories'].append(child)
                atomic_json(marker,document)
        args=self.network.probe_args('')
        args[args.index('32')]='2048';args[args.index('64m')]='3g'
        args[args.index('--entrypoint')+1]='python3'
        index=args.index(IMAGE)
        args[index:]=[RUNTIME_IMAGE,'/opt/bridge.py']
        args[index:index]=['--init','--name',self.name,'--shm-size','512m',
            # Docker's default AppArmor profile denies mounts inside Steam's
            # nested user namespace. Other container restrictions remain active.
            '--security-opt','apparmor=unconfined',
            '--security-opt','seccomp='+str(Path(__file__).parent/'tools/steam/seccomp.json'),
            '--mount',f'type=volume,src={self.volume},dst=/home/steam',
            '--mount',f'type=bind,src={self.root/"library"},dst=/library',
            '--mount',f'type=bind,src={self.root/"control"},dst=/control',
            '--tmpfs','/tmp:rw,nosuid,nodev,size=512m',
            '-e','HOME=/home/steam','-e','DISPLAY=:1','-e','LIBGL_ALWAYS_SOFTWARE=1']
        return args

    def start(self):
        self.network.check()
        result=subprocess.run(['docker','inspect',self.name],capture_output=True,text=True)
        if result.returncode==0:
            entry=json.loads(result.stdout)[0]
            if entry['State']['Running'] and entry['HostConfig']['NetworkMode']=='container:'+self.network.container:
                return 'Isolated Steam environment is running.'
            subprocess.run(['docker','rm','-f',self.name],capture_output=True,check=True)
        subprocess.run(['docker','image','inspect',RUNTIME_IMAGE],capture_output=True,check=True)
        volume=subprocess.run(['docker','volume','inspect',self.volume],capture_output=True)
        if volume.returncode:
            subprocess.run(['docker','volume','create','--label',OWNER_LABEL+'='+OWNER,self.volume],capture_output=True,check=True)
        # Local Steam/Lumen/VNC IPC stays inside this dedicated network namespace.
        try:self.network.docker('exec',self.network.container,'iptables','-C','PLAYLITE_WORKER','-o','lo','-j','RETURN')
        except RuntimeError:self.network.docker('exec',self.network.container,'iptables','-I','PLAYLITE_WORKER','1','-o','lo','-j','RETURN')
        args=self.arguments()
        args.insert(1,'--detach')
        launched=subprocess.run(['docker',*args],capture_output=True,text=True)
        if launched.returncode:
            raise RuntimeError('Could not start isolated Steam: '+(launched.stderr.strip() or 'Docker rejected the container launch.'))
        return 'Starting isolated Steam environment. Refresh status in a moment.'

    def request(self,command,appid=None,*,platform=None,language='english',dlc=None,eula_id=None,eula_version=None):
        if command not in ('authentication_status','start','status','stop','install_moon','add','add_status','cancel_add','install','download_status','pause','eula_status','eula_accept','installed_games','uninstall','retail_selection','has_game','finish_export'):raise ValueError('Unknown runtime action.')
        if command=='install' and platform not in ('windows','linux'):
            raise ValueError('Select Windows or Linux for isolated Steam. macOS is unavailable.')
        if command in ('authentication_status','start','install_moon','add','install','eula_status','eula_accept','installed_games','uninstall','retail_selection','has_game'):self.network.check()
        with socket.socket(socket.AF_UNIX) as connection:
            # Older running bridges copy a verified private-library install
            # synchronously. Allow them to finish during an in-place upgrade;
            # new bridges report asynchronous export progress instead.
            connection.settimeout(60 if command=='authentication_status' else 1800 if command=='download_status' else 120 if command=='retail_selection' else 90 if command=='finish_export' else 35 if command in ('install','eula_status','eula_accept','has_game','installed_games','uninstall') else 10)
            directory=os.open(self.root/'control',os.O_RDONLY|os.O_DIRECTORY)
            try:
                connection.connect(f'/proc/self/fd/{directory}/bridge.sock')
            finally:os.close(directory)
            payload={'command':command,'appid':appid}
            if command=='eula_accept':payload.update(eula_id=eula_id,eula_version=eula_version)
            if command=='install':payload.update(platform=platform,language=language,dlc=dlc or [])
            connection.sendall(json.dumps(payload).encode()+b'\n')
            data=b''
            while not data.endswith(b'\n'):
                chunk=connection.recv(8192)
                if not chunk or len(data)+len(chunk)>(1048576 if command=='eula_status' else 65536):raise RuntimeError('Invalid runtime response.')
                data+=chunk
        response=json.loads(data)
        if not response.get('ok'):raise RuntimeError(response.get('error','Runtime request failed.'))
        return response['result']


def installation_status(network):
    """Inspect the existing installation without starting or provisioning it."""
    image=subprocess.run(['docker','image','inspect',RUNTIME_IMAGE],capture_output=True,timeout=10)
    if image.returncode:return 'missing'
    runtime=SteamRuntime(network)
    volume=subprocess.run(['docker','volume','inspect',runtime.volume],capture_output=True,timeout=10)
    if volume.returncode:return 'missing'
    if not network.container:return 'stopped'
    try:state=runtime.request('status')
    except (OSError,RuntimeError):return 'stopped'
    if not state.get('steam_ready'):return 'incomplete'
    return 'running' if state.get('steam_running') else 'stopped'


def start_with_vpn(network,progress=lambda message:None):
    """Launch an installed private Steam client whenever its VPN is opened."""
    import time
    if not network.container:return
    image=subprocess.run(['docker','image','inspect',RUNTIME_IMAGE],capture_output=True,timeout=30)
    if image.returncode:
        return 'Steam is not installed. Use Set up isolated Steam.'
    runtime=SteamRuntime(network)
    volume=subprocess.run(['docker','volume','inspect',runtime.volume],capture_output=True,timeout=30)
    if volume.returncode:
        return 'Steam is not installed. Use Set up isolated Steam.'
    progress('Starting isolated Steam…')
    runtime.start()
    deadline=time.monotonic()+90
    while True:
        network.check_cancelled()
        try:
            state=runtime.request('status')
            if isinstance(state,dict) and not state.get('steam_ready'):
                return 'VPN connected. Steam setup is incomplete; use Set up isolated Steam.'
            runtime.request('start');return 'Steam started.'
        except (OSError,RuntimeError):
            if time.monotonic()>=deadline:
                raise RuntimeError('VPN connected, but isolated Steam could not start. Open Steam setup to retry.')
            network.cancelled.wait(.5)
