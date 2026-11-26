"""First-launch choices; pure state preparation, never native write authority."""
from dataclasses import dataclass
from pathlib import Path

from . import metadata, metadata_cleanup_policy
from .library import Library

FORMAT = 'soundbox-first-launch-v1'
AUTO_ARTWORK = 'auto_artwork'


def has_existing_state(root):
    """Legacy/recoverable state must never be mistaken for an empty first run."""
    return any((Path(root) / name).exists() or (Path(root) / name).is_symlink()
               for name in ('library.json', '.state-records', '.state-transaction', 'backups', 'art'))


@dataclass(frozen=True)
class CleanupExample:
    before_title: str
    before_artist: str
    after_title: str
    after_artist: str


@dataclass(frozen=True)
class LibraryPreview:
    library: Library
    examples: tuple[CleanupExample, ...]
    changed_tracks: int


def preview(library: Library) -> LibraryPreview:
    """Use the real whole-library registry without touching media or the source."""
    tracks = tuple(library.tracks_by_id.values())
    registry = metadata.build_registry(
        [metadata.derive_artist(track.name, track.artist)[0] for track in tracks],
        [track.artist for track in tracks if track.artist])
    examples: list[CleanupExample] = []
    changed = 0
    for track in tracks:
        artist = metadata.effective_artist(track.track_id, track.name, track.artist,
                                           registry=registry, stem=track.path.stem)
        title = metadata.effective_title(track.track_id, track.name, artist, source_artist=track.artist, registry=registry)
        artist = '' if artist == metadata.UNKNOWN_ARTIST else artist
        if (title, artist) != (track.name, track.artist):
            changed += 1
            if len(examples) < 3:
                examples.append(CleanupExample(track.name, track.artist, title, artist))
    return LibraryPreview(library, tuple(examples), changed)


def setup_state(previous, library, *, clean, artwork):
    """The caller durably saves this before creating the ordinary main window.

    A cleanup choice supplies intent only. Independently admitted native profiles
    and exact library authorities continue to decide whether writes may run.
    """
    if type(clean) is not bool or type(artwork) is not bool:
        raise ValueError('First-launch choices must be explicit')
    mode = 'clean' if clean else 'original'
    settings = metadata_cleanup_policy.with_choice(previous.get('settings') or {}, library, mode)
    settings.update(rekordbox_db_path=str(library.source), auto_artwork=artwork,
                    first_launch=dict(format=FORMAT, completed=True,
                                      scope=metadata_cleanup_policy.source_scope(library),
                                      cleanup=mode, artwork=artwork))
    return {**previous, 'settings': settings}
