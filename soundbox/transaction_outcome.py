"""Explicit transaction evidence, mirrored locally when a mount disappears."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import stat
import struct
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from . import stock_delete

LOG = logging.getLogger("soundbox")
_RETIREMENT = 'soundbox-device-payload-retirement-v1'
_PENDING = 'soundbox-device-pending-retirement-v1'
_METADATA = frozenset(('plan.json', 'artwork-ranges.json', '._plan.json', '._artwork-ranges.json'))
_legacy_limit_logged = False


def _evidence_child(directory: Path, path: Path, *, payload: bool = False) -> Path:
    """Saved device metadata cannot grant access outside its private archive."""
    try:relative = path.relative_to(directory)
    except ValueError:
        raise RuntimeError('Recovery evidence escapes its transaction') from None
    if (path.is_absolute() or not relative.parts
            or any(part == '..' or '\\' in part for part in path.parts)
            or (payload and relative.as_posix() in _METADATA)):
        raise RuntimeError('Recovery evidence is not a private transaction child')
    return relative


def _directory_identity(session: Any, path: Path) -> tuple[int, int]:
    with session.parent_fd(path / '.recovery-directory') as (handle, _name):
        info = os.fstat(handle)
        return info.st_dev, info.st_ino


def _read_metadata(session: Any, path: Path, limit: int) -> dict:
    with session.parent_fd(path) as (parent, name):
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(descriptor, 'rb') as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
                raise RuntimeError('Recovery plan is not bounded regular metadata')
            raw = handle.read()
            if (stock_delete._epoch(os.fstat(handle.fileno())) != stock_delete._epoch(before)
                    or stock_delete._epoch(os.stat(name, dir_fd=parent, follow_symlinks=False)) != stock_delete._epoch(before)):
                raise RuntimeError('Recovery plan changed while reading')
    result = json.loads(raw)
    if not isinstance(result, dict):raise RuntimeError('Unknown recovery plan')
    return result


def _read_plan(session: Any, directory: Path) -> dict:
    return _read_metadata(session, directory / 'plan.json', 16 * 1024**2)


def _pending(session: Any, root: Path) -> dict:
    try:row = _read_metadata(session, root / 'pending-retirement.json', 64 * 1024)
    except FileNotFoundError:return {'format': _PENDING, 'directories': {}}
    entries = row.get('directories')
    if (set(row) != {'format', 'directories'} or row.get('format') != _PENDING
            or not isinstance(entries, dict) or len(entries) > 64
            or any(type(name) is not str or not name or name in ('.', '..')
                   or Path(name).name != name or len(name) > 128
                   or not isinstance(identity, list) or len(identity) != 2
                   or any(type(cell) is not int or cell < 0 for cell in identity)
                   for name, identity in entries.items())):
        raise RuntimeError('Unrecognized pending recovery index is preserved')
    return row


def _save_pending(session: Any, root: Path, before: dict, after: dict) -> None:
    if _pending(session, root) != before:raise RuntimeError('Pending recovery index changed')
    session.atomic_write(root / 'pending-retirement.json', json.dumps(after, sort_keys=True).encode() + b'\n')


def _forget_pending(session: Any, directory: Path, identity: tuple[int, int]) -> None:
    before = _pending(session, directory.parent);entries = dict(before['directories'])
    if directory.name not in entries:return
    if tuple(entries[directory.name]) != identity:raise RuntimeError('Pending recovery directory changed')
    entries.pop(directory.name)
    _save_pending(session, directory.parent, before, dict(format=_PENDING, directories=entries))


def _appledouble(session: Any, item: stock_delete.IndexedFile) -> bool:
    """Recognize bounded OS metadata beside an explicitly created private entity."""
    if not 26 <= item.size <= 128 * 1024:return False
    with session.parent_fd(item.relative_path) as (parent, name):
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
        with os.fdopen(descriptor, 'rb') as handle:
            if stock_delete._epoch(os.fstat(handle.fileno())) != item.epoch:return False
            header = handle.read(26)
            if len(header) != 26:return False
            magic, version, _filler, count = struct.unpack('>II16sH', header)
            if magic != 0x00051607 or version != 0x00020000 or not 1 <= count <= 32:return False
            table = handle.read(count * 12)
            if len(table) != count * 12:return False
            for _kind, offset, size in struct.iter_unpack('>III', table):
                if offset < 26 + count * 12 or offset + size > item.size:return False
            if stock_delete._epoch(os.fstat(handle.fileno())) != item.epoch:return False
            return stock_delete._epoch(os.stat(name, dir_fd=parent, follow_symlinks=False)) == item.epoch


@dataclass(frozen=True)
class RecoveryPayloads:
    """Entities created by this transaction, never a device-media ownership grant.

    Only immutable metadata crosses an inner stock commit. Its outer owner keeps
    these exact rollback payloads until the complete composite transaction ends.
    """
    directory: Path
    mount_identity: tuple[int, int]
    directory_identity: tuple[int, int]
    files: tuple[stock_delete.IndexedFile, ...] = ()
    directories: tuple[tuple[Path, tuple[int, int]], ...] = ()

    def require_current(self, session: Any) -> None:
        session.revalidate()
        if (session.mount_identity != self.mount_identity
                or _directory_identity(session, self.directory) != self.directory_identity):
            raise RuntimeError('Recovery directory changed before payload retirement')

    def remember(self, session: Any, paths=(), *, directories=()) -> RecoveryPayloads:
        self.require_current(session)
        files = {item.relative_path: item for item in self.files}
        known = dict(self.directories)
        new_directories = {Path(path) for path in directories}
        companions = set()
        for path in paths:
            path = Path(path)
            relative = path.relative_to(self.directory)
            if not relative.parts or '..' in relative.parts or str(relative) in _METADATA:
                raise RuntimeError('Invalid transient recovery payload')
            item = stock_delete.capture(session, path)
            if item is None:raise RuntimeError(f'Recovery payload is missing: {relative}')
            files[path] = item
            companions.add(path.with_name('._' + path.name))
            new_directories.update(parent for parent in path.parents if parent != self.directory and parent.is_relative_to(self.directory))
        for path in sorted(new_directories):
            if path in known:continue
            if path == self.directory or not path.is_relative_to(self.directory):
                raise RuntimeError('Invalid recovery child directory')
            known[path] = _directory_identity(session, path)
            companions.add(path.with_name('._' + path.name))
        for path in sorted(companions):
            item = stock_delete.capture(session, path)
            if item is not None and _appledouble(session, item):files[path] = item
        return replace(self, files=tuple(files.values()), directories=tuple(known.items()))

    def _inventory(self, session: Any) -> tuple[set[Path], dict[Path, tuple[int, int]]]:
        self.require_current(session)
        files: set[Path] = set();directories: dict[Path, tuple[int, int]] = {}
        def visit(descriptor, path):
            with os.scandir(descriptor) as scan:entries = list(scan)
            if len(files) + len(directories) + len(entries) > 65536:
                raise RuntimeError('Recovery inventory exceeds its bounded transaction scope')
            for entry in entries:
                relative = path / entry.name
                info = entry.stat(follow_symlinks=False)
                if info.st_dev != self.mount_identity[0]:raise RuntimeError('Recovery inventory crosses the device')
                if stat.S_ISDIR(info.st_mode):
                    child = os.open(entry.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY, dir_fd=descriptor)
                    try:
                        opened = os.fstat(child)
                        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):raise RuntimeError('Recovery directory changed')
                        directories[relative] = (opened.st_dev, opened.st_ino)
                        visit(child, relative)
                    finally:os.close(child)
                elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:files.add(relative)
                else:raise RuntimeError('Unknown recovery symlink or special file is preserved')
        with session.parent_fd(self.directory / 'plan.json') as (descriptor, _name):
            if (os.fstat(descriptor).st_dev, os.fstat(descriptor).st_ino) != self.directory_identity:
                raise RuntimeError('Recovery directory changed while opening')
            visit(descriptor, self.directory)
        return files, directories

    def retire(self, session: Any) -> None:
        """Retire this completed transaction without streaming its saved payloads.

        The intent precedes disposal and survives partial cleanup. Unknown or
        replaced entries preserve evidence; cleanup failure never rolls back a
        committed device generation. Existing unmarked archives are not adopted.
        """
        # Resume reads untrusted device JSON. Lexical is_relative_to alone also
        # admits archive/../../../foreign files; validate every row before any
        # disposal, including rows absent after an interrupted prior cleanup.
        if (self.directory.is_absolute() or len(self.directory.parts) != 3
                or self.directory.parts[0] != '.soundbox'
                or self.directory.parts[1] not in ('stock-ipod-recovery', 'dual-recovery')
                or self.directory.name in ('.', '..') or '\\' in self.directory.name):
            raise RuntimeError('Invalid recovery transaction directory')
        for evidence_file in self.files:
            _evidence_child(self.directory, evidence_file.relative_path, payload=True)
            if (len(evidence_file.epoch) != 7 or any(type(value) is not int for value in evidence_file.epoch)
                    or len(evidence_file.parent_identity) != 2
                    or any(type(value) is not int for value in evidence_file.parent_identity)):
                raise RuntimeError('Invalid recovery payload identity')
        for path, identity in self.directories:
            _evidence_child(self.directory, path)
            if len(identity) != 2 or any(type(value) is not int for value in identity):
                raise RuntimeError('Invalid recovery child directory identity')
        self.require_current(session)
        plan = _read_plan(session, self.directory)
        if (plan.get('transaction_state') not in ('committed', 'rolled back')
                or plan.get('target') != str(session.target)
                or tuple(plan.get('mount_identity') or ()) != self.mount_identity):
            raise RuntimeError('Unfinished recovery payloads must be preserved')
        present, directories = self._inventory(session)
        # FAT spelling is NFD/case-insensitive even when the captured plan used
        # NFC. Matching a spelling never replaces the exact entity guards below.
        expected = {stock_delete.path_key(item.relative_path): item for item in self.files}
        observed = {stock_delete.path_key(path): path for path in present}
        observed_directories = {stock_delete.path_key(path): identity for path,identity in directories.items()}
        known_directories = {stock_delete.path_key(path): identity for path,identity in self.directories}
        if (len(expected) != len(self.files) or len(observed) != len(present)
                or len(observed_directories) != len(directories)
                or len(known_directories) != len(self.directories)):
            raise RuntimeError('Recovery required: ambiguous filesystem spellings are preserved')
        metadata = {self.directory / name for name in _METADATA}
        metadata_keys = {stock_delete.path_key(path) for path in metadata}
        for key in metadata_keys & observed.keys():
            path = observed[key]
            if path.name.startswith('._'):
                item = stock_delete.capture(session, path)
                if item is None or not _appledouble(session, item):
                    raise RuntimeError('Recovery required: unrecognized metadata companion is preserved')
        unknown = [path for key,path in observed.items() if key not in expected and key not in metadata_keys]
        replaced = [path for path, identity in directories.items() if known_directories.get(stock_delete.path_key(path)) != identity]
        if unknown or replaced:
            detail = ', '.join(str(path.relative_to(self.directory)) for path in sorted([*unknown, *replaced])[:4])
            raise RuntimeError(f'Recovery required: unknown recovery entries are preserved: {detail}')
        for key, item in expected.items():
            if key in observed:stock_delete.verify(session, item)
        claims = self.directory / '.retiring'
        current = self
        if stock_delete.path_key(claims) not in observed_directories and expected.keys() & observed.keys():
            with session.parent_fd(claims, create=True) as (parent, name):os.mkdir(name, dir_fd=parent)
            current = self.remember(session, directories=(claims,))
            # FAT can create a companion for this newly owned directory. Only
            # that freshly captured, validated OS metadata joins the inventory;
            # an absent row from the saved plan grants no later disposal.
            for captured in current.files:
                key = stock_delete.path_key(captured.relative_path)
                if key not in expected:observed[key] = captured.relative_path
        plan['payload_retirement'] = {
            'format': _RETIREMENT, 'complete': False,
            'directory_identity': self.directory_identity, 'mount_identity': self.mount_identity,
            'files': [dict(path=str(item.relative_path), epoch=item.epoch, parent=item.parent_identity) for item in current.files],
            'directories': [dict(path=str(path), identity=identity) for path, identity in current.directories],
        }
        session.atomic_write(self.directory / 'plan.json', json.dumps(plan, indent=2, sort_keys=True).encode() + b'\n')
        # Retire each companion before its base entity so a filesystem-managed
        # metadata rename cannot leave an unclaimed sibling in the claims dir.
        ordered = sorted((item for item in current.files
                          if stock_delete.path_key(item.relative_path) in observed),
                         key=lambda item: not item.relative_path.name.startswith('._'))
        for index, item in enumerate(ordered):
            if stock_delete.capture(session, item.relative_path) is not None:
                stock_delete.retire(session, item, claims / f'{index:08d}.pending')
        remaining, directories = current._inventory(session)
        if any(stock_delete.path_key(path) not in metadata_keys for path in remaining):raise RuntimeError('Recovery payload retirement remains incomplete')
        known = {stock_delete.path_key(path):identity for path,identity in current.directories}
        for path in sorted(directories, key=lambda item: len(item.parts), reverse=True):
            if known.get(stock_delete.path_key(path)) != directories[path]:raise RuntimeError('Recovery child directory changed before cleanup')
            with session.parent_fd(path) as (parent, name):
                info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if (info.st_dev, info.st_ino) != known[stock_delete.path_key(path)]:raise RuntimeError('Recovery directory was replaced')
                session.revalidate();os.rmdir(name, dir_fd=parent)
        plan['payload_retirement'] = dict(format=_RETIREMENT, complete=True,
            retired_files=len(current.files), retired_bytes=sum(item.size for item in current.files))
        plan.pop('delete_candidates', None)
        session.atomic_write(self.directory / 'plan.json', json.dumps(plan, indent=2, sort_keys=True).encode() + b'\n')
        _forget_pending(session, self.directory, self.directory_identity)


def begin_recovery(session: Any, directory: Path) -> RecoveryPayloads:
    """Exclusively create one new transaction namespace; never reuse an archive."""
    directory = Path(directory)
    if (len(directory.parts) != 3 or directory.parts[0] != '.soundbox'
            or directory.parts[1] not in ('stock-ipod-recovery', 'dual-recovery')):
        raise RuntimeError('Invalid recovery transaction directory')
    with session.parent_fd(directory, create=True) as (parent, name):os.mkdir(name, mode=0o700, dir_fd=parent)
    identity = _directory_identity(session, directory)
    before = _pending(session, directory.parent);entries = dict(before['directories'])
    if len(entries) >= 64:raise RuntimeError('Pending recovery index is full; unfinished work must be reviewed')
    entries[directory.name] = list(identity)
    _save_pending(session, directory.parent, before, dict(format=_PENDING, directories=entries))
    return RecoveryPayloads(directory, session.mount_identity, identity)


def resume_completed_payload_retirement(session: Any, family: str) -> tuple[str, ...]:
    """Resume bounded sealed cleanup only; legacy/unproved archives stay untouched."""
    global _legacy_limit_logged
    if family not in ('stock-ipod-recovery', 'dual-recovery'):raise ValueError(family)
    if not _legacy_limit_logged:
        LOG.info('Device payload cleanup reads only its bounded pending index; unsealed legacy archives are not scanned or adopted')
        _legacy_limit_logged = True
    root = Path('.soundbox') / family
    entries = _pending(session, root)['directories']
    failures = []
    for name, identity in entries.items():
        directory = root / name
        try:
            if _directory_identity(session, directory) != tuple(identity):raise RuntimeError('Pending recovery directory was replaced')
            plan = _read_plan(session, directory);row = plan.get('payload_retirement') or {}
            if row.get('format') != _RETIREMENT:continue
            if row.get('complete') is True:
                _forget_pending(session, directory, tuple(identity));continue
            evidence = RecoveryPayloads(directory, tuple(row['mount_identity']), tuple(row['directory_identity']),
                tuple(stock_delete.IndexedFile(Path(item['path']), tuple(item['epoch']), tuple(item['parent'])) for item in row['files']),
                tuple((Path(item['path']), tuple(item['identity'])) for item in row['directories']))
            evidence.retire(session)
        except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
            failures.append(f'{directory}: {error}')
    return tuple(failures)


def record_outcome(session: Any, recovery: Path, state: str, *,
                   error: str = "", local_root: Path | None = None) -> None:
    if state not in {"executing", "committed", "rolled back", "recovery required"}:
        raise ValueError(state)
    row = {"target": str(session.target), "mount_identity": session.mount_identity,
           "recovery": str(recovery), "transaction_state": state, "error": error}
    # A local record survives an unplug and never authorizes automatic writes.
    if local_root is not None:
        try:
            root = local_root / "transaction-outcomes-v1"
            root.mkdir(parents=True, exist_ok=True)
            key = hashlib.sha256((str(session.target) + str(recovery)).encode()).hexdigest()
            fd, name = tempfile.mkstemp(dir=root, prefix=".outcome-")
            try:
                with os.fdopen(fd, "w") as handle:
                    json.dump(row, handle, sort_keys=True)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(name, root / f"{key}.json")
            finally:
                Path(name).unlink(missing_ok=True)
        except OSError as exc:
            LOG.warning("Could not persist local transaction outcome: %s", exc)
    try:
        session.revalidate()
        journal = session.guard(recovery / "plan.json")
        data = json.loads(journal.read_text())
        data.update(row)
        session.atomic_write(journal, json.dumps(data, sort_keys=True, indent=2).encode() + b"\n")
    except (OSError, RuntimeError, ValueError) as exc:
        LOG.warning("Could not publish %s transaction evidence on device: %s", state, exc)
