"""Generated encrypted-copy native order proof; no source acceptance/provisioning."""
from copy import deepcopy
from dataclasses import asdict,replace
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from soundbox import rekordbox_tag_definitions as definitions,rekordbox_tag_order as order
from soundbox import rekordbox_write as writer
from soundbox.library import LibraryTag
from tests import test_rekordbox_tag_definitions as fixture


CLOCKS=[['2026-11-19 09:16:09.241 +00:00','2026-11-19 09:16:09.246 +00:00']]


class NativeTagOrder(unittest.TestCase):
    def setUp(self):
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.source=self.root/'source/master.db';self.source.parent.mkdir()
        conn=fixture.fixture(self.source)
        conn.execute("UPDATE djmdMyTag SET UUID='00000000-0000-4000-8000-000000000011' WHERE ID='11'")
        conn.execute("UPDATE djmdMyTag SET UUID='00000000-0000-4000-8000-000000000012' WHERE ID='12'")
        self.schema=definitions._schema(conn);conn.close()
        self.enterContext(mock.patch.object(definitions,'_assert_closed'))
        self.enterContext(mock.patch.object(definitions,'_installed_version',return_value=order.NATIVE_VERSION))
        self.authority=order.TagOrderAuthority(str(self.source),'synthetic-library',self.schema,
            accepted=True,protocol_profile=order.NATIVE_ORDER_PROFILE.profile)
        self.store=order.TagOrderStore(self.authority,self.root/'recovery')
        self.tags=[LibraryTag('11','Dubstep','Genre','00000000-0000-4000-8000-000000000011','10','category'),
                   LibraryTag('12','Riddim','Genre','00000000-0000-4000-8000-000000000012','10','category')]
        self.request=order.request_for('synthetic-library',self.tags,['12','11'],step_clocks=CLOCKS)

    def snapshot(self):
        conn=writer._connect(self.source,readonly=True)
        try:
            return writer._state(conn),{table:writer._rows(conn,table) for table in ('djmdMyTag','agentRegistry')}
        finally:conn.close()

    def test_clock_memo_preserves_the_original_parser_for_valid_invalid_and_subclass_values(self):
        def original(value):
            if not isinstance(value,str):return False
            try:
                parsed=datetime.strptime(value,'%Y-%m-%d %H:%M:%S.%f +00:00')
                return parsed.year>=1970 and parsed.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]+' +00:00'==value
            except ValueError:return False
        class UnhashableClock(str):
            __hash__=None
        samples=[*CLOCKS[0],'1970-01-01 00:00:00.000 +00:00','2024-02-29 23:59:59.999 +00:00',
            '1969-12-31 23:59:59.999 +00:00','2025-02-29 00:00:00.000 +00:00',
            '2026-11-19 09:16:09.2410 +00:00','2026-11-19 09:16:09.24 +00:00',
            '2026-11-19 09:16:09.241 +01:00','2026-11-19 09:16:60.000 +00:00',
            'x'*10000,None,False,[],{},1,UnhashableClock(CLOCKS[0][0])]
        order._clock_string.cache_clear();self.addCleanup(order._clock_string.cache_clear)
        for value in samples:
            with self.subTest(value=repr(value)[:80]):
                self.assertEqual(order._clock(value),original(value))
                self.assertEqual(order._clock(value),original(value))
        before=order._clock_string.cache_info()
        order._clock(UnhashableClock(CLOCKS[0][0]));order._clock('x'*10000)
        self.assertEqual(order._clock_string.cache_info(),before)
        for index in range(3000):order._clock(str(index).zfill(30))
        self.assertEqual(order._clock_string.cache_info().currsize,2048)

    def test_creation_deletion_or_disabled_authority_cannot_enable_tag_order(self):
        path=self.root/'authority.json'
        for authority in (definitions.TagDefinitionAuthority(str(self.source),'synthetic-library',self.schema,
                accepted=True,protocol_profile=definitions.NATIVE_CREATE_PROFILE.profile),
                definitions.TagDeletionAuthority(str(self.source),'synthetic-library',self.schema,
                accepted=True,protocol_profile=definitions.NATIVE_HISTORY_DELETE_PROFILE.profile)):
            path.write_text(json.dumps(asdict(authority)));self.assertIsNone(order.load_authority(path,self.source,'synthetic-library'))
            with self.assertRaises(writer.WriteProofError):order.TagOrderStore(authority,self.root/'foreign')
        for authority in (replace(self.authority,accepted=False),replace(self.authority,protocol_profile='other')):
            path.write_text(json.dumps(asdict(authority)));self.assertIsNone(order.load_authority(path,self.source,'synthetic-library'))
            store=order.TagOrderStore(authority,self.root/'disabled')
            with mock.patch.object(store.lease,'_connect') as connect:
                with self.assertRaises(writer.WriteProofError):store.apply(self.request)
                connect.assert_not_called()

    def test_adjacent_native_projection_changes_only_two_positions_clocks_and_local_usns(self):
        before,rows=self.snapshot();original=deepcopy(rows)
        category=next(row for row in rows['djmdMyTag'] if row['ID']=='10')
        siblings=[row for row in rows['djmdMyTag'] if row['ParentID']=='10']
        counter=next(row for row in rows['agentRegistry'] if row['registry_id']=='localUpdateCount')
        changes=order.plan_order(self.request,category,siblings,counter,order.NATIVE_ORDER_PROFILE)
        self.assertEqual([row['key'] for row in changes],['12','11','localUpdateCount'])
        for index,(change,clock) in enumerate(zip(changes[:2],CLOCKS[0],strict=True),1):
            self.assertEqual(change['after'],{**change['before'],'Seq':index,'updated_at':clock,'rb_local_usn':counter['int_1']+index})
        self.assertEqual(changes[-1]['after'],{**counter,'int_1':counter['int_1']+2})
        self.assertEqual(rows,original);self.assertEqual(self.snapshot()[0],before)

    def test_actual_transaction_preserves_all_other_tables_registry_rows_category_history_and_xml(self):
        xml=self.source.with_name('masterPlaylists6.xml');xml.write_bytes(b'<synthetic-preserved/>')
        before,rows=self.snapshot();request=deepcopy(self.request)
        result=self.store.apply(self.request);after,changed=self.snapshot()
        self.assertEqual(result['ID'],'12');self.assertEqual(self.request,request)
        self.assertEqual({table for table in before['tables'] if before['tables'][table]!=after['tables'][table]},
                         {'djmdMyTag','agentRegistry'})
        self.assertEqual((before['schema'],before['metadata']),(after['schema'],after['metadata']))
        untouched=lambda inventory:[row for row in inventory['djmdMyTag'] if row['ID'] not in ('11','12')]
        self.assertEqual(untouched(rows),untouched(changed))
        before_tags={row['ID']:row for row in rows['djmdMyTag']};after_tags={row['ID']:row for row in changed['djmdMyTag']}
        for identity,position,clock,usn in (('12',1,CLOCKS[0][0],42),('11',2,CLOCKS[0][1],43)):
            self.assertEqual(after_tags[identity],{**before_tags[identity],'Seq':position,'updated_at':clock,'rb_local_usn':usn})
        for old,new in zip(rows['agentRegistry'],changed['agentRegistry'],strict=True):
            self.assertEqual(new,{**old,'int_1':43} if old['registry_id']=='localUpdateCount' else old)
        self.assertEqual(xml.read_bytes(),b'<synthetic-preserved/>');self.assertFalse(self.store.journal.exists())
        self.assertNotEqual((self.store.root/'baseline.db').read_bytes()[:16],b'SQLite format 3\0')
        self.assertEqual(self.store.apply(self.request),result);self.assertEqual(self.snapshot()[0],after)

    def test_exact_order_names_uuid_category_and_sync_profile_are_fresh_fences(self):
        for sql in ("UPDATE djmdMyTag SET UUID='replacement' WHERE ID='11'",
                    "UPDATE djmdMyTag SET Name='Other' WHERE ID='11'",
                    "UPDATE djmdMyTag SET Seq=7 WHERE ID='11'",
                    "UPDATE djmdMyTag SET UUID='replacement' WHERE ID='10'",
                    "UPDATE djmdMyTag SET usn=NULL WHERE ID='11'",
                    "UPDATE djmdMyTag SET rb_local_synced=0 WHERE ID='11'"):
            with self.subTest(sql=sql):
                conn=writer._connect(self.source);conn.execute('BEGIN');conn.execute(sql)
                category=self.store._row(conn,'djmdMyTag','10')
                siblings=self.store.lease._rows(conn,'djmdMyTag',' WHERE ParentID=? ORDER BY ID',('10',))
                counter=self.store._row(conn,'agentRegistry','localUpdateCount')
                with self.assertRaises(writer.WriteProofError):order.plan_order(self.request,category,siblings,counter,order.NATIVE_ORDER_PROFILE)
                conn.rollback();conn.close()

    def test_queued_capture_is_exact_and_cross_category_or_unsupported_large_gesture_is_refused(self):
        changed=deepcopy(self.request);changed['before'][0]['uuid']='replacement'
        self.assertNotEqual(changed,self.request)
        other=LibraryTag('21','Other','Components','00000000-0000-4000-8000-000000000021','20','other-category')
        with self.assertRaisesRegex(writer.WriteProofError,'between categories'):
            order.request_for('synthetic-library',[*self.tags,other],['12','21','11'])
        for value in ({**self.request,'action':'create'}, {**self.request,'order':['11','12']},
                      {**self.request,'clocks':[['not a clock','not a clock']]},
                      {**self.request,'tag_uuid':'replacement'}):
            with self.subTest(value=value),self.assertRaises(writer.WriteProofError):order.validate_request(value)

    def test_independent_adjacent_composition_is_atomic_and_preserves_non_position_fields(self):
        conn=writer._connect(self.source)
        conn.execute("INSERT INTO djmdMyTag SELECT '14',3,'Third',Attribute,ParentID,'00000000-0000-4000-8000-000000000014',rb_data_status,rb_local_data_status,rb_local_deleted,rb_local_synced,usn,rb_local_usn,created_at,updated_at FROM djmdMyTag WHERE ID='11'")
        conn.close()
        self.tags.append(LibraryTag('14','Third','Genre','00000000-0000-4000-8000-000000000014','10','category'))
        request=order.request_for('synthetic-library',self.tags,['14','11','12'],step_clocks=CLOCKS*2)
        before,rows=self.snapshot();self.store.apply(request);after,changed=self.snapshot()
        active=sorted((row for row in changed['djmdMyTag'] if row['ParentID']=='10' and not row['rb_local_deleted']),key=lambda row:row['Seq'])
        self.assertEqual([row['ID'] for row in active],['14','11','12'])
        counter=next(row for row in changed['agentRegistry'] if row['registry_id']=='localUpdateCount')
        self.assertEqual(counter['int_1'],45)
        self.assertEqual({name for name in before['tables'] if before['tables'][name]!=after['tables'][name]}, {'djmdMyTag','agentRegistry'})
        for original in rows['djmdMyTag']:
            current=next(row for row in changed['djmdMyTag'] if row['ID']==original['ID'])
            self.assertEqual({key:value for key,value in current.items() if key not in ('Seq','rb_local_usn','updated_at')},
                             {key:value for key,value in original.items() if key not in ('Seq','rb_local_usn','updated_at')})

    def test_crash_before_commit_rolls_back_and_after_commit_recovers_exact_order_without_reapply(self):
        for phase in ('before-commit','after-commit'):
            with self.subTest(phase=phase):
                before=self.snapshot()[0]
                def fault(current):
                    if current==phase:raise RuntimeError('Generated interruption')
                with mock.patch.object(definitions,'_fault',side_effect=fault),self.assertRaises(RuntimeError):self.store.apply(self.request)
                self.assertTrue(self.store.journal.exists())
                recovered=self.store.recover()
                self.assertEqual(recovered,'rolled-back' if phase=='before-commit' else 'committed')
                if phase=='before-commit':self.assertEqual(self.snapshot()[0],before)
                else:
                    after=self.snapshot()[0];self.store.apply(self.request);self.assertEqual(self.snapshot()[0],after)

    def test_authorizer_refuses_assignments_definitions_deletion_and_foreign_clock_mutation(self):
        for action,table,column in ((sqlite3.SQLITE_UPDATE,'djmdSongMyTag','MyTagID'),
                (sqlite3.SQLITE_DELETE,'djmdMyTag',None),(sqlite3.SQLITE_INSERT,'djmdMyTag',None),
                (sqlite3.SQLITE_UPDATE,'djmdMyTag','Name'),(sqlite3.SQLITE_UPDATE,'agentRegistry','updated_at')):
            self.assertEqual(order.TagOrderStore._authorizer(action,table,column,'main',None),sqlite3.SQLITE_DENY)

    def test_new_local_and_synced_null_remote_rows_require_separately_observed_v2_profile(self):
        conn=writer._connect(self.source)
        conn.execute('UPDATE djmdMyTag SET rb_data_status=0,usn=NULL,rb_local_synced=0 WHERE ID=?',('12',))
        conn.execute('UPDATE djmdMyTag SET rb_data_status=0,usn=NULL,rb_local_synced=1 WHERE ID=?',('11',))
        conn.close();before,rows=self.snapshot()
        historical=order.TagOrderStore(replace(self.authority,protocol_profile=order.HISTORICAL_ORDER_PROFILE.profile),self.root/'old-profile')
        with self.assertRaises(writer.WriteProofError):historical.apply(self.request)
        self.assertEqual(self.snapshot()[0],before)
        self.store.apply(self.request);after,changed=self.snapshot()
        for identity,synced in (('11',1),('12',0)):
            row=next(row for row in changed['djmdMyTag'] if row['ID']==identity)
            self.assertEqual((row['rb_data_status'],row['rb_local_synced'],row['usn']),(0,synced,None))
        self.assertEqual({name for name in before['tables'] if before['tables'][name]!=after['tables'][name]}, {'djmdMyTag','agentRegistry'})

    def test_reviewed_whole_source_guard_runs_under_lease_before_recovery_or_source_sql(self):
        before,_=self.snapshot();stale=deepcopy(before);stale['tables']['djmdContent']='0'*64
        with mock.patch.object(self.store,'_recover') as recover,mock.patch.object(self.store.lease,'_connect') as connect:
            with self.assertRaises(writer.ConflictError):self.store.apply(self.request,expected_state=stale)
            recover.assert_not_called();connect.assert_not_called()
        self.assertEqual(self.snapshot()[0],before)
        self.store.apply(self.request,expected_state=before)
        with self.assertRaises(writer.RecoveryRequired):self.store.apply(self.request,expected_state=self.snapshot()[0])
