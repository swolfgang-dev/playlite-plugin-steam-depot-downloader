import copy
import unittest
from unittest.mock import Mock, patch
import test_worker
from downloader.app_info import build_plan, validate_info, content_depots, fetch_app_info
from downloader.providers import Depot


def depot(os='',dlc=None):
    row={'config':{'oslist':os},'manifests':{'public':{'gid':'1'}}}
    if dlc:row['dlcappid']=str(dlc)
    return row


def fixture():
    return {'game':{'id':10,'name':'Game','owned':True,'depots':{'11':depot(),'12':depot('linux'),'13':depot('windows'),'21':depot('linux',20)}},
            'dlc':[{'id':20,'name':'Expansion','owned':True,'depots':{}},{'id':30,'name':'Bundled DLC','owned':True,'depots':{}},{'id':40,'name':'Unowned','owned':False,'depots':{'41':depot('linux')}}]}


class ContentTests(unittest.TestCase):
    def setUp(self):
        self.info=fixture()
        self.packs={10:[Depot(i,1,b'manifest') for i in (11,12,13,21)]}

    def test_base_game_contains_common_and_matching_platform_only(self):
        self.assertEqual([row.id for _,row,_ in build_plan(self.info,self.packs,'linux',set())],[11,12])

    def test_selected_expansion_is_included_once(self):
        self.info['dlc'][0]['depots']={'21':depot('linux')}
        self.assertEqual([(app,row.id) for app,row,_ in build_plan(self.info,self.packs,'linux',{20})],[(10,11),(10,12),(10,21)])

    def test_missing_manifest_aborts_entire_plan(self):
        self.packs[10]=self.packs[10][:3]
        with self.assertRaisesRegex(ValueError,'missing depot 21'):
            build_plan(self.info,self.packs,'linux',{20})
        self.packs[20]=[Depot(21,1,b'manifest')]
        self.assertEqual(len(build_plan(self.info,self.packs,'linux',{20})),3)

    def test_unknown_dlc_is_rejected_but_package_ownership_is_not_a_gate(self):
        with self.assertRaises(ValueError):build_plan(self.info,self.packs,'linux',{999})
        self.info['game']['owned']=False
        self.packs[40]=[Depot(41,1,b'manifest')]
        self.assertEqual(len(build_plan(self.info,self.packs,'linux',{40})),3)

    def test_language_and_architecture_filter(self):
        self.info['game']['depots']['14']=depot('linux')
        self.info['game']['depots']['14']['config']['language']='german'
        self.info['game']['depots']['15']=depot('linux')
        self.info['game']['depots']['15']['config']['osarch']='32'
        self.assertEqual(len(build_plan(self.info,self.packs,'linux',set())),2)

    def test_shared_depot_and_virtual_dlc(self):
        self.info['game']['depots']['11']={'depotfromapp':'100'}
        self.info['game']['depots']['31']={'dlcappid':'30'}
        self.assertEqual(content_depots(self.info,self.info['dlc'][1]),{})
        self.assertEqual(len(build_plan(self.info,self.packs,'linux',set())),2)

    def test_wrong_app_duplicate_and_oversized_metadata_rejected(self):
        validate_info(self.info,10)
        for change in ('app','duplicate','many'):
            info=copy.deepcopy(self.info)
            if change=='app': info['game']['id']=11
            if change=='duplicate': info['dlc'].append(info['dlc'][0])
            if change=='many': info['dlc']=info['dlc']*101
            with self.assertRaises(ValueError):validate_info(info,10)

    def test_metadata_does_not_start_worker_without_saved_authentication(self):
        network=Mock()
        with patch('downloader.app_info.SteamSessionWallet') as wallet,patch('downloader.app_info.subprocess.run') as run:
            wallet.return_value.read.return_value=None
            with self.assertRaises(ValueError):fetch_app_info(network,10,'account')
            run.assert_not_called()

class ChecklistTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app=QApplication.instance() or QApplication([])

    def dialog(self):
        from downloader.download_dialog import DownloadDialog
        dialog=DownloadDialog(Mock())
        dialog.content_info=fixture(); dialog.pack_app=10
        dialog.rows=[Depot(i,1,b'manifest') for i in (11,12,13,21)]
        dialog.depot.addItem('Linux','linux'); dialog.update_dlc_list()
        return dialog

    def test_separate_dlc_is_checkable_and_bundled_dlc_explained(self):
        from PyQt6.QtCore import Qt
        dialog=self.dialog()
        self.assertTrue(dialog.dlc_list.item(0).flags() & Qt.ItemFlag.ItemIsUserCheckable)
        self.assertEqual(dialog.dlc_list.item(0).checkState(),Qt.CheckState.Unchecked)
        self.assertIn('Included in base-game files',dialog.dlc_list.item(1).text())
        self.assertFalse(dialog.dlc_list.item(1).flags() & Qt.ItemFlag.ItemIsEnabled)
        self.assertTrue(dialog.dlc_list.item(2).flags() & Qt.ItemFlag.ItemIsUserCheckable)
        dialog.close()

    def test_failed_manifest_keeps_selection_and_explains_failure(self):
        from PyQt6.QtCore import Qt
        dialog=self.dialog();dialog.pack_source='Hubcap'
        dialog.dlc_list.item(0).setCheckState(Qt.CheckState.Checked)
        dialog.content_info['dlc'][0].update(manifest_error='Provider unavailable',manifest_provider='Hubcap')
        dialog.update_dlc_list();item=dialog.dlc_list.item(0)
        self.assertIn('Manifest request failed',item.text())
        self.assertIn('Provider unavailable',item.toolTip())
        self.assertTrue(item.flags() & Qt.ItemFlag.ItemIsEnabled)
        self.assertEqual(item.checkState(),Qt.CheckState.Checked)
        dialog.close()

    def test_manifest_presence_does_not_claim_cdn_access(self):
        dialog=self.dialog()
        self.assertIn('Steam/CDN access has not been tested',dialog.dlc_list.item(0).toolTip())
        dialog.close()

    def test_owned_dlc_is_default_but_manual_selection_is_allowed(self):
        from PyQt6.QtCore import Qt
        dialog=self.dialog();dialog.default_dlc_selection={20,30}
        dialog.update_dlc_list()
        self.assertEqual(dialog.dlc_list.item(0).checkState(),Qt.CheckState.Checked)
        self.assertEqual(dialog.dlc_list.item(2).checkState(),Qt.CheckState.Unchecked)
        dialog.dlc_list.item(2).setCheckState(Qt.CheckState.Checked)
        dialog.update_dlc_list()
        self.assertEqual(dialog.dlc_list.item(2).checkState(),Qt.CheckState.Checked)
        dialog.close()

    def test_known_cdn_failure_is_visible_without_dropping_selection(self):
        from PyQt6.QtCore import Qt
        dialog=self.dialog();dialog.dlc_list.item(0).setCheckState(Qt.CheckState.Checked)
        dialog.content_info['_cdn_failures']={21:(1,'Steam rejected the CDN check')}
        dialog.update_dlc_list();item=dialog.dlc_list.item(0)
        self.assertIn('CDN access check failed',item.text())
        self.assertIn('Steam rejected',item.toolTip())
        self.assertEqual(item.checkState(),Qt.CheckState.Checked)
        dialog.close()

    def test_stale_dlc_manifest_is_identified_before_queueing(self):
        dialog=self.dialog();dialog.rows[-1]=Depot(21,2,b'manifest')
        dialog.update_dlc_list()
        self.assertIn('Manifest version mismatch',dialog.dlc_list.item(0).text())
        dialog.close()

    def test_intermediate_success_does_not_publish_and_final_success_does(self):
        import tempfile
        from pathlib import Path
        dialog=self.dialog()
        dialog.content_plan=[(10,dialog.rows[0],'Base'),(20,dialog.rows[-1],'Expansion')]
        dialog.process=Mock();dialog.process.readAllStandardOutput.return_value=b''
        dialog.read_output=Mock();dialog.download=Mock()
        dialog.output='Total downloaded: 100 bytes (200 bytes uncompressed) from 1 depots'
        dialog.started_at=0
        with tempfile.TemporaryDirectory() as directory:
            dialog.download_destination=Path(directory)
            dialog.download_staging=Path(directory)/'.playlite-download';dialog.download_staging.mkdir()
            (dialog.download_staging/'base.txt').write_text('base')
            dialog.finished(0)
            dialog.download.assert_called_once()
            self.assertFalse((Path(directory)/'base.txt').exists())
            self.assertFalse(dialog.open_folder.isEnabled())
            (dialog.download_staging/'dlc.txt').write_text('dlc')
            dialog.finished(0)
            self.assertTrue((Path(directory)/'base.txt').exists())
            self.assertTrue((Path(directory)/'dlc.txt').exists())
            self.assertFalse(dialog.download_staging.exists())
            self.assertTrue(dialog.open_folder.isEnabled())
        dialog.process=None;dialog.close()

    def test_failed_content_stops_queue_and_retains_staging(self):
        import tempfile
        from pathlib import Path
        dialog=self.dialog();dialog.content_plan=[(10,dialog.rows[0],'Base'),(20,dialog.rows[-1],'Expansion')]
        dialog.read_output=Mock();dialog.download=Mock();dialog.output='result: AccessDenied'
        with tempfile.TemporaryDirectory() as directory:
            dialog.download_destination=Path(directory)
            dialog.download_staging=Path(directory)/'.playlite-download';dialog.download_staging.mkdir()
            (dialog.download_staging/'partial.txt').write_text('partial')
            dialog.finished(1)
            dialog.download.assert_not_called()
            self.assertTrue((dialog.download_staging/'partial.txt').exists())
            self.assertFalse(dialog.open_folder.isEnabled())
        dialog.close()

class MetadataOutputTests(unittest.TestCase):
    def test_interactive_challenge_does_not_wait_for_input(self):
        import sys
        from downloader.app_info import metadata_output
        with self.assertRaisesRegex(ValueError,'requires authentication'):
            metadata_output([sys.executable,'-c',"print('STEAM GUARD! Please enter the auth code sent to email:',flush=True);import time;time.sleep(10)"],timeout=1)

    def test_output_limit_is_enforced(self):
        import sys
        from downloader.app_info import metadata_output
        with self.assertRaisesRegex(ValueError,'size limit'):
            metadata_output([sys.executable,'-c',"print('x'*10000)"],limit=100)

    def test_metadata_worker_has_finite_deadline(self):
        import sys
        from downloader.app_info import metadata_output
        with self.assertRaisesRegex(RuntimeError,'timed out'):
            metadata_output([sys.executable,'-c','import time;time.sleep(10)'],timeout=.1)

class ManifestPreparationTests(unittest.TestCase):
    def test_provider_error_identifies_blocked_dlc_and_alternative_actions(self):
        from downloader.app_info import prepare_content
        info=fixture();rows=[Depot(i,1,b'data') for i in (11,12,13)]
        fetch=Mock(side_effect=RuntimeError('The provider blocked the isolated HTTP worker with a browser challenge.'))
        with self.assertRaisesRegex(RuntimeError,'Hubcap.*Expansion.*App ID 20.*browser challenge.*Unselect'):
            prepare_content(info,rows,'linux',{20},'Hubcap',fetch)
        fetch.assert_called_once_with(20)

    def test_unselected_dlc_does_not_contact_its_provider(self):
        from downloader.app_info import prepare_content
        fetch=Mock();rows=[Depot(i,1,b'data') for i in (11,12,13)]
        plan=prepare_content(fixture(),rows,'linux',set(),'Hubcap',fetch)
        fetch.assert_not_called();self.assertEqual(len(plan),2)

class SteamPlanningTests(unittest.TestCase):
    def test_parent_dlc_keeps_steam_override_order(self):
        info=fixture();info['game']['depots']={'21':depot('linux',20),'11':depot(),'12':depot('linux')}
        rows=[Depot(i,1,b'manifest') for i in (11,12,21)]
        self.assertEqual([row.id for _,row,_ in build_plan(info,{10:rows},'linux',{20})],[21,11,12])

    def test_shared_source_properties_and_app_are_used(self):
        from downloader.app_info import resolve_shared
        info=fixture();info['game']['depots']={'11':{'depotfromapp':'100','config':{'language':'english'}}}
        shared={'game':{'id':100,'name':'Shared','depots':{'11':depot('linux')}},'dlc':[]}
        shared['game']['depots']['11']['config']['language']='german'
        fetch=Mock(return_value=shared);resolved=resolve_shared(info,fetch)
        rows=[Depot(11,1,b'manifest')]
        with self.assertRaisesRegex(ValueError,'No downloadable'):build_plan(resolved,{10:rows},'linux',set())
        self.assertEqual(build_plan(resolved,{10:rows},'linux',set(),'german')[0][0],100)
        fetch.assert_called_once_with(100)

    def test_shared_cycles_and_missing_source_fail_closed(self):
        from downloader.app_info import resolve_shared
        info=fixture();info['game']['depots']={'11':{'depotfromapp':'100'}}
        source={'game':{'id':100,'name':'Shared','depots':{'11':{'depotfromapp':'10'}}},'dlc':[]}
        with self.assertRaisesRegex(ValueError,'Cyclic'):resolve_shared(info,Mock(return_value=source))
        source['game']['depots']={}
        with self.assertRaisesRegex(ValueError,'did not return'):resolve_shared(info,Mock(return_value=source))

    def test_language_architecture_and_beta_choose_exact_manifests(self):
        info=fixture();info['game']['depots']={'11':depot('linux')}
        node=info['game']['depots']['11'];node['config'].update(language='german',osarch='32')
        node['manifests']['beta']={'gid':'2'}
        plan=build_plan(info,{10:[Depot(11,2,b'manifest')]},'linux',set(),'german','32','beta')
        self.assertEqual(plan[0][1].manifest,2)
        with self.assertRaisesRegex(ValueError,'does not match'):build_plan(info,{10:[Depot(11,1,b'manifest')]},'linux',set(),'german','32','beta')

    def test_missing_beta_override_uses_public_but_protected_beta_is_rejected(self):
        from downloader.app_info import manifest_id
        node=depot();self.assertEqual(manifest_id(node,'beta'),1)
        node['encryptedmanifests']={'beta':{'encrypted_gid':'secret'}}
        with self.assertRaisesRegex(ValueError,'requires a password'):manifest_id(node,'beta')

    def test_source_pack_is_fetched_for_missing_shared_depot(self):
        from downloader.app_info import prepare_content
        info=fixture();info['game']['depots']={'11':dict(depot('linux'),depotfromapp='100',_source_app=100)}
        fetch=Mock(return_value=[Depot(11,1,b'manifest')])
        self.assertEqual(prepare_content(info,[],'linux',set(),'Hubcap',fetch)[0][0],100)
        fetch.assert_called_once_with(100)

    def test_shared_depot_discovers_its_source_dlc_requirement(self):
        from downloader.app_info import resolve_shared
        info=fixture();info['game']['depots']={'11':{'depotfromapp':'100'}}
        shared={'game':{'id':100,'name':'Shared','depots':{'11':depot('linux',200)}},'dlc':[{'id':200,'name':'Shared expansion','owned':True,'depots':{}}]}
        fetch=Mock(return_value=shared);resolved=resolve_shared(info,fetch)
        self.assertIn(200,{row['id'] for row in resolved['dlc']})
        self.assertEqual(build_plan(resolved,{10:[Depot(11,1,b'manifest')]},'linux',{200})[0][0],100)
        fetch.assert_called_once_with(100)

class PackageSelectionTests(unittest.TestCase):
    def test_package_metadata_excludes_unpublished_depots_without_name_guessing(self):
        info=fixture();info['game']['package_depots']=[11,12]
        info['game']['depots']['99']=depot('linux')
        rows=[Depot(i,1,b'manifest') for i in (11,12)]
        self.assertEqual([row.id for _,row,_ in build_plan(info,{10:rows},'linux',set())],[11,12])

    def test_selected_dlc_is_allowed_outside_base_package(self):
        info=fixture();info['game']['owned']=False;info['game']['package_depots']=[11,12]
        rows=[Depot(i,1,b'manifest') for i in (11,12,21)]
        self.assertEqual([row.id for _,row,_ in build_plan(info,{10:rows},'linux',{20})],[11,12,21])

    def test_bad_package_depot_ids_are_rejected(self):
        info=fixture();info['game']['package_depots']=['11']
        with self.assertRaisesRegex(ValueError,'package depot'):validate_info(info,10)

    def test_public_package_discovery_is_bounded_and_validates_ids(self):
        import json
        from downloader.app_info import store_package_ids
        with patch('downloader.providers.Transport') as transport:
            transport.return_value.request.return_value=json.dumps({'10':{'success':True,'data':{'packages':[123,123,-1,'secret',True]}}}).encode()
            self.assertEqual(store_package_ids(Mock(),10),[123])
            transport.return_value.request.return_value=json.dumps({'10':{'success':True,'data':{'packages':list(range(100))}}}).encode()
            self.assertEqual(store_package_ids(Mock(),10),[])
