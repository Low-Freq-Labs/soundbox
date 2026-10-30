"""Synthetic native membership protocol, without a live writer or discovery."""
from copy import deepcopy
import sqlite3
import unittest

from research.cdj_export import playlist_membership as proof
from research.cdj_export import playlist_lifecycle as lifecycle
from research.cdj_export.playlist_reference import compare_rows
from research.cdj_export.playlist_replay import _apply, _reference_differences
from soundbox.rekordbox_write import _state


class NativeMembershipProof(unittest.TestCase):
    def setUp(self):
        self.playlist = dict(ID='playlist',Attribute=0,SmartList=None,rb_data_status=0,
            rb_local_data_status=0,rb_local_deleted=0,rb_local_synced=0,usn=None)
        self.counter = dict(registry_id='localUpdateCount',int_1=40,updated_at='unchanged',opaque='keep')
        self.rows = []
        for i in range(2):
            plan = proof.append(self.playlist,self.rows,self.counter,dict(ID=str(i),UUID='content'+str(i)),
                identity=f'00000000-0000-4000-8000-{i:012}',uuid=f'10000000-0000-4000-8000-{i:012}',timestamp='created')
            self.rows,self.counter = plan['members'],plan['counter']

    def test_remove_physically_deletes_and_renumbers_without_changing_survivor_identity(self):
        baseline = deepcopy(self.rows)
        result = proof.remove(self.playlist,self.rows,self.counter,self.rows[0]['ID'],timestamp='updated')
        self.assertEqual(self.rows,baseline)
        self.assertEqual(len(result['members']),1)
        survivor = result['members'][0]
        self.assertEqual((survivor['ID'],survivor['UUID'],survivor['created_at']),
                         (baseline[1]['ID'],baseline[1]['UUID'],'created'))
        self.assertEqual((survivor['TrackNo'],survivor['rb_local_usn']),(1,44))
        self.assertEqual(result['counter'],{**self.counter,'int_1':44})
        deleted = next(c for c in result['changes'] if c['id']==baseline[0]['ID'])
        self.assertIsNone(deleted['after']);self.assertFalse(result['source_write_authority'])

    def test_unknown_sync_and_nonordinary_playlists_fail_closed(self):
        for row in ({**self.playlist,'Attribute':4},{**self.playlist,'rb_local_synced':1}):
            with self.assertRaises(ValueError):proof.remove(row,self.rows,self.counter,self.rows[0]['ID'],timestamp='x')
        rows = deepcopy(self.rows);rows[1]['usn']=7
        with self.assertRaises(ValueError):proof.remove(self.playlist,rows,self.counter,rows[0]['ID'],timestamp='x')

    def test_reference_diff_reports_deletion_and_survivor_fields_separately(self):
        result = proof.remove(self.playlist,self.rows,self.counter,self.rows[0]['ID'],timestamp='updated')
        diff = compare_rows({'djmdSongPlaylist':self.rows},{'djmdSongPlaylist':result['members']})
        self.assertIsNone(diff['djmdSongPlaylist'][0]['after'])
        self.assertEqual(diff['djmdSongPlaylist'][1]['fields'],['TrackNo','rb_local_usn','updated_at'])


class NativePlaylistLifecycleProof(unittest.TestCase):
    def setUp(self):
        self.counter = dict(registry_id='localUpdateCount', int_1=41, updated_at='keep')
        self.identity = '123456789'
        self.uuid = '00000000-0000-4000-8000-000000000001'

    def create(self, rows=()):
        return lifecycle.create(list(rows), self.counter, identity=self.identity,
            uuid=self.uuid, name='Untitled Playlist', timestamp='created')

    def test_creation_ignores_deleted_sequence_history_and_preserves_inputs(self):
        rows = [dict(ID='1', ParentID='root', Seq=1, rb_local_deleted=0),
                dict(ID='2', ParentID='root', Seq=9, rb_local_deleted=1)]
        before = deepcopy(rows)
        plan = self.create(rows)
        self.assertEqual(rows, before)
        self.assertEqual(plan['playlist']['Seq'], 2)
        self.assertEqual(plan['playlist']['rb_local_usn'], 42)
        self.assertEqual(plan['playlist']['created_at'], plan['playlist']['updated_at'])
        self.assertEqual(plan['counter'], {**self.counter, 'int_1':42})
        self.assertFalse(plan['source_write_authority'])

    def test_creation_rejects_identity_reuse_and_ambiguous_sibling_order(self):
        for rows in ([dict(ID=self.identity, rb_local_deleted=1)],
                     [dict(UUID=self.uuid, rb_local_deleted=1)],
                     [dict(ID='2', ParentID='root', Seq=2, rb_local_deleted=0)]):
            with self.assertRaises(ValueError):self.create(rows)

    def test_rename_preserves_identity_membership_independent_fields_and_opaque_metadata(self):
        created = self.create()
        row = {**created['playlist'], 'opaque':'preserve'}
        before = deepcopy(row)
        plan = lifecycle.rename(row, created['counter'], name='Bridge Test', timestamp='renamed')
        self.assertEqual(row, before)
        changes = compare_rows({'djmdPlaylist':[row]}, {'djmdPlaylist':[plan['playlist']]})
        self.assertEqual(changes['djmdPlaylist'][0]['fields'], ['Name','rb_local_usn','updated_at'])
        self.assertEqual(plan['counter']['int_1'], 43)
        self.assertEqual(plan['playlist']['opaque'], 'preserve')

    def test_rename_rejects_unproven_sync_types_and_unchanged_edits(self):
        created = self.create()
        for updates in ({'rb_local_synced':1}, {'Attribute':1}, {'usn':7},
                        {'SmartList':'rule'}, {'Name':'Bridge Test'}):
            with self.assertRaises(ValueError):
                lifecycle.rename({**created['playlist'], **updates}, created['counter'],
                    name='Bridge Test', timestamp='renamed')

    def test_single_member_last_root_deletion_preserves_collection_and_inputs(self):
        created = self.create(); row = created['playlist']
        member = dict(ID='member', PlaylistID=row['ID'], ContentID='collection-track', TrackNo=1,
            rb_data_status=0, rb_local_data_status=0, rb_local_deleted=0, rb_local_synced=0, usn=None)
        before = deepcopy([row, member])
        plan = lifecycle.delete_last([row], [member], created['counter'], identity=row['ID'])
        self.assertEqual([row, member], before)
        self.assertEqual(plan['counter']['int_1'], 44)
        self.assertEqual([c['table'] for c in plan['changes']],
            ['djmdSongPlaylist', 'djmdPlaylist', 'agentRegistry'])
        self.assertTrue(all(c['after'] is None for c in plan['changes'][:2]))

    def test_deletion_rejects_unobserved_member_counts_and_sibling_renumbering(self):
        created = self.create(); row = created['playlist']
        member = dict(ID='member', PlaylistID=row['ID'], TrackNo=1, rb_data_status=0,
            rb_local_data_status=0, rb_local_deleted=0, rb_local_synced=0, usn=None)
        for members in ([], [member, {**member, 'ID':'other', 'TrackNo':2}],
                        [{**member, 'rb_local_synced':1}]):
            with self.assertRaises(ValueError):
                lifecycle.delete_last([row], members, created['counter'], identity=row['ID'])
        sibling = {**row, 'ID':'999', 'Seq':2}
        with self.assertRaises(ValueError):
            lifecycle.delete_last([row, sibling], [member], created['counter'], identity=row['ID'])


class NativePlaylistReplayGuards(unittest.TestCase):
    def test_stale_row_and_unknown_table_cannot_mutate_a_copy(self):
        with sqlite3.connect(':memory:') as conn:
            conn.execute('CREATE TABLE djmdPlaylist (ID TEXT PRIMARY KEY, Name TEXT)')
            conn.execute("INSERT INTO djmdPlaylist VALUES ('1', 'current')")
            for table, before in (('djmdPlaylist', dict(ID='1', Name='stale')),
                                  ('djmdContent', None)):
                with self.assertRaises(ValueError):
                    _apply(conn, [dict(table=table, id='1', before=before,
                        after=dict(ID='1', Name='replacement'))])
            self.assertEqual(conn.execute('SELECT * FROM djmdPlaylist').fetchall(), [('1','current')])

    def test_unknown_columns_are_rejected_and_partial_transaction_rolls_back(self):
        with sqlite3.connect(':memory:') as conn:
            conn.execute('CREATE TABLE djmdPlaylist (ID TEXT PRIMARY KEY, Name TEXT)')
            conn.execute("INSERT INTO djmdPlaylist VALUES ('1', 'before')")
            conn.commit()
            conn.execute('BEGIN IMMEDIATE')
            with self.assertRaises(ValueError):
                _apply(conn, [
                    dict(table='djmdPlaylist', id='1', before=dict(ID='1', Name='before'),
                         after=dict(ID='1', Name='changed')),
                    dict(table='djmdPlaylist', id='2', before=None,
                         after=dict(ID='2', Name='new', unknown='unproven'))])
            conn.rollback()
            self.assertEqual(conn.execute('SELECT * FROM djmdPlaylist').fetchall(), [('1','before')])

    def test_notification_clock_is_an_explicit_audited_difference_not_complete_match(self):
        with sqlite3.connect(':memory:') as conn:
            conn.execute('CREATE TABLE agentRegistry (registry_id TEXT PRIMARY KEY, updated_at TEXT, int_1 INTEGER)')
            conn.execute("INSERT INTO agentRegistry VALUES ('notificationNextFetch', 'baseline', 4)")
            baseline = _state(conn)
            reference = dict(registry_id='notificationNextFetch', updated_at='later', int_1=4)
            conn.execute("UPDATE agentRegistry SET updated_at='later'")
            expected = _state(conn)
            conn.execute("UPDATE agentRegistry SET updated_at='baseline'")
            with self.assertRaises(ValueError):
                _reference_differences(conn, expected, {'agentRegistry':[reference]})
            differences = _reference_differences(conn, expected, {'agentRegistry':[reference]},
                audit_notification_timestamp=True)
            self.assertEqual(differences[0]['fields'], ['updated_at'])
            self.assertEqual(_state(conn), baseline)
            with self.assertRaises(ValueError):
                _reference_differences(conn, expected, {'agentRegistry':[{**reference, 'int_1':5}]},
                    audit_notification_timestamp=True)
            with self.assertRaises(ValueError):
                _apply(conn, [dict(table='agentRegistry', id='notificationNextFetch', before=None, after=reference)])
