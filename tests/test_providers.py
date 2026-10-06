import io
import json
import zipfile
import unittest
from unittest.mock import Mock, patch
import test_worker
from downloader.providers import parse_pack, Transport
from downloader.moon import Moon


def pack(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        for name, data in entries.items(): archive.writestr(name, data)
    return stream.getvalue()

class ProviderTests(unittest.TestCase):
    def test_prepared_inputs_are_readable_under_private_parent_with_restrictive_umask(self):
        import tempfile,os,stat
        from pathlib import Path
        from downloader.providers import prepare_depot,Depot
        with tempfile.TemporaryDirectory() as parent:
            previous=os.umask(0o077)
            try:
                directory=Path(parent)/'pack'
                prepare_depot(Depot(123,456,b'blob','ab'*32),directory)
            finally:
                os.umask(previous)
            self.assertEqual(stat.S_IMODE(Path(parent).stat().st_mode),0o700)
            for name in ('manifest.bin','depot.keys'):
                self.assertEqual(stat.S_IMODE((directory/name).stat().st_mode),0o644)

    def test_app_declaration_with_provider_arguments_is_accepted(self):
        data = pack({'736260.lua': 'addappid(736260, 1, "")\naddappid(736263,1,"' + 'ab'*32 + '")\nsetManifestid(736263,"7874711158613025532",112508590)', '736263_7874711158613025532.manifest': b'blob'})
        rows = parse_pack(data,736260)
        self.assertEqual(rows[0].id,736263)
        self.assertEqual(rows[0].key,'ab'*32)

    def test_parse_pinned_manifest_without_executing_lua(self):
        data = pack({'736260.lua':'addappid(736260)\naddappid(123,1,"'+'ab'*32+'")\nsetManifestid(123,"456")\nos.execute("do not run")', '123_456.manifest':b'blob', '123_789.manifest':b'other'})
        rows = parse_pack(data,736260)
        self.assertEqual([(r.id,r.manifest) for r in rows],[(123,456)])
        self.assertEqual(rows[0].key,'ab'*32)
        self.assertNotIn('abab',repr(rows))

    def test_traversal_and_wrong_game_rejected(self):
        for entries in [{'../bad.lua':'addappid(736260)'}, {'a.lua':'addappid(1)', '123_456.manifest':b'blob'}]:
            with self.assertRaises(ValueError): parse_pack(pack(entries),736260)

    def test_conflicting_key_and_pin_rejected(self):
        text='addappid(736260)\nsetManifestid(123,"456")\nsetManifestid(123,"789")'
        with self.assertRaises(ValueError): parse_pack(pack({'a.lua':text,'123_456.manifest':b'blob'}),736260)

    def test_http_never_starts_without_verified_vpn(self):
        network=Mock();network.check.side_effect=RuntimeError('VPN unavailable')
        with patch('downloader.providers.subprocess.run') as run:
            with self.assertRaises(RuntimeError): Transport(network).request('https://lua.tools')
        run.assert_not_called()

    def test_moon_code_and_refresh_use_separate_session(self):
        transport=Mock()
        transport.request.side_effect=[json.dumps({'token':'verification'}).encode(),json.dumps({'access_token':'access','refresh_token':'refresh','expires_in':3600}).encode(),json.dumps({'access_token':'new','refresh_token':'new-refresh','expires_in':3600}).encode()]
        moon=Moon(transport);moon.login('ABC123')
        self.assertEqual(moon.token(),'access')
        moon.session['expires_at']=0
        self.assertEqual(moon.token(),'new')
        self.assertIn('grant_type=refresh_token',transport.request.call_args.args[0])

class AuthenticationWorkerTests(unittest.TestCase):
    def test_steam_password_not_in_arguments_and_unique_login_id(self):
        import tempfile
        from downloader.network import Network
        from downloader.worker import worker_args, Request
        network = Network(); network.container='test-gateway';network.check=Mock()
        with tempfile.TemporaryDirectory() as directory:
            network.directory=directory
            first=worker_args(network,Request(736260,123),directory,username='owned-account')
            second=worker_args(network,Request(736260,123),directory,username='owned-account')
        self.assertIn('-i',first)
        self.assertEqual(first[first.index('--workdir')+1],'/tmp')
        self.assertIn('-username',first)
        self.assertNotIn('-password',first)
        self.assertNotIn('-remember-password',first)
        self.assertNotEqual(first[first.index('-loginid')+1],second[second.index('-loginid')+1])

    def test_editor_controls_and_moon_login_are_separate(self):
        from PyQt6.QtWidgets import QApplication,QLineEdit
        from downloader.download_dialog import DownloadDialog
        app=QApplication.instance() or QApplication([])
        dialog=DownloadDialog(Mock())
        self.assertEqual(dialog.code.echoMode(),QLineEdit.EchoMode.Password)
        self.assertEqual(dialog.response.echoMode(),QLineEdit.EchoMode.Password)
        self.assertIn(dialog.provider.currentText(),('Luie','Hubcap','Sushi','Ryuu'))
        self.assertIsNone(dialog.moon.session)
        dialog.close()

class LoginDiagnosticsTests(unittest.TestCase):
    def test_error_reports_login_stage_without_code(self):
        transport=Mock();transport.request.side_effect=RuntimeError('Provider blocked request')
        with self.assertRaisesRegex(RuntimeError,'Discord code redemption: Provider blocked request') as error:
            Moon(transport).login('ABC123')
        self.assertNotIn('ABC123',str(error.exception))

    def test_cloudflare_and_invalid_code_have_distinct_messages(self):
        import tempfile
        from types import SimpleNamespace
        from downloader.network import Network
        for reason, message in [('browser_challenge','browser challenge'),('invalid_code','rejected the login code'),('expired_code','expired or was already used'),('invalid_api_key','public API client key')]:
            network=Network();network.container='gateway';network.check=Mock()
            with tempfile.TemporaryDirectory() as directory:
                network.directory=directory
                response=SimpleNamespace(returncode=0,stdout=json.dumps({'status':403,'body':'','reason':reason}))
                with patch('downloader.providers.subprocess.run',return_value=response):
                    with self.assertRaisesRegex(RuntimeError,message):
                        Transport(network).request('https://lua.tools/api/auth/code/redeem',data={'code':'ABC123'})

class ResponseFormatTests(unittest.TestCase):
    def test_gzip_response_and_utf16_lua(self):
        import gzip
        data=pack({'a.lua':'addappid(736260)'.encode('utf-16'),'123_456.manifest':b'manifest'})
        self.assertEqual(parse_pack(gzip.compress(data),736260)[0].id,123)

    def test_non_archive_error_identifies_format_without_content(self):
        for data,kind in [(b'{"secret":"do-not-echo"}','JSON response'),(b'<html>private</html>','HTML page')]:
            with self.assertRaisesRegex(ValueError,kind) as error:
                parse_pack(data,736260)
            self.assertNotIn('do-not-echo',str(error.exception))

class SavedSessionTests(unittest.TestCase):
    def test_saved_session_restored_without_network_when_valid(self):
        import time
        store=Mock();store.read.return_value={'access_token':'access','refresh_token':'refresh','expires_at':time.time()+3600}
        transport=Mock();moon=Moon(transport,store)
        self.assertEqual(moon.token(),'access');transport.request.assert_not_called()
        moon.logout();store.clear.assert_called_once();self.assertIsNone(moon.session)

    def test_expired_session_refresh_is_saved(self):
        store=Mock();store.read.return_value={'access_token':'old','refresh_token':'refresh','expires_at':0}
        transport=Mock();transport.request.return_value=json.dumps({'access_token':'new','refresh_token':'rotated','expires_in':3600}).encode()
        self.assertEqual(Moon(transport,store).token(),'new')
        self.assertEqual(store.save.call_args.args[0]['refresh_token'],'rotated')

    def test_wallet_failure_does_not_claim_login_persisted(self):
        store=Mock();store.save.side_effect=RuntimeError('Wallet unavailable')
        transport=Mock();transport.request.side_effect=[b'{"token":"verification"}',b'{"access_token":"access","refresh_token":"refresh"}']
        moon=Moon(transport,store)
        with self.assertRaisesRegex(RuntimeError,'Wallet unavailable'):moon.login('ABC123')
        self.assertIsNone(moon.session)

class SteamSessionTests(unittest.TestCase):
    def test_sessions_are_separate_for_each_account_and_from_moon(self):
        from downloader.credentials import SteamSessionWallet, MoonSessionWallet
        self.assertNotEqual(SteamSessionWallet('one').entry,SteamSessionWallet('two').entry)
        self.assertEqual(SteamSessionWallet(' ONE ').entry,SteamSessionWallet('one').entry)
        self.assertNotEqual(SteamSessionWallet('one').entry,MoonSessionWallet.entry)

    def test_worker_session_is_saved_then_plaintext_staging_is_removed(self):
        import tempfile,base64
        from PyQt6.QtWidgets import QApplication
        from downloader.download_dialog import DownloadDialog
        from pathlib import Path
        app=QApplication.instance() or QApplication([])
        dialog=DownloadDialog(Mock())
        dialog.process=Mock();dialog.process.readAllStandardOutput.return_value=b''
        dialog.temporary=tempfile.TemporaryDirectory()
        path=Path(dialog.temporary.name)
        dialog.auth_file=path/'account.config';dialog.auth_file.write_bytes(b'test-session')
        dialog.steam_wallet=Mock()
        dialog.finished(1)
        stored=dialog.steam_wallet.save.call_args.args[0]
        self.assertEqual(base64.b64decode(stored['data']),b'test-session')
        self.assertFalse(path.exists())
        dialog.close()

class SeparatedWorkflowTests(unittest.TestCase):
    def test_menu_downloader_hides_authentication_and_settings_show_it(self):
        from PyQt6.QtWidgets import QApplication
        from downloader.download_dialog import DownloadDialog
        app=QApplication.instance() or QApplication([])
        downloader=DownloadDialog(Mock());downloader.show();app.processEvents()
        self.assertFalse(downloader.code.isVisible());self.assertFalse(downloader.username.isVisible())
        self.assertFalse(downloader.response.isVisible());self.assertTrue(downloader.appid.isVisible())
        settings=DownloadDialog(Mock(),authentication=True);settings.show();app.processEvents()
        self.assertTrue(settings.code.isVisible());self.assertTrue(settings.username.isVisible())
        self.assertTrue(settings.response.isVisible());self.assertFalse(settings.appid.isVisible())
        self.assertEqual(settings.start_button.text(),'Authenticate')
        downloader.close();settings.close()

    def test_plugin_exposes_main_menu_action(self):
        from downloader.plugin import Plugin
        self.assertEqual(Plugin().main_menu_actions(Mock())[0][0],'Steam Depot Downloader…')

class LuaOnlyTests(unittest.TestCase):
    def test_plain_lua_pins_and_keys_are_data_without_binary_manifest(self):
        text='addappid(736260)\naddappid(123,1,"'+'ab'*32+'")\nsetManifestid(123,"456")\nos.execute("never run")'
        row=parse_pack(text.encode(),736260)[0]
        self.assertEqual((row.id,row.manifest,row.data),(123,456,b''))
        self.assertEqual(row.key,'ab'*32)
        self.assertNotIn('abab',repr(row))

    def test_key_only_input_can_use_steam_current_manifest(self):
        row=parse_pack(('addappid(736260)\naddappid(123,1,"'+'ab'*32+'")').encode(),736260)[0]
        self.assertIsNone(row.manifest);self.assertFalse(row.data)

    def test_lua_only_zip_and_numeric_pins_are_accepted(self):
        rows=parse_pack(pack({'game.lua':'addappid(736260)\nsetManifestid(123,456)'}),736260)
        self.assertEqual((rows[0].id,rows[0].manifest),(123,456))

    def test_safe_windows_archive_paths_are_normalized(self):
        rows=parse_pack(pack({'game\\game.lua':'addappid(736260)','game\\123_456.manifest':b'blob'}),736260)
        self.assertEqual(rows[0].id,123)
        for path in ('game\\..\\evil.lua','C:\\evil.lua','\\absolute.lua'):
            with self.assertRaises(ValueError):parse_pack(pack({path:'addappid(736260)'}),736260)

    def test_normalized_duplicate_names_and_wrong_game_are_rejected(self):
        with self.assertRaises(ValueError):parse_pack(pack({'a\\game.lua':'addappid(736260)','a/game.lua':'addappid(736260)'}),736260)
        with self.assertRaises(ValueError):parse_pack(b'addappid(999)\nsetManifestid(123,"456")',736260)

    def test_key_only_inputs_do_not_fabricate_manifest_files(self):
        import tempfile
        from pathlib import Path
        from downloader.providers import prepare_depot,Depot
        with tempfile.TemporaryDirectory() as root:
            directory=Path(root)/'pack';prepare_depot(Depot(123,456,b'','ab'*32),directory)
            self.assertFalse((directory/'manifest.bin').exists())
            self.assertTrue((directory/'depot.keys').exists())
