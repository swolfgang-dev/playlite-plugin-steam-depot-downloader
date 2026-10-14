"""Keep the isolated VPN alive for queued workers, then release it once idle."""
from PyQt6.QtCore import QThreadPool, QObject, QEvent, QTimer, QSettings
from PyQt6.QtWidgets import QApplication


def session_preferences():
    settings=QSettings('Playlite','SteamDownloader')
    return (settings.value('keep_steam_open',False,type=bool),
            settings.value('keep_vpn_open',False,type=bool))


def has_open_windows(network):
    app=QApplication.instance()
    if app is None:return False
    for widget in app.allWidgets():
        if getattr(widget,'network',None) is network:
            if getattr(widget,'settings_closed',None) is False and widget.window().isVisible():return True
            if (widget.__class__.__name__=='DownloadDialog' and not widget.authentication
                    and widget.isVisible() and not getattr(widget,'queue_runner',False)
                    and not getattr(widget,'lifecycle_closed',False)):
                return True
        if (widget.__class__.__name__=='DownloadsPanel' and widget.isVisible()
                and widget.opened and widget.queue is vars(network).get('download_queue')):
            return True
    return False


class WindowWatcher(QObject):
    def __init__(self,network):
        super().__init__();self.network=network

    def eventFilter(self,watched,event):
        if event.type() in (QEvent.Type.Hide,QEvent.Type.Close):
            QTimer.singleShot(0,self.check)
        return False

    def check(self):
        if vars(self.network).get('disconnect_pending',False):
            disconnect_when_idle(self.network)


def has_downloads(network):
    queue=vars(network).get('download_queue')
    return queue is not None and any(row.state in ('Queued','Downloading') for row in queue.entries)


def disconnect_when_idle(network,release_session=False):
    if getattr(network,'is_vm',False) is True:
        watcher=vars(network).get('_idle_watcher')
        if watcher is not None:watcher.check()
        return
    if has_downloads(network) or has_open_windows(network):
        network.disconnect_pending=True
        queue=vars(network).get('download_queue')
        if queue is not None:watch_queue(network,queue)
        app=QApplication.instance()
        if app is not None:
            watcher=vars(network).get('_window_watcher')
            if watcher is None:
                watcher=WindowWatcher(network);network._window_watcher=watcher
                app.installEventFilter(watcher)
        return
    if vars(network).get('_disconnect_job') is not None:return
    network.disconnect_pending=False
    keep_steam,keep_vpn=(False,False) if release_session else session_preferences()
    if keep_steam:return  # Steam depends on the isolated VPN namespace.
    if not keep_vpn:network.cancel()
    from .plugin import Job
    def disconnect():
        if keep_vpn:
            if vars(network).get('container'):
                from .steam_runtime import SteamRuntime
                try:SteamRuntime(network).request('stop')
                except OSError:pass  # Steam has not been started in this session.
            return 'Steam stopped; VPN retained for this Playlite session'
        network.disconnect()
        return 'Disconnected'
    job=Job(disconnect)
    network._disconnect_job=job
    def finished(_):
        # Keep the runnable and its signal owner alive until Qt has finished
        # delivering this signal. Releasing the last reference inside the
        # connected callback can destroy the signal while it is being emitted.
        def release():
            if vars(network).get('_disconnect_job') is job:
                network._disconnect_job=None
        QTimer.singleShot(0,release)
    job.signals.finished.connect(finished)
    QThreadPool.globalInstance().start(job)


def watch_queue(network,queue):
    network.download_queue=queue
    if getattr(network,'is_vm',False) is True and vars(network).get('_idle_watcher') is None:
        network._idle_watcher=VMIdleWatcher(network,queue)
    if vars(network).get('_watched_queue') is queue:return
    old=vars(network).get('_watched_queue')
    if old is not None:
        try:old.changed.disconnect(network._queue_changed)
        except (RuntimeError,TypeError):pass  # The previous library window may already be destroyed.
    network._watched_queue=queue
    network._queue_was_busy=has_downloads(network)
    def changed():
        busy=has_downloads(network)
        drained=network._queue_was_busy and not busy
        network._queue_was_busy=busy
        if not busy and (drained or vars(network).get('disconnect_pending',False)):
            disconnect_when_idle(network)
    network._queue_changed=changed
    queue.changed.connect(changed)


class VMIdleWatcher(QObject):
    """Release an idle VM without booting it or blocking the library UI."""
    def __init__(self,network,queue):
        super().__init__(queue)
        self.network=network
        self.job=None
        self.timer=QTimer(self)
        self.timer.setInterval(15000)
        self.timer.timeout.connect(self.check)
        self.timer.start()

    def check(self):
        if self.job is not None or has_downloads(self.network):return
        app=QApplication.instance()
        if app and any(getattr(widget,'network',None) is self.network and
                       (getattr(widget,'busy',False) or getattr(widget,'jobs',None)) for widget in app.allWidgets()):return
        from .plugin import Job
        from .vm_backend import Agent,installer_module
        import time
        def work():
            if not (self.network.profile/'vm.json').exists():return 'VM is not installed'
            marker=self.network.profile/'last-use'
            if marker.exists() and time.time()-marker.stat().st_mtime<60:return 'VM recently used'
            cfg=self.network.cfg
            state=installer_module().execute(['virsh','-c','qemu:///session','domstate',cfg['domain']]).stdout.strip()
            if state!='running':return 'VM is off'
            agent=Agent(cfg);agent.track_activity=False
            if agent.rpc({'command':'idle_status'}).get('busy') is not False:return 'VM has active work'
            if has_downloads(self.network) or marker.exists() and time.time()-marker.stat().st_mtime<60:return 'VM recently used'
            self.network.close_session_background()
            return 'Idle Steam VM shutdown requested'
        job=Job(work);self.job=job
        def done(_):
            QTimer.singleShot(0,lambda:setattr(self,'job',None))
        job.signals.finished.connect(done)
        QThreadPool.globalInstance().start(job)
