"""Track export menu uses cached per-target admission without device I/O."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QMainWindow

from desktop.devices import DeviceFacts
from desktop.track_menu import TrackMenuMixin


class TrackMenuAdmission(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app = QApplication.instance() or QApplication([])

    def test_another_format_only_disables_its_target_and_preserves_export_capture(self):
        class Window(QMainWindow, TrackMenuMixin):
            def _device_operation_owned(self, target=None):
                return target in (None, '/fixture/formatting')

            def _begin_cdj_export(self, *args, **kwargs):self.export = (args, kwargs)

        window = Window()
        self.addCleanup(window.deleteLater)
        song = SimpleNamespace(imported=False)
        window.settings = {}
        window.lib = SimpleNamespace(tracks_by_id={'song': song})
        window.player_bar = SimpleNamespace(current_track=lambda: 'song')
        window._device_profiles = {
            path: SimpleNamespace(kind='generic_usb', cdj_export=True)
            for path in ('/fixture/formatting', '/fixture/ready')}
        window._device_mount_tokens = {'/fixture/formatting': 11, '/fixture/ready': 22}
        window._shutting_down = window._state_maintenance = False
        with patch('desktop.track_menu.device_facts',
                   return_value=DeviceFacts(volume_uuid='fixture-volume', writable=True)) as facts:
            menu = window._track_menu()
        self.addCleanup(menu.deleteLater)
        self.assertTrue(all(call.kwargs == {'cached_only': True} for call in facts.call_args_list))
        exports = next(action.menu() for action in menu.actions() if action.text() == 'Export to USB')
        formatting, ready = exports.actions()
        self.assertFalse(formatting.isEnabled())
        self.assertTrue(ready.isEnabled())
        ready.trigger()
        self.assertEqual(window.export, (('/fixture/ready',), {
            'standalone': (song,), 'from_drop': True, 'drop_target': (22, 'fixture-volume')}))
