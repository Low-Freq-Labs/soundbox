"""One reviewed batch of generated multi-format registrations; no app authority.

Preparation is copy-only. Source publication requires its own exact-plan grant,
closed apps and the shared native edit owner/journal. Source audio, analysis,
cues, playlists and XML remain immutable. Original WAV pilots stay unchanged.
"""
from datetime import datetime,timezone
import json
from pathlib import Path
import secrets
import shutil
import tempfile
from uuid import uuid4

from soundbox.rekordbox_cue_store import CueWriteAuthority,PilotCueStore
from soundbox.rekordbox_tags import NATIVE_VERSION,_installed_version,_schema
from soundbox.rekordbox_write import (ConflictError,RecoveryRequired,WriteProofError,_cell,_connect,
    _identity,_json,_quote,_regular,_rows,_state,assert_rekordbox_closed,encrypted_backup)
from soundbox.state_io import atomic_bytes
from research.cdj_export.playlist_pilot import _apps_closed
from research.cdj_export.playlist_pilot_plan import shared_root
from research.cdj_export.playlist_transaction import _copy_backup,_sha
from . import media_registration as media,registration
from .media_workspace import OfflineMediaRegistrationWorkspace
from .registration_pilot import RegistrationPilot,_paths,PAYLOADS

FORMAT='soundbox-native-media-registration-pilot-v1'
PLAN_FORMAT='soundbox-media-registration-pilot-plan-v1'
APPROVAL_FORMAT='soundbox-media-registration-pilot-approval-v1'
# Exact generated October30 compatibility audio, copied under NEW reference names.
# The natively retagged04 is deliberately excluded; its old digest is historical.
REFERENCES={
 '01 - WAV16 44k.wav':'f3c0536e1637361847a5982aaf54dd4a0430d183a7be9b562ae999cf3bcbd711',
 '02 - WAV16 44k quiet.wav':'cda6d3c4ecce934b42ee943fe2be02167544642e2c6266523d76f153d1d20462',
 '03 - WAV24 48k.wav':'a5b3fe10d8bf20c315b628b7fd98b9f0e02ffc442e7d57ca63da48445bfb8251',
 '05 - AIFF24 48k.aiff':'4cd055c20e4a3cb2ba105f2f0fb3d413f06c71f31f12657665bb812cf6f1d28b',
 '06 - MP3 CBR.mp3':'be7d00010d39bca7dd70e773f8b20f87e9aa2961ec2b05d6587e69c0d94c501c',
 '07 - MP3 VBR.mp3':'4284d3f1a03bccceeee1eae94c96b2d73327694eae36d5c3b77f475f61b98caf',
 '08 - FLAC16 44k.flac':'98291861e9514cc837b91d316d99a33c196167d0f2dd505419d0dce584d5d80f',
 '09 - M4A AAC.m4a':'fa56c8f43fc31683cdd6b27cd9cb04b25b6d0a660c749c46e0d7c7a149ac84af',
 '10 - M4A ALAC.m4a':'496bbfeaf563f2c11663601ec9b84b564c2673dd644accf829215285df03ff28',
 '11 - WAV24 48k classic JUNK.wav':'ec34faea7b8a2da96befd67f8459d626ec6bcb8bf1e0f265cc95260443ef66ad',
}
NAME_PREFIX='SoundBox registration - '


def _fault(_phase):
    """Synthetic TEMP tests only; never inject faults into a real library."""


def _scope(audio):
    audio.validate();name=Path(audio.path).name
    if not name.startswith(NAME_PREFIX) or REFERENCES.get(name[len(NAME_PREFIX):])!=audio.sha256:
        raise WriteProofError('Only the exact generated multi-format registration references are in scope')


def prepare(source,audio_paths,output,*,data_root):
    source,output,data_root=(Path(p).expanduser().absolute() for p in (source,output,data_root))
    _paths(source,output,data_root);_regular(source)
    paths=[Path(p).absolute() for p in audio_paths]
    if not 1<=len(paths)<=10 or len(set(paths))!=len(paths):raise WriteProofError('One bounded distinct reference batch required')
    assert_rekordbox_closed();_apps_closed()
    if _installed_version()!=NATIVE_VERSION:raise WriteProofError('Native media reference version changed')
    xml=source.with_name('masterPlaylists6.xml');_regular(xml);xml_bytes=xml.read_bytes()
    output.mkdir(parents=True,mode=0o700)
    with tempfile.TemporaryDirectory(prefix='soundbox-media-pilot-prepare-') as tmp:
        root=Path(tmp).resolve();frozen=root/'frozen';frozen.mkdir()
        with encrypted_backup(source) as copied:_copy_backup(copied,frozen/'master.db')
        conn=_connect(frozen/'master.db',readonly=True)
        try:
            library_id,_=_identity(conn);schema=_schema(conn);before=_state(conn)
            occupied={r['ID'] for r in _rows(conn,'djmdContent')}
        finally:conn.close()
        recovery=shared_root(data_root,source,library_id)
        if (output.is_relative_to(recovery) or recovery.is_relative_to(output)
                or (recovery/'transaction.json').exists() or (recovery/'grid-transaction').exists()):
            raise RecoveryRequired('Resolve shared recovery before preparing media registration')
        (frozen/'state.json').write_bytes(_json(before));(frozen/'masterPlaylists6.xml').write_bytes(xml_bytes)
        workspace=OfflineMediaRegistrationWorkspace.capture(frozen);candidates=[]
        try:
            for path in paths:
                if path.is_relative_to(source.parent) or path.is_relative_to(output):
                    raise WriteProofError('Generated audio must stay outside source and plan directories')
                audio=workspace.inspect_media(path);_scope(audio)
                identity=str(secrets.randbelow((1<<28)-1)+1)
                while identity in occupied:identity=str(secrets.randbelow((1<<28)-1)+1)
                occupied.add(identity);now=datetime.now(timezone.utc)
                timestamp=now.strftime('%Y-%m-%d %H:%M:%S.')+f'{now.microsecond//1000:03d} +00:00'
                plan=workspace.plan(audio,identity=identity,uuid=str(uuid4()),timestamp=timestamp,
                                     day=datetime.now().astimezone().date().isoformat())
                workspace.apply(plan);candidates.append(plan['candidate'])
            conn=_connect(workspace.database,readonly=True)
            try:after=_state(conn)
            finally:conn.close()
            shutil.copyfile(workspace.root/'baseline.db',output/'before.db')
            shutil.copyfile(workspace.database,output/'after.db')
        except BaseException as exc:
            exc.add_note('Incomplete multi-format registration workspace retained at '+str(workspace.root));raise
        else:shutil.rmtree(workspace.root)
    for name in ('before.xml','after.xml'):atomic_bytes(output/name,xml_bytes)
    record=dict(format=PLAN_FORMAT,operation_id=str(uuid4()),source=str(source),
        source_file_id=[source.stat().st_dev,source.stat().st_ino],library_id=library_id,schema_sha256=schema,
        application_version=NATIVE_VERSION,data_root=str(data_root),recovery_root=str(recovery),candidates=candidates,
        before=before,after=after,source_write_authority=False,payload_sha256={n:_sha(output/n) for n in PAYLOADS})
    atomic_bytes(output/'plan.json',_json(record));assert_rekordbox_closed();_apps_closed()
    if xml.read_bytes()!=xml_bytes:raise ConflictError('Native XML changed during media preparation')
    return load_plan(output)


def load_plan(folder):
    folder=Path(folder).absolute()
    if folder!=folder.resolve() or folder.stat().st_mode&0o077:raise WriteProofError('Private original media plan folder required')
    for name in (*PAYLOADS,'plan.json'):_regular(folder/name)
    record=json.loads((folder/'plan.json').read_bytes())
    if (record.get('format')!=PLAN_FORMAT or record.get('application_version')!=NATIVE_VERSION
            or record.get('source_write_authority') is not False or not 1<=len(record['candidates'])<=10):
        raise WriteProofError('Unknown media registration plan/version')
    registration._uuid(record['operation_id']);_paths(Path(record['source']),folder,Path(record['data_root']))
    if shared_root(record['data_root'],record['source'],record['library_id'])!=Path(record['recovery_root']):
        raise WriteProofError('Media registration requires the common native recovery root')
    if set(record['payload_sha256'])!=set(PAYLOADS):raise WriteProofError('Unknown media evidence payloads')
    for name in PAYLOADS:
        if _sha(folder/name)!=record['payload_sha256'][name]:raise RecoveryRequired('Media plan payload changed')
    if (folder/'before.xml').read_bytes()!=(folder/'after.xml').read_bytes():raise WriteProofError('Registration cannot change XML')
    identities=set();uuids=set();paths=set()
    for candidate in record['candidates']:
        audio=media.MediaIdentity.from_dict(candidate['audio']);_scope(audio);row=candidate['row']
        if row['MasterDBID']!=record['library_id'] or row['ID'] in identities or row['UUID'] in uuids or audio.path in paths:
            raise WriteProofError('Ambiguous media batch identities')
        identities.add(row['ID']);uuids.add(row['UUID']);paths.add(audio.path)
    with tempfile.TemporaryDirectory(prefix=media.PREFIX) as tmp:
        root=Path(tmp).resolve();media.mark(root)
        shutil.copyfile(folder/'before.db',root/'working.db');shutil.copyfile(folder/'after.db',root/'native.db')
        conn=_connect(root/'working.db');native=_connect(root/'native.db',readonly=True)
        try:
            if _schema(conn)!=record['schema_sha256'] or _state(conn)!=record['before']:
                raise WriteProofError('Media plan before inventory differs')
            conn.execute('BEGIN IMMEDIATE')
            for candidate in record['candidates']:media.apply_temp(conn,candidate)
            if _state(conn)!=_state(native) or _state(conn)!=record['after']:
                raise WriteProofError('Media batch does not reconstruct the complete native inventory')
            conn.rollback()
        finally:conn.close();native.close()
    return record


class _MediaLease(PilotCueStore):
    def __init__(self,owner):
        self.owner=owner;plan=owner.plan
        super().__init__(CueWriteAuthority(plan['source'],plan['library_id'],plan['schema_sha256'],
            'media-registration:'+plan['operation_id'],plan['application_version']),Path(plan['recovery_root']))

    def _guard(self):
        self.owner._approved();_apps_closed();super()._guard();self.owner._source_identity()

    def _record(self):return self.owner._record()


class MediaRegistrationPilot(RegistrationPilot):
    def __init__(self,folder,approval_file):
        self.folder=Path(folder).absolute();self.plan=load_plan(self.folder);self.plan_sha=_sha(self.folder/'plan.json')
        self.approval_file=Path(approval_file).absolute();self.approval_sha=_sha(self.approval_file)
        self.database=Path(self.plan['source']);self.xml=self.database.with_name('masterPlaylists6.xml')
        self.root=Path(self.plan['recovery_root']);self.journal=self.root/'transaction.json'
        self.archive=self.root/('media-registration-pilot-'+self.plan['operation_id'])
        self._approved();self.lease=_MediaLease(self)

    def _approved(self):
        if self.folder!=self.folder.resolve() or self.approval_file!=self.approval_file.resolve():
            raise WriteProofError('Media approval redirects')
        if (_sha(self.approval_file)!=self.approval_sha or _sha(self.folder/'plan.json')!=self.plan_sha
                or json.loads((self.folder/'plan.json').read_bytes())!=self.plan):
            raise WriteProofError('Media plan or approval changed')
        approval=json.loads(self.approval_file.read_bytes())
        if (approval.get('format')!=APPROVAL_FORMAT or approval.get('approved') is not True
                or approval.get('plan_sha256')!=self.plan_sha or approval.get('source')!=self.plan['source']
                or approval.get('operation_id')!=self.plan['operation_id']):
            raise WriteProofError('This exact multi-format source pilot has not been approved')

    def _manifest(self):
        return dict(format=FORMAT,plan_sha256=self.plan_sha,operation_id=self.plan['operation_id'],
            source=self.plan['source'],source_file_id=self.plan['source_file_id'],library_id=self.plan['library_id'],
            schema_sha256=self.plan['schema_sha256'],payloads=self.archive.name,payload_sha256=self.plan['payload_sha256'],
            before=self.plan['before'],after=self.plan['after'],candidates=self.plan['candidates'])

    def _matches_final(self,conn):
        expected=[('djmdContent','ID',c['row']) for c in self.plan['candidates']]
        expected.append(('agentRegistry','registry_id',self.plan['candidates'][-1]['counter_after']))
        for table,key,row in expected:
            actual=self.lease._rows(conn,table,' WHERE '+_quote(key)+'=?',(row[key],))
            if [{k:_cell(v) for k,v in r.items()} for r in actual]!=[row]:
                raise WriteProofError('Registered media source rows differ from the exact planned result')

    def apply(self):
        with self.lease._lock():
            if self._recover()=='committed':return 'already-completed'
            audio=[media.MediaIdentity.from_dict(c['audio']) for c in self.plan['candidates']]
            for facts in audio:facts.revalidate()
            conn=self.lease._connect()
            try:
                conn.execute('BEGIN IMMEDIATE')
                if self._snapshot()!=self.plan['before'] or _sha(self.xml)!=self.plan['payload_sha256']['before.xml']:
                    raise ConflictError('Media source changed since review; no registrations published')
                self._install_payloads();_fault('payload-ready');atomic_bytes(self.journal,_json(self._manifest()));_fault('journal-ready')
                self.lease._guard()
                conn.set_authorizer(registration._authorizer)
                for candidate,facts in zip(self.plan['candidates'],audio,strict=True):
                    facts.revalidate();row=candidate['row'];columns=list(row)
                    if conn.execute('SELECT COUNT(*) FROM djmdContent WHERE ID=? OR UUID=? OR FolderPath=?',
                                    (row['ID'],row['UUID'],row['FolderPath'])).fetchone()!=(0,):
                        raise ConflictError('Media registration identity already exists')
                    conn.execute('INSERT INTO djmdContent ('+','.join(_quote(c) for c in columns)+') VALUES ('+
                                 ','.join('?' for _ in columns)+')',list(row.values()))
                    if conn.execute("UPDATE agentRegistry SET int_1=? WHERE registry_id='localUpdateCount' AND int_1=?",
                        (candidate['counter_after']['int_1'],candidate['counter_before']['int_1'])).rowcount!=1:
                        raise ConflictError('Native media counter changed')
                self._matches_final(conn);_fault('sql-applied');self.lease._guard()
                for facts in audio:facts.revalidate()
                if _sha(self.xml)!=self.plan['payload_sha256']['before.xml']:raise ConflictError('XML changed during registration')
                conn.commit();_fault('sql-committed')
            except BaseException:conn.rollback();raise
            finally:conn.close()
            if self._recover()!='committed':raise RecoveryRequired('Media registration did not verify as committed')
            return [c['row']['ID'] for c in self.plan['candidates']]
