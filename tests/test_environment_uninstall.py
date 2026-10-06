import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
import test_worker
from downloader.constants import LABEL,WORKER_LABEL,OWNER_LABEL,OWNER
from downloader.environment_uninstall import remove_environment


class EnvironmentRemovalTests(unittest.TestCase):
    def test_only_owned_containers_images_and_volumes_are_removed(self):
        calls=[]
        network=Mock();network.name='gateway-name';network.container='gateway';
        with tempfile.TemporaryDirectory() as directory:
            runtime=Mock(root=Path(directory),volume='owned-volume')
            def run(arguments,**kwargs):
                calls.append(arguments)
                if arguments[1:3]==['ps','-aq']:
                    text='gateway' if '--filter' in arguments and 'label='+LABEL+'='+str(os.getuid()) in arguments else 'worker other-worker'
                    return Mock(returncode=0,stdout=text)
                if arguments[1]=='inspect':
                    identity=arguments[2]
                    labels={LABEL:str(os.getuid())} if identity=='gateway' else {WORKER_LABEL:'gateway' if identity=='worker' else 'foreign'}
                    return Mock(returncode=0,stdout=json.dumps([{'Id':identity,'Config':{'Labels':labels}}]))
                if arguments[2]=='inspect':
                    labels={OWNER_LABEL:OWNER}
                    return Mock(returncode=0,stdout=json.dumps([{'Config':{'Labels':labels},'Labels':labels}]))
                return Mock(returncode=0,stdout='')
            with patch('downloader.environment_uninstall.SteamRuntime',return_value=runtime),patch('downloader.environment_uninstall.subprocess.run',side_effect=run),patch('downloader.vpn_lifecycle.has_downloads',return_value=False):
                remove_environment(network,True)
            self.assertIn(['docker','rm','-f','worker'],calls)
            self.assertIn(['docker','rm','-f','gateway'],calls)
            self.assertNotIn(['docker','rm','-f','other-worker'],calls)
            self.assertIn(['docker','volume','rm','owned-volume'],calls)

    def test_unowned_volume_and_image_are_preserved(self):
        network=Mock();network.name='gateway-name';network.container=None
        calls=[]
        def run(arguments,**kwargs):
            calls.append(arguments)
            if arguments[1]=='ps':return Mock(returncode=0,stdout='')
            return Mock(returncode=0,stdout=json.dumps([{'Config':{'Labels':{}},'Labels':{}}]))
        with tempfile.TemporaryDirectory() as directory,patch('downloader.environment_uninstall.SteamRuntime',return_value=Mock(root=Path(directory),volume='unowned')),patch('downloader.environment_uninstall.subprocess.run',side_effect=run),patch('downloader.vpn_lifecycle.has_downloads',return_value=False):
            remove_environment(network,True)
        self.assertFalse(any('rm' in call for call in calls))

    def test_active_downloads_prevent_all_cleanup(self):
        with patch('downloader.vpn_lifecycle.has_downloads',return_value=True),patch('downloader.environment_uninstall.subprocess.run') as run:
            with self.assertRaises(RuntimeError):remove_environment(Mock(),True)
            run.assert_not_called()

    def test_private_cleanup_preserves_untracked_root_files_and_exported_games(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'steam-runtime';root.mkdir()
            library=root/'library';library.mkdir();(library/'private-game').write_text('private')
            (root/'manual-note').write_text('external addition')
            exported=Path(directory)/'exported';exported.mkdir();(exported/'game').write_text('exported')
            (root/'environment-owner.json').write_text(json.dumps({'owner':OWNER,'directories':['library']}))
            runtime=Mock(root=root,volume='volume')
            def run(arguments,**kwargs):
                if arguments[1]=='ps':return Mock(returncode=0,stdout='')
                if 'inspect' in arguments:return Mock(returncode=0,stdout=json.dumps([{'Labels':{OWNER_LABEL:OWNER},'Config':{'Labels':{OWNER_LABEL:OWNER}}}]))
                return Mock(returncode=0,stdout='')
            network=Mock();network.container=None;network.name='gateway'
            with patch('downloader.environment_uninstall.SteamRuntime',return_value=runtime),patch('downloader.environment_uninstall.subprocess.run',side_effect=run),patch('downloader.vpn_lifecycle.has_downloads',return_value=False):
                remove_environment(network,True)
            self.assertFalse(library.exists())
            self.assertEqual((root/'manual-note').read_text(),'external addition')
            self.assertEqual((exported/'game').read_text(),'exported')
