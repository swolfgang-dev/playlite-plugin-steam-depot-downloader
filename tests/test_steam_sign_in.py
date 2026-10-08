import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
import test_worker
from PyQt6.QtWidgets import QApplication
from downloader.steam_sign_in_dialog import SteamSignInDialog

class SteamSignInTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def test_saved_login_recognizes_modern_and_legacy_steam_formats(self):
        path=Path(__file__).resolve().parents[1]/'tools/steam/bridge.py'
        spec=importlib.util.spec_from_file_location('sign_in_bridge',path)
        bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)
        with tempfile.TemporaryDirectory() as folder:
            home=Path(folder);config=home/'.steam/steam/config/loginusers.vdf';config.parent.mkdir(parents=True)
            for key in ('RememberPassword','AutoLogin','AllowAutoLogin'):
                config.write_text('"users" { "76561198000000000" { "'+key+'" "1" } }')
                self.assertTrue(bridge.has_saved_login(home),key)
            config.write_text('"users" { "76561198000000000" { "RememberPassword" "0" "AutoLogin" "0" } }')
            self.assertFalse(bridge.has_saved_login(home))
    def test_success_closes_only_owned_viewer_and_returns_to_setup(self):
        network=Mock();dialog=SteamSignInDialog(network)
        viewer=Mock();viewer.poll.return_value=None;dialog.viewer=viewer
        with patch('downloader.plugin.Job') as job,patch('downloader.steam_sign_in_dialog.QThreadPool'),patch.object(dialog,'accept',wraps=dialog.accept) as accept:
            dialog.refresh()
            callback=job.return_value.signals.finished.connect.call_args.args[0]
            callback('{"steam_running":true,"steam_session_saved":true}')
            accept.assert_called_once();viewer.terminate.assert_called_once()
            network.disconnect.assert_not_called();network.close_session.assert_not_called()
            self.assertFalse(dialog.timer.isActive())
        dialog.close()
    def test_start_connects_vm_then_launches_owned_native_viewer(self):
        network=Mock();network.agent.rpc.return_value={'steam_ready':True,'moon_installed':True,'steam_session_saved':False}
        dialog=SteamSignInDialog(network)
        with patch('downloader.plugin.Job') as job,patch('downloader.steam_sign_in_dialog.QThreadPool'),patch('downloader.steam_vm_viewer.open_viewer') as viewer:
            dialog.start_steam();job.call_args.args[0]()
            network.connect.assert_called_once();viewer.assert_called_once()
            self.assertTrue(any(call.args[0]=={'command':'start'} for call in network.agent.rpc.call_args_list))
        dialog.close()
