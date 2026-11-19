"""Advisory full-export correspondence from actual native output, not the GUI."""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
import hashlib
import io

from clickwheel.ipod import get_ipod_tracks, parse_itunesdb  # type: ignore[import-untyped]

from .library import Library, Playlist
from .manifest import playlist_key
from .stock_ipod_catalog_state import CatalogWitnessRow, StockCatalogWitness
from .stock_ipod_playlist_binding import PlaylistBinding, SourcePlaylist, source_playlist_identity


@dataclass(frozen=True)
class ExportWitnessResult:
    witness: StockCatalogWitness | None
    notice: str = ''
    warning: str = ''


def stored_metadata(raw: Mapping) -> tuple[tuple[str, str | int], ...]:
    return (('title', str(raw.get('title') or '')), ('artist', str(raw.get('artist') or '')),
        ('album', str(raw.get('album') or '')), ('genre', str(raw.get('genre') or '')),
        ('comments', str(raw.get('Comment') or raw.get('comment') or '')),
        ('year', int(raw.get('year') or 0)), ('bpm', int(raw.get('bpm') or 0)))


def _retained_correspondence(
    library, selected, available, previous, previous_database, previous_artwork_sha256,
    rendered, artwork_sha256, new_rows, new_bindings, artwork_keys, preserved_artwork_dbids, selected_dbids,
):
    """Renew only an exact old witness against the freshly rendered catalog.

    Neither a shadow manifest nor a matching label can supply a missing link.
    ``preserved_artwork_dbids`` comes from the renderer's unprepared, preserved
    native links: those pixels were retained, even when full compaction moved
    their offsets. It is useful only after the exact old ArtworkDB hash matches.
    """
    notice = 'Some previous playlist comparison links could not be renewed; export those playlists to associate them again.'
    if previous is None:
        return (), (), ''
    try:
        if (previous.source != (str(library.source), library.source_kind, library.source_library_id)
                or previous_database is None or previous_artwork_sha256 is None):
            raise ValueError('Previous source or catalog capture is unavailable')
        previous.encode()
        verify_export_witness(previous, previous_database, previous_artwork_sha256)
    except (ValueError, TypeError, KeyError, OverflowError):
        return (), (), notice

    parsed = parse_itunesdb(io.BytesIO(rendered))
    native = get_ipod_tracks(parsed)
    by_dbid = {int(row.get('dbid') or 0): row for row in native}
    by_track = {int(row.get('trackID') or 0): int(row.get('dbid') or 0) for row in native}
    selected_sources = [source_playlist_identity(library, playlist) for playlist in selected]
    selected_ids = {local for source in selected_sources for local in source.members}
    selected_keys = {source.key for source in selected_sources}
    new_ids = {row.local_id for row in new_rows}
    old_local_counts = Counter(row.local_id for row in previous.rows)
    old_dbid_counts = Counter(row.dbid for row in previous.rows)
    retained = []; dropped = False
    for row in previous.rows:
        if row.local_id in selected_ids or row.local_id in new_ids:
            continue
        track = library.tracks_by_id.get(row.local_id)
        actual = by_dbid.get(row.dbid)
        if (old_local_counts[row.local_id] != 1 or old_dbid_counts[row.dbid] != 1
                or row.dbid in selected_dbids or track is None or actual is None
                or (track.native_id, track.source_uuid, track.source_native_path or str(track.path))
                    != (row.native_id, row.uuid, row.source_path)
                or str(actual.get('location') or '') != row.location):
            dropped = True
            continue
        if row.dbid in artwork_keys:
            art_key = artwork_keys[row.dbid]
        elif artwork_sha256 == previous_artwork_sha256 or row.dbid in preserved_artwork_dbids:
            art_key = row.artwork_key
        else:
            # Metadata/membership correspondence remains useful, but an old
            # local cover key must not certify unproven new device artwork.
            art_key = ''
            dropped = dropped or bool(row.artwork_key)
        retained.append(replace(row, device_track_id=int(actual['trackID']),
            metadata=stored_metadata(actual), artwork_key=art_key))

    sources: dict[str, set[SourcePlaylist]] = {}
    prior_keys = {binding.key for binding in previous.bindings} - selected_keys
    for playlist in (*library.playlists, *available, *selected):
        if playlist_key(playlist) not in prior_keys:
            continue
        source = source_playlist_identity(library, playlist)
        sources.setdefault(source.key, set()).add(source)
    current_pairs = {(row.local_id, row.dbid) for row in (*retained, *new_rows)}
    prior_rows = {(row.local_id, row.dbid): row for row in previous.rows}
    next_rows = {(row.local_id, row.dbid): row for row in (*retained, *new_rows)}
    new_device_ids = {binding.device_playlist_id for binding in new_bindings}
    bindings = []
    for binding in previous.bindings:
        if binding.key in selected_keys:
            continue
        candidates = sources.get(binding.key, set())
        matches = [row for row in parsed.get('mhlp', ()) if not row.get('isMaster')
                   and int(row.get('playlistID') or 0) == binding.device_playlist_id]
        current_source = next(iter(candidates)) if len(candidates) == 1 else None
        old_identities = {identity[0]: identity for identity in binding.source_members}
        current_identities = {local: (local, track.native_id, track.source_uuid,
            track.source_native_path or str(track.path), str(track.path))
            for local, _dbid in binding.members if (track := library.tracks_by_id.get(local)) is not None}
        row_identities_current = all(
            pair in prior_rows and pair in next_rows
            and (prior_rows[pair].native_id, prior_rows[pair].uuid, prior_rows[pair].source_path)
                == (next_rows[pair].native_id, next_rows[pair].uuid, next_rows[pair].source_path)
            and (pair[0] not in old_identities
                 or old_identities[pair[0]] == current_identities.get(pair[0]))
            for pair in binding.members)
        if (current_source is None or current_source.identity_error
                or (current_source.native_id, current_source.uuid) != (binding.native_id, binding.uuid)
                or binding.device_playlist_id in new_device_ids
                or not set(binding.members) <= current_pairs or not row_identities_current or len(matches) != 1
                or str(matches[0].get('Title') or '') != binding.name
                or tuple(by_track.get(int(item.get('trackID') or 0), 0)
                    for item in matches[0].get('items', ())) != tuple(dbid for _local, dbid in binding.members)):
            dropped = True
            continue
        bindings.append(binding)
    return tuple(retained), tuple(bindings), notice if dropped else ''


def build_export_witness(
    library: Library, selected: Sequence[Playlist], names: Sequence[str], *,
    device_key: str, rendered: bytes, artwork_sha256: str,
    track_dbids: Mapping[str, int], artwork_keys: Mapping[int, str],
    previous_witness: StockCatalogWitness | None = None,
    previous_database: bytes | None = None, previous_artwork_sha256: str | None = None,
    available_playlists: Sequence[Playlist] = (),
    preserved_artwork_dbids: frozenset[int] = frozenset(),
) -> ExportWitnessResult:
    """Missing/ambiguous optional correspondence never fails a valid export."""
    if not device_key:
        return ExportWitnessResult(None)
    dbid_counts = Counter(track_dbids.values())
    aliased = {local for local, dbid in track_dbids.items() if dbid_counts[dbid] != 1}
    alias_notice = ('Exported song aliases share an iPod record; their optional comparison links '
                    'were not saved. Full Sync remains available.')
    if aliased and previous_witness is None:
        return ExportWitnessResult(None, notice=alias_notice)
    try:
        parsed = parse_itunesdb(io.BytesIO(rendered))
        native = get_ipod_tracks(parsed)
        by_dbid = {int(row.get('dbid') or 0): row for row in native}
        by_track = {int(row.get('trackID') or 0): int(row.get('dbid') or 0) for row in native}
        if len(by_dbid) != len(native) or len(by_track) != len(native):
            raise ValueError('Native output song identities are ambiguous')
        rows = []
        for local, dbid in track_dbids.items():
            if local in aliased:
                continue
            track = library.tracks_by_id[local]
            output = by_dbid[dbid]
            rows.append(CatalogWitnessRow(local, track.native_id, track.source_uuid,
                track.source_native_path or str(track.path), dbid, int(output['trackID']),
                str(output['location']), stored_metadata(output), artwork_keys.get(dbid, '')))
        bindings = []; notices = [alias_notice] if aliased else []
        for playlist, name in zip(selected, names, strict=True):
            source = source_playlist_identity(library, playlist)
            if any(local in aliased for local in source.members):
                continue
            if source.identity_error:
                notices.append('Some exported playlists lack a native UUID; '
                    'their optional catalog association was not saved. Full Sync remains available.')
                continue
            matches = [row for row in parsed.get('mhlp', ())
                       if not row.get('isMaster') and row.get('Title') == name]
            if len(matches) != 1:
                raise ValueError('Native output playlist identity is ambiguous')
            output = matches[0]
            members = tuple((local, track_dbids[local]) for local in source.members if local in track_dbids)
            actual = tuple(by_track.get(int(item.get('trackID') or 0), 0)
                           for item in output.get('items', ()))
            if actual != tuple(dbid for _local, dbid in members):
                raise ValueError('Native output occurrence membership changed')
            bindings.append(PlaylistBinding(source.key, source.native_id, source.uuid,
                int(output.get('playlistID') or 0), name, members, source.label, source.identities))
        if previous_witness is not None and previous_witness.device_key != device_key:
            notices.append('Previous playlist comparison links belong to another device and were not renewed.')
        else:
            retained_rows, retained_bindings, notice = _retained_correspondence(
                library, selected, available_playlists, previous_witness, previous_database,
                previous_artwork_sha256, rendered, artwork_sha256, rows, bindings,
                artwork_keys, preserved_artwork_dbids, set(track_dbids.values()))
            rows = [*retained_rows, *rows]
            bindings = [*retained_bindings, *bindings]
            if notice:
                notices.append(notice)
        witness = StockCatalogWitness((str(library.source), library.source_kind, library.source_library_id),
            device_key, hashlib.sha256(rendered).hexdigest(), tuple(rows), artwork_sha256, tuple(bindings))
        witness.encode()
        verify_export_witness(witness, rendered, artwork_sha256)
        return ExportWitnessResult(witness, notice=' '.join(dict.fromkeys(notices)))
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        return ExportWitnessResult(None, warning=f'The optional catalog comparison baseline is unavailable: {exc}')


def verify_export_witness(witness: StockCatalogWitness, database: bytes, artwork_sha256: str) -> None:
    """Reopen exact committed output and verify every advisory row/occurrence."""
    if (hashlib.sha256(database).hexdigest() != witness.database_sha256
            or artwork_sha256 != witness.artwork_database_sha256):
        raise ValueError('Committed native catalog changed before comparison readback')
    parsed = parse_itunesdb(io.BytesIO(database))
    native = get_ipod_tracks(parsed)
    counts = Counter(int(row.get('dbid') or 0) for row in native)
    by_dbid = {int(row.get('dbid') or 0): row for row in native}
    by_track = {int(row.get('trackID') or 0): int(row.get('dbid') or 0) for row in native}
    for row in witness.rows:
        actual = by_dbid.get(row.dbid)
        if (counts[row.dbid] != 1 or actual is None
                or (int(actual.get('trackID') or 0), str(actual.get('location') or ''), stored_metadata(actual))
                != (row.device_track_id, row.location, row.metadata)):
            raise ValueError('Committed song correspondence did not survive readback')
    for binding in witness.bindings:
        matches = [row for row in parsed.get('mhlp', ()) if not row.get('isMaster')
                   and int(row.get('playlistID') or 0) == binding.device_playlist_id]
        if len(matches) != 1 or str(matches[0].get('Title') or '') != binding.name:
            raise ValueError('Committed playlist identity did not survive readback')
        actual = tuple(by_track.get(int(item.get('trackID') or 0), 0)
                       for item in matches[0].get('items', ()))
        if actual != tuple(dbid for _local, dbid in binding.members):
            raise ValueError('Committed playlist membership did not survive readback')


def rebase_export_witness(
    previous_witness: StockCatalogWitness | None, *, device_key: str,
    previous_database: bytes, previous_artwork_sha256: str, rendered: bytes,
    artwork_sha256: str, preserved_artwork_dbids: frozenset[int] = frozenset(),
) -> ExportWitnessResult:
    """Retain exact surviving advisory links after a device-only deletion.

    No current source is required to delete a device playlist. Historical
    source identities stay historical; later comparison still checks the
    current source. Only unchanged surviving native occurrences are renewed,
    so deleting a song cannot become an invented source-format omission.
    """
    if previous_witness is None:
        return ExportWitnessResult(None)
    notice = 'Some previous playlist comparison links could not be renewed; export those playlists to associate them again.'
    try:
        previous_witness.encode()
        if previous_witness.device_key != device_key:
            raise ValueError('Previous catalog belongs to another device')
        verify_export_witness(previous_witness, previous_database, previous_artwork_sha256)
        parsed = parse_itunesdb(io.BytesIO(rendered))
        native = get_ipod_tracks(parsed)
        by_dbid = {int(row.get('dbid') or 0): row for row in native}
        by_track = {int(row.get('trackID') or 0): int(row.get('dbid') or 0) for row in native}
        if len(by_dbid) != len(native) or len(by_track) != len(native):
            raise ValueError('Native deletion output song identities are ambiguous')
        before = {int(row.get('dbid') or 0): row for row in
                  get_ipod_tracks(parse_itunesdb(io.BytesIO(previous_database)))}
        rows = []; dropped = False
        for row in previous_witness.rows:
            actual = by_dbid.get(row.dbid)
            if actual is None:
                continue
            if str(actual.get('location') or '') != row.location or stored_metadata(actual) != row.metadata:
                dropped = True
                continue
            unchanged_art = all(actual.get(field) == before[row.dbid].get(field)
                                for field in ('mhiiLink', 'artworkCount', 'artworkSize'))
            art_key = (row.artwork_key if unchanged_art and (
                artwork_sha256 == previous_artwork_sha256 or row.dbid in preserved_artwork_dbids) else '')
            dropped = dropped or bool(row.artwork_key and not art_key)
            rows.append(replace(row, device_track_id=int(actual['trackID']), artwork_key=art_key))
        pairs = {(row.local_id, row.dbid) for row in rows}
        bindings = []
        for binding in previous_witness.bindings:
            matches = [row for row in parsed.get('mhlp', ()) if not row.get('isMaster')
                       and int(row.get('playlistID') or 0) == binding.device_playlist_id]
            if not matches:
                continue
            if (len(matches) != 1 or str(matches[0].get('Title') or '') != binding.name
                    or not set(binding.members) <= pairs
                    or tuple(by_track.get(int(item.get('trackID') or 0), 0)
                        for item in matches[0].get('items', ())) != tuple(dbid for _local, dbid in binding.members)):
                dropped = True
                continue
            bindings.append(binding)
        witness = replace(previous_witness, database_sha256=hashlib.sha256(rendered).hexdigest(),
            rows=tuple(rows), artwork_database_sha256=artwork_sha256, bindings=tuple(bindings))
        witness.encode()
        verify_export_witness(witness, rendered, artwork_sha256)
        return ExportWitnessResult(witness, notice=notice if dropped else '')
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        return ExportWitnessResult(None, notice=f'{notice} {exc}')
