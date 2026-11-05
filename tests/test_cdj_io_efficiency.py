"""Bounded catalog capture and linear local recovery proofs, on private fixtures."""
from collections import Counter
from dataclasses import replace
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from scripts.check_fixtures import cdj_reference
from soundbox import cdj_export, cdj_incremental, cdj_transaction as tx, cdj_usb
from tests import test_cdj_transaction as fixtures
from tests import test_cdj_integration as export_fixtures


class CatalogCaptureTests(unittest.TestCase):
    def setUp(self):
        area = tempfile.TemporaryDirectory()
        self.addCleanup(area.cleanup)
        self.target = Path(area.name).resolve()/'USB'
        shutil.copytree(cdj_reference()/'native', self.target)
        self.token = self.target.stat().st_dev

    def test_reads_each_catalog_and_profile_twice_to_verify_coarse_filesystem_epochs(self):
        reads = Counter()
        original = cdj_usb.CdjReadSession.read
        def capture(session, path, **kwargs):
            reads[path] += 1
            return original(session, path, **kwargs)
        with patch.object(cdj_usb.CdjReadSession, 'read', capture):
            snapshot = cdj_usb.read_library(self.target, self.token)
        self.assertGreater(len(snapshot.songs), 0)
        for path, _raw in snapshot.files:
            self.assertEqual(reads[path], 2, path)

    def test_equal_epochs_do_not_bless_changed_catalog_profile_or_wal_bytes(self):
        for relative in (cdj_usb.DATABASE_FILES[0], 'PIONEER/MYSETTING.DAT',
                         cdj_usb.DATABASE_FILES[2]+'-wal'):
            with self.subTest(relative=relative):
                destination=self.target/relative
                previous=destination.read_bytes() if destination.exists() else None
                if previous is None:destination.write_bytes(b'captured WAL bytes')
                original_read=cdj_usb.CdjReadSession.read
                original_stamp=cdj_usb.CdjReadSession.stamp
                stamps={};reads=Counter();mutated=False
                def stamp(session,path):
                    if path not in stamps:stamps[path]=original_stamp(session,path)
                    return stamps[path]
                def capture(session,path,**kwargs):
                    nonlocal mutated
                    raw=original_read(session,path,**kwargs)
                    reads[path]+=1
                    if path==relative and not mutated:
                        mutated=True
                        destination.write_bytes(bytes([raw[0]^1])+raw[1:])
                    return raw
                try:
                    with patch.object(cdj_usb.CdjReadSession,'read',capture), \
                         patch.object(cdj_usb.CdjReadSession,'stamp',stamp), \
                         patch.object(cdj_usb,'run_renderer',side_effect=AssertionError('Parsed changed bytes')):
                        with self.assertRaisesRegex(ValueError,'changed during capture'):
                            cdj_usb.read_library(self.target,self.token,snapshot_only=True)
                    self.assertTrue(mutated)
                    self.assertEqual(reads[relative],2)
                finally:
                    if previous is None:destination.unlink()
                    else:destination.write_bytes(previous)

    def test_new_companion_catalog_or_profile_during_capture_is_rejected(self):
        for relative in (cdj_usb.DATABASE_FILES[2]+'-wal', cdj_usb.DATABASE_FILES[1],
                         'PIONEER/djprofile.nxs'):
            with self.subTest(relative=relative):
                path = self.target/relative
                original_bytes = path.read_bytes() if path.exists() else None
                path.unlink(missing_ok=True)
                original = cdj_usb.CdjReadSession.read
                def capture(session, current, **kwargs):
                    raw = original(session, current, **kwargs)
                    if current == cdj_usb.DATABASE_FILES[2]:
                        path.write_bytes(original_bytes or b'new native companion')
                    return raw
                with patch.object(cdj_usb.CdjReadSession, 'read', capture), \
                        patch.object(cdj_usb, 'run_renderer', side_effect=AssertionError('Parsed an unstable capture')):
                    with self.assertRaisesRegex(ValueError, 'changed during capture'):
                        cdj_usb.read_library(self.target, self.token, snapshot_only=True)
                if original_bytes is None:path.unlink()
                else:path.write_bytes(original_bytes)

    def test_same_bytes_replacement_still_invalidates_the_capture_epoch(self):
        original = cdj_usb.CdjReadSession.read
        def capture(session, current, **kwargs):
            raw = original(session, current, **kwargs)
            if current == cdj_usb.DATABASE_FILES[2]:
                destination = self.target/cdj_usb.DATABASE_FILES[0]
                replacement = destination.with_suffix('.replacement')
                replacement.write_bytes(destination.read_bytes())
                replacement.replace(destination)
            return raw
        with patch.object(cdj_usb.CdjReadSession, 'read', capture):
            with self.assertRaisesRegex(ValueError, 'changed during capture'):
                cdj_usb.read_library(self.target, self.token)

    def test_cancel_before_capture_starts_no_payload_read_or_renderer(self):
        with patch.object(cdj_usb.CdjReadSession, 'read', side_effect=AssertionError('Read after cancel')), \
                patch.object(cdj_usb, 'run_renderer', side_effect=AssertionError('Rendered after cancel')):
            with self.assertRaisesRegex(RuntimeError, 'cancelled'):
                cdj_usb.read_library(self.target, self.token, should_cancel=lambda:True)

    def test_inspection_receives_the_operation_cancellation_callback(self):
        cancel = lambda:False
        original = cdj_usb.run_renderer
        with patch.object(cdj_usb, 'run_renderer', wraps=original) as renderer:
            cdj_usb.read_library(self.target, self.token, should_cancel=cancel, check_pair=True)
        self.assertEqual([call.args[0] for call in renderer.call_args_list], ['inspect','catalog-check'])
        self.assertTrue(all(call.kwargs['should_cancel'] is cancel for call in renderer.call_args_list))


class RecoveryContentTests(unittest.TestCase):
    def setUp(self):
        # Reuse setup helpers without inheriting/recollecting their test methods.
        self.fixture = fixtures.CdjTransactionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def receipt(self, plan):
        case = self.fixture
        return case.root/'receipts'/case.volume/(plan.manifest['generation']+'.json')

    def test_commit_writes_one_intent_receipt_without_per_asset_acceleration_records(self):
        case = self.fixture
        for index in range(24):
            case.put(case.candidate/f'PIONEER/Artwork/00001/a{index}.jpg', b'fixture-cover')
        plan = case.plan()
        tx.commit(plan)
        receipt = self.receipt(plan)
        self.assertEqual(list(receipt.parent.iterdir()),[receipt])
        self.assertEqual(len(json.loads(receipt.read_bytes())['changes']),len(plan.changes))

    def interrupted(self):
        case = self.fixture
        plan = case.plan()
        def crash(*_args):raise fixtures.SimulatedCrash()
        with self.assertRaises(fixtures.SimulatedCrash):
            tx.commit(plan,on_progress=crash)
        return plan

    def legacy_proofs(self, plan):
        case = self.fixture
        receipt = self.receipt(plan)
        relative = 'Contents/Artist/track.wav'
        change = next(c for c in plan.changes if c.path==relative)
        observation = list(tx._identity((case.target/relative).stat()))
        proof = dict(sha256=change.new_sha256,observation=observation)
        records = {
            receipt.with_suffix('.completed.json'):tx._json(dict(schema=1,volume_uuid=case.volume,
                generation=plan.manifest['generation'],files={relative:proof})),
            receipt.with_suffix('.completed.jsonl'):tx._json(dict(schema=1,volume_uuid=case.volume,
                generation=plan.manifest['generation'],path=relative,**proof))+b'{"torn":'}
        for path,raw in records.items():path.write_bytes(raw)
        return observation,records

    def test_old_maps_and_torn_ledgers_are_preserved_but_never_replace_exact_recovery_hashes(self):
        case = self.fixture
        plan = self.interrupted()
        _observation,records = self.legacy_proofs(plan)
        with patch.object(tx,'_hash_file',wraps=tx._hash_file) as hashes:
            tx.recover(case.session,case.root/'receipts',retain_new=True)
        self.assertIn(case.target/'Contents/Artist/track.wav',[call.args[0] for call in hashes.call_args_list])
        self.assertEqual({path:path.read_bytes() for path in records},records)
        case.assert_old_generation(reusable=True)

    def test_equal_stat_changed_audio_is_not_blessed_by_saved_completed_records(self):
        case = self.fixture
        plan = self.interrupted()
        observation,_records = self.legacy_proofs(plan)
        audio = case.target/'Contents/Artist/track.wav'
        raw = audio.read_bytes()
        audio.write_bytes(bytes([raw[0]^1])+raw[1:])
        before = {p:p.read_bytes() for p in case.target.rglob('*') if p.is_file()}
        # Model the equal complete epochs observed on actual FAT32/exFAT, rather
        # than assuming APFS's finer timestamps establish USB safety.
        with patch.object(tx,'_identity',return_value=tuple(observation)):
            with self.assertRaisesRegex(ValueError,'Ambiguous CDJ recovery'):
                tx.recover(case.session,case.root/'receipts',retain_new=True)
        self.assertEqual({p:p.read_bytes() for p in case.target.rglob('*') if p.is_file()},before)

    def test_equal_stat_abandoned_audio_is_rehashed_before_reusable_adoption(self):
        case = self.fixture
        self.interrupted()
        tx.recover(case.session,case.root/'receipts',retain_new=True)
        audio = case.target/'Contents/Artist/track.wav'
        observation = tx._identity(audio.stat())
        raw = audio.read_bytes()
        audio.write_bytes(bytes([raw[0]^1])+raw[1:])
        before = {p:p.read_bytes() for p in case.target.rglob('*') if p.is_file()}
        with patch.object(tx,'_identity',return_value=observation):
            with self.assertRaisesRegex(ValueError,'Unowned CDJ file conflicts'):
                case.plan()
        self.assertEqual({p:p.read_bytes() for p in case.target.rglob('*') if p.is_file()},before)

    def test_cancellation_during_baseline_hash_stops_before_any_usb_publication(self):
        case = self.fixture
        plan = case.plan()
        cancelling = False
        original = tx._hash_file
        def hash_file(path, cancel=lambda:False):
            nonlocal cancelling
            cancelling = True
            return original(path,cancel)
        with patch.object(tx,'_hash_file',hash_file):
            with self.assertRaisesRegex(RuntimeError,'cancelled'):
                tx.commit(plan,should_cancel=lambda:cancelling)
        case.assert_old_generation()
        self.assertFalse((case.target/tx.JOURNAL).exists())


class BoundTrackExportTests(unittest.TestCase):
    def setUp(self):
        self.fixture = export_fixtures.CdjIntegrationTests()
        self.fixture.setUpClass()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        case = self.fixture
        track = replace(case.lib.tracks_by_id['17'],track_id='sb-stable',source_native_id='17')
        case.lib.tracks_by_id = {track.track_id:track}
        case.playlist.keys = [track.track_id,track.track_id]

    def test_initial_repeat_and_changed_export_keep_native_identity_and_logical_binding(self):
        case = self.fixture
        first = case.plan()
        tx.commit(first)
        repeat = case.plan()
        self.assertFalse(repeat.requires_commit)
        self.assertEqual(repeat.changes,())
        case.request['tracks'][0]['title'] = 'Changed logical track title'
        update = case.plan()
        tx.commit(update)
        song, = cdj_usb.read_library(case.target,case.token).songs
        self.assertEqual(song.source_id,'17')
        self.assertEqual(song.title,'Changed logical track title')
        self.assertEqual(case.playlist.keys,['sb-stable','sb-stable'])

    def test_native_artwork_adoption_checks_the_logical_manual_cover_entry(self):
        case = self.fixture
        plan = case.plan()
        row = plan.manifest['view']['track_rows'][0]
        dto = case.request['tracks'][0]
        extra = case.request['track_extras'][0]
        self.assertTrue(cdj_incremental.native_matches(row,dto,extra,None,None))
        from types import SimpleNamespace
        art_cache = SimpleNamespace(index={'sb-stable':dict(origin='manual',status='ok')})
        self.assertFalse(cdj_incremental.native_matches(row,dto,extra,None,art_cache,'sb-stable'))

    def test_initial_audio_contract_uses_the_existing_candidate_hash_pass_once(self):
        with patch.object(tx,'_hash_file',wraps=tx._hash_file) as hashes:
            plan = self.fixture.plan()
        audio = [call.args[0] for call in hashes.call_args_list
                 if call.args[0].is_relative_to(plan.candidate/'Contents')]
        self.assertEqual(len(audio),1)

    def test_corrupt_initial_candidate_fails_the_source_contract_before_device_writes(self):
        original = cdj_export.make_plan
        def corrupt(session, candidate, **kwargs):
            audio = next((candidate/'Contents').rglob('*.wav'))
            with audio.open('ab') as output:output.write(b'corrupted candidate')
            return original(session,candidate,**kwargs)
        with patch.object(cdj_export,'make_plan',corrupt):
            with self.assertRaisesRegex(ValueError,'Generated CDJ audio differs'):
                self.fixture.plan()
        self.assertEqual(list(self.fixture.target.iterdir()),[])

    def test_initial_candidate_cannot_alias_different_captured_audio_contracts(self):
        case=self.fixture
        extra=case.extra_track('18');case.playlist.keys=['sb-stable','18']
        raw=extra.path.read_bytes()
        extra.path.write_bytes(raw[:-1]+bytes([raw[-1]^1]))
        original=cdj_export.run_renderer
        def alias(command,*args,**kwargs):
            result=original(command,*args,**kwargs)
            if command=='render':
                first,second=result['view']['track_rows']
                first['audio']=second['audio']
            return result
        with patch.object(cdj_export,'run_renderer',side_effect=alias), \
                patch.object(cdj_export,'make_plan',side_effect=AssertionError('Ambiguous contracts reached planner')):
            with self.assertRaisesRegex(ValueError,'aliases different captured sources'):
                case.plan()
        self.assertEqual(list(case.target.iterdir()),[])
