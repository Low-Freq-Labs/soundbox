import base64
import binascii
import hashlib
import json
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
import zlib

from sqlcipher3 import dbapi2 as sqlcipher
from research.cdj_export.export_compare import compare, device_string, pdb_rows


def framed_pdb():
    data = bytearray(3 * 4096)
    struct.pack_into("<7I", data, 0, 0, 4096, 1, 4, 1, 7, 0)
    struct.pack_into("<4I", data, 28, 8, 3, 1, 2)
    struct.pack_into("<3I", data, 4100, 1, 8, 2)
    data[4096 + 27] = 0x64
    struct.pack_into("<3I", data, 8196, 2, 8, 3)
    data[8216:8220] = bytes.fromhex("01 20 00 24")
    struct.pack_into("<H", data, 8222, 12)
    struct.pack_into("<3I", data, 8232, 1, 2, 3)
    struct.pack_into("<3H", data, len(data) - 6, 0, 1, 1)
    return bytes(data)


class ExportCompareTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.reference = self.root / "reference"
        self.candidate = self.root / "candidate"
        self.audio = self.root / "audio"
        self.db = self.reference / "PIONEER/rekordbox"
        self.db.mkdir(parents=True)
        self.audio.mkdir()
        keyfile = self.root / "rbxport-kit-b739d369/crates/rbl-onelibrary/src/key.rs"
        keyfile.parent.mkdir(parents=True)
        self.key = "fixture-only-passphrase"
        xor = b"fixture"
        compressed = zlib.compress(self.key.encode())
        encoded = base64.b85encode(bytes(b ^ xor[i % len(xor)] for i, b in enumerate(compressed))).decode()
        keyfile.write_text(f'const BLOB: &str = "{encoded}";\nconst XOR_KEY: &[u8] = b"fixture";\n')
        self.make_sql(self.db / "exportLibrary.db")
        for name in ("export.pdb", "exportExt.pdb"):
            (self.db / name).write_bytes(framed_pdb())
        files = {"PIONEER/rekordbox/" + p.name: {"size": p.stat().st_size,
                 "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in self.db.iterdir()}
        (self.reference / "capture.json").write_text(json.dumps({"metadata_files": files}))
        (self.audio / "capture.json").write_text(json.dumps({"audio": {}}))
        shutil.copytree(self.reference / "PIONEER", self.candidate / "PIONEER")

    def tearDown(self):
        self.temporary.cleanup()

    def make_sql(self, path, value="original"):
        conn = sqlcipher.connect(path)
        try:
            conn.execute("PRAGMA key='" + self.key + "'")
            conn.execute("CREATE TABLE content (content_id INTEGER PRIMARY KEY, title TEXT)")
            conn.execute("INSERT INTO content VALUES (1,?)", [value])
            conn.commit()
        finally:
            conn.close()

    def report(self):
        return compare(self.root, self.reference, self.candidate, self.audio)

    def test_identical_export_passes_strict_comparison(self):
        result = self.report()
        self.assertTrue(result["strict_byte_match"])
        self.assertTrue(result["native_equivalence_match"])
        self.assertTrue(result["all_database_rows_match"])
        self.assertEqual(result["byte_identical_files"], 3)

    def test_equal_sql_rows_with_different_ciphertext_still_fail_raw_gate(self):
        path = self.candidate / "PIONEER/rekordbox/exportLibrary.db"
        path.unlink()
        self.make_sql(path)
        result = self.report()
        self.assertFalse(result["strict_byte_match"])
        self.assertTrue(result["all_database_rows_match"])
        self.assertTrue(result["native_equivalence_match"])
        self.assertFalse(result["files"]["PIONEER/rekordbox/exportLibrary.db"]["same_bytes"])

    def test_changed_sql_value_is_not_normalized_away(self):
        path = self.candidate / "PIONEER/rekordbox/exportLibrary.db"
        path.unlink()
        self.make_sql(path, "changed")
        result = self.report()
        self.assertFalse(result["all_database_rows_match"])
        self.assertFalse(result["native_equivalence_match"])
        self.assertIn("content", result["databases"]["exportLibrary.db"]["different_tables"])

    def test_extra_files_fail_the_strict_gate(self):
        (self.candidate / "unwanted.sync").write_bytes(b"extra")
        result = self.report()
        self.assertFalse(result["strict_byte_match"])
        self.assertFalse(result["native_equivalence_match"])
        self.assertEqual(result["extra_files"], ["unwanted.sync"])

    def test_reference_tampering_is_rejected(self):
        (self.db / "export.pdb").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "Reference changed"):
            self.report()

    def test_outside_workspace_or_symlink_is_rejected(self):
        with self.assertRaises(ValueError):
            compare(self.root, self.reference, self.root.parent, self.audio)
        (self.candidate / "redirect").symlink_to(self.db / "export.pdb")
        with self.assertRaises(ValueError):
            self.report()

    def test_reference_path_traversal_is_rejected(self):
        receipt = self.reference / "capture.json"
        receipt.write_text(json.dumps({"metadata_files": {"../outside": {"sha256": "bad", "size": 0}}}))
        with self.assertRaisesRegex(ValueError, "escaped"):
            self.report()

    def test_native_counts_and_bad_string_bounds(self):
        self.assertEqual(len(pdb_rows(framed_pdb())[8]), 1)
        self.assertEqual(device_string(b"\x07hi", 0), ("hi", 3))
        for value, offset in ((b"\x09x", 0), (b"\x40\x01\x00\0", 0), (b"x", 8)):
            with self.subTest(value=binascii.hexlify(value)), self.assertRaises(ValueError):
                device_string(value, offset)


if __name__ == "__main__":
    unittest.main()
