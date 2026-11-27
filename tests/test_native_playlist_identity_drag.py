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

    def nested(self):
        tree=UnifiedPlaylistTree();self.addCleanup(tree.deleteLater)
        layout=((10,'SETS','root',1),(20,'HAMDI','10',1),(30,'BASSSTATION','20',0),
                (40,'DEEP','20',1),(50,'Nested set','40',0),(60,'DUB','10',1),(70,'Other set','60',0))
        items={};nodes=[]
        paths={30:['SETS','HAMDI'],50:['SETS','HAMDI','DEEP'],70:['SETS','DUB']}
        playlists=[]
        for identity,title,parent,kind in layout:
            item=QTreeWidgetItem([title]);items[identity]=item
            item.setData(0,NATIVE_TREE_ROLE,True);item.setData(0,RB_NODE_ROLE,str(identity))
            item.setData(0,RB_NODE_KIND_ROLE,kind);item.setData(0,NATIVE_BINDING_ROLE,binding(identity))
            (tree.addTopLevelItem(item) if parent=='root' else items[int(parent)].addChild(item))
            nodes.append(dict(ID=str(identity),UUID=binding(identity)['uuid'],Name=title,
                              ParentID=parent,Attribute=kind,rb_local_deleted=0))
            if kind==0:
                playlist=Playlist(title,paths[identity],[str(identity)],str(identity))
                playlists.append(playlist);item.setData(0,PATH_ROLE,playlist.display_path)
        lib=Library({},playlists,'/synthetic/master.db',source_kind='rekordbox-db',
                    source_library_id='library',source_playlist_catalog=dict(nodes=nodes,members=[]))
        return tree,items,SimpleNamespace(lib=lib,_flash_status=mock.Mock(),_custom_playlists=lambda:[])

    def drop_selection(self,tree,items,host):
        from desktop.app import MainWindow
        target,row=self.target();captured=[]
        target.dropped.connect(lambda _dev,jobs:captured.append(jobs))
        mime=tree.mimeData(items)
        event=QDropEvent(QPointF(target.visualItemRect(row).center()),Qt.CopyAction,
                        mime,Qt.LeftButton,Qt.NoModifier)
        target.dropEvent(event);self.assertTrue(event.isAccepted())
        return MainWindow._resolve_jobs(host,captured[0])

    def test_selected_nested_folder_becomes_destination_root_without_source_changes(self):
        from soundbox.cdj_export import _playlist_dto
        from soundbox.export_snapshot import capture_export
        tree,items,host=self.nested();before=deepcopy(host.lib)
        # A leaf-only legacy selection retains its existing canonical ancestry.
        leaf=self.drop_selection(tree,[items[30]],host)
        old,_selection=_playlist_dto(host.lib,{p.pid:p for p in leaf},[],None)
        self.assertEqual([row['name'] for row in old if row['folder']],['SETS','HAMDI'])
        selected=self.drop_selection(tree,[items[20]],host)
        self.assertEqual([p.pid for p in selected],['30','50'])
        self.assertEqual([p.folder_path for p in selected],[['SETS','HAMDI'],['SETS','HAMDI','DEEP']])
        self.assertEqual([p.destination_folder_path for p in selected],[['HAMDI'],['HAMDI','DEEP']])
        dto,selection=_playlist_dto(host.lib,{p.pid:p for p in selected},[],None)
        self.assertEqual([row['name'] for row in dto if row['folder']],['HAMDI','DEEP'])
        self.assertEqual([row['source_folder'] for row in selection],
                         [['SETS','HAMDI'],['SETS','HAMDI','DEEP']])
        frozen=capture_export(host.lib,selected).materialize()
        self.assertEqual([p.destination_folder_path for p in frozen.playlists],
                         [['HAMDI'],['HAMDI','DEEP']])
        self.assertTrue(all(p.export_folder_path is None for p in frozen.library.playlists))
        self.assertEqual(host.lib,before)

    def test_parent_sibling_and_overlapping_folder_selections_keep_explicit_roots(self):
        tree,items,host=self.nested()
        whole=self.drop_selection(tree,[items[10]],host)
        self.assertEqual([p.destination_folder_path for p in whole],
                         [['SETS','HAMDI'],['SETS','HAMDI','DEEP'],['SETS','DUB']])
        siblings=self.drop_selection(tree,[items[20],items[60]],host)
        self.assertEqual([p.destination_folder_path for p in siblings],
                         [['HAMDI'],['HAMDI','DEEP'],['DUB']])
        overlap=self.drop_selection(tree,[items[20],items[30]],host)
        self.assertEqual([p.pid for p in overlap],['30','50'])
        self.assertEqual([p.destination_folder_path for p in overlap],[['HAMDI'],['HAMDI','DEEP']])

    def test_stale_folder_uuid_or_missing_descendant_refuses_whole_selection(self):
        tree,items,host=self.nested()
        items[20].setData(0,NATIVE_BINDING_ROLE,{**binding(20),'uuid':binding(999)['uuid']})
        self.assertEqual(self.drop_selection(tree,[items[20]],host),[])
        items[20].setData(0,NATIVE_BINDING_ROLE,binding(20))
        items[40].takeChild(0)
        self.assertEqual(self.drop_selection(tree,[items[20]],host),[])
        self.assertEqual(host._flash_status.call_count,2)

    def test_incremental_and_later_export_retain_the_projected_root(self):
        from soundbox.cdj_incremental import _catalog
        from soundbox.cdj_export import _selected
        tree,items,host=self.nested();host.lib.source_library_id='1'
        for item in items.values():
            value=item.data(0,NATIVE_BINDING_ROLE);value['library_id']='1';item.setData(0,NATIVE_BINDING_ROLE,value)
        selected=self.drop_selection(tree,[items[20]],host)
        previous=dict(view=dict(playlists=[],track_rows=[]),selection=[],next_track_id=1,next_playlist_id=1)
        catalog=_catalog(host.lib,selected,previous,(),True)
        self.assertEqual([row['name'] for row in catalog['nodes'] if row['folder']],['HAMDI','DEEP'])
        previous['selection']=catalog['selection']
        resumed=_selected(host.lib,[],host.lib.playlists,previous)
        self.assertEqual([p.destination_folder_path for p in resumed.values()],[['HAMDI'],['HAMDI','DEEP']])
        again=_catalog(host.lib,[host.lib.playlists[0]],previous,(),True)
        self.assertEqual(again['jobs'][0].destination_folder_path,['HAMDI'])
        self.assertEqual(host.lib.playlists[0].folder_path,['SETS','HAMDI'])

    def test_local_folder_root_and_invalid_destination_keep_canonical_inputs(self):
        from soundbox.export_selection import project_folder_roots,retain_projection
        from dataclasses import replace
        from soundbox import manifest
        local=[dict(folder='00000000-0000-4000-8000-000000000010',name='SETS',children=[
            dict(folder='00000000-0000-4000-8000-000000000020',name='HAMDI',children=[dict(playlist='Bass')])])]
        canonical=Playlist('Bass',['SETS','HAMDI'],['one'])
        library=Library({},[],'/generated/local.xml')
        selected=project_folder_roots(library,[canonical],[dict(kind='local',
            id='00000000-0000-4000-8000-000000000020')],local_tree=local)
        self.assertEqual(selected[0].destination_folder_path,['HAMDI'])
        self.assertEqual(manifest.playlist_key(selected[0]),manifest.playlist_key(canonical))
        self.assertEqual(canonical.folder_path,['SETS','HAMDI'])
        self.assertIsNone(canonical.export_folder_path)
        with self.assertRaises(ValueError):
            replace(canonical,export_folder_path=['Foreign']).destination_folder_path
        with self.assertRaises(ValueError):
            retain_projection(replace(canonical,folder_path=['OTHER','HAMDI']),
                dict(folder_path=['SETS','HAMDI'],export_folder_path=['HAMDI']))

    def test_old_ancestor_pruning_preserves_foreign_unselected_and_classic_only_nodes(self):
        from soundbox.cdj_export import _playlist_dto,_stable_id
        from soundbox.cdj_incremental import _catalog
        from dataclasses import replace
        from soundbox import manifest
        canonical=Playlist('Bass',['SETS','HAMDI'],[],'30')
        library=Library({},[canonical],'/generated/native',source_library_id='1')
        dto,selection=_playlist_dto(library,{manifest.playlist_key(canonical):canonical},[],None)
        ids={row['id']:row['device_id'] for row in dto}
        original=[dict(id=str(row['device_id']),source_id=row['id'],title=row['name'],
            parent=str(ids.get(row['parent_id'],0)),folder=row['folder'],entries=[]) for row in dto]
        old_hamdi=int(next(row['id'] for row in original if row['title']=='HAMDI'))
        for mode in ('foreign-same-name','unselected-child','classic-only-child','divergent-folder'):
            with self.subTest(mode=mode):
                before=deepcopy(original)
                # An unrelated owned empty folder must never enter pruning.
                before.append(dict(id='903',source_id=_stable_id('folder:Elsewhere'),title='Elsewhere',
                                   parent='0',folder=True,entries=[]))
                foreign=dict(id='900',title='SETS',parent='0',folder=True,entries=[])
                before.append(foreign)
                classic=[];foreign_ids=[900]
                if mode=='unselected-child':
                    before.append(dict(id='901',title='Unselected',parent=str(old_hamdi),folder=False,entries=[]))
                    foreign_ids.append(901)
                if mode=='classic-only-child':
                    classic=[dict(id='902',title='Classic only',parent=str(old_hamdi),folder=False,entries=[])]
                previous=dict(view=dict(playlists=before,track_rows=[],classic_playlists=classic,
                    divergent_playlists=[old_hamdi] if mode=='divergent-folder' else []),
                    selection=selection,next_track_id=1,next_playlist_id=1000,foreign_playlists=foreign_ids)
                result=_catalog(library,[replace(canonical,export_folder_path=['HAMDI'])],previous,(),True)
                rows={row['id']:row for row in result['nodes']}
                self.assertIn(900,rows);self.assertIn(903,rows)
                self.assertNotIn(900,result['touched_nodes']);self.assertNotIn(903,result['touched_nodes'])
                if mode!='foreign-same-name':self.assertIn(old_hamdi,rows)
                if mode=='unselected-child':self.assertEqual(rows[901]['parent'],old_hamdi)
