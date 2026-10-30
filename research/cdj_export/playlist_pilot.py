"""One-plan desktop playlist pilot; separate explicit approval is mandatory.

No production authority loader or app integration. Uses the existing Tag/Cue/Grid
writer lock, source guards and journal path. A successful SQL commit is not native
UI acceptance. OfflineWorkspace remains TEMP-only and is never relaxed here.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

from soundbox.rekordbox_cue_store import PilotCueStore, CueWriteAuthority
from soundbox.rekordbox_write import (
    ConflictError, RecoveryRequired, WriteProofError, _connect, _inactive_journal_proof,
    _json, _regular, _state, encrypted_backup,
)
from soundbox.state_io import atomic_bytes, copy_state, sync_dir
from .playlist_pilot_plan import PAYLOADS, load_plan, pilot_authorizer
from .playlist_replay import _apply
from .playlist_transaction import _sha

FORMAT = 'soundbox-native-playlist-pilot-v1'
APPROVAL_FORMAT = 'soundbox-playlist-pilot-approval-v1'


def _fault(_phase):
    """Synthetic tests only. Never inject faults into a real source library."""


def _apps_closed():
    try:
        result = subprocess.run(
            ['/usr/bin/pgrep','-f',r'[/]Soundbox.app/Contents/MacOS/Soundbox( |$)|[s]oundbox_app.py'],
            capture_output=True,text=True,timeout=5)
    except (OSError,subprocess.SubprocessError) as exc:
        raise WriteProofError('Could not verify that the SoundBox source owner is closed') from exc
    if result.returncode!=1 or result.stdout.strip() or result.stderr.strip():
        raise WriteProofError('Quit SoundBox normally before the standalone playlist pilot')


class _PlaylistLease(PilotCueStore):
    def __init__(self, owner):
        self.owner = owner
        plan = owner.plan
        super().__init__(CueWriteAuthority(plan['source'],plan['library_id'],
            plan['schema_sha256'],'playlist:'+plan['playlist_id'],plan['application_version']),
            Path(plan['recovery_root']))

    def _guard(self):
        self.owner._approved()
        _apps_closed()
        super()._guard()
        self.owner._source_identity()

    def _record(self):
        return self.owner._record()


class PlaylistPilot:
    def __init__(self, plan_folder, approval_file):
        self.folder = Path(plan_folder).absolute()
        self.plan = load_plan(self.folder)
        self.plan_sha = _sha(self.folder/'plan.json')
        self.approval_file = Path(approval_file).absolute()
        self.approval_sha = _sha(self.approval_file)
        self.database = Path(self.plan['source'])
        self.xml = self.database.with_name('masterPlaylists6.xml')
        self.root = Path(self.plan['recovery_root'])
        self.journal = self.root/'transaction.json'
        self.archive = self.root/('playlist-pilot-'+self.plan['operation_id'])
        self._approved()
        self.lease = _PlaylistLease(self)

    def _approved(self):
        if self.folder!=self.folder.resolve() or self.approval_file!=self.approval_file.resolve():
            raise WriteProofError('Pilot plan/approval path redirects')
        if (_sha(self.approval_file)!=self.approval_sha
                or _sha(self.folder/'plan.json')!=self.plan_sha
                or json.loads((self.folder/'plan.json').read_bytes())!=self.plan):
            raise WriteProofError('Pilot plan/approval changed')
        approval = json.loads(self.approval_file.read_bytes())
        if (approval.get('format')!=APPROVAL_FORMAT or approval.get('approved') is not True
                or approval.get('plan_sha256')!=self.plan_sha
                or approval.get('operation_id')!=self.plan['operation_id']
                or approval.get('source')!=self.plan['source']):
            raise WriteProofError('This exact source pilot has not been approved')

    def _source_identity(self):
        info = self.database.stat()
        if [info.st_dev,info.st_ino]!=self.plan['source_file_id']:
            raise ConflictError('Pilot source database was replaced')
        _regular(self.xml)
        if self.xml!=self.xml.resolve() or self.xml.stat().st_uid!=os.getuid():
            raise WriteProofError('Pilot XML redirects')

    def _manifest(self):
        return dict(format=FORMAT,plan_sha256=self.plan_sha,operation_id=self.plan['operation_id'],
            source=self.plan['source'],source_file_id=self.plan['source_file_id'],
            library_id=self.plan['library_id'],schema_sha256=self.plan['schema_sha256'],
            payloads=self.archive.name,payload_sha256=self.plan['payload_sha256'],
            before=self.plan['before'],after=self.plan['after'],changes=self.plan['changes'])

    def _payloads(self):
        if (self.archive!=self.archive.resolve() or not self.archive.is_dir()
                or self.archive.stat().st_mode & 0o077):
            raise RecoveryRequired('Playlist pilot archive redirects or is missing')
        allowed = {*PAYLOADS,'operation.json','result.json'}
        for path in self.archive.iterdir():
            if path.name not in allowed:raise RecoveryRequired('Unknown pilot recovery payload')
            _regular(path)
        _regular(self.archive/'operation.json')
        if json.loads((self.archive/'operation.json').read_bytes())!=self._manifest():
            raise RecoveryRequired('Pilot archive identity mismatch')
        for name in PAYLOADS:
            if _sha(self.archive/name)!=self.plan['payload_sha256'][name]:
                raise RecoveryRequired('Pilot recovery payload changed')

    def _record(self):
        self._approved();self._source_identity()
        _regular(self.journal)
        record = json.loads(self.journal.read_bytes())
        if record!=self._manifest():
            raise RecoveryRequired('Another domain or unknown pilot owns native recovery')
        self._payloads()
        return record

    def _install_payloads(self):
        if self.archive.exists() or self.archive.is_symlink():
            self._payloads();return
        self.archive.mkdir(mode=0o700)
        atomic_bytes(self.archive/'operation.json',_json(self._manifest()))
        for name in PAYLOADS:copy_state(self.folder/name,self.archive/name)
        self._payloads()
        sync_dir(self.archive);sync_dir(self.root)

    def _snapshot(self):
        # All complete inventories remain on the encrypted read-only snapshot.
        # No whole-source restore or complete live SQL table scan is admitted.
        with encrypted_backup(self.database) as copied:
            conn = _connect(copied,readonly=True)
            try:return _state(conn)
            finally:conn.close()

    _authorizer = staticmethod(pilot_authorizer)

    def _matches_final(self, conn):
        folded = {(c['table'],c['id']):c['after'] for c in self.plan['changes']}
        for (table,identity),row in folded.items():
            key = 'registry_id' if table=='agentRegistry' else 'ID'
            cursor = conn.execute(f'SELECT * FROM "{table}" WHERE "{key}"=?',(identity,))
            names = [c[0] for c in cursor.description]
            actual = [dict(zip(names,r,strict=True)) for r in cursor]
            if actual!=([] if row is None else [row]):
                raise WriteProofError('Pilot source rows differ from the exact requested result')

    def _recover(self):
        if not self.journal.exists():
            result = self.archive/'result.json'
            if result.exists():
                self._payloads();record = json.loads(result.read_bytes())
                if {k:record[k] for k in self._manifest()}!=self._manifest():
                    raise RecoveryRequired('Unknown pilot completion receipt')
                if record.get('outcome') not in ('committed','rolled-back'):
                    raise RecoveryRequired('Unverified pilot completion outcome')
                return record['outcome']
            return None
        record = self._record()
        before_xml = self.plan['payload_sha256']['before.xml']
        after_xml = self.plan['payload_sha256']['after.xml']
        current_xml = _sha(self.xml)
        if current_xml not in (before_xml,after_xml):
            raise RecoveryRequired('Pilot XML is outside the exact recovery pair')
        conn = self.lease._connect()  # owned journal verified before SQLite recovery
        try:
            conn.execute('BEGIN IMMEDIATE')
            current = self._snapshot()
            self.lease._guard()
            if _sha(self.xml)!=current_xml:
                raise RecoveryRequired('Pilot XML changed during recovery validation')
            if current==self.plan['after'] and current_xml==after_xml:outcome = 'committed'
            elif current==self.plan['before']:
                if current_xml!=before_xml:
                    self.lease._guard()
                    if _sha(self.xml)!=current_xml:
                        raise RecoveryRequired('Pilot XML changed before rollback')
                    atomic_bytes(self.xml,(self.archive/'before.xml').read_bytes())
                outcome = 'rolled-back'
            else:
                raise RecoveryRequired('Pilot source DB/XML has unknown recovery state; preserved')
            self.lease._guard()
            expected_xml = after_xml if outcome=='committed' else before_xml
            if _sha(self.xml)!=expected_xml:
                raise RecoveryRequired('Pilot XML changed before recovery completion')
            receipt = {**record,'outcome':outcome,'inactive_sqlite_journal':_inactive_journal_proof(self.database),
                       'native_acceptance':False}
            atomic_bytes(self.archive/'result.json',_json(receipt))
            atomic_bytes(self.root/'last-result.json',_json(receipt))
            _fault('receipt-ready')
            self.journal.unlink();sync_dir(self.root)
            _fault('journal-retired')
            return outcome
        finally:
            conn.rollback();conn.close()

    def recover(self):
        with self.lease._lock():return self._recover()

    def apply(self):
        with self.lease._lock():
            if self._recover()=='committed':return 'already-completed'
            conn = self.lease._connect()
            try:
                conn.execute('BEGIN IMMEDIATE')
                if (self._snapshot()!=self.plan['before']
                        or _sha(self.xml)!=self.plan['payload_sha256']['before.xml']):
                    raise ConflictError('Pilot source changed since review; no edits published')
                self._install_payloads();_fault('payload-ready')
                atomic_bytes(self.journal,_json(self._manifest()));_fault('journal-ready')
                self.lease._guard()
                conn.set_authorizer(self._authorizer)
                _apply(conn,self.plan['changes']);self._matches_final(conn)
                _fault('sql-applied')
                self.lease._guard()
                if _sha(self.xml)!=self.plan['payload_sha256']['before.xml']:
                    raise ConflictError('Pilot XML changed before publication')
                atomic_bytes(self.xml,(self.archive/'after.xml').read_bytes());_fault('xml-published')
                self.lease._guard()
                if _sha(self.xml)!=self.plan['payload_sha256']['after.xml']:
                    raise ConflictError('Pilot XML changed before SQL commit')
                conn.commit();_fault('sql-committed')
            except BaseException:
                conn.rollback();raise
            finally:conn.close()
            if self._recover()!='committed':
                raise RecoveryRequired('Pilot publication did not verify as committed')
            return self.plan['playlist_id']
