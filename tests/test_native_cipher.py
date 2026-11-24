"""Generated encrypted TEMP databases; stock passphrase readers stay compatible."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from sqlcipher3 import dbapi2 as sqlcipher

from soundbox import native_cipher as cipher
from soundbox.rekordbox_write import _connect


class NativeCipher(unittest.TestCase):
    def setUp(self):
        self.area = tempfile.TemporaryDirectory(); self.addCleanup(self.area.cleanup)
        self.root = Path(self.area.name).resolve()
        cipher._derive.cache_clear(); self.addCleanup(cipher._derive.cache_clear)

    def create(self, name):
        path = self.root / name
        conn = _connect(path, create=True)
        try:
            conn.execute('CREATE TABLE proof(value TEXT)')
            conn.execute("INSERT INTO proof VALUES('original')")
        finally:
            conn.close()
        return path

    def read(self, path, *, stock=False):
        if stock:
            conn = sqlcipher.connect(str(path))
            cipher.key_connection(conn, reuse=False)
        else:
            conn = _connect(path, readonly=True)
        try:
            return conn.execute('SELECT * FROM proof').fetchall()
        finally:
            conn.close()

    def test_reuse_across_copies_and_writes_preserves_original_passphrase_and_encryption(self):
        path = self.create('original.db'); before = path.read_bytes()
        self.assertEqual(self.read(path), [('original',)])
        self.assertEqual(cipher._derive.cache_info().misses, 1)
        clone = self.root / 'clone.db'; shutil.copyfile(path, clone)
        self.assertEqual(self.read(clone), [('original',)])
        self.assertEqual(cipher._derive.cache_info().hits, 1)
        self.assertEqual(path.read_bytes(), before)
        conn = _connect(clone)
        try:
            conn.execute("UPDATE proof SET value='new write'")
        finally:
            conn.close()
        self.assertEqual(self.read(clone, stock=True), [('new write',)])
        self.assertEqual(self.read(path, stock=True), [('original',)])
        self.assertNotEqual(clone.read_bytes()[:16], b'SQLite format 3\0')
        self.assertEqual(clone.read_bytes()[:16], before[:16])

    def test_replaced_salt_at_same_path_and_changed_passphrase_never_use_old_key(self):
        path = self.create('original.db'); other = self.create('replacement.db')
        self.read(path); other.replace(path)
        self.assertEqual(self.read(path), [('original',)])
        self.assertEqual(cipher._derive.cache_info().misses, 2)
        before = path.read_bytes()
        with mock.patch('pyrekordbox.utils.deobfuscate', return_value='wrong passphrase'):
            with self.assertRaises(sqlcipher.DatabaseError):
                self.read(path)
        self.assertEqual(cipher._derive.cache_info().misses, 3)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.read(path, stock=True), [('original',)])

    def test_warm_key_still_authenticates_changed_ciphertext(self):
        path = self.create('original.db'); self.read(path)
        payload = bytearray(path.read_bytes()); payload[100] ^= 1; path.write_bytes(payload)
        damaged = path.read_bytes()
        with self.assertRaises(sqlcipher.DatabaseError):
            self.read(path)
        self.assertEqual(path.read_bytes(), damaged)
        self.assertEqual(cipher._derive.cache_info().hits, 1)

    def test_actual_kdf_defaults_are_part_of_key_and_stock_reader_can_open_result(self):
        control = sqlcipher.connect(':memory:')
        original_count = control.execute('PRAGMA cipher_default_kdf_iter').fetchone()[0]
        original_algorithm = control.execute('PRAGMA cipher_default_kdf_algorithm').fetchone()[0]
        try:
            for algorithm in ('PBKDF2_HMAC_SHA512', 'PBKDF2_HMAC_SHA256', 'PBKDF2_HMAC_SHA1'):
                with self.subTest(algorithm=algorithm):
                    control.execute('PRAGMA cipher_default_kdf_iter=64000')
                    control.execute('PRAGMA cipher_default_kdf_algorithm=' + algorithm)
                    path = self.create(algorithm + '.db')
                    self.assertEqual(self.read(path), [('original',)])
                    conn = _connect(path)
                    try:
                        conn.execute("UPDATE proof SET value='profile preserved'")
                    finally:
                        conn.close()
                    self.assertEqual(self.read(path, stock=True), [('profile preserved',)])
        finally:
            control.execute('PRAGMA cipher_default_kdf_iter=' + str(original_count))
            control.execute('PRAGMA cipher_default_kdf_algorithm=' + original_algorithm)
            control.close()

    def test_cache_is_bounded_and_parallel_readers_keep_independent_connections(self):
        paths = [self.create(str(i) + '.db') for i in range(9)]
        for path in paths:
            self.assertEqual(self.read(path), [('original',)])
        self.assertEqual(cipher._derive.cache_info().currsize, 8)
        misses = cipher._derive.cache_info().misses
        self.read(paths[0]); self.assertEqual(cipher._derive.cache_info().misses, misses + 1)
        with ThreadPoolExecutor(max_workers=3) as pool:
            self.assertEqual(list(pool.map(self.read, [paths[0]] * 3)), [[('original',)]] * 3)

    def test_unrecognized_cipher_profile_keeps_ordinary_keying_without_derivation(self):
        conn = mock.Mock()
        conn.execute.return_value.fetchone.side_effect = [('unknown',), ('256000',), ('00' * 16,)]
        with mock.patch.object(cipher, '_derive', side_effect=AssertionError('Unknown profile')):
            cipher.key_connection(conn)
        self.assertEqual(len([call for call in conn.execute.call_args_list
                              if call.args[0].startswith('PRAGMA key=')]), 1)
