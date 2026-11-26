"""Explicit CDJ publication coexists with generated stock and Rockbox trees."""
from dataclasses import replace
import unittest

from soundbox import cdj_transaction as tx
from tests import test_cdj_transaction as fixtures


class CdjIpodUsb(unittest.TestCase):
    def setUp(self):
        self.case=fixtures.CdjTransactionTests();self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.target=self.case.target
        for name in ('iPod_Control/iTunes/iTunesDB','iPod_Control/Artwork/ArtworkDB',
                     'iPod_Control/Artwork/F1060_1.ithmb','iPod_Control/Music/F00/OLD.wav',
                     '.rockbox/config.cfg','.soundbox/manifest.json','Music/rockbox.wav','Playlists/old.m3u8'):
            self.case.put(self.target/name,('protected '+name).encode())
        self.before=self.protected()
        self.case.session=self.session()

    def session(self):
        return tx.CdjSession(self.target,[],token=self.case.token,volume_uuid=self.case.volume,target_role='ipod_usb')

    def protected(self):
        result={}
        for tree in ('iPod_Control','.rockbox','.soundbox','Music','Playlists'):
            for path in (self.target/tree).rglob('*'):
                if path.is_file():
                    info=path.stat();result[path.relative_to(self.target).as_posix()]=(path.read_bytes(),info.st_ino,info.st_mode,info.st_mtime_ns)
        return result

    def test_role_is_retained_through_commit_and_both_firmware_trees_are_exact(self):
        plan=self.case.plan()
        self.assertEqual(plan.target_role,'ipod_usb')
        self.assertEqual(plan.ipod_control_identity,self.case.session.ipod_control_identity)
        result=tx.commit(plan)
        self.assertEqual(result,plan.manifest)
        self.assertEqual(self.protected(),self.before)
        for path in tx.DATABASE_FILES:
            self.assertEqual((self.target/path).read_bytes(),(self.case.candidate/path).read_bytes())

    def test_failed_publication_and_fresh_recovery_preserve_stock_bytes(self):
        plan=self.case.plan()
        with self.assertRaises(fixtures.SimulatedCrash):
            tx.commit(plan,on_progress=lambda *_:(_ for _ in ()).throw(fixtures.SimulatedCrash()))
        self.assertTrue((self.target/tx.JOURNAL).exists())
        tx.recover(self.session(),self.case.root/'receipts')
        self.case.assert_old_generation()
        self.assertEqual(self.protected(),self.before)

    def test_default_role_and_unknown_role_do_not_admit_an_ipod(self):
        for options in ({},{'target_role':'stock_ipod'},{'target_role':True}):
            with self.subTest(options=options),self.assertRaises(ValueError):
                tx.CdjSession(self.target,[],token=self.case.token,volume_uuid=self.case.volume,**options)
        with self.assertRaises(ValueError):tx.commit(replace(self.case.plan(),target_role='generic_usb'))
        self.case.assert_old_generation();self.assertEqual(self.protected(),self.before)

    def test_explicit_role_refuses_missing_false_linked_or_case_aliased_layout(self):
        marker=self.target/'iPod_Control';saved=self.target/'held-stock';marker.rename(saved)
        for shape in ('missing','file','symlink','case'):
            with self.subTest(shape=shape):
                if shape=='file':marker.write_bytes(b'not an iPod directory')
                elif shape=='symlink':marker.symlink_to(saved,target_is_directory=True)
                elif shape=='case':(self.target/'ipod_control').mkdir()
                with self.assertRaises(ValueError):self.session()
                if shape=='case':(self.target/'ipod_control').rmdir()
                elif marker.exists() or marker.is_symlink():marker.unlink()
        saved.rename(marker)
        self.assertEqual(self.protected(),self.before)

    def test_marker_replacement_after_plan_refuses_before_publication(self):
        plan=self.case.plan();marker=self.target/'iPod_Control';marker.rename(self.target/'saved-stock');marker.mkdir()
        with self.assertRaisesRegex(ValueError,'layout changed'):tx.commit(plan)
        self.case.assert_old_generation()
        marker.rmdir();(self.target/'saved-stock').rename(marker)
        self.assertEqual(self.protected(),self.before)

    def test_live_session_revalidates_layout_and_rejects_other_firmware_destinations(self):
        for path in ('iPod_Control/iTunes/iTunesDB','.rockbox/config.cfg','.soundbox/manifest.json',
                     'Music/rockbox.wav','Playlists/old.m3u8',
                     '.soundbox-cdj/../iPod_Control/iTunes/iTunesDB',
                     'PIONEER/../iPod_Control/iTunes/iTunesDB'):
            with self.subTest(path=path),self.assertRaisesRegex(ValueError,'namespace'):
                self.case.session.atomic_write(self.target/path,b'forbidden')
        (self.target/'.PIONEER').mkdir()
        with self.assertRaises(ValueError):self.case.session.guard(self.target/'Contents/new.wav')
        (self.target/'.PIONEER').rmdir()
        self.assertEqual(self.protected(),self.before)

    def test_role_preserves_source_and_mount_guards(self):
        with self.assertRaises(ValueError):
            tx.CdjSession(self.target,[],token=self.case.token+1,volume_uuid=self.case.volume,target_role='ipod_usb')
        with self.assertRaises(RuntimeError):
            tx.CdjSession(self.target,[self.target/'iPod_Control/Music/source.wav'],token=self.case.token,
                          volume_uuid=self.case.volume,target_role='ipod_usb')
        self.assertEqual(self.protected(),self.before)
