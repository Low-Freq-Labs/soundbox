"""Native reorder gestures preserve the export MIME and fixed sidebar rows."""
from types import SimpleNamespace
from copy import deepcopy
import unittest
from unittest import mock

from PySide6.QtCore import QPoint,QPointF,Qt
from PySide6.QtWidgets import QApplication,QTreeWidgetItem

from desktop.trees import (UnifiedPlaylistTree,COLLECTION_ROLE,PATH_ROLE,RB_NODE_ROLE,
    RB_NODE_KIND_ROLE,NATIVE_TREE_ROLE,PINNED_TREE_ROLE)


class NativeReorderGestures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.tree=UnifiedPlaylistTree();self.tree.resize(360,300)
        collection=QTreeWidgetItem(['Collection']);collection.setData(0,COLLECTION_ROLE,True)
        collection.setData(0,PINNED_TREE_ROLE,True);self.tree.addTopLevelItem(collection)
        def node(identity,name,parent=None,folder=False,pinned=False):
            item=QTreeWidgetItem([name]);item.setData(0,RB_NODE_ROLE,identity)
            item.setData(0,NATIVE_TREE_ROLE,True);item.setData(0,RB_NODE_KIND_ROLE,int(folder))
            item.setData(0,PINNED_TREE_ROLE,pinned)
            if not folder:item.setData(0,PATH_ROLE,name)
            if parent:parent.addChild(item)
            else:self.tree.addTopLevelItem(item)
            return item
        self.artists=node('10','Artists',folder=True,pinned=True)
        self.first=node('11','First artist',self.artists)
        self.second=node('12','Second artist',self.artists)
        self.folder=node('20','Empty folder',folder=True)
        self.playlist=node('30','Ordinary playlist')
        self.artists.setExpanded(True);self.tree.show();self.app.processEvents()
        self.addCleanup(self.close)

    def close(self):
        self.tree.close();self.tree.deleteLater();self.app.processEvents()

    def event(self,item,mime=None,*,after=False):
        rect=self.tree.visualItemRect(item)
        point=QPoint(rect.center().x(),rect.bottom()-1 if after else rect.top()+1)
        return SimpleNamespace(source=lambda:self.tree,mimeData=lambda:mime,
                               position=lambda:QPointF(point),acceptProposedAction=mock.Mock(),ignore=mock.Mock())

    def test_native_internal_mime_keeps_device_export_text_and_cannot_move_fixed_rows(self):
        md=self.tree.mimeData([self.playlist])
        self.assertEqual(md.text(),'rb\tOrdinary playlist')
        self.assertEqual(bytes(md.data(self.tree._native_mime)),b'30')
        for fixed in (self.artists,self.tree.topLevelItem(0)):
            self.assertFalse(self.tree.mimeData([fixed]).hasFormat(self.tree._native_mime))
        self.assertIsNone(self.tree._native_drop_destination(QPoint(1,1),'10'))

    def test_empty_native_folder_can_start_a_reorder_drag(self):
        with mock.patch.object(self.tree,'_playlist_drag_items',return_value=[self.folder]), \
                mock.patch('desktop.trees._start_chip_drag') as drag:
            self.tree.startDrag(Qt.CopyAction|Qt.MoveAction)
            drag.assert_called_once()
            self.assertTrue(drag.call_args.args[1].hasFormat(self.tree._native_mime))

    def test_root_drop_above_collection_clamps_below_both_fixed_rows(self):
        self.assertEqual(self.tree._native_drop_destination(QPoint(1,1),'30')[:2],('20',False))
        emitted=[];self.tree.native_reordered.connect(lambda *args:emitted.append(args))
        event=self.event(self.folder,self.tree.mimeData([self.playlist]))
        self.tree.dragMoveEvent(event)
        self.assertIsNotNone(self.tree._insert_y)
        self.tree.dropEvent(event)
        self.assertEqual(emitted,[('30','20',False)])
        self.assertIsNone(self.tree._insert_y)
        # Qt does not move anything before the caller persists the intent.
        self.assertEqual(self.tree.topLevelItem(2),self.folder)
        self.assertEqual(self.tree.topLevelItem(3),self.playlist)

    def test_artist_children_can_reorder_without_reparenting(self):
        point=self.tree.visualItemRect(self.first).topLeft()+QPoint(50,1)
        self.assertEqual(self.tree._native_drop_destination(point,'12')[:2],('11',False))
        outside=self.tree.visualItemRect(self.playlist).center()
        self.assertIsNone(self.tree._native_drop_destination(outside,'12'))


class PlaylistBatchFIFO(unittest.TestCase):
    def test_rapid_gestures_capture_newer_pending_xml_clocks_before_save_without_mutating_callers(self):
        from test_ui_source_playlists import NativePlaylistGestures
        from soundbox.rekordbox_playlist_store import batch_for,request_for
        from desktop.rekordbox_playlists import QUEUE
        NativePlaylistGestures.setUpClass()
        fixture=NativePlaylistGestures();fixture.setUp();self.addCleanup(fixture.tearDown)
        window=fixture.window;catalog=deepcopy(fixture.catalog);saved=[]
        window._save_state.side_effect=lambda:saved.append(deepcopy(window.settings)) or True
        create=request_for(catalog,'create',name='Future')
        preview=deepcopy(catalog);preview['nodes'].append(dict(ID=create['playlist_id'],UUID=create['playlist_uuid']))
        append=request_for(preview,'append',identity=create['playlist_id'],tracks=list(window.lib.tracks_by_id.values()))
        create['xml_timestamp']+=1000000;append['xml_timestamp']=create['xml_timestamp']
        first=batch_for(catalog,[create,append]);original_first=deepcopy(first)
        self.assertTrue(fixture.controller.enqueue(first))
        window.tag_controller.busy=True
        try:
            rename=request_for(preview,'rename',identity=create['playlist_id'],name='Rapid rename')
            rename['xml_timestamp']=first['requests'][-1]['xml_timestamp']-1;original_rename=deepcopy(rename)
            self.assertTrue(fixture.controller.enqueue(rename))
            captured=fixture.controller.pending[-1]['request']
            self.assertEqual(captured['xml_timestamp'],first['requests'][-1]['xml_timestamp']+1)
            second=batch_for(catalog,[request_for(preview,'append',identity=create['playlist_id'],
                                                 tracks=list(window.lib.tracks_by_id.values()))])
            original_second=deepcopy(second)
            self.assertTrue(fixture.controller.enqueue(second))
            self.assertEqual(fixture.controller.pending[-1]['request']['requests'][0]['xml_timestamp'],captured['xml_timestamp']+1)
            self.assertEqual(saved[-1][QUEUE],fixture.controller.pending)
            self.assertEqual((first,rename,second),(original_first,original_rename,original_second))
        finally:window.tag_controller.busy=False

    def test_fifo_rebases_envelope_and_every_child_before_admitting_the_next_worker(self):
        from test_ui_source_playlists import NativePlaylistGestures
        from soundbox.rekordbox_playlist_store import batch_for,request_for,validate_request
        from soundbox.rekordbox_playlist_plan import revision
        NativePlaylistGestures.setUpClass()
        fixture=NativePlaylistGestures();fixture.setUp();self.addCleanup(fixture.tearDown)
        window=fixture.window;catalog=deepcopy(fixture.catalog);calls=[]
        def apply(request):
            validate_request(request)
            self.assertEqual(request['revision'],catalog['revision'])
            calls.append(deepcopy(request))
            if request['operation']=='rename':catalog['nodes'][0]['Name']=request['name']
            else:
                for child in request['requests']:
                    self.assertEqual(child['revision'],request['revision'])
                    if child['operation']=='create':
                        catalog['nodes'].append(dict(ID=child['playlist_id'],UUID=child['playlist_uuid'],Name=child['name'],
                            ParentID=child['parent'],Seq=2,Attribute=0,rb_local_deleted=0))
                    else:
                        for track in child['tracks']:
                            catalog['members'].append(dict(ID=track['id'],UUID=track['membership_uuid'],
                                PlaylistID=child['playlist_id'],ContentID=track['track_id'],TrackNo=1,rb_local_deleted=0))
            catalog['revision']=revision(catalog['nodes'],catalog['members'])
            return deepcopy(catalog)
        fixture.backend.apply=apply
        fixture.backend.committed_transition=lambda request: (
            (request['revision'],catalog['revision']) if calls and calls[-1]==request else None)
        preview=deepcopy(catalog)
        create=request_for(preview,'create',name='Batched')
        preview['nodes'].append(dict(ID=create['playlist_id'],UUID=create['playlist_uuid']))
        append=request_for(preview,'append',identity=create['playlist_id'],tracks=list(window.lib.tracks_by_id.values()))
        request=batch_for(catalog,[create,append])
        self.assertTrue(fixture.controller.enqueue(fixture.rename('First')))
        self.assertTrue(fixture.controller.enqueue(request))
        fixture.wait(lambda:not fixture.controller.pending and not window.tag_controller.owned)
        self.assertEqual([r['operation'] for r in calls],['rename','batch'])
        self.assertNotEqual(calls[1]['revision'],request['revision'])
        self.assertEqual(next(p.keys for p in window.lib.playlists if p.pid==create['playlist_id']),['one'])


if __name__=='__main__':unittest.main()
