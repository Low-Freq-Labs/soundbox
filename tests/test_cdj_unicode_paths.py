"""Real macOS Unicode spellings through CDJ publication and exact recovery."""
from pathlib import Path
import shutil
import unicodedata
import unittest
from unittest import mock

from soundbox import cdj_transaction as tx,cdj_usb
from tests import test_cdj_transaction as fixtures
from tests import test_cdj_integration as integration

ARTIST='L\u00c4RJ'
NAME='Jay Z - Big Pimpin (L\u00c4RJ Flip).wav'
RELATIVE=f'Contents/{ARTIST}/UnknownAlbum/{NAME}'


def nfd(value):return unicodedata.normalize('NFD',value)


class CdjUnicodePaths(unittest.TestCase):
    def setUp(self):
        self.case=fixtures.CdjTransactionTests();self.case.setUp();self.addCleanup(self.case.doCleanups)
        self.target=self.case.target;self.candidate=self.case.candidate
        (self.candidate/'Contents/Artist/track.wav').unlink()
        self.case.put(self.candidate/RELATIVE,b'captured Unicode audio')
        self.folder=self.target/'Contents'/nfd(ARTIST)/'UnknownAlbum';self.folder.mkdir(parents=True)
        self.assertEqual(next((self.target/'Contents').iterdir()).name,nfd(ARTIST))
        self.assertEqual((self.target/'Contents'/ARTIST).stat().st_ino,(self.target/'Contents'/nfd(ARTIST)).stat().st_ino)

    def plan(self):return self.case.plan()

    def emit_nfd(self,path):
        """Model FAT's emitted spelling, changing no file bytes or entity."""
        parent=self.target
        for component in path.relative_to(self.target).parts:
            actual=next(p for p in parent.iterdir() if nfd(p.name)==nfd(component))
            if actual.name!=nfd(actual.name):actual.rename(parent/nfd(actual.name))
            parent=parent/nfd(component)
        return parent

    def test_single_nfd_parent_and_filename_accept_the_same_nfc_reference(self):
        actual=self.folder/nfd(NAME);actual.write_bytes(b'captured Unicode audio');before=actual.stat()
        requested=self.target/RELATIVE
        self.assertEqual(requested.stat().st_ino,before.st_ino)
        plan=self.plan();self.assertFalse(plan.manifest['files'][RELATIVE]['owned'])
        tx.commit(plan)
        self.assertEqual(actual.read_bytes(),b'captured Unicode audio')
        self.assertEqual((actual.stat().st_ino,actual.stat().st_mtime_ns),(before.st_ino,before.st_mtime_ns))

    def test_new_copy_preserves_nfc_catalog_path_and_normalized_parent(self):
        plan=self.plan();tx.commit(plan)
        self.assertEqual((self.target/RELATIVE).read_bytes(),b'captured Unicode audio')
        self.assertEqual(next((self.target/'Contents').iterdir()).name,nfd(ARTIST))
        self.assertIn(RELATIVE,plan.manifest['files'])
        repeated=self.plan();self.assertFalse(repeated.requires_commit)

    def test_runtime_failure_recovers_after_backup_names_are_emitted_as_nfd(self):
        first=self.plan();tx.commit(first);old=(self.target/RELATIVE).read_bytes();old_inode=(self.target/RELATIVE).stat().st_ino
        self.emit_nfd(self.target/RELATIVE);self.case.put(self.candidate/RELATIVE,b'updated captured audio')
        plan=self.plan();rename=tx.CdjSession.rename
        def normalized(session,source,destination):
            rename(session,source,destination)
            if '.soundbox-cdj/transactions/' in str(destination) and destination.name==NAME:self.emit_nfd(destination)
        with mock.patch.object(tx.CdjSession,'rename',normalized):
            with self.assertRaisesRegex(RuntimeError,'generated failure'):
                tx.commit(plan,on_progress=lambda *_:(_ for _ in ()).throw(RuntimeError('generated failure')))
        self.assertEqual((self.target/RELATIVE).read_bytes(),old)
        self.assertEqual((self.target/RELATIVE).stat().st_ino,old_inode)
        self.assertFalse((self.target/tx.JOURNAL).exists())
        self.assertEqual((self.target/tx.MANIFEST).read_bytes(),tx._json(first.manifest))

    def test_fresh_session_crash_recovery_accepts_nfd_backup_and_live_paths(self):
        first=self.plan();tx.commit(first);old=(self.target/RELATIVE).read_bytes();old_inode=(self.target/RELATIVE).stat().st_ino
        self.emit_nfd(self.target/RELATIVE);self.case.put(self.candidate/RELATIVE,b'new Unicode payload')
        plan=self.plan()
        with self.assertRaises(fixtures.SimulatedCrash):
            tx.commit(plan,on_progress=lambda *_:(_ for _ in ()).throw(fixtures.SimulatedCrash()))
        backup=self.target/'.soundbox-cdj/transactions'/plan.manifest['generation']/RELATIVE
        self.emit_nfd(backup);self.emit_nfd(self.target/RELATIVE)
        fresh=tx.CdjSession(self.target,[],token=self.case.token,volume_uuid=self.case.volume)
        tx.recover(fresh,self.case.root/'receipts')
        self.assertEqual((self.target/RELATIVE).read_bytes(),old)
        self.assertEqual((self.target/RELATIVE).stat().st_ino,old_inode)
        self.assertEqual((self.target/tx.MANIFEST).read_bytes(),tx._json(first.manifest))
        self.assertFalse((self.target/tx.JOURNAL).exists())

    def test_a_single_case_variant_still_refuses_without_writes(self):
        (self.target/'Contents'/nfd(ARTIST)).rename(self.target/'Contents'/nfd(ARTIST.lower()))
        with self.assertRaisesRegex(ValueError,'Ambiguous FAT spelling'):self.plan()
        self.case.assert_old_generation()

    def test_multiple_unicode_equivalent_names_still_refuse(self):
        parent=self.target/'Contents';iterate=Path.iterdir
        def aliases(path):
            if path==parent:return iter((parent/ARTIST,parent/nfd(ARTIST)))
            return iterate(path)
        with mock.patch.object(Path,'iterdir',aliases):
            with self.assertRaisesRegex(ValueError,'Ambiguous FAT spelling'):self.plan()
        self.case.assert_old_generation()


class CdjUnicodeRenderer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):integration.CdjIntegrationTests.setUpClass()

    def test_real_render_catalog_and_selected_read_resolve_nfc_paths_on_nfd_folders(self):
        case=integration.CdjIntegrationTests();case.setUp();self.addCleanup(case.doCleanups)
        source=case.root/'sources'/NAME;source.parent.mkdir();shutil.copyfile(case.fixture/'source.wav',source)
        row=case.request['tracks'][0];row.update(source_path=str(source),artist=ARTIST,album='',title='Unicode fixture')
        track=case.lib.tracks_by_id['17'];track.location_uri=source.as_uri();track.artist=ARTIST
        parent=case.target/'Contents'/nfd(ARTIST);parent.mkdir(parents=True)
        plan=case.plan();tx.commit(plan)
        native=cdj_usb.read_library(case.target,case.token);song,=native.songs
        self.assertIn(ARTIST,song.path);self.assertIn(NAME,song.path)
        self.assertEqual(next((case.target/'Contents').iterdir()).name,nfd(ARTIST))
        selected=cdj_usb.prepare_playback(case.target,case.token,song)
        self.assertEqual(Path(selected[0]).read_bytes(),source.read_bytes())
