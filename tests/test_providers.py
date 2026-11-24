"""Profile admission, failure isolation and actual provider-free core execution."""
from __future__ import annotations

import copy
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from soundbox import providers
from scripts.build_composition import composition

ROOT = Path(__file__).resolve().parents[1]


class ProviderComposition(unittest.TestCase):
    def test_admitted_search_code_is_prepared_before_first_use(self):
        with mock.patch.object(providers.importlib, 'import_module',
                               wraps=providers.importlib.import_module) as imports:
            result = providers.resolve(composition('personal')['manifest'])
        self.assertIsNotNone(result.artwork)
        for name in ('yt_dlp', 'yt_dlp.networking.common', 'yt_dlp.extractor.soundcloud'):
            self.assertIn(mock.call(name), imports.call_args_list)
        with mock.patch.object(providers.importlib.util, 'find_spec', return_value=None):
            self.assertEqual(providers.resolve(composition('personal')['manifest']).actions, ())
    def test_public_never_imports_optional_packages(self):
        with mock.patch.object(providers.importlib, "import_module",
                               side_effect=AssertionError("optional import")):
            public = providers.resolve(composition("public")["manifest"])
        self.assertIsNone(public.artwork)
        self.assertEqual(public.actions, ())
        self.assertEqual(set(composition("public")["excludes"]),
                         {"yt_dlp", "soundbox_personal", "research", "tests",
                          "sqlalchemy.testing", "sqlalchemy.ext.mypy", "mypy", "ast_serialize", "Crypto.SelfTest",
                          "pyrekordbox.__main__", "sqlalchemy.dialects.mssql", "sqlalchemy.dialects.mysql",
                          "sqlalchemy.dialects.oracle", "sqlalchemy.dialects.postgresql"})

    def test_missing_or_broken_provider_preserves_core(self):
        manifest = composition("personal")["manifest"]
        for error in (ModuleNotFoundError("yt_dlp"), RuntimeError("bad initialization")):
            with self.subTest(error=error), mock.patch.object(
                    providers.importlib, "import_module", side_effect=error):
                result = providers.resolve(manifest)
                self.assertIsNone(result.artwork)
                self.assertIsNone(result.artists)
                self.assertEqual(result.actions, ())
                self.assertIn(str(error), result.diagnostic)

    def test_incompatible_and_unselected_modules_never_import(self):
        personal = composition("personal")["manifest"]
        variants = []
        for key, value in (("id", "arbitrary.module"), ("core_api", 999),
                           ("actions", ["device.write"]), ("version", "9")):
            variant = copy.deepcopy(personal)
            variant["modules"][0][key] = value
            variants.append(variant)
        variants.append({**personal, "profile": "public"})
        for manifest in variants:
            with self.subTest(manifest=manifest), mock.patch.object(
                    providers.importlib, "import_module",
                    side_effect=AssertionError("optional import")) as imports:
                result = providers.resolve(manifest)
                self.assertEqual(result.actions, ())
                imports.assert_not_called()

    def test_personal_registers_existing_functions_without_network(self):
        from soundbox_personal import discovery
        from soundbox_personal.connection import Keychain
        with mock.patch('socket.socket.connect', side_effect=AssertionError('network')), \
                mock.patch.object(Keychain, 'read', side_effect=AssertionError('credentials')):
            result = providers.resolve(composition("personal")["manifest"])
        self.assertIs(result.artwork, discovery)
        self.assertIs(result.artists, discovery)
        self.assertEqual(result.actions, providers.ACTIONS)

    def test_frozen_public_ignores_personal_environment(self):
        with tempfile.TemporaryDirectory() as area:
            shutil.copy2(providers.profile_path("public"), Path(area) / "soundbox-build.json")
            providers.runtime_providers.cache_clear()
            try:
                with mock.patch.object(sys, "frozen", True, create=True), mock.patch.object(
                        sys, "_MEIPASS", area, create=True), mock.patch.dict(
                            os.environ, {"SOUNDBOX_PROFILE": "personal"}):
                    self.assertEqual(providers.runtime_providers().profile, "public")
            finally:
                providers.runtime_providers.cache_clear()

    def test_core_runs_with_personal_source_absent_and_network_denied(self):
        # A separate source tree contains no personal package. -I excludes cwd
        # and PYTHONPATH; an import blocker also makes installed yt_dlp absent.
        with tempfile.TemporaryDirectory(prefix="sb-public-core-") as area:
            root = Path(area)
            for name in ("soundbox", "desktop", "profiles", "assets"):
                shutil.copytree(ROOT / name, root / name,
                                ignore=shutil.ignore_patterns("__pycache__"))
            script = r'''
import importlib.abc
import socket
import sys
from pathlib import Path
from unittest import mock
sys.path.insert(0, sys.argv[1])
root = Path(sys.argv[1])
class NoPersonal(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in ('soundbox_personal', 'yt_dlp'):
            raise ModuleNotFoundError(fullname)
sys.meta_path.insert(0, NoPersonal())
def denied(*args, **kwargs):
    raise AssertionError('network attempted')
socket.socket.connect = denied
socket.create_connection = denied
from PySide6.QtCore import QStandardPaths
QStandardPaths.setTestModeEnabled(True)
from soundbox import providers, rekordbox_db, sync
from soundbox.library import Library, Playlist, Track
from desktop.app import MainWindow, make_app
with mock.patch.object(QStandardPaths, 'writableLocation', return_value=str(root / 'state')), \
     mock.patch.object(rekordbox_db, 'db_available', return_value=False), \
     mock.patch.object(MainWindow, '_refresh_devices', side_effect=AssertionError('device probe')):
    # Suppress before construction and throughout callback delivery.
    with mock.patch.object(MainWindow, '_refresh_devices', return_value=None):
        app = make_app()
        win = MainWindow()
        assert win.providers.profile == 'public'
        assert win.providers.artwork is None
        before = dict(win.art_cache.index)
        for command in ('_maybe_autostart_art_fill', '_start_art_fill', '_rescan_covers', '_suggest_artists'):
            getattr(win, command)()
        win._art_fetch_one('1')
        win._art_from_url('1')
        assert win.art_cache.index == before
        assert win._art_worker is None
        from desktop.artpicker import ArtPickerDialog
        picker = ArtPickerDialog('Artist', 'Track')
        assert picker._worker is None and picker.url_edit.isHidden()
        picker._use_url()
        assert picker.choice is None
        picker.shutdown()
        from soundbox.artwork import ArtResult
        from PIL import Image
        import io
        image = io.BytesIO()
        Image.new('RGB', (16,16), 'red').save(image, 'PNG')
        win.art_cache.store_ok('1', ArtResult(image.getvalue(), 'image/png', 1.0, 'file:test', 'local'))
        assert win.art_cache.canonical_image_path('1').exists()
        win.art_cache.block('2')
        (root / 'sources').mkdir()
        src = root / 'sources' / 'source.mp3'
        src.write_bytes(b'synthetic source')
        library = Library({'1': Track('1','Track','Artist',src.as_uri())}, [Playlist('Set', [], keys=['1'])], source=root/'sources'/'synthetic.xml')
        target = root / 'USB'
        report = sync.run(library, library.playlists, target, dry_run=True)
        assert report.bytes_to_copy == src.stat().st_size
        assert not target.exists()
        win.shutdown()
assert not any(m.startswith(('soundbox_personal', 'yt_dlp')) for m in sys.modules)
print('public core: no personal package, no network, local art and guarded dry-run passed')
'''
            result = subprocess.run([sys.executable, "-I", "-c", script, area],
                                    cwd=area, env={**os.environ, "SOUNDBOX_PROFILE": "public",
                                                   "QT_QPA_PLATFORM": "offscreen"},
                                    capture_output=True, text=True, timeout=45)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
