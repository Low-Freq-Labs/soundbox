"""Real header gestures and scoped persistence on isolated complete windows."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from PySide6.QtCore import QPoint,Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QFileSystemModel,QHeaderView,QLineEdit,QStyleOptionHeader
from PySide6.QtGui import QStandardItemModel

from desktop.app import MainWindow
from desktop.browser_columns import KEY,decoded,encoded,source
from desktop.model import COLUMNS
from desktop.trees import ARTIST_TRACK_ROLE
from soundbox.library import Library,Playlist,Track
from soundbox.providers import Providers
from soundbox.state_repository import StateRepository


class BrowserColumnLayouts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.enterContext(mock.patch.object(MainWindow,'_refresh_devices'))
        self.enterContext(mock.patch('soundbox.rekordbox_db.load_live',side_effect=AssertionError('Live source')))
        self.window=MainWindow(providers=Providers('public'),isolated_dir=self.root)
        self.addCleanup(self.close)
        tracks={str(i):Track(str(i),'Title '+str(i),'Artist',f'file:///owned-missing/{i}.wav',added_ts=i)
                for i in range(1,5)}
        self.window.lib=Library(tracks,[],str(self.root/'source.xml'),source_kind='rekordbox-xml')
        self.window.custom={'A':['1','2'],'B':['3'],'Empty':[]}
        self.window._apply_loaded_library();self.window.show();self.window._show_collection();self.settle()
        self.assertTrue(self.window._save_state())

    def close(self):
        self.window.shutdown();self.window.close();self.window.deleteLater();self.app.processEvents()

    def settle(self):self.app.processEvents();self.app.processEvents()

    def layout(self):
        h=self.window.table.horizontalHeader()
        return tuple((COLUMNS[h.logicalIndex(i)][0],h.sectionSize(h.logicalIndex(i)),
                      h.isSectionHidden(h.logicalIndex(i))) for i in range(h.count()))

    def order(self,header=None):
        h=header or self.window.table.horizontalHeader()
        return [h.logicalIndex(i) for i in range(h.count())]

    def drag(self,header,logical,to,*,before=False,during=None):
        start=QPoint(header.sectionViewportPosition(logical)+header.sectionSize(logical)//2,header.height()//2)
        end=QPoint(header.sectionViewportPosition(to)+(10 if before else header.sectionSize(to)-12),start.y())
        QTest.mousePress(header.viewport(),Qt.LeftButton,Qt.NoModifier,start)
        steps=max(4,abs(end.x()-start.x())//16)
        for step in range(1,steps+1):
            QTest.mouseMove(header.viewport(),QPoint(start.x()+(end.x()-start.x())*step//steps,start.y()),2)
            if during is not None and step==max(1,steps//2):during()
        QTest.mouseRelease(header.viewport(),Qt.LeftButton,Qt.NoModifier,end);self.settle()

    def resize_header(self,logical,delta):
        header=self.window.table.horizontalHeader()
        start=QPoint(header.sectionViewportPosition(logical)+header.sectionSize(logical)-1,header.height()//2)
        end=start+QPoint(delta,0)
        QTest.mousePress(header.viewport(),Qt.LeftButton,Qt.NoModifier,start)
        QTest.mouseMove(header.viewport(),end,10)
        QTest.mouseRelease(header.viewport(),Qt.LeftButton,Qt.NoModifier,end);self.settle()

    def test_real_table_header_drag_preserves_rows_selection_and_independent_device_columns(self):
        w=self.window;w.table.selectRow(1);selected=w._selected_ids();rows=w.model._all_rows
        from desktop.theme_controller import install
        from test_usb_playlist_layout import painted_header_label
        install(self.app)
        reference=painted_header_label(w.table.horizontalHeader())
        headers=[w.fs_view.header(),w.usb_device_pages.playlist_tree.header(),
                 w.stock_ipod_pages.playlist_tree.header(),w.stock_ipod_pages.music_table.horizontalHeader(),
                 *(tree.header() for tree in w.usb_device_pages.detail_trees.values()),
                 *(tree.header() for tree in w.stock_ipod_pages.detail_trees.values())]
        for header in headers:
            with self.subTest(surface=header.parent().objectName()):
                for column in range(header.count()):
                    option=QStyleOptionHeader();header.initStyleOptionForIndex(option,column)
                    self.assertEqual(option.textAlignment,Qt.AlignCenter)
                self.assertEqual(painted_header_label(header),reference)
        plain=QFileSystemModel(w);plain.setReadOnly(True)
        own=self.root/'header-body-fixture.txt';own.write_text('Owned header fixture')
        plain.setRootPath(str(self.root));w.fs_model.setRootPath(str(self.root))
        current=w.fs_model.index(str(own));original=plain.index(str(own))
        self.assertTrue(current.isValid());self.assertTrue(original.isValid())
        self.assertTrue(w.fs_model.isReadOnly())
        self.assertEqual(w.fs_model.columnCount(),plain.columnCount())
        for column in range(plain.columnCount()):
            for role in (Qt.DisplayRole,Qt.FontRole,Qt.ToolTipRole):
                self.assertEqual(w.fs_model.headerData(column,Qt.Horizontal,role),
                                 plain.headerData(column,Qt.Horizontal,role))
            for role in (Qt.DisplayRole,Qt.TextAlignmentRole,Qt.EditRole,Qt.ToolTipRole):
                self.assertEqual(w.fs_model.data(current.siblingAtColumn(column),role),
                                 plain.data(original.siblingAtColumn(column),role))
        self.assertEqual(w.fs_model.headerData(0,Qt.Vertical,Qt.TextAlignmentRole),
                         plain.headerData(0,Qt.Vertical,Qt.TextAlignmentRole))
        device=w.usb_device_pages.playlist_tree
        self.settle()
        device_before=[device.columnWidth(i) for i in range(3)]
        self.drag(w.table.horizontalHeader(),0,3)
        self.assertEqual(self.order()[:4],[1,2,3,0])
        self.assertEqual(self.order(w.artists_view.header()),self.order())
        self.assertIs(w.model._all_rows,rows);self.assertEqual(w._selected_ids(),selected)
        self.assertEqual([device.columnWidth(i) for i in range(3)],device_before)
        self.assertIn('default',w.settings[KEY])

    def test_actual_first_artists_column_drag_keeps_root_children_expander_and_selection(self):
        w=self.window;w._show_artists();self.settle()
        root=w.artists_view.topLevelItem(0);root.setExpanded(True)
        child=root.child(0);w.artists_view.setCurrentItem(child);child.setSelected(True)
        self.drag(w.artists_view.header(),0,3)
        self.assertEqual(self.order()[:4],[1,2,3,0])
        self.assertEqual(self.order(w.artists_view.header()),self.order())
        self.assertIs(w.artists_view.topLevelItem(0),root);self.assertIs(root.child(0),child)
        self.assertTrue(root.isExpanded());self.assertTrue(child.isSelected())
        self.assertEqual(child.data(0,ARTIST_TRACK_ROLE),'1')
        self.assertTrue(root.text(0).startswith('Artist'))
        rect=w.artists_view.visualRect(w.artists_view.indexFromItem(root,0))
        QTest.mouseClick(w.artists_view.viewport(),Qt.LeftButton,Qt.NoModifier,QPoint(rect.left()-8,rect.center().y()))
        self.assertFalse(root.isExpanded())

    def test_styled_header_weight_is_identical_with_selection_and_playlist_switches(self):
        from desktop.theme_controller import install
        install(self.app)
        w=self.window;header=w.table.horizontalHeader();w.table.clearSelection();self.settle()
        def painted():
            QTest.mouseMove(w.table.viewport(),QPoint(6,6));self.settle()
            return header.grab().toImage()
        baseline=painted();default=self.layout()
        w.table.selectAll();self.settle()
        self.assertGreater(len(w.table.selectedIndexes()),0)
        self.assertEqual(painted(),baseline)
        w._show_custom('A');w._restore_view('sb:A');self.settle()
        self.assertEqual(len(w.table.selectedIndexes()),0)
        self.assertEqual(painted(),baseline)
        w._set_column_hidden(2,True);self.drag(header,6,1);chosen=self.layout()
        w._show_collection();w._restore_view('sb:Collection');self.settle()
        self.assertEqual(self.layout(),default)
        w._show_custom('A');w._restore_view('sb:A');self.settle()
        self.assertEqual(self.layout(),chosen)
        w._show_artists();self.settle();artist_header=w.artists_view.header()
        w.artists_view.clearSelection();unselected=artist_header.grab().toImage()
        w.artists_view.selectAll();self.settle()
        self.assertGreater(len(w.artists_view.selectedItems()),0)
        self.assertEqual(artist_header.grab().toImage(),unselected)

    def test_restore_normalizes_legacy_stretch_without_discarding_scoped_layout(self):
        w=self.window;w._show_custom('A');w._set_column_hidden(2,True)
        self.drag(w.table.horizontalHeader(),6,1);chosen=self.layout();saved=deepcopy(w.settings[KEY])
        for header in (w.table.horizontalHeader(),w.artists_view.header()):
            header.setStretchLastSection(True);header.setSectionResizeMode(0,QHeaderView.Stretch)
        w._browser_columns.activate('sb:A',force=True);self.settle()
        for header in (w.table.horizontalHeader(),w.artists_view.header()):
            self.assertFalse(header.stretchLastSection())
            self.assertTrue(all(header.sectionResizeMode(i)==QHeaderView.Interactive for i in range(header.count())))
        self.assertEqual(self.layout(),chosen);self.assertEqual(w.settings[KEY],saved)

    def test_logical_edit_and_tab_targets_follow_visual_header_order(self):
        w=self.window;header=w.table.horizontalHeader()
        self.drag(header,1,0,before=True)
        self.assertEqual(self.order()[:2],[1,0])
        point=QPoint(header.sectionViewportPosition(1)+20,10)
        self.assertEqual(header.logicalIndexAt(point),1)
        index=w.model.index(0,1);w.table.setCurrentIndex(index);w.table.edit(index);self.settle()
        editor=next(editor for editor in w.table.findChildren(QLineEdit) if editor.isVisible())
        self.assertEqual(editor.text(),'Artist')
        QTest.keyClick(editor,Qt.Key_Tab);QTest.qWait(5);self.settle()
        current=w.table.currentIndex()
        self.assertEqual((current.row(),current.column()),(0,0))
        editor=next(editor for editor in w.table.findChildren(QLineEdit) if editor.isVisible())
        self.assertEqual(editor.text(),w.model._rows[0]['title'])
        QTest.keyClick(editor,Qt.Key_Escape)

    def test_per_playlist_visibility_order_widths_default_inheritance_and_empty_playlist(self):
        w=self.window;baseline=self.layout()
        w._show_custom('A');w._restore_view('sb:A');self.settle()
        self.assertEqual(self.layout(),baseline)
        w._set_column_hidden(2,True);self.drag(w.table.horizontalHeader(),6,1)
        before_width=w.table.columnWidth(3);self.resize_header(3,-12)
        self.assertEqual(w.table.columnWidth(3),before_width-12)
        chosen=self.layout();scope=w._browser_columns.scope
        self.assertNotEqual(chosen,baseline);self.assertIn(scope,w.settings[KEY]['playlists'])
        w._show_custom('B');w._restore_view('sb:B');self.settle();self.assertEqual(self.layout(),baseline)
        w._show_custom('Empty');w._restore_view('sb:Empty');w._set_column_hidden(4,True)
        self.assertIn(w._browser_columns.scope,w.settings[KEY]['playlists'])
        w._show_collection();self.settle();self.assertEqual(self.layout(),baseline)
        w._show_artists();self.settle();self.assertEqual(self.layout(),baseline)
        w._show_custom('A');self.settle();self.assertEqual(self.layout(),chosen)
        w._show_collection();w._set_column_hidden(4,True)
        w._show_custom('B');self.assertTrue(w.table.isColumnHidden(4))
        w._show_custom('A');self.assertEqual(self.layout(),chosen)

    def test_untouched_first_visit_restore_and_fit_never_create_playlist_override(self):
        w=self.window
        for name in ('A','B','Empty','A'):
            w._show_custom(name);w._restore_view('sb:'+name);w._fit_browser_columns(fill=True)
        header=w.table.horizontalHeader()
        QTest.mouseClick(header.viewport(),Qt.LeftButton,Qt.NoModifier,
            QPoint(header.sectionViewportPosition(3)+header.sectionSize(3)//2,header.height()//2))
        self.settle();self.assertTrue(w.model._sort)
        w._save_columns();self.settle()
        self.assertFalse(w.settings.get(KEY,{}).get('playlists'))
        self.assertFalse(w.settings.get(KEY,{}).get('local_ids'))

    def test_scoped_layout_is_durable_and_shutdown_on_playlist_does_not_replace_default(self):
        w=self.window;default=self.layout()
        frame=(w.size().width(),w.table.viewport().width(),w._browser_split.sizes())
        w._show_custom('A');w._restore_view('sb:A');w._set_column_hidden(2,True)
        self.drag(w.table.horizontalHeader(),6,1);chosen=self.layout();scope=w._browser_columns.scope
        w._save_columns();w._flush_settings_preferences()
        for _ in range(200):
            self.settle()
            if w._state_committer is not None and not w._state_committer.pending:break
            QTest.qWait(5)
        saved=StateRepository(w._state_repository.path).load()['settings']
        self.assertIn(scope,saved[KEY]['playlists'])
        w.settings=deepcopy(saved);w._restore_columns();self.settle();self.assertEqual(self.layout(),default)
        w._show_custom('A');self.settle();self.assertEqual(self.layout(),chosen)
        library=w.lib
        w.shutdown();w.close();w.deleteLater();self.settle()
        # Hold the viewport constant: the offscreen screen is smaller than the
        # app's minimum width, whose separate window-clamping policy is unrelated.
        with mock.patch('desktop.app.QApplication.screens',return_value=[]):
            self.window=MainWindow(providers=Providers('public'),isolated_dir=self.root)
        w=self.window;w.lib=library;w._apply_loaded_library();w.show();w._show_collection();self.settle()
        self.assertEqual(self.layout(),default,(frame,(w.size().width(),w.table.viewport().width(),w._browser_split.sizes()),w.settings.get('window_rect')))
        w._show_custom('A');self.settle();self.assertEqual(self.layout(),chosen)

    def test_local_rename_delete_recreate_are_atomic_and_preserve_other_source_same_name(self):
        w=self.window;w._show_custom('A');w._set_column_hidden(2,True)
        scope=w._browser_columns.scope;payload=deepcopy(w.settings[KEY]);namespace=encoded(source(w.lib))
        other=encoded(['rekordbox-xml','other.xml',''])
        payload['local_ids'][other]={'A':'00000000-0000-4000-8000-000000009999'}
        w.settings[KEY]=payload
        with mock.patch.object(w,'_save_state',return_value=False):w._rename_local_node('playlist','A','Renamed')
        self.assertEqual(w.settings[KEY],payload);self.assertIn('A',w.custom)
        with mock.patch.object(w,'_save_state',return_value=True):w._rename_local_node('playlist','A','Renamed')
        self.assertEqual(w.settings[KEY]['local_ids'][namespace]['Renamed'],payload['local_ids'][namespace]['A'])
        self.assertEqual(w.settings[KEY]['local_ids'][other],payload['local_ids'][other])
        w._show_custom('Renamed');self.assertEqual(w._browser_columns.scope,scope)
        before=deepcopy(w.settings[KEY])
        with mock.patch('desktop.playlist_folders.QMessageBox.question',return_value=16384), \
                mock.patch.object(w,'_save_state',return_value=False):w._delete_local_node('playlist','Renamed')
        self.assertEqual(w.settings[KEY],before);self.assertIn('Renamed',w.custom)
        with mock.patch('desktop.playlist_folders.QMessageBox.question',return_value=16384), \
                mock.patch.object(w,'_save_state',return_value=True):w._delete_local_node('playlist','Renamed')
        self.assertNotIn(scope,w.settings[KEY]['playlists'])
        w.custom['Renamed']=[];w._show_custom('Renamed');self.assertFalse(w.table.isColumnHidden(2))

    def test_native_rename_keeps_uuid_scope_but_reused_id_and_other_source_do_not_borrow(self):
        w=self.window;w.lib.source_kind='rekordbox-db';w.lib.source_library_id='owned'
        row=dict(ID='1000',UUID='00000000-0000-4000-8000-000000001000',Name='Native',
                 ParentID='root',Seq=1,Attribute=0,rb_local_deleted=0)
        w.lib.source_playlist_catalog=dict(nodes=[row],members=[],revision='a'*64)
        w.lib.playlists=[Playlist('Native',[],['1'],'1000')]
        w._browser_columns.activate('rb:Native');w._set_column_hidden(2,True);scope=w._browser_columns.scope
        row['Name']='Renamed';w.lib.playlists[0].name='Renamed'
        w._browser_columns.activate('rb:Renamed');self.assertEqual(w._browser_columns.scope,scope)
        self.assertTrue(w.table.isColumnHidden(2))
        from soundbox import playlist_unification as merge
        merged=merge.new(w.lib.source,w.lib.source_library_id)
        merged.update(customs_done=True,nodes={'custom:A':dict(id='1000',uuid=row['UUID'])})
        w.settings[merge.KEY]=merged
        w._show_custom('A');self.assertEqual(w._browser_columns.scope,scope)
        row['UUID']='00000000-0000-4000-8000-000000002000'
        w._browser_columns.activate('rb:Renamed');self.assertFalse(w.table.isColumnHidden(2))
        w.lib=replace(w.lib,source=str(self.root/'other.db'))
        w._browser_columns.activate('rb:Renamed');self.assertFalse(w.table.isColumnHidden(2))

    def test_actual_native_sidebar_same_display_paths_keep_exact_independent_layouts(self):
        from soundbox import engine
        w=self.window;w.lib.source_kind='rekordbox-db';w.lib.source_library_id='owned'
        rows=[dict(ID=str(1000+i),UUID=f'00000000-0000-4000-8000-{1000+i:012d}',Name='Same',
                   ParentID='root',Seq=i+1,Attribute=0,rb_local_deleted=0) for i in range(2)]
        w.lib.source_playlist_catalog=dict(nodes=rows,members=[],revision='a'*64)
        w.lib.playlists=[Playlist('Same',[],[str(i+1)],row['ID']) for i,row in enumerate(rows)]
        w._populate_tree(engine.summary(w.lib))
        first=w._find_tree_item('',rows[0]['ID']);second=w._find_tree_item('',rows[1]['ID'])
        self.assertIsNotNone(first);self.assertIsNotNone(second)
        w.tree.setCurrentItem(first);self.settle();w._set_column_hidden(2,True)
        first_scope=w._browser_columns.scope;self.assertIsNotNone(first_scope)
        w.tree.setCurrentItem(second);self.settle()
        second_scope=w._browser_columns.scope
        self.assertIsNotNone(second_scope);self.assertNotEqual(first_scope,second_scope)
        self.assertFalse(w.table.isColumnHidden(2));w._set_column_hidden(4,True)
        w.tree.setCurrentItem(first);self.settle()
        self.assertEqual(w._browser_columns.scope,first_scope)
        self.assertTrue(w.table.isColumnHidden(2));self.assertFalse(w.table.isColumnHidden(4))
        w.tree.setCurrentItem(second);self.settle()
        self.assertEqual(w._browser_columns.scope,second_scope)
        self.assertFalse(w.table.isColumnHidden(2));self.assertTrue(w.table.isColumnHidden(4))

    def test_local_edit_during_real_held_commit_cannot_queue_rolled_back_layout_tokens(self):
        w=self.window;w._show_custom('A');w._set_column_hidden(2,True)
        entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        original=w._state_repository.commit_patch
        def held(patch):
            entered.set()
            if not release.wait(5):raise AssertionError('Held canonical test worker did not retire')
            return original(patch)
        completed=mock.Mock();failed=mock.Mock()
        with mock.patch.object(w._state_repository,'commit_patch',side_effect=held):
            try:
                self.assertTrue(w._commit_metadata_state({('settings','quantize'):True},(),completed,failed))
                self.assertTrue(entered.wait(1));self.assertTrue(w._state_publication)
                w._set_column_hidden(4,True)
                w._rename_local_node('playlist','A','Renamed')
                with mock.patch('desktop.playlist_folders.QMessageBox.question',return_value=16384):
                    w._delete_local_node('playlist','A')
                self.assertIn('A',w.custom);self.assertNotIn('Renamed',w.custom)
                self.assertTrue(w._flush_settings_preferences())
            finally:release.set()
            for _ in range(400):
                self.settle()
                if not w._state_committer.pending:break
                QTest.qWait(5)
        self.assertFalse(w._state_committer.pending);failed.assert_not_called();completed.assert_called_once()
        saved=StateRepository(w._state_repository.path).load()
        self.assertIn('A',saved['custom']);self.assertNotIn('Renamed',saved['custom'])
        namespace=encoded(source(w.lib));ids=saved['settings'][KEY]['local_ids'][namespace]
        self.assertIn('A',ids);self.assertNotIn('Renamed',ids)
        layout=saved['settings'][KEY]['playlists'][w._browser_columns.scope]
        self.assertTrue(next(hidden for key,_width,hidden in decoded(layout) if key=='key'))

    def test_old_nonmovable_blob_unknown_keys_and_invalid_layouts_preserve_saved_data(self):
        w=self.window;old=QHeaderView(Qt.Horizontal);model=QStandardItemModel(0,8,old);old.setModel(model)
        keys=['future',*[key for key,_label,_hidden in COLUMNS[:7]]]
        old.setSectionsMovable(False);old.resizeSection(4,81);old.hideSection(3)
        old.moveSection(7,1)
        legacy=dict(state=bytes(old.saveState().toBase64()).decode('ascii'),keys=keys)
        old.deleteLater();w.settings['columns_state']=legacy['state'];w.settings['columns_keys']=keys
        w.settings[KEY]={'version':1,'default':{'state':'invalid !','keys':['title']},'playlists':{'untouched':'bad'}}
        before=deepcopy(w.settings[KEY]);w._restore_columns();self.settle()
        self.assertTrue(w.table.horizontalHeader().sectionsMovable());self.assertTrue(w.artists_view.header().isFirstSectionMovable())
        self.assertEqual(self.order()[:2],[6,0]);self.assertTrue(w.table.isColumnHidden(2))
        self.assertEqual(w.table.columnWidth(3),81);self.assertEqual(w.settings[KEY],before)
        w._save_columns();self.assertEqual(w.settings[KEY],before)
        before_order=self.order()
        self.drag(w.table.horizontalHeader(),0,1);self.assertNotEqual(self.order(),before_order)

    def test_first_frame_switch_and_stale_visibility_menu_binding(self):
        w=self.window;w._show_custom('A');w._set_column_hidden(2,True);stale=w._browser_columns.binding()
        with mock.patch.object(w,'_set_refreshed_rows',wraps=w._set_refreshed_rows) as rows:
            def first_frame(data):
                self.assertFalse(w.table.isColumnHidden(2));self.assertEqual(w._browser_columns.view,'sb:B')
                return rows._mock_wraps(data)
            rows.side_effect=first_frame;w._show_custom('B')
        w._set_column_hidden(4,True,expected=stale)
        self.assertFalse(w.table.isColumnHidden(4))

    def test_invalid_saved_unification_does_not_block_local_layouts_or_discard_evidence(self):
        from soundbox import playlist_unification as merge
        w=self.window;saved={'schema':99};w.settings[merge.KEY]=saved
        w._show_custom('B');w._set_column_hidden(2,True)
        self.assertTrue(w.table.isColumnHidden(2))
        self.assertIs(w.settings[merge.KEY],saved)

    def test_same_source_fresh_library_during_real_drag_preserves_requested_order(self):
        w=self.window;w._show_custom('A');w._set_column_hidden(2,True)
        scope=w._browser_columns.scope;before=self.order()
        def refresh():
            w.lib=replace(w.lib,tracks_by_id=dict(w.lib.tracks_by_id))
            w._show_custom('A')
        self.drag(w.table.horizontalHeader(),0,3,during=refresh)
        self.assertNotEqual(self.order(),before)
        self.assertEqual(w._browser_columns.scope,scope)
        saved=w.settings[KEY]['playlists'][scope]
        self.assertEqual([key for key,_width,_hidden in decoded(saved)],
                         [COLUMNS[index][0] for index in self.order()])

    def test_changed_source_or_restore_during_real_drag_cannot_save_into_new_owner(self):
        w=self.window;w._show_custom('A');w._set_column_hidden(2,True)
        w._flush_settings_preferences()
        for _ in range(200):
            self.settle()
            if not w._state_committer.pending:break
            QTest.qWait(5)
        original=w.lib;area=w._state_repository.area;generation=area.generation
        for change in ('source','restore'):
            saved=deepcopy(w.settings[KEY])
            def changed():
                if change=='source':
                    w.lib=replace(original,source=str(self.root/'replacement.xml'));w._show_custom('A')
                else:area.generation+=1
            try:
                with self.subTest(change=change):
                    self.drag(w.table.horizontalHeader(),0,3,during=changed)
                    self.assertEqual(w.settings[KEY],saved)
            finally:
                area.generation=generation;w.lib=original;w._show_custom('A')


if __name__=='__main__':unittest.main()
