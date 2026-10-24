"""Run a prepared game download independently of its selection dialog."""
import time
from PyQt6.QtCore import QSettings, QThreadPool
from .download_dialog import DownloadDialog
from .credentials import Wallet
from .settings import Preferences


def validate_snapshot(snapshot):
    """Persist choices only; reacquire manifests, keys and sessions on resume."""
    if not isinstance(snapshot,dict):raise ValueError('Invalid depot download choices.')
    app=snapshot.get('app');dlc=snapshot.get('dlc',[])
    if type(app) is not int or not 0<app<2**32:raise ValueError('Invalid Steam App ID.')
    if snapshot.get('platform') not in ('windows','linux'):raise ValueError('Choose Windows or Linux.')
    if snapshot.get('architecture') not in ('32','64'):raise ValueError('Invalid architecture.')
    if snapshot.get('provider') not in ('Hubcap','Luie'):raise ValueError('Choose Hubcap or LuaTools.')
    if not isinstance(dlc,list) or len(dlc)>256 or any(type(value) is not int or not 0<value<2**32 for value in dlc):
        raise ValueError('Invalid DLC choices.')
    for field in ('username','language','branch'):
        value=snapshot.get(field)
        if not isinstance(value,str) or not value.strip() or len(value)>256 or any(c in value for c in '\r\n\0'):
            raise ValueError('Invalid download '+field+'.')
    return {key:snapshot[key] for key in ('app','platform','architecture','provider','username','language','branch')}|{'dlc':list(dict.fromkeys(dlc))}


class QueueRunner(DownloadDialog):
    def __init__(self,network,window,queue,entry,snapshot):
        super().__init__(network,window)
        self.queue_runner=True;self.queue=queue;self.entry=entry;self.auto_started=True
        self.snapshot=snapshot
        self.selected_app=snapshot['app'];self.appid.setText(entry.name);self.pack_app=snapshot['app']
        self.rows=list(snapshot.get('rows',[]));self.content_plan=list(snapshot.get('plan',[]))
        self.content_info=snapshot.get('info');self.game_name=entry.name
        self.username.setText(snapshot['username']);self.destination.setText(entry.destination)
        if getattr(network,'is_depot',False) is True:
            self.depot.addItem(snapshot['platform'].title(),snapshot['platform'])
        branch=snapshot.get('branch','public')
        if self.branch.findData(branch)<0:self.branch.addItem(branch,branch)
        self.branch.setCurrentIndex(self.branch.findData(branch))
        self.content_index=0;self.batch_started_at=time.monotonic()
        self.connecting=False;self.done=False;self.download_complete=False

    def start(self):
        from .plugin import Job
        self.connecting=True;self.busy=True
        result=[]
        def operation():
            self.network.cancelled.clear()
            if self.network.container:self.network.check()
            else:
                settings=QSettings('Playlite','SteamDownloader')
                preferences=Preferences(settings.value('country',''),settings.value('protocol','udp'))
                self.network.connect(preferences,*Wallet().read(),progress=job.signals.progress.emit)
            if getattr(self.network,'is_depot',False) is True:
                from .depot_setup import require_worker
                from .app_info import fetch_app_info
                from .manifest_resolver import ManifestResolver
                from .preflight import check_plan
                require_worker()
                snapshot=validate_snapshot(self.snapshot)
                progress=job.signals.progress.emit
                progress('Loading current Steam depot information…')
                info=fetch_app_info(self.network,snapshot['app'],snapshot['username'])
                resolver=ManifestResolver(snapshot['provider'],self.fetch_manifest,progress,
                                          providers=('Hubcap','Luie'))
                plan=resolver.prepare(info,[],snapshot['platform'],set(snapshot['dlc']),
                                      snapshot['language'],snapshot['architecture'],snapshot['branch'])
                if self.entry.cancelled:return 'Cancelled'
                check_plan(self.network,plan,snapshot['username'],snapshot['branch'],progress)
                self.content_info=info;self.content_plan=plan;self.rows=[row for _,row,_ in plan]
            result.append(True);return 'Connected'
        job=Job(operation);self.jobs.add(job)
        job.signals.progress.connect(lambda message:self.queue.update(self.entry,None,message))
        def complete(message):
            self.jobs.discard(job);self.connecting=False;self.busy=False
            if not result or self.entry.cancelled:
                self.finish_queue(False,'Cancelled' if self.entry.cancelled else message);return
            self.download()
            if not self.busy:self.finish_queue(False,self.status.text())
        job.signals.finished.connect(complete)
        QThreadPool.globalInstance().start(job)

    def read_output(self):
        super().read_output()
        progress=None if self.progress_bar.maximum()==0 or not self.content_plan else (self.content_index+self.progress_bar.value()/1000)/len(self.content_plan)*100
        if not self.done:self.queue.update(self.entry,progress,self.status.text()+' · '+self.progress_info.text())

    def finished(self,code,*args):
        super().finished(1 if self.entry.cancelled else code,*args)
        if not self.busy:self.finish_queue(self.download_complete,self.status.text())

    def finish_queue(self,success,status):
        if self.done:return
        self.done=True;self.queue.finish(self.entry,success,status)
        # Keep completed controls out of the widget tree and release manifest data.
        self.rows=[];self.content_plan=[]
        if not getattr(self.network,'is_depot',False) is True:self.entry.factory=None
        self.log.clear()
        if not success:
            self.entry.controller=None
            self.deleteLater()

    def dispose(self):
        self.deleteLater()

    def cancel(self):
        if self.connecting:
            self.network.cancel()
            self.queue.update(self.entry,None,'Stopping preparation…')
        elif self.busy:self.close_or_cancel()
        else:self.finish_queue(False,'Cancelled')
