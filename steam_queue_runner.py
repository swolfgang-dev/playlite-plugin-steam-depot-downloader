"""Queue isolated Steam installs with an explicit platform selection."""
import json
import re
from pathlib import Path
import shutil
import threading
import time
from PyQt6.QtCore import QObject,QSettings,QThreadPool
from .steam_runtime import SteamRuntime


class PausedRecovery:
    """Recover once, only after an unchanged paused download for 30 seconds."""
    def __init__(self):
        self.since=None;self.position=None;self.used=False

    def needed(self,state,now):
        position=(state.get('downloaded',0),state.get('disk_processed',0))
        paused=bool(int(state.get('flags',0)) & 512)
        if not paused or state.get('installed') or state.get('export_pending'):
            self.since=None;self.position=position
            return False
        if self.since is None or position!=self.position:
            self.since=now;self.position=position
        if not self.used and now-self.since>=30:
            self.used=True
            return True
        return False


def persisted_snapshot(snapshot):
    """Retain install choices and public platform metadata, never provider secrets."""
    from .app_info import depot_entries
    def app_metadata(app):
        return {'id':app.get('id'),'name':app.get('name',''),
                'depots':{str(identifier):{'config':{'oslist':entry.get('config',{}).get('oslist','')}}
                          for identifier,entry in depot_entries(app).items()}}
    info=snapshot.get('info',{})
    return {key:snapshot[key] for key in ('app','platform','language','dlc')}|{
        'info':{'game':app_metadata(info.get('game',{})),
                'dlc':[app_metadata(app) for app in info.get('dlc',[])]}}


def provider_limit_message(row):
    details=[]
    if 'daily_usage' in row and 'daily_limit' in row:
        details.append(f"daily usage {row['daily_usage']}/{row['daily_limit']} · {row['daily_remaining']} remaining")
    if 'remaining' in row:
        details.append(f"requests remaining {row['remaining']}"+(f"/{row['limit']}" if 'limit' in row else ''))
    elif 'limit' in row:details.append(f"request limit {row['limit']}")
    if not details:details.append('remaining quota not reported')
    if 'retry_after' in row:
        value=row['retry_after'];details.append(f'retry after {value}'+(' seconds' if isinstance(value,int) else ''))
    elif row.get('http_status')==429:details.append('request throttled · retry time not reported')
    if 'reset' in row:details.append(f"reset (provider value) {row['reset']}")
    return 'API limits · '+row['provider']+' · HTTP '+str(row['http_status'])+' · '+' · '.join(details)


def verify_platform(state,info,platform):
    """Never export a completed install containing a known wrong-platform depot."""
    from .app_info import depot_entries
    depots={}
    for entry in [info['game'],*info.get('dlc',[])]:depots.update(depot_entries(entry))
    for identifier in state.get('depots',[]):
        oslist=str(depots.get(identifier,{}).get('config',{}).get('oslist',''))
        if oslist and platform not in [value.strip().lower() for value in oslist.split(',')]:
            return False
    return bool(state.get('installed'))


def download_failure(state,previous_events=()):
    """Only interpret historical content errors while Steam is currently paused."""
    if not int(state.get('flags',0)) & 512:return None
    events=[event for event in state.get('events',[]) if event not in previous_events]
    for event in reversed(events):
        if 'scheduler finished :' in event:
            match=re.search(r'\(result (.*?), state ',event)
            if match and match[1]!='No Error':
                reason=match[1]
                details=next((line.split('update canceled :',1)[1].strip() for line in reversed(events) if 'update canceled :' in line),'')
                return 'Steam stopped the installation: '+reason+('. '+details if details else '')
            return None
    return None


def export_install(source,destination,progress=None,cancelled=None):
    from .download_flow import prepare_destination,copy_file_exclusive,finalize_download
    source=Path(source).resolve()
    if not source.is_dir():raise ValueError('Steam installation files are unavailable.')
    total=0
    for path in source.rglob('*'):
        if path.is_symlink() and (Path(path.readlink()).is_absolute() or not path.resolve().is_relative_to(source)):
            raise ValueError('Steam content contains a link outside the game folder.')
        if path.is_file() and not path.is_symlink():total+=path.stat().st_size
    destination,staging=prepare_destination(destination,reuse_staging=True)
    # A retained copy must only contain paths from this Steam installation.
    for path in staging.rglob('*'):
        relative=path.relative_to(staging)
        original=source/relative
        if path.is_symlink() or not original.exists() or path.is_dir()!=original.is_dir():
            raise ValueError('Retained download staging contains unexpected files: '+str(relative))
    counts={'copy':0,'verify':0};last_update=[0.]
    elapsed={'copy':0.,'verify':0.};last_sample=[time.monotonic()]

    def update(amount,phase):
        if cancelled and cancelled():raise RuntimeError('Installation copy stopped · partial files retained')
        counts[phase]+=amount
        now=time.monotonic()
        elapsed[phase]+=max(0.,now-last_sample[0]);last_sample[0]=now
        speed=counts[phase]/max(.001,elapsed[phase])
        rates={key:counts[key]/elapsed[key] if elapsed[key]>0 and counts[key]>0 else speed for key in counts}
        remaining=sum(max(0,total-counts[key])/max(1.,rates[key]) for key in counts)
        if progress and now-last_update[0]>=.2:
            last_update[0]=now
            progress(('Copying files' if phase=='copy' else 'Verifying copied files')
                     +f' · {counts[phase]/1048576:.1f} / {total/1048576:.1f} MiB'
                     +f' · ~{speed*8/1000000:.1f} Mbps · ~{remaining/60:.1f} min remaining',
                     min(99,sum(counts.values())/max(1,total*2)*100))
    if progress:progress('Copying files to install location',0)
    def copy_file(source,target):
        import hashlib
        source=Path(source);target=Path(target)
        if target.exists():
            def digest(path):
                result=hashlib.sha256()
                with path.open('rb') as stream:
                    while data:=stream.read(1024*1024):
                        result.update(data)
                        if cancelled and cancelled():raise RuntimeError('Installation copy stopped · partial files retained')
                return result.digest()
            if target.is_file() and target.stat().st_size==source.stat().st_size and digest(target)==digest(source):
                update(source.stat().st_size,'copy');update(source.stat().st_size,'verify')
                return str(target)
            target.unlink()
        copy_file_exclusive(Path(source),Path(target),update)
        return target
    shutil.copytree(source,staging,dirs_exist_ok=True,symlinks=True,copy_function=copy_file)
    if cancelled and cancelled():raise RuntimeError('Installation copy stopped · partial files retained')
    finalize_download(destination,staging)
    if progress:progress('Install files copied and verified',100)


def ensure_vpn(network,country,protocol,progress):
    if getattr(network, 'is_vm', False) is True:
        from .settings import Preferences
        network.cancelled.clear()
        try:
            if network.container:
                network.check();return
        except RuntimeError:
            pass
        network.connect(Preferences(country,protocol),progress=progress)
        return
    if network.container:
        progress('Checking VPN connection…')
        try:
            network.check()
            return
        except RuntimeError:
            progress('VPN connection lost · reconnecting…')
    else:
        progress('Connecting VPN…')
    from .credentials import Wallet
    from .settings import Preferences
    credentials=Wallet().read()
    network.cancelled.clear()
    network.connect(Preferences(country,protocol),*credentials,progress=progress)
    network.check()


class SteamQueueRunner(QObject):
    def __init__(self,network,window,queue,entry,snapshot):
        super().__init__(window)
        self.network=network;self.window=window;self.queue=queue;self.entry=entry
        self.snapshot=snapshot;self.runtime=SteamRuntime(network)
        self.cancelled=threading.Event();self.job=None;self.done=False;self.dialog=None
        self.agreement_event=threading.Event();self.agreement_dialog=None;self.accepted_agreement=None

    def start(self):
        from .plugin import Job
        settings=QSettings('Playlite','SteamDownloader')
        country=settings.value('country','');protocol=settings.value('protocol','udp')
        result=[]
        def progress(status,amount=None,events=None,agreement=None,destination=None):
            if self.cancelled.is_set():raise RuntimeError('Cancelled')
            self.job.signals.progress.emit(json.dumps({'status':status,'progress':amount,'events':events or [],'agreement':agreement,'destination':destination}))
        def operation():
            app=self.snapshot['app'];platform=self.snapshot['platform']
            configured=False
            try:
                ensure_vpn(self.network,country,protocol,progress)
                progress('Starting isolated Steam')
                self.runtime.start()
                deadline=time.monotonic()+90
                while True:
                    if self.cancelled.is_set():raise RuntimeError('Cancelled')
                    try:self.runtime.request('start');break
                    except (OSError,RuntimeError):
                        if time.monotonic()>deadline:raise RuntimeError('Isolated Steam could not start. Open Isolated Steam setup.')
                        self.cancelled.wait(.5)
                progress('Checking LuaMoon')
                try:cached=self.runtime.request('has_game',app).get('exists') is True
                except (OSError,RuntimeError):cached=False
                if cached:
                    progress('Reusing the existing LuaMoon game setup · no provider request needed')
                else:
                    deadline=time.monotonic()+90
                    while True:
                        if self.cancelled.is_set():raise RuntimeError('Cancelled')
                        try:
                            self.runtime.request('add',app);break
                        except (OSError,RuntimeError):
                            if time.monotonic()>deadline:raise RuntimeError('LuaMoon is not ready. Open its isolated Steam desktop and sign in.')
                            self.cancelled.wait(1)
                    deadline=time.monotonic()+180
                    reported_limits=set()
                    while True:
                        if self.cancelled.is_set():raise RuntimeError('Cancelled')
                        provider_state=self.runtime.request('add_status',app)
                        for row in provider_state.get('provider_limits',[]):
                            message=provider_limit_message(row)
                            if message not in reported_limits:
                                progress(message);reported_limits.add(message)
                        state=provider_state.get('state',{})
                        if state.get('status')=='done':break
                        if state.get('status') in ('failed','cancelled'):
                            if state.get('errorCode') in ('rate_limited','daily_limit','quota_exhausted') and not reported_limits:
                                progress('API limits · '+str(state.get('errorSource') or 'LuaMoon provider')+' · '+state['errorCode']+' · remaining quota and retry time not reported')
                            raise RuntimeError(state.get('error') or 'LuaMoon could not add this game.')
                        if time.monotonic()>deadline:raise RuntimeError('LuaMoon manifest preparation timed out.')
                        progress('Preparing manifests with LuaMoon');self.cancelled.wait(1)
                progress('Checking Steam retail base-game package depot membership')
                deadline=time.monotonic()+90
                while True:
                    if self.cancelled.is_set():raise RuntimeError('Cancelled')
                    try:
                        selection=self.runtime.request('retail_selection',app);break
                    except (OSError,RuntimeError) as error:
                        # Cached LuaMoon files exist before SharedJSContext is
                        # ready on a cold start. Do not mistake that startup
                        # interval for a content-selection failure.
                        transient=isinstance(error,OSError) or 'Steam could not apply the selected content settings' in str(error) or 'Steam UI is not ready' in str(error)
                        if not transient or time.monotonic()>deadline:raise
                        progress('Waiting for Steam’s content API to become ready')
                        self.cancelled.wait(2)
                progress('Retail packages '+', '.join(map(str,selection.get('packages',[])))+' · excluded depots '+(', '.join(map(str,selection.get('excluded_depots',[]))) or 'none'))
                if selection.get('restart_required'):
                    progress('Restarting isolated Steam to apply retail depot selection')
                    self.runtime.request('stop')
                    deadline=time.monotonic()+30
                    while self.runtime.request('status').get('steam_running'):
                        if self.cancelled.is_set():raise RuntimeError('Cancelled')
                        if time.monotonic()>deadline:raise RuntimeError('Steam did not stop to reload its depot selection. Close it in the isolated desktop and retry.')
                        self.cancelled.wait(.5)
                    self.runtime.request('start')
                    deadline=time.monotonic()+90
                    while True:
                        if self.cancelled.is_set():raise RuntimeError('Cancelled')
                        try:
                            reloaded=self.runtime.request('retail_selection',app)
                            break
                        except (OSError,RuntimeError):
                            if time.monotonic()>deadline:raise RuntimeError('Steam did not become ready after applying retail depot selection.')
                            self.cancelled.wait(1)
                    if reloaded.get('restart_required'):raise RuntimeError('The integration restored excluded depots. Retry after closing isolated Steam.')
                progress('Applying '+platform.title()+' content selection')
                installation=self.runtime.request('install',app,platform=platform,language=self.snapshot['language'],dlc=self.snapshot['dlc'])
                if installation.get('restarted_paused'):progress('Cancelled the previous paused attempt and queued a new Steam download · partial files retained')
                previous_events=set(installation.get('previous_events',[]))
                configured=True
                registered_deadline=time.monotonic()+180
                seen_events=set(previous_events);samples=[];agreement_check=0;activity_seen=False
                recovery=PausedRecovery()
                permission_recovered=False
                while True:
                    if self.cancelled.is_set():raise RuntimeError('Cancelled')
                    self.network.check()
                    state=self.runtime.request('download_status',app)
                    if recovery.needed(state,time.monotonic()):
                        progress('Steam did not resume after 30 seconds · restarting the stuck attempt with partial files retained')
                        installation=self.runtime.request('install',app,platform=platform,language=self.snapshot['language'],dlc=self.snapshot['dlc'],recover_paused=True)
                        previous_events.update(installation.get('previous_events',[]))
                        registered_deadline=time.monotonic()+180
                        continue
                    current_events=[event for event in state.get('events',[]) if event not in previous_events]
                    activity_seen=activity_seen or bool(current_events) or bool(int(state.get('flags',0)) & 1024) or bool(state.get('export_pending'))
                    failure=download_failure(state,previous_events)
                    if failure:
                        if 'missing file permissions' in failure.lower() and not permission_recovered:
                            permission_recovered=True
                            progress('Repairing temporary download permissions and retrying with partial files retained')
                            installation=self.runtime.request('install',app,platform=platform,language=self.snapshot['language'],dlc=self.snapshot['dlc'],recover_paused=True)
                            previous_events.update(state.get('events',[]));previous_events.update(installation.get('previous_events',[]))
                            registered_deadline=time.monotonic()+180
                            continue
                        raise RuntimeError(failure)
                    if not activity_seen and not state.get('installed') and time.monotonic()>registered_deadline:
                        raise RuntimeError('Steam did not start the current installation within three minutes. Check its isolated desktop for a confirmation or error.')
                    if not state.get('installed') and time.monotonic()>=agreement_check:
                        agreement_check=time.monotonic()+10
                        try:agreement=self.runtime.request('eula_status',app).get('agreement')
                        except (OSError,RuntimeError) as error:
                            progress('Could not read Steam agreement · '+str(error));agreement=None
                        if isinstance(agreement,dict) and agreement.get('text'):
                            self.agreement_event.clear();self.accepted_agreement=None
                            progress('Waiting for your acceptance of the Steam agreement',agreement=agreement)
                            while not self.agreement_event.wait(.2):
                                if self.cancelled.is_set():raise RuntimeError('Agreement declined or download cancelled')
                            if self.cancelled.is_set() or self.accepted_agreement!=(agreement['id'],agreement['version']):
                                raise RuntimeError('Agreement declined or download cancelled')
                            progress('Submitting your acceptance of agreement '+agreement['id']+' · version '+str(agreement['version']))
                            self.runtime.request('eula_accept',app,eula_id=agreement['id'],eula_version=agreement['version'])
                            registered_deadline=time.monotonic()+180;agreement_check=0
                            continue

                    if state.get('branch','public')!='public':
                        raise RuntimeError('Steam has a beta branch selected. Select the default branch in isolated Steam before retrying.')
                    events=[event for event in state.get('events',[]) if event not in seen_events]
                    seen_events.update(events)
                    if verify_platform(state,self.snapshot['info'],platform):
                        progress('Steam reports installation complete',100,events);break
                    total=state.get('total',0);downloaded=state.get('downloaded',0)
                    amount=min(99,downloaded/total*100) if total else None
                    message='Steam is downloading '+platform.title()+' content'
                    now=time.monotonic();samples.append((now,downloaded))
                    samples=[sample for sample in samples if now-sample[0]<=30]
                    if total:
                        message+=f' · {downloaded/total*100:.1f}% · {downloaded/1048576:.1f} / {total/1048576:.1f} MiB'
                        if len(samples)>1 and now>samples[0][0] and downloaded>samples[0][1]:
                            speed=(downloaded-samples[0][1])/(now-samples[0][0])
                            message+=f' · ~{speed*8/1000000:.1f} Mbps · ~{max(0,total-downloaded)/speed/60:.1f} min remaining'
                    elif not state.get('flags'):
                        message='Waiting for Steam to register the installation · check its desktop for a confirmation'
                        if time.monotonic()>registered_deadline:raise RuntimeError('Steam did not register the installation within three minutes. Open its desktop and check for a confirmation or error.')
                    else:message+=f' · Steam state {state["flags"]} · waiting for transfer details'
                    if int(state.get('flags',0)) & 512:message='Steam download is paused · resume it in the isolated Steam desktop'
                    if state.get('installed'):message='Waiting for Steam to switch to '+platform.title()+' content'
                    elif total and downloaded>=total:message='Steam is verifying '+platform.title()+' content'
                    if state.get('disk_total'):
                        message+=f' · disk {state.get("disk_processed",0)/1048576:.1f} / {state["disk_total"]/1048576:.1f} MiB'
                    if state.get('export_pending'):
                        message='Steam verification complete · preparing export from the private library'
                        if state.get('export_total'):
                            message+=f' · {state.get("export_copied",0)/1048576:.1f} / {state["export_total"]/1048576:.1f} MiB copied'
                        amount=99
                    progress(message,amount,events);self.cancelled.wait(2)
                if getattr(self.runtime,'direct_install',False) is True:
                    source=self.runtime.installation_path(state)
                    progress('Steam verified the shared installation · ready to launch',100,destination=str(source))
                    result.append(True)
                    return platform.title()+' download completed and verified by Steam'
                progress('Steam verification complete · copying game files to '+self.entry.destination,0)
                source=(self.runtime.root/'library'/state['directory']).resolve()
                if not source.is_relative_to((self.runtime.root/'library/steamapps/common').resolve()):
                    raise ValueError('Invalid Steam installation path.')
                export_install(source,self.entry.destination,progress=progress,cancelled=self.cancelled.is_set)
                result.append(True)
                progress('Export verified · removing the private Steam game copy',100)
                try:
                    cleanup=self.runtime.request('finish_export',app)
                    if cleanup.get('retained') is True:
                        progress('Export verified · existing shared Steam installation retained',100)
                        return platform.title()+' download completed and verified by Steam'
                except Exception as error:
                    progress('Game installed successfully; private-copy cleanup needs attention: '+str(error),100)
                    return platform.title()+' download completed · private Steam cleanup pending'
                progress('Private Steam game copy removed · exported game retained',100)
                return platform.title()+' download completed and verified by Steam'
            except Exception:
                try:self.runtime.request('pause' if configured else 'cancel_add',app)
                except Exception:pass
                raise
        self.job=Job(operation)
        def update(raw):
            data=json.loads(raw)
            if data.get('destination'):
                self.entry.destination=data['destination']
            agreement=data.get('agreement')
            if agreement:self.review_agreement(agreement)
            events=getattr(self.entry,'steam_events',[])
            events.extend(data.get('events',[]));self.entry.steam_events=events[-2000:]
            self.queue.update(self.entry,data['progress'],data['status'])
        self.job.signals.progress.connect(update)
        def finished(message):
            self.done=True;self.job=None
            if self.agreement_dialog:self.agreement_dialog.close()
            self.queue.finish(self.entry,bool(result),message)
        self.job.signals.finished.connect(finished)
        QThreadPool.globalInstance().start(self.job)

    def review_agreement(self,agreement):
        from .agreement_dialog import AgreementDialog
        dialog=AgreementDialog(self.entry.name,agreement,self.window)
        self.agreement_dialog=dialog
        def accepted():
            self.accepted_agreement=(agreement['id'],agreement['version'])
            self.agreement_event.set()
        dialog.accepted.connect(accepted)
        dialog.rejected.connect(self.cancel)
        dialog.show();dialog.raise_();dialog.activateWindow()

    def cancel(self):self.cancelled.set()

    def add_to_library(self):
        from .download_dialog import DownloadDialog
        if self.dialog is None:
            self.dialog=DownloadDialog(self.network,self.window)
            self.dialog.queue_runner=True
            self.dialog.selected_app=self.snapshot['app'];self.dialog.game_name=self.entry.name
            self.dialog.appid.setText(self.entry.name)
            self.dialog.destination.setText(self.entry.destination)
        return self.dialog.add_to_library()

    def dispose(self):
        if self.agreement_dialog:self.agreement_dialog.deleteLater()
        if self.dialog:self.dialog.deleteLater()
        self.deleteLater()
