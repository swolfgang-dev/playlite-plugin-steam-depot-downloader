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
        self.preferences=patch('downloader.vpn_lifecycle.session_preferences',return_value=(False,False)).start()
        self.pool=patch('downloader.vpn_lifecycle.QThreadPool').start()
        self.addCleanup(patch.stopall)
        self.pool.globalInstance.return_value.start.side_effect=lambda job:job.run()

    def test_finished_disconnect_releases_job_after_signal_dispatch(self):
        from PyQt6.QtWidgets import QApplication
        app=QApplication.instance() or QApplication([])
        disconnect_when_idle(self.network)
        self.assertIsNotNone(self.network._disconnect_job)
        self.assertFalse(self.network._disconnect_job.autoDelete())
        app.processEvents()
        self.assertIsNone(self.network._disconnect_job)

    def test_game_launch_releases_idle_environment_despite_session_preferences(self):
        self.preferences.return_value=(True,True)
        disconnect_when_idle(self.network,release_session=True)
        self.network.cancel.assert_called_once()
        self.network.disconnect.assert_called_once()

    def test_game_launch_preserves_active_and_queued_downloads(self):
        self.preferences.return_value=(True,True)
        self.network.download_queue=Queue(['Downloading','Queued'])
        disconnect_when_idle(self.network,release_session=True)
        self.network.cancel.assert_not_called()
        self.network.disconnect.assert_not_called()

    def test_game_launch_preserves_environment_for_open_windows(self):
        with patch('downloader.vpn_lifecycle.has_open_windows',return_value=True):
            disconnect_when_idle(self.network,release_session=True)
        self.network.disconnect.assert_not_called()

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

    def test_queue_drains_without_open_windows(self):
        queue=Queue(['Downloading']);watch_queue(self.network,queue)
        queue.entries[0].state='Failed';queue.changed.emit()
        self.network.disconnect.assert_called_once()
        queue.changed.emit();self.network.disconnect.assert_called_once()

    def test_finished_queue_keeps_connection_for_open_window(self):
        queue=Queue(['Downloading']);watch_queue(self.network,queue)
        with patch('downloader.vpn_lifecycle.has_open_windows',return_value=True):
            queue.entries[0].state='Complete';queue.changed.emit()
            self.network.disconnect.assert_not_called()
            self.network.cancel.assert_not_called()
            self.assertTrue(self.network.disconnect_pending)
        disconnect_when_idle(self.network)
        self.network.disconnect.assert_called_once()

    def test_closing_one_window_keeps_connection_for_another(self):
        with patch('downloader.vpn_lifecycle.has_open_windows',return_value=True):
            disconnect_when_idle(self.network)
            self.network.disconnect.assert_not_called()
            self.network.cancel.assert_not_called()
        disconnect_when_idle(self.network)
        self.network.disconnect.assert_called_once()

    def test_cancelled_waiting_download_releases_vpn(self):
        queue=Queue(['Queued']);watch_queue(self.network,queue)
        disconnect_when_idle(self.network)
        queue.entries[0].state='Cancelled';queue.changed.emit()
        self.network.disconnect.assert_called_once()

    def test_keep_steam_also_keeps_vpn(self):
        self.preferences.return_value=(True,False)
        queue=Queue(['Downloading']);watch_queue(self.network,queue)
        queue.entries[0].state='Complete';queue.changed.emit()
        self.network.cancel.assert_not_called()
        self.network.disconnect.assert_not_called()
        self.pool.globalInstance.return_value.start.assert_not_called()

    def test_keep_vpn_stops_only_steam(self):
        self.preferences.return_value=(False,True)
        self.network.container='isolated-vpn'
        with patch('downloader.steam_runtime.SteamRuntime') as runtime:
            disconnect_when_idle(self.network)
            runtime.return_value.request.assert_called_once_with('stop')
        self.network.cancel.assert_not_called()
        self.network.disconnect.assert_not_called()

    def test_keep_vpn_does_not_stop_steam_during_download(self):
        self.preferences.return_value=(False,True)
        queue=Queue(['Downloading']);watch_queue(self.network,queue)
        with patch('downloader.steam_runtime.SteamRuntime') as runtime:
            disconnect_when_idle(self.network)
            runtime.assert_not_called()
