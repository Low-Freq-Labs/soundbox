"""Separately approved initial analysis for the one accepted generated WAV.

No runtime integration or production authority. Existing copy-only workspaces
remain copy-only. The standalone publisher owns a new exact plan, packet,
approval, shared journal and recoverable directory before opening source SQL.
"""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import tempfile
from uuid import uuid4

from soundbox.rekordbox_cue_store import CueWriteAuthority, PilotCueStore
from soundbox.rekordbox_tags import NATIVE_VERSION, _installed_version, _schema
from soundbox.rekordbox_write import (
    ConflictError, RecoveryRequired, WriteProofError, _connect, _identity, _inactive_journal_proof,
    _json, _quote, _regular, _state, assert_rekordbox_closed, encrypted_backup,
)
from soundbox.state_io import atomic_bytes, copy_state, sync_dir
from research.cdj_export.playlist_pilot import PlaylistPilot, _apps_closed
from research.cdj_export.playlist_pilot_plan import shared_root
from research.cdj_export.playlist_transaction import _copy_backup, _sha
from . import analysis_workspace as offline, fixture_analysis as planner, registration
from .registration_pilot import _paths

FORMAT = 'soundbox-native-initial-analysis-pilot-v1'
PLAN_FORMAT = 'soundbox-initial-analysis-pilot-plan-v1'
APPROVAL_FORMAT = 'soundbox-initial-analysis-pilot-approval-v1'
TRACK_ID = '190717052'
TRACK_UUID = '2e47171e-a854-4b9c-be9b-128e28202cce'
REFERENCE_NAME = 'SoundBox-first reference.wav'
PAYLOADS = ('before.db','after.db','before.xml','after.xml',*offline.NAMES)


def _fault(_phase):
    """Synthetic crash tests only; never inject faults into an actual library."""


def _scope(audio, track):
    audio.validate()
    if (audio.sha256 != planner.REFERENCE_SHA or audio.seconds != 32
            or Path(audio.path).name != REFERENCE_NAME
            or track['ID'] != TRACK_ID or track['UUID'] != TRACK_UUID):
        raise WriteProofError('Only the accepted generated SoundBox-first registration is in scope')


def _native_directory(path):
    info = path.lstat()
    if path != path.resolve() or not path.is_dir() or path.is_symlink() or info.st_uid != os.getuid():
        raise RecoveryRequired('Native analysis directory redirects or changed owner')


def _seeds():
    now = datetime.now(timezone.utc)
    def stamp(offset):
        return (now+timedelta(milliseconds=offset)).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]+' +00:00'
    return dict(mixer_id=str(uuid4()),mixer_uuid=str(uuid4()),mixer_stamp=stamp(0),track_stamp=stamp(1),
        file_uuids={ext:str(uuid4()) for ext in planner.FILE_ORDER},
        file_stamps={ext:stamp(i) for i,ext in enumerate(planner.FILE_ORDER,2)})


def prepare(source, audio_path, packet, output, *, data_root):
    source, output, data_root = (Path(p).expanduser().absolute() for p in (source,output,data_root))
    _paths(source,output,data_root); _regular(source)
    assert_rekordbox_closed(); _apps_closed()
    if _installed_version() != NATIVE_VERSION:
        raise WriteProofError('Native analysis reference version changed')
    xml = source.with_name('masterPlaylists6.xml'); _regular(xml); xml_bytes = xml.read_bytes()
    share = source.parent/'share'; _native_directory(share)
    packet = Path(packet).absolute()
    if packet != packet.resolve() or {p.name for p in packet.iterdir()} != set(offline.NAMES):
        raise WriteProofError('Exact prepared DAT/EXT/2EX packet required')
    for name in offline.NAMES: _regular(packet/name)
    images = {Path(name).suffix:(packet/name).read_bytes() for name in offline.NAMES}
    output.mkdir(parents=True,mode=0o700)
    with tempfile.TemporaryDirectory(prefix='soundbox-analysis-plan-') as area:
        scratch = Path(area).resolve(); frozen = scratch/'frozen'; frozen.mkdir(mode=0o700)
        with encrypted_backup(source) as copied: _copy_backup(copied,frozen/'master.db')
        conn = _connect(frozen/'master.db',readonly=True)
        try:
            library_id,_ = _identity(conn); schema = _schema(conn); before = _state(conn)
            track = offline._base(conn,TRACK_ID)['track']
        finally: conn.close()
        recovery = shared_root(data_root,source,library_id)
        if (output.is_relative_to(recovery) or recovery.is_relative_to(output)
                or (recovery/'transaction.json').exists() or (recovery/'grid-transaction').exists()):
            raise RecoveryRequired('Resolve shared native recovery before preparing initial analysis')
        atomic_bytes(frozen/'state.json',_json(before)); atomic_bytes(frozen/'masterPlaylists6.xml',xml_bytes)
        audio = registration.inspect_wave(Path(audio_path),scratch); _scope(audio,track)
        workspace = offline.OfflineAnalysisWorkspace.capture(frozen)
        try:
            planned = workspace.plan(TRACK_ID,audio,images,share_root=str(share),seeds=_seeds())
            if workspace.apply() != 'committed': raise WriteProofError('Copy-only analysis did not commit')
            shutil.copyfile(workspace.root/'before.db',output/'before.db')
            shutil.copyfile(workspace.database,output/'after.db')
            record = dict(format=PLAN_FORMAT,operation_id=planned['operation_id'],source=str(source),
                source_file_id=[source.stat().st_dev,source.stat().st_ino],library_id=library_id,
                schema_sha256=schema,application_version=NATIVE_VERSION,data_root=str(data_root),recovery_root=str(recovery),
                share=str(share),share_file_id=[share.stat().st_dev,share.stat().st_ino],audio=dict(path=audio.path,
                epoch=list(audio.epoch),sha256=audio.sha256,seconds=audio.seconds),seeds=planned['seeds'],
                before=before,after=planned['after'],candidate=planned['candidate'],source_write_authority=False)
        finally:
            if not workspace.journal.exists(): shutil.rmtree(workspace.root)
    for name in ('before.xml','after.xml'): atomic_bytes(output/name,xml_bytes)
    for name in offline.NAMES: atomic_bytes(output/name,images[Path(name).suffix])
    record['payload_sha256'] = {name:_sha(output/name) for name in PAYLOADS}
    atomic_bytes(output/'plan.json',_json(record))
    assert_rekordbox_closed(); _apps_closed(); audio.revalidate()
    if xml.read_bytes() != xml_bytes: raise ConflictError('Native XML changed during analysis preparation')
    return load_plan(output)


def load_plan(folder):
    folder = Path(folder).absolute()
    if folder != folder.resolve() or folder.stat().st_mode & 0o077:
        raise WriteProofError('Private original initial-analysis plan required')
    for name in (*PAYLOADS,'plan.json'): _regular(folder/name)
    record = json.loads((folder/'plan.json').read_bytes())
    if (record['format'] != PLAN_FORMAT or record['application_version'] != NATIVE_VERSION
            or record.get('source_write_authority') is not False):
        raise WriteProofError('Unknown initial-analysis plan/version')
    registration._uuid(record['operation_id']); _paths(Path(record['source']),folder,Path(record['data_root']))
    if (shared_root(record['data_root'],record['source'],record['library_id']) != Path(record['recovery_root'])
            or Path(record['share']) != Path(record['source']).parent/'share'):
        raise WriteProofError('Initial analysis must use its exact native share/recovery root')
    if set(record['payload_sha256']) != set(PAYLOADS): raise WriteProofError('Unknown analysis payloads')
    for name in PAYLOADS:
        if _sha(folder/name) != record['payload_sha256'][name]: raise RecoveryRequired('Initial-analysis payload changed')
    if (folder/'before.xml').read_bytes() != (folder/'after.xml').read_bytes():
        raise WriteProofError('Initial analysis cannot change playlist XML')
    with tempfile.TemporaryDirectory(prefix='soundbox-analysis-plan-check-') as area:
        scratch = Path(area).resolve()
        for name in ('before.db','after.db'): shutil.copyfile(folder/name,scratch/name)
        before = _connect(scratch/'before.db',readonly=True); after = _connect(scratch/'after.db',readonly=True)
        try:
            audio = registration.WaveIdentity.from_dict(record['audio']); base = offline._base(before,TRACK_ID); _scope(audio,base['track'])
            expected = planner.plan(base,audio,{Path(n).suffix:(folder/n).read_bytes() for n in offline.NAMES},
                                    share_root=record['share'],seeds=record['seeds'])
            if (_schema(before) != record['schema_sha256'] or _identity(before)[0] != record['library_id']
                    or _state(before) != record['before'] or expected != record['candidate']
                    or offline._verify(before,after,expected['changes']) != record['after']):
                raise WriteProofError('Initial-analysis plan no longer proves its exact full delta')
        finally: before.close(); after.close()
    return record


class _Lease(PilotCueStore):
    def __init__(self, owner):
        self.owner = owner; p = owner.plan
        super().__init__(CueWriteAuthority(p['source'],p['library_id'],p['schema_sha256'],
            'initial-analysis:'+TRACK_ID,p['application_version']),Path(p['recovery_root']))

    def _guard(self):
        self.owner._approved(); _apps_closed(); super()._guard(); self.owner._source_identity()

    def _record(self): return self.owner._record()


class InitialAnalysisPilot(PlaylistPilot):
    def __init__(self, folder, approval_file):
        self.folder = Path(folder).absolute(); self.plan = load_plan(self.folder)
        self.plan_sha = _sha(self.folder/'plan.json'); self.approval_file = Path(approval_file).absolute()
        self.approval_sha = _sha(self.approval_file); self.database = Path(self.plan['source'])
        self.xml = self.database.with_name('masterPlaylists6.xml'); self.root = Path(self.plan['recovery_root'])
        self.journal = self.root/'transaction.json'; self.archive = self.root/('initial-analysis-pilot-'+self.plan['operation_id'])
        self.share = Path(self.plan['share']); self._approved(); self.lease = _Lease(self)

    def _approved(self):
        if (self.folder != self.folder.resolve() or self.approval_file != self.approval_file.resolve()
                or _sha(self.approval_file) != self.approval_sha or _sha(self.folder/'plan.json') != self.plan_sha
                or json.loads((self.folder/'plan.json').read_bytes()) != self.plan):
            raise WriteProofError('Initial-analysis plan/approval changed or redirects')
        approval = json.loads(self.approval_file.read_bytes())
        if (approval.get('format') != APPROVAL_FORMAT or approval.get('approved') is not True
                or approval.get('plan_sha256') != self.plan_sha or approval.get('source') != self.plan['source']
                or approval.get('operation_id') != self.plan['operation_id']):
            raise WriteProofError('This exact initial-analysis source pilot has not been approved')

    def _source_identity(self):
        super()._source_identity(); _native_directory(self.share)
        if [self.share.stat().st_dev,self.share.stat().st_ino] != self.plan['share_file_id']:
            raise RecoveryRequired('Native share was replaced')
        self._locations()

    def _manifest(self):
        return dict(format=FORMAT,plan_sha256=self.plan_sha,operation_id=self.plan['operation_id'],
            source=self.plan['source'],source_file_id=self.plan['source_file_id'],library_id=self.plan['library_id'],
            schema_sha256=self.plan['schema_sha256'],payloads=self.archive.name,payload_sha256=self.plan['payload_sha256'],
            before=self.plan['before'],after=self.plan['after'],candidate=self.plan['candidate'],
            share=self.plan['share'],share_file_id=self.plan['share_file_id'])

    def _payloads(self):
        _native_directory(self.archive)
        if self.archive.stat().st_mode & 0o077: raise RecoveryRequired('Analysis archive is not private')
        if not {p.name for p in self.archive.iterdir()} <= {*PAYLOADS,'operation.json','result.json'}:
            raise RecoveryRequired('Unknown analysis archive payload')
        for name in (*PAYLOADS,'operation.json'): _regular(self.archive/name)
        if (self.archive/'result.json').exists() or (self.archive/'result.json').is_symlink():
            _regular(self.archive/'result.json')
            receipt = json.loads((self.archive/'result.json').read_bytes())
            if ({k:receipt.get(k) for k in self._manifest()} != self._manifest()
                    or receipt.get('outcome') not in ('committed','rolled-back')
                    or receipt.get('native_acceptance') is not False):
                raise RecoveryRequired('Unknown initial-analysis receipt; preserved')
        if json.loads((self.archive/'operation.json').read_bytes()) != self._manifest():
            raise RecoveryRequired('Analysis archive identity changed')
        for name in PAYLOADS:
            if _sha(self.archive/name) != self.plan['payload_sha256'][name]: raise RecoveryRequired('Analysis archive bytes changed')

    def _install_payloads(self):
        if not self.archive.exists():
            self.archive.mkdir(mode=0o700)
            atomic_bytes(self.archive/'operation.json',_json(self._manifest()))
            for name in PAYLOADS: copy_state(self.folder/name,self.archive/name)
            sync_dir(self.archive); sync_dir(self.root)
        self._payloads()

    def _record(self):
        self._approved(); _regular(self.journal); record = json.loads(self.journal.read_bytes())
        if ({k:record.get(k) for k in self._manifest()} != self._manifest()
                or set(record) != {*self._manifest(),'directory_id','cleanup_started'}
                or type(record['cleanup_started']) is not bool
                or (record['directory_id'] is not None and (not isinstance(record['directory_id'],list)
                    or len(record['directory_id']) != 2 or any(type(n) is not int or n < 0 for n in record['directory_id'])))):
            raise RecoveryRequired('Unknown domain or initial-analysis journal identity')
        self._payloads(); return record

    def _locations(self):
        target = self.share/'PIONEER'/'USBANLZ'/TRACK_UUID[:3]/TRACK_UUID[3:]
        stage = self.share/('.soundbox-initial-analysis-'+self.plan['operation_id'])
        for path in (stage,target,*target.parents):
            if not path.is_relative_to(self.share): continue
            if path.exists() or path.is_symlink(): _native_directory(path)
        return stage,target

    def _files(self, directory, *, complete=False):
        _native_directory(directory)
        names = {p.name for p in directory.iterdir()}
        if not names <= set(offline.NAMES) or complete and names != set(offline.NAMES):
            raise RecoveryRequired('Unknown or missing initial-analysis file')
        for path in directory.iterdir():
            _regular(path)
            if _sha(path) != self.plan['payload_sha256'][path.name]:
                raise RecoveryRequired('Owned initial-analysis bytes changed; preserved')

    def _finish(self, record, outcome):
        self.lease._guard()
        if (self._snapshot() != self.plan['after' if outcome=='committed' else 'before']
                or _sha(self.xml) != self.plan['payload_sha256']['before.xml']):
            raise RecoveryRequired('Initial-analysis DB/XML changed before recovery completion')
        receipt = {**record,'outcome':outcome,'native_acceptance':False,
                   'inactive_sqlite_journal':_inactive_journal_proof(self.database)}
        atomic_bytes(self.archive/'result.json',_json(receipt)); atomic_bytes(self.root/'last-result.json',_json(receipt))
        _fault('receipt-ready'); self.lease._guard(); self.journal.unlink(); sync_dir(self.root); _fault('journal-retired')
        self._retire_inactive(receipt)
        return outcome

    def _retire_inactive(self, receipt):
        proof = receipt['inactive_sqlite_journal']; journal = self.database.with_name('master.db-journal')
        if proof is not None and journal.exists():
            self.lease._guard()
            if _inactive_journal_proof(self.database) != proof: raise RecoveryRequired('Inactive analysis journal changed')
            journal.unlink(); sync_dir(self.database.parent)

    def _recover(self):
        if not self.journal.exists():
            if not (self.archive/'result.json').exists(): return None
            self._payloads(); receipt = json.loads((self.archive/'result.json').read_bytes())
            if ({k:receipt.get(k) for k in self._manifest()} != self._manifest()
                    or receipt.get('outcome') not in ('committed','rolled-back')):
                raise RecoveryRequired('Unknown initial-analysis completion receipt')
            self._retire_inactive(receipt)
            return receipt['outcome']
        record = self._record(); stage,target = self._locations()
        if _sha(self.xml) != self.plan['payload_sha256']['before.xml']:
            raise RecoveryRequired('Native XML changed; initial-analysis recovery preserved')
        conn = self.lease._connect()
        try: conn.execute('BEGIN IMMEDIATE'); current = self._snapshot()
        finally: conn.rollback(); conn.close()
        if current not in (self.plan['before'],self.plan['after']):
            raise RecoveryRequired('Native DB is outside the exact initial-analysis recovery pair')
        existing = [p for p in (stage,target) if p.exists() or p.is_symlink()]
        if len(existing) > 1: raise RecoveryRequired('Impossible initial-analysis directory prefix')
        identity = record['directory_id']
        if current == self.plan['after']:
            if existing != [target] or [target.stat().st_dev,target.stat().st_ino] != identity:
                raise RecoveryRequired('Committed analysis directory missing or replaced')
            self._files(target,complete=True); return self._finish(record,'committed')
        if existing:
            directory = existing[0]; self._files(directory)
            if identity is None:
                if directory != stage or any(directory.iterdir()): raise RecoveryRequired('Unowned analysis stage')
            elif [directory.stat().st_dev,directory.stat().st_ino] != identity:
                raise RecoveryRequired('Owned analysis directory was replaced')
            self.lease._guard(); record['cleanup_started'] = True; atomic_bytes(self.journal,_json(record))
            for path in list(directory.iterdir()):
                self.lease._guard(); self._files(directory); path.unlink(); sync_dir(directory)
            self.lease._guard(); directory.rmdir(); sync_dir(directory.parent)
        elif identity is not None and not record['cleanup_started']:
            raise RecoveryRequired('Owned analysis directory disappeared unexpectedly')
        return self._finish(record,'rolled-back')

    def apply(self):
        with self.lease._lock():
            if self._recover() == 'committed': return 'already-completed'
            registration.WaveIdentity.from_dict(self.plan['audio']).revalidate()
            if self._snapshot() != self.plan['before'] or _sha(self.xml) != self.plan['payload_sha256']['before.xml']:
                raise ConflictError('Initial-analysis source changed since review')
            stage,target = self._locations()
            if any(p.exists() or p.is_symlink() for p in (stage,target)):
                raise ConflictError('Initial-analysis destination is occupied')
            self._install_payloads(); _fault('payload-ready')
            record = {**self._manifest(),'directory_id':None,'cleanup_started':False}
            atomic_bytes(self.journal,_json(record)); _fault('journal-ready')
            self.lease._guard(); stage.mkdir(mode=0o700); sync_dir(self.share); _fault('stage-created')
            record['directory_id'] = [stage.stat().st_dev,stage.stat().st_ino]
            atomic_bytes(self.journal,_json(record))
            for name in offline.NAMES:
                self.lease._guard(); atomic_bytes(stage/name,(self.archive/name).read_bytes()); _fault('file-'+name)
            self._files(stage,complete=True)
            chain=[]; parent=target.parent
            while parent != self.share: chain.append(parent); parent=parent.parent
            for parent in reversed(chain):
                self.lease._guard()
                if not parent.exists(): parent.mkdir(mode=0o700); sync_dir(parent.parent)
            self.lease._guard(); self._locations()
            if target.exists() or target.is_symlink(): raise ConflictError('Analysis target appeared before publication')
            os.rename(stage,target); sync_dir(target.parent); sync_dir(self.share); _fault('files-published')
            conn = self.lease._connect()
            try:
                conn.execute('BEGIN IMMEDIATE')
                if self._snapshot() != self.plan['before']: raise ConflictError('Analysis source changed before SQL')
                self._apply_sql(conn); _fault('sql-applied'); self.lease._guard()
                registration.WaveIdentity.from_dict(self.plan['audio']).revalidate(); self._files(target,complete=True)
                if _sha(self.xml) != self.plan['payload_sha256']['before.xml']: raise ConflictError('Native XML changed')
                conn.commit(); _fault('sql-committed')
            except BaseException: conn.rollback(); raise
            finally: conn.close()
            if self._recover() != 'committed': raise RecoveryRequired('Initial-analysis publication did not verify')
            return TRACK_ID

    def _apply_sql(self, conn):
        databases = conn.execute('PRAGMA database_list').fetchall()
        if len(databases) != 1 or Path(databases[0][2]) != self.database:
            raise WriteProofError('Initial-analysis SQL belongs only to its explicitly approved source')
        conn.set_authorizer(offline._authorizer)
        try:
            for change in self.plan['candidate']['changes']:
                table=change['table']; key='registry_id' if table=='agentRegistry' else 'ID'
                cursor=conn.execute('SELECT * FROM '+_quote(table)+' WHERE '+_quote(key)+'=?',(change['id'],))
                columns=[c[0] for c in cursor.description]
                actual=[offline._encoded(dict(zip(columns,row,strict=True))) for row in cursor]
                if actual != ([] if change['before'] is None else [change['before']]): raise ConflictError('Stale analysis row')
                row=change['after']
                if set(columns) != set(row) or row[key] != change['id']: raise WriteProofError('Analysis row/schema changed')
                if change['before'] is None:
                    conn.execute('INSERT INTO '+_quote(table)+' ('+','.join(_quote(c) for c in columns)+') VALUES ('+
                                 ','.join('?' for _ in columns)+')',[row[c] for c in columns])
                else:
                    cols=[c for c in columns if row[c] != change['before'][c]]
                    conn.execute('UPDATE '+_quote(table)+' SET '+','.join(_quote(c)+'=?' for c in cols)+' WHERE '+_quote(key)+'=?',
                                 [row[c] for c in cols]+[change['id']])
                check=conn.execute('SELECT * FROM '+_quote(table)+' WHERE '+_quote(key)+'=?',(change['id'],)).fetchall()
                if [offline._encoded(dict(zip(columns,r,strict=True))) for r in check] != [row]:
                    raise WriteProofError('Analysis source row readback differs')
        finally: conn.set_authorizer(None)


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__); commands=parser.add_subparsers(dest='command',required=True)
    prep=commands.add_parser('prepare')
    for name in ('source','audio','packet','output'): prep.add_argument(name,type=Path)
    prep.add_argument('--data-root',type=Path,required=True)
    show=commands.add_parser('show'); show.add_argument('plan',type=Path)
    for name in ('apply','recover'):
        action=commands.add_parser(name); action.add_argument('plan',type=Path); action.add_argument('--approval',type=Path,required=True)
    args=parser.parse_args()
    if args.command=='prepare': record=prepare(args.source,args.audio,args.packet,args.output,data_root=args.data_root)
    elif args.command=='show': record=load_plan(args.plan)
    else:
        pilot=InitialAnalysisPilot(args.plan,args.approval)
        print(pilot.apply() if args.command=='apply' else pilot.recover()); return
    print(json.dumps(dict(operation_id=record['operation_id'],source=record['source'],track_id=TRACK_ID,
        bpm=120,key='8B',beats=64,cues=0,new_files=list(offline.NAMES),source_write_authority=False,
        scope='One accepted generated WAV only; +20 counter, target metadata +3 files +1 mixer; XML/audio/playlists preserved'),indent=2))


if __name__=='__main__': main()
