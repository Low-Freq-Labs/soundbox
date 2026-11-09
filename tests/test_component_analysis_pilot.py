"""The new source adapter runs only against explicit synthetic TEMP sources."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import test_analysis_workspace as fixtures
import test_fixture_analysis as packets
from tests.support.audio import wave
from research.native_library import component_pilot as pilot
from research.native_library import registration
from soundbox.analysis import ENGINE
from soundbox.native_media import capture_media
from research.cdj_export.playlist_transaction import _inventory, _sha
from soundbox import rekordbox_cue_store as cues, rekordbox_tags as tags
from soundbox.rekordbox_write import _connect, _json, _quote, _state, _rows, _LINK_COLUMNS, ConflictError, RecoveryRequired, WriteProofError
from soundbox.state_io import atomic_bytes


class SavedComponentsSourcePilot(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        for obj,name,value in ((pilot,'_installed_version',lambda:tags.NATIVE_VERSION),
                               (cues,'_installed_version',lambda:tags.NATIVE_VERSION),
                               (cues,'_closed',lambda:None),
                               (pilot,'assert_rekordbox_closed',lambda:None),
                               (pilot,'_apps_closed',lambda:None)):
            patch=mock.patch.object(obj,name,value);patch.start();self.addCleanup(patch.stop)
        self.source,self.folder,self.approval=self.prepare(self.root/'case')

    def prepare(self, root):
        source=root/'source'; data=fixtures.fixture(source)
        (source/'share').mkdir(mode=0o700)
        audio_path=root/'Saved components.wav';wave(audio_path,seconds=32)
        audio=registration.inspect_wave(audio_path,root/'scratch')
        from uuid import uuid4
        second_path=root/'Second saved components.wav';wave(second_path,seconds=32)
        second=registration.inspect_wave(second_path,root/'scratch-two');second_uuid=str(uuid4())
        patch=mock.patch.object(pilot,'TARGETS',{'123':(data.track['UUID'],audio.sha256,'8B',12000),
            '124':(second_uuid,second.sha256,'8B',12000)});patch.start();self.addCleanup(patch.stop)
        conn=_connect(source/'master.db')
        try:
            conn.execute('CREATE TABLE djmdSongMyTag ('+','.join('"'+c+'" TEXT' for c in _LINK_COLUMNS)+')')
            conn.execute('CREATE TABLE djmdMyTag(ID TEXT PRIMARY KEY,UUID TEXT,Attribute INTEGER,rb_local_deleted INTEGER)')
            conn.execute('UPDATE djmdContent SET FolderPath=?,FileSize=?,rb_file_id=? WHERE ID=?',
                         (audio.path,audio.epoch[2],str(audio.epoch[1]),'123'))
            row=dict(data.track,ID='124',UUID=second_uuid,FolderPath=second.path,
                     FileSize=second.epoch[2],rb_file_id=str(second.epoch[1]))
            columns=list(row)
            conn.execute('INSERT INTO djmdContent ('+','.join(_quote(c) for c in columns)+') VALUES ('+
                         ','.join('?' for _ in columns)+')',list(row.values()))
            atomic_bytes(source/'state.json',_json(_state(conn)))
        finally:conn.close()
        packet=root/'packet';packet.mkdir(mode=0o700)
        for ext,raw in packets.packet('Saved components.wav').items():atomic_bytes(packet/('ANLZ0000'+ext),raw)
        media=capture_media(audio_path,root/'captured')
        result=dict(engine=ENGINE,path=audio.path,sha256=audio.sha256,size=audio.epoch[2],epoch=list(media.epoch),
                    duration=32.0,bpm=120.0,key='8B',beats=[[i%4+1,249+500*i,120.0] for i in range(64)],first_cue=None)
        folder=root/'plan';record=pilot.prepare(source/'master.db','123',result,packet,folder,data_root=root/'appdata')
        approval=root/'approval.json'
        atomic_bytes(approval,_json(dict(format=pilot.APPROVAL_FORMAT,approved=True,plan_sha256=_sha(folder/'plan.json'),
            operation_id=record['operation_id'],source=str(source/'master.db'))))
        return source,folder,approval

    def store(self):return pilot.SavedComponentsPilot(self.folder,self.approval)

    def test_exact_packet_and_target_rows_commit_together_without_audio_xml_or_other_changes(self):
        store=self.store();before=store.plan['before'];audio=Path(store.plan['audio']['path'])
        original=audio.read_bytes();epoch=audio.stat();xml=store.xml.read_bytes();xml_epoch=store.xml.stat()
        self.assertEqual(store.apply(),'123')
        self.assertEqual(_inventory(store.database),store.plan['after'])
        self.assertNotEqual(before,store.plan['after'])
        stage,target=store._locations();self.assertFalse(stage.exists())
        self.assertEqual({p.name for p in target.iterdir()},set(pilot.offline.NAMES))
        self.assertEqual(store.xml.read_bytes(),xml);self.assertEqual(store.xml.stat().st_mtime_ns,xml_epoch.st_mtime_ns)
        self.assertEqual(audio.read_bytes(),original);self.assertEqual(audio.stat().st_ino,epoch.st_ino)
        self.assertFalse(store.journal.exists());self.assertEqual(self.store().apply(),'already-completed')
        receipt=json.loads((store.archive/'result.json').read_bytes())
        self.assertFalse(receipt['native_acceptance']);self.assertEqual(receipt['format'],pilot.FORMAT)

    def test_missing_foreign_or_changed_approval_and_running_apps_never_open_source_sql(self):
        body=json.loads(self.approval.read_bytes());body['format']='registration-permission'
        atomic_bytes(self.approval,_json(body))
        with self.assertRaises(WriteProofError):self.store()
        body['format']=pilot.APPROVAL_FORMAT;atomic_bytes(self.approval,_json(body));store=self.store()
        with mock.patch.object(pilot,'_apps_closed',side_effect=WriteProofError('owner open')), \
                mock.patch.object(store.lease,'_connect') as connection,self.assertRaises(WriteProofError):store.apply()
        connection.assert_not_called();self.assertFalse(store.journal.exists())

    def test_stale_source_audio_and_occupied_or_redirected_target_cannot_publish(self):
        store=self.store();stage,target=store._locations();target.parent.mkdir(parents=True,mode=0o700)
        target.symlink_to(self.root,target_is_directory=True)
        with self.assertRaises(RecoveryRequired):store.apply()
        target.unlink();target.mkdir(mode=0o700);(target/'unowned').write_bytes(b'keep')
        with self.assertRaises(ConflictError):store.apply()
        self.assertEqual((target/'unowned').read_bytes(),b'keep')
        (target/'unowned').unlink();target.rmdir()
        audio=Path(store.plan['audio']['path']);os.utime(audio,ns=(audio.stat().st_atime_ns,audio.stat().st_mtime_ns+1))
        with self.assertRaises(ConflictError):store.apply()
        self.assertEqual(_inventory(store.database),store.plan['before']);self.assertFalse(store.journal.exists())

    def test_changed_packet_or_other_domain_and_corrupt_recovery_never_open_source_sql(self):
        store=self.store();atomic_bytes(store.journal,b'{"format":"other-domain"}')
        with mock.patch.object(store.lease,'_connect') as connection,self.assertRaises(RecoveryRequired):store.apply()
        connection.assert_not_called();store.journal.unlink()
        def fail(phase):
            if phase=='files-published':raise RuntimeError('interrupted')
        with mock.patch.object(pilot,'_fault',side_effect=fail),self.assertRaises(RuntimeError):store.apply()
        (store.archive/'after.db').write_bytes(b'corrupt')
        with mock.patch.object(store.lease,'_connect') as connection,self.assertRaises(RecoveryRequired):store.recover()
        connection.assert_not_called();self.assertTrue(store.journal.exists())

    def test_unknown_db_xml_and_modified_owned_files_are_preserved(self):
        store=self.store()
        def fail(phase):
            if phase=='files-published':raise RuntimeError('interrupted')
        with mock.patch.object(pilot,'_fault',side_effect=fail),self.assertRaises(RuntimeError):store.apply()
        stage,target=store._locations();payload=target/'ANLZ0000.DAT';original=payload.read_bytes()
        payload.write_bytes(original+b'foreign')
        with self.assertRaises(RecoveryRequired):store.recover()
        self.assertEqual(payload.read_bytes(),original+b'foreign');self.assertTrue(store.journal.exists())
        payload.write_bytes(original);xml=store.xml.read_bytes();store.xml.write_bytes(b'foreign XML')
        with self.assertRaises(RecoveryRequired):store.recover()
        self.assertEqual(store.xml.read_bytes(),b'foreign XML');store.xml.write_bytes(xml)
        conn=_connect(store.database)
        try:conn.execute("UPDATE unknown_table SET opaque=X'66'")
        finally:conn.close()
        changed=_inventory(store.database)
        with self.assertRaises(RecoveryRequired):store.recover()
        self.assertEqual(_inventory(store.database),changed);self.assertTrue(target.exists())

    def test_scope_changed_saved_result_cannot_borrow_registration_or_other_analysis_permission(self):
        store=self.store();record=store.plan['result']
        audio=pilot.MediaIdentity.from_dict(store.plan['audio'])
        before=store.plan['before']
        with mock.patch.object(pilot,'TARGETS',{}),self.assertRaises(WriteProofError):
            pilot.load_plan(self.folder)
        changed=dict(record,key='7A')
        with self.assertRaises(WriteProofError):
            pilot._scope(audio,dict(ID='123',UUID=store.plan['track_uuid']),changed)
        self.assertEqual(_inventory(self.source/'master.db'),before)
        self.assertFalse(store.journal.exists())

    def test_component_sql_denies_gain_cue_audio_and_unrelated_metadata_updates(self):
        from sqlcipher3 import dbapi2 as sqlite3
        conn=_connect(self.source/'master.db')
        try:
            conn.set_authorizer(pilot._authorizer)
            for sql in ("UPDATE djmdContent SET Title='changed' WHERE ID='123'",
                        "UPDATE djmdContent SET Length=30 WHERE ID='123'",
                        "DELETE FROM contentFile", "DELETE FROM djmdCue",
                        "INSERT INTO djmdMixerParam(ID) VALUES ('unapproved')"):
                with self.subTest(sql=sql),self.assertRaises(sqlite3.DatabaseError):conn.execute(sql)
        finally:conn.set_authorizer(None);conn.close()
        self.assertEqual(_inventory(self.source/'master.db'),self.store().plan['before'])

    def test_two_reviewed_plans_chain_on_copies_and_preserve_both_publications(self):
        first=self.store();conn=_connect(self.source/'master.db',readonly=True)
        try:row=next(r for r in _rows(conn,'djmdContent') if r['ID']=='124')
        finally:conn.close()
        media=capture_media(Path(row['FolderPath']),self.root/'second-capture')
        result=dict(first.plan['result'],path=media.path,size=media.epoch[2],sha256=media.sha256,epoch=list(media.epoch))
        packet=self.root/'second-packet';packet.mkdir(mode=0o700)
        for ext,raw in packets.packet(Path(media.path).name).items():atomic_bytes(packet/('ANLZ0000'+ext),raw)
        folder=self.root/'second-plan'
        record=pilot.prepare(self.source/'master.db','124',result,packet,folder,
                             data_root=Path(first.plan['data_root']),predecessor=self.folder)
        self.assertEqual(first.plan['after'],record['before'])
        self.assertEqual(_inventory(self.source/'master.db'),first.plan['before'])
        approval=self.root/'second-approval.json'
        atomic_bytes(approval,_json(dict(format=pilot.APPROVAL_FORMAT,approved=True,plan_sha256=_sha(folder/'plan.json'),
            operation_id=record['operation_id'],source=str(self.source/'master.db'))))
        second=pilot.SavedComponentsPilot(folder,approval)
        with self.assertRaises(ConflictError):second.apply()
        self.assertEqual(first.apply(),'123');self.assertEqual(second.apply(),'124')
        self.assertEqual(_inventory(self.source/'master.db'),record['after'])
        self.assertTrue(first._locations()[1].exists());self.assertTrue(second._locations()[1].exists())

    def test_real_exit_recovers_directory_and_sql_wal_spill_and_receipt_boundaries(self):
        script='''
import os,sys
from pathlib import Path
from research.native_library import component_pilot as p
from soundbox import rekordbox_cue_store as c,rekordbox_tags as t
p.TARGETS={'123':(sys.argv[4],sys.argv[5],'8B',12000)}
p._apps_closed=lambda:None;c._closed=lambda:None;p._installed_version=lambda:t.NATIVE_VERSION;c._installed_version=lambda:t.NATIVE_VERSION
w=p.SavedComponentsPilot(Path(sys.argv[1]),Path(sys.argv[2]))
original=w.lease._connect
def connect():
 conn=original()
 if sys.argv[6]=='wal':conn.execute('PRAGMA journal_mode=WAL')
 if sys.argv[6]=='spill':conn.execute('PRAGMA cache_size=1');conn.execute('PRAGMA cache_spill=1')
 return conn
w.lease._connect=connect
def crash(phase):
 if phase==sys.argv[3]:os._exit(73)
p._fault=crash;w.apply()
'''
        cases=[(p,'delete') for p in ('journal-ready','stage-created',*(f'file-{n}' for n in pilot.offline.NAMES),
            'files-published','sql-applied','sql-committed','receipt-ready','journal-retired')]
        cases += [('sql-applied','wal'),('sql-committed','wal'),('sql-applied','spill')]
        for index,(phase,mode) in enumerate(cases):
            with self.subTest(phase=phase,mode=mode):
                source,folder,approval=self.prepare(self.root/str(index))
                store=pilot.SavedComponentsPilot(folder,approval)
                result=subprocess.run([sys.executable,'-c',script,str(folder),str(approval),phase,store.plan['track_uuid'],
                    store.plan['audio']['sha256'],mode],capture_output=True,text=True,timeout=40)
                self.assertEqual(result.returncode,73,result.stderr)
                outcome=store.recover()
                expected='committed' if phase in ('sql-committed','receipt-ready','journal-retired') else 'rolled-back'
                self.assertEqual(outcome,expected)
                self.assertEqual(_inventory(source/'master.db'),store.plan['after' if expected=='committed' else 'before'])
                stage,target=store._locations();self.assertFalse(stage.exists())
                self.assertEqual(target.exists(),expected=='committed');self.assertFalse(store.journal.exists())


if __name__=='__main__':unittest.main()
