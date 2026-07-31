"""Read-only rekordbox XML adapter.

The parser normalizes rekordbox XML into the source-neutral model in
``soundbox.library``. Everything downstream consumes that model and does not
depend on this parser.

XML parsing below is strictly read-only.

SAFETY: This module only ever READS the XML file path it is given. It opens the
file in binary read mode for parsing and never writes, moves, or deletes
anything. It never opens the live rekordbox database (master.db) and never
looks inside rekordbox application folders.

rekordbox XML shape (verified against a real export):

  <DJ_PLAYLISTS Version="1.0.0">
    <COLLECTION Entries="N">
      <TRACK TrackID="..." Name="..." Artist="..." Location="file://localhost/..."/>
      ...
    </COLLECTION>
    <PLAYLISTS>
      <NODE Type="0" Name="ROOT" Count="...">       <- folder
        <NODE Type="1" Name="My Crate" KeyType="0" Entries="..."> <- playlist
          <TRACK Key="..."/>   <- Key is a lookup into COLLECTION
        </NODE>
      </NODE>
    </PLAYLISTS>
  </DJ_PLAYLISTS>

NODE Type: "0" = folder (contains child NODEs), "1" = playlist (contains TRACKs).
KeyType:   "0" = TRACK Key is a TrackID; "1" = TRACK Key is a Location string.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from .library import Library, Playlist, Track


def load(xml_path: str | Path) -> Library:
    """Parse a rekordbox XML export. READ ONLY."""
    path = Path(xml_path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"rekordbox XML not found: {path}")

    # Opened read-only ("rb"); ElementTree never writes back to this handle.
    with path.open("rb") as fh:
        tree = ET.parse(fh)
    root = tree.getroot()

    tracks_by_id: dict[str, Track] = {}
    collection = root.find("COLLECTION")
    if collection is not None:
        for el in collection.findall("TRACK"):
            tid = el.get("TrackID", "")
            if not tid:
                continue
            tracks_by_id[tid] = Track(
                track_id=tid,
                name=el.get("Name", ""),
                artist=el.get("Artist", ""),
                location_uri=el.get("Location", ""),
                bpm=el.get("AverageBpm", ""),
                key=el.get("Tonality", ""),
                total_time=int(el.get("TotalTime", "0") or 0),
                date_added=el.get("DateAdded", ""),
                genre=el.get("Genre", ""),
                kind=el.get("Kind", ""),
                size=int(el.get("Size", "0") or 0),
                bitrate=el.get("BitRate", ""),
                rating=el.get("Rating", ""),
                year=el.get("Year", ""),
            )

    playlists: list[Playlist] = []
    playlists_root = root.find("PLAYLISTS")
    if playlists_root is not None:
        ids_by_location = {
            track.location_uri: track_id
            for track_id, track in tracks_by_id.items()
            if track.location_uri
        }
        for node in playlists_root.findall("NODE"):
            _walk(node, [], playlists, ids_by_location)

    return Library(
        tracks_by_id=tracks_by_id,
        playlists=playlists,
        source=path,
        source_kind="rekordbox-xml",
    )


def _walk(
    node: ET.Element,
    parents: list[str],
    out: list[Playlist],
    ids_by_location: dict[str, str],
) -> None:
    """Recursively collect playlists from the NODE tree."""
    node_type = node.get("Type", "")
    name = node.get("Name", "")

    if node_type == "1":  # playlist
        keys = [t.get("Key", "") for t in node.findall("TRACK")]
        if node.get("KeyType", "0") == "1":
            keys = [ids_by_location.get(key, "") for key in keys]
        out.append(
            Playlist(
                name=name,
                folder_path=list(parents),
                keys=[k for k in keys if k],
            )
        )
        return

    # Folder (Type "0"): recurse, extending the folder path (skip the ROOT label).
    next_parents = parents if name in ("", "ROOT") else parents + [name]
    for child in node.findall("NODE"):
        _walk(child, next_parents, out, ids_by_location)
