"""Exact small-catalog captures under coarse epochs and concurrent changes."""

from pathlib import Path
import os
import struct
import tempfile
import unittest
from unittest.mock import patch

from soundbox import stock_ipod_capture as native


class StockNativeCapture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.database = self.root / "iPod_Control/iTunes/iTunesDB"
        self.database.parent.mkdir(parents=True)
        self.database.write_bytes(b"A" * 128)

    def test_reads_only_small_catalogs_twice_and_retains_exact_bytes(self):
        artwork = self.root / "iPod_Control/Artwork/ArtworkDB"
        artwork.parent.mkdir(parents=True)
        artwork.write_bytes(b"art")
        play_counts = self.database.with_name("Play Counts")
        play_counts.write_bytes(b"plays")
        forbidden = self.root / "iPod_Control/Artwork/F1028_1.ithmb"
        forbidden.write_bytes(b"pixels")
        opened = []
        original_open = os.open

        def tracked(path, *args, **kwargs):
            opened.append(path)
            return original_open(path, *args, **kwargs)

        with patch.object(native.os, "open", tracked):
            captured = native.capture_stock_ipod_native(self.root)
        self.assertEqual(captured.database, b"A" * 128)
        self.assertEqual(captured.artwork_database, b"art")
        self.assertEqual(captured.play_counts, b"plays")
        self.assertEqual(opened.count(self.database), 2)
        self.assertEqual(opened.count(artwork), 2)
        self.assertEqual(opened.count(play_counts), 2)
        self.assertNotIn(forbidden, opened)

    def test_same_epoch_same_size_different_bytes_are_rejected(self):
        initial_stat = self.database.stat()
        original_epoch = native._epoch
        original_read = native._read
        epoch = original_epoch(self.database, self.root)
        changed = False

        def coarse_epoch(path, target):
            return epoch if path == self.database else original_epoch(path, target)

        def mutate_after_read(path, target, expected, limit):
            nonlocal changed
            result = original_read(path, target, expected, limit)
            if path == self.database and not changed:
                changed = True
                path.write_bytes(b"B" * 128)
                os.utime(path, ns=(initial_stat.st_atime_ns, initial_stat.st_mtime_ns))
            return result

        with (patch.object(native, "_epoch", coarse_epoch),
              patch.object(native.os, "fstat", return_value=initial_stat),
              patch.object(native, "_read", mutate_after_read)):
            with self.assertRaisesRegex(native.StockIpodCaptureError, "changed"):
                native.capture_stock_ipod_native(self.root)

    def test_pending_play_counts_appearing_mid_capture_are_rejected(self):
        original_read = native._read
        pending = self.database.with_name("Play Counts")
        created = False

        def appear_after_absence(path, target, expected, limit):
            nonlocal created
            result = original_read(path, target, expected, limit)
            if path == pending and not created:
                pending.write_bytes(b"new playback")
                created = True
            return result

        with patch.object(native, "_read", appear_after_absence):
            with self.assertRaisesRegex(native.StockIpodCaptureError, "changed"):
                native.capture_stock_ipod_native(self.root)

    def test_outside_catalog_symlink_is_rejected(self):
        outside = self.root / "outside"
        outside.write_bytes(b"unrelated")
        self.database.unlink()
        self.database.symlink_to(outside)
        with self.assertRaises(native.StockIpodCaptureError):
            native.capture_stock_ipod_native(self.root)

    def test_parent_redirect_into_pioneer_never_opens_reserved_payload(self):
        self.database.unlink()
        self.database.parent.rmdir()
        reserved = self.root / "PIONEER/catalogs"
        reserved.mkdir(parents=True)
        (reserved / "iTunesDB").write_bytes(b"reserved")
        self.database.parent.symlink_to(reserved, target_is_directory=True)
        with patch.object(native.os, "open", side_effect=AssertionError("payload read")):
            with self.assertRaisesRegex(native.StockIpodCaptureError, "parent"):
                native.capture_stock_ipod_native(self.root)

    def test_oversize_catalog_is_rejected_before_payload_read(self):
        with self.database.open("wb") as handle:
            handle.truncate(256 * 1024 * 1024 + 1)
        with patch.object(Path, "open", side_effect=AssertionError("payload read")):
            with self.assertRaisesRegex(native.StockIpodCaptureError, "bounded"):
                native.capture_stock_ipod_native(self.root)

    def test_corrupt_zero_width_playback_entries_are_bounded_before_native_parser(self):
        pending = self.database.with_name("Play Counts")
        pending.write_bytes(b"mhdp" + struct.pack("<III", 16, 0, 0xFFFFFFFF))
        captured = native.capture_stock_ipod_native(self.root)
        with patch.object(native.tempfile, "TemporaryDirectory",
                          side_effect=AssertionError("native parser admission")):
            self.assertIsNone(captured.playback_entries())

    def test_mismatched_playback_count_is_rejected_before_native_object_allocation(self):
        pending = self.database.with_name("Play Counts")
        count = 100_000
        pending.write_bytes(b"mhdp" + struct.pack("<III", 16, 4, count) + bytes(count * 4))
        captured = native.capture_stock_ipod_native(self.root)
        with patch.object(native.tempfile, "TemporaryDirectory",
                          side_effect=AssertionError("native object parser admission")):
            self.assertIsNone(captured.playback_entries(expected_tracks=1))
