import tempfile
import unittest
from pathlib import Path
import test_worker
from downloader.download_flow import game_folder,prepare_destination,finalize_download

class DestinationTests(unittest.TestCase):
    def test_named_folder_is_created_and_verified_files_are_promoted(self):
        with tempfile.TemporaryDirectory() as root:
            path=game_folder(root,'Baba Is You')
            destination,staging=prepare_destination(path)
            (staging/'game').write_bytes(b'game bytes')
            (staging/'Data').mkdir();(staging/'Data'/'level').write_bytes(b'level')
            finalize_download(destination,staging)
            self.assertEqual((destination/'game').read_bytes(),b'game bytes')
            self.assertEqual((destination/'Data'/'level').read_bytes(),b'level')
            self.assertFalse(staging.exists())

    def test_existing_game_files_are_never_overwritten(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'game';path.mkdir();(path/'save').write_bytes(b'save')
            with self.assertRaises(ValueError):prepare_destination(path)
            self.assertEqual((path/'save').read_bytes(),b'save')

    def test_file_added_during_download_prevents_promotion(self):
        with tempfile.TemporaryDirectory() as root:
            destination,staging=prepare_destination(Path(root)/'game')
            (staging/'save').write_bytes(b'download');(destination/'save').write_bytes(b'user save')
            with self.assertRaises(ValueError):finalize_download(destination,staging)
            self.assertEqual((staging/'save').read_bytes(),b'download')
            self.assertEqual((destination/'save').read_bytes(),b'user save')

    def test_name_cannot_escape_download_root(self):
        self.assertEqual(game_folder('/downloads','../../Baba: Is You').parent,Path('/downloads'))
