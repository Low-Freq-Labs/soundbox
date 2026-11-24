"""Shared native-library admission, locking and encrypted connection mechanics.

This lease grants no SQL scope or recovery protocol. Each accepted domain owns
its authority, record validation, backups and publication. Guard calls remain
fresh before/after lock admission and whenever a connection opens.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
from typing import Protocol

from .rekordbox_write import (
    ConflictError, RecoveryRequired, WriteProofError, _guard_grid_recovery,
    _identity, _owned_inactive_journal, _quote, _regular,
)
from .rekordbox_tags import _schema


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
