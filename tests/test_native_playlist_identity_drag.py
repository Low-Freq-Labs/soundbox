"""Native drag selection keeps source UUIDs across tree/device reentrancy."""
from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtCore import QPointF,Qt
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QApplication,QTreeWidgetItem
from desktop.trees import (UnifiedPlaylistTree,DeviceTree,PATH_ROLE,RB_NODE_ROLE,NATIVE_TREE_ROLE,
    DEVPATH_ROLE,NATIVE_BINDING_ROLE,NATIVE_PLAYLIST_MIME,RB_NODE_KIND_ROLE)
from soundbox.library import Library,Playlist


def binding(identity):
    return dict(source='/synthetic/master.db',library_id='library',id=str(identity),
                uuid=f'00000000-0000-4000-8000-{identity:012d}',alias=None)


class NativePlaylistIdentityDrag(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def tree(self):
        tree=UnifiedPlaylistTree();tree.resize(320,220);tree.show()
        self.addCleanup(tree.close);self.addCleanup(tree.deleteLater)
        rows=[]
        for identity in (1000,2000):
            item=QTreeWidgetItem(['Same']);item.setData(0,PATH_ROLE,'Same')
            item.setData(0,RB_NODE_ROLE,str(identity));item.setData(0,NATIVE_TREE_ROLE,True)
            item.setData(0,RB_NODE_KIND_ROLE,0)
            item.setData(0,NATIVE_BINDING_ROLE,binding(identity));tree.addTopLevelItem(item);rows.append(item)
        self.app.processEvents();return tree,rows

    def target(self):
        tree=DeviceTree();tree.resize(320,220);tree.show()
        self.addCleanup(tree.close);self.addCleanup(tree.deleteLater)
        item=QTreeWidgetItem(['USB']);item.setData(0,DEVPATH_ROLE,'/fixture/usb');tree.addTopLevelItem(item)
        self.app.processEvents();return tree,item

    def host(self):
        nodes=[dict(ID=str(identity),UUID=binding(identity)['uuid'],rb_local_deleted=0) for identity in (1000,2000)]
        library=Library({},[Playlist('Same',[],['first'],'1000'),Playlist('Same',[],['second'],'2000')],
                        '/synthetic/master.db',source_kind='rekordbox-db',source_library_id='library',
                        source_playlist_catalog=dict(nodes=nodes,members=[]))
        return SimpleNamespace(lib=library,_flash_status=mock.Mock(),_custom_playlists=lambda:[])

    def test_same_display_path_retains_both_native_ids_through_device_drop_and_resolution(self):
        from desktop.app import MainWindow
        source,rows=self.tree();mime=source.mimeData(rows)
        self.assertEqual(mime.text(),'rb\tSame')  # Legacy text is not selection authority.
        self.assertEqual(len(json.loads(bytes(mime.data(NATIVE_PLAYLIST_MIME)))),2)
        target,item=self.target();events=[];target.dropped.connect(lambda path,jobs:events.append((path,jobs)))
        event=QDropEvent(QPointF(target.visualItemRect(item).center()),Qt.CopyAction,mime,Qt.LeftButton,Qt.NoModifier)
        target.dropEvent(event)
        self.assertTrue(event.isAccepted());self.assertEqual(events[0][0],'/fixture/usb')
        jobs=events[0][1];self.assertEqual([kind for kind,_value in jobs],['rbid','rbid'])
        host=self.host();selected=MainWindow._resolve_jobs(host,jobs)
        self.assertEqual([(playlist.pid,playlist.keys) for playlist in selected],[('1000',['first']),('2000',['second'])])

    def test_stale_source_or_uuid_cancels_whole_export_instead_of_exporting_same_named_other_list(self):
        from desktop.app import MainWindow
        for field,value in (('uuid',binding(999)['uuid']),('library_id','another-library'),('source','/other/master.db')):
            host=self.host();old=deepcopy(host.lib.playlists)
            changed={**binding(2000),field:value}
            selected=MainWindow._resolve_jobs(host,[('rbid',json.dumps(binding(1000))),('rbid',json.dumps(changed))])
            self.assertEqual(selected,[]);host._flash_status.assert_called_once()
            self.assertEqual(host.lib.playlists,old)
        host=self.host()
        self.assertEqual(MainWindow._resolve_jobs(host,[('rb','Same')]),[])
        self.assertIn('ambiguous',host._flash_status.call_args.args[0])

    def test_reorder_mime_cannot_retarget_a_reused_id_or_replacement_library(self):
        tree,rows=self.tree();mime=tree.mimeData([rows[0]])
        event=SimpleNamespace(source=lambda:tree,mimeData=lambda:mime,
                              position=lambda:QPointF(tree.visualItemRect(rows[1]).center()))
        self.assertIsNotNone(tree._native_drag(event)[1])
        for field,value in (('uuid',binding(999)['uuid']),('library_id','different')):
            rows[0].setData(0,NATIVE_BINDING_ROLE,{**binding(1000),field:value})
            self.assertEqual(tree._native_drag(event),('1000',None))

    def test_native_alias_newline_cannot_inject_an_extra_legacy_custom_export(self):
        from desktop.trees import CUSTOM_ROLE
        source,rows=self.tree();alias='Old name\ncust\tAnother playlist'
        rows[0].setData(0,CUSTOM_ROLE,alias)
        rows[0].setData(0,NATIVE_BINDING_ROLE,{**binding(1000),'alias':alias})
        target,item=self.target();events=[];target.dropped.connect(lambda _path,jobs:events.append(jobs))
        mime=source.mimeData([rows[0]])
        event=QDropEvent(QPointF(target.visualItemRect(item).center()),Qt.CopyAction,mime,Qt.LeftButton,Qt.NoModifier)
        target.dropEvent(event)
        self.assertTrue(event.isAccepted());self.assertEqual(len(events[0]),1)
        self.assertEqual(events[0][0][0],'rbid');self.assertEqual(json.loads(events[0][0][1])['alias'],alias)

    def test_partial_native_binding_is_rejected_without_legacy_name_fallback(self):
        source,rows=self.tree();rows[1].setData(0,NATIVE_BINDING_ROLE,None)
        mime=source.mimeData(rows);self.assertTrue(mime.hasFormat(NATIVE_PLAYLIST_MIME))
        target,item=self.target();events=[];target.dropped.connect(lambda *args:events.append(args))
        event=QDropEvent(QPointF(target.visualItemRect(item).center()),Qt.CopyAction,mime,Qt.LeftButton,Qt.NoModifier)
        target.dropEvent(event)
        self.assertFalse(event.isAccepted());self.assertEqual(events,[])
