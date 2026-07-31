import unittest
from pathlib import Path

from soundbox.library import Library, Playlist, Track


def T(tid, *, bpm="", key="", secs=200):
    return Track(track_id=tid, name=tid, artist="A", location_uri="",
                 bpm=bpm, key=key, total_time=secs)


class TestUntrackedRule(unittest.TestCase):
    def test_real_track_never_hidden(self):
        real = T("real", bpm="140.00", key="", secs=0)  # keyless + 0s, but HAS a bpm
        for nokey in (True, False):
            for short in (True, False):
                self.assertFalse(real.is_untracked(nokey=nokey, short=short, short_secs=60))

    def test_nokey_rule(self):
        marker = T("---", bpm="", key="", secs=200)      # no bpm, keyless, long
        self.assertTrue(marker.is_untracked(nokey=True, short=False))
        self.assertFalse(marker.is_untracked(nokey=False, short=False))  # rule off -> keep

    def test_short_rule_and_threshold(self):
        stub = T("stub", bpm="", key="7A", secs=1)       # no bpm, HAS key, 1s
        self.assertTrue(stub.is_untracked(nokey=False, short=True, short_secs=5))
        self.assertFalse(stub.is_untracked(nokey=False, short=False))     # rule off -> keep
        ten = T("ten", bpm="", key="7A", secs=10)
        self.assertFalse(ten.is_untracked(nokey=False, short=True, short_secs=5))   # 10 >= 5
        self.assertTrue(ten.is_untracked(nokey=False, short=True, short_secs=15))   # 10 < 15

    def test_keyed_full_length_no_bpm_kept(self):
        pending = T("pending", bpm="", key="7A", secs=200)  # not-yet-analyzed real track
        self.assertFalse(pending.is_untracked(nokey=True, short=True, short_secs=5))

    def test_both_sub_rules_off_hides_nothing(self):
        marker = T("---", bpm="", key="", secs=1)  # the junkiest possible entry
        self.assertFalse(marker.is_untracked(nokey=False, short=False))

    def test_resolve_respects_master_and_rules(self):
        tracks = {t.track_id: t for t in (
            T("real", bpm="140.00", key="", secs=1),
            T("---", bpm="", key="", secs=200),   # keyless marker
            T("stub", bpm="", key="7A", secs=1),  # short stub
        )}
        pl = Playlist(name="P", folder_path=[], keys=["real", "---", "stub"])
        lib = Library(tracks_by_id=tracks, playlists=[pl], source=Path("x"))

        lib.hide_untracked = False
        self.assertEqual([t.track_id for t in lib.resolve(pl)], ["real", "---", "stub"])

        lib.hide_untracked = True  # defaults: nokey + short + 5s -> both junk gone
        self.assertEqual([t.track_id for t in lib.resolve(pl)], ["real"])

        lib.hide_short = False  # only keyless hidden now; the keyed short stub stays
        self.assertEqual([t.track_id for t in lib.resolve(pl)], ["real", "stub"])


if __name__ == "__main__":
    unittest.main()


class ZeroSecondDividers(unittest.TestCase):
    def test_zero_length_keyed_divider_is_hidden(self):
        # "*** SINGLES ***": no BPM, HAS a key, 0:00 — slipped the old '0 <' guard.
        t = Track("1", "*** SINGLES ***", "", "file://localhost/x.wav",
                  bpm="", key="4A", total_time=0)
        self.assertTrue(t.is_untracked(nokey=True, short=True, short_secs=5))
        # short rule alone catches it too (nokey off)
        self.assertTrue(t.is_untracked(nokey=False, short=True, short_secs=5))
        # but with the short rule OFF and a key present, it stays (user's choice)
        self.assertFalse(t.is_untracked(nokey=True, short=False, short_secs=5))

    def test_real_track_with_bpm_never_hidden(self):
        t = Track("2", "REAL", "", "file://localhost/x.wav",
                  bpm="140.00", key="", total_time=0)
        self.assertFalse(t.is_untracked(nokey=True, short=True, short_secs=5))
