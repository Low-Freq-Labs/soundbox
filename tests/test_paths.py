import unittest
from pathlib import Path

from soundbox.paths import (
    decode_location, device_music_path, is_external_device_path, is_within,
    sanitize_component,
)


class TestDecode(unittest.TestCase):
    def test_localhost_percent_decoded(self):
        loc = "file://localhost/Users/me/Music/Some%20Track%20%26%20VIP.flac"
        self.assertEqual(decode_location(loc), Path("/Users/me/Music/Some Track & VIP.flac"))

    def test_braces_and_colons(self):
        loc = "file://localhost/Users/me/Desktop/%7bSUB%7d/USB%20Prep/3%3A7/x.wav"
        self.assertEqual(decode_location(loc), Path("/Users/me/Desktop/{SUB}/USB Prep/3:7/x.wav"))


class TestSanitize(unittest.TestCase):
    def test_illegal_chars_replaced(self):
        self.assertEqual(sanitize_component('a:b/c*d?"e'), "a-b - cd'e")

    def test_trailing_dot_space_trimmed(self):
        self.assertEqual(sanitize_component("name. "), "name")

    def test_latin_is_readably_transliterated_without_placeholders(self):
        self.assertEqual(sanitize_component("Café Tröll"), "Cafe Troll")
        self.assertEqual(sanitize_component("CØSTA ŁØVE"), "COSTA LOVE")
        self.assertEqual(
            sanitize_component("DÊTRE / Dr. Ushūu"), "DETRE - Dr. Ushuu")
        self.assertEqual(sanitize_component("不明"), "不明")

    def test_decorative_forbidden_characters_do_not_leave_placeholders(self):
        self.assertEqual(sanitize_component("*** CHOPS ***"), "CHOPS")
        self.assertEqual(sanitize_component("M?STIC & ROI*"), "MSTIC & ROI")
        self.assertEqual(sanitize_component("///"), "_")

    def test_compatibility_punctuation_and_reserved_names(self):
        self.assertEqual(
            sanitize_component("1931 HITS ARCHIVE： Minnie， Vol. 4²"),
            "1931 HITS ARCHIVE- Minnie, Vol. 42",
        )
        self.assertEqual(sanitize_component("CON"), "CON-")
        self.assertEqual(sanitize_component("LPT1.mix"), "LPT1-.mix")

    def test_never_empty(self):
        self.assertEqual(sanitize_component(""), "_")
        self.assertEqual(sanitize_component("   "), "_")


class TestDevicePath(unittest.TestCase):
    def test_pioneer_tree_is_reserved_case_insensitively(self):
        self.assertTrue(is_external_device_path("/PIONEER/rekordbox/export.pdb"))
        self.assertTrue(is_external_device_path(Path("pioneer/USBANLZ")))
        self.assertTrue(is_external_device_path("/Music/../PIONEER/export.pdb"))
        self.assertFalse(is_external_device_path("/Music/PIONEER/song.wav"))
        self.assertFalse(is_external_device_path("/Playlists/set.m3u8"))

    def test_basic(self):
        self.assertEqual(
            device_music_path(Path("/x/Track.flac"), "Artist"),
            "/Music/Artist/Track/Track.flac",  # per-track folder for cover.jpg
        )

    def test_blank_artist(self):
        self.assertEqual(
            device_music_path(Path("/x/y.mp3"), "  "),
            "/Music/Unknown Artist/y/y.mp3",
        )

    def test_illegal_in_filename(self):
        self.assertEqual(
            device_music_path(Path("/x/3:7 final.wav"), "DJ"),
            "/Music/DJ/3-7 final/3-7 final.wav",
        )


class TestIsWithin(unittest.TestCase):
    def test_nested(self):
        self.assertTrue(is_within(Path("/a/b/c"), Path("/a")))

    def test_same(self):
        self.assertTrue(is_within(Path("/a"), Path("/a")))

    def test_sibling(self):
        self.assertFalse(is_within(Path("/a/b"), Path("/a/c")))


if __name__ == "__main__":
    unittest.main()


class PathLengthBudget(unittest.TestCase):
    """FAT32 caps components at 255; Rockbox MAX_PATH is 260 total. Unbounded
    titles must never produce an on-device path the iPod chokes on."""

    def test_component_clamped(self):
        from soundbox.paths import sanitize_component
        out = sanitize_component("X" * 300)
        self.assertLessEqual(len(out), 120)
        self.assertEqual(sanitize_component("Y" * 300, max_len=80),
                         "Y" * 80)

    def test_full_device_path_under_rockbox_max(self):
        from pathlib import Path
        from soundbox.paths import device_music_path
        p = device_music_path(Path("/tmp/" + "T" * 300 + ".flac"),
                              "A" * 300)
        self.assertLess(len(p), 245)  # comfortable margin under 260
        for comp in p.strip("/").split("/"):
            self.assertLessEqual(len(comp), 90)

    def test_short_names_untouched(self):
        from pathlib import Path
        from soundbox.paths import device_music_path
        self.assertEqual(
            device_music_path(Path("/x/Enter The Dragon.wav"), "Zero"),
            "/Music/Zero/Enter The Dragon/Enter The Dragon.wav")


class FileLandedTime(unittest.TestCase):
    """file_landed_time: filesystem birth/creation time, missing → 0.0."""

    def test_missing_file_is_zero(self):
        from soundbox.paths import file_landed_time
        self.assertEqual(file_landed_time("/no/such/file.wav"), 0.0)

    def test_newer_file_has_greater_time(self):
        import tempfile, time
        from pathlib import Path
        from soundbox.paths import file_landed_time
        with tempfile.TemporaryDirectory() as td:
            a = Path(td) / "a.wav"; a.write_bytes(b"a")
            time.sleep(0.05)
            b = Path(td) / "b.wav"; b.write_bytes(b"b")
            self.assertGreater(file_landed_time(b), 0.0)
            self.assertGreaterEqual(file_landed_time(b), file_landed_time(a))
