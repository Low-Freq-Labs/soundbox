"""The live-DB reader's SACRED invariants, tested against mock pyrekordbox rows
(no real master.db needed — the logic is pure over row objects):
  • track order inside a playlist = DjmdSongPlaylist.TrackNo, never ContentID
  • sidebar tree order = Seq per folder level, depth-first from 'root'
  • smart playlists (Attribute 4) are computed, with active My Tag endpoints
  • rb_local_deleted rows are skipped everywhere (tracks, nodes, membership,
    tag definitions, and tag associations)
  • cloud nodes (Attribute -128) are skipped
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from soundbox.rekordbox_db import (
    _copy_stable_snapshot, _hot_cue_slot, _read_cues, _read_playlists, _read_tags, _read_tracks,
    _smart_playlist_contents,
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
    def __init__(self, tracks=(), nodes=(), members=(), smart=None, cues=(), tags=(), tag_links=()):
        self._tracks = list(tracks)
        self._nodes = list(nodes)
        self._members = list(members)
        self._smart = smart or {}
        self._cues = list(cues)
        self._tags = list(tags)
        self._tag_links = list(tag_links)

    def get_my_tag(self):
        return self._tags

    def get_my_tag_songs(self):
        return self._tag_links

    def get_property(self):
        return [Row(DBID="synthetic-library")]

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
    def test_unreferenced_track_is_not_normalized(self):
        # Missing metadata would fail normalization; excluded tracks need only ID.
        unwanted = Row(ID='unreferenced')
        self.assertEqual(_read_tracks(FakeDB(tracks=[unwanted]), track_ids=set()), {})

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

    def test_mytag_smart_playlist_requires_active_association(self):
        class Query:
            def __init__(self):
                self.clause = None

            def filter(self, clause):
                self.clause = clause
                return self

        class DB:
            def __init__(self):
                self.query_result = Query()

            def query(self, _entity):
                return self.query_result

        db = DB()
        playlist = Row(SmartList=(
            '<NODE Id="1" LogicalOperator="1" AutomaticUpdate="0">'
            '<CONDITION PropertyName="myTag" Operator="8" ValueUnit="" '
            'ValueLeft="-2019392369" ValueRight=""/>'
            '</NODE>'
        ))
        result = _smart_playlist_contents(db, playlist)
        self.assertIs(result, db.query_result)
        sql = str(result.clause.compile(compile_kwargs={"literal_binds": True}))
        self.assertIn('"djmdSongMyTag".rb_local_deleted', sql)
        self.assertIn('"djmdMyTag".rb_local_deleted', sql)
        self.assertIn('"djmdContent".rb_local_deleted', sql)
        self.assertIn('coalesce', sql.lower())
        self.assertIn("2275574927", sql)  # signed XML ID restored losslessly

    def test_mytag_not_contains_checks_absence_of_active_association(self):
        class Query:
            def __init__(self):
                self.clause = None

            def filter(self, clause):
                self.clause = clause
                return self

        class DB:
            def __init__(self):
                self.query_result = Query()

            def query(self, _entity):
                return self.query_result

        db = DB()
        playlist = Row(SmartList=(
            '<NODE Id="1" LogicalOperator="1" AutomaticUpdate="0">'
            '<CONDITION PropertyName="myTag" Operator="9" ValueUnit="" '
            'ValueLeft="1039281992" ValueRight=""/>'
            '</NODE>'
        ))
        result = _smart_playlist_contents(db, playlist)
        sql = str(result.clause.compile(compile_kwargs={"literal_binds": True}))
        self.assertIn("NOT ((EXISTS", sql)
        self.assertIn('"djmdMyTag".rb_local_deleted', sql)
        self.assertIn('"djmdSongMyTag".rb_local_deleted', sql)

    def test_soft_deleted_and_cloud_skipped(self):
        db = FakeDB(nodes=[
            _node("10", "KEEP", seq=1),
            _node("11", "GONE", seq=2, deleted=1),
            _node("12", "CLOUD", attr=-128, seq=3),
        ], members=[_member("10", "1", 1), _member("10", "2", 2, deleted=1)])
        out = _read_playlists(db)
        self.assertEqual([p.name for p in out], ["KEEP"])
        self.assertEqual(out[0].keys, ["1"])  # deleted membership row skipped

    def test_memberships_of_inactive_tracks_are_skipped(self):
        db = FakeDB(nodes=[_node("10", "KEEP")],
                    members=[_member("10", "1", 1), _member("10", "2", 2)])
        out = _read_playlists(db, {"1"})
        self.assertEqual(out[0].keys, ["1"])

    def test_inactive_smart_results_are_skipped(self):
        db = FakeDB(nodes=[_node("s1", "SMART", attr=4)],
                    smart={"s1": [Row(ID="1"), Row(ID="2")]})
        out = _read_playlists(db, {"1"})
        self.assertEqual(out[0].keys, ["1"])

    def test_duplicate_track_numbers_fail_closed(self):
        db = FakeDB(nodes=[_node("10", "BROKEN")], members=[
            _member("10", "1", 1), _member("10", "2", 1),
        ])
        with self.assertRaisesRegex(ValueError, "duplicate TrackNo"):
            _read_playlists(db)

    def test_deleted_playlist_history_is_not_validated_as_live(self):
        db = FakeDB(nodes=[_node("10", "GONE", deleted=1)], members=[
            _member("10", "1", 1), _member("10", "2", 1),
        ])
        self.assertEqual(_read_playlists(db), [])

    def test_duplicate_sibling_sequences_fail_closed(self):
        db = FakeDB(nodes=[
            _node("10", "ONE", seq=1), _node("11", "TWO", seq=1),
        ])
        with self.assertRaisesRegex(ValueError, "duplicate sequences"):
            _read_playlists(db)

    def test_orphaned_active_playlist_fails_closed(self):
        db = FakeDB(nodes=[_node("10", "ORPHAN", parent="missing")])
        with self.assertRaisesRegex(ValueError, "no active parent folder"):
            _read_playlists(db)

    def test_unknown_active_playlist_type_fails_closed(self):
        db = FakeDB(nodes=[_node("10", "MYSTERY", attr=99)])
        with self.assertRaisesRegex(ValueError, "Unknown active rekordbox playlist type"):
            _read_playlists(db)

    def test_duplicate_display_paths_fail_closed(self):
        db = FakeDB(nodes=[
            _node("10", "SAME", seq=1),
            _node("11", "SAME", seq=2),
        ])
        with self.assertRaisesRegex(ValueError, "duplicate display paths"):
            _read_playlists(db)

    def test_unverified_smart_field_fails_instead_of_loading_every_track(self):
        node = _node("s1", "GROUPING", attr=4)
        node.SmartList = (
            '<NODE Id="1" LogicalOperator="1" AutomaticUpdate="0">'
            '<CONDITION PropertyName="grouping" Operator="1" ValueUnit="" '
            'ValueLeft="x" ValueRight=""/>'
            '</NODE>'
        )
        db = FakeDB(nodes=[node])
        with self.assertRaisesRegex(RuntimeError, "Could not evaluate rekordbox"):
            _read_playlists(db)


class TrackMapping(unittest.TestCase):
    def _track(self, **kw):
        base = dict(ID=265095720, Title="T", Artist=Named("A"),
                    FolderPath="/Users/x/My Music/Track (VIP).wav",
                    AnalysisDataPath="/PIONEER/USBANLZ/x/ANLZ0000.DAT",
                    BPM=14567, Key=Named("Gm"), Length=200,
                    DateCreated="2026-01-02 03:04", Genre=Named("Dub"),
                    FileSize=9, BitRate=1411, Rating=0, ReleaseYear=2026)
        base.update(kw)
        return Row(**base)

    def test_field_mapping(self):
        out = _read_tracks(FakeDB(tracks=[self._track()]))
        t = out["265095720"]
        self.assertEqual(t.bpm, "145.67")           # rekordbox stores BPM*100
        self.assertEqual(t.key, "6A")               # Gm → Camelot
        self.assertEqual(t.date_added, "2026-01-02")
        self.assertEqual(t.year, "2026")
        self.assertIn("Track%20%28VIP%29.wav", t.location_uri)  # percent-encoded
        self.assertEqual(str(t.path), "/Users/x/My Music/Track (VIP).wav")
        self.assertTrue(t.source_analysis_path.endswith(
            "/rekordbox/share/PIONEER/USBANLZ/x/ANLZ0000.DAT"))

    def test_deleted_track_skipped(self):
        out = _read_tracks(FakeDB(tracks=[self._track(rb_local_deleted=1)]))
        self.assertEqual(out, {})

    def test_live_db_authoritative_empty_cues(self):
        out = _read_tracks(FakeDB(tracks=[self._track()]))
        self.assertEqual(out["265095720"].source_cues, [])

    def test_selected_database_owns_the_analysis_root(self):
        source = Path("/synthetic/relocated/rekordbox/master.db")
        out = _read_tracks(FakeDB(tracks=[self._track()]), source)
        self.assertEqual(out["265095720"].source_analysis_path,
                         "/synthetic/relocated/rekordbox/share/PIONEER/USBANLZ/x/ANLZ0000.DAT")


class SelectedDatabase(unittest.TestCase):
    def test_explicit_database_snapshots_only_selected_source_and_never_modifies_it(self):
        from soundbox import rekordbox_db
        with tempfile.TemporaryDirectory() as area:
            source = Path(area) / "relocated" / "master.db"
            source.parent.mkdir()
            source.write_bytes(b"selected database")
            wal = source.with_name("master.db-wal")
            wal.write_bytes(b"matching WAL")
            shm = source.with_name("master.db-shm")
            shm.write_bytes(b"source locking state")
            before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in (source, wal, shm)}
            snapshots = []
            def open_snapshot(*, path):
                snapshot = Path(path)
                self.assertNotEqual(snapshot, source)
                self.assertEqual(snapshot.read_bytes(), b"selected database")
                self.assertEqual(snapshot.with_name("master.db-wal").read_bytes(), b"matching WAL")
                self.assertFalse(snapshot.with_name("master.db-shm").exists())
                snapshots.append(snapshot)
                return FakeDB()
            with mock.patch.object(rekordbox_db, "REKORDBOX_DB", Path(area) / "missing-default.db"), \
                    mock.patch("pyrekordbox.Rekordbox6Database", side_effect=open_snapshot), \
                    mock.patch.object(rekordbox_db, "_read_playlists", return_value=[]):
                self.assertFalse(rekordbox_db.db_available())
                self.assertTrue(rekordbox_db.db_available(source))
                library = rekordbox_db.load_live(source)
            self.assertEqual(library.source, source)
            self.assertEqual(library.source_kind, "rekordbox-db")
            self.assertEqual({path: (path.read_bytes(), path.stat().st_mtime_ns) for path in before}, before)
            self.assertTrue(snapshots)
            self.assertFalse(snapshots[0].parent.exists())

    def test_collection_pin_keeps_only_the_same_active_native_uuid_after_last_membership_removal(self):
        from soundbox import rekordbox_db
        track=TrackMapping()._track(UUID='expected-native-uuid')
        db=FakeDB(tracks=[track]);db.close=lambda:None
        with tempfile.TemporaryDirectory() as area:
            source=Path(area)/'master.db';source.write_bytes(b'private synthetic source')
            with mock.patch('pyrekordbox.Rekordbox6Database',return_value=db), \
                    mock.patch.object(rekordbox_db,'_playlist_catalog',return_value={}), \
                    mock.patch.object(rekordbox_db,'_populate_cue_edit_state'):
                lib=rekordbox_db.load_live(source)
                self.assertEqual(lib.tracks_by_id,{})
                lib=rekordbox_db.load_live(source,retain_tracks={'synthetic-library':{'265095720':'expected-native-uuid'}})
                self.assertEqual(set(lib.tracks_by_id),{'265095720'})
                wrong=rekordbox_db.load_live(source,retain_tracks={'synthetic-library':{'265095720':'replaced-uuid'}})
                self.assertEqual(wrong.tracks_by_id,{})
                foreign=rekordbox_db.load_live(source,retain_tracks={'another-library':{'265095720':'expected-native-uuid'}})
                self.assertEqual(foreign.tracks_by_id,{})
                track.rb_local_deleted=1
                deleted=rekordbox_db.load_live(source,retain_tracks={'synthetic-library':{'265095720':'expected-native-uuid'}})
                self.assertEqual(deleted.tracks_by_id,{})


class MyTags(unittest.TestCase):
    def test_flat_order_uses_categories_and_leaf_sequence_without_merging_names(self):
        categories = [Row(ID='c2',Name='Components',Attribute=1,ParentID='root',Seq=2),
                      Row(ID='c1',Name='Genre',Attribute=1,ParentID='root',Seq=1)]
        leaves = [Row(ID='b',UUID='ub',Name='Dubstep',Attribute=0,ParentID='c2',Seq=1),
                  Row(ID='a2',UUID='ua2',Name='Riddim',Attribute=0,ParentID='c1',Seq=2),
                  Row(ID='a1',UUID='ua1',Name='Dubstep',Attribute=0,ParentID='c1',Seq=1)]
        tags, _ = _read_tags(FakeDB(tags=categories+leaves))
        self.assertEqual([tag.tag_id for tag in tags], ['a1','a2','b'])
        self.assertEqual([tag.name for tag in tags], ['Dubstep','Riddim','Dubstep'])
        self.assertEqual(tags[0].source_uuid, 'ua1')

    def test_deleted_tag_definitions_and_assignments_do_not_resurrect_memberships(self):
        tags = [Row(ID='c',Name='Genre',Attribute=1,ParentID='root',Seq=1),
                Row(ID='a',Name='Dubstep',Attribute=0,ParentID='c',Seq=1),
                Row(ID='b',Name='Gone',Attribute=0,ParentID='c',Seq=2,rb_local_deleted=1)]
        links = [Row(ContentID='1',MyTagID='a'),
                 Row(ContentID='2',MyTagID='a',rb_local_deleted=1),
                 Row(ContentID='1',MyTagID='b')]
        definitions, memberships = _read_tags(FakeDB(tags=tags,tag_links=links))
        self.assertEqual([tag.tag_id for tag in definitions], ['a'])
        self.assertEqual(memberships, {'1':['a']})


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


class StableSnapshot(unittest.TestCase):
    def test_nonzero_rollback_journal_blocks_snapshot_without_touching_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / 'master.db'
            destination = Path(td) / 'snapshot.db'
            source.write_bytes(b'unchanged main database')
            journal = source.with_name('master.db-journal')
            payload = b'\xd9\xd5\x05\xf9\x20\xa1\x63\xd7' + b'pending recovery' * 100
            journal.write_bytes(payload)
            with self.assertRaisesRegex(RuntimeError, 'needs recovery'):
                _copy_stable_snapshot(source, destination)
            self.assertFalse(destination.exists())
            self.assertEqual(journal.read_bytes(), payload)
            self.assertEqual(source.read_bytes(), b'unchanged main database')

    def test_zero_header_inactive_journal_remains_untouched(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / 'master.db'
            destination = Path(td) / 'snapshot.db'
            source.write_bytes(b'consistent database')
            journal = source.with_name('master.db-journal')
            payload = b'\0' * 8 + b'inactive payload'
            journal.write_bytes(payload)
            _copy_stable_snapshot(source, destination)
            self.assertEqual(destination.read_bytes(), source.read_bytes())
            self.assertEqual(journal.read_bytes(), payload)

    def test_copies_main_and_wal_but_never_live_shm(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "master.db"
            destination = root / "copy" / "master.db"
            destination.parent.mkdir()
            source.write_bytes(b"main")
            source.with_name("master.db-wal").write_bytes(b"wal")
            source.with_name("master.db-shm").write_bytes(b"live locks")
            _copy_stable_snapshot(source, destination)
            self.assertEqual(destination.read_bytes(), b"main")
            self.assertEqual(
                destination.with_name("master.db-wal").read_bytes(), b"wal")
            self.assertFalse(destination.with_name("master.db-shm").exists())

    def test_retries_when_source_changes_during_copy(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "master.db"
            destination = root / "copy.db"
            source.write_bytes(b"first")
            original = shutil.copy2
            main_copies = 0

            def changing_copy(src, dst):
                nonlocal main_copies
                result = original(src, dst)
                if Path(src) == source:
                    main_copies += 1
                    if main_copies == 1:
                        source.write_bytes(b"second")
                return result

            with mock.patch(
                    "soundbox.rekordbox_db.shutil.copy2",
                    side_effect=changing_copy):
                _copy_stable_snapshot(source, destination, retries=2)
            self.assertEqual(main_copies, 2)
            self.assertEqual(destination.read_bytes(), b"second")

    def test_retries_when_wal_disappears_mid_copy(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "master.db"
            wal = root / "master.db-wal"
            destination = root / "copy.db"
            source.write_bytes(b"main")
            wal.write_bytes(b"old wal")
            original = shutil.copy2
            wal_attempts = 0

            def checkpointing_copy(src, dst):
                nonlocal wal_attempts
                if Path(src) == wal:
                    wal_attempts += 1
                    if wal_attempts == 1:
                        wal.unlink()
                        raise FileNotFoundError(wal)
                return original(src, dst)

            with mock.patch(
                    "soundbox.rekordbox_db.shutil.copy2",
                    side_effect=checkpointing_copy):
                _copy_stable_snapshot(source, destination, retries=2)
            self.assertEqual(wal_attempts, 1)
            self.assertEqual(destination.read_bytes(), b"main")
            self.assertFalse(destination.with_name("copy.db-wal").exists())

if __name__ == "__main__":
    unittest.main()
