import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import test_worker
from PyQt6.QtWidgets import QApplication
from downloader.downloader_log import DownloaderLog,redact

class LogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def test_authentication_secrets_are_redacted(self):
        message=redact('password=private api_key=key123 Authorization: Bearer secret123 https://example.test/?token=hidden&appid=10')
        for secret in ('private','key123','secret123','hidden'):self.assertNotIn(secret,message)
        self.assertIn('appid=10',message)

    def test_timestamped_log_is_persisted_with_private_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'log';log=DownloaderLog(path=path)
            log.appendPlainText('Steam verification complete')
            self.assertIn('Steam verification complete',path.read_text())
            self.assertTrue(log.toPlainText().startswith('['))
            self.assertEqual(path.stat().st_mode&0o777,0o600)
            log.deleteLater()

    def test_repetitive_connection_updates_are_throttled(self):
        with tempfile.TemporaryDirectory() as directory:
            log=DownloaderLog(path=Path(directory)/'log')
            with patch('downloader.downloader_log.time.monotonic',side_effect=[100,101,111]):
                for seconds in (0,1,11):log.appendPlainText(f'Connecting to NordVPN ({seconds}s / 90s)…')
            self.assertEqual(log.document().blockCount(),2)
            log.deleteLater()
