import importlib.util
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('resource_cleanup', Path(__file__).resolve().parents[1] / 'uninstall_resources.py')
cleanup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleanup)


class ResourceCleanupTests(unittest.TestCase):
    def test_only_receipted_private_folders_are_removed(self):
        with TemporaryDirectory() as temporary, patch.object(cleanup.shutil, 'which', return_value=None):
            data = Path(temporary)
            root = data / 'steam-runtime'; root.mkdir()
            owned = root / 'library'; owned.mkdir(); (owned / 'game').write_text('private')
            external = root / 'manual'; external.mkdir(); (external / 'file').write_text('keep')
            marker = root / 'environment-owner.json'
            marker.write_text(json.dumps({'owner': str(os.getuid()), 'directories': ['library']}))
            args = SimpleNamespace(purge_data=True, dry_run=True, purge_secrets=False, remove_resource_images=False)
            removed = []; failures = []
            cleanup.cleanup(data, data, args, removed.append, None, None, failures)
            self.assertTrue(owned.exists())
            args.dry_run = False
            cleanup.cleanup(data, data, args, removed.append, None, None, failures)
            self.assertFalse(owned.exists())
            self.assertTrue((external / 'file').exists())
            self.assertEqual(failures, [])

    def test_unowned_volume_and_other_profile_worker_are_kept(self):
        with TemporaryDirectory() as temporary, patch.object(cleanup.shutil, 'which', return_value='docker'):
            args = SimpleNamespace(purge_data=True, dry_run=False, purge_secrets=False, remove_resource_images=False)
            def run(command):
                if command[1:3] == ['ps', '-aq']:
                    text = 'repo-worker' if command[-1].endswith('.gateway') else ''
                elif command[1] == 'inspect':
                    text = json.dumps([{'Config': {'Labels': {cleanup.LABEL + '.gateway': 'other-profile'}}}])
                else:
                    text = json.dumps([{'Labels': {}}])
                return SimpleNamespace(returncode=0, stdout=text)
            actions = []; failures = []
            data = Path(temporary)
            cleanup.cleanup(data, data, args, lambda path: self.fail(str(path)), run, actions.append, failures)
            self.assertEqual(actions, [])
            self.assertEqual(failures, [])
