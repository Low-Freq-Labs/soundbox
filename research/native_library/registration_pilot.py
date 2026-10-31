"""One reviewed generated-WAV registration pilot; NOT a production writer.

Preparation is copy-only. Publication needs a separate exact-plan approval and
closed native apps/SoundBox. The Cue transport supplies shared ownership/guards,
not Cue permission: this domain's own plan, approval and journal remain mandatory.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import secrets
import shutil
import tempfile
from uuid import uuid4

from soundbox.rekordbox_cue_store import CueWriteAuthority, PilotCueStore
from soundbox.rekordbox_tags import NATIVE_VERSION, _installed_version, _schema
from soundbox.rekordbox_write import (
    ConflictError, RecoveryRequired, WriteProofError, _cell, _connect, _identity,
    _inactive_journal_proof, _json, _quote, _regular, _rows, _state, assert_rekordbox_closed, encrypted_backup,
)
from soundbox.state_io import atomic_bytes, sync_dir
from research.cdj_export.playlist_pilot import PlaylistPilot, _apps_closed
from research.cdj_export.playlist_pilot_plan import shared_root
from research.cdj_export.playlist_transaction import _copy_backup, _sha
from . import registration
from .registration_workspace import OfflineRegistrationWorkspace

FORMAT = 'soundbox-native-registration-pilot-v1'
PLAN_FORMAT = 'soundbox-registration-pilot-plan-v1'
APPROVAL_FORMAT = 'soundbox-registration-pilot-approval-v1'
PAYLOADS = ('before.db', 'after.db', 'before.xml', 'after.xml')
REFERENCE_SHA256 = 'f3c0536e1637361847a5982aaf54dd4a0430d183a7be9b562ae999cf3bcbd711'
REFERENCE_NAME = 'SoundBox-first reference.wav'


def _fault(_phase):
    """Synthetic TEMP tests only; never fault-inject an actual library."""


def _scope(audio):
    audio.validate()
    if Path(audio.path).name != REFERENCE_NAME or audio.sha256 != REFERENCE_SHA256 or audio.seconds != 32:
        raise WriteProofError('Pilot scope is the one generated SoundBox-first reference WAV')


def _paths(source, output, data_root):
    for path in (source, output, data_root):
        if not path.is_absolute() or path != path.resolve() or path.is_relative_to('/Volumes'):
            raise WriteProofError('Explicit canonical local pilot paths required')
    if (source.name != 'master.db' or output.is_relative_to(source.parent)
            or source.is_relative_to(output) or data_root.is_relative_to(source.parent)
            or source.is_relative_to(data_root)):
        raise WriteProofError('Registration pilot paths overlap')


def prepare(source, audio_path, output, *, data_root):
    source, output, data_root = (Path(p).expanduser().absolute() for p in (source, output, data_root))
    _paths(source, output, data_root); _regular(source)
    assert_rekordbox_closed(); _apps_closed()
    if _installed_version() != NATIVE_VERSION:
        raise WriteProofError('Native registration reference version changed')
    xml = source.with_name('masterPlaylists6.xml'); _regular(xml)
    xml_bytes = xml.read_bytes()
    output.mkdir(parents=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix='soundbox-registration-plan-') as area:
        root = Path(area).resolve(); frozen = root/'frozen'; frozen.mkdir()
        with encrypted_backup(source) as copied:
            _copy_backup(copied, frozen/'master.db')
        conn = _connect(frozen/'master.db', readonly=True)
        try:
            library_id, _ = _identity(conn); schema = _schema(conn); before = _state(conn)
            occupied = {r['ID'] for r in _rows(conn, 'djmdContent')}
        finally:
            conn.close()
        recovery = shared_root(data_root, source, library_id)
        if (output.is_relative_to(recovery) or recovery.is_relative_to(output)
                or (recovery/'transaction.json').exists() or (recovery/'grid-transaction').exists()):
            raise RecoveryRequired('Resolve existing shared recovery before preparing registration')
        (frozen/'state.json').write_bytes(_json(before))
        (frozen/'masterPlaylists6.xml').write_bytes(xml_bytes)
        workspace = OfflineRegistrationWorkspace.capture(frozen)
        try:
            audio = workspace.inspect_wave(audio_path); _scope(audio)
            identity = str(secrets.randbelow((1 << 28)-1)+1)
            while identity in occupied:
                identity = str(secrets.randbelow((1 << 28)-1)+1)
            now = datetime.now(timezone.utc)
            timestamp = now.strftime('%Y-%m-%d %H:%M:%S.') + f'{now.microsecond//1000:03d} +00:00'
            plan = workspace.plan(audio, identity=identity, uuid=str(uuid4()), timestamp=timestamp,
                                  day=datetime.now().astimezone().date().isoformat())
            workspace.apply(plan)
            after = _state_from(workspace.database)
            shutil.copyfile(workspace.root/'baseline.db', output/'before.db')
            shutil.copyfile(workspace.database, output/'after.db')
            candidate = plan['candidate']
            record = dict(format=PLAN_FORMAT, operation_id=plan['operation_id'], source=str(source),
                source_file_id=[source.stat().st_dev, source.stat().st_ino], library_id=library_id,
                schema_sha256=schema, application_version=NATIVE_VERSION, data_root=str(data_root),
                recovery_root=str(recovery), candidate=candidate, before=before, after=after,
                native_id=identity, native_uuid=candidate['row']['UUID'], source_write_authority=False)
        finally:
            # Copy transaction/receipt verified above. Any failed operation keeps
            # its workspace evidence, with the retained path attached to the error.
            if not workspace.transaction.exists() and not workspace.retired.exists():
                shutil.rmtree(workspace.root)
    atomic_bytes(output/'before.xml', xml_bytes); atomic_bytes(output/'after.xml', xml_bytes)
    record['payload_sha256'] = {name:_sha(output/name) for name in PAYLOADS}
    atomic_bytes(output/'plan.json', _json(record))
    assert_rekordbox_closed(); _apps_closed()
    if xml.read_bytes() != xml_bytes:
        raise ConflictError('Native XML changed during registration preparation')
    return load_plan(output)


def _state_from(path):
    conn = _connect(path, readonly=True)
    try:
        return _state(conn)
    finally:
        conn.close()


def load_plan(folder):
    folder = Path(folder).absolute()
    if folder != folder.resolve() or folder.stat().st_mode & 0o077:
        raise WriteProofError('Private original registration plan folder required')
    for name in (*PAYLOADS, 'plan.json'):
        _regular(folder/name)
    record = json.loads((folder/'plan.json').read_bytes())
    if (record['format'] != PLAN_FORMAT or record['application_version'] != NATIVE_VERSION
            or record.get('source_write_authority') is not False):
        raise WriteProofError('Unknown registration pilot plan/version')
    registration._uuid(record['operation_id'])
    _paths(Path(record['source']), folder, Path(record['data_root']))
    if shared_root(record['data_root'], record['source'], record['library_id']) != Path(record['recovery_root']):
        raise WriteProofError('Registration does not use the shared recovery root')
    for name in PAYLOADS:
        if _sha(folder/name) != record['payload_sha256'][name]:
            raise RecoveryRequired('Registration plan payload changed')
    if (folder/'before.xml').read_bytes() != (folder/'after.xml').read_bytes():
        raise WriteProofError('Bare registration cannot change playlist XML')
    audio = registration.WaveIdentity.from_dict(record['candidate']['audio']); _scope(audio)
    if (record['candidate']['row']['ID'] != record['native_id'] or record['candidate']['row']['UUID'] != record['native_uuid']
            or record['candidate']['row']['MasterDBID'] != record['library_id']
            or record['candidate']['baseline'] != record['before']):
        raise WriteProofError('Registration identity/base differs from its requested intent')
    with tempfile.TemporaryDirectory(prefix='soundbox-registration-plan-check-') as area:
        root = Path(area).resolve()
        for name in ('before.db','after.db'):
            shutil.copyfile(folder/name, root/name)
        before = _connect(root/'before.db', readonly=True); after = _connect(root/'after.db', readonly=True)
        try:
            if _schema(before) != record['schema_sha256'] or registration.verify(before, after, record['candidate']) != record['after']:
                raise WriteProofError('Registration plan does not prove its exact full delta')
        finally:
            before.close(); after.close()
    return record


class _RegistrationLease(PilotCueStore):
    def __init__(self, owner):
        self.owner = owner
        plan = owner.plan
        super().__init__(CueWriteAuthority(plan['source'], plan['library_id'], plan['schema_sha256'],
            'registration:'+plan['native_id'], plan['application_version']), Path(plan['recovery_root']))

    def _guard(self):
        self.owner._approved(); _apps_closed()
        super()._guard(); self.owner._source_identity()

    def _record(self):
        return self.owner._record()


class RegistrationPilot(PlaylistPilot):
    """Reuses paired evidence/lock machinery, never playlist permission or SQL."""
    def __init__(self, folder, approval_file):
        self.folder = Path(folder).absolute(); self.plan = load_plan(self.folder)
        self.plan_sha = _sha(self.folder/'plan.json')
        self.approval_file = Path(approval_file).absolute(); self.approval_sha = _sha(self.approval_file)
        self.database = Path(self.plan['source']); self.xml = self.database.with_name('masterPlaylists6.xml')
        self.root = Path(self.plan['recovery_root']); self.journal = self.root/'transaction.json'
        self.archive = self.root/('registration-pilot-'+self.plan['operation_id'])
        self._approved(); self.lease = _RegistrationLease(self)

    def _approved(self):
        if self.folder != self.folder.resolve() or self.approval_file != self.approval_file.resolve():
            raise WriteProofError('Registration approval redirects')
        if (_sha(self.approval_file) != self.approval_sha or _sha(self.folder/'plan.json') != self.plan_sha
                or json.loads((self.folder/'plan.json').read_bytes()) != self.plan):
            raise WriteProofError('Registration plan or approval changed')
        approval = json.loads(self.approval_file.read_bytes())
        if (approval.get('format') != APPROVAL_FORMAT or approval.get('approved') is not True
                or approval.get('plan_sha256') != self.plan_sha or approval.get('source') != self.plan['source']
                or approval.get('operation_id') != self.plan['operation_id']):
            raise WriteProofError('This exact registration source pilot has not been approved')

    def _manifest(self):
        return dict(format=FORMAT, plan_sha256=self.plan_sha, operation_id=self.plan['operation_id'],
            source=self.plan['source'], source_file_id=self.plan['source_file_id'], library_id=self.plan['library_id'],
            schema_sha256=self.plan['schema_sha256'], payloads=self.archive.name,
            payload_sha256=self.plan['payload_sha256'], before=self.plan['before'], after=self.plan['after'],
            candidate=self.plan['candidate'])

    def _matches_final(self, conn):
        candidate = self.plan['candidate']
        for table, key, expected in (('djmdContent','ID',candidate['row']),
                                     ('agentRegistry','registry_id',candidate['counter_after'])):
            actual = self.lease._rows(conn, table, ' WHERE '+_quote(key)+'=?', (expected[key],))
            if [{k:_cell(v) for k,v in row.items()} for row in actual] != [expected]:
                raise WriteProofError('Registration source target differs from its exact result')

    def _recover(self):
        outcome = super()._recover()
        if outcome is not None:
            receipt = json.loads((self.archive/'result.json').read_bytes())
            proof = receipt.get('inactive_sqlite_journal')
            if proof is not None and self.database.with_name('master.db-journal').exists():
                self.lease._guard()
                if _inactive_journal_proof(self.database) != proof:
                    raise RecoveryRequired('Registration inactive journal differs from its owned receipt')
                self.lease._guard()
                if _inactive_journal_proof(self.database) != proof:
                    raise RecoveryRequired('Registration inactive journal changed before retirement')
                self.database.with_name('master.db-journal').unlink(); sync_dir(self.database.parent)
        return outcome

    def apply(self):
        with self.lease._lock():
            if self._recover() == 'committed':
                return 'already-completed'
            audio = registration.WaveIdentity.from_dict(self.plan['candidate']['audio'])
            conn = self.lease._connect()
            try:
                conn.execute('BEGIN IMMEDIATE')
                audio.revalidate()
                if self._snapshot() != self.plan['before'] or _sha(self.xml) != self.plan['payload_sha256']['before.xml']:
                    raise ConflictError('Registration source changed since review; no edits published')
                self._install_payloads(); _fault('payload-ready')
                atomic_bytes(self.journal, _json(self._manifest())); _fault('journal-ready')
                self.lease._guard(); audio.revalidate()
                candidate = self.plan['candidate']; row = candidate['row']; columns = list(row)
                if conn.execute('SELECT COUNT(*) FROM djmdContent WHERE ID=? OR UUID=? OR FolderPath=?',
                                (row['ID'],row['UUID'],row['FolderPath'])).fetchone() != (0,):
                    raise ConflictError('Registration identity already exists')
                conn.set_authorizer(registration._authorizer)
                conn.execute('INSERT INTO djmdContent ('+','.join(_quote(c) for c in columns)+') VALUES ('+
                             ','.join('?' for _ in columns)+')', list(row.values()))
                conn.execute("UPDATE agentRegistry SET int_1=? WHERE registry_id='localUpdateCount' AND int_1=?",
                             (candidate['counter_after']['int_1'],candidate['counter_before']['int_1']))
                self._matches_final(conn); _fault('sql-applied')
                self.lease._guard(); audio.revalidate()
                if _sha(self.xml) != self.plan['payload_sha256']['before.xml']:
                    raise ConflictError('Playlist XML changed during bare registration')
                conn.commit(); _fault('sql-committed')
            except BaseException:
                conn.rollback()
                raise
            finally:
                conn.close()
            if self._recover() != 'committed':
                raise RecoveryRequired('Registration source result did not verify as committed')
            return self.plan['native_id']


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prep = commands.add_parser('prepare')
    prep.add_argument('source', type=Path); prep.add_argument('audio', type=Path); prep.add_argument('output', type=Path)
    prep.add_argument('--data-root', type=Path, required=True)
    show = commands.add_parser('show'); show.add_argument('plan', type=Path)
    for name in ('apply', 'recover'):
        action = commands.add_parser(name); action.add_argument('plan', type=Path)
        action.add_argument('--approval', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'prepare':
        record = prepare(args.source, args.audio, args.output, data_root=args.data_root)
    elif args.command == 'show':
        record = load_plan(args.plan)
    else:
        store = RegistrationPilot(args.plan, args.approval)
        print(store.apply() if args.command == 'apply' else store.recover())
        return
    print(json.dumps(dict(operation_id=record['operation_id'], source=record['source'],
        audio=record['candidate']['audio']['path'], native_id=record['native_id'], native_uuid=record['native_uuid'],
        counter_before=record['candidate']['counter_before']['int_1'], counter_after=record['candidate']['counter_after']['int_1'],
        title=record['candidate']['row']['Title'], source_write_authority=False,
        scope='One generated bare WAV row +1 counter; XML/audio/analysis/playlists unchanged'), indent=2))


if __name__ == '__main__':
    main()
