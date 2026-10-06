import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

if 'downloader' not in sys.modules:
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location('downloader', root / '__init__.py', submodule_search_locations=[str(root)])
    module = importlib.util.module_from_spec(spec)
    sys.modules['downloader'] = module
    spec.loader.exec_module(module)
from downloader.network import Network
from downloader.worker import Request, worker_args, require_download_success

class WorkerTests(unittest.TestCase):
    def test_invalid_ids_are_rejected(self):
        for request in [Request(0, 1), Request(True, 1), Request(1, 2**32), Request(1, 2, 2**64)]:
            with self.assertRaises(ValueError): request.arguments()

    def test_unhealthy_gateway_never_constructs_worker(self):
        network = Mock()
        network.check.side_effect = RuntimeError('Unhealthy')
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(RuntimeError): worker_args(network, Request(736260, 736263), directory)
        network.probe_args.assert_not_called()

    def test_worker_is_unprivileged_and_mounts_pack_readonly(self):
        network = Network()
        network.container = 'exact-gateway-id'
        network.check = Mock()
        with tempfile.TemporaryDirectory() as directory:
            network.directory = directory
            output = Path(directory, 'output'); output.mkdir()
            pack = Path(directory, 'pack'); pack.mkdir()
            (pack / 'manifest.bin').write_bytes(b'manifest')
            (pack / 'depot.keys').write_text('private test data')
            args = worker_args(network, Request(736260, 736263, 123), output, pack=pack)
        self.assertIn('container:exact-gateway-id', args)
        self.assertIn('65534:65534', args)
        self.assertIn('--read-only', args)
        self.assertIn('no-new-privileges', args)
        self.assertTrue(any('dst=/input,readonly' in arg for arg in args))
        self.assertNotIn('/bin/sh', args)
        self.assertNotIn('private test data', repr(args))
        self.assertIn('/input/depot.keys', args)
        network.check.assert_called_once()

    def test_zero_exit_after_depot_failure_is_not_success(self):
        with self.assertRaises(RuntimeError):
            require_download_success(0, 'Got depot key result: AccessDenied\nTotal downloaded: 0 bytes from 0 depots')
        with self.assertRaises(RuntimeError):
            require_download_success(0, 'Total downloaded: 0 bytes from 0 depots')
        require_download_success(0, 'Total downloaded: 100 bytes from 1 depots')

if __name__ == '__main__': unittest.main()

class KeyOnlyWorkerTests(unittest.TestCase):
    def test_missing_binary_manifest_is_fetched_by_native_worker(self):
        network=Network();network.container='test-vpn';network.check=Mock()
        with tempfile.TemporaryDirectory() as directory:
            network.directory=directory
            pack=Path(directory)/'pack';pack.mkdir();(pack/'depot.keys').write_text('test data')
            args=worker_args(network,Request(10,11,12),directory,pack=pack,username='account')
        self.assertNotIn('-manifestfile',args)
        self.assertIn('-depotkeys',args)
        self.assertIn('-manifest',args)
        self.assertNotIn('test data',str(args))
