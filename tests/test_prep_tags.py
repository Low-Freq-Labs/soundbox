"""Flat tags and captured selection ownership; synthetic state only."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path
from dataclasses import replace
import tempfile
import threading
import time
import unittest
from unittest import mock

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication

from desktop.prep_tags import MyTagsButton
from desktop.prep_grid import BpmValue
from desktop.tag_controller import TagEditController
from soundbox.library import Library, LibraryTag, Playlist, Track


def _library():
    return Library({'1': Track('1','One','A','file:///not/read/one.wav',source_uuid='u1',source_tag_ids=['tag']),
                    '2': Track('2','Two','B','file:///not/read/two.wav',source_uuid='u2',source_tag_ids=[])},
                   [], '/synthetic/master.db', source_kind='rekordbox-db',
                   source_tags=[LibraryTag('tag','Dubstep','Genre','tag-uuid')],
                   source_library_id='library')


class TagInterface(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait_for(self, condition):
        deadline = time.monotonic() + 5
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertTrue(condition())

    def test_bpm_display_is_readonly_and_formats_saved_values(self):
        field = BpmValue()
        self.assertTrue(field.isReadOnly())
        self.assertEqual(field.focusPolicy(), Qt.NoFocus)
        self.assertEqual(field.text(), '000.00')
        for value, expected in [('140', '140.00'), ('90.125', '90.12'),
                                (None, '000.00'), ('', '000.00'),
                                ('nan', '000.00'), ('invalid', '000.00')]:
            field.set_bpm(value)
            self.assertEqual(field.text(), expected)
        field.deleteLater()

    def test_popup_matches_button_with_two_columns_and_keeps_long_labels_readable(self):
        button = MyTagsButton()
        button.setFixedWidth(240)
        tags = [LibraryTag('a', 'Dubstep'), LibraryTag('b', 'Deep Dub')]
        try:
            button.set_state(tags, [set()])
            button.popup.popup(QPoint(100, 100))
            self.app.processEvents()
            short = button.popup.width()
            self.assertEqual(short, button.width())
            boxes = list(button.checkboxes.values())
            self.assertEqual(boxes[0].y(), boxes[1].y())
            self.assertLess(boxes[0].geometry().right(), boxes[1].x())
            self.assertTrue(all(box.height() >= box.fontMetrics().height() + 8 for box in boxes))
            button.set_state(tags + [LibraryTag('c', 'A much longer tag name needing more room')], [set()])
            self.app.processEvents()
            self.assertEqual(button.popup.width(), short)
            self.assertIn("A much longer tag name", button.checkboxes["c"].toolTip())
            self.assertLess(button.checkboxes["a"].geometry().bottom(), button.checkboxes["b"].y())
            button.set_state(tags, [set()], 'Close rekordbox and its background agent before editing tags')
            self.assertEqual(button.popup.width(), short)
            self.assertFalse(button.summary.isVisible())
            self.assertIn('Close rekordbox',button.summary.toolTip())
        finally:
            button.popup.close()
            button.deleteLater()

    def test_mixed_checkbox_checks_all_then_clears_without_closing_the_menu(self):
        button = MyTagsButton()
        emitted=[]
        button.assignment_requested.connect(lambda tag,present: emitted.append((tag,present)))
        button.set_state(_library().source_tags,[{'tag'},set()])
        box=button.checkboxes['tag']
        self.assertEqual(box.checkState(),Qt.PartiallyChecked)
        box.click()
        self.assertEqual(emitted,[('tag',True)])
        box.click()
        self.assertEqual(emitted[-1],('tag',False))
        button.deleteLater()

    def test_popup_reads_down_left_column_then_down_right_in_source_order(self):
        button = MyTagsButton()
        button.setFixedWidth(300)
        tags = [LibraryTag(str(i), name) for i, name in enumerate(
            ['Dubstep', 'Riddim', 'Deep Dub', 'DnB', 'Techno', 'House',
             'Trap', 'Brostep', 'Tearout', 'SONNY', 'ZYKO'])]
        try:
            button.set_state(tags, [set()])
            boxes = list(button.checkboxes.values())
            self.assertEqual([b.x() for b in boxes[:6]], [boxes[0].x()] * 6)
            self.assertEqual([b.y() for b in boxes[:6]], sorted(b.y() for b in boxes[:6]))
            self.assertLess(boxes[0].geometry().right(), boxes[6].x())
            self.assertEqual(boxes[0].y(), boxes[6].y())
            self.assertEqual([b.x() for b in boxes[6:]], [boxes[6].x()] * 5)
            reading_order = sorted(boxes, key=lambda b: (b.x(), b.y()))
            self.assertEqual([b.text() for b in reading_order], [t.name for t in tags])
            self.assertEqual(button.popup.width(), button.width())
        finally:
            button.deleteLater()

    def test_busy_retains_preview_and_disables_further_changes(self):
        button=MyTagsButton()
        button.set_state(_library().source_tags,[{'tag'}],'Saving tags…')
        self.assertEqual(button.checkboxes['tag'].checkState(),Qt.Checked)
        self.assertFalse(button.checkboxes['tag'].isEnabled())
        button.deleteLater()

    def test_save_status_changes_do_not_relayout_an_open_tag_popup(self):
        from unittest import mock
        button=MyTagsButton();button.setFixedWidth(240)
        tags=_library().source_tags
        button.set_state(tags,[{'tag'}]);button.popup.popup(QPoint(100,100));self.app.processEvents()
        geometry=button.popup.geometry();summary=button.summary.geometry();box=button.checkboxes['tag']
        try:
            with mock.patch.object(button.content,'updateGeometry') as changed:
                for reason in ('Updating playlist…','','Confirming cue/grid saves…',''):
                    button.set_state(tags,[{'tag'}],reason)
                    self.app.processEvents()
                    self.assertEqual(button.popup.geometry(),geometry)
                    self.assertEqual(button.summary.geometry(),summary)
                    self.assertIs(button.checkboxes['tag'],box)
                    self.assertTrue(button.popup.isVisible())
                changed.assert_not_called()
        finally:button.popup.close();button.deleteLater()

    def test_membership_tooltips_refresh_without_relayout_or_losing_elided_full_label(self):
        from unittest import mock
        button = MyTagsButton();button.setFixedWidth(240)
        self.addCleanup(button.deleteLater)
        tags = [LibraryTag('short', 'Dubstep'),
                LibraryTag('long', 'A very long tag label that must be shown in its tooltip')]
        button.set_state(tags, [{'short', 'long'}, set()])
        boxes = dict(button.checkboxes)
        self.assertIn('Assigned to 1 of 2', boxes['short'].toolTip())
        self.assertIn('Assigned to 1 of 2', boxes['long'].toolTip())
        with mock.patch.object(button.content, 'updateGeometry') as changed:
            button.set_state(tags, [{'short', 'long'}, {'short', 'long'}])
            changed.assert_not_called()
        self.assertIs(button.checkboxes['short'], boxes['short'])
        self.assertEqual(boxes['short'].toolTip(), '')
        self.assertEqual(boxes['long'].toolTip(), tags[1].name)
        button.set_state(tags, [{'long'}, set(), set()])
        self.assertIn('Assigned to 1 of 3', boxes['long'].toolTip())
        self.assertEqual(boxes['short'].toolTip(), '')

    def test_long_new_names_keep_two_columns_and_header_has_no_overlapping_children(self):
        from desktop import theme
        button = MyTagsButton()
        self.addCleanup(button.deleteLater)
        button.setFixedWidth(191)
        button.popup.setStyleSheet(theme.QSS)
        tags = [LibraryTag(str(i), name) for i, name in enumerate(
            ['Dubstep', 'Riddim', 'Deep Dub', 'DnB', 'Techno', 'House', 'Trap',
             'Brostep', 'Tearout', 'SONNY', 'ZYKO', 'SoundBox Test Tag',
             'SoundBox Verify Tag', 'SoundBox Acceptance Tag'])]
        button.set_state(tags, [], creation_reason='', deletion_reason='')
        button.popup.popup(QPoint(100, 100))
        self.app.processEvents()
        try:
            boxes = list(button.checkboxes.values())
            self.assertEqual(len({box.x() for box in boxes}), 2)
            self.assertEqual(boxes[0].y(), boxes[7].y())
            self.assertEqual(boxes[6].y(), boxes[13].y())
            self.assertLess(button.popup.height(), 300)
            self.assertIn(tags[13].name, boxes[13].toolTip())
            for x in (4, 30, button.create_tag.width() // 2):
                position = button.create_tag.mapTo(button.popup, QPoint(x, 8))
                self.assertIs(button.popup.childAt(position), button.create_tag)
            self.assertFalse(button.summary.isVisible())
            self.assertTrue(button.delete_tag.isEnabled())
            self.assertTrue(all(not box.isEnabled() for box in boxes))
            requested = []
            button.deletion_requested.connect(lambda: requested.append(button.popup.isVisible()))
            button.delete_tag.click()
            self.assertEqual(requested, [False])
        finally:
            button.popup.close()

    def test_request_captures_selected_ids_and_uuid_before_a_selection_change(self):
        with tempfile.TemporaryDirectory() as td:
            entered=threading.Event();release=threading.Event();requests=[]
            class Store:
                def apply(self, request):
                    requests.append(request);entered.set();release.wait(5)
            lib=_library()
            controller=TagEditController(Path(td),store_factory=lambda _lib:Store(),loader=lambda _p:lib)
            controller.configure(lib,True)
            selection=['2']
            self.assertTrue(controller.request(selection,'tag',True))
            selection[:]=['1']
            self.wait_for(entered.is_set)
            self.assertTrue(controller.busy)
            self.assertEqual(controller.selected_tags(['2']),[{'tag'}])
            self.assertFalse(controller.request(['1'],'tag',False))
            self.assertEqual(requests[0].tracks[0].track_id,'2')
            self.assertEqual(requests[0].tracks[0].source_uuid,'u2')
            release.set()
            self.wait_for(lambda:not controller.busy)
            controller.shutdown()

    def test_imported_tracks_unverified_cache_and_shutdown_reject_edits(self):
        with tempfile.TemporaryDirectory() as td:
            controller=TagEditController(Path(td),store_factory=lambda _lib:None)
            lib=_library();controller.configure(lib,False)
            self.assertFalse(controller.request(['1'],'tag',False))
            controller.configure(lib,True);lib.tracks_by_id['2'].imported=True
            self.assertFalse(controller.request(['1','2'],'tag',False))
            controller.shutdown()
            self.assertFalse(controller.request(['1'],'tag',False))

    def test_logical_selection_sends_native_id_and_retains_logical_pending_preview(self):
        from dataclasses import replace
        with tempfile.TemporaryDirectory() as td:
            lib=_library();track=lib.tracks_by_id.pop('2')
            lib.tracks_by_id['sb-stable']=replace(track,track_id='sb-stable',source_native_id='2')
            requests=[];release=threading.Event()
            class Store:
                def apply(self,request):requests.append(request);release.wait(5)
            controller=TagEditController(Path(td),store_factory=lambda _:Store(),loader=lambda _:lib)
            controller.configure(lib,True)
            try:
                self.assertTrue(controller.request(['sb-stable'],'tag',True))
                self.assertEqual(controller.selected_tags(['sb-stable']),[{'tag'}])
                self.wait_for(lambda:bool(requests))
                self.assertEqual(requests[0].tracks[0].track_id,'2')
            finally:
                release.set();controller.shutdown()

    def test_failure_discards_preview_and_does_not_emit_saved(self):
        with tempfile.TemporaryDirectory() as td:
            class Store:
                def apply(self,_request):raise OSError('disk full')
            controller=TagEditController(Path(td),store_factory=lambda _lib:Store())
            controller.configure(_library(),True);saved=[];failed=[]
            controller.saved.connect(saved.append);controller.failed.connect(failed.append)
            self.assertTrue(controller.request(['2'],'tag',True))
            self.wait_for(lambda:not controller.busy)
            self.assertEqual(saved,[]);self.assertEqual(failed,['disk full'])
            self.assertEqual(controller.selected_tags(['2']),[set()])
            controller.shutdown()

    def test_default_tag_readback_skips_media_walk_and_keeps_fresh_smart_membership_and_cues(self):
        with tempfile.TemporaryDirectory() as td:
            previous=_library()
            for track in previous.tracks_by_id.values():
                track.source_native_path=str(track.path)
                track.location_uri=f'file:///canonical/{track.track_id}.wav'
            fresh_tracks={tid:replace(track,location_uri='file://'+track.source_native_path,
                source_native_path='',name='Fresh '+track.name) for tid,track in previous.tracks_by_id.items()}
            cues=[{'position':1234}]
            fresh_tracks['2']=replace(fresh_tracks['2'],source_tag_ids=['tag'],source_tag_times={'tag':42},source_cues=cues)
            smart=Playlist('Smart','',['2'],pid='smart')
            fresh=replace(previous,tracks_by_id=fresh_tracks,playlists=[smart])
            class Store:
                def apply(self,request):
                    self.request=request
            store=Store();saved=[];failed=[]
            def read(_source,*,resolve_media_paths=True):
                if resolve_media_paths:raise AssertionError('Unrelated media path resolution during tag save')
                return fresh
            with mock.patch('soundbox.rekordbox_db.load_live',side_effect=read) as load:
                controller=TagEditController(Path(td),store_factory=lambda _:store)
                controller.configure(previous,True)
                controller.saved.connect(lambda library:controller.configure(library,True))
                controller.saved.connect(saved.append);controller.failed.connect(failed.append)
                try:
                    with mock.patch('desktop.tag_controller.validate_track_path') as selected_guard:
                        self.assertTrue(controller.request(['2'],'tag',True))
                        self.wait_for(lambda:controller.owner.pending==0)
                    load.assert_called_once_with(Path(previous.source),resolve_media_paths=False)
                    selected_guard.assert_called_once_with(previous.tracks_by_id['2'])
                    self.assertEqual(failed,[]);self.assertEqual(saved,[fresh]);self.assertIs(controller.lib,fresh)
                    self.assertIs(fresh.playlists[0],smart);self.assertEqual(smart.keys,['2'])
                    self.assertIs(fresh.tracks_by_id['2'].source_cues,cues)
                    self.assertEqual(fresh.tracks_by_id['2'].source_tag_times,{'tag':42})
                    for tid,track in fresh.tracks_by_id.items():
                        self.assertEqual(track.location_uri,f'file:///canonical/{tid}.wav')
                        self.assertEqual(track.source_native_path,previous.tracks_by_id[tid].source_native_path)
                        self.assertTrue(track.name.startswith('Fresh '))
                    self.assertEqual(previous.tracks_by_id['2'].source_tag_ids,[])
                    self.assertEqual(store.request.tracks[0].source_uuid,'u2')
                finally:controller.shutdown();controller.deleteLater()

    def test_readiness_probe_blocks_editing_when_rekordbox_is_open_and_can_recheck(self):
        with tempfile.TemporaryDirectory() as td:
            blocked=[True]
            class Store:
                def check_ready(self):
                    if blocked[0]:raise RuntimeError('Close rekordbox before editing tags')
            controller=TagEditController(Path(td),store_factory=lambda _lib:Store())
            controller.configure(_library(),True)
            controller.probe()
            self.assertIn('Checking',controller.reason(['1']))
            self.wait_for(lambda:not controller.checking)
            self.assertIn('Close rekordbox',controller.reason(['1']))
            self.assertFalse(controller.request(['1'],'tag',False))
            blocked[0]=False;controller.probe()
            self.wait_for(lambda:not controller.checking)
            self.assertEqual(controller.reason(['1']),'')
            controller.shutdown()
