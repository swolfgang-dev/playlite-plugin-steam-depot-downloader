import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
import test_worker
from PyQt6.QtWidgets import QApplication,QWidget
from playlite.downloads import DownloadQueue
from downloader.download_dialog import DownloadDialog
from downloader.steam_queue_runner import SteamQueueRunner,verify_platform,export_install


def metadata():
    return {'game':{'id':10,'name':'Game','depots':{
        '11':{'config':{'oslist':'windows'},'manifests':{}},
        '12':{'config':{'oslist':'linux'},'manifests':{}}}},'dlc':[]}


class SteamQueueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def test_platform_and_language_are_snapshotted_when_queued(self):
        window=QWidget();window.download_queue=DownloadQueue(window)
        window.download_queue.pump=Mock();window.downloads_panel=Mock()
        dialog=DownloadDialog(Mock(),window)
        dialog.backend.blockSignals(True);dialog.backend.setCurrentIndex(dialog.backend.findData('steam'))
        dialog.content_info=metadata();dialog.pack_app=10;dialog.selected_app=10;dialog.game_name='Game'
        dialog.appid.setText('Game')
        dialog.depot.addItem('Windows','windows');dialog.depot.setCurrentIndex(0)
        with tempfile.TemporaryDirectory() as directory:
            dialog.destination.setText(str(Path(directory)/'Game'))
            dialog.prepare_download()
            self.assertEqual(len(window.download_queue.entries),1)
            row=window.download_queue.entries[0]
            controller=row.factory(window.download_queue,row)
            dialog.depot.addItem('Linux','linux');dialog.depot.setCurrentIndex(1)
            self.assertEqual(controller.snapshot['platform'],'windows')
            self.assertEqual(controller.snapshot['language'],'english')
            self.assertIsNot(controller.snapshot['info'],dialog.content_info)
            controller.dispose()
        window.download_queue.shutdown();dialog.deleteLater();window.close()

    def test_macos_selection_never_silently_queues_linux(self):
        window=QWidget();window.download_queue=DownloadQueue(window);window.downloads_panel=Mock()
        dialog=DownloadDialog(Mock(),window)
        dialog.backend.blockSignals(True);dialog.backend.setCurrentIndex(dialog.backend.findData('steam'))
        dialog.content_info=metadata();dialog.pack_app=10;dialog.selected_app=10
        dialog.depot.addItem('macOS','macos');dialog.depot.setCurrentIndex(0)
        dialog.prepare_download()
        self.assertEqual(window.download_queue.entries,[])
        self.assertIn('macOS',dialog.status.text())
        dialog.deleteLater();window.close()

    def test_completed_wrong_platform_is_not_accepted(self):
        self.assertFalse(verify_platform({'installed':True,'depots':[12]},metadata(),'windows'))
        self.assertTrue(verify_platform({'installed':True,'depots':[11]},metadata(),'windows'))
        self.assertFalse(verify_platform({'installed':False,'depots':[11]},metadata(),'windows'))

    def test_queue_applies_platform_before_waiting_and_exporting(self):
        window=QWidget();queue=DownloadQueue(window);queue.pump=Mock()
        row=queue.enqueue('Game','/tmp/playlite-steam-queue-test',Mock())
        queue.active=row;row.state='Downloading'
        network=Mock();network.container='gateway'
        runtime=Mock();runtime.root=Path('/private')
        statuses=iter([{'installed':True,'depots':[12]},
                       {'installed':True,'depots':[11],'directory':'steamapps/common/Game'}])
        calls=[]
        def request(command,app=None,**kwargs):
            calls.append((command,kwargs))
            if command=='add_status':return {'state':{'status':'done'}}
            if command=='download_status':return next(statuses)
            return {}
        runtime.request.side_effect=request
        snapshot={'app':10,'platform':'windows','language':'english','dlc':[],'info':metadata()}
        with patch('downloader.steam_queue_runner.SteamRuntime',return_value=runtime), \
             patch('downloader.steam_queue_runner.QThreadPool') as pool, \
             patch('downloader.steam_queue_runner.export_install') as export:
            runner=SteamQueueRunner(network,window,queue,row,snapshot)
            runner.cancelled.wait=Mock(return_value=False)
            pool.globalInstance.return_value.start.side_effect=lambda job:job.run()
            runner.start()
            self.assertEqual(row.state,'Complete')
            self.assertIn(('install',{'platform':'windows','language':'english','dlc':[]}),calls)
            self.assertEqual(sum(command=='download_status' for command,_ in calls),2)
            export.assert_called_once_with(Path('/private/library/steamapps/common/Game'),row.destination)
            runner.dispose()
        window.close()

    def test_export_keeps_steam_source_and_refuses_existing_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source';source.mkdir()
            (source/'Game.exe').write_bytes(b'verified-content')
            destination=root/'output';export_install(source,destination)
            self.assertEqual((destination/'Game.exe').read_bytes(),b'verified-content')
            self.assertTrue((source/'Game.exe').exists())
            with self.assertRaises(ValueError):export_install(source,destination)
            self.assertEqual((destination/'Game.exe').read_bytes(),b'verified-content')

    def test_cancelling_active_steam_download_pauses_only_that_app(self):
        window=QWidget();queue=DownloadQueue(window);queue.pump=Mock()
        row=queue.enqueue('Game','/tmp/playlite-steam-cancel-test',Mock())
        queue.active=row;row.state='Downloading'
        network=Mock();network.container='gateway';runtime=Mock()
        snapshot={'app':10,'platform':'windows','language':'english','dlc':[],'info':metadata()}
        with patch('downloader.steam_queue_runner.SteamRuntime',return_value=runtime), \
             patch('downloader.steam_queue_runner.QThreadPool') as pool, \
             patch('downloader.steam_queue_runner.export_install') as export:
            runner=SteamQueueRunner(network,window,queue,row,snapshot);row.controller=runner
            def request(command,app=None,**kwargs):
                if command=='add_status':return {'state':{'status':'done'}}
                if command=='download_status':
                    queue.cancel(row);return {'installed':False}
                return {}
            runtime.request.side_effect=request
            pool.globalInstance.return_value.start.side_effect=lambda job:job.run()
            runner.start()
            self.assertEqual(row.state,'Cancelled')
            runtime.request.assert_any_call('pause',10)
            export.assert_not_called();runner.dispose()
        window.close()
