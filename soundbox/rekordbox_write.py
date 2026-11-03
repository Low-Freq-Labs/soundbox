"""Offline rekordbox tag-write proof. NOT a live-library writer.

Only freshly captured, marked private temporary workspaces are writable. The
source is copied without opening it with SQLCipher; all SQL runs on copies.
There is deliberately no commit-to-source API, application integration, analysis
writer, or production compatibility claim. Tag metadata conventions remain
provisional until the separately reviewed native rekordbox pilot.

Each operation proves its complete semantic delta on an encrypted staging DB,
backs up the preceding state, journals before mutation, commits atomically with
SQLite, and reads back before reporting success. Recovery recognizes only the
exact before/after states; it never overwrites an unexpected database.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from typing import Any, Iterator, Literal
from uuid import uuid4

from .rekordbox_db import _copy_stable_snapshot, _snapshot_identity
from .state_io import atomic_bytes, sync_dir

_PREFIX = "soundbox-rb-write-"
_FORMAT = "soundbox-offline-rekordbox-write-v1"
_LINK_COLUMNS = (
    "ID", "MyTagID", "ContentID", "TrackNo", "UUID", "rb_data_status",
    "rb_local_data_status", "rb_local_deleted", "rb_local_synced", "usn",
    "rb_local_usn", "created_at", "updated_at",
)
_TRANSACTION_FILES = {
    f"{name}{suffix}" for name in ("before.db", "after.db")
    for suffix in ("", "-wal", "-shm", "-journal")
} | {"journal.json"}


class WriteProofError(RuntimeError):
    """The offline operation could not be safely completed."""


class ConflictError(WriteProofError):
    """The source identity or expected baseline no longer matches."""


class RecoveryRequired(WriteProofError):
    """Preserved recovery material must be reconciled before further writes."""


@dataclass(frozen=True)
class TagAssignmentPlan:
    workspace_id: str
    baseline_sha256: str
    track_ids: tuple[str, ...]
    tag_id: str
    present: bool
    expected_present: tuple[bool, ...]


@dataclass(frozen=True)
class TagEditResult:
    operation_id: str
    outcome: Literal["changed", "unchanged", "recovered-commit", "recovered-rollback"]
    state_sha256: str
    changed_tracks: int


def _json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode()


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value)).hexdigest()


def _regular(path: Path) -> None:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise WriteProofError(f"Not a private regular workspace file: {path.name}")


def _inactive_journal_proof(database: Path) -> dict | None:
    """Identify an inactive SQLite journal without changing or deleting it.

    SQLite's rollback journal is not hot with a zero header. A crash before
    dirty pages spill can leave this file behind even after a verified rollback.
    https://www.sqlite.org/lockingv3.html#dealing_with_hot_journals
    Only a verified recovery caller may publish this proof in its receipt.
    """
    journal = database.with_name(database.name + '-journal')
    if not journal.exists():
        return None
    _regular(journal)
    def identity():
        s = journal.stat()
        return [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns]
    before = identity()
    sha = hashlib.sha256()
    with journal.open('rb') as handle:
        first = handle.read(8)
        if first != b'\0' * len(first):
            raise RecoveryRequired('SQLite journal is not verified inactive; evidence preserved')
        sha.update(first)
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            sha.update(block)
    if before != identity():
        raise RecoveryRequired('SQLite journal changed during recovery inspection')
    return {'identity': before, 'sha256': sha.hexdigest()}


def _owned_inactive_journal(database: Path, recovery_root: Path, library_id: str) -> bool:
    """A shared-domain receipt may recognize only the exact inactive leftover."""
    try:
        receipt = recovery_root / 'last-result.json'
        _regular(receipt)
        record = json.loads(receipt.read_bytes())
        info = database.stat()
        return (record.get('format') in ('soundbox-native-cues-v1', 'soundbox-native-grid-v1', 'soundbox-native-playlists-v1',
                                       'soundbox-native-registration-v1','soundbox-native-removal-v1',
                                       'soundbox-native-media-locator-v1')
                and record.get('source') == str(database) and record.get('library_id') == library_id
                and record.get('source_file_id') == [info.st_dev, info.st_ino]
                and record.get('outcome') in ('committed', 'rolled-back')
                and record.get('inactive_sqlite_journal') is not None
                and record['inactive_sqlite_journal'] == _inactive_journal_proof(database))
    except (OSError, ValueError, TypeError, AttributeError, WriteProofError):
        return False


def _directory(path: Path) -> None:
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077:
        raise WriteProofError(f"Not a private workspace directory: {path.name}")


def _guard_grid_recovery(root: Path, journal: Path) -> None:
    payloads = root / 'grid-transaction'
    if (payloads.exists() or payloads.is_symlink()) and not journal.exists():
        raise RecoveryRequired('Grid recovery must retire before another library edit')


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _connect(path: Path, *, create: bool = False, readonly: bool = False):
    """Internal copy-only connection. Never print the key or connection URI."""
    from pyrekordbox.db6.database import BLOB
    from pyrekordbox.utils import deobfuscate
    from sqlcipher3 import dbapi2 as sqlcipher

    if (not path.is_absolute() or path != path.resolve()
            or not path.is_relative_to(Path(tempfile.gettempdir()).resolve())):
        raise WriteProofError("SQL connections are restricted to temporary copies")
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = path.with_name(path.name + suffix)
        if sidecar.exists() or sidecar.is_symlink():
            _regular(sidecar)
    if create:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
    _regular(path)
    mode = "ro" if readonly else "rw"
    conn = sqlcipher.connect(f"{path.as_uri()}?mode={mode}", uri=True,
                             isolation_level=None, timeout=0.25)
    try:
        key = deobfuscate(BLOB)
        # This is the pinned package's passphrase, never input or diagnostic text.
        conn.execute("PRAGMA key='" + key.replace("'", "''") + "'")
        conn.execute("PRAGMA trusted_schema=OFF")
        conn.execute("PRAGMA synchronous=FULL")
        if readonly:
            conn.execute("PRAGMA query_only=ON")
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        return conn
    except BaseException:
        conn.close()
        raise


def _backup(source: Path, destination: Path) -> None:
    src = _connect(source, readonly=True)
    try:
        dst = _connect(destination, create=True)
        try:
            src.backup(dst)
            _integrity(dst)
        finally:
            dst.close()
    finally:
        src.close()
    with destination.open("rb") as handle:
        os.fsync(handle.fileno())
    sync_dir(destination.parent)


def assert_rekordbox_closed() -> None:
    """Fresh kernel query, retaining the independent pgrep path on uncertainty."""
    from .mac_process import rekordbox_running
    running = rekordbox_running()
    if running is True:
        raise WriteProofError('Close rekordbox and its background agent before editing')
    if running is False:
        return
    try:
        result = subprocess.run(
            ['/usr/bin/pgrep', '-x', '(rekordbox|rekordboxAgent)'],
            capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError) as exc:
        raise WriteProofError('Could not verify that rekordbox and its agent are closed') from exc
    if result.returncode == 0:
        raise WriteProofError('Close rekordbox and its background agent before editing')
    if result.returncode != 1 or result.stdout.strip() or result.stderr.strip():
        raise WriteProofError('Could not verify that rekordbox and its agent are closed')


def _empty_captured_wal(path: Path) -> bool:
    """Only an absent/zero-byte private WAL proves no committed pages to fold."""
    wal = path.with_name(path.name + '-wal')
    if not wal.exists() and not wal.is_symlink():
        return True
    _regular(wal)
    return wal.stat().st_size == 0


def _captured_sha(path: Path) -> str:
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def _standalone_capture(captured: Path, baseline: Path) -> bool:
    """Verify and durably adopt one self-contained encrypted TEMP capture.

    Stable source DB/WAL copying and hot-journal refusal already happened. Any
    nonempty WAL (even header-only) keeps the original SQLite folding path. A
    known DELETE/WAL-mode main file with no WAL pages needs no re-encryption:
    full integrity/identity and unchanged captured bytes are proved on the
    private copy before it is exposed. No source SQL or mode changes occur.
    """
    if not _empty_captured_wal(captured):
        return False
    _regular(captured)
    before = _snapshot_identity(captured)
    digest = _captured_sha(captured)
    conn = _connect(captured, readonly=True)
    try:
        if conn.execute('PRAGMA journal_mode').fetchone() not in (('delete',), ('wal',)):
            return False
        _integrity(conn)
        _identity(conn)
    finally:
        conn.close()
    if (_snapshot_identity(captured) != before or _captured_sha(captured) != digest
            or not _empty_captured_wal(captured)):
        raise WriteProofError('Private encrypted capture changed during verification')
    with captured.open('rb') as handle:
        os.fsync(handle.fileno())
    # Both names are in one newly created private TEMP directory. Link creation
    # is exclusive, so even an unexpected baseline occupant is never replaced.
    # Remove our old name before publishing, retaining one independent main DB
    # with no dependency on captured.db's empty WAL/SHM companions.
    os.link(captured, baseline)
    captured.unlink()
    _regular(baseline)
    sync_dir(baseline.parent)
    return True


@contextmanager
def encrypted_backup(source: Path) -> Iterator[Path]:
    """Verified encrypted DB/WAL snapshot only; no offline research inventory.

    Every SQLite connection remains on private copies. The caller still owns
    authority/closed-app checks, exact publication hashing and recovery binding.
    OfflineWorkspace retains its full semantic proof separately.
    """
    source = Path(source).expanduser().absolute()
    _regular(source)
    if source.is_symlink():
        raise WriteProofError('Source database must not be a symlink')
    with tempfile.TemporaryDirectory(prefix='soundbox-encrypted-backup-') as area:
        root = Path(area).resolve()
        captured, baseline = root / 'captured.db', root / 'baseline.db'
        _copy_stable_snapshot(source.resolve(), captured)
        captured.chmod(0o600)
        wal = captured.with_name(captured.name + '-wal')
        if wal.exists():
            wal.chmod(0o600)
        if _standalone_capture(captured, baseline):
            yield baseline
            return
        _backup(captured, baseline)  # folds WAL, checks SQLite integrity and fsyncs
        conn = _connect(baseline, readonly=True)
        try:
            _identity(conn)
        finally:
            conn.close()
        yield baseline


def _rows(conn, table: str) -> list[dict]:
    cursor = conn.execute(f"SELECT * FROM {_quote(table)}")
    names = [item[0] for item in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor]


def _cell(value):
    return {"blob": value.hex()} if isinstance(value, bytes) else value


def _state(conn) -> dict:
    """Complete logical inventory, including unknown tables, indexes/triggers.

    Intentionally linear in database size for the offline proof. This is not a
    proposed per-keypress production read path and never reads referenced files.
    """
    schema = list(conn.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"))
    tables = {}
    for kind, name, _, _ in schema:
        if kind == "table":
            encoded = sorted(_json([_cell(x) for x in row]) for row in
                             conn.execute(f"SELECT * FROM {_quote(name)}"))
            sha = hashlib.sha256()
            for row in encoded:
                sha.update(len(row).to_bytes(8, "big"))
                sha.update(row)
            tables[name] = sha.hexdigest()
    metadata = {name: conn.execute(f"PRAGMA {name}").fetchone()[0]
                for name in ("user_version", "application_id", "encoding")}
    return {"schema": _digest(schema), "metadata": metadata, "tables": tables}


def _integrity(conn) -> None:
    if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise WriteProofError("Workspace database integrity check failed")


def _identity(conn) -> tuple[str, str]:
    rows = conn.execute("SELECT DBID,DBVersion FROM djmdProperty").fetchall()
    if len(rows) != 1 or not rows[0][0] or rows[0][1] != "6000":
        raise WriteProofError("Unverified rekordbox library identity/version")
    columns = tuple(row[1] for row in conn.execute('PRAGMA table_info("djmdSongMyTag")'))
    if columns != _LINK_COLUMNS:
        raise WriteProofError("Unverified tag-assignment schema")
    for table, required in {
        "djmdContent": {"ID", "UUID", "rb_local_deleted"},
        "djmdMyTag": {"ID", "UUID", "Attribute", "rb_local_deleted"},
        "agentRegistry": {"registry_id", "int_1", "updated_at"},
    }.items():
        actual = {row[1] for row in conn.execute(f"PRAGMA table_info({_quote(table)})")}
        if not required <= actual:
            raise WriteProofError(f"Unverified schema: {table}")
    counter = conn.execute(
        "SELECT int_1 FROM agentRegistry WHERE registry_id='localUpdateCount'").fetchall()
    if len(counter) != 1 or type(counter[0][0]) is not int or counter[0][0] < 0:
        raise WriteProofError("Invalid local update counter")
    return str(rows[0][0]), str(rows[0][1])


def _assignment_state(conn, track_ids: tuple[str, ...], tag_id: str) -> tuple[bool, ...]:
    if (not track_ids or len(track_ids) != len(set(track_ids))
            or any(not isinstance(tid, str) or not tid for tid in track_ids)
            or not isinstance(tag_id, str) or not tag_id):
        raise WriteProofError("Select unique rekordbox track IDs and one existing tag")
    tag = conn.execute(
        "SELECT Attribute,rb_local_deleted FROM djmdMyTag WHERE ID=?", (tag_id,)).fetchone()
    if tag is None or tag[0] != 0 or tag[1] not in (0, None):
        raise WriteProofError("Tag is missing, deleted, or a category")
    present = []
    for tid in track_ids:
        track = conn.execute(
            "SELECT UUID,rb_local_deleted FROM djmdContent WHERE ID=?", (tid,)).fetchone()
        if track is None or not track[0] or track[1] not in (0, None):
            raise WriteProofError("A selected rekordbox track is missing or deleted")
        links = conn.execute(
            "SELECT ID,TrackNo FROM djmdSongMyTag WHERE ContentID=? AND MyTagID=? "
            "AND coalesce(rb_local_deleted,0)=0", (tid, tag_id)).fetchall()
        if len(links) > 1 or any(row[1] is not None for row in links):
            raise WriteProofError("Ambiguous or unverified tag assignment")
        present.append(bool(links))
    return tuple(present)


def _checkpoint(_name: str) -> None:
    """Copy-only failure/crash injection boundary; no production caller."""


class OfflineWorkspace:
    """Explicit copy target. Reopening requires its original private temp root."""

    def __init__(self, root: Path):
        self.root = Path(root).absolute()
        # Never follow an alias into a live tree, even if it has a copied marker.
        if (self.root.is_symlink() or self.root != self.root.resolve()
                or not self.root.name.startswith(_PREFIX)
                or not self.root.is_relative_to(Path(tempfile.gettempdir()).resolve())):
            raise WriteProofError("Writes require an original private temporary workspace")
        _directory(self.root)
        marker = self.root / "workspace.json"
        _regular(marker)
        self.metadata = json.loads(marker.read_bytes())
        if (self.metadata.get("format") != _FORMAT
                or self.metadata.get("root") != str(self.root)):
            raise WriteProofError("Invalid or relocated offline workspace")
        self.database = self.root / "working.db"
        self.transaction = self.root / "transaction"
        self.retired = self.root / "retired"
        self._guard()

    @classmethod
    def capture(cls, source: str | Path) -> OfflineWorkspace:
        """Read/copy an explicit DB, including its WAL, without opening it."""
        source = Path(source).expanduser().absolute()
        _regular(source)
        if source.is_symlink():
            raise WriteProofError("Source database must not be a symlink")
        source = source.resolve()
        root = Path(tempfile.mkdtemp(prefix=_PREFIX)).resolve()
        root.chmod(0o700)
        try:
            return cls._capture_into(source, root)
        except BaseException as exc:
            # A fresh capture has admitted no edits. Remove only regular files
            # created in this newly allocated root, never any source material.
            try:
                for path in root.iterdir():
                    _regular(path)
                    path.unlink()
                root.rmdir()
            except (OSError, WriteProofError) as cleanup_error:
                exc.add_note(f"Incomplete offline capture retained at {root}: {cleanup_error}")
            raise

    @classmethod
    def _capture_into(cls, source: Path, root: Path) -> OfflineWorkspace:
        captured = root / "captured.db"
        _copy_stable_snapshot(source, captured)
        captured.chmod(0o600)
        wal = root / "captured.db-wal"
        if wal.exists():
            wal.chmod(0o600)
        _backup(captured, root / "baseline.db")
        _backup(root / "baseline.db", root / "working.db")
        conn = _connect(root / "baseline.db", readonly=True)
        try:
            identity = _identity(conn)
            baseline = _digest(_state(conn))
        finally:
            conn.close()
        info = source.stat()
        atomic_bytes(root / "workspace.json", _json({
            "format": _FORMAT, "root": str(root), "workspace_id": str(uuid4()),
            "source": str(source), "source_file_id": [info.st_dev, info.st_ino],
            "library_id": identity[0], "db_version": identity[1],
            "baseline_sha256": baseline,
        }))
        # Retain one encrypted baseline, not an extra raw DB/WAL capture.
        for suffix in ("", "-wal", "-shm", "-journal"):
            path = root / ("captured.db" + suffix)
            if path.exists():
                _regular(path)
                path.unlink()
        sync_dir(root)
        return cls(root)

    def _guard(self) -> None:
        _directory(self.root)
        source = Path(self.metadata["source"])
        if self.root.is_relative_to(source.parent):
            raise WriteProofError("Workspace overlaps the source library")
        for name in ("working.db", "baseline.db"):
            path = self.root / name
            _regular(path)
            info = path.stat()
            if [info.st_dev, info.st_ino] == self.metadata["source_file_id"]:
                raise WriteProofError("Workspace aliases its source database")
            for suffix in ("-wal", "-shm", "-journal"):
                sidecar = path.with_name(path.name + suffix)
                if sidecar.exists() or sidecar.is_symlink():
                    _regular(sidecar)
        for folder in (self.transaction, self.retired):
            if folder.exists() or folder.is_symlink():
                _directory(folder)
                for child in folder.iterdir():
                    if child.name not in _TRANSACTION_FILES:
                        raise RecoveryRequired("Unknown file in offline recovery directory")
                    _regular(child)

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self._guard()
        path = self.root / "writer.lock"
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            _regular(path)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise WriteProofError("Offline workspace already has an active writer") from exc
            self._guard()
            yield
        finally:
            os.close(fd)

    def _read_state(self, path: Path) -> dict:
        conn = _connect(path, readonly=True)
        try:
            conn.execute("BEGIN")
            identity = _identity(conn)
            if identity != (self.metadata["library_id"], self.metadata["db_version"]):
                raise ConflictError("Workspace library identity changed")
            _integrity(conn)
            return _state(conn)
        finally:
            conn.close()

    def plan_tag_assignment(self, track_ids: tuple[str, ...], tag_id: str,
                            present: bool) -> TagAssignmentPlan:
        with self._lock():
            if self.transaction.exists() or self.retired.exists():
                raise RecoveryRequired("Recover the offline workspace before planning")
            if type(present) is not bool:
                raise WriteProofError("Tag membership must be a boolean")
            state = self._read_state(self.database)
            conn = _connect(self.database, readonly=True)
            try:
                before = _assignment_state(conn, track_ids, tag_id)
            finally:
                conn.close()
            return TagAssignmentPlan(self.metadata["workspace_id"], _digest(state),
                                     tuple(track_ids), tag_id, present, before)

    def _change_rows(self, conn, plan: TagAssignmentPlan) -> list[tuple[str, str, dict | None, dict]]:
        before = _assignment_state(conn, plan.track_ids, plan.tag_id)
        if before != plan.expected_present:
            raise ConflictError("Expected tag memberships changed")
        now = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("T", " ")
        registry = {row["registry_id"]: row for row in _rows(conn, "agentRegistry")}
        counter = registry["localUpdateCount"]
        next_counter = dict(counter)
        changes: list[tuple[str, str, dict | None, dict]] = []
        links = _rows(conn, "djmdSongMyTag")
        for tid, was_present in zip(plan.track_ids, before, strict=True):
            if was_present == plan.present:
                continue
            next_counter["int_1"] += 1
            if plan.present:
                old = None
                row = dict.fromkeys(_LINK_COLUMNS)
                row.update(ID=str(uuid4()), UUID=str(uuid4()), MyTagID=plan.tag_id,
                           ContentID=tid, rb_data_status=0, rb_local_data_status=0,
                           rb_local_deleted=0, rb_local_synced=0,
                           rb_local_usn=next_counter["int_1"], created_at=now, updated_at=now)
            else:
                old = next(row for row in links if row["ContentID"] == tid
                           and row["MyTagID"] == plan.tag_id and not row["rb_local_deleted"])
                row = {**old, "rb_data_status": 2, "rb_local_deleted": 1,
                       "rb_local_synced": 0, "rb_local_usn": next_counter["int_1"],
                       "updated_at": now}
            changes.append(("djmdSongMyTag", str(row["ID"]), old, row))
        if changes:
            next_counter["updated_at"] = now
            changes.append(("agentRegistry", "localUpdateCount", counter, next_counter))
        return changes

    @staticmethod
    def _apply_rows(conn, changes) -> None:
        for table, key, old, row in changes:
            if old is None:
                names = list(row)
                columns = ",".join(_quote(name) for name in names)
                placeholders = ",".join("?" for _ in names)
                conn.execute(f"INSERT INTO {_quote(table)} ({columns}) VALUES ({placeholders})",
                             tuple(row.values()))
            else:
                names = [name for name in row if row[name] != old[name]]
                assignments = ",".join(f"{_quote(name)}=?" for name in names)
                pk = "ID" if table == "djmdSongMyTag" else "registry_id"
                conn.execute(f"UPDATE {_quote(table)} SET {assignments} WHERE {_quote(pk)}=?",
                             (*[row[name] for name in names], key))

    @staticmethod
    def _verify_delta(before_conn, after_conn, before: dict, after: dict, changes) -> None:
        if (before["schema"] != after["schema"] or before["metadata"] != after["metadata"]
                or before["tables"].keys() != after["tables"].keys()):
            raise WriteProofError("Tag operation changed the database schema")
        for table in before["tables"]:
            if table not in ("djmdSongMyTag", "agentRegistry"):
                if before["tables"][table] != after["tables"][table]:
                    raise WriteProofError(f"Tag operation changed unrelated table: {table}")
                continue
            pk = "ID" if table == "djmdSongMyTag" else "registry_id"
            expected = {row[pk]: row for row in _rows(before_conn, table)}
            for changed_table, key, old, row in changes:
                if changed_table == table:
                    if expected.get(key) != old:
                        raise ConflictError("Tag row baseline changed")
                    expected[key] = row
            actual = {row[pk]: row for row in _rows(after_conn, table)}
            if actual != expected:
                raise WriteProofError(f"Tag operation changed unexpected rows/fields: {table}")

    def _remove_directory(self, path: Path) -> None:
        if path not in (self.transaction, self.retired):
            raise WriteProofError("Cleanup is restricted to this workspace's recovery directories")
        self._guard()
        for child in path.iterdir():
            child.unlink()
        path.rmdir()
        sync_dir(self.root)

    def _retire(self) -> None:
        os.replace(self.transaction, self.retired)
        sync_dir(self.root)
        self._remove_directory(self.retired)

    def apply(self, plan: TagAssignmentPlan) -> TagEditResult:
        """Apply only to working.db. Never publishes to the captured source."""
        with self._lock():
            if self.transaction.exists() or self.retired.exists():
                raise RecoveryRequired("Recover the offline workspace before writing")
            if plan.workspace_id != self.metadata["workspace_id"]:
                raise ConflictError("Plan belongs to a different offline workspace")
            if type(plan.present) is not bool:
                raise WriteProofError("Tag membership must be a boolean")
            before = self._read_state(self.database)
            if _digest(before) != plan.baseline_sha256:
                raise ConflictError("Offline database changed since planning")
            self.transaction.mkdir(mode=0o700)
            conn = None
            journal_written = False
            try:
                # Persist the transaction directory's entry in its parent,
                # not just the files and directory contents below that entry.
                sync_dir(self.root)
                backup = self.transaction / "before.db"
                staged = self.transaction / "after.db"
                _backup(self.database, backup)
                if _digest(self._read_state(backup)) != plan.baseline_sha256:
                    raise ConflictError("Database changed while backing up")
                _checkpoint("backup_ready")
                _backup(backup, staged)
                stage_conn = _connect(staged)
                baseline_conn = _connect(backup, readonly=True)
                try:
                    changes = self._change_rows(stage_conn, plan)
                    if not changes:
                        self._remove_directory(self.transaction)
                        return TagEditResult("", "unchanged", plan.baseline_sha256, 0)
                    stage_conn.execute("BEGIN IMMEDIATE")
                    self._apply_rows(stage_conn, changes)
                    after = _state(stage_conn)
                    self._verify_delta(baseline_conn, stage_conn, before, after, changes)
                    _integrity(stage_conn)
                    stage_conn.commit()
                finally:
                    baseline_conn.close()
                    stage_conn.close()
                after_sha = _digest(self._read_state(staged))
                if after_sha != _digest(after):
                    raise WriteProofError("Staged database did not persist exactly")
                operation_id = str(uuid4())
                journal = {"format": _FORMAT, "workspace_id": plan.workspace_id,
                           "operation_id": operation_id, "before": plan.baseline_sha256,
                           "after": after_sha, "changed_tracks": len(changes) - 1}
                atomic_bytes(self.transaction / "journal.json", _json(journal))
                journal_written = True
                _checkpoint("journal_ready")
                conn = _connect(self.database)
                conn.execute("BEGIN IMMEDIATE")
                if _digest(_state(conn)) != plan.baseline_sha256:
                    raise ConflictError("Database changed before commit")
                self._apply_rows(conn, changes)
                if _digest(_state(conn)) != after_sha:
                    raise WriteProofError("Working changes differ from the verified stage")
                _integrity(conn)
                _checkpoint("before_commit")
                conn.commit()
                _checkpoint("after_commit")
                conn.close()
                conn = None
                if _digest(self._read_state(self.database)) != after_sha:
                    raise RecoveryRequired("Committed database needs recovery inspection")
                _checkpoint("verified")
                atomic_bytes(self.root / "last-result.json", _json(journal))
                self._retire()
                return TagEditResult(operation_id, "changed", after_sha, len(changes) - 1)
            except BaseException:
                if conn is not None:
                    try:
                        conn.rollback()
                    finally:
                        conn.close()
                # A durable journal is retained even for a definite rollback;
                # recovery proves the outcome instead of guessing from exceptions.
                if not journal_written and self.transaction.exists():
                    self._remove_directory(self.transaction)
                raise

    def recover(self) -> TagEditResult | None:
        """Reconcile copies only. Unexpected state is never auto-restored."""
        with self._lock():
            if self.retired.exists():
                self._remove_directory(self.retired)
            if not self.transaction.exists():
                return None
            journal_path = self.transaction / "journal.json"
            if not journal_path.exists():
                # No mutation is admitted until this record has been fsynced.
                self._remove_directory(self.transaction)
                return None
            journal = json.loads(journal_path.read_bytes())
            if (journal.get("format") != _FORMAT
                    or journal.get("workspace_id") != self.metadata["workspace_id"]):
                raise RecoveryRequired("Recovery journal identity does not match")
            for name, expected in (("before.db", journal["before"]),
                                   ("after.db", journal["after"])):
                if _digest(self._read_state(self.transaction / name)) != expected:
                    raise RecoveryRequired("Offline recovery evidence is damaged")
            # A process can exit after dirty pages spill to the main DB. SQLite
            # then needs a writable connection to roll back its hot journal.
            # This is strictly the guarded temporary copy, never the source.
            conn = _connect(self.database)
            conn.close()
            current = _digest(self._read_state(self.database))
            if current == journal["after"]:
                outcome: Literal["recovered-commit", "recovered-rollback"] = "recovered-commit"
                atomic_bytes(self.root / "last-result.json", _json(journal))
            elif current == journal["before"]:
                outcome = "recovered-rollback"
            else:
                raise RecoveryRequired("Database differs from both recovery baselines; preserved")
            self._retire()
            return TagEditResult(journal["operation_id"], outcome, current,
                                 journal["changed_tracks"] if outcome == "recovered-commit" else 0)
