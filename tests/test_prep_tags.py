"""Flat tags and captured selection ownership; synthetic state only."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path
import tempfile
import threading
import time
import unittest

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication

from desktop.prep_tags import MyTagsButton
from desktop.prep_grid import BpmValue
from desktop.tag_controller import TagEditController
from soundbox.library import Library, LibraryTag, Track


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
            self.assertGreater(button.summary.height(), button.summary.fontMetrics().height())
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
