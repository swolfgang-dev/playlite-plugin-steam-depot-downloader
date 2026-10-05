import importlib.util
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch
from PyQt6.QtWidgets import QApplication

if 'downloader' not in sys.modules:
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location('downloader', root / '__init__.py', submodule_search_locations=[str(root)])
    module = importlib.util.module_from_spec(spec)
    sys.modules['downloader'] = module
    spec.loader.exec_module(module)
from downloader.plugin import Plugin

class UiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait(self, plugin):
        end = time.monotonic() + 3
        while plugin.busy and time.monotonic() < end:
            self.app.processEvents()
            time.sleep(.01)
        self.assertFalse(plugin.busy)
        self.app.processEvents()

    def test_shutdown_disconnects_an_idle_session(self):
        plugin = Plugin()
        with patch.object(plugin.network, 'disconnect') as disconnect:
            plugin.shutdown()
            disconnect.assert_called_once()
        self.assertTrue(plugin.network.cancelled.is_set())

    def test_shutdown_cancels_inflight_connection(self):
        plugin = Plugin()
        plugin.busy = True
        with patch.object(plugin.network, 'disconnect') as disconnect:
            plugin.shutdown()
            disconnect.assert_not_called()
        self.assertTrue(plugin.network.cancelled.is_set())

    def test_worker_failure_restores_controls_and_reports_error(self):
        plugin = Plugin()
        widget = plugin.create_settings()
        def fail(progress):
            raise RuntimeError('Authentication rejected')
        plugin.start(widget, fail, cancellable=True)
        self.wait(plugin)
        self.assertEqual(widget.status.text(), 'Authentication rejected')
        self.assertTrue(all(button.isEnabled() for button in widget.buttons))
        self.assertEqual(widget.buttons[-1].text(), 'Disconnect')
        self.assertFalse(plugin.jobs)

    def test_cancel_is_available_while_connection_runs(self):
        plugin = Plugin()
        widget = plugin.create_settings()
        def operation(progress):
            plugin.network.cancelled.wait(2)
            plugin.network.check_cancelled()
            return 'Unexpected success'
        plugin.start(widget, operation, cancellable=True)
        self.assertTrue(widget.buttons[-1].isEnabled())
        self.assertEqual(widget.buttons[-1].text(), 'Cancel connection')
        self.assertTrue(all(not button.isEnabled() for button in widget.buttons[:-1]))
        widget.buttons[-1].click()
        self.wait(plugin)
        self.assertIn('cancelled', widget.status.text())
        self.assertTrue(all(button.isEnabled() for button in widget.buttons))

if __name__ == '__main__': unittest.main()

class SharedRuntimeTests(unittest.TestCase):
    def test_menu_and_settings_plugin_instances_share_connection(self):
        app=QApplication.instance() or QApplication([])
        first,second=Plugin(),Plugin()
        self.assertIs(first.network,second.network)
