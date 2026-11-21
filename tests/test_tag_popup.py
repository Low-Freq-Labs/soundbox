"""Tag checklist dismissal delivers the same pointer gesture to the browser."""
from pathlib import Path
from dataclasses import replace
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtCore import QEvent, QPoint, QStandardPaths, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtWidgets import QApplication

from desktop.app import MainWindow
from soundbox.library import Library, LibraryTag, Track
from soundbox.providers import Providers


class TagPopupBrowsing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.enterContext(mock.patch.object(QStandardPaths, 'isTestModeEnabled', return_value=True))
        self.enterContext(mock.patch.object(MainWindow, '_refresh_devices'))
        self.enterContext(mock.patch('soundbox.rekordbox_db.load_live', side_effect=AssertionError('live source')))
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.win = MainWindow(providers=Providers('public'), isolated_dir=root)
        self.addCleanup(self.win.deleteLater)
        self.addCleanup(self.win.close)
        self.addCleanup(self.win.shutdown)
        self.win.lib = Library({str(i): Track(str(i), f'Song {i}', 'Artist',
                                f'file:///unavailable-popup-fixture/{i}.wav') for i in range(3)}, [], 'synthetic')
        self.win.model.set_rows([dict(id=str(i), title=f'Song {i}', artist='Artist') for i in range(3)])
        self.win.table.setCurrentIndex(self.win.model.index(0, 0))
        self.win.show()
        self.app.processEvents()
        self.button = self.win.my_tags
        # Exercise panel/input behavior only; tag source writes are separately
        # covered by disposable controller tests and never admitted here.
        self.button.popup.aboutToShow.disconnect(self.win._probe_tags)
        self.button.assignment_requested.disconnect(self.win._assign_tag)
        self.tags = [LibraryTag('tag', 'Fixture tag')]
        self.assigned = []
        self.button.assignment_requested.connect(self.assign)
        self.button.set_state(self.tags, [set()])
        self.loaded = self.enterContext(mock.patch.object(self.win, '_load_track'))

    def assign(self, tag, present):
        self.assigned.append((tag, present))
        self.button.set_state(self.tags, [{'tag'}], 'Updating My Tags…')
        self.button.set_state(self.tags, [{'tag'}])

    def open_and_assign(self):
        self.button.popup.popup(self.button.mapToGlobal(QPoint(0, self.button.height())))
        self.app.processEvents()
        box = self.button.checkboxes['tag']
        QTest.mouseClick(box, Qt.LeftButton, pos=QPoint(8, box.height() // 2))
        self.assertEqual(self.assigned, [('tag', True)])
        self.assertTrue(self.button.popup.isVisible())

    def song_point(self):
        rect = self.win.table.visualRect(self.win.model.index(2, 0))
        return self.win.table.viewport().mapTo(self.win, rect.center())

    def test_first_outside_press_dismisses_and_selects_the_clicked_song(self):
        self.open_and_assign()
        QTest.mouseClick(self.win.windowHandle(), Qt.LeftButton, pos=self.song_point())
        self.assertFalse(self.button.popup.isVisible())
        self.assertEqual(self.win._selected_ids(), ['2'])
        self.loaded.assert_not_called()

    def test_one_double_click_after_assignment_loads_once_through_real_button_menu(self):
        observed = []
        def browse():
            try:
                box = self.button.checkboxes['tag']
                QTest.mouseClick(box, Qt.LeftButton, pos=QPoint(8, box.height() // 2))
                observed.append(self.button.popup.isVisible())
                QTest.mouseDClick(self.win.windowHandle(), Qt.LeftButton, pos=self.song_point())
            finally:
                self.button.popup.close()
        QTimer.singleShot(0, self.button, browse)
        QTest.mouseClick(self.button, Qt.LeftButton)
        self.app.processEvents()
        self.assertEqual(observed, [True])
        self.assertEqual(self.assigned, [('tag', True)])
        self.assertFalse(self.button.popup.isVisible())
        self.loaded.assert_called_once_with(self.win.lib.tracks_by_id['2'])

    def test_button_toggle_and_escape_dismiss_without_an_extra_assignment(self):
        self.open_and_assign()
        QTest.mouseClick(self.button, Qt.LeftButton)
        self.assertFalse(self.button.popup.isVisible())
        self.button.popup.popup(self.button.mapToGlobal(QPoint(0, self.button.height())))
        QTest.keyClick(self.button.checkboxes['tag'], Qt.Key_Escape)
        self.assertFalse(self.button.popup.isVisible())
        self.assertEqual(self.assigned, [('tag', True)])

    def test_opening_reconciles_a_stale_tool_window_with_the_complete_grid(self):
        self.tags = [LibraryTag(str(i), name) for i, name in enumerate((
            'Dubstep', 'Riddim', 'Deep Dub', 'DnB', 'Techno', 'House',
            'Trap', 'Brostep', 'Tearout', 'SONNY', 'ZYKO'))]
        self.button.set_state(self.tags, [set()], creation_reason='', deletion_reason='')
        # Model the native tool surface retaining its empty startup geometry.
        # A cached content layout must still repair the window on opening.
        self.button.popup.resize(100, 86)
        self.assertEqual(self.button.popup.size().toTuple(), (100, 86))
        QTest.mouseClick(self.button, Qt.LeftButton)
        self.app.processEvents()
        self.assertEqual(self.button.popup.width(), self.button.width())
        for control in (self.button.create_tag, *self.button.checkboxes.values()):
            rect = control.geometry().translated(self.button.content.pos())
            self.assertTrue(self.button.popup.rect().contains(rect), control.text())
            self.assertTrue(control.isVisible())
        self.assertEqual(self.button.create_tag.text(), 'Create New Tag')
        self.assertGreater(self.button.checkboxes['6'].x(), self.button.checkboxes['0'].x())

    def test_deactivation_and_shutdown_close_the_modeless_panel(self):
        self.open_and_assign()
        QApplication.sendEvent(self.app, QEvent(QEvent.ApplicationDeactivate))
        self.assertFalse(self.button.popup.isVisible())
        self.button.popup.popup(self.button.mapToGlobal(QPoint(0, self.button.height())))
        self.win.shutdown()
        self.assertFalse(self.button.popup.isVisible())

    def test_right_click_on_disabled_tag_targets_that_definition_without_assignment_or_footer(self):
        button = self.button
        button.deletion_requested.disconnect(self.win._delete_tag)
        tag = LibraryTag('context', 'Tag', 'Genre', 'tag-uuid', 'category', 'category-uuid')
        context = ('/synthetic/source', 'rekordbox-db', 'library')
        button.set_state([tag], [], 'Select tracks', creation_reason='', deletion_reason='',
                         deletion_context=context)
        button.popup.popup(QPoint(100, 100)); self.app.processEvents()
        box = button.checkboxes[tag.tag_id]
        self.assertFalse(box.isEnabled())
        self.assertFalse(hasattr(button, 'delete_tag'))
        deleted = []; button.deletion_requested.connect(lambda tag, scope:deleted.append((tag, scope)))
        def choose(menu, _position):
            self.assertEqual([action.text() for action in menu.actions()], ['Rename Tag…', 'Delete Tag…'])
            self.assertFalse(menu.actions()[0].isEnabled())
            self.assertTrue(menu.actions()[1].isEnabled()); menu.actions()[1].trigger()
        with mock.patch('desktop.prep_tags.exec_transient_menu', side_effect=choose):
            event = QContextMenuEvent(QContextMenuEvent.Mouse, box.rect().center(),
                                      box.mapToGlobal(box.rect().center()))
            QApplication.sendEvent(box, event)
        self.assertEqual(deleted, [(tag, context)])
        self.assertFalse(button.popup.isVisible()); self.assertEqual(self.assigned, [])

    def test_tag_context_rejects_changed_definition_source_or_admission(self):
        button = self.button; button.deletion_requested.disconnect(self.win._delete_tag)
        tag = LibraryTag('context', 'Tag', 'Genre', 'uuid', 'category', 'category-uuid')
        context = ('/source', 'rekordbox-db', 'library')
        deleted = []; button.deletion_requested.connect(lambda *args:deleted.append(args))
        for change in ('tag', 'source', 'busy'):
            button.set_state([tag], [], deletion_reason='', deletion_context=context)
            def choose(menu, _position):
                from dataclasses import replace
                button.set_state([replace(tag, source_uuid='new') if change == 'tag' else tag], [],
                    deletion_reason='Saving' if change == 'busy' else '',
                    deletion_context=('/other', 'rekordbox-db', 'library') if change == 'source' else context)
                menu.actions()[1].trigger()
            with mock.patch('desktop.prep_tags.exec_transient_menu', side_effect=choose):
                button._tag_context_menu(button.checkboxes[tag.tag_id].geometry().center())
        self.assertEqual(deleted, [])

    def test_rename_context_targets_clicked_disabled_assignment_and_rejects_stale_menu(self):
        button = self.button; button.rename_requested.disconnect(self.win._rename_tag)
        tag = LibraryTag('context', 'Tag', 'Genre', 'uuid', 'category', 'category-uuid')
        context = ('/source', 'rekordbox-db', 'library')
        renamed = []; button.rename_requested.connect(lambda *args: renamed.append(args))
        for change in ('unchanged', 'tag', 'source', 'busy'):
            button.set_state([tag], [], 'Select tracks', rename_reason='', rename_context=context)
            button.popup.popup(QPoint(100, 100)); self.app.processEvents()
            self.assertFalse(button.checkboxes[tag.tag_id].isEnabled())
            def choose(menu, _position):
                self.assertEqual(menu.actions()[0].text(), 'Rename Tag…')
                self.assertTrue(menu.actions()[0].isEnabled())
                button.set_state([replace(tag, source_uuid='new') if change == 'tag' else tag], [],
                    rename_reason='Saving' if change == 'busy' else '',
                    rename_context=('/other', 'rekordbox-db', 'library') if change == 'source' else context)
                menu.actions()[0].trigger()
            with mock.patch('desktop.prep_tags.exec_transient_menu', side_effect=choose):
                button._tag_context_menu(button.checkboxes[tag.tag_id].geometry().center())
        self.assertEqual(renamed, [(tag, context)])
        self.assertEqual(self.assigned, [])

    def test_pending_creation_is_assignable_but_cannot_rename_or_delete_before_publication(self):
        button = self.button
        tag = LibraryTag('future', 'R&B', 'Genre', 'future-uuid', '1', '1')
        context = ('/synthetic/source', 'rekordbox-db', 'library')
        button.set_state([tag], [set()], deletion_reason='', deletion_context=context,
                         rename_reason='', rename_context=context, pending_creations={tag.tag_id})
        self.assertTrue(button.checkboxes[tag.tag_id].isEnabled())
        requested = []
        button.rename_requested.connect(lambda *args: requested.append(args))
        button.deletion_requested.connect(lambda *args: requested.append(args))
        def inspect(menu, _position):
            self.assertEqual([action.text() for action in menu.actions()], ['Rename Tag…', 'Delete Tag…'])
            for action in menu.actions():
                self.assertFalse(action.isEnabled())
                self.assertIn('creating this tag', action.toolTip())
        with mock.patch('desktop.prep_tags.exec_transient_menu', side_effect=inspect):
            button._tag_context_menu(button.checkboxes[tag.tag_id].geometry().center())
        self.assertEqual(requested, [])

    def test_pending_rename_disables_only_its_assignment_checkbox(self):
        tags = [LibraryTag('one', 'First'), LibraryTag('two', 'Second')]
        self.button.set_state(tags, [set()], assignment_reasons={'one': 'Renaming this tag'})
        self.assertFalse(self.button.checkboxes['one'].isEnabled())
        self.assertTrue(self.button.checkboxes['two'].isEnabled())
        self.assertIn('Renaming this tag', self.button.checkboxes['one'].toolTip())

    def test_library_tag_controls_and_save_gesture_stay_live_during_ipod_commands(self):
        win = self.win
        win.lib = replace(win.lib, source_kind='rekordbox-db', source_library_id='library',
            source_tags=self.tags, tracks_by_id={key:replace(track, source_tag_ids=[], source_uuid='uuid-' + key)
                                               for key, track in win.lib.tracks_by_id.items()})
        controller = SimpleNamespace(lib=win.lib, reason=mock.Mock(return_value=''),
            selected_tags=mock.Mock(return_value=[set()]), request=mock.Mock(return_value=True),
            probe=mock.Mock(), footer_status_text='', status_text='')
        definitions = SimpleNamespace(reason=lambda:'', deletion_reason=lambda:'', rename_reason=lambda:'',
            assignment_reason=lambda *_args:'',
            native_order=lambda tags:list(tags),
            order_reason=lambda:'Native order is not admitted by this assignment fixture')
        dev = '/generated/no-device-io'
        command = SimpleNamespace(target=SimpleNamespace(path=dev))
        owner = SimpleNamespace(active=SimpleNamespace(command=command))
        for intent in ('export', 'sync', 'delete-playlists'):
            with self.subTest(intent=intent), \
                    mock.patch.object(win, 'tag_controller', controller), \
                    mock.patch.object(win, 'tag_definition_controller', definitions), \
                    mock.patch.object(win, '_device_operation_owned', return_value=True), \
                    mock.patch.object(win, '_device_coordinator', owner), \
                    mock.patch.object(win, '_device_profiles', {dev:SimpleNamespace(kind='stock_ipod')}), \
                    mock.patch.object(win, '_sync_rekordbox_connection'):
                command.intent = intent
                win._set_device_progress(420); win.progress.show(); win.cancel_btn.show()
                win.status_label.setText('Copying iPod')
                win._sync_tags(); self.assertTrue(self.button.checkboxes['tag'].isEnabled())
                self.assertFalse(self.button._order_available())
                self.assertTrue(self.button.create_tag.isEnabled())
                controller.request.reset_mock(); controller.probe.reset_mock()
                win._probe_tags(); controller.probe.assert_called_once()
                # Fixture ordinarily intercepts assignment for popup tests.
                self.button.assignment_requested.connect(win._assign_tag)
                try:QTest.mouseClick(self.button.checkboxes['tag'], Qt.LeftButton)
                finally:self.button.assignment_requested.disconnect(win._assign_tag)
                controller.request.assert_called_once_with(['0'], 'tag', True)
                self.assertEqual(win.progress.value(), 420)
                self.assertEqual(win.status_label.text(), 'Copying iPod')
                self.assertTrue(win.cancel_btn.isVisible())

    def test_open_checklist_keeps_transport_shortcuts_with_its_controls(self):
        self.open_and_assign()
        with (mock.patch.object(self.win.player_bar, 'current_track', return_value='already-loaded'),
              mock.patch.object(self.win.player_bar, 'toggle') as toggle):
            QTest.keyClick(self.win.table, Qt.Key_Space)
            toggle.assert_not_called()
            QTest.mouseClick(self.win.windowHandle(), Qt.LeftButton, pos=self.song_point())
            self.win.table.setFocus()
            QTest.keyClick(self.win.table, Qt.Key_Space)
            toggle.assert_called_once()
