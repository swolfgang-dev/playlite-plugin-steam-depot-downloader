import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

class BootstrapTests(unittest.TestCase):
    def setUp(self):
        path=Path(__file__).resolve().parents[1]/'tools/steam/bootstrap_steam.py'
        spec=importlib.util.spec_from_file_location('bootstrap',path)
        self.bootstrap=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.bootstrap)
    def test_checksum_failure_does_not_write_bootstrap(self):
        with tempfile.TemporaryDirectory() as home,patch.object(self.bootstrap.urllib.request,'urlopen') as download:
            download.return_value.__enter__.return_value.read.return_value=b'untrusted'
            with self.assertRaisesRegex(RuntimeError,'checksum'):self.bootstrap.install(home)
            self.assertFalse((Path(home)/'.steam').exists())
    def test_complete_bootstrap_is_retained_without_redownload(self):
        with tempfile.TemporaryDirectory() as home:
            root=Path(home)/'.steam/debian-installation'
            for name in ('steam.sh','ubuntu12_32/steam','ubuntu12_32/steam-runtime/run.sh','ubuntu12_32/steam-runtime/setup.sh'):
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('existing')
            with patch.object(self.bootstrap.urllib.request,'urlopen') as download:
                self.bootstrap.install(home);download.assert_not_called()
