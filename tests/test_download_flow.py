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

class FuseFinalizationTests(unittest.TestCase):
    def unsupported(self):
        from unittest.mock import patch
        import errno
        return patch('downloader.download_flow.atomic_rename_without_overwrite',side_effect=OSError(errno.EINVAL,'Unsupported rename flags'))

    def test_fuse_fallback_promotes_directories_files_and_links(self):
        with tempfile.TemporaryDirectory() as root,self.unsupported():
            destination,staging=prepare_destination(Path(root)/'game')
            (staging/'Data').mkdir();(staging/'Data'/'file').write_bytes(b'validated')
            (staging/'game').write_bytes(b'executable');(staging/'game').chmod(0o755)
            (staging/'link').symlink_to('Data/file')
            finalize_download(destination,staging)
            self.assertEqual((destination/'Data'/'file').read_bytes(),b'validated')
            self.assertEqual((destination/'game').stat().st_mode & 0o777,0o755)
            self.assertEqual((destination/'link').readlink(),Path('Data/file'))
            self.assertFalse(staging.exists())

    def test_fallback_will_not_overwrite_a_concurrently_created_target(self):
        from downloader.download_flow import rename_without_overwrite
        with tempfile.TemporaryDirectory() as root,self.unsupported():
            source=Path(root)/'source';target=Path(root)/'target'
            source.write_bytes(b'download');target.write_bytes(b'user data')
            with self.assertRaises(FileExistsError):rename_without_overwrite(source,target)
            self.assertEqual(source.read_bytes(),b'download');self.assertEqual(target.read_bytes(),b'user data')

    def test_copy_fallback_verifies_and_preserves_permissions(self):
        import errno
        from unittest.mock import patch
        from downloader.download_flow import rename_without_overwrite
        with tempfile.TemporaryDirectory() as root,self.unsupported(),patch('downloader.download_flow.os.link',side_effect=OSError(errno.EXDEV,'Different branches')):
            source=Path(root)/'source';target=Path(root)/'target'
            source.write_bytes(b'game bytes');source.chmod(0o755)
            rename_without_overwrite(source,target)
            self.assertEqual(target.read_bytes(),b'game bytes');self.assertFalse(source.exists())
            self.assertEqual(target.stat().st_mode & 0o777,0o755)

    def test_failed_directory_move_rolls_back_completed_children(self):
        from unittest.mock import patch
        from downloader import download_flow
        original=download_flow.rename_without_overwrite
        with tempfile.TemporaryDirectory() as root,self.unsupported():
            source=Path(root)/'source';source.mkdir();target=Path(root)/'target'
            (source/'one').write_bytes(b'one');(source/'two').write_bytes(b'two')
            moved=[]
            def move(start,end):
                if Path(start).parent==source:
                    if moved:raise OSError('Simulated filesystem failure')
                    moved.append(start)
                return original(start,end)
            with patch('downloader.download_flow.rename_without_overwrite',side_effect=move):
                with self.assertRaises(OSError):original(source,target)
            self.assertEqual((source/'one').read_bytes(),b'one');self.assertEqual((source/'two').read_bytes(),b'two')
            self.assertFalse(target.exists())

    def test_failed_copy_verification_retains_original_and_removes_copy(self):
        from unittest.mock import patch,Mock
        from downloader.download_flow import copy_file_exclusive
        with tempfile.TemporaryDirectory() as root:
            source=Path(root)/'source';target=Path(root)/'target';source.write_bytes(b'original')
            expected=Mock();expected.digest.return_value=b'expected'
            actual=Mock();actual.digest.return_value=b'corrupt'
            with patch('downloader.download_flow.hashlib.sha256',side_effect=[expected,actual]):
                with self.assertRaisesRegex(OSError,'verification'):copy_file_exclusive(source,target)
            self.assertEqual(source.read_bytes(),b'original');self.assertFalse(target.exists())

class PrefixSuggestionTests(unittest.TestCase):
    def test_download_outside_installation_root_uses_configured_prefix_parent(self):
        from downloader.download_flow import suggested_wine_prefix
        self.assertEqual(suggested_wine_prefix('/downloads/West of Loathing','/wine-prefixes'),Path('/wine-prefixes/west-of-loathing'))

    def test_camel_case_name_is_readable_and_suggestion_creates_nothing(self):
        from downloader.download_flow import suggested_wine_prefix
        with tempfile.TemporaryDirectory() as root:
            prefix=suggested_wine_prefix('/downloads/AnotherGame',root)
            self.assertEqual(prefix.name,'another-game')
            self.assertFalse(prefix.exists())
