"""Flat My Tags checklist; selection and save ownership stay with its caller."""
from __future__ import annotations

from collections import Counter
import json

from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPolygonF
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QLabel, QPushButton, QToolButton, QWidget,
)
from . import theme
from .choices import Menu as QMenu, exec_transient_menu
from .trees import _start_chip_drag


class _TagContent(QWidget):
    def __init__(self, owner):
        super().__init__(owner.popup)
        self.owner = owner
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        event.acceptProposedAction() if self.owner._tag_drag(event) is not None else event.ignore()

    def dragMoveEvent(self, event):
        destination = self.owner._tag_drop_target(event)
        self.owner._insert_line = destination[2] if destination is not None else None
        self.update()
        event.acceptProposedAction() if destination is not None else event.ignore()

    def dragLeaveEvent(self, event):
        self.owner._insert_line = None;self.update();event.accept()

    def dropEvent(self, event):
        self.owner._drop_tag(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        line = self.owner._insert_line
        if line is not None:
            painter = QPainter(self);painter.setPen(QColor(theme.PURPLE))
            painter.drawLine(line[0], line[1], line[2], line[1]);painter.end()


class _TagDragLabel(QWidget):
    """Keep labels draggable while checkbox assignment itself is unavailable."""
    def __init__(self, owner, tag_id):
        super().__init__(owner.content)
        self.owner, self.tag_id = owner, tag_id
        self._press = None
        self.setCursor(Qt.OpenHandCursor)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda point:
            owner._tag_context_menu(self.mapTo(owner.content, point)))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._press = event.position().toPoint(), self.owner.order_binding()
            event.accept();return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (self._press is not None and event.buttons() & Qt.LeftButton
                and (event.position().toPoint() - self._press[0]).manhattanLength()
                    >= QApplication.startDragDistance()):
            _position, binding = self._press;self._press = None
            if self.owner.order_binding() == binding and self.owner._order_available():
                try:
                    _start_chip_drag(self.owner.content, self.owner._tag_mime(self.tag_id),
                                     self.owner._labels[self.tag_id], Qt.MoveAction)
                finally:
                    self.owner._insert_line = None;self.owner.content.update()
            event.accept();return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        press, self._press = self._press, None
        if event.button() == Qt.LeftButton and press is not None:
            if self.rect().contains(event.position().toPoint()) and self.owner.order_binding() == press[1]:
                box = self.owner.checkboxes.get(self.tag_id)
                if box is not None and box.isEnabled():box.click()
            event.accept();return
        super().mouseReleaseEvent(event)


class _TagPanel(QWidget):
    """A modeless tool panel; native QMenu helper widgets can cover its header."""
    aboutToShow = Signal()

    def __init__(self, parent):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint)
        self.setObjectName('MyTagsMenu')

    def popup(self, position):
        self.aboutToShow.emit()
        # The hidden tool window can still have its initial native geometry.
        # Derive every opening from the prepared content, without retaining
        # native min/max constraints from the empty startup panel.
        self.resize(self.sizeHint())
        screen = QApplication.screenAt(position) or self.screen()
        bounds = screen.availableGeometry()
        self.move(max(bounds.left(), min(position.x(), bounds.right() - self.width() + 1)),
                  max(bounds.top(), min(position.y(), bounds.bottom() - self.height() + 1)))
        self.show()
        self.raise_()

    def sizeHint(self):
        return self.childrenRect().size() + QSize(2, 2)

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
    rename_requested = Signal(object, object)  # exact tag, captured source scope
    deletion_requested = Signal(object, object)  # exact tag, captured source scope
    order_requested = Signal(object, object)  # exact ID order, captured source/catalog binding

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
        self._insert_line = None
        self.content = _TagContent(self)
        self.content.setObjectName('MyTagsContent')
        self.content.setContextMenuPolicy(Qt.CustomContextMenu)
        self.content.customContextMenuRequested.connect(self._tag_context_menu)
        self.summary = QLabel("Select tracks to edit tags", self.content)
        self.summary.setObjectName("MyTagsSummary")
        self.summary.setTextFormat(Qt.PlainText)
        self.summary.setWordWrap(False)
        self.summary.hide()
        self.create_tag = QPushButton('Create New Tag', self.content)
        self.create_tag.setObjectName('MyTagsCreate')
        self.create_tag.setCursor(Qt.PointingHandCursor)
        self.create_tag.clicked.connect(self._create)
        self._context_menu = None
        self._definitions = {}
        self._deletion_reason = None
        self._deletion_context = None
        self._rename_reason = None
        self._rename_context = None
        self._pending_creations = set()
        self._labels = {}
        self._membership_tips = {}
        self.checkboxes: dict[str, QCheckBox] = {}
        self._drag_labels = {}
        self._order_context = None
        self._order_identity = None
        self._order_revision = 0
        self._order_original = None
        self._signature = None
        self._rendering = False
        self._popup_layout=None
        self.set_state([], [], "Select tracks to edit tags")

    def dismiss_outside(self, global_position):
        if (self._context_menu is not None and self._context_menu.isVisible()
                and self._context_menu.rect().contains(self._context_menu.mapFromGlobal(global_position))):
            return
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
                  creation_reason: str | None = None, deletion_reason: str | None = None,
                  deletion_context=None, order_context=None, order_reason='',
                  rename_reason: str | None = None, rename_context=None, assignment_reasons=None,
                  pending_creations=()):
        """Each selected set is authoritative. An unavailable selection is [] + reason."""
        signature = tuple((tag.tag_id, tag.name, tag.category) for tag in tags)
        context = deletion_context if order_context is None else order_context
        identity = (context, tuple((tag.tag_id, tag.source_uuid, tag.category_id, tag.category_uuid,
                                   tag.name, tag.category) for tag in tags))
        if identity != self._order_identity:
            self._order_revision += 1
            self._order_identity = identity
            self._order_original = None
        self._order_context = context
        self._order_reason = order_reason
        self._definitions = {tag.tag_id: tag for tag in tags}
        self._deletion_reason = deletion_reason
        self._deletion_context = deletion_context
        self._rename_reason = rename_reason
        self._rename_context = rename_context
        self._pending_creations = set(pending_creations)
        if signature != self._signature:
            for box in self.checkboxes.values():
                box.hide()
                box.deleteLater()
            for label in self._drag_labels.values():label.hide();label.deleteLater()
            self.checkboxes = {}
            self._drag_labels = {}
            self._labels = {}
            counts = Counter(tag.name for tag in tags)
            for tag in tags:
                name = tag.name if counts[tag.name] == 1 else f"{tag.name} · {tag.category}"
                box = QCheckBox(name.replace('&', '&&'), self.content)
                box.setAccessibleName(name)
                box.installEventFilter(self)
                box.setObjectName("MyTagCheck")
                box.setAttribute(Qt.WA_LayoutUsesWidgetRect, True)
                box.setTristate(True)
                # A partial assignment becomes checked, not unchecked, on click.
                box.nextCheckState = lambda b=box: b.setCheckState(
                    Qt.Unchecked if b.checkState() == Qt.Checked else Qt.Checked)
                box.clicked.connect(lambda checked=False, tid=tag.tag_id:
                                    self._request(tid))
                self.checkboxes[tag.tag_id] = box
                self._drag_labels[tag.tag_id] = _TagDragLabel(self, tag.tag_id)
                self._labels[tag.tag_id] = name
            self._signature = signature
        self._rendering = True
        try:
            self.summary.setText(f"{len(selected_tags)} selected" if selected_tags else 'Select tracks')
            self.summary.setToolTip(reason)
            self.setAccessibleDescription(self.summary.text() + (': ' + reason if reason else ''))
            self.create_tag.setEnabled(creation_reason == '')
            self.create_tag.setToolTip(creation_reason or 'Create a new My Tag')
            for tid, box in self.checkboxes.items():
                count = sum(tid in assigned for assigned in selected_tags)
                state = (Qt.Unchecked if not count else
                         Qt.Checked if count == len(selected_tags) else Qt.PartiallyChecked)
                box.setCheckState(state)
                assignment_reason = (assignment_reasons or {}).get(tid, '')
                box.setEnabled(bool(selected_tags) and not reason and not assignment_reason)
                self._membership_tips[tid] = assignment_reason or ((f"Assigned to {count} of {len(selected_tags)} selected tracks; "
                                "click to assign to all") if state == Qt.PartiallyChecked else "")
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
            # Button text treats '&' as a mnemonic marker. Elide the literal
            # source name first, then escape only its presentation for Qt.
            box.setText(label.replace('&', '&&'))
            box.setGeometry(8 + (index // rows) * (column_width + 8),
                            first_y + (index % rows) * (row_height + 2), cell_width, row_height)
            box.show()
            handle = self._drag_labels[tid]
            handle.setGeometry(box.geometry().adjusted(24, 0, 0, 0))
            handle.setToolTip(box.toolTip())
            handle.show();handle.raise_()
        bottom = first_y + max(0, rows * (row_height + 2) - 2) + 8
        self.content.setGeometry(1, 1, self.width() - 2, bottom)
        self.content.updateGeometry()
        self.popup.resize(self.popup.sizeHint())
        self._sync_membership_tips()

    def _sync_membership_tips(self):
        for tid, box in self.checkboxes.items():
            tip = self._membership_tips.get(tid, '')
            if box.text() != self._labels[tid].replace('&', '&&'):
                tip = (tip + '\n' if tip else '') + self._labels[tid]
            box.setToolTip(tip)
            self._drag_labels[tid].setToolTip('\n'.join(filter(None,(tip,self._order_reason))))

    def order_binding(self):
        return self._order_context, self._order_identity, self._order_revision

    def _order_available(self):
        definitions = self._definitions
        return bool(not self._order_reason and self._order_context is not None and len(definitions) > 1
            and self._order_original is None
            and len(definitions) == len(self._order_identity[1])
            and len({tag.source_uuid for tag in definitions.values()}) == len(definitions)
            and all(tag.tag_id and tag.source_uuid for tag in definitions.values()))

    def _tag_mime(self, tag_id):
        mime = QMimeData()
        mime.setData('application/x-soundbox-tag-order-v1', json.dumps(dict(
            identity=tag_id, binding=self.order_binding(), order=list(self.checkboxes))).encode())
        return mime

    def _tag_drag(self, event):
        if event.source() is not self.content or not self._order_available():return None
        try:
            raw = bytes(event.mimeData().data('application/x-soundbox-tag-order-v1'))
            if len(raw) > 64 * 1024:return None
            captured = json.loads(raw)
            if (captured['binding'] != json.loads(json.dumps(self.order_binding()))
                    or captured['order'] != list(self.checkboxes) or captured['identity'] not in self.checkboxes):return None
            return captured['identity']
        except (ValueError, TypeError, KeyError):return None

    def _tag_drop_target(self, event):
        identity = self._tag_drag(event)
        if identity is None:return None
        point = event.position().toPoint()
        target = next((tid for tid, box in self.checkboxes.items() if box.geometry().contains(point)), None)
        if target is None or target == identity:return None
        rect = self.checkboxes[target].geometry()
        after = point.y() > rect.center().y()
        return target, after, (rect.left() + 24, rect.bottom() if after else rect.top(), rect.right())

    def _drop_tag(self, event):
        target = self._tag_drop_target(event)
        self._insert_line = None;self.content.update()
        if target is None:event.ignore();return
        identity = self._tag_drag(event);anchor, after, _line = target
        previous = tuple(self.checkboxes)
        order = [tid for tid in previous if tid != identity]
        order.insert(order.index(anchor) + int(after), identity)
        if tuple(order) == previous:event.ignore();return
        self._order_revision += 1
        binding = self.order_binding()
        self._order_original = binding, previous
        self.checkboxes = {tid: self.checkboxes[tid] for tid in order}
        self._popup_layout = None;self._size_popup()
        self.order_requested.emit(tuple(order), binding)
        event.acceptProposedAction()

    def restore_order(self, binding):
        if self._order_original is None or self._order_original[0] != binding or self.order_binding() != binding:return
        previous = self._order_original[1]
        self.checkboxes = {tid: self.checkboxes[tid] for tid in previous}
        self._order_original = None
        self._order_revision += 1
        self._popup_layout = None;self._size_popup()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, '_popup_layout'):
            self._size_popup()

    def _request(self, tag_id: str):
        if not self._rendering:
            self.assignment_requested.emit(tag_id,
                                            self.checkboxes[tag_id].checkState() == Qt.Checked)

    def _create(self):
        self.popup.close()
        self.creation_requested.emit()

    def _tag_context_menu(self, position):
        tag_id = next((tid for tid, box in self.checkboxes.items()
                       if box.geometry().contains(position)), None)
        tag = self._definitions.get(tag_id)
        if tag is None:
            return
        context = self._deletion_context
        menu = QMenu(self.popup)
        rename_context = self._rename_context
        creating = 'Finish creating this tag before changing its definition' if tag_id in self._pending_creations else ''
        rename = menu.addAction('Rename Tag…')
        rename.setEnabled(not creating and self._rename_reason == '' and rename_context is not None)
        rename.setToolTip(creating or self._rename_reason or '')
        def rename_requested():
            if (tag_id in self._pending_creations or self._rename_reason != '' or rename_context != self._rename_context
                    or self._definitions.get(tag_id) != tag):
                return
            self.popup.close()
            self.rename_requested.emit(tag, rename_context)
        rename.triggered.connect(rename_requested)
        action = menu.addAction('Delete Tag…')
        action.setEnabled(not creating and self._deletion_reason == '' and context is not None)
        action.setToolTip(creating or self._deletion_reason or '')
        def requested():
            if (tag_id in self._pending_creations or self._deletion_reason != '' or context != self._deletion_context
                    or self._definitions.get(tag_id) != tag):
                return
            self.popup.close()
            self.deletion_requested.emit(tag, context)
        action.triggered.connect(requested)
        self._context_menu = menu
        try:
            exec_transient_menu(menu, self.content.mapToGlobal(position))
        finally:
            self._context_menu = None

    def eventFilter(self, watched, event):
        # Disabled assignment controls still allow definition actions.
        if event.type() == QEvent.ContextMenu and watched in self.checkboxes.values():
            self._tag_context_menu(self.content.mapFromGlobal(event.globalPos()))
            return True
        return super().eventFilter(watched, event)
