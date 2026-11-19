"""Native tag/smart acknowledgements preserve prepared browsing and fresh state."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

from PySide6.QtCore import QStandardPaths, Qt
from PySide6.QtWidgets import QApplication

from desktop.app import MainWindow
from desktop.rekordbox_playlists import PlaylistEditController, QUEUE as PLAYLIST_QUEUE
from desktop.tag_controller import TagEditController
from desktop.tag_creation import TagCreationMixin
from desktop.tag_creation_controller import TagDefinitionController, QUEUE as TAG_QUEUE
from soundbox import rekordbox_tag_definitions as definitions
from soundbox.library import Library, LibraryTag, Playlist, Track
from soundbox.providers import Providers
from tests import test_tag_creation_controller as fixtures


class DefaultNativeReadback(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        root=Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.window=fixtures.Window(root)
        # The default controller delegates to its owned process service. Keep
        # this publication fixture's reader observation injectable underneath
        # that boundary; real encrypted process reads have their own suite.
        from soundbox import rekordbox_db
        self.window.native_reader=SimpleNamespace(read_metadata=lambda previous:rekordbox_db.load_metadata_readback(previous))
        self.addCleanup(self.window.deleteLater)
        self.tags=TagEditController(root,self.window)
        self.window.tag_controller=self.tags
        self.addCleanup(self.tags.shutdown)
        self.tags.configure(self.window.lib,True)
        self.tags._recover_pending=mock.Mock()
        self.enterContext(mock.patch('desktop.tag_creation_controller.assert_rekordbox_closed'))
        self.enterContext(mock.patch('desktop.rekordbox_playlists.assert_rekordbox_closed'))
        def run(_kind,work,finished):
            finished(SimpleNamespace(error='',result=work()))
            return True
        self.tags._run_source_job=run

    def test_default_reader_is_complete_metadata_readback_and_injected_reader_keeps_path_interface(self):
        previous=self.window.lib
        fresh=replace(previous,source_tags=[LibraryTag('42','Fresh')])
        self.assertIsNone(self.tags._loader)
        with mock.patch('desktop.tag_controller.rekordbox_db.load_metadata_readback',return_value=fresh) as read:
            self.assertIs(self.tags.readback_library(previous),fresh)
        read.assert_called_once_with(previous)
        injected=mock.Mock(return_value=fresh)
        self.tags._loader=injected
        with mock.patch('desktop.tag_controller.rekordbox_db.load_metadata_readback',side_effect=AssertionError('unexpected reader')):
            self.assertIs(self.tags.readback_library(previous),fresh)
        injected.assert_called_once_with(Path(previous.source))

    def test_definition_create_and_delete_acknowledge_using_the_production_default_reader(self):
        for deleting in (False,True):
            with self.subTest(deleting=deleting):
                request=definitions.request_for('library','1','1','Saved tag')
                tag=LibraryTag(request['tag_id'],'Saved tag','Genre',request['tag_uuid'],'1','1')
                previous=replace(self.window.lib,source_tags=[tag] if deleting else [])
                self.window.lib=previous
                self.tags.configure(previous,True)
                fresh=replace(previous,source_tags=[] if deleting else [tag])
                if deleting:
                    request=definitions.deletion_request_for('library','1','1',tag.tag_id,tag.source_uuid,tag.name)
                apply=mock.Mock(return_value={'ID':tag.tag_id})
                controller=TagDefinitionController(self.window,
                    store_factory=lambda _:SimpleNamespace(apply=apply),
                    deletion_store_factory=lambda _:SimpleNamespace(apply=apply))
                self.addCleanup(controller.stop)
                controller.schedule=mock.Mock()
                self.assertIsNone(controller.loader)
                with mock.patch('desktop.tag_controller.rekordbox_db.load_metadata_readback',return_value=fresh) as read:
                    self.assertTrue(controller.enqueue(request))
                    controller.pump()
                apply.assert_called_once_with(request)
                read.assert_called_once_with(previous)
                self.assertEqual(self.window.saved[-1][TAG_QUEUE],[])
                self.assertIs(self.window.published[-1],fresh)

    def test_smart_creation_uses_production_default_reader_before_exact_acknowledgement(self):
        fresh,catalog,request=fixtures.SmartReadback().setup_readback()
        previous=replace(fresh,playlists=[],source_playlist_catalog=dict(nodes=[],members=[],revision='a'*64))
        self.window.lib=previous
        self.tags.configure(previous,True)
        self.window._native_pending_changed=mock.Mock()
        self.tags.playlist_update_status=''
        apply=mock.Mock(return_value=catalog)
        store=SimpleNamespace(apply=apply,committed_transition=lambda _:(request['revision'],catalog['revision']))
        controller=PlaylistEditController(self.window,store_factory=lambda _:store)
        self.addCleanup(controller.stop)
        controller.schedule=mock.Mock()
        with mock.patch('desktop.tag_controller.rekordbox_db.load_metadata_readback',return_value=fresh) as read:
            self.assertTrue(controller.enqueue(request))
            controller.pump()
        apply.assert_called_once_with(request)
        read.assert_called_once_with(previous)
        self.assertEqual(self.window.saved[-1][PLAYLIST_QUEUE],[])
        self.assertIs(self.window.published[-1],fresh)


class PreparedPublication(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.enterContext(mock.patch.object(QStandardPaths,'isTestModeEnabled',return_value=True))
        self.enterContext(mock.patch.object(MainWindow,'_refresh_devices'))
        self.enterContext(mock.patch('soundbox.rekordbox_db.load_live',side_effect=AssertionError('live source')))
        root=Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.window=MainWindow(providers=Providers('public'),isolated_dir=root)
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.shutdown)
        self.window.lib=Library({str(i):Track(str(i),'Song '+str(i),'Artist',f'file:///missing/{i}.wav',
            bpm='140',source_uuid=f'00000000-0000-4000-8000-{i:012d}',source_tag_ids=[])
            for i in (1,2,3)},[Playlist('Smart',[],['1'],'99')],
            '/synthetic/master.db',source_kind='rekordbox-db',source_library_id='library',
            source_playlist_catalog=dict(nodes=[dict(ID='99',UUID='smart',Name='Smart',ParentID='root',
                Seq=1,Attribute=4,rb_local_deleted=0,SmartList='<unchanged/>')],members=[],revision='a'*64))
        self.window._rekordbox_source_verified=True
        self.window._apply_loaded_library()
        self.window._show_collection()
        self.window.table.selectRow(1)
        self.app.processEvents()

    def test_unassigned_tag_and_smart_membership_publish_without_preparation_or_collection_reset(self):
        window=self.window
        old_rows=window.model._rows
        selected=window.model._rows[window.table.currentIndex().row()]['id']
        fresh=replace(window.lib,source_tags=[LibraryTag('42','Fresh tag')],
            playlists=[Playlist('Smart',[],['1','2'],'99')])
        reset=mock.Mock()
        window.model.modelReset.connect(reset)
        with (mock.patch.object(window,'_refresh_devices',side_effect=AssertionError('device restart')),
              mock.patch.object(window,'_resume_analysis_jobs',side_effect=AssertionError('prep restart')),
              mock.patch.object(window,'_arm_waveform_rechecks',side_effect=AssertionError('waveform restart')),
              mock.patch.object(window,'_schedule_stock_artwork',side_effect=AssertionError('art restart')),
              mock.patch.object(window.art_cache,'configure_default_tracks',side_effect=AssertionError('default art restart')),
              mock.patch.object(window.graveyard_controller,'prepare_library',side_effect=AssertionError('graveyard restart')),
              mock.patch.object(window,'_restore_browser_seat',wraps=window._restore_browser_seat) as restore):
            window._publish_created_library(fresh)
        self.assertEqual(window.lib.source_tags[0].name,'Fresh tag')
        self.assertEqual(window.lib.tracks_by_id['2'].source_tag_ids,[])
        self.assertEqual(window.lib.playlists[0].keys,['1','2'])
        self.assertIs(window.model._rows,old_rows)
        self.assertEqual(window.model._rows[window.table.currentIndex().row()]['id'],selected)
        self.assertTrue(any('Smart' in item.text(0) and '(2)' in item.text(0)
            for item in window.tree.findItems('',Qt.MatchContains|Qt.MatchRecursive)))
        restore.assert_not_called()
        reset.assert_not_called()

    def test_assigned_tag_refreshes_visible_row_and_retains_selected_song(self):
        window=self.window
        selected=window.model._rows[window.table.currentIndex().row()]['id']
        fresh=replace(window.lib,tracks_by_id={**window.lib.tracks_by_id,
            '2':replace(window.lib.tracks_by_id['2'],source_tag_ids=['42'])},
            source_tags=[LibraryTag('42','Fresh tag')],playlists=[Playlist('Smart',[],['1','2'],'99')])
        window._publish_created_library(fresh)
        row=next(row for row in window.model._rows if row['id']=='2')
        self.assertEqual(row['myTags'],'Fresh tag')
        self.assertEqual(window.model._rows[window.table.currentIndex().row()]['id'],selected)
        self.assertEqual(window.lib.playlists[0].keys,['1','2'])

    def test_wrapper_preserves_a_changed_browser_owner_and_skips_inactive_browser_restore(self):
        for active,changed in ((True,False),(True,True),(False,True)):
            with self.subTest(active=active,changed=changed):
                model=SimpleNamespace(_rows=[])
                stack=SimpleNamespace(currentIndex=lambda:0 if active else 1)
                host=SimpleNamespace(model=model,stack=stack,_browser_seat=mock.Mock(return_value='seat'),
                    _restore_browser_seat=mock.Mock(),_finish_tag_refresh=mock.Mock(),_finish_edit_status=mock.Mock())
                def publish(_library,**kwargs):
                    self.assertEqual(kwargs,dict(prepared_native_ids=('song',)))
                    if changed:model._rows=[]
                host._on_refresh_done=publish
                TagCreationMixin._publish_created_library(host,object(),changed_ids=('song',))
                self.assertEqual(host._restore_browser_seat.call_count,int(active and changed))
