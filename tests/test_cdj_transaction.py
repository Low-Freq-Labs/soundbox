"""Disposable USBs only: no mounted targets, source library, or app state."""
import binascii
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import uuid

from soundbox import cdj_transaction as tx
from soundbox.cdj_usb import (DATABASE_FILES, CdjReadSession, default_display,
                             display_values, patch_display)


class SimulatedCrash(BaseException):
    pass


class CdjTransactionTests(unittest.TestCase):
    def setUp(self):
        self.area = tempfile.TemporaryDirectory()
        self.addCleanup(self.area.cleanup)
        self.root = Path(self.area.name).resolve()
        self.target = self.root/'USB'
        self.target.mkdir()
        self.candidate = self.root/'candidate'
        self.candidate.mkdir()
        self.volume = str(uuid.uuid4())
        self.token = self.target.stat().st_dev
        self.session = tx.CdjSession(self.target, [], token=self.token, volume_uuid=self.volume)
        self.original = {}
        for i,path in enumerate(DATABASE_FILES):
            old, new = f'original native database {i}'.encode(), f'candidate native database {i}'.encode()
            self.put(self.target/path, old)
            self.put(self.candidate/path, new)
            self.original[path] = old
        self.put(self.candidate/'Contents/Artist/track.wav', b'captured audio')

    @staticmethod
    def put(path, raw):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

    def plan(self, **kwargs):
        return tx.make_plan(self.session, self.candidate, selection=[{'key':'test'}], db_id=42,
            view={'tracks':1,'playlists':[{'folder':False}]}, recovery_root=self.root/'receipts', **kwargs)

    def assert_old_generation(self, *, reusable=False):
        for path, raw in self.original.items():
            self.assertEqual((self.target/path).read_bytes(), raw)
        self.assertEqual((self.target/'Contents/Artist/track.wav').exists(),reusable)
        self.assertFalse((self.target/tx.MANIFEST).exists())
        self.assertFalse((self.target/tx.JOURNAL).exists())

    def test_planning_is_read_only_and_replacement_is_explicit(self):
        before = sorted(p.relative_to(self.target).as_posix() for p in self.target.rglob('*'))
        plan = self.plan()
        self.assertTrue(plan.replaces_native_library)
        self.assertEqual(len(plan.changes),4)
        self.assertEqual(before, sorted(p.relative_to(self.target).as_posix() for p in self.target.rglob('*')))
        self.assert_old_generation()

    def test_commit_publishes_both_libraries_and_manifest_last(self):
        plan = self.plan()
        phases = []
        real_write = tx.CdjSession.atomic_write
        def record(session, destination, data):
            phases.append(destination.relative_to(self.target).as_posix())
            return real_write(session,destination,data)
        with mock.patch.object(tx.CdjSession,'atomic_write',record):
            result = tx.commit(plan)
        self.assertEqual(result['volume_uuid'], self.volume)
        self.assertEqual(phases[-2:], [tx.MANIFEST,tx.JOURNAL])
        for path, raw in self.original.items():
            self.assertNotEqual((self.target/path).read_bytes(), raw)
            self.assertEqual((self.target/'.soundbox-cdj/transactions'/result['generation']/path).read_bytes(), raw)
        self.assertFalse((self.target/tx.JOURNAL).exists())

    def test_partial_postcommit_retirement_is_saved_and_fresh_recovery_finishes_only_cleanup(self):
        removed = ('Contents/old-a.wav', 'Contents/old-b.wav')
        for path in removed:
            self.put(self.target/path, path.encode())
        plan = self.plan(removed_files=removed)
        actual = tx.CdjSession.unlink
        def refuse_second(session, path):
            if path.name == 'old-b.wav':
                raise OSError('device cleanup temporarily unavailable')
            return actual(session, path)
        with mock.patch.object(tx.CdjSession, 'unlink', refuse_second), \
                self.assertRaises(tx.CommittedCleanupPending) as caught:
            tx.commit(plan)
        self.assertIsInstance(caught.exception, RuntimeError)
        self.assertIs(caught.exception.manifest, plan.manifest)
        self.assertIn('Export saved', str(caught.exception))
        self.assertEqual((self.target/tx.MANIFEST).read_bytes(), tx._json(plan.manifest))
        backup = self.target/'.soundbox-cdj/transactions'/plan.manifest['generation']
        self.assertFalse((backup/removed[0]).exists())
        self.assertEqual((backup/removed[1]).read_bytes(), removed[1].encode())
        self.assertTrue((self.target/tx.JOURNAL).is_file())
        expected = {path: (self.target/path).read_bytes() for path in DATABASE_FILES}
        session = tx.CdjSession(self.target, [], token=self.token, volume_uuid=self.volume)
        tx.recover(session, self.root/'receipts')
        self.assertFalse((self.target/tx.JOURNAL).exists())
        self.assertTrue(all(not (backup/path).exists() and not (self.target/path).exists() for path in removed))
        self.assertEqual({path: (self.target/path).read_bytes() for path in DATABASE_FILES}, expected)
        self.assertEqual((self.target/tx.MANIFEST).read_bytes(), tx._json(plan.manifest))

    def test_committed_journal_unlink_refusal_is_saved_and_retry_keeps_new_generation(self):
        plan = self.plan()
        actual = tx.CdjSession.unlink
        def refuse_journal(session, path):
            if path == self.target/tx.JOURNAL:
                raise OSError('pending journal removal refused')
            return actual(session, path)
        with mock.patch.object(tx.CdjSession, 'unlink', refuse_journal), \
                self.assertRaises(tx.CommittedCleanupPending) as caught:
            tx.commit(plan)
        self.assertIn('journal removal refused', str(caught.exception))
        self.assertEqual((self.target/tx.MANIFEST).read_bytes(), tx._json(plan.manifest))
        self.assertTrue((self.target/tx.JOURNAL).is_file())
        expected = {path: (self.target/path).read_bytes() for path in DATABASE_FILES}
        tx.recover(self.session, self.root/'receipts')
        self.assertFalse((self.target/tx.JOURNAL).exists())
        self.assertEqual({path: (self.target/path).read_bytes() for path in DATABASE_FILES}, expected)

    def test_mid_copy_failure_restores_entire_original_set(self):
        plan = self.plan()
        actual = tx.CdjSession.atomic_copy
        calls = []
        def fail(session, source, destination, **kwargs):
            calls.append(destination)
            if len(calls)==3:
                raise OSError('injected device copy failure')
            return actual(session,source,destination,**kwargs)
        with mock.patch.object(tx.CdjSession,'atomic_copy',fail):
            with self.assertRaisesRegex(OSError,'injected'):
                tx.commit(plan)
        self.assert_old_generation(reusable=True)
        retry=self.plan()
        self.assertNotIn('Contents/Artist/track.wav',[c.path for c in retry.changes])
        self.assertTrue(retry.manifest['files']['Contents/Artist/track.wav']['owned'])

    def test_cancel_before_publication_makes_no_journal_or_recovery_directory(self):
        plan = self.plan()
        with self.assertRaisesRegex(RuntimeError,'cancelled'):
            tx.commit(plan,should_cancel=lambda:True)
        self.assert_old_generation()
        self.assertFalse((self.target/'.soundbox-cdj').exists())

    def test_cancel_after_one_published_file_restores_originals(self):
        plan = self.plan()
        cancelled = []
        with self.assertRaisesRegex(RuntimeError,'cancelled'):
            tx.commit(plan,should_cancel=lambda:bool(cancelled),on_progress=lambda *_:cancelled.append(True))
        self.assert_old_generation(reusable=True)

    def test_crash_recovery_uses_local_receipt_and_restores_previous_generation(self):
        plan = self.plan()
        with self.assertRaises(SimulatedCrash):
            tx.commit(plan,on_progress=lambda *_:(_ for _ in ()).throw(SimulatedCrash()))
        self.assertTrue((self.target/tx.JOURNAL).exists())
        tx.recover(self.session,self.root/'receipts')
        self.assert_old_generation()

    def test_unmatched_local_recovery_authority_never_mutates_usb(self):
        plan = self.plan()
        with self.assertRaises(SimulatedCrash):
            tx.commit(plan,on_progress=lambda *_:(_ for _ in ()).throw(SimulatedCrash()))
        before = {p.relative_to(self.target).as_posix():p.read_bytes() for p in self.target.rglob('*') if p.is_file()}
        with self.assertRaises(OSError):
            tx.recover(self.session,self.root/'unrelated-receipts')
        self.assertEqual(before,{p.relative_to(self.target).as_posix():p.read_bytes() for p in self.target.rglob('*') if p.is_file()})
        tx.recover(self.session,self.root/'receipts')
        self.assert_old_generation()

    def test_changed_target_or_candidate_expires_confirmation_before_any_write(self):
        plan = self.plan()
        self.put(self.candidate/DATABASE_FILES[0],b'changed candidate')
        with self.assertRaisesRegex(ValueError,'Candidate changed'):
            tx.commit(plan)
        self.assert_old_generation()
        plan = self.plan()
        self.put(self.target/DATABASE_FILES[1],b'outside edit')
        with self.assertRaisesRegex(ValueError,'USB changed'):
            tx.commit(plan)
        self.assertFalse((self.target/tx.JOURNAL).exists())

    def test_unowned_media_conflict_is_refused_without_replacement(self):
        self.put(self.target/'Contents/Artist/track.wav',b'unowned recording')
        with self.assertRaisesRegex(ValueError,'Unowned CDJ file'):
            self.plan()
        self.assertEqual((self.target/'Contents/Artist/track.wav').read_bytes(), b'unowned recording')

    def test_identical_preexisting_media_is_referenced_without_ownership(self):
        self.put(self.target/'Contents/Artist/track.wav',b'captured audio')
        plan = self.plan()
        self.assertFalse(plan.manifest['files']['Contents/Artist/track.wav']['owned'])
        self.assertNotIn('Contents/Artist/track.wav',[c.path for c in plan.changes])

    def test_previously_referenced_unowned_media_cannot_be_overwritten(self):
        self.put(self.target/'Contents/Artist/track.wav',b'captured audio')
        first=self.plan();tx.commit(first)
        self.put(self.candidate/'Contents/Artist/track.wav',b'new source bytes')
        with self.assertRaisesRegex(ValueError,'Unowned CDJ file'):
            self.plan()
        self.assertEqual((self.target/'Contents/Artist/track.wav').read_bytes(),b'captured audio')

    def test_symlinks_fat_aliases_bad_mounts_and_source_overlap_are_refused(self):
        with self.assertRaises(ValueError):
            tx.CdjSession(self.target,[],token=self.token+1,volume_uuid=self.volume)
        with self.assertRaises(RuntimeError):
            tx.CdjSession(self.target,[self.target/'source.wav'],token=self.token,volume_uuid=self.volume)
        (self.target/'redirect').symlink_to(self.root)
        with self.assertRaises((ValueError,RuntimeError)):
            self.session.guard(self.target/'redirect/outside')
        (self.target/'PIONEER').rename(self.target/'pioneer')
        with self.assertRaises(ValueError):
            self.session.guard(self.target/DATABASE_FILES[0])

    def test_ipod_rockbox_and_hidden_libraries_are_not_cdj_targets(self):
        for marker in ('iPod_Control','.rockbox','.PIONEER'):
            with self.subTest(marker=marker):
                (self.target/marker).mkdir()
                with self.assertRaises(ValueError):
                    tx.CdjSession(self.target,[],token=self.token,volume_uuid=self.volume)
                (self.target/marker).rmdir()

    def test_settings_only_plan_retains_managed_media_without_reading_it(self):
        first = self.plan()
        tx.commit(first)
        (self.candidate/'Contents/Artist/track.wav').unlink()
        self.put(self.candidate/DATABASE_FILES[0],b'settings edit')
        with mock.patch.object(tx,'_hash_file',wraps=tx._hash_file) as hashes:
            settings = self.plan(settings_only=True)
        self.assertTrue(settings.manifest['files']['Contents/Artist/track.wav']['owned'])
        self.assertFalse(any(c.new_sha256 is None for c in settings.changes))
        self.assertNotIn(self.target/'Contents/Artist/track.wav',[c.args[0] for c in hashes.call_args_list])

    def test_unreconciled_sqlite_companions_block_publication_before_writes(self):
        plan = self.plan()
        sidecar = self.target/(DATABASE_FILES[2]+'-wal')
        self.put(sidecar,b'foreign native transaction')
        with self.assertRaisesRegex(ValueError,'unreconciled SQLite'):
            tx.commit(plan)
        self.assert_old_generation()
        self.assertEqual(sidecar.read_bytes(),b'foreign native transaction')

    def test_read_session_rejects_oversize_and_internal_redirects(self):
        reader = CdjReadSession(self.target,self.token)
        self.addCleanup(reader.close)
        with self.assertRaises(ValueError):
            reader.read(DATABASE_FILES[0],limit=4)
        (self.target/'alias').symlink_to(self.target/'PIONEER',target_is_directory=True)
        with self.assertRaises(OSError):
            reader.read('alias/rekordbox/export.pdb')


class CdjDisplayCodecTests(unittest.TestCase):
    def test_explicit_new_usb_profile_is_self_contained(self):
        raw = default_display()
        self.assertEqual(display_values(raw),dict(waveform_color='RGB',waveform_position='LEFT',
            overview_waveform='Half Waveform',key_format='Alphanumeric'))

    def test_each_display_edit_changes_only_its_byte_and_checksum(self):
        from soundbox.cdj_usb import DISPLAY_FIELDS
        raw = default_display()
        for key,(offset,options) in DISPLAY_FIELDS.items():
            for label,value in options:
                with self.subTest(field=key,value=label):
                    edited = patch_display(raw,{key:label})
                    self.assertEqual(display_values(edited)[key],label)
                    self.assertEqual(edited[offset],value)
                    self.assertEqual(int.from_bytes(edited[136:138],'little'),binascii.crc_hqx(edited[104:136],0))
                    self.assertEqual([b for i,b in enumerate(edited) if i not in (offset,136,137)],
                                     [b for i,b in enumerate(raw) if i not in (offset,136,137)])

    def test_unknown_choices_and_checksum_fail_closed(self):
        raw = bytearray(default_display())
        raw[114] = 0xFE
        raw[136:138] = binascii.crc_hqx(raw[104:136],0).to_bytes(2,'little')
        self.assertEqual(display_values(raw)['waveform_color'],'Unsupported')
        with self.assertRaises(ValueError):
            patch_display(raw,{'waveform_color':'RGB'})
        other = patch_display(raw,{'waveform_position':'CENTER'})
        self.assertEqual(other[114],0xFE)
        raw[114] = 1
        with self.assertRaises(ValueError):
            patch_display(raw,{'waveform_position':'CENTER'})
