"""Recoverable audio replacement, with separately authorized locator updates.

Actual headers are authoritative technical facts, as in rekordbox's decoder.
Musical rows, cached native technical columns, XML and analysis remain untouched.
One common desktop source owner/lock/journal serializes each atomic replacement.
"""
from dataclasses import asdict,dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
from uuid import UUID

from . import cdj_compatibility as compatibility
from .analysis import identity
from .rekordbox_analysis_store import shared_root
from .rekordbox_cue_store import CueWriteAuthority,PilotCueStore
from .rekordbox_tags import NATIVE_VERSION,_schema
from .rekordbox_write import (_regular,_json,_connect,_identity,_rows,encrypted_backup,
                             WriteProofError,ConflictError,RecoveryRequired)
from .source_acquisition import SourceContract,acquire_source
from .state_io import atomic_bytes,sync_dir

FORMAT='soundbox-native-media-conversion-v1'
AUTHORITY_FORMAT='soundbox-media-conversion-authority-v1'


@dataclass(frozen=True)
class ConversionAuthority:
    database:str
    library_id:str
    schema_sha256:str
    accepted_reference_sha256:str
    accepted:bool
    application_version:str=NATIVE_VERSION

    def __post_init__(self):
        CueWriteAuthority(self.database,self.library_id,self.schema_sha256,'media-conversion',self.application_version)
        if (self.accepted is not True or not isinstance(self.accepted_reference_sha256,str)
                or len(self.accepted_reference_sha256)!=64
                or any(v not in '0123456789abcdef' for v in self.accepted_reference_sha256)):
            raise WriteProofError('Existing media conversion requires separate native acceptance')


def load_authority(path,source,library_id):
    try:
        _regular(Path(path));value=json.loads(Path(path).read_bytes())
        if value.pop('format',None)!=AUTHORITY_FORMAT:return None
        result=ConversionAuthority(**value)
        return result if (result.database,result.library_id)==(str(source),library_id) else None
    except (OSError,ValueError,TypeError,WriteProofError):return None


def _sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def _audio(path):
    s=Path(path).lstat()
    if (not stat.S_ISREG(s.st_mode) or s.st_nlink!=1 or s.st_uid!=os.getuid()
            or Path(path).resolve(strict=True)!=path):
        raise WriteProofError('Conversion requires one canonical, user-owned regular audio file')
    return s


def _current_audio(path):
    """Bind one existing digest pass to the exact stable source inode/epoch."""
    before = identity(_audio(path))
    with Path(path).open('rb') as stream:
        opened = identity(os.fstat(stream.fileno()))
        digest = hashlib.file_digest(stream,'sha256').hexdigest()
        closed = identity(os.fstat(stream.fileno()))
    if not before == opened == closed == identity(_audio(path)):
        raise RecoveryRequired('Audio changed during conversion verification; evidence retained')
    return digest, before


def _require_audio_epoch(path, expected):
    if identity(_audio(path)) != expected:
        raise RecoveryRequired('Audio changed before conversion publication; evidence retained')


def _fault(_phase):pass


class _Lease(PilotCueStore):
    def __init__(self,store):
        self.store=store;a=store.authority
        super().__init__(CueWriteAuthority(a.database,a.library_id,a.schema_sha256,'media-conversion',a.application_version),store.root)
    def _record(self):return self.store._record()
    def _guard(self):
        super()._guard();s=self.database.stat()
        if (s.st_dev,s.st_ino)!=self.store.file_id:raise ConflictError('Media conversion source was replaced')


class ConversionStore:
    def __init__(self,authority,data_root):
        if not isinstance(authority,ConversionAuthority):raise WriteProofError('Explicit conversion authority required')
        self.authority=authority;self.database=Path(authority.database);self.data_root=Path(data_root).absolute()
        if (self.data_root.resolve()!=self.data_root or self.data_root.is_relative_to(self.database.parent)
                or self.database.is_relative_to(self.data_root)):raise WriteProofError('Conversion recovery overlaps native data')
        s=self.database.stat();self.file_id=(s.st_dev,s.st_ino)
        self.root=shared_root(self.data_root,self.database,authority.library_id);self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.journal=self.root/'transaction.json';self.lease=_Lease(self)

    def _rows(self):
        with encrypted_backup(self.database) as captured:
            conn=_connect(captured,readonly=True)
            try:
                if _identity(conn)!=(self.authority.library_id,'6000') or _schema(conn)!=self.authority.schema_sha256:
                    raise WriteProofError('Conversion library/schema changed')
                return [r for r in _rows(conn,'djmdContent') if not r['rb_local_deleted'] and r.get('FolderPath')]
            finally:conn.close()

    def _bindings(self,path,expected,*,wave_only=True):
        rows=self._rows();by_id={r['ID']:r for r in rows}
        for ref in expected:
            row=by_id.get(ref['id'])
            if (not row or row['UUID']!=ref['uuid'] or wave_only and row['FileType']!=11
                    or Path(row['FolderPath']).resolve(strict=False)!=path):
                raise ConflictError('Conversion song identity or native file path changed')
        # Shared aliases keep all of their native records. No new path/content
        # publication is implied by an audio-format replacement at this path.
        return rows

    def _codec_bindings(self,path,expected):
        if not expected:self._unregistered(path);return
        from .rekordbox_locator_store import load_authority
        authority=load_authority(self.data_root/'rekordbox-media-locator-authority.json',self.database,self.authority.library_id)
        if authority is None or authority.track_ids and any(r['id'] not in authority.track_ids for r in expected):
            raise WriteProofError('Codec replacement has no matching locator authority')
        rows=self._bindings(path,expected,wave_only=False)
        # Resolve each freshly captured path once for both alias and destination
        # checks. This observation never outlives this binding check.
        paths={r['ID']:Path(r['FolderPath']).resolve(strict=False) for r in rows}
        matched={identity for identity,canonical in paths.items() if canonical==path}
        if matched!={r['id'] for r in expected}:raise ConflictError('All native references to this audio must move together')
        for row in rows:
            if row['ID'] in matched and (row.get('usn') is not None or row.get('rb_local_synced') not in (0,1)):
                raise WriteProofError('This native sync state cannot be relocated; original audio retained')
            if row['ID'] not in matched and paths[row['ID']]==path.with_suffix('.wav'):
                raise ConflictError('The WAVE destination belongs to another native track; original audio retained')

    def _unregistered(self,path):
        if any(Path(r['FolderPath']).resolve(strict=False)==path for r in self._rows()):
            raise WriteProofError('Registered codec conversion requires its accepted native relocation profile')

    def _folder(self,operation_id):
        if str(UUID(operation_id))!=operation_id:raise WriteProofError('Invalid conversion operation ID')
        return self.data_root/'media-conversion-originals'/operation_id

    def _load(self,folder,expected_sha):
        folder=Path(folder)
        if folder!=self._folder(folder.name) or folder.resolve()!=folder:raise RecoveryRequired('Conversion recovery redirects')
        path=folder/'plan.json';_regular(path)
        if _sha(path)!=expected_sha:raise RecoveryRequired('Conversion plan changed')
        plan=json.loads(path.read_bytes())
        if (plan.get('format')!=FORMAT or plan.get('authority')!=asdict(self.authority)
                or plan.get('source_file_id')!=list(self.file_id) or plan.get('operation_id')!=folder.name):
            raise RecoveryRequired('Conversion recovery authority changed')
        source=Path(plan['path']);target=Path(plan['target']);stage=Path(plan['stage'])
        if (source.suffix.lower() not in ('.wav','.aif','.aiff','.flac','.m4a','.mp3','.aac') or source.is_relative_to('/Volumes')
                or source.is_relative_to(self.data_root) or source.is_relative_to(self.database.parent)
                or target!=(source if source.suffix.lower()=='.wav' else source.with_suffix('.wav')) or stage.parent!=source.parent
                or stage.name!='.soundbox-media-'+folder.name+'.wav'):
            raise RecoveryRequired('Conversion recovery escaped its selected audio')
        original=folder/('original'+source.suffix.lower());_regular(original)
        if _sha(original)!=plan['before_sha256']:raise RecoveryRequired('Original conversion backup changed')
        return plan

    def _record(self):
        _regular(self.journal);record=json.loads(self.journal.read_bytes())
        if (set(record)!={'format','source','library_id','source_file_id','folder','plan_sha256'}
                or record['format']!=FORMAT or record['source']!=str(self.database)
                or record['library_id']!=self.authority.library_id or record['source_file_id']!=list(self.file_id)):
            raise RecoveryRequired('Unknown media conversion journal; evidence retained')
        return record,self._load(record['folder'],record['plan_sha256'])

    def _recover_locked(self):
        if not self.journal.exists():return None
        record,plan=self._record();path=Path(plan['path']);target=Path(plan['target']);stage=Path(plan['stage']);folder=Path(record['folder'])
        original=folder/('original'+path.suffix.lower())
        if path==target:
            current,current_epoch=_current_audio(path)
            if current==plan['before_sha256']:
                self._bindings(path,plan['native']);_audio(stage)
                if _sha(stage)!=plan['after_sha256']:raise RecoveryRequired('Prepared conversion bytes changed')
                self.lease._guard();_require_audio_epoch(path,current_epoch)
                os.replace(stage,path);sync_dir(path.parent);_fault('replaced')
            elif current!=plan['after_sha256']:
                raise RecoveryRequired('Audio differs from both conversion states; preserve newer data and original backup')
        else:
            if target.exists() or target.is_symlink():
                if stage.exists() and not stage.is_symlink():
                    a,b=stage.stat(),target.lstat()
                    if ((a.st_dev,a.st_ino)==(b.st_dev,b.st_ino) and a.st_nlink==2
                            and _sha(stage)==plan['after_sha256']):stage.unlink();sync_dir(target.parent)
                _audio(target)
                if _sha(target)!=plan['after_sha256']:raise RecoveryRequired('Conversion destination has different bytes')
            else:
                self._codec_bindings(path,plan['native']);current,current_epoch=_current_audio(path)
                if current!=plan['before_sha256']:raise RecoveryRequired('Original codec bytes changed')
                _audio(stage)
                if _sha(stage)!=plan['after_sha256']:raise RecoveryRequired('Prepared codec bytes changed')
                self.lease._guard();_require_audio_epoch(path,current_epoch)
                os.link(stage,target);stage.unlink();sync_dir(target.parent);_fault('replaced')
            displaced=folder/('displaced'+path.suffix.lower())
            if path.exists() or path.is_symlink():
                self._codec_bindings(path,plan['native']);current,current_epoch=_current_audio(path)
                if current!=plan['before_sha256'] or displaced.exists():raise RecoveryRequired('Original codec occupant changed')
                self.lease._guard();_require_audio_epoch(path,current_epoch)
                path.rename(displaced);sync_dir(path.parent);_fault('displaced')
            if displaced.exists():
                _regular(displaced)
                if _sha(displaced)!=plan['before_sha256']:raise RecoveryRequired('Displaced original differs')
                os.replace(displaced,original);sync_dir(folder)
        self.lease._guard();_audio(target)
        if _sha(target)!=plan['after_sha256']:raise RecoveryRequired('Converted audio changed before acknowledgement')
        facts=compatibility.inspect(target)
        if facts['status']!='compatible':raise RecoveryRequired('Converted file failed independent format readback')
        result=dict(format=FORMAT,operation_id=plan['operation_id'],path=str(path),target=str(target),
            before_sha256=plan['before_sha256'],after_sha256=plan['after_sha256'],before_epoch=plan['before_epoch'],
            epoch=identity(target.stat()),size=target.stat().st_size,facts=facts,measurements=plan['measurements'],
            original=str(original),native=plan['native'])
        atomic_bytes(folder/'receipt.json',_json(result));_fault('receipt')
        self.journal.unlink();sync_dir(self.root);return result

    def recover(self):
        with self.lease._lock():return self._recover_locked()

    def convert(self,path,operation_id,*,native=()):
        path=Path(path)
        unique:dict[str,dict]={}
        for ref in native:
            if ref['id'] in unique and unique[ref['id']]!=ref:raise ConflictError('Conflicting native audio references')
            unique[ref['id']]=ref
        native=list(unique.values())
        if (path.suffix.lower() not in ('.wav','.aif','.aiff','.flac','.m4a','.mp3','.aac')
                or path.is_relative_to('/Volumes') or path.is_relative_to(self.data_root)
                or path.is_relative_to(self.database.parent)):
            raise WriteProofError('This conversion profile requires supported desktop audio')
        target=path if path.suffix.lower()=='.wav' else path.with_suffix('.wav')
        folder=self._folder(operation_id)
        with self.lease._lock():
            recovered=self._recover_locked()
            if recovered and recovered['operation_id']==operation_id:return recovered
            self.lease._guard()
            receipt=folder/'receipt.json'
            if receipt.exists():
                _regular(receipt);result=json.loads(receipt.read_bytes())
                plan=self._load(folder,_sha(folder/'plan.json'))
                if (result.get('operation_id')!=operation_id or result.get('format')!=FORMAT
                        or result['path']!=str(path) or result.get('target',str(path))!=str(target)
                        or result['after_sha256']!=plan['after_sha256'] or result['before_sha256']!=plan['before_sha256']
                        or _sha(target)!=result['after_sha256']):
                    raise ConflictError('Completed conversion belongs to different audio bytes')
                result.update(epoch=identity(target.stat()),size=target.stat().st_size,facts=compatibility.inspect(target));return result
            _audio(path)
            # Provider header seeks can fail even after a successful stream.
            # Inspect and render the one verified local capture, never reopen
            # the canonical cloud file just to classify its container.
            with tempfile.TemporaryDirectory(prefix='soundbox-media-capture-') as scratch, \
                    acquire_source(SourceContract(path),Path(scratch).resolve()) as captured:
                facts=compatibility.inspect(captured.path)
                if identity(_audio(path))!=identity(captured.observation):
                    raise ConflictError('Audio changed during compatibility inspection')
                if facts['status']=='compatible':return dict(unchanged=True,path=str(path),epoch=identity(captured.observation),facts=facts)
                if facts['status']!='convert':raise WriteProofError(facts['reason'])
                if facts.get('codec') in ('mp3','aac'):
                    raise WriteProofError('This compressed format needs a verified timing-preserving conversion; original retained')
                if target!=path:
                    self._codec_bindings(path,native)
                    if path.stat().st_dev!=self.data_root.stat().st_dev:raise WriteProofError('Codec replacement requires the desktop recovery volume')
                else:self._bindings(path,native)
                if target!=path and (target.exists() or target.is_symlink()):raise ConflictError('A same-name WAVE already exists; original retained')
                folder.mkdir(parents=True,exist_ok=True,mode=0o700)
                if folder.resolve()!=folder:raise WriteProofError('Conversion backup redirects')
                if (folder/'plan.json').exists():
                    plan=self._load(folder,_sha(folder/'plan.json'))
                    if plan['path']!=str(path) or plan['native']!=list(native):raise ConflictError('Prepared conversion identity changed')
                    record=dict(format=FORMAT,source=str(self.database),library_id=self.authority.library_id,
                        source_file_id=list(self.file_id),folder=str(folder),plan_sha256=_sha(folder/'plan.json'))
                    self.lease._guard();atomic_bytes(self.journal,_json(record));return self._recover_locked()
                stage=path.parent/('.soundbox-media-'+operation_id+'.wav')
                if stage.exists() or stage.is_symlink():raise RecoveryRequired('Unjournaled conversion stage already exists')
                epoch=identity(self.database.stat())
                wal=self.database.with_name(self.database.name+'-wal');wal_epoch=identity(wal.stat()) if wal.exists() else None
                stage_epoch=None
                try:
                    original=folder/('original'+path.suffix.lower())
                    if original.exists():
                        _regular(original)
                        if _sha(original)!=captured.sha256:raise ConflictError('Original conversion backup has different bytes')
                    else:
                        with original.open('xb') as dst,captured.path.open('rb') as src:
                            shutil.copyfileobj(src,dst,1024*1024);dst.flush();os.fsync(dst.fileno())
                        sync_dir(folder)
                    if _sha(original)!=captured.sha256:raise WriteProofError('Conversion original verification failed')
                    _fault('backup');measurements=compatibility.render(captured.path,stage)
                    stage_epoch=identity(_audio(stage))
                    s=_audio(path)
                    if identity(s)!=identity(captured.observation):raise ConflictError('Audio changed during conversion')
                    os.chmod(stage,stat.S_IMODE(s.st_mode));os.utime(stage,ns=(s.st_atime_ns,s.st_mtime_ns))
                    from . import mac_xattrs
                    for name in ('com.apple.metadata:_kMDItemUserTags','com.apple.FinderInfo'):
                        value=mac_xattrs.get(path,name)
                        if value is not None:
                            mac_xattrs.set(original,name,value)
                            mac_xattrs.set(stage,name,b'\0'*8+value[8:] if name=='com.apple.FinderInfo' and target!=path else value)
                    stage_epoch=identity(stage.stat())
                    self.lease._guard()
                    if (identity(self.database.stat())!=epoch or (identity(wal.stat()) if wal.exists() else None)!=wal_epoch):
                        raise ConflictError('Native source changed during audio preparation')
                    plan=dict(format=FORMAT,authority=asdict(self.authority),source_file_id=list(self.file_id),
                        operation_id=operation_id,path=str(path),target=str(target),stage=str(stage),native=list(native),
                        before_sha256=captured.sha256,after_sha256=_sha(stage),before_epoch=identity(captured.observation),
                        measurements=measurements)
                    atomic_bytes(folder/'plan.json',_json(plan))
                    _fault('prepared')
                    record=dict(format=FORMAT,source=str(self.database),library_id=self.authority.library_id,
                        source_file_id=list(self.file_id),folder=str(folder),plan_sha256=_sha(folder/'plan.json'))
                    self.lease._guard()
                    if identity(_audio(path))!=identity(captured.observation):raise ConflictError('Audio changed before replacement')
                    atomic_bytes(self.journal,_json(record));_fault('journal')
                    return self._recover_locked()
                finally:
                    if (not self.journal.exists() and not (folder/'plan.json').exists() and stage_epoch is not None
                            and stage.exists() and not stage.is_symlink() and identity(stage.stat())==stage_epoch):stage.unlink()
