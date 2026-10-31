"""Initial-analysis DB + directory publication proof inside marked TEMP only.

No source writer or authority loader. The physical share is a disposable mapping
of explicit logical file paths. A private staged directory publishes atomically
before SQL commit; its durable inode proof owns only this transaction's files.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from uuid import uuid4

from soundbox.rekordbox_write import (
    ConflictError, RecoveryRequired, WriteProofError, _cell, _connect, _directory, _inactive_journal_proof,
    _integrity, _json, _quote, _regular, _rows, _state,
)
from soundbox.state_io import atomic_bytes, sync_dir
from research.cdj_export.playlist_transaction import _copy_backup, _inventory, _sha
from . import fixture_analysis as planner
from .registration import WaveIdentity, _uuid

FORMAT='soundbox-offline-initial-analysis-v1'
PREFIX='soundbox-initial-analysis-'
NAMES=('ANLZ0000.DAT','ANLZ0000.EXT','ANLZ0000.2EX')
TABLES=('djmdContent','agentRegistry','contentFile','djmdMixerParam')


def _checkpoint(_phase):
    """Synthetic process exits only; this module has no source target."""


def _encoded(row):return {k:_cell(v) for k,v in row.items()}


def _base(conn,tid):
    tracks=[_encoded(r) for r in _rows(conn,'djmdContent') if r['ID']==tid]
    if len(tracks)!=1:raise ConflictError('Exact initial-analysis track required')
    counters=[_encoded(r) for r in _rows(conn,'agentRegistry') if r['registry_id']=='localUpdateCount']
    if len(counters)!=1:raise ConflictError('Exact native counter required')
    return dict(track=tracks[0],counter=counters[0],keys=_rows(conn,'djmdKey'),
        files=_rows(conn,'contentFile'),mixers=_rows(conn,'djmdMixerParam'),
        cues=[r for r in _rows(conn,'djmdCue') if r['ContentID']==tid],
        aggregates=[r for r in _rows(conn,'contentCue') if r['ContentID']==tid])


def _authorizer(action,table,column,database,trigger):
    if action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE):
        if trigger or database!='main':return sqlite3.SQLITE_DENY
        if action==sqlite3.SQLITE_INSERT and table in ('contentFile','djmdMixerParam'):return sqlite3.SQLITE_OK
        if action==sqlite3.SQLITE_UPDATE:
            if table=='agentRegistry' and column=='int_1':return sqlite3.SQLITE_OK
            if table=='djmdContent' and column in ('BPM','Length','BitRate','BitDepth','SampleRate','KeyID','AnalysisDataPath',
                'ContentLink','Analysed','AnalysisUpdated','TrackInfoUpdated','rb_local_usn','updated_at'):return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK if action in (sqlite3.SQLITE_READ,sqlite3.SQLITE_SELECT,sqlite3.SQLITE_FUNCTION,
        sqlite3.SQLITE_TRANSACTION,sqlite3.SQLITE_RECURSIVE) else sqlite3.SQLITE_DENY


def _apply(conn,changes):
    paths=conn.execute('PRAGMA database_list').fetchall()
    if len(paths)!=1:raise WriteProofError('No attached analysis databases')
    path=Path(paths[0][2]);root=path.parent
    if (path!=path.resolve() or path.name not in ('working.db','after.db') or not root.name.startswith(PREFIX)
            or not root.is_relative_to(Path(tempfile.gettempdir()).resolve())):
        raise WriteProofError('Analysis SQL is restricted to marked TEMP copies')
    _regular(root/'workspace.json')
    if json.loads((root/'workspace.json').read_bytes()).get('format')!=FORMAT:
        raise WriteProofError('Unmarked analysis connection')
    conn.set_authorizer(_authorizer)
    try:
        for change in changes:
            table=change['table'];key='registry_id' if table=='agentRegistry' else 'ID'
            if table not in TABLES:raise WriteProofError('Unplanned analysis table')
            cursor=conn.execute('SELECT * FROM '+_quote(table)+' WHERE '+_quote(key)+'=?',(change['id'],))
            columns=[c[0] for c in cursor.description]
            rows=[_encoded(dict(zip(columns,r,strict=True))) for r in cursor]
            if rows!=([] if change['before'] is None else [change['before']]):raise ConflictError('Stale analysis row')
            row=change['after']
            if change['before'] is None:
                cols=list(row)
                conn.execute('INSERT INTO '+_quote(table)+' ('+','.join(_quote(c) for c in cols)+') VALUES ('+
                    ','.join('?' for _ in cols)+')',list(row.values()))
            else:
                cols=[k for k in row if row[k]!=change['before'][k]]
                conn.execute('UPDATE '+_quote(table)+' SET '+','.join(_quote(k)+'=?' for k in cols)+' WHERE '+_quote(key)+'=?',
                             [row[k] for k in cols]+[change['id']])
    finally:conn.set_authorizer(None)


def _verify(before,after,changes):
    left,right=_state(before),_state(after)
    if left['schema']!=right['schema'] or left['metadata']!=right['metadata']:raise WriteProofError('Analysis changed schema/metadata')
    for table in left['tables'].keys()|right['tables'].keys():
        if table not in TABLES and left['tables'].get(table)!=right['tables'].get(table):raise WriteProofError('Analysis changed unrelated data')
    for table in TABLES:
        key='registry_id' if table=='agentRegistry' else 'ID'
        old=[_encoded(r) for r in _rows(before,table)];current=[_encoded(r) for r in _rows(after,table)]
        expected={r[key]:r for r in old}
        if len(expected)!=len(old):raise WriteProofError('Ambiguous analysis row identity')
        for change in changes:
            if change['table']!=table:continue
            if expected.get(change['id'])!=change['before']:raise ConflictError('Analysis projection is stale')
            expected[change['id']]=change['after']
        if len(current)!=len(expected) or {r[key]:r for r in current}!=expected:raise WriteProofError('Analysis changed unplanned rows')
    return right


class OfflineAnalysisWorkspace:
    def __init__(self,root):
        self.root=Path(root).absolute()
        if (self.root!=self.root.resolve() or not self.root.name.startswith(PREFIX)
                or not self.root.is_relative_to(Path(tempfile.gettempdir()).resolve())):
            raise WriteProofError('Original marked TEMP analysis workspace required')
        _directory(self.root);_regular(self.root/'workspace.json')
        self.metadata=json.loads((self.root/'workspace.json').read_bytes())
        info=self.root.stat()
        if (self.metadata.get('format')!=FORMAT or self.metadata.get('root')!=str(self.root)
                or self.metadata.get('root_id')!=[info.st_dev,info.st_ino]):raise WriteProofError('Relocated analysis workspace')
        self.database=self.root/'working.db';self.journal=self.root/'transaction.json';self.share=self.root/'share'
        self._guard()
        if _sha(self.root/'before.db')!=self.metadata['baseline_sha256'] or _inventory(self.root/'before.db')!=self.metadata['before']:
            raise RecoveryRequired('Analysis baseline changed')

    @classmethod
    def capture(cls,folder):
        source=Path(folder).absolute()
        if source!=source.resolve(strict=True) or source.is_relative_to('/Volumes'):raise WriteProofError('Explicit frozen local capture required')
        for name in ('master.db','state.json','masterPlaylists6.xml'):_regular(source/name)
        if any((source/('master.db'+s)).exists() for s in ('-wal','-shm','-journal')):raise WriteProofError('Frozen input must be consolidated')
        root=Path(tempfile.mkdtemp(prefix=PREFIX)).resolve();root.chmod(0o700)
        shutil.copyfile(source/'master.db',root/'raw.db');_copy_backup(root/'raw.db',root/'before.db');(root/'raw.db').unlink()
        state=_inventory(root/'before.db')
        if state!=json.loads((source/'state.json').read_bytes()):raise ConflictError('Frozen analysis inventory mismatch')
        _copy_backup(root/'before.db',root/'working.db')
        atomic_bytes(root/'masterPlaylists6.xml',(source/'masterPlaylists6.xml').read_bytes())
        (root/'share').mkdir(mode=0o700);info=root.stat()
        atomic_bytes(root/'workspace.json',_json(dict(format=FORMAT,root=str(root),root_id=[info.st_dev,info.st_ino],source=str(source),
            workspace_id=str(uuid4()),before=state,baseline_sha256=_sha(root/'before.db'),xml_sha256=_sha(root/'masterPlaylists6.xml'))))
        return cls(root)

    def _guard(self):
        _directory(self.root)
        info=self.root.stat()
        if (self.root!=self.root.resolve() or info.st_uid!=os.getuid() or [info.st_dev,info.st_ino]!=self.metadata['root_id']
                or self.root.is_relative_to(Path(self.metadata['source']))
                or json.loads((self.root/'workspace.json').read_bytes())!=self.metadata):
            raise RecoveryRequired('Analysis workspace marker changed')
        for name in ('before.db','working.db','masterPlaylists6.xml','after.db','plan.json','transaction.json','last-result.json','writer.lock'):
            path=self.root/name
            if path.exists() or path.is_symlink():_regular(path)
        if _sha(self.root/'masterPlaylists6.xml')!=self.metadata['xml_sha256']:raise RecoveryRequired('Analysis XML changed')
        _directory(self.share)
        for suffix in ('-wal','-shm','-journal'):
            path=self.database.with_name('working.db'+suffix)
            if path.exists() or path.is_symlink():
                _regular(path)
                if not self.journal.exists():raise RecoveryRequired('Unowned analysis SQL companion')
        packet=self.root/'packet'
        if packet.exists() or packet.is_symlink():
            _directory(packet)
            if {p.name for p in packet.iterdir()}!=set(NAMES):raise RecoveryRequired('Unknown packet inventory')
            for p in packet.iterdir():_regular(p)

    @contextmanager
    def _lock(self):
        self._guard();path=self.root/'writer.lock';fd=os.open(path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try:
            _regular(path)
            if (os.fstat(fd).st_dev,os.fstat(fd).st_ino)!=(path.stat().st_dev,path.stat().st_ino):
                raise WriteProofError('Analysis lock was replaced')
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError as exc:raise WriteProofError('Analysis workspace busy') from exc
            self._guard();yield
        finally:os.close(fd)

    def plan(self,tid,audio,images,*,share_root,seeds):
        with self._lock():
            if self.journal.exists() or (self.root/'plan.json').exists():raise RecoveryRequired('One initial-analysis intent per workspace')
            if _inventory(self.database)!=self.metadata['before']:raise ConflictError('Analysis before-state changed')
            conn=_connect(self.database,readonly=True)
            try:base=_base(conn,tid);candidate=planner.plan(base,audio,images,share_root=share_root,seeds=seeds)
            finally:conn.close()
            packet=self.root/'packet';packet.mkdir(mode=0o700)
            for ext,raw in images.items():atomic_bytes(packet/('ANLZ0000'+ext),raw)
            _copy_backup(self.database,self.root/'after.db')
            record=dict(format=FORMAT,workspace_id=self.metadata['workspace_id'],operation_id=str(uuid4()),track_id=tid,
                audio=dict(path=audio.path,epoch=list(audio.epoch),sha256=audio.sha256,seconds=audio.seconds),logical_share=share_root,
                seeds=seeds,candidate=candidate,packet_sha256={name:_sha(packet/name) for name in NAMES})
            conn=_connect(self.root/'after.db');before=_connect(self.root/'before.db',readonly=True)
            try:
                conn.execute('BEGIN IMMEDIATE');_apply(conn,candidate['changes']);record['after']=_verify(before,conn,candidate['changes'])
                conn.commit();_integrity(conn)
            finally:before.close();conn.close()
            record['after_sha256']=_sha(self.root/'after.db')
            atomic_bytes(self.root/'plan.json',_json(record));return record

    def _plan(self):
        self._guard();record=json.loads((self.root/'plan.json').read_bytes())
        if record['format']!=FORMAT or record['workspace_id']!=self.metadata['workspace_id']:raise RecoveryRequired('Foreign analysis plan')
        _uuid(record['operation_id'])
        for name in NAMES:
            if _sha(self.root/'packet'/name)!=record['packet_sha256'][name]:raise RecoveryRequired('Analysis packet changed')
        if _sha(self.root/'after.db')!=record['after_sha256']:raise RecoveryRequired('Analysis after backup changed')
        before=_connect(self.root/'before.db',readonly=True);after=_connect(self.root/'after.db',readonly=True)
        try:
            images={Path(name).suffix:(self.root/'packet'/name).read_bytes() for name in NAMES}
            expected=planner.plan(_base(before,record['track_id']),WaveIdentity.from_dict(record['audio']),images,
                                  share_root=record['logical_share'],seeds=record['seeds'])
            if expected!=record['candidate'] or _verify(before,after,expected['changes'])!=record['after']:
                raise RecoveryRequired('Analysis plan no longer proves its full delta')
        finally:before.close();after.close()
        return record

    def _locations(self,plan):
        uuid=next(c['after']['UUID'] for c in plan['candidate']['changes'] if c['table']=='djmdContent')
        target=self.share/'PIONEER'/'USBANLZ'/uuid[:3]/uuid[3:]
        stage=self.share/('.stage-'+plan['operation_id'])
        checks=[stage,target];parent=target.parent
        while parent.is_relative_to(self.root):
            checks.append(parent)
            if parent==self.root:break
            parent=parent.parent
        for path in checks:
            if path.exists() or path.is_symlink():
                if path!=path.resolve():raise RecoveryRequired('Analysis directory redirects')
                _directory(path)
        return stage,target

    def _directory_files(self,path,expected=None):
        _directory(path)
        if path!=path.resolve():raise RecoveryRequired('Analysis payload directory redirects')
        names={p.name for p in path.iterdir()}
        if not names<=set(NAMES) or (expected is not None and names!=set(NAMES)):
            raise RecoveryRequired('Unknown analysis directory contents')
        for p in path.iterdir():
            _regular(p)
            if expected is not None and (p.stat().st_size!=(self.root/'packet'/p.name).stat().st_size or _sha(p)!=expected[p.name]):
                raise RecoveryRequired('Published analysis bytes differ')

    def _finish(self,record,outcome):
        # A read-only reopen can leave companions on a WAL-mode database. Fold
        # only this verified disposable working copy before retiring ownership;
        # this is never applied to the source or a protected frozen input.
        expected=record['after'] if outcome=='committed' else self.metadata['before']
        conn=_connect(self.database)
        try:
            if _state(conn)!=expected:raise RecoveryRequired('Analysis changed before recovery completion')
            if conn.execute('PRAGMA journal_mode=DELETE').fetchone()[0]!='delete':
                raise RecoveryRequired('Could not retire TEMP analysis WAL mode')
            _integrity(conn)
            if _state(conn)!=expected:raise RecoveryRequired('Analysis changed during WAL retirement')
        finally:conn.close()
        inactive=_inactive_journal_proof(self.database)
        if inactive is not None:
            if _inactive_journal_proof(self.database)!=inactive:raise RecoveryRequired('Analysis inactive journal changed')
            self.database.with_name('working.db-journal').unlink();sync_dir(self.root)
        atomic_bytes(self.root/'last-result.json',_json(dict(operation_id=record['operation_id'],outcome=outcome,after=record['after'])))
        _checkpoint('receipt-ready');self.journal.unlink();sync_dir(self.root);_checkpoint('journal-retired')
        return outcome

    def _recover(self):
        if not self.journal.exists():return None
        plan=self._plan();record=json.loads(self.journal.read_bytes())
        if (record['plan_sha256']!=_sha(self.root/'plan.json') or record['operation_id']!=plan['operation_id']
                or record['database_id']!=[self.database.stat().st_dev,self.database.stat().st_ino]):
            raise RecoveryRequired('Analysis journal identity mismatch')
        stage,target=self._locations(plan)
        conn=_connect(self.database)
        try:_integrity(conn);current=_state(conn)
        finally:conn.close()
        if current not in (self.metadata['before'],plan['after']):raise RecoveryRequired('Unknown analysis database state')
        identity=record.get('directory_id')
        existing=[p for p in (stage,target) if p.exists() or p.is_symlink()]
        if len(existing)>1:raise RecoveryRequired('Impossible analysis directory prefix')
        if current==plan['after']:
            if existing!=[target] or identity!=[target.stat().st_dev,target.stat().st_ino]:raise RecoveryRequired('Committed analysis directory missing or replaced')
            self._directory_files(target,plan['packet_sha256']);return self._finish(plan,'committed')
        if existing:
            directory=existing[0];info=directory.stat()
            if identity is None:
                if directory!=stage or any(directory.iterdir()):raise RecoveryRequired('Unowned staging directory')
            elif identity!=[info.st_dev,info.st_ino]:raise RecoveryRequired('Analysis directory was replaced')
            self._directory_files(directory,plan['packet_sha256'] if directory==target else None)
            record['cleanup_started']=True;atomic_bytes(self.journal,_json(record))
            for p in directory.iterdir():p.unlink()
            directory.rmdir();sync_dir(directory.parent)
        elif identity is not None and not record.get('cleanup_started'):
            raise RecoveryRequired('Owned analysis directory disappeared unexpectedly')
        return self._finish(plan,'rolled-back')

    def recover(self):
        with self._lock():return self._recover()

    def apply(self):
        with self._lock():
            self._recover();plan=self._plan();stage,target=self._locations(plan)
            receipt=self.root/'last-result.json'
            if receipt.exists() and json.loads(receipt.read_bytes()).get('outcome')=='committed':
                if _inventory(self.database)!=plan['after']:raise RecoveryRequired('Committed analysis changed')
                self._directory_files(target,plan['packet_sha256']);return 'already-committed'
            if _inventory(self.database)!=self.metadata['before']:raise ConflictError('Analysis baseline is stale')
            if stage.exists() or target.exists() or stage.is_symlink() or target.is_symlink():raise ConflictError('Initial analysis target is occupied')
            record=dict(plan_sha256=_sha(self.root/'plan.json'),operation_id=plan['operation_id'],
                database_id=[self.database.stat().st_dev,self.database.stat().st_ino],directory_id=None,cleanup_started=False)
            atomic_bytes(self.journal,_json(record));_checkpoint('journal-ready')
            stage.mkdir(mode=0o700);sync_dir(self.share);_checkpoint('stage-created')
            info=stage.stat();record['directory_id']=[info.st_dev,info.st_ino];atomic_bytes(self.journal,_json(record))
            for name in NAMES:
                atomic_bytes(stage/name,(self.root/'packet'/name).read_bytes());_checkpoint('file-'+name)
            self._directory_files(stage,plan['packet_sha256']);sync_dir(stage)
            # All physical paths are under the private proof share, never the
            # logical rb_local_path stored in the copied native metadata.
            chain=[];parent=target.parent
            while parent!=self.share:chain.append(parent);parent=parent.parent
            for parent in reversed(chain):
                if not parent.exists():parent.mkdir(mode=0o700);sync_dir(parent.parent)
            self._locations(plan);os.rename(stage,target);sync_dir(target.parent);sync_dir(self.share);_checkpoint('files-published')
            conn=_connect(self.database);before=_connect(self.root/'before.db',readonly=True)
            try:
                conn.execute('BEGIN IMMEDIATE')
                if _state(conn)!=self.metadata['before']:raise ConflictError('Analysis DB changed before commit')
                _apply(conn,plan['candidate']['changes']);_verify(before,conn,plan['candidate']['changes']);_checkpoint('sql-applied')
                self._guard();self._directory_files(target,plan['packet_sha256']);conn.commit();_checkpoint('sql-committed')
            except BaseException:conn.rollback();raise
            finally:before.close();conn.close()
            return self._recover()
