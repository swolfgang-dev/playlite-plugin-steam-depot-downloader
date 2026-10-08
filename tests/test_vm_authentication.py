import unittest
from unittest.mock import Mock,patch
import test_worker
from downloader import vm_setup
from downloader.vm_backend import VMSteamRuntime
from downloader.vm_authentication_dialog import VMAuthenticationDialog

class AuthenticationTests(unittest.TestCase):
    def test_transient_failure_retries_without_resetting_vm(self):
        network=Mock()
        with patch.object(vm_setup,'_provision_vm',side_effect=[RuntimeError('guest agent unavailable'),'ready']) as install,patch.object(vm_setup.time,'sleep'):
            self.assertEqual(vm_setup.provision_vm(network,Mock(),Mock(),{'nord_token':'secret'}),'ready')
            self.assertEqual(install.call_count,2)
            self.assertEqual(install.call_args.args[-1],{'nord_token':'secret'})
    def test_authentication_failure_does_not_retry(self):
        with patch.object(vm_setup,'_provision_vm',side_effect=vm_setup.VMLoginRequired('Sign in')) as install:
            with self.assertRaises(vm_setup.VMLoginRequired):vm_setup.provision_vm(Mock(),Mock(),Mock())
            install.assert_called_once()
    def test_provider_credential_stays_in_private_rpc(self):
        network=Mock(cfg={'shared':'/games','staging':'.steam-vm-123456789abc','domain':'owned'})
        VMSteamRuntime(network).request('provider_login',provider='hubcap',credential='secret')
        self.assertEqual(network.agent.rpc.call_args.args[0]['credential'],'secret')
