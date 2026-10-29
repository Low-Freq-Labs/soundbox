"""Recoverable publication of one authorized, dual-format CDJ generation.

Only exact candidate paths and previously owned paths can change. Original
metadata is retained in the transaction; unowned media is never replaced or
removed. Manifest publication is last. A mount identity is not portable authority.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import json
import io
import errno
import secrets
import tempfile
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import unicodedata
import uuid

from .cdj_usb import DATABASE_FILES, PROFILE_FILES, CdjReadSession, assert_no_database_journals, _identity
from .sync import DeviceSession

MANIFEST = '.soundbox-cdj/manifest.json'
JOURNAL = '.soundbox-cdj/pending.json'


def _key(path):
    return unicodedata.normalize('NFD', path).casefold()


def _relative(path):
    parts = PurePosixPath(path).parts
    if not parts or any(p in ('/', '.', '..') or '\\' in p or ':' in p for p in parts):
        raise ValueError('Invalid CDJ relative path')
    allowed = (path in DATABASE_FILES or path in ('PIONEER/' + n for n in PROFILE_FILES)
               or path.startswith(('Contents/', 'PIONEER/USBANLZ/', 'PIONEER/Artwork/')))
    if not allowed:
        raise ValueError(f'Unrecognized CDJ destination: {path}')
    return path


class CdjSession(DeviceSession):
    def __init__(self, target, sources, *, token, volume_uuid):
        if not volume_uuid:
            raise ValueError('A verified USB volume identity is required')
        super().__init__(Path(target), list(sources), create=False, allow_external_roots=True)
        if self.mount_identity[0] != token:
            raise ValueError('USB was remounted; prepare the export again')
        self.volume_uuid = volume_uuid
        self._directory_cache = {}
        for marker in ('iPod_Control', '.rockbox', '.PIONEER'):
            if (self.target/marker).exists() or (self.target/marker).is_symlink():
                raise ValueError('CDJ export requires a generic USB, without iPod/Rockbox/hidden-library layouts')

    def guard(self, path):
        candidate = super().guard(path)
        relative = candidate.relative_to(self.target)
        # Resolve case/normalization ambiguities on FAT and reject all links,
        # including links that still resolve inside this USB.
        parent = self.target
        for component in relative.parts:
            if parent.exists():
                info = parent.stat()
                identity = (info.st_dev,info.st_ino,info.st_mtime_ns,info.st_ctime_ns)
                cached = self._directory_cache.get(parent)
                if cached is None or cached[0] != identity:
                    all_names: dict[str,list[str]] = {}
                    for entry in parent.iterdir():
                        all_names.setdefault(_key(entry.name),[]).append(entry.name)
                    cached = (identity,all_names)
                    self._directory_cache[parent] = cached
                names = cached[1].get(_key(component),[])
                if names and names != [component]:
                    raise ValueError('Ambiguous FAT spelling in CDJ path')
            child = parent/component
            if child.is_symlink():
                raise ValueError('Linked CDJ paths are not writable')
            if child.exists() and child.stat().st_dev != self.mount_identity[0]:
                raise ValueError('CDJ path crosses the USB mount')
            parent = child
        return candidate

    @contextmanager
    def parent_fd(self, path, *, create=False):
        relative = self.guard(path).relative_to(self.target)
        fd = os.open(self.target, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY)
        try:
            info = os.fstat(fd)
            if (info.st_dev, info.st_ino) != self.mount_identity:
                raise ValueError('USB root changed before publication')
            for component in relative.parts[:-1]:
                if create:
                    try:
                        os.mkdir(component, dir_fd=fd)
                    except FileExistsError:
                        pass
                child = os.open(component, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY, dir_fd=fd)
                os.close(fd)
                fd = child
                if os.fstat(fd).st_dev != self.mount_identity[0]:
                    raise ValueError('CDJ publication crosses the USB boundary')
            self.revalidate()
            yield fd, relative.name
        finally:
            os.close(fd)

    @staticmethod
    def _flush_directory(fd):
        try:
            os.fsync(fd)
        except OSError as exc:
            if exc.errno != errno.EINVAL:
                raise

    def _publish(self, stream, destination, should_cancel=lambda:False, expected_sha256=None):
        import hashlib
        copied=hashlib.sha256() if expected_sha256 else None
        with self.parent_fd(destination, create=True) as (fd, name):
            temporary = '.soundbox-'+secrets.token_hex(12)+'.sbpart'
            handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            try:
                with os.fdopen(handle, 'wb') as output:
                    while block := stream.read(1024*1024):
                        if should_cancel():
                            raise RuntimeError('CDJ publication cancelled')
                        output.write(block)
                        if copied is not None:copied.update(block)
                    output.flush()
                    os.fsync(output.fileno())
                if copied is not None and copied.hexdigest()!=expected_sha256:
                    raise ValueError('Candidate changed while copying')
                self.revalidate()
                os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
                self._flush_directory(fd)
            finally:
                try:
                    os.unlink(temporary, dir_fd=fd)
                except FileNotFoundError:
                    pass

    def atomic_write(self, destination, data):
        self._publish(io.BytesIO(data), destination)

    def atomic_copy(self, source, destination, *, should_cancel=lambda:False, expected_sha256=None):
        with Path(source).open('rb') as stream:
            self._publish(stream, destination, should_cancel,expected_sha256)

    def rename(self, source, destination):
        with self.parent_fd(source) as (source_fd, source_name):
            with self.parent_fd(destination, create=True) as (target_fd, target_name):
                self.revalidate()
                os.replace(source_name, target_name, src_dir_fd=source_fd, dst_dir_fd=target_fd)
                self._flush_directory(source_fd)
                self._flush_directory(target_fd)

    def unlink(self, path, *, missing_ok=False):
        with self.parent_fd(path) as (fd, name):
            self.revalidate()
            try:
                os.unlink(name, dir_fd=fd)
            except FileNotFoundError:
                if not missing_ok:
                    raise
            self._flush_directory(fd)

    def read_optional(self, path, *, limit=64*1024*1024):
        self.guard(self.target/path)
        reader = CdjReadSession(self.target, self.mount_identity[0])
        try:
            try:
                return reader.read(path, limit=limit)
            except FileNotFoundError:
                return None
        finally:
            reader.close()


def load_manifest(session):
    raw = session.read_optional(MANIFEST, limit=8*1024*1024)
    if raw is None:
        return None
    value = json.loads(raw)
    if (value.get('schema') != 1 or value.get('volume_uuid') != session.volume_uuid
            or not isinstance(value.get('files'), dict) or not isinstance(value.get('selection'), list)):
        raise ValueError('Unsupported or mismatched CDJ manifest')
    seen = set()
    for path, contract in value['files'].items():
        _relative(path)
        key = _key(path)
        if key in seen or not isinstance(contract.get('owned'), bool) or not isinstance(contract.get('size'), int) or len(contract.get('sha256', '')) != 64:
            raise ValueError('Invalid CDJ ownership inventory')
        seen.add(key)
    return value


@dataclass(frozen=True)
class Change:
    path: str
    old_sha256: str | None
    new_sha256: str | None
    size: int = 0


@dataclass
class CdjPlan:
    target: Path
    mount_identity: tuple[int, int]
    volume_uuid: str
    candidate: Path
    manifest: dict
    changes: tuple[Change, ...]
    old_manifest: bytes | None
    replaces_native_library: bool
    tracks: int
    playlists: int
    workspace: tempfile.TemporaryDirectory | None = field(default=None, repr=False)
    recovery_root: Path | None = None

    @property
    def requires_commit(self):
        if self.changes:return True
        if self.old_manifest is None:return bool(self.manifest.get('selection'))
        before=json.loads(self.old_manifest)
        def normalized(value):
            result={k:v for k,v in value.items() if k!='generation'}
            for key in ('foreign_tracks','foreign_playlists'):result.setdefault(key,[])
            return result
        return normalized(before)!=normalized(self.manifest)

    def close(self):
        if self.workspace is not None:
            self.workspace.cleanup()
            self.workspace = None


def _hash_file(path, cancel=lambda: False):
    import hashlib
    digest_value = hashlib.sha256()
    with path.open('rb') as stream:
        while block := stream.read(1024*1024):
            if cancel():
                raise RuntimeError('CDJ operation cancelled')
            digest_value.update(block)
    return digest_value.hexdigest()


def make_plan(session, candidate, *, selection, db_id, view, workspace=None, should_cancel=lambda:False, settings_only=False, recovery_root=None, allowed_profile_changes=(), retained_files=(), source_contracts=None, catalog_baseline=None, allowed_asset_changes=()):
    if session.read_optional(JOURNAL, limit=8*1024*1024) is not None:
        raise ValueError('Interrupted CDJ transaction requires recovery before another export')
    previous = load_manifest(session)
    if catalog_baseline is not None:
        original = previous['files'] if previous else {}
        if any(contract != original.get(path) for path,contract in catalog_baseline['files'].items() if path not in DATABASE_FILES):
            raise ValueError('Catalog refresh cannot change media ownership')
        previous = catalog_baseline
    previous_files = previous['files'] if previous else {}
    old_manifest = session.read_optional(MANIFEST, limit=8*1024*1024)
    candidate = Path(candidate)
    retained_files = set(retained_files)
    if any(path not in previous_files or path in DATABASE_FILES for path in retained_files):
        raise ValueError('Retained CDJ inventory has no matching committed owner')
    # Normal export trusts unchanged committed inventory. Verify/Deep Verify
    # audit retained files explicitly; an addition does not walk their paths.
    inventory = {path: dict(previous_files[path]) for path in retained_files}
    changes, seen = [], {_key(path) for path in retained_files}
    replaces = False
    source_contracts = source_contracts or {}
    if any(not p.startswith('PIONEER/USBANLZ/') or PurePosixPath(p).suffix not in ('.DAT','.EXT','.2EX') for p in allowed_asset_changes):
        raise ValueError('Only selected native analysis may change through catalog adoption')
    reuse_proof = reusable_proof(session,recovery_root)
    reusable = reuse_proof.get('files',{})
    # Never let a partial native candidate reach publication.
    if not settings_only and any(not (candidate/p).is_file() for p in DATABASE_FILES):
        raise ValueError('Both native library formats are required')
    for local in sorted(candidate.rglob('*')):
        if should_cancel():
            raise RuntimeError('CDJ preparation cancelled')
        if local.is_symlink():
            raise ValueError('Candidate contains a linked path')
        if not local.is_file():
            continue
        path = _relative(local.relative_to(candidate).as_posix())
        if _key(path) in seen:
            raise ValueError('Candidate has a FAT filename collision')
        seen.add(_key(path))
        session.guard(session.target/path)
        new_hash, size = _hash_file(local, should_cancel), local.stat().st_size
        if path in source_contracts and (new_hash,size)!=(source_contracts[path]['sha256'],source_contracts[path]['size']):
            raise ValueError('Generated CDJ audio differs from captured source')
        destination = session.target/path
        owner = previous_files.get(path)
        old = None
        if destination.exists():
            info = destination.lstat()
            if not stat.S_ISREG(info.st_mode):
                raise ValueError('CDJ destination is not a regular file')
            # Hash the affected destination before approval; never infer ownership
            # from a matching name, source path, or browser display record.
            if (owner and owner['owned'] and path not in DATABASE_FILES
                    and path not in ('PIONEER/'+n for n in PROFILE_FILES)
                    and owner['sha256'] == new_hash and info.st_size == owner['size']):
                old = owner['sha256']
            elif reusable.get(path)==new_hash and reuse_proof.get('observations',{}).get(path)==list(_identity(info)):
                old=new_hash
            else:
                old = _hash_file(destination, should_cancel)
        profile_path = path in ('PIONEER/' + n for n in PROFILE_FILES)
        if profile_path and old is not None and old != new_hash and path not in allowed_profile_changes:
            raise ValueError('USB settings changed during preparation; retry to preserve the current profile')
        if owner and old != owner['sha256'] and not profile_path and not settings_only and path not in allowed_asset_changes:
            raise ValueError(f'Owned CDJ file changed outside SoundBox: {path}')
        owned = bool(owner and owner['owned']) or old is None or path in DATABASE_FILES or reusable.get(path)==new_hash==old
        if old is not None and old != new_hash:
            if (not owner or not owner['owned']) and path not in DATABASE_FILES and path not in allowed_profile_changes and path not in allowed_asset_changes:
                raise ValueError(f'Unowned CDJ file conflicts with the export: {path}')
            if path in DATABASE_FILES and (previous is None or not previous.get('library_managed', True)):
                replaces = True
        inventory[path] = dict(size=size, sha256=new_hash, owned=owned)
        if old != new_hash:
            changes.append(Change(path, old, new_hash, size))
    for path, owner in previous_files.items():
        if path in inventory:
            continue
        if settings_only or not owner['owned']:
            inventory[path] = owner
            continue
        # Removed managed media participates in this same reviewed rollback set.
        destination = session.guard(session.target/path)
        if not destination.is_file() or _hash_file(destination, should_cancel) != owner['sha256']:
            raise ValueError(f'Removed owned CDJ file changed: {path}')
        changes.append(Change(path, owner['sha256'], None))
    if not source_contracts.keys() <= inventory.keys():
        raise ValueError('Captured CDJ audio is missing from the candidate')
    required = sum(c.size for c in changes if c.new_sha256)
    free = shutil.disk_usage(session.target).free
    if required + 16*1024*1024 > free:
        raise ValueError('Insufficient USB free space for the recoverable generation')
    manifest = dict(schema=1, volume_uuid=session.volume_uuid, generation=uuid.uuid4().hex,
                    db_id=db_id, selection=selection, files=inventory, view=view,
                    library_managed=(previous.get('library_managed', True) if previous else False) if settings_only else True)
    return CdjPlan(session.target, session.mount_identity, session.volume_uuid, candidate,
                   manifest, tuple(changes), old_manifest, replaces, int(view['tracks']),
                   len([p for p in view['playlists'] if not p['folder']]), workspace, recovery_root)


def _json(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'))+'\n').encode()


def _check_baseline(session, change):
    path = session.guard(session.target/change.path)
    if change.old_sha256 is None:
        if path.exists():
            raise ValueError('USB changed after approval; a new destination is occupied')
    elif not path.is_file() or _hash_file(path) != change.old_sha256:
        raise ValueError('USB changed after approval; prepare the export again')


def commit(plan, *, should_cancel=lambda:False, on_progress=lambda *_:None):
    """Publish a prepared plan in its owned lane, authorized by a drop or review."""
    session = CdjSession(plan.target, [], token=plan.mount_identity[0], volume_uuid=plan.volume_uuid)
    if session.mount_identity != plan.mount_identity:
        raise ValueError('USB identity changed after approval')
    if session.read_optional(JOURNAL, limit=8*1024*1024) is not None:
        raise ValueError('An interrupted generation is awaiting recovery')
    if session.read_optional(MANIFEST, limit=8*1024*1024) != plan.old_manifest:
        raise ValueError('USB manifest changed after approval')
    assert_no_database_journals(session.target,session.mount_identity[0])
    # Entire target/candidate contract is checked before the first write.
    for change in plan.changes:
        if should_cancel():
            raise RuntimeError('CDJ export cancelled before publication')
        _check_baseline(session, change)
        # Small metadata is checked before publication; new audio is hashed in
        # its one copy pass, rather than read twice locally and again from USB.
        if change.new_sha256 and not change.path.startswith('Contents/') and _hash_file(plan.candidate/change.path, should_cancel) != change.new_sha256:
            raise ValueError('Candidate changed after approval')
    if not plan.requires_commit:
        return plan.manifest
    for path in DATABASE_FILES:
        if path in plan.manifest['files'] and not any(c.path==path for c in plan.changes):
            if _hash_file(session.guard(session.target/path))!=plan.manifest['files'][path]['sha256']:
                raise ValueError('USB library changed during preparation; retry the update')
    transaction = '.soundbox-cdj/transactions/'+plan.manifest['generation']
    journal = dict(schema=1, volume_uuid=plan.volume_uuid, mount_identity=list(plan.mount_identity),
                   transaction=transaction, state='executing',
                   old_manifest=plan.old_manifest.decode() if plan.old_manifest else None,
                   new_manifest=plan.manifest,
                   changes=[dict(path=c.path, old=c.old_sha256, new=c.new_sha256) for c in plan.changes])
    if plan.recovery_root is None:
        raise ValueError('A local CDJ recovery receipt directory is required')
    receipt = _receipt_path(plan.recovery_root, session.volume_uuid, plan.manifest['generation'])
    receipt.parent.mkdir(parents=True, exist_ok=True)
    with receipt.open('xb') as output:
        output.write(_json({k:v for k,v in journal.items() if k != 'state'}))
        output.flush()
        os.fsync(output.fileno())
    directory_fd = os.open(receipt.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    session.atomic_write(session.target/JOURNAL, _json(journal))
    completed={}
    try:
        # Retire each original immediately before its replacement. The durable
        # intent covers the whole set: original bytes remain live or in recovery
        # at every interruption boundary.
        for index, change in enumerate(plan.changes):
            if should_cancel():
                raise RuntimeError('CDJ export cancelled; restoring previous generation')
            if change.path in DATABASE_FILES:
                assert_no_database_journals(session.target,session.mount_identity[0])
            _check_baseline(session, change)
            path = session.guard(session.target/change.path)
            if change.old_sha256:
                backup = session.guard(session.target/transaction/change.path)
                session.rename(path, backup)
            if change.new_sha256:
                session.atomic_copy(plan.candidate/change.path, path, should_cancel=should_cancel,expected_sha256=change.new_sha256)
                if change.old_sha256 is None and change.path not in DATABASE_FILES and change.path not in {'PIONEER/'+name for name in PROFILE_FILES}:
                    completed[change.path]=dict(sha256=change.new_sha256,observation=list(_identity(path.stat())))
                    from .state_io import atomic_bytes
                    atomic_bytes(receipt.with_suffix('.completed.json'),_json(dict(schema=1,
                        volume_uuid=session.volume_uuid,generation=plan.manifest['generation'],files=completed)))
            on_progress(index+1, len(plan.changes), change.path)
        session.atomic_write(session.target/MANIFEST, _json(plan.manifest))
        journal['state'] = 'committed'
        session.atomic_write(session.target/JOURNAL, _json(journal))
    except Exception:
        recover(session, plan.recovery_root, retain_new=True)
        raise
    # Keep exact recovery material; no automatic bulk deletion of old media.
    session.unlink(session.target/JOURNAL)
    return plan.manifest


def _receipt_path(root, volume_uuid, generation):
    uuid.UUID(volume_uuid)
    if len(generation) != 32 or any(c not in '0123456789abcdef' for c in generation):
        raise ValueError('Invalid CDJ generation identity')
    requested = Path(root).absolute()
    if any(p.is_symlink() for p in (requested,*requested.parents)):
        raise ValueError('Local recovery paths cannot be linked')
    root = requested.resolve(strict=False)
    return root/volume_uuid/(generation+'.json')


def recover(session, recovery_root, *, dry_run=False, retain_new=False):
    assert_no_database_journals(session.target,session.mount_identity[0])
    raw = session.read_optional(JOURNAL, limit=8*1024*1024)
    if raw is None:
        return
    value = json.loads(raw)
    if (value.get('schema') != 1 or value.get('volume_uuid') != session.volume_uuid
            or value.get('state') not in ('executing', 'committed')):
        raise ValueError('Unknown CDJ recovery state; preserve the USB unchanged')
    if recovery_root is None:
        raise ValueError('Local CDJ recovery authority is unavailable')
    receipt = _receipt_path(recovery_root, session.volume_uuid, value['new_manifest']['generation'])
    expected = json.loads(receipt.read_bytes())
    if expected != {k:v for k,v in value.items() if k != 'state'}:
        raise ValueError('USB journal does not match its locally approved recovery receipt')
    completed={}
    try:
        record=json.loads(receipt.with_suffix('.completed.json').read_bytes())
        if (record.get('schema')==1 and record.get('volume_uuid')==session.volume_uuid
                and record.get('generation')==value['new_manifest']['generation']):completed=record.get('files',{})
    except (OSError,ValueError,TypeError):pass
    transaction = value.get('transaction', '')
    if (not transaction.startswith('.soundbox-cdj/transactions/')
            or len(PurePosixPath(transaction).parts) != 3
            or len(PurePosixPath(transaction).name) != 32):
        raise ValueError('Invalid CDJ recovery path')
    # A later native export supersedes this interrupted generation. Inspect
    # only the small current catalogs; never roll them back over the new export
    # or audit its songs. Leave all media and saved metadata untouched.
    newer=False
    for change in value['changes']:
        if change['path'] in DATABASE_FILES:
            current=session.read_optional(change['path'])
            if current is not None and __import__('hashlib').sha256(current).hexdigest() not in (change['old'],change['new']):newer=True
    if newer:
        from .cdj_usb import read_library
        native=read_library(session.target,session.mount_identity[0],allow_pending=True,check_pair=True)
        if native is None:raise ValueError('Finish the native export before updating this USB')
        if dry_run:return dict(state='superseded',files=0,journal_sha256=__import__('hashlib').sha256(raw).hexdigest(),mount_identity=session.mount_identity)
        current=session.read_optional(MANIFEST,limit=8*1024*1024)
        proof=dict(schema=1,volume_uuid=session.volume_uuid,generation=value['new_manifest']['generation'],
            old_manifest=current.decode() if current else None,files={},observations={},
            selection=value['new_manifest']['selection'],db_id=value['new_manifest']['db_id'])
        from .state_io import atomic_bytes
        atomic_bytes(receipt.with_suffix('.reusable.json'),_json(proof))
        session.atomic_write(session.target/'.soundbox-cdj/reusable.json',_json(proof))
        session.unlink(session.target/JOURNAL)
        return
    # Validate the whole recovery set before changing any member.
    actions = []
    for change in value['changes']:
        path = session.guard(session.target/_relative(change['path']))
        backup = session.guard(session.target/transaction/change['path'])
        proof=completed.get(change['path'],{})
        if path.is_file() and proof.get('sha256')==change['new'] and proof.get('observation')==list(_identity(path.stat())):
            live=change['new']
        else:live = _hash_file(path) if path.is_file() else None
        saved = _hash_file(backup) if backup.is_file() else None
        if saved not in (None, change['old']) or live not in (None, change['old'], change['new']):
            raise ValueError('Ambiguous CDJ recovery bytes; preserve all evidence')
        if change['old'] and saved is None and live != change['old']:
            raise ValueError('Original CDJ recovery bytes are missing')
        if value['state'] == 'committed' and live != change['new']:
            raise ValueError('Committed CDJ files do not match their journal')
        actions.append((change, path, backup, saved, live))
    if dry_run:
        return dict(state=value['state'], files=len(actions), journal_sha256=__import__('hashlib').sha256(raw).hexdigest(),
                    mount_identity=session.mount_identity)
    if value['state'] == 'committed':
        current = session.read_optional(MANIFEST, limit=8*1024*1024)
        if current != _json(value['new_manifest']):
            raise ValueError('Committed CDJ manifest does not match its journal')
    else:
        reusable={};observations={}
        for change, path, backup, saved, live in reversed(actions):
            if saved:
                session.rename(backup, path)
            elif change['old'] is None and live == change['new']:
                if retain_new and change['path'] not in DATABASE_FILES and change['path'] not in {'PIONEER/'+name for name in PROFILE_FILES}:
                    reusable[change['path']]=live
                    observations[change['path']]=list(_identity(path.stat()))
                else:session.unlink(path)
        old = value['old_manifest']
        if old is None:
            session.unlink(session.target/MANIFEST, missing_ok=True)
        else:
            session.atomic_write(session.target/MANIFEST, old.encode())
        if retain_new:
            proof=dict(schema=1,volume_uuid=session.volume_uuid,generation=value['new_manifest']['generation'],
                old_manifest=old,files=reusable,observations=observations,
                selection=value['new_manifest']['selection'],db_id=value['new_manifest']['db_id'])
            # A USB file cannot grant ownership. The matching local record is
            # written only after the complete journal has been reconciled.
            local=receipt.with_suffix('.reusable.json')
            from .state_io import atomic_bytes
            atomic_bytes(local,_json(proof))
            session.atomic_write(session.target/'.soundbox-cdj/reusable.json',_json(proof))
    session.unlink(session.target/JOURNAL)


def reusable_proof(session,recovery_root):
    raw=session.read_optional('.soundbox-cdj/reusable.json',limit=8*1024*1024)
    if raw is None or recovery_root is None:return {}
    try:
        proof=json.loads(raw)
        if proof.get('schema')!=1 or proof.get('volume_uuid')!=session.volume_uuid:return {}
        receipt=_receipt_path(recovery_root,session.volume_uuid,proof['generation'])
        if json.loads(receipt.with_suffix('.reusable.json').read_bytes())!=proof:return {}
        old=session.read_optional(MANIFEST,limit=8*1024*1024)
        if proof['old_manifest']!=(old.decode() if old else None):return {}
        return dict(proof)
    except (OSError,ValueError,KeyError,TypeError):return {}
