"""Compact accepted membership composition versus its complete sequential oracle.

Encrypted synthetic TEMP DB/XML only; no native/source discovery or app control.
The reference is the unchanged, unmarked sequential planner, physically replayed
onto a separate DB so final catalog equality alone cannot hide unrelated edits.
"""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
from uuid import UUID

from test_playlist_transaction import fixture
from soundbox import rekordbox_playlist_store as store, rekordbox_cue_store as cues
from soundbox.rekordbox_tags import NATIVE_VERSION, _schema
from soundbox.rekordbox_write import _connect, _rows, _state, ConflictError, WriteProofError

MARKER = 'membership-replay-v1'


class PlaylistMembershipReplay(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix='sb-membership-replay-'))).resolve()
        self.source = self.root/'source'; self.source.mkdir(); fixture(self.source)
        self.database = self.source/'master.db'
        self.xml = (self.source/'masterPlaylists6.xml').read_bytes()
        self.tracks = {
            'track': SimpleNamespace(track_id='track', source_uuid='uuid'),
            'track-two': SimpleNamespace(track_id='track-two', source_uuid='uuid-two'),
        }

    def seed(self, count, *, synced=False, history=True, counter=40):
        conn = _connect(self.database)
        try:
            conn.execute('DELETE FROM djmdSongPlaylist')
            conn.execute("UPDATE agentRegistry SET int_1=? WHERE registry_id='localUpdateCount'", (counter,))
            values = []
            for index in range(count):
                # Alternate ContentID deliberately: selections are occurrences.
                values.append((str(UUID(int=100+index, version=4)), '1000',
                    'track' if index % 2 == 0 else 'track-two', index+1,
                    str(UUID(int=10000+index, version=4)), 0, 0, 0,
                    int(synced and index % 2 == 0), None, 20,
                    f'created-{index}', f'updated-{index}'))
            if history:
                values.extend([
                    (str(UUID(int=500000, version=4)), '1000', 'historical-missing-content', 80,
                     str(UUID(int=500001, version=4)), 2, 0, 1, 1, None, 19, 'history-created', 'history-updated'),
                    (str(UUID(int=500002, version=4)), '9999', 'unrelated-missing-content', 91,
                     str(UUID(int=500003, version=4)), 2, 0, 1, 1, None, 18, 'other-created', 'other-updated'),
                ])
            conn.executemany('INSERT INTO djmdSongPlaylist VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)', values)
            return store.catalog(conn)
        finally:
            conn.close()

    def envelope(self, catalog, removed_indexes, append_indexes):
        rows = [row for row in catalog['members'] if row['PlaylistID']=='1000' and not row['rb_local_deleted']]
        rows.sort(key=lambda row: row['TrackNo'])
        removed = [rows[index] for index in removed_indexes]
        remove = store.request_for(catalog, 'remove', identity='1000',
            tracks=[self.tracks[row['ContentID']] for row in removed], memberships=removed)
        append = store.request_for(catalog, 'append', identity='1000', allow_duplicate=True,
            tracks=[self.tracks[rows[index]['ContentID']] for index in append_indexes])
        # Distinct private clocks expose accidental last-command substitution.
        remove.update(timestamp='2026-11-21 08:01:02.003 +00:00',
                      survivor_timestamp='2026-11-21 08:01:03.004 +00:00', xml_timestamp=1800000000001)
        append.update(timestamp='2026-11-21 08:02:03.004 +00:00',
                      survivor_timestamp='2026-11-21 08:02:04.005 +00:00', xml_timestamp=1800000000002)
        request = store.batch_for(catalog, [remove, append])
        request.pop('plan_encoding', None)
        return request

    def plan(self, request):
        conn = _connect(self.database, readonly=True)
        try:
            return store.build_plan(conn, request, self.xml)
        finally:
            conn.close()

    def replay(self, plan, name):
        path = self.root/name
        shutil.copyfile(self.database, path)
        conn = _connect(path)
        try:
            conn.execute('BEGIN IMMEDIATE')
            store.apply_changes(conn, plan['changes'])
            conn.commit()
            return _state(conn), store.catalog(conn), _rows(conn, 'agentRegistry')
        finally:
            conn.close()
            path.unlink()

    def assert_equivalent(self, request):
        original = deepcopy(request)
        source_bytes = self.database.read_bytes()
        old = self.plan(request)
        marked = {**deepcopy(request), 'plan_encoding': MARKER}
        compact = self.plan(marked)
        self.assertEqual(request, original)
        self.assertEqual(self.database.read_bytes(), source_bytes)
        self.assertEqual(compact['xml'], old['xml'])
        self.assertEqual(compact['catalog'], old['catalog'])
        self.assertEqual(self.replay(compact, 'compact.db'), self.replay(old, 'sequential.db'))
        # One final delta per affected identity is the compact wire contract.
        identities = [(change['table'], change['id']) for change in compact['changes']]
        self.assertEqual(len(identities), len(set(identities)))
        return old, compact

    def test_complete_rows_counter_and_xml_equal_sequential_composition_across_sizes_and_orders(self):
        for count in (1, 2, 3, 7, 19):
            for synced in (False, True):
                cases = [list(reversed(range(count))), list(reversed(range(0, count, 2))),
                         list(reversed(range(count//2, count)))]
                for removed in cases:
                    with self.subTest(count=count, synced=synced, removed=removed):
                        catalog = self.seed(count, synced=synced)
                        self.assert_equivalent(self.envelope(catalog, removed, list(reversed(removed))))

    def test_noncontiguous_survivors_duplicate_occurrences_and_tombstone_history_stay_exact(self):
        catalog = self.seed(9, synced=True)
        request = self.envelope(catalog, [7, 5, 3, 1], [3, 7, 1, 5])
        _, plan = self.assert_equivalent(request)
        before = {row['ID']: row for row in catalog['members']}
        after = {row['ID']: row for row in plan['catalog']['members']}
        for old in catalog['members']:
            if old['rb_local_deleted']:
                self.assertEqual(after[old['ID']], old)
        survivors = [row for row in catalog['members'] if not row['rb_local_deleted'] and row['TrackNo'] % 2]
        for index, row in enumerate(survivors, 1):
            current = after[row['ID']]
            self.assertEqual(current['TrackNo'], index)
            for key in ('ID','UUID','ContentID','created_at','rb_local_synced'):
                self.assertEqual(current[key], before[row['ID']][key])
        self.assertEqual([row['ID'] for row in catalog['members'] if row['rb_local_deleted']],
                         [row['ID'] for row in plan['catalog']['members'] if row['ID'] in before and before[row['ID']]['rb_local_deleted']])

    def test_private_remove_survivor_append_clocks_and_counter_groups_are_not_flattened(self):
        catalog = self.seed(5, synced=True)
        request = self.envelope(catalog, [4, 2], [2, 4])
        _, plan = self.assert_equivalent(request)
        rows = {row['ID']: row for row in plan['catalog']['members']}
        old = sorted((r for r in catalog['members'] if not r['rb_local_deleted']), key=lambda r:r['TrackNo'])
        self.assertEqual(rows[old[4]['ID']]['rb_local_usn'], 41)
        self.assertEqual(rows[old[4]['ID']]['updated_at'], request['requests'][0]['timestamp'])
        self.assertEqual(rows[old[2]['ID']]['rb_local_usn'], 43)
        for index in (0, 1, 3):
            self.assertEqual(rows[old[index]['ID']]['rb_local_usn'], 44)
            self.assertEqual(rows[old[index]['ID']]['updated_at'], request['requests'][0]['survivor_timestamp'])
        appended = plan['catalog']['members'][-2:]
        self.assertEqual([row['rb_local_usn'] for row in appended], [45,46])
        self.assertTrue(all(row['created_at']==request['requests'][1]['timestamp'] for row in appended))

    def test_counter_last_valid_value_and_overflow_match_sequential_rejection(self):
        for counter in (2**63-7, 2**63-6):
            catalog = self.seed(3, counter=counter)
            request = self.envelope(catalog, [2,1], [1,2])
            with self.subTest(counter=counter):
                if counter == 2**63-7:
                    self.assert_equivalent(request)
                else:
                    for candidate in (request, {**request,'plan_encoding':MARKER}):
                        with self.assertRaises((ValueError, WriteProofError)):
                            self.plan(candidate)

    def test_fresh_content_uuid_or_deleted_state_is_checked_without_mutating_source(self):
        for column,value in (('UUID','replacement'), ('rb_local_deleted',1)):
            catalog = self.seed(5)
            request = self.envelope(catalog,[4,2],[2,4])
            conn = _connect(self.database)
            try:
                conn.execute(f'UPDATE djmdContent SET {column}=? WHERE ID=?', (value,'track'))
            finally:
                conn.close()
            before = self.database.read_bytes()
            for candidate in (request, {**request,'plan_encoding':MARKER}):
                with self.assertRaises((ConflictError, WriteProofError, ValueError)):
                    self.plan(candidate)
            self.assertEqual(before,self.database.read_bytes())
            conn = _connect(self.database)
            try:
                conn.execute('UPDATE djmdContent SET UUID=?,rb_local_deleted=0 WHERE ID=?', ('uuid','track'))
            finally:
                conn.close()

    def writer(self):
        for name,value in (('_closed',None),('_installed_version',NATIVE_VERSION)):
            self.enterContext(mock.patch.object(cues,name,return_value=value))
        conn=_connect(self.database,readonly=True)
        try:
            schema=_schema(conn)
        finally:
            conn.close()
        authority=store.PlaylistAuthority(str(self.database),'synthetic-playlists',schema,True)
        return store.PlaylistStore(authority,self.root/'recovery')

    def test_marked_and_unmarked_archives_reconstruct_their_own_exact_change_encoding(self):
        for marked in (False,True):
            with self.subTest(marked=marked):
                other=PlaylistMembershipReplay();other.setUp()
                try:
                    catalog=other.seed(9,synced=True)
                    request=other.envelope(catalog,[8,5,2],[2,8,5])
                    if marked:request['plan_encoding']=MARKER
                    expected=other.plan(request)
                    writer=other.writer();writer.apply(request)
                    operation=writer.root/('playlist-'+request['operation_id'])/'operation.json'
                    record=json.loads(operation.read_bytes())
                    self.assertEqual(record['request'],request)
                    self.assertEqual(record['changes'],expected['changes'])
                    before=other.database.read_bytes(),writer.xml.read_bytes()
                    writer.apply(request)
                    self.assertEqual((other.database.read_bytes(),writer.xml.read_bytes()),before)
                    self.assertIsNotNone(writer.committed_transition(request))
                    forged=deepcopy(request)
                    if marked:forged.pop('plan_encoding')
                    else:forged['plan_encoding']=MARKER
                    with self.assertRaises(WriteProofError):
                        writer.apply(forged)
                    self.assertEqual((other.database.read_bytes(),writer.xml.read_bytes()),before)
                finally:
                    other.doCleanups()

    def test_marker_malformed_or_wrong_composition_is_rejected_before_publication(self):
        catalog=self.seed(7)
        request=self.envelope(catalog,[6,4,2],[2,6,4])
        candidates=[]
        for marker in ('membership-replay-v2',None,True,{},1):
            candidates.append({**deepcopy(request),'plan_encoding':marker})
        for field,value in (('allow_duplicate',False),('playlist_id','9999'),('playlist_uuid',str(UUID(int=900,version=4)))):
            value_request={**deepcopy(request),'plan_encoding':MARKER}
            value_request['requests'][1][field]=value;candidates.append(value_request)
        reversed_children={**deepcopy(request),'plan_encoding':MARKER}
        reversed_children['requests'].reverse();candidates.append(reversed_children)
        wrong_multiset={**deepcopy(request),'plan_encoding':MARKER}
        wrong_multiset['requests'][1]['tracks'].pop();candidates.append(wrong_multiset)
        ascending={**deepcopy(request),'plan_encoding':MARKER}
        ascending['requests'][0]['tracks'].reverse();candidates.append(ascending)
        repeated={**deepcopy(request),'plan_encoding':MARKER}
        repeated['requests'][0]['tracks'][1]=deepcopy(repeated['requests'][0]['tracks'][0])
        candidates.append(repeated)
        before=self.database.read_bytes()
        for index,candidate in enumerate(candidates):
            with self.subTest(candidate=index),self.assertRaises((WriteProofError,ValueError,ConflictError)):
                self.plan(candidate)
        self.assertEqual(self.database.read_bytes(),before)

    def test_append_identity_collisions_include_unrelated_and_deleted_history(self):
        catalog=self.seed(7,synced=True)
        request=self.envelope(catalog,[6,4,2],[2,6,4])
        historical=next(row for row in catalog['members'] if row['PlaylistID']=='9999')
        for field,value in (('id',historical['ID']),('membership_uuid',historical['UUID'])):
            forged={**deepcopy(request),'plan_encoding':MARKER}
            forged['requests'][1]['tracks'][-1][field]=value
            with self.assertRaises((ConflictError,WriteProofError,ValueError)):
                self.plan(forged)
        forged={**deepcopy(request),'plan_encoding':MARKER}
        forged['requests'][1]['tracks'][-1]['id']=forged['requests'][1]['tracks'][0]['membership_uuid']
        with self.assertRaises((ConflictError,WriteProofError,ValueError)):
            self.plan(forged)

    def test_large_2917_entry_upward_move_has_bounded_final_deltas_without_sequential_remove_loop(self):
        count=2917
        catalog=self.seed(count,synced=True)
        request=self.envelope(catalog,list(reversed(range(count-1))),list(range(count-1)))
        request['plan_encoding']=MARKER
        with mock.patch.object(store.planner,'remove',wraps=store.planner.remove) as remove:
            plan=self.plan(request)
        self.assertLessEqual(remove.call_count,1,'full sequential removal must remain confined to the bounded guard probe')
        self.assertLessEqual(len(plan['changes']),count*2+1)
        self.assertEqual(len({(c['table'],c['id']) for c in plan['changes']}),len(plan['changes']))
        active=sorted((row for row in plan['catalog']['members'] if not row['rb_local_deleted']),key=lambda row:row['TrackNo'])
        original=sorted((row for row in catalog['members'] if not row['rb_local_deleted']),key=lambda row:row['TrackNo'])
        self.assertEqual(active[0]['ID'],original[-1]['ID'])
        self.assertEqual([row['TrackNo'] for row in active],list(range(1,count+1)))
        final_counter=next(c['after']['int_1'] for c in plan['changes'] if c['table']=='agentRegistry')
        self.assertEqual(final_counter,40+3*(count-1))
        self.assertLess(len(json.dumps(plan['changes'])),16*1024*1024)

    def test_missing_legacy_xml_and_db_xml_parent_guards_match_old_children(self):
        original_xml=self.xml
        for synced in (0,1):
            catalog=self.seed(5)
            conn=_connect(self.database)
            try:
                conn.execute('UPDATE djmdPlaylist SET rb_local_synced=? WHERE ID=?',(synced,'1000'))
                catalog=store.catalog(conn)
            finally:
                conn.close()
            request=self.envelope(catalog,[4,2],[2,4])
            token=next(line for line in original_xml.splitlines(keepends=True) if b'Id="3E8"' in line)
            self.xml=original_xml.replace(token,b'')
            if synced:
                self.assert_equivalent(request)
            else:
                for candidate in (request,{**request,'plan_encoding':MARKER}):
                    with self.assertRaises((ConflictError,WriteProofError,ValueError)):
                        self.plan(candidate)
            self.xml=original_xml.replace(b'Id="3E8" ParentId="0"',b'Id="3E8" ParentId="3E9"')
            for candidate in (request,{**request,'plan_encoding':MARKER}):
                with self.assertRaises((ConflictError,WriteProofError,ValueError)):
                    self.plan(candidate)
        self.xml=original_xml

    def test_unknown_survivor_sync_or_cloud_metadata_never_bypasses_ordinary_rules(self):
        for field,value in (('rb_data_status',1),('rb_local_synced',2),('usn',17)):
            catalog=self.seed(5)
            conn=_connect(self.database)
            try:
                conn.execute(f'UPDATE djmdSongPlaylist SET {field}=? WHERE TrackNo=2 AND PlaylistID=?',(value,'1000'))
                catalog=store.catalog(conn)
            finally:
                conn.close()
            request=self.envelope(catalog,[4,2],[2,4])
            before=self.database.read_bytes()
            for candidate in (request,{**request,'plan_encoding':MARKER}):
                with self.assertRaises((ConflictError,WriteProofError,ValueError)):
                    self.plan(candidate)
            self.assertEqual(self.database.read_bytes(),before)


class PlaylistCanonicalUUID(unittest.TestCase):
    @staticmethod
    def original(value):
        try:
            return str(UUID(value)) == value
        except (ValueError,TypeError,AttributeError):
            return False

    def test_canonical_uuid_matches_original_stdlib_predicate_for_all_uuid_bit_patterns(self):
        import random
        randomizer=random.Random(20261121)
        values=[str(UUID(int=randomizer.getrandbits(128))) for _ in range(4096)]
        # Explicitly cover every version nibble and all variant bit patterns.
        for version in range(16):
            for variant in range(16):
                values.append(f'01234567-89ab-{version:x}def-{variant:x}123-456789abcdef')
        values.extend(['00000000-0000-0000-0000-000000000000',
                       'ffffffff-ffff-ffff-ffff-ffffffffffff'])
        for value in values:
            with self.subTest(value=value):
                self.assertTrue(self.original(value))
                self.assertIs(store._canonical_uuid(value),True)

    def test_canonical_uuid_matches_original_on_noncanonical_text_and_nonstring_values(self):
        canonical='abcdef01-2345-6789-abcd-ef0123456789'
        values=[canonical.upper(),canonical.title(),'{'+canonical+'}',
                'urn:uuid:'+canonical,canonical.replace('-',''),
                ' '+canonical,canonical+' ',canonical+'\n',canonical+'\r\n',
                '\t'+canonical,canonical+'\0',canonical[:8]+'\0'+canonical[9:],
                canonical.replace('a','а'),canonical.replace('1','１'),
                canonical.replace('-','−'),canonical[:-1],canonical+'0',
                '',None,False,True,0,1,1.5,canonical.encode(),bytearray(canonical.encode()),
                [],{},UUID(canonical)]
        class Text(str):
            pass
        values.append(Text(canonical))
        for value in values:
            with self.subTest(value=repr(value),kind=type(value)):
                self.assertEqual(store._canonical_uuid(value),self.original(value))
