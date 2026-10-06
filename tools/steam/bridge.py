"""Private container control plane. No host protocol handler or shell RPC."""
import json
import os
import re
from pathlib import Path
import signal
import socketserver
import subprocess
import threading
import time
import uuid

CONTROL=Path('/control');HOME=Path('/home/steam');LIBRARY=Path('/library')
children=[];steam=None;installer=None
lock=threading.Lock()

def steam_running():
    for entry in Path('/proc').iterdir():
        if not entry.name.isdecimal():continue
        try:
            if (entry/'comm').read_text().strip()=='steam' and entry.stat().st_uid==os.getuid():
                return True
        except (OSError,ValueError):pass
    return False

def steam_binary():
    wrapper=HOME/'.local/share/SLSsteam/path/steam'
    return str(wrapper) if wrapper.is_file() else '/usr/games/steam'

def app_id(value):
    if type(value) is not int or not 0<value<2**32:raise ValueError('Invalid App ID.')
    return value

def parse_vdf(source):
    if len(source)>1024*1024:raise ValueError('Steam manifest exceeds limit.')
    tokens=re.findall(r'"((?:\\.|[^"\\])*)"|([{}])',source)
    index=0
    def block(depth=0):
        nonlocal index
        if depth>16:raise ValueError('Steam manifest nesting exceeds limit.')
        result={}
        while index<len(tokens):
            key,brace=tokens[index];index+=1
            if brace=='}':return result
            if brace or index>=len(tokens):raise ValueError('Invalid Steam manifest.')
            value,brace=tokens[index];index+=1
            if brace=='{':value=block(depth+1)
            elif brace:raise ValueError('Invalid Steam manifest.')
            result[key]=value
        if depth:raise ValueError('Incomplete Steam manifest.')
        return result
    return block()

def download_status(app):
    app_id(app)
    manifest=LIBRARY/'steamapps'/f'appmanifest_{app}.acf'
    if not manifest.exists():return {'installed':False,'downloaded':0,'total':0,'depots':[]}
    state=parse_vdf(manifest.read_text()).get('AppState',{})
    directory=LIBRARY/'steamapps/common'/str(state.get('installdir',''))
    if directory.resolve().parent!=(LIBRARY/'steamapps/common').resolve():
        raise ValueError('Invalid Steam installation directory.')
    flags=int(state.get('StateFlags',0))
    installed=flags==4 and directory.is_dir()
    if installed:
        # Only game content is exposed to the host. Steam's private home stays private.
        for parent in (directory.parent.parent,directory.parent,directory):parent.chmod(0o755)
        for path in directory.rglob('*'):
            if not path.is_symlink():path.chmod(0o755 if path.is_dir() else path.stat().st_mode&0o777|0o444)
    return {'installed':installed,'flags':flags,
            'downloaded':int(state.get('BytesDownloaded',0)),
            'total':int(state.get('BytesToDownload',0)),
            'directory':str(directory.relative_to(LIBRARY)),
            'branch':state.get('UserConfig',{}).get('BetaKey','public') or 'public',
            'depots':[int(value) for value in state.get('InstalledDepots',{}) if value.isdecimal()]}

def steam_action(request):
    payload={key:request[key] for key in ('action','appid','platform','language','dlc') if key in request}
    environment=dict(os.environ,PLAYLITE_STEAM_ACTION=json.dumps(payload))
    result=subprocess.run([str(HOME/'.local/share/Lumen/lumen'),'--test','/opt/steam_control.lua'],
                          cwd=HOME/'.local/share/Lumen',env=environment,capture_output=True,text=True,timeout=12)
    prefix='PLAYLITE_STEAM_RESULT '
    lines=[line[len(prefix):] for line in result.stdout.splitlines() if line.startswith(prefix)]
    if result.returncode or len(lines)!=1:
        raise RuntimeError('Steam could not apply the selected content settings. Check its isolated desktop.')
    response=json.loads(lines[0])
    if request.get('action')=='install' and not response.get('requested'):
        raise RuntimeError('Steam rejected the selected '+str(response.get('failed_stage','content'))+' settings.')
    return response

def prepare_library():
    library=HOME/'.steam/debian-installation/steamapps/libraryfolders.vdf'
    library.parent.mkdir(parents=True,exist_ok=True)
    (LIBRARY/'steamapps').mkdir(parents=True,exist_ok=True)
    source=library.read_text() if library.exists() else '\"libraryfolders\"\n{\n \"0\" { \"path\" \"/home/steam/.steam/debian-installation\" \"apps\" {} }\n}\n'
    if re.search(r'"path"\s+"/library"',source):return
    if len(source)>65536 or not source.rstrip().endswith('}'):
        raise RuntimeError('Cannot safely update Steam library configuration.')
    keys=[int(value) for value in re.findall(r'^\s{0,1}"(\d+)"',source,re.MULTILINE)]
    key=max(keys,default=0)+1
    entry=f'\n "{key}" {{ "path" "/library" "label" "Playlite downloads" "apps" {{}} }}\n'
    temporary=library.with_suffix('.playlite-tmp')
    temporary.write_text(source.rstrip()[:-1]+entry+'}\n')
    temporary.replace(library)

def launch(args,log,umask=0o077,environment=None):
    output=open(CONTROL/log,'ab',buffering=0)
    try:process=subprocess.Popen(args,stdout=output,stderr=subprocess.STDOUT,start_new_session=True,umask=umask,env=environment)
    finally:output.close()
    children.append(process)
    return process

def dispatch(request):
    global steam,installer
    command=request.get('command')
    if command=='status':
        return {'steam_running':steam_running(),
                'moon_installed':(HOME/'.local/share/Lumen/luatools').is_dir(),
                'moon_installing':installer is not None and installer.poll() is None,
                'moon_install_exit':installer.poll() if installer else None,
                'library':'/library'}
    if command=='start':
        if not steam_running():
            prepare_library()
            steam=launch([steam_binary(),'-no-cef-sandbox'],'steam.log',umask=0o022)
        lumen=HOME/'.local/share/Lumen'
        if (lumen/'lumen').is_file():
            environment=dict(os.environ,LUMEN_BACKEND_DIR=str(lumen/'luatools/backend'),LUMEN_LUA_DIR=str(lumen/'lua'))
            # Lumen's own flock prevents duplicate sidecars. Launching explicitly
            # also covers Steam's bootstrap/restart path skipping the wrapper.
            if not any(process.poll() is None and getattr(process,'playlite_lumen',False) for process in children):
                sidecar=launch([str(lumen/'lumen')],'lumen.log',environment=environment)
                sidecar.playlite_lumen=True
        return dispatch({'command':'status'})
    if command=='install_moon':
        if installer is None or installer.poll() is not None:
            installer=launch(['python3','/opt/setup_moon.py'],'moon-setup.log')
        return dispatch({'command':'status'})
    if command in ('add','add_status','cancel_add'):
        app=request.get('appid')
        app_id(app)
        identifier=uuid.uuid4().hex
        pending=CONTROL/'moon-request.tmp'
        pending.write_text(json.dumps({'id':identifier,'command':command,'appid':app}))
        pending.replace(CONTROL/'moon-request.json')
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            path=CONTROL/'moon-response.json'
            if path.exists() and path.stat().st_size<=65536:
                reply=json.loads(path.read_text())
                if reply.get('id')==identifier:
                    if not reply.get('ok'):raise RuntimeError('LuaMoon request failed.')
                    return json.loads(reply['result']) if isinstance(reply['result'],str) else reply['result']
            time.sleep(.1)
        (CONTROL/'moon-request.json').unlink(missing_ok=True)
        raise RuntimeError('LuaMoon bridge is not ready. Open Steam with LuaMoon loaded.')
    if command=='stop':
        if steam_running():
            launch([steam_binary(),'-shutdown'],'steam.log')
        return {'stopping':True}
    if command=='install':
        app=app_id(request.get('appid'))
        if request.get('platform') not in ('windows','linux'):
            raise ValueError('Choose Windows or Linux for isolated Steam. Use the depot downloader for macOS.')
        dlc=request.get('dlc',[])
        if not isinstance(dlc,list) or len(dlc)>256:raise ValueError('Invalid DLC selection.')
        for item in dlc:
            if not isinstance(item,dict) or type(item.get('enabled')) is not bool:raise ValueError('Invalid DLC selection.')
            app_id(item.get('id'))
        language=request.get('language','english')
        if not isinstance(language,str) or not re.fullmatch('[a-z]{1,32}',language):raise ValueError('Invalid language.')
        result=dispatch({'command':'add_status','appid':app})
        if not isinstance(result,dict) or result.get('state',{}).get('status')!='done':
            raise RuntimeError('LuaMoon must finish adding this App ID before installation.')
        if not steam_running():raise RuntimeError('Start isolated Steam first.')
        return steam_action({'action':'install','appid':app,'platform':request['platform'],'dlc':dlc,'language':language})
    if command=='download_status':return download_status(app_id(request.get('appid')))
    if command=='pause':return steam_action({'action':'pause','appid':app_id(request.get('appid'))})
    raise ValueError('Unsupported control command.')

class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(30)
        try:
            line=self.rfile.readline(65537)
            if len(line)>65536:raise ValueError('Control request exceeds limit.')
            request=json.loads(line)
            if not isinstance(request,dict):raise ValueError('Invalid request.')
            with lock:result=dispatch(request)
            response={'ok':True,'result':result}
        except (ValueError,RuntimeError) as error:response={'ok':False,'error':str(error)}
        except Exception:response={'ok':False,'error':'Container control request failed; see runtime logs.'}
        self.wfile.write(json.dumps(response).encode()+b'\n')

def stop(*_):
    for process in reversed(children):
        if process.poll() is None:
            try:os.killpg(process.pid,signal.SIGTERM)
            except ProcessLookupError:pass
    raise SystemExit(0)

if __name__=='__main__':
    os.umask(0o077)
    CONTROL.mkdir(exist_ok=True)
    for name in ('desktop.sock','web.sock'):
        path=CONTROL/name
        if path.exists():path.unlink()
    display_directory=Path('/tmp/.X11-unix')
    display_directory.mkdir(exist_ok=True)
    display_directory.chmod(0o1777)
    display=launch(['Xvfb',':1','-screen','0','1280x800x24','-nolisten','tcp'],'desktop.log')
    deadline=time.monotonic()+15
    while not (display_directory/'X1').exists():
        if display.poll() is not None or time.monotonic()>=deadline:
            raise RuntimeError('Virtual display did not start; see desktop.log.')
        time.sleep(.1)
    launch(['openbox'],'desktop.log')
    # VNC listens only inside the VPN namespace; access is through a private socket.
    launch(['x11vnc','-display',':1','-localhost','-forever','-shared','-nopw','-rfbport','5900'],'desktop.log')
    launch(['socat','UNIX-LISTEN:/control/desktop.sock,fork,mode=0666','TCP:127.0.0.1:5900'],'desktop.log')
    launch(['websockify','--web=/usr/share/novnc','127.0.0.1:6080','127.0.0.1:5900'],'desktop.log')
    launch(['socat','UNIX-LISTEN:/control/web.sock,fork,mode=0666','TCP:127.0.0.1:6080'],'desktop.log')
    path=CONTROL/'bridge.sock'
    if path.exists():path.unlink()
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    with socketserver.ThreadingUnixStreamServer(str(path),Handler) as server:
        path.chmod(0o666)
        server.serve_forever()
