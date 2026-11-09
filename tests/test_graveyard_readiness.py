"""Graveyard metadata is source-bound and prepared independently of tab visits."""
from pathlib import Path
import tempfile
from threading import Event
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtCore import QEventLoop, QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication, QLabel, QTreeWidget, QWidget

from desktop.graveyard import GraveyardController
from soundbox.library import Library


class Tags(QObject):
    changed = Signal()
    owned = False
    verified = True


class Host(QWidget):
    def __init__(self, root):
        super().__init__()
        self.lib = Library({}, [], 'synthetic', source_library_id='first')
        self.tag_controller = Tags(self)
        self.tag_controller.data_root = root
        self.hidden = {'removed'}
        self.removals = {}
        self.imported = {}
        self.titles = {}
        self.overrides = {}
        self.settings = {}
        self._shutting_down = self._state_maintenance = False
        self._refresh_worker = None
        self._state_repository = SimpleNamespace(writable=False)
        self._grave = QTreeWidget(self)
        self._grave_storage = QLabel(self)
        self._filter_graveyard = mock.Mock()


class GraveyardReadiness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.host = Host(root)
        self.controller = GraveyardController(self.host)
        self.addCleanup(self.host.deleteLater)
        self.addCleanup(self.controller.shutdown)

    def wait(self, predicate):
        loop = QEventLoop()
        poll = QTimer()
        poll.timeout.connect(lambda: loop.quit() if predicate() else None)
        timeout = QTimer()
        timeout.setSingleShot(True)
        timeout.timeout.connect(loop.quit)
        poll.start(5)
        timeout.start(4000)
        if not predicate():
            loop.exec()
        poll.stop()
        timeout.stop()
        self.assertTrue(predicate())

    def entries(self, title='Prepared history'):
        return {'removed': dict(id='removed', path='', display=dict(title=title, artist='Artist'))}

    def test_hidden_preparation_settles_before_tab_visit_without_a_second_admission(self):
        with mock.patch('soundbox.graveyard.catalog', return_value=self.entries()) as catalog:
            self.controller.prepare_library()
            self.wait(lambda: bool(self.controller.entries) and not self.controller.tasks.pending)
            self.assertFalse(self.host._grave.isVisible())
            self.assertEqual(self.controller.info('removed')['title'], 'Prepared history')
            self.controller.schedule()
            self.controller.schedule()
            self.app.processEvents()
            self.assertFalse(self.controller.timer.isActive())
            catalog.assert_called_once()

    def test_source_replacement_rejects_old_result_and_prepares_current_history(self):
        entered = Event()
        release = Event()
        self.addCleanup(release.set)
        def catalog(_source, library_id, _ids, **_kwargs):
            if library_id == 'first':
                entered.set()
                release.wait(3)
            return self.entries(library_id)
        with mock.patch('soundbox.graveyard.catalog', side_effect=catalog) as read:
            self.controller.prepare_library()
            self.wait(entered.is_set)
            self.host.lib = Library({}, [], 'other', source_library_id='second')
            self.controller.prepare_library()
            self.assertEqual(self.controller.entries, {})
            release.set()
            self.wait(lambda: self.controller.info('removed') is not None
                      and self.controller.info('removed')['title'] == 'second'
                      and not self.controller.tasks.pending)
            self.assertEqual(read.call_count, 2)

    def test_shutdown_cancels_metadata_and_rejects_late_publication(self):
        entered = Event()
        release = Event()
        self.addCleanup(release.set)
        def catalog(*_args, should_cancel, **_kwargs):
            entered.set()
            release.wait(3)
            if should_cancel():
                raise RuntimeError('cancelled')
            return self.entries()
        with mock.patch('soundbox.graveyard.catalog', side_effect=catalog):
            self.controller.prepare_library()
            self.wait(entered.is_set)
            QTimer.singleShot(0, release.set)
            self.controller.shutdown()
        self.assertEqual(self.controller.tasks.pending, 0)
        self.assertEqual(self.controller.entries, {})
        self.assertFalse(self.controller.timer.isActive())

    def test_native_source_waits_for_verification_then_prepares_without_show(self):
        self.host.lib.source_kind = 'rekordbox-db'
        self.host.tag_controller.verified = False
        with mock.patch('soundbox.graveyard.catalog', return_value=self.entries()) as catalog:
            self.controller.prepare_library()
            self.app.processEvents()
            catalog.assert_not_called()
            self.assertEqual(self.host._grave_storage.text(), 'Loading source…')
            self.host.tag_controller.verified = True
            self.host.tag_controller.changed.emit()
            self.wait(lambda: bool(self.controller.entries) and not self.controller.tasks.pending)
            catalog.assert_called_once()
            self.assertFalse(self.host._grave.isVisible())
            catalog.side_effect = OSError('fixture source unavailable')
            self.controller.prepare_library()
            self.wait(lambda: not self.controller.tasks.pending
                      and self.controller._notice.startswith('Storage unavailable'))
            self.controller.selection_changed()
            self.assertIn('fixture source unavailable', self.host._grave_storage.text())
            self.controller.schedule()
            self.app.processEvents()
            self.assertEqual(catalog.call_count, 2)
