"""The live-DB reader's SACRED invariants, tested against mock pyrekordbox rows
(no real master.db needed — the logic is pure over row objects):
  • track order inside a playlist = DjmdSongPlaylist.TrackNo, never ContentID
  • sidebar tree order = Seq per folder level, depth-first from 'root'
  • smart playlists (Attribute 4) come from get_playlist_contents
  • rb_local_deleted rows are skipped everywhere (tracks, nodes, membership)
  • cloud nodes (Attribute -128) are skipped
"""

import unittest

from soundbox.rekordbox_db import (
    _hot_cue_slot, _read_cues, _read_playlists, _read_tracks,
)


class Row:
    """A duck-typed pyrekordbox row."""
    def __init__(self, **kw):
        self.rb_local_deleted = 0
        self.__dict__.update(kw)


class Named:
    def __init__(self, name):
        self.Name = name
        self.ScaleName = name


class FakeDB:
    def __init__(self, tracks=(), nodes=(), members=(), smart=None, cues=()):
        self._tracks = list(tracks)
        self._nodes = list(nodes)
        self._members = list(members)
        self._smart = smart or {}
        self._cues = list(cues)

    def get_content(self):
        return self._tracks

    def get_playlist(self):
        return self._nodes

    def query(self, _cls):
        db = self

        class Q:
            def all(self):
                return db._members
        return Q()

    def get_playlist_contents(self, p):
        return self._smart.get(str(p.ID), [])

    def get_cue(self):
        return self._cues


def _node(id, name, parent="root", attr=0, seq=0, deleted=0):
    return Row(ID=id, Name=name, ParentID=parent, Attribute=attr, Seq=seq,
               rb_local_deleted=deleted)


def _member(pl, content, trackno, deleted=0):
    return Row(PlaylistID=pl, ContentID=content, TrackNo=trackno,
               rb_local_deleted=deleted)


class SacredOrder(unittest.TestCase):
    def test_track_order_is_trackno_not_contentid(self):
        # ContentIDs descend while TrackNo ascends — TrackNo must win.
        db = FakeDB(nodes=[_node("10", "SET")],
                    members=[_member("10", "999", 1), _member("10", "500", 2),
                             _member("10", "100", 3)])
        (pl,) = _read_playlists(db)
        self.assertEqual(pl.keys, ["999", "500", "100"])

    def test_tree_order_is_seq_depth_first(self):
        db = FakeDB(nodes=[
            _node("f1", "CRATES", attr=1, seq=2),
            _node("p_top", "TOP", seq=1),
            _node("p_in_a", "A", parent="f1", seq=2),
            _node("p_in_b", "B", parent="f1", seq=1),
        ])
        out = _read_playlists(db)
        self.assertEqual([(p.display_path) for p in out],
                         ["TOP", "CRATES / B", "CRATES / A"])
        self.assertEqual(out[1].pid, "p_in_b")

    def test_smart_playlists_use_computed_contents(self):
        db = FakeDB(nodes=[_node("s1", "SMART", attr=4)],
                    smart={"s1": [Row(ID=7), Row(ID=3)]})
        (pl,) = _read_playlists(db)
        self.assertEqual(pl.keys, ["7", "3"])

    def test_soft_deleted_and_cloud_skipped(self):
        db = FakeDB(nodes=[
            _node("10", "KEEP", seq=1),
            _node("11", "GONE", seq=2, deleted=1),
            _node("12", "CLOUD", attr=-128, seq=3),
        ], members=[_member("10", "1", 1), _member("10", "2", 2, deleted=1)])
        out = _read_playlists(db)
        self.assertEqual([p.name for p in out], ["KEEP"])
        self.assertEqual(out[0].keys, ["1"])  # deleted membership row skipped


class TrackMapping(unittest.TestCase):
    def _track(self, **kw):
        base = dict(ID=265095720, Title="T", Artist=Named("A"),
                    FolderPath="/Users/x/My Music/Track (VIP).wav",
                    AnalysisDataPath="/PIONEER/USBANLZ/x/ANLZ0000.DAT",
                    BPM=14567, Key=Named("Gm"), Length=200,
                    DateCreated="2026-01-02 03:04", Genre=Named("Dub"),
                    FileSize=9, BitRate=1411, Rating=0)
        base.update(kw)
        return Row(**base)

    def test_field_mapping(self):
        out = _read_tracks(FakeDB(tracks=[self._track()]))
        t = out["265095720"]
        self.assertEqual(t.bpm, "145.67")           # rekordbox stores BPM*100
        self.assertEqual(t.key, "6A")               # Gm → Camelot
        self.assertEqual(t.date_added, "2026-01-02")
        self.assertIn("Track%20%28VIP%29.wav", t.location_uri)  # percent-encoded
        self.assertEqual(str(t.path), "/Users/x/My Music/Track (VIP).wav")
        self.assertTrue(t.analysis_path.endswith(
            "/rekordbox/share/PIONEER/USBANLZ/x/ANLZ0000.DAT"))

    def test_deleted_track_skipped(self):
        out = _read_tracks(FakeDB(tracks=[self._track(rb_local_deleted=1)]))
        self.assertEqual(out, {})

    def test_live_db_authoritative_empty_cues(self):
        out = _read_tracks(FakeDB(tracks=[self._track()]))
        self.assertEqual(out["265095720"].rekordbox_cues, [])


class CueMapping(unittest.TestCase):
    def test_rekordbox_reserved_kind_gap_maps_exact_a_through_p(self):
        expected = {
            1: 0, 2: 1, 3: 2,
            5: 3, 6: 4, 7: 5, 8: 6, 9: 7,
            10: 8, 11: 9, 12: 10, 13: 11,
            14: 12, 15: 13, 16: 14, 17: 15,
        }
        self.assertEqual(
            {kind: _hot_cue_slot(kind) for kind in expected}, expected)
        for reserved in (-1, 0, 4, 18, 999):
            self.assertIsNone(_hot_cue_slot(reserved))

    def test_hot_memory_loop_comment_and_overflow(self):
        rows = [
            Row(ContentID=7, Kind=10, InMsec=1200, OutMsec=-1,
                Comment="I"),
            Row(ContentID=7, Kind=4, InMsec=777, OutMsec=-1,
                Comment="reserved"),
            Row(ContentID=7, Kind=0, InMsec=900, OutMsec=-1,
                Comment="later"),
            Row(ContentID=7, Kind=0, InMsec=100, OutMsec=400,
                Comment="loop"),
            Row(ContentID=8, Kind=1, InMsec=1, OutMsec=-1,
                Comment="", rb_local_deleted=1),
        ]
        cues = _read_cues(FakeDB(cues=rows))["7"]
        self.assertEqual(cues[0]["kind"], "hot")
        self.assertEqual(cues[0]["slot"], 8)  # I retained, not demoted
        self.assertNotIn(777, [cue["position_ms"] for cue in cues])
        memory = [cue for cue in cues if cue["kind"] == "memory"]
        self.assertEqual([cue["slot"] for cue in memory], [0, 1])
        self.assertEqual(memory[0]["position_ms"], 100)
        self.assertEqual(memory[0]["end_ms"], 400)
        self.assertEqual(memory[0]["type"], "loop")
        self.assertEqual(memory[0]["label"], "loop")
        self.assertNotIn("8", cues)

    def test_explicit_rgb_and_deterministic_fallback(self):
        rows = [
            Row(ContentID="x", Kind=1, InMsec=0, OutMsec=-1, Comment="",
                ColorRed=1, ColorGreen=2, ColorBlue=3),
            Row(ContentID="x", Kind=2, InMsec=1, OutMsec=-1, Comment=""),
        ]
        cues = _read_cues(FakeDB(cues=rows))["x"]
        self.assertEqual(cues[0]["color"], [1, 2, 3])
        self.assertEqual(cues[1]["color"], [69, 172, 219])

    def test_live_kind_values_keep_d_through_h_in_their_letters(self):
        rows = [
            Row(ContentID="x", Kind=kind, InMsec=slot * 100,
                OutMsec=-1, Comment=chr(ord("A") + slot))
            for kind, slot in (
                (1, 0), (2, 1), (3, 2), (5, 3),
                (6, 4), (7, 5), (8, 6), (9, 7))
        ]
        cues = _read_cues(FakeDB(cues=rows))["x"]
        self.assertEqual(
            [(cue["slot"], cue["label"], cue["position_ms"]) for cue in cues],
            [(slot, chr(ord("A") + slot), slot * 100)
             for slot in range(8)],
        )


if __name__ == "__main__":
    unittest.main()
