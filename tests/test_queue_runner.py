import unittest
from unittest.mock import Mock,patch
import tempfile
from pathlib import Path
import test_worker
from PyQt6.QtWidgets import QApplication,QWidget
from playlite.downloads import DownloadQueue
from downloader.download_dialog import DownloadDialog
from downloader.queue_runner import QueueRunner
from downloader.providers import Depot

class QueueIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def test_enqueue_transfers_download_and_dialog_can_close_without_disconnect(self):
        window=QWidget();window.download_queue=DownloadQueue(window);window.downloads_panel=Mock()
        window.download_queue.pump=Mock()
        network=Mock();dialog=DownloadDialog(network,window)
        dialog.backend.blockSignals(True)
        dialog.backend.setCurrentIndex(dialog.backend.findData('depot'))
        dialog.backend.blockSignals(False)
        dialog.owns_connection=True;dialog.pack_app=10;dialog.appid.setText('10');dialog.pack_source='Hubcap'
        dialog.game_name='Game';dialog.content_info={'game':{'id':10,'name':'Game','depots':{}},'dlc':[]}
        dialog.rows=[Depot(11,1,b'manifest')];plan=[(10,dialog.rows[0],'Game')]
        dialog.destination.setText('/tmp/playlite-queue-integration-new')
        with patch.object(dialog,'task',side_effect=lambda operation,done,**kwargs:done((plan,['OK']))):
            dialog.prepare_legacy_download()
        self.assertEqual(len(window.download_queue.entries),1)
        self.assertFalse(dialog.owns_connection)
        self.assertEqual(dialog.start_button.text(),'Add to queue')
        dialog.close();self.app.processEvents();network.disconnect.assert_not_called()
        window.download_queue.shutdown();window.close()

    def test_runner_completion_updates_queue_and_keeps_add_game_action(self):
        window=QWidget();queue=DownloadQueue(window);network=Mock()
        row=queue.enqueue('Game','/tmp/queue-native',Mock());queue.pump=Mock();queue.active=row;row.state='Downloading'
        depot=Depot(11,1,b'manifest')
        runner=QueueRunner(network,window,queue,row,{'app':10,'rows':[depot],'plan':[(10,depot,'Game')],'info':{},'username':'account'})
        row.controller=runner;runner.read_output=Mock();runner.started_at=0
        runner.output='Total downloaded: 10 bytes (20 bytes uncompressed) from 1 depots'
        with tempfile.TemporaryDirectory() as directory:
            runner.download_destination=Path(directory);runner.download_staging=Path(directory)/'.playlite-download';runner.download_staging.mkdir()
            (runner.download_staging/'file').write_text('validated')
            runner.finished(0)
            self.assertEqual(row.state,'Complete');self.assertIsNone(queue.active)
            self.assertIs(row.controller,runner);self.assertTrue(callable(row.controller.add_to_library))
        queue.clear_finished();window.close()
