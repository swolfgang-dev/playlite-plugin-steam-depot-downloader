import unittest
from unittest.mock import Mock
import test_worker
from unittest.mock import patch
from downloader.dlc_names import resolve_names
from downloader.providers import Depot

class DlcNameTests(unittest.TestCase):
    def setUp(self):
        self.overrides=patch('downloader.dlc_names.NAME_OVERRIDES',{})
        self.overrides.start();self.addCleanup(self.overrides.stop)

    def info(self):
        return {'game':{'id':597220,'name':'West of Loathing','depots':{'686420':{'dlcappid':'686420','manifests':{'public':{'gid':'1'}}}}},'dlc':[{'id':686420,'name':'','depots':{}}]}

    def test_store_title_fills_empty_name(self):
        search=Mock();search.details.return_value={'name':'West of Loathing: Horse Armor'}
        info=resolve_names(self.info(),[],search)
        self.assertEqual(info['dlc'][0]['name'],'West of Loathing: Horse Armor')

    def test_hidden_store_page_uses_matching_provider_depot_name(self):
        search=Mock();search.details.side_effect=ValueError('No store page')
        rows=[Depot(686420,1,b'data',name='DLC 686420 West of Loathing - Horse Armor (686420) Depot')]
        info=resolve_names(self.info(),rows,search)
        self.assertEqual(info['dlc'][0]['name'],'West of Loathing - Horse Armor')

    def test_existing_title_does_not_request_store_metadata(self):
        info=self.info();info['dlc'][0]['name']='Known DLC';search=Mock()
        resolve_names(info,[],search);search.details.assert_not_called()

    def test_unrelated_and_numeric_names_are_not_used(self):
        search=Mock();search.details.side_effect=ValueError('Missing')
        rows=[Depot(99,1,b'data',name='Unrelated game'),Depot(686420,1,b'data',name='DLC 686420')]
        self.assertEqual(resolve_names(self.info(),rows,search)['dlc'][0]['name'],'')

    def test_missing_name_requests_are_bounded(self):
        info=self.info();info['dlc']=[{'id':i,'name':'','depots':{}} for i in range(1,100)]
        search=Mock();search.details.side_effect=ValueError('Missing')
        resolve_names(info,[],search);self.assertEqual(search.details.call_count,8)

class HiddenDlcTitleTests(unittest.TestCase):
    def test_real_horse_armor_entry_resolves_without_unavailable_store_page(self):
        info={'game':{'id':597220,'depots':{}},'dlc':[{'id':686420,'name':'','depots':{}}]}
        search=Mock();search.details.side_effect=ValueError('No store page')
        resolve_names(info,[],search)
        self.assertEqual(info['dlc'][0]['name'],'West of Loathing: Horse Armor')
        search.details.assert_not_called()

    def test_override_does_not_apply_to_an_unrelated_game(self):
        info={'game':{'id':99,'depots':{}},'dlc':[{'id':686420,'name':'','depots':{}}]}
        search=Mock();search.details.side_effect=ValueError('No store page')
        self.assertEqual(resolve_names(info,[],search)['dlc'][0]['name'],'')
