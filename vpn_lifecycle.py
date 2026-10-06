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


def disconnect_when_idle(network):
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
    keep_steam,keep_vpn=session_preferences()
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
    def finished(_):network._disconnect_job=None
    job.signals.finished.connect(finished)
    QThreadPool.globalInstance().start(job)


def watch_queue(network,queue):
    network.download_queue=queue
    if vars(network).get('_watched_queue') is queue:return
    old=vars(network).get('_watched_queue')
    if old is not None:old.changed.disconnect(network._queue_changed)
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
