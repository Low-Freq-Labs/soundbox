"""A new tag can be clicked in the real window before its native creation."""
from copy import deepcopy
from dataclasses import replace
import unittest
from unittest import mock

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from soundbox import rekordbox_tag_definitions as definitions
from soundbox.library import LibraryTag, TagCategory
from tests import test_tag_order_preferences as fixture


class ImmediateTagWindow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):fixture.TagWindowPreferences.setUpClass()

    def test_new_tag_is_visible_and_rapid_clicks_keep_all_selected_tracks_before_source_save(self):
        case=fixture.TagWindowPreferences();case.setUp();self.addCleanup(case.doCleanups)
        w=case.w;controller=w.tag_controller;created=w.tag_definition_controller
        w.lib.source_tag_categories=[TagCategory('1','Genre','1')]
        source=deepcopy(w.lib);events=[]
        class Definitions:
            def apply(self,request):
                source.source_tags.append(LibraryTag(request['tag_id'],request['name'],'Genre',
                    request['tag_uuid'],request['category_id'],request['category_uuid']))
                events.append('create');return dict(ID=request['tag_id'])
        class Assignments:
            def apply(self,request):
                self_case.assertEqual(set(target.track_id for target in request.tracks),selected)
                self_case.assertEqual(events[0],'create')
                for target in request.tracks:
                    track=source.tracks_by_id[target.track_id];tags=set(track.source_tag_ids)
                    self_case.assertEqual(request.tag_id in tags,target.expected_present)
                    if request.present:tags.add(request.tag_id)
                    else:tags.discard(request.tag_id)
                    source.tracks_by_id[target.track_id]=replace(track,source_tag_ids=sorted(tags))
                events.append(request.present);return len(request.tracks)
        self_case=self
        created.factory=lambda _:Definitions();created.loader=lambda _:deepcopy(source)
        controller._requires_authority=False;controller._store_factory=lambda _:Assignments()
        controller._loader=lambda _:deepcopy(source);controller._store=None;controller._ready=True
        controller.configure(w.lib,True);controller.prep_priority=True
        self.addCleanup(setattr,controller,'prep_priority',False)
        self.enterContext(mock.patch('desktop.tag_creation_controller.assert_rekordbox_closed'))
        self.assertTrue(w._accept_source_curation(w.lib));self.assertTrue(w._save_state())
        w.table.selectAll();selected=set(w._selected_ids())
        request=definitions.request_for(w.lib.source_library_id,'1','1','R&B')
        self.assertTrue(created.enqueue(request))
        self.assertNotIn(request['tag_id'],[tag.tag_id for tag in w.lib.source_tags])
        self.assertIn(request['tag_id'],w.my_tags.checkboxes)
        box=w.my_tags.checkboxes[request['tag_id']]
        self.assertTrue(box.isEnabled());self.assertEqual(box.accessibleName(),'R&B')
        w.my_tags.popup.popup(w.my_tags.mapToGlobal(QPoint(0,w.my_tags.height())))
        for desired in (True,False,True):
            QTest.mouseClick(box,Qt.LeftButton,pos=QPoint(8,box.height()//2))
            self.assertEqual(box.checkState()==Qt.Checked,desired)
            self.assertEqual(set(w._selected_ids()),selected)
        self.assertEqual(events,[])
        self.assertNotIn(request['tag_id'],[tag.tag_id for tag in w.lib.source_tags])
        controller.prep_priority=False;created.schedule();controller._schedule_tag_intent()
        case.wait(lambda:not created.pending and not created.admission_pending and not controller.pending
            and not controller.tag_state_lane.pending and not controller.tag_ack_pending
            and not controller.busy and not w._state_committer.pending)
        self.assertEqual(events,['create',True,False,True])
        self.assertEqual(set(w._selected_ids()),selected)
        self.assertTrue(w.my_tags.checkboxes[request['tag_id']].isChecked())
        self.assertTrue(all(request['tag_id'] in source.tracks_by_id[tid].source_tag_ids for tid in selected))


if __name__=='__main__':unittest.main()
