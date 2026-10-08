import os
import unittest
from unittest.mock import patch
import test_worker
from downloader.desktop_links import open_account_link

class DesktopLinkTests(unittest.TestCase):
    def test_repo_links_use_regular_desktop_profile(self):
        with patch.dict(os.environ,{'PLAYLITE_PROFILE':'repo','HOME':'/isolated','PLAYLITE_HOST_HOME':'/regular','XDG_CONFIG_HOME':'/isolated/config','PLAYLITE_HOST_XDG_CONFIG_HOME':'/regular/config','XDG_DATA_HOME':'/isolated/data'},clear=True),patch('downloader.desktop_links.subprocess.Popen') as launch:
            open_account_link('https://my.nordaccount.com/')
            self.assertEqual(launch.call_args.args[0],['xdg-open','https://my.nordaccount.com/'])
            env=launch.call_args.kwargs['env']
            self.assertEqual(env['HOME'],'/regular')
            self.assertEqual(env['XDG_CONFIG_HOME'],'/regular/config')
            self.assertNotIn('XDG_DATA_HOME',env)
    def test_non_web_links_are_rejected(self):
        with patch('downloader.desktop_links.subprocess.Popen') as launch:
            with self.assertRaises(ValueError):open_account_link('file:///tmp/test')
            launch.assert_not_called()
