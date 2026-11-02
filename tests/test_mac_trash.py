"""File Provider fallback invariants using private synthetic files/Trash only."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from uuid import uuid4

from soundbox import mac_trash,mac_xattrs
from soundbox.rekordbox_removal_store import RemovalStore


class ConfirmedTrashFallback(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name).resolve()
        self.trash=self.root/'trash';self.trash.mkdir();self.source=self.root/'song.wav'
        self.source.write_bytes(b'private synthetic original');self.tag=str(uuid4())

    def tagged(self):mac_xattrs.set(self.source,mac_trash.TAG,self.tag.encode())

    def test_atomic_move_preserves_inode_bytes_tag_and_original_is_recoverable(self):
        self.tagged();before=self.source.stat();raw=self.source.read_bytes()
        target=mac_trash.move_confirmed(self.source,self.trash)
        self.assertFalse(self.source.exists());self.assertEqual(target.read_bytes(),raw)
        self.assertEqual(target.stat().st_ino,before.st_ino)
        self.assertEqual(mac_xattrs.get(target,mac_trash.TAG),self.tag.encode())
        target.rename(self.source);self.assertEqual(self.source.read_bytes(),raw)

    def test_absent_confirmation_or_collision_cannot_move_or_overwrite_any_file(self):
        with self.assertRaises(OSError):mac_trash.move_confirmed(self.source,self.trash)
        self.tagged();target=self.trash/mac_trash.fallback_name(self.source,self.tag);target.write_bytes(b'unrelated')
        raw=self.source.read_bytes()
        with self.assertRaises(OSError):mac_trash.move_confirmed(self.source,self.trash)
        self.assertEqual(self.source.read_bytes(),raw);self.assertEqual(target.read_bytes(),b'unrelated')
        alias=self.root/'redirect.wav';alias.symlink_to(self.source)
        with self.assertRaises(OSError):mac_trash.move_confirmed(alias,self.trash)

    def test_long_unicode_name_can_be_found_after_process_loss_before_progress_receipt(self):
        self.source.rename(self.root/('é'*120+'.wav'));self.source=self.root/('é'*120+'.wav');self.tagged()
        record=dict(path=str(self.source),tag=self.tag,sha256=hashlib.sha256(self.source.read_bytes()).hexdigest())
        target=mac_trash.move_confirmed(self.source,self.trash)
        self.assertLessEqual(len(target.name.encode()),255)
        owner=object.__new__(RemovalStore);owner.trash_root=self.trash
        self.assertEqual(owner._trash_location(record),target)

    def test_all_sources_use_exact_confirmed_destination_without_os_name_guessing(self):
        from desktop.removal import move_to_trash
        result=self.trash/'expected.wav'
        with mock.patch.object(mac_trash,'move_confirmed',return_value=result) as move:
            self.assertEqual(move_to_trash(self.source),result);move.assert_called_once_with(self.source)
        self.assertTrue(self.source.exists())
        cloud=Path.home()/'Library/CloudStorage/private-test/song.wav'
        with mock.patch.object(mac_trash,'move_confirmed',return_value=result) as move:
            self.assertEqual(move_to_trash(cloud),result);move.assert_called_once_with(cloud)

    def test_private_trash_needs_no_directory_read_for_move_or_unrecorded_recovery(self):
        self.tagged();raw=self.source.read_bytes()
        record=dict(path=str(self.source),tag=self.tag,sha256=hashlib.sha256(raw).hexdigest())
        self.trash.chmod(0o300)
        try:
            with self.assertRaises(PermissionError):list(self.trash.iterdir())
            target=mac_trash.move_confirmed(self.source,self.trash)
            owner=object.__new__(RemovalStore);owner.trash_root=self.trash
            with mock.patch.object(Path,'iterdir',side_effect=AssertionError('Trash must not be enumerated')):
                self.assertEqual(owner._trash_location(record),target)
                self.assertEqual(owner._trash_location(record,str(target)),target)
            mac_trash.exclusive_move(target,self.source)
            self.assertEqual(self.source.read_bytes(),raw)
        finally:self.trash.chmod(0o700)
