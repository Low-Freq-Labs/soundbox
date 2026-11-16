"""Sidebar trees, track table and drag/drop chrome.

Mime protocol: each dragged line is "<kind>\t<value>"; kinds: rb (rekordbox
display_path), cust (custom playlist name), trk (comma-joined track ids).
"""

from __future__ import annotations

import math
import json
from uuid import UUID

from PySide6.QtCore import QMimeData, QModelIndex, QPersistentModelIndex, QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QDrag, QFontMetrics, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QLineEdit, QStyledItemDelegate, QTableView,
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
RB_NODE_ROLE = Qt.UserRole + 21  # stable native playlist/folder ID
RB_NODE_KIND_ROLE = Qt.UserRole + 22
NATIVE_BINDING_ROLE = Qt.UserRole + 83
NATIVE_PLAYLIST_MIME = 'application/x-soundbox-native-playlists-v1'
_NATIVE_LOCAL_MIME = 'application/x-soundbox-native-drag-local-selection-v1'
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


def native_playlist_binding(value):
    if (not isinstance(value,dict) or set(value)!={'source','library_id','id','uuid','alias'}
            or any(type(value[key]) is not str or not value[key] or len(value[key])>8192 or '\0' in value[key]
                   for key in ('source','library_id','id','uuid'))
            or value['alias'] is not None and (type(value['alias']) is not str or not value['alias'] or len(value['alias'])>8192 or '\0' in value['alias'])):
        raise ValueError('Invalid native playlist drag identity')
    identity=value['id']
    if (not identity.isascii() or not identity.isdecimal() or not 0<int(identity)<2**32
            or str(int(identity))!=identity or str(UUID(value['uuid']))!=value['uuid']):
        raise ValueError('Invalid native playlist drag identity')
    return dict(value)


def _descendant_native_bindings(item):
    if item.data(0,PATH_ROLE):
        value=item.data(0,NATIVE_BINDING_ROLE)
        return [native_playlist_binding(value) if value is not None else None]
    return [binding for index in range(item.childCount()) for binding in _descendant_native_bindings(item.child(index))]


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
    if md is None or not (md.hasText() or md.hasFormat('application/x-soundbox-native-playlist')):
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


class TrackBrowserTree(BrowserDetailTree):
    """Container rows toggle on one click; only track rows can be selected.

    Callers remove ItemIsSelectable from folders/playlists. Keeping the current
    item independent of selection preserves keyboard navigation and playlist
    actions without painting a selected container.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setExpandsOnDoubleClick(False)
        self._container_press = None

    def mousePressEvent(self, event):
        position = event.position().toPoint()
        index = self.indexAt(position)
        item = self.itemFromIndex(index)
        self._container_press = None
        if (event.button() == Qt.LeftButton and item is not None
                and not item.flags() & Qt.ItemIsSelectable):
            option = QStyleOptionViewItem()
            option.initFrom(self)
            option.rect = self.visualRect(index)
            self.itemDelegateForIndex(index).initStyleOption(option, index)
            check = self.style().subElementRect(QStyle.SE_ItemViewItemCheckIndicator, option, self)
            if not (item.flags() & Qt.ItemIsUserCheckable and check.contains(position)):
                self._container_press = (QPersistentModelIndex(index), item.isExpanded())
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        press = self._container_press
        self._container_press = None
        super().mouseReleaseEvent(event)
        if (event.button() == Qt.LeftButton and press is not None and press[0].isValid()
                and self.indexAt(event.position().toPoint()) == press[0]):
            item = self.itemFromIndex(QModelIndex(press[0]))
            # The native branch hit test already toggles on press.
            if item.isExpanded() == press[1]:
                item.setExpanded(not press[1])


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


def sidebar_row_geometry(rect, indentation=16):
    """One branch/icon center and label start for every sidebar row kind."""
    rect=QRectF(rect)
    center=rect.left()-indentation/2+6
    return (QRectF(center-6,rect.center().y()-6,12,12),rect.adjusted(8,0,-4,0))


def _sidebar_icon(painter, box, kind, expanded=False):
    path=QPainterPath()
    if kind=='folder':
        if expanded:path.moveTo(-4,-2);path.lineTo(0,2);path.lineTo(4,-2)
        else:path.moveTo(-2,-4);path.lineTo(2,0);path.lineTo(-2,4)
    elif kind=='home':
        path.moveTo(-5,0);path.lineTo(0,-5);path.lineTo(5,0)
        path.moveTo(-3.5,-1.5);path.lineTo(-3.5,5);path.lineTo(3.5,5);path.lineTo(3.5,-1.5)
        path.moveTo(-1,5);path.lineTo(-1,1);path.lineTo(1,1);path.lineTo(1,5)
    elif kind=='star':
        for i in range(10):
            angle=-math.pi/2+i*math.pi/5;radius=5 if i%2==0 else 2.3
            point=(radius*math.cos(angle),radius*math.sin(angle))
            if i:path.lineTo(*point)
            else:path.moveTo(*point)
        path.closeSubpath()
    elif kind=='person':
        path.addEllipse(QRectF(-2.25,-5,4.5,4.5))
        path.moveTo(-4.5,5);path.lineTo(-4.5,3.5)
        path.cubicTo(-4.5,-.5,4.5,-.5,4.5,3.5);path.lineTo(4.5,5)
        path.closeSubpath()
    elif kind=='crate':
        # Open rim, straight sides and one handle slot.
        path.moveTo(-5,-2.2);path.lineTo(-3.2,-5)
        path.lineTo(3.2,-5);path.lineTo(5,-2.2)
        path.addRect(QRectF(-5,-2.2,10,7.2))
        path.addRoundedRect(QRectF(-1.7,-.8,3.4,1.6),.8,.8)
    else:
        path.addEllipse(QRectF(-4.5,1,4.5,3.5))
        path.moveTo(0,2.5);path.lineTo(0,-5)
        path.cubicTo(0,-2.5,4.5,-3,4.5,0)
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing,True)
    # Center actual vector bounds, avoiding font bearings and asymmetric glyphs.
    center=box.center()-path.boundingRect().center()
    painter.translate(center)
    pen=QPen(painter.pen().color(),1.25,Qt.SolidLine,Qt.RoundCap,Qt.RoundJoin)
    painter.setPen(pen);painter.setBrush(Qt.NoBrush);painter.drawPath(path)
    painter.restore()


def sidebar_choice_icon(kind):
    pixmap=QPixmap(20,20);pixmap.fill(Qt.transparent)
    painter=QPainter(pixmap);painter.setPen(QColor(theme.TEXT))
    _sidebar_icon(painter,QRectF(4,4,12,12),kind)
    painter.end()
    return QIcon(pixmap)


class _PlaylistDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        context = getattr(self.parent(), '_inline_rename', None)
        if not context or context[0] != index:return None
        editor = QLineEdit(parent)
        editor.setObjectName('PlaylistNameEditor')
        editor._rename_name, editor._rename_save = context[1:]
        return editor

    def setEditorData(self, editor, index):
        editor.setText(editor._rename_name); editor.selectAll()

    def setModelData(self, editor, model, index):
        value = editor.text().strip()
        if value and value != editor._rename_name:
            save = editor._rename_save
            QTimer.singleShot(0, self.parent(), lambda: save(value))

    def updateEditorGeometry(self, editor, option, index):
        _icon, text = sidebar_row_geometry(option.rect, self.parent().indentation())
        editor.setGeometry(text.toAlignedRect())

    def destroyEditor(self, editor, index):
        self.parent()._inline_rename = None
        super().destroyEditor(editor, index)

    def paint(self, painter, option, index):
        option = QStyleOptionViewItem(option)
        self.initStyleOption(option, index)
        icon,text=sidebar_row_geometry(option.rect,self.parent().indentation())
        panel=QStyleOptionViewItem(option)
        panel.rect.setLeft(0)
        option.widget.style().drawPrimitive(QStyle.PE_PanelItemViewItem,panel,painter,option.widget)
        kind=self.parent().sidebar_icon_kind(index)
        label=option.text
        if kind!='folder' and label.partition('  ')[0] in ('♪','⌂','★'):
            label=label.partition('  ')[2]
        painter.save();painter.setFont(option.font)
        selected=bool(option.state & QStyle.State_Selected)
        painter.setPen(QColor(theme.ON_ACCENT if selected else theme.TEXT))
        _sidebar_icon(painter,icon,kind,bool(option.state & QStyle.State_Open))
        painter.drawText(text,Qt.AlignVCenter|Qt.AlignLeft,
                         option.fontMetrics.elidedText(label,Qt.ElideRight,max(0,int(text.width()))))
        painter.restore()


class PlaylistFolderTree(NoTypeAhead, QTreeWidget):
    """Folders navigate without selecting a playlist; pressed folders still drag."""
    def __init__(self):
        super().__init__()
        self.setExpandsOnDoubleClick(False)
        self.setIndentation(16)
        self.setItemDelegate(_PlaylistDelegate(self))
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)

    def rename_inline(self, item, name, save):
        if item is None:return False
        index = self.indexFromItem(item)
        self._inline_rename = (QPersistentModelIndex(index), name, save)
        item.setFlags(item.flags() | Qt.ItemIsEditable)
        self.setCurrentItem(item); self.setFocus(); self.editItem(item, 0)
        return True

    def drawBranches(self, painter, rect, index):
        # The delegate paints the branch and icon through the same geometry.
        # Qt still owns expansion hit testing and keyboard navigation.
        pass

    def sidebar_icon_kind(self,index):
        if index.data(COLLECTION_ROLE):return 'home'
        if index.data(ARTISTS_ROLE):return 'person'
        default='star' if index.data(CUSTOM_ROLE) else 'note' if index.data(PATH_ROLE) else 'folder'
        getter=getattr(self.window(),'_playlist_icon_kind',None)
        return getter(index,default) if getter and default!='folder' else default

    def playlist_icon_item_at(self,pos):
        item=self.itemAt(pos)
        if (item is None or item.data(0,COLLECTION_ROLE) or item.data(0,ARTISTS_ROLE)
                or not (item.data(0,PATH_ROLE) or item.data(0,CUSTOM_ROLE))):return None
        icon,_text=sidebar_row_geometry(self.visualItemRect(item),self.indentation())
        return item if icon.contains(pos) else None

    @staticmethod
    def is_folder(item):
        return item is not None and not any(item.data(0, role) for role in
            (PATH_ROLE, CUSTOM_ROLE, COLLECTION_ROLE, ARTISTS_ROLE))

    def mousePressEvent(self, event):
        if event.button()==Qt.RightButton and self.playlist_icon_item_at(event.position().toPoint()):
            event.accept();return
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
    tracks_dropped = Signal(str, list)  # native playlist ID, track IDs
    files_dropped = Signal(str, list)

    def __init__(self):
        super().__init__()
        self.setDropIndicatorShown(False)
        self._hover = QPersistentModelIndex()

    def _set_hover(self, item):
        index = QPersistentModelIndex(self.indexFromItem(item)) if item is not None else QPersistentModelIndex()
        if index == self._hover:
            return
        if self._hover.isValid():
            self.itemFromIndex(QModelIndex(self._hover)).setBackground(0, QBrush())
        self._hover = index
        if item is not None:
            item.setBackground(0, QColor(theme.PURPLE))

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


    def dragEnterEvent(self, event):
        if 'trk\t' in event.mimeData().text() or _audio_paths(event.mimeData()):event.acceptProposedAction()
        else:event.ignore()

    def dragMoveEvent(self, event):
        item=self.itemAt(event.position().toPoint())
        if (item and item.data(0,PATH_ROLE) and item.data(0,RB_NODE_KIND_ROLE)==0
                and ('trk\t' in event.mimeData().text() or _audio_paths(event.mimeData()))):
            self._set_hover(item)
            event.acceptProposedAction()
        else:
            self._set_hover(None)
            event.ignore()

    def dragLeaveEvent(self, event):
        self._set_hover(None)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self._set_hover(None)
        item=self.itemAt(event.position().toPoint())
        if item is None or not item.data(0,PATH_ROLE) or item.data(0,RB_NODE_KIND_ROLE)!=0:event.ignore();return
        files=_audio_paths(event.mimeData())
        if files and item.data(0,RB_NODE_ROLE):
            self.files_dropped.emit(item.data(0,RB_NODE_ROLE),files);event.acceptProposedAction();return
        ids=[]
        for line in event.mimeData().text().split('\n'):
            kind,_,value=line.partition('\t')
            if kind=='trk':ids.extend(v for v in value.split(',') if v)
        if ids and item.data(0,RB_NODE_ROLE):
            self.tracks_dropped.emit(item.data(0,RB_NODE_ROLE),ids);event.acceptProposedAction()
        else:event.ignore()


def _audio_paths(md) -> list[str]:
    """Local audio files in a Finder drag (empty for anything else)."""
    from pathlib import Path
    from soundbox.imports import is_audio
    if not md.hasUrls():
        return []
    return [u.toLocalFile() for u in md.urls()
            if u.isLocalFile() and (is_audio(Path(u.toLocalFile()))
                                   or Path(u.toLocalFile()).is_dir())]


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
        self._hover = QPersistentModelIndex()
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
        index = QPersistentModelIndex(self.indexFromItem(item)) if item is not None else QPersistentModelIndex()
        if index == self._hover:
            return
        # Source readback can rebuild the sidebar during a Finder drag. Qt
        # invalidates this index when its row is deleted; a Python item wrapper
        # would still point at the freed C++ object on the next move/drop.
        if self._hover.isValid():
            self.itemFromIndex(QModelIndex(self._hover)).setBackground(0, QBrush())
        self._hover = index
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


NATIVE_TREE_ROLE = Qt.UserRole + 80
PINNED_TREE_ROLE = Qt.UserRole + 81


class UnifiedPlaylistTree(SoundBoxTree):
    """One navigation surface for native and not-yet-migrated local playlists."""
    native_tracks_dropped = Signal(str,list)
    native_files_dropped = Signal(str,list)
    native_reordered = Signal(str,str,bool)  # dragged ID, anchor ID, after
    _native_mime = 'application/x-soundbox-native-playlist'

    def mimeData(self,items):
        local=[item for item in items if not item.data(0,NATIVE_TREE_ROLE)]
        md=SoundBoxTree.mimeData(self,local)
        local_text=md.text()
        lines=local_text.splitlines()
        for item in items:
            if item in local:continue
            if item.data(0,CUSTOM_ROLE):lines.append('cust\t'+item.data(0,CUSTOM_ROLE))
            else:lines.extend('rb\t'+path for path in _descendant_rb_paths(item))
        bindings=[]
        try:
            for item in items:
                if item.data(0,NATIVE_TREE_ROLE):bindings.extend(_descendant_native_bindings(item))
        except (ValueError,TypeError):
            md.setData(NATIVE_PLAYLIST_MIME,b'')  # Reject a partially malformed native selection at the target.
        else:
            if any(binding is not None for binding in bindings):
                if any(binding is None for binding in bindings):
                    md.setData(NATIVE_PLAYLIST_MIME,b'')
                    bindings=[]
            else:bindings=[]
            if bindings:
                unique={json.dumps(value,sort_keys=True):value for value in bindings}
                md.setData(NATIVE_PLAYLIST_MIME,json.dumps(list(unique.values()),ensure_ascii=False).encode())
        if len(items)==1 and items[0].data(0,NATIVE_TREE_ROLE) and not items[0].data(0,PINNED_TREE_ROLE):
            identity=items[0].data(0,RB_NODE_ROLE)
            binding=items[0].data(0,NATIVE_BINDING_ROLE)
            if identity:
                md.setData(self._native_mime,json.dumps(binding).encode() if binding is not None else str(identity).encode('ascii'))
        if md.hasFormat(NATIVE_PLAYLIST_MIME):
            # Legacy native alias text can contain newlines. Only the separate
            # local selection may supplement a typed native export selection.
            md.setData(_NATIVE_LOCAL_MIME,json.dumps(local_text).encode())
        md.setText('\n'.join(dict.fromkeys(lines)));return md

    def startDrag(self,actions):
        items=self._playlist_drag_items()
        if items:
            md=self.mimeData(items)
            if md.hasText() or md.hasFormat(self._native_mime):_start_chip_drag(self,md,items[0].text(0),actions)

    def _native_drop_destination(self,pos,identity):
        source=next((item for item in self.findItems('',Qt.MatchContains|Qt.MatchRecursive)
                     if item.data(0,RB_NODE_ROLE)==identity),None)
        if source is None or source.data(0,PINNED_TREE_ROLE):return None
        parent=source.parent();root=parent or self.invisibleRootItem()
        candidates=[root.child(i) for i in range(root.childCount())
                    if root.child(i).data(0,NATIVE_TREE_ROLE) and not root.child(i).data(0,PINNED_TREE_ROLE)
                    and root.child(i) is not source]
        if not candidates:return None
        item=self.itemAt(pos)
        if item is None:
            if parent is not None:return None
            item=candidates[-1];after=True
        elif parent is None and item.data(0,PINNED_TREE_ROLE):
            item=candidates[0];after=False
        else:
            # Hovering an expanded root folder's contents still addresses
            # that root sibling; no drag implicitly reparents native items.
            while item is not None and item.parent() is not parent:item=item.parent()
            if item not in candidates:return None
            after=pos.y()>self.visualItemRect(item).center().y()
        rect=self.visualItemRect(item)
        if after:
            last=item
            while last.isExpanded() and last.childCount():last=last.child(last.childCount()-1)
            rect=self.visualItemRect(last)
        y=rect.bottom() if after else rect.top()
        return item.data(0,RB_NODE_ROLE),after,y

    def _native_drag(self,event):
        if event.source() is not self or not event.mimeData().hasFormat(self._native_mime):return None
        try:
            raw=bytes(event.mimeData().data(self._native_mime))
            if len(raw)>64*1024:raise ValueError('Unbounded native drag')
            text=raw.decode('utf-8')
            captured=native_playlist_binding(json.loads(text)) if text.startswith('{') else None
            identity=captured['id'] if captured else text
            current=next((item for item in self.findItems('',Qt.MatchContains|Qt.MatchRecursive)
                          if item.data(0,RB_NODE_ROLE)==identity),None)
            binding=current.data(0,NATIVE_BINDING_ROLE) if current is not None else None
            if captured:
                binding=native_playlist_binding(binding)
                if any(captured[key]!=binding[key] for key in ('source','library_id','id','uuid')):
                    return identity,None
            elif binding is not None:return identity,None
        except (UnicodeError,ValueError,TypeError):return '',None
        return identity,self._native_drop_destination(event.position().toPoint(),identity)

    def dragMoveEvent(self,event):
        reorder=self._native_drag(event)
        if reorder is not None:
            _identity,destination=reorder
            self._set_hover(None);self._set_insert(None)
            if destination is None:event.ignore();return
            self._insert_y=destination[2];self.viewport().update();event.acceptProposedAction();return
        item=self.itemAt(event.position().toPoint())
        if item and item.data(0,NATIVE_TREE_ROLE):
            PlaylistTree.dragMoveEvent(self,event)
        else:SoundBoxTree.dragMoveEvent(self,event)

    def dropEvent(self,event):
        reorder=self._native_drag(event)
        if reorder is not None:
            identity,destination=reorder;self._set_hover(None);self._set_insert(None)
            if destination is None:event.ignore();return
            anchor,after,_y=destination
            self.native_reordered.emit(identity,anchor,after);event.acceptProposedAction();return
        item=self.itemAt(event.position().toPoint())
        if item and item.data(0,NATIVE_TREE_ROLE):
            self._set_hover(None);self._set_insert(None)
            if not item.data(0,PATH_ROLE) or item.data(0,RB_NODE_KIND_ROLE)!=0:event.ignore();return
            files=_audio_paths(event.mimeData())
            if files:self.native_files_dropped.emit(item.data(0,RB_NODE_ROLE),files);event.acceptProposedAction();return
            ids=[]
            for line in event.mimeData().text().splitlines():
                kind,_,value=line.partition('\t')
                if kind=='trk':ids.extend(i for i in value.split(',') if i)
            if ids:self.native_tracks_dropped.emit(item.data(0,RB_NODE_ROLE),ids);event.acceptProposedAction()
            else:event.ignore()
        else:SoundBoxTree.dropEvent(self,event)


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
        self._hover = QPersistentModelIndex()

    def _set_hover(self, item):
        index = QPersistentModelIndex(self.indexFromItem(item)) if item is not None else QPersistentModelIndex()
        if index == self._hover:
            return
        # Discovery may rebuild the device rows while a drag remains active.
        # Deleted rows invalidate this index instead of leaving a freed wrapper.
        if self._hover.isValid():
            self.itemFromIndex(QModelIndex(self._hover)).setBackground(0, QBrush())
        self._hover = index
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
        typed=e.mimeData().hasFormat(NATIVE_PLAYLIST_MIME)
        if typed:
            try:
                raw=bytes(e.mimeData().data(NATIVE_PLAYLIST_MIME))
                if len(raw)>4*1024**2:raise ValueError('Unbounded playlist drag')
                values=json.loads(raw)
                if not isinstance(values,list) or not 1<=len(values)<=4096:raise ValueError('Invalid playlist drag')
                for value in values:
                    binding=native_playlist_binding(value)
                    items.append(('rbid',json.dumps(binding,ensure_ascii=False)))
            except (ValueError,TypeError,UnicodeError):e.ignore();return
        text=e.mimeData().text()
        if typed:
            try:
                raw=bytes(e.mimeData().data(_NATIVE_LOCAL_MIME))
                if len(raw)>4*1024**2:raise ValueError('Unbounded local selection')
                text=json.loads(raw) if raw else ''
                if not isinstance(text,str):raise ValueError('Invalid local selection')
            except (ValueError,TypeError,UnicodeError):e.ignore();return
        for line in text.split("\n"):
            kind, _, val = line.partition("\t")
            if kind in (("cust","sbview") if typed else ("rb", "cust", "sbview")) and val:
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
    which returns a custom playlist, Collection, a regular native playlist, or
    None. Native destinations accept registered track IDs through their guarded
    writer; source-only file imports remain a separate registration protocol."""
    tracks_dropped = Signal(str, list)  # custom playlist name, [track_ids]
    rekordbox_tracks_dropped = Signal(str, list)
    rekordbox_files_dropped = Signal(str, list)
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
        elif "trk\t" in md.text() and kind in ("playlist","rekordbox") and e.source() is not self:
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
            if kind=='rekordbox':self.rekordbox_files_dropped.emit(name,files)
            else:self.files_dropped.emit(name if kind == "playlist" else "", files)
            e.acceptProposedAction()
            return
        if kind in ("playlist","rekordbox") and e.source() is not self:
            ids: list[str] = []
            for line in e.mimeData().text().split("\n"):
                k, _, val = line.partition("\t")
                if k == "trk":
                    ids += [i for i in val.split(",") if i]
            if ids:
                signal=self.rekordbox_tracks_dropped if kind=="rekordbox" else self.tracks_dropped
                signal.emit(name,ids)
                e.acceptProposedAction()
                return
        e.ignore()
