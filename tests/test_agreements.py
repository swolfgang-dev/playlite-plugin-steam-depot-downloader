import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock,patch
import test_worker
from PyQt6.QtWidgets import QApplication,QWidget,QPushButton
from downloader.agreement_dialog import AgreementDialog
from downloader.steam_queue_runner import SteamQueueRunner
from playlite.downloads import DownloadQueue

class AgreementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def bridge(self):
        path=Path(__file__).resolve().parents[1]/'tools/steam/bridge.py'
        spec=importlib.util.spec_from_file_location('agreement_bridge',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        return module

    def test_agreement_fetch_rejects_non_steam_urls(self):
        bridge=self.bridge()
        for url in ('file:///etc/passwd','http://store.steampowered.com/eula/10','https://other.test/eula/10','https://store.steampowered.com@other.test/eula/10'):
            with self.assertRaises(ValueError):bridge.agreement_url(url)

    def test_rendered_text_omits_scripts_and_styles(self):
        parser=self.bridge().AgreementText();parser.feed('<p>Read &amp; agree</p><script>hidden()</script><style>body{}</style>')
        self.assertEqual(''.join(parser.parts).strip(),'Read & agree')

    def test_changed_agreement_is_not_reported_as_accepted(self):
        bridge=self.bridge()
        with patch.object(bridge,'steam_action',return_value={'accepted':False}):
            with self.assertRaisesRegex(RuntimeError,'changed'):
                bridge.dispatch({'command':'eula_accept','appid':10,'eula_id':'10_eula_0','eula_version':2})

    def test_only_explicit_accept_click_completes_the_dialog(self):
        agreement={'id':'10_eula_0','version':2,'text':'Terms for review'}
        dialog=AgreementDialog('Game',agreement)
        accepted=Mock();dialog.accepted.connect(accepted)
        accepted.assert_not_called()
        button=next(button for button in dialog.findChildren(QPushButton) if button.text()=='Accept agreement')
        self.assertFalse(button.autoDefault())
        button.click();accepted.assert_called_once()
        dialog.deleteLater()

    def test_cancellation_never_sends_acceptance_to_steam(self):
        window=QWidget();queue=DownloadQueue(window);queue.pump=Mock()
        entry=queue.enqueue('Game','/tmp/playlite-agreement-cancel',Mock())
        queue.active=entry;entry.state='Downloading'
        network=Mock();network.container='vpn';runtime=Mock()
        def request(command,*args,**kwargs):
            if command=='add_status':return {'state':{'status':'done'}}
            if command=='download_status':return {'installed':False,'total':0}
            if command=='eula_status':return {'agreement':{'id':'10_eula_0','version':2,'text':'Terms'}}
            return {}
        runtime.request.side_effect=request
        with patch('downloader.steam_queue_runner.SteamRuntime',return_value=runtime),patch('downloader.steam_queue_runner.QThreadPool') as pool:
            runner=SteamQueueRunner(network,window,queue,entry,{'app':10,'platform':'windows','language':'english','dlc':[],'info':{'game':{'depots':{}},'dlc':[]}})
            runner.review_agreement=lambda agreement:runner.cancel()
            pool.globalInstance.return_value.start.side_effect=lambda job:job.run()
            runner.start()
            self.assertFalse(any(call.args[0]=='eula_accept' for call in runtime.request.call_args_list))
            self.assertIn('cancelled',entry.status)
            runner.dispose()
        window.deleteLater()
