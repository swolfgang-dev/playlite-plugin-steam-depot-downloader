import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import test_worker
from PyQt6.QtWidgets import QApplication,QLabel
from downloader.vm_backend import VMNetwork
from downloader.steam_runtime_dialog import SteamRuntimeDialog

class StepWizardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def test_configuration_automatically_chains_install_stages(self):
        with tempfile.TemporaryDirectory() as folder,patch('downloader.vm_backend.enabled',return_value=False),patch('downloader.credentials.VMAuthenticationWallet') as wallet,patch('downloader.vm_wizard_steps.QTimer.singleShot') as schedule:
            wallet.return_value.read.return_value={}
            dialog=SteamRuntimeDialog(VMNetwork(Path(folder)/'private'))
            dialog.shared_folder.setText(str(Path(folder)/'games'))
            dialog.wizard_secret.setText('a'*64)
            schedule.reset_mock()
            dialog.install()
            self.assertEqual(dialog.wizard_step,1)
            wallet.return_value.save.assert_called_once_with({'nord_token':'a'*64})
            self.assertTrue(schedule.called)
            with patch.object(dialog,'task') as task:
                schedule.call_args.args[1]()
                task.assert_called_once()
                task.call_args.args[1]()
                self.assertEqual(dialog.wizard_step,2)
                schedule.call_args.args[1]()
                self.assertEqual(task.call_count,2)
                task.call_args.args[1]()
            self.assertEqual(dialog.wizard_step,3)
            self.assertEqual(dialog.buttons[0].text(),'Sign into Steam…')
            dialog.close()
    def test_final_verification_skips_empty_hubcap_key(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as folder,patch('downloader.vm_backend.enabled',return_value=False),patch('downloader.credentials.VMAuthenticationWallet') as wallet,patch('downloader.vm_backend.VMSteamRuntime') as factory:
            wallet.return_value.read.return_value={}
            runtime=factory.return_value
            runtime.request.side_effect=[{'steam_session_saved':True,'steam_running':True,'moon_installed':True},{'luatools':{'state':'saved_session'}}]
            network=VMNetwork(Path(folder)/'private');network.check=Mock()
            dialog=SteamRuntimeDialog(network);dialog.wizard_step=4
            with patch.object(dialog,'task') as task:
                dialog.install()
                task.call_args.args[0](Mock())
                task.call_args.args[1]()
            self.assertTrue(dialog.ready)
            self.assertEqual(dialog.buttons[0].text(),'Open Downloader')
            self.assertFalse(any(call.args[0]=='provider_login' for call in runtime.request.call_args_list))
            dialog.close()

    def test_base_vm_defers_nordvpn_and_steam_packages(self):
        root=Path(__file__).resolve().parents[1]
        recipe=(root/'tools/vm/provision-guest.sh').read_text()
        self.assertNotIn('steam-installer',recipe)
        self.assertNotIn('downloads.nordcdn.com',recipe)
        self.assertIn('setup-complete',recipe)
        self.assertIn('downloads.nordcdn.com',(root/'tools/vm/install-nordvpn.sh').read_text())

    def test_expanded_log_scrolls_without_overlapping_footer(self):
        from PyQt6.QtCore import QPoint
        with tempfile.TemporaryDirectory() as folder,patch('downloader.vm_backend.enabled',return_value=False),patch('downloader.credentials.VMAuthenticationWallet') as wallet:
            wallet.return_value.read.return_value={}
            dialog=SteamRuntimeDialog(VMNetwork(Path(folder)/'private'))
            dialog.wizard_step=4;dialog.render_wizard_step();dialog.details_toggle.setChecked(True)
            dialog.resize(800,560);dialog.show();self.app.processEvents()
            self.assertGreater(dialog.form_scroll.verticalScrollBar().maximum(),0)
            scroll_bottom=dialog.form_scroll.mapTo(dialog,QPoint(0,dialog.form_scroll.height())).y()
            for button in dialog.buttons[:2]:
                if not button.isVisible():continue
                self.assertGreaterEqual(button.mapTo(dialog,QPoint()).y(),scroll_bottom)
            labels=dialog.form_scroll.findChildren(QLabel)
            hubcap_label=next(label for label in labels if label.text().startswith('Hubcap Manifest API key'))
            self.assertLessEqual(dialog.wizard_moon.geometry().bottom(),hubcap_label.geometry().top())
            dialog.close()

    def test_steam_sign_in_advances_to_provider_step(self):
        with tempfile.TemporaryDirectory() as folder,patch('downloader.vm_backend.enabled',return_value=False),patch('downloader.credentials.VMAuthenticationWallet') as wallet,patch('downloader.steam_sign_in_dialog.SteamSignInDialog') as login:
            wallet.return_value.read.return_value={};login.return_value.exec.return_value=1
            dialog=SteamRuntimeDialog(VMNetwork(Path(folder)/'private'))
            dialog.wizard_step=3;dialog.render_wizard_step();dialog.install()
            self.assertEqual(dialog.wizard_step,4)
            self.assertEqual(dialog.buttons[0].text(),'Verify and finish')
            dialog.close()

    def test_every_step_uses_the_available_form_width(self):
        with tempfile.TemporaryDirectory() as folder,patch('downloader.vm_backend.enabled',return_value=False),patch('downloader.credentials.VMAuthenticationWallet') as wallet:
            wallet.return_value.read.return_value={}
            dialog=SteamRuntimeDialog(VMNetwork(Path(folder)/'private'));dialog.show()
            for step in range(5):
                dialog.wizard_step=step;dialog.render_wizard_step();self.app.processEvents();self.app.processEvents()
                viewport=dialog.form_scroll.viewport().width()
                self.assertGreaterEqual(dialog.form_scroll.widget().width(),viewport-2)
                self.assertGreater(dialog.stage.width(),viewport*0.8)
            dialog.close()
