"""Keyboard editor capture, failed-save rollback and search without live state."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from unittest.mock import Mock

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from desktop.keyboard import Keymap
from desktop.keyboard_page import BindingDialog, KeyboardPage

_app = None


def setUpModule():
    global _app
    _app = QApplication.instance() or QApplication([])


class KeyboardEditor(unittest.TestCase):
    def test_capture_explains_conflict_without_changing_or_executing_it(self):
        mapping = Keymap()
        dialog = BindingDialog(mapping, "transport.cue")
        self.addCleanup(dialog.deleteLater)
        try:
            dialog.eventFilter(dialog, QKeyEvent(QEvent.KeyPress, Qt.Key_Space, Qt.NoModifier))
            self.assertTrue(dialog.move)
            self.assertIn("Play / pause", dialog.feedback.text())
            self.assertEqual(dialog.save.text(), "Move shortcut")
            self.assertEqual(mapping.action("Space"), "transport.play")
            dialog.eventFilter(dialog, QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
            self.assertEqual(mapping.action("Space"), "transport.play")
        finally:
            dialog.reject()

    def test_failed_save_restores_editor_and_leaves_runtime_snapshot_unchanged(self):
        runtime = Keymap()
        save = Mock(return_value=False)
        page = KeyboardPage(Keymap(runtime.dump()), save)
        self.addCleanup(page.deleteLater)
        page._change(lambda: page.keymap.assign("transport.cue", "V"))
        self.assertEqual(page.keymap.bindings("transport.cue"), ["C"])
        self.assertEqual(runtime.bindings("transport.cue"), ["C"])
        self.assertIn("not saved", page.message.text())
        save.assert_called_once()

    def test_search_matches_actions_and_bindings_without_collapsing_categories(self):
        page = KeyboardPage(Keymap(), lambda _state: True)
        self.addCleanup(page.deleteLater)
        page.search.setText("session")
        visible = [command.id for command, row in page.rows if not row.isHidden()]
        self.assertEqual(visible, ["transport.cue"])
        page.search.setText("Space")
        visible = [command.id for command, row in page.rows if not row.isHidden()]
        self.assertEqual(visible, ["transport.play"])
