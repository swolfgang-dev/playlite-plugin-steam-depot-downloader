import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
import test_worker
from PyQt6.QtWidgets import QApplication,QWidget,QMessageBox
from downloader.installed_games_dialog import InstalledGamesDialog,queued_app

class InstalledGamesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def bridge(self):
        spec=importlib.util.spec_from_file_location('installed_games_bridge',Path(__file__).resolve().parents[1]/'tools/steam/bridge.py')
        bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge);return bridge

    def test_native_list_includes_only_private_libraries(self):
        bridge=self.bridge()
        with tempfile.TemporaryDirectory() as directory:
            bridge.HOME=Path(directory)/'home';bridge.LIBRARY=Path(directory)/'library'
            apps=bridge.LIBRARY/'steamapps';apps.mkdir(parents=True)
            (apps/'appmanifest_10.acf').write_text('"AppState" { "StateFlags" "4" }')
            (apps/'appmanifest_20.acf').write_text('"AppState" { "StateFlags" "1042" }')
            folders=[{'path':str(bridge.LIBRARY),'mounted':True,'apps':[{'appid':10,'name':'Game','size':50},{'appid':20,'name':'Downloading','size':0,'staged':25}]},{'path':'/host/Steam','mounted':True,'apps':[{'appid':99,'name':'Host game'}]}]
            with patch.object(bridge,'steam_action',return_value={'folders':folders}):result=bridge.installed_games()['games']
            self.assertEqual({row['appid'] for row in result},{10,20})
            self.assertEqual(next(row for row in result if row['appid']==20)['status'],'Downloading')

    def test_backend_rejects_absent_or_active_game_before_native_uninstall(self):
        bridge=self.bridge()
        for rows in ([],[{'appid':10,'status':'Downloading'}]):
            with patch.object(bridge,'installed_games',return_value={'games':rows}),patch.object(bridge,'steam_action') as action:
                with self.assertRaises(RuntimeError):bridge.dispatch({'command':'uninstall','appid':10})
                action.assert_not_called()

    def test_confirmation_decline_never_requests_uninstall(self):
        network=Mock();network.download_queue=None;runtime=Mock();window=QWidget();window.network=network
        with patch('downloader.installed_games_dialog.SteamRuntime',return_value=runtime):dialog=InstalledGamesDialog(network,window)
        dialog.populate({'games':[{'appid':10,'name':'Game','size':50,'status':'Installed','library':'/library'}]})
        dialog.table.selectRow(0)
        with patch('downloader.installed_games_dialog.QMessageBox.question',return_value=QMessageBox.StandardButton.No):dialog.uninstall()
        runtime.request.assert_not_called();dialog.close();window.close()

    def test_accepted_uninstall_waits_for_native_list_removal(self):
        network=Mock();network.download_queue=None;runtime=Mock();window=QWidget();window.network=network
        with patch('downloader.installed_games_dialog.SteamRuntime',return_value=runtime):dialog=InstalledGamesDialog(network,window)
        dialog.populate({'games':[{'appid':10,'name':'Game','size':50,'status':'Installed','library':'/library'}]});dialog.table.selectRow(0)
        runtime.request.side_effect=[{'requested':True},{'games':[]}]
        with patch('downloader.installed_games_dialog.QMessageBox.question',return_value=QMessageBox.StandardButton.Yes),patch('downloader.installed_games_dialog.QThreadPool') as pool:
            pool.globalInstance.return_value.start.side_effect=lambda job:job.run()
            dialog.uninstall()
        runtime.request.assert_any_call('uninstall',10)
        self.assertEqual(dialog.games,[]);self.assertIn('was uninstalled',dialog.status.text())
        dialog.close();window.close()

    def test_queued_item_without_controller_is_protected(self):
        network=Mock();network.download_queue=Mock();row=Mock();row.state='Queued';row.steam_appid=10;row.controller=None
        network.download_queue.entries=[row]
        self.assertTrue(queued_app(network,10));self.assertFalse(queued_app(network,20))
