import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

if 'downloader' not in sys.modules:
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location('downloader', root / '__init__.py', submodule_search_locations=[str(root)])
    module = importlib.util.module_from_spec(spec)
    sys.modules['downloader'] = module
    spec.loader.exec_module(module)
from downloader import guardian
from downloader.network import Network, IMAGE, LABEL, WORKER_LABEL

class GuardianTests(unittest.TestCase):
    def test_parent_token_distinguishes_missing_process(self):
        self.assertIsNotNone(guardian.process_token(os.getpid()))
        self.assertIsNone(guardian.process_token(999999999))

    def test_symlink_and_non_private_directories_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix='playlite-vpn-') as directory:
            self.assertTrue(guardian.safe_directory(directory))
            link = directory + '-link'
            try:
                Path(link).symlink_to(directory)
                self.assertFalse(guardian.safe_directory(link))
            finally:
                Path(link).unlink()
            Path(directory).chmod(0o755)
            self.assertFalse(guardian.safe_directory(directory))

    def test_foreign_container_prevents_directory_deletion(self):
        with tempfile.TemporaryDirectory(prefix='playlite-vpn-') as directory:
            data = [{'Id': 'id', 'Config': {'Labels': {}, 'Image': IMAGE}}]
            response = subprocess.CompletedProcess([], 0, json.dumps(data), '')
            with patch.object(guardian, 'docker', return_value=response) as docker:
                self.assertFalse(guardian.cleanup('id', directory))
            self.assertTrue(Path(directory).exists())
            docker.assert_called_once_with('inspect', 'id')

    def test_cleanup_removes_workers_before_gateway_and_secrets(self):
        directory = tempfile.mkdtemp(prefix='playlite-vpn-')
        data = [{'Id': 'id', 'Config': {'Labels': {LABEL: str(os.getuid())}, 'Image': IMAGE}}]
        responses = [subprocess.CompletedProcess([], 0, json.dumps(data), ''),
                     subprocess.CompletedProcess([], 0, 'worker-id', ''),
                     subprocess.CompletedProcess([], 0, '', ''),
                     subprocess.CompletedProcess([], 0, '', '')]
        with patch.object(guardian, 'docker', side_effect=responses) as docker:
            self.assertTrue(guardian.cleanup('id', directory))
        self.assertEqual(docker.call_args_list[-2].args, ('rm', '-f', 'worker-id'))
        self.assertEqual(docker.call_args_list[-1].args, ('rm', '-f', 'id'))
        self.assertFalse(Path(directory).exists())

    def test_unreachable_daemon_retains_private_files_for_retry(self):
        with tempfile.TemporaryDirectory(prefix='playlite-vpn-') as directory:
            with patch.object(guardian, 'docker', return_value=subprocess.CompletedProcess([], 1, '', 'unreachable')):
                self.assertFalse(guardian.cleanup('id', directory))
            self.assertTrue(Path(directory).exists())

if __name__ == '__main__': unittest.main()
