"""Completed device payload retirement uses generated private directories only."""
import hashlib
import json
import os
import struct
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from soundbox import stock_delete
from soundbox.sync import DeviceSession
from soundbox import transaction_outcome as outcome


class RecoveryPayloadRetirement(unittest.TestCase):
    def setUp(self):
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.session=DeviceSession(self.root,[],create=False)
        self.directory=Path('.soundbox/stock-ipod-recovery/generated-private-transaction')
        self.evidence=outcome.begin_recovery(self.session,self.directory)
        self.session.atomic_write(self.directory/'plan.json',b'{"transaction_state":"executing"}\n')
        self.paths=[self.directory/'iTunesDB.before',self.directory/'media/original.before']
        for index,path in enumerate(self.paths):self.session.atomic_write(path,bytes([index+1])*65536)
        self.evidence=self.evidence.remember(self.session,self.paths)

    def complete(self,state='committed'):
        outcome.record_outcome(self.session,self.directory,state)

    def seal_retirement(self, *, files=None, directories=None):
        self.complete()
        plan=json.loads((self.root/self.directory/'plan.json').read_bytes())
        plan['payload_retirement']={
            'format':outcome._RETIREMENT,'complete':False,
            'mount_identity':self.evidence.mount_identity,
            'directory_identity':self.evidence.directory_identity,
            'files':([dict(path=str(item.relative_path),epoch=item.epoch,parent=item.parent_identity)
                      for item in self.evidence.files] if files is None else files),
            'directories':([dict(path=str(path),identity=identity)
                            for path,identity in self.evidence.directories] if directories is None else directories),
        }
        self.session.atomic_write(self.directory/'plan.json',json.dumps(plan).encode())
        return plan['payload_retirement']

    def test_resumed_traversal_refuses_all_disposal_before_descriptor_guard_is_needed(self):
        outside=Path('foreign-original.wav');original=self.root/outside
        original.write_bytes(b'unrelated device audio remains exact')
        traversal=self.directory/'../../../foreign-original.wav'
        self.assertTrue(traversal.is_relative_to(self.directory))  # Insufficient old guard.
        # Descriptor publication already refuses traversal independently. The
        # complete resumed record must fail before earlier valid rows retire.
        with self.assertRaisesRegex(RuntimeError,'Invalid device publication path'):
            stock_delete.capture(self.session,traversal)
        captured=stock_delete.capture(self.session,outside)
        row=self.seal_retirement()
        row['files'].append(dict(path=str(traversal),epoch=captured.epoch,parent=captured.parent_identity))
        self.seal_retirement(files=row['files'])
        before=original.read_bytes();identity=stock_delete.capture(self.session,outside)
        with mock.patch.object(stock_delete,'retire',wraps=stock_delete.retire) as retirement:
            warnings=outcome.resume_completed_payload_retirement(self.session,'stock-ipod-recovery')
        self.assertEqual(len(warnings),1);self.assertIn('private transaction child',warnings[0])
        retirement.assert_not_called()
        self.assertEqual(original.read_bytes(),before)
        self.assertEqual(stock_delete.capture(self.session,outside),identity)
        self.assertTrue(all((self.root/path).is_file() for path in self.paths))

    def test_resumed_file_and_directory_evidence_rejects_invalid_paths_before_disposal(self):
        row=self.seal_retirement()
        invalid=[self.directory/'../foreign',self.directory/'media/../../foreign',
                 self.root/'foreign',self.directory,self.directory/'media\\..\\foreign']
        for field in ('files','directories'):
            for path in invalid+([self.directory/'plan.json'] if field=='files' else []):
                changed=json.loads(json.dumps(row))
                changed[field].append(dict(path=str(path),**(
                    {'epoch':self.evidence.files[0].epoch,'parent':self.evidence.files[0].parent_identity}
                    if field=='files' else {'identity':self.evidence.directory_identity})))
                with self.subTest(field=field,path=path):
                    self.seal_retirement(files=changed['files'],directories=changed['directories'])
                    with mock.patch.object(stock_delete,'retire',wraps=stock_delete.retire) as retirement:
                        warnings=outcome.resume_completed_payload_retirement(self.session,'stock-ipod-recovery')
                    self.assertEqual(len(warnings),1);retirement.assert_not_called()
                    self.assertTrue(all((self.root/item).is_file() for item in self.paths))

    def test_resumed_unknown_inventory_is_preserved_before_any_known_payload_disposal(self):
        self.seal_retirement();foreign=self.root/self.directory/'foreign-original'
        foreign.write_bytes(b'unknown contents')
        with mock.patch.object(stock_delete,'retire',wraps=stock_delete.retire) as retirement:
            warnings=outcome.resume_completed_payload_retirement(self.session,'stock-ipod-recovery')
        self.assertEqual(len(warnings),1);self.assertIn('unknown',warnings[0])
        retirement.assert_not_called();self.assertEqual(foreign.read_bytes(),b'unknown contents')
        self.assertTrue(all((self.root/path).is_file() for path in self.paths))

    def test_resumed_stale_file_or_directory_identity_refuses_before_disposal(self):
        row=self.seal_retirement()
        cases=[]
        for field,cell in (('files','epoch'),('files','parent'),('directories','identity')):
            changed=json.loads(json.dumps(row));changed[field][0][cell][1]+=1;cases.append((field,cell,changed))
        malformed=json.loads(json.dumps(row));malformed['files'][0]['epoch']=[]
        cases.append(('files','malformed epoch',malformed))
        for field,cell,changed in cases:
            with self.subTest(field=field,cell=cell):
                self.seal_retirement(files=changed['files'],directories=changed['directories'])
                with mock.patch.object(stock_delete,'retire',wraps=stock_delete.retire) as retirement:
                    warnings=outcome.resume_completed_payload_retirement(self.session,'stock-ipod-recovery')
                self.assertEqual(len(warnings),1);retirement.assert_not_called()
                self.assertTrue(all((self.root/path).is_file() for path in self.paths))

    def test_success_removes_payloads_without_reading_them_and_keeps_compact_receipt(self):
        self.complete();original_open=Path.open
        def opened(path,*args,**kwargs):
            if path in {self.root/path for path in self.paths}:raise AssertionError('Payload was read during cleanup')
            return original_open(path,*args,**kwargs)
        with (mock.patch.object(Path,'open',opened),
              mock.patch.object(hashlib,'file_digest',side_effect=AssertionError('No payload digest required'))):
            self.evidence.retire(self.session)
        self.assertEqual([path.relative_to(self.root/self.directory).as_posix()
                          for path in (self.root/self.directory).rglob('*')],['plan.json'])
        receipt=json.loads((self.root/self.directory/'plan.json').read_bytes())
        self.assertTrue(receipt['payload_retirement']['complete'])
        self.assertEqual(receipt['payload_retirement']['retired_bytes'],2*65536)
        self.assertLess((self.root/self.directory/'plan.json').stat().st_size,2048)

    def test_unfinished_and_unknown_or_replaced_entities_are_preserved(self):
        with self.assertRaisesRegex(RuntimeError,'Unfinished'):self.evidence.retire(self.session)
        self.complete();foreign=self.root/self.directory/'foreign-original'
        foreign.write_bytes(b'keep unrelated contents')
        with self.assertRaisesRegex(RuntimeError,'[Uu]nknown'):self.evidence.retire(self.session)
        self.assertEqual(foreign.read_bytes(),b'keep unrelated contents');foreign.unlink()
        path=self.root/self.paths[0];replacement=self.root/'replacement'
        replacement.write_bytes(path.read_bytes());replacement.replace(path)
        with self.assertRaises(stock_delete.IndexedDeleteError):self.evidence.retire(self.session)
        self.assertEqual(path.read_bytes(),b'\x01'*65536)
        self.assertTrue((self.root/self.paths[1]).exists())

    def test_foreign_leaf_arriving_during_claim_is_restored_and_never_disposed(self):
        self.complete();original=stock_delete._exclusive_move;changed=[]
        def replace(parent,name,holding,claim):
            if name=='iTunesDB.before' and not changed:
                changed.append(True);path=self.root/self.paths[0]
                replacement=self.root/'replacement';replacement.write_bytes(b'foreign replacement')
                replacement.replace(path)
            return original(parent,name,holding,claim)
        with mock.patch.object(stock_delete,'_exclusive_move',side_effect=replace):
            with self.assertRaises(stock_delete.IndexedDeleteError):self.evidence.retire(self.session)
        self.assertEqual((self.root/self.paths[0]).read_bytes(),b'foreign replacement')
        self.assertTrue((self.root/self.paths[1]).exists())

    def test_partial_cleanup_resumes_from_its_bound_metadata_without_payload_reads(self):
        self.complete();original=stock_delete.retire;calls=[]
        def interrupted(session,item,claim):
            calls.append(item.relative_path)
            if len(calls)==2:raise OSError('Generated cleanup interruption')
            return original(session,item,claim)
        with mock.patch.object(stock_delete,'retire',side_effect=interrupted):
            with self.assertRaisesRegex(OSError,'interruption'):self.evidence.retire(self.session)
        self.assertFalse((self.root/self.paths[0]).exists());self.assertTrue((self.root/self.paths[1]).exists())
        self.assertEqual(outcome.resume_completed_payload_retirement(self.session,'stock-ipod-recovery'),())
        self.assertFalse((self.root/self.paths[1]).exists())
        self.assertTrue(json.loads((self.root/self.directory/'plan.json').read_bytes())['payload_retirement']['complete'])

    def test_crash_after_empty_directory_removal_resumes_without_recreating_claims(self):
        self.complete();original=DeviceSession.atomic_write
        def interrupted(session,destination,data):
            if Path(destination)==self.directory/'plan.json' and json.loads(data).get('payload_retirement',{}).get('complete') is True:
                raise SystemExit('Generated crash before final completion receipt')
            return original(session,destination,data)
        with mock.patch.object(DeviceSession,'atomic_write',interrupted):
            with self.assertRaises(SystemExit):self.evidence.retire(self.session)
        self.assertFalse((self.root/self.directory/'.retiring').exists())
        self.assertFalse(any((self.root/path).exists() for path in self.paths))
        self.assertEqual(outcome.resume_completed_payload_retirement(self.session,'stock-ipod-recovery'),())
        self.assertTrue(json.loads((self.root/self.directory/'plan.json').read_bytes())['payload_retirement']['complete'])

    def test_only_captured_valid_private_appledouble_companions_are_retired(self):
        directory=self.directory.with_name('generated-os-companions')
        evidence=outcome.begin_recovery(self.session,directory)
        self.session.atomic_write(directory/'plan.json',b'{"transaction_state":"executing"}')
        payload=directory/'catalog/old.before';self.session.atomic_write(payload,b'previous catalog')
        companion=struct.pack('>II16sHIII',0x00051607,0x00020000,b'Mac OS X        ',1,9,38,32)+b'\0'*32
        for path in (directory/'._catalog',directory/'catalog/._old.before',directory/'._plan.json'):
            self.session.atomic_write(path,companion)
        evidence=evidence.remember(self.session,(payload,))
        outcome.record_outcome(self.session,directory,'committed');evidence.retire(self.session)
        self.assertEqual({path.relative_to(self.root/directory).as_posix() for path in (self.root/directory).rglob('*')},
                         {'plan.json','._plan.json'})
        self.complete();foreign=self.root/self.directory/'media/._original.before'
        foreign.write_bytes(b'foreign content with an OS-like filename')
        with self.assertRaisesRegex(RuntimeError,'unknown'):self.evidence.retire(self.session)
        self.assertEqual(foreign.read_bytes(),b'foreign content with an OS-like filename')

    def test_new_claim_directory_appledouble_is_captured_before_retirement(self):
        self.complete();original=os.mkdir
        companion=struct.pack('>II16sHIII',0x00051607,0x00020000,b'Mac OS X        ',1,9,38,32)+b'\0'*32
        def with_os_metadata(path,*args,**kwargs):
            result=original(path,*args,**kwargs)
            if path=='.retiring':
                (self.root/self.directory/'._.retiring').write_bytes(companion)
            return result
        with mock.patch.object(os,'mkdir',side_effect=with_os_metadata):self.evidence.retire(self.session)
        self.assertEqual({path.relative_to(self.root/self.directory).as_posix()
                          for path in (self.root/self.directory).rglob('*')},{'plan.json'})

    def test_rolled_back_payloads_retire_but_unproved_legacy_archives_do_not(self):
        self.complete('rolled back');self.evidence.retire(self.session)
        legacy=Path('.soundbox/stock-ipod-recovery/legacy-completed')
        self.session.atomic_write(legacy/'plan.json',b'{"transaction_state":"committed"}')
        self.session.atomic_write(legacy/'iTunesDB.before',b'preserve legacy unknown outer ownership')
        with mock.patch.object(outcome.os,'scandir',side_effect=AssertionError('No historical directory walk')):
            warnings=outcome.resume_completed_payload_retirement(self.session,'stock-ipod-recovery')
        self.assertEqual(warnings,())
        self.assertEqual((self.root/legacy/'iTunesDB.before').read_bytes(),b'preserve legacy unknown outer ownership')

    def test_crash_after_claim_preserves_both_original_and_foreign_claims_with_honest_notice(self):
        self.complete();original=stock_delete._exclusive_move;claimed=[]
        def interrupted(parent,name,holding,claim):
            result=original(parent,name,holding,claim)
            if name=='iTunesDB.before':
                claimed.append(claim)
                raise SystemExit('Generated crash after claim')
            return result
        with mock.patch.object(stock_delete,'_exclusive_move',side_effect=interrupted):
            with self.assertRaises(SystemExit):self.evidence.retire(self.session)
        path=self.root/self.directory/'.retiring'/claimed[0]
        self.assertEqual(path.read_bytes(),b'\x01'*65536)
        warnings=outcome.resume_completed_payload_retirement(self.session,'stock-ipod-recovery')
        self.assertEqual(len(warnings),1);self.assertIn('Recovery required',warnings[0])
        self.assertEqual(path.read_bytes(),b'\x01'*65536)
        replacement=self.root/'foreign';replacement.write_bytes(b'foreign claimed entity');replacement.replace(path)
        warnings=outcome.resume_completed_payload_retirement(self.session,'stock-ipod-recovery')
        self.assertEqual(len(warnings),1);self.assertIn('Recovery required',warnings[0])
        self.assertEqual(path.read_bytes(),b'foreign claimed entity')

    def test_namespace_is_exclusive_and_symlink_or_directory_replacement_cannot_be_retired(self):
        with self.assertRaises(FileExistsError):outcome.begin_recovery(self.session,self.directory)
        self.complete();directory=self.root/self.directory
        moved=directory.with_name('preserved');directory.rename(moved);directory.mkdir()
        with self.assertRaisesRegex(RuntimeError,'directory changed'):self.evidence.retire(self.session)
        directory.rmdir();directory.symlink_to(moved,target_is_directory=True)
        with self.assertRaises((OSError,RuntimeError)):self.evidence.retire(self.session)
        self.assertTrue((moved/'iTunesDB.before').exists())
