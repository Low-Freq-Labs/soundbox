"""Source-neutral library model shared by readers, curation, and export.

Source adapters normalize their native data into these three types. The core
model deliberately does not import rekordbox (or any future DJ-library
provider), so downstream code depends on SoundBox concepts rather than on the
reader that produced them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .paths import decode_location

_JUNK_MAX_SECONDS = 5


@dataclass(frozen=True)
class LibraryTag:
    tag_id: str
    name: str
    category: str = ""
    source_uuid: str = ""


@dataclass
class Track:
    track_id: str
    name: str
    artist: str
    location_uri: str
    bpm: str = ""
    key: str = ""
    total_time: int = 0
    date_added: str = ""
    # When the track entered its source library, as a POSIX timestamp.
    # SoundBox-native imports use their import time. 0.0 means unknown.
    added_ts: float = 0.0
    genre: str = ""
    kind: str = ""
    size: int = 0
    bitrate: str = ""
    rating: str = ""
    year: str = ""
    info: dict = field(default_factory=dict)
    # True for files imported directly into SoundBox. They have no DJ-library
    # analysis, so the flag exempts them from source cue-marker filtering.
    imported: bool = False
    # Optional read-only provider analysis sidecar. The current rekordbox
    # adapter supplies ANLZ0000.DAT; source-native tracks leave it blank.
    source_analysis_path: str = ""
    # Canonical cue rows normalized by the source adapter. ``None`` means no
    # authoritative cue source was available; ``[]`` means the source
    # authoritatively contains no active cues for this track.
    source_cues: list[dict] | None = None
    source_uuid: str = ""
    # None is an unavailable authoritative tag source; [] is no assignments.
    source_tag_ids: list[str] | None = None
    # Edit identity is separate from display cues and waveform fingerprints.
    # Missing revision (old cache/XML/import) never authorizes a cue write.
    source_cue_revision: str = ""
    source_cue_refs: list[dict] = field(default_factory=list)

    # Logical IDs survive first registration; only native adapters use this ID.
    # Empty retains the original native/XML identity convention.
    source_native_id: str = ""

    @property
    def native_id(self) -> str:
        return self.source_native_id or self.track_id

    @property
    def path(self) -> Path:
        """Decoded local filesystem path (not verified to exist)."""
        return decode_location(self.location_uri)

    @property
    def duration_str(self) -> str:
        """Duration as M:SS, promoting long audio to H:MM:SS."""
        hours, remainder = divmod(max(0, int(self.total_time or 0)), 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"

    @property
    def file_type(self) -> str:
        """Clean format label derived from the path, then the source kind."""
        ext = self.path.suffix.lstrip(".").upper()
        if ext == "AIF":
            ext = "AIFF"
        if ext == "M4A":
            ext = "ALAC/AAC"
        return ext or (self.kind.replace(" File", "").upper() if self.kind else "")

    def is_untracked(
        self,
        *,
        nokey: bool = True,
        short: bool = True,
        short_secs: int = _JUNK_MAX_SECONDS,
    ) -> bool:
        """Whether this source track matches SoundBox's cue-marker filter."""
        if self.imported:
            return False
        bpm = (self.bpm or "").strip()
        try:
            has_bpm = float(bpm) > 0
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
        """Serializable row for the UI table."""
        row = {
            "id": self.track_id,
            "title": self.name,
            "artist": self.artist,
            "bpm": self.bpm,
            "key": self.key,
            "time": self.duration_str,
            "dateAdded": self.date_added,
            "genre": self.genre,
            "format": self.file_type,
            "bitrate": self.bitrate,
        }
        from .track_info import FIELDS
        row.update({key:str(self.info.get(key,'')) for key,_label in FIELDS})
        row.update(year=self.year,rating=self.rating,size=str(self.size or ''),location=str(self.path))
        return row

    # Compatibility aliases for XML-era callers while the source-neutral names
    # become the public boundary.
    @property
    def analysis_path(self) -> str:
        return self.source_analysis_path

    @analysis_path.setter
    def analysis_path(self, value: str) -> None:
        self.source_analysis_path = value

    @property
    def rekordbox_cues(self) -> list[dict] | None:
        return self.source_cues

    @rekordbox_cues.setter
    def rekordbox_cues(self, value: list[dict] | None) -> None:
        self.source_cues = value


@dataclass
class Playlist:
    name: str
    folder_path: list[str]
    # Source adapters normalize these to Track.track_id values in exact order.
    keys: list[str] = field(default_factory=list)
    # Stable source playlist ID. Empty when a source cannot provide one.
    pid: str = ""
    # SoundBox-owned append layer for read-only source playlists.  The provider
    # remains untouched; recognized additions made in a managed Rockbox
    # playlist are published here only after the coordinated device commit.
    added_keys: list[str] = field(default_factory=list)

    @property
    def effective_keys(self) -> list[str]:
        return [*self.keys, *self.added_keys]

    @property
    def display_path(self) -> str:
        return " / ".join(self.folder_path + [self.name])


@dataclass
class Library:
    """Normalized, in-memory, read-only view of one source library."""

    tracks_by_id: dict[str, Track]
    playlists: list[Playlist]
    source: Path | str
    # Stable adapter identifier for diagnostics and future source selection.
    source_kind: str = "unknown"
    hide_untracked: bool = False
    hide_nokey: bool = True
    hide_short: bool = True
    hide_short_secs: int = _JUNK_MAX_SECONDS
    artist_registry: object | None = None
    hidden_ids: set = field(default_factory=set)
    removed_by_playlist: dict = field(default_factory=dict)
    title_junk: list | None = None
    source_tags: list[LibraryTag] = field(default_factory=list)
    source_library_id: str = ""
    # Read-only native playlist identities/history for exact edit baselines.
    source_playlist_catalog: dict = field(default_factory=dict)

    @property
    def track_count(self) -> int:
        return len(self.tracks_by_id)

    @property
    def source_xml(self) -> Path | str:
        """Compatibility alias retained for XML-era callers."""
        return self.source

    def resolve(self, playlist: Playlist) -> list[Track]:
        """Resolve a playlist in exact order, applying SoundBox-side filters."""
        out: list[Track] = []
        removed = self.removed_by_playlist.get(playlist.pid or "", ())
        for key in playlist.effective_keys:
            track = self.tracks_by_id.get(key)
            if track is None or key in self.hidden_ids or key in removed:
                continue
            if self.hide_untracked and track.is_untracked(
                nokey=self.hide_nokey,
                short=self.hide_short,
                short_secs=self.hide_short_secs,
            ):
                continue
            out.append(track)
        return out
