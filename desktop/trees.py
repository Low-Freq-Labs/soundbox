"""Sidebar trees, track table and drag/drop chrome.

Mime protocol: each dragged line is "<kind>\t<value>"; kinds: rb (rekordbox
display_path), cust (custom playlist name), trk (comma-joined track ids).
"""

from __future__ import annotations

from PySide6.QtCore import QMimeData, QModelIndex, QPersistentModelIndex, QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QDrag, QFontMetrics, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QStyledItemDelegate, QTableView,
    QTreeWidget, QTreeWidgetItem, QStyle, QStyleOptionViewItem,
)

from . import theme

PATH_ROLE = Qt.UserRole + 1     # rekordbox playlist display_path
DEVPATH_ROLE = Qt.UserRole + 2  # device mount path
CUSTOM_ROLE = Qt.UserRole + 3   # custom (SoundBox) playlist name
COLLECTION_ROLE = Qt.UserRole + 6  # the built-in "Collection" all-tracks view
ARTISTS_ROLE = Qt.UserRole + 7     # the built-in "Artists" folder (expandable)
ARTIST_ROLE = Qt.UserRole + 8      # one artist entry under the Artists folder
ARTIST_TRACK_ROLE = Qt.UserRole + 9  # a track row inside the Artists browser (its id)
ARTIST_IDS_ROLE = Qt.UserRole + 10   # an artist row's full track-id list (for drag)
CUSTOM_FOLDER_ROLE = Qt.UserRole + 11  # local folder UUID, independent of playlist names


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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # QTableView enables the macOS text-input client even when its edit
        # triggers are disabled.  Cocoa then treats a held letter as text and
        # opens the accent-character picker before Qt's repeat KeyPress events
        # reach the application filter.  Browser views do not accept text, so
        # keep their native input-method client disabled.  A delegate-created
        # QLineEdit is a separate widget and retains full IME support.
        self.setAttribute(Qt.WA_InputMethodEnabled, False)

    def keyboardSearch(self, _search: str) -> None:
        pass

    def currentChanged(self, current, previous) -> None:
        # QAbstractItemView re-enables its input-method attribute whenever an
        # editable model index becomes current, even when NoEditTriggers is in
        # force. Reassert the browser contract after Qt updates the selection;
        # explicit delegate editors still own their own text-input client.
        super().currentChanged(current, previous)
        self.setAttribute(Qt.WA_InputMethodEnabled, False)


class BrowserDetailTree(NoTypeAhead, QTreeWidget):
    """Device trees share the browser's 24-point grid through empty space."""

    def paintEvent(self, event):
        super().paintEvent(event)
        # QTreeView alternates populated rows only. Continue from its last
        # visible row without adding fake model entries or selectable blanks.
        viewport = self.viewport()
        index = self.indexAt(QPoint(1, 1))
        y = 0
        row = self.verticalScrollBar().value()
        while index.isValid():
            rect = self.visualRect(index)
            y = rect.bottom() + 1
            if y >= viewport.height():
                return
            row += 1
            index = self.indexBelow(index)
        painter = QPainter(viewport)
        painter.setClipRegion(event.region())
        while y < viewport.height():
            painter.fillRect(0, y, viewport.width(), 24,
                             QColor(theme.ROW_ALT if row % 2 else theme.ROW))
            y += 24
            row += 1


class GraveyardTree(NoTypeAhead, QTreeWidget):
    """Read-only audition selection; native text entry must not claim held C."""


class ArtistsTree(NoTypeAhead, QTreeWidget):
    """The Artists browser page (artist folders → song rows). Drag source: a
    track row carries its own id; an artist row carries ALL its tracks — drop
    either onto a SoundBox playlist to add them (same `trk` mime the track table
    uses, so the existing SoundBoxTree drop handling works unchanged)."""

    @staticmethod
    def _ids(items) -> list[str]:
        ids: list[str] = []
        for it in items:
            tid = it.data(0, ARTIST_TRACK_ROLE)
            if tid:
                ids.append(tid)
            else:  # an artist header row → all of that artist's tracks
                ids += it.data(0, ARTIST_IDS_ROLE) or []
        return list(dict.fromkeys(ids))  # de-dupe, preserve order

    def mimeData(self, items):  # type: ignore[override]
        md = QMimeData()
        ids = self._ids(items)
        if ids:
            md.setText("trk\t" + ",".join(ids))
        return md

    def startDrag(self, actions):  # type: ignore[override]
        items = self.selectedItems()
        ids = self._ids(items)
        if not ids:
            return
        label = items[0].text(0) + (f"   +{len(ids) - 1}" if len(ids) > 1 else "")
        _start_chip_drag(self, self.mimeData(items), label, actions)


class _PlaylistDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        option = QStyleOptionViewItem(option)
        self.initStyleOption(option, index)
        # Fill the original row before insetting its contents, so selected
        # nested leaves have no gap between the branch and label backgrounds.
        option.widget.style().drawPrimitive(
            QStyle.PE_PanelItemViewItem, option, painter, option.widget)
        option.rect.adjust(6, 0, 0, 0)
        # A leaf's icon occupies the branch column, just as a folder's chevron
        # does. Keep the model text/roles and the tree's hit/drag geometry intact.
        if any(index.data(role) for role in (PATH_ROLE, CUSTOM_ROLE, COLLECTION_ROLE, ARTISTS_ROLE)):
            self.initStyleOption(option, index)
            icon, separator, text = option.text.partition('  ')
            if separator:
                option.text = text
                style = option.widget.style()
                style.drawControl(QStyle.CE_ItemViewItem, option, painter, option.widget)
                painter.save()
                selected = bool(option.state & QStyle.State_Selected)
                painter.setPen(QColor(theme.ON_ACCENT if selected else theme.TEXT))
                branch = option.rect.adjusted(-self.parent().indentation(), 0,
                    -option.rect.width(), 0)
                painter.drawText(branch, Qt.AlignCenter, icon)
                painter.restore()
                return
        super().paint(painter, option, index)


class PlaylistFolderTree(NoTypeAhead, QTreeWidget):
    """Folders navigate without selecting a playlist; pressed folders still drag."""
    def __init__(self):
        super().__init__()
        self.setExpandsOnDoubleClick(False)
        self.setIndentation(16)
        self.setItemDelegate(_PlaylistDelegate(self))

    def drawBranches(self, painter, rect, index):
        painter.save()
        painter.translate(6, 0)
        super().drawBranches(painter, rect, index)
        painter.restore()

    @staticmethod
    def is_folder(item):
        return item is not None and not any(item.data(0, role) for role in
            (PATH_ROLE, CUSTOM_ROLE, COLLECTION_ROLE, ARTISTS_ROLE))

    def mousePressEvent(self, event):
        item = self.itemAt(event.position().toPoint())
        self._folder_press = (QPersistentModelIndex(self.indexFromItem(item))
            if event.button() == Qt.LeftButton and self.is_folder(item) else None)
        self._folder_position = event.position().toPoint()
        self._folder_expanded = item.isExpanded() if self._folder_press else False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        press = getattr(self, '_folder_press', None)
        if (press is not None and press.isValid() and event.buttons() & Qt.LeftButton
                and (event.position().toPoint()-self._folder_position).manhattanLength() >= QApplication.startDragDistance()):
            self.startDrag(Qt.CopyAction | Qt.MoveAction); return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        press = getattr(self, '_folder_press', None)
        self._folder_press = None
        super().mouseReleaseEvent(event)
        if press is not None and press.isValid() and self.indexAt(event.position().toPoint()) == press:
            item = self.itemFromIndex(QModelIndex(press))
            if item.isExpanded() == self._folder_expanded:item.setExpanded(not self._folder_expanded)

    def _playlist_drag_items(self):
        press = getattr(self, '_folder_press', None)
        self._folder_press = None
        if press is not None and press.isValid():return [self.itemFromIndex(QModelIndex(press))]
        return self.selectedItems()


class PlaylistTree(PlaylistFolderTree):
    """Rekordbox playlist tree — drag source (rb)."""
    def mimeData(self, items):  # type: ignore[override]
        paths: list[str] = []
        for it in items:
            paths += _descendant_rb_paths(it)
        md = QMimeData()
        md.setText("\n".join(f"rb\t{p}" for p in dict.fromkeys(paths)))
        return md

    def startDrag(self, actions):  # type: ignore[override]
        items = self._playlist_drag_items()
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


class SoundBoxTree(PlaylistFolderTree):
    """Custom playlists — drag source (cust), drop target for tracks (trk) and
    Finder audio files (import), and drag-to-reorder within itself (cust
    dropped back on the list)."""
    tracks_dropped = Signal(str, list)  # custom name, [track_ids]
    playlist_moved = Signal(str, int)   # custom name, target index in the custom order
    node_moved = Signal(str, str, int)  # playlist:name/folder:UUID, parent UUID, sibling index
    files_dropped = Signal(str, list)   # custom name ("" = Collection), [file paths]

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDropIndicatorShown(False)
        self.setRootIsDecorated(True)
        self.setIndentation(16)
        self._hover = None
        self._insert_row: int | None = None  # reorder line position (top-level row)
        self._insert_y = None

    @staticmethod
    def _drag_line(it) -> str:
        """Mime line for a draggable row: custom playlists by name, the pinned
        Collection/Artists views as synthetic 'sbview' playlists (drag one onto
        a device = export the whole pool in that view's order)."""
        if it.data(0, CUSTOM_ROLE):
            return f"cust\t{it.data(0, CUSTOM_ROLE)}"
        if it.data(0, CUSTOM_FOLDER_ROLE):
            return f"sbfolder\t{it.data(0, CUSTOM_FOLDER_ROLE)}"
        if it.data(0, COLLECTION_ROLE):
            return "sbview\tcollection"
        if it.data(0, ARTISTS_ROLE):
            return "sbview\tartists"
        return ""

    def mimeData(self, items):  # type: ignore[override]
        md = QMimeData()
        lines = []
        def collect(item):
            line = self._drag_line(item)
            if line:lines.append(line)
            if item.data(0, CUSTOM_FOLDER_ROLE):
                for index in range(item.childCount()):collect(item.child(index))
        for item in items:collect(item)
        md.setText("\n".join(dict.fromkeys(lines)))
        return md

    def startDrag(self, actions):  # type: ignore[override]
        items = [it for it in self._playlist_drag_items() if self._drag_line(it)]
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
            if self.topLevelItem(i).data(0, CUSTOM_ROLE) or self.topLevelItem(i).data(0, CUSTOM_FOLDER_ROLE):
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
        cleared = row is None and self._insert_y is not None
        if row is None:self._insert_y = None
        if row != self._insert_row or cleared:
            self._insert_row = row
            self.viewport().update()

    def _drop_destination(self, pos):
        item = self.itemAt(pos)
        if item is None:
            count = self.topLevelItemCount()
            last = self.topLevelItem(count - 1) if count else None
            while last is not None and last.isExpanded() and last.childCount():
                last = last.child(last.childCount()-1)
            y = self.visualItemRect(last).bottom() if last is not None else 1
            return '', count - self._first_custom_row(), None, y
        rect = self.visualItemRect(item)
        if item.data(0, CUSTOM_FOLDER_ROLE) and rect.top() + 4 < pos.y() < rect.bottom() - 4:
            return item.data(0, CUSTOM_FOLDER_ROLE), item.childCount(), item, None
        parent = item.parent()
        after = pos.y() > rect.center().y()
        index = parent.indexOfChild(item) if parent else self.indexOfTopLevelItem(item) - self._first_custom_row()
        if not parent and index < 0:return '', 0, None, self.visualItemRect(self.topLevelItem(self._first_custom_row())).top() if self._first_custom_row() < self.topLevelItemCount() else rect.bottom()
        return parent.data(0, CUSTOM_FOLDER_ROLE) if parent else '', index + int(after), None, rect.bottom() if after else rect.top()

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
            target = item if item and item.data(0, CUSTOM_ROLE) else None
            self._set_hover(target)
            self._set_insert(None)
            e.acceptProposedAction() if target else e.ignore()
        elif ("cust\t" in text or "sbfolder\t" in text) and e.source() is self:
            _parent, _index, target, y = self._drop_destination(e.position().toPoint())
            self._set_hover(target)
            self._set_insert(None); self._insert_y = y; self.viewport().update()
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
        folder_ids: list[str] = []
        for line in e.mimeData().text().split("\n"):
            kind, _, val = line.partition("\t")
            if kind == "trk":
                ids += [i for i in val.split(",") if i]
            elif kind == "cust" and val:
                custs.append(val)
            elif kind == 'sbfolder' and val:
                folder_ids.append(val)
        if name and ids:
            self.tracks_dropped.emit(name, ids)
            e.acceptProposedAction()
        elif (custs or folder_ids) and e.source() is self:
            parent, index, _target, _y = self._drop_destination(e.position().toPoint())
            token = 'folder:'+folder_ids[0] if folder_ids else 'playlist:'+custs[0]
            self.node_moved.emit(token, parent, index)
            e.acceptProposedAction()
        else:
            e.ignore()

    def paintEvent(self, e):
        super().paintEvent(e)
        if self._insert_y is not None:
            p = QPainter(self.viewport()); p.setPen(QPen(QColor(theme.PURPLE_LIGHT), 2))
            y = max(1, min(self._insert_y, self.viewport().height() - 1))
            p.drawLine(0, y, self.viewport().width(), y); p.end(); return
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
    """External devices — drop target (rb/cust/sbview). Dropping starts an export."""
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
            if kind in ("rb", "cust", "sbview") and val:
                items.append((kind, val))
        if dev and items:
            self.dropped.emit(dev, items)
            e.acceptProposedAction()
        else:
            e.ignore()


class TrackTable(NoTypeAhead, QTableView):
    """Track table — drag source (trk). Uses the chip drag ghost (the track title on
    a bordered box) instead of Qt's default transparent floating text.

    Also a drop target when a WRITABLE SoundBox view is on screen, mirroring the
    sidebar: existing tracks (trk) add to the current custom playlist, Finder
    audio files import (+add). The app decides the target via `resolve_drop`,
    which returns ("playlist", name) | ("collection", "") | None — Artists and
    read-only rekordbox views resolve to None so nothing writes to them."""
    tracks_dropped = Signal(str, list)  # custom playlist name, [track_ids]
    files_dropped = Signal(str, list)   # name ("" = Collection, import only), [paths]

    #: set by the app to () -> ("playlist", name) | ("collection", "") | None
    resolve_drop = None

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

    def _drop_target(self):
        fn = self.resolve_drop
        return fn() if callable(fn) else None

    def dragEnterEvent(self, e):
        md = e.mimeData()
        if self._drop_target() is not None and (_audio_paths(md) or "trk\t" in md.text()):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        tgt = self._drop_target()
        if tgt is None:
            e.ignore()
            return
        kind, _name = tgt
        md = e.mimeData()
        if _audio_paths(md):
            e.acceptProposedAction()  # Finder files import into any writable view
        elif "trk\t" in md.text() and kind == "playlist" and e.source() is not self:
            e.acceptProposedAction()  # existing tracks add only to a real playlist
        else:
            e.ignore()               # (never re-drop the table's own rows onto itself)

    def dropEvent(self, e):
        tgt = self._drop_target()
        if tgt is None:
            e.ignore()
            return
        kind, name = tgt
        files = _audio_paths(e.mimeData())
        if files:
            self.files_dropped.emit(name if kind == "playlist" else "", files)
            e.acceptProposedAction()
            return
        if kind == "playlist" and e.source() is not self:
            ids: list[str] = []
            for line in e.mimeData().text().split("\n"):
                k, _, val = line.partition("\t")
                if k == "trk":
                    ids += [i for i in val.split(",") if i]
            if ids:
                self.tracks_dropped.emit(name, ids)
                e.acceptProposedAction()
                return
        e.ignore()
