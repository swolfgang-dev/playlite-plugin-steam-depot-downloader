import base64
import unittest
from unittest.mock import Mock,patch
import test_worker
from downloader.preflight import check_plan
from downloader.providers import Depot

class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.row=Depot(11,1,b'manifest')
        self.wallet=Mock();self.wallet.read.return_value={'data':base64.b64encode(b'cache').decode()}
        self.wallet_patch=patch('downloader.preflight.SteamSessionWallet',return_value=self.wallet)
        self.wallet_patch.start();self.addCleanup(self.wallet_patch.stop)

    def run_check(self,text,code=0):
        args=['run','--entrypoint','/tool/DepotDownloaderMod','--workdir','/tmp','playlite-depot-worker:test','-app','10']
        with patch('downloader.preflight.worker_args',return_value=args),patch('downloader.preflight.metadata_output',return_value=(code,text)) as output,patch('downloader.preflight.subprocess.run') as cleanup:
            result=check_plan(Mock(),[(10,self.row,'Game')],'account')
            self.assertIn('-playlite-preflight',output.call_args.args[0]);cleanup.assert_called_once()
            return result

    def test_cdn_marker_is_required_not_just_zero_exit_code(self):
        with self.assertRaisesRegex(RuntimeError,'Nothing was queued'):self.run_check('Total downloaded: 0 bytes from 1 depots')
        self.wallet.save.assert_not_called()

    def test_verified_chunk_saves_refreshed_session(self):
        self.assertEqual(self.run_check('PLAYLITE_PREFLIGHT 11 1 OK'),['OK'])
        self.wallet.save.assert_called_once()

    def test_wrong_depot_or_manifest_cannot_pass(self):
        with self.assertRaises(RuntimeError):self.run_check('PLAYLITE_PREFLIGHT 12 1 OK')
        with self.assertRaises(RuntimeError):self.run_check('PLAYLITE_PREFLIGHT 11 2 OK')

    def test_empty_depot_is_distinguished_from_verified_cdn(self):
        self.assertEqual(self.run_check('PLAYLITE_PREFLIGHT 11 1 EMPTY'),['EMPTY'])

    def test_saved_authentication_is_required(self):
        self.wallet.read.return_value=None
        with patch('downloader.preflight.worker_args') as worker:
            with self.assertRaises(ValueError):check_plan(Mock(),[(10,self.row,'Game')],'account')
            worker.assert_not_called()
