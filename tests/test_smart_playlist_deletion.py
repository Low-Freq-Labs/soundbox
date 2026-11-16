"""Unaccepted smart deletion uses generated TEMP context, never inferred SQL."""
from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from test_playlist_transaction import fixture
from soundbox import rekordbox_playlist_plan as plan, rekordbox_playlist_store as store
from soundbox import rekordbox_playlist_xml as xml, rekordbox_cue_store as cues
from soundbox.rekordbox_tags import _schema, NATIVE_VERSION
from soundbox.rekordbox_write import _connect, _rows, _state, ConflictError, WriteProofError


def native_uuid(number):
    return f'00000000-0000-4000-8000-{number:012d}'


class SmartPlaylistDeletion(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.source = self.root / 'source'; self.source.mkdir(); fixture(self.source)
        self.database = self.source / 'master.db'; self.xml = self.source / 'masterPlaylists6.xml'
        self.rule = '<NODE Id="2000" LogicalOperator="2" AutomaticUpdate="1"><OPAQUE Keep="exact"/></NODE>'
        conn = _connect(self.database)
        try:
            template = _rows(conn, 'djmdPlaylist')[0]
            for number, attribute in ((2000, 4), (3000, 0)):
                row = {**template, 'ID': str(number), 'UUID': native_uuid(number),
                       'Seq': number // 1000, 'Attribute': attribute,
                       'SmartList': self.rule if attribute == 4 else None}
                columns = list(row)
                conn.execute(f'INSERT INTO djmdPlaylist ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})',
                             [row[key] for key in columns])
            conn.commit(); self.catalog = store.catalog(conn); schema = _schema(conn)
        finally:
            conn.close()
        payload = xml.append_smart_node(self.xml.read_bytes(), '2000', timestamp=200)
        self.xml.write_bytes(xml.append_node(payload, '3000'))
        self.authority = store.PlaylistAuthority(str(self.database), 'synthetic-playlists', schema, True,
            smart_create_accepted=True, smart_create_profile=store.SMART_CREATE_PROFILE)
        self.writer = store.PlaylistStore(self.authority, self.root / 'recovery')
        for name, value in (('_closed', None), ('_installed_version', NATIVE_VERSION)):
            self.enterContext(mock.patch.object(cues, name, return_value=value))

    def request(self):
        return store.request_for(self.catalog, 'delete_smart', identity='2000')

    def test_context_is_detached_and_does_not_invent_native_deletion_effects(self):
        request = self.request(); original = deepcopy(request)
        before = self.database.read_bytes(), self.xml.read_bytes()
        conn = _connect(self.database, readonly=True)
        try:
            proposal = store.smart_deletion_proposal(conn, request, self.xml.read_bytes())
        finally:
            conn.close()
        self.assertFalse(proposal['source_write_authority'])
        self.assertTrue(proposal['complete_sql_plan'])
        self.assertEqual(proposal['counter']['int_1'], 41)
        self.assertEqual(proposal['changes'][0]['before']['SmartList'], self.rule)
        self.assertIsNone(proposal['changes'][0]['after'])
        self.assertEqual(proposal['xml'], before[1])
        proposal['changes'][0]['before']['Name'] = 'Changed'; proposal['request']['playlist_uuid'] = native_uuid(999)
        self.assertEqual(request, original)
        self.assertEqual((self.database.read_bytes(), self.xml.read_bytes()), before)

    def test_create_authority_and_manually_marked_delete_profile_never_publish(self):
        request = self.request(); before = self.database.read_bytes(), self.xml.read_bytes()
        old = asdict(self.authority); old.pop('smart_delete_accepted'); old.pop('smart_delete_profile')
        restored = store.PlaylistAuthority(**old)
        self.assertFalse(restored.smart_delete_accepted); self.assertEqual(restored.smart_delete_profile, '')
        for enabled, profile in ((False, ''), (True, ''), (True, 'unverified-delete-v1')):
            authority = replace(self.authority, smart_delete_accepted=enabled, smart_delete_profile=profile)
            writer = store.PlaylistStore(authority, self.root / 'recovery')
            with self.subTest(enabled=enabled, profile=profile), \
                    mock.patch.object(writer.lease, '_lock', side_effect=AssertionError('Entered source lease')), \
                    mock.patch.object(store, 'encrypted_backup', side_effect=AssertionError('Prepared source publication')):
                for operation in (writer.apply, writer.prepare_retry):
                    with self.assertRaises(WriteProofError):
                        operation(request)
        conn = _connect(self.database, readonly=True)
        try:
            self.assertFalse(store.build_plan(conn, request, self.xml.read_bytes())['source_write_authority'])
        finally:
            conn.close()
        self.assertEqual((self.database.read_bytes(), self.xml.read_bytes()), before)
        self.assertEqual(list((self.root / 'recovery').iterdir()), [])

    def test_exact_target_rule_parent_and_xml_context_are_bound(self):
        baseline = self.request()
        for field, value in (('playlist_uuid', native_uuid(999)), ('smart_rule', '<changed/>')):
            request = {**baseline, field: value}
            conn = _connect(self.database, readonly=True)
            try:
                with self.subTest(field=field), self.assertRaises(ValueError):
                    store.smart_deletion_proposal(conn, request, self.xml.read_bytes())
            finally:
                conn.close()
        changed = self.xml.read_bytes().replace(b'Id="7D0" ParentId="0"', b'Id="7D0" ParentId="3E8"')
        conn = _connect(self.database, readonly=True)
        try:
            with self.assertRaises(ConflictError):
                store.smart_deletion_proposal(conn, baseline, changed)
        finally:
            conn.close()

    def test_no_regular_delete_widening_or_batch_or_malformed_capability(self):
        conn = _connect(self.database, readonly=True)
        try:
            ordinary = store.request_for(self.catalog, 'delete', identity='2000')
            with self.assertRaises(ValueError):
                store.build_plan(conn, ordinary, self.xml.read_bytes())
        finally:
            conn.close()
        with self.assertRaises(WriteProofError):
            store.batch_for(self.catalog, [self.request()])
        for field, value in (('smart_delete_accepted', 1), ('smart_delete_profile', None)):
            with self.subTest(field=field), self.assertRaises(WriteProofError):
                replace(self.authority, **{field: value})
        for field, value in (('folder', True), ('smart_delete_profile', 'invented'), ('parent_uuid', native_uuid(999))):
            request = {**self.request(), field: value}
            with self.subTest(field=field), self.assertRaises(WriteProofError):
                store.validate_request(request)

    def accepted_temp_writer(self, profile=store.SMART_DELETE_PROFILE):
        authority = replace(self.authority, smart_delete_accepted=True,
                            smart_delete_profile=profile, retain_full_backups=True)
        return store.PlaylistStore(authority, self.root / 'accepted-temp-recovery')

    def state(self):
        conn = _connect(self.database, readonly=True)
        try:return _state(conn), _rows(conn, 'djmdPlaylist')
        finally:conn.close()

    def test_matched_profile_temp_publication_preserves_musical_tables_xml_and_replays(self):
        request = self.request(); writer = self.accepted_temp_writer()
        before, old_nodes = self.state(); before_xml = self.xml.read_bytes()
        writer.apply(request)
        after, nodes = self.state(); old = {row['ID']: row for row in old_nodes}; current = {row['ID']: row for row in nodes}
        self.assertNotIn('2000', current)
        self.assertEqual(current['1000'], old['1000'])
        self.assertEqual(current['3000'], {**old['3000'], 'Seq': 2, 'rb_local_usn': 41,
                                          'updated_at': request['smart_delete_clocks']['3000']})
        for table, state in before['tables'].items():
            if table not in ('djmdPlaylist', 'agentRegistry'):
                self.assertEqual(after['tables'][table], state, table)
        self.assertEqual(self.xml.read_bytes(), before_xml)
        replay = self.database.read_bytes(), self.xml.read_bytes()
        writer.apply(request); self.assertEqual(writer.prepare_retry(request), request)
        self.assertEqual((self.database.read_bytes(), self.xml.read_bytes()), replay)

    def test_interrupted_delete_recovers_exact_before_or_after_then_renews_explicit_retry(self):
        writer = self.accepted_temp_writer(); request = self.request()
        before = self.state(), self.xml.read_bytes()
        def interrupt(phase):
            if phase == 'sql-applied':raise RuntimeError('interrupted smart delete')
        with mock.patch.object(store, '_fault', side_effect=interrupt):
            with self.assertRaisesRegex(RuntimeError, 'interrupted smart delete'):writer.apply(request)
        self.assertEqual(writer.recover(), 'rolled-back')
        self.assertEqual((self.state(), self.xml.read_bytes()), before)
        retry = writer.prepare_retry(request)
        self.assertNotEqual(retry['operation_id'], request['operation_id'])
        self.assertEqual(retry['playlist_uuid'], request['playlist_uuid'])
        self.assertEqual(retry['smart_rule'], request['smart_rule'])
        def after_commit(phase):
            if phase == 'sql-committed':raise RuntimeError('committed before ack')
        with mock.patch.object(store, '_fault', side_effect=after_commit):
            with self.assertRaisesRegex(RuntimeError, 'committed before ack'):writer.apply(retry)
        self.assertEqual(writer.recover(), 'committed')
        self.assertNotIn('2000', {row['ID'] for row in writer.snapshot()['nodes']})
        self.assertEqual(writer.prepare_retry(retry), retry)

    def test_committed_replay_rejects_reused_uuid_or_leftover_membership_and_children(self):
        writer = self.accepted_temp_writer(); request = self.request(); writer.apply(request)
        conn = _connect(self.database)
        try:
            template = _rows(conn, 'djmdPlaylist')[0]; columns = list(template)
            ghost = {**template, 'ID': '9000', 'UUID': request['playlist_uuid'], 'Seq': 3}
            conn.execute(f'INSERT INTO djmdPlaylist ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})',
                         [ghost[key] for key in columns]); conn.commit()
        finally:conn.close()
        before = self.database.read_bytes(), self.xml.read_bytes()
        for action in (writer.apply, writer.prepare_retry, writer.committed_transition):
            with self.subTest(action=action.__name__), self.assertRaises(ConflictError):action(request)
        self.assertEqual((self.database.read_bytes(), self.xml.read_bytes()), before)
        conn = _connect(self.database)
        try:
            conn.execute('UPDATE djmdPlaylist SET UUID=?,ParentID=? WHERE ID=?',
                         (native_uuid(9000), '2000', '9000')); conn.commit()
        finally:conn.close()
        with self.assertRaises(ConflictError):writer.apply(request)
        conn = _connect(self.database)
        try:
            conn.execute('DELETE FROM djmdPlaylist WHERE ID=?', ('9000',))
            conn.execute('INSERT INTO djmdSongPlaylist (ID,PlaylistID) VALUES (?,?)', ('ghost', '2000')); conn.commit()
        finally:conn.close()
        with self.assertRaises(ConflictError):writer.prepare_retry(request)

    def test_sibling_clocks_are_exact_and_empty_loop_still_consumes_one_counter(self):
        request = self.request()
        conn = _connect(self.database, readonly=True)
        try:
            for mapping in ({}, {**request['smart_delete_clocks'], '999': request['timestamp']}):
                changed = {**request, 'smart_delete_clocks': mapping}
                with self.subTest(mapping=mapping), self.assertRaises((ValueError, WriteProofError)):
                    store.build_plan(conn, changed, self.xml.read_bytes())
        finally:
            conn.close()
        conn = _connect(self.database)
        try:
            conn.execute('DELETE FROM djmdPlaylist WHERE ID=?', ('3000',)); conn.commit()
            current = store.catalog(conn)
            last = store.request_for(current, 'delete_smart', identity='2000')
            self.assertEqual(last['smart_delete_clocks'], {})
            result = store.build_plan(conn, last, self.xml.read_bytes())
            self.assertEqual(result['counter']['int_1'], 41)
            self.assertEqual([change['table'] for change in result['changes']], ['djmdPlaylist', 'agentRegistry'])
            self.assertIsNone(result['changes'][0]['after'])
        finally:
            conn.close()

    def test_synced_target_is_separate_scope_and_xml_cannot_drop_or_edit_retained_smart(self):
        conn = _connect(self.database)
        try:
            conn.execute('UPDATE djmdPlaylist SET rb_local_synced=1 WHERE ID=?', ('2000',)); conn.commit()
            current = store.catalog(conn)
            request = store.request_for(current, 'delete_smart', identity='2000')
            self.assertEqual(request['smart_delete_profile'], store.SMART_LOCAL_DELETE_PROFILE)
            with mock.patch.object(self.writer.lease, '_lock', side_effect=AssertionError('Entered source lease')):
                with self.assertRaises(WriteProofError):self.writer.apply(request)
        finally:
            conn.close()
        conn = _connect(self.database)
        try:
            conn.execute('UPDATE djmdPlaylist SET rb_local_synced=0 WHERE ID=?', ('2000',)); conn.commit()
            result = store.build_plan(conn, self.request(), self.xml.read_bytes())
        finally:
            conn.close()
        before = self.xml.read_bytes()
        token = xml._target(before, '2000', created_smart=True)[1]
        removed = before[:token[0]] + before[token[1]:]
        for changed in (removed, before.replace(b'Timestamp="200"', b'Timestamp="201"')):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                xml.validate_transition(before, changed, result['changes'], smart_deleted=('2000',))
        with self.assertRaises(ValueError):
            xml.validate_transition(before, before, result['changes'])

    def synced_request(self):
        conn = _connect(self.database)
        try:
            conn.execute('UPDATE djmdPlaylist SET rb_local_synced=1 WHERE ID=?', ('2000',)); conn.commit()
            self.catalog = store.catalog(conn)
        finally:conn.close()
        request = self.request()
        request.update(timestamp='2026-11-13 02:59:18.251 +00:00',
                       survivor_timestamp='2026-11-13 02:59:18.323 +00:00',
                       smart_delete_clocks={'3000': '2026-11-13 02:59:18.323 +00:00'})
        return request

    def test_combined_profile_synced_tombstone_has_independent_target_and_sibling_clocks(self):
        request = self.synced_request(); writer = self.accepted_temp_writer(store.SMART_LOCAL_DELETE_PROFILE)
        before, old_rows = self.state(); old = {row['ID']: row for row in old_rows}; old_xml = self.xml.read_bytes()
        writer.apply(request)
        after, rows = self.state(); nodes = {row['ID']: row for row in rows}
        self.assertEqual(nodes['2000'], {**old['2000'], 'rb_data_status': 2, 'rb_local_deleted': 1,
                                        'rb_local_usn': 41, 'updated_at': request['timestamp']})
        self.assertEqual(nodes['3000'], {**old['3000'], 'Seq': 2, 'rb_local_usn': 42,
                                        'updated_at': request['smart_delete_clocks']['3000']})
        self.assertEqual(nodes['1000'], old['1000'])
        for table, state in before['tables'].items():
            if table not in ('djmdPlaylist', 'agentRegistry'):self.assertEqual(after['tables'][table], state)
        self.assertEqual(self.xml.read_bytes(), old_xml)
        replay = self.database.read_bytes(), self.xml.read_bytes()
        writer.apply(request); self.assertEqual(writer.prepare_retry(request), request)
        self.assertEqual((self.database.read_bytes(), self.xml.read_bytes()), replay)
        conn = _connect(self.database)
        try:
            conn.execute('UPDATE djmdPlaylist SET updated_at=? WHERE ID=?',
                         ('2026-11-13 02:59:18.252 +00:00', '2000')); conn.commit()
        finally:conn.close()
        for action in (writer.apply, writer.prepare_retry, writer.committed_transition):
            with self.subTest(action=action.__name__), self.assertRaises(ConflictError):action(request)

    def test_synced_profile_is_not_inherited_and_request_cannot_switch_target_history(self):
        request = self.synced_request(); old = self.accepted_temp_writer()
        with mock.patch.object(old.lease, '_lock', side_effect=AssertionError('Entered source lease')):
            with self.assertRaisesRegex(WriteProofError, 'profile'):old.apply(request)
        forged = {**request, 'smart_delete_profile': store.SMART_DELETE_PROFILE}
        conn = _connect(self.database, readonly=True)
        try:
            with self.assertRaisesRegex(ConflictError, 'history'):store.build_plan(conn, forged, self.xml.read_bytes())
        finally:conn.close()
        combined = replace(old.authority, smart_delete_profile=store.SMART_LOCAL_DELETE_PROFILE)
        self.assertEqual(store.smart_deletion_reason(combined), '')
        self.assertEqual(store.smart_deletion_reason(combined, profile=store.SMART_LOCAL_DELETE_PROFILE), '')

    def test_synced_interrupted_save_and_postcommit_recovery_keep_exact_tombstone(self):
        request = self.synced_request(); writer = self.accepted_temp_writer(store.SMART_LOCAL_DELETE_PROFILE)
        before = self.state(), self.xml.read_bytes()
        def interrupt(phase):
            if phase == 'sql-applied':raise RuntimeError('synced before commit')
        with mock.patch.object(store, '_fault', side_effect=interrupt):
            with self.assertRaises(RuntimeError):writer.apply(request)
        self.assertEqual(writer.recover(), 'rolled-back')
        self.assertEqual((self.state(), self.xml.read_bytes()), before)
        retry = writer.prepare_retry(request)
        self.assertNotEqual(retry['operation_id'], request['operation_id'])
        self.assertEqual(retry['smart_delete_profile'], store.SMART_LOCAL_DELETE_PROFILE)
        def committed(phase):
            if phase == 'sql-committed':raise RuntimeError('synced after commit')
        with mock.patch.object(store, '_fault', side_effect=committed):
            with self.assertRaises(RuntimeError):writer.apply(retry)
        self.assertEqual(writer.recover(), 'committed')
        self.assertEqual(writer.prepare_retry(retry), retry)
        conn = _connect(self.database, readonly=True)
        try:self.assertEqual(store.row(conn, 'djmdPlaylist', '2000')['rb_local_deleted'], 1)
        finally:conn.close()

    def test_pure_context_rejects_stored_memberships_and_reused_parent(self):
        conn = _connect(self.database, readonly=True)
        try:
            nodes = _rows(conn, 'djmdPlaylist'); counter = store.row(conn, 'agentRegistry', 'localUpdateCount')
        finally:
            conn.close()
        arguments = dict(identity='2000', uuid=native_uuid(2000), parent='root', parent_uuid='', rule=self.rule)
        with self.assertRaisesRegex(ValueError, 'stored membership'):
            plan.smart_delete_context(nodes, [{'PlaylistID': '2000'}], counter, **arguments)
        with self.assertRaisesRegex(ValueError, 'changed'):
            plan.smart_delete_context(nodes, [], counter, **{**arguments, 'parent': '1000'})
