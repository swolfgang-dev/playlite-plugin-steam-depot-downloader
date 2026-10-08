import unittest
from unittest.mock import Mock, patch
import test_worker
from downloader.vm_workshop import WorkshopVM


class WorkshopTests(unittest.TestCase):
    def runtime(self):
        network=Mock(cfg={'domain':'test-vm','shared':'/games','staging':'.steam-vm-123456789abc'})
        return WorkshopVM(network)

    def test_failed_add_does_not_open_workshop(self):
        runtime = self.runtime()
        with patch.object(runtime, 'start'), patch.object(runtime, 'request') as request:
            request.side_effect = [{}, {'exists': False}, {}, {'state': {'status': 'failed', 'error': 'Quota exceeded'}}]
            with self.assertRaisesRegex(RuntimeError, 'Quota exceeded'):
                runtime.prepare(123, lambda _: None)
            self.assertNotIn('workshop', [call.args[0] for call in request.call_args_list])

    def test_cached_game_does_not_request_provider(self):
        runtime = self.runtime()
        with patch.object(runtime, 'start'), patch.object(runtime, 'request') as request:
            request.side_effect = [{}, {'exists': True}, {'opened': True}]
            runtime.prepare(123, lambda _: None)
            self.assertEqual([call.args[0] for call in request.call_args_list],
                             ['authentication_status', 'has_game', 'workshop'])
