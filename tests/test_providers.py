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
        self.assertEqual(dialog.provider.currentText(),'Luie')
        self.assertIsNone(dialog.moon.session)
        dialog.close()
