import unittest
from unittest.mock import Mock
import test_worker
from test_app_info import fixture,depot
from downloader.manifest_resolver import ManifestResolver
from downloader.providers import Depot,SOURCES

class FallbackTests(unittest.TestCase):
    def setUp(self):
        self.info=fixture();self.base=[Depot(11,1,b'data')]

    def test_missing_base_depot_is_filled_by_another_provider(self):
        fetch=Mock(side_effect=lambda source,app:[Depot(12,1,b'data')] if source=='Hubcap' else [])
        resolver=ManifestResolver('Luie',fetch)
        plan=resolver.prepare(self.info,self.base,'linux',set())
        self.assertEqual([row.id for _,row,_ in plan],[11,12])
        self.assertEqual(resolver.used[12],'Hubcap')
        self.assertNotIn(('Luie',10),[call.args for call in fetch.call_args_list])
        self.assertNotIn(('Sushi',10),[call.args for call in fetch.call_args_list])

    def test_stale_manifest_does_not_prevent_later_exact_match(self):
        fetch=Mock(side_effect=lambda source,app:[Depot(12,2 if source=='Hubcap' else 1,b'data')])
        resolver=ManifestResolver('Luie',fetch)
        plan=resolver.prepare(self.info,self.base,'linux',set())
        self.assertEqual(plan[1][1].manifest,1)
        self.assertEqual(resolver.used[12],'Sushi')

    def test_failures_continue_through_every_provider_then_report_exhaustion(self):
        fetch=Mock(side_effect=RuntimeError('Provider rejected request'))
        resolver=ManifestResolver('Luie',fetch)
        with self.assertRaisesRegex(ValueError,'Checked Luie, Hubcap, Sushi, Ryuu'):
            resolver.prepare(self.info,self.base,'linux',set())
        self.assertEqual([call.args[0] for call in fetch.call_args_list],['Hubcap','Sushi','Ryuu'])
        self.assertIn('Provider rejected request',self.info['game']['manifest_error'])

    def test_selected_dlc_can_be_found_in_a_separate_fallback_pack(self):
        base=[Depot(i,1,b'data') for i in (11,12)]
        fetch=Mock(side_effect=lambda source,app:[Depot(21,1,b'data')] if source=='Sushi' and app==20 else [])
        resolver=ManifestResolver('Luie',fetch)
        plan=resolver.prepare(self.info,base,'linux',{20})
        self.assertEqual([row.id for _,row,_ in plan],[11,12,21])
        self.assertEqual(plan[-1][0],10)
        self.assertEqual(resolver.used[21],'Sushi')

    def test_each_provider_app_pair_is_requested_once(self):
        info=fixture();info['game']['depots']['14']=depot('linux')
        fetch=Mock(side_effect=lambda source,app:[Depot(i,1,b'data') for i in (12,14)] if source=='Hubcap' else [])
        resolver=ManifestResolver('Luie',fetch)
        resolver.prepare(info,self.base,'linux',set())
        fetch.assert_called_once_with('Hubcap',10)

    def test_initial_pack_tries_all_providers_when_preferred_is_blocked(self):
        fetch=Mock(side_effect=lambda source,app:[Depot(11,1,b'data')] if source=='Ryuu' else [])
        resolver=ManifestResolver('Luie',fetch)
        self.assertTrue(resolver.first_pack(10))
        self.assertEqual(resolver.first_source,'Ryuu')
        self.assertEqual(fetch.call_count,len(SOURCES))

    def test_request_budget_stops_excessive_fetching(self):
        resolver=ManifestResolver('Luie',Mock(return_value=[]),limit=1)
        with self.assertRaisesRegex(ValueError,'request limit'):resolver.first_pack(10)
