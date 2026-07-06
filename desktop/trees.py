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


class NoTypeAhead:
    """Typing a letter must NEVER teleport the selection (Qt's built-in
    type-ahead) — filtering is the search box's job, and a stray keypress
    losing your place in a 1000-track list is infuriating. Mixed into every
    browser view. Shortcuts (Space, ⌘R…) are unaffected: they resolve before
    the key ever reaches the view."""

    def keyboardSearch(self, _search: str) -> None:
        pass


class ArtistsTree(NoTypeAhead, QTreeWidget):
    """The Artists browser page (artist folders → song rows)."""


class PlaylistTree(NoTypeAhead, QTreeWidget):
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


def _audio_paths(md) -> list[str]:
    """Local audio files in a Finder drag (empty for anything else)."""
    from pathlib import Path
    from soundbox.imports import is_audio
    if not md.hasUrls():
        return []
    return [u.toLocalFile() for u in md.urls()
            if u.isLocalFile() and is_audio(Path(u.toLocalFile()))]


class SoundBoxTree(NoTypeAhead, QTreeWidget):
    """Custom playlists — drag source (cust), drop target for tracks (trk) and
    Finder audio files (import), and drag-to-reorder within itself (cust
    dropped back on the list)."""
    tracks_dropped = Signal(str, list)  # custom name, [track_ids]
    playlist_moved = Signal(str, int)   # custom name, target index in the custom order
    files_dropped = Signal(str, list)   # custom name ("" = Collection), [file paths]

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDropIndicatorShown(False)
        # Flat list — no expand arrows. Removing the reserved indent makes the row
        # (and its purple drop-hover) fill the FULL width from the left edge, instead
        # of the highlight starting inset past a phantom branch column.
        self.setRootIsDecorated(False)
        self.setIndentation(0)
        self._hover = None
        self._insert_row: int | None = None  # reorder line position (top-level row)

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

    def _first_custom_row(self) -> int:
        """Row of the first custom playlist — the pinned Collection/Artists rows
        above it are not reorderable, so drops clamp below them."""
        for i in range(self.topLevelItemCount()):
            if self.topLevelItem(i).data(0, CUSTOM_ROLE):
                return i
        return self.topLevelItemCount()

    def _drop_row(self, pos) -> int:
        """Top-level row the dragged playlist would be inserted at (above/below
        the hovered row by cursor half), clamped below the pinned rows."""
        item = self.itemAt(pos)
        if item is None:
            row = self.topLevelItemCount()
        else:
            row = self.indexOfTopLevelItem(item)
            if pos.y() > self.visualItemRect(item).center().y():
                row += 1
        return max(row, self._first_custom_row())

    def _set_insert(self, row: int | None):
        if row != self._insert_row:
            self._insert_row = row
            self.viewport().update()

    def dragEnterEvent(self, e):
        md = e.mimeData()
        if md.hasText() or _audio_paths(md):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        # Tracks drop ONTO a playlist (glow it); a playlist dragged over its own
        # list reorders (purple insert line); Finder audio files drop onto a
        # playlist OR Collection (import). Anything else is ignored.
        if _audio_paths(e.mimeData()):
            item = self.itemAt(e.position().toPoint())
            ok = item is not None and (item.data(0, CUSTOM_ROLE)
                                       or item.data(0, COLLECTION_ROLE))
            self._set_hover(item if ok else None)
            self._set_insert(None)
            e.acceptProposedAction() if ok else e.ignore()
            return
        text = e.mimeData().text()
        if "trk\t" in text:
            item = self.itemAt(e.position().toPoint())
            self._set_hover(item if item and item.data(0, CUSTOM_ROLE) else None)
            self._set_insert(None)
            e.acceptProposedAction()
        elif "cust\t" in text and e.source() is self:
            self._set_hover(None)
            self._set_insert(self._drop_row(e.position().toPoint()))
            e.acceptProposedAction()
        else:
            self._set_hover(None)
            self._set_insert(None)
            e.ignore()

    def dragLeaveEvent(self, e):
        self._set_hover(None)
        self._set_insert(None)
        super().dragLeaveEvent(e)

    def dropEvent(self, e):
        item = self.itemAt(e.position().toPoint())
        name = item.data(0, CUSTOM_ROLE) if item else None
        self._set_hover(None)
        self._set_insert(None)
        files = _audio_paths(e.mimeData())
        if files:
            if name:
                self.files_dropped.emit(name, files)          # import + add
                e.acceptProposedAction()
            elif item is not None and item.data(0, COLLECTION_ROLE):
                self.files_dropped.emit("", files)            # import only
                e.acceptProposedAction()
            else:
                e.ignore()
            return
        ids: list[str] = []
        custs: list[str] = []
        for line in e.mimeData().text().split("\n"):
            kind, _, val = line.partition("\t")
            if kind == "trk":
                ids += [i for i in val.split(",") if i]
            elif kind == "cust" and val:
                custs.append(val)
        if name and ids:
            self.tracks_dropped.emit(name, ids)
            e.acceptProposedAction()
        elif custs and e.source() is self:
            # Reorder: index within the custom order = rows above the insert
            # line that are custom playlists.
            row = self._drop_row(e.position().toPoint())
            self.playlist_moved.emit(custs[0], row - self._first_custom_row())
            e.acceptProposedAction()
        else:
            e.ignore()

    def paintEvent(self, e):
        super().paintEvent(e)
        if self._insert_row is None or not self.topLevelItemCount():
            return
        if self._insert_row < self.topLevelItemCount():
            y = self.visualItemRect(self.topLevelItem(self._insert_row)).top()
        else:
            y = self.visualItemRect(
                self.topLevelItem(self.topLevelItemCount() - 1)).bottom()
        y = max(1, min(y, self.viewport().height() - 1))
        p = QPainter(self.viewport())
        p.setPen(QPen(QColor(theme.PURPLE_LIGHT), 2))
        p.drawLine(0, y, self.viewport().width(), y)
        p.end()


class DeviceTree(NoTypeAhead, QTreeWidget):
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


class TrackTable(NoTypeAhead, QTableView):
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

