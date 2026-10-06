"""Run a prepared game download independently of its selection dialog."""
import time
from PyQt6.QtCore import QSettings, QThreadPool
from .download_dialog import DownloadDialog
from .credentials import Wallet
from .settings import Preferences


class QueueRunner(DownloadDialog):
    def __init__(self,network,window,queue,entry,snapshot):
        super().__init__(network,window)
        self.queue_runner=True;self.queue=queue;self.entry=entry;self.auto_started=True
        self.selected_app=snapshot['app'];self.appid.setText(entry.name);self.pack_app=snapshot['app']
        self.rows=list(snapshot['rows']);self.content_plan=list(snapshot['plan'])
        self.content_info=snapshot['info'];self.game_name=entry.name
        self.username.setText(snapshot['username']);self.destination.setText(entry.destination)
        branch=snapshot.get('branch','public')
        if self.branch.findData(branch)<0:self.branch.addItem(branch,branch)
        self.branch.setCurrentIndex(self.branch.findData(branch))
        self.content_index=0;self.batch_started_at=time.monotonic()
        self.connecting=False;self.done=False

    def start(self):
        from .plugin import Job
        self.connecting=True;self.busy=True
        result=[]
        def operation():
            if self.network.container:self.network.check()
            else:
                settings=QSettings('Playlite','SteamDownloader')
                preferences=Preferences(settings.value('country',''),settings.value('protocol','udp'))
                self.network.connect(preferences,*Wallet().read(),progress=job.signals.progress.emit)
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
        progress=None if self.progress_bar.maximum()==0 else (self.content_index+self.progress_bar.value()/1000)/len(self.content_plan)*100
        if not self.done:self.queue.update(self.entry,progress,self.status.text()+' · '+self.progress_info.text())

    def finished(self,code,*args):
        super().finished(1 if self.entry.cancelled else code,*args)
        if not self.busy:self.finish_queue(self.open_folder.isEnabled(),self.status.text())

    def finish_queue(self,success,status):
        if self.done:return
        self.done=True;self.queue.finish(self.entry,success,status)
        # Keep completed controls out of the widget tree and release manifest data.
        self.rows=[];self.content_plan=[];self.entry.factory=None
        self.log.clear()
        if not success:
            self.entry.controller=None
            self.deleteLater()

    def dispose(self):
        self.deleteLater()

    def cancel(self):
        if self.connecting:self.network.cancel()
        elif self.busy:self.close_or_cancel()
        else:self.finish_queue(False,'Cancelled')
