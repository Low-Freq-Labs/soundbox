"""Exact clicked-tag rename forms; no live source or source-write worker."""
from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtWidgets import QApplication, QDialog

from desktop.tag_creation import RenameTagDialog, TagCreationMixin
from soundbox.library import LibraryTag
from tests.test_tag_creation_controller import Window


TAG = LibraryTag('42', 'Original', 'Genre', 'a1500c8c-b985-4d2a-bf20-0532fb55879f',
                 '1', 'f7b4958a-7c63-4fd8-8e0f-de058f95e74d')


class TagRenameForm(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_prefill_duplicate_category_and_valid_trimmed_label(self):
        other = replace(TAG, tag_id='43', name='Taken')
        elsewhere = replace(other, tag_id='44', category_id='2')
        dialog = RenameTagDialog(TAG, [TAG, other, elsewhere])
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.name.text(), TAG.name)
        self.assertEqual(dialog.name.selectedText(), TAG.name)
        for name in ('', ' taken ', 'bad\x7fname'):
            dialog.name.setText(name)
            self.assertFalse(dialog.save.isEnabled())
        dialog.name.setText(' Original ')
        self.assertTrue(dialog.save.isEnabled())
        self.assertEqual(dialog.value(), 'Original')
        dialog = RenameTagDialog(TAG, [TAG, elsewhere])
        self.addCleanup(dialog.deleteLater)
        dialog.name.setText(' Taken ')
        self.assertEqual(dialog.value(), 'Taken')
        self.assertTrue(dialog.save.isEnabled())

    def setUp(self):
        area = self.enterContext(tempfile.TemporaryDirectory())
        self.window = Window(Path(area))
        self.addCleanup(self.window.deleteLater)
        self.window.lib.source_tags = [TAG]
        self.controller = SimpleNamespace(rename_reason=lambda *_args: '', enqueue_rename=mock.Mock(return_value=True))
        self.window.tag_definition_controller = self.controller
        lib = self.window.lib
        self.context = str(lib.source), lib.source_kind, lib.source_library_id

    def test_rename_pins_identity_and_keeps_same_name_in_another_category(self):
        second = replace(TAG, tag_id='43', name='New', category_id='2')
        self.window.lib.source_tags.append(second)
        def accept(dialog):
            dialog.name.setText(' New ')
            return QDialog.Accepted
        with mock.patch.object(RenameTagDialog, 'exec', accept):
            self.assertTrue(TagCreationMixin._rename_tag(self.window, TAG, self.context))
        request = self.controller.enqueue_rename.call_args.args[0]
        self.assertEqual(tuple(request[key] for key in
            ('tag_id', 'tag_uuid', 'category_id', 'category_uuid', 'name', 'new_name')),
            (TAG.tag_id, TAG.source_uuid, TAG.category_id, TAG.category_uuid, 'Original', 'New'))

    def test_cancel_and_unchanged_name_admit_nothing(self):
        with mock.patch.object(RenameTagDialog, 'exec', return_value=QDialog.Rejected):
            self.assertFalse(TagCreationMixin._rename_tag(self.window, TAG, self.context))
        with mock.patch.object(RenameTagDialog, 'exec', return_value=QDialog.Accepted):
            self.assertTrue(TagCreationMixin._rename_tag(self.window, TAG, self.context))
        self.controller.enqueue_rename.assert_not_called()

    def test_changed_source_identity_or_new_duplicate_during_form_admits_nothing(self):
        original = self.window.lib
        for change in ('source', 'uuid', 'duplicate'):
            self.window.lib = replace(original, source_tags=[TAG])
            def changed(dialog):
                dialog.name.setText('New')
                if change == 'source':
                    self.window.lib = replace(self.window.lib, source='/different/master.db')
                elif change == 'uuid':
                    self.window.lib.source_tags = [replace(TAG, source_uuid='replaced')]
                else:
                    self.window.lib.source_tags.append(replace(TAG, tag_id='43', name='New'))
                return QDialog.Accepted
            with self.subTest(change=change), mock.patch.object(RenameTagDialog, 'exec', changed):
                self.assertFalse(TagCreationMixin._rename_tag(self.window, TAG, self.context))
        self.controller.enqueue_rename.assert_not_called()
