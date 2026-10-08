import tempfile
import os
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
import json
import test_worker
from downloader.steam_runtime import SteamRuntime,RUNTIME_IMAGE
from downloader.constants import IMAGE,WORKER_LABEL

class RuntimeTests(unittest.TestCase):
    def test_permission_repair_only_changes_owned_temporary_files(self):
        spec=importlib.util.spec_from_file_location('permissions_bridge',Path(__file__).resolve().parents[1]/'tools/steam/bridge.py')
        bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)
        with tempfile.TemporaryDirectory() as folder:
            bridge.HOME=Path(folder)
            root=bridge.HOME/'.steam/debian-installation/steamapps/downloading';root.mkdir(parents=True)
            temporary=root/'partial';temporary.write_text('partial');temporary.chmod(0o555)
            installed=bridge.HOME/'installed';installed.write_text('game');installed.chmod(0o555)
            (root/'link').symlink_to(installed)
            self.assertEqual(bridge.repair_download_permissions(),1)
            self.assertEqual(temporary.stat().st_mode & 0o777,0o755)
            self.assertEqual(installed.stat().st_mode & 0o777,0o555)


    def test_exited_steam_process_is_not_running(self):
        path=Path(__file__).resolve().parents[1]/'tools/steam/bridge.py'
        spec=importlib.util.spec_from_file_location('zombie_bridge',path)
        bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)
        with tempfile.TemporaryDirectory() as folder:
            process=Path(folder)/'123';process.mkdir()
            (process/'comm').write_text('steam')
            (process/'stat').write_text('123 (steam) Z 1 0')
            with patch.object(Path,'iterdir',return_value=iter([process])):
                self.assertFalse(bridge.steam_running())
            (process/'stat').write_text('123 (steam) S 1 0')
            with patch.object(Path,'iterdir',return_value=iter([process])):
                self.assertTrue(bridge.steam_running())


    def test_bridge_requests_support_long_repository_profile_paths(self):
        import socket,threading
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/('long-profile-folder-'*7)
            control=root/'control';control.mkdir(parents=True)
            self.assertGreater(len(str(control/'bridge.sock')),108)
            descriptor=os.open(control,os.O_RDONLY|os.O_DIRECTORY)
            try:
                with socket.socket(socket.AF_UNIX) as listener:
                    listener.bind(f'/proc/self/fd/{descriptor}/bridge.sock')
                    listener.listen();listener.settimeout(3)
                    def respond():
                        with listener.accept()[0] as client:
                            client.recv(4096)
                            client.sendall(b'{"ok":true,"result":{"steam_running":false}}\n')
                    thread=threading.Thread(target=respond);thread.start()
                    try:self.assertEqual(SteamRuntime(Mock(),root).request('status'),{'steam_running':False})
                    finally:thread.join(3)
            finally:os.close(descriptor)
    def test_start_exposes_cef_for_luamoon_ui_injection(self):
        path=Path(__file__).resolve().parents[1]/'tools/steam/bridge.py'
        spec=importlib.util.spec_from_file_location('ui_start_bridge',path)
        bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)
        with tempfile.TemporaryDirectory() as directory:
            bridge.HOME=Path(directory)
            with patch.object(bridge,'steam_running',return_value=False),patch.object(bridge,'prepare_library'),patch.object(bridge,'launch') as launch:
                bridge.dispatch({'command':'start'})
                args=launch.call_args.args[0]
                self.assertIn('-cef-enable-debugging',args)
                self.assertEqual(launch.call_args.kwargs['environment']['SLSSTEAM_AUDIT_BINDALL'],'1')

    def test_manual_stop_releases_only_owned_environment(self):
        network=Mock();network.container='gateway';runtime=SteamRuntime(network)
        entry={'Config':{'Labels':{WORKER_LABEL:'gateway'}},'HostConfig':{'NetworkMode':'container:gateway'}}
        with patch('downloader.steam_runtime.subprocess.run',return_value=Mock(returncode=0,stdout=json.dumps([entry]))) as run,patch.object(runtime,'request') as request:
            message=runtime.stop()
            request.assert_called_once_with('stop')
            self.assertEqual(run.call_args.args[0],['docker','stop','--time','10',runtime.name])
            network.disconnect.assert_not_called()
            self.assertIn('VPN remains connected',message)

    def test_manual_stop_protects_downloads_and_foreign_container(self):
        network=Mock();runtime=SteamRuntime(network)
        with patch('downloader.vpn_lifecycle.has_downloads',return_value=True),patch('downloader.steam_runtime.subprocess.run') as run:
            with self.assertRaisesRegex(RuntimeError,'Pause or cancel'):runtime.stop()
            run.assert_not_called()
        network.container='gateway'
        entry={'Config':{'Labels':{WORKER_LABEL:'other'}},'HostConfig':{'NetworkMode':'container:other'}}
        with patch('downloader.steam_runtime.subprocess.run',return_value=Mock(returncode=0,stdout=json.dumps([entry]))) as run:
            with self.assertRaisesRegex(RuntimeError,'Refusing'):runtime.stop()
            self.assertEqual(run.call_count,1)
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
            self.assertEqual(first.count('"path" "'+str(bridge.LIBRARY)+'"'),1)
            bridge.prepare_library();self.assertEqual(library.read_text(),first)

    def test_completed_default_private_library_install_is_exported(self):
        path=Path(__file__).resolve().parents[1]/'tools/steam/bridge.py'
        spec=importlib.util.spec_from_file_location('default_library_bridge',path)
        bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)
        with tempfile.TemporaryDirectory() as directory:
            bridge.HOME=Path(directory)/'home';bridge.LIBRARY=Path(directory)/'library'
            steam=bridge.HOME/'.steam/steam'
            game=steam/'steamapps/common/Game';game.mkdir(parents=True)
            (game/'Game.exe').write_bytes(b'verified game')
            (steam/'steamapps/appmanifest_10.acf').write_text('"AppState" { "StateFlags" "4" "installdir" "Game" "InstalledDepots" { "11" { "manifest" "123" } } }')
            result=bridge.download_status(10)
            self.assertFalse(result['installed'])
            self.assertTrue(result['export_pending'])
            next(iter(bridge.exports.values()))['thread'].join(2)
            result=bridge.download_status(10)
            self.assertTrue(result['installed'])
            exported=bridge.LIBRARY/result['directory']
            self.assertEqual((exported/'Game.exe').read_bytes(),b'verified game')
            self.assertTrue((game/'Game.exe').exists())
            self.assertEqual(bridge.download_status(10)['directory'],result['directory'])

    def test_private_export_keeps_status_responsive_and_withholds_incomplete_files(self):
        import threading
        path=Path(__file__).resolve().parents[1]/'tools/steam/bridge.py'
        spec=importlib.util.spec_from_file_location('export_progress_bridge',path)
        bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)
        with tempfile.TemporaryDirectory() as directory:
            bridge.HOME=Path(directory)/'home';bridge.LIBRARY=Path(directory)/'library'
            steam=bridge.HOME/'.steam/steam';game=steam/'steamapps/common/Game';game.mkdir(parents=True)
            (game/'Game.exe').write_bytes(b'verified game')
            (steam/'steamapps/appmanifest_10.acf').write_text('"AppState" { "StateFlags" "4" "installdir" "Game" }')
            started=threading.Event();release=threading.Event();copytree=bridge.shutil.copytree
            def slow_copy(*args,**kwargs):
                started.set()
                if not release.wait(2):raise RuntimeError('Test copy was not released')
                return copytree(*args,**kwargs)
            bridge.shutil.copytree=slow_copy
            try:
                first=bridge.download_status(10)
                job=next(iter(bridge.exports.values()))
                self.assertTrue(started.wait(1))
                second=bridge.download_status(10)
                self.assertFalse(second['installed']);self.assertTrue(second['export_pending'])
                self.assertIsNone(second['directory'])
                self.assertEqual(second['export_total'],len(b'verified game'))
                self.assertEqual(len(bridge.exports),1)
            finally:
                release.set();job['thread'].join(2);bridge.shutil.copytree=copytree
            self.assertFalse(job['thread'].is_alive())
            self.assertTrue(bridge.download_status(10)['installed'])

    def test_content_events_are_bounded_and_exclude_other_apps_and_connection_data(self):
        path=Path(__file__).resolve().parents[1]/'tools/steam/bridge.py'
        spec=importlib.util.spec_from_file_location('content_event_bridge',path)
        bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)
        with tempfile.TemporaryDirectory() as directory:
            bridge.HOME=Path(directory)
            log=bridge.HOME/'.steam/steam/logs/content_log.txt';log.parent.mkdir(parents=True)
            lines=['AppID 20 state changed : Downloading','AppID 10 account authorization secret','Connection to CDN token=secret']
            lines += [f'AppID 10 state changed : Downloading {index}' for index in range(30)]
            log.write_text('\n'.join(lines))
            events=bridge.content_events(10)
            self.assertEqual(len(events),12)
            self.assertTrue(all('AppID 10 state changed' in event for event in events))
            self.assertFalse(any('secret' in event for event in events))

    def test_cached_game_detection_is_app_specific_and_requires_enabled_nonempty_script(self):
        path=Path(__file__).resolve().parents[1]/'tools/steam/bridge.py'
        spec=importlib.util.spec_from_file_location('cache_bridge',path)
        bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)
        with tempfile.TemporaryDirectory() as directory:
            bridge.HOME=Path(directory)
            scripts=bridge.HOME/'.steam/steam/config/stplug-in';scripts.mkdir(parents=True)
            (scripts/'10.lua').write_text('existing provider data')
            self.assertTrue(bridge.dispatch({'command':'has_game','appid':10})['exists'])
            self.assertFalse(bridge.dispatch({'command':'has_game','appid':11})['exists'])
            (scripts/'10.lua').rename(scripts/'10.lua.disabled')
            self.assertFalse(bridge.dispatch({'command':'has_game','appid':10})['exists'])

    def test_incomplete_default_library_download_reports_progress_without_export_path(self):
        path=Path(__file__).resolve().parents[1]/'tools/steam/bridge.py'
        spec=importlib.util.spec_from_file_location('incomplete_library_bridge',path)
        bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)
        with tempfile.TemporaryDirectory() as directory:
            bridge.HOME=Path(directory)/'home';bridge.LIBRARY=Path(directory)/'library'
            steamapps=bridge.HOME/'.steam/steam/steamapps';steamapps.mkdir(parents=True)
            (steamapps/'appmanifest_10.acf').write_text('"AppState" { "StateFlags" "1024" "installdir" "Game" "BytesDownloaded" "100" "BytesToDownload" "500" }')
            bridge.steam_action=Mock(side_effect=RuntimeError('Steam UI starting'))
            state=bridge.download_status(10)
            self.assertFalse(state['installed'])
            self.assertIsNone(state['directory'])
            self.assertEqual((state['downloaded'],state['total']),(100,500))
            self.assertFalse(bridge.LIBRARY.exists())
            bridge.steam_action=Mock(return_value={'transfer':{'downloaded':350,'total':500,'speed':20,'disk_processed':800,'disk_total':1000}})
            live=bridge.download_status(10)
            self.assertEqual((live['downloaded'],live['total'],live['speed']),(350,500,20))
            self.assertEqual((live['disk_processed'],live['disk_total']),(800,1000))
            self.assertFalse(live['installed'])
            self.assertIsNone(live['directory'])

    def test_finished_export_uninstalls_private_game_and_removes_only_its_cache(self):
        path=Path(__file__).resolve().parents[1]/'tools/steam/bridge.py'
        spec=importlib.util.spec_from_file_location('finish_export_bridge_test',path)
        bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            bridge.LIBRARY=Path(directory)
            common=bridge.LIBRARY/'steamapps/common';common.mkdir(parents=True)
            owned=common/'playlite-import-10-0123456789abcdef';owned.mkdir();(owned/'game').write_text('copy')
            other=common/'playlite-import-20-0123456789abcdef';other.mkdir()
            exported=Path(directory)/'external-destination';exported.mkdir();(exported/'game').write_text('verified')
            with patch.object(bridge,'installed_games',side_effect=[{'games':[{'appid':10,'status':'Installed'}]},{'games':[]}]),patch.object(bridge,'steam_action',return_value={'requested':True}) as uninstall:
                self.assertEqual(bridge.finish_export(10),{'removed':True})
                uninstall.assert_called_once_with({'action':'uninstall','appid':10})
            self.assertFalse(owned.exists());self.assertTrue(other.exists())
            self.assertEqual((exported/'game').read_text(),'verified')
