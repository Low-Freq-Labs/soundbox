import struct
import tempfile
import unittest
from pathlib import Path

from research.cdj_export.pdb_audit import audit, read_copy


def native_fixture():
    """Hand-framed three-row page: expected native bytes, no prototype writer."""
    data = bytearray(3 * 4096)
    struct.pack_into("<7I", data, 0, 0, 4096, 1, 4, 1, 7, 0)
    struct.pack_into("<4I", data, 28, 8, 9, 1, 2)  # candidate beyond EOF is legal
    struct.pack_into("<3I", data, 4100, 1, 8, 2)
    data[4096 + 27] = 0x64
    page = 8192
    struct.pack_into("<3I", data, page + 4, 2, 8, 9)
    data[page + 24:page + 28] = bytes.fromhex("03 60 00 24")
    struct.pack_into("<H", data, page + 30, 36)
    struct.pack_into("<2H", data, page + 32, 3, 0)
    for i in range(3):
        struct.pack_into("<3I", data, page + 40 + 12 * i, i + 1, i + 10, 1)
        struct.pack_into("<H", data, len(data) - 6 - i * 2, i * 12)
    struct.pack_into("<2H", data, len(data) - 4, 7, 7)
    return data


class PdbAuditTests(unittest.TestCase):
    def test_typed_rows_must_identify_their_actual_page_slot(self):
        data = native_fixture()
        # Same hand-framed three-slot page, now a typed track table.
        for at in (28, 4096 + 8, 8192 + 8):
            struct.pack_into("<I", data, at, 0)
        for slot in range(3):
            struct.pack_into("<2H", data, 8192 + 40 + slot * 12, 0x24, slot * 32)
        self.assertEqual(audit(data)["issues"], [])
        struct.pack_into("<H", data, 8192 + 40 + 12 + 2, 0)
        self.assertIn("page-slot identity", " ".join(audit(data)["issues"]))

    def test_native_counts_and_candidate_beyond_eof(self):
        report = audit(native_fixture())
        self.assertEqual(report["issues"], [])
        self.assertEqual(report["tables"][0]["present_rows"], 3)

    def test_rejected_candidate_byte_order_is_detected(self):
        data = native_fixture()
        data[8192 + 24:8192 + 27] = bytes.fromhex("00 18 03")
        self.assertIn("overlap (slots=6144)", " ".join(audit(data)["issues"]))

    def test_deleted_slots_are_not_live_rows(self):
        data = native_fixture()
        data[8192 + 24:8192 + 27] = bytes.fromhex("03 40 00")
        struct.pack_into("<2H", data, len(data) - 4, 5, 5)
        struct.pack_into("<H", data, len(data) - 8, 65535)  # deleted row offset
        report = audit(data)
        self.assertEqual(report["issues"], [])
        self.assertEqual(report["tables"][0]["present_rows"], 2)

    def test_native_page_above_255_rows(self):
        data = native_fixture()
        page = 8192
        data[page:] = bytes(4096)
        struct.pack_into("<3I", data, page + 4, 2, 8, 9)
        data[page + 24:page + 28] = bytes.fromhex("1c 81 23 24")
        struct.pack_into("<3H", data, page + 30, 3408, 1, 283)
        for slot in range(284):
            group, within = divmod(slot, 16)
            struct.pack_into("<H", data, len(data) - group * 36 - 6 - within * 2,
                             slot * 12)
        for group in range(18):
            struct.pack_into("<2H", data, len(data) - group * 36 - 4,
                             0xFFFF if group < 17 else 0x0FFF,
                             0 if group < 17 else 0x0800)
        report = audit(data)
        self.assertEqual(report["issues"], [])
        self.assertEqual(report["tables"][0]["present_rows"], 284)

    def test_truncation_and_directory_bounds(self):
        for data in (b"", bytes(28), native_fixture()[:-1]):
            self.assertTrue(audit(data)["issues"])
        data = native_fixture()
        struct.pack_into("<I", data, 8, 1000)
        self.assertIn("directory", " ".join(audit(data)["issues"]))

    def test_chain_cycle_is_bounded(self):
        data = native_fixture()
        struct.pack_into("<I", data, 40, 1)  # declared last is now index page
        struct.pack_into("<I", data, 36, 2)  # start at data page 2
        struct.pack_into("<I", data, 8192 + 12, 2)
        self.assertIn("chain ended", " ".join(audit(data)["issues"]))

    def test_symlink_inputs_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            (base / "copy.pdb").write_bytes(native_fixture())
            (base / "link.pdb").symlink_to(base / "copy.pdb")
            self.assertEqual(read_copy(base / "copy.pdb")["issues"], [])
            with self.assertRaises(ValueError):
                read_copy(base / "link.pdb")


if __name__ == "__main__":
    unittest.main()
