"""SoundBox folder menus and local state changes, independent of source writers."""
from uuid import uuid4

from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QInputDialog, QMessageBox, QTreeWidgetItem
from .choices import Menu as QMenu

from soundbox import playlist_folders as folders
from .trees import CUSTOM_ROLE, CUSTOM_FOLDER_ROLE, NATIVE_TREE_ROLE


class PlaylistFoldersMixin:
    def _local_playlist_tree(self):
        return folders.reconcile(self.custom, self.settings.get('custom_playlist_tree'))

    def _local_parent(self, item=None):
        item = item or self.sb_tree.currentItem()
        if item is None:return ''
        if item.data(0, CUSTOM_FOLDER_ROLE):return item.data(0, CUSTOM_FOLDER_ROLE)
        return item.parent().data(0, CUSTOM_FOLDER_ROLE) if item.parent() else ''

    def _commit_local_tree(self, nodes, custom=None, *, select=None, icon_rename=None):
        previous = self.custom
        existed = 'custom_playlist_tree' in self.settings
        old_tree = self.settings.get('custom_playlist_tree')
        old_expanded = self.settings.get('custom_playlist_expanded')
        old_icons=self.settings.get('playlist_icons');had_icons='playlist_icons' in self.settings
        members = previous if custom is None else custom
        nodes = folders.reconcile(members, nodes)
        self.custom = {n['playlist']:members[n['playlist']] for n, _ in folders.walk(nodes) if 'playlist' in n}
        self.settings['custom_playlist_tree'] = nodes
        icon_key=getattr(self,'_local_playlist_icon_key',None)
        if isinstance(old_icons,dict) and icon_key:
            icons=dict(old_icons)
            if icon_rename:
                old,new=map(icon_key,icon_rename)
                if old in icons:icons[new]=icons[old]
            for name in previous.keys()-members.keys():icons.pop(icon_key(name),None)
            self.settings['playlist_icons']=icons
        if old_expanded is not None:
            live = {n['folder'] for n, _ in folders.walk(nodes) if 'folder' in n}
            self.settings['custom_playlist_expanded'] = [key for key in old_expanded if key in live]
        if not self._save_state():
            self.custom = previous
            if existed:self.settings['custom_playlist_tree'] = old_tree
            else:self.settings.pop('custom_playlist_tree', None)
            if old_expanded is not None:self.settings['custom_playlist_expanded'] = old_expanded
            if had_icons:self.settings['playlist_icons']=old_icons
            else:self.settings.pop('playlist_icons',None)
            return False
        self._refresh_custom()
        if select:
            for item in self.sb_tree.findItems('', Qt.MatchContains | Qt.MatchRecursive):
                if item.data(0, select[0]) == select[1]:
                    parent = item.parent()
                    while parent:
                        parent.setExpanded(True); parent = parent.parent()
                    self.sb_tree.setCurrentItem(item); break
        return True

    def _populate_custom_folders(self, keep_name, keep_folder):
        expanded = set(self.settings.get('custom_playlist_expanded', []))
        root_index=next((i for i in range(self.sb_tree.topLevelItemCount())
                         if self.sb_tree.topLevelItem(i).data(0,NATIVE_TREE_ROLE)),self.sb_tree.topLevelItemCount())
        def add(nodes, parent=None):
            nonlocal root_index
            for node in nodes:
                if 'folder' in node:
                    item = QTreeWidgetItem([node['name']])
                    item.setData(0, CUSTOM_FOLDER_ROLE, node['folder'])
                    item.setFlags(item.flags() & ~Qt.ItemIsSelectable)
                    item.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
                else:
                    key = node['playlist']
                    item = QTreeWidgetItem([f'★  {key}  ({len(self.custom[key])})'])
                    item.setData(0, CUSTOM_ROLE, key)
                if parent:parent.addChild(item)
                else:
                    self.sb_tree.insertTopLevelItem(root_index,item);root_index+=1
                if 'folder' in node:
                    add(node['children'], item); item.setExpanded(node['folder'] in expanded)
                if (item.data(0, CUSTOM_ROLE) == keep_name and keep_name is not None
                        or item.data(0, CUSTOM_FOLDER_ROLE) == keep_folder and keep_folder is not None):
                    self.sb_tree.setCurrentItem(item)
        add(self._local_playlist_tree())

    def _local_folder_expanded(self, item, expanded):
        key = item.data(0, CUSTOM_FOLDER_ROLE)
        if not key:return
        values = set(self.settings.get('custom_playlist_expanded', []))
        if expanded:values.add(key)
        else:values.discard(key)
        self.settings['custom_playlist_expanded'] = sorted(values)
        self._schedule_settings_save()

    def _new_local_playlist(self, parent=None):
        if self.lib is None:
            QMessageBox.information(self, 'Open a library first', 'Load your music library first.'); return
        parent = self._local_parent() if parent is None else parent
        value, ok = QInputDialog.getText(self, 'New SoundBox Playlist', 'Name:')
        value = value.strip()
        if not ok or not value:return
        try:
            folders.name(value)
            if self._custom_name_taken(value):raise ValueError('That playlist name is already used')
            nodes = self._local_playlist_tree(); folders.children(nodes, parent).append({'playlist':value})
            self._commit_local_tree(nodes, {**self.custom, value:[]}, select=(CUSTOM_ROLE, value))
        except (ValueError, KeyError) as exc:QMessageBox.information(self, 'Could not create playlist', str(exc))

    def _new_local_folder(self, parent=None):
        parent = self._local_parent() if parent is None else parent
        value, ok = QInputDialog.getText(self, 'New SoundBox Folder', 'Name:')
        value = value.strip()
        if not ok or not value:return
        try:
            nodes = self._local_playlist_tree(); key = str(uuid4())
            folders.children(nodes, parent).append(dict(folder=key, name=value, children=[]))
            folders.validate(nodes)
            self._commit_local_tree(nodes, select=(CUSTOM_FOLDER_ROLE, key))
        except (ValueError, KeyError) as exc:QMessageBox.information(self, 'Could not create folder', str(exc))

    def _rename_local_node(self, kind, key, value=None):
        nodes = self._local_playlist_tree()
        try:_siblings, _index, node = folders.locate(nodes, kind, key)
        except KeyError:
            self._flash_status('That '+kind+' has changed; select it again');return
        old = node['name'] if kind == 'folder' else key
        if value is None:
            role=CUSTOM_FOLDER_ROLE if kind=='folder' else CUSTOM_ROLE
            item=next((i for i in self.sb_tree.findItems('',Qt.MatchContains|Qt.MatchRecursive)
                       if i.data(0,role)==key),None)
            return self.sb_tree.rename_inline(item,old,lambda text:self._rename_local_node(kind,key,text))
        value = value.strip()
        if not value or value == old:return
        try:
            folders.name(value); custom = self.custom
            if kind == 'folder':node['name'] = value
            else:
                if self._custom_name_taken(value):raise ValueError('That playlist name is already used')
                node['playlist'] = value
                custom = {value if name == key else name:ids for name, ids in self.custom.items()}
            folders.validate(nodes)
            self._commit_local_tree(nodes, custom, select=(CUSTOM_FOLDER_ROLE if kind == 'folder' else CUSTOM_ROLE,
                                                         key if kind == 'folder' else value),
                                    icon_rename=(key,value) if kind=='playlist' else None)
        except ValueError as exc:QMessageBox.information(self, 'Could not rename '+kind, str(exc))

    def _delete_local_node(self, kind, key):
        nodes = self._local_playlist_tree(); siblings, index, node = folders.locate(nodes, kind, key)
        names = [n['playlist'] for n, _ in folders.walk([node]) if 'playlist' in n]
        label = node['name'] if kind == 'folder' else key
        detail = f' and its {len(names)} playlist(s)' if kind == 'folder' and names else ''
        if QMessageBox.question(self, 'Delete '+kind,
                f'Delete SoundBox {kind} “{label}”{detail}?\nSongs remain in Collection and audio files are preserved.',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:return
        siblings.pop(index)
        if self._commit_local_tree(nodes, {name:ids for name, ids in self.custom.items() if name not in names}):
            self.model.set_rows([])

    def _move_local_node(self, token, parent, index):
        kind, _, key = token.partition(':')
        if kind not in ('playlist', 'folder'):return
        try:
            nodes = folders.move(self._local_playlist_tree(), kind, key, parent, index)
            self._commit_local_tree(nodes, select=(CUSTOM_FOLDER_ROLE if kind == 'folder' else CUSTOM_ROLE, key))
        except (ValueError, KeyError) as exc:self._flash_status(str(exc))

    def _new_soundbox_item_menu(self):
        menu = QMenu(self); parent = self._local_parent()
        menu.addAction('New Playlist', lambda:self._new_local_playlist(parent))
        menu.addAction('New Folder', lambda:self._new_local_folder(parent))
        menu.exec(QCursor.pos())

    def _soundbox_context_menu(self, pos):
        icon=self.sb_tree.playlist_icon_item_at(pos)
        chooser=getattr(self,'_playlist_icon_menu',None)
        menu=chooser(self.sb_tree,icon) if chooser and icon is not None else None
        if menu is not None:
            menu.exec(self.sb_tree.viewport().mapToGlobal(pos));return
        item = self.sb_tree.itemAt(pos)
        menu = self._local_context_menu(item)
        menu.exec(self.sb_tree.viewport().mapToGlobal(pos))

    def _local_context_menu(self, item):
        parent = self._local_parent(item) if item else ''
        key = item.data(0, CUSTOM_FOLDER_ROLE) if item else None
        kind = 'folder' if key else 'playlist'
        key = key or (item.data(0, CUSTOM_ROLE) if item else None)
        menu = QMenu(self)
        menu.addAction('New Playlist', lambda:self._new_local_playlist(parent))
        menu.addAction('New Folder', lambda:self._new_local_folder(parent))
        rename = menu.addAction('Rename', lambda:self._rename_local_node(kind, key)); rename.setEnabled(bool(key))
        delete = menu.addAction('Delete', lambda:self._delete_local_node(kind, key)); delete.setEnabled(bool(key))
        return menu
