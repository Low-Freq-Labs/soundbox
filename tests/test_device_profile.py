"""Read-only device classification and stock-iPod planning."""

from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock
import wave

from soundbox.device_profile import inspect_device, plan_stock_ipod_export
from soundbox.library import Library, Playlist, Track
from soundbox import sync as sync_mod


class DeviceProfiles(unittest.TestCase):
    @staticmethod
    def _header(*, version=0x19, checksum=0, total=0xA4):
        header = bytearray(0xA4)
        header[:4] = b'mhbd'
        struct.pack_into('<III', header, 4, len(header), total, 1)
        struct.pack_into('<I', header, 0x10, version)
        struct.pack_into('<H', header, 0x30, checksum)
        return bytes(header)

    def test_model_probe_bounds_reads_before_allocating_oversized_metadata(self):
        root = self._root()
        device = root / 'iPod_Control' / 'Device'
        device.mkdir(parents=True)
        (root / 'iPod_Control' / 'iTunes').mkdir()
        oversized = device / 'SysInfoExtended'
        with oversized.open('wb') as output:
            output.seek(2 * 1024 * 1024 * 1024)
            output.write(b'\0')
        (device / 'SysInfo').write_bytes(b'ModelNumStr: MA446LL/A\n')
        before = oversized.stat()
        real_open = Path.open
        reads = []
        testcase = self
        class BoundedReader:
            def __init__(self, path, args, kwargs):
                self.handle = real_open(path, *args, **kwargs)
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.handle.close()
            def read(self, size=-1):
                testcase.assertGreater(size, 0, 'unbounded device metadata read')
                testcase.assertLessEqual(size, 1024 * 1024 + 1)
                reads.append(size)
                return self.handle.read(size)
        def guarded_open(path, *args, **kwargs):
            if path.parent == device:
                return BoundedReader(path, args, kwargs)
            return real_open(path, *args, **kwargs)
        with mock.patch.object(Path, 'open', autospec=True, side_effect=guarded_open):
            profile = inspect_device(root, 'msdos')
        self.assertEqual(profile.model, 'MA446LL/A')
        self.assertEqual(reads, [1024 * 1024 + 1, 1024 * 1024 + 1])
        self.assertEqual((oversized.stat().st_size, oversized.stat().st_mtime_ns),
                         (before.st_size, before.st_mtime_ns))

    def _root(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        return Path(td.name)

    @staticmethod
    def _snapshot(root: Path):
        return sorted(
            (str(path.relative_to(root)), path.read_bytes() if path.is_file() else None)
            for path in root.rglob("*")
        )

    def test_generic_fat32_is_rockbox_style_writable(self):
        root = self._root()
        before = self._snapshot(root)
        profile = inspect_device(root, "msdos")
        self.assertEqual(profile.kind, "generic_usb")
        self.assertEqual(profile.label, "USB")
        self.assertTrue(profile.rockbox_export)
        self.assertEqual(self._snapshot(root), before)

    def test_rockbox_stock_and_dual_boot_are_distinct(self):
        root = self._root()
        (root / ".rockbox").mkdir()
        self.assertEqual(inspect_device(root, "msdos").kind, "rockbox")
        (root / "iPod_Control/iTunes").mkdir(parents=True)
        (root / "iPod_Control/iTunes/iTunesDB").write_bytes(b"sacred")
        before = self._snapshot(root)
        profile = inspect_device(root, "msdos")
        self.assertEqual(profile.kind, "dual_boot")
        self.assertEqual(profile.label, "iTunes/Rockbox")
        self.assertTrue(profile.rockbox_export)
        self.assertTrue(profile.stock_preview)
        self.assertFalse(profile.stock_write)  # fixture database is deliberately invalid
        self.assertEqual(self._snapshot(root), before)

    def test_dual_boot_reports_compatible_stock_half_as_writable(self):
        root = self._root()
        (root / ".rockbox").mkdir()
        itunes = root / "iPod_Control/iTunes"
        device = root / "iPod_Control/Device"
        itunes.mkdir(parents=True)
        device.mkdir(parents=True)
        (itunes / "iTunesDB").write_bytes(self._header())
        (device / "SysInfo").write_text("ModelNumStr: MA446LL/A\n")
        before = self._snapshot(root)

        profile = inspect_device(root, "msdos")

        self.assertEqual(profile.kind, "dual_boot")
        self.assertTrue(profile.rockbox_export)
        self.assertTrue(profile.stock_write)
        self.assertEqual(self._snapshot(root), before)

    def test_dual_boot_exports_stock_unsafe_wav_only_through_rockbox(self):
        root = self._root()
        target = root / "IPOD"
        (target / ".rockbox").mkdir(parents=True)
        itunes = target / "iPod_Control/iTunes"
        itunes.mkdir(parents=True)
        database = itunes / "iTunesDB"
        database.write_bytes(b"sacred stock database")

        source_dir = root / "library"
        source_dir.mkdir()
        source = source_dir / "Loot.wav"
        with wave.open(str(source), "wb") as output:
            output.setnchannels(2)
            output.setsampwidth(3)  # PCM24: proven unsafe on the stock MA446.
            output.setframerate(44_100)
            output.writeframes(b"\0" * 6)
        playlist = Playlist("HOUSE", [], ["1"], "house")
        library = Library(
            {"1": Track(
                "1", "Loot", "Hostage Situation", source.as_uri(),
                kind="WAV File")},
            [playlist], source_dir / "library.xml", source_kind="fixture",
        )

        profile = inspect_device(target, "msdos")
        self.assertEqual(profile.kind, "dual_boot")
        self.assertTrue(profile.rockbox_export)
        self.assertFalse(profile.stock_write)

        sync_mod.run(library, [playlist], target, dry_run=False)

        self.assertTrue(
            (target / "Music/Hostage Situation/Loot/Loot.wav").is_file())
        self.assertIn(
            "/Music/Hostage Situation/Loot/Loot.wav",
            (target / "Playlists/HOUSE.m3u8").read_text(encoding="utf-8"),
        )
        self.assertEqual(database.read_bytes(), b"sacred stock database")

    def test_stock_ipod_plan_is_explicitly_no_write(self):
        root = self._root()
        (root / "iPod_Control/iTunes").mkdir(parents=True)
        database = root / "iPod_Control/iTunes/iTunesDB"
        database.write_bytes(b"years of user state")
        audio = root / "fixture.m4a"
        audio.write_bytes(b"fixture")
        before = self._snapshot(root)
        profile = inspect_device(root, "msdos")
        plan = plan_stock_ipod_export(profile, [audio, root / "missing.mp3"])
        self.assertEqual(profile.kind, "stock_ipod")
        self.assertFalse(profile.rockbox_export)
        self.assertFalse(plan["writable"])
        self.assertEqual(plan["formats"], {".m4a": 1})
        self.assertEqual(plan["missing_count"], 1)
        self.assertEqual(self._snapshot(root), before)

    def test_non_fat_generic_is_not_exportable(self):
        root = self._root()
        profile = inspect_device(root, "exfat")
        self.assertEqual(profile.kind, "generic_usb")
        self.assertFalse(profile.rockbox_export)

    def test_supported_format_is_writable_without_model_confirmation(self):
        root = self._root()
        itunes = root / "iPod_Control/iTunes"
        device = root / "iPod_Control/Device"
        itunes.mkdir(parents=True)
        device.mkdir(parents=True)
        (itunes / "iTunesDB").write_bytes(self._header())
        for label in ('', 'MA446LL/A', 'FRIENDS-IPOD'):
            with self.subTest(label=label):
                (device / 'SysInfo').write_text(f'ModelNumStr: {label}\n')
                before = self._snapshot(root)
                profile = inspect_device(root, 'msdos')
                self.assertEqual(profile.model, label)
                self.assertTrue(profile.stock_write)
                self.assertTrue(profile.stock_format.writable)
                self.assertFalse(profile.stock_format.supports_artwork)
                self.assertEqual(self._snapshot(root), before)

    def test_known_model_does_not_admit_unsupported_or_truncated_catalogs(self):
        root = self._root()
        itunes = root / 'iPod_Control/iTunes'; itunes.mkdir(parents=True)
        device = root / 'iPod_Control/Device'; device.mkdir()
        (device / 'SysInfo').write_text('ModelNumStr: MA446LL/A\n')
        database = itunes / 'iTunesDB'
        for content, reason in ((self._header(checksum=1), 'checksum'),
                                (self._header(version=0x30), 'version'),
                                (self._header(total=4096), 'truncated'),
                                (self._header() + b'external', 'trailing'),
                                (b'mhbd', 'uncompressed')):
            with self.subTest(reason=reason):
                database.write_bytes(content)
                before = self._snapshot(root)
                profile = inspect_device(root, 'msdos')
                self.assertFalse(profile.stock_write)
                self.assertIn(reason, profile.stock_format.reason.lower())
                self.assertEqual(self._snapshot(root), before)
        database.write_bytes(self._header())
        for other, content in (('iTunesCDB', b'compressed'), ('Library.itdb', b'sqlite')):
            with self.subTest(layout=other):
                path = itunes / other; path.write_bytes(content)
                before = self._snapshot(root)
                self.assertFalse(inspect_device(root, 'msdos').stock_write)
                self.assertEqual(self._snapshot(root), before)
                path.unlink()

    def test_catalog_classification_reads_fixed_headers_and_never_loads_media(self):
        root = self._root()
        itunes = root / 'iPod_Control/iTunes'; itunes.mkdir(parents=True)
        database = itunes / 'iTunesDB'
        with database.open('wb') as output:
            output.write(self._header(total=16 * 1024))
            output.truncate(16 * 1024)
        original_open = Path.open; reads = []; testcase = self
        class Reader:
            def __init__(self, path, args, kwargs):
                self.handle = original_open(path, *args, **kwargs)
            def __enter__(self):return self
            def __exit__(self, *args):self.handle.close()
            def fileno(self):return self.handle.fileno()
            def read(self, size=-1):
                testcase.assertIn(size, (0x50, 0xA4))
                reads.append(size)
                return self.handle.read(size)
        def opened(path, *args, **kwargs):
            return Reader(path, args, kwargs) if path == database else original_open(path, *args, **kwargs)
        before = database.stat()
        with mock.patch.object(Path, 'open', autospec=True, side_effect=opened), \
                mock.patch.object(Path, 'read_bytes', side_effect=AssertionError('full catalog read')), \
                mock.patch.object(Path, 'iterdir', side_effect=AssertionError('device enumeration')):
            profile = inspect_device(root, 'msdos')
        self.assertTrue(profile.stock_write)
        self.assertEqual(reads, [0xA4, 0x50])
        self.assertEqual((database.stat().st_size, database.stat().st_mtime_ns),
                         (before.st_size, before.st_mtime_ns))
