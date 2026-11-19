"""Source-conversion effects, independent of device and CDJ codec policies."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock
from uuid import uuid4

from soundbox import media_transition as transition
from soundbox.operation_control import OperationCancelled, operation_scope
from soundbox.rekordbox_analysis_store import shared_root


class MediaTransitionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.data = self.root / 'data'
        self.data.mkdir()
        self.database = self.root / 'source' / 'master.db'
        self.database.parent.mkdir()
        self.database.write_bytes(b'Native database must not be opened')
        self.audio = self.root / 'music' / 'NOXIOUS.mp3'
        self.audio.parent.mkdir()
        self.audio.write_bytes(b'Audio must not be opened, classified, rendered or altered')
        self.job = dict(id=str(uuid4()), source=str(self.database), library_id='library',
                        path=str(self.audio), ids=['song'],
                        native=[dict(id='native-song', uuid='native-uuid')], error='')
        self.recovery = shared_root(self.data, self.database, 'library')
        self.folder = self.data / 'media-conversion-originals' / self.job['id']
        self.stage = self.audio.parent / ('.soundbox-media-' + self.job['id'] + '.wav')

    def capture(self, jobs=None):
        return transition.capture_media_transitions(self.data, self.database, 'library',
                                                    [self.job] if jobs is None else jobs)

    def external_source(self):
        """Map only the /Volumes directory boundary to generated TEMP storage.

        Below that boundary the production probe owns real directory descriptors
        and performs real no-follow stat/open/identity/absence checks. Nothing
        reads, mounts or writes a physical external volume.
        """
        volumes = self.root / 'generated-volume-boundary'
        volume = volumes / 'Generated-SOURCE'
        audio = volume / 'music' / 'EXTERNAL.mp3'
        audio.parent.mkdir(parents=True)
        audio.write_bytes(b'Generated external source must remain untouched')
        self.job['path'] = '/Volumes/Generated-SOURCE/music/EXTERNAL.mp3'
        real_stat, real_open = os.stat, os.open
        root_identity = transition._identity(real_stat('/'))
        def volume_entry(path, kwargs):
            descriptor = kwargs.get('dir_fd')
            return (path == 'Volumes' and descriptor is not None
                    and transition._identity(os.fstat(descriptor)) == root_identity)
        def redirected_stat(path, *args, **kwargs):
            if volume_entry(path, kwargs):
                return real_stat(volumes, follow_symlinks=kwargs.get('follow_symlinks', True))
            return real_stat(path, *args, **kwargs)
        def redirected_open(path, flags, *args, **kwargs):
            if volume_entry(path, kwargs):
                return real_open(volumes, flags, *args)
            return real_open(path, flags, *args, **kwargs)
        self.enterContext(mock.patch.object(transition.os, 'stat', side_effect=redirected_stat))
        self.enterContext(mock.patch.object(transition.os, 'open', side_effect=redirected_open))
        return volume, audio, audio.parent / self.stage.name

    def test_dormant_queued_and_failed_policy_intentions_never_open_or_change_audio_or_database(self):
        before = self.audio.read_bytes(), self.database.read_bytes()
        for error in ('', 'This compressed format needs a verified timing-preserving conversion; original retained',
                      'Any other policy refusal; text is not authority'):
            with self.subTest(error=error):
                self.job['error'] = error
                original = deepcopy(self.job)
                with mock.patch.object(Path, 'open', side_effect=AssertionError('File payload opened')), \
                        mock.patch('soundbox.cdj_compatibility.inspect',
                                   side_effect=AssertionError('CDJ policy reached iPod transition probe')), \
                        mock.patch('soundbox.media_conversion_store.ConversionStore',
                                   side_effect=AssertionError('Source writer instantiated')):
                    proof = self.capture()
                    self.assertTrue(proof.require_current())
                self.assertEqual(self.job, original)
                self.assertEqual(json.loads(proof.jobs_payload), [original])
                self.assertFalse(self.folder.exists())
                self.assertFalse(self.stage.exists())
                self.assertFalse(self.recovery.exists())
        self.assertEqual((self.audio.read_bytes(), self.database.read_bytes()), before)

    def test_any_selected_operation_folder_holds_including_empty_unknown_plan_backup_and_receipt(self):
        self.folder.mkdir(parents=True)
        for child in (None, 'unknown', 'plan.json', 'original.mp3', 'receipt.json'):
            with self.subTest(child=child):
                if child is not None:
                    (self.folder / child).write_bytes(b'Preserve recovery evidence')
                with self.assertRaisesRegex(transition.MediaTransitionPending, 'NOXIOUS.mp3'):
                    self.capture()
                if child is not None:
                    self.assertEqual((self.folder / child).read_bytes(), b'Preserve recovery evidence')

    def test_exact_stage_holds_without_plan_or_journal_and_retains_its_bytes(self):
        self.stage.write_bytes(b'Prepared audio, never publish or discard here')
        with self.assertRaisesRegex(transition.MediaTransitionPending, 'NOXIOUS.mp3'):
            self.capture()
        self.assertEqual(self.stage.read_bytes(), b'Prepared audio, never publish or discard here')
        self.assertFalse(self.folder.exists())

    def test_shared_transaction_and_grid_recovery_hold_even_without_selected_operation_folder(self):
        self.recovery.mkdir(parents=True)
        for name in ('transaction.json', 'grid-transaction'):
            with self.subTest(name=name):
                path = self.recovery / name
                path.write_bytes(b'Unknown or admitted recovery must remain untouched')
                with self.assertRaises(transition.MediaTransitionPending):
                    self.capture()
                self.assertEqual(path.read_bytes(), b'Unknown or admitted recovery must remain untouched')
                path.unlink()

    def test_receipt_appearance_after_capture_refuses_old_absence_proof(self):
        self.folder.parent.mkdir()
        proof = self.capture()
        self.folder.mkdir()
        receipt = self.folder / 'receipt.json'
        receipt.write_bytes(b'Converted source awaiting local or native locator acknowledgement')
        with self.assertRaisesRegex(transition.MediaTransitionPending, 'NOXIOUS.mp3'):
            proof.require_current()
        self.assertEqual(receipt.read_bytes(), b'Converted source awaiting local or native locator acknowledgement')

    def test_staging_links_and_redirected_recovery_or_audio_ancestry_refuse_without_following(self):
        outside = self.root / 'foreign'
        outside.mkdir()
        target = outside / 'occupant'
        target.write_bytes(b'Foreign bytes')
        self.stage.symlink_to(target)
        with self.assertRaisesRegex(transition.MediaTransitionChanged, 'NOXIOUS.mp3'):
            self.capture()
        self.stage.unlink()
        (self.data / 'media-conversion-originals').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(transition.MediaTransitionChanged):
            self.capture()
        (self.data / 'media-conversion-originals').unlink()
        moved = self.audio.parent.with_name('original-music')
        self.audio.parent.rename(moved)
        self.audio.parent.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(transition.MediaTransitionChanged):
            self.capture()
        self.assertEqual(target.read_bytes(), b'Foreign bytes')

    def test_data_root_redirect_and_missing_root_are_not_created_or_accepted(self):
        moved = self.data.with_name('original-data')
        self.data.rename(moved)
        self.data.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(transition.MediaTransitionChanged):
            self.capture()
        self.data.unlink()
        with self.assertRaises(transition.MediaTransitionChanged):
            self.capture()
        self.assertFalse(self.data.exists())

    def test_scope_job_identity_and_unsafe_paths_are_rejected_before_observation(self):
        variants = [dict(self.job, source=str(self.root / 'other.db')),
                    dict(self.job, library_id='other'), dict(self.job, id='not-an-operation'),
                    dict(self.job, path=str(self.data / 'source.mp3')),
                    dict(self.job, path=str(self.database.parent / 'source.mp3')),
                    dict(self.job, path=str(self.audio.parent / '..' / 'song.mp3'))]
        for job in variants:
            with self.subTest(job=job), mock.patch.object(transition, '_Observation',
                                                        side_effect=AssertionError('Invalid scope reached IO')):
                with self.assertRaises(transition.MediaTransitionChanged):
                    self.capture([job])
        second = dict(self.job, path=str(self.audio.with_name('another.mp3')))
        with self.assertRaises(transition.MediaTransitionChanged):
            self.capture([self.job, second])

    def test_unrelated_sibling_activity_does_not_invalidate_directory_entity_proof(self):
        self.folder.parent.mkdir()
        self.recovery.mkdir(parents=True)
        proof = self.capture()
        (self.data / 'unrelated-canonical-save').write_bytes(b'Unrelated state')
        (self.folder.parent / str(uuid4())).mkdir()
        (self.audio.parent / 'another-song.mp3').write_bytes(b'Unrelated audio')
        (self.recovery / 'completed-unrelated.json').write_bytes(b'Compact completed receipt')
        self.assertTrue(proof.require_current())

    def test_same_named_data_recovery_or_audio_directory_replacement_invalidates_old_proof(self):
        self.recovery.mkdir(parents=True)
        self.folder.parent.mkdir()
        for path in (self.recovery, self.audio.parent, self.data):
            with self.subTest(path=path):
                proof = self.capture()
                moved = path.with_name(path.name + '-old')
                path.rename(moved)
                path.mkdir()
                with self.assertRaises(transition.MediaTransitionChanged):
                    proof.require_current()
                path.rmdir()
                moved.rename(path)

    def test_missing_ancestry_appearing_invalidates_old_proof_even_without_claiming_a_transition(self):
        proof = self.capture()
        self.folder.parent.mkdir()
        with self.assertRaises(transition.MediaTransitionChanged):
            proof.require_current()
        self.assertTrue(self.capture().require_current())

    def test_appearance_between_first_absence_and_final_check_is_rejected(self):
        real_stat = os.stat
        calls = 0
        def changing(path, *args, **kwargs):
            nonlocal calls
            if path == self.stage.name:
                calls += 1
                if calls == 2:
                    self.stage.write_bytes(b'Concurrent prepared stage')
            return real_stat(path, *args, **kwargs)
        with mock.patch.object(transition.os, 'stat', side_effect=changing):
            with self.assertRaisesRegex(transition.MediaTransitionChanged, 'appeared'):
                self.capture()
        self.assertEqual(self.stage.read_bytes(), b'Concurrent prepared stage')

    def test_parent_replacement_after_descriptor_open_does_not_bless_detached_absences(self):
        real_open = os.open
        replaced = False
        moved = self.data.with_name('detached-data')
        def changing(path, *args, **kwargs):
            nonlocal replaced
            descriptor = real_open(path, *args, **kwargs)
            if path == self.data.name and kwargs.get('dir_fd') is not None and not replaced:
                replaced = True
                self.data.rename(moved)
                self.data.mkdir()
            return descriptor
        with mock.patch.object(transition.os, 'open', side_effect=changing):
            with self.assertRaisesRegex(transition.MediaTransitionChanged, 'replaced'):
                self.capture()
        self.assertTrue(moved.is_dir())
        self.assertTrue(self.data.is_dir())

    def test_cancel_is_cooperative_and_never_creates_transition_evidence(self):
        stop = threading.Event()
        stop.set()
        with operation_scope(stop), self.assertRaises(OperationCancelled):
            self.capture()
        self.assertFalse(self.folder.exists())
        self.assertFalse(self.stage.exists())
        self.assertFalse(self.recovery.exists())

    def test_inaccessible_directory_observation_refuses_with_no_recovery_or_media_changes(self):
        before = self.audio.read_bytes(), self.database.read_bytes()
        with mock.patch.object(transition.os, 'open', side_effect=PermissionError('Cannot inspect ancestry')):
            with self.assertRaisesRegex(transition.MediaTransitionChanged, 'could not be checked'):
                self.capture()
        self.assertEqual((self.audio.read_bytes(), self.database.read_bytes()), before)
        self.assertFalse(self.folder.exists())
        self.assertFalse(self.stage.exists())

    def test_external_dormant_policy_intentions_admit_without_media_or_cdj_policy_reads(self):
        _volume, audio, stage = self.external_source()
        before = audio.read_bytes(), self.database.read_bytes()
        for error in ('', 'CDJ policy conversion is unsupported; original retained'):
            with self.subTest(error=error):
                self.job['error'] = error
                with mock.patch.object(Path, 'open', side_effect=AssertionError('File payload opened')), \
                        mock.patch('soundbox.cdj_compatibility.inspect',
                                   side_effect=AssertionError('CDJ policy reached read-only probe')):
                    proof = self.capture()
                    self.assertTrue(proof.require_current())
                self.assertEqual(json.loads(proof.jobs_payload)[0]['path'], self.job['path'])
                self.assertFalse(stage.exists())
                self.assertFalse(self.folder.exists())
        self.assertEqual((audio.read_bytes(), self.database.read_bytes()), before)

    def test_external_stage_operation_receipt_and_shared_recovery_still_hold_absence_proof(self):
        _volume, audio, stage = self.external_source()
        self.folder.parent.mkdir()
        self.recovery.mkdir(parents=True)
        proof = self.capture()
        stage.write_bytes(b'Prepared external transition evidence')
        with self.assertRaisesRegex(transition.MediaTransitionPending, 'EXTERNAL.mp3'):
            proof.require_current()
        self.assertEqual(stage.read_bytes(), b'Prepared external transition evidence')
        stage.unlink()
        self.folder.mkdir()
        receipt = self.folder / 'receipt.json'
        receipt.write_bytes(b'Awaiting exact source and locator acknowledgement')
        with self.assertRaisesRegex(transition.MediaTransitionPending, 'EXTERNAL.mp3'):
            proof.require_current()
        self.assertEqual(receipt.read_bytes(), b'Awaiting exact source and locator acknowledgement')
        receipt.unlink()
        self.folder.rmdir()
        journal = self.recovery / 'transaction.json'
        journal.write_bytes(b'Unknown shared recovery remains guarded')
        with self.assertRaises(transition.MediaTransitionPending):
            proof.require_current()
        self.assertEqual(journal.read_bytes(), b'Unknown shared recovery remains guarded')
        self.assertEqual(audio.read_bytes(), b'Generated external source must remain untouched')

    def test_external_volume_replacement_and_audio_ancestry_redirect_invalidate_proof(self):
        volume, audio, _stage = self.external_source()
        proof = self.capture()
        moved = volume.with_name('detached-external-source')
        volume.rename(moved)
        (volume / 'music').mkdir(parents=True)
        with self.assertRaises(transition.MediaTransitionChanged):
            proof.require_current()
        (volume / 'music').rmdir()
        volume.rmdir()
        volume.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(transition.MediaTransitionChanged):
            self.capture()
        self.assertEqual((moved / 'music' / audio.name).read_bytes(),
                         b'Generated external source must remain untouched')

    def test_source_converter_still_refuses_external_mutation_before_recovery_or_audio_access(self):
        from soundbox.media_conversion_store import ConversionStore
        from soundbox.rekordbox_write import WriteProofError
        _volume, audio, stage = self.external_source()
        writer = object.__new__(ConversionStore)
        writer.database, writer.data_root = self.database, self.data
        with mock.patch.object(writer, '_folder', side_effect=AssertionError('Source recovery reached')), \
                mock.patch.object(Path, 'open', side_effect=AssertionError('Source payload opened')):
            with self.assertRaisesRegex(WriteProofError, 'supported desktop audio'):
                writer.convert(self.job['path'], self.job['id'])
        self.assertEqual(audio.read_bytes(), b'Generated external source must remain untouched')
        self.assertFalse(stage.exists())
        self.assertFalse(self.folder.exists())
