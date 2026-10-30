"""Library snapshot cache: faithful round-trip, corruption-tolerant load."""

import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from soundbox import libcache
from soundbox.library import Library, LibraryTag, Playlist, Track


def _lib() -> Library:
    tracks = {
        "1": Track("1", "Bravo", "Zed", "file://localhost/tmp/b.wav",
                   bpm="140.00", key="10A", total_time=185, date_added="2026-01-02",
                   added_ts=1_767_000_000.0, genre="Riddim", size=123, bitrate="1411",
                   source_analysis_path="/rekordbox/share/x/ANLZ0000.DAT",
                   source_cues=[{
                       "kind": "hot", "slot": 0, "position_ms": 1000,
                       "end_ms": -1, "type": "single", "label": "DROP",
                       "color": [255, 0, 0],
                   }]),
        "sb-x": Track("sb-x", "Liveset", "", "file://localhost/tmp/set.m4a",
                      total_time=5400, added_ts=1_768_000_000.5, imported=True),
    }
    pls = [Playlist("SET ONE", ["CRATES"], keys=["1", "sb-x"], pid="99")]
    return Library(tracks_by_id=tracks, playlists=pls, source="/x/lib.xml")


class RoundTrip(unittest.TestCase):
    def test_large_snapshot_encodes_records_without_one_uninterruptible_library_dump(self):
        from dataclasses import replace
        lib = _lib()
        track = lib.tracks_by_id['1']
        lib.tracks_by_id = {str(i): replace(track, track_id=str(i), name='曲' * 100)
                            for i in range(1000)}
        encode = json.dumps
        def bounded(value, *args, **kwargs):
            if isinstance(value, dict) and 'tracks' in value:
                raise AssertionError('Whole-library C encoding blocks model callbacks')
            return encode(value, *args, **kwargs)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'cache.json'
            with mock.patch.object(libcache.json, 'dumps', side_effect=bounded):
                libcache.dump(lib, path)
            got = libcache.load(path)
            self.assertEqual(len(got.tracks_by_id), 1000)
            self.assertEqual(got.tracks_by_id['999'].name, '曲' * 100)

    def test_native_catalog_roundtrip_is_bounded_and_corruption_rejects_edit_identity(self):
        from soundbox.rekordbox_playlist_plan import revision
        lib=_lib()
        nodes=[dict(ID=str(i),Name='List',ParentID='root',Seq=i,Attribute=0,rb_local_deleted=0)
               for i in range(1,101)]
        members=[dict(ID=str(i),PlaylistID='1',ContentID='1',TrackNo=i,rb_local_deleted=0)
                 for i in range(1,1001)]
        lib.source_playlist_catalog=dict(nodes=nodes,members=members,revision=revision(nodes,members))
        encode=json.dumps
        def bounded(value,*args,**kwargs):
            if isinstance(value,dict) and 'members' in value:
                raise AssertionError('Native catalog must encode one row at a time')
            return encode(value,*args,**kwargs)
        with tempfile.TemporaryDirectory() as area:
            path=Path(area)/'cache.json'
            with mock.patch.object(libcache.json,'dumps',side_effect=bounded):libcache.dump(lib,path)
            self.assertEqual(libcache.load(path).source_playlist_catalog,lib.source_playlist_catalog)
            data=json.loads(path.read_bytes());data['source_playlist_catalog']['members'][0]['TrackNo']=99
            path.write_text(json.dumps(data));self.assertIsNone(libcache.load(path))

    def test_failed_chunk_encoding_keeps_the_previous_atomic_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'cache.json'
            libcache.dump(_lib(), path)
            before = path.read_bytes()
            broken = _lib()
            broken.tracks_by_id['1'].source_cues = [{'unsupported': object()}]
            with self.assertRaises(TypeError):
                libcache.dump(broken, path)
            self.assertEqual(path.read_bytes(), before)

    def test_cue_revision_and_native_ids_roundtrip_separate_from_display(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'cache.json'
            lib = _lib()
            track = lib.tracks_by_id['1']
            track.source_cue_revision = 'a' * 64
            track.source_cue_refs = [dict(kind='hot', slot=0, id='77', uuid='cue-uuid')]
            libcache.dump(lib, path)
            got = libcache.load(path).tracks_by_id['1']
            self.assertEqual(got.source_cues, track.source_cues)
            self.assertEqual(got.source_cue_revision, track.source_cue_revision)
            self.assertEqual(got.source_cue_refs, track.source_cue_refs)
            data = json.loads(path.read_bytes())
            data['schema'] = 6
            data['tracks'] = [r[:-2] for r in data['tracks']]
            path.write_text(json.dumps(data))
            old = libcache.load(path).tracks_by_id['1']
            self.assertEqual(old.source_cues, track.source_cues)
            self.assertEqual(old.source_cue_revision, '')
            self.assertEqual(old.source_cue_refs, [])
    def test_tag_catalog_membership_and_library_identity_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'cache.json';lib=_lib()
            lib.source_library_id='library'
            lib.source_tags=[LibraryTag('tag','Dubstep','Genre','tag-uuid')]
            lib.tracks_by_id['1'].source_uuid='track-uuid'
            lib.tracks_by_id['1'].source_tag_ids=['tag']
            libcache.dump(lib,path);got=libcache.load(path)
            self.assertEqual(got.source_tags,lib.source_tags)
            self.assertEqual(got.source_library_id,'library')
            self.assertEqual(got.tracks_by_id['1'].source_uuid,'track-uuid')
            self.assertEqual(got.tracks_by_id['1'].source_tag_ids,['tag'])

    def test_dump_load_faithful(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "libcache.json"
            libcache.dump(_lib(), p)
            got = libcache.load(p)
            self.assertIsNotNone(got)
            self.assertEqual(set(got.tracks_by_id), {"1", "sb-x"})
            t = got.tracks_by_id["1"]
            self.assertEqual((t.name, t.artist, t.bpm, t.genre), ("Bravo", "Zed", "140.00", "Riddim"))
            self.assertTrue(got.tracks_by_id["sb-x"].imported)  # imported flag survives
            # added_ts MUST survive — the Collection newest-added sort depends on
            # it; when it was dropped here, instant-paint fell back to birthtime.
            self.assertEqual(got.tracks_by_id["1"].added_ts, 1_767_000_000.0)
            self.assertEqual(got.tracks_by_id["sb-x"].added_ts, 1_768_000_000.5)
            self.assertEqual(got.tracks_by_id["1"].source_analysis_path,
                             "/rekordbox/share/x/ANLZ0000.DAT")
            self.assertEqual(
                got.tracks_by_id["1"].source_cues[0]["label"], "DROP")
            self.assertIsNone(got.tracks_by_id["sb-x"].source_cues)
            self.assertEqual(got.playlists[0].keys, ["1", "sb-x"])
            self.assertEqual(got.playlists[0].pid, "99")

    def test_path_property_reconstructs(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "c.json"
            libcache.dump(_lib(), p)
            got = libcache.load(p)
            self.assertEqual(got.tracks_by_id["sb-x"].path.name, "set.m4a")


class Tolerant(unittest.TestCase):
    def test_v4_snapshot_is_normalized_without_losing_instant_paint(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "c.json"
            old = {
                "schema": 4,
                "source": "/x/rekordbox.xml",
                "tracks": [[
                    "1", "Track", "Artist", "file:///tmp/track.wav",
                    "140.00", "8A", 180, "2026-01-01", 0.0, "", "", 0,
                    "", "", "", False, "/x/ANLZ0000.DAT", [],
                ]],
                "playlists": [["Set", ["Folder"], ["1"], "0", "stable-id"]],
            }
            p.write_text(json.dumps(old), encoding="utf-8")
            got = libcache.load(p)

            self.assertIsNotNone(got)
            self.assertEqual(got.playlists[0].pid, "stable-id")
            self.assertEqual(got.tracks_by_id["1"].source_analysis_path,
                             "/x/ANLZ0000.DAT")

    def test_missing_file_is_none(self):
        self.assertIsNone(libcache.load("/no/such/libcache.json"))

    def test_corrupt_json_is_none(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "c.json"; p.write_text("{not json")
            self.assertIsNone(libcache.load(p))

    def test_wrong_schema_is_none(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "c.json"; p.write_text(json.dumps({"schema": 999, "tracks": []}))
            self.assertIsNone(libcache.load(p))

    def test_empty_library_is_none(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "c.json"
            libcache.dump(Library(tracks_by_id={}, playlists=[], source="x"), p)
            self.assertIsNone(libcache.load(p))  # nothing to paint → treat as no cache


if __name__ == "__main__":
    unittest.main()
