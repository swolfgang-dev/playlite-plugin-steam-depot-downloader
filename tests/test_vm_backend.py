import base64
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch, PropertyMock
import test_worker
from downloader.vm_backend import Agent, VMNetwork, VMSteamRuntime, configuration, OWNER
from downloader.steam_runtime import SteamRuntime
from downloader.providers import Transport
from downloader.steam_queue_runner import ensure_vpn


class VMBackendTests(unittest.TestCase):
    def test_plugin_shutdown_uses_vm_session_cleanup(self):
        from downloader.plugin import Plugin
        with tempfile.TemporaryDirectory() as folder:
            network = VMNetwork(folder)
            with patch.object(network, 'close_session_background') as close, patch.object(network, 'disconnect') as disconnect:
                Plugin.shutdown(Mock(network=network))
            close.assert_called_once_with()
            disconnect.assert_not_called()
            self.assertTrue(network.cancelled.is_set())

    def test_closing_settings_keeps_vm_vpn_connected(self):
        from downloader.vpn_lifecycle import disconnect_when_idle
        with tempfile.TemporaryDirectory() as folder:
            network = VMNetwork(folder)
            with patch.object(network, 'disconnect') as disconnect, patch.object(network, 'cancel') as cancel:
                disconnect_when_idle(network)
            disconnect.assert_not_called()
            cancel.assert_not_called()

    def test_session_shutdown_launches_independent_helper(self):
        with tempfile.TemporaryDirectory() as folder:
            network=VMNetwork(folder);(Path(folder)/'vm.json').write_text('{}')
            with patch.object(VMNetwork,'cfg',new_callable=PropertyMock,return_value={'domain':'test-vm'}),patch('downloader.vm_backend.installer_module') as installer,patch('downloader.vm_backend.shutil.which',return_value='/usr/bin/virsh'),patch('downloader.vm_backend.subprocess.Popen') as spawn:
                installer.return_value.host_environment.return_value={}
                network.close_session_background()
            self.assertTrue(spawn.call_args.kwargs['start_new_session'])
            self.assertEqual(spawn.call_args.args[0][-1],'test-vm')
            self.assertEqual((Path(folder)/'shutdown.log').stat().st_mode & 0o777,0o600)


    def test_shared_folder_change_blocks_downloads_before_touching_vm(self):
        network=VMNetwork('/tmp/folder-test')
        with patch.object(VMNetwork,'cfg',new_callable=PropertyMock,return_value={'shared':'/old'}),patch('downloader.vpn_lifecycle.has_downloads',return_value=True),patch('downloader.vm_backend.installer_module') as installer:
            with self.assertRaisesRegex(RuntimeError,'Pause or cancel'):
                network.set_shared_folder('/new')
            installer.assert_not_called()


    def test_connection_recovers_when_vpn_daemon_finishes_starting(self):
        network=VMNetwork('/tmp/vpn-recovery-test')
        agent=Mock()
        agent.rpc.side_effect=[RuntimeError('starting'),RuntimeError('setting unavailable')]
        with patch.object(VMNetwork,'agent',new_callable=PropertyMock,return_value=agent),patch.object(network,'boot'),patch.object(network,'check',return_value='verified') as check,patch.object(network.cancelled,'wait',return_value=False):
            self.assertEqual(network.connect(Mock(country='',protocol='tcp')),'verified')
        check.assert_called_once()


    def test_configuration_rejects_foreign_owner_and_moved_profile(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            cfg={'owner':OWNER,'uid':os.getuid(),'root':folder,'shared':'/games',
                 'domain':'test-vm','service':'test-share','staging':'.steam-vm-123456789abc'}
            (root/'vm.json').write_text(json.dumps(cfg))
            self.assertEqual(configuration(root)['domain'],'test-vm')
            for field,value in [('uid',os.getuid()+1),('owner','foreign'),('root','/moved')]:
                bad={**cfg,field:value};(root/'vm.json').write_text(json.dumps(bad))
                with self.assertRaises(RuntimeError):configuration(root)

    def test_agent_keeps_input_out_of_process_arguments(self):
        agent=Agent({'domain':'test-vm'})
        with patch('downloader.vm_backend.subprocess.run',return_value=Mock(returncode=0,stdout='{"return":{}}')) as run:
            agent.call({'secret':'TOKEN'})
            self.assertNotIn('TOKEN',str(run.call_args.args))
            self.assertIn('TOKEN',run.call_args.kwargs['input'])

    def test_guest_failure_and_truncated_output_are_not_success(self):
        agent=Agent({'domain':'test-vm'})
        with patch.object(agent,'execute',return_value=(1,b'{"ok":false,"error":"VPN disconnected"}',b'')):
            with self.assertRaisesRegex(RuntimeError,'VPN disconnected'):agent.rpc({'command':'add'})
        with patch.object(agent,'call',side_effect=[{'pid':1},{'exited':True,'exitcode':0,'out-truncated':True}]):
            with self.assertRaisesRegex(RuntimeError,'truncated'):agent.execute(['/usr/bin/true'])

    def test_large_reply_uses_only_validated_guest_path_and_removes_spool(self):
        agent=Agent({'domain':'test-vm'});identifier='a'*32
        body=json.dumps({'ok':True,'result':{'body':'x'*50000}}).encode()
        with patch.object(agent,'execute',side_effect=[(0,json.dumps({'reply':identifier}).encode(),b''),(0,b'',b'')]) as execute,patch.object(agent,'call') as call:
            call.side_effect=[99,{'buf-b64':base64.b64encode(body).decode(),'eof':True},{}]
            self.assertEqual(len(agent.rpc({'command':'http'})['body']),50000)
            self.assertTrue(call.call_args_list[0].args[0]['arguments']['path'].endswith(identifier+'.json'))
            self.assertEqual(execute.call_args.args[0][-2:],['--remove-reply',identifier])
        with patch.object(agent,'execute',return_value=(0,b'{"reply":"../../etc/passwd"}',b'')),patch.object(agent,'call') as call:
            with self.assertRaisesRegex(RuntimeError,'response file'):agent.rpc({'command':'http'})
            call.assert_not_called()

    def test_runtime_factory_preserves_selected_content_options_without_docker(self):
        network=Mock(is_vm=True,cfg={'domain':'test-vm','shared':'/games','staging':'.steam-vm-123456789abc'})
        network.agent.rpc.return_value={'requested':True}
        runtime=SteamRuntime(network)
        self.assertIsInstance(runtime,VMSteamRuntime)
        runtime.request('install',123,platform='windows',language='french',dlc=[{'id':124,'enabled':True}],recover_paused=True)
        payload=network.agent.rpc.call_args.args[0]
        self.assertEqual(payload['language'],'french');self.assertEqual(payload['dlc'][0]['id'],124)
        self.assertEqual(runtime.root,Path('/games/.steam-vm-123456789abc'))
        with self.assertRaises(ValueError):runtime.request('install',123,platform='macos')

    def test_direct_path_maps_guest_library_to_host_without_copy_and_rejects_escape(self):
        with tempfile.TemporaryDirectory() as folder:
            shared=Path(folder)/'games';shared.mkdir()
            game=shared/'Steam Folder';game.mkdir()
            network=Mock(cfg={'domain':'test-vm','shared':str(shared),'staging':'.steam-vm-123456789abc'})
            runtime=VMSteamRuntime(network)
            self.assertEqual(runtime.installation_path({'directory':'steamapps/common/Steam Folder'}),game)
            for directory in ('/etc/passwd','steamapps/common/../../private','steamapps/common','steamapps/common/missing'):
                with self.assertRaises(ValueError):runtime.installation_path({'directory':directory})
            outside=Path(folder)/'private';outside.mkdir();(shared/'escape').symlink_to(outside)
            with self.assertRaises(ValueError):runtime.installation_path({'directory':'steamapps/common/escape'})

    def test_vm_connect_never_reads_host_wallet(self):
        network=Mock(is_vm=True,container=None)
        with patch('downloader.credentials.Wallet') as wallet:
            ensure_vpn(network,'Canada','tcp',lambda _:None)
            wallet.assert_not_called()
            self.assertEqual(network.connect.call_args.args[0].country,'Canada')
            self.assertEqual(len(network.connect.call_args.args),1)

    def test_store_transport_uses_vm_and_preserves_rejection(self):
        network=Mock(is_vm=True)
        network.http.return_value={'status':200,'body':base64.b64encode(b'content').decode()}
        with patch('downloader.providers.subprocess.run') as run:
            self.assertEqual(Transport(network).request('https://store.steampowered.com/'),b'content')
            run.assert_not_called()
        network.http.return_value={'status':401,'body':''}
        with self.assertRaisesRegex(RuntimeError,'authentication was rejected'):
            Transport(network).request('https://store.steampowered.com/')


class VMGuestTests(unittest.TestCase):
    def test_nord_setting_already_correct_is_success(self):
        import guest_control
        with patch.object(guest_control,'run',side_effect=[Mock(returncode=1,stdout='Already set'),Mock(returncode=0,stdout='Protocol: TCP\n')]):
            guest_control.set_nord_option('protocol','tcp')
        with patch.object(guest_control,'run',side_effect=[Mock(returncode=1,stdout='Failed'),Mock(returncode=0,stdout='Protocol: UDP\n')]):
            with self.assertRaisesRegex(RuntimeError,'protocol'):
                guest_control.set_nord_option('protocol','tcp')


    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();home=Path(self.tmp.name)
        root=Path(__file__).resolve().parents[1]
        original_read=Path.read_text
        def read(path,*args,**kwargs):
            if str(path)=='/etc/playlite-vm.json':return '{"staging":".steam-vm-123456789abc"}'
            return original_read(path,*args,**kwargs)
        spec=importlib.util.spec_from_file_location('guest_runtime_test',root/'tools/vm/runtime_server.py')
        self.guest=importlib.util.module_from_spec(spec)
        with patch.object(Path,'home',return_value=home),patch.object(Path,'read_text',read),patch.dict(os.environ,{},clear=False),patch.object(sys,'path',sys.path.copy()):
            sys.path.insert(0,str(root/'tools/vm'))
            sys.path.insert(0,str(root/'tools/steam'))
            spec.loader.exec_module(self.guest)
        # The imported shared bridge's environment is isolated by the patch.

    def tearDown(self):self.tmp.cleanup()

    def test_vpn_loss_blocks_install_and_http_but_allows_pause(self):
        guest=self.guest
        with patch.object(guest,'vpn_check',side_effect=RuntimeError('VPN disconnected')),patch.object(guest.bridge,'dispatch') as dispatch:
            for command in ('install','add','retail_selection','http','eula_accept'):
                with self.subTest(command=command),self.assertRaisesRegex(RuntimeError,'VPN disconnected'):
                    guest.dispatch({'command':command,'appid':123})
            dispatch.assert_not_called()
            guest.dispatch({'command':'pause','appid':123})
            dispatch.assert_called_once()

    def test_cleanup_never_uninstalls_manual_shared_library(self):
        guest=self.guest
        with patch.object(guest,'vpn_check'),patch.object(guest.bridge,'installed_games',return_value={'games':[{'appid':123,'library':'/home/ubuntu/.steam/debian-installation'}]}),patch.object(guest.bridge,'dispatch') as dispatch:
            result=guest.dispatch({'command':'finish_export','appid':123})
            self.assertTrue(result['retained']);dispatch.assert_not_called()

    def test_missing_share_and_arbitrary_actions_are_rejected(self):
        guest=self.guest
        with patch.object(guest,'vpn_check'),patch.object(guest.os.path,'ismount',return_value=False),patch.object(guest.bridge,'dispatch') as dispatch:
            with self.assertRaisesRegex(RuntimeError,'Shared games'):guest.dispatch({'command':'install','appid':123})
            with self.assertRaisesRegex(ValueError,'Unsupported'):guest.dispatch({'command':'shell'})
            dispatch.assert_not_called()

    def test_http_rejects_plaintext_and_header_injection(self):
        for request in ({'url':'http://example.com'}, {'url':'https://example.com','headers':{'X-Test':'a\r\nb'}}):
            with self.assertRaises(ValueError):self.guest.http(request)

    def test_nord_token_errors_do_not_expose_token(self):
        token='a'*64
        with patch.object(self.guest,'run',side_effect=RuntimeError('failed '+token)):
            with self.assertRaises(RuntimeError) as error:
                self.guest.dispatch({'command':'nord_login_token','token':token})
        self.assertNotIn(token,str(error.exception))
        with patch.object(self.guest,'run') as run:
            with self.assertRaises(ValueError):self.guest.dispatch({'command':'nord_login_token','token':'invalid\nargument'})
            run.assert_not_called()

    def test_nord_token_login_does_not_require_connected_vpn(self):
        with patch.object(self.guest,'run',return_value=Mock(returncode=0)) as run,patch.object(self.guest,'vpn_check',side_effect=RuntimeError('disconnected')):
            result=self.guest.dispatch({'command':'nord_login_token','token':'a'*64})
        self.assertIn('signed in',result['message'])
        run.assert_called_once_with(['nordvpn','login','--token','a'*64],60)
