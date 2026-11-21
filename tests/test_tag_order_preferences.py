"""Actual window tag ordering and selected bulk gestures use isolated state."""
from copy import deepcopy
from dataclasses import replace
from dataclasses import asdict
import threading
import unittest
from unittest import mock

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from desktop import tag_order
from desktop.tag_assignment_intents import KEY as ASSIGNMENTS
from soundbox.library import LibraryTag, TagCategory, Playlist
from soundbox.state_repository import StateRepository, read_state
from soundbox.rekordbox_tags import TagWriteRequest, SelectedTagTrack
from tests import test_device_navigation_publication as fixture


class TagWindowPreferences(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        fixture.DeviceNavigationPublication.setUpClass()

    def setUp(self):
        self.case=fixture.DeviceNavigationPublication();self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.w=self.case.window
        for timer in self.w.findChildren(QTimer):timer.stop()
        self.w.lib.source_tags=[LibraryTag('a','Dubstep',source_uuid='tag-a'),
                                LibraryTag('b','Favorites',source_uuid='tag-b')]
        self.w.lib.tracks_by_id={tid:replace(track,source_tag_ids=[])
                                for tid,track in self.w.lib.tracks_by_id.items()}
        self.w.tag_controller.configure(self.w.lib,True)
        self.w.table.selectAll();self.w._sync_tags()
        self.assertTrue(self.w._save_state())

    def wait(self,predicate):
        for _ in range(3000):
            if predicate():return
            QTest.qWait(5)
        self.fail('Tag window operation did not settle')

    def xml_preferences(self):
        self.w.lib.source_kind='rekordbox-xml'
        self.w.tag_controller.configure(self.w.lib,True)
        self.w._sync_tags()

    def test_display_order_roundtrips_through_real_preference_worker_without_native_write(self):
        self.xml_preferences()
        w=self.w;source=deepcopy(w.lib.source_tags)
        binding=w.my_tags.order_binding()
        with mock.patch.object(w,'_save_state',side_effect=AssertionError('Full GUI save')):
            with mock.patch.object(w.tag_controller,'_store_factory',side_effect=AssertionError('Native definition write')):
                w.my_tags.order_requested.emit(('b','a'),binding)
                self.assertEqual(tuple(w.my_tags.checkboxes),('b','a'))
                self.wait(lambda:w._state_committer is not None and not w._state_committer.pending)
        reopened=StateRepository(w._state_repository.path).load()['settings']
        context=(str(w.lib.source),w.lib.source_kind,w.lib.source_library_id)
        self.assertEqual([tag.tag_id for tag in tag_order.ordered_tags(source,reopened,context)],['b','a'])
        self.assertEqual(w.lib.source_tags,source)
        self.assertFalse(reopened.get(ASSIGNMENTS))
        self.assertEqual([tag.tag_id for tag in tag_order.ordered_tags(source,reopened,('/other.db','rekordbox-db','other'))],['a','b'])
        replaced=[source[0],replace(source[1],source_uuid='reused-native-id')]
        self.assertEqual([tag.tag_id for tag in tag_order.ordered_tags(replaced,reopened,context)],['a','b'])

    def test_rejected_order_preference_restores_exact_binding_and_previous_settings(self):
        self.xml_preferences()
        w=self.w;before=deepcopy(w.settings);binding=w.my_tags.order_binding()
        with mock.patch.object(w,'_flush_settings_preferences',return_value=False):
            with mock.patch.object(w.my_tags,'restore_order',wraps=w.my_tags.restore_order) as restore:
                w.my_tags.order_requested.emit(('b','a'),binding)
        restore.assert_called_once_with(binding)
        self.assertEqual(w.settings,before)
        self.assertEqual(tuple(w.my_tags.checkboxes),('a','b'))
        self.assertNotIn(tag_order.KEY,read_state(w._state_repository.path)['settings'])

    def test_failed_display_order_storage_stays_pending_and_retries_without_native_write(self):
        self.xml_preferences()
        w=self.w;binding=w.my_tags.order_binding()
        with mock.patch.object(w._state_repository.area,'publish',side_effect=OSError('Generated preference volume full')):
            w.my_tags.order_requested.emit(('b','a'),binding)
            self.wait(lambda:w._state_committer.preferences_error is not None)
            self.assertEqual(tuple(w.my_tags.checkboxes),('b','a'))
            self.assertNotIn(tag_order.KEY,read_state(w._state_repository.path)['settings'])
            self.assertTrue(w._state_committer.preferences_pending)
            self.assertIn('attention',w._pending_save_reason())
        self.assertTrue(w._state_committer.retry_preferences())
        self.wait(lambda:not w._state_committer.pending)
        self.assertIn(tag_order.KEY,read_state(w._state_repository.path)['settings'])
        self.assertFalse(w.settings.get(ASSIGNMENTS))

    def native_categories(self):
        self.w.lib.source_tag_categories=[TagCategory('c','Genre','category-uuid')]
        self.w.lib.source_tags=[replace(tag,category='Genre',category_id='c',category_uuid='category-uuid')
                                for tag in self.w.lib.source_tags]
        self.w._sync_tags()

    def test_native_order_ignores_view_preferences_and_requires_separate_acceptance(self):
        self.native_categories();w=self.w
        context=(str(w.lib.source),w.lib.source_kind,w.lib.source_library_id)
        w.settings[tag_order.KEY]={tag_order._scope(context):[['b','tag-b'],['a','tag-a']]}
        w._sync_tags()
        self.assertEqual(tuple(w.my_tags.checkboxes),('a','b'))
        self.assertFalse(w.my_tags._order_available())
        self.assertIn('acceptance',w.my_tags._drag_labels['a'].toolTip())
        before=deepcopy(w.settings)
        w.my_tags.order_requested.emit(('b','a'),w.my_tags.order_binding())
        self.assertEqual(w.settings,before)
        self.assertFalse(w.tag_definition_controller.pending)

    def test_native_order_dispatches_exact_source_intention_and_projects_without_local_preference(self):
        self.native_categories();w=self.w;controller=w.tag_definition_controller
        controller.order_factory=lambda _library:None
        w.tag_controller.prep_priority=True
        w._sync_tags();self.assertTrue(w.my_tags._order_available())
        w.my_tags.order_requested.emit(('b','a'),w.my_tags.order_binding())
        self.assertEqual(tuple(w.my_tags.checkboxes),('b','a'))
        self.assertNotIn(tag_order.KEY,w.settings)
        self.assertEqual(controller.pending[0]['request']['action'],'reorder')
        self.assertEqual(controller.pending[0]['request']['order'],['b','a'])
        self.assertEqual([tag.tag_id for tag in w.lib.source_tags],['a','b'])
        self.wait(lambda:not controller.admission.pending)
        self.assertEqual(read_state(w._state_repository.path)['settings']['pending_tag_definitions'][0]['request']['order'],['b','a'])

    def test_actual_select_all_212_first_click_and_rapid_tags_remain_selected_before_durability(self):
        w=self.w;sample=w.lib.tracks_by_id['1']
        w.lib.tracks_by_id={str(index):replace(sample,track_id=str(index),name=f'Generated {index}',
            source_uuid=f'uuid-{index}',location_uri=f'file:///synthetic/{index}.wav',source_tag_ids=[])
            for index in range(1,213)}
        w._apply_loaded_library();w._show_collection();w.table.selectAll()
        source=deepcopy(w.lib);calls=[]
        class Store:
            def apply(self,request):
                jobs=read_state(w._state_repository.path)['settings'][ASSIGNMENTS]
                assert jobs[0]['request']['operation_id']==request.operation_id
                for target in request.tracks:
                    track=source.tracks_by_id[target.track_id];tags=set(track.source_tag_ids)
                    assert (request.tag_id in tags)==target.expected_present
                    if request.present:tags.add(request.tag_id)
                    else:tags.discard(request.tag_id)
                    source.tracks_by_id[target.track_id]=replace(track,source_tag_ids=sorted(tags))
                calls.append(request);return len(request.tracks)
        controller=w.tag_controller
        controller._requires_authority=False;controller._store_factory=lambda _lib:Store()
        controller._loader=lambda _path:deepcopy(source);controller._store=None;controller._ready=True
        controller.configure(w.lib,True);w._sync_tags()
        expected=set(w.lib.tracks_by_id)
        self.assertEqual(set(w._selected_ids()),expected)
        self.assertTrue(w._accept_source_curation(w.lib))
        self.assertTrue(w._save_state())
        entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        original=w._state_repository.commit_patch
        def held(patch):
            if not entered.is_set():
                entered.set()
                if not release.wait(5):raise RuntimeError('Generated admission was not released')
            return original(patch)
        with mock.patch.object(w._state_repository,'commit_patch',side_effect=held):
            with mock.patch.object(w,'_save_state',side_effect=AssertionError('Gesture performed full GUI save')) as full_save:
                for tag in ('a','b','a','a'):
                    box=w.my_tags.checkboxes[tag]
                    QTest.mouseClick(box,Qt.LeftButton,pos=QPoint(8,box.height()//2))
                self.assertEqual(w.my_tags.checkboxes['a'].checkState(),Qt.Checked)
                self.assertEqual(w.my_tags.checkboxes['b'].checkState(),Qt.Checked)
                self.assertEqual(set(w._selected_ids()),expected)
                self.assertFalse(calls)
                self.wait(entered.is_set);self.assertFalse(calls)
                release.set()
                self.wait(lambda:len(calls)==4 and not controller.pending and not controller.tag_state_lane.pending
                          and not controller.busy and not controller.tag_ack_pending)
        full_save.assert_not_called()
        self.assertTrue(all({target.track_id for target in request.tracks}==expected for request in calls))
        self.assertEqual([(request.tag_id,request.present) for request in calls],
                         [('a',True),('b',True),('a',False),('a',True)])
        self.assertEqual(set(w._selected_ids()),expected)
        self.assertTrue(all(set(track.source_tag_ids)=={'a','b'} for track in source.tracks_by_id.values()))
        self.assertTrue(all(set(track.source_tag_ids)=={'a','b'} for track in w.lib.tracks_by_id.values()))
        self.assertEqual(read_state(w._state_repository.path)['settings'][ASSIGNMENTS],[])

    def test_smart_off_on_keeps_212_gesture_targets_without_export_membership_or_stale_navigation(self):
        w=self.w;sample=w.lib.tracks_by_id['1'];ids=[str(index) for index in range(1,213)]
        w.lib.tracks_by_id={tid:replace(sample,track_id=tid,name='Generated '+tid,source_uuid='uuid-'+tid,
            location_uri=f'file:///synthetic/{tid}.wav',source_tag_ids=['11']) for tid in ids}
        w.lib.source_tags=[LibraryTag('11','Dubstep',source_uuid='tag-a'),LibraryTag('22','Favorites',source_uuid='tag-b')]
        w.lib.playlists=[Playlist('Smart',[],list(ids),'smart')]
        w.lib.source_playlist_catalog={'nodes':[dict(ID='smart',UUID='smart-uuid',Attribute=4,
            ParentID='root',Seq=1,Name='Smart',
            rb_local_deleted=0,SmartList='<NODE><CONDITION PropertyName="myTag" Operator="8" ValueLeft="11"/></NODE>')],
            'members':[],'revision':'a'*64}
        w.lib=deepcopy(w.lib);w._apply_loaded_library()
        def show_smart():
            item=w._find_tree_item('Smart','smart');self.assertIsNotNone(item)
            w.tree.setCurrentItem(item);w._on_rb_select(item,None);w.table.selectAll()
        show_smart()
        source=deepcopy(w.lib);calls=[];entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        class Store:
            def apply(self,request):
                for target in request.tracks:
                    track=source.tracks_by_id[target.track_id];assigned=set(track.source_tag_ids)
                    assert (request.tag_id in assigned)==target.expected_present
                    if request.present:assigned.add(request.tag_id)
                    else:assigned.discard(request.tag_id)
                    source.tracks_by_id[target.track_id]=replace(track,source_tag_ids=sorted(assigned))
                source.playlists[0].keys=[tid for tid in ids if '11' in source.tracks_by_id[tid].source_tag_ids]
                calls.append(request);return len(request.tracks)
        def readback(_path):
            if len(calls)==2:
                entered.set()
                if not release.wait(5):raise RuntimeError('Generated second readback was not released')
            return deepcopy(source)
        controller=w.tag_controller;controller._requires_authority=False
        controller._store_factory=lambda _lib:Store();controller._store=None;controller._loader=readback
        controller.configure(w.lib,True);controller._ready=True;w._sync_tags();self.assertTrue(w._save_state())
        def click(tag):
            box=w.my_tags.checkboxes[tag];QTest.mouseClick(box,Qt.LeftButton,pos=QPoint(8,box.height()//2))
        click('11');click('11')
        self.wait(entered.is_set)
        self.assertEqual(w.lib.playlists[0].keys,[])  # Authoritative/export membership is already OFF.
        self.assertEqual(len(w.model._rows),212)
        self.assertEqual(set(w._selected_ids()),set(ids))
        click('22')
        staged=controller.tag_state_lane.requests()
        self.assertEqual({target.track_id for request in staged if request.tag_id=='22' for target in request.tracks},set(ids))
        w._select_only(w.sb_tree);w._view_key='sb:Collection';w._show_collection();w.table.clearSelection()
        w.table.selectRow(0);latest=w._selected_ids()
        release.set()
        self.wait(lambda:len(calls)==3 and not controller.pending and not controller.tag_state_lane.pending
                  and not controller.busy and not controller.tag_ack_pending)
        self.assertEqual({target.track_id for target in calls[2].tracks},set(ids))
        self.assertEqual(w._view_key,'sb:Collection');self.assertEqual(w._selected_ids(),latest)
        show_smart();w._sync_tags()
        click('11')
        self.wait(lambda:len(calls)==4 and not controller.pending and not controller.busy and not controller.tag_ack_pending)
        self.assertEqual(w.lib.playlists[0].keys,[])
        self.assertEqual(w.model._rows,[]);self.assertEqual(w._selected_ids(),[])

    def test_bound_failed_smart_selection_survives_ram_retry_and_releases_only_on_discard(self):
        from desktop.tag_selection_view import capture
        w=self.w;c=w.tag_controller;ids=list(w.lib.tracks_by_id)
        for mode in ('durable','ram'):
            with self.subTest(mode=mode):
                w.lib=replace(w.lib,tracks_by_id={tid:replace(track,source_tag_ids=['a'])
                    for tid,track in w.lib.tracks_by_id.items()},playlists=[Playlist('Smart',[],list(ids),'smart')],
                    source_playlist_catalog={'nodes':[dict(ID='smart',UUID='smart-uuid',Attribute=4,
                        ParentID='root',Seq=1,Name='Smart',rb_local_deleted=0,SmartList='<NODE/>')],
                        'members':[],'revision':'a'*64})
                w._apply_loaded_library();item=w._find_tree_item('Smart','smart')
                w.tree.setCurrentItem(item);w._on_rb_select(item,None);w.table.selectAll()
                c._requires_authority=False;c._ready=True;c.prep_priority=True
                request=TagWriteRequest(w.lib.source_library_id,tuple(SelectedTagTrack(tid,'uuid-'+tid,False) for tid in ids),
                    'a','tag-a',True,'Dubstep',('a' if mode=='durable' else 'b')*32)
                raw=asdict(request);raw['tracks']=[asdict(target) for target in request.tracks]
                job=dict(source=str(w.lib.source),library_id=w.lib.source_library_id,request=raw,error='Native failed')
                if mode=='durable':
                    w.settings[ASSIGNMENTS]=[job];self.assertTrue(w._save_state())
                else:
                    job['error']=''
                    self.assertTrue(c.tag_state_lane.admit(job,request,None,ids))
                    def failed(_replacements,_removals,_committed,on_error,**_options):
                        on_error(OSError('Generated RAM admission failure'));return True
                    with mock.patch.object(w,'_start_rekordbox_refresh'):
                        with mock.patch.object(w,'_commit_tag_state',side_effect=failed):c.tag_state_lane.pump()
                    self.assertTrue(c.tag_state_lane.failed)
                w._tag_selection_view=capture(w);self.assertIsNotNone(w._tag_selection_view)
                w.lib=replace(w.lib,tracks_by_id={tid:replace(track,source_tag_ids=[])
                    for tid,track in w.lib.tracks_by_id.items()},playlists=[replace(w.lib.playlists[0],keys=[])])
                w._apply_loaded_library();w._reload_current_view()
                self.assertEqual(w.lib.playlists[0].keys,[])
                self.assertEqual(set(w._selected_ids()),set(ids));self.assertEqual(len(w.model._rows),len(ids))
                self.assertEqual(c.selected_tags(ids),[{'a'}]*len(ids))
                if mode=='ram':
                    c.admission_paused=True
                    self.assertTrue(c.retry());self.wait(lambda:not c.tag_state_lane.pending
                        and not getattr(w._state_committer,'pending',False))
                    self.assertTrue(c.pending)
                with mock.patch('desktop.tag_assignment_intents.QMessageBox.question',return_value=QMessageBox.Yes):
                    self.assertTrue(c.discard())
                self.assertFalse(c.pending);self.assertFalse(c.tag_state_lane.failed)
                c.admission_paused=False
                self.assertEqual(w.lib.playlists[0].keys,[])
                self.assertEqual(w.model._rows,[]);self.assertEqual(w._selected_ids(),[])
