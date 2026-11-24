"""Track-bound source protocol on synthetic temp libraries, never live AppData."""
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from sqlcipher3 import dbapi2 as sqlcipher

from soundbox import rekordbox_cue_store as cues
from soundbox import rekordbox_grid_store as grids
from soundbox import rekordbox_tags as tags
from soundbox import rekordbox_write as offline
from soundbox.rekordbox_grid import Beat, GridEdit, read_grid
from tests.test_rekordbox_grid import primary, container, section, content_file
from tests.test_rekordbox_grid_proof import fixture
from tests.test_pcm_endpoint import wave_bytes

UID = '11111111-2222-4333-8444-555555555555'
ANALYSIS = f'/PIONEER/USBANLZ/{UID[:3]}/{UID[3:]}/ANLZ0000.DAT'


class GridSourceTransactions(unittest.TestCase):
    def test_library_authority_requires_explicit_acceptance_and_exact_source(self):
        with tempfile.TemporaryDirectory() as area:
            path = Path(area) / 'authority.json'
            values = dict(database=str(Path(area) / 'master.db'), library_id='synthetic',
                          schema_sha256='a' * 64, accepted=True)
            source = Path(values['database'])
            self.assertIsNone(grids.load_grid_authority(path, source, 'synthetic'))
            path.write_text(json.dumps({**values, 'accepted': False}))
            self.assertIsNone(grids.load_grid_authority(path, source, 'synthetic'))
            path.write_text(json.dumps(values))
            authority = grids.load_grid_authority(path, source, 'synthetic')
            self.assertIsNotNone(authority)
            self.assertEqual(authority.bind('2', UID).track_uuid, UID)
            self.assertFalse(authority.bind('2', UID).manual_bpm_accepted)
            path.write_text(json.dumps({**values, 'manual_bpm_accepted': True}))
            authority = grids.load_grid_authority(path, source, 'synthetic')
            self.assertTrue(authority.bind('2', UID).manual_bpm_accepted)
            self.assertIsNone(grids.load_grid_authority(path, source, 'other'))
            self.assertIsNone(grids.load_grid_authority(path, source.with_name('other.db'), 'synthetic'))

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.source = self.base / 'source/master.db'
        self.source.parent.mkdir()
        fixture(self.source)
        conn = offline._connect(self.source)
        conn.execute('ALTER TABLE djmdContent ADD COLUMN AnalysisDataPath TEXT')
        conn.execute('UPDATE djmdContent SET UUID=?,AnalysisDataPath=? WHERE ID=?', (UID, ANALYSIS, '2'))
        self.authority = grids.GridWriteAuthority(str(self.source), 'synthetic-library',
                                                  tags._schema(conn), '2', track_uuid=UID)
        conn.close()
        self.dat = primary(tuple(Beat((i + 3) % 4 + 1, 9000, 39 + i * 2000 // 3)
                                 for i in range(252)))
        self.ext = container(section(b'PQT2', bytes.fromhex('000000000100000200000000')
                                    + bytes(16) + struct.pack('>IIII', 3, 123, 0, 0)
                                    + bytes(6), 56),
                             section(b'PCOB', b'unchanged cue section'),
                             section(b'PWV3', struct.pack('>III', 1, 25143, 0x960000)
                                     + bytes(25143), 24))
        self.dat_path = self.source.parent / 'share' / ANALYSIS.lstrip('/')
        self.dat_path.parent.mkdir(parents=True)
        self.dat_path.write_bytes(self.dat)
        self.ext_path = self.dat_path.with_suffix('.EXT')
        self.ext_path.write_bytes(self.ext)
        self.extra_path = self.dat_path.with_suffix('.2EX')
        self.extra_path.write_bytes(b'never touch this companion')
        self.root = self.base / 'recovery'
        self.enterContext(mock.patch.object(cues, '_closed'))
        self.enterContext(mock.patch.object(cues, '_installed_version', return_value=tags.NATIVE_VERSION))
        self.enterContext(mock.patch.object(tags, '_assert_closed'))
        self.enterContext(mock.patch.object(tags, '_installed_version', return_value=tags.NATIVE_VERSION))
        self.store = grids.PilotGridStore(self.authority, self.root)

    def request(self, operation='shift', value=1):
        snapshot = self.store.snapshot()
        return GridEdit('2', UID, snapshot.revision, operation, value)

    def prepared_record(self, operation='shift', value=1):
        def stop(phase):
            if phase == 'journal-ready':raise RuntimeError('synthetic prepared journal')
        request = self.request(operation, value)
        with mock.patch.object(grids, '_fault', side_effect=stop), \
                self.assertRaisesRegex(RuntimeError, 'synthetic prepared journal'):
            self.store.apply(request)
        return json.loads(self.store.journal.read_bytes())

    def test_recovery_plan_reuse_keeps_fresh_payload_reads_endpoint_and_cold_store_proof(self):
        record = self.prepared_record()
        original_read = Path.read_bytes
        reads = []
        def read(path):
            reads.append(path)
            return original_read(path)
        with mock.patch.object(grids, 'plan_grid_edit', wraps=grids.plan_grid_edit) as plan, \
                mock.patch.object(self.store, '_exact_end', wraps=self.store._exact_end) as endpoint:
            for decision in ('prepared', 'committed'):
                record['decision'] = decision
                self.store.journal.write_bytes(grids._json(record))
                reads.clear()
                with mock.patch.object(Path, 'read_bytes', autospec=True, side_effect=read):
                    self.assertEqual(self.store._record(), record)
                for name in ('before.DAT', 'before.EXT', 'after.DAT', 'after.EXT'):
                    self.assertEqual(reads.count(self.store.payloads / name), 1)
            self.assertEqual(plan.call_count, 1)
            self.assertEqual(endpoint.call_count, 2)
            # A new owner, including a cold recovery owner, reconstructs fully.
            cold = grids.PilotGridStore(self.authority, self.root)
            self.assertIsNone(cold._record_proof)
            self.assertEqual(cold._record(), record)
            self.assertEqual(plan.call_count, 2)
        self.assertEqual(self.dat_path.read_bytes(), self.dat)
        self.assertEqual(self.ext_path.read_bytes(), self.ext)

    def test_successful_grid_save_reconstructs_once_then_independently_recovers(self):
        request = self.request()
        with mock.patch.object(grids, 'plan_grid_edit', wraps=grids.plan_grid_edit) as plan:
            self.assertTrue(self.store.apply(request))
        self.assertEqual(plan.call_count, 2)  # initial plan plus first journal proof
        self.assertEqual(read_grid(self.dat_path.read_bytes())[0].time_ms,
                         read_grid(self.dat)[0].time_ms + 1)
        self.assertFalse(self.store.journal.exists())

    def test_failed_content_file_semantics_do_not_seed_recovery_proof(self):
        self.enable_manual_bpm()
        record = self.prepared_record('bpm', 9100)
        original = grids._json(record)
        record['content_file_after']['Size'] += 1
        self.store.journal.write_bytes(grids._json(record))
        with self.assertRaisesRegex(offline.RecoveryRequired, 'Grid recovery evidence'):
            self.store._record()
        self.assertIsNone(self.store._record_proof)
        self.store.journal.write_bytes(original)
        with mock.patch.object(grids, 'plan_grid_edit', wraps=grids.plan_grid_edit) as plan:
            self.store._record();self.store._record()
        self.assertEqual(plan.call_count, 1)

    def test_warm_grid_proof_still_rejects_every_changed_archived_payload(self):
        record = self.prepared_record()
        self.store._record()
        for name in ('before.DAT', 'before.EXT', 'after.DAT', 'after.EXT'):
            path = self.store.payloads / name
            original = path.read_bytes()
            with self.subTest(payload=name):
                path.write_bytes(original + b'changed fixture bytes')
                with self.assertRaises(offline.RecoveryRequired):self.store._record()
                # Matching a changed payload hash is still not a valid plan.
                side, role = name.split('.')
                record['files'][role][side] = grids._sha(path.read_bytes())
                self.store.journal.write_bytes(grids._json(record))
                with self.assertRaises((offline.RecoveryRequired, offline.WriteProofError)):
                    self.store._record()
                path.write_bytes(original)
                record['files'][role][side] = grids._sha(original)
                self.store.journal.write_bytes(grids._json(record))
                self.assertEqual(self.store._record(), record)

    def test_warm_grid_proof_binds_request_authority_marker_and_fresh_decision(self):
        record = self.prepared_record();self.store._record()
        original = grids._json(record)
        for change in ('request', 'decision', 'marker'):
            with self.subTest(change=change):
                if change == 'request':record['request']['value'] = 2
                elif change == 'decision':record['decision'] = 'unexpected'
                else:(self.store.payloads / 'operation.json').write_bytes(b'{}')
                self.store.journal.write_bytes(grids._json(record))
                with self.assertRaises(offline.RecoveryRequired):self.store._record()
                record = json.loads(original);self.store.journal.write_bytes(original)
                (self.store.payloads / 'operation.json').write_bytes(
                    grids._json({'operation_id': record['operation_id']}))
        self.store.authority = replace(self.authority, manual_bpm_accepted=True)
        with mock.patch.object(grids, 'plan_grid_edit', wraps=grids.plan_grid_edit) as plan:
            self.assertEqual(self.store._record(), record)
        self.assertEqual(plan.call_count, 1)

    def test_warm_grid_proof_reacquires_pcm_endpoint_before_accepting_a_hit(self):
        audio = self.ambiguous_fixture()
        self.prepared_record('bpm', 13800)
        self.store._record()
        with mock.patch.object(grids, 'plan_grid_edit', wraps=grids.plan_grid_edit) as plan:
            self.store._record()
            plan.assert_not_called()
            audio.touch()
            with self.assertRaisesRegex(offline.RecoveryRequired, 'Audio endpoint identity changed'):
                self.store._record()
            plan.assert_not_called()

    def state(self):
        conn = offline._connect(self.source, readonly=True)
        try:
            return offline._state(conn)
        finally:
            conn.close()

    def test_roundtrip_changes_only_proven_rows_and_sections(self):
        before = self.state()
        self.assertTrue(self.store.apply(self.request()))
        self.assertEqual(self.state(), before)
        self.store.apply(self.request(value=-1))
        self.assertEqual(self.dat_path.read_bytes(), self.dat)
        self.store.apply(self.request('double', 0))
        snap = self.store.snapshot()
        self.assertEqual((snap.track['BPM'], snap.track['TrackInfoUpdated'], snap.track['rb_local_usn']),
                         (18000, '7', 43))
        self.store.apply(self.request('halve', 0))
        self.assertEqual(self.dat_path.read_bytes(), self.dat)
        after = self.state()
        self.assertEqual({t for t in before['tables'] if before['tables'][t] != after['tables'][t]},
                         {'djmdContent', 'agentRegistry'})
        self.assertEqual(self.extra_path.read_bytes(), b'never touch this companion')
        self.assertFalse(self.store.journal.exists())
        self.assertFalse(self.store.payloads.exists())

    def test_independent_recovery_and_unchanged_secondary_create_no_staging_writes(self):
        original = self.store._copy_for_publish
        with mock.patch.object(self.store, '_copy_for_publish', wraps=original) as stage:
            self.assertTrue(self.store.apply(self.request()))
            self.assertEqual([call.args[0] for call in stage.call_args_list], ['DAT', 'EXT'])
            stage.reset_mock()
            before = self.ext_path.stat()
            self.assertTrue(self.store.apply(self.request()))
            # The primary changes once. Its independent recovery readback and
            # both unchanged secondary publications retain their exact bytes.
            self.assertEqual([call.args[0] for call in stage.call_args_list], ['DAT'])
            after = self.ext_path.stat()
            self.assertEqual((before.st_ino, before.st_mode, before.st_mtime_ns),
                             (after.st_ino, after.st_mode, after.st_mtime_ns))

    def test_noop_publication_preserves_source_and_checks_all_leftover_evidence(self):
        self.store.payloads.mkdir()
        info = self.dat_path.stat()
        meta = dict(path=str(self.dat_path), mode=info.st_mode & 0o777,
                    mtime_ns=info.st_mtime_ns, before=grids._sha(self.dat), after=grids._sha(self.dat))
        record = {'files': {'DAT': meta}}
        staged = self.store.payloads / 'publish.DAT'
        for leftover in (None, self.dat):
            with self.subTest(leftover=leftover is not None):
                if leftover is not None:
                    staged.write_bytes(leftover)
                with mock.patch.object(grids, 'atomic_bytes', wraps=grids.atomic_bytes) as publish, \
                        mock.patch('os.fsync', wraps=os.fsync) as sync:
                    self.store._publish('DAT', self.dat, record)
                publish.assert_not_called()
                sync.assert_not_called()
                self.assertFalse(staged.exists())
        after = self.dat_path.stat()
        self.assertEqual((info.st_ino, info.st_mode, info.st_mtime_ns),
                         (after.st_ino, after.st_mode, after.st_mtime_ns))
        staged.write_bytes(b'unknown recovery bytes')
        with self.assertRaisesRegex(offline.RecoveryRequired, 'Unknown grid publication'):
            self.store._publish('DAT', self.dat, record)
        self.assertEqual(staged.read_bytes(), b'unknown recovery bytes')
        staged.unlink()
        staged.symlink_to(self.extra_path)
        with self.assertRaises(offline.WriteProofError):
            self.store._publish('DAT', self.dat, record)
        self.assertEqual(self.extra_path.read_bytes(), b'never touch this companion')
        staged.unlink()
        for values in (dict(mode=True), dict(mode=0o1000), dict(mtime_ns=True), dict(mtime_ns='invalid')):
            with self.subTest(values=values), self.assertRaises(offline.RecoveryRequired):
                self.store._publish('DAT', self.dat, {'files': {'DAT': {**meta, **values}}})
        with self.assertRaises((OverflowError, OSError)):
            self.store._publish('DAT', self.dat, {'files': {'DAT': {**meta, 'mtime_ns': 10**100}}})
        # A metadata mismatch still exercises the old native staging checks and
        # leaves the already-equal source's metadata alone.
        with mock.patch.object(self.store, '_copy_for_publish', wraps=self.store._copy_for_publish) as copy:
            different_mode = (info.st_mode & 0o777) ^ 0o040
            self.store._publish('DAT', self.dat, {'files': {'DAT': {**meta, 'mode': different_mode}}})
        copy.assert_called_once()
        self.assertEqual(self.dat_path.stat().st_mode, info.st_mode)
        self.assertEqual(self.dat_path.read_bytes(), self.dat)

    def test_publication_rechecks_changed_target_after_private_staging(self):
        self.store.payloads.mkdir()
        info = self.dat_path.stat()
        desired = self.dat + b'new private fixture bytes'
        record = {'files': {'DAT': dict(path=str(self.dat_path), mode=info.st_mode & 0o777,
            mtime_ns=info.st_mtime_ns, before=grids._sha(self.dat), after=grids._sha(desired))}}
        original = self.store._copy_for_publish
        def race(*args):
            staged = original(*args)
            self.dat_path.write_bytes(b'foreign source replacement')
            return staged
        with mock.patch.object(self.store, '_copy_for_publish', side_effect=race), \
                mock.patch.object(self.store.lease, '_guard'), \
                self.assertRaises(offline.RecoveryRequired):
            self.store._publish('DAT', desired, record)
        self.assertEqual(self.dat_path.read_bytes(), b'foreign source replacement')
        self.assertTrue((self.store.payloads / 'publish.DAT').exists())

    def test_manual_bpm_requires_separate_acceptance_and_recovers_new_metadata(self):
        before = self.store.snapshot()
        with self.assertRaisesRegex(offline.WriteProofError, 'separate native acceptance'):
            self.store.apply(self.request('bpm', 9100))
        self.assertEqual(self.store.snapshot(), before)
        self.enable_manual_bpm()
        before = self.store.snapshot()
        for phase in ('published-DAT', 'after-commit'):
            self.crash('bpm', phase, wal=True)
            self.store = grids.PilotGridStore(self.authority, self.root)
            self.assertEqual(self.store.recover(),
                             'committed' if phase == 'after-commit' else 'rolled-back')
            current = self.store.snapshot()
            if phase == 'published-DAT':
                self.assertEqual(current, before)
            else:
                self.assertEqual((current.track['BPM'], current.track['AnalysisUpdated'],
                                  current.track['TrackInfoUpdated'], current.track['rb_local_usn']),
                                 (9100, '3', '7', 45))
        self.assertEqual(self.extra_path.read_bytes(), b'never touch this companion')

    def enable_manual_bpm(self):
        before = self.store.snapshot()
        conn = offline._connect(self.source)
        file = content_file(before.track, before.dat, str(self.dat_path))
        columns = ','.join('"' + k + '"' for k in file)
        conn.execute('CREATE TABLE contentFile (' + columns + ')')
        conn.execute('INSERT INTO contentFile VALUES (' + ','.join('?' for _ in file) + ')',
                     tuple(file.values()))
        schema = tags._schema(conn)
        conn.close()
        self.authority = replace(self.authority, schema_sha256=schema, manual_bpm_accepted=True)
        self.store = grids.PilotGridStore(self.authority, self.root)

    def ambiguous_fixture(self):
        audio = self.base / 'synthetic.wav'
        audio.write_bytes(wave_bytes())
        conn = offline._connect(self.source)
        conn.execute("UPDATE djmdContent SET FolderPath=?, BPM=13801 WHERE ID='2'", (str(audio),))
        conn.close()
        rows = tuple(Beat(i % 4 + 1, 13801, 54 + (i * 6000000 + 6900) // 13801)
                     for i in range(297))
        self.dat = primary(rows)
        self.ext = container(section(b'PQT2', bytes.fromhex('000000000100000200000000') + bytes(32), 56),
                        section(b'PWV3', struct.pack('>III', 1, 19313, 0x960000)
                                     + bytes(19313), 24), section(b'PCOB', b'unchanged cue'))
        self.dat_path.write_bytes(self.dat)
        self.ext_path.write_bytes(self.ext)
        self.enable_manual_bpm()
        return audio

    def test_exact_pcm_eof_save_and_wal_recovery_preserve_audio_and_metadata_contract(self):
        audio = self.ambiguous_fixture()
        original_audio = audio.read_bytes()
        before = self.store.snapshot()
        for phase in ('published-DAT', 'after-commit'):
            self.crash('bpm', phase, wal=True, value=13800)
            self.store = grids.PilotGridStore(self.authority, self.root)
            self.assertEqual(self.store.recover(),
                             'committed' if phase == 'after-commit' else 'rolled-back')
            current = self.store.snapshot()
            if phase == 'published-DAT':
                self.assertEqual(current, before)
            else:
                self.assertEqual(current.track['BPM'], 13800)
                self.assertEqual(read_grid(current.dat)[-1], Beat(1, 13800, 128750))
                self.assertEqual(current.track['rb_local_usn'], before.track['rb_local_usn'] + 4)
                self.assertNotEqual(current.content_file['Hash'], before.content_file['Hash'])
                receipt = json.loads(self.store.completed.read_text())
                self.assertEqual(receipt['exact_pcm_end']['end_ms'], 128750)
                self.assertEqual(receipt['counter_after']['int_1'] - receipt['counter_before']['int_1'], 4)
        self.assertTrue(self.store.apply(self.request('bpm', 13900)))
        self.assertEqual(self.store.snapshot().track['BPM'], 13900)
        self.assertEqual(audio.read_bytes(), original_audio)
        self.assertEqual(self.extra_path.read_bytes(), b'never touch this companion')
        self.assertFalse(self.store.journal.exists())

    def test_changed_pcm_proof_blocks_publication_and_recovery_without_more_writes(self):
        audio = self.ambiguous_fixture()
        before = self.state()
        def changed(phase):
            if phase == 'journal-ready':
                audio.touch()
        with mock.patch.object(grids, '_fault', side_effect=changed):
            with self.assertRaises(offline.RecoveryRequired):
                self.store.apply(self.request('bpm', 13800))
        self.assertEqual(self.state(), before)
        self.assertEqual(self.dat_path.read_bytes(), self.dat)
        self.assertEqual(self.ext_path.read_bytes(), self.ext)
        with self.assertRaises(offline.RecoveryRequired):
            self.store.recover()
        self.assertTrue(self.store.journal.exists())

    def test_ordinary_bpm_does_not_probe_audio(self):
        self.enable_manual_bpm()
        with mock.patch.object(grids, 'capture_pcm_endpoint', side_effect=AssertionError('unneeded audio read')):
            self.assertTrue(self.store.apply(self.request('bpm', 9100)))

    def test_stale_identity_and_analysis_lock_fail_without_writes(self):
        edit = self.request()
        for bad in (replace(edit, track_uuid='other'), replace(edit, track_id='1'),
                    replace(edit, revision='stale')):
            with self.subTest(edit=bad), self.assertRaises(offline.WriteProofError):
                self.store.apply(bad)
        conn = offline._connect(self.source)
        conn.execute("UPDATE djmdContent SET Analysed=233 WHERE ID='2'")
        conn.close()
        edit = self.request()
        with self.assertRaisesRegex(offline.WriteProofError, 'Lock'):
            self.store.apply(edit)
        self.assertEqual(self.dat_path.read_bytes(), self.dat)
        self.assertEqual(self.ext_path.read_bytes(), self.ext)

    def crash(self, operation, phase, wal=False, value=None):
        if wal:
            conn = offline._connect(self.source)
            conn.execute('PRAGMA journal_mode=WAL')
            conn.close()
        script = '''
from pathlib import Path
import json,os,sys
from soundbox import rekordbox_cue_store as cues,rekordbox_grid_store as grids
from soundbox.rekordbox_grid import GridEdit
cues._closed=lambda:None
cues._installed_version=lambda:'6.8.7.0303'
authority=grids.GridWriteAuthority(**json.loads(sys.argv[1]))
store=grids.PilotGridStore(authority,Path(sys.argv[2]))
snapshot=store.snapshot()
edit=GridEdit(authority.pilot_track_id,authority.track_uuid,snapshot.revision,sys.argv[3],
              int(sys.argv[5]))
def fault(phase):
    if phase==sys.argv[4]:os._exit(86)
grids._fault=fault
store.apply(edit)
'''
        result = subprocess.run([sys.executable, '-c', script, json.dumps(asdict(self.authority)),
                                 str(self.root), operation, phase,
                                 str(value if value is not None else
                                     1 if operation == 'shift' else 9100 if operation == 'bpm' else 0)],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 86, result.stdout + result.stderr)

    def test_real_exit_recovers_file_only_and_sql_operations(self):
        for operation in ('shift', 'double'):
            for phase in ('journal-ready', 'published-DAT', 'published-EXT', 'before-commit',
                          'after-commit', 'decision-durable', 'journal-retired',
                          'payload-retired', 'marker-retired'):
                with self.subTest(operation=operation, phase=phase):
                    # Reuse one library, recording a new exact baseline each time.
                    original = self.store.snapshot()
                    self.crash(operation, phase)
                    self.store = grids.PilotGridStore(self.authority, self.root)
                    retired = phase in ('journal-retired', 'payload-retired', 'marker-retired')
                    committed = (phase == 'decision-durable' or retired
                                 or operation == 'double' and phase == 'after-commit')
                    result = self.store.recover()
                    if retired:
                        self.assertIsNone(result)
                    else:
                        self.assertEqual(result, 'committed' if committed else 'rolled-back')
                    current = self.store.snapshot()
                    if committed:
                        self.assertNotEqual(current.dat, original.dat)
                    else:
                        self.assertEqual(current, original)
                    # Keep repeated doubles bounded for this matrix.
                    if committed and operation == 'double':
                        self.store.apply(self.request('halve', 0))
                    self.assertFalse(self.store.journal.exists())
                    self.assertFalse(self.store.payloads.exists())

    def test_wal_crashes_recover_both_sides_of_commit(self):
        for phase in ('published-DAT', 'after-commit'):
            with self.subTest(phase=phase):
                original = self.store.snapshot()
                self.crash('double', phase, wal=True)
                self.store = grids.PilotGridStore(self.authority, self.root)
                self.assertEqual(self.store.recover(), 'committed' if phase == 'after-commit' else 'rolled-back')
                if phase == 'published-DAT':
                    self.assertEqual(self.store.snapshot(), original)

    def test_recovery_stops_on_foreign_analysis_before_restoring_any_file(self):
        self.crash('double', 'published-DAT')
        self.ext_path.write_bytes(b'foreign edit')
        current_dat = self.dat_path.read_bytes()
        with self.assertRaises(offline.RecoveryRequired):
            self.store.recover()
        self.assertEqual(self.dat_path.read_bytes(), current_dat)
        self.assertEqual(self.ext_path.read_bytes(), b'foreign edit')
        self.assertTrue(self.store.journal.exists())

    def test_path_redirect_noop_and_process_guard(self):
        self.assertFalse(self.store.apply(self.request(value=0)))
        self.assertFalse(self.root.joinpath('baseline.db').exists())
        with mock.patch.object(cues, '_closed', side_effect=offline.WriteProofError('running')):
            with self.assertRaises(offline.WriteProofError):
                self.store.snapshot()
        self.dat_path.unlink()
        self.dat_path.symlink_to(self.extra_path)
        with self.assertRaises(offline.WriteProofError):
            self.store.snapshot()
        self.assertEqual(self.extra_path.read_bytes(), b'never touch this companion')

    def test_trigger_cannot_change_unrelated_data(self):
        conn = offline._connect(self.source)
        conn.execute("CREATE TRIGGER unsafe_grid AFTER UPDATE OF BPM ON djmdContent BEGIN "
                     "UPDATE unknown_table SET opaque=X'02' WHERE id=1; END")
        self.authority = replace(self.authority, schema_sha256=tags._schema(conn))
        conn.close()
        self.store = grids.PilotGridStore(self.authority, self.root)
        before = self.state()
        with self.assertRaises(sqlcipher.DatabaseError):
            self.store.apply(self.request('double', 0))
        self.assertEqual(self.state(), before)
        self.assertEqual(self.dat_path.read_bytes(), self.dat)
        self.assertEqual(self.store.recover(), 'rolled-back')

    def test_cue_and_tag_domains_refuse_unresolved_grid_journal(self):
        self.crash('shift', 'published-DAT')
        for factory in (lambda: cues.PilotCueStore(cues.CueWriteAuthority(
                            self.authority.database, self.authority.library_id,
                            self.authority.schema_sha256, '2'), self.root).snapshot(),
                        lambda: tags.LiveTagStore(tags.TagWriteAuthority(
                            self.authority.database, self.authority.library_id,
                            self.authority.schema_sha256), self.root).check_ready()):
            with self.assertRaises(offline.WriteProofError):
                factory()
        self.store.recover()
        self.assertEqual(read_grid(self.dat_path.read_bytes()), read_grid(self.dat))

    def test_damaged_backup_and_changed_database_block_recovery(self):
        self.crash('double', 'published-DAT')
        before = self.dat_path.read_bytes(), self.ext_path.read_bytes()
        saved = self.root / 'grid-transaction/before.DAT'
        saved.write_bytes(b'broken backup')
        with self.assertRaises(offline.RecoveryRequired):
            self.store.recover()
        self.assertEqual((self.dat_path.read_bytes(), self.ext_path.read_bytes()), before)
        self.assertTrue(self.store.journal.exists())

    def test_missing_journal_preserves_unfinished_evidence_and_blocks_all_domains(self):
        with mock.patch.object(grids, 'atomic_bytes', wraps=grids.atomic_bytes) as publish:
            original = publish._mock_wraps
            def write(path, data):
                if path == self.store.journal:
                    raise OSError('journal unavailable')
                original(path, data)
            publish.side_effect = write
            with self.assertRaises(OSError):
                self.store.apply(self.request('double', 0))
        self.assertEqual(self.dat_path.read_bytes(), self.dat)
        self.assertTrue(self.store.payloads.exists())
        with self.assertRaises(offline.RecoveryRequired):
            self.store.snapshot()
        cue_store = cues.PilotCueStore(cues.CueWriteAuthority(
            self.authority.database, self.authority.library_id, self.authority.schema_sha256, '2'), self.root)
        with self.assertRaises(offline.RecoveryRequired):
            cue_store.snapshot()

    def test_native_version_and_sync_metadata_are_not_inferred_from_cache(self):
        with mock.patch.object(cues, '_installed_version', return_value='new version'):
            with self.assertRaises(offline.WriteProofError):
                self.store.snapshot()
        conn = offline._connect(self.source)
        conn.execute('ALTER TABLE djmdContent ADD COLUMN rb_local_synced INTEGER DEFAULT 0')
        conn.execute("UPDATE djmdContent SET rb_local_synced=2 WHERE ID='2'")
        self.authority = replace(self.authority, schema_sha256=tags._schema(conn))
        conn.close()
        self.store = grids.PilotGridStore(self.authority, self.root)
        with self.assertRaisesRegex(offline.WriteProofError, 'synchronization'):
            self.store.snapshot()
        self.assertEqual(self.dat_path.read_bytes(), self.dat)

    def test_native_historical_sync_flag_survives_bpm_edit(self):
        conn = offline._connect(self.source)
        conn.execute('ALTER TABLE djmdContent ADD COLUMN rb_local_synced INTEGER DEFAULT 0')
        conn.execute("UPDATE djmdContent SET rb_local_synced=1 WHERE ID='2'")
        self.authority = replace(self.authority, schema_sha256=tags._schema(conn))
        conn.close()
        self.store = grids.PilotGridStore(self.authority, self.root)
        self.store.apply(self.request('double', 0))
        self.assertEqual(self.store.snapshot().track['rb_local_synced'], 1)


if __name__ == '__main__':
    unittest.main()
