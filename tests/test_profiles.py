"""Release and repository sessions must not claim one another's resources."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class ProfileTests(unittest.TestCase):
    def test_repo_resources_are_separate(self):
        tests = Path(__file__).resolve().parent
        code = '''import test_worker
import json
from downloader.constants import LABEL
from downloader.network import Network
from downloader.steam_runtime import SteamRuntime,RUNTIME_IMAGE
from downloader.credentials import FOLDER
network=Network();runtime=SteamRuntime(network)
print(json.dumps([LABEL,network.name,runtime.name,runtime.volume,RUNTIME_IMAGE,FOLDER,str(runtime.root)]))
'''
        with tempfile.TemporaryDirectory() as home:
            values = []
            for profile in ('installed', 'repo'):
                environment = dict(os.environ, PLAYLITE_PROFILE=profile, HOME=home+'/'+profile, PYTHONPATH=str(tests))
                result = subprocess.run([sys.executable, '-c', code], env=environment, capture_output=True, text=True, check=True)
                values.append(json.loads(result.stdout))
            for release, repo in zip(*values):
                self.assertNotEqual(release, repo)
