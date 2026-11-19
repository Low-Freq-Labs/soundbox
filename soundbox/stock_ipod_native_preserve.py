"""Preserve native classic catalog data around explicitly rendered changes.

No file IO or device discovery. Known album/artist and browse indexes follow
rendered metadata; existing opaque records, identities and children stay native.
"""
from __future__ import annotations

from collections import Counter, defaultdict, deque
from collections.abc import Mapping, Sequence
import struct


class NativePreservationError(ValueError):
    pass


_FIELDS = {'title': 1, 'artist': 4, 'album': 3, 'genre': 5, 'comments': 8}


def _u32(data: bytes | bytearray, offset: int) -> int:
    if offset < 0 or offset + 4 > len(data):
        raise NativePreservationError('Native chunk is truncated')
    return int(struct.unpack_from('<I', data, offset)[0])


def _track_field(data: bytes, offset: int) -> int:
    return _u32(data, offset) if _u32(data, 4) >= offset + 4 else 0


def _chunks(data: bytes, offset: int, count: int) -> tuple[list[bytes], int]:
    result = []
    if count > len(data) // 12:
        raise NativePreservationError('Native child count is invalid')
    for _ in range(count):
        header, total = _u32(data, offset + 4), _u32(data, offset + 8)
        if header < 12 or total < header or offset + total > len(data):
            raise NativePreservationError('Native child framing is invalid')
        result.append(data[offset:offset + total]); offset += total
    return result, offset


def _datasets(data: bytes) -> tuple[bytes, dict[int, bytes]]:
    header = _u32(data, 4)
    if data[:4] != b'mhbd' or header < 0x50 or header > len(data) or _u32(data, 8) != len(data):
        raise NativePreservationError('Native database framing is invalid')
    chunks, end = _chunks(data, header, _u32(data, 0x14))
    if end != len(data):
        raise NativePreservationError('Native dataset inventory is incomplete')
    result = {}
    for chunk in chunks:
        kind = _u32(chunk, 12)
        if chunk[:4] != b'mhsd' or kind in result:
            raise NativePreservationError('Native dataset identity is ambiguous')
        result[kind] = chunk
    return data[:header], result


def _list(dataset: bytes) -> tuple[bytes, list[bytes]]:
    offset = _u32(dataset, 4)
    header = _u32(dataset, offset + 4)
    if header < 12 or offset + header > len(dataset):
        raise NativePreservationError('Native list header is invalid')
    chunks, end = _chunks(dataset, offset + header, _u32(dataset, offset + 8))
    if end != len(dataset):
        raise NativePreservationError('Native list inventory is incomplete')
    return dataset[offset:offset + header], chunks


def _mhods(chunk: bytes) -> tuple[list[bytes], int]:
    children, end = _chunks(chunk, _u32(chunk, 4), _u32(chunk, 12))
    if any(child[:4] != b'mhod' for child in children):
        raise NativePreservationError('Native metadata child is unsupported')
    return children, end


def _merge_mhods(before: Sequence[bytes], after: Sequence[bytes], changed: set[int]) -> list[bytes]:
    replacements: dict[int, list[bytes]] = defaultdict(list)
    counts = Counter(_u32(child, 12) for child in before)
    if any(counts[kind] > 1 for kind in changed & {1, 3, 4, 5, 8}):
        raise NativePreservationError('Native editable metadata is ambiguous')
    for child in after:
        replacements[_u32(child, 12)].append(child)
    result = []; replaced = set()
    for child in before:
        kind = _u32(child, 12)
        if kind in changed:
            if kind not in replaced:
                result.extend(replacements[kind]); replaced.add(kind)
        else:
            result.append(child)
    for child in after:
        kind = _u32(child, 12)
        if kind in changed and kind not in replaced:
            result.extend(replacements[kind]); replaced.add(kind)
    return result


def _string(child: bytes) -> str:
    offset = _u32(child, 4)
    encoding, length = _u32(child, offset), _u32(child, offset + 4)
    payload = child[offset + 16:offset + 16 + length]
    if len(payload) != length or encoding not in {1, 2}:
        raise NativePreservationError('Native index string encoding is unsupported')
    try:
        return payload.decode('utf-16-le' if encoding == 1 else 'utf-8')
    except UnicodeError as exc:
        raise NativePreservationError('Native index string is invalid') from exc


def _labels(chunk: bytes) -> dict[int, str]:
    children, _end = _mhods(chunk)
    return {_u32(child, 12): _string(child) for child in children
            if _u32(child, 12) in {3, 4, 200, 201, 300}}


def _rebuilt_dataset(before: bytes | None, after: bytes, chunks: Sequence[bytes]) -> bytes:
    ds_header = bytearray((before or after)[:_u32(before or after, 4)])
    list_header = bytearray(_list(before or after)[0])
    struct.pack_into('<I', list_header, 8, len(chunks))
    result = ds_header + list_header + b''.join(chunks)
    struct.pack_into('<I', result, 8, len(result))
    return bytes(result)


def preserve_native_catalog(original: bytes, rendered: bytes, *,
                            metadata_fields: Mapping[int, Sequence[str]] = {}) -> bytes:
    """Keep opaque data and stable native IDs; rebuild only defined indexes."""
    root, before = _datasets(original); _rendered_root, after = _datasets(rendered)
    if len(root) != len(_rendered_root) or 1 not in before or 1 not in after:
        raise NativePreservationError('Native root/track layout is unsupported')
    old_tracks = _list(before[1])[1]; new_tracks = _list(after[1])[1]
    old_by_dbid = {int(struct.unpack_from('<Q', row, 0x70)[0]): row for row in old_tracks}
    if len(old_by_dbid) != len(old_tracks):
        raise NativePreservationError('Native track identity is ambiguous')
    old_id_to_dbid = {_u32(row, 0x10): dbid for dbid, row in old_by_dbid.items()}
    rendered_id_to_dbid = {_u32(row, 0x10): int(struct.unpack_from('<Q', row, 0x70)[0]) for row in new_tracks}
    occupied = {value for row in old_tracks for value in (_u32(row, 0x10), _track_field(row, 0x120),
                _track_field(row, 0x1E0), _track_field(row, 0x1F4)) if value}
    for kind in (4, 8):
        if kind in before:
            occupied.update(_u32(row, 0x10) for row in _list(before[kind])[1])
    for kind in (2, 3, 5):
        if kind not in before:
            continue
        for playlist in _list(before[kind])[1]:
            _children, offset = _mhods(playlist)
            items, _end = _chunks(playlist, offset, _u32(playlist, 0x10))
            for item in items:
                children, _end = _mhods(item)
                occupied.update(_u32(child, _u32(child, 4)) for child in children if _u32(child, 12) == 100)
    next_id = max(occupied, default=0) + 1
    def allocate() -> int:
        nonlocal next_id
        if next_id > 0xFFFFFFFF:
            raise NativePreservationError('Native identity space is exhausted')
        value = next_id; next_id += 1
        return value
    grouped_datasets = {}; index_maps: dict[int, dict[int, int]] = {}
    artist_index: dict[str, int] = {}
    for kind in (4, 8):
        if kind not in after or kind not in before:
            continue
        def key(row: bytes) -> tuple[str, ...]:
            labels = _labels(row)
            return (labels.get(200, ''), labels.get(201, '')) if kind == 4 else (labels.get(300, '').lower(),)
        raw_originals = _list(before[kind])[1] if kind in before else []
        originals = {key(row): row for row in raw_originals}
        if len(originals) != len(raw_originals):
            raise NativePreservationError('Native album/artist grouping is ambiguous')
        mapped = {}; rows = []
        for row in _list(after[kind])[1]:
            old = originals.get(key(row)); identity = _u32(old, 0x10) if old is not None else allocate()
            mapped[_u32(row, 0x10)] = identity
            native = bytearray(old or row)
            struct.pack_into('<I', native, 0x10, identity)
            rows.append(bytes(native))
            if kind == 8:
                artist_index[key(row)[0]] = identity
        index_maps[kind] = mapped
        grouped_datasets[kind] = _rebuilt_dataset(before.get(kind), after[kind], rows)
    # Some classic catalogs carry native artist IDs without a type-8 table.
    # Keep those IDs for unchanged names; new names receive distinct IDs.
    artist_ids: dict[str, int] = {}
    for row in old_tracks:
        artist = _labels(row).get(4, '').lower(); identity = _track_field(row, 0x1E0)
        if artist and identity:
            artist_ids.setdefault(artist, identity)
    patched_tracks = []; output_ids: dict[int, int] = {}
    for row in new_tracks:
        dbid = int(struct.unpack_from('<Q', row, 0x70)[0]); old = old_by_dbid.get(dbid)
        native = bytearray(row[:_u32(row, 4)])
        identity = _u32(old, 0x10) if old is not None else allocate()
        output_ids[dbid] = identity; struct.pack_into('<I', native, 0x10, identity)
        if 4 in index_maps and len(native) >= 0x124:
            struct.pack_into('<I', native, 0x120, index_maps[4].get(_u32(row, 0x120), 0))
        if old is not None and 'artist' not in metadata_fields.get(dbid, ()) and len(native) >= 0x1E4:
            struct.pack_into('<I', native, 0x1E0, _track_field(old, 0x1E0))
        elif 8 in index_maps and len(native) >= 0x1E4:
            struct.pack_into('<I', native, 0x1E0, artist_index.get(_labels(row).get(4, '').lower(), 0))
        elif len(native) >= 0x1E4:
            artist = _labels(row).get(4, '').lower()
            if artist and artist not in artist_ids:
                artist_ids[artist] = allocate()
            struct.pack_into('<I', native, 0x1E0, artist_ids.get(artist, 0))
        if old is not None and len(native) >= 0x1F8:
            struct.pack_into('<I', native, 0x1F4, _track_field(old, 0x1F4))
        rendered_children, end = _mhods(row)
        if end != len(row):
            raise NativePreservationError('Native track has unsupported trailing data')
        if old is not None:
            original_children, end = _mhods(old)
            if end != len(old):
                raise NativePreservationError('Native track has unsupported trailing data')
            changed = {_FIELDS[field] for field in metadata_fields.get(dbid, ()) if field in _FIELDS}
            children = _merge_mhods(original_children, rendered_children, changed)
        else:
            children = rendered_children
        struct.pack_into('<II', native, 8, len(native) + sum(map(len, children)), len(children))
        patched_tracks.append(bytes(native) + b''.join(children))
    result_datasets = {1: _rebuilt_dataset(before[1], after[1], patched_tracks), **grouped_datasets}
    for kind in (2, 3, 5):
        if kind not in after:
            continue
        def playlist_key(row: bytes) -> int:
            return 0 if kind in {2, 3} and row[0x14] else int(struct.unpack_from('<Q', row, 0x1C)[0])
        original_playlists = {playlist_key(row): row for row in _list(before[kind])[1]} if kind in before else {}
        playlists = []
        for row in _list(after[kind])[1]:
            old = original_playlists.get(playlist_key(row))
            children, offset = _mhods(row)
            items, end = _chunks(row, offset, _u32(row, 0x10))
            if end != len(row) or any(item[:4] != b'mhip' for item in items):
                raise NativePreservationError('Native playlist item framing is invalid')
            old_items: dict[int, deque[bytes]] = defaultdict(deque)
            old_sequence = []
            if old is not None:
                original_children, offset = _mhods(old)
                raw_items, end = _chunks(old, offset, _u32(old, 0x10))
                if end != len(old):
                    raise NativePreservationError('Native playlist has unsupported trailing data')
                for item in raw_items:
                    member_dbid = old_id_to_dbid.get(_u32(item, 0x18))
                    if member_dbid is None:
                        raise NativePreservationError('Native playlist references an unknown track')
                    old_items[member_dbid].append(item)
                    old_sequence.append(member_dbid)
                old_title = next((_string(child) for child in original_children if _u32(child, 12) == 1), '')
                new_title = next((_string(child) for child in children if _u32(child, 12) == 1), '')
                children = _merge_mhods(original_children, children,
                                       {52, 53} | ({1} if old_title != new_title else set()))
            patched_items = []
            new_sequence = [rendered_id_to_dbid.get(_u32(item, 0x18)) for item in items]
            membership_changed = old is None or old_sequence != new_sequence
            for position, item in enumerate(items):
                member_dbid = rendered_id_to_dbid.get(_u32(item, 0x18))
                if member_dbid is None or member_dbid not in output_ids:
                    raise NativePreservationError('Rendered playlist references an unknown track')
                native_item = bytearray(old_items[member_dbid].popleft() if old_items[member_dbid] else item)
                struct.pack_into('<I', native_item, 0x18, output_ids[member_dbid])
                item_children, end = _mhods(bytes(native_item))
                if end != len(native_item):
                    raise NativePreservationError('Native playlist item has unsupported trailing data')
                patched_children = []
                for child in item_children:
                    if membership_changed and _u32(child, 12) == 100:
                        changed_child = bytearray(child)
                        struct.pack_into('<I', changed_child, _u32(child, 4), position)
                        child = bytes(changed_child)
                    patched_children.append(child)
                patched_items.append(bytes(native_item[:_u32(native_item, 4)]) + b''.join(patched_children))
            native_header = bytearray((old or row)[:_u32(old or row, 4)])
            body = b''.join(children) + b''.join(patched_items)
            struct.pack_into('<III', native_header, 8, len(native_header) + len(body), len(children), len(items))
            playlists.append(bytes(native_header) + body)
        result_datasets[kind] = _rebuilt_dataset(before.get(kind), after[kind], playlists)
    # A reference layout is a capability fact. Do not introduce an artist/
    # album dataset merely because the generic renderer knows that family.
    order = list(before)
    chunks = [result_datasets.get(kind, before.get(kind, after.get(kind))) for kind in order]
    if any(chunk is None for chunk in chunks):
        raise NativePreservationError('Native dataset preservation failed')
    result = bytearray(root) + b''.join(chunk for chunk in chunks if chunk is not None)
    struct.pack_into('<I', result, 8, len(result)); struct.pack_into('<I', result, 0x14, len(chunks))
    return bytes(result)
