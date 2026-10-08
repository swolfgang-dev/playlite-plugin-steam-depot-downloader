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
    def test_vpn_reconnects_after_failed_check_and_checks_new_tunnel(self):
        from downloader.steam_queue_runner import ensure_vpn
        network=Mock();network.container='existing';network.check.side_effect=[RuntimeError('lost'),None]
        with patch('downloader.credentials.Wallet') as wallet:
            wallet.return_value.read.return_value=('user','secret')
            ensure_vpn(network,'Canada','udp',Mock())
        network.connect.assert_called_once()
        network.cancelled.clear.assert_called_once()
        self.assertEqual(network.check.call_count,2)

    def test_healthy_vpn_is_kept_and_reconnection_failure_propagates(self):
        from downloader.steam_queue_runner import ensure_vpn
        network=Mock();network.container='existing'
        ensure_vpn(network,'','udp',Mock());network.connect.assert_not_called()
        network.check.side_effect=RuntimeError('lost')
        network.connect.side_effect=RuntimeError('connection failed')
        with patch('downloader.credentials.Wallet') as wallet:
            wallet.return_value.read.return_value=('user','secret')
            with self.assertRaisesRegex(RuntimeError,'connection failed'):ensure_vpn(network,'','udp',Mock())

    def test_retry_reuses_verified_staging_and_copies_missing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source';source.mkdir()
            (source/'ready.bin').write_bytes(b'ready');(source/'missing.bin').write_bytes(b'missing')
            staging=root/'installed/.playlite-download';staging.mkdir(parents=True)
            (staging/'ready.bin').write_bytes(b'ready')
            with patch('downloader.download_flow.copy_file_exclusive',wraps=__import__('downloader.download_flow',fromlist=['copy_file_exclusive']).copy_file_exclusive) as copy:
                export_install(source,root/'installed')
                self.assertEqual(copy.call_count,1)
            self.assertEqual((root/'installed/ready.bin').read_bytes(),b'ready')
            self.assertEqual((root/'installed/missing.bin').read_bytes(),b'missing')
            self.assertFalse(staging.exists())

    def test_retry_rejects_unrelated_staging_and_destination_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source';source.mkdir();(source/'game.bin').write_bytes(b'game')
            staging=root/'installed/.playlite-download';staging.mkdir(parents=True)
            (staging/'unrelated.txt').write_text('keep')
            with self.assertRaisesRegex(ValueError,'unexpected'):export_install(source,root/'installed')
            self.assertEqual((staging/'unrelated.txt').read_text(),'keep')
            (root/'installed/user.txt').write_text('keep')
            with self.assertRaisesRegex(ValueError,'already contains'):export_install(source,root/'installed')

    def test_install_copy_reports_chunk_progress_and_verification(self):
        import itertools
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source';source.mkdir()
            (source/'game.bin').write_bytes(b'x'*(3*1024*1024))
            updates=[]
            with patch('downloader.steam_queue_runner.time.monotonic',side_effect=itertools.count(1)):
                export_install(source,root/'installed',progress=lambda message,amount:updates.append((message,amount)))
            self.assertEqual(updates[0][1],0)
            self.assertEqual(updates[-1][1],100)
            self.assertTrue(any('Copying files ·' in message and 0<amount<100 for message,amount in updates))
            self.assertTrue(any('Verifying copied files' in message for message,_ in updates))
            for message,amount in updates[1:-1]:
                self.assertIn('Mbps',message)
                self.assertIn('min remaining',message)

            self.assertEqual([amount for _,amount in updates],sorted(amount for _,amount in updates))
            self.assertEqual((root/'installed/game.bin').stat().st_size,3*1024*1024)

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
            from unittest.mock import ANY
            export.assert_called_once_with(Path('/private/library/steamapps/common/Game'),row.destination,progress=ANY,cancelled=ANY)
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

class VMDirectInstallTests(unittest.TestCase):
    def setUp(self):
        self.app=QApplication.instance() or QApplication([])

    def test_completed_vm_install_uses_same_files_and_never_exports_or_uninstalls(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'Steam Folder';source.mkdir();(source/'game.exe').write_bytes(b'verified')
            window=QWidget();queue=DownloadQueue(window,storage=Path(folder)/'queue.json');queue.pump=Mock()
            row=queue.enqueue('Store title',str(Path(folder)/'Store title'),Mock());queue.active=row;row.state='Downloading'
            network=Mock(is_vm=True,container='vm')
            runtime=Mock(direct_install=True)
            runtime.installation_path.return_value=source
            def request(command,*args,**kwargs):
                if command=='has_game':return {'exists':True}
                if command=='retail_selection':return {'restart_required':False}
                if command=='download_status':return {'installed':True,'flags':4,'directory':'steamapps/common/Steam Folder','depots':[11]}
                return {}
            runtime.request.side_effect=request
            snapshot={'app':10,'platform':'windows','language':'english','dlc':[],'info':metadata()}
            with patch('downloader.steam_queue_runner.SteamRuntime',return_value=runtime),patch('downloader.steam_queue_runner.QThreadPool') as pool,patch('downloader.steam_queue_runner.export_install') as export:
                pool.globalInstance.return_value.start.side_effect=lambda job:job.run()
                runner=SteamQueueRunner(network,window,queue,row,snapshot);runner.start()
                self.assertEqual(row.state,'Complete');self.assertEqual(row.destination,str(source))
                export.assert_not_called()
                self.assertNotIn('finish_export',[call.args[0] for call in runtime.request.call_args_list])
                self.assertNotIn('uninstall',[call.args[0] for call in runtime.request.call_args_list])
                self.assertEqual((source/'game.exe').read_bytes(),b'verified')
                self.assertFalse((source/'.playlite-download').exists())
                import json
                self.assertEqual(json.loads(queue.storage.read_text())[0]['destination'],str(source))
                runner.dispose()
            window.close()
