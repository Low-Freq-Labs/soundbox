"""Read-only witnesses for actual source-conversion transitions.

A conversion policy intention is not an audio mutation. Device exporters use
their own media policies; this module checks only the selected operation's
recovery/staging evidence. Call while the shared source lane is reserved, and
recheck on the worker before publication. It never reads media or native DBs,
creates recovery directories, invokes recovery, or interprets error messages.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat

from .media_conversion_jobs import validate
from .operation_control import checkpoint
from .rekordbox_analysis_store import shared_root


class MediaTransitionChanged(RuntimeError):
    """The exact read-only transition observation cannot be established."""


class MediaTransitionPending(RuntimeError):
    """Recovery/staging exists and must finish before exporting this source."""


def _identity(value: os.stat_result) -> tuple[int, ...]:
    return value.st_dev, value.st_ino, value.st_mode, value.st_uid


def _absolute(value: str | Path, label: str) -> Path:
    if not isinstance(value, (str, Path)):
        raise MediaTransitionChanged(f'{label} must be an explicit canonical path')
    text = str(value)
    path = Path(text)
    if (not path.is_absolute() or '\0' in text or '..' in path.parts
            or str(path) != text or path == Path('/')):
        raise MediaTransitionChanged(f'{label} must be an explicit canonical path')
    return path


class _Observation:
    """Own directory descriptors through the final named-parent checks."""
    def __init__(self):
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        root = os.open('/', flags)
        try:
            identity = _identity(os.fstat(root))
        except OSError:
            os.close(root)
            raise
        self.handles = {Path('/'): root}
        self.directories = {Path('/'): identity}
        self.absent: dict[Path, tuple[int, ...]] = {}
        self.flags = flags

    def close(self):
        for descriptor in reversed(tuple(self.handles.values())):
            os.close(descriptor)

    def directory(self, path: Path) -> int | None:
        if path in self.handles:
            return self.handles[path]
        current = Path('/')
        for name in path.parts[1:]:
            checkpoint()
            child = current / name
            if child in self.absent:
                return None
            if child in self.handles:
                current = child
                continue
            parent = self.handles[current]
            try:
                before = os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                self.absent[child] = self.directories[current]
                return None
            if not stat.S_ISDIR(before.st_mode):
                raise MediaTransitionChanged('Conversion recovery or audio ancestry redirects')
            descriptor = os.open(name, self.flags, dir_fd=parent)
            self.handles[child] = descriptor
            identity = _identity(os.fstat(descriptor))
            if identity != _identity(before):
                raise MediaTransitionChanged('Conversion recovery or audio ancestry changed')
            self.directories[child] = identity
            current = child
        return self.handles[path]

    def require_absent(self, path: Path) -> None:
        parent = self.directory(path.parent)
        if parent is None:
            return
        checkpoint()
        try:
            present = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            self.absent[path] = self.directories[path.parent]
            return
        if stat.S_ISLNK(present.st_mode):
            raise MediaTransitionChanged('Conversion recovery or staging redirects')
        raise MediaTransitionPending('Selected song conversion or locator recovery must finish before export')

    def finish(self):
        # Directory mtimes may change for unrelated saves. Pin the directory
        # entity and its named parent relationship, not every sibling entry.
        for path, descriptor in self.handles.items():
            checkpoint()
            identity = self.directories[path]
            if _identity(os.fstat(descriptor)) != identity:
                raise MediaTransitionChanged('Conversion transition ancestry changed')
            if path != Path('/'):
                named = os.stat(path.name, dir_fd=self.handles[path.parent], follow_symlinks=False)
                if _identity(named) != identity:
                    raise MediaTransitionChanged('Conversion transition ancestry was replaced')
        for path in self.absent:
            checkpoint()
            try:
                os.stat(path.name, dir_fd=self.handles[path.parent], follow_symlinks=False)
            except FileNotFoundError:
                continue
            raise MediaTransitionChanged('Conversion transition appeared during export preparation')
        return (tuple((str(path), identity) for path, identity in sorted(self.directories.items())),
                tuple((str(path), identity) for path, identity in sorted(self.absent.items())))


@dataclass(frozen=True)
class MediaTransitionProof:
    data_root: str
    source: str
    library_id: str
    jobs_payload: bytes
    directories: tuple
    absent: tuple

    def require_current(self) -> bool:
        """Worker-only fresh absence check; no policy or success inference."""
        current = capture_media_transitions(self.data_root, self.source, self.library_id,
                                            json.loads(self.jobs_payload))
        if current.directories != self.directories or current.absent != self.absent:
            selected = json.loads(self.jobs_payload)
            name = Path(selected[0]['path']).name + ': ' if len(selected) == 1 else ''
            raise MediaTransitionChanged(name + 'Conversion transition ownership changed before export')
        return True


def capture_media_transitions(data_root: str | Path, source: str | Path,
                              library_id: str, jobs) -> MediaTransitionProof:
    """Prove selected policy jobs have no physical transition evidence.

    Every existing selected operation folder (including empty/unknown or a
    completed receipt awaiting acknowledgement), exact stage, shared journal or
    grid recovery holds export. An errored or merely queued job without those
    effects is not a physical transition. Active workers are a separate desktop
    ownership fence. Fresh acquisition and each target's codec rules still apply.
    """
    root = _absolute(data_root, 'AppData root')
    database = _absolute(source, 'Source database')
    if (type(library_id) is not str or not library_id or '\0' in library_id
            or root.is_relative_to(database.parent) or database.is_relative_to(root)):
        raise MediaTransitionChanged('Conversion transition source scope is invalid')
    try:
        payload = json.dumps(list(jobs), sort_keys=True, allow_nan=False,
                             separators=(',', ':')).encode()
        selected = json.loads(payload)
        validate(selected)
    except (TypeError, ValueError) as exc:
        raise MediaTransitionChanged('Conversion transition job identity is invalid') from exc
    if (any((job['source'], job['library_id']) != (str(database), library_id) for job in selected)
            or len({job['id'] for job in selected}) != len(selected)):
        raise MediaTransitionChanged('Conversion transition job source or operation changed')
    audio = [_absolute(job['path'], 'Selected audio path') for job in selected]
    if any(path.is_relative_to(root) or path.is_relative_to(database.parent) for path in audio):
        raise MediaTransitionChanged('Conversion transition audio scope is invalid')
    observation = None
    try:
        observation = _Observation()
        if observation.directory(root) is None:
            raise MediaTransitionChanged('AppData root is unavailable for conversion transition capture')
        recovery = shared_root(root, database, library_id)
        observation.require_absent(recovery / 'transaction.json')
        observation.require_absent(recovery / 'grid-transaction')
        for job, path in zip(selected, audio):
            checkpoint()
            try:
                observation.require_absent(root / 'media-conversion-originals' / job['id'])
                observation.require_absent(path.parent / ('.soundbox-media-' + job['id'] + '.wav'))
            except (MediaTransitionPending, MediaTransitionChanged) as exc:
                raise type(exc)(path.name + ': ' + str(exc)) from exc
        directories, absent = observation.finish()
    except OSError as exc:
        raise MediaTransitionChanged('Conversion transition paths changed or could not be checked') from exc
    finally:
        if observation is not None:
            observation.close()
    return MediaTransitionProof(str(root), str(database), library_id, payload, directories, absent)
