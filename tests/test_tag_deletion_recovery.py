"""Shared startup recovery dispatches independently authorized tag deletions."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from desktop.tag_controller import TagEditController
from soundbox import rekordbox_tag_definitions as definitions
from soundbox.rekordbox_write import _inactive_journal_proof, _owned_inactive_journal


class TagDeletionRecovery(unittest.TestCase):
    def test_startup_recovers_delete_journal_only_with_its_own_authority(self):
        with tempfile.TemporaryDirectory() as area:
            root = Path(area)
            lib = SimpleNamespace(source=root / 'master.db', source_library_id='library')
            authority_path = root / 'rekordbox-tag-definition-delete-authority.json'
            authority_path.write_text(json.dumps({'library_id':'library'}))
            (root / 'transaction.json').write_text(json.dumps({'format': definitions.DELETE_FORMAT}))
            authority = SimpleNamespace(library_id='library')
            owner = SimpleNamespace(data_root=root, _write_root=lambda _: root)
            owner._recover_pending = lambda library: TagEditController._recover_pending(owner, library)
            with mock.patch.object(definitions, 'load_deletion_authority', return_value=authority) as load, \
                    mock.patch.object(definitions, 'TagDeletionStore') as store:
                TagEditController.recover_before_read(owner, lib.source)
                self.assertEqual(load.call_count, 2)
                load.assert_called_with(authority_path, lib.source, 'library')
                store.assert_called_once_with(authority, root)
                store.return_value.recover.assert_called_once_with()
            with mock.patch.object(definitions, 'load_deletion_authority', return_value=None), \
                    mock.patch.object(definitions, 'TagDeletionStore') as store:
                with self.assertRaisesRegex(RuntimeError, 'accepted library authority'):
                    owner._recover_pending(lib)
                store.assert_not_called()
            self.assertTrue((root / 'transaction.json').exists())

    def test_metadata_recovery_uses_its_own_authority_and_common_data_root(self):
        from soundbox import rekordbox_metadata_store as metadata
        with tempfile.TemporaryDirectory() as area:
            root = Path(area)
            lib = SimpleNamespace(source=root / 'master.db', source_library_id='library')
            authority = SimpleNamespace(library_id='library')
            (root / 'transaction.json').write_text(json.dumps({'format': metadata.FORMAT}))
            (root / 'rekordbox-metadata-authority.json').write_text(json.dumps({'library_id':'library'}))
            owner = SimpleNamespace(data_root=root, _write_root=lambda _: root)
            owner._recover_pending = lambda library: TagEditController._recover_pending(owner, library)
            with mock.patch.object(metadata, 'load_authority', return_value=authority) as load, \
                    mock.patch.object(metadata, 'SourceMetadataStore') as store:
                TagEditController.recover_before_read(owner, lib.source)
                load.assert_called_with(root / 'rekordbox-metadata-authority.json', lib.source, 'library')
                store.assert_called_once_with(authority, root)
                store.return_value.recover.assert_called_once_with()
            with mock.patch.object(metadata, 'load_authority', return_value=None), \
                    mock.patch.object(metadata, 'SourceMetadataStore') as store:
                with self.assertRaisesRegex(RuntimeError, 'accepted library authority'):
                    owner._recover_pending(lib)
                store.assert_not_called()

    def test_only_exact_delete_receipt_recognizes_an_inactive_sqlite_journal(self):
        with tempfile.TemporaryDirectory() as area:
            root = Path(area); database = root / 'master.db'
            database.write_bytes(b'synthetic source identity')
            database.with_name('master.db-journal').write_bytes(b'\0' * 512)
            info = database.stat()
            record = dict(format=definitions.DELETE_FORMAT, source=str(database), library_id='library',
                          source_file_id=[info.st_dev, info.st_ino], outcome='committed',
                          inactive_sqlite_journal=_inactive_journal_proof(database))
            receipt = root / 'last-result.json'
            for format_name in (definitions.DELETE_FORMAT, 'soundbox-native-tag-repair-v1',
                                'soundbox-native-metadata-v1', 'soundbox-native-mytag-v1'):
                record['format'] = format_name
                receipt.write_text(json.dumps(record))
                self.assertTrue(_owned_inactive_journal(database, root, 'library'))
            database.with_name('master.db-journal').write_bytes(b'\0' * 513)
            self.assertFalse(_owned_inactive_journal(database, root, 'library'))
