"""Ordinary runtime warming imports code without admitting content work."""
import unittest
from unittest import mock

from soundbox import desktop_runtime


class DesktopRuntime(unittest.TestCase):
    def setUp(self):
        desktop_runtime.prepare_desktop_runtime.cache_clear()
        self.addCleanup(desktop_runtime.prepare_desktop_runtime.cache_clear)

    def test_warms_fixed_ordinary_dependencies_once(self):
        with mock.patch.object(desktop_runtime.importlib, 'import_module') as imports:
            self.assertEqual(desktop_runtime.prepare_desktop_runtime(), ())
            self.assertEqual(desktop_runtime.prepare_desktop_runtime(), ())
        self.assertEqual(imports.call_args_list, [mock.call(name) for name in
            ('numpy', 'scipy.signal', 'scipy.ndimage', 'soundfile', 'zstandard')])

    def test_broken_dependency_reports_failure_and_keeps_other_services_ready(self):
        def load(name):
            if name == 'soundfile':
                raise OSError('synthetic missing decoder')
            return object()

        with mock.patch.object(desktop_runtime.importlib, 'import_module', side_effect=load) as imports:
            diagnostics = desktop_runtime.prepare_desktop_runtime()
        self.assertEqual(len(diagnostics), 1)
        self.assertIn('soundfile', diagnostics[0])
        self.assertIn('synthetic missing decoder', diagnostics[0])
        self.assertEqual(imports.call_args_list[-1], mock.call('zstandard'))

    def test_real_warmup_starts_no_decode_or_network_work(self):
        import soundfile
        with mock.patch('socket.socket.connect', side_effect=AssertionError('network')), \
                mock.patch.object(soundfile, 'SoundFile', side_effect=AssertionError('decode')):
            self.assertEqual(desktop_runtime.prepare_desktop_runtime(), ())
