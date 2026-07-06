"""Core data model (Track / Playlist / Library) + read-only rekordbox XML parser.

The Library here is what the WHOLE app runs on: the live-database reader
(rekordbox_db) builds the same objects, so everything downstream is source-
agnostic. Library also carries the user's SoundBox-side filters (hide rules,
deletes, per-playlist removals, title-junk patterns) — resolve() applies them,
and both the table view and every export funnel through resolve(), so display
and device can never disagree.

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
from dataclasses import dataclass, field
from pathlib import Path

from .paths import decode_location

# A no-BPM entry shorter than this many seconds is treated as a placeholder/stub
# (e.g. a 0:01 "TESTING" marker), not a real track. See Track.is_untracked.
_JUNK_MAX_SECONDS = 5


@dataclass
class Track:
    track_id: str
    name: str
    artist: str
    location_uri: str
    # Rich metadata for the browser table (all present ~100% in rekordbox XML).
    bpm: str = ""           # AverageBpm, e.g. "145.00"
    key: str = ""           # Tonality, e.g. "2A" (Camelot)
    total_time: int = 0     # seconds
    date_added: str = ""    # "YYYY-MM-DD"
    genre: str = ""
    kind: str = ""          # e.g. "MP3 File"
    size: int = 0           # bytes
    bitrate: str = ""
    rating: str = ""
    year: str = ""
    # True for files imported directly into SoundBox (livesets etc. — see
    # soundbox.imports). They have no rekordbox analysis (no BPM/key), which is
    # exactly what is_untracked keys on, so the flag exempts them: a track the
    # user deliberately added must never be swept as a "cue marker".
    imported: bool = False

    @property
    def path(self) -> Path:
        """Decoded local filesystem path (not verified to exist)."""
        return decode_location(self.location_uri)

    @property
    def duration_str(self) -> str:
        """TotalTime seconds as M:SS for display."""
        m, s = divmod(int(self.total_time or 0), 60)
        return f"{m}:{s:02d}"

    @property
    def file_type(self) -> str:
        """Clean format label for the audiophile column: FLAC / WAV / AIFF / MP3.

        Derived from the file extension (more reliable than rekordbox 'Kind'),
        falling back to Kind if there's no usable extension.
        """
        ext = self.path.suffix.lstrip(".").upper()
        if ext == "AIF":
            ext = "AIFF"
        if ext == "M4A":
            ext = "ALAC/AAC"
        return ext or (self.kind.replace(" File", "").upper() if self.kind else "")

    def is_untracked(self, *, nokey: bool = True, short: bool = True,
                     short_secs: int = _JUNK_MAX_SECONDS) -> bool:
        """True for a rekordbox non-track (visual cue / placeholder) under the given
        rule. The ANCHOR is always 'no BPM': a real track ALWAYS has a BPM from
        rekordbox analysis — even keyless dubstep — so any BPM immediately means
        'keep'. Among no-BPM entries, hide when (`nokey` and it's keyless, e.g. '---'
        / '*** Intro ***') OR (`short` and length < `short_secs` — INCLUDING 0:00:
        a keyed no-BPM divider like '*** SINGLES ***' at 0 seconds slipped the old
        '0 <' guard). With both sub-rules off, nothing is hidden. Real tracks are
        never affected and rekordbox is never touched (pure display/export filter).
        Imported (non-rekordbox) tracks are always kept — no analysis, but chosen
        on purpose."""
        if self.imported:
            return False
        bpm = (self.bpm or "").strip()
        try:
            has_bpm = float(bpm) > 0        # "0.00"/"0" (XML) count as untracked
        except ValueError:
            has_bpm = bool(bpm)
        if has_bpm:
            return False
        if nokey and not (self.key or "").strip():
            return True
        if short and (self.total_time or 0) < short_secs:
            return True
        return False

    def to_row(self) -> dict:
        """Serializable row for the UI table (JSON-friendly)."""
        return {
            "id": self.track_id,
            "title": self.name,
            "artist": self.artist,
            "bpm": self.bpm,
            "key": self.key,
            "time": self.duration_str,
            "dateAdded": self.date_added,
            "genre": self.genre,
            "format": self.file_type,   # default audiophile column
            "bitrate": self.bitrate,    # e.g. "1411" — pairs with format
        }


@dataclass
class Playlist:
    name: str
    # Full path through the folder tree, e.g. ["Crates", "Bass", "VIPs"].
    folder_path: list[str]
    # Track keys as they appear in the XML, in playlist order.
    keys: list[str] = field(default_factory=list)
    key_type: str = "0"
    # Stable rekordbox playlist ID — survives renames AND moves, so it's the real
    # identity for device sync (name/path can change; this can't). Empty for the
    # XML fallback (XML has no persistent ID); the live DB is the identity source.
    pid: str = ""

    @property
    def display_path(self) -> str:
        return " / ".join(self.folder_path + [self.name])


@dataclass
class Library:
    """A parsed, in-memory, read-only view of the export."""

    tracks_by_id: dict[str, Track]
    playlists: list[Playlist]
    source_xml: Path
    # When hide_untracked is True, resolve() drops cue/marker tracks per the rule
    # (see Track.is_untracked). Set from the user's Settings; affects BOTH the table
    # view and export (both funnel through resolve()) so they can't drift. The two
    # sub-rules refine which no-BPM entries to hide. Never touches rekordbox.
    hide_untracked: bool = False
    hide_nokey: bool = True                        # also require: keyless
    hide_short: bool = True                        # or catch: very short stubs
    hide_short_secs: int = _JUNK_MAX_SECONDS       # the "shorter than" threshold
    # Canonical-artist map (metadata.ArtistRegistry), built once at load. Dedups
    # artist spellings and rescues Unknowns from the title. None = no cleanup.
    artist_registry: object | None = None
    # Tracks the user DELETED from SoundBox (right-click → Delete in Collection).
    # SOUNDBOX-ONLY: rekordbox is never touched — these just stop appearing in
    # views and exports (both funnel through resolve()). Persisted; restorable.
    hidden_ids: set = field(default_factory=set)
    # Per-playlist REMOVALS (right-click → Remove from Playlist): track ids the
    # user removed from ONE rekordbox playlist without deleting from Collection.
    # Keyed by Playlist.pid (custom playlists are edited directly instead).
    removed_by_playlist: dict = field(default_factory=dict)
    # User-customizable patterns auto-dropped from DISPLAY/EXPORT titles
    # (Settings). None = metadata.DEFAULT_TITLE_JUNK.
    title_junk: list | None = None

    @property
    def track_count(self) -> int:
        return len(self.tracks_by_id)

    def resolve(self, playlist: Playlist) -> list[Track]:
        """Resolve a playlist's keys to Track objects (skips unknown keys, and —
        when hide_untracked is on — rekordbox visual-cue markers)."""
        out: list[Track] = []
        removed = self.removed_by_playlist.get(playlist.pid or "", ())
        for key in playlist.keys:
            track = self.tracks_by_id.get(key)
            if track is None:
                continue
            if key in self.hidden_ids:  # user deleted it from SoundBox
                continue
            if key in removed:  # user removed it from THIS playlist only
                continue
            if self.hide_untracked and track.is_untracked(
                    nokey=self.hide_nokey, short=self.hide_short,
                    short_secs=self.hide_short_secs):
                continue
            out.append(track)
        return out


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
        for node in playlists_root.findall("NODE"):
            _walk(node, [], playlists)

    return Library(tracks_by_id=tracks_by_id, playlists=playlists, source_xml=path)


def _walk(node: ET.Element, parents: list[str], out: list[Playlist]) -> None:
    """Recursively collect playlists from the NODE tree."""
    node_type = node.get("Type", "")
    name = node.get("Name", "")

    if node_type == "1":  # playlist
        keys = [t.get("Key", "") for t in node.findall("TRACK")]
        out.append(
            Playlist(
                name=name,
                folder_path=list(parents),
                keys=[k for k in keys if k],
                key_type=node.get("KeyType", "0"),
            )
        )
        return

    # Folder (Type "0"): recurse, extending the folder path (skip the ROOT label).
    next_parents = parents if name in ("", "ROOT") else parents + [name]
    for child in node.findall("NODE"):
        _walk(child, next_parents, out)
