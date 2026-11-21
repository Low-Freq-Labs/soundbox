"""Unsupported intelligent gestures never poison the source FIFO."""
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtWidgets import QApplication, QTreeWidgetItem

from desktop.trees import RB_NODE_ROLE
from soundbox import rekordbox_playlist_plan as planner, rekordbox_playlist_store as store
from tests import test_ui_source_playlists as fixture


class SmartMetadataAdmission(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        fixture.NativePlaylistGestures.setUpClass()

    def setUp(self):
        self.case=fixture.NativePlaylistGestures();self.case.setUp();self.addCleanup(self.case.tearDown)
        self.window=self.case.window;self.controller=self.case.controller
        node=self.window.lib.source_playlist_catalog['nodes'][0]
        node.update(Attribute=4,SmartList='<NODE Opaque="preserve"/>',rb_local_synced=0)
        self.refresh()

    def refresh(self):
        catalog=self.window.lib.source_playlist_catalog
        catalog['revision']=planner.revision(catalog['nodes'],catalog['members'])
        self.case.catalog=catalog

    def test_ordinary_smart_rename_is_rejected_before_any_durable_save(self):
        request=store.request_for(self.case.catalog,'rename',identity='1000',name='Changed')
        self.assertFalse(self.controller.enqueue(request))
        self.assertEqual(self.controller.pending,[]);self.window._save_state.assert_not_called()
        self.assertIn('separate native acceptance',self.window._flash_status.call_args.args[0])

    def test_ordinary_drag_across_smart_neighbor_is_rejected_before_fifo(self):
        node=self.case.catalog['nodes'][0];node.update(Attribute=0,SmartList=None)
        self.case.catalog['nodes'] += [dict(node,ID='1001',UUID='00000000-0000-4000-8000-000000000002',
            Seq=2,Attribute=4,SmartList='<NODE Keep="rule"/>'),
            dict(node,ID='1002',UUID='00000000-0000-4000-8000-000000000003',Seq=3)]
        self.refresh()
        request=store.request_for(self.case.catalog,'reorder',identity='1002',anchor_id='1000')
        self.assertFalse(self.controller.enqueue(request))
        self.window._save_state.assert_not_called();self.assertEqual(self.case.calls,[])

    def test_explicit_smart_candidate_keeps_its_independent_acceptance_gate(self):
        request=store.request_for(self.case.catalog,'rename_smart',identity='1000',name='Changed')
        self.assertFalse(self.controller.enqueue(request))
        self.assertEqual(self.controller.pending,[]);self.window._save_state.assert_not_called()
        self.assertIn('renaming requires separate acceptance',self.window._flash_status.call_args.args[0])

    def test_unsaved_tag_click_errors_have_retry_and_guarded_discard_without_durable_fifo(self):
        tags=self.window.tag_controller
        lane=SimpleNamespace(failed=[object()],error='Unconfirmed My Tag clicks need storage attention')
        with (mock.patch.object(tags,'tag_state_lane',lane),mock.patch.object(tags,'retry') as retry,
              mock.patch.object(tags,'discard') as discard):
            self.assertFalse(tags.pending)
            item=QTreeWidgetItem(['Smart']);item.setData(0,RB_NODE_ROLE,'1000')
            menu=self.window._native_menu(item)
            try:
                actions={action.text():action for action in menu.actions()}
                action=actions['Retry My Tag assignments'];self.assertEqual(action.toolTip(),lane.error)
                self.assertTrue(action.isEnabled());action.trigger();retry.assert_called_once()
                action=actions['Discard unsaved My Tag clicks'];self.assertTrue(action.isEnabled())
                action.trigger();discard.assert_called_once()
            finally:menu.deleteLater()
