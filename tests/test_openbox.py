import importlib.util
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET


class OpenboxTests(unittest.TestCase):
    def test_popup_fix_preserves_other_bindings_and_is_idempotent(self):
        spec=importlib.util.spec_from_file_location('openbox_bridge',Path(__file__).resolve().parents[1]/'tools/steam/bridge.py')
        bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)
        namespace='http://openbox.org/3.4/rc'
        ns={'o':namespace}
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'source.xml';destination=Path(directory)/'fixed.xml'
            source.write_text('''<openbox_config xmlns="http://openbox.org/3.4/rc"><mouse>
              <context name="Client"><mousebind button="Left" action="Press">
                <action name="Focus"/><action name="Raise"/>
              </mousebind></context>
              <context name="Titlebar"><mousebind button="Left" action="Press">
                <action name="Focus"/>
              </mousebind></context>
            </mouse></openbox_config>''')
            bridge.prepare_openbox_config(source,destination)
            tree=ET.parse(destination)
            binding=tree.find('.//o:context[@name="Client"]/o:mousebind',ns)
            self.assertEqual([a.get('name') for a in binding],['If','Raise'])
            self.assertEqual(binding.find('o:action/o:query/o:focused',ns).text,'no')
            self.assertEqual(binding.find('o:action/o:then/o:action',ns).get('name'),'Focus')
            self.assertIsNotNone(tree.find('.//o:context[@name="Titlebar"]/o:mousebind/o:action[@name="Focus"]',ns))
            first=destination.read_bytes()
            bridge.prepare_openbox_config(destination,destination)
            self.assertEqual(destination.read_bytes(),first)
