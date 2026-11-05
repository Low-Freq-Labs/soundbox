import json
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from soundbox import manifest
from soundbox.library import Library, Playlist


def PL(pid, name, folder, keys):
    return Playlist(name=name, folder_path=folder, keys=list(keys), pid=pid)


def lib_of(*pls):
    return Library(tracks_by_id={}, playlists=list(pls), source=Path("x"))


class TestManifest(unittest.TestCase):
    def test_manifest_reads_and_save_refuse_redirected_state_directory(self):
        for destination in ("PIONEER", "outside"):
            with self.subTest(destination=destination), tempfile.TemporaryDirectory() as td:
                base = Path(td)
                root = base / "IPOD"
                root.mkdir()
                forbidden = root / "PIONEER" if destination == "PIONEER" else base / "outside"
                forbidden.mkdir()
                payload = forbidden / "manifest.json"
                payload.write_bytes(b"protected native bytes")
                (root / ".soundbox").symlink_to(forbidden, target_is_directory=True)
                for reader in (manifest.load, manifest.load_composite,
                               manifest.has_composite_ownership):
                    with mock.patch.object(Path, "open", side_effect=AssertionError("no payload read")):
                        with self.assertRaises(RuntimeError):
                            reader(root)
                with self.assertRaises(RuntimeError):
                    manifest.save(root, {"schema": 2, "playlists": {}, "files": {}})
                self.assertEqual(payload.read_bytes(), b"protected native bytes")
                self.assertEqual(list(forbidden.iterdir()), [payload])

    def test_manifest_file_symlink_never_reads_pioneer_payload(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            forbidden = root / "PIONEER/export.pdb"
            forbidden.parent.mkdir()
            forbidden.write_bytes(b"protected native bytes")
            path = manifest.manifest_path(root)
            path.parent.mkdir()
            path.symlink_to(forbidden)
            with mock.patch.object(Path, "open", side_effect=AssertionError("no payload read")):
                for reader in (manifest.load, manifest.load_composite,
                               manifest.has_composite_ownership):
                    with self.assertRaises(RuntimeError):
                        reader(root)

    def test_external_pioneer_ownership_is_discarded_without_touching_payload(self):
        root = Path(tempfile.mkdtemp())
        pioneer = root / "PIONEER" / "rekordbox" / "export.pdb"
        pioneer.parent.mkdir(parents=True)
        pioneer.write_bytes(b"rekordbox-owned")
        path = manifest.manifest_path(root)
        path.parent.mkdir()
        path.write_text(json.dumps({
            "schema": manifest.SCHEMA,
            "playlists": {},
            "files": {
                "/PIONEER/rekordbox/export.pdb": {
                    "kind": "track", "owned": True,
                },
                "/Music/SoundBox/song.wav": {
                    "kind": "track", "owned": True,
                },
            },
        }), encoding="utf-8")

        loaded = manifest.load(root)

        self.assertNotIn("/PIONEER/rekordbox/export.pdb", loaded["files"])
        self.assertIn("/Music/SoundBox/song.wav", loaded["files"])
        self.assertEqual(pioneer.read_bytes(), b"rekordbox-owned")

    def test_manifest_save_refuses_external_pioneer_ownership(self):
        root = Path(tempfile.mkdtemp())
        payload = {
            "schema": manifest.SCHEMA,
            "playlists": {},
            "files": {
                "/PIONEER/rekordbox/export.pdb": {"owned": True},
                "/Music/SoundBox/song.wav": {"owned": True},
            },
        }

        manifest.save(root, payload)
        saved = json.loads(
            manifest.manifest_path(root).read_text(encoding="utf-8"))

        self.assertNotIn("/PIONEER/rekordbox/export.pdb", saved["files"])
        self.assertIn("/Music/SoundBox/song.wav", saved["files"])
        self.assertIn("/PIONEER/rekordbox/export.pdb", payload["files"])

    def test_sig_changes_with_tracks(self):
        a = PL("1", "Set", [], ["t1", "t2", "t3"])
        b = PL("1", "Set", [], ["t1", "t3", "t2"])  # reordered
        self.assertNotEqual(manifest.playlist_sig(a), manifest.playlist_sig(b))
        self.assertEqual(manifest.playlist_sig(a), manifest.playlist_sig(PL("1", "Set", [], ["t1", "t2", "t3"])))

    def test_sig_and_snapshot_include_soundbox_append_layer(self):
        playlist = PL("1", "Set", [], ["source"])
        baseline = manifest.playlist_sig(playlist)
        playlist.added_keys = ["rockbox"]

        self.assertNotEqual(manifest.playlist_sig(playlist), baseline)
        self.assertEqual(
            manifest.entry_for(playlist)["tracks"],
            ["source", "rockbox"],
        )

    def test_key_prefers_pid_then_custom(self):
        self.assertEqual(manifest.playlist_key(PL("42", "X", [], [])), "42")
        self.assertEqual(manifest.playlist_key(PL("", "MyMix", [], [])), "cust:MyMix")

    def test_record_and_roundtrip(self):
        d = Path(tempfile.mkdtemp())
        manifest.record_export(d, [PL("1", "Set A", ["crates"], ["t1", "t2"])])
        m = manifest.load(d)
        self.assertIn("1", m["playlists"])
        self.assertEqual(m["playlists"]["1"]["name"], "Set A")
        # A second export accumulates, doesn't clobber.
        manifest.record_export(d, [PL("2", "Set B", [], ["t9"])])
        m = manifest.load(d)
        self.assertEqual(set(m["playlists"]), {"1", "2"})

    def test_diff_detects_rename_move_change_remove(self):
        d = Path(tempfile.mkdtemp())
        manifest.record_export(d, [
            PL("1", "SONNY.AIF", [], ["a", "b"]),   # will be renamed + moved
            PL("2", "Bass", ["crates"], ["x"]),      # will change tracks
            PL("3", "Old", [], ["z"]),               # will be deleted in rekordbox
            PL("4", "Stable", [], ["k"]),            # unchanged
        ])
        m = manifest.load(d)
        # Current library: 1 renamed+moved, 2 track added, 3 gone, 4 same.
        lib = lib_of(
            PL("1", "SONNY.AIF v7", ["sets"], ["a", "b"]),
            PL("2", "Bass", ["crates"], ["x", "y"]),
            PL("4", "Stable", [], ["k"]),
        )
        dr = manifest.diff(m, lib)
        self.assertEqual([k for k, *_ in dr.renamed], ["1"])
        self.assertEqual([k for k, *_ in dr.moved], ["1"])
        self.assertEqual([k for k, *_ in dr.changed], ["2"])
        self.assertEqual([k for k, *_ in dr.removed], ["3"])
        self.assertTrue(dr.any())
        self.assertEqual(dr.count(), 3)  # pl 1 counted once despite rename+move

    def test_diff_clean_when_nothing_changed(self):
        d = Path(tempfile.mkdtemp())
        pls = [PL("1", "A", [], ["a"]), PL("2", "B", ["f"], ["b", "c"])]
        manifest.record_export(d, pls)
        dr = manifest.diff(manifest.load(d), lib_of(*pls))
        self.assertFalse(dr.any())

    def test_old_rockbox_profile_drifts_each_playlist_once(self):
        playlist = PL("1", "A", [], ["a"])
        old = {"playlists": {
            "1": manifest.entry_for(playlist),
        }}
        drift = manifest.diff(old, lib_of(playlist))
        self.assertEqual(drift.changed, [("1", "A")])

        current = {"playlists": {
            "1": manifest.entry_for(
                playlist,
                export_profile=manifest.ROCKBOX_EXPORT_PROFILE),
        }}
        self.assertFalse(manifest.diff(current, lib_of(playlist)).any())

    def test_load_composite_preserves_schema_three_ownership(self):
        root = Path(tempfile.mkdtemp())
        path = manifest.manifest_path(root)
        path.parent.mkdir()
        payload = {
            "schema": manifest.COMPOSITE_SCHEMA,
            "profile": manifest.COMPOSITE_EXPORT_PROFILE,
            "playlists": {"1": {"name": "A"}},
            "files": {"/Playlists/A.m3u8": {"owned": True}},
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        self.assertEqual(manifest.load_composite(root), payload)
        self.assertEqual(manifest.load(root)["schema"], manifest.SCHEMA)

    def test_composite_manifest_discards_external_pioneer_ownership(self):
        root = Path(tempfile.mkdtemp())
        path = manifest.manifest_path(root)
        path.parent.mkdir()
        payload = {
            "schema": manifest.COMPOSITE_SCHEMA,
            "profile": manifest.COMPOSITE_EXPORT_PROFILE,
            "playlists": {"1": {"tracks": ["a"]}},
            "files": {
                "/PIONEER/rekordbox/export.pdb": {
                    "kind": "shared-stock-media", "dbid": 99,
                },
                "/iPod_Control/Music/F00/A.mp3": {
                    "kind": "shared-stock-media", "dbid": 10,
                },
            },
        }
        path.write_text(json.dumps(payload), encoding="utf-8")

        loaded = manifest.load_composite(root)

        self.assertNotIn("/PIONEER/rekordbox/export.pdb", loaded["files"])
        self.assertEqual(
            manifest.composite_library_counts(loaded).stock_visible_tracks, 1)

    def test_load_composite_accepts_previous_profile_for_safe_migration(self):
        root = Path(tempfile.mkdtemp())
        path = manifest.manifest_path(root)
        path.parent.mkdir()
        legacy_profile = next(iter(manifest.LEGACY_COMPOSITE_EXPORT_PROFILES))
        payload = {
            "schema": manifest.COMPOSITE_SCHEMA,
            "profile": legacy_profile,
            "playlists": {"1": {"tracks": ["a", "b"]}},
            "files": {
                "/iPod_Control/Music/F00/A.mp3": {
                    "kind": "shared-stock-media", "dbid": 10},
                "/Music/B/B.flac": {"kind": "rockbox-media"},
            },
        }
        path.write_text(json.dumps(payload), encoding="utf-8")

        self.assertEqual(manifest.load_composite(root), payload)
        counts = manifest.composite_library_counts(payload)
        self.assertEqual(counts.managed_tracks, 2)
        self.assertEqual(counts.stock_visible_tracks, 1)
        self.assertEqual(counts.rockbox_only_tracks, 1)

    def test_composite_counts_union_tracks_and_split_stock_subset(self):
        payload = {
            "schema": manifest.COMPOSITE_SCHEMA,
            "profile": manifest.COMPOSITE_EXPORT_PROFILE,
            "playlists": {
                "1": {"tracks": ["a", "b", "c"]},
                "2": {"tracks": ["c", "d"]},
            },
            "files": {
                "/iPod_Control/Music/F00/A.mp3": {
                    "kind": "shared-stock-media", "dbid": 10},
                "/iPod_Control/Music/F00/B.wav": {
                    "kind": "shared-stock-media", "dbid": 20},
                "/Music/C/C.flac": {"kind": "rockbox-media"},
                "/Music/D/D.aiff": {"kind": "rockbox-media"},
            },
        }
        counts = manifest.composite_library_counts(payload)
        self.assertEqual(counts.managed_tracks, 4)
        self.assertEqual(counts.stock_visible_tracks, 2)
        self.assertEqual(counts.rockbox_only_tracks, 2)
        self.assertEqual(counts.managed_playlists, 2)

    def test_composite_playlist_entries_reads_only_verified_m3u_payload(self):
        root = Path(tempfile.mkdtemp())
        playlist = root / "Playlists/Set.m3u8"
        playlist.parent.mkdir()
        payload_bytes = (
            b"#EXTM3U\n/iPod_Control/Music/F00/A.mp3\n"
            b"/Music/B/B.flac\n"
        )
        playlist.write_bytes(payload_bytes)
        payload = {
            "schema": manifest.COMPOSITE_SCHEMA,
            "profile": manifest.COMPOSITE_EXPORT_PROFILE,
            "playlists": {"set": {"tracks": ["a", "b"]}},
            "files": {
                "/Playlists/Set.m3u8": {
                    "kind": "playlist",
                    "owned": True,
                    "source": "set",
                    "size": len(payload_bytes),
                    "sha256": hashlib.sha256(payload_bytes).hexdigest(),
                },
            },
        }
        self.assertEqual(manifest.composite_playlist_entries(root, payload), {
            "set": (
                "/iPod_Control/Music/F00/A.mp3",
                "/Music/B/B.flac",
            ),
        })
        playlist.write_bytes(payload_bytes + b"/Music/C/C.wav\n")
        status = manifest.composite_playlist_status(root, payload)
        self.assertEqual(status.entries, {})
        self.assertEqual(status.changed_keys, frozenset({"set"}))
        with self.assertRaisesRegex(ValueError, "changed since"):
            manifest.composite_playlist_entries(root, payload)

    def test_managed_current_playlists_reuses_stable_identity_and_order(self):
        one = PL("1", "One renamed", ["new"], ["a"])
        two = PL("2", "Two", [], ["b"])
        current = lib_of(two, one)
        device = {
            "playlist_order": ["1", "gone", "2"],
            "playlists": {"1": {}, "gone": {}, "2": {}},
        }
        self.assertEqual(
            manifest.managed_current_playlists(device, current),
            [one, two],
        )


if __name__ == "__main__":
    unittest.main()
