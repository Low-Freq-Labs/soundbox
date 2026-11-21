"""Production default read routing and committed-intent recovery on fixtures."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtWidgets import QApplication

from desktop.app import MainWindow
from desktop.metadata_controller import MetadataController
from desktop.shared_imports import SharedImportController
from desktop.tag_controller import TagEditController
from soundbox import rekordbox_db
from soundbox.worker_protocol import ProtocolError
from tests import test_metadata_controller as fixtures


class NativeReadRouting(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.phases = fixtures.MetadataPhases();self.phases.setUp()
        self.addCleanup(self.phases.doCleanups)
        self.w, self.controller = self.phases.w, self.phases.controller
        self.service = self.w.native_reader = SimpleNamespace(read_metadata=mock.Mock(return_value=self.w.lib))

    def test_metadata_production_default_uses_owned_service_and_injected_loader_keeps_path_api(self):
        controller = MetadataController(self.w);self.addCleanup(controller.stop)
        with mock.patch.object(rekordbox_db, 'load_metadata_readback', side_effect=AssertionError('No GIL-heavy fallback')):
            self.assertIs(controller._readback(self.w.lib), self.w.lib)
        self.service.read_metadata.assert_called_once_with(self.w.lib)
        controller.loader = mock.Mock(return_value=self.w.lib)
        self.service.read_metadata.side_effect = AssertionError('Explicit loader must win')
        self.assertIs(controller._readback(self.w.lib), self.w.lib)
        controller.loader.assert_called_once_with(Path(self.w.lib.source))

    def test_missing_service_is_not_a_default_fallback_but_explicit_store_factory_di_still_works(self):
        del self.w.native_reader
        controller = MetadataController(self.w);self.addCleanup(controller.stop)
        with mock.patch.object(rekordbox_db, 'load_metadata_readback', return_value=self.w.lib) as read:
            with self.assertRaisesRegex(RuntimeError, 'background native reader'):controller._readback(self.w.lib)
            read.assert_not_called()
            controller.factory = lambda _lib:None
            self.assertIs(controller._readback(self.w.lib), self.w.lib)
        read.assert_called_once_with(self.w.lib)

    def test_tags_definitions_and_smart_reader_share_service_and_never_hide_service_failure(self):
        tags = TagEditController(Path(self.phases.area), self.w);self.addCleanup(tags.shutdown)
        with mock.patch.object(rekordbox_db, 'load_metadata_readback', side_effect=AssertionError('No default fallback')):
            self.assertIs(tags.readback_library(self.w.lib), self.w.lib)
            self.service.read_metadata.side_effect = ProtocolError('Synthetic broken read pipe')
            with self.assertRaisesRegex(ProtocolError, 'broken read pipe'):tags.readback_library(self.w.lib)

    def test_import_membership_uses_same_complete_reader_and_preserves_failure_and_loader_contract(self):
        tags = TagEditController(Path(self.phases.area), self.w)
        self.addCleanup(tags.shutdown)
        self.w.tag_controller = tags
        controller = SharedImportController(self.w)
        self.addCleanup(controller.stop)
        with mock.patch.object(rekordbox_db, 'load_metadata_readback',
                               side_effect=AssertionError('No GIL-heavy membership fallback')):
            self.assertIs(controller._membership_readback(self.w.lib), self.w.lib)
            self.service.read_metadata.side_effect = ProtocolError('Synthetic import read failure')
            with self.assertRaisesRegex(ProtocolError, 'import read failure'):
                controller._membership_readback(self.w.lib)
        self.service.read_metadata.assert_called_with(self.w.lib)
        loader = controller.loader = mock.Mock(return_value=self.w.lib)
        self.assertIs(controller._membership_readback(self.w.lib), self.w.lib)
        loader.assert_called_once_with(self.w.lib.source)

    def test_committed_write_then_reader_failure_retains_exact_request_and_retry_acknowledges_once_unblocking_quit(self):
        p = self.phases
        self.assertTrue(self.controller.enqueue('local-song', {'title': 'Desired Title'}))
        p.pump();p.deliver()
        request = deepcopy(self.controller.pending[0]['request'])
        self.controller.loader = None
        calls = 0
        def read(_previous):
            nonlocal calls
            calls += 1
            if calls == 1:raise ProtocolError('The read-only helper exited; retry the saved operation')
            return p.load(None)
        self.service.read_metadata.side_effect = read
        self.w.metadata_controller = self.controller
        p.pump();p.deliver()
        self.assertEqual(len(p.writes), 1)
        self.assertEqual(self.controller.pending[0]['request'], request)
        self.assertFalse(self.w.published)
        self.assertIn('attention', MainWindow._pending_save_reason(self.w))
        self.assertTrue(self.controller.retry())
        p.pump();p.deliver()
        p.pump();p.deliver()
        self.assertEqual(len(p.writes), 1)
        self.assertFalse(self.controller.pending)
        self.assertEqual(len(self.w.published), 1)
        self.assertEqual(MainWindow._pending_save_reason(self.w), '')
