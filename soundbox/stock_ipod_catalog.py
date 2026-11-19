"""Read-only playlist recognition and song correspondence from cached catalogs.

Name/count associates a playlist, never audio ownership or content integrity.
Song associations use cached identities and metadata, independently of order.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, cast

from . import manifest, metadata
from .library import Library, Playlist, Track
from .stock_ipod_catalog_state import CatalogWitnessRow, StockCatalogWitness
from .stock_ipod_playlist_binding import (
    SourcePlaylist, DevicePlaylist, PlaylistBindingComparison, compare_playlist_bindings,
    source_playlist_identity, normalize_playlist_name,
)

if TYPE_CHECKING:
    from .stock_ipod_info import StockIpodSnapshot, StockIpodTrackSnapshot


STOCK_AUDIO_SUFFIXES = frozenset({".mp3", ".wav"})
CATALOG_METADATA_FIELDS = ("title", "artist", "album", "genre", "comments", "year", "bpm")


@dataclass(frozen=True)
class StockCatalogMetadataChange:
    local_id: str
    dbid: int
    field: str
    before: str | int
    after: str | int


@dataclass(frozen=True)
class StockCatalogComparison:
    recognition: tuple[StockPlaylistRecognition, ...]
    changes: tuple[StockCatalogMetadataChange, ...]
    artwork_track_ids: tuple[str, ...] = ()
    playlist_changes: tuple[PlaylistBindingComparison, ...] = ()

    @property
    def needs_sync(self) -> bool:
        return bool(self.changes or self.artwork_track_ids
                    or any(row.needs_sync for row in self.playlist_changes))


@dataclass(frozen=True)
class StockCatalogTrackMatch:
    local_id: str
    dbid: int
    device_track_id: int
    location: str


@dataclass(frozen=True)
class StockPlaylistRecognition:
    playlist_key: str
    name: str
    count: int
    device_playlist_id: int | None
    tracks: tuple[StockCatalogTrackMatch, ...] = ()
    unmatched_track_ids: tuple[str, ...] = ()
    detail: str = ""

    @property
    def recognized(self) -> bool:
        return self.device_playlist_id is not None


def _labels(track: Track, library: Library, derive_artists: bool,
            artist_overrides: dict | None, title_overrides: dict | None) -> set[tuple[str, str]]:
    title = str((title_overrides or {}).get(track.track_id) or track.name)
    artist = metadata.effective_artist(
        track.track_id, title, track.artist, artist_overrides or {}, derive_artists,
        registry=cast("metadata.ArtistRegistry | None", library.artist_registry), stem=track.path.stem)
    title = metadata.effective_title(track.track_id, track.name, artist, title_overrides,
                                     getattr(library, "title_junk", None)) or track.path.stem
    return {(str(track.name).strip(), str(track.artist).strip()),
            (title.strip(), artist.strip())}


def catalog_metadata(track: Track, library: Library, *, derive_artists: bool = True,
                     artist_overrides: dict | None = None, title_overrides: dict | None = None,
                     display_rows: Mapping[str, Mapping] | None = None) -> tuple[tuple[str, str | int], ...]:
    """Exact displayed musical values; technical/playback fields are excluded."""
    row = track.to_row()
    display = None
    if display_rows is not None and track.track_id in display_rows:
        display = display_rows[track.track_id]
        if display.get("id", track.track_id) != track.track_id:
            raise ValueError("Displayed catalog metadata belongs to another song")
    # A complete effective pair has already passed the GUI's derivation policy.
    # Repeating title rescue here is both wasted work and a second dependency
    # on registry state that the frozen pair deliberately replaces.
    if display is None or 'title' not in display or 'artist' not in display:
        raw_title = (title_overrides or {}).get(track.track_id) or track.name
        artist = metadata.effective_artist(track.track_id, raw_title, track.artist, artist_overrides,
            derive_artists, cast("metadata.ArtistRegistry | None", library.artist_registry), stem=track.path.stem)
        row.update(title=metadata.effective_title(track.track_id, track.name, artist, title_overrides,
                                                getattr(library, "title_junk", None)), artist=artist)
    row.update(getattr(library, "info_overrides", {}).get(track.track_id, {}))
    if display is not None:
        row.update(display)
    values: list[tuple[str, str | int]] = []
    for field in CATALOG_METADATA_FIELDS:
        if field == "bpm" and (row.get("bpm_estimated", False) or not row.get("bpm")):
            continue
        value = row.get(field, "")
        if field in {"year", "bpm"}:
            try:
                numeric = Decimal(str(value or "0"))
                if not numeric.is_finite() or numeric < 0 or numeric > 65535:
                    raise ValueError("Catalog numeric metadata is outside its supported range")
                if field == "year" and numeric != int(numeric):
                    raise ValueError("Catalog year must be an integer")
                value = int(numeric)
            except InvalidOperation as exc:
                raise ValueError("Invalid catalog numeric metadata") from exc
        else:
            if type(value) is not str or "\0" in value or len(value) > 4096:
                raise ValueError("Invalid displayed catalog metadata")
            if field == "title" and not value:
                value = track.path.stem
        values.append((field, value))
    return tuple(values)


def catalog_match_labels(library: Library, selected: list[Playlist], origins: Mapping) -> dict[str, tuple]:
    """Selected exact-identity origins, never queue-wide or filename guesses."""
    from . import alias_intents, source_truth
    scope = source_truth.scope(library)
    result = {}
    for playlist in selected:
        for track in library.resolve(playlist):
            reference = dict(local_id=track.track_id, native_id=track.native_id,
                             uuid=track.source_uuid, path=track.source_native_path or str(track.path))
            identity = alias_intents.key(scope, reference)
            origin = origins.get(identity)
            if not origin:
                continue
            try:
                alias_intents.validate_origins({identity: origin})
                if origin['scope'] != scope or any(origin[key] != value for key, value in reference.items()):
                    continue
                pairs = [(origin['native_title'], origin['native_artist'])]
                raw = origin.get('provenance', {}).get('raw')
                if raw is not None:
                    pairs.append((raw['title'], raw['artist']))
                result[track.track_id] = tuple(dict.fromkeys(pairs))
            except (ValueError, TypeError, KeyError):
                continue
    return result


def snapshot_metadata(row: StockIpodTrackSnapshot) -> tuple[tuple[str, str | int], ...]:
    return tuple((field, getattr(row, field)) for field in CATALOG_METADATA_FIELDS)


def catalog_witness_target_rows(library: Library, snapshot: StockIpodSnapshot,
                               witness: StockCatalogWitness | None) -> dict[tuple[str, int], CatalogWitnessRow]:
    """Validate remembered target rows; missing sources grant reference history only."""
    if (witness is None or witness.device_key != snapshot.identity_key
            or witness.source != (str(library.source), library.source_kind, library.source_library_id)):
        return {}
    counts = Counter(row.dbid for row in snapshot.tracks)
    rows = {row.dbid: row for row in snapshot.tracks if counts[row.dbid] == 1}
    result = {}
    for remembered in witness.rows:
        current = rows.get(remembered.dbid)
        if (current is not None
                and (current.track_id, current.location) == (remembered.device_track_id, remembered.location)
                and dict(snapshot_metadata(current)) == dict(remembered.metadata)):
            result[(remembered.local_id, remembered.dbid)] = remembered
    return result


def matching_catalog_witness_rows(library: Library, snapshot: StockIpodSnapshot,
                                 witness: StockCatalogWitness | None) -> dict[tuple[str, int], CatalogWitnessRow]:
    result = {}
    for pair, remembered in catalog_witness_target_rows(library, snapshot, witness).items():
        source = library.tracks_by_id.get(remembered.local_id)
        if (source is not None and (source.native_id, source.source_uuid,
                source.source_native_path or str(source.path))
                == (remembered.native_id, remembered.uuid, remembered.source_path)):
            result[pair] = remembered
    return result


def recognize_stock_playlists(
        library: Library, selected: list[Playlist], snapshot: StockIpodSnapshot, *,
        derive_artists: bool = True, artist_overrides: dict | None = None,
        title_overrides: dict | None = None, display_rows: Mapping[str, Mapping] | None = None,
        match_labels: Mapping[str, Sequence[tuple[str, str]]] | None = None,
        catalog_witness: StockCatalogWitness | None = None,
        incremental: bool = False) -> tuple[StockPlaylistRecognition, ...]:
    """Recognize unique normalized names/counts without source/device file IO.

    A recognized playlist remains recognized when some songs are ambiguous.
    Distinct local IDs cannot silently share one device row; repeated occurrences
    of the same local/device identity remain ordinary playlist duplicates.
    """
    rows: dict[int, list[StockIpodTrackSnapshot]] = defaultdict(list)
    track_id_counts = Counter(row.track_id for row in snapshot.tracks)
    for row in snapshot.tracks:
        rows[row.dbid].append(row)
    members = [library.resolve(playlist) for playlist in selected]
    source_names = Counter(normalize_playlist_name(playlist.name) for playlist in selected)
    result = []
    remembered = matching_catalog_witness_rows(library, snapshot, catalog_witness)
    target_history = catalog_witness_target_rows(library, snapshot, catalog_witness) if incremental else {}
    history_identities: dict[int, set[tuple[str, str, str]]] = defaultdict(set)
    for history in target_history.values():
        history_identities[history.dbid].add((history.native_id, history.uuid, history.source_path))
    remembered_by_local: dict[str, set[int]] = defaultdict(set)
    for local, dbid in remembered:
        remembered_by_local[local].add(dbid)
    for playlist, tracks in zip(selected, members, strict=True):
        key = manifest.playlist_key(playlist)
        bound_ids = set()
        if (incremental and catalog_witness is not None
                and catalog_witness.source == (str(library.source), library.source_kind, library.source_library_id)
                and catalog_witness.device_key == snapshot.identity_key):
            identity = catalog_source_playlist(library, playlist)
            bound_ids = {binding.device_playlist_id for binding in catalog_witness.bindings
                         if (binding.key, binding.native_id, binding.uuid)
                         == (identity.key, identity.native_id, identity.uuid)}
        named = [row for row in snapshot.playlists if row.kind == 'Playlist' and row.playlist_id > 0
                 and normalize_playlist_name(row.title) == normalize_playlist_name(playlist.name)]
        candidates = ([row for row in snapshot.playlists if row.kind == 'Playlist'
                       and row.playlist_id in bound_ids] if bound_ids else
                      [row for row in named if incremental or len(row.entries) == len(tracks)])
        source_unique = bool(bound_ids) or source_names[normalize_playlist_name(playlist.name)] == 1
        if not bound_ids and len(named) > 1:
            candidates = []
        if len(candidates) != 1 or not source_unique:
            result.append(StockPlaylistRecognition(
                key, playlist.name, len(tracks), None,
                unmatched_track_ids=tuple(dict.fromkeys(track.track_id for track in tracks)),
                detail="Playlist name/count is ambiguous" if candidates else "No matching playlist name/count"))
            continue
        target = candidates[0]
        candidate_ids = rows if incremental else {entry.dbid for entry in target.entries}
        valid_rows = {dbid: rows[dbid][0] for dbid in candidate_ids
                      if len(rows[dbid]) == 1 and dbid > 0}
        by_label: dict[tuple[str, str], set[int]] = defaultdict(set)
        for dbid, row in valid_rows.items():
            if row.track_id > 0 and track_id_counts[row.track_id] == 1 and row.location:
                by_label[(row.title.strip(), row.artist.strip())].add(dbid)
        possible: dict[str, set[int]] = {}
        strict_candidates = set()
        for track in tracks:
            matched: set[int] = set()
            for label in _labels(track, library, derive_artists, artist_overrides, title_overrides):
                matched.update(by_label.get(label, ()))
            if display_rows is not None:
                desired = dict(catalog_metadata(track, library, derive_artists=derive_artists,
                    artist_overrides=artist_overrides, title_overrides=title_overrides, display_rows=display_rows))
                matched.update(by_label.get((str(desired['title']).strip(), str(desired['artist']).strip()), ()))
            for title, artist in (match_labels or {}).get(track.track_id, ()):
                if type(title) is not str or type(artist) is not str:
                    raise ValueError("Invalid catalog correspondence labels")
                matched.update(by_label.get((title.strip(), artist.strip()), ()))
            witnessed = {dbid for dbid in remembered_by_local.get(track.track_id, ())
                         if dbid in valid_rows}
            if witnessed:
                matched = witnessed
            if matched:
                strict_candidates.add(track.track_id)
            if incremental:
                current_identity = (track.native_id, track.source_uuid, track.source_native_path or str(track.path))
                matched = {dbid for dbid in matched if not history_identities[dbid]
                           or history_identities[dbid] == {current_identity}}
            if track.total_time > 0:
                matched = {dbid for dbid in matched if not valid_rows[dbid].length_ms
                           or abs(valid_rows[dbid].length_ms - track.total_time * 1000) <= 1000}
            # Size can disambiguate repeated labels, but differing container/tag
            # sizes do not disprove an otherwise unique metadata association.
            if len(matched) > 1 and track.size > 0:
                sized = {dbid for dbid in matched if valid_rows[dbid].size == track.size}
                if sized:
                    matched = sized
            if track.track_id in possible:
                possible[track.track_id] &= matched
            else:
                possible[track.track_id] = matched
        # Music may index a manual file using its filename and a blank artist.
        # This is a catalog correspondence only. It needs unique source/target
        # names, positive cached durations and no competing strict claim.
        local_tracks = {track.track_id: track for track in tracks}
        stems = Counter(normalize_playlist_name(track.path.stem) for track in local_tracks.values())
        member_rows = {entry.dbid: valid_rows[entry.dbid] for entry in target.entries
                       if entry.dbid in valid_rows}
        titles = Counter(normalize_playlist_name(row.title) for row in member_rows.values())
        by_title = {normalize_playlist_name(row.title): dbid for dbid, row in member_rows.items()}
        claimed = set().union(*possible.values()) if possible else set()
        for local, track in local_tracks.items():
            if possible[local] or local in strict_candidates or track.total_time <= 0:
                continue
            stem = normalize_playlist_name(track.path.stem)
            if not stem or stems[stem] != 1 or titles[stem] != 1:
                continue
            dbid = by_title[stem]; row = member_rows[dbid]
            source_identity = (track.native_id, track.source_uuid, track.source_native_path or str(track.path))
            if (row.artist.strip() or dbid in claimed or row.track_id <= 0
                    or track_id_counts[row.track_id] != 1 or not row.location or row.length_ms <= 0
                    or abs(row.length_ms - track.total_time * 1000) > 1000
                    or incremental and history_identities[dbid] and history_identities[dbid] != {source_identity}):
                continue
            possible[local] = {dbid}; claimed.add(dbid)
        claims = Counter(next(iter(ids)) for ids in possible.values() if len(ids) == 1)
        matches = []
        unmatched = []
        for local, ids in possible.items():
            if len(ids) != 1 or claims[next(iter(ids))] != 1:
                unmatched.append(local)
                continue
            dbid = next(iter(ids)); row = valid_rows[dbid]
            matches.append(StockCatalogTrackMatch(local, dbid, row.track_id, row.location))
        result.append(StockPlaylistRecognition(
            key, playlist.name, len(tracks), target.playlist_id, tuple(matches), tuple(unmatched),
            "Cached playlist association; song links use cached identities and metadata"))
    owners: dict[int, set[str]] = defaultdict(set)
    if incremental:
        # A new native identity with coincident labels cannot displace the
        # independently validated prior song association, even after removal
        # from the selected playlist. It needs its own verified new copy.
        for local, dbid in target_history:
            owners[dbid].add(local)
    for recognized in result:
        for match in recognized.tracks:
            owners[match.dbid].add(match.local_id)
    return tuple(StockPlaylistRecognition(
        row.playlist_key, row.name, row.count, row.device_playlist_id,
        tuple(match for match in row.tracks if len(owners[match.dbid]) == 1),
        tuple(dict.fromkeys((*row.unmatched_track_ids, *(match.local_id for match in row.tracks
                                                        if len(owners[match.dbid]) != 1)))),
        row.detail) for row in result)


def compare_stock_catalog(
        library: Library, selected: list[Playlist], snapshot: StockIpodSnapshot, *,
        derive_artists: bool = True, artist_overrides: dict | None = None,
        title_overrides: dict | None = None, display_rows: Mapping[str, Mapping] | None = None,
        match_labels: Mapping[str, Sequence[tuple[str, str]]] | None = None,
        catalog_witness: StockCatalogWitness | None = None,
        artwork_keys: Mapping[str, str | None] | None = None,
        incremental: bool = False) -> StockCatalogComparison:
    """Pure cached Sync hint; it reads no files and grants no write authority.

    Missing artwork_keys means artwork has not been compared; None preserves a
    song with no current local image. Supplied keys come from owned MA446 local
    preparation, never from a device media scan.
    """
    recognition = recognize_stock_playlists(library, selected, snapshot,
        derive_artists=derive_artists, artist_overrides=artist_overrides, title_overrides=title_overrides,
        display_rows=display_rows, match_labels=match_labels, catalog_witness=catalog_witness,
        incremental=incremental)
    rows = {row.dbid: row for row in snapshot.tracks}
    remembered = matching_catalog_witness_rows(library, snapshot, catalog_witness)
    changes = []; artwork = set(); seen = set()
    for playlist in recognition:
        for match in playlist.tracks:
            if (match.local_id, match.dbid) in seen:
                continue
            seen.add((match.local_id, match.dbid))
            desired = catalog_metadata(library.tracks_by_id[match.local_id], library,
                derive_artists=derive_artists, artist_overrides=artist_overrides,
                title_overrides=title_overrides, display_rows=display_rows)
            current = dict(snapshot_metadata(rows[match.dbid]))
            for field, value in desired:
                if value != current[field]:
                    changes.append(StockCatalogMetadataChange(match.local_id, match.dbid, field, current[field], value))
            key = (artwork_keys or {}).get(match.local_id)
            prior = remembered.get((match.local_id, match.dbid))
            if key is not None and (prior is None or prior.artwork_key != key
                    or catalog_witness is None
                    or catalog_witness.database_sha256 != snapshot.database_sha256
                    or catalog_witness.artwork_database_sha256 != snapshot.artwork_database_sha256):
                artwork.add(match.local_id)
    playlist_changes: tuple[PlaylistBindingComparison, ...] = ()
    if incremental:
        bindings = (catalog_witness.bindings if catalog_witness is not None
                    and catalog_witness.source == (str(library.source), library.source_kind, library.source_library_id)
                    and catalog_witness.device_key == snapshot.identity_key else ())
        links = {match.local_id: match.dbid for row in recognition for match in row.tracks}
        # A source song removed from this selected playlist still labels its
        # old occurrence. This removes a reference, never the underlying file.
        for local, dbid in remembered:
            links.setdefault(local, dbid)
        if catalog_witness is not None:
            bound_pairs = {pair for binding in bindings for pair in binding.members}
            for (local, dbid) in catalog_witness_target_rows(library, snapshot, catalog_witness):
                if (local, dbid) in bound_pairs and local not in library.tracks_by_id:
                    links.setdefault(local, dbid)
        playlist_changes = compare_playlist_bindings(
            tuple(catalog_source_playlist(library, playlist) for playlist in selected),
            tuple(DevicePlaylist(row.playlist_id, row.title, tuple(entry.dbid for entry in row.entries))
                  for row in snapshot.playlists if row.kind == 'Playlist'), track_links=links, bindings=bindings)
        valid = {row.source.key for row in playlist_changes if row.recognized}
        recognition = tuple(row if row.playlist_key in valid else StockPlaylistRecognition(
            row.playlist_key, row.name, row.count, None, (), row.unmatched_track_ids,
            'No unambiguous cached playlist association') for row in recognition)
        valid_pairs = {(match.local_id, match.dbid) for row in recognition for match in row.tracks}
        changes = [change for change in changes if (change.local_id, change.dbid) in valid_pairs]
        artwork.intersection_update(local for local, _dbid in valid_pairs)
    return StockCatalogComparison(recognition, tuple(changes), tuple(sorted(artwork)), playlist_changes)


def catalog_source_playlist(library: Library, playlist: Playlist) -> SourcePlaylist:
    return source_playlist_identity(library, playlist)
