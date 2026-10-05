import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import tempfile
spec = importlib.util.spec_from_file_location('downloader', Path(__file__).resolve().parents[1] / '__init__.py', submodule_search_locations=[str(Path(__file__).resolve().parents[1])])
module = importlib.util.module_from_spec(spec)
sys.modules['downloader'] = module
spec.loader.exec_module(module)
from downloader.network import Network, IMAGE, LABEL, GUARD
from downloader.settings import Preferences
from downloader.credentials import validate_credentials

class Tests(unittest.TestCase):
    def test_check_without_session_reports_reconnect_before_docker(self):
        network = Network()
        with patch.object(network, "docker") as docker:
            with self.assertRaisesRegex(RuntimeError, "Connect the VPN from this settings window"):
                network.check()
            docker.assert_not_called()

    def test_preferences_reject_multiple_countries_and_invalid_protocol(self):
        for preferences in [Preferences('Canada,Germany'), Preferences('', 'wireguard')]:
            with self.assertRaises(ValueError): preferences.validate()
    def test_credentials_reject_secret_file_injection(self):
        with self.assertRaises(ValueError): validate_credentials('user', 'password\nother')
    def test_worker_has_no_privileges_or_host_network(self):
        network = Network()
        network.container = 'exact-container-id'
        with tempfile.TemporaryDirectory() as directory:
            network.directory = directory
            args = network.probe_args('true')
            self.assertIn('container:exact-container-id', args)
            self.assertIn('65534:65534', args)
            self.assertIn('--read-only', args)
            self.assertIn('no-new-privileges', args)
            self.assertNotIn('--privileged', args)
    def test_foreign_container_rejected(self):
        network = Network()
        with patch.object(network, 'docker', return_value='[{"Config":{"Labels":{}}}]'):
            with self.assertRaises(RuntimeError): network.inspect()
    def test_unhealthy_vpn_never_runs_worker(self):
        network = Network()
        with patch.object(network, 'inspect', return_value={'State': {'Running':True, 'Health': {'Status':'unhealthy'}}}), patch.object(network, 'docker') as docker:
            with self.assertRaises(RuntimeError): network.check()
            docker.assert_not_called()
    def test_reconnect_refuses_an_unsafe_previous_secret_mount(self):
        network = Network()
        data = {'Mounts': [{'Destination': '/run/secrets', 'Source': '/home', 'RW': True}]}
        with patch.object(network, 'docker', return_value='old-id') as docker, patch.object(network, 'inspect', return_value=data):
            with self.assertRaisesRegex(RuntimeError, 'unsafe credential mount'):
                network.connect(Preferences(), 'user', 'password')
            self.assertFalse(any(call.args[0] == 'rm' for call in docker.call_args_list))
        self.assertIsNone(network.container)

    def test_disconnect_removes_workers_before_gateway(self):
        network = Network()
        network.container = 'gateway-id'
        def docker(*args, **kwargs):
            return 'worker-id' if args[0] == 'ps' else ''
        with patch.object(network, 'docker', side_effect=docker) as calls, patch.object(network, 'inspect'):
            network.disconnect()
        removed = [call.args for call in calls.call_args_list if call.args[0] == 'rm']
        self.assertEqual(removed, [('rm', '-f', 'worker-id'), ('rm', '-f', 'gateway-id')])

    def test_guard_failure_removes_container_and_secrets(self):
        network = Network()
        calls = []
        def docker(*args, **kwargs):
            calls.append(args)
            if args[0] == 'ps': return ''
            if args[0] == 'run': return 'container-id'
            if args[0] == 'exec': raise RuntimeError('guard failed')
            return ''
        with patch.object(network, 'docker', side_effect=docker), patch.object(network, 'inspect'):
            with self.assertRaises(RuntimeError): network.connect(Preferences(), 'secretuser', 'secretpassword')
        self.assertIsNone(network.directory)
        self.assertIsNone(network.container)
        self.assertIn(('rm', '-f', 'container-id'), calls)
        self.assertNotIn('secretpassword', repr(calls))
        self.assertNotIn('secretuser', repr(calls))

class ConnectionTests(unittest.TestCase):
    def run_failed_connect(self, logs='', progress=lambda network, text: None):
        network = Network()
        calls = []
        def docker(*args, **kwargs):
            calls.append(args)
            if args[0] == 'ps': return ''
            if args[0] == 'run': return 'container-id'
            if args[0] == 'logs': return logs
            return ''
        state = {'State': {'Running': True, 'Health': {'Status': 'unhealthy'}}}
        with patch.object(network, 'docker', side_effect=docker), patch.object(network, 'inspect', return_value=state), patch.object(network, 'start_guardian'), patch.object(network.cancelled, 'wait'):
            with self.assertRaises(RuntimeError) as error:
                network.connect(Preferences(), 'test-service-user', 'test-service-password', deadline=5,
                                progress=lambda text: progress(network, text))
        self.assertIsNone(network.container)
        self.assertIsNone(network.directory)
        self.assertIn(('rm', '-f', 'container-id'), calls)
        return str(error.exception), calls

    def test_authentication_rejection_reported_without_timeout_or_log_secrets(self):
        error, calls = self.run_failed_connect('AUTH: Received control message: AUTH_FAILED\nsecret diagnostic detail')
        self.assertIn('NordVPN failed all 5 connection attempts', error)
        self.assertNotIn('secret diagnostic detail', error)
        self.assertEqual(sum(args[0] == 'logs' for args in calls), 5)

    def test_cancel_interrupts_connection_and_cleans_up(self):
        error, calls = self.run_failed_connect(progress=lambda network, text: network.cancel() if text.startswith('Connecting') else None)
        self.assertIn('cancelled', error)

    def test_stale_health_waits_for_route_and_isolation_readiness(self):
        network = Network()
        def docker(*args, **kwargs):
            if args[0] == 'ps': return ''
            if args[0] == 'run': return 'container-id'
            return ''
        state = {'State': {'Running': True, 'Health': {'Status': 'healthy'}}}
        progress = []
        with patch.object(network, 'docker', side_effect=docker), patch.object(network, 'inspect', return_value=state), patch.object(network, 'start_guardian'), patch.object(network, 'check', side_effect=[RuntimeError('route not ready'), 'VPN ready']) as check, patch.object(network.cancelled, 'wait'):
            self.assertEqual(network.connect(Preferences(), 'test-user', 'test-pass', deadline=5, progress=progress.append), 'VPN ready')
            self.assertEqual(check.call_count, 2)
            self.assertTrue(any('Waiting for the tunnel route' in text for text in progress))
            network.disconnect()

    def test_kernel_normalized_firewall_rules_are_accepted(self):
        network = Network()
        network.container = 'exact-id'
        network.directory = '/test-session'  # DNS/probe creation is mocked below.
        outputs = [
            '-P OUTPUT DROP\n-A OUTPUT -m owner --uid-owner 65534 -j PLAYLITE_WORKER',
            '1',
            '-N PLAYLITE_WORKER\n-A PLAYLITE_WORKER -o tun0 -j RETURN\n-A PLAYLITE_WORKER -j REJECT --reject-with icmp-port-unreachable',
            '1.1.1.1 dev tun0 src 10.1.0.1',
            '203.0.113.5',
        ]
        state = {'State': {'Running': True, 'Health': {'Status': 'healthy'}}}
        with patch.object(network, 'inspect', return_value=state), patch.object(network, 'docker', side_effect=outputs), patch.object(network, 'probe_args', return_value=['probe']):
            self.assertIn('203.0.113.5', network.check())

if __name__ == '__main__': unittest.main()

class RetryTests(unittest.TestCase):
    def test_first_rejection_retries_and_returns_verified_connection(self):
        from downloader.network import AuthenticationRejected
        network=Network()
        with patch.object(network, '_connect_once', side_effect=[AuthenticationRejected('rejected'), 'VPN verified']) as connect, patch.object(network.cancelled, 'wait'):
            self.assertEqual(network.connect(Preferences(), 'user', 'password'), 'VPN verified')
            self.assertEqual(connect.call_count,2)

    def test_repeated_rejection_is_bounded(self):
        from downloader.network import AuthenticationRejected
        network=Network()
        with patch.object(network, '_connect_once', side_effect=AuthenticationRejected('rejected')) as connect, patch.object(network.cancelled, 'wait') as wait:
            with self.assertRaisesRegex(RuntimeError, 'can be temporary'):
                network.connect(Preferences(), 'user', 'password')
            self.assertEqual(connect.call_count,5)
            self.assertEqual([call.args[0] for call in wait.call_args_list],[5,10,20,30])

    def test_cancellation_during_retry_never_starts_second_attempt(self):
        from downloader.network import AuthenticationRejected
        network=Network()
        with patch.object(network, '_connect_once', side_effect=AuthenticationRejected('rejected')) as connect, patch.object(network.cancelled, 'wait', side_effect=lambda _: network.cancel()):
            with self.assertRaisesRegex(RuntimeError, 'cancelled'):
                network.connect(Preferences(), 'user', 'password')
            self.assertEqual(connect.call_count,1)
