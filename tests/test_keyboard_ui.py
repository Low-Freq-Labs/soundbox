"""Keyboard editor capture, failed-save rollback and search without live state."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from unittest.mock import Mock, patch

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

    def test_unbind_removes_only_edited_binding_and_persists_explicit_empty(self):
        mapping = Keymap()
        mapping.assign('cues.hot_page', 'F9')
        saved = []
        def save(state):
            saved.append(state)
            return True
        page = KeyboardPage(Keymap(mapping.dump()), save)
        self.addCleanup(page.deleteLater)
        def unbind(dialog):
            self.assertTrue(dialog.unbind_button.isEnabled())
            dialog.unbind_button.click()
            return dialog.result()
        with patch.object(BindingDialog, 'exec', unbind):
            page._capture('cues.hot_page', '9')
            self.assertEqual(page.keymap.bindings('cues.hot_page'), ['F9'])
            page._capture('cues.hot_page', 'F9')
        self.assertEqual(Keymap(saved[-1]).bindings('cues.hot_page'), [])
        self.assertEqual(saved[-1]['bindings']['cues.hot_page'], [])
        self.assertEqual(mapping.bindings('cues.hot_page'), ['9','F9'])

    def test_unbind_failed_save_restores_the_existing_shortcut(self):
        page = KeyboardPage(Keymap(), lambda _state: False)
        self.addCleanup(page.deleteLater)
        def unbind(dialog):
            dialog.unbind_button.click()
            return dialog.result()
        with patch.object(BindingDialog, 'exec', unbind):
            page._capture('cues.hot_page', '9')
        self.assertEqual(page.keymap.bindings('cues.hot_page'), ['9'])
        self.assertIn('not saved', page.message.text())

    def test_search_matches_actions_and_bindings_without_collapsing_categories(self):
        page = KeyboardPage(Keymap(), lambda _state: True)
        self.addCleanup(page.deleteLater)
        page.search.setText("session")
        visible = [command.id for command, row in page.rows if not row.isHidden()]
        self.assertEqual(visible, ["transport.cue"])
        page.search.setText("Space")
        visible = [command.id for command, row in page.rows if not row.isHidden()]
        self.assertEqual(visible, ["transport.play"])
