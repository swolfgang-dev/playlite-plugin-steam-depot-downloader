"""Private container control plane. No host protocol handler or shell RPC."""
import json
import os
import re
from pathlib import Path
import signal
import shutil
import hashlib
from html.parser import HTMLParser
import urllib.request
import urllib.parse
import socketserver
import subprocess
import threading
import time
import uuid
import xml.etree.ElementTree as ET

CONTROL=Path('/control');HOME=Path('/home/steam');LIBRARY=Path('/library')
children=[];steam=None;installer=None
lock=threading.Lock()
exports={}


def prepare_openbox_config(source=Path('/etc/xdg/openbox/rc.xml'),destination=Path('/tmp/playlite-openbox.xml')):
    """Avoid Openbox's redundant focus event swallowing Steam popup clicks."""
    namespace='http://openbox.org/3.4/rc'
    ET.register_namespace('',namespace)
    tree=ET.parse(source)
    for context in tree.findall(f'.//{{{namespace}}}context[@name="Client"]'):
        for binding in context:
            for action in list(binding):
                if action.tag==f'{{{namespace}}}action' and action.get('name')=='Focus':
                    position=list(binding).index(action)
                    binding.remove(action)
                    conditional=ET.Element(f'{{{namespace}}}action',name='If')
                    query=ET.SubElement(conditional,f'{{{namespace}}}query')
                    ET.SubElement(query,f'{{{namespace}}}focused').text='no'
                    ET.SubElement(conditional,f'{{{namespace}}}then').append(action)
                    binding.insert(position,conditional)
    tree.write(destination,encoding='utf-8',xml_declaration=True)
    return destination

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

def paused_install(app):
    for library in (LIBRARY,HOME/'.steam/steam'):
        manifest=library/'steamapps'/f'appmanifest_{app}.acf'
        if manifest.exists():
            state=parse_vdf(manifest.read_text()).get('AppState',{})
            return bool(int(state.get('StateFlags',0)) & 512)
    return False


def content_events(app):
    """Return bounded app-specific lifecycle messages, excluding account/CDN data."""
    path=HOME/'.steam/steam/logs/content_log.txt'
    try:
        with path.open('rb') as stream:
            stream.seek(0,2);size=stream.tell();stream.seek(max(0,size-131072))
            source=stream.read(131072).decode('utf-8',errors='replace')
    except OSError:return []
    allowed=('state changed :','App update changed :','finished update,','scheduler finished :','update canceled :')
    marker=f'AppID {app} '
    return [line.strip()[:700] for line in source.splitlines() if marker in line and any(term in line for term in allowed)][-12:]


def prepare_export(directory,exported):
    """Copy verified content without blocking the control/status socket."""
    key=str(exported)
    if key in exports:return exports[key]
    job={'copied':0,'total':0,'error':None};exports[key]=job
    def copy():
        temporary=exported.with_name(exported.name+'-'+uuid.uuid4().hex+'.tmp')
        try:
            paths=list(directory.rglob('*'))
            for path in paths:
                if path.is_symlink() and (path.readlink().is_absolute() or not path.resolve().is_relative_to(directory.resolve())):
                    raise ValueError('Steam content contains a link outside the game folder.')
            job['total']=sum(path.stat().st_size for path in paths if path.is_file() and not path.is_symlink())
            exported.parent.mkdir(parents=True,exist_ok=True)
            def copy_file(source,target):
                shutil.copy2(source,target)
                job['copied']+=Path(source).stat().st_size
                return target
            shutil.copytree(directory,temporary,symlinks=True,copy_function=copy_file)
            for path in [temporary,*temporary.rglob('*')]:
                if not path.is_symlink():path.chmod(0o755 if path.is_dir() else path.stat().st_mode&0o777|0o444)
            temporary.rename(exported)
        except Exception as error:job['error']=str(error)
        finally:
            if temporary.exists():shutil.rmtree(temporary)
    job['thread']=threading.Thread(target=copy,daemon=True);job['thread'].start()
    return job


def download_status(app):
    app_id(app)
    manifest=LIBRARY/'steamapps'/f'appmanifest_{app}.acf'
    library=LIBRARY
    if not manifest.exists():
        library=HOME/'.steam/steam'
        manifest=library/'steamapps'/f'appmanifest_{app}.acf'
    if not manifest.exists():return {'installed':False,'downloaded':0,'total':0,'depots':[],'events':content_events(app)}
    state=parse_vdf(manifest.read_text()).get('AppState',{})
    directory=library/'steamapps/common'/str(state.get('installdir',''))
    if directory.resolve().parent!=(library/'steamapps/common').resolve():
        raise ValueError('Invalid Steam installation directory.')
    flags=int(state.get('StateFlags',0))
    installed=flags==4 and directory.is_dir()
    export_pending=False;export_progress={}
    transfer={}
    if flags & 1024:
        try:
            live=steam_action({'action':'download_progress','appid':app}).get('transfer')
            if isinstance(live,dict) and live.get('total',0)>0:transfer=live
        except (OSError,RuntimeError,subprocess.SubprocessError):pass
    if installed and library!=LIBRARY:
        # Steam may resume an existing installation in its default private
        # library. Export only verified game content, never the private home.
        fingerprint=hashlib.sha256(json.dumps([state.get('buildid'),state.get('InstalledDepots'),state.get('UserConfig')],sort_keys=True).encode()).hexdigest()[:16]
        exported=LIBRARY/'steamapps/common'/f'playlite-import-{app}-{fingerprint}'
        if not exported.exists():
            export_progress=prepare_export(directory,exported)
            if export_progress['error']:raise RuntimeError('Steam verified the game, but preparing its export failed: '+export_progress['error'])
            installed=False;export_pending=True
        else:directory=exported
    if installed:
        # Only game content is exposed to the host. Steam's private home stays private.
        for parent in (directory.parent.parent,directory.parent,directory):parent.chmod(0o755)
        for path in directory.rglob('*'):
            if not path.is_symlink():path.chmod(0o755 if path.is_dir() else path.stat().st_mode&0o777|0o444)
    return {'installed':installed,'flags':flags,'events':content_events(app),
            'export_pending':export_pending,'export_copied':export_progress.get('copied',0),'export_total':export_progress.get('total',0),
            'disk_processed':int(transfer.get('disk_processed',state.get('BytesStaged',0))),
            'disk_total':int(transfer.get('disk_total',state.get('BytesToStage',0))),
            'downloaded':int(transfer.get('downloaded',state.get('BytesDownloaded',0))),
            'total':int(transfer.get('total',state.get('BytesToDownload',0))),
            'speed':max(0,int(transfer.get('speed',0))),
            'directory':str(directory.relative_to(LIBRARY)) if directory.is_relative_to(LIBRARY) else None,
            'branch':state.get('UserConfig',{}).get('BetaKey','public') or 'public',
            'depots':[int(value) for value in state.get('InstalledDepots',{}) if value.isdecimal()]}

def installed_games():
    result=steam_action({'action':'installed_games','appid':1})
    folders=result.get('folders')
    if not isinstance(folders,list):raise RuntimeError('Steam could not list its installed games. Wait for the client to finish starting.')
    games=[]
    permitted={LIBRARY.resolve(),(HOME/'.steam/steam').resolve(),(HOME/'.steam/debian-installation').resolve()}
    for folder in folders:
        library=Path(folder['path'])
        if not folder.get('mounted') or library.resolve() not in permitted:continue
        for item in folder.get('apps',[]):
            app=app_id(item.get('appid'))
            manifest=library/'steamapps'/f'appmanifest_{app}.acf'
            if not manifest.is_file():continue
            state=parse_vdf(manifest.read_text()).get('AppState',{})
            flags=int(state.get('StateFlags',0))
            status='Installed' if flags==4 else 'Downloading' if flags&1024 else 'Paused' if flags&512 else 'Incomplete'
            games.append({'appid':app,'name':str(item.get('name') or state.get('name') or f'Steam App {app}'),
                          'size':int(item.get('size',0))+int(item.get('staged',0)),
                          'status':status,'library':str(library)})
            if len(games)>1024:raise RuntimeError('Installed content list exceeds the limit.')
    return {'games':sorted(games,key=lambda row:row['name'].casefold())}


def finish_export(app):
    """Release private game copies only after the host verified its export."""
    game=next((row for row in installed_games()['games'] if row['appid']==app),None)
    if game:
        if game['status']!='Installed':raise RuntimeError('Private game is active or incomplete; cleanup deferred.')
        result=steam_action({'action':'uninstall','appid':app})
        if not result.get('requested'):raise RuntimeError(result.get('error') or 'Steam rejected private-copy cleanup.')
        deadline=time.monotonic()+45
        while any(row['appid']==app for row in installed_games()['games']):
            if time.monotonic()>=deadline:raise RuntimeError('Private-copy uninstall is still pending. Check isolated Steam.')
            time.sleep(1)
    common=LIBRARY/'steamapps/common'
    for path in common.glob(f'playlite-import-{app}-*'):
        if not re.fullmatch(r'playlite-import-'+str(app)+r'-[0-9a-f]{16}',path.name):continue
        if path.is_symlink():raise RuntimeError('Refusing an external export cache link.')
        shutil.rmtree(path)
        exports.pop(str(path),None)
    return {'removed':True}


def steam_action(request):
    payload={key:request[key] for key in ('action','appid','platform','language','dlc','eula_id','eula_version','package_ids','restart_paused') if key in request}
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

class AgreementText(HTMLParser):
    def __init__(self):super().__init__(convert_charrefs=True);self.parts=[];self.hidden=0
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style'):self.hidden+=1
        if not self.hidden and tag in ('p','br','div','li','h1','h2','h3'):self.parts.append('\n')
    def handle_endtag(self,tag):
        if tag in ('script','style'):self.hidden=max(0,self.hidden-1)
    def handle_data(self,data):
        if not self.hidden:self.parts.append(data)


def agreement_url(url):
    parsed=urllib.parse.urlsplit(url)
    if parsed.scheme!='https' or parsed.hostname!='store.steampowered.com' or parsed.username or parsed.password or parsed.port not in (None,443) or not parsed.path.startswith(('/eula/','//eula/')):
        raise ValueError('Steam agreement URL is not supported. Review it in the isolated desktop.')
    return url


def load_agreement(app):
    state=steam_action({'action':'eula_status','appid':app})
    agreements=state.get('agreements',[])
    if not isinstance(agreements,list) or not agreements:return {'agreement':None}
    item=agreements[0]
    url=agreement_url(item['url'])
    query=urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query)
    query=[pair for pair in query if pair[0] not in ('json','eulaLang')]+[('json','1'),('eulaLang','english')]
    url=urllib.parse.urlunsplit(urllib.parse.urlsplit(url)._replace(query=urllib.parse.urlencode(query)))
    class Redirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,req,fp,code,msg,headers,newurl):
            agreement_url(newurl)
            return super().redirect_request(req,fp,code,msg,headers,newurl)
    with urllib.request.build_opener(Redirect()).open(url,timeout=20) as response:
        body=response.read(524289)
        if len(body)>524288:raise RuntimeError('Agreement exceeds display limit. Review it in the Steam desktop.')
    data=json.loads(body)
    content=data.get('content','')
    if not isinstance(content,str) or not content.strip():raise RuntimeError('Agreement text could not be loaded. Review it in the Steam desktop.')
    parser=AgreementText();parser.feed(content)
    text=''.join(parser.parts).strip()
    if not text:raise RuntimeError('Agreement text is unavailable.')
    return {'agreement':{'id':item['id'],'version':item['version'],'text':text,'url':item['url']}}


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
                'steam_ready':(HOME/'.steam/steam/steam.sh').is_file() and (HOME/'.steam/steam/ubuntu12_32/steamclient.so').is_file(),
                'moon_installed':(HOME/'.local/share/Lumen/luatools').is_dir(),
                'moon_installing':installer is not None and installer.poll() is None,
                'moon_install_exit':installer.poll() if installer else None,
                'library':'/library'}
    if command=='start':
        if not steam_running():
            prepare_library()
            # The UI sidecar requires CEF debugging even when the wrapper's
            # webhelper argument rewrite misses a newer Steam launch path.
            steam=launch([steam_binary(),'-no-cef-sandbox','-cef-enable-debugging'],'steam.log',umask=0o022,
                         environment=dict(os.environ,SLSSTEAM_AUDIT_BINDALL='1'))
        lumen=HOME/'.local/share/Lumen'
        backend_main=lumen/'luatools/backend/main.lua'
        marker='-- Playlite private control bridge'
        if backend_main.is_file():
            source=backend_main.read_text()
            if marker in source:
                updated=source[:source.index(marker)]+marker+'\n'+Path('/opt/moon_bridge.lua').read_text()
                if updated!=source:
                    temporary=backend_main.with_suffix('.playlite-tmp')
                    temporary.write_text(updated);temporary.replace(backend_main)
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
    if command in ('add','add_status','cancel_add','authentication_status'):
        app=1 if command=='authentication_status' else request.get('appid')
        app_id(app)
        from provider_limits import install_capture,read_limits
        backend=HOME/'.local/share/Lumen/luatools/backend'
        if command=='add':
            install_capture(backend)
            (backend/'temp_dl'/f'{app}_state.json.limits.jsonl').unlink(missing_ok=True)
        identifier=uuid.uuid4().hex
        pending=CONTROL/'moon-request.tmp'
        pending.write_text(json.dumps({'id':identifier,'command':command,'appid':app}))
        pending.replace(CONTROL/'moon-request.json')
        deadline=time.monotonic()+(45 if command=='authentication_status' else 8)
        while time.monotonic()<deadline:
            path=CONTROL/'moon-response.json'
            if path.exists() and path.stat().st_size<=65536:
                reply=json.loads(path.read_text())
                if reply.get('id')==identifier:
                    if not reply.get('ok'):raise RuntimeError('LuaMoon request failed.')
                    result=json.loads(reply['result']) if isinstance(reply['result'],str) else reply['result']
                    if command=='add_status':result['provider_limits']=read_limits(backend,app)
                    return result
            time.sleep(.1)
        (CONTROL/'moon-request.json').unlink(missing_ok=True)
        raise RuntimeError('LuaMoon bridge is not ready. Open Steam with LuaMoon loaded.')
    if command=='stop':
        if steam_running():
            launch([steam_binary(),'-shutdown'],'steam.log')
        return {'stopping':True}
    if command=='installed_games':return installed_games()
    if command=='finish_export':return finish_export(app_id(request.get('appid')))
    if command=='uninstall':
        app=app_id(request.get('appid'))
        game=next((row for row in installed_games()['games'] if row['appid']==app),None)
        if game is None:raise RuntimeError('Game is no longer installed in isolated Steam. Refresh the list.')
        if game['status']=='Downloading':raise RuntimeError('Cancel this game’s active download before uninstalling it.')
        result=steam_action({'action':'uninstall','appid':app})
        if not result.get('requested'):raise RuntimeError(result.get('error') or 'Steam did not accept the uninstall request.')
        return result
    if command=='has_game':
        app=app_id(request.get('appid'))
        path=HOME/'.steam/steam/config/stplug-in'/f'{app}.lua'
        return {'exists':path.is_file() and path.stat().st_size>0}
    if command=='eula_status':return load_agreement(app_id(request.get('appid')))
    if command=='eula_accept':
        app=app_id(request.get('appid'))
        identifier=request.get('eula_id');version=request.get('eula_version')
        if not isinstance(identifier,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}',identifier) or type(version) is not int or version<0:
            raise ValueError('Invalid agreement identity.')
        result=steam_action({'action':'eula_accept','appid':app,'eula_id':identifier,'eula_version':version})
        if not result.get('accepted'):raise RuntimeError('Steam agreement changed. Review the current version again.')
        return result
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
        if (not isinstance(result,dict) or result.get('state',{}).get('status')!='done') and not dispatch({'command':'has_game','appid':app})['exists']:
            raise RuntimeError('LuaMoon must finish adding this App ID before installation.')
        if not steam_running():raise RuntimeError('Start isolated Steam first.')
        previous_events=content_events(app)
        recover=request.get('recover_paused',False)
        if type(recover) is not bool:raise ValueError('Invalid paused download recovery selection.')
        response=steam_action({'action':'install','appid':app,'platform':request['platform'],'dlc':dlc,'language':language,'restart_paused':recover and paused_install(app)})
        response['previous_events']=previous_events
        return response
    if command=='retail_selection':
        from retail_selection import retail_packages,package_depots,restrict_depots
        app=app_id(request.get('appid'))
        packages=retail_packages(app)
        records={};depots={}
        # Native console requests missing metadata asynchronously. Retry only
        # this bounded set; no provider calls or separate Steam login.
        for attempt in range(4):
            for offset in range(0,len(packages),4):
                result=steam_action({'action':'package_info','appid':app,'package_ids':packages[offset:offset+4]})
                text=result.get('text','')
                if len(text)>524288:raise RuntimeError('Steam package response exceeds the limit.')
                for match in re.finditer(r'^"(\d+)"\s*\n\{',text,re.M):
                    depth=0;end=None
                    start=text.index('{',match.start())
                    for token in re.finditer(r'"(?:\\.|[^"\\])*"|[{}]',text[start:]):
                        if token[0]=='{':depth+=1
                        elif token[0]=='}':
                            depth-=1
                            if depth==0:end=start+token.end();break
                    if end is not None:
                        data=parse_vdf(text[match.start():end])
                        for key,value in data.items():
                            if key==str(app) and isinstance(value,dict) and isinstance(value.get('depots'),dict):depots=value['depots']
                            if key in map(str,packages) and isinstance(value,dict) and 'appids' in value:records[key]=value
            if depots and all(str(value) in records for value in packages):break
        allowed=package_depots(app,packages,records)
        removed=restrict_depots(HOME,app,allowed,depots)
        return {'packages':packages,'allowed_depots':sorted(allowed),'excluded_depots':removed['excluded'],'restart_required':removed['changed']}
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
    launch(['openbox','--config-file',str(prepare_openbox_config())],'desktop.log')
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
