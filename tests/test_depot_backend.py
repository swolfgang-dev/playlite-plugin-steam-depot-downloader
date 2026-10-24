"""Desktop-free provider/CDN routing, persistence and failure handling."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
import test_worker
from PyQt6.QtCore import QCoreApplication,QEvent
from PyQt6.QtWidgets import QWidget
from PyQt6 import sip
from playlite.downloads import DownloadQueue
from downloader.download_dialog import DownloadDialog
from downloader.network import Network
from downloader.plugin import Plugin
from downloader.queue_runner import QueueRunner,validate_snapshot
from downloader.depot_setup import DepotSettings,require_worker,REVISION
from downloader.manifest_resolver import ManifestResolver
from downloader.providers import Depot
from test_app_info import fixture


def choices():
    return dict(app=10,platform='linux',language='english',architecture='64',
                branch='public',provider='Hubcap',username='account',dlc=[])


class DepotBackendTests(unittest.TestCase):
    def setUp(self):self.windows=[]
    def tearDown(self):
        for widget in test_worker.APP.topLevelWidgets():
            if not sip.isdeleted(widget):
                widget.close();sip.delete(widget)
        QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)

    def network(self):
        network=Network();network.is_depot=True
        network.container='verified-gateway';network.check=Mock(return_value='VPN protected')
        return network

    def test_default_plugin_never_constructs_vm_and_uses_depot_settings(self):
        with patch.dict(os.environ,{'PLAYLITE_STEAM_BACKEND':'depot'}),patch.object(test_worker.APP,'_playlite_depot_network',None,create=True),patch('downloader.vm_backend.VMNetwork') as vm:
            plugin=Plugin()
            self.assertIs(plugin.network.is_depot,True)
            self.assertIsInstance(plugin.create_settings(),DepotSettings)
            vm.assert_not_called()

    def test_worker_requires_pinned_revision(self):
        with patch('downloader.depot_setup.subprocess.run',return_value=Mock(returncode=0,stdout=json.dumps([{'Config':{'Labels':{'io.playlite.depot-worker.revision':REVISION}}}]))):
            self.assertIn('ready',require_worker())
        with patch('downloader.depot_setup.subprocess.run',return_value=Mock(returncode=0,stdout='[{"Config":{"Labels":{}}}]')):
            with self.assertRaisesRegex(RuntimeError,'Build the depot worker'):require_worker()

    def test_selection_uses_worker_metadata_without_steam_desktop(self):
        dialog=DownloadDialog(self.network())
        dialog.appid.setText('10');dialog.username.setText('account')
        dialog.task=lambda operation,done,**kwargs:done(operation())
        with patch('downloader.app_info.fetch_app_info',return_value=fixture()) as metadata,patch('downloader.steam_catalog.fetch_catalog') as store:
            dialog.fetch_pack()
        metadata.assert_called_once_with(dialog.network,10,'account');store.assert_not_called()
        self.assertGreaterEqual(dialog.depot.findData('linux'),0)
        self.assertEqual(dialog.transport.image,'playlite-depot-worker:test')

    def test_persisted_queue_contains_choices_and_restores_factory(self):
        window=QWidget()
        with tempfile.TemporaryDirectory() as directory:
            window.download_queue=DownloadQueue(window,storage=Path(directory)/'queue.json')
            window.download_queue.pump=Mock()
            dialog=DownloadDialog(self.network(),window)
            dialog.appid.setText('10');dialog.pack_app=10;dialog.content_info=fixture();dialog.game_name='Game'
            dialog.username.setText('account');dialog.depot.addItem('Linux','linux')
            dialog.destination.setText(str(Path(directory)/'game'))
            dialog.prepare_download()
            self.assertEqual(len(window.download_queue.entries),1,dialog.status.text())
            row=window.download_queue.entries[0]
            self.assertEqual(row.metadata,{'backend':'isolated_depot','snapshot':choices()})
            restored=DownloadQueue(window,storage=window.download_queue.storage)
            plugin=Plugin.__new__(Plugin);plugin.network=dialog.network
            plugin.restore_downloads(window,restored)
            self.assertEqual(restored.entries[0].state,'Paused')
            self.assertTrue(callable(restored.entries[0].factory))
            window.download_queue.shutdown();restored.shutdown()

    def runner(self,directory):
        window=QWidget();queue=DownloadQueue(window);queue.pump=Mock()
        self.windows.append(window)
        row=queue.enqueue('Game',str(Path(directory)/'game'),Mock())
        queue.active=row;row.state='Downloading'
        runner=QueueRunner(self.network(),window,queue,row,choices());row.controller=runner
        return runner,queue,row

    def test_start_resolves_matching_manifests_checks_cdn_then_downloads(self):
        jobs=[]
        with tempfile.TemporaryDirectory() as directory:
            runner,queue,row=self.runner(directory)
            runner.download=Mock(side_effect=lambda:setattr(runner,'busy',True))
            runner.fetch_manifest=Mock(return_value=[Depot(11,1,b'one'),Depot(12,1,b'two')])
            with patch('downloader.plugin.QThreadPool.globalInstance',return_value=Mock(start=jobs.append)),patch('downloader.depot_setup.require_worker'),patch('downloader.app_info.fetch_app_info',return_value=fixture()),patch('downloader.preflight.check_plan',return_value=['OK','OK']) as preflight,patch('downloader.steam_runtime.start_with_vpn') as steam:
                runner.start();jobs[0].run()
            self.assertEqual([depot.id for _,depot,_ in runner.content_plan],[11,12],row.status)
            preflight.assert_called_once();runner.download.assert_called_once();steam.assert_not_called()
            runner.busy=False;runner.finish_queue(False,'Stopped');queue.shutdown()

    def test_failed_vpn_never_fetches_providers_or_launches_download(self):
        jobs=[]
        with tempfile.TemporaryDirectory() as directory:
            runner,queue,row=self.runner(directory)
            runner.network.check.side_effect=RuntimeError('VPN disconnected')
            runner.download=Mock();runner.fetch_manifest=Mock()
            with patch('downloader.plugin.QThreadPool.globalInstance',return_value=Mock(start=jobs.append)):
                runner.start();jobs[0].run()
            self.assertEqual(row.state,'Failed');runner.download.assert_not_called();runner.fetch_manifest.assert_not_called()
            self.assertTrue(callable(row.factory));queue.shutdown()

    def test_provider_fallback_is_limited_to_hubcap_and_luatools(self):
        fetch=Mock(return_value=[])
        resolver=ManifestResolver('Hubcap',fetch,providers=('Hubcap','Luie'))
        self.assertEqual(resolver.first_pack(10),[])
        self.assertEqual([call.args[0] for call in fetch.call_args_list],['Hubcap','Luie'])

    def test_snapshot_drops_secrets_and_rejects_malformed_choices(self):
        self.assertEqual(validate_snapshot(choices()|{'key':'secret','access_token':'secret'}),choices())
        for extra in ({'app':True},{'dlc':[False]},{'provider':'Ryuu'},{'username':'user\nsecret'},{'language':None}):
            with self.subTest(extra=extra),self.assertRaises(ValueError):validate_snapshot(choices()|extra)

    def test_completed_worker_is_not_promoted_after_pause(self):
        with tempfile.TemporaryDirectory() as directory:
            runner,queue,row=self.runner(directory)
            runner.busy=True;row.cancelled=True;row.paused=True
            runner.read_output=Mock();runner.output='Total downloaded: 1 bytes from 1 depots'
            with patch('downloader.download_flow.finalize_download') as finalize:
                runner.finished(0)
            finalize.assert_not_called();self.assertEqual(row.state,'Paused');queue.shutdown()
