"""Synthetic-only checks for changed-input admission and durable-save elision."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from soundbox.preparation_inventory import PreparationInventory, signature
from soundbox.state_repository import StateRepository
from soundbox.state_io import StateChangedError


class PreparationReceipts(unittest.TestCase):
    def test_roundtrip_mutations_missing_and_corrupt_inventory(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / 'source'
            path.write_bytes(b'one')
            index = root / 'index.json'
            inventory = PreparationInventory(index)
            before = signature([path], ['policy-1'])
            inventory.remember('one', before, before)
            inventory.flush()
            loaded = PreparationInventory(index)
            self.assertTrue(loaded.matches('one', before))
            self.assertFalse(loaded.matches('one', signature([path], ['policy-2'])))
            path.write_bytes(b'two')
            self.assertFalse(loaded.matches('one', signature([path], ['policy-1'])))
            path.unlink()
            self.assertFalse(loaded.matches('one', signature([path], ['policy-1'])))
            with mock.patch.object(Path, 'stat', side_effect=PermissionError()):
                self.assertIsNone(signature([path], []))
            index.write_text('not json')
            self.assertFalse(PreparationInventory(index).matches('one', before))

    def test_unstable_work_does_not_publish_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            inventory = PreparationInventory(Path(td) / 'index.json')
            inventory.remember('one', 'before', 'after')
            inventory.remember('two', None, None)
            inventory.flush()
            self.assertFalse(inventory.path.exists())


class IdenticalStateSaves(unittest.TestCase):
    def test_identical_saves_and_restart_do_not_publish_or_fsync(self):
        with tempfile.TemporaryDirectory() as td:
            repo = StateRepository(Path(td) / 'library.json')
            document = {'custom': {'Important': ['1', '2']}}
            repo.save(document)
            for instance in (repo, StateRepository(repo.path)):
                instance.load()
                with mock.patch.object(instance.area, 'publish') as publish, mock.patch('os.fsync') as fsync:
                    for _ in range(10):
                        instance.save(document)
                publish.assert_not_called()
                fsync.assert_not_called()
            # Caller mutation is real curation, not a no-op.
            document['custom']['Important'].append('3')
            repo.save(document)
            self.assertEqual(repo.load()['custom']['Important'], ['1', '2', '3'])

    def test_removed_file_is_republished_and_old_restore_generation_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            repo = StateRepository(Path(td) / 'library.json')
            repo.save({'settings': {'waveform_zoom': 100}})
            repo.path.unlink()
            repo.save({})
            self.assertTrue(repo.path.exists())
            repo.generation -= 1
            with self.assertRaises(StateChangedError):
                repo.save({})
