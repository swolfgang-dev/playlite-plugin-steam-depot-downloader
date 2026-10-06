import json
import unittest
from unittest.mock import Mock
import test_worker
from downloader.game_search import GameSearch,RESULT_LIMIT,MAX_COVER_BYTES

class SearchTests(unittest.TestCase):
    def test_search_is_bounded_before_covers_are_downloaded(self):
        transport=Mock()
        transport.request.return_value=json.dumps({'items':[{'id':i,'name':f'Game {i}'} for i in range(1,100)]}).encode()
        rows=GameSearch(transport).search('Baba Is You')
        self.assertEqual(len(rows),RESULT_LIMIT)
        transport.request.assert_called_once()
        self.assertIn('term=Baba+Is+You',transport.request.call_args.args[0])

    def test_short_queries_and_numeric_ids_do_not_search(self):
        transport=Mock();search=GameSearch(transport)
        for query in ('','ba','736260'):
            self.assertEqual(search.search(query),[])
        transport.request.assert_not_called()

    def test_cover_cache_avoids_repeat_downloads(self):
        transport=Mock();transport.request.return_value=b'cover'
        search=GameSearch(transport)
        self.assertEqual(search.cover(736260),b'cover')
        self.assertEqual(search.cover(736260),b'cover')
        transport.request.assert_called_once()

    def test_missing_or_oversized_cover_does_not_block_game_selection(self):
        transport=Mock();transport.request.return_value=b'x'*(MAX_COVER_BYTES+1)
        self.assertEqual(GameSearch(transport).cover(736260),b'')
        transport.request.side_effect=RuntimeError('Missing')
        self.assertEqual(GameSearch(transport).cover(736260),b'')

    def test_duplicate_and_invalid_ids_are_ignored(self):
        transport=Mock()
        transport.request.return_value=json.dumps({'items':[{'id':1,'name':'Game'},{'id':1,'name':'Game'},{'id':True,'name':'bad'},{'id':-1,'name':'bad'}]}).encode()
        self.assertEqual(GameSearch(transport).search('Game'),[{'id':1,'name':'Game'}])

    def test_missing_library_art_falls_back_to_header(self):
        transport=Mock()
        transport.request.side_effect=[RuntimeError('Missing cover'),RuntimeError('Missing capsule'),b'header']
        self.assertEqual(GameSearch(transport).cover(123),b'header')
        self.assertIn('header.jpg',transport.request.call_args.args[0])
        self.assertEqual(transport.request.call_count,3)

    def test_capsule_is_preferred_to_header(self):
        transport=Mock()
        transport.request.side_effect=[RuntimeError('Missing cover'),b'capsule']
        self.assertEqual(GameSearch(transport).cover(123),b'capsule')
        self.assertIn('capsule_231x87.jpg',transport.request.call_args.args[0])
        self.assertEqual(transport.request.call_count,2)
