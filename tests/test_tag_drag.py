"""Tag display-order gestures preserve assignments and exact source/catalog scope."""
from dataclasses import replace
import unittest
from unittest import mock

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from desktop.prep_tags import MyTagsButton
from soundbox.library import LibraryTag


class Drop:
    def __init__(self, source, mime, point):
        self.origin, self.mime, self.point = source, mime, QPointF(point)
        self.accepted = False
    def source(self):return self.origin
    def mimeData(self):return self.mime
    def position(self):return self.point
    def acceptProposedAction(self):self.accepted = True
    def ignore(self):self.accepted = False


class TagDrag(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.button = MyTagsButton(); self.button.setFixedWidth(300)
        self.tags = [LibraryTag(str(i), f'Tag {i}', 'Genre', f'uuid-{i}', 'category', 'category-uuid')
                     for i in range(6)]
        self.context = ('/synthetic/source.db', 'rekordbox-db', 'library')
        self.assignments, self.orders = [], []
        self.button.assignment_requested.connect(lambda *request:self.assignments.append(request))
        self.button.order_requested.connect(lambda *request:self.orders.append(request))
        self.state()
        self.button.popup.popup(QPoint(100, 100)); self.app.processEvents()
        self.addCleanup(self.button.deleteLater); self.addCleanup(self.button.popup.close)

    def state(self, *, enabled=True, tags=None, context=None):
        self.button.set_state(tags if tags is not None else self.tags,
            [{'0'}, set()] if enabled else [], '' if enabled else 'Select songs',
            deletion_context=self.context if context is None else context, deletion_reason='')

    def drop(self, source='0', target='4', *, after=True, mime=None, origin=None):
        rect = self.button.checkboxes[target].geometry()
        point = QPoint(rect.center().x(), rect.bottom() - 1 if after else rect.top() + 1)
        event = Drop(self.button.content if origin is None else origin,
                     self.button._tag_mime(source) if mime is None else mime, point)
        self.button.content.dropEvent(event)
        return event

    def test_cross_column_drop_emits_exact_ids_and_binding_without_assignment(self):
        original = dict(self.button.checkboxes)
        self.assertTrue(self.drop().accepted)
        order, binding = self.orders[0]
        self.assertEqual(order, ('1', '2', '3', '4', '0', '5'))
        self.assertEqual(binding[0], self.context); self.assertEqual(binding, self.button.order_binding())
        self.assertEqual(tuple(self.button.checkboxes), order)
        self.assertTrue(all(self.button.checkboxes[key] is box for key, box in original.items()))
        self.assertEqual(self.button.checkboxes['0'].checkState(), Qt.PartiallyChecked)
        self.assertEqual(self.assignments, [])
        self.assertGreater(self.button.checkboxes['4'].x(), self.button.checkboxes['1'].x())
        self.assertFalse(self.drop('1', '5').accepted); self.assertEqual(len(self.orders), 1)

    def test_label_click_and_indicator_click_each_toggle_once_but_threshold_drag_does_not(self):
        label = self.button._drag_labels['0']
        QTest.mouseClick(label, Qt.LeftButton, pos=label.rect().center())
        self.assertEqual(self.assignments, [('0', True)])
        self.state()
        box = self.button.checkboxes['0']
        QTest.mouseClick(box, Qt.LeftButton, pos=QPoint(8, box.height() // 2))
        self.assertEqual(self.assignments, [('0', True), ('0', True)])
        self.state(enabled=False)
        self.assertFalse(box.isEnabled()); label = self.button._drag_labels['0']
        def execute(source, mime, _label, actions):
            self.assertIs(source, self.button.content); self.assertEqual(actions, Qt.MoveAction)
            self.assertTrue(self.drop(mime=mime).accepted)
        point = label.rect().center()
        with mock.patch('desktop.prep_tags._start_chip_drag', side_effect=execute) as drag:
            QTest.mousePress(label, Qt.LeftButton, pos=point)
            QTest.mouseMove(label, point + QPoint(2, 0)); drag.assert_not_called()
            event = QMouseEvent(QEvent.MouseMove, QPointF(point + QPoint(20, 0)), QPointF(point + QPoint(20, 0)),
                                Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
            QApplication.sendEvent(label, event); drag.assert_called_once()
            QTest.mouseRelease(label, Qt.LeftButton, pos=point)
        self.assertEqual(len(self.assignments), 2); self.assertEqual(len(self.orders), 1)

    def test_source_catalog_uuid_and_layout_revisions_reject_stale_drags(self):
        for change in ('source', 'uuid', 'order'):
            with self.subTest(change=change):
                self.state(); mime = self.button._tag_mime('0')
                if change == 'source':self.state(context=('/other.db', 'rekordbox-db', 'other'))
                elif change == 'uuid':self.state(tags=[replace(self.tags[0], source_uuid='replacement'), *self.tags[1:]])
                else:self.state(tags=list(reversed(self.tags)))
                self.assertFalse(self.drop(mime=mime).accepted)
        self.assertEqual(self.orders, [])

    def test_external_missing_context_duplicate_ids_and_uuids_never_reorder(self):
        self.assertFalse(self.drop(origin=object()).accepted)
        self.button.set_state(self.tags, [set()])
        self.assertFalse(self.drop().accepted)
        self.state(tags=[self.tags[0], replace(self.tags[1], source_uuid=self.tags[0].source_uuid), *self.tags[2:]])
        self.assertFalse(self.drop().accepted)
        self.state(tags=[self.tags[0], replace(self.tags[1], tag_id='0'), *self.tags[2:]])
        self.assertFalse(self.drop().accepted); self.assertEqual(self.orders, [])

    def test_rejected_save_restores_grid_and_stale_refusal_cannot_undo_new_context(self):
        self.drop(); _order, binding = self.orders[-1]
        self.button.restore_order(binding)
        self.assertEqual(tuple(self.button.checkboxes), tuple(tag.tag_id for tag in self.tags))
        self.drop(); self.state(context=('/new.db', 'rekordbox-db', 'new'))
        self.button.restore_order(binding)
        self.assertEqual(self.button.order_binding()[0], ('/new.db', 'rekordbox-db', 'new'))

    def test_successful_display_preference_rebuild_keeps_exact_membership(self):
        self.drop(); order, _binding = self.orders[-1]
        catalog = {tag.tag_id:tag for tag in self.tags}
        self.state(tags=[catalog[key] for key in order])
        self.assertEqual(tuple(self.button.checkboxes), order)
        self.assertEqual(self.button.checkboxes['0'].checkState(), Qt.PartiallyChecked)
        self.assertTrue(self.button._order_available()); self.assertEqual(self.assignments, [])
