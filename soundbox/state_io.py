"""Serialized, recoverable publication of SoundBox-owned state only.

One area lock spans library, artwork and backup snapshots. A write-ahead journal
retains original paths until every replacement is durable. Startup rolls back an
interrupted publication; a committed journal only needs cleanup. Not a device API.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
import threading
from pathlib import Path
from typing import Any


class StateChangedError(OSError):
    """A pre-restore consumer must reload before writing again."""


def sync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f'.{path.name}-', dir=path.parent)
    tmp = Path(name)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
        sync_dir(path.parent)
    finally:
        tmp.unlink(missing_ok=True)


def plain_tree(path: Path) -> list[Path]:
    """Reject links/special files, including dangling links, without following."""
    mode = path.lstat().st_mode
    if stat.S_ISREG(mode):
        return [path]
    if not stat.S_ISDIR(mode):
        raise ValueError(f'Not a regular state file/directory: {path}')
    files = []
    for child in sorted(path.iterdir()):
        files.extend(plain_tree(child))
    return files


def copy_state(src: Path, dst: Path) -> None:
    files = plain_tree(src)
    if src.is_dir():
        dst.mkdir(parents=True, exist_ok=True)
        for source in files:
            copy_state(source, dst / source.relative_to(src))
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        with dst.open('rb') as handle:
            os.fsync(handle.fileno())
    if dst.is_dir():
        for directory in sorted((p for p in dst.rglob('*') if p.is_dir()),
                                key=lambda p: len(p.parts), reverse=True):
            sync_dir(directory)
        sync_dir(dst)
    sync_dir(dst.parent)


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


class StateArea:
    def __init__(self, root: Path):
        self.root = root
        self.lock = threading.RLock()
        self.generation = 0
        self.art_revision = 0
        self.journal = root / '.state-transaction'
        self.retired = root / '.state-retired'
        self.root.mkdir(parents=True, exist_ok=True)
        self.recover()

    def path(self, relative: str) -> Path:
        p = Path(relative)
        if (p.is_absolute() or not p.parts or any(x in ('.', '..') for x in p.parts)
                or p.parts[0] in ('.state-transaction', '.state-retired')):
            raise ValueError(f'Invalid state publication path: {relative}')
        target = self.root / p
        for parent in (target, *target.parents):
            if parent == self.root:
                break
            if parent.is_symlink():
                raise ValueError(f'Symlink in state path: {parent}')
        return target

    def recover(self) -> None:
        with self.lock:
            if self.retired.exists() or self.retired.is_symlink():
                plain_tree(self.retired)
                _remove(self.retired)
                sync_dir(self.root)
            if not self.journal.exists():
                return
            plain_tree(self.journal)
            record = self.journal / 'journal.json'
            if not record.exists():
                # Preparation never publishes before this record is durable.
                _remove(self.journal)
                return
            rows = json.loads(record.read_text())
            if not isinstance(rows, list):
                raise ValueError('Invalid state recovery journal')
            for row in rows:
                self.path(row['path'])
            if not (self.journal / 'committed').exists():
                for i in reversed(range(len(rows))):
                    row = rows[i]
                    dest = self.path(row['path'])
                    old = self.journal / f'old-{i}'
                    new = self.journal / f'new-{i}'
                    if old.exists():
                        # Keep rollback restartable: original stays in old until
                        # the replacement has been removed, then moves home.
                        if old.is_dir() or dest.is_dir():
                            _remove(dest)
                        os.replace(old, dest)
                        sync_dir(dest.parent)
                        sync_dir(self.journal)
                    elif not row['existed'] and not new.exists():
                        _remove(dest)
                        sync_dir(dest.parent)
            # Retire atomically before recursive deletion. A crash midway
            # through cleanup must never erase the commit marker and cause
            # a later startup to roll back only some already-committed paths.
            os.replace(self.journal, self.retired)
            sync_dir(self.root)
            _remove(self.retired)
            sync_dir(self.root)

    def publish(self, changes: dict[str, bytes | Path | None]) -> None:
        """Caller holds lock when composing mutations from current state."""
        with self.lock:
            if self.retired.exists():
                self.recover()
            if self.journal.exists():
                raise OSError('State recovery is pending; restart before more writes')
            paths = [self.path(name) for name in changes]
            _reject_overlapping_paths(paths)
            for p in paths:
                if p.exists():
                    plain_tree(p)
            self.journal.mkdir()
            rows: list[dict[str, Any]] = []
            try:
                for i, (name, value) in enumerate(changes.items()):
                    target = paths[i]
                    target.parent.mkdir(parents=True, exist_ok=True)
                    new = self.journal / f'new-{i}'
                    if isinstance(value, bytes):
                        atomic_bytes(new, value)
                    elif isinstance(value, Path):
                        copy_state(value, new)
                    rows.append({'path': name, 'existed': target.exists()})
                atomic_bytes(self.journal / 'journal.json', json.dumps(rows).encode())
                sync_dir(self.root)
                for i, target in enumerate(paths):
                    if rows[i]['existed']:
                        old = self.journal / f'old-{i}'
                        if target.is_file():
                            # Preserve ordinary file replacement atomicity: the
                            # live name remains present while its exact rollback
                            # copy is durably published. Directories use rename.
                            atomic_bytes(old, target.read_bytes())
                        else:
                            os.replace(target, old)
                        sync_dir(target.parent)
                        sync_dir(self.journal)
                    new = self.journal / f'new-{i}'
                    if new.exists():
                        os.replace(new, target)
                    else:
                        _remove(target)
                    sync_dir(target.parent)
                    sync_dir(self.journal)
                try:
                    atomic_bytes(self.journal / 'committed', b'1')
                except Exception:
                    (self.journal / 'committed').unlink(missing_ok=True)
                    raise
            except Exception:
                # If rollback itself fails, retain the journal and all remaining
                # originals. Further writes refuse until recovery succeeds.
                try:
                    self.recover()
                except Exception as recovery_error:
                    raise OSError(
                        "State recovery is incomplete. Resolve the storage error and restart; "
                        f"preserve {self.journal} containing the remaining originals.") from recovery_error
                raise
            # Publication succeeded. A cleanup failure is not a failed mutation;
            # the durable commit marker makes startup cleanup unambiguous.
            try:
                self.recover()
            except OSError:
                pass


def _reject_overlapping_paths(paths: list[Path]) -> None:
    # Any ancestor/descendant pair has one endpoint among the other's proper
    # parents. Check those prefixes once instead of comparing every pair.
    staged = set(paths)
    if any(parent in staged for path in paths for parent in path.parents):
        raise ValueError('Overlapping state publication paths')


_areas: dict[Path, StateArea] = {}
_registry_lock = threading.RLock()


def state_area(root: Path) -> StateArea:
    # Resolve the area itself; child symlinks are always rejected by path().
    root = Path(root).resolve()
    with _registry_lock:
        if root not in _areas:
            _areas[root] = StateArea(root)
        return _areas[root]
