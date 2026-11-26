"""Real browser editing gestures use private generated library windows."""
import unittest
from unittest import mock

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from tests import test_inline_drafts as fixtures


class InlineEditGestures(unittest.TestCase):
    setUpClass = classmethod(fixtures.InlineDraftPublication.setUpClass.__func__)
    setUp = fixtures.InlineDraftPublication.setUp
    settle = fixtures.InlineDraftPublication.settle
    edit = fixtures.InlineDraftPublication.edit
    editors = fixtures.InlineDraftPublication.editors
    publish = fixtures.InlineDraftPublication.publish

    def test_outside_row_click_commits_and_selects_the_clicked_song_once(self):
        w = self.window
        self.edit()
        destination = w.model.index(1, 0)
        point = w.table.visualRect(destination).center()
        with mock.patch.object(w.metadata_controller, 'enqueue', return_value=True) as enqueue, \
                mock.patch.object(w, '_art_fetch_one'):
            QTest.mouseClick(w.table.viewport(), Qt.LeftButton, pos=point)
            self.settle()
        enqueue.assert_called_once_with('1', {'title': 'Typed unsaved title'})
        self.assertEqual(w.model._rows[w.table.currentIndex().row()]['id'], '2')
        self.assertFalse(self.editors())
        self.assertFalse(w._inline_drafts.retained)

    def test_sorted_commit_preserves_clicked_song_and_selection(self):
        w = self.window
        self.edit(text='ZZZ revised title')
        point = w.table.visualRect(w.model.index(1, 0)).center()
        with mock.patch.object(w.metadata_controller, 'enqueue', return_value=True) as enqueue, \
                mock.patch.object(w, '_art_fetch_one'):
            QTest.mouseClick(w.table.viewport(), Qt.LeftButton, pos=point)
            self.settle()
        enqueue.assert_called_once_with('1', {'title': 'ZZZ revised title'})
        self.assertEqual(w.model._rows[w.table.currentIndex().row()]['id'], '2')
        self.assertEqual([w.model._rows[index.row()]['id']
                          for index in w.table.selectionModel().selectedRows()], ['2'])

    def test_header_click_commits_and_performs_the_requested_sort(self):
        w = self.window
        self.edit(text='ZZZ revised title')
        header = w.table.horizontalHeader()
        with mock.patch.object(w.metadata_controller, 'enqueue', return_value=True) as enqueue, \
                mock.patch.object(w, '_art_fetch_one'):
            QTest.mouseClick(header.viewport(), Qt.LeftButton,
                pos=QPoint(header.sectionViewportPosition(0) + 20, header.height() // 2))
            self.settle()
        enqueue.assert_called_once_with('1', {'title': 'ZZZ revised title'})
        self.assertFalse(self.editors())
        self.assertFalse(w._inline_drafts.retained)

    def test_nonfocusable_footer_click_finishes_the_edit(self):
        w = self.window
        self.edit()
        with mock.patch.object(w.metadata_controller, 'enqueue', return_value=True) as enqueue, \
                mock.patch.object(w, '_art_fetch_one'):
            QTest.mouseClick(w.status_label, Qt.LeftButton)
            self.settle()
        enqueue.assert_called_once_with('1', {'title': 'Typed unsaved title'})
        self.assertFalse(self.editors())

    def test_declined_outside_commit_keeps_recovery_and_allows_next_action(self):
        w = self.window
        self.edit()
        with mock.patch.object(w.metadata_controller, 'enqueue', return_value=False) as enqueue:
            QTest.mouseClick(w.search, Qt.LeftButton)
            QTest.keyClicks(w.search, 'Song 2')
            self.settle()
        enqueue.assert_called_once_with('1', {'title': 'Typed unsaved title'})
        self.assertEqual(w.search.text(), 'Song 2')
        self.assertTrue(w.search.hasFocus())
        self.assertEqual(next(iter(w._inline_drafts.retained.values())).text, 'Typed unsaved title')

    def test_enter_commits_and_escape_cancels_without_saving(self):
        w = self.window
        editor = self.edit()
        with mock.patch.object(w.metadata_controller, 'enqueue', return_value=True) as enqueue, \
                mock.patch.object(w, '_art_fetch_one'):
            QTest.keyClick(editor, Qt.Key_Return)
            self.settle()
        enqueue.assert_called_once_with('1', {'title': 'Typed unsaved title'})
        editor = self.edit(column=1, text='Cancelled artist')
        with mock.patch.object(w.metadata_controller, 'enqueue') as enqueue:
            QTest.keyClick(editor, Qt.Key_Escape)
            self.settle()
        enqueue.assert_not_called()
        self.assertFalse(self.editors())
        self.assertFalse(w._inline_drafts.retained)

    def test_late_ack_cannot_replace_the_next_field_draft_or_selection(self):
        w = self.window
        editor = self.edit()
        with mock.patch.object(w.metadata_controller, 'enqueue', return_value=True), \
                mock.patch.object(w, '_art_fetch_one'):
            QTest.keyClick(editor, Qt.Key_Tab)
            self.settle()
        editor = self.editors()[0]
        editor.selectAll()
        QTest.keyClicks(editor, 'Still editing artist')
        editor.setSelection(4, 5)
        caret = (editor.cursorPosition(), editor.selectionStart(), editor.selectionLength())
        self.publish(**{'1': {'name': 'Typed unsaved title'}})
        self.assertEqual(self.editors()[0].text(), 'Still editing artist')
        editor = self.editors()[0]
        self.assertEqual((editor.cursorPosition(), editor.selectionStart(), editor.selectionLength()), caret)
        self.assertTrue(editor.hasFocus())
        self.assertEqual(w.model._rows[w.table.currentIndex().row()]['id'], '1')
        self.assertEqual(w.table.currentIndex().column(), 1)

    def test_artists_tab_and_backtab_follow_visible_editable_fields(self):
        w = self.window
        w.sb_tree.setCurrentItem(w.sb_tree.topLevelItem(1))
        self.settle()
        editor = self.edit(view=w.artists_view, column=1, text='New artist')
        with mock.patch.object(w.metadata_controller, 'enqueue', return_value=True) as enqueue, \
                mock.patch.object(w, '_art_fetch_one'):
            QTest.keyClick(editor, Qt.Key_Tab)
            self.settle()
            self.assertEqual(w.artists_view.currentIndex().column(), 0)
            self.assertEqual(self.editors(w.artists_view)[0].text(), 'Song 2')
            QTest.keyClick(self.editors(w.artists_view)[0], Qt.Key_Backtab)
            self.settle()
        self.assertEqual(w.artists_view.currentIndex().column(), 1)
        self.assertEqual(self.editors(w.artists_view)[0].text(), 'New artist')
        self.assertEqual(enqueue.call_args_list[0], mock.call('1', {'artist': 'New artist'}))

    def test_tab_from_artist_opens_the_next_song_title(self):
        w = self.window
        editor = self.edit(column=1, text='New artist')
        with mock.patch.object(w.metadata_controller, 'enqueue', return_value=True) as enqueue, \
                mock.patch.object(w, '_art_fetch_one'):
            QTest.keyClick(editor, Qt.Key_Tab)
            self.settle()
        enqueue.assert_called_once_with('1', {'artist': 'New artist'})
        self.assertEqual((w.table.currentIndex().row(), w.table.currentIndex().column()), (1, 0))
        self.assertEqual(len(self.editors()), 1)
        self.assertEqual(self.editors()[0].text(), 'Song 2')

    def test_search_click_commits_and_accepts_typing_on_first_click(self):
        w = self.window
        self.edit()
        with mock.patch.object(w.metadata_controller, 'enqueue', return_value=True) as enqueue, \
                mock.patch.object(w, '_art_fetch_one'):
            QTest.mouseClick(w.search, Qt.LeftButton)
            QTest.keyClicks(w.search, 'Song 2')
            self.settle()
        enqueue.assert_called_once_with('1', {'title': 'Typed unsaved title'})
        self.assertTrue(w.search.hasFocus())
        self.assertEqual(w.search.text(), 'Song 2')
        self.assertFalse(self.editors())

    def test_tab_commits_and_opens_next_editable_field(self):
        w = self.window
        editor = self.edit()
        with mock.patch.object(w.metadata_controller, 'enqueue', return_value=True) as enqueue, \
                mock.patch.object(w, '_art_fetch_one'):
            QTest.keyClick(editor, Qt.Key_Tab)
            self.settle()
        enqueue.assert_called_once_with('1', {'title': 'Typed unsaved title'})
        self.assertEqual(w.table.currentIndex().column(), 1)
        self.assertEqual(len(self.editors()), 1)
        self.assertTrue(self.editors()[0].hasFocus())
        self.assertEqual(self.editors()[0].text(), 'Artist')


if __name__ == '__main__':
    unittest.main()
