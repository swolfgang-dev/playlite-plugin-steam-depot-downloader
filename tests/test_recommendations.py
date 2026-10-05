import unittest
from unittest.mock import patch,Mock
import test_worker
from downloader.network import Network
from downloader.settings import Preferences
from downloader.recommendations import recommended_servers

class RecommendationTests(unittest.TestCase):
    def test_recommendation_order_is_preserved(self):
        with patch('downloader.recommendations.api',return_value=[{'hostname':'ca2.nordvpn.com','status':'online'},{'hostname':'ca1.nordvpn.com','status':'online'},{'hostname':'bad.example','status':'online'}]) as api:
            self.assertEqual(recommended_servers(Preferences()),['ca2.nordvpn.com','ca1.nordvpn.com'])
            self.assertEqual(api.call_args.args[1]['filters[servers_technologies][identifier]'],'openvpn_udp')

    def test_country_and_protocol_filter(self):
        with patch('downloader.recommendations.api',side_effect=[[{'id':38,'name':'Canada'}],[{'hostname':'ca1.nordvpn.com','status':'online'}]]) as api:
            recommended_servers(Preferences('Canada','tcp'))
            self.assertEqual(api.call_args.args[1]['filters[country_id]'],38)
            self.assertEqual(api.call_args.args[1]['filters[servers_technologies][identifier]'],'openvpn_tcp')

    def test_retry_skips_failed_and_incompatible_servers(self):
        network=Network();network.compatible_servers={'ca1.nordvpn.com','ca2.nordvpn.com'}
        network.attempted_servers={'ca1.nordvpn.com'}
        with patch('downloader.network.recommended_servers',return_value=['ca3.nordvpn.com','ca1.nordvpn.com','ca2.nordvpn.com']):
            self.assertEqual(network.choose_server(Preferences(),Mock()),'ca2.nordvpn.com')

    def test_api_unavailable_falls_back_without_credentials(self):
        with patch('downloader.network.recommended_servers',side_effect=OSError('offline')):
            self.assertIsNone(Network().choose_server(Preferences(),Mock()))
