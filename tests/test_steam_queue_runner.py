import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
import test_worker
from PyQt6.QtWidgets import QApplication,QWidget
from playlite.downloads import DownloadQueue
from downloader.download_dialog import DownloadDialog
from downloader.steam_queue_runner import SteamQueueRunner,verify_platform,export_install,persisted_snapshot,PausedRecovery


def metadata():
    return {'game':{'id':10,'name':'Game','depots':{
        '11':{'config':{'oslist':'windows'},'manifests':{}},
        '12':{'config':{'oslist':'linux'},'manifests':{}}}},'dlc':[]}


class SteamQueueTests(unittest.TestCase):
    def test_paused_recovery_waits_for_stall_and_runs_only_once(self):
        recovery=PausedRecovery()
        state={'flags':1538,'downloaded':100}
        self.assertFalse(recovery.needed(state,0))
        self.assertFalse(recovery.needed(state,29))
        state['downloaded']=200
        self.assertFalse(recovery.needed(state,30))
        self.assertFalse(recovery.needed(state,59))
        self.assertTrue(recovery.needed(state,60))
        self.assertFalse(recovery.needed(state,100))

    def test_resumed_download_never_triggers_recovery(self):
        recovery=PausedRecovery()
        self.assertFalse(recovery.needed({'flags':512},0))
        self.assertFalse(recovery.needed({'flags':1024},10))
        self.assertFalse(recovery.needed({'flags':1024},100))
    def test_persisted_choices_exclude_provider_credentials_and_depot_keys(self):
        info=metadata();info['api_key']='secret';info['game']['depots']['11']['key']='secret'
        snapshot=persisted_snapshot({'app':10,'platform':'windows','language':'english','dlc':[],'info':info})
        self.assertNotIn('secret',str(snapshot))
        self.assertEqual(snapshot['info']['game']['depots']['11']['config']['oslist'],'windows')

    def test_restore_rebinds_retry_and_completed_handoff_without_starting(self):
        from downloader.plugin import Plugin
        import json
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'downloads.json'
            snapshot=persisted_snapshot({'app':10,'platform':'windows','language':'english','dlc':[],'info':metadata()})
            path.write_text(json.dumps([{'name':'Game','destination':'/tmp/restored-game','id':'restored',
                'state':'Complete','status':'Verified','progress':100,'metadata':{'backend':'isolated_steam','snapshot':snapshot}}]))
            window=QWidget();queue=DownloadQueue(window,storage=path);plugin=Plugin()
            with patch('downloader.steam_queue_runner.SteamQueueRunner') as runner:
                plugin.restore_downloads(window,queue)
                row=queue.entries[0]
                self.assertTrue(callable(row.factory));self.assertEqual(row.steam_appid,10)
                runner.return_value.start.assert_not_called()
                self.assertIs(row.controller,runner.return_value)

    def test_pause_stops_native_transfer_and_retains_resumable_entry(self):
        window=QWidget();queue=DownloadQueue(window);queue.pump=Mock()
        row=queue.enqueue('Game','/tmp/paused-native',Mock());queue.active=row;row.state='Downloading'
        network=Mock();network.container='gateway';runtime=Mock()
        def request(command,*args,**kwargs):
            if command=='has_game':return {'exists':True}
            if command=='download_status':return {'installed':False,'flags':1024,'downloaded':10,'total':100}
            return {}
        runtime.request.side_effect=request
        snapshot={'app':10,'platform':'windows','language':'english','dlc':[],'info':metadata()}
        with patch('downloader.steam_queue_runner.SteamRuntime',return_value=runtime),patch('downloader.steam_queue_runner.QThreadPool') as pool:
            runner=SteamQueueRunner(network,window,queue,row,snapshot);row.controller=runner
            runner.cancelled.wait=Mock(return_value=False)
            queue.changed.connect(lambda:queue.pause(row) if 'Steam is downloading' in row.status and not row.cancelled else None)
            pool.globalInstance.return_value.start.side_effect=lambda job:job.run()
            runner.start()
            self.assertEqual(row.state,'Paused')
            self.assertIn('partial files retained',row.status)
            self.assertIn(unittest.mock.call('pause',10),runtime.request.call_args_list)

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

    def test_download_panel_handoff_retains_selected_game_identity(self):
        window=QWidget();queue=DownloadQueue(window);queue.pump=Mock()
        row=queue.enqueue('ELDEN RING','/tmp/playlite-elden-handoff',Mock())
        snapshot={'app':1245620,'platform':'windows','language':'english','dlc':[],'info':metadata()}
        runner=SteamQueueRunner(Mock(),window,queue,row,snapshot)
        from PyQt6.QtWidgets import QLineEdit
        handoff=Mock();handoff.appid=QLineEdit();handoff.destination=QLineEdit()
        with patch('downloader.download_dialog.DownloadDialog',return_value=handoff):
            runner.add_to_library()
            self.assertEqual(DownloadDialog.game_app(runner.dialog),1245620)
            self.assertEqual(runner.dialog.appid.text(),'ELDEN RING')
            self.assertEqual(runner.dialog.destination.text(),row.destination)
            handoff.add_to_library.assert_called_once()
        runner.dispose();window.close()

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
            if command=='finish_export':self.assertTrue(export.called,'Private cleanup must follow verified export')
            if command=='retail_selection' and sum(name=='retail_selection' for name,_ in calls)==1:
                raise RuntimeError('Steam could not apply the selected content settings. Check its isolated desktop.')
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
            self.assertEqual(sum(command=='retail_selection' for command,_ in calls),2)
            self.assertIn(('install',{'platform':'windows','language':'english','dlc':[]}),calls)
            self.assertEqual(sum(command=='download_status' for command,_ in calls),2)
            export.assert_called_once_with(Path('/private/library/steamapps/common/Game'),row.destination)
            self.assertIn(('finish_export',{}),calls)
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

    def test_paused_invalid_configuration_reports_manifest_failure(self):
        from downloader.steam_queue_runner import download_failure
        state={'flags':530,'events':[
            'AppID 10 update canceled : Manifest 11_123 is still encrypted (Invalid content configuration)',
            'AppID 10 scheduler finished : removed from schedule (result Invalid content configuration, state 0x212)']}
        message=download_failure(state)
        self.assertIn('Invalid content configuration',message)
        self.assertIn('Manifest 11_123 is still encrypted',message)
        state['flags']=1024
        self.assertIsNone(download_failure(state))

    def test_manual_pause_without_failed_result_is_not_an_installation_error(self):
        from downloader.steam_queue_runner import download_failure
        self.assertIsNone(download_failure({'flags':512,'events':['AppID 10 state changed : Update Paused,']}))

    def test_retry_ignores_previous_paused_manifest_error(self):
        from downloader.steam_queue_runner import download_failure
        previous=[
            '[old] AppID 10 update canceled : Manifest 11_123 is still encrypted (Invalid content configuration)',
            '[old] AppID 10 scheduler finished : removed from schedule (result Invalid content configuration, state 0x212)']
        state={'flags':530,'events':list(previous)}
        self.assertIsNone(download_failure(state,set(previous)))
        state['events'] += [
            '[new] AppID 10 update canceled : Content servers unreachable',
            '[new] AppID 10 scheduler finished : removed from schedule (result Content servers unreachable, state 0x212)']
        message=download_failure(state,set(previous))
        self.assertIn('Content servers unreachable',message)
        self.assertNotIn('still encrypted',message)

    def test_private_cleanup_follows_export_and_never_runs_after_failed_export(self):
        for export_fails,cleanup_fails in ((True,False),(False,True),(False,False)):
            with self.subTest(export_fails=export_fails,cleanup_fails=cleanup_fails):
                window=QWidget();queue=DownloadQueue(window);queue.pump=Mock()
                row=queue.enqueue('Game','/tmp/private-cleanup-test',Mock());queue.active=row;row.state='Downloading'
                network=Mock();network.container='gateway';runtime=Mock();runtime.root=Path('/private')
                def request(command,*args,**kwargs):
                    if command=='has_game':return {'exists':True}
                    if command=='download_status':return {'installed':True,'depots':[11],'directory':'steamapps/common/Game'}
                    if command=='finish_export':
                        self.assertTrue(export.called)
                        if cleanup_fails:raise RuntimeError('private cleanup failed')
                    return {}
                runtime.request.side_effect=request
                with patch('downloader.steam_queue_runner.SteamRuntime',return_value=runtime),patch('downloader.steam_queue_runner.QThreadPool') as pool,patch('downloader.steam_queue_runner.export_install',side_effect=OSError('copy failed') if export_fails else None) as export:
                    snapshot={'app':10,'platform':'windows','language':'english','dlc':[],'info':metadata()}
                    runner=SteamQueueRunner(network,window,queue,row,snapshot)
                    pool.globalInstance.return_value.start.side_effect=lambda job:job.run()
                    runner.start()
                    cleanup_calls=[call for call in runtime.request.call_args_list if call.args[0]=='finish_export']
                    self.assertEqual(bool(cleanup_calls),not export_fails)
                    self.assertEqual(row.state,'Failed' if export_fails else 'Complete')
                    if cleanup_fails:self.assertIn('cleanup pending',row.status)
                    runner.dispose()
                window.close()
