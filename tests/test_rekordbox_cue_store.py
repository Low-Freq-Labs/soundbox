"""Pilot cue transactions against synthetic encrypted databases only."""
from dataclasses import asdict, replace
from itertools import product
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from soundbox import rekordbox_cue_store as store_module
from soundbox import rekordbox_cues as cues
from soundbox import rekordbox_tags as tags
from soundbox import rekordbox_write as offline
from tests.test_rekordbox_write import _fixture


def fixture(path):
    conn = _fixture(path)
    for name, kind in (('FileType', 'INTEGER'), ('Length', 'INTEGER'),
                       ('CueUpdated', 'TEXT'), ('rb_local_usn', 'INTEGER'), ('updated_at', 'TEXT')):
        conn.execute(f'ALTER TABLE djmdContent ADD COLUMN {name} {kind}')
    conn.execute("UPDATE djmdContent SET FileType=11,Length=100,FolderPath='/synthetic/audio'")
    conn.execute('ALTER TABLE djmdCue RENAME TO original_cue_fixture')
    integers = {'InMsec', 'InFrame', 'InMpegFrame', 'InMpegAbs', 'OutMsec', 'OutFrame',
                'OutMpegFrame', 'OutMpegAbs', 'Kind', 'Color', 'ColorTableIndex', 'ActiveLoop',
                'BeatLoopSize', 'CueMicrosec', 'rb_data_status', 'rb_local_data_status',
                'rb_local_deleted', 'rb_local_synced', 'usn', 'rb_local_usn', 'rb_cue_count'}
    for table, columns in (('djmdCue', cues._CUE_COLUMNS), ('contentCue', cues._AGGREGATE_COLUMNS)):
        definitions = ','.join('"' + k + '" ' + ('INTEGER' if k in integers else 'TEXT')
                               + (' PRIMARY KEY' if k == 'ID' else '') for k in columns)
        conn.execute(f'CREATE TABLE {table} ({definitions})')
    conn.execute('INSERT INTO djmdCue (ID,ContentID,Kind,Comment) SELECT ID,ContentID,Kind,Comment FROM original_cue_fixture')
    conn.execute('DROP TABLE original_cue_fixture')
    return conn


class PilotCueTransactions(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.source = self.root / 'source' / 'master.db'
        self.source.parent.mkdir()
        conn = fixture(self.source)
        self.authority = store_module.CueWriteAuthority(str(self.source), 'synthetic-library',
                                                        tags._schema(conn), '2')
        conn.close()
        self.enterContext(mock.patch.object(store_module, '_closed'))
        self.enterContext(mock.patch.object(store_module, '_installed_version', return_value=tags.NATIVE_VERSION))
        self.enterContext(mock.patch.object(tags, '_assert_closed'))
        self.enterContext(mock.patch.object(tags, '_installed_version', return_value=tags.NATIVE_VERSION))
        self.store = store_module.PilotCueStore(self.authority, self.root / 'recovery')

    def connect(self):
        return offline._connect(self.source)

    def state(self):
        conn = self.connect()
        try:
            return offline._state(conn)
        finally:
            conn.close()

    def request(self, **kwargs):
        state = self.store.snapshot()
        edit = cues.CueEdit('2', 'track-two', state.revision, 'hot', 0, 'add', 9449)
        return replace(edit, **kwargs)

    def remove(self, page='hot', slot=0):
        state = self.store.snapshot()
        row = cues.cue_at_slot(state, page, slot)
        return cues.CueEdit('2', 'track-two', state.revision, page, slot, 'delete', None,
                            row['ID'] if row else '', row['UUID'] if row else '')

    def test_add_delete_exact_tables_and_native_fields(self):
        before = self.state()
        self.assertTrue(self.store.apply(self.request()))
        after = self.state()
        self.assertEqual({t for t in before['tables'] if before['tables'][t] != after['tables'][t]},
                         {'djmdContent', 'djmdCue', 'contentCue', 'agentRegistry'})
        state = self.store.snapshot()
        self.assertEqual(state.cues[0]['InMsec'], 9449)
        self.assertEqual(state.cues[0]['InFrame'], 1417)
        self.assertIsNone(state.cues[0]['rb_local_usn'])
        self.assertEqual(state.track['CueUpdated'], '1')
        self.assertEqual(state.aggregate['rb_local_usn'], 43)
        self.assertTrue(self.store.apply(self.remove()))
        state = self.store.snapshot()
        self.assertEqual(state.cues, ())
        self.assertEqual(state.aggregate['Cues'], '[]')
        self.assertEqual(state.track['CueUpdated'], '2')
        self.assertFalse(self.store.journal.exists())
        self.assertTrue((self.store.root / 'baseline.db').is_file())

    def test_library_authority_requires_acceptance_and_exact_source_identity(self):
        path = self.root / 'cue-authority.json'
        authority = store_module.CueLibraryAuthority(self.authority.database,
            self.authority.library_id, self.authority.schema_sha256, True)
        path.write_text(json.dumps(asdict(authority)))
        got = store_module.load_cue_authority(path, self.source, self.authority.library_id)
        self.assertEqual(got.bind('2'), self.authority)
        self.assertIsNone(store_module.load_cue_authority(path, self.source, 'another-library'))
        self.assertIsNone(store_module.load_cue_authority(path, self.source.parent / 'other.db',
                                                         self.authority.library_id))
        data = asdict(authority)
        data['accepted'] = False
        path.write_text(json.dumps(data))
        self.assertIsNone(store_module.load_cue_authority(path, self.source, self.authority.library_id))

    def test_stale_and_other_track_requests_fail_without_changes(self):
        edit = self.request()
        self.store.apply(edit)
        before = self.state()
        for stale in (edit, replace(edit, track_id='1'), replace(edit, track_uuid='rebound')):
            with self.subTest(stale=stale), self.assertRaises(offline.WriteProofError):
                self.store.apply(stale)
        self.assertEqual(self.state(), before)

    def test_empty_deletion_is_noop_without_backup(self):
        before = self.source.read_bytes()
        self.assertFalse(self.store.apply(self.remove()))
        self.assertEqual(self.source.read_bytes(), before)
        self.assertFalse((self.store.root / 'baseline.db').exists())

    def test_mixed_hot_memory_and_chronological_delete_preserve_other_rows(self):
        self.store.apply(self.request())
        first = self.store.snapshot().cues[0]
        self.store.apply(self.request(page='memory', slot=0, position_ms=19021))
        self.store.apply(self.request(page='memory', slot=1, position_ms=9691))
        self.store.apply(self.remove('memory', 0))
        state = self.store.snapshot()
        self.assertIn(first, state.cues)
        self.assertEqual([r['InMsec'] for r in state.cues if r['Kind'] == 0], [19021])

    def test_version_process_and_schema_guards(self):
        edit = self.request()
        with mock.patch.object(store_module, '_closed', side_effect=offline.WriteProofError('running')):
            with self.assertRaises(offline.WriteProofError):
                self.store.apply(edit)
        with mock.patch.object(store_module, '_installed_version', return_value='other'):
            with self.assertRaises(offline.WriteProofError):
                self.store.apply(edit)
        conn = self.connect()
        conn.execute('CREATE TABLE unsupported_schema (ID TEXT)')
        conn.close()
        with self.assertRaises(offline.ConflictError):
            self.store.apply(edit)

    def test_backup_failure_prevents_all_mutations(self):
        edit = self.request()
        before = self.state()
        with mock.patch.object(self.store, '_backup', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.store.apply(edit)
        self.assertEqual(self.state(), before)

    def test_precommit_and_postcommit_errors_recover_exactly(self):
        for phase in ('journal-ready', 'before-commit', 'after-commit'):
            with self.subTest(phase=phase):
                edit = self.request()
                def fault(current):
                    if current == phase:
                        raise OSError('injected')
                with mock.patch.object(store_module, '_fault', fault), self.assertRaises(OSError):
                    self.store.apply(edit)
                self.assertTrue(self.store.journal.exists())
                state = self.store.snapshot()  # independently reconcile, do not retry stale request
                self.assertEqual(bool(state.cues), phase == 'after-commit')
                self.assertFalse(self.store.journal.exists())
                if state.cues:
                    self.store.apply(self.remove())

    def test_uncertain_result_blocks_both_cue_and_tag_domains(self):
        edit = self.request()
        def fault(phase):
            if phase == 'after-commit':
                raise OSError('injected')
        with mock.patch.object(store_module, '_fault', fault), self.assertRaises(OSError):
            self.store.apply(edit)
        tag_authority = tags.TagWriteAuthority(str(self.source), self.authority.library_id,
                                                self.authority.schema_sha256)
        tag_store = tags.LiveTagStore(tag_authority, self.store.root)
        with self.assertRaises(offline.RecoveryRequired):
            tag_store.check_ready()
        conn = self.connect()
        conn.execute("UPDATE djmdContent SET UUID='rebound' WHERE ID='2'")
        conn.close()
        before = self.state()
        with self.assertRaises(offline.RecoveryRequired):
            self.store.snapshot()
        self.assertEqual(self.state(), before)
        self.assertTrue(self.store.journal.exists())

    def test_tag_journal_blocks_cue_connection_before_recovery(self):
        tag_authority = tags.TagWriteAuthority(str(self.source), self.authority.library_id,
                                                self.authority.schema_sha256)
        tag_store = tags.LiveTagStore(tag_authority, self.store.root)
        request = tags.TagWriteRequest(self.authority.library_id,
            (tags.SelectedTagTrack('2', 'track-two', False),), '11', 'tag-dubstep', True, 'Dubstep')
        def fault(phase):
            if phase == 'after-commit':
                raise OSError('injected')
        with mock.patch.object(tags, '_fault', fault), self.assertRaises(OSError):
            tag_store.apply(request)
        with mock.patch.object(self.store, '_connect') as connection:
            with self.assertRaises(offline.RecoveryRequired):
                self.store.snapshot()
            connection.assert_not_called()

    def test_shared_lock_excludes_other_store(self):
        other = store_module.PilotCueStore(self.authority, self.store.root)
        with self.store._lock(), self.assertRaises(offline.WriteProofError):
            other.snapshot()

    def test_damaged_recovery_backup_is_never_used(self):
        edit = self.request()
        def fault(phase):
            if phase == 'after-commit':
                raise OSError('injected')
        with mock.patch.object(store_module, '_fault', fault), self.assertRaises(OSError):
            self.store.apply(edit)
        (self.store.root / 'baseline.db').write_bytes(b'damaged')
        before = self.state()
        with self.assertRaises(offline.RecoveryRequired):
            self.store.snapshot()
        self.assertEqual(self.state(), before)
        self.assertTrue(self.store.journal.exists())

    def test_recovery_delta_with_another_target_is_preserved_and_blocked(self):
        edit = self.request()
        def fault(phase):
            if phase == 'before-commit':
                raise OSError('injected')
        with mock.patch.object(store_module, '_fault', fault), self.assertRaises(OSError):
            self.store.apply(edit)
        record = json.loads(self.store.journal.read_bytes())
        next(d for d in record['changes'] if d['table'] == 'djmdContent')['key'] = '1'
        self.store.journal.write_text(json.dumps(record))
        with self.assertRaises(offline.RecoveryRequired):
            self.store.snapshot()
        self.assertTrue(self.store.journal.exists())

    def test_replaced_source_file_is_blocked_before_sqlite_recovery_opens_it(self):
        edit = self.request()
        def fault(phase):
            if phase == 'before-commit':
                raise OSError('injected')
        with mock.patch.object(store_module, '_fault', fault), self.assertRaises(OSError):
            self.store.apply(edit)
        replacement = self.source.with_name('replacement.db')
        conn = fixture(replacement)
        conn.close()
        replacement.replace(self.source)
        before = self.source.read_bytes()
        with mock.patch.object(self.store, '_connect') as connection:
            with self.assertRaises(offline.RecoveryRequired):
                self.store.snapshot()
            connection.assert_not_called()
        self.assertEqual(self.source.read_bytes(), before)
        self.assertTrue(self.store.journal.exists())

    def test_redirect_and_unowned_hot_journal_are_rejected(self):
        edit = self.request()
        redirected = self.source.with_name('alias.db')
        redirected.symlink_to(self.source)
        store = store_module.PilotCueStore(replace(self.authority, database=str(redirected)), self.root / 'alias-recovery')
        with self.assertRaises(offline.WriteProofError):
            store.apply(edit)
        hot = self.source.with_name('master.db-journal')
        hot.write_bytes(b'unowned')
        with self.assertRaises(offline.RecoveryRequired):
            self.store.apply(edit)

    def test_authorizer_rejects_trigger_side_effects(self):
        conn = self.connect()
        conn.execute("CREATE TRIGGER bad AFTER INSERT ON djmdCue BEGIN UPDATE djmdContent SET UUID='oops' WHERE ID='1'; END")
        authority = replace(self.authority, schema_sha256=tags._schema(conn))
        conn.close()
        store = store_module.PilotCueStore(authority, self.store.root)
        state = store.snapshot()
        edit = cues.CueEdit('2', 'track-two', state.revision, 'hot', 0, 'add', 9449)
        before = self.state()
        with self.assertRaises(Exception):
            store.apply(edit)
        self.assertEqual(self.state(), before)
        self.assertEqual(store.snapshot().cues, ())

    def test_real_process_exit_recovers_before_and_after_commit(self):
        for mode, phase in product(('delete', 'wal', 'spill'),
                                   ('journal-ready', 'before-commit', 'after-commit')):
            with self.subTest(mode=mode, phase=phase):
                conn = self.connect()
                conn.execute('PRAGMA journal_mode=' + ('WAL' if mode == 'wal' else 'DELETE'))
                conn.close()
                edit = self.request()
                program = '''
import json,os,sys
from soundbox import rekordbox_cue_store as m
from soundbox.rekordbox_cues import CueEdit
from pathlib import Path
m._closed=lambda:None
m._installed_version=lambda:m.NATIVE_VERSION
authority=m.CueWriteAuthority(**json.loads(sys.argv[1]))
store=m.PilotCueStore(authority,Path(sys.argv[2]))
if sys.argv[5]=='spill':
 original=store._connect
 def connection():
  conn=original()
  conn.execute('PRAGMA cache_size=1')
  conn.execute('PRAGMA cache_spill=ON')
  return conn
 store._connect=connection
def fault(phase):
 if phase==sys.argv[4]:os._exit(73)
m._fault=fault
store.apply(CueEdit(**json.loads(sys.argv[3])))
'''
                result = subprocess.run([sys.executable, '-c', program, json.dumps(asdict(self.authority)),
                    str(self.store.root), json.dumps(asdict(edit)), phase, mode], capture_output=True, text=True)
                self.assertEqual(result.returncode, 73, result.stderr)
                state = self.store.snapshot()
                self.assertEqual(bool(state.cues), phase == 'after-commit')
                self.assertFalse(self.store.journal.exists())
                if state.cues:
                    self.store.apply(self.remove())
                # The same receipt also admits a harmless inactive journal to
                # the tag domain; neither domain manually deletes SQLite files.
                tag_authority = tags.TagWriteAuthority(str(self.source), self.authority.library_id,
                                                       self.authority.schema_sha256)
                tags.LiveTagStore(tag_authority, self.store.root).check_ready()
