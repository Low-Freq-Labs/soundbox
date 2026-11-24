"""Owned private ORM snapshots retain encryption, freshness and failure truth."""
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from sqlcipher3 import dbapi2 as sqlcipher
from sqlalchemy.exc import DatabaseError

from soundbox import native_cipher
from soundbox.native_snapshot import native_snapshot
from soundbox.rekordbox_write import _connect
from tests.test_native_reader_process import fixture


class NativeSnapshot(unittest.TestCase):
    def setUp(self):
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.source=self.root/'master.db';fixture(self.source)
        native_cipher._derive.cache_clear();self.addCleanup(native_cipher._derive.cache_clear)

    def test_fresh_snapshot_connections_reuse_only_key_derivation_and_retire_owned_engine(self):
        before=self.source.read_bytes()
        with native_snapshot(self.source) as db:
            self.assertEqual(db.get_content(ID='1').Title,'日本語 - Song')
            engine=db.engine;self.assertGreater(len(engine._compiled_cache),0)
            connection=db.session.connection().connection.dbapi_connection
        self.assertIsNone(db.session)
        self.assertEqual(len(engine._compiled_cache),0)
        with self.assertRaises(sqlcipher.ProgrammingError):connection.execute('SELECT 1')
        self.assertEqual(self.source.read_bytes(),before)
        self.assertEqual(native_cipher._derive.cache_info().misses,1)
        with native_snapshot(self.source) as second:
            self.assertEqual(second.get_content(ID='1').Title,'日本語 - Song')
            self.assertIsNot(second.engine,engine)
        self.assertEqual(native_cipher._derive.cache_info().hits,1)
        connection=_connect(self.source)
        try:connection.execute("UPDATE djmdContent SET Title='Fresh native change' WHERE ID='1'")
        finally:connection.close()
        with native_snapshot(self.source) as third:
            self.assertEqual(third.get_content(ID='1').Title,'Fresh native change')
        self.assertEqual(native_cipher._derive.cache_info().misses,1)

    def test_changed_ciphertext_still_fails_with_a_warm_snapshot_key(self):
        with native_snapshot(self.source) as db:db.get_property().all()
        payload=bytearray(self.source.read_bytes());payload[100]^=1;self.source.write_bytes(payload)
        with self.assertRaises(DatabaseError):
            with native_snapshot(self.source) as db:db.get_property().all()
        self.assertEqual(self.source.read_bytes(),bytes(payload))

    def test_every_cleanup_runs_without_replacing_an_earlier_reader_error(self):
        calls=[];original=RuntimeError('Reader failed')
        def close():calls.append('session');raise OSError('Close failed')
        def clear():calls.append('statements');raise ValueError('Clear failed')
        def dispose():calls.append('pool');raise RuntimeError('Dispose failed')
        db=SimpleNamespace(close=close,engine=SimpleNamespace(clear_compiled_cache=clear,dispose=dispose))
        with mock.patch('pyrekordbox.Rekordbox6Database',return_value=db):
            with self.assertRaises(RuntimeError) as failure:
                with native_snapshot(self.source):raise original
        self.assertIs(failure.exception,original)
        self.assertEqual(calls,['session','statements','pool'])
        calls.clear()
        with mock.patch('pyrekordbox.Rekordbox6Database',return_value=db):
            with self.assertRaisesRegex(OSError,'Close failed'):
                with native_snapshot(self.source):pass
        self.assertEqual(calls,['session','statements','pool'])

    def test_snapshot_helper_import_is_qt_free(self):
        subprocess.run([sys.executable,'-c',
            'import sys; import soundbox.native_snapshot; '
            'assert not any(name.startswith("PySide6") for name in sys.modules)'],check=True,
            cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True)
