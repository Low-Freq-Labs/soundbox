"""Source-neutral library boundary and adapter normalization."""

import ast
import tempfile
import unittest
from pathlib import Path

from soundbox import rekordbox_xml
from soundbox.library import Library, Playlist, Track


class LibraryBoundaryTests(unittest.TestCase):
    def test_model_has_no_provider_dependency(self):
        tree = ast.parse(Path("soundbox/library.py").read_text(encoding="utf-8"))
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imports.update(
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        )
        imports.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        )
        self.assertFalse(any("rekordbox" in name for name in imports))
        self.assertFalse(any("serato" in name for name in imports))
        self.assertFalse(any("traktor" in name for name in imports))

    def test_xml_module_reexports_the_canonical_types(self):
        self.assertIs(rekordbox_xml.Track, Track)
        self.assertIs(rekordbox_xml.Playlist, Playlist)
        self.assertIs(rekordbox_xml.Library, Library)

    def test_source_metadata_is_provider_neutral(self):
        lib = Library({}, [], source="/tmp/library", source_kind="test-adapter")
        self.assertEqual(lib.source, "/tmp/library")
        self.assertEqual(lib.source_kind, "test-adapter")
        self.assertEqual(lib.source_xml, lib.source)  # compatibility alias

    def test_source_playlist_resolves_soundbox_append_layer_in_order(self):
        tracks = {
            key: Track(key, key, "", f"file://localhost/tmp/{key}.wav")
            for key in ("source", "rockbox")
        }
        playlist = Playlist(
            "Read Only", [], ["source"], pid="stable",
            added_keys=["rockbox"],
        )
        lib = Library(tracks, [playlist], source="fixture")

        self.assertEqual(playlist.keys, ["source"])
        self.assertEqual(
            [track.track_id for track in lib.resolve(playlist)],
            ["source", "rockbox"],
        )


class RekordboxXmlAdapterTests(unittest.TestCase):
    def test_location_key_playlists_are_normalized_to_track_ids(self):
        location = "file://localhost/tmp/Track%20One.wav"
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <COLLECTION Entries="1">
    <TRACK TrackID="42" Name="Track One" Location="{location}" />
  </COLLECTION>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT">
      <NODE Type="1" Name="Location Keys" KeyType="1" Entries="1">
        <TRACK Key="{location}" />
      </NODE>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rekordbox.xml"
            path.write_text(xml, encoding="utf-8")
            lib = rekordbox_xml.load(path)

        self.assertEqual(lib.source_kind, "rekordbox-xml")
        self.assertEqual(lib.playlists[0].keys, ["42"])
        self.assertEqual(
            [track.track_id for track in lib.resolve(lib.playlists[0])],
            ["42"],
        )


if __name__ == "__main__":
    unittest.main()
