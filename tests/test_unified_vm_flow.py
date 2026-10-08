"""Fresh setup routing and session lifetime regressions."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch, PropertyMock
import test_worker
from PyQt6.QtWidgets import QApplication, QWidget
from downloader.vm_backend import VMNetwork
from downloader.plugin import Plugin
from downloader.vpn_lifecycle import disconnect_when_idle


class UnifiedVMFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def test_fresh_plugin_defaults_to_vm_and_one_menu_entry(self):
        cached=getattr(self.app,'_playlite_depot_network',None)
        try:
            self.app._playlite_depot_network=None
            with patch.dict(os.environ,{'PLAYLITE_STEAM_BACKEND':'vm'}):
                plugin=Plugin()
            self.assertIsInstance(plugin.network,VMNetwork)
            self.assertEqual([name for name,_ in plugin.main_menu_actions(QWidget())],['Steam Downloader…'])
        finally:self.app._playlite_depot_network=cached

    def test_closing_windows_keeps_vm_session_connected(self):
        network=VMNetwork()
        with patch.object(network,'disconnect') as disconnect:
            disconnect_when_idle(network)
            disconnect_when_idle(network,release_session=True)
        disconnect.assert_not_called()
        self.assertFalse(network.cancelled.is_set())

    def test_signed_in_but_disconnected_vpn_is_connected_before_setup(self):
        from downloader.settings import Preferences
        network=VMNetwork();agent=Mock()
        agent.rpc.side_effect=[RuntimeError('NordVPN disconnected'),{}, {'connected':True,'protected':True}]
        with patch.object(network,'boot'),patch.object(VMNetwork,'agent',new_callable=PropertyMock,return_value=agent),patch.object(VMNetwork,'cfg',new_callable=PropertyMock,return_value={'domain':'test-vm'}):
            network.connect(Preferences('','tcp'))
        self.assertEqual([call.args[0]['command'] for call in agent.rpc.call_args_list],['vpn_check','vpn_connect','vpn_check'])

    def test_downloads_prevent_suspend(self):
        network=VMNetwork()
        network.download_queue=Mock(entries=[Mock(state='Downloading')])
        with patch('downloader.vm_backend.installer_module') as installer:
            self.assertFalse(network.suspend())
        installer.assert_not_called()

    def test_suspend_and_resume_preserve_session_without_disconnecting(self):
        network=VMNetwork()
        installer=Mock();installer.execute.return_value=Mock(stdout='running\n')
        with patch.object(VMNetwork,'cfg',new_callable=PropertyMock,return_value={'domain':'test-vm'}),patch('downloader.vm_backend.installer_module',return_value=installer),patch.object(network,'disconnect') as disconnect:
            self.assertTrue(network.suspend())
            self.assertTrue(network.session_suspended)
            network.resume()
            self.assertFalse(network.session_suspended)
        commands=[call.args[0][3] for call in installer.execute.call_args_list]
        self.assertEqual(commands,['domstate','suspend','resume'])
        disconnect.assert_not_called()

    def test_exit_disconnects_then_shuts_down_without_snapshotting_shared_filesystem(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'vm.json').write_text('{}')
            network=VMNetwork(root);agent=Mock();installer=Mock()
            installer.execute.return_value=Mock(stdout='running\n')
            with patch.object(VMNetwork,'cfg',new_callable=PropertyMock,return_value={'domain':'test-vm'}),patch.object(VMNetwork,'agent',new_callable=PropertyMock,return_value=agent),patch('downloader.vm_backend.installer_module',return_value=installer):
                network.close_session()
            agent.rpc.assert_called_once_with({'command':'disconnect'})
            self.assertEqual(installer.execute.call_args.args[0],['virsh','-c','qemu:///session','shutdown','test-vm'])
            self.assertFalse(any('managedsave' in call.args[0] for call in installer.execute.call_args_list))

    def test_wizard_fresh_profile_offers_folder_and_continuation(self):
        from downloader.steam_runtime_dialog import SteamRuntimeDialog
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'PLAYLITE_STEAM_VM_ROOT':folder,'XDG_STATE_HOME':folder}):
            dialog=SteamRuntimeDialog(VMNetwork())
            self.assertIsNone(dialog.runtime)
            self.assertFalse(dialog.shared_folder.isReadOnly())
            self.assertEqual(dialog.buttons[0].text(),'Start installation')
            self.assertEqual(dialog.buttons[2].text(),'Check sign-in')
            self.assertTrue(dialog.log_path.is_file())
            dialog.close()

    def test_login_poll_continues_setup_after_sign_in(self):
        from downloader.steam_runtime_dialog import SteamRuntimeDialog
        import time
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'PLAYLITE_STEAM_VM_ROOT':folder,'XDG_STATE_HOME':folder}):
            network=VMNetwork();agent=Mock()
            agent.execute.return_value=(0,b'Email: signed in',b'')
            dialog=SteamRuntimeDialog(network);dialog.waiting_login=True
            with patch.object(VMNetwork,'agent',new_callable=PropertyMock,return_value=agent),patch.object(dialog,'install') as install:
                dialog.poll_login()
                deadline=time.monotonic()+3
                while dialog.jobs and time.monotonic()<deadline:
                    self.app.processEvents();time.sleep(.01)
                self.app.processEvents()
                install.assert_called_once()
                self.assertFalse(dialog.waiting_login)
            dialog.close()

    def test_vm_settings_can_open_before_vm_exists(self):
        cached=getattr(self.app,'_playlite_depot_network',None)
        try:
            with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'PLAYLITE_STEAM_VM_ROOT':folder,'PLAYLITE_STEAM_BACKEND':'vm'}):
                self.app._playlite_depot_network=None
                plugin=Plugin();widget=plugin.create_settings()
                self.assertFalse(widget.download_root.isReadOnly())
                self.assertTrue(widget.keep_steam_open.isChecked())
                self.assertTrue(widget.keep_vpn_open.isChecked())
                widget.steam_timer.stop();widget.deleteLater()
        finally:self.app._playlite_depot_network=cached

    def test_opening_unconfigured_downloader_does_not_open_setup_or_connect(self):
        from downloader.download_dialog import DownloadDialog
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'PLAYLITE_STEAM_VM_ROOT':folder}):
            dialog=DownloadDialog(VMNetwork())
            with patch.object(dialog,'open_setup') as setup,patch.object(dialog,'connect_vpn') as connect,patch.object(dialog,'refresh_steam_status'):
                dialog.show();self.app.processEvents()
                setup.assert_not_called();connect.assert_not_called()
                self.assertIn('Settings',dialog.steam_status.text())
                dialog.close()

    def test_install_hook_opens_setup(self):
        cached=getattr(self.app,'_playlite_depot_network',None)
        try:
            with patch('downloader.steam_runtime_dialog.SteamRuntimeDialog') as wizard:
                Plugin().post_install()
                wizard.return_value.exec.assert_called_once()
        finally:self.app._playlite_depot_network=cached

    def test_delete_removes_owned_private_profile_but_preserves_shared_games(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'steam-vm';root.mkdir()
            shared=Path(folder)/'games';shared.mkdir();game=shared/'game.dat';game.write_text('keep')
            (root/'vm.json').write_text('{}');(root/'disk.qcow2').write_text('private')
            network=VMNetwork(root)
            cfg={'domain':'test-vm','service':'test-share','shared':str(shared)}
            installer=Mock()
            def execute(args):
                if 'dumpxml' in args:return Mock(stdout=f'<domain><devices><disk device="disk"><source file="{root}/disk.qcow2"/></disk></devices></domain>')
                if 'show' in args:return Mock(stdout=str(root/'test-share.service'))
                if 'domstate' in args:return Mock(stdout='shut off\n')
                return Mock(stdout='')
            installer.execute.side_effect=execute
            with patch.object(VMNetwork,'cfg',new_callable=PropertyMock,return_value=cfg),patch('downloader.vm_backend.installer_module',return_value=installer),patch.object(network,'close_session'):
                network.delete_vm()
            self.assertFalse(root.exists());self.assertEqual(game.read_text(),'keep')

    def test_delete_rejects_foreign_vm_disk(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'vm.json').write_text('{}')
            network=VMNetwork(root);installer=Mock()
            installer.execute.return_value=Mock(stdout='<domain><devices><disk device="disk"><source file="/foreign/disk.qcow2"/></disk></devices></domain>')
            cfg={'domain':'foreign','service':'test-share','shared':'/games'}
            with patch.object(VMNetwork,'cfg',new_callable=PropertyMock,return_value=cfg),patch('downloader.vm_backend.installer_module',return_value=installer):
                with self.assertRaisesRegex(RuntimeError,'does not belong'):network.delete_vm()
            self.assertTrue((root/'vm.json').is_file())

    def test_ready_screen_keeps_explicit_open_downloader_action(self):
        from downloader.steam_runtime_dialog import SteamRuntimeDialog
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'PLAYLITE_STEAM_VM_ROOT':folder,'XDG_STATE_HOME':folder}):
            dialog=SteamRuntimeDialog(VMNetwork())
            with patch.object(dialog,'accept') as accept:
                dialog.show_ready()
                accept.assert_not_called()
                self.assertEqual(dialog.buttons[0].text(),'Open Downloader')
                self.assertEqual(dialog.stage.text(),'Setup complete')
                dialog.install();accept.assert_called_once()
            dialog.close()
