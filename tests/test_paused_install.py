import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]

class PausedInstallTests(unittest.TestCase):
    def test_paused_detection_reads_private_libraries_without_copying_content(self):
        spec=importlib.util.spec_from_file_location('paused_bridge',ROOT/'tools/steam/bridge.py')
        bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)
        with tempfile.TemporaryDirectory() as directory:
            bridge.LIBRARY=Path(directory)/'library';bridge.HOME=Path(directory)/'home'
            folder=bridge.HOME/'.steam/steam/steamapps';folder.mkdir(parents=True)
            manifest=folder/'appmanifest_10.acf'
            for flags,expected in [(530,True),(512,True),(4,False),(1024,False)]:
                manifest.write_text(f'"AppState" {{ "StateFlags" "{flags}" }}')
                self.assertEqual(bridge.paused_install(10),expected)
            manifest.unlink();self.assertFalse(bridge.paused_install(10))

    @unittest.skipUnless(shutil.which('node'),'JavaScript runtime unavailable')
    def test_native_retry_cancels_only_selected_app_then_requeues_before_resume(self):
        source=(ROOT/'tools/steam/steam_control.lua').read_text()
        expression=source.split("json.encode(request)..[[;",1)[1].split('})()]]',1)[0]
        for paused in (False,True):
            request={'appid':10,'action':'install','platform':'windows','language':'english','dlc':[],'restart_paused':paused}
            js='''const calls=[];const action=name=>async(...args)=>calls.push([name,...args]);
const SteamClient={Apps:{GetAvailableCompatTools:async()=>[{strToolName:'proton_experimental'}],SpecifyCompatTool:action('platform'),SetAppCurrentLanguage:action('language')},Downloads:{RemoveFromDownloadList:action('remove'),QueueAppUpdate:action('queue'),EnableAllDownloads:action('enable'),ResumeAppUpdate:action('resume')},InstallFolder:{GetInstallFolders:async()=>[{strFolderPath:'/library',bIsMounted:true,nFolderIndex:1,vecApps:[{nAppID:10}]}]}};
'''+ '(async()=>{const r='+json.dumps(request)+';'+expression+'})().then(result=>console.log(JSON.stringify({result,calls})));'
            result=json.loads(subprocess.run(['node','-e',js],capture_output=True,text=True,check=True).stdout)
            self.assertTrue(result['result']['requested'])
            for call in result['calls']:
                if call[0] in ('remove','queue','resume','enable'):self.assertEqual(call[-1],'0')
            names=[call[0] for call in result['calls']]
            self.assertEqual('remove' in names,paused);self.assertIn('queue',names)
            self.assertLess(names.index('queue'),names.index('resume'))
            if paused:
                self.assertLess(names.index('remove'),names.index('queue'))
                self.assertLess(names.index('queue'),names.index('resume'))
                self.assertEqual(next(call for call in result['calls'] if call[0]=='remove'),['remove',10,'0'])
