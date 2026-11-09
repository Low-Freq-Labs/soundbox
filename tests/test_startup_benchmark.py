"""The benchmark preparer must not leave live media/bookmarks/source authority."""
import json
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest

from scripts.benchmark_startup import prepare, validate_result
from soundbox import libcache


class StartupMetadata(unittest.TestCase):
    def test_eager_readiness_cannot_pass_with_missing_runtime_or_device_work(self):
        result = dict(painted=True, search_matched=True, rows=12, native=True,
                      selected=True, settings_ready=True, artists_ready=True, artists_rows=12, collection_rows=12,
                      desktop_runtime_missing=[], desktop_runtime_errors=[], provider_diagnostic='',
                      device_surfaces=[], device_backends=[], stock_converter_loaded=False)
        validate_result(result, require_desktop_ready=True)
        for key, value in (('settings_ready', False), ('artists_rows', 11),
                           ('desktop_runtime_missing', ['numpy']),
                           ('desktop_runtime_errors', ['unavailable']),
                           ('provider_diagnostic', 'provider unavailable'),
                           ('device_backends', ['soundbox.stock_ipod'])):
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                validate_result(dict(result, **{key: value}), require_desktop_ready=True)

    def test_old_baseline_reports_remain_usable_without_claiming_eager_readiness(self):
        result = dict(painted=True, search_matched=True, rows=12)
        validate_result(result)
        with self.assertRaises(RuntimeError):
            validate_result(result, require_desktop_ready=True)

    def test_desktop_import_does_not_initialize_stock_image_conversion(self):
        code = (
            'from PySide6.QtCore import QStandardPaths; '
            'QStandardPaths.setTestModeEnabled(True); '
            'import desktop.app; import sys; '
            'assert "clickwheel.ipod.artworkdb_writer.rgb565" not in sys.modules; '
            'assert "numpy" not in sys.modules'
        )
        result = subprocess.run([sys.executable, '-c', code],
                                cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_flat_color_control_preserves_native_big_endian_heights(self):
        from scripts.profile_player import flat_red_pwv5
        self.assertEqual(flat_red_pwv5(bytes.fromhex('1234a47c')), bytes.fromhex('e034e07c'))
        with self.assertRaises(ValueError):
            flat_red_pwv5(b'x')

    def test_synthetic_default_and_copied_metadata_are_disposable(self):
        with tempfile.TemporaryDirectory() as area:
            base = Path(area)
            source, target = base / 'source', base / 'target'
            source.mkdir(); target.mkdir()
            prepare(source, None, 12)
            state = json.loads((source / 'library.json').read_text())
            state.update(imported={'track': {'path': '/live/source.wav', 'bookmark': 'opaque'}},
                         last_xml='/live/library.xml')
            state['settings']['rekordbox_db_path'] = '/live/master.db'
            (source / 'library.json').write_text(json.dumps(state))
            prepare(target, source, 12)
            result = json.loads((target / 'library.json').read_text())
            self.assertEqual(result['imported'], {})
            self.assertEqual(result['last_xml'], '')
            self.assertNotIn('rekordbox_db_path', result['settings'])
            lib = libcache.load(target / 'libcache.json')
            self.assertEqual(lib.source_kind, 'cache')
            self.assertEqual(len(lib.tracks_by_id), 12)
            self.assertTrue(all(t.path.is_relative_to(target.resolve()) and not t.source_analysis_path
                                for t in lib.tracks_by_id.values()))
