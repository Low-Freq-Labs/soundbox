"""Production planners share proven defaults without granting source writes."""
from copy import deepcopy
from pathlib import Path
import shutil
import tempfile
import unittest

from soundbox import native_media as media,native_registration_plan as planner
from soundbox.rekordbox_write import ConflictError,WriteProofError,_connect,_state
from soundbox.native_paths import capture_aliases, AliasProof
from test_registration_proof import fixture,wave


class NativeRegistrationPlan(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name).resolve()
        self.frozen=self.root/'frozen';self.frozen.mkdir();fixture(self.frozen)
        self.path=self.root/'source.wav';wave(self.path)
        self.facts=media.capture_media(self.path,self.root/'scratch')
        self.conn=_connect(self.frozen/'master.db');self.addCleanup(self.conn.close)

    def claim(self,local='sb-original',facts=None,identity='2',uuid='00000000-0000-4000-8000-000000000002'):
        return planner.make_claim(local,facts or self.facts,native_id=identity,native_uuid=uuid,
                                  timestamp='2026-10-31 04:00:00.123 +00:00',day='2026-10-31')

    def request(self,claims=None):
        return dict(format=planner.REQUEST_FORMAT,operation_id='00000000-0000-4000-8000-000000000099',
                    source=str(self.frozen/'master.db'),library_id='123',schema_sha256='0'*64,
                    tracks=claims or [self.claim()])

    def native(self,*,deleted=0,uuid='00000000-0000-4000-8000-000000000003',file_type=11):
        self.conn.execute('UPDATE djmdContent SET FolderPath=?,UUID=?,FileType=?,MasterDBID=?,rb_local_deleted=? WHERE ID=?',
                          (str(self.path),uuid,file_type,'123',deleted,'1'))

    def test_new_batch_uses_original_inode_proven_defaults_and_preserves_opaque_counter(self):
        second=self.root/'other.wav';shutil.copyfile(self.path,second);facts=media.capture_media(second,self.root/'scratch')
        request=self.request([self.claim(),self.claim('sb-other',facts,'3','00000000-0000-4000-8000-000000000004')])
        before=_state(self.conn);plan=planner.build_plan(self.conn,request)
        self.assertEqual(_state(self.conn),before)
        self.assertEqual([b['local_id'] for b in plan['bindings']],['sb-original','sb-other'])
        self.assertEqual(plan['changes'][-1]['after']['int_1'],42)
        self.assertEqual(plan['changes'][-1]['after']['opaque'],{'blob':'00ff'})
        for change in plan['changes'][::2]:
            row=change['after'];self.assertEqual((row['BPM'],row['KeyID'],row['Analysed']),(0,'0',0))
        self.assertEqual(plan['changes'][0]['after']['rb_file_id'],str(self.path.stat().st_ino))

    def test_exact_native_path_reuses_identity_without_any_changes_or_new_counters(self):
        self.native();before=_state(self.conn);plan=planner.build_plan(self.conn,self.request())
        self.assertEqual(plan['changes'],[]);self.assertFalse(plan['bindings'][0]['registered'])
        self.assertEqual(plan['bindings'][0]['native_id'],'1');self.assertEqual(plan['bindings'][0]['local_id'],'sb-original')
        self.assertEqual(len(plan['guards']),1);self.assertEqual(_state(self.conn),before)

    def test_captured_symlink_alias_reuses_raw_native_identity_and_rejects_redirects(self):
        alias=self.root/'legacy';alias.symlink_to(self.root,target_is_directory=True)
        raw=str(alias/self.path.name)
        self.native();self.conn.execute("UPDATE djmdContent SET FolderPath=? WHERE ID='1'",(raw,))
        row=dict(zip([r[1] for r in self.conn.execute('PRAGMA table_info(djmdContent)')],
                     self.conn.execute("SELECT * FROM djmdContent WHERE ID='1'").fetchone(),strict=True))
        proofs=capture_aliases([row],self.facts)
        request=self.request();request['tracks'][0]['aliases']=proofs
        before=_state(self.conn);plan=planner.build_plan(self.conn,request)
        self.assertEqual(plan['changes'],[]);self.assertEqual(plan['bindings'][0]['native_path'],raw)
        self.assertEqual(plan['bindings'][0]['native_id'],'1');self.assertEqual(_state(self.conn),before)
        alias.unlink();elsewhere=self.root/'elsewhere';elsewhere.mkdir();alias.symlink_to(elsewhere,target_is_directory=True)
        with self.assertRaises((ConflictError,OSError)):AliasProof.from_dict(proofs[0]).revalidate()
        bad=deepcopy(request);bad['tracks'][0]['aliases'][0]['native_uuid']='00000000-0000-4000-8000-000000000077'
        with self.assertRaises(ConflictError):planner.build_plan(self.conn,bad)

    def test_historical_duplicate_path_wrong_file_type_and_colliding_seeds_fail_closed(self):
        for change in (dict(deleted=1),dict(file_type=6),dict(uuid='invalid')):
            self.native(**change)
            with self.assertRaises(WriteProofError):planner.build_plan(self.conn,self.request())
        self.native();self.conn.execute('UPDATE djmdContent SET FolderPath=? WHERE ID=?',('/other/path.wav','1'))
        request=self.request();request['tracks'][0]['native_id']='1'
        with self.assertRaises(ConflictError):planner.build_plan(self.conn,request)

    def test_transferred_native_song_keeps_its_producer_identity_without_duplicate_registration(self):
        self.native();self.conn.execute("UPDATE djmdContent SET MasterDBID='456' WHERE ID='1'")
        before=_state(self.conn);result=planner.build_plan(self.conn,self.request())
        self.assertEqual(result['changes'],[]);self.assertEqual(result['guards'][0]['row']['MasterDBID'],'456')
        self.assertEqual(result['bindings'][0]['library_id'],'123');self.assertEqual(_state(self.conn),before)

    def test_request_tampering_and_duplicate_logical_or_path_identity_are_rejected(self):
        original=self.request();before=_state(self.conn)
        for change in ('extra','clock','path','duplicate','library'):
            value=deepcopy(original)
            if change=='extra':value['unexpected']=True
            elif change=='clock':value['tracks'][0]['timestamp']='not a clock'
            elif change=='path':value['tracks'][0]['audio']['path']='/bad/../source.wav'
            elif change=='duplicate':value['tracks'].append(deepcopy(value['tracks'][0]))
            else:value['library_id']='different'
            with self.assertRaises(WriteProofError):planner.build_plan(self.conn,value)
        self.assertEqual(_state(self.conn),before)
