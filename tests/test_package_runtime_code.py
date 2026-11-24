"""Archive checks prove capabilities and code, including hidden PYZ contents."""
import json
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from scripts.build_composition import (REQUIRED_RUNTIME_MODULES, verify_runtime_code,
    verify_startup_scripts, composition, collection_options, runtime_submodule,
    source_fingerprint, inspect_artifact)


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
                     'mypy.nodes', 'ast_serialize', 'Crypto.SelfTest.Cipher.test_AES'):
            with self.subTest(module=name):
                self.code[name] = compile('', name, 'exec')
                with self.assertRaisesRegex(ValueError, 'Developer Python modules leaked'):
                    self.verify()
                del self.code[name]

    def test_build_filter_retains_runtime_orm_and_typing_dependencies(self):
        for name in ('sqlalchemy', 'sqlalchemy.orm', 'sqlalchemy.ext.asyncio',
                     'sqlalchemy.sql', 'mypy_extensions', 'pyrekordbox.db6',
                     'sqlalchemy.dialects', 'sqlalchemy.dialects.sqlite',
                     'sqlalchemy.dialects.sqlite.pysqlite', 'sqlalchemy.dialects.sqlite.pysqlcipher',
                     'sqlcipher3', 'PIL.Image', 'numpy', 'scipy', 'Crypto.Cipher.AES'):
            with self.subTest(module=name):
                self.assertTrue(runtime_submodule(name))

    def test_unused_installer_and_other_database_backends_are_excluded_and_rejected(self):
        for name in ('pyrekordbox.__main__', 'sqlalchemy.dialects.mssql',
                     'sqlalchemy.dialects.mysql.pymysql', 'sqlalchemy.dialects.oracle.base',
                     'sqlalchemy.dialects.postgresql.psycopg'):
            with self.subTest(module=name):
                self.assertFalse(runtime_submodule(name))
                for profile in ('public', 'personal'):
                    self.assertTrue(any(name == prefix or name.startswith(prefix + '.')
                                        for prefix in composition(profile)['excludes']))
                self.code[name] = compile('', name, 'exec')
                with self.assertRaisesRegex(ValueError, 'Unused dependency Python modules leaked'):
                    self.verify()
                del self.code[name]

    def test_real_dependency_data_collection_omits_installer_but_keeps_reader_data(self):
        from PyInstaller.utils.hooks import collect_data_files
        options = collection_options('pyrekordbox')
        data = collect_data_files('pyrekordbox', include_py_files=True,
                                  excludes=options['exclude_datas'])
        files = [Path(source) for source, _destination in data]
        self.assertNotIn('__main__.py', [path.name for path in files])
        self.assertTrue(any(path.parts[-3:] == ('pyrekordbox', 'db6', 'database.py') for path in files))
        self.assertTrue(options['filter_submodules']('pyrekordbox.db6'))
        self.assertFalse(options['filter_submodules']('pyrekordbox.__main__'))
        self.assertEqual(collection_options('PIL'), {})

    def test_raw_unused_dependency_files_are_rejected_without_pyz_entries(self):
        app = self.root / 'Soundbox.app'
        resources = app / 'Contents/Resources'
        for relative in ('Resources/assets/soundbox-wordmark.svg',
                         'Frameworks/native/libsoundbox_process_guard.dylib',
                         'Frameworks/native/soundbox-ipod-identity'):
            target = app / 'Contents' / relative
            target.parent.mkdir(parents=True, exist_ok=True);target.write_bytes(b'fixture')
        manifest = {**composition('personal')['manifest'], 'source_sha256': 'a'*64}
        (resources / 'soundbox-build.json').write_text(json.dumps(manifest))
        (app / 'Contents/Info.plist').write_bytes(plistlib.dumps(dict(
            SoundboxBuildProfile='personal', SoundboxSourceFingerprint='a'*64,
            SoundboxModules=[entry['id'] for entry in manifest['modules']])))
        pyz = SimpleNamespace(toc={'soundbox.providers': None, 'soundbox.artwork': None,
                                  'soundbox_personal': None, 'yt_dlp': None})
        archive = SimpleNamespace(open_embedded_archive=lambda _name: pyz)
        with mock.patch('PyInstaller.archive.readers.CArchiveReader', return_value=archive), \
                mock.patch('scripts.build_composition.verify_startup_scripts', return_value={}), \
                mock.patch('scripts.build_composition.verify_runtime_code', return_value=[]):
            for relative in ('Resources/pyrekordbox/__main__.py',
                             'Frameworks/pyrekordbox/__main__.py',
                             *(f'Resources/sqlalchemy/dialects/{name}/__init__.py'
                               for name in ('mssql', 'mysql', 'oracle', 'postgresql'))):
                with self.subTest(artifact=relative):
                    self.assertEqual(inspect_artifact(app, 'personal')['profile'], 'personal')
                    path = app / 'Contents' / relative
                    path.parent.mkdir(parents=True, exist_ok=True);path.write_bytes(b'fixture')
                    with self.assertRaisesRegex(ValueError, 'Unused dependency artifact leaked'):
                        inspect_artifact(app, 'personal')
                    path.unlink();path.parent.rmdir()

    def test_native_orm_and_encrypted_sql_run_with_development_modules_denied(self):
        # Exercise the actual native ORM schema and SQLCipher together on a
        # TEMP DB, with development namespaces absent at import time.
        from test_playlist_transaction import fixture
        native = self.root.resolve()
        fixture(native)
        script = '''
import importlib.abc, sys
from pathlib import Path
excluded=('sqlalchemy.testing','sqlalchemy.ext.mypy','mypy','ast_serialize',
          'pyrekordbox.__main__','sqlalchemy.dialects.mssql','sqlalchemy.dialects.mysql',
          'sqlalchemy.dialects.oracle','sqlalchemy.dialects.postgresql')
class NoDevelopment(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        for prefix in excluded:
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
from sqlcipher3 import dbapi2 as sqlcipher
engine=create_engine('sqlite+pysqlcipher://:soundbox-private-probe@/:memory:',module=sqlcipher)
assert engine.dialect.name=='sqlite' and engine.dialect.driver=='pysqlcipher'
with engine.begin() as connection:
    connection.exec_driver_sql('CREATE TABLE djmdPlaylist (ID TEXT)')
    connection.exec_driver_sql("INSERT INTO djmdPlaylist VALUES ('1000')")
with Session(engine) as session:
    assert session.execute(select(tables.DjmdPlaylist.ID)).scalars().all()==['1000']
engine.dispose()
assert not any(name==prefix or name.startswith(prefix+'.') for name in sys.modules for prefix in excluded)
'''
        result = subprocess.run([sys.executable, '-c', script, str(native / 'master.db')],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_isolated_runtime_sql_probe_is_memory_only(self):
        from desktop.startup_check import runtime_sql_check
        self.assertTrue(runtime_sql_check())

    def test_isolated_runtime_aes_probe_loads_the_real_backend(self):
        from desktop.startup_check import runtime_crypto_check
        self.assertTrue(runtime_crypto_check())


class PackagedStartupScripts(unittest.TestCase):
    def setUp(self):
        self.area = tempfile.TemporaryDirectory(); self.addCleanup(self.area.cleanup)
        self.root = Path(self.area.name)
        (self.root / 'soundbox').mkdir()
        self.hook = self.root / 'soundbox/packaged_worker_hook.py'
        self.hook.write_text('def configure():\n    return "worker-only"\nconfigure()\n')
        self.entry = self.root / 'soundbox_app.py'
        self.entry.write_text('def dispatch():\n    return "desktop-or-worker"\ndispatch()\n')
        self.bootstrap = self.root / 'bootstrap.py'
        self.bootstrap.write_text('pass\n')
        self.order = ['pyiboot01_bootstrap', 'packaged_worker_hook',
                      'pyi_rth_inspect', 'pyi_rth_pyside6', 'soundbox_app']

    def archive(self, order=None, *, hook_type='s'):
        from PyInstaller.archive.readers import CArchiveReader
        from PyInstaller.archive.writers import CArchiveWriter
        paths = {'packaged_worker_hook': self.hook, 'soundbox_app': self.entry}
        entries = [(name, str(paths.get(name, self.bootstrap)), True,
                    hook_type if name == 'packaged_worker_hook' else 's')
                   for name in (self.order if order is None else order)]
        path = self.root / 'startup.pkg'
        CArchiveWriter(str(path), entries, 'Python')
        return CArchiveReader(str(path))

    def test_real_archive_verifies_worker_hook_and_entry_code_in_boot_order(self):
        result = verify_startup_scripts(self.archive(), root=self.root)
        self.assertEqual(result, dict(order=self.order,
                                      verified=['packaged_worker_hook', 'soundbox_app']))

    def test_missing_or_unexecuted_startup_scripts_are_rejected(self):
        for name in ('pyiboot01_bootstrap', 'packaged_worker_hook', 'pyi_rth_pyside6', 'soundbox_app'):
            with self.subTest(missing=name):
                archive = self.archive([item for item in self.order if item != name])
                with self.assertRaisesRegex(ValueError, 'Startup scripts missing: ' + name):
                    verify_startup_scripts(archive, root=self.root)
        with self.assertRaisesRegex(ValueError, 'Startup scripts missing: packaged_worker_hook'):
            verify_startup_scripts(self.archive(hook_type='x'), root=self.root)

    def test_late_hook_early_entry_and_prebootstrap_hook_are_rejected(self):
        orders = (
            ['pyiboot01_bootstrap', 'pyi_rth_pyside6', 'packaged_worker_hook', 'soundbox_app'],
            ['pyiboot01_bootstrap', 'pyi_rth_inspect', 'packaged_worker_hook', 'pyi_rth_pyside6', 'soundbox_app'],
            ['packaged_worker_hook', 'pyiboot01_bootstrap', 'pyi_rth_pyside6', 'soundbox_app'],
            ['pyiboot01_bootstrap', 'packaged_worker_hook', 'soundbox_app', 'pyi_rth_pyside6'],
        )
        for order in orders:
            with self.subTest(order=order):
                with self.assertRaisesRegex(ValueError, 'Worker runtime hook must run first'):
                    verify_startup_scripts(self.archive(order), root=self.root)

    def test_nested_worker_hook_code_and_missing_source_are_rejected(self):
        archive = self.archive()
        original = self.hook.read_text()
        self.hook.write_text(original.replace('worker-only', 'wrong-worker'))
        with self.assertRaisesRegex(ValueError, 'startup code differs from tested source: packaged_worker_hook'):
            verify_startup_scripts(archive, root=self.root)
        self.hook.unlink()
        with self.assertRaisesRegex(ValueError, 'startup script has no source: packaged_worker_hook'):
            verify_startup_scripts(archive, root=self.root)

    def test_stale_entry_dispatch_code_is_rejected(self):
        archive = self.archive()
        self.entry.write_text('def dispatch():\n    return "old-entry"\ndispatch()\n')
        with self.assertRaisesRegex(ValueError, 'startup code differs from tested source: soundbox_app'):
            verify_startup_scripts(archive, root=self.root)
