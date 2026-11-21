"""Selected Rockbox flat-name migration; no media or foreign file ownership."""
from collections.abc import Sequence
import hashlib
import os
from pathlib import Path
from uuid import uuid4

from . import playlists, stock_delete
from .library import Playlist
from .manifest import playlist_key

PENDING_RETIREMENT = 'pending_playlist_name_retirement'


def remember_retirement(relative: Path, row: dict) -> dict:
    """Persist the original path/byte contract before current names replace it."""
    return {**row, PENDING_RETIREMENT: dict(
        source=row['source'], path=relative.as_posix(), size=row.get('size'), sha256=row.get('sha256'))}


def legacy_flat_playlists(current: dict, selected: Sequence[Playlist]) -> dict[str, tuple[Path, dict]]:
    """Find only exact prior manifest-owned flat files replaced by leaf names."""
    output = {}
    for playlist in selected:
        key = playlist_key(playlist)
        desired = playlists.flat_playlist_path(Path('Playlists'), [], playlist.name)
        # A previous publication may already have replaced the canonical source
        # name/folder. Its retained file row still owns the original path/proof.
        for device_path, row in current.get('files', {}).items():
            if not isinstance(row, dict):
                continue
            proof = row.get(PENDING_RETIREMENT)
            if not isinstance(proof, dict):
                continue
            relative = Path(str(proof.get('path') or ''))
            if (set(proof) == {'source', 'path', 'size', 'sha256'}
                    and row.get('kind') == 'playlist' and row.get('owned') is True
                    and row.get('source') == proof['source'] == key
                    and isinstance(row.get('playlists'), list) and set(row['playlists']) <= {key}
                    and row.get('size') == proof['size'] and row.get('sha256') == proof['sha256']
                    and device_path == '/' + relative.as_posix()
                    and not relative.is_absolute() and '..' not in relative.parts
                    and relative.parts and relative.parts[0] == 'Playlists'
                    and relative.suffix == '.m3u8'
                    and stock_delete.path_key(relative) != stock_delete.path_key(desired)):
                output[key] = relative, dict(row)
                break
        if key in output:
            continue
        previous = current.get('playlists', {}).get(key)
        if not isinstance(previous, dict):
            continue
        old = playlists.flat_playlist_path(Path('Playlists'), previous.get('folder_path') or [],
                                          str(previous.get('name') or ''))
        if stock_delete.path_key(old) == stock_delete.path_key(desired):
            continue
        row = current.get('files', {}).get('/' + old.as_posix())
        if (isinstance(row, dict) and row.get('kind') == 'playlist' and row.get('owned') is True
                and row.get('source') == key and isinstance(row.get('playlists'), list)
                and set(row['playlists']) <= {key}):
            output[key] = old, dict(row)
    return output


def retire_playlist(session, relative: Path, row: dict) -> None:
    """Prove the old owned text bytes, then use the guarded entity claim primitive."""
    if (type(row.get('size')) is not int or row['size'] < 0 or not isinstance(row.get('sha256'), str)
            or len(row['sha256']) != 64):
        raise OSError('The old playlist has no exact saved byte proof')
    item = stock_delete.capture(session, relative)
    if item is None:
        return
    with session.parent_fd(relative) as (parent, name):
        handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            if item.epoch != stock_delete._epoch(os.fstat(handle)) or item.size != row['size']:
                raise OSError('The old playlist changed; its file was preserved')
            digest = hashlib.sha256()
            while data := os.read(handle, 128 * 1024):
                digest.update(data)
            if item.epoch != stock_delete._epoch(os.fstat(handle)) or digest.hexdigest() != row['sha256']:
                raise OSError('The old playlist changed; its file was preserved')
        finally:
            os.close(handle)
    stock_delete.verify(session, item)
    stock_delete.retire(session, item, Path('.soundbox') / ('playlist-retire-' + uuid4().hex + '.m3u8'))
