"""Accepted playlist commands share one exact native transaction, never SQL scope."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import os
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from test_playlist_transaction import fixture
from soundbox import rekordbox_playlist_store as store,rekordbox_cue_store as cues
from soundbox.rekordbox_tags import _schema,NATIVE_VERSION
from soundbox.rekordbox_write import _connect,_state,_json,ConflictError,WriteProofError,RecoveryRequired


class PlaylistBatches(unittest.TestCase):
    def setUp(self):
        self.area=tempfile.TemporaryDirectory();self.addCleanup(self.area.cleanup)
        self.root=Path(self.area.name).resolve();self.source=self.root/'source';self.source.mkdir();fixture(self.source)
        database=self.source/'master.db';conn=_connect(database,readonly=True)
        try:schema=_schema(conn)
        finally:conn.close()
        self.authority=store.PlaylistAuthority(str(database),'synthetic-playlists',schema,True)
        self.writer=store.PlaylistStore(self.authority,self.root/'recovery')
        for name,value in (('_closed',None),('_installed_version',NATIVE_VERSION)):
            patch=mock.patch.object(cues,name,return_value=value);patch.start();self.addCleanup(patch.stop)

    def state(self,writer=None):
        conn=_connect((writer or self.writer).database,readonly=True)
        try:return _state(conn)
        finally:conn.close()

    def batch(self,writer=None):
        writer=writer or self.writer;catalog=writer.snapshot();planning=deepcopy(catalog);commands=[]
        for name,parent,folder in (('Artists','root',True),('One',None,False),('Two',None,False)):
            parent=parent or commands[0]['playlist_id']
            command=store.request_for(planning,'create',name=name,parent=parent,folder=folder)
            commands.append(command)
            planning['nodes'].append(dict(ID=command['playlist_id'],UUID=command['playlist_uuid']))
            if not folder:
                tracks=[SimpleNamespace(track_id='track',source_uuid='uuid'),
                        SimpleNamespace(track_id='track-two',source_uuid='uuid-two')]
                commands.append(store.request_for(planning,'append',identity=command['playlist_id'],tracks=tracks))
        return store.batch_for(catalog,commands)

    def test_bounded_batch_equals_exact_sequential_accepted_commands_and_replays(self):
        other=self.root/'sequential';shutil.copytree(self.source,other)
        sequential=store.PlaylistStore(replace(self.authority,database=str(other/'master.db')),self.root/'other-recovery')
        before=self.state();request=self.batch()
        result=self.writer.apply(request)
        for child in request['requests']:
            sequential.apply({**child,'revision':sequential.snapshot()['revision']})
        self.assertEqual(self.state(),self.state(sequential))
        self.assertEqual(self.writer.xml.read_bytes(),sequential.xml.read_bytes())
        for table,digest in before['tables'].items():
            if table not in ('djmdPlaylist','djmdSongPlaylist','agentRegistry'):
                self.assertEqual(self.state()['tables'][table],digest,table)
        self.assertEqual(self.writer.apply(request),result)
        later=store.request_for(self.writer.snapshot(),'rename',identity=request['requests'][1]['playlist_id'],name='Later')
        self.writer.apply(later);newer=self.state()
        self.writer.apply(request)
        self.assertEqual(self.state(),newer)
        with self.assertRaises(ConflictError):self.writer.apply(request['requests'][0])

    def test_every_existing_publication_crash_prefix_recovers_and_retries_the_batch(self):
        for phase in ('journal-ready','sql-applied','xml-published','sql-committed'):
            with self.subTest(phase=phase):
                source=self.root/phase;shutil.copytree(self.source,source)
                writer=store.PlaylistStore(replace(self.authority,database=str(source/'master.db')),self.root/(phase+'-recovery'))
                before=self.state(writer);xml=writer.xml.read_bytes();request=self.batch(writer)
                def fault(point):
                    if point==phase:raise RuntimeError('simulated interruption')
                with mock.patch.object(store,'_fault',side_effect=fault),self.assertRaisesRegex(RuntimeError,'simulated'):
                    writer.apply(request)
                outcome=writer.recover()
                if phase=='sql-committed':
                    self.assertEqual(outcome,'committed');self.assertEqual(writer.prepare_retry(request),request)
                    saved=self.state(writer);writer.apply(request);self.assertEqual(self.state(writer),saved)
                else:
                    self.assertEqual(outcome,'rolled-back');self.assertEqual(self.state(writer),before)
                    self.assertEqual(writer.xml.read_bytes(),xml)
                    renewed=writer.prepare_retry(request)
                    self.assertNotEqual(renewed['operation_id'],request['operation_id'])
                    clocks=[child['xml_timestamp'] for child in renewed['requests']]
                    self.assertEqual(clocks,sorted(set(clocks)))
                    store.validate_request(renewed);writer.apply(renewed)
                self.assertFalse(writer.journal.exists())

    def test_invalid_child_identity_scope_revision_and_size_fail_before_native_write(self):
        request=self.batch();before=self.state();xml=self.writer.xml.read_bytes()
        mutations=(lambda r:r['requests'].append(deepcopy(r['requests'][0])),
            lambda r:r['requests'][0].update(revision='0'*64),
            lambda r:r['requests'][0].update(operation='delete'),
            lambda r:r.update(requests=[deepcopy(r['requests'][0])]*65),
            lambda r:r['requests'][0].update(extra_sql='DROP TABLE djmdContent'),
            lambda r:r.update(requests=[deepcopy(r)]))
        for mutate in mutations:
            bad=deepcopy(request);mutate(bad)
            with self.assertRaises(WriteProofError):self.writer.apply(bad)
        changed=deepcopy(request);changed['requests'][2]['tracks'][0]['uuid']='wrong-native-uuid'
        with self.assertRaises(ConflictError):self.writer.apply(changed)
        self.assertEqual(self.state(),before);self.assertEqual(self.writer.xml.read_bytes(),xml)
        self.assertFalse(self.writer.journal.exists())

    def test_verified_plan_memo_still_rechecks_payload_journal_scope_and_closed_apps(self):
        request=self.batch()
        def fault(phase):
            if phase=='journal-ready':raise RuntimeError('interrupt')
        with mock.patch.object(store,'_fault',side_effect=fault),self.assertRaises(RuntimeError):self.writer.apply(request)
        record=self.writer._record()
        with mock.patch.object(store,'build_plan',side_effect=AssertionError('Exact verified plan need not be reconstructed')):
            self.assertEqual(self.writer._record(),record)
            archive=self.writer.root/record['archive'];payload=archive/'before.xml';original=payload.read_bytes()
            payload.write_bytes(original+b'\n')
            with self.assertRaisesRegex(RecoveryRequired,'backup failed verification'):self.writer._record()
            payload.write_bytes(original)
            authority=self.writer.authority
            self.writer.authority=replace(authority,pilot_playlist_ids=('1000',))
            with self.assertRaises(WriteProofError):self.writer._record()
            self.writer.authority=authority
            with mock.patch.object(cues,'_closed',side_effect=WriteProofError('Close native apps')):
                with self.assertRaisesRegex(WriteProofError,'Close native apps'):self.writer.lease._guard()
        changed=deepcopy(record);changed['request']['requests'][0]['name']='Unproven name'
        operation=archive/'operation.json';operation.write_bytes(_json(changed));self.writer.journal.write_bytes(_json(changed))
        with self.assertRaisesRegex(RecoveryRequired,'exceeds its original intent'):self.writer._record()
        operation.write_bytes(_json(record));self.writer.journal.write_bytes(_json(record))
        fresh=store.PlaylistStore(self.authority,self.writer.root)
        with mock.patch.object(store,'build_plan',wraps=store.build_plan) as rebuild:
            fresh._record();rebuild.assert_called_once()
        self.assertEqual(self.writer.recover(),'rolled-back')

    def test_xml_parse_cache_is_exact_immutable_bounded_and_scoped_to_one_plan(self):
        from soundbox import rekordbox_playlist_xml as xml
        payload=self.writer.xml.read_bytes()
        with mock.patch.object(xml,'_parse_uncached',wraps=xml._parse_uncached) as parse:
            with xml.parsing_session():
                rows,_=xml._parse(payload);xml._parse(bytes(payload));self.assertEqual(parse.call_count,1)
                with self.assertRaises(TypeError):rows[1000][0]['Timestamp']='999'
                with self.assertRaises(TypeError):rows[1000]=None
                for number in range(5):xml._parse(payload+b'\n'*(number+1))
                xml._parse(payload);self.assertEqual(parse.call_count,7)
            with xml.parsing_session():xml._parse(payload)
            self.assertEqual(parse.call_count,8)

    def test_transaction_parse_reuse_reads_changed_xml_despite_restored_mtime(self):
        from soundbox import rekordbox_playlist_xml as xml
        for operation in ('create','reorder','batch'):
            with self.subTest(operation=operation):
                self.writer.authority=replace(self.authority,reorder_accepted=True)
                catalog=self.writer.snapshot()
                if operation=='reorder':
                    anchor=store.request_for(catalog,'create',name='Anchor')
                    catalog=self.writer.apply(anchor)
                    request=store.request_for(catalog,'reorder',identity='1000',
                                              anchor_id=anchor['playlist_id'],after=True)
                elif operation=='batch':request=self.batch()
                else:request=store.request_for(catalog,'create',name='Fresh bytes')
                before=self.state();payload=self.writer.xml.read_bytes();info=self.writer.xml.stat()
                changed=payload.replace(b'unknown note',b'changed note')
                source_connect=self.writer.lease._connect
                def change_after_staging():
                    connection=source_connect()
                    self.writer.xml.write_bytes(changed)
                    os.utime(self.writer.xml,ns=(info.st_atime_ns,info.st_mtime_ns))
                    return connection
                with mock.patch.object(self.writer.lease,'_connect',side_effect=change_after_staging), \
                        mock.patch.object(xml,'_parse_uncached',wraps=xml._parse_uncached) as parse:
                    with self.assertRaisesRegex(ConflictError,'XML changed before publication'):
                        self.writer.apply(request)
                    self.assertIn(mock.call(changed),parse.call_args_list)
                self.assertEqual(self.state(),before)
                self.assertFalse(self.writer.journal.exists())
                self.assertIsNone(xml._PARSE_SESSION.get())
                self.writer.xml.write_bytes(payload)

    def test_standalone_recovery_rechecks_changed_xml_after_cached_apply(self):
        from soundbox import rekordbox_playlist_xml as xml
        request=self.batch();payload=self.writer.xml.read_bytes();info=self.writer.xml.stat()
        def fault(phase):
            if phase=='journal-ready':raise RuntimeError('interrupt')
        with mock.patch.object(store,'_fault',side_effect=fault),self.assertRaises(RuntimeError):
            self.writer.apply(request)
        self.assertIsNone(xml._PARSE_SESSION.get())
        self.writer.xml.write_bytes(payload.replace(b'unknown note',b'changed note'))
        os.utime(self.writer.xml,ns=(info.st_atime_ns,info.st_mtime_ns))
        with self.assertRaisesRegex(RecoveryRequired,'XML changed after interrupted save'):
            self.writer.recover()
        self.assertTrue(self.writer.journal.exists())
        self.assertIsNone(xml._PARSE_SESSION.get())
        self.writer.xml.write_bytes(payload)
        self.assertEqual(self.writer.recover(),'rolled-back')

    def test_nested_parse_scope_shares_immutable_bytes_and_cleans_up_on_failure(self):
        from soundbox import rekordbox_playlist_xml as xml
        payload=self.writer.xml.read_bytes()
        with mock.patch.object(xml,'_parse_uncached',wraps=xml._parse_uncached) as parse:
            with xml.parsing_session():
                outer=xml._PARSE_SESSION.get();xml._parse(payload)
                with self.assertRaisesRegex(RuntimeError,'nested'):
                    with xml.parsing_session():
                        self.assertIs(xml._PARSE_SESSION.get(),outer)
                        xml._parse(payload);raise RuntimeError('nested')
                self.assertIs(xml._PARSE_SESSION.get(),outer)
                xml._parse(payload);self.assertEqual(parse.call_count,1)
            self.assertIsNone(xml._PARSE_SESSION.get())
            with xml.parsing_session():xml._parse(payload)
            self.assertEqual(parse.call_count,2)
