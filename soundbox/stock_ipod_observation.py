"""Advisory linked-playlist comparison, separate from exact writer proofs.

An external exporter may add unrelated native content. These observations can
refresh a view or label an action; they never renew a witness or grant a write.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
import io

from .ipod_export_witness import stored_metadata
from .stock_ipod_capture import StockIpodNativeCapture
from .stock_ipod_catalog_state import StockCatalogWitness
from .stock_ipod_shadow import StockIpodShadow


@dataclass(frozen=True)
class LinkedCatalogObservation:
    changes: frozenset[str]
    live_keys: frozenset[str]
    local_ids: frozenset[str]
    dbids: frozenset[int]
    metadata: Mapping[tuple[str, int], tuple[tuple[str, str | int], ...]]
    artwork_links: Mapping[int, int]


def linked_artwork_matches(observed: LinkedCatalogObservation, shadow: StockIpodShadow,
                           captured: StockIpodNativeCapture) -> bool:
    """Compare known captured image records; foreign records carry no drift."""
    from .stock_artwork import StockArtworkError, parse_stock_artwork_slots
    known = {int(dbid): row for dbid, row in shadow.artwork.items() if int(dbid) in observed.dbids}
    if not known:
        return True
    try:
        slots = parse_stock_artwork_slots(captured.artwork_database or b'')
        for dbid, row in known.items():
            image_id = int(row.get('image_id') or 0)
            slot = slots.get(image_id)
            if (slot is None or observed.artwork_links.get(dbid) != image_id
                    or slot.source_size != int(row.get('source_size') or 0)
                    or dict(slot.offsets) != {int(key): int(value)
                                             for key, value in (row.get('offsets') or {}).items()}):
                return False
    except (StockArtworkError, ValueError, TypeError, KeyError, OverflowError):
        return False
    return True


def observe_linked_catalog(witness: StockCatalogWitness, captured: StockIpodNativeCapture,
                           source: tuple[str, str, str], device_key: str) -> LinkedCatalogObservation:
    """Compare only saved live bindings; newly observed rows are device-owned."""
    from clickwheel.ipod import get_ipod_tracks, parse_itunesdb  # type: ignore[import-untyped]
    from .stock_ipod_info import _header_from_bytes

    witness.encode()
    if (witness.source != source or witness.device_key != device_key or not witness.bindings
            or device_key.rsplit(':', 1)[-1]
                != f'{_header_from_bytes(captured.database).library_persistent_id:016X}'):
        raise ValueError('Linked comparison identity is unavailable')
    if any(count != 1 for count in Counter(row.device_playlist_id for row in witness.bindings).values()):
        raise ValueError('Linked playlist identity is ambiguous')
    parsed = parse_itunesdb(io.BytesIO(captured.database))
    tracks = get_ipod_tracks(parsed)
    by_dbid = {int(row.get('dbid') or 0): row for row in tracks}
    by_track = {int(row.get('trackID') or 0): int(row.get('dbid') or 0) for row in tracks}
    if (len(by_dbid) != len(tracks) or len(by_track) != len(tracks)
            or 0 in by_dbid or 0 in by_track):
        raise ValueError('Observed song identities are ambiguous')
    playlists: dict[int, list] = defaultdict(list)
    for row in parsed.get('mhlp', ()):
        if not row.get('isMaster'):
            playlists[int(row.get('playlistID') or 0)].append(row)
    pairs = {(row.local_id, row.dbid): row for row in witness.rows}
    changes: set[str] = set()
    live_keys: set[str] = set()
    wanted: set[tuple[str, int]] = set()
    for binding in witness.bindings:
        current = playlists.get(binding.device_playlist_id, ())
        if not current:
            # Device-side deletion is an observed fact, not consent to recreate.
            continue
        if len(current) != 1:
            raise ValueError('Observed linked playlist identity is ambiguous')
        live_keys.add(binding.key)
        wanted.update(binding.members)
        occurrences = tuple(by_track.get(int(item.get('trackID') or 0), 0)
                       for item in current[0].get('items', ()))
        if 0 in occurrences:
            raise ValueError('Linked playlist contains an unknown native reference')
        if (str(current[0].get('Title') or '') != binding.name
                or occurrences != tuple(dbid for _local, dbid in binding.members)):
            changes.add(binding.key)
    metadata = {}
    artwork = {}
    local_counts = Counter(row.local_id for row in witness.rows)
    dbid_counts = Counter(row.dbid for row in witness.rows)
    for pair in wanted:
        row = pairs.get(pair)
        if row is None or local_counts[row.local_id] != 1 or dbid_counts[row.dbid] != 1:
            raise ValueError('Saved song correspondence is ambiguous')
        actual = by_dbid.get(row.dbid)
        if actual is None:
            continue  # The changed known membership above remains pending.
        if str(actual.get('location') or '') != row.location:
            raise ValueError('Observed linked song location changed')
        metadata[pair] = stored_metadata(actual)
        artwork[row.dbid] = int(actual.get('mhiiLink') or 0)
    return LinkedCatalogObservation(frozenset(changes), frozenset(live_keys),
        frozenset(local for local, _dbid in wanted), frozenset(dbid for _local, dbid in wanted),
        metadata, artwork)
