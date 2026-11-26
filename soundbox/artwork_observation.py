"""Current local cover choices for advisory device drift, never writer proof.

Only workers resolve/stat/hash these images. The bounded epoch memo supplies a
pending-state hint; exporters retain independent source/payload verification.
"""
from collections import OrderedDict
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import stat
from threading import Lock


_DIGESTS: OrderedDict[tuple, str] = OrderedDict()
_LOCK = Lock()
_LIMIT = 8192


def _epoch(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def image_digest(path) -> str:
    """Read-only scheduling hint, invalidated by every ordinary file change."""
    path = Path(path).absolute()
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > 64 * 1024**2:
        raise ValueError('Current cover is not a bounded regular image')
    key = str(path), _epoch(before)
    with _LOCK:
        found = _DIGESTS.get(key)
        if found is not None:
            _DIGESTS.move_to_end(key)
            if _epoch(path.lstat()) != key[1]:
                raise ValueError('Current cover changed during cached observation')
            return found
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, 'rb') as stream:
        if _epoch(os.fstat(stream.fileno())) != _epoch(before):
            raise ValueError('Current cover changed before observation')
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if _epoch(os.fstat(stream.fileno())) != _epoch(before):
            raise ValueError('Current cover changed during observation')
    if _epoch(path.lstat()) != _epoch(before):
        raise ValueError('Current cover changed after observation')
    with _LOCK:
        _DIGESTS[key] = digest
        _DIGESTS.move_to_end(key)
        while len(_DIGESTS) > _LIMIT:
            _DIGESTS.popitem(last=False)
    return digest


@contextmanager
def selected_images(art_cache, track_ids):
    """Observe one source/song-bound choice set; reject retired or changed views."""
    identities = tuple(dict.fromkeys(track_ids))
    token = art_cache.selection_token(identities)
    if not art_cache.selection_current(token):
        raise ValueError('Artwork source changed during device comparison')
    images = {tid: art_cache.image_path(tid) for tid in identities}
    yield images
    if not art_cache.selection_current(token):
        raise ValueError('Selected artwork changed during device comparison')


def changed_playlists(manifest, library, playlists, art_cache):
    """Compare owned Rockbox art baselines without reading any device pixels."""
    if art_cache is None:
        return ()
    from .manifest import playlist_key
    managed = manifest.get('playlists', {})
    wanted = [playlist for playlist in playlists if playlist_key(playlist) in managed]
    tracks = {track.track_id for playlist in wanted for track in library.resolve(playlist)}
    rows: dict[tuple[str, str | None], list[dict]] = {}
    for row in manifest.get('files', {}).values():
        if not isinstance(row, dict) or row.get('kind') not in ('artwork', 'rockbox-artwork'):
            continue
        for key in row.get('playlists', ()):
            rows.setdefault((str(key), row.get('track_id')), []).append(row)
    changed = []
    with selected_images(art_cache, tracks) as images:
        digests = {}
        for playlist in wanted:
            key = playlist_key(playlist)
            for track in library.resolve(playlist):
                image = images[track.track_id]
                if image is None:
                    continue  # Missing local art preserves existing device covers.
                previous = rows.get((key, track.track_id), ())
                if track.track_id not in digests:
                    digests[track.track_id] = image_digest(image)
                if not previous or any(row.get('source_sha256') != digests[track.track_id]
                                       for row in previous):
                    changed.append((key, playlist.name))
                    break
    return tuple(changed)


def stock_changes(shadow, library, linked_ids, native_capture, art_cache, current_keys=None):
    """Compare today's selected image, including fallback-to-cover path changes."""
    ids = set(linked_ids) & library.tracks_by_id.keys()
    previous = {str(row.get('track_id')): row for row in shadow.artwork.values()}
    changed = set()
    profile = None
    current_keys = current_keys or {}
    with selected_images(art_cache, ids) as images:
        for tid, image in images.items():
            if image is None:
                continue
            row = previous.get(tid)
            current_key = current_keys.get(tid)
            if row is None and not current_key:
                changed.add('art:' + tid); continue
            row = row or {}
            image = Path(image)
            info = image.lstat()
            if not stat.S_ISREG(info.st_mode):
                raise ValueError('Current cover is not a regular image')
            if (str(image) == row.get('art_path') and info.st_size == row.get('art_size')
                    and info.st_mtime_ns == row.get('art_mtime_ns')
                    and (not current_key or current_key == row.get('cache_key'))):
                continue
            digest = image_digest(image)
            expected = row.get('source_sha256') if not current_key else None
            if expected:
                same = digest == expected
            else:
                # Earlier shadows retain a prepared-profile key rather than a
                # JPEG hash. Identical adopted art must not demand a rewrite.
                if profile is None:
                    from .stock_artwork import _database_profile
                    profile = _database_profile(native_capture.artwork_database)
                key = hashlib.sha256((profile + ':' + digest).encode()).hexdigest()
                same = key == (current_key or row.get('cache_key'))
            if not same:
                changed.add('art:' + tid)
    return changed
