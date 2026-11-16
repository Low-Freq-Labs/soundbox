"""My Tag smart creation proposals use synthetic TEMP DB/XML only.

Observed constructor/placement and combined-definition windows are reproduced;
no source authority or production capability is issued by these TEMP proofs.
"""
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
import shutil
import tempfile
import unittest
from unittest import mock
from xml.etree import ElementTree

from test_playlist_transaction import fixture
from soundbox.library import LibraryTag
from soundbox import rekordbox_playlist_plan as plan, rekordbox_playlist_store as store
from soundbox import rekordbox_playlist_xml as xml, rekordbox_cue_store as cues
from soundbox.rekordbox_tags import _schema, NATIVE_VERSION
from soundbox.rekordbox_write import _connect, _rows, _state, ConflictError, WriteProofError


def native_uuid(number):
    return f'00000000-0000-4000-8000-{number:012d}'


class SmartCreationProposals(unittest.TestCase):
    def setUp(self):
        self.area = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.area).resolve()
        source = self.root / 'source'; source.mkdir(); fixture(source)
        self.database = source / 'master.db'
        self.xml_path = source / 'masterPlaylists6.xml'
        conn = _connect(self.database)
        try:
            for name, kind in (('Name', 'TEXT'), ('ParentID', 'TEXT'), ('Seq', 'INTEGER'),
                               ('rb_data_status', 'INTEGER'), ('rb_local_data_status', 'INTEGER'),
                               ('rb_local_synced', 'INTEGER'), ('usn', 'INTEGER'), ('rb_local_usn', 'INTEGER')):
                conn.execute(f'ALTER TABLE djmdMyTag ADD COLUMN {name} {kind}')
            for identity, name, attribute, parent, seq in (('200', 'Kinds', 1, 'root', 1),
                    ('201', 'First', 0, '200', 1), ('202', 'Second', 0, '200', 2)):
                conn.execute('INSERT INTO djmdMyTag VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                    (identity, native_uuid(int(identity)), attribute, 0, name, parent, seq, 0, 0, 0, None, 12))
            conn.commit()
            self.catalog = store.catalog(conn)
            self.schema = _schema(conn)
        finally:
            conn.close()
        self.authority = store.PlaylistAuthority(str(self.database), 'synthetic-playlists', self.schema, True)
        self.writer = store.PlaylistStore(self.authority, self.root / 'recovery')
        for name, value in (('_closed', None), ('_installed_version', NATIVE_VERSION)):
            self.enterContext(mock.patch.object(cues, name, return_value=value))
        self.tags = [LibraryTag('201', 'First', 'Kinds', native_uuid(201), '200', native_uuid(200)),
                     LibraryTag('202', 'Second', 'Kinds', native_uuid(202), '200', native_uuid(200))]

    def request(self, **kwargs):
        return store.request_for(self.catalog, 'create_smart', name='New smart playlist',
                                 tags=kwargs.pop('tags', self.tags), **kwargs)

    def proposal(self, request=None):
        conn = _connect(self.database, readonly=True)
        try:
            return store.smart_creation_proposal(conn, request or self.request(), self.xml_path.read_bytes())
        finally:
            conn.close()

    def add_folder(self):
        conn = _connect(self.database)
        try:
            template = _rows(conn, 'djmdPlaylist')[0]
            folder = {**template, 'ID': '2000', 'UUID': native_uuid(2000), 'Seq': 2, 'Attribute': 1}
            sibling = {**template, 'ID': '2001', 'UUID': native_uuid(2001), 'Seq': 1,
                       'ParentID': '2000', 'Attribute': 4, 'SmartList': plan.smart_tag_rule('2001', ['201'])}
            columns = list(folder)
            conn.execute(f'INSERT INTO djmdPlaylist ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})',
                         [folder[key] for key in columns])
            conn.execute(f'INSERT INTO djmdPlaylist ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})',
                         [sibling[key] for key in columns]); conn.commit()
            self.catalog = store.catalog(conn)
        finally:
            conn.close()
        payload = xml.append_node(self.xml_path.read_bytes(), '2000', attribute=1)
        self.xml_path.write_bytes(xml.append_smart_node(payload, '2001', '2000'))

    def test_any_is_explicit_default_for_one_or_multiple_exact_tag_bindings(self):
        for tags in (self.tags[:1], self.tags):
            with self.subTest(count=len(tags)):
                request = self.request(tags=tags)
                self.assertEqual(request['match'], 'any')
                self.assertEqual(request['parent_uuid'], '')
                self.assertEqual([tag['id'] for tag in request['tags']], [tag.tag_id for tag in tags])
                self.assertEqual(request['tags'][0]['category_uuid'], native_uuid(200))
                proposal = self.proposal(request)
                self.assertFalse(proposal['source_write_authority'])
                self.assertTrue(proposal['complete_sql_plan'])
                self.assertTrue(proposal['provisional'])
                final = next(change['after'] for change in reversed(proposal['changes'])
                             if change['table'] == 'djmdPlaylist')
                self.assertEqual(final['Attribute'], 4)
                self.assertEqual(final['Name'], 'New smart playlist')
                self.assertEqual(final['SmartList'].count('PropertyName="myTag"'), len(tags))

    def test_rule_matches_frozen_native_any_reference_without_inferring_aggregate_counter(self):
        actual = plan.smart_tag_rule('541807528', ['1239045613', '79899458'])
        self.assertEqual(actual,
            '<NODE Id="541807528" LogicalOperator="2" AutomaticUpdate="0">'
            '<CONDITION PropertyName="myTag" Operator="8" ValueUnit="" ValueLeft="1239045613" ValueRight=""/>'
            '<CONDITION PropertyName="myTag" Operator="8" ValueUnit="" ValueLeft="79899458" ValueRight=""/></NODE>')
        self.assertIn('ValueLeft="-1"', plan.smart_tag_rule('1', ['4294967295']))
        for identity, expected in (('541807528', '541807528'), ('4016338146', '-278629150'),
                                   ('2635359582', '-1659607714'), ('4294967295', '-1')):
            root = ElementTree.fromstring(plan.smart_tag_rule(identity, ['4294967295']))
            self.assertEqual(root.attrib['Id'], expected)
            self.assertEqual(int(root.attrib['Id']) % 2**32, int(identity))
            self.assertEqual(int(root[0].attrib['ValueLeft']) % 2**32, 4294967295)
        for tags in ([], ['1', '1'], ['0'], ['01'], ['4294967296'], ['<injected>'], [[]]):
            with self.subTest(tags=tags), self.assertRaises(ValueError):
                plan.smart_tag_rule('1', tags)
        with self.assertRaises(ValueError):
            plan.smart_tag_rule('1', ['201'], match='all')

    def test_complete_proposal_is_read_only_and_exact_changes_are_temp_executable(self):
        before = self.database.read_bytes(), self.xml_path.read_bytes()
        proposal = self.proposal()
        self.assertEqual((self.database.read_bytes(), self.xml_path.read_bytes()), before)
        work = self.root / 'working.db'; shutil.copyfile(self.database, work)
        conn = _connect(work)
        try:
            previous = _state(conn)
            conn.set_authorizer(store.authorizer)
            store.apply_changes(conn, proposal['changes'])
            conn.set_authorizer(None)
            conn.commit()
            after = _state(conn)
            for table, rows in previous['tables'].items():
                if table not in ('djmdPlaylist', 'agentRegistry'):
                    self.assertEqual(after['tables'][table], rows, table)
            created = store.row(conn, 'djmdPlaylist', proposal['request']['playlist_id'])
            self.assertEqual(created['Attribute'], 4)
            self.assertEqual(created['SmartList'], plan.smart_tag_rule(created['ID'], ['201', '202']))
            self.assertEqual(store.row(conn, 'agentRegistry', 'localUpdateCount')['int_1'], 43)
            self.assertEqual(created['Name'], 'New smart playlist')
        finally:
            conn.close()
        self.assertEqual((self.database.read_bytes(), self.xml_path.read_bytes()), before)

    def test_old_authority_and_manually_true_flag_both_fail_before_any_source_work(self):
        request = self.request(); before = self.database.read_bytes(), self.xml_path.read_bytes()
        self.assertFalse(self.authority.smart_create_accepted)
        self.assertTrue(store.smart_creation_reason(None))
        self.assertTrue(store.smart_creation_reason(self.authority))
        self.assertIn('profile', store.smart_creation_reason(replace(self.authority, smart_create_accepted=True)))
        old = asdict(self.authority); old.pop('smart_create_accepted'); old.pop('smart_create_profile')
        self.assertFalse(store.PlaylistAuthority(**old).smart_create_accepted)
        for accepted in (False, True):
            writer = store.PlaylistStore(replace(self.authority, smart_create_accepted=accepted), self.root / 'recovery')
            with self.subTest(accepted=accepted), \
                    mock.patch.object(writer.lease, '_lock', side_effect=AssertionError('Entered source lease')), \
                    mock.patch.object(store, 'encrypted_backup', side_effect=AssertionError('Prepared source publication')):
                with self.assertRaises(WriteProofError):
                    writer.apply(request)
                with self.assertRaises(WriteProofError):
                    writer.prepare_retry(request)
        self.assertEqual((self.database.read_bytes(), self.xml_path.read_bytes()), before)
        self.assertEqual(list((self.root / 'recovery').iterdir()), [])

    def test_all_empty_duplicate_ambiguous_and_ordinary_batch_requests_fail(self):
        for kwargs in ({'match': 'all'}, {'tags': []}, {'tags': [self.tags[0], self.tags[0]]}):
            with self.subTest(kwargs=kwargs), self.assertRaises(WriteProofError):
                self.request(**kwargs)
        for field, value in (('folder', True), ('parent_uuid', native_uuid(100)), ('tracks', [{'bad': 'field'}]),
                             ('tags', [{'id': '201'}])):
            request = self.request(); request[field] = value
            with self.subTest(field=field), self.assertRaises(WriteProofError):
                store.validate_request(request)
        request = self.request()
        with self.assertRaises(WriteProofError):
            store.batch_for(self.catalog, [request])
        conn = _connect(self.database, readonly=True)
        try:
            self.assertFalse(store.build_plan(conn, request, self.xml_path.read_bytes())['source_write_authority'])
        finally:
            conn.close()

    def test_fresh_source_rejects_deleted_replaced_renamed_moved_or_cloud_tags(self):
        changes = [('rb_local_deleted', 1, '201'), ('UUID', native_uuid(999), '201'),
                   ('Name', 'Renamed', '201'), ('ParentID', 'root', '201'), ('Attribute', 1, '201'),
                   ('usn', 44, '201'), ('UUID', native_uuid(998), '200'), ('Name', 'Other category', '200')]
        request = self.request()
        for field, value, identity in changes:
            with self.subTest(field=field, identity=identity):
                conn = _connect(self.database)
                try:
                    before = store._rows(conn, 'djmdMyTag')
                    old = next(row for row in before if row['ID'] == identity)[field]
                    conn.execute(f'UPDATE djmdMyTag SET {field}=? WHERE ID=?', (value, identity)); conn.commit()
                finally:
                    conn.close()
                try:
                    with self.assertRaises((ValueError, ConflictError)):
                        self.proposal(request)
                finally:
                    conn = _connect(self.database)
                    try:
                        conn.execute(f'UPDATE djmdMyTag SET {field}=? WHERE ID=?', (old, identity)); conn.commit()
                    finally:
                        conn.close()

    def test_observed_native_historical_read_references_and_opaque_category_uuid_are_supported(self):
        conn = _connect(self.database)
        try:
            conn.execute('UPDATE djmdMyTag SET UUID=?,rb_data_status=256,rb_local_synced=1,usn=81,rb_local_usn=77 WHERE ID=?',
                         ('1', '200'))
            conn.execute('UPDATE djmdMyTag SET rb_data_status=257,rb_local_synced=1,usn=82,rb_local_usn=210784 WHERE ID=?',
                         ('202',))
            conn.execute('UPDATE agentRegistry SET int_1=368583 WHERE registry_id=?', ('localUpdateCount',))
            conn.commit()
        finally:
            conn.close()
        tags = [replace(tag, category_uuid='1') for tag in self.tags]
        proposal = self.proposal(self.request(tags=tags))
        self.assertEqual([tag['category_uuid'] for tag in proposal['tag_bindings']], ['1', '1'])
        self.assertFalse(proposal['source_write_authority'])
        for field, value in (('rb_data_status', 258), ('rb_local_synced', 0), ('usn', -1), ('rb_local_deleted', 1)):
            conn = _connect(self.database)
            try:
                old = store._rows(conn, 'djmdMyTag')
                old_value = next(row for row in old if row['ID'] == '202')[field]
                conn.execute(f'UPDATE djmdMyTag SET {field}=? WHERE ID=?', (value, '202')); conn.commit()
            finally:
                conn.close()
            try:
                with self.subTest(field=field), self.assertRaises(ConflictError):
                    self.proposal(self.request(tags=tags))
            finally:
                conn = _connect(self.database)
                try:
                    conn.execute(f'UPDATE djmdMyTag SET {field}=? WHERE ID=?', (old_value, '202')); conn.commit()
                finally:
                    conn.close()

    def test_reference_local_counter_cannot_exceed_fresh_native_counter(self):
        request = self.request()
        conn = _connect(self.database)
        try:
            conn.execute('UPDATE djmdMyTag SET rb_local_usn=41 WHERE ID=?', ('201',)); conn.commit()
        finally:
            conn.close()
        with self.assertRaisesRegex(ConflictError, 'reference state'):
            self.proposal(request)

    def test_parent_uuid_binding_survives_catalog_revision_rebinding(self):
        self.add_folder()
        request = self.request(parent='2000')
        self.assertEqual(request['parent_uuid'], native_uuid(2000))
        self.proposal(request)
        conn = _connect(self.database)
        try:
            conn.execute('UPDATE djmdPlaylist SET UUID=? WHERE ID=?', (native_uuid(9999), '2000')); conn.commit()
            request['revision'] = store.catalog(conn)['revision']
        finally:
            conn.close()
        with self.assertRaisesRegex(ConflictError, 'parent changed'):
            self.proposal(request)

    def test_nonroot_parent_requires_exact_matching_local_folder_xml(self):
        original = self.xml_path.read_bytes()
        self.add_folder()
        request = self.request(parent='2000')
        valid = self.xml_path.read_bytes()
        self.proposal(request)
        for changed in (original, valid.replace(b'Id="7D0" ParentId="0" Attribute="1"',
                b'Id="7D0" ParentId="0" Attribute="0"'),
                valid.replace(b'Id="7D0" ParentId="0"', b'Id="7D0" ParentId="3E8"')):
            with self.subTest(changed=changed):
                self.xml_path.write_bytes(changed)
                with self.assertRaises((ValueError, ConflictError)):
                    self.proposal(request)
        self.xml_path.write_bytes(valid)

    def test_existing_smart_rule_cannot_be_renamed_deleted_reordered_or_given_members(self):
        conn = _connect(self.database)
        try:
            template = _rows(conn, 'djmdPlaylist')[0]
            smart = {**template, 'ID': '3000', 'UUID': native_uuid(3000), 'Seq': 2, 'Attribute': 4,
                     'SmartList': plan.smart_tag_rule('3000', ['201'])}
            columns = list(smart)
            conn.execute(f'INSERT INTO djmdPlaylist ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})',
                         [smart[key] for key in columns]); conn.commit()
            catalog = store.catalog(conn)
        finally:
            conn.close()
        payload = xml.append_smart_node(self.xml_path.read_bytes(), '3000', timestamp=100)
        for operation, kwargs in (('rename', {'name': 'Changed'}), ('delete', {}),
                                  ('reorder', {'anchor_id': '1000'}),
                                  ('append', {'tracks': [SimpleNamespace(track_id='track', source_uuid='uuid')]})):
            request = store.request_for(catalog, operation, identity='3000', **kwargs)
            conn = _connect(self.database, readonly=True)
            try:
                with self.subTest(operation=operation), self.assertRaises(ValueError):
                    store.build_plan(conn, request, payload)
            finally:
                conn.close()

    def test_fresh_preview_store_uses_read_only_snapshot_and_exact_capability_types(self):
        before = self.database.read_bytes(), self.xml_path.read_bytes()
        preview = self.writer.propose_smart_creation(self.request())
        self.assertFalse(preview['source_write_authority'])
        self.assertEqual((self.database.read_bytes(), self.xml_path.read_bytes()), before)
        for value in (1, 'yes', None):
            with self.subTest(value=value), self.assertRaises(WriteProofError):
                replace(self.authority, smart_create_accepted=value)

    def accepted_temp_writer(self):
        authority = replace(self.authority, smart_create_accepted=True,
                            smart_create_profile=store.SMART_CREATE_PROFILE, retain_full_backups=True)
        return store.PlaylistStore(authority, self.root / 'accepted-temp-recovery')

    def inspect(self):
        conn = _connect(self.database, readonly=True)
        try:
            return _state(conn), {table: _rows(conn, table) for table in
                ('djmdPlaylist', 'agentRegistry', 'djmdMyTag', 'djmdSongMyTag', 'djmdSongPlaylist')}
        finally:
            conn.close()

    def test_explicit_matched_profile_publishes_only_create_scoped_temp_effects_and_replays(self):
        self.add_folder()
        before, old_rows = self.inspect(); old_xml = self.xml_path.read_bytes()
        request = self.request(parent='2000'); proposal = self.proposal(request)
        writer = self.accepted_temp_writer()
        writer.apply(request)
        after, rows = self.inspect()
        for table, state in before['tables'].items():
            if table not in ('djmdPlaylist', 'agentRegistry'):
                self.assertEqual(after['tables'][table], state, table)
        old = {node['ID']: node for node in old_rows['djmdPlaylist']}
        nodes = {node['ID']: node for node in rows['djmdPlaylist']}
        self.assertEqual(nodes['1000'], old['1000'])
        self.assertEqual(nodes['2000'], old['2000'])
        changed = {key for key in old['2001'] if old['2001'][key] != nodes['2001'][key]}
        self.assertEqual(changed, {'Seq', 'updated_at', 'rb_local_usn'})
        self.assertEqual(nodes['2001']['Seq'], 2)
        self.assertEqual(nodes[request['playlist_id']]['Seq'], 1)
        self.assertEqual(next(row['int_1'] for row in rows['agentRegistry']
                              if row['registry_id'] == 'localUpdateCount'), 45)
        self.assertEqual(self.xml_path.read_bytes(), proposal['xml'])
        self.assertEqual(xml._target(old_xml, '2001', created_smart=True)[0],
                         xml._target(self.xml_path.read_bytes(), '2001', created_smart=True)[0])
        replay = self.database.read_bytes(), self.xml_path.read_bytes()
        writer.apply(request)
        self.assertEqual((self.database.read_bytes(), self.xml_path.read_bytes()), replay)
        self.assertEqual(writer.prepare_retry(request), request)

    def test_interrupted_folder_creation_recovers_exact_pair_then_explicit_retry(self):
        self.add_folder()
        before = self.inspect(), self.xml_path.read_bytes()
        writer = self.accepted_temp_writer(); request = self.request(parent='2000')
        def interrupt(phase):
            if phase == 'xml-published':
                raise RuntimeError('interrupted smart creation')
        with mock.patch.object(store, '_fault', side_effect=interrupt):
            with self.assertRaisesRegex(RuntimeError, 'interrupted smart creation'):
                writer.apply(request)
        self.assertTrue(writer.journal.exists())
        self.assertEqual(writer.recover(), 'rolled-back')
        self.assertEqual((self.inspect(), self.xml_path.read_bytes()), before)
        renewed = writer.prepare_retry(request)
        self.assertNotEqual(renewed['operation_id'], request['operation_id'])
        self.assertEqual(renewed['playlist_id'], request['playlist_id'])
        self.assertEqual(renewed['tags'], request['tags'])
        writer.apply(renewed)
        self.assertIn(request['playlist_id'], {node['ID'] for node in writer.snapshot()['nodes']})

    def test_committed_receipt_replay_and_retry_reject_changed_selected_tag(self):
        request = self.request(); writer = self.accepted_temp_writer(); writer.apply(request)
        conn = _connect(self.database)
        try:
            conn.execute('UPDATE djmdMyTag SET UUID=? WHERE ID=?', (native_uuid(999), '201'))
            conn.commit()
        finally:
            conn.close()
        before = self.database.read_bytes(), self.xml_path.read_bytes()
        for action in (writer.apply, writer.prepare_retry, writer.committed_transition):
            with self.subTest(action=action.__name__), self.assertRaises(ConflictError):
                action(request)
        self.assertEqual((self.database.read_bytes(), self.xml_path.read_bytes()), before)

    def test_known_causal_rebase_renews_exact_sibling_map_and_keeps_selected_pins(self):
        self.add_folder()
        first = self.request(parent='2000'); second = self.request(parent='2000')
        writer = self.accepted_temp_writer(); current = writer.apply(first)
        with self.assertRaises(ConflictError):
            self.proposal(second)
        renewed = store.renew_smart_request(second, current)
        self.assertEqual(renewed['playlist_id'], second['playlist_id'])
        self.assertEqual(renewed['playlist_uuid'], second['playlist_uuid'])
        self.assertEqual(renewed['parent_uuid'], second['parent_uuid'])
        self.assertEqual(renewed['tags'], second['tags'])
        self.assertEqual(set(renewed['smart_clocks']['sibling_timestamps']), {'2001', first['playlist_id']})
        writer.apply(renewed)
        self.assertEqual(writer.snapshot()['revision'], self.inspect_catalog_revision())

    def inspect_catalog_revision(self):
        conn = _connect(self.database, readonly=True)
        try:
            return store.catalog(conn)['revision']
        finally:
            conn.close()

    def test_new_type4_xml_is_create_only_and_preserves_every_retained_byte(self):
        before = self.xml_path.read_bytes()
        request = self.request(); proposal = self.proposal(request); after = proposal['xml']
        new_token = (f'    <NODE Id="{int(request["playlist_id"]):X}" ParentId="0" Attribute="4" '
                     f'Timestamp="{request["xml_timestamp"]}" Lib_Type="0" CheckType="0"/>\n').encode()
        self.assertEqual(after, before.replace(b'  </PLAYLISTS>', new_token + b'  </PLAYLISTS>'))
        with self.assertRaises(ValueError):
            xml.append_node(before, request['playlist_id'], attribute=4)
        with self.assertRaises(ValueError):
            xml.touch(after, request['playlist_id'], request['xml_timestamp'] + 1)
        with self.assertRaises(ValueError):
            xml.append_smart_node(after, request['playlist_id'])
        with self.assertRaises(ValueError):
            xml.validate_transition(before, after, [])
        with self.assertRaises(ValueError):
            xml.validate_transition(before, after.replace(b'6.8.4', b'6.8.7'),
                [dict(table='djmdPlaylist', id=request['playlist_id'], before=None,
                      after=dict(ID=request['playlist_id'], ParentID='root', Attribute=4))],
                smart_created=(request['playlist_id'],))
