import unittest
from unittest.mock import Mock
import test_worker
from unittest.mock import patch
from downloader.dlc_names import resolve_names
from downloader.providers import Depot

class DlcNameTests(unittest.TestCase):
    def setUp(self):
        self.cache=Mock();self.cache.read.return_value=None
        self.resolver=patch('downloader.dlc_names.NameCache',return_value=self.cache)
        self.resolver.start();self.addCleanup(self.resolver.stop)

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
        resolve_names(info,[],search);self.assertEqual(search.details.call_count+search.secondary_name.call_count,8)

    def test_secondary_source_handles_any_app_id(self):
        info=self.info();info['dlc'][0]['id']=12345
        search=Mock();search.details.side_effect=ValueError('Missing')
        search.secondary_name.return_value='Example Expansion'
        self.assertEqual(resolve_names(info,[],search)['dlc'][0]['name'],'Example Expansion')
        search.secondary_name.assert_called_once_with(12345)

    def test_cached_title_avoids_requests(self):
        self.cache.read.return_value='Cached Expansion';search=Mock()
        self.assertEqual(resolve_names(self.info(),[],search)['dlc'][0]['name'],'Cached Expansion')
        search.details.assert_not_called();search.secondary_name.assert_not_called()

    def test_cached_failure_avoids_requests(self):
        self.cache.read.return_value='';search=Mock()
        self.assertEqual(resolve_names(self.info(),[],search)['dlc'][0]['name'],'')
        search.details.assert_not_called();search.secondary_name.assert_not_called()

    def test_source_failures_are_cached(self):
        search=Mock();search.details.side_effect=ValueError('Missing')
        search.secondary_name.side_effect=RuntimeError('Browser challenge')
        resolve_names(self.info(),[],search)
        self.cache.save.assert_called_once_with(686420,'')
