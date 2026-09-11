"""Property-based tests (hypothesis) for the device-safety invariants — the
functions where ONE weird unicode edge case = a broken file on the iPod.
Test-only dependency; the app never imports hypothesis."""

import unittest
from pathlib import Path

from hypothesis import given, settings, strategies as st

from soundbox import manifest
from soundbox.metadata import neat_title, tidy_title
from soundbox.paths import device_music_path, sanitize_component
from soundbox.playlists import M3UEntry, render_m3u8
from soundbox.library import Playlist

_ILLEGAL = set('<>:"/\\|?*')
text = st.text(max_size=300)


class DevicePathInvariants(unittest.TestCase):
    @given(text)
    @settings(max_examples=300, derandomize=True)
    def test_sanitize_always_fat_safe(self, s):
        out = sanitize_component(s)
        self.assertTrue(out)                       # never empty
        self.assertLessEqual(len(out), 120)        # never over budget
        self.assertFalse(set(out) & _ILLEGAL)      # never an illegal char
        self.assertTrue(all(c.isprintable() for c in out))  # no \x85/\u2028 etc.
        self.assertFalse(out.endswith((" ", ".")))  # FAT drops these silently

    @given(title=text, artist=text)
    @settings(max_examples=200, derandomize=True)
    def test_device_path_always_within_rockbox_budget(self, title, artist):
        p = device_music_path(Path("/x/src.wav"), artist, name_override=title)
        self.assertLess(len(p), 245)               # under Rockbox MAX_PATH 260
        self.assertTrue(p.startswith("/Music/"))
        self.assertTrue(p.endswith(".wav"))        # extension always preserved
        for comp in p.strip("/").split("/"):
            self.assertTrue(comp)
            self.assertFalse(set(comp) & _ILLEGAL)


class TitlePipelineInvariants(unittest.TestCase):
    @given(name=st.text(min_size=1, max_size=200).filter(
               lambda s: __import__("re").search(r"[^\s\-–—_]", s)), artist=text)
    @settings(max_examples=300, derandomize=True)
    def test_titles_never_vanish(self, name, artist):
        # Any name with real content yields a non-empty title. (Pure-junk names
        # may empty out — export then falls back to the disk stem by design.)
        out = tidy_title(neat_title(name, artist))
        self.assertTrue(out.strip())

    @given(name=st.from_regex(r"[A-Za-z0-9]([A-Za-z0-9 ]{0,60}[A-Za-z0-9])?",
                              fullmatch=True))
    @settings(max_examples=200, derandomize=True)
    def test_tidy_with_no_rules_preserves_clean_names(self, name):
        # With every rule deleted, a clean name passes through untouched
        # (tidy still collapses runs of spaces — that's edge grooming, not junk).
        import re
        self.assertEqual(tidy_title(name, []), re.sub(r"\s{2,}", " ", name))


class SigAndPlaylistInvariants(unittest.TestCase):
    @given(keys=st.lists(st.text(min_size=1, max_size=8), max_size=30),
           hidden=st.sets(st.text(min_size=1, max_size=8), max_size=10))
    @settings(max_examples=200, derandomize=True)
    def test_sig_deterministic_and_veto_sensitive(self, keys, hidden):
        pl = Playlist("P", [], keys=keys, pid="1")
        a = manifest.playlist_sig(pl, hidden)
        self.assertEqual(a, manifest.playlist_sig(pl, hidden))  # stable
        # vetoing has an effect iff it removes a present key
        if hidden & set(keys):
            self.assertNotEqual(a, manifest.playlist_sig(pl, frozenset()))
        else:
            self.assertEqual(a, manifest.playlist_sig(pl, frozenset()))

    @given(st.lists(st.tuples(text, text, text), max_size=20))
    @settings(max_examples=200, derandomize=True)
    def test_m3u8_always_parseable(self, rows):
        entries = [M3UEntry(f"/Music/{sanitize_component(a or 'X')}/{i}.wav"
                            if i % 2 == 0 else None, a, n)
                   for i, (a, n, _x) in enumerate(rows)]
        out = render_m3u8(entries)
        lines = out.splitlines()
        self.assertEqual(lines[0], "#EXTM3U")
        for ln in lines[1:]:
            self.assertTrue(ln.startswith("/") or ln.startswith("#"))


if __name__ == "__main__":
    unittest.main()
