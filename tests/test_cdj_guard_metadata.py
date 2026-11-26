"""Fresh CDJ guard boundaries with a measured per-operation metadata budget."""
from contextlib import contextmanager
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unicodedata
import unittest
from unittest import mock
import uuid

from soundbox.cdj_transaction import CdjSession


class CdjGuardMetadata(unittest.TestCase):
    def setUp(self):
        # Keep the benchmark's canonical root depth fixed: fresh root.resolve
        # must still inspect its ancestors, independently of path-guard savings.
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory(dir='/tmp'))).resolve()
        self.target=self.root/'USB';self.target.mkdir();(self.target/'iPod_Control').mkdir()
        self.token=self.target.stat().st_dev;self.volume=str(uuid.uuid4())
        self.session=self.new_session();self.outside=self.root/'originals';self.outside.mkdir()
        (self.outside/'keep.wav').write_bytes(b'original source bytes')

    def new_session(self):
        return CdjSession(self.target,[],token=self.token,volume_uuid=self.volume,target_role='ipod_usb')

    @contextmanager
    def metadata_counts(self):
        counts={'stat':0,'lstat':0,'list':0};raw_stat=os.stat;raw_lstat=os.lstat;raw_iter=Path.iterdir
        def observed_stat(*args,**kwargs):counts['stat']+=1;return raw_stat(*args,**kwargs)
        def observed_lstat(*args,**kwargs):counts['lstat']+=1;return raw_lstat(*args,**kwargs)
        def observed_iter(path):counts['list']+=1;return raw_iter(path)
        with (mock.patch.object(os,'stat',observed_stat),mock.patch.object(os,'lstat',observed_lstat),
              mock.patch.object(Path,'iterdir',observed_iter)):
            yield counts

    def test_707_new_destinations_do_not_repeat_resolve_and_exists_passes(self):
        seed=self.target/'PIONEER/rekordbox/export.pdb';seed.parent.mkdir(parents=True);seed.write_bytes(b'metadata')
        session=self.new_session()
        paths=([f'Contents/generated{i}.wav' for i in range(90)]
            +[f'PIONEER/USBANLZ/P{i//8:03X}/{i:08X}/ANLZ0000.DAT' for i in range(600)]
            +[f'PIONEER/Artwork/{i:05}/a{i}.jpg' for i in range(17)])
        with self.metadata_counts() as counts:
            for path in paths:self.assertEqual(session.guard(self.target/path),self.target/path)
        self.assertLessEqual(counts['stat']+counts['lstat'],12*len(paths)+512,counts)
        self.assertLessEqual(counts['list'],2,counts)
        self.assertFalse((self.target/'Contents').exists())
        self.assertFalse((self.target/'PIONEER/USBANLZ').exists())

    def test_existing_deep_directory_names_reuse_only_fresh_identity_matched_lists(self):
        path=self.target/'PIONEER/USBANLZ/P001/00000001/ANLZ0000.DAT'
        path.parent.mkdir(parents=True);path.write_bytes(b'analysis metadata');session=self.new_session()
        with self.metadata_counts() as counts:
            for _ in range(120):self.assertEqual(session.guard(path),path)
        self.assertLessEqual(counts['list'],5,counts)
        self.assertLessEqual(counts['stat']+counts['lstat'],28*120+128,counts)

    def test_missing_tail_stays_read_only_and_publication_reopens_each_parent(self):
        path=self.target/'Contents/not-created/deep/more/song.wav'
        self.assertEqual(self.session.guard(path),path);self.assertFalse((self.target/'Contents').exists())
        self.session.atomic_write(path,b'captured payload');self.assertEqual(path.read_bytes(),b'captured payload')
        late=self.target/'Contents/late/deep/song.wav';self.session.guard(late)
        (self.target/'Contents/late').symlink_to(self.outside,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'Linked CDJ'):self.session.atomic_write(late,b'forbidden')
        self.assertFalse((self.outside/'deep').exists())

    def test_cached_parent_replacement_with_a_link_cannot_grant_permission(self):
        folder=self.target/'Contents/Artist';folder.mkdir(parents=True);path=folder/'track.wav';path.write_bytes(b'old')
        self.session.guard(path);folder.rename(folder.with_name('held'))
        folder.symlink_to(self.outside,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'Linked CDJ'):self.session.guard(path)
        with self.assertRaisesRegex(ValueError,'Linked CDJ'):self.new_session().guard(path)
        self.assertEqual((self.outside/'keep.wav').read_bytes(),b'original source bytes')

    def test_final_links_are_rejected_even_when_dangling(self):
        folder=self.target/'Contents';folder.mkdir()
        for name,destination in [('existing',self.outside/'keep.wav'),('dangling',self.outside/'missing.wav')]:
            path=folder/name;path.symlink_to(destination)
            with self.subTest(name=name),self.assertRaisesRegex(ValueError,'Linked CDJ'):self.session.guard(path)
        self.assertEqual((self.outside/'keep.wav').read_bytes(),b'original source bytes')
        self.assertFalse((self.outside/'missing.wav').exists())

    def test_unicode_equivalence_keeps_case_and_multiple_alias_refusal(self):
        artist='L\u00c4RJ';actual=unicodedata.normalize('NFD',artist)
        parent=self.target/'Contents';(parent/actual).mkdir(parents=True)
        requested=parent/artist/'new.wav';self.assertEqual(self.session.guard(requested),requested)
        self.assertEqual((parent/artist).stat().st_ino,(parent/actual).stat().st_ino)
        with self.assertRaisesRegex(ValueError,'Ambiguous FAT spelling'):self.session.guard(parent/artist.lower()/'new.wav')
        session=self.new_session();raw=Path.iterdir
        def aliases(path):return iter((parent/artist,parent/actual)) if path==parent else raw(path)
        with mock.patch.object(Path,'iterdir',aliases):
            with self.assertRaisesRegex(ValueError,'Ambiguous FAT spelling'):session.guard(requested)

    def test_lexical_outside_traversal_and_other_firmware_namespaces_refuse(self):
        for path in (self.outside/'new.wav',self.root/'USB-other/song.wav',
                     self.target/'unmade/../../escape.wav',self.target/'Contents/unmade/../../../escape.wav',
                     self.target/'PIONEER/../iPod_Control/iTunes/iTunesDB',self.target/'.rockbox/config.cfg'):
            with self.subTest(path=path),self.assertRaises((ValueError,RuntimeError)):self.session.guard(path)
        self.assertFalse((self.outside/'new.wav').exists())

    def test_reroot_and_source_overlap_are_still_independent_guards(self):
        with self.assertRaises(RuntimeError):
            CdjSession(self.target,[self.target/'Contents/source.wav'],token=self.token,volume_uuid=self.volume,target_role='ipod_usb')
        self.target.rename(self.root/'held-USB');self.target.mkdir();(self.target/'iPod_Control').mkdir()
        with self.assertRaisesRegex(RuntimeError,'changed or was remounted'):self.session.guard(self.target/'Contents/song.wav')

    def test_cross_mount_observation_refuses_even_after_name_cache_is_warm(self):
        folder=self.target/'Contents/Artist';folder.mkdir(parents=True);path=folder/'new.wav';self.session.guard(path)
        raw=Path.lstat
        def other_mount(value,*args,**kwargs):
            result=raw(value,*args,**kwargs)
            if value==folder:
                return SimpleNamespace(st_mode=result.st_mode,st_dev=result.st_dev+1,st_ino=result.st_ino,
                    st_mtime_ns=result.st_mtime_ns,st_ctime_ns=result.st_ctime_ns)
            return result
        with mock.patch.object(Path,'lstat',other_mount):
            with self.assertRaisesRegex(ValueError,'crosses the USB mount'):self.session.guard(path)

    def test_cached_real_directory_replaced_by_regular_file_cannot_be_walked(self):
        folder=self.target/'Contents/Artist';folder.mkdir(parents=True);path=folder/'new.wav';self.session.guard(path)
        folder.rename(folder.with_name('held'));folder.write_bytes(b'foreign regular file')
        with self.assertRaisesRegex(ValueError,'parent is not a real directory'):self.session.guard(path)
        self.assertEqual(folder.read_bytes(),b'foreign regular file')
