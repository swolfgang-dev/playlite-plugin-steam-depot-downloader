import importlib.util
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('retail_selection',Path(__file__).resolve().parents[1]/'tools/steam/retail_selection.py')
selection=importlib.util.module_from_spec(spec);spec.loader.exec_module(selection)

class RetailSelectionTests(unittest.TestCase):
    def test_package_union_requires_complete_game_membership(self):
        records={'1':{'appids':{'0':'10'},'depotids':{'0':'11'}},'2':{'appids':{'0':'10','1':'20'},'depotids':{'0':'12'}}}
        self.assertEqual(selection.package_depots(10,[1,2],records),{11,12})
        with self.assertRaises(RuntimeError):selection.package_depots(10,[1,3],records)
        with self.assertRaises(RuntimeError):selection.package_depots(99,[1],records)

    def fixture(self,home):
        script=home/'.steam/steam/config/stplug-in/10.lua';script.parent.mkdir(parents=True)
        script.write_text('addappid(10)\naddappid(11,1,"'+'a'*64+'")\naddappid(12,1,"'+'b'*64+'")\nsetManifestid(12,"123")\n')
        cache=home/'.config/SLSsteam/cache';cache.mkdir(parents=True)
        for depot,app in [(11,10),(12,10),(99,90)]:
            (cache/f'depotkey_{depot}.yaml').write_text(f'appId: {app}\ndepotId: {depot}\nkey: private\n')
        return script,cache

    def test_exclusion_preserves_other_games_and_restores_when_membership_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory);script,cache=self.fixture(home);original=script.read_text()
            self.assertEqual(selection.restrict_depots(home,10,{11},{"11":{},"12":{}}),{'excluded':[12],'changed':True})
            self.assertNotIn('addappid(12',script.read_text())
            self.assertFalse((cache/'depotkey_12.yaml').exists())
            self.assertTrue((cache/'depotkey_99.yaml').exists())
            self.assertEqual(selection.restrict_depots(home,10,{11},{"11":{},"12":{}}),{'excluded':[12],'changed':False})
            result=selection.restrict_depots(home,10,{11,12},{"11":{},"12":{}})
            self.assertTrue(result['changed']);self.assertEqual(script.read_text(),original)
            self.assertTrue((cache/'depotkey_12.yaml').exists())

    def test_compound_script_is_rejected_without_partial_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory);script,cache=self.fixture(home)
            script.write_text('addappid(12,1,"'+'b'*64+'"); addappid(11)\n')
            original=script.read_text()
            with self.assertRaises(RuntimeError):selection.restrict_depots(home,10,{11},{"11":{},"12":{}})
            self.assertEqual(script.read_text(),original);self.assertTrue((cache/'depotkey_12.yaml').exists())

    def test_app_shared_and_dlc_ids_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory);script,cache=self.fixture(home)
            for identifier in (10,20,30):
                (cache/f'depotkey_{identifier}.yaml').write_text(f'appId: 10\ndepotId: {identifier}\nkey: private\n')
            script.write_text(script.read_text()+'addappid(20)\naddappid(30)\n')
            selection.restrict_depots(home,10,{11},{'11':{},'12':{},'20':{'depotfromapp':'99'},'30':{'dlcappid':'30'}})
            for identifier in (10,20,30):
                self.assertTrue((cache/f'depotkey_{identifier}.yaml').exists())
                self.assertIn(f'addappid({identifier})',script.read_text())
