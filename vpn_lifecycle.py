"""Keep the isolated VPN alive for queued workers, then release it once idle."""
from PyQt6.QtCore import QThreadPool


def has_downloads(network):
    queue=vars(network).get('download_queue')
    return queue is not None and any(row.state in ('Queued','Downloading') for row in queue.entries)


def disconnect_when_idle(network):
    if has_downloads(network):
        network.disconnect_pending=True
        watch_queue(network,network.download_queue)
        return
    if vars(network).get('_disconnect_job') is not None:return
    network.disconnect_pending=False
    network.cancel()
    from .plugin import Job
    def disconnect():
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
