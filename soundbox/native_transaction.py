"""Shared native-library admission, locking and encrypted connection mechanics.

This lease grants no SQL scope or recovery protocol. Each accepted domain owns
its authority, record validation, backups and publication. Guard calls remain
fresh before/after lock admission and whenever a connection opens.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
import fcntl
import os
from pathlib import Path
import stat
from threading import Lock
from typing import Protocol

from .rekordbox_write import (
    ConflictError, RecoveryRequired, WriteProofError, _guard_grid_recovery,
    _identity, _owned_inactive_journal, _quote, _regular,
)
from .rekordbox_tags import _schema
from .operation_control import checkpoint, copied_operation_contexts


class _EvidenceSession:
    """Verified private payloads belong only to the current exclusive writer.

    Recovery archives are immutable during this lease. Reobserve their complete
    file epochs and private parent identities instead of streaming the same DBs
    at every guard. This retains no source observation or authority, and cannot
    survive lock release, changed bytes/paths, or a later recovery attempt.
    """

    def __init__(self, root: Path, source_device: int | None):
        self.root = root
        self.device = root.stat().st_dev if source_device is None else source_device
        # This is private local native recovery, never FAT/exFAT iPod evidence.
        # Match the admitted native source's local filesystem as well as the
        # existing private ownership checks; redirected/external roots opt out.
        self.active = (root == root.resolve() and not root.is_relative_to('/Volumes')
                       and root.stat().st_dev == self.device)
        self.proofs: dict[tuple[Path, int], tuple[Callable[[Path], str], tuple, str]] = {}
        self.lock = Lock()

    def epoch(self, path: Path) -> tuple | None:
        try:
            if not path.is_relative_to(self.root) or path != path.resolve():
                return None
            info = path.lstat()
            if (info.st_dev != self.device or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid != os.getuid()):
                return None
            parents = []
            for parent in path.parents:
                row = parent.lstat()
                if (not stat.S_ISDIR(row.st_mode) or row.st_mode & 0o077
                        or row.st_uid != os.getuid()):
                    return None
                # New receipt/journal names can change directory timestamps;
                # they do not change an already verified payload's bytes.
                parents.append((row.st_dev, row.st_ino, row.st_mode, row.st_uid, row.st_gid))
                if parent == self.root:
                    break
            return ((info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
                     info.st_uid, info.st_gid, info.st_size, info.st_mtime_ns,
                     info.st_ctime_ns, getattr(info, 'st_flags', 0)), tuple(parents))
        except (OSError, RuntimeError):
            return None  # Scheduling/reuse never substitutes the domain's error.

    def get(self, path, hashing, epoch):
        with self.lock:
            found = self.proofs.get((path, id(hashing)))
            if (self.active and epoch is not None and found is not None
                    and found[0] is hashing and found[1] == epoch):
                return found[2]
        return None

    def remember(self, path, hashing, before, digest):
        if (before is None or type(digest) is not str or len(digest) != 64
                or any(char not in '0123456789abcdef' for char in digest)
                or self.epoch(path) != before):
            return
        with self.lock:
            if self.active:
                if len(self.proofs) >= 64:
                    self.proofs.pop(next(iter(self.proofs)))
                self.proofs[path, id(hashing)] = hashing, before, digest

    def close(self):
        with self.lock:
            self.active = False
            self.proofs.clear()


_evidence_session: ContextVar[_EvidenceSession | None] = ContextVar('native_evidence_session', default=None)


@contextmanager
def _private_evidence_session(root: Path, *, source_device: int | None = None):
    session = _EvidenceSession(root, source_device)
    token = _evidence_session.set(session)
    try:
        yield
    finally:
        session.close()
        _evidence_session.reset(token)


def hash_evidence(paths: Iterable[Path], hash_file: Callable[[Path], str]) -> list[str]:
    """Verify private archive evidence within the current writer lease.

    Each domain supplies its unchanged descriptor/identity-checking hasher.
    First use and any changed epoch hash independently; matching private
    evidence reuses that lease's verification. Outside a lease every call hashes.
    All siblings join before the owner retires; small receipts stay serial.
    """
    paths = list(paths)
    session = _evidence_session.get()
    def hashing(path):
        before = session.epoch(path) if session is not None else None
        found = session.get(path, hash_file, before) if session is not None else None
        if found is not None:
            checkpoint()
            return found
        digest = hash_file(path)
        if session is not None:
            session.remember(path, hash_file, before, digest)
        return digest
    try:
        parallel = sum(path.lstat().st_size >= 1024 * 1024 for path in paths) >= 2
    except OSError:
        # Scheduling hints must not preempt the domain's ordered admission or
        # check error with a failed metadata lookup of a later payload.
        parallel = False
    if not parallel:
        return [hashing(path) for path in paths]
    contexts = copied_operation_contexts(len(paths))
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix='native-evidence') as workers:
        pending = [workers.submit(context.run, hashing, path)
                   for context, path in zip(contexts, paths, strict=True)]
        # Executor exit joins every sibling even if an earlier result fails or
        # cancellation is raised. Recovery/locks outlive the actual hash work.
        return [future.result() for future in pending]

class NativeTransactionAuthority(Protocol):
    @property
    def database(self) -> str: ...
    @property
    def library_id(self) -> str: ...
    @property
    def schema_sha256(self) -> str: ...
    @property
    def application_version(self) -> str: ...


class NativeTransactionLease:
    """Infrastructure only; subclasses supply fresh app/version/recovery checks."""

    def _native_version(self):
        raise NotImplementedError

    def _assert_native_closed(self):
        raise NotImplementedError

    def _record(self):
        raise NotImplementedError

    def __init__(self, authority: NativeTransactionAuthority, recovery_root: Path):
        self.authority = authority
        self.database = Path(authority.database)
        self.root = Path(recovery_root).absolute()
        if (self.root != self.root.resolve()
                or self.root.is_relative_to(self.database.parent)
                or self.database.is_relative_to(self.root)):
            raise WriteProofError('Cue recovery overlaps or redirects the source library')
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.stat().st_mode & 0o077:
            raise WriteProofError('Cue recovery directory must be private')
        self.journal = self.root / 'transaction.json'
        self._last_counter: int | None = None
        self._backup_proof: tuple | None = None
        self._backup_sha = ''

    def _guard_other_recovery(self):
        _guard_grid_recovery(self.root, self.journal)

    def _guard(self):
        if (not self.database.is_absolute() or self.database != self.database.resolve()
                or self.database.name != 'master.db' or self.database.is_relative_to('/Volumes')):
            raise WriteProofError('Cue writes require the explicitly enabled local database')
        _regular(self.database)
        if self.database.stat().st_uid != os.getuid():
            raise WriteProofError('Source database is not owned by the current user')
        if self.root != self.root.resolve() or self.root.stat().st_mode & 0o077:
            raise WriteProofError('Cue recovery directory changed')
        self._guard_other_recovery()
        for suffix in ('-wal', '-shm', '-journal'):
            p = self.database.with_name(self.database.name + suffix)
            if p.exists() or p.is_symlink():
                _regular(p)
        for name in ('baseline.db', 'baseline.json', 'transaction.json', 'last-result.json'):
            p = self.root / name
            if p.exists() or p.is_symlink():
                _regular(p)
        if self._native_version() != self.authority.application_version:
            raise WriteProofError('This rekordbox version has no cue-write authority')
        self._assert_native_closed()
        if self.journal.exists():
            self._record()  # validate evidence before admitting hot-journal recovery

    @contextmanager
    def _lock(self):
        self._guard()
        fd = os.open(self.root / 'writer.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            _regular(self.root / 'writer.lock')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise WriteProofError('Another rekordbox operation is finishing') from exc
            with _private_evidence_session(self.root, source_device=self.database.stat().st_dev):
                self._guard()
                yield
        finally:
            os.close(fd)

    def _connect(self):
        from .native_cipher import key_connection
        from sqlcipher3 import dbapi2 as sqlcipher
        self._guard()
        if (self.database.with_name('master.db-journal').exists() and not self.journal.exists()
                and not _owned_inactive_journal(self.database, self.root, self.authority.library_id)):
            raise RecoveryRequired('An unowned SQLite journal needs rekordbox recovery first')
        conn = sqlcipher.connect(f'{self.database.as_uri()}?mode=rw', uri=True,
                                 isolation_level=None, timeout=0.5)
        try:
            key_connection(conn)
            conn.execute('PRAGMA trusted_schema=OFF')
            conn.execute('PRAGMA synchronous=FULL')
            if (_identity(conn) != (self.authority.library_id, '6000')
                    or _schema(conn) != self.authority.schema_sha256):
                raise ConflictError('The enabled rekordbox identity/schema changed')
            return conn
        except BaseException:
            conn.close()
            raise

    @staticmethod
    def _rows(conn, table, where='', params=()):
        cur = conn.execute(f'SELECT * FROM {_quote(table)}' + where, params)
        names = [v[0] for v in cur.description]
        return [dict(zip(names, row, strict=True)) for row in cur]
