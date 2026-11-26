"""Bounded setup observations and explicit empty CDJ configuration on fake USBs."""
import os
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest import mock

from soundbox import cdj_export,cdj_transaction as tx,cdj_usb,my_settings
from soundbox.operation_control import OperationCancelled
from tests import test_cdj_transaction as faults


class CdjSetup(unittest.TestCase):
    def setUp(self):
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.target=self.root/'USB';self.target.mkdir();self.token=self.target.stat().st_dev
        self.volume=str(uuid.uuid4());self.recovery=self.root/'receipts';self.plans=[]
        self.addCleanup(lambda:[plan.close() for plan in self.plans])

    def put(self,name,raw):
        path=self.target/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw);return path

    def probe(self,role='generic_usb'):
        return cdj_usb.setup_readiness(self.target,token=self.token,volume_uuid=self.volume,target_role=role)

    def plan(self,proof=None,role='generic_usb',**kwargs):
        plan=cdj_export.plan_initialize(self.target,token=self.token,volume_uuid=self.volume,
            target_role=role,expected_setup=proof,recovery_root=self.recovery,**kwargs)
        self.plans.append(plan);return plan

    def assert_no_catalog(self):
        self.assertTrue(all(not(self.target/path).exists() for path in cdj_usb.DATABASE_FILES))
        self.assertFalse((self.target/tx.JOURNAL).exists())

    def test_probe_reads_only_bounded_metadata_names_and_fixed_stamps(self):
        self.put('PIONEER/MYSETTING.DAT',b'existing settings')
        with (mock.patch.object(cdj_usb.CdjReadSession,'read',side_effect=AssertionError('Payload read')),
              mock.patch.object(cdj_usb,'read_library',side_effect=AssertionError('Catalog parse')),
              mock.patch.object(cdj_usb,'run_renderer',side_effect=AssertionError('Renderer')),
              mock.patch.object(Path,'read_bytes',side_effect=AssertionError('Payload read'))):
            proof=self.probe()
        self.assertEqual(proof.state,'missing');self.assertEqual(proof.mount_identity,(self.token,self.target.stat().st_ino))
        self.assertIn('PIONEER/MYSETTING.DAT',dict(proof.stamps));self.assert_no_catalog()

    def test_existing_catalogs_even_partial_or_empty_suppress_configuration(self):
        for name in cdj_usb.DATABASE_FILES:
            with self.subTest(name=name):
                path=self.put(name,b'')
                self.assertEqual(self.probe().state,'configured')
                with self.assertRaises(ValueError):self.plan()
                self.assertEqual(path.read_bytes(),b'');path.unlink()

    def test_pending_sqlite_companions_and_transaction_never_look_unconfigured(self):
        for name in (*(cdj_usb.DATABASE_FILES[2]+suffix for suffix in ('-wal','-shm','-journal')),tx.JOURNAL):
            with self.subTest(name=name):
                path=self.put(name,b'pending')
                self.assertEqual(self.probe().state,'unavailable')
                with self.assertRaises(ValueError):self.plan()
                self.assertEqual(path.read_bytes(),b'pending');path.unlink()

    def test_owned_manifest_without_catalogs_is_recovery_not_a_blank_usb(self):
        path=self.put(tx.MANIFEST,b'{"owned prior library":true}');before=path.read_bytes()
        with mock.patch.object(cdj_export,'run_renderer',side_effect=AssertionError('Orphan setup rendered')):
            self.assertEqual(self.probe().state,'unavailable')
            with self.assertRaises(ValueError):self.plan()
        self.assertEqual(path.read_bytes(),before);self.assert_no_catalog()

    def test_alias_symlink_and_oversized_parent_refuse_without_payloads(self):
        pioneer=self.target/'pioneer';pioneer.mkdir()
        self.assertEqual(self.probe().state,'unavailable');pioneer.rmdir()
        outside=self.root/'outside';outside.mkdir();(self.target/'PIONEER').symlink_to(outside,target_is_directory=True)
        self.assertEqual(self.probe().state,'unavailable');(self.target/'PIONEER').unlink()
        self.put('one',b'one');self.put('two',b'two')
        with mock.patch.object(cdj_usb,'_SETUP_DIRECTORY_LIMIT',1):self.assertEqual(self.probe().state,'unavailable')
        with mock.patch.object(cdj_usb.CdjReadSession,'__init__',side_effect=OSError('unavailable')):
            self.assertEqual(self.probe().state,'unavailable')

    def test_setup_preserves_existing_profiles_and_unrelated_files_and_adds_missing_defaults(self):
        custom=my_settings.new_files({'MYSETTING.DAT':{'tempo_range':'ten'}})['MYSETTING.DAT']
        profile=self.put('PIONEER/MYSETTING.DAT',custom)
        display=self.put('PIONEER/DEVSETTING.DAT',cdj_usb.patch_display(cdj_usb.default_display(),{'waveform_color':'RGB'}))
        opaque=self.put('PIONEER/djprofile.nxs',b'opaque existing player profile')
        unrelated=self.put('personal/notes.txt',b'keep this original')
        before={p:(p.read_bytes(),p.stat().st_ino,p.stat().st_mtime_ns) for p in (profile,display,opaque,unrelated)}
        proof=self.probe()
        with mock.patch('soundbox.cdj_source.capture_metadata',side_effect=AssertionError('Setup read source SQL')):
            plan=self.plan(proof,profile={'MYSETTING.DAT':{'tempo_range':'six'}})
        self.assertFalse(any(change.path in ('PIONEER/MYSETTING.DAT','PIONEER/DEVSETTING.DAT') for change in plan.changes))
        self.assertEqual(len(plan.setup_profile_hashes),3);tx.commit(plan)
        native=cdj_usb.read_library(self.target,self.token)
        self.assertFalse(native.songs);self.assertFalse(native.playlists)
        self.assertEqual({p:(p.read_bytes(),p.stat().st_ino,p.stat().st_mtime_ns) for p in before},before)
        self.assertTrue(all((self.target/'PIONEER'/name).is_file() for name in cdj_usb.PROFILE_FILES))
        self.assertEqual(self.probe().state,'configured')

    def test_ipod_usb_setup_preserves_stock_and_rockbox_trees(self):
        protected=[]
        for name in ('iPod_Control/iTunes/iTunesDB','iPod_Control/Artwork/F1060_1.ithmb',
                     'iPod_Control/Music/F00/OLD.wav','.rockbox/config.cfg','.soundbox/manifest.json'):
            protected.append(self.put(name,name.encode()))
        before={p:(p.read_bytes(),p.stat().st_ino,p.stat().st_mtime_ns) for p in protected}
        self.assertEqual(self.probe().state,'unavailable')
        proof=self.probe('ipod_usb');self.assertEqual(proof.state,'missing')
        plan=self.plan(proof,'ipod_usb');tx.commit(plan)
        self.assertEqual(plan.ipod_control_identity,proof.ipod_control_identity)
        self.assertEqual({p:(p.read_bytes(),p.stat().st_ino,p.stat().st_mtime_ns) for p in before},before)

    def test_changed_profile_after_question_refuses_without_initializing(self):
        path=self.put('PIONEER/MYSETTING.DAT',b'before');proof=self.probe();path.write_bytes(b'after')
        with self.assertRaisesRegex(ValueError,'changed after the setup question'):self.plan(proof)
        self.assertEqual(path.read_bytes(),b'after');self.assert_no_catalog()

    def test_profile_edit_during_render_is_not_overwritten_or_adopted(self):
        raw=my_settings.new_files({})['MYSETTING.DAT'];path=self.put('PIONEER/MYSETTING.DAT',raw)
        proof=self.probe();render=cdj_export.run_renderer
        def changed(*args,**kwargs):
            result=render(*args,**kwargs);new=bytearray(path.read_bytes());new[-1]^=1;path.write_bytes(new);return result
        with mock.patch.object(cdj_export,'run_renderer',side_effect=changed):
            with self.assertRaisesRegex(ValueError,'settings changed'):self.plan(proof)
        self.assertNotEqual(path.read_bytes(),raw);self.assert_no_catalog()

    def test_profile_edit_after_plan_is_a_commit_precondition_even_without_change_row(self):
        path=self.put('PIONEER/MYSETTING.DAT',my_settings.new_files({})['MYSETTING.DAT'])
        plan=self.plan(self.probe());before=path.stat();changed=bytearray(path.read_bytes());changed[-1]^=1
        path.write_bytes(changed);os.utime(path,ns=(before.st_atime_ns,before.st_mtime_ns))
        with self.assertRaisesRegex(ValueError,'settings changed after setup'):tx.commit(plan)
        self.assertEqual(path.read_bytes(),bytes(changed));self.assert_no_catalog()

    def test_catalog_appearing_after_question_or_plan_refuses_without_overwrite(self):
        proof=self.probe();path=self.put(cdj_usb.DATABASE_FILES[0],b'foreign catalog')
        with self.assertRaises(ValueError):self.plan(proof)
        self.assertEqual(path.read_bytes(),b'foreign catalog');path.unlink()
        plan=self.plan(self.probe());path.write_bytes(b'new foreign catalog')
        with self.assertRaisesRegex(ValueError,'new destination is occupied'):tx.commit(plan)
        self.assertEqual(path.read_bytes(),b'new foreign catalog');self.assertFalse((self.target/tx.JOURNAL).exists())

    def test_catalog_appearing_during_render_or_final_plan_is_preserved(self):
        renderer=cdj_export.run_renderer;make_plan=cdj_export.make_plan
        path=self.target/cdj_usb.DATABASE_FILES[0]
        def rendered(*args,**kwargs):
            value=renderer(*args,**kwargs);self.put(cdj_usb.DATABASE_FILES[0],b'foreign during render');return value
        with mock.patch.object(cdj_export,'run_renderer',side_effect=rendered):
            with self.assertRaisesRegex(ValueError,'changed during setup'):self.plan(self.probe())
        self.assertEqual(path.read_bytes(),b'foreign during render');path.unlink()
        def planned(*args,**kwargs):
            self.put(cdj_usb.DATABASE_FILES[0],b'foreign during per-file planning');return make_plan(*args,**kwargs)
        with mock.patch.object(cdj_export,'make_plan',side_effect=planned):
            with self.assertRaisesRegex(ValueError,'catalog appeared during setup'):self.plan(self.probe())
        self.assertEqual(path.read_bytes(),b'foreign during per-file planning')
        self.assertFalse((self.target/tx.JOURNAL).exists())
        self.assertTrue(all(not(self.target/name).exists() for name in cdj_usb.DATABASE_FILES[1:]))

    def test_reroot_remount_and_role_changes_after_question_refuse(self):
        proof=self.probe();self.target.rename(self.root/'held');self.target.mkdir()
        with self.assertRaisesRegex(ValueError,'changed after the setup question'):self.plan(proof)
        self.assertEqual(cdj_usb.setup_readiness(self.target,token=self.token+1,volume_uuid=self.volume).state,'unavailable')
        with self.assertRaises(ValueError):self.plan(proof,'ipod_usb')
        self.assert_no_catalog()

    def test_cancel_and_crash_recovery_restore_absence_without_touching_profiles(self):
        raw=my_settings.new_files({})['MYSETTING.DAT'];path=self.put('PIONEER/MYSETTING.DAT',raw)
        proof=self.probe()
        with self.assertRaises(OperationCancelled):self.plan(proof,should_cancel=lambda:True)
        self.assert_no_catalog();plan=self.plan(proof)
        with self.assertRaises(faults.SimulatedCrash):
            tx.commit(plan,on_progress=lambda *_:(_ for _ in ()).throw(faults.SimulatedCrash()))
        session=tx.CdjSession(self.target,[],token=self.token,volume_uuid=self.volume)
        tx.recover(session,self.recovery)
        self.assert_no_catalog();self.assertEqual(path.read_bytes(),raw)
