"""Separately gated nonempty-folder smart append over encrypted TEMP copies."""
from copy import deepcopy
from dataclasses import replace
import unittest
from unittest import mock

import test_smart_playlist_creation as fixtures
from soundbox import rekordbox_playlist_store as store, rekordbox_playlist_plan as planner
from soundbox import rekordbox_playlist_xml as xml
from soundbox.rekordbox_write import _connect, _rows, _state, WriteProofError, ConflictError


class SmartAppendCreation(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.SmartCreationProposals('test_any_is_explicit_default_for_one_or_multiple_exact_tag_bindings')
        self.f.setUp();self.addCleanup(self.f.doCleanups);self.f.add_folder()
        conn=_connect(self.f.database)
        try:
            conn.execute("UPDATE djmdPlaylist SET Attribute=0,SmartList=NULL WHERE ID='2001'")
            self.before=_state(conn);self.nodes=_rows(conn,'djmdPlaylist');self.catalog=store.catalog(conn)
        finally:conn.close()
        original=(self.f.root/'source'/'masterPlaylists6.xml').read_bytes()
        self.f.xml_path.write_bytes(original.replace(b'Id="7D1" ParentId="7D0" Attribute="4"',
                                                      b'Id="7D1" ParentId="7D0" Attribute="0"'))
        self.xml_before=self.f.xml_path.read_bytes()
        self.request=store.request_for(self.catalog,'create_smart_append',name='Appended smart',
                                      parent='2000',tags=self.f.tags)
        self.authority=replace(self.f.authority,smart_append_create_accepted=True,
                               smart_append_create_profile=store.SMART_APPEND_CREATE_PROFILE)
        self.writer=store.PlaylistStore(self.authority,self.f.root/'append-recovery')

    def inspect(self):
        conn=_connect(self.f.database,readonly=True)
        try:return _state(conn),_rows(conn,'djmdPlaylist'),store.row(conn,'agentRegistry','localUpdateCount')
        finally:conn.close()

    def test_old_front_profile_never_admits_append_and_default_is_off(self):
        for authority in (self.f.authority,replace(self.f.authority,smart_create_accepted=True,
                smart_create_profile=store.SMART_CREATE_PROFILE),
                replace(self.f.authority,smart_append_create_accepted=True)):
            writer=store.PlaylistStore(authority,self.f.root/'blocked')
            with self.subTest(authority=authority),mock.patch.object(writer.lease,'_lock',side_effect=AssertionError('source lease')):
                with self.assertRaises(WriteProofError):writer.apply(self.request)
        self.assertTrue(store.smart_creation_parent_reason(self.catalog,'2000'))
        self.assertFalse(store.smart_append_creation_parent_reason(self.catalog,'2000'))
        self.assertEqual(self.inspect()[0],self.before)

    def test_append_preserves_every_original_row_and_only_new_rule_parent_xml_counter_change(self):
        observed,edge=self.writer.apply_with_transition(self.request)
        after,nodes,counter=self.inspect();created=next(r for r in nodes if r['ID']==self.request['playlist_id'])
        self.assertEqual((created['Seq'],created['Attribute'],created['rb_local_usn'],counter['int_1']),(2,4,43,43))
        self.assertEqual(created['SmartList'],planner.smart_tag_rule(created['ID'],['201','202']))
        self.assertEqual([r for r in nodes if r['ID']!=created['ID']],self.nodes)
        for table,rows in self.before['tables'].items():
            if table not in ('djmdPlaylist','agentRegistry'):self.assertEqual(after['tables'][table],rows,table)
        expected=xml.touch(self.xml_before,'2000',self.request['smart_clocks']['parent_xml_timestamp'])
        expected=xml.append_smart_node(expected,created['ID'],'2000')
        expected=xml._touch(expected,created['ID'],self.request['xml_timestamp'],created_smart=True)
        self.assertEqual(self.f.xml_path.read_bytes(),expected)
        self.assertEqual(edge,(self.request['revision'],observed['revision']))
        before=self.f.database.read_bytes(),self.f.xml_path.read_bytes()
        self.assertEqual(self.writer.apply(self.request),observed)
        self.assertEqual((self.f.database.read_bytes(),self.f.xml_path.read_bytes()),before)

    def test_empty_root_cloud_unknown_and_noncontiguous_parents_remain_guarded(self):
        cases=[]
        empty=deepcopy(self.catalog);empty['nodes']=[r for r in empty['nodes'] if r['ParentID']!='2000'];cases.append(empty)
        for field,value in (('usn',1),('Attribute',17),('Seq',3),('UUID','bad')):
            changed=deepcopy(self.catalog);next(r for r in changed['nodes'] if r['ID']=='2001')[field]=value;cases.append(changed)
        for current in cases:
            with self.subTest(current=current):self.assertTrue(store.smart_append_creation_parent_reason(current,'2000'))
        self.assertTrue(store.smart_append_creation_parent_reason(self.catalog,'root'))
        changed=deepcopy(self.request);changed['smart_clocks']['position_timestamp']=changed['timestamp']
        with self.assertRaises(WriteProofError):store.validate_request(changed)
        changed=deepcopy(self.request);changed['smart_profile']=store.SMART_CREATE_PROFILE
        with self.assertRaises(WriteProofError):store.validate_request(changed)

    def test_fresh_tag_parent_and_catalog_guards_still_refuse(self):
        for table,identity,field,value in (('djmdMyTag','201','UUID','replaced'),
                ('djmdPlaylist','2000','UUID','replaced'),('djmdPlaylist','2001','Name','External')):
            conn=_connect(self.f.database)
            try:
                old=next(r for r in _rows(conn,table) if r['ID']==identity)[field]
                conn.execute(f'UPDATE {table} SET {field}=? WHERE ID=?',(value,identity))
            finally:conn.close()
            before=self.f.database.read_bytes(),self.f.xml_path.read_bytes()
            with self.subTest(field=field),self.assertRaises((WriteProofError,ConflictError)):
                self.writer.apply(self.request)
            self.assertEqual((self.f.database.read_bytes(),self.f.xml_path.read_bytes()),before)
            conn=_connect(self.f.database)
            try:conn.execute(f'UPDATE {table} SET {field}=? WHERE ID=?',(old,identity))
            finally:conn.close()

    def test_interrupted_append_retains_pair_and_recovery_then_explicit_retry(self):
        def interrupt(stage):
            if stage=='xml-published':raise RuntimeError('held append')
        with mock.patch.object(store,'_fault',side_effect=interrupt),self.assertRaisesRegex(RuntimeError,'held append'):
            self.writer.apply(self.request)
        self.assertEqual(self.writer.recover(),'rolled-back')
        self.assertEqual(self.inspect()[0],self.before);self.assertEqual(self.f.xml_path.read_bytes(),self.xml_before)
        renewed=self.writer.prepare_retry(self.request)
        self.assertEqual(renewed['operation'],'create_smart_append')
        self.assertEqual(renewed['smart_profile'],store.SMART_APPEND_CREATE_PROFILE)
        self.writer.apply(renewed)
