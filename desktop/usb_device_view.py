"""Shared USB presentation with separate bounded native and M3U readers.

The M3U reader opens only /Playlists, never referenced media or PIONEER. Generic
USB native metadata/profile reads use the dedicated cdj_usb boundary. Snapshots
are presentation data, never confirmation or device-write authority.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import os
from pathlib import Path, PurePosixPath
import stat

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QGridLayout, QLineEdit, QPushButton, QScrollArea, QStackedWidget,
    QTreeWidgetItem, QTreeWidgetItemIterator, QVBoxLayout, QWidget, QLabel, QHBoxLayout,
)
from .choices import ComboBox as QComboBox

from soundbox.cdj_usb import CdjLibrary, CdjPlaylist, CdjSong, DISPLAY_FIELDS

from .devices import DeviceFacts, human
from .device_playlists import create_playlist_page
from .stock_device_view import StockIpodPages
from .trees import TrackBrowserTree
from .choices import Menu as QMenu, exec_transient_menu
from .usb_preferences import MySettingsEditor, OrderedChoices, BrowsePreferences, USB_TABS
from soundbox import my_settings

_MAX_FILES = 1024
_MAX_BYTES = 8 * 1024 * 1024
_MAX_TOTAL_BYTES = 32 * 1024 * 1024
_MAX_ENTRIES = 100_000
_MAX_DIRECTORY_ENTRIES = 8192


@dataclass(frozen=True)
class UsbPlaylist:
    title: str
    location: str
    entries: tuple[tuple[str, str], ...]
    skipped: int = 0


@dataclass(frozen=True)
class UsbSnapshot:
    target: Path
    mount_token: int
    facts: DeviceFacts
    playlists: tuple[UsbPlaylist, ...] = ()
    error: str = ""
    native: CdjLibrary | None = None
    native_stamp: tuple = ()
    notice: str = ""


def _media_path(folder: str, text: str) -> str:
    text = text.replace('\\', '/')
    if ':' in text or '\0' in text:
        return ''
    parts = [] if text.startswith('/') else list(PurePosixPath(folder).parts)
    for part in text.split('/'):
        if part in ('', '.'):
            continue
        if part == '..':
            if not parts:
                return ''
            parts.pop()
        else:
            parts.append(part)
    if not parts or parts[0].casefold() == 'pioneer':
        return ''
    return '/' + '/'.join(parts)


def read_usb_snapshot(target: str, mount_token: int, facts: DeviceFacts) -> UsbSnapshot:
    """Read playlist text through pinned no-follow directory descriptors."""
    root = Path(target)
    rows = []
    budget = [0, 0, 0]  # directory entries, payload bytes, playlist entries
    flags = os.O_RDONLY | os.O_NOFOLLOW

    def read_folder(fd, folder, depth=0):
        if depth > 16:
            raise ValueError('Playlist folders are nested too deeply')
        before = os.fstat(fd)
        with os.scandir(fd) as iterator:
            names = []
            for entry in iterator:
                budget[0] += 1
                if budget[0] > _MAX_DIRECTORY_ENTRIES:
                    raise ValueError('Too many entries in the Playlists folder')
                if not entry.name.startswith('.'):
                    names.append(entry.name)
        for name in sorted(names, key=str.casefold):
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                raise ValueError('Linked files or folders cannot be read as playlists')
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, flags | os.O_DIRECTORY, dir_fd=fd)
                try:
                    if os.fstat(child).st_dev != mount_token:
                        raise ValueError('Playlist folder crosses the device boundary')
                    read_folder(child, folder + '/' + name, depth + 1)
                finally:
                    os.close(child)
                continue
            if Path(name).suffix.casefold() not in ('.m3u', '.m3u8'):
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_BYTES:
                raise ValueError('Playlist is not a supported regular text file')
            if len(rows) >= _MAX_FILES:
                raise ValueError('Too many playlist files')
            child = os.open(name, flags | os.O_NONBLOCK, dir_fd=fd)
            with os.fdopen(child, 'rb') as stream:
                start = os.fstat(stream.fileno())
                if not stat.S_ISREG(start.st_mode) or start.st_dev != mount_token:
                    raise ValueError('Playlist identity changed while opening it')
                raw = stream.read(_MAX_BYTES + 1)
                end = os.fstat(stream.fileno())
            if (len(raw) > _MAX_BYTES or len(raw) != start.st_size
                    or (start.st_ino, start.st_size, start.st_mtime_ns, start.st_ctime_ns)
                    != (end.st_ino, end.st_size, end.st_mtime_ns, end.st_ctime_ns)):
                raise ValueError('Playlist changed during the read')
            budget[1] += len(raw)
            if budget[1] > _MAX_TOTAL_BYTES:
                raise ValueError('Playlist text exceeds the display limit')
            entries, title, skipped = [], '', 0
            for line in raw.decode('utf-8-sig', errors='replace').splitlines():
                line = line.strip()
                if line.startswith('#EXTINF:'):
                    title = line.partition(',')[2].strip()
                elif line and not line.startswith('#'):
                    budget[2] += 1
                    if budget[2] > _MAX_ENTRIES:
                        raise ValueError('Too many playlist entries to display')
                    path = _media_path(folder, line)
                    if path:
                        entries.append((title or PurePosixPath(path).stem, path))
                    else:
                        skipped += 1
                    title = ''
            rows.append(UsbPlaylist(Path(name).stem, folder + '/' + name,
                                    tuple(entries), skipped))
        after = os.fstat(fd)
        if (before.st_ino, before.st_mtime_ns) != (after.st_ino, after.st_mtime_ns):
            raise ValueError('Playlist folder changed during the read')

    root_fd = None
    try:
        if mount_token <= 0:
            raise ValueError('Device identity is unavailable')
        root_fd = os.open(root, flags | os.O_DIRECTORY)
        initial = os.fstat(root_fd)
        if initial.st_dev != mount_token:
            raise ValueError('Device was remounted before the playlist read')
        try:
            playlist_fd = os.open('Playlists', flags | os.O_DIRECTORY, dir_fd=root_fd)
        except FileNotFoundError:
            playlist_fd = None
        if playlist_fd is not None:
            try:
                if os.fstat(playlist_fd).st_dev != mount_token:
                    raise ValueError('Playlists folder crosses the device boundary')
                read_folder(playlist_fd, 'Playlists')
            finally:
                os.close(playlist_fd)
        current = os.stat(root, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (initial.st_dev, initial.st_ino):
            raise ValueError('Device was remounted during the playlist read')
        return UsbSnapshot(root, mount_token, facts, tuple(rows))
    except (OSError, ValueError) as exc:
        return UsbSnapshot(root, mount_token, facts, error=str(exc))
    finally:
        if root_fd is not None:
            os.close(root_fd)


def read_native_usb_snapshot(target, mount_token, facts):
    from soundbox.cdj_usb import read_library,metadata_stamp
    try:
        before=metadata_stamp(target,mount_token)
        native = read_library(target, mount_token,snapshot_only=True)
        if before!=metadata_stamp(target,mount_token):raise ValueError('USB library is updating; reading it again shortly')
        if native is not None:
            return UsbSnapshot(Path(target), mount_token, facts, native=native,native_stamp=before)
    except (OSError, ValueError, RuntimeError) as exc:
        return UsbSnapshot(Path(target), mount_token, facts, error=str(exc))
    return replace(read_usb_snapshot(target, mount_token, facts),native_stamp=before)


class UsbDevicePages(QWidget):
    """Reuse the iPod dashboard's four panels and playlist-tree appearance."""
    settings_requested = Signal(dict)
    song_requested = Signal(object)
    view_changed = Signal()
    global_requested = Signal(dict)
    global_load_requested = Signal()
    delete_requested = Signal(list)
    primary_action_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack)
        overview = QWidget()
        grid = QGridLayout(overview)
        grid.setContentsMargins(16, 12, 16, 12)
        grid.setSpacing(10)
        self.general = QWidget()
        self.general_layout = QGridLayout(self.general)
        self.general_layout.setContentsMargins(8,0,8,12)
        self.general_layout.setHorizontalSpacing(28)
        self.general_layout.setVerticalSpacing(8)
        grid.addWidget(self.general, 0, 0, 1, 2)
        self.controls = {}
        self._general_identity = None
        self.apply_settings = QPushButton('Apply')
        self.apply_settings.setEnabled(False)
        self.apply_settings.clicked.connect(self._apply_settings)
        self.detail_trees = {}
        for i, title in enumerate(('Identity', 'Storage', 'Playlists', 'SoundBox')):
            tree = StockIpodPages._detail_tree(title)
            self.detail_trees[title] = tree
            grid.addWidget(tree, 1 + i // 2, i % 2)
            tree.setMinimumHeight(175)
        for i in range(2):
            grid.setColumnStretch(i, 1)
            grid.setRowStretch(1 + i, 10 if i == 0 else 8)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(overview)
        self.overview_scroll = scroll
        self.stack.addWidget(scroll)
        playlist_page = create_playlist_page(TrackBrowserTree(),
                                             selection_mode=QAbstractItemView.ExtendedSelection)
        self.playlist_tree = playlist_page.tree
        self.playlist_tree.customContextMenuRequested.connect(self._playlist_context_menu)
        self._delete_enabled=True
        self._delete_profile=None
        self._playlist_items={}
        self._primary_enabled=False
        self.export_playlist_button=playlist_page.primary
        self.export_playlist_button.clicked.connect(self._request_primary_action)
        self.delete_playlist_button=playlist_page.delete
        self.delete_playlist_button.clicked.connect(self._request_playlist_delete)
        self.playlist_tree.itemChanged.connect(self._update_delete_playlist_button)
        self.playlist_tree.itemDoubleClicked.connect(self._activate_song)
        self.playlist_tree.itemActivated.connect(self._activate_song)
        self._last_activation = None
        self._states = {}
        self._view_key = ''
        self._restoring = False
        self._view_save_timer = QTimer(self)
        self._view_save_timer.setSingleShot(True)
        self._view_save_timer.setInterval(120)
        self._view_save_timer.timeout.connect(self.view_changed.emit)
        self._songs = {}
        self._songs_by_id = {}
        self._display_rows = {}
        self.row_enricher = None
        self.stack.addWidget(playlist_page.page)
        self.categories = OrderedChoices('categories', (4,17,19,20,24))
        self.sorts = OrderedChoices('sort options', (25,26))
        self.column = BrowsePreferences('column')
        self.colors = BrowsePreferences('colors')
        self.my_settings = MySettingsEditor()
        self.global_profile = {}
        for page in (self.categories,self.sorts,self.column,self.colors,self.my_settings):
            self.stack.addWidget(page)
        self.categories.save_requested.connect(lambda order:self.settings_requested.emit({'categories':order}))
        self.sorts.save_requested.connect(lambda order:self.settings_requested.emit({'sorts':order}))
        self.column.save_requested.connect(self.settings_requested)
        self.colors.save_requested.connect(self.settings_requested)
        self.my_settings.save_requested.connect(lambda profile:self.settings_requested.emit({'my_settings':profile}))
        self.my_settings.promote_requested.connect(self.global_requested)
        self.my_settings.load_requested.connect(self.global_load_requested)
        self._snapshot = object()
        self.playlist_tree.itemExpanded.connect(self._expand_playlist)
        self.playlist_tree.itemExpanded.connect(self._remember_view)
        self.playlist_tree.itemCollapsed.connect(self._remember_view)
        self.playlist_tree.itemChanged.connect(self._remember_checked)
        self.playlist_tree.currentItemChanged.connect(self._remember_view)
        self.playlist_tree.verticalScrollBar().valueChanged.connect(self._remember_scroll)
        self.overview_scroll.verticalScrollBar().valueChanged.connect(self._remember_scroll)
        self.stack.currentChanged.connect(self._remember_view)

    def set_page(self, index):
        self.stack.setCurrentIndex(max(0, min(len(USB_TABS)-1, index)))

    def set_primary_action(self, _caption, enabled, reason=''):
        """The USB role always exports; it never inherits the stock Sync label."""
        self._primary_enabled=bool(enabled)
        self.export_playlist_button.setText('Export')
        self.export_playlist_button.setEnabled(self._primary_enabled)
        self.export_playlist_button.setToolTip(str(reason))

    def _request_primary_action(self):
        if self._primary_enabled:self.primary_action_requested.emit()

    def _rows(self, title, rows):
        tree = self.detail_trees[title]
        shown = [(label, value or 'Not reported') for label, value in rows]
        if [(tree.topLevelItem(i).text(0), tree.topLevelItem(i).text(1))
                for i in range(tree.topLevelItemCount())] == shown:
            return
        tree.clear()
        for row in shown:
            QTreeWidgetItem(tree, row)

    def set_snapshot(self, snapshot, *, name, profile, free, drift):
        self._delete_profile=profile
        facts = snapshot.facts if snapshot is not None else DeviceFacts()
        target = str(snapshot.target) if snapshot is not None else ''
        self._rows('Identity', (('Name', name), ('Type', getattr(profile, 'label', 'USB')),
                   ('Device', facts.device_identifier), ('Mount', target)))
        capacity = facts.capacity_bytes
        self._rows('Storage', (('Capacity', human(capacity) if capacity else 'Unknown'),
                   ('Used', human(max(0, capacity-free)) if capacity and free is not None else 'Unknown'),
                   ('Free', human(free) if free is not None else 'Unknown'), ('Filesystem', facts.filesystem_name),
                   ('Volume access', ('Writable' if facts.writable else 'Read-only')
                    if snapshot is not None and (facts.device_identifier or facts.filesystem_name)
                    else 'Unknown')))
        notice = snapshot.notice if snapshot else ''
        available = snapshot is not None and not snapshot.error and not notice
        playlists = snapshot.playlists if available else ()
        native = snapshot.native if snapshot else None
        same_device = self._view_key == (facts.volume_uuid or target)
        preserve_drafts = bool(notice and same_device)
        self.general.setVisible(getattr(profile, 'kind', '') in {'generic_usb','ipod_usb'})
        if not preserve_drafts:
            self._set_general(native)
        count = len([p for p in native.playlists if not p.folder]) if native else len(playlists)
        self._rows('Playlists', (('Playlists', str(count) if available else 'Not loaded' if notice else 'Unavailable'),
                   ('Location', '/PIONEER/rekordbox' if native or getattr(profile, 'kind', '') in {'generic_usb','ipod_usb'} else '/Playlists'),
                   ('Status', notice or (snapshot.error if snapshot and snapshot.error
                    else ('Ready' if available else 'Reading device…')))))
        if getattr(profile, 'kind', '') in {'generic_usb','ipod_usb'}:
            supported = getattr(profile, 'cdj_export', False)
            self._rows('SoundBox', (('Playlist export', 'Ready' if supported else 'FAT32 or exFAT required'),
                       ('Library format', 'Device Library + Device Library Plus'),
                       ('Export', 'Choose playlists and Export')))
        else:
            self._rows('SoundBox', (('Sync status', 'Status pending' if drift is None else
                        ('Up to date' if drift == 0 else 'Sync needed')),
                       ('Pending changes', str(drift) if drift is not None else 'Unknown'),
                       ('Playlist export', 'FAT32 supported' if getattr(profile, 'rockbox_export', False) else 'FAT32 required')))
        if snapshot is self._snapshot:
            self._update_delete_playlist_button()
            return
        if not getattr(self._snapshot, 'notice', ''):
            self._remember_view()
        self._restoring = True
        self._snapshot = snapshot
        preferences = native.preferences if native else {}
        if not preserve_drafts:
            self.categories.set_slots(preferences.get('categories',[]))
            self.sorts.set_slots(preferences.get('sorts',[]))
            self.categories.setEnabled(bool(preferences.get('categories')))
            self.sorts.setEnabled(bool(preferences.get('sorts')))
            self.column.set_preferences(preferences)
            self.colors.set_preferences(preferences)
            current=my_settings.values(native.files) if native else {}
            if self.my_settings.profile!=current or self.my_settings.global_profile!=self.global_profile:
                self.my_settings.set_profile(current,self.global_profile,preserve_edits=same_device)
        self._view_key = facts.volume_uuid or target
        self._songs = {s.path:s for s in native.songs} if native and available else {}
        self._songs_by_id = {s.id:s for s in native.songs} if native and available else {}
        self._display_rows.clear()
        self._playlist_items={}
        self.playlist_tree.clear()
        if not available:
            QTreeWidgetItem(self.playlist_tree, [notice or ('Playlists unavailable' if snapshot else 'Reading playlists…'),
                           '', snapshot.error if snapshot else ''])
        elif native:
            items = {}
            for playlist in native.playlists:
                item = QTreeWidgetItem([playlist.title, '' if playlist.folder else str(len(playlist.entries)),
                                       'Folder' if playlist.folder else 'USB'])
                item.setFlags(item.flags() & ~Qt.ItemIsSelectable & ~Qt.ItemIsDragEnabled
                              & ~Qt.ItemIsDropEnabled & ~Qt.ItemIsUserCheckable)
                if not playlist.folder:
                    item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                    item.setCheckState(0, Qt.Unchecked)
                items[playlist.id] = item
                item.setData(0, Qt.UserRole, playlist)
                if playlist.entries:
                    item.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
            self._playlist_items=items
            for playlist in native.playlists:
                parent = items.get(playlist.parent)
                if parent is not None and parent is not items[playlist.id]:
                    parent.addChild(items[playlist.id])
                else:
                    self.playlist_tree.addTopLevelItem(items[playlist.id])
            if not native.playlists:
                QTreeWidgetItem(self.playlist_tree, ['No rekordbox playlists', '', ''])
        elif not playlists:
            QTreeWidgetItem(self.playlist_tree, ['No M3U playlists in /Playlists', '', ''])
        for playlist in playlists:
            kind = Path(playlist.location).suffix.lstrip('.').upper()
            if playlist.skipped:
                kind += f' · {playlist.skipped} skipped'
            item = QTreeWidgetItem(self.playlist_tree, [playlist.title, str(len(playlist.entries)), kind])
            item.setFlags(item.flags() & ~Qt.ItemIsSelectable & ~Qt.ItemIsUserCheckable
                          & ~Qt.ItemIsDragEnabled & ~Qt.ItemIsDropEnabled)
            item.setToolTip(0, '/' + playlist.location)
            item.setData(0, Qt.UserRole, playlist)
            if playlist.entries:
                item.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)

        iterator = QTreeWidgetItemIterator(self.playlist_tree)
        while iterator.value():
            item = iterator.value()
            playlist=item.data(0,Qt.UserRole)
            if not isinstance(playlist,CdjPlaylist) or playlist.folder:
                item.setFlags(item.flags() & ~Qt.ItemIsSelectable)
            iterator += 1
        self._restore_view()
        self._restoring = False
        self._update_delete_playlist_button()

    def set_playlist_deletion_available(self,available,reason=''):
        self._delete_enabled=bool(available)
        self.delete_playlist_button.setToolTip(str(reason))
        self._update_delete_playlist_button()

    def _playlist_ids(self, selected):
        snapshot=self._snapshot
        native=getattr(snapshot,'native',None)
        if (not self._delete_enabled or native is None or getattr(snapshot,'error','') or getattr(snapshot,'notice','')
                or getattr(self._delete_profile,'kind','') not in {'generic_usb','ipod_usb'}
                or getattr(snapshot.facts,'writable',False) is not True):return ()
        rows={}
        for playlist in native.playlists:
            if playlist.id in rows:return ()
            rows[playlist.id]=playlist
        if not selected:return ()
        ids=[]
        for item in selected:
            playlist=item.data(0,Qt.UserRole)
            if (not isinstance(playlist,CdjPlaylist) or playlist.folder or str(playlist.id) in {'','0','root'}
                    or rows.get(playlist.id) is not playlist):return ()
            ids.append(str(playlist.id))
        return tuple(ids) if len(set(ids))==len(ids) else ()

    def _checked_playlist_ids(self):
        checked=[item for item in self._playlist_items.values()
                 if item.flags() & Qt.ItemIsUserCheckable and item.checkState(0)==Qt.Checked]
        return self._playlist_ids(checked)

    def _update_delete_playlist_button(self, *_args):
        ids=self._checked_playlist_ids()
        count=len(ids)
        self.delete_playlist_button.setText('Delete checked playlist…' if count==1 else
            f'Delete {count} checked playlists…' if count else 'Delete checked playlists…')
        self.delete_playlist_button.setEnabled(bool(ids))

    def _request_playlist_delete(self):
        ids=self._checked_playlist_ids()
        if ids:self.delete_requested.emit(list(ids))

    def _remember_checked(self, *_args):
        if self._restoring or not self._view_key:return
        state=self._states.get(self._view_key)
        if not isinstance(state,dict):
            self._remember_view()
            return
        state['checked']=[self._token(item) for item in self._playlist_items.values()
                          if item.flags() & Qt.ItemIsUserCheckable and item.checkState(0)==Qt.Checked]
        self.view_changed.emit()

    def _playlist_context_menu(self,pos):
        item=self.playlist_tree.itemAt(pos)
        playlist=item.data(0,Qt.UserRole) if item is not None else None
        if not isinstance(playlist,CdjPlaylist) or playlist.folder:return
        self.playlist_tree.setCurrentItem(item)
        checked=self._checked_playlist_ids()
        ids=checked if str(playlist.id) in checked else self._playlist_ids([item])
        if not ids:return
        snapshot=self._snapshot
        menu=QMenu(self);action=menu.addAction('Delete playlist' if len(ids)==1 else 'Delete playlists')
        def remove():
            if self._snapshot is not snapshot:return
            current=self._checked_playlist_ids() if str(playlist.id) in checked else self._playlist_ids([item])
            if current==ids:self.delete_requested.emit(list(ids))
        action.triggered.connect(remove)
        exec_transient_menu(menu,self.playlist_tree.mapToGlobal(pos))

    @staticmethod
    def _token(item):
        if item is None:
            return ''
        value = item.data(0,Qt.UserRole)
        if isinstance(value,CdjPlaylist):
            return 'playlist:'+value.id
        if isinstance(value,UsbPlaylist):
            return 'm3u:'+value.location
        if isinstance(value,CdjSong):
            parent = item.parent()
            return 'song:'+UsbDevicePages._token(parent)+':'+str(parent.indexOfChild(item) if parent else 0)+':'+value.path
        return ''

    def _remember_view(self, *_):
        if self._restoring or not self._view_key:
            return
        self._view_save_timer.stop()
        if getattr(self._snapshot, 'notice', ''):
            # The notice has no catalog rows. Retain the last real expansion
            # and selection while remembering the user's newly chosen tab.
            state = self._states.get(self._view_key)
            if isinstance(state, dict):
                state['page'] = self.stack.currentIndex()
                self.view_changed.emit()
            return
        expanded, checked = [], []
        iterator = QTreeWidgetItemIterator(self.playlist_tree)
        while iterator.value():
            item = iterator.value()
            if item.isExpanded():
                expanded.append(self._token(item))
            if item.flags() & Qt.ItemIsUserCheckable and item.checkState(0)==Qt.Checked:
                checked.append(self._token(item))
            iterator += 1
        self._states[self._view_key] = dict(page=self.stack.currentIndex(),expanded=expanded,checked=checked,
            selected=self._token(self.playlist_tree.currentItem()),
            scroll=self.playlist_tree.verticalScrollBar().value(),
            overview_scroll=self.overview_scroll.verticalScrollBar().value())
        self.view_changed.emit()

    def _remember_scroll(self, *_):
        """Capture positions cheaply; coalesce persistence across wheel ticks."""
        if self._restoring or not self._view_key:
            return
        state = self._states.get(self._view_key)
        if not isinstance(state, dict) or not {'page','expanded','selected'} <= state.keys():
            self._remember_view()
            return
        # Scrolling changes neither expansion nor selection. Full captures on
        # those signals, snapshot switches and shutdown retain their old path.
        state['scroll'] = self.playlist_tree.verticalScrollBar().value()
        state['overview_scroll'] = self.overview_scroll.verticalScrollBar().value()
        self._view_save_timer.start()

    def _restore_view(self):
        state = self._states.get(self._view_key,{})
        state = state if isinstance(state,dict) else {}
        key = self._view_key
        iterator = QTreeWidgetItemIterator(self.playlist_tree)
        while iterator.value():
            item = iterator.value()
            token = self._token(item)
            if token in state.get('expanded',[]):
                item.setExpanded(True)
            if token in state.get('checked',[]) and item.flags() & Qt.ItemIsUserCheckable:
                item.setCheckState(0,Qt.Checked)
            if token and token == state.get('selected'):
                self.playlist_tree.setCurrentItem(item)
            iterator += 1
        self.stack.setCurrentIndex(max(0,min(len(USB_TABS)-1,int(state.get('page',0)))))
        def scroll():
            if self._view_key==key:
                restoring = self._restoring
                self._restoring = True
                try:
                    self.playlist_tree.verticalScrollBar().setValue(int(state.get('scroll',0)))
                    self.overview_scroll.verticalScrollBar().setValue(int(state.get('overview_scroll',0)))
                finally:
                    self._restoring = restoring
        QTimer.singleShot(0,self,scroll)

    def _expand_playlist(self, item):
        playlist = item.data(0, Qt.UserRole)
        if not isinstance(playlist, (UsbPlaylist, CdjPlaylist)) or item.childCount():
            return
        for index,(title,path) in enumerate(playlist.entries):
            identity = playlist.song_ids[index] if isinstance(playlist,CdjPlaylist) and index<len(playlist.song_ids) else ''
            song = self._songs_by_id.get(identity) if identity else self._songs.get(path)
            song = song or CdjSong(path,path,title)
            seconds = max(0,song.duration)
            hours,remainder = divmod(seconds,3600)
            minutes,seconds = divmod(remainder,60)
            time = f'{hours}:{minutes:02d}:{seconds:02d}' if hours else f'{minutes}:{seconds:02d}'
            row = dict(id=song.id,title=song.title,artist=song.artist,bpm=f'{song.bpm/100:.2f}' if song.bpm else '',
                key=song.key,time=time if song.duration else '',format=PurePosixPath(song.path).suffix.lstrip('.').upper(),
                bitrate=str(song.bitrate) if song.bitrate else '',genre=song.genre,dateAdded=song.date_added,
                art='cover' if song.artwork else '')
            row.update(song.info)
            if self.row_enricher:
                self.row_enricher(song,row)
            self._display_rows[song.id] = row
            child = QTreeWidgetItem(item,[f'{index+1}. {row["title"]}', '', 'USB'])
            child.setData(0,Qt.UserRole,song)
            child.setFlags(child.flags() & ~Qt.ItemIsUserCheckable
                           & ~Qt.ItemIsDragEnabled & ~Qt.ItemIsDropEnabled)
            child.setToolTip(0, '\n'.join(filter(None,(str(row['title']),str(row['artist']),song.path))))

    def _activate_song(self, item, *_):
        from time import monotonic
        song = item.data(0,Qt.UserRole)
        if not isinstance(song,CdjSong):
            return
        now = monotonic()
        if self._last_activation and self._last_activation[0]==song.path and now-self._last_activation[1]<.1:
            return
        self._last_activation = (song.path,now)
        self.song_requested.emit(song)

    def _set_general(self, native):
        identity = None if native is None else (native.name, native.background_plus,
                    native.background_classic, native.display)
        if identity == self._general_identity and self.controls:
            return
        self._general_identity = identity
        while self.general_layout.count():
            item=self.general_layout.takeAt(0)
            if item.widget():item.widget().deleteLater()
        self.controls.clear()
        colors = ('Default', 'Pink', 'Red', 'Orange', 'Yellow', 'Green', 'Aqua', 'Blue', 'Purple')
        fields = [('device_name', 'Device Name', None, native.name if native else ''),
                  ('background_plus', 'Device Library Plus color', colors,
                   native.background_plus if native else None),
                  ('background_classic', 'Device Library color', colors,
                   native.background_classic if native else None)]
        display = dict(native.display) if native else {}
        labels = ('Waveform color', 'Current position', 'Overview waveform', 'Key display')
        for (key, (_, choices)), label in zip(DISPLAY_FIELDS.items(), labels):
            fields.append((key, label, tuple(c[0] for c in choices), display.get(key, 'Unavailable')))
        for at,(key, label, choices, value) in enumerate(fields):
            col = 0 if at<3 else 1
            row = at if at<3 else at-3
            host=QWidget();layout=QVBoxLayout(host);layout.setContentsMargins(0,0,0,0);layout.setSpacing(4)
            text=QLabel(label);text.setObjectName('SettingsSub');layout.addWidget(text)
            if choices is None:
                control = QLineEdit(str(value or ''))
                control.setMaxLength(128)
                control.textEdited.connect(self._settings_changed)
            else:
                control = QComboBox()
                control.addItems(choices)
                chosen = choices[value] if isinstance(value, int) and 0 <= value < len(choices) else value
                if chosen not in choices:
                    control.addItem(str(chosen or 'Unavailable'))
                control.setCurrentText(str(chosen or 'Unavailable'))
                control.currentTextChanged.connect(self._settings_changed)
                control.setEnabled(native is not None and chosen in choices)
            if native is None:
                control.setEnabled(False)
            control.setFixedHeight(28)
            self.controls[key] = control
            layout.addWidget(control)
            self.general_layout.addWidget(host,row,col)
        self.general_layout.setColumnStretch(0,1)
        self.general_layout.setColumnStretch(1,1)
        actions=QWidget();buttons=QHBoxLayout(actions);buttons.setContentsMargins(0,2,0,0);buttons.addStretch()
        self.apply_settings = QPushButton('Save to USB')
        self.apply_settings.setFixedHeight(28)
        self.apply_settings.setEnabled(False)
        self.apply_settings.clicked.connect(self._apply_settings)
        buttons.addWidget(self.apply_settings)
        self.general_layout.addWidget(actions,4,0,1,2)
        if native is None:
            self.general.setToolTip('Export a playlist to initialize native USB settings')
        else:
            self.general.setToolTip('')

    def _settings_changed(self, *_):
        self.apply_settings.setEnabled(bool(self._settings_changes()))

    def _settings_changes(self):
        snapshot = self._snapshot
        native = snapshot.native if isinstance(snapshot, UsbSnapshot) else None
        if native is None:
            return {}
        current = dict(native.display, device_name=native.name,
                       background_plus=native.background_plus, background_classic=native.background_classic)
        changes = {}
        for key, control in self.controls.items():
            if not control.isEnabled():
                continue
            value = control.text() if isinstance(control, QLineEdit) else control.currentText()
            if key.startswith('background_'):
                value = control.currentIndex()
            if value != current[key]:
                changes[key] = value
        return changes

    def _apply_settings(self):
        changes = self._settings_changes()
        if changes:
            self.settings_requested.emit(changes)
