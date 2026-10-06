import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
import test_worker
from downloader.steam_setup import provision,image_revision
from downloader.steam_catalog import fetch_catalog

class SetupTests(unittest.TestCase):
    def test_fresh_setup_waits_for_bootstrap_installs_moon_then_restarts(self):
        runtime=Mock();runtime.network.container=None
        with patch('downloader.steam_setup.ensure_image') as image,patch('downloader.steam_setup.wait_for',side_effect=[{'moon_installed':False},{'steam_ready':True},{'moon_installing':False,'moon_installed':True,'moon_install_exit':0}]):
            provision(runtime,'preferences',('user','secret'),Mock())
        image.assert_called_once()
        runtime.network.connect.assert_called_once()
        self.assertEqual([call.args[0] for call in runtime.request.call_args_list],['start','install_moon','start'])

    def test_existing_sessions_skip_moon_reinstallation(self):
        runtime=Mock();runtime.network.container='vpn'
        with patch('downloader.steam_setup.ensure_image'),patch('downloader.steam_setup.wait_for',return_value={'moon_installed':True}):
            provision(runtime,None,None,Mock())
        runtime.network.connect.assert_not_called()
        self.assertNotIn('install_moon',[call.args[0] for call in runtime.request.call_args_list])

    def test_failed_install_does_not_report_success_or_restart(self):
        runtime=Mock()
        with patch('downloader.steam_setup.ensure_image'),patch('downloader.steam_setup.wait_for',side_effect=[{'moon_installed':False},{'steam_ready':True},{'moon_installed':False,'moon_install_exit':1}]):
            with self.assertRaisesRegex(RuntimeError,'installation failed'):provision(runtime,None,None,Mock())
        self.assertEqual(runtime.request.call_count,2)

    def test_changed_build_context_invalidates_cached_image(self):
        with tempfile.TemporaryDirectory() as directory:
            context=Path(directory);file=context/'Dockerfile';file.write_text('first')
            first=image_revision(context);file.write_text('second')
            self.assertNotEqual(first,image_revision(context))

    def test_catalog_needs_no_worker_login_or_provider(self):
        transport=Mock()
        data={'10':{'success':True,'data':{'name':'Game','platforms':{'windows':True},'dlc':[11,11,True]}},'11':{'success':False}}
        transport.request.side_effect=lambda url:json.dumps({key:value for key,value in data.items() if 'appids='+key+'&' in url})
        info=fetch_catalog(transport,10)
        self.assertEqual(info['platforms'],['windows'])
        self.assertEqual(info['dlc'],[{'id':11,'name':'','depots':{}}])
        self.assertEqual(transport.request.call_count,2)

    def test_catalog_dlc_stays_selectable_without_binary_manifest_preflight(self):
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import Qt
        from downloader.download_dialog import DownloadDialog
        app=QApplication.instance() or QApplication([])
        dialog=DownloadDialog(Mock());dialog.pack_app=10
        dialog.content_info={'game':{'id':10,'name':'Game','depots':{}},'dlc':[{'id':11,'name':'','depots':{}}],'_steam_catalog':True}
        dialog.depot.addItem('Windows','windows');dialog.update_dlc_list()
        item=dialog.dlc_list.item(0)
        self.assertTrue(item.flags() & Qt.ItemFlag.ItemIsUserCheckable)
        self.assertTrue(item.flags() & Qt.ItemFlag.ItemIsEnabled)
        self.assertNotIn('Included in base-game',item.text())
        self.assertEqual(dialog.backend.count(),1)
        self.assertTrue(dialog.provider.isHidden())
        dialog.deleteLater()

    def test_closing_setup_keeps_parent_settings_connection(self):
        from PyQt6.QtWidgets import QApplication,QWidget
        from downloader.steam_runtime_dialog import SteamRuntimeDialog
        app=QApplication.instance() or QApplication([])
        settings=QWidget();settings.settings_closed=False
        dialog=SteamRuntimeDialog(Mock(),settings)
        with patch('downloader.vpn_lifecycle.disconnect_when_idle') as disconnect:
            dialog.close()
        disconnect.assert_not_called()
        settings.deleteLater()

    def test_closing_setup_keeps_parent_downloader_connection(self):
        from PyQt6.QtWidgets import QApplication,QWidget
        from downloader.steam_runtime_dialog import SteamRuntimeDialog
        app=QApplication.instance() or QApplication([])
        parent=QWidget();parent.network=Mock()
        dialog=SteamRuntimeDialog(parent.network,parent)
        with patch('downloader.vpn_lifecycle.disconnect_when_idle') as disconnect:
            dialog.close()
        disconnect.assert_not_called()
        parent.deleteLater()

    def test_standalone_setup_releases_connection_when_closed(self):
        from PyQt6.QtWidgets import QApplication
        from downloader.steam_runtime_dialog import SteamRuntimeDialog
        app=QApplication.instance() or QApplication([])
        network=Mock();dialog=SteamRuntimeDialog(network)
        with patch('downloader.vpn_lifecycle.disconnect_when_idle') as disconnect:
            dialog.close()
        disconnect.assert_called_once_with(network)
        dialog.deleteLater()

    def test_popup_close_does_not_stop_shared_desktop_relay(self):
        from PyQt6.QtWidgets import QApplication,QWidget
        from downloader.steam_runtime_dialog import SteamRuntimeDialog
        app=QApplication.instance() or QApplication([])
        parent=QWidget();parent.settings_closed=False
        dialog=SteamRuntimeDialog(Mock(),parent);dialog.relay=Mock()
        dialog.relay.poll.return_value=None
        dialog.close()
        dialog.relay.terminate.assert_not_called()
        parent.deleteLater()

    def test_vpn_disconnect_releases_desktop_relay(self):
        from downloader.network import Network
        network=Network();relay=Mock();relay.poll.return_value=None
        network._steam_desktop_relay=(relay,17864)
        network.disconnect()
        relay.terminate.assert_called_once()
        self.assertNotIn('_steam_desktop_relay',vars(network))

    def test_vpn_open_starts_installed_steam_client(self):
        from downloader.steam_runtime import start_with_vpn
        network=Mock();network.container='vpn'
        with patch('downloader.steam_runtime.subprocess.run',return_value=Mock(returncode=0)),patch('downloader.steam_runtime.SteamRuntime') as runtime:
            start_with_vpn(network)
        runtime.return_value.start.assert_called_once()
        runtime.return_value.request.assert_called_once_with('start')

    def test_vpn_open_does_not_provision_an_uninstalled_image(self):
        from downloader.steam_runtime import start_with_vpn
        network=Mock();network.container='vpn'
        with patch('downloader.steam_runtime.subprocess.run',return_value=Mock(returncode=1)),patch('downloader.steam_runtime.SteamRuntime') as runtime:
            start_with_vpn(network)
        runtime.assert_not_called()

    def test_downloader_status_reports_process_and_moon_without_claiming_login(self):
        from downloader.download_dialog import DownloadDialog
        self.assertEqual(DownloadDialog.steam_status_text({'steam_running':False}),'Steam: stopped')
        self.assertEqual(DownloadDialog.steam_status_text({'steam_running':True,'moon_installed':True}),'Steam: running · LuaMoon: installed')
        self.assertIn('LuaMoon: installing',DownloadDialog.steam_status_text({'steam_running':True,'moon_installing':True}))

    def test_downloader_does_not_probe_steam_before_vpn_connection_finishes(self):
        from PyQt6.QtWidgets import QApplication
        from downloader.download_dialog import DownloadDialog
        app=QApplication.instance() or QApplication([])
        dialog=DownloadDialog(Mock());dialog.auto_started=True;dialog.vpn_connecting=True
        with patch('downloader.steam_runtime.SteamRuntime') as runtime:
            dialog.show();app.processEvents();dialog.refresh_steam_status()
            runtime.assert_not_called()
            self.assertIn('waiting for the VPN',dialog.steam_status.text())
        dialog.hide();dialog.deleteLater()

    def test_delayed_status_response_cannot_report_unavailable_during_connect(self):
        from PyQt6.QtWidgets import QApplication
        from downloader.download_dialog import DownloadDialog
        app=QApplication.instance() or QApplication([])
        dialog=DownloadDialog(Mock());dialog.auto_started=True;dialog.vpn_connecting=True
        dialog.show();app.processEvents()
        dialog.steam_status_finished('Steam: unavailable')
        self.assertIn('waiting for the VPN',dialog.steam_status.text())
        self.assertNotIn('unavailable',dialog.log.toPlainText())
        dialog.hide();dialog.deleteLater()
