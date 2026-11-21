"""Default-off smart reparent candidate over generated encrypted TEMP catalogs."""
from copy import deepcopy
from dataclasses import replace
import unittest
from unittest import mock

import test_smart_playlist_metadata as fixtures
from soundbox import rekordbox_playlist_store as store,rekordbox_playlist_xml as xml
from soundbox import rekordbox_playlist_move as move
from soundbox.rekordbox_write import _connect,_rows,_state,WriteProofError,ConflictError


class SmartMoveCandidate(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.SmartMetadataCandidate('test_inline_name_native_window_updates_exact_row_counter_plus_one_and_one_xml_clock')
        self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.database=self.f.database;self.path=self.f.path
        conn=_connect(self.database)
        try:
            template=store.row(conn,'djmdPlaylist','4000')
            destination={**template,'ID':'5000','UUID':fixtures.fixture.native_uuid(5000),'Seq':2,'Name':'Empty destination','rb_local_synced':1}
            conn.execute('INSERT INTO djmdPlaylist ('+','.join(destination)+') VALUES('+','.join('?' for _ in destination)+')',tuple(destination.values()))
            conn.execute("UPDATE djmdPlaylist SET rb_local_synced=1 WHERE ID='4000'")
        finally:conn.close()
        self.path.write_bytes(xml.append_node(self.path.read_bytes(),'5000',attribute=1))
        self.authority=replace(self.f.case.authority,smart_move_accepted=True,smart_move_profile=store.SMART_MOVE_PROFILE)
        self.writer=store.PlaylistStore(self.authority,self.f.case.root/'move-recovery')

    def request(self,parent='root'):
        return store.request_for(self.writer.snapshot(),'move_smart',identity='2000',parent=parent)

    def inspect(self):
        conn=_connect(self.database,readonly=True)
        try:return _state(conn),{name:_rows(conn,name) for name in ('djmdPlaylist','agentRegistry','djmdSongPlaylist')}
        finally:conn.close()

    def test_old_authorities_never_admit_cross_parent_source_work(self):
        request=self.request();before=self.inspect(),self.path.read_bytes()
        old=replace(self.authority,smart_move_accepted=False,smart_move_profile='',smart_reorder_accepted=True,
                    smart_reorder_profile=store.SMART_REORDER_PROFILE)
        for authority in (old,replace(old,smart_move_accepted=True)):
            writer=store.PlaylistStore(authority,self.f.case.root/'blocked')
            with self.subTest(authority=authority),mock.patch.object(writer.lease,'_lock',side_effect=AssertionError('Source lease')):
                with self.assertRaises(WriteProofError):writer.apply(request)
                with self.assertRaises(WriteProofError):writer.prepare_retry(request)
        self.assertEqual((self.inspect(),self.path.read_bytes()),before)

    def test_folder_to_root_preserves_source_gaps_rules_history_all_other_domains_and_exact_xml(self):
        before,rows=self.inspect();nodes={r['ID']:r for r in rows['djmdPlaylist']};raw=self.path.read_bytes()
        counter=next(r for r in rows['agentRegistry'] if r['registry_id']=='localUpdateCount')
        request=self.request();catalog,edge=self.writer.apply_with_transition(request)
        after,current=self.inspect();actual={r['ID']:r for r in current['djmdPlaylist']}
        self.assertEqual(actual['2000'],{**nodes['2000'],'ParentID':'root','Seq':3,
            'updated_at':request['timestamp'],'rb_local_usn':counter['int_1']+1})
        self.assertEqual(actual['4000'],nodes['4000'])
        self.assertEqual(actual['1000'],nodes['1000']);self.assertEqual(actual['3000'],nodes['3000'])
        self.assertEqual((actual['1000']['Seq'],actual['3000']['Seq']),(1,3))
        for table,digest in before['tables'].items():
            if table not in ('djmdPlaylist','agentRegistry'):self.assertEqual(after['tables'][table],digest,table)
        expected=xml._touch(xml.reparent_smart(raw,'2000','root'),'2000',request['xml_timestamp'],created_smart=True)
        self.assertEqual(self.path.read_bytes(),expected);self.assertEqual(edge,(request['revision'],catalog['revision']))
        previous=self.database.read_bytes(),self.path.read_bytes();self.writer.apply(request)
        self.assertEqual((self.database.read_bytes(),self.path.read_bytes()),previous)
        self.assertEqual(self.writer.committed_transition(request),edge)

    def test_root_to_nonempty_folder_normalizes_destination_gaps_only(self):
        self.writer.apply(self.request());before,rows=self.inspect();old={r['ID']:r for r in rows['djmdPlaylist']}
        counter=next(r['int_1'] for r in rows['agentRegistry'] if r['registry_id']=='localUpdateCount')
        request=self.request('4000');self.writer.apply(request);after,current=self.inspect();new={r['ID']:r for r in current['djmdPlaylist']}
        self.assertEqual((new['1000']['Seq'],new['3000']['Seq'],new['2000']['Seq']),(1,2,3))
        self.assertEqual(new['2000']['rb_local_usn'],counter+1);self.assertEqual(new['3000']['rb_local_usn'],counter+2)
        self.assertEqual(new['4000'],old['4000']);self.assertEqual(new['5000'],old['5000'])
        self.assertEqual(after['tables']['djmdSongPlaylist'],before['tables']['djmdSongPlaylist'])

    def test_generated_nested_empty_folder_candidate_keeps_both_folder_histories_and_rejects_same_parent(self):
        conn=_connect(self.database)
        try:conn.execute("UPDATE djmdPlaylist SET ParentID='4000',Seq=4 WHERE ID='5000'")
        finally:conn.close()
        self.path.write_bytes(self.path.read_bytes().replace(b'Id="1388" ParentId="0"',b'Id="1388" ParentId="FA0"'))
        request=self.request('5000');self.writer.apply(request)
        nodes={row['ID']:row for row in self.writer.snapshot()['nodes']}
        self.assertEqual((nodes['2000']['ParentID'],nodes['2000']['Seq']),('5000',1))
        self.assertEqual((nodes['4000']['rb_local_synced'],nodes['5000']['rb_local_synced']),(1,1))
        with self.assertRaises(ValueError):self.request('5000')

    def test_stale_parent_uuid_rule_and_cloud_targets_refuse_without_publication(self):
        request=self.request('5000')
        for field,value in (('parent_uuid',fixtures.fixture.native_uuid(999)),('source_parent_uuid',fixtures.fixture.native_uuid(998)),
                            ('smart_rule','<NODE changed="1"/>')):
            changed={**request,field:value};before=self.inspect(),self.path.read_bytes()
            with self.subTest(field=field),self.assertRaises((ConflictError,WriteProofError)):self.writer.apply(changed)
            self.assertEqual((self.inspect(),self.path.read_bytes()),before)
        conn=_connect(self.database)
        try:conn.execute("UPDATE djmdPlaylist SET usn=1 WHERE ID='2000'")
        finally:conn.close()
        with self.assertRaises(ValueError):self.request('5000')

    def test_interrupted_move_recovers_exact_before_then_explicit_retry_preserves_endpoint_pins(self):
        request=self.request();before=self.inspect(),self.path.read_bytes()
        def interrupted(stage):
            if stage=='xml-published':raise RuntimeError('Held move')
        with mock.patch.object(store,'_fault',side_effect=interrupted),self.assertRaisesRegex(RuntimeError,'Held move'):
            self.writer.apply(request)
        self.assertEqual(self.writer.recover(),'rolled-back');self.assertEqual((self.inspect(),self.path.read_bytes()),before)
        renewed=self.writer.prepare_retry(request)
        for key in ('playlist_id','playlist_uuid','source_parent','source_parent_uuid','parent','parent_uuid','smart_rule'):
            self.assertEqual(renewed[key],request[key])
        self.writer.apply(renewed)

    def test_xml_move_whitelist_cannot_rename_change_rule_or_touch_unrelated_tokens(self):
        request=self.request();conn=_connect(self.database,readonly=True)
        try:proposal=store.build_plan(conn,request,self.path.read_bytes())
        finally:conn.close()
        for field,value in (('Name','Wrong'),('SmartList','Wrong'),('UUID',fixtures.fixture.native_uuid(777))):
            changes=deepcopy(proposal['changes']);changes[0]['after'][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):
                xml.validate_transition(self.path.read_bytes(),proposal['xml'],changes,smart_moved=('2000',))
        with self.assertRaises(ValueError):
            xml.validate_transition(self.path.read_bytes(),proposal['xml']+b' ',proposal['changes'],smart_moved=('2000',))
        with self.assertRaises(ValueError):
            xml.validate_transition(self.path.read_bytes(),proposal['xml'],proposal['changes'])

    def test_destination_ancestry_cycle_duplicates_unknown_rows_and_counter_overflow_refuse(self):
        nodes=self.writer.snapshot()['nodes']
        for changed in ({'ParentID':'5000'},{'usn':1},{'Attribute':0}):
            modified=deepcopy(nodes);next(row for row in modified if row['ID']=='5000').update(changed)
            with self.subTest(changed=changed):self.assertTrue(move.reason(modified,identity='2000',parent='5000'))
        request=self.request();conn=_connect(self.database)
        try:conn.execute("UPDATE agentRegistry SET int_1=? WHERE registry_id='localUpdateCount'",(2**63-1,))
        finally:conn.close()
        overflow=self.request();before=self.inspect(),self.path.read_bytes()
        with self.assertRaises(ValueError):self.writer.apply(overflow)
        self.assertEqual((self.inspect(),self.path.read_bytes()),before)
        invalid=deepcopy(request);invalid['smart_move_clocks']['source_parent_timestamp']=''
        with self.assertRaises(WriteProofError):store.validate_request(invalid)
