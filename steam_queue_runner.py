"""Queue isolated Steam installs with an explicit platform selection."""
import json
from pathlib import Path
import shutil
import threading
import time
from PyQt6.QtCore import QObject,QSettings,QThreadPool
from .steam_runtime import SteamRuntime


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


def export_install(source,destination):
    from .download_flow import prepare_destination,copy_file_exclusive,finalize_download
    source=Path(source).resolve()
    if not source.is_dir():raise ValueError('Steam installation files are unavailable.')
    for path in source.rglob('*'):
        if path.is_symlink() and (Path(path.readlink()).is_absolute() or not path.resolve().is_relative_to(source)):
            raise ValueError('Steam content contains a link outside the game folder.')
    destination,staging=prepare_destination(destination)
    def copy_file(source,target):
        copy_file_exclusive(Path(source),Path(target))
        return target
    shutil.copytree(source,staging,dirs_exist_ok=True,symlinks=True,copy_function=copy_file)
    finalize_download(destination,staging)


class SteamQueueRunner(QObject):
    def __init__(self,network,window,queue,entry,snapshot):
        super().__init__(window)
        self.network=network;self.window=window;self.queue=queue;self.entry=entry
        self.snapshot=snapshot;self.runtime=SteamRuntime(network)
        self.cancelled=threading.Event();self.job=None;self.done=False;self.dialog=None

    def start(self):
        from .plugin import Job
        settings=QSettings('Playlite','SteamDownloader')
        country=settings.value('country','');protocol=settings.value('protocol','udp')
        result=[]
        def progress(status,amount=None):
            self.job.signals.progress.emit(json.dumps({'status':status,'progress':amount}))
        def operation():
            app=self.snapshot['app'];platform=self.snapshot['platform']
            configured=False
            try:
                if self.network.container:self.network.check()
                else:
                    from .credentials import Wallet
                    from .settings import Preferences
                    self.network.connect(Preferences(country,protocol),*Wallet().read(),progress=progress)
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
                deadline=time.monotonic()+90
                while True:
                    if self.cancelled.is_set():raise RuntimeError('Cancelled')
                    try:
                        self.runtime.request('add',app);break
                    except (OSError,RuntimeError):
                        if time.monotonic()>deadline:raise RuntimeError('LuaMoon is not ready. Open its isolated Steam desktop and sign in.')
                        self.cancelled.wait(1)
                deadline=time.monotonic()+180
                while True:
                    if self.cancelled.is_set():raise RuntimeError('Cancelled')
                    state=self.runtime.request('add_status',app).get('state',{})
                    if state.get('status')=='done':break
                    if state.get('status') in ('failed','cancelled'):
                        raise RuntimeError(state.get('error') or 'LuaMoon could not add this game.')
                    if time.monotonic()>deadline:raise RuntimeError('LuaMoon manifest preparation timed out.')
                    progress('Preparing manifests with LuaMoon');self.cancelled.wait(1)
                progress('Applying '+platform.title()+' content selection')
                self.runtime.request('install',app,platform=platform,language=self.snapshot['language'],dlc=self.snapshot['dlc'])
                configured=True
                while True:
                    if self.cancelled.is_set():raise RuntimeError('Cancelled')
                    self.network.check()
                    state=self.runtime.request('download_status',app)
                    if state.get('branch','public')!='public':
                        raise RuntimeError('Steam has a beta branch selected. Select the default branch in isolated Steam before retrying.')
                    if verify_platform(state,self.snapshot['info'],platform):break
                    total=state.get('total',0);downloaded=state.get('downloaded',0)
                    amount=min(99,downloaded/total*100) if total else None
                    message='Steam is downloading '+platform.title()+' content'
                    if state.get('installed'):message='Waiting for Steam to switch to '+platform.title()+' content'
                    elif total and downloaded>=total:message='Steam is verifying '+platform.title()+' content'
                    progress(message,amount);self.cancelled.wait(2)
                progress('Copying verified game files to the download folder',99)
                source=(self.runtime.root/'library'/state['directory']).resolve()
                if not source.is_relative_to((self.runtime.root/'library/steamapps/common').resolve()):
                    raise ValueError('Invalid Steam installation path.')
                export_install(source,self.entry.destination)
                result.append(True)
                return platform.title()+' download completed and verified by Steam'
            except Exception:
                try:self.runtime.request('pause' if configured else 'cancel_add',app)
                except Exception:pass
                raise
        self.job=Job(operation)
        def update(raw):
            data=json.loads(raw);self.queue.update(self.entry,data['progress'],data['status'])
        self.job.signals.progress.connect(update)
        def finished(message):
            self.done=True;self.job=None
            self.queue.finish(self.entry,bool(result),message)
        self.job.signals.finished.connect(finished)
        QThreadPool.globalInstance().start(self.job)

    def cancel(self):self.cancelled.set()

    def add_to_library(self):
        from .download_dialog import DownloadDialog
        if self.dialog is None:
            self.dialog=DownloadDialog(self.network,self.window)
            self.dialog.queue_runner=True
            self.dialog.selected_app=self.snapshot['app'];self.dialog.game_name=self.entry.name
            self.dialog.destination.setText(self.entry.destination)
        self.dialog.add_to_library()

    def dispose(self):
        if self.dialog:self.dialog.deleteLater()
        self.deleteLater()
