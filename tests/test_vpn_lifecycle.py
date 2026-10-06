import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
import test_worker
from PyQt6.QtCore import QObject,pyqtSignal
from downloader.vpn_lifecycle import disconnect_when_idle,watch_queue

class Queue(QObject):
    changed=pyqtSignal()
    def __init__(self,states):
        super().__init__();self.entries=[SimpleNamespace(state=state) for state in states]

class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.network=Mock()
        self.pool=patch('downloader.vpn_lifecycle.QThreadPool').start()
        self.addCleanup(patch.stopall)
        self.pool.globalInstance.return_value.start.side_effect=lambda job:job.run()

    def test_idle_close_disconnects_even_without_connection_ownership(self):
        disconnect_when_idle(self.network)
        self.network.disconnect.assert_called_once()

    def test_close_keeps_vpn_until_last_download_finishes(self):
        queue=Queue(['Downloading','Queued']);watch_queue(self.network,queue)
        disconnect_when_idle(self.network)
        self.network.cancel.assert_not_called();self.network.disconnect.assert_not_called()
        queue.entries[0].state='Complete';queue.entries[1].state='Downloading';queue.changed.emit()
        self.network.disconnect.assert_not_called()
        queue.entries[1].state='Complete';queue.changed.emit()
        self.network.disconnect.assert_called_once()

    def test_queue_drains_even_if_downloader_stays_open(self):
        queue=Queue(['Downloading']);watch_queue(self.network,queue)
        queue.entries[0].state='Failed';queue.changed.emit()
        self.network.disconnect.assert_called_once()
        queue.changed.emit();self.network.disconnect.assert_called_once()

    def test_cancelled_waiting_download_releases_vpn(self):
        queue=Queue(['Queued']);watch_queue(self.network,queue)
        disconnect_when_idle(self.network)
        queue.entries[0].state='Cancelled';queue.changed.emit()
        self.network.disconnect.assert_called_once()
