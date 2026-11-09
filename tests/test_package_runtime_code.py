"""Archive checks prove capabilities and code, including hidden PYZ contents."""
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from scripts.build_composition import REQUIRED_RUNTIME_MODULES, verify_runtime_code, composition, runtime_submodule, source_fingerprint


class PackagedRuntimeCode(unittest.TestCase):
    def setUp(self):
        self.area = tempfile.TemporaryDirectory(); self.addCleanup(self.area.cleanup)
        self.root = Path(self.area.name)
        self.code = {}
        for name in REQUIRED_RUNTIME_MODULES:
            path = self.root / (name.replace('.', '/') + '.py')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('def capability(value):\n    return value + 1\n')
            self.code[name] = compile(path.read_bytes(), 'bundled/' + name, 'exec', dont_inherit=True)

    def verify(self):
        return verify_runtime_code(SimpleNamespace(toc=self.code, extract=self.code.__getitem__), root=self.root)

    def test_direct_bundle_icon_bytes_participate_in_source_fingerprint(self):
        root = Path(__file__).resolve().parents[1]
        icon = root / 'assets/Soundbox.icns'
        self.assertIn('assets/Soundbox.icns', (root / 'Soundbox.spec').read_text())
        baseline = source_fingerprint(root)
        read = Path.read_bytes
        def changed(path):
            data = read(path)
            return data + b'changed bundle icon' if path == icon else data
        # Simulate a changed direct package input without touching the real icon.
        with mock.patch.object(Path, 'read_bytes', changed):
            self.assertNotEqual(source_fingerprint(root), baseline)
        self.assertEqual(source_fingerprint(root), baseline)

    def test_every_required_capability_and_nested_code_match_despite_bundle_paths(self):
        self.assertEqual(self.verify(), sorted(REQUIRED_RUNTIME_MODULES))
        self.assertTrue({'research', 'tests'} <= set(composition('personal')['excludes']))
        self.assertTrue({'research', 'tests'} <= set(composition('public')['excludes']))

    def test_missing_cbr_capability_cannot_pass_a_matching_profile_manifest(self):
        del self.code['soundbox.rekordbox_cbr_analysis_store']
        with self.assertRaisesRegex(ValueError, 'Shared runtime modules missing'):
            self.verify()

    def test_nested_implementation_mismatch_and_missing_source_fail(self):
        name = 'soundbox.mp3_scan'
        original = self.code[name]
        self.code[name] = compile('def capability(value):\n    return value + 2\n', 'bundled', 'exec')
        with self.assertRaisesRegex(ValueError, 'differs from tested source'):
            self.verify()
        self.code[name] = original
        (self.root / 'soundbox/mp3_scan.py').unlink()
        with self.assertRaisesRegex(ValueError, 'has no source'):
            self.verify()

    def test_developer_modules_hidden_only_inside_pyz_are_rejected(self):
        for name in ('research', 'research.native_library.mp3_analysis_pilot', 'tests.test_cbr_analysis_store',
                     'sqlalchemy.testing', 'sqlalchemy.testing.fixtures', 'sqlalchemy.ext.mypy.plugin',
                     'mypy.nodes', 'ast_serialize'):
            with self.subTest(module=name):
                self.code[name] = compile('', name, 'exec')
                with self.assertRaisesRegex(ValueError, 'Developer Python modules leaked'):
                    self.verify()
                del self.code[name]

    def test_build_filter_retains_runtime_orm_and_typing_dependencies(self):
        for name in ('sqlalchemy', 'sqlalchemy.orm', 'sqlalchemy.ext.asyncio',
                     'sqlalchemy.sql', 'mypy_extensions', 'pyrekordbox.db6',
                     'sqlcipher3', 'PIL.Image', 'numpy', 'scipy'):
            with self.subTest(module=name):
                self.assertTrue(runtime_submodule(name))

    def test_native_orm_and_encrypted_sql_run_with_development_modules_denied(self):
        # Exercise the actual native ORM schema and SQLCipher together on a
        # TEMP DB, with development namespaces absent at import time.
        from test_playlist_transaction import fixture
        native = self.root.resolve()
        fixture(native)
        script = '''
import importlib.abc, sys
from pathlib import Path
class NoDevelopment(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        for prefix in ('sqlalchemy.testing','sqlalchemy.ext.mypy','mypy','ast_serialize'):
            if fullname==prefix or fullname.startswith(prefix+'.'):
                raise ModuleNotFoundError(fullname)
sys.meta_path.insert(0,NoDevelopment())
from pyrekordbox.db6 import tables
from sqlalchemy import create_engine,select
from sqlalchemy.orm import Session
from soundbox.rekordbox_write import _connect
engine=create_engine('sqlite://',creator=lambda:_connect(Path(sys.argv[1])))
with Session(engine) as session:
    ids=session.execute(select(tables.DjmdPlaylist.ID)).scalars().all()
    assert ids==['1000'],ids
engine.dispose()
assert not any(name.startswith(('sqlalchemy.testing','sqlalchemy.ext.mypy','mypy.','ast_serialize')) for name in sys.modules)
'''
        result = subprocess.run([sys.executable, '-c', script, str(native / 'master.db')],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_isolated_runtime_sql_probe_is_memory_only(self):
        from desktop.startup_check import runtime_sql_check
        self.assertTrue(runtime_sql_check())
