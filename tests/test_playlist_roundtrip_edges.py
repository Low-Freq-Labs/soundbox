"""Selected native occurrences and queued sibling order on isolated copies only."""
from copy import deepcopy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from test_playlist_transaction import fixture
from desktop.rekordbox_playlists import NativePlaylistsMixin
from soundbox import playlist_presentation, rekordbox_cue_store as cues
from soundbox import rekordbox_playlist_store as store, rekordbox_playlist_plan as planner
from soundbox.library import Library, LibraryTag, Playlist, Track
from soundbox.rekordbox_tags import NATIVE_VERSION, _schema
from soundbox.rekordbox_write import _connect, ConflictError, WriteProofError


class NativeOccurrenceRemoval(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        source = self.root / 'source'; source.mkdir(); fixture(source)
        self.database = source / 'master.db'
        conn = _connect(self.database, readonly=True)
        try:schema = _schema(conn)
        finally:conn.close()
        for name, value in (('_closed', None), ('_installed_version', NATIVE_VERSION)):
            self.enterContext(mock.patch.object(cues, name, return_value=value))
        authority = store.PlaylistAuthority(str(self.database), 'synthetic-playlists', schema, True)
        self.writer = store.PlaylistStore(authority, self.root / 'recovery')
        self.a = SimpleNamespace(track_id='track', source_uuid='uuid')
        self.b = SimpleNamespace(track_id='track-two', source_uuid='uuid-two')
        self.writer.apply(store.request_for(self.writer.snapshot(), 'append', identity='1000',
                                          tracks=[self.a, self.b, self.a], allow_duplicate=True))

    def members(self):
        return sorted(self.writer.snapshot()['members'], key=lambda row: row['TrackNo'])

    def test_selected_later_duplicate_roundtrips_without_removing_first_occurrence(self):
        before = self.members(); selected = before[-1]
        request = store.request_for(self.writer.snapshot(), 'remove', identity='1000',
                                    tracks=[self.a], memberships=[selected])
        self.assertEqual(request['tracks'][0]['membership_id'], selected['ID'])
        self.writer.apply(request)
        remaining = self.members()
        self.assertEqual([row['ID'] for row in remaining], [row['ID'] for row in before[:2]])
        self.assertEqual([row['ContentID'] for row in remaining], ['track', 'track-two'])
        exact = self.database.read_bytes(), self.writer.xml.read_bytes()
        self.writer.apply(request)
        self.assertEqual((self.database.read_bytes(), self.writer.xml.read_bytes()), exact)

    def test_stale_selected_occurrence_cannot_remove_another_duplicate_after_retry(self):
        selected = self.members()[-1]
        request = store.request_for(self.writer.snapshot(), 'remove', identity='1000',
                                   tracks=[self.a], memberships=[selected])
        self.writer.apply(request)
        renewed = {**request, 'operation_id': 'a' * 32, 'revision': self.writer.snapshot()['revision']}
        before = self.database.read_bytes(), self.writer.xml.read_bytes()
        with self.assertRaises(ConflictError):self.writer.apply(renewed)
        self.assertEqual((self.database.read_bytes(), self.writer.xml.read_bytes()), before)
        self.assertEqual([row['ContentID'] for row in self.members()], ['track', 'track-two'])

    def host(self):
        class Host(NativePlaylistsMixin):pass
        host = Host(); host.settings = {}; host._flash_status = mock.Mock()
        tracks = {'alias-a': Track('alias-a', 'A', 'Artist', 'file:///unavailable/a.wav',
                                  source_native_id='track', source_uuid='uuid'),
                  'alias-b': Track('alias-b', 'B', 'Artist', 'file:///unavailable/b.wav',
                                  source_native_id='track-two', source_uuid='uuid-two')}
        host.lib = Library(tracks, [Playlist('Existing', [], ['alias-a', 'alias-b', 'alias-a'], '1000')],
            self.database, source_kind='rekordbox-db', source_library_id='synthetic-playlists',
            source_playlist_catalog=self.writer.snapshot())
        host.playlist_controller = SimpleNamespace(pending=[], queue_error=lambda: '', reason=lambda: '',
                                                   enqueue=mock.Mock(return_value=True))
        return host

    def test_display_duplicates_get_independent_pins_and_stale_menu_rejects_before_queue(self):
        host = self.host(); cached_a = dict(id='alias-a', title='A'); cached_b = dict(id='alias-b', title='B')
        rows = [cached_a, cached_a, cached_b]
        projected = host._native_membership_rows(host.lib.playlists[0], rows)
        self.assertIsNot(projected[0], projected[1]); self.assertNotIn('_native_membership', cached_a)
        first, _, second = self.members()
        self.assertEqual(projected[0]['_native_membership']['ID'], first['ID'])
        self.assertEqual(projected[1]['_native_membership']['ID'], second['ID'])
        captured = host._native_capture_remove('Existing', ['alias-a'], [projected[1]])
        self.assertTrue(host._native_submit_remove(captured))
        saved = host.playlist_controller.enqueue.call_args.args[0]
        self.assertEqual(saved['tracks'][0]['membership_id'], second['ID'])
        host.playlist_controller.enqueue.reset_mock()
        host.lib.source_playlist_catalog['members'] = [row for row in host.lib.source_playlist_catalog['members']
                                                       if row['ID'] != second['ID']]
        self.assertFalse(host._native_submit_remove(captured))
        host.playlist_controller.enqueue.assert_not_called()

    def test_projection_removes_selected_duplicate_and_preserves_source_catalog(self):
        host = self.host(); before = deepcopy(host.lib.source_playlist_catalog)
        selected = self.members()[-1]
        request = store.request_for(before, 'remove', identity='1000', tracks=[self.a], memberships=[selected])
        jobs = [dict(source=str(host.lib.source), library_id=host.lib.source_library_id, error='', request=request)]
        projected = playlist_presentation.project(host.lib, jobs)
        self.assertEqual(projected.playlists[0].keys, ['alias-a', 'alias-b'])
        self.assertEqual(host.lib.source_playlist_catalog, before)
        for bad in ({**selected, 'UUID': '00000000-0000-4000-8000-000000000999'},
                    {**selected, 'PlaylistID': '999'}):
            with self.subTest(bad=bad), self.assertRaises(WriteProofError):
                store.request_for(before, 'remove', identity='1000', tracks=[self.a], memberships=[bad])


class QueuedDeleteSiblingOrder(unittest.TestCase):
    def test_regular_delete_compacts_siblings_before_next_smart_creation_capture(self):
        def node(identity, seq, attribute, parent='root'):
            return dict(ID=str(identity), UUID=f'00000000-0000-4000-8000-{identity:012d}', Seq=seq,
                Name=str(identity), ParentID=parent, Attribute=attribute, SmartList=None,
                rb_data_status=0, rb_local_data_status=0, rb_local_deleted=0, rb_local_synced=0,
                usn=None, rb_local_usn=1)
        nodes = [node(100, 1, 1), node(200, 1, 0, '100'), node(300, 2, 4, '100')]
        nodes[-1]['SmartList'] = planner.smart_tag_rule('300', ['20'])
        catalog = dict(nodes=nodes, members=[]); catalog['revision'] = planner.revision(nodes, [])
        library = Library({}, [Playlist('200', ['100'], [], '200'), Playlist('300', ['100'], [], '300')],
                          '/synthetic/master.db', source_kind='rekordbox-db', source_library_id='library',
                          source_playlist_catalog=catalog)
        request = store.request_for(catalog, 'delete', identity='200')
        projected = playlist_presentation.project(library,
            [dict(source=str(library.source), library_id='library', error='', request=request)])
        self.assertEqual(next(row['Seq'] for row in projected.source_playlist_catalog['nodes'] if row['ID']=='300'), 1)
        tags = [LibraryTag('20', 'Tag', 'Category', '00000000-0000-4000-8000-000000000020',
                           '10', '00000000-0000-4000-8000-000000000010')]
        smart = store.request_for(projected.source_playlist_catalog, 'create_smart', parent='100', name='Next', tags=tags)
        self.assertEqual(set(smart['smart_clocks']['sibling_timestamps']), {'300'})
        self.assertEqual(nodes[-1]['Seq'], 2)


class NativeOccurrenceContextMenu(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def window(self):
        from PySide6.QtCore import QStandardPaths
        from desktop.app import MainWindow
        from soundbox.providers import Providers
        self.enterContext(mock.patch.object(QStandardPaths, 'isTestModeEnabled', return_value=True))
        self.enterContext(mock.patch.object(MainWindow, '_refresh_devices'))
        self.enterContext(mock.patch('soundbox.rekordbox_db.load_live', side_effect=AssertionError('live source')))
        root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        win = MainWindow(providers=Providers('public'), isolated_dir=root)
        self.addCleanup(win.deleteLater); self.addCleanup(win.close); self.addCleanup(win.shutdown)
        node = dict(ID='1000', UUID='00000000-0000-4000-8000-000000001000', Name='List', ParentID='root',
                    Seq=1, Attribute=0, rb_local_deleted=0)
        members = [dict(ID=str(index), UUID=f'00000000-0000-4000-8000-{index:012d}', PlaylistID='1000',
                        ContentID=content, TrackNo=index, rb_local_deleted=0)
                   for index, content in enumerate(('42', '43', '42', '44'), 1)]
        catalog = dict(nodes=[node], members=members); catalog['revision'] = planner.revision([node], members)
        tracks = {key: Track(key, key.upper(), 'Artist', f'file:///unavailable/{key}.wav',
                            source_native_id=native, source_uuid='uuid-' + key, added_ts=1)
                  for key, native in (('a','42'),('b','43'),('c','44'))}
        win.lib = Library(tracks, [Playlist('List', [], ['a', 'b', 'a', 'c'], '1000')], '/synthetic/master.db',
            source_kind='rekordbox-db', source_library_id='library', source_playlist_catalog=catalog)
        controller = SimpleNamespace(pending=[], queue_error=lambda: '', reason=lambda: '',
                                     enqueue=mock.Mock(return_value=True), stop=mock.Mock())
        win.playlist_controller = controller; win._active_native_playlist_id = '1000'; win._view_key = 'rb:List'
        rows = win._native_membership_rows(win.lib.playlists[0],
            [dict(id=key, title=key.upper(), artist='Artist', bpm=bpm) for key,bpm in
             (('a','100'),('b','200'),('a','100'),('c','300'))])
        win.model.set_rows(rows); win.show(); self.app.processEvents()
        return win, rows

    def select(self, win, membership):
        from PySide6.QtCore import QItemSelectionModel
        index = next(index for index,row in enumerate(win.model._rows)
                     if row['_native_membership']['ID'] == membership)
        win.table.selectionModel().setCurrentIndex(win.model.index(index,0),
            QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)

    def selected(self, win):
        return [win.model._rows[index.row()]['_native_membership']['ID']
                for index in win.table.selectionModel().selectedRows()]

    def test_right_click_later_duplicate_captures_clicked_row_outside_existing_selection(self):
        win, rows = self.window(); win.model.set_rows([rows[0],rows[2],rows[1],rows[3]])
        self.select(win,'1')
        def choose(menu, _position):
            action = next(action for action in menu.actions() if action.text() == 'Remove from Playlist')
            self.assertTrue(action.isEnabled()); action.trigger()
        with mock.patch('desktop.app.exec_transient_menu', side_effect=choose):
            win._table_context(win.table.visualRect(win.model.index(1, 0)).center())
        request = win.playlist_controller.enqueue.call_args.args[0]
        self.assertEqual(request['tracks'][0]['membership_id'], '3')
        self.assertEqual(len(request['tracks']), 1)
        self.assertEqual([row['ID'] for row in win.lib.source_playlist_catalog['members']], ['1','2','3','4'])

    def test_refresh_sort_filter_and_analysis_reset_preserve_only_selected_occurrence(self):
        from PySide6.QtCore import Qt
        from desktop.model import COLUMNS
        win, rows = self.window(); win.model.set_filter('Artist')
        bpm = next(index for index,column in enumerate(COLUMNS) if column[0] == 'bpm')
        win.model.sort(bpm, Qt.AscendingOrder); self.select(win,'3')
        seat = win._browser_seat()
        with mock.patch.object(win,'_schedule_artists_refresh'), mock.patch.object(win,'_row_for',
                return_value=dict(bpm='350',key='',bpm_estimated=False)):
            win._refresh_analysis_row('a')
        self.assertEqual(self.selected(win),['3'])
        self.assertEqual(win._browser_seat()['current'],seat['current'])
        win._update_browser_bpm('a','450')
        self.assertEqual(self.selected(win),['3'])
        updated = deepcopy(win.model._all_rows)
        with mock.patch.object(win,'_render_current_view', side_effect=lambda:win.model.set_rows(updated)):
            win._reload_current_view()
        self.assertEqual(self.selected(win),['3'])
        self.assertNotIn('_native_membership',win._row_cache.get('a',{}))
        self.assertEqual([row['ID'] for row in win.lib.source_playlist_catalog['members']], ['1','2','3','4'])

    def test_tag_and_created_library_publication_preserve_one_duplicate_occurrence(self):
        win, rows=self.window()
        def refreshed(library,*,prepared_native_ids):
            self.assertEqual(prepared_native_ids,())
            win.lib=library;win.model.set_rows(deepcopy(rows))
        for name in ('_tag_saved','_publish_created_library'):
            win.model.set_rows(deepcopy(rows));self.select(win,'3')
            with self.subTest(publisher=name), mock.patch.object(win,'_on_refresh_done',side_effect=refreshed), \
                    mock.patch.object(win,'_finish_tag_refresh'),mock.patch.object(win,'_finish_edit_status'):
                getattr(win,name)(win.lib)
                self.assertEqual(self.selected(win),['3'])

    def test_old_membership_seat_cannot_select_same_track_in_another_source(self):
        win, rows = self.window(); self.select(win,'3'); seat=win._browser_seat()
        other=deepcopy(rows)
        for row in other:row['_native_membership']['library_id']='different-library'
        win.model.set_rows(other); win._restore_browser_seat(seat)
        self.assertEqual(self.selected(win),[])
