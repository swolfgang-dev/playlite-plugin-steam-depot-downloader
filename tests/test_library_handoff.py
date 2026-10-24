from contextlib import contextmanager,nullcontext
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
import test_worker
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QWidget,QDialog
from downloader.network import Network
from downloader.download_dialog import DownloadDialog


class LutrisHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.window=QWidget();self.addCleanup(self.window.close)
        self.window.data=self.root;self.window.games=[]
        self.window.settings=QSettings(str(self.root/'ui.ini'),QSettings.Format.IniFormat)
        self.window.focus_added_game=Mock()
        self.game=self.root/'Downloaded';self.game.mkdir()
        self.exe=self.game/'game.exe';self.exe.write_bytes(b'fixture')
        network=Network();network.is_depot=True
        self.dialog=DownloadDialog(network,self.window);self.addCleanup(self.dialog.close)
        self.dialog.selected_app=10;self.dialog.game_name='Downloaded Game'
        self.dialog.appid.setText('10')
        self.dialog.destination.setText(str(self.game));self.dialog.depot.addItem('Windows','windows')

    def test_download_registers_lutris_entry_and_imports_it_without_legacy_plugins(self):
        action={'LutrisDatabase':str(self.root/'pga.db'),'GameId':'1001'}
        reviewed=Mock();reviewed.result_plan=object()
        with patch('playlite.lutris_add_dialog.AddExecutableDialog',return_value=reviewed),patch('playlite.lifecycle.run_dialog',return_value=QDialog.DialogCode.Accepted),patch('playlite.lutris_add.register_executable',return_value=nullcontext(action)) as register,patch('playlite.lutris_library.import_entry',return_value=([{'Id':'new'}],'new')) as imported:
            identity=self.dialog.add_to_library()
        self.assertEqual(identity,'new');register.assert_called_once_with(reviewed.result_plan)
        self.assertEqual(imported.call_args.kwargs['metadata_ids'],{'SteamMetadata':'10'})
        self.window.focus_added_game.assert_called_once_with('new')
        self.assertEqual(self.window.games,[{'Id':'new'}])

    def test_failed_import_rolls_back_registration_and_reports_inline_error(self):
        rolled_back=[]
        @contextmanager
        def registration(plan):
            try:yield {'LutrisDatabase':str(self.root/'pga.db'),'GameId':'1001'}
            except Exception:rolled_back.append(True);raise
        with patch('playlite.lutris_add_dialog.AddExecutableDialog',return_value=Mock(result_plan=object())),patch('playlite.lifecycle.run_dialog',return_value=QDialog.DialogCode.Accepted),patch('playlite.lutris_add.register_executable',side_effect=registration),patch('playlite.lutris_library.import_entry',side_effect=ValueError('library write failed')):
            self.assertIsNone(self.dialog.add_to_library())
        self.assertEqual(rolled_back,[True]);self.assertIn('library write failed',self.dialog.status.text())
        self.assertEqual(self.window.games,[])
