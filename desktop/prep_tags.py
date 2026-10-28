"""Flat My Tags checklist; selection and save ownership stay with its caller."""
from __future__ import annotations

from collections import Counter

from PySide6.QtCore import QPointF, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPolygonF
from PySide6.QtWidgets import (
    QCheckBox, QLabel, QMenu, QStyle, QStyleOption, QToolButton,
    QWidget, QWidgetAction,
)
from . import theme


class MyTagsButton(QToolButton):
    assignment_requested = Signal(str, bool)  # tag ID, desired membership

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setText("My Tags")
        self.setObjectName("MyTagsButton")
        self.setFocusPolicy(Qt.NoFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setPopupMode(QToolButton.InstantPopup)
        self.setFixedHeight(26)
        self.popup = QMenu(self)
        self.popup.setObjectName("MyTagsMenu")
        self.setMenu(self.popup)
        self.content = QWidget()
        self.summary = QLabel("Select tracks to edit tags", self.content)
        self.summary.setObjectName("MyTagsSummary")
        self.summary.setTextFormat(Qt.PlainText)
        self.summary.setWordWrap(True)
        self._labels = {}
        self._membership_tips = {}
        self.checkboxes: dict[str, QCheckBox] = {}
        self._signature = None
        self._rendering = False
        action = QWidgetAction(self.popup)
        action.setDefaultWidget(self.content)
        self.popup.addAction(action)
        self._content_action = action
        self.set_state([], [], "Select tracks to edit tags")

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

    def set_state(self, tags, selected_tags: list[set[str]], reason: str = ""):
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
            self.summary.setText(reason or f"{len(selected_tags)} selected")
            for tid, box in self.checkboxes.items():
                count = sum(tid in assigned for assigned in selected_tags)
                state = (Qt.Unchecked if not count else
                         Qt.Checked if count == len(selected_tags) else Qt.PartiallyChecked)
                box.setCheckState(state)
                box.setEnabled(bool(selected_tags) and not reason)
                self._membership_tips[tid] = (f"Assigned to {count} of {len(selected_tags)} selected tracks; "
                                "click to assign to all") if state == Qt.PartiallyChecked else ""
            self.setEnabled(bool(tags))
            self.setToolTip(reason or "Assign existing rekordbox tags to selected tracks")
        finally:
            self._rendering = False
        self._size_popup()

    def _size_popup(self):
        self.ensurePolished()
        option = QStyleOption()
        option.initFrom(self.popup)
        metric = lambda name: self.popup.style().pixelMetric(name, option, self.popup)
        border = metric(QStyle.PM_MenuPanelWidth)
        padding = QSize(2 * (border + metric(QStyle.PM_MenuHMargin)),
                        2 * (border + metric(QStyle.PM_MenuVMargin)))
        width = max(1, self.width() - padding.width() - 16)
        column_width = max(1, (width - 8) // 2)
        widest = 0
        for tid, box in self.checkboxes.items():
            box.setFont(self.font())
            box.setText(self._labels[tid])
            box.ensurePolished()
            widest = max(widest, box.sizeHint().width())
        columns = 2 if widest <= column_width else 1
        rows = (len(self.checkboxes) + columns - 1) // columns
        self.summary.ensurePolished()
        header_height = max(self.summary.fontMetrics().height(),
            self.summary.fontMetrics().boundingRect(
                QRect(0, 0, width, 10000), Qt.TextWordWrap, self.summary.text()).height())
        self.summary.setGeometry(8, 8, width, header_height)
        first_y = 8 + header_height + 6
        row_height = max([24] + [box.fontMetrics().height() + 8 for box in self.checkboxes.values()])
        for index, (tid, box) in enumerate(self.checkboxes.items()):
            cell_width = column_width if columns == 2 else width
            label = box.fontMetrics().elidedText(self._labels[tid], Qt.ElideRight, max(1, cell_width - 24))
            box.setText(label)
            tip = self._membership_tips.get(tid, '')
            if label != self._labels[tid]:
                tip = (tip + '\n' if tip else '') + self._labels[tid]
            box.setToolTip(tip)
            box.setGeometry(8 + (index // rows) * (column_width + 8),
                            first_y + (index % rows) * (row_height + 2), cell_width, row_height)
            box.show()
        height = first_y + max(0, rows * (row_height + 2) - 2) + 10
        self.content.setFixedSize(self.width() - padding.width(), height)
        self.content.updateGeometry()
        self._content_action.changed.emit()
        self.popup.setFixedSize(QSize(self.width(), self.content.height() + padding.height()))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, '_content_action'):
            self._size_popup()

    def _request(self, tag_id: str):
        if not self._rendering:
            self.assignment_requested.emit(tag_id,
                                            self.checkboxes[tag_id].checkState() == Qt.Checked)
