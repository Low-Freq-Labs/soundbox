"""Sidebar trees, track table and drag/drop chrome.

Mime protocol: each dragged line is "<kind>\t<value>"; kinds: rb (rekordbox
display_path), cust (custom playlist name), trk (comma-joined track ids).
"""

from __future__ import annotations

from PySide6.QtCore import QMimeData, QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QDrag, QFontMetrics, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QStyledItemDelegate, QTableView,
    QTreeWidget, QTreeWidgetItem,
)

from . import theme

PATH_ROLE = Qt.UserRole + 1     # rekordbox playlist display_path
DEVPATH_ROLE = Qt.UserRole + 2  # device mount path
CUSTOM_ROLE = Qt.UserRole + 3   # custom (SoundBox) playlist name
COLLECTION_ROLE = Qt.UserRole + 6  # the built-in "Collection" all-tracks view
ARTISTS_ROLE = Qt.UserRole + 7     # the built-in "Artists" folder (expandable)
ARTIST_ROLE = Qt.UserRole + 8      # one artist entry under the Artists folder


class CellEditDelegate(QStyledItemDelegate):
    """Opens the cell editor pre-filled with the existing text, cursor at the end
    (no select-all) — so you can tweak a word instead of retyping."""

    def setEditorData(self, editor, index):
        text = index.data(Qt.EditRole)
        if hasattr(editor, "setText"):
            # Track-table title/artist editors stop AT the device budget, so a
            # user can't type a name longer than what the export can use.
            from .model import COLUMNS, TrackModel, _EDIT_MAX
            if (isinstance(index.model(), TrackModel)
                    and hasattr(editor, "setMaxLength")
                    and index.column() < len(COLUMNS)):
                editor.setMaxLength(_EDIT_MAX.get(COLUMNS[index.column()][0], 32767))
            editor.setText("" if text is None else str(text))
            editor.deselect()
            editor.end(False)
        else:
            super().setEditorData(editor, index)



def _descendant_rb_paths(item: QTreeWidgetItem) -> list[str]:
    p = item.data(0, PATH_ROLE)
    if p:
        return [p]
    out: list[str] = []
    for i in range(item.childCount()):
        out += _descendant_rb_paths(item.child(i))
    return out


# ---- drag/drop mime: each line is "<kind>\t<value>"; kinds: rb, cust, trk ----

def _chip_pixmap(text: str) -> QPixmap:
    """Drag ghost as a tidy 'chip' — the label on a dark rounded box with a purple
    border — instead of Qt's default transparent floating text."""
    if len(text) > 42:
        text = text[:41] + "…"
    font = QApplication.font()
    fm = QFontMetrics(font)
    pad_x, pad_y = 12, 6
    w = fm.horizontalAdvance(text) + pad_x * 2
    h = fm.height() + pad_y * 2
    dpr = 2
    pm = QPixmap(int(w * dpr), int(h * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(theme.PANEL2))
    p.setPen(QPen(QColor(theme.PURPLE_LIGHT), 1.5))
    p.drawRoundedRect(QRectF(0.75, 0.75, w - 1.5, h - 1.5), 6, 6)
    p.setPen(QColor(theme.TEXT_HI))
    p.setFont(font)
    p.drawText(QRectF(0, 0, w, h), Qt.AlignCenter, text)
    p.end()
    return pm


def _start_chip_drag(view, md, label: str, actions) -> None:
    """Begin a drag from `view` carrying `md`, using the chip pixmap as the ghost."""
    if md is None or not md.hasText():
        return
    drag = QDrag(view)
    drag.setMimeData(md)
    pm = _chip_pixmap(label)
    drag.setPixmap(pm)
    drag.setHotSpot(QPoint(14, int(pm.height() / pm.devicePixelRatio() / 2)))
    drag.exec(actions)


class PlaylistTree(QTreeWidget):
    """Rekordbox playlist tree — drag source (rb)."""
    def mimeData(self, items):  # type: ignore[override]
        paths: list[str] = []
        for it in items:
            paths += _descendant_rb_paths(it)
        md = QMimeData()
        md.setText("\n".join(f"rb\t{p}" for p in dict.fromkeys(paths)))
        return md

    def startDrag(self, actions):  # type: ignore[override]
        items = self.selectedItems()
        if not items:
            return
        label = items[0].text(0) + (f"   +{len(items) - 1}" if len(items) > 1 else "")
        _start_chip_drag(self, self.mimeData(items), label, actions)


class SoundBoxTree(QTreeWidget):
    """Custom playlists — drag source (cust) and drop target for tracks (trk)."""
    tracks_dropped = Signal(str, list)  # custom name, [track_ids]

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDropIndicatorShown(False)
        # The built-in Artists folder expands into per-artist rows, so branch
        # decoration is on; indentation kept slim so drop-hover rows still read
        # nearly full-width.
        self.setRootIsDecorated(True)
        self.setIndentation(12)
        self._hover = None

    def mimeData(self, items):  # type: ignore[override]
        md = QMimeData()
        names = [it.data(0, CUSTOM_ROLE) for it in items if it.data(0, CUSTOM_ROLE)]
        md.setText("\n".join(f"cust\t{n}" for n in names))
        return md

    def startDrag(self, actions):  # type: ignore[override]
        items = [it for it in self.selectedItems() if it.data(0, CUSTOM_ROLE)]
        if not items:
            return
        label = items[0].text(0) + (f"   +{len(items) - 1}" if len(items) > 1 else "")
        _start_chip_drag(self, self.mimeData(items), label, actions)

    def _set_hover(self, item):
        if item is self._hover:
            return
        if self._hover is not None:
            self._hover.setBackground(0, QBrush())
        self._hover = item
        if item is not None:
            item.setBackground(0, QColor(theme.PURPLE))  # glow the playlist target

    def dragEnterEvent(self, e):
        e.acceptProposedAction() if e.mimeData().hasText() else e.ignore()

    def dragMoveEvent(self, e):
        # Only tracks can be dropped onto a SoundBox playlist; glow the target.
        if "trk\t" not in e.mimeData().text():
            self._set_hover(None)
            e.ignore()
            return
        item = self.itemAt(e.position().toPoint())
        self._set_hover(item if item and item.data(0, CUSTOM_ROLE) else None)
        e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self._set_hover(None)
        super().dragLeaveEvent(e)

    def dropEvent(self, e):
        item = self.itemAt(e.position().toPoint())
        name = item.data(0, CUSTOM_ROLE) if item else None
        self._set_hover(None)
        ids: list[str] = []
        for line in e.mimeData().text().split("\n"):
            kind, _, val = line.partition("\t")
            if kind == "trk":
                ids += [i for i in val.split(",") if i]
        if name and ids:
            self.tracks_dropped.emit(name, ids)
            e.acceptProposedAction()
        else:
            e.ignore()


class DeviceTree(QTreeWidget):
    """External devices — drop target (rb/cust). Dropping starts an export."""
    dropped = Signal(str, list)  # device_path, [(kind, value)]

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setDragEnabled(False)
        self.setDropIndicatorShown(False)
        # Flat list — kill the reserved indent so the purple drop-hover fills the full
        # row width (was starting inset past a phantom branch column).
        self.setRootIsDecorated(False)
        self.setIndentation(0)
        self._hover = None

    def _set_hover(self, item):
        if item is self._hover:
            return
        if self._hover is not None:
            self._hover.setBackground(0, QBrush())  # clear
        self._hover = item
        if item is not None:
            item.setBackground(0, QColor(theme.PURPLE))  # glow the drop target

    def dragEnterEvent(self, e):
        e.acceptProposedAction() if e.mimeData().hasText() else e.ignore()

    def dragMoveEvent(self, e):
        if not e.mimeData().hasText():
            e.ignore()
            return
        item = self.itemAt(e.position().toPoint())
        while item and item.parent():
            item = item.parent()
        self._set_hover(item)
        e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self._set_hover(None)
        super().dragLeaveEvent(e)

    def dropEvent(self, e):
        item = self.itemAt(e.position().toPoint())
        while item and item.parent():
            item = item.parent()
        dev = item.data(0, DEVPATH_ROLE) if item else None
        self._set_hover(None)
        items = []
        for line in e.mimeData().text().split("\n"):
            kind, _, val = line.partition("\t")
            if kind in ("rb", "cust") and val:
                items.append((kind, val))
        if dev and items:
            self.dropped.emit(dev, items)
            e.acceptProposedAction()
        else:
            e.ignore()


class TrackTable(QTableView):
    """Track table — drag source (trk). Uses the chip drag ghost (the track title on
    a bordered box) instead of Qt's default transparent floating text."""
    def startDrag(self, actions):  # type: ignore[override]
        m = self.model()
        sm = self.selectionModel()
        sel = sm.selectedRows() if sm else []
        if not m or not sel:
            return
        label = str(m.data(sel[0]) or "track")  # title column (col 0)
        if len(sel) > 1:
            label += f"   +{len(sel) - 1}"
        _start_chip_drag(self, m.mimeData(sel), label, actions)

