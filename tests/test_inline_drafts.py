"""Genuinely typed inline drafts survive actual compatible native publication."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from dataclasses import replace
import gc
import unittest
from unittest import mock
import weakref

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit

from desktop.choices import Menu
from desktop.inline_drafts import add_recovery_actions
from tests import test_prepared_native_publication as fixtures


class InlineDraftPublication(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.PreparedPublication.setUpClass()
        cls.app = QApplication.instance()

    def setUp(self):
        self.enterContext(mock.patch('desktop.player.NativeDeckBackend',
                                     side_effect=RuntimeError('synthetic draft test disables native audio')))
        self.enterContext(mock.patch('soundbox.rekordbox_db.db_available', return_value=False))
        for name in ('_maybe_fill_waveforms', '_resume_analysis_jobs', '_arm_waveform_rechecks',
                     '_schedule_stock_artwork', '_repair_import_locations'):
            self.enterContext(mock.patch.object(fixtures.MainWindow, name))
        fixture = fixtures.PreparedPublication()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.window = fixture.window
        self.window.show()
        self.settle()

    def settle(self):
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()

    def editors(self, view=None):
        return (view or self.window.table).findChildren(QLineEdit)

    def edit(self, identity='1', *, view=None, column=0, text='Typed unsaved title'):
        w = self.window
        view = view or w.table
        if view is w.table:
            row = next(i for i, value in enumerate(w.model._rows) if value['id'] == identity)
            index = w.model.index(row, column)
        else:
            item = w._artists_browser._tracks[identity]
            item.parent().setExpanded(True)
            index = view.indexFromItem(item, column)
        view.setCurrentIndex(index)
        view.edit(index)
        self.settle()
        self.assertEqual(len(self.editors(view)), 1)
        editor = self.editors(view)[0]
        editor.selectAll()
        QTest.keyClicks(editor, text)
        self.assertTrue(editor.isModified())
        return editor

    def publish(self, **changes):
        w = self.window
        fresh = replace(w.lib, tracks_by_id={**w.lib.tracks_by_id,
            **{identity: replace(w.lib.tracks_by_id[identity], **values)
               for identity, values in changes.items()}})
        w._publish_metadata_library(fresh, tuple(changes))
        self.settle()

    def test_same_order_ack_keeps_modified_editor_text_caret_and_selection(self):
        w = self.window
        editor = self.edit()
        editor.setSelection(5, 7)
        state = (editor.cursorPosition(), editor.selectionStart(), editor.selectionLength())
        changed = mock.Mock()
        w.model.cell_edited.connect(changed)
        reset = mock.Mock()
        w.model.modelReset.connect(reset)
        self.publish(**{'1': {'name': 'Song 1 ACK'}})
        self.assertIs(self.editors()[0], editor)
        self.assertEqual(editor.text(), 'Typed unsaved title')
        self.assertTrue(editor.isModified())
        self.assertEqual((editor.cursorPosition(), editor.selectionStart(), editor.selectionLength()), state)
        reset.assert_not_called()
        changed.assert_not_called()
        self.assertEqual(w.model._rows[0]['title'], 'Song 1 ACK')

    def test_unrelated_reordering_ack_rebinds_exact_draft_without_committing(self):
        w = self.window
        editor = self.edit()
        editor.setSelection(12, -6)
        state = (editor.cursorPosition(), editor.selectionStart(), editor.selectionLength())
        changed = mock.Mock()
        w.model.cell_edited.connect(changed)
        self.publish(**{'2': {'name': 'AAA changed song'}})
        self.assertEqual([row['id'] for row in w.model._rows], ['2', '1', '3'])
        self.assertEqual(len(self.editors()), 1)
        restored = self.editors()[0]
        self.assertEqual(restored.text(), 'Typed unsaved title')
        self.assertTrue(restored.isModified())
        self.assertTrue(restored.hasFocus())
        self.assertEqual((restored.cursorPosition(), restored.selectionStart(), restored.selectionLength()), state)
        self.assertEqual(w.model._rows[w.table.currentIndex().row()]['id'], '1')
        self.assertFalse(w.titles)
        changed.assert_not_called()

    def test_artists_regroup_and_same_item_ack_preserve_the_typed_draft(self):
        w = self.window
        w.sb_tree.setCurrentItem(w.sb_tree.topLevelItem(1))
        self.settle()
        editor = self.edit(view=w.artists_view)
        self.publish(**{'1': {'name': 'Song 1 ACK'}})
        self.assertEqual(editor.text(), 'Typed unsaved title')
        self.assertTrue(editor.isModified())
        self.publish(**{'2': {'name': 'AAA changed song'}})
        self.assertEqual(len(self.editors(w.artists_view)), 1)
        self.assertEqual(self.editors(w.artists_view)[0].text(), 'Typed unsaved title')
        self.assertTrue(self.editors(w.artists_view)[0].isModified())
        self.assertFalse(w.titles)

    def test_replaced_identity_never_receives_old_draft_and_recovery_copy_is_explicit(self):
        w = self.window
        self.edit()
        old = w.lib.tracks_by_id['1']
        self.publish(**{'1': {'source_uuid': '00000000-0000-4000-8000-999999999999'}})
        self.assertFalse(self.editors())
        self.assertEqual(len(w._inline_drafts.retained), 1)
        menu = Menu(w)
        self.addCleanup(menu.deleteLater)
        add_recovery_actions(w, menu)
        clipboard = mock.Mock()
        with mock.patch.object(QApplication, 'clipboard', return_value=clipboard):
            menu.actions()[0].menu().actions()[0].trigger()
        clipboard.setText.assert_called_once_with('Typed unsaved title')
        self.assertFalse(w.titles)
        self.publish(**{'1': {'source_uuid': old.source_uuid}})
        row = next(i for i, value in enumerate(w.model._rows) if value['id'] == '1')
        w.table.setCurrentIndex(w.model.index(row, 0))
        w.table.edit(w.model.index(row, 0))
        self.settle()
        editor = self.editors()[0]
        self.assertEqual(editor.text(), 'Typed unsaved title')
        self.assertTrue(editor.isModified())
        QTest.keyClick(editor, Qt.Key_Escape)
        self.settle()
        self.assertFalse(w._inline_drafts.retained)
        self.assertFalse(w.titles)

    def test_direct_commit_rejects_an_in_place_replacement(self):
        w = self.window
        editor = self.edit()
        index = w.table.currentIndex()
        w.lib.tracks_by_id['1'] = replace(w.lib.tracks_by_id['1'],
            location_uri='file:///replacement.wav', source_uuid='00000000-0000-4000-8000-999999999999')
        with mock.patch.object(w.metadata_controller, 'enqueue') as enqueue:
            w.table.itemDelegate().setModelData(editor, w.model, index)
        enqueue.assert_not_called()
        self.assertEqual(next(iter(w._inline_drafts.retained.values())).text, 'Typed unsaved title')
        self.assertEqual(w.model._rows[index.row()]['title'], 'Song 1')

    def test_newer_search_focus_and_input_are_not_replaced_by_draft_recovery(self):
        w = self.window
        self.edit()
        render = w._render_current_view
        def newer_input():
            render()
            w.search.setFocus()
            QTest.keyClicks(w.search, 'Unmatched new query')
        with mock.patch.object(w, '_render_current_view', side_effect=newer_input):
            self.publish(**{'2': {'name': 'AAA changed song'}})
        self.assertTrue(w.search.hasFocus())
        self.assertEqual(w.search.text(), 'Unmatched new query')
        self.assertFalse(self.editors())
        self.assertEqual(next(iter(w._inline_drafts.retained.values())).text, 'Typed unsaved title')
        self.assertFalse(w.titles)

    def test_explicit_commit_uses_existing_admission_and_refusal_retains_draft(self):
        w = self.window
        editor = self.edit()
        with mock.patch.object(w.metadata_controller, 'enqueue', return_value=False) as enqueue:
            QTest.keyClick(editor, Qt.Key_Return)
            self.settle()
        enqueue.assert_called_once_with('1', {'title': 'Typed unsaved title'})
        self.assertFalse(self.editors())
        self.assertEqual(next(iter(w._inline_drafts.retained.values())).text, 'Typed unsaved title')
        self.assertFalse(w.titles)

    def test_successful_explicit_commit_retires_only_its_recovered_draft(self):
        w = self.window
        self.edit()
        self.publish(**{'2': {'name': 'AAA changed song'}})
        self.assertEqual(len(w._inline_drafts.retained), 1)
        with mock.patch.object(w.metadata_controller, 'enqueue', return_value=True) as enqueue, \
                mock.patch.object(w, '_art_fetch_one'):
            QTest.keyClick(self.editors()[0], Qt.Key_Return)
            self.settle()
        enqueue.assert_called_once_with('1', {'title': 'Typed unsaved title'})
        self.assertFalse(w._inline_drafts.retained)
        self.assertFalse(self.editors())

    def test_canonical_storage_failure_preserves_saved_value_and_the_typed_draft(self):
        w = self.window
        w.lib = replace(w.lib, source_kind='xml')
        editor = self.edit()
        with mock.patch.object(w, '_save_state', return_value=False) as save:
            QTest.keyClick(editor, Qt.Key_Return)
            self.settle()
        save.assert_called_once_with()
        self.assertFalse(w.titles)
        self.assertEqual(w.model._rows[0]['title'], 'Song 1')
        self.assertEqual(next(iter(w._inline_drafts.retained.values())).text, 'Typed unsaved title')

    def test_accepted_synchronous_reordering_callback_does_not_keep_an_already_saved_draft(self):
        w = self.window
        editor = self.edit()
        def accepted(identity, changes):
            self.assertEqual(identity, '1')
            fresh = replace(w.lib, tracks_by_id={**w.lib.tracks_by_id,
                identity: replace(w.lib.tracks_by_id[identity], name=changes['title'])})
            w._publish_metadata_library(fresh, (identity,))
            return True
        with mock.patch.object(w.metadata_controller, 'enqueue', side_effect=accepted) as enqueue, \
                mock.patch.object(w, '_art_fetch_one'):
            QTest.keyClick(editor, Qt.Key_Return)
            self.settle()
        enqueue.assert_called_once_with('1', {'title': 'Typed unsaved title'})
        self.assertFalse(w._inline_drafts.retained)
        self.assertEqual([row['id'] for row in w.model._rows], ['2', '3', '1'])
        index = w.model.index(2, 0)
        w.table.setCurrentIndex(index)
        w.table.edit(index)
        self.settle()
        self.assertEqual(self.editors()[0].text(), 'Typed unsaved title')
        self.assertFalse(self.editors()[0].isModified())

    def test_same_ids_in_a_different_source_do_not_receive_old_draft(self):
        w = self.window
        self.edit()
        fresh = replace(w.lib, source='/synthetic/another.db', source_library_id='another-library')
        w._publish_metadata_library(fresh, ('1',))
        self.settle()
        self.assertFalse(self.editors())
        self.assertFalse(w.titles)
        retained = next(iter(w._inline_drafts.retained.values()))
        self.assertEqual(retained.target.source, ('/synthetic/master.db', 'rekordbox-db', 'library'))
        self.assertEqual(retained.text, 'Typed unsaved title')

    def test_newer_programmatic_focus_is_preserved_without_new_key_input(self):
        w = self.window
        self.edit()
        render = w._render_current_view
        def newer_focus():
            render()
            w.search.setFocus()
        with mock.patch.object(w, '_render_current_view', side_effect=newer_focus):
            self.publish(**{'2': {'name': 'AAA changed song'}})
        self.assertTrue(w.search.hasFocus())
        self.assertFalse(self.editors())
        self.assertEqual(next(iter(w._inline_drafts.retained.values())).text, 'Typed unsaved title')

    def test_older_publication_snapshot_cannot_replace_newer_typed_recovery(self):
        w = self.window
        editor = self.edit()
        render = w._render_current_view
        def newer_typing():
            QTest.keyClicks(editor, ' plus newer text')
            render()
        with mock.patch.object(w, '_render_current_view', side_effect=newer_typing):
            self.publish(**{'2': {'name': 'AAA changed song'}})
        self.assertFalse(self.editors())
        self.assertEqual(next(iter(w._inline_drafts.retained.values())).text,
                         'Typed unsaved title plus newer text')
        self.assertFalse(w.titles)

    def test_deactivation_during_refresh_keeps_text_without_reopening_editor(self):
        w = self.window
        self.edit()
        render = w._render_current_view
        def deactivate():
            render()
            QApplication.sendEvent(w, QEvent(QEvent.ApplicationDeactivate))
        with mock.patch.object(w, '_render_current_view', side_effect=deactivate):
            self.publish(**{'2': {'name': 'AAA changed song'}})
        self.assertTrue(w._inline_drafts.inactive)
        self.assertFalse(self.editors())
        self.assertEqual(next(iter(w._inline_drafts.retained.values())).text, 'Typed unsaved title')

    def test_duplicate_native_occurrence_rebinds_itself_and_never_its_sibling(self):
        w = self.window
        first = dict(id='1', title='Song 1', artist='Artist')
        track = w.lib.tracks_by_id['1']
        pin = dict(source=str(w.lib.source), library_id=w.lib.source_library_id,
                   playlist_id='99', playlist_uuid='playlist', ContentID='1', track_uuid=track.source_uuid)
        rows = [{**first, '_native_membership': {**pin, 'ID': str(i), 'UUID': f'member-{i}'}}
                for i in (1, 2)]
        w._view_key = 'rb:Ordinary'
        w.model.set_rows(rows)
        w.table.setCurrentIndex(w.model.index(1, 0))
        w.table.edit(w.model.index(1, 0))
        self.settle()
        editor = self.editors()[0]
        editor.selectAll()
        QTest.keyClicks(editor, 'Second occurrence draft')
        with mock.patch.object(w, '_render_current_view', side_effect=lambda: w.model.set_rows(rows[::-1])):
            w._reload_current_view()
        self.settle()
        self.assertEqual(w.table.currentIndex().row(), 0)
        self.assertEqual(self.editors()[0].text(), 'Second occurrence draft')
        with mock.patch.object(w, '_render_current_view', side_effect=lambda: w.model.set_rows(rows[:1])):
            w._reload_current_view()
        self.settle()
        self.assertFalse(self.editors())
        self.assertEqual(next(iter(w._inline_drafts.retained)).occurrence[-4], '2')
        self.assertFalse(w.titles)

    def test_blank_artist_uses_raw_text_and_keeps_the_existing_edit_budget(self):
        from soundbox.paths import ARTIST_MAX
        w = self.window
        w.model.set_rows([dict(id='1', title='Song 1', artist='')])
        w.table.setCurrentIndex(w.model.index(0, 1))
        w.table.edit(w.model.index(0, 1))
        self.settle()
        editor = self.editors()[0]
        self.assertEqual(editor.text(), '')
        self.assertEqual(editor.maxLength(), ARTIST_MAX)
        QTest.keyClicks(editor, 'X' * (ARTIST_MAX + 15))
        self.assertEqual(editor.text(), 'X' * ARTIST_MAX)
        self.assertTrue(editor.isModified())
        w.model.patch_rows({'1': dict(id='1', title='Song 1', artist='Native replacement')})
        self.assertEqual(editor.text(), 'X' * ARTIST_MAX)
        self.assertTrue(editor.isModified())


class DraftOwnership(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.PreparedPublication.setUpClass()

    def test_focus_signal_does_not_retain_retired_windows_or_their_curation(self):
        class Payload:
            pass
        for _ in range(2):
            with mock.patch('desktop.player.NativeDeckBackend', side_effect=RuntimeError) as native, \
                    mock.patch.object(fixtures.MainWindow, '_maybe_fill_waveforms'), \
                    mock.patch.object(fixtures.MainWindow, '_resume_analysis_jobs'), \
                    mock.patch.object(fixtures.MainWindow, '_schedule_stock_artwork'):
                fixture = fixtures.PreparedPublication()
                fixture.setUp()
                window = fixture.window
                window._curation_lifetime_probe = Payload()
                window.table.edit(window.model.index(1, 0))
                owner_ref = weakref.ref(window._inline_drafts)
                window_ref = weakref.ref(window)
                payload_ref = weakref.ref(window._curation_lifetime_probe)
                fixture.doCleanups()
                del window, fixture
                # The no-audio constructor mock records child widgets as call
                # arguments; release that test-owned reference before auditing GC.
                native.reset_mock()
                QApplication.instance().processEvents()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                QApplication.instance().processEvents()
                gc.collect()
                self.assertIsNone(owner_ref())
                self.assertIsNone(window_ref())
                self.assertIsNone(payload_ref())


if __name__ == '__main__':
    unittest.main()
