"""Dedicated GUI Steam runtime, sharing only the verified VPN network."""
import json
import os
from pathlib import Path
import socket
import subprocess
from .constants import IMAGE

RUNTIME_IMAGE='playlite-steam-runtime:test'

class SteamRuntime:
    def __init__(self,network,root=None):
        self.network=network
        self.root=Path(root or Path.home()/'.local/share/playlite/steam-runtime')
        self.name=f'playlite-steam-runtime-{os.getuid()}'
        self.volume=f'playlite-steam-home-{os.getuid()}'

    def arguments(self):
        self.network.check()
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700);self.root.chmod(0o700)
        for child in ('control','library'):
            path=self.root/child;path.mkdir(exist_ok=True,mode=0o777);path.chmod(0o777)
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
        # Local Steam/Lumen/VNC IPC stays inside this dedicated network namespace.
        try:self.network.docker('exec',self.network.container,'iptables','-C','PLAYLITE_WORKER','-o','lo','-j','RETURN')
        except RuntimeError:self.network.docker('exec',self.network.container,'iptables','-I','PLAYLITE_WORKER','1','-o','lo','-j','RETURN')
        args=self.arguments()
        args.insert(1,'--detach')
        subprocess.run(['docker',*args],capture_output=True,text=True,check=True)
        return 'Starting isolated Steam environment. Refresh status in a moment.'

    def request(self,command,appid=None,*,platform=None,language='english',dlc=None):
        if command not in ('start','status','stop','install_moon','add','add_status','cancel_add','install','download_status','pause'):raise ValueError('Unknown runtime action.')
        if command=='install' and platform not in ('windows','linux'):
            raise ValueError('Select Windows or Linux for isolated Steam. Use Depot downloader for macOS.')
        if command in ('start','install_moon','add','install'):self.network.check()
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(30 if command=='install' else 10)
            connection.connect(str(self.root/'control/bridge.sock'))
            payload={'command':command,'appid':appid}
            if command=='install':payload.update(platform=platform,language=language,dlc=dlc or [])
            connection.sendall(json.dumps(payload).encode()+b'\n')
            data=b''
            while not data.endswith(b'\n'):
                chunk=connection.recv(8192)
                if not chunk or len(data)+len(chunk)>65536:raise RuntimeError('Invalid runtime response.')
                data+=chunk
        response=json.loads(data)
        if not response.get('ok'):raise RuntimeError(response.get('error','Runtime request failed.'))
        return response['result']
