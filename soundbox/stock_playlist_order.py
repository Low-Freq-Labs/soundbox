"""Guarded stock-only playlist order, preserving native chunks and sidecars."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
import hashlib
import io
from pathlib import Path
import struct
from typing import TYPE_CHECKING, Sequence

from clickwheel.ipod import parse_itunesdb, get_ipod_tracks  # type: ignore[import-untyped]

from .ipod_export_witness import verify_export_witness
from .operation_control import CommittedOperationError, checkpoint
from .stock_ipod_capture import capture_stock_ipod_native
from .stock_ipod_catalog_state import StockCatalogWitness
from .stock_ipod_native_preserve import _datasets, _list, _rebuilt_dataset
from .stock_ipod_shadow import StockIpodShadow, StockIpodShadowStore
from .stock_ipod_signing import sign_stock_database, verified_stock_signing_context
from .sync import DeviceSession

if TYPE_CHECKING:
    from .stock_ipod import StockIpodSyncPlan


def _regular(database: bytes) -> list[dict]:
    from .stock_ipod_info import _playlist_snapshots
    parsed = parse_itunesdb(io.BytesIO(database))
    snapshots = _playlist_snapshots(parsed, get_ipod_tracks(parsed))
    smart_titles = {row.title.casefold() for row in snapshots if row.kind == 'Smart playlist'}
    eligible = {row.playlist_id for row in snapshots if row.kind == 'Playlist'
                and not (not row.entries and row.title.casefold() in smart_titles)}
    return [row for row in parsed.get('mhlp', ())
            if not row.get('isMaster') and not row.get('isSmartPlaylist')
            and int(row.get('playlistID') or 0) in eligible]


def _order(database: bytes, requested: tuple[int, ...], *, firewire_guid: str = '') -> bytes:
    """Permute complete MHYP chunks in regular datasets; all other bytes stay exact."""
    from .stock_ipod import StockIpodError
    rows = _regular(database)
    identities = [int(row.get('playlistID') or 0) for row in rows]
    if (any(type(value) is not int or not 0 < value < 2**64 for value in requested)
            or len(set(requested)) != len(requested)
            or Counter(identities) != Counter(requested) or any(count != 1 for count in Counter(identities).values())):
        raise StockIpodError('Playlist order must contain every current regular native playlist exactly once')
    wanted = set(requested)
    root, datasets = _datasets(database)
    result = []
    for kind, dataset in datasets.items():
        if kind not in {2, 3}:
            result.append(dataset); continue
        _header, chunks = _list(dataset)
        native = {}
        for chunk in chunks:
            if chunk[:4] != b'mhyp' or len(chunk) < 0x30:
                raise StockIpodError('Native playlist chunk is unavailable')
            identity = struct.unpack_from('<Q', chunk, 0x1C)[0]
            if identity in wanted:
                if identity in native:
                    raise StockIpodError('Native playlist order is ambiguous')
                native[identity] = chunk
        if set(native) != wanted:
            raise StockIpodError('Native playlist mirrors do not describe the same regular playlists')
        ordered = iter(native[identity] for identity in requested)
        rewritten = [next(ordered) if struct.unpack_from('<Q', chunk, 0x1C)[0] in native else chunk
                     for chunk in chunks]
        result.append(_rebuilt_dataset(dataset, dataset, rewritten))
    rendered = root + b''.join(result)
    if struct.unpack_from('<H', database, 0x30)[0] == 1 and rendered != database:
        rendered = sign_stock_database(rendered, verified_stock_signing_context(database, firewire_guid))
    return rendered


def validate_order_only_payload(before: bytes, after: bytes, *, firewire_guid: str = '') -> None:
    """A preserved history sidecar can never bypass an unrelated track mutation."""
    from .stock_ipod import StockIpodError
    requested = tuple(int(row.get('playlistID') or 0) for row in _regular(after))
    if _order(before, requested, firewire_guid=firewire_guid) != after:
        raise StockIpodError('Playlist-order output changed native data outside playlist ordering')


@dataclass(frozen=True)
class StockPlaylistOrderPlan:
    sync_plan: StockIpodSyncPlan
    playlist_ids: tuple[int, ...]
    original_database: bytes
    artwork_sha256: str
    witness: StockCatalogWitness | None = None
    shadow: StockIpodShadow | None = None
    shadow_store: StockIpodShadowStore | None = None

    @property
    def target(self) -> Path:return self.sync_plan.target

    @property
    def requires_commit(self) -> bool:return self.sync_plan.requires_commit

    @property
    def playlist_names(self) -> tuple[str, ...]:return self.sync_plan.playlist_names


def plan_stock_playlist_order(target: str | Path, playlist_ids: Sequence[int], *, snapshot,
                              model_number: str, filesystem: str, usb_family: str = '',
                              firewire_guid: str = '', shadow_store: StockIpodShadowStore | None = None,
                              catalog_witness: StockCatalogWitness | None = None) -> StockPlaylistOrderPlan:
    """Plan against a fresh small catalog; no source association or audio read."""
    from .stock_ipod import (StockIpodError, StockIpodSyncPlan, _captured_write_format,
                             _db_path, _shadow_matches_live, normalize_model_number)
    checkpoint()
    root = Path(target).expanduser().resolve(strict=True)
    session = DeviceSession(root, [], create=False)
    if str(filesystem).lower() not in {'fat', 'fat32', 'msdos'} or (root / '.rockbox').exists():
        raise StockIpodError('Native playlist order requires a stock-only FAT32 iPod')
    if Path(snapshot.target) != root:
        raise StockIpodError('The playlist order belongs to another iPod')
    _db_path(root)
    captured = capture_stock_ipod_native(root, include_artwork=True)
    if (captured.database_sha256 != snapshot.database_sha256
            or captured.artwork_database_sha256 != snapshot.artwork_database_sha256):
        raise StockIpodError('The native catalog changed; refresh the iPod playlist view')
    write_format = _captured_write_format(captured, usb_family=usb_family, firewire_guid=firewire_guid)
    requested = tuple(playlist_ids)
    rendered = _order(captured.database, requested, firewire_guid=write_format.firewire_guid)
    validate_order_only_payload(captured.database, rendered, firewire_guid=write_format.firewire_guid)
    rows = _regular(rendered)
    if tuple(int(row['playlistID']) for row in rows) != requested:
        raise StockIpodError('Rendered native playlist order did not survive independent parsing')
    witness = None
    if catalog_witness is not None and catalog_witness.device_key == snapshot.identity_key:
        try:
            verify_export_witness(catalog_witness, captured.database, captured.artwork_database_sha256)
            witness = replace(catalog_witness, database_sha256=hashlib.sha256(rendered).hexdigest())
            verify_export_witness(witness, rendered, captured.artwork_database_sha256)
        except (ValueError, TypeError, KeyError, OverflowError):
            witness = None
    shadow = shadow_store.load(snapshot.identity_key) if shadow_store else None
    if shadow is not None and not _shadow_matches_live(shadow, root=root, identity_key=snapshot.identity_key,
            model=normalize_model_number(model_number), serial_number=snapshot.serial_number,
            database_sha256=captured.database_sha256, native_capture=captured)[0]:
        shadow = None
    sync = StockIpodSyncPlan(target=root, target_mount_identity=session.mount_identity,
        model_number=normalize_model_number(model_number), database_relative_path=Path('iPod_Control/iTunes/iTunesDB'),
        database_sha256_before=captured.database_sha256,
        play_counts_sha256_before=captured.play_counts_sha256 if captured.play_counts is not None else None,
        rendered_database=rendered, playlist_names=tuple(str(row.get('Title') or '') for row in rows),
        identity_key=snapshot.identity_key, serial_number=snapshot.serial_number, catalog_only=True,
        stock_format=write_format, preserve_play_counts=True,
        catalog_playlist_updates=tuple(int(after['playlistID']) for before, after in
            zip(_regular(captured.database), rows, strict=True) if before['playlistID'] != after['playlistID']))
    session.revalidate()
    return StockPlaylistOrderPlan(sync, requested, captured.database, captured.artwork_database_sha256,
                                  witness, shadow, shadow_store)


def commit_stock_playlist_order(plan: StockPlaylistOrderPlan):
    """Use the normal guarded DB-last writer, then prove output before cache renewal."""
    from .stock_ipod import StockIpodCommitResult, commit_stock_ipod_sync
    session = DeviceSession(plan.target, [], create=False)
    if session.mount_identity != plan.sync_plan.target_mount_identity:
        raise ValueError('The iPod was remounted after playlist ordering was planned')
    result = (commit_stock_ipod_sync(plan.sync_plan) if plan.requires_commit else
        StockIpodCommitResult(plan.target, 0, 0, 0, 0, plan.sync_plan.playlist_names,
                             plan.sync_plan.database_sha256_before))
    try:
        captured = capture_stock_ipod_native(plan.target, include_artwork=True)
        session.revalidate()
        if (captured.database != plan.sync_plan.rendered_database
                or captured.artwork_database_sha256 != plan.artwork_sha256
                or (captured.play_counts_sha256 if captured.play_counts is not None else None)
                    != plan.sync_plan.play_counts_sha256_before):
            raise ValueError('Native playlist-order readback changed; refresh the connected iPod')
        witness = plan.witness
        if witness is not None:
            verify_export_witness(witness, captured.database, captured.artwork_database_sha256)
    except Exception as exc:
        if plan.requires_commit:
            raise CommittedOperationError('Playlist order saved; native readback needs attention: ' + str(exc)) from exc
        raise
    updated = False; warning = ''
    if plan.requires_commit and plan.shadow is not None and plan.shadow_store is not None:
        try:
            updated = plan.shadow_store.replace_if_current(plan.shadow,
                replace(plan.shadow, generation=plan.shadow.generation + 1,
                        database_sha256=captured.database_sha256))
            if not updated:warning = 'Playlist order saved; its optional comparison cache changed independently'
        except (OSError, ValueError) as exc:
            warning = 'Playlist order saved; optional comparison cache could not be renewed: ' + str(exc)
    return replace(result, catalog_witness=witness, shadow_updated=updated, shadow_warning=warning)
