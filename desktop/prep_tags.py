"""Flat My Tags checklist; selection and save ownership stay with its caller."""
from __future__ import annotations

from collections import Counter

from PySide6.QtCore import QPoint, QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPolygonF
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QLabel, QPushButton, QToolButton, QWidget,
)
from . import theme


class _TagPanel(QWidget):
    """A modeless tool panel; native QMenu helper widgets can cover its header."""
    aboutToShow = Signal()

    def __init__(self, parent):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint)
        self.setObjectName('MyTagsMenu')

    def popup(self, position):
        self.aboutToShow.emit()
        screen = QApplication.screenAt(position) or self.screen()
        bounds = screen.availableGeometry()
        self.move(max(bounds.left(), min(position.x(), bounds.right() - self.width() + 1)),
                  max(bounds.top(), min(position.y(), bounds.bottom() - self.height() + 1)))
        self.show()
        self.raise_()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(theme.PANEL))
        painter.setPen(QColor(theme.BORDER))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        painter.end()


class MyTagsButton(QToolButton):
    assignment_requested = Signal(str, bool)  # tag ID, desired membership
    creation_requested = Signal()
    deletion_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setText("My Tags")
        self.setObjectName("MyTagsButton")
        self.setFocusPolicy(Qt.NoFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(26)
        self.clicked.connect(self._toggle_popup)
        # A tool panel's transient owner must be a top-level window, including
        # when this button later sits inside the native prep-toolbar child.
        self.popup = _TagPanel(self.window())
        self.content = QWidget(self.popup)
        self.content.setObjectName('MyTagsContent')
        self.summary = QLabel("Select tracks to edit tags", self.content)
        self.summary.setObjectName("MyTagsSummary")
        self.summary.setTextFormat(Qt.PlainText)
        self.summary.setWordWrap(False)
        self.summary.hide()
        self.create_tag = QPushButton('Create New Tag', self.content)
        self.create_tag.setObjectName('MyTagsCreate')
        self.create_tag.setCursor(Qt.PointingHandCursor)
        self.create_tag.clicked.connect(self._create)
        self.delete_tag = QPushButton('Delete Tag…', self.content)
        self.delete_tag.setObjectName('MyTagsDelete')
        self.delete_tag.setCursor(Qt.PointingHandCursor)
        self.delete_tag.clicked.connect(self._delete)
        self._labels = {}
        self._membership_tips = {}
        self.checkboxes: dict[str, QCheckBox] = {}
        self._signature = None
        self._rendering = False
        self._popup_layout=None
        self.set_state([], [], "Select tracks to edit tags")

    def dismiss_outside(self, global_position):
        if (self.popup.isVisible()
                and not self.popup.rect().contains(self.popup.mapFromGlobal(global_position))
                and not self.rect().contains(self.mapFromGlobal(global_position))):
            self.popup.close()

    def _toggle_popup(self):
        if self.popup.isVisible():
            self.popup.close()
        else:
            self.popup.popup(self.mapToGlobal(QPoint(0, self.height())))

    def paintEvent(self, event):
        super().paintEvent(event)
        # The macOS toolbutton indicator ignores the intended compact geometry.
        # Draw a small, centered chevron in the reserved right-hand padding.
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme.TEXT if self.isEnabled() else theme.TEXT_DIM))
        x, y = self.width() - 12, self.height() / 2
        painter.drawPolygon(QPolygonF([QPointF(x - 3, y - 1.5),
                                       QPointF(x + 3, y - 1.5), QPointF(x, y + 2)]))
        painter.end()

    def set_state(self, tags, selected_tags: list[set[str]], reason: str = "", *,
                  creation_reason: str | None = None, deletion_reason: str | None = None):
        """Each selected set is authoritative. An unavailable selection is [] + reason."""
        signature = tuple((tag.tag_id, tag.name, tag.category) for tag in tags)
        if signature != self._signature:
            for box in self.checkboxes.values():
                box.hide()
                box.deleteLater()
            self.checkboxes = {}
            self._labels = {}
            counts = Counter(tag.name for tag in tags)
            for tag in tags:
                name = tag.name if counts[tag.name] == 1 else f"{tag.name} · {tag.category}"
                box = QCheckBox(name, self.content)
                box.setObjectName("MyTagCheck")
                box.setAttribute(Qt.WA_LayoutUsesWidgetRect, True)
                box.setTristate(True)
                # A partial assignment becomes checked, not unchecked, on click.
                box.nextCheckState = lambda b=box: b.setCheckState(
                    Qt.Unchecked if b.checkState() == Qt.Checked else Qt.Checked)
                box.clicked.connect(lambda checked=False, tid=tag.tag_id:
                                    self._request(tid))
                self.checkboxes[tag.tag_id] = box
                self._labels[tag.tag_id] = name
            self._signature = signature
        self._rendering = True
        try:
            self.summary.setText(f"{len(selected_tags)} selected" if selected_tags else 'Select tracks')
            self.summary.setToolTip(reason)
            self.setAccessibleDescription(self.summary.text() + (': ' + reason if reason else ''))
            self.create_tag.setEnabled(creation_reason == '')
            self.create_tag.setToolTip(creation_reason or 'Create a new My Tag')
            self.delete_tag.setEnabled(bool(tags) and deletion_reason == '')
            self.delete_tag.setToolTip(deletion_reason or 'Delete a My Tag from the library')
            for tid, box in self.checkboxes.items():
                count = sum(tid in assigned for assigned in selected_tags)
                state = (Qt.Unchecked if not count else
                         Qt.Checked if count == len(selected_tags) else Qt.PartiallyChecked)
                box.setCheckState(state)
                box.setEnabled(bool(selected_tags) and not reason)
                self._membership_tips[tid] = (f"Assigned to {count} of {len(selected_tags)} selected tracks; "
                                "click to assign to all") if state == Qt.PartiallyChecked else ""
            self.setEnabled(bool(tags) or creation_reason == '')
            self.setToolTip('Create a My Tag, or select songs to assign tags'
                            if creation_reason == '' and not selected_tags
                            else reason or "Assign existing rekordbox tags to selected tracks")
        finally:
            self._rendering = False
        self._size_popup()

    def _size_popup(self):
        signature=(self.width(),self._signature,self.font().toString())
        if self._popup_layout==signature:
            self._sync_membership_tips()
            return
        self._popup_layout=signature
        self.ensurePolished()
        # Keep the compact source-order grid even when a new name is long.
        # Elision and full-name tooltips handle width without a column jump.
        width = max(1, self.width() - 18)
        column_width = max(1, (width - 8) // 2)
        for box in self.checkboxes.values():
            box.setFont(self.font())
            box.ensurePolished()
        rows = (len(self.checkboxes) + 1) // 2
        self.summary.ensurePolished()
        header_height = self.summary.fontMetrics().height()
        self.summary.setGeometry(8, 8, width, header_height)
        self.create_tag.setFont(self.font())
        create_height = max(26, self.create_tag.fontMetrics().height() + 8)
        self.create_tag.setGeometry(8, 8, width, create_height)
        first_y = 8 + create_height + 8
        row_height = max([24] + [box.fontMetrics().height() + 8 for box in self.checkboxes.values()])
        for index, (tid, box) in enumerate(self.checkboxes.items()):
            cell_width = column_width
            label = box.fontMetrics().elidedText(self._labels[tid], Qt.ElideRight, max(1, cell_width - 24))
            box.setText(label)
            box.setGeometry(8 + (index // rows) * (column_width + 8),
                            first_y + (index % rows) * (row_height + 2), cell_width, row_height)
            box.show()
        footer_y = first_y + max(0, rows * (row_height + 2) - 2) + 8
        self.delete_tag.setFont(self.font())
        self.delete_tag.setGeometry(8, footer_y, width, create_height)
        self.content.setGeometry(1, 1, self.width() - 2, footer_y + create_height + 8)
        self.content.updateGeometry()
        self.popup.setFixedSize(self.width(), self.content.height() + 2)
        self._sync_membership_tips()

    def _sync_membership_tips(self):
        for tid, box in self.checkboxes.items():
            tip = self._membership_tips.get(tid, '')
            if box.text() != self._labels[tid]:
                tip = (tip + '\n' if tip else '') + self._labels[tid]
            box.setToolTip(tip)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'delete_tag'):
            self._size_popup()

    def _request(self, tag_id: str):
        if not self._rendering:
            self.assignment_requested.emit(tag_id,
                                            self.checkboxes[tag_id].checkState() == Qt.Checked)

    def _create(self):
        self.popup.close()
        self.creation_requested.emit()

    def _delete(self):
        self.popup.close()
        self.deletion_requested.emit()
