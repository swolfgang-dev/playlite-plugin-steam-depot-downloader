import tempfile
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import Mock
import test_worker
from downloader.steam_runtime import SteamRuntime,RUNTIME_IMAGE
from downloader.constants import IMAGE,WORKER_LABEL

class RuntimeTests(unittest.TestCase):
    def test_gui_runtime_retains_guarded_identity_and_private_mounts(self):
        network=Mock();network.container='verified-gateway'
        network.probe_args.return_value=['run','--rm','--network','container:verified-gateway','--label',WORKER_LABEL+'=verified-gateway','--user','65534:65534','--cap-drop','ALL','--security-opt','no-new-privileges','--read-only','--pids-limit','32','--memory','64m','--entrypoint','/bin/sh',IMAGE,'-c','']
        with tempfile.TemporaryDirectory() as directory:
            runtime=SteamRuntime(network,Path(directory)/'private')
            args=runtime.arguments()
            self.assertEqual(args[args.index('--network')+1],'container:verified-gateway')
            self.assertEqual(args[args.index('--user')+1],'65534:65534')
            self.assertIn('--read-only',args);self.assertIn('no-new-privileges',args)
            self.assertNotIn('--privileged',args);self.assertNotIn('-p',args)
            self.assertEqual(args[-2:], [RUNTIME_IMAGE,'/opt/bridge.py'])
            self.assertEqual(runtime.root.stat().st_mode & 0o777,0o700)
            mounts=[args[i+1] for i,value in enumerate(args) if value=='--mount']
            self.assertEqual(len(mounts),3)
            self.assertTrue(all('/.steam' not in value and '/var/run/docker.sock' not in value for value in mounts))
        network.check.assert_called_once()

    def test_network_failure_prevents_runtime_directory_and_launch(self):
        network=Mock();network.check.side_effect=RuntimeError('VPN down')
        with tempfile.TemporaryDirectory() as directory:
            runtime=SteamRuntime(network,Path(directory)/'absent')
            with self.assertRaisesRegex(RuntimeError,'VPN down'):runtime.arguments()
            self.assertFalse(runtime.root.exists())

    def test_control_plane_rejects_arbitrary_commands(self):
        runtime=SteamRuntime(Mock())
        with self.assertRaises(ValueError):runtime.request('shell')

    def test_install_requires_an_explicit_supported_platform(self):
        network=Mock();runtime=SteamRuntime(network)
        for platform in (None,'','macos','Windows','windows; command'):
            with self.subTest(platform=platform):
                with self.assertRaises(ValueError):runtime.request('install',10,platform=platform)
        network.check.assert_not_called()

    def test_bridge_reads_installed_depots_and_rejects_path_escape(self):
        path=Path(__file__).resolve().parents[1]/'tools/steam/bridge.py'
        spec=importlib.util.spec_from_file_location('manifest_bridge_test',path)
        bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)
        with tempfile.TemporaryDirectory() as directory:
            bridge.LIBRARY=Path(directory)
            game=bridge.LIBRARY/'steamapps/common/Game';game.mkdir(parents=True)
            (game/'Game.exe').write_bytes(b'game')
            manifest=bridge.LIBRARY/'steamapps/appmanifest_10.acf'
            source='"AppState" { "StateFlags" "4" "installdir" "Game" "BytesDownloaded" "20" "BytesToDownload" "20" "InstalledDepots" { "11" { "manifest" "123" } } }'
            manifest.write_text(source)
            result=bridge.download_status(10)
            self.assertTrue(result['installed']);self.assertEqual(result['depots'],[11])
            self.assertEqual(result['directory'],'steamapps/common/Game')
            manifest.write_text(source.replace('"installdir" "Game"','"installdir" "../../private"'))
            with self.assertRaises(ValueError):bridge.download_status(10)

    def test_moon_bridge_rejects_invalid_app_ids_before_writing_requests(self):
        path=Path(__file__).resolve().parents[1]/'tools/steam/bridge.py'
        spec=importlib.util.spec_from_file_location('private_bridge_test',path)
        bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)
        with tempfile.TemporaryDirectory() as directory:
            bridge.CONTROL=Path(directory)
            for value in (None,True,0,-1,2**32,'736260','736260; touch /tmp/unsafe'):
                with self.subTest(appid=value):
                    with self.assertRaises(ValueError):
                        bridge.dispatch({'command':'add','appid':value})
            self.assertEqual(list(bridge.CONTROL.iterdir()),[])

    def test_library_registration_preserves_existing_entries_and_is_idempotent(self):
        path=Path(__file__).resolve().parents[1]/'tools/steam/bridge.py'
        spec=importlib.util.spec_from_file_location('library_bridge_test',path)
        bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)
        with tempfile.TemporaryDirectory() as directory:
            bridge.HOME=Path(directory)/'home';bridge.LIBRARY=Path(directory)/'library'
            library=bridge.HOME/'.steam/debian-installation/steamapps/libraryfolders.vdf'
            library.parent.mkdir(parents=True)
            original='"libraryfolders"\n{\n\t"0" { "path" "/existing" "apps" { "123" "456" } }\n}\n'
            library.write_text(original)
            bridge.prepare_library();first=library.read_text()
            self.assertIn('"path" "/existing"',first)
            self.assertIn('"123" "456"',first)
            self.assertEqual(first.count('"path" "/library"'),1)
            bridge.prepare_library();self.assertEqual(library.read_text(),first)
