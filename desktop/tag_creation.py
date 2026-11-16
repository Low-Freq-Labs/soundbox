"""Creation forms capture choices; native publishers retain their own admission."""
from __future__ import annotations

from collections import Counter

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QVBoxLayout,
)

from .choices import ComboBox
from soundbox.rekordbox_write import WriteProofError


def _name(value):
    value = value.strip()
    if not value or len(value) > 255 or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError('Enter a name of 1–255 characters without control characters.')
    return value


def tag_deletion_effects(library, tag):
    """Presentation only; the writer independently checks fresh native rows."""
    from soundbox.rekordbox_tag_definitions import tag_rule_reference
    assigned = len({track.native_id for track in library.tracks_by_id.values()
                    if tag.tag_id in (track.source_tag_ids or ())})
    references, unknown = [], False
    catalog = library.source_playlist_catalog
    if not isinstance(catalog, dict) or not isinstance(catalog.get('nodes'), list):
        return assigned, (), True
    for row in catalog['nodes']:
        if row.get('rb_local_deleted') != 0 or row.get('Attribute') != 4:
            continue
        try:
            if tag_rule_reference(row.get('SmartList'), tag.tag_id):
                references.append(row.get('Name') or row['ID'])
        except WriteProofError:
            unknown = True
    return assigned, tuple(references), unknown


class DeleteTagDialog(QDialog):
    def __init__(self, tags, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Delete Tag')
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('Choose the My Tag to delete:', self))
        self.tag = ComboBox(self)
        labels = Counter((tag.category, tag.name) for tag in tags)
        for tag in tags:
            suffix = f' · {tag.category_id}' if labels[(tag.category, tag.name)] > 1 else ''
            self.tag.addItem(f'{tag.category} / {tag.name}{suffix}', tag)
        layout.addWidget(self.tag)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel, self)
        self.continue_button = buttons.addButton('Continue', QDialogButtonBox.AcceptRole)
        self.continue_button.setEnabled(bool(tags))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def value(self):
        tag = self.tag.currentData()
        if not tag or not all((tag.tag_id, tag.source_uuid, tag.category_id, tag.category_uuid)):
            raise ValueError('Refresh the library before deleting a tag.')
        return tag

    def accept(self):
        try:
            self.value()
        except ValueError:
            return
        super().accept()


class NewTagDialog(QDialog):
    def __init__(self, tags, categories, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Create New Tag')
        self.setMinimumWidth(360)
        self._tags = tuple(tags)
        self._categories = tuple(categories)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        layout.addLayout(form)
        self.name = QLineEdit(self)
        self.name.setPlaceholderText('Tag name')
        self.name.setMaxLength(255)
        form.addRow('Name', self.name)
        self.category = ComboBox(self)
        for index, category in enumerate(categories):
            repeated = sum(item.name == category.name for item in categories) > 1
            label = f'{category.name} · Column {index + 1}' if repeated else category.name
            self.category.addItem(label, category)
        form.addRow('Category', self.category)
        self.message = QLabel(self)
        self.message.setObjectName('Hint')
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel, self)
        self.create = buttons.addButton('Create', QDialogButtonBox.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.name.textChanged.connect(self._validate)
        self.category.currentIndexChanged.connect(self._validate)
        self._validate()

    def values(self):
        name = _name(self.name.text())
        if self.category.currentIndex() < 0:
            raise ValueError('No tag categories are available in this library.')
        category = self.category.currentData()
        if not category.category_id or not category.source_uuid:
            raise ValueError('Refresh the library before creating a tag.')
        unique_name = sum(item.name == category.name for item in self._categories) == 1
        if any((tag.category_id == category.category_id if tag.category_id else unique_name and tag.category == category.name)
               and tag.name.casefold() == name.casefold() for tag in self._tags):
            raise ValueError('A tag with that name already exists in this category.')
        return dict(name=name, category=category)

    def _validate(self):
        try:
            self.values()
        except ValueError as exc:
            self.create.setEnabled(False)
            self.message.setText(str(exc) if self.name.text().strip() else '')
        else:
            self.create.setEnabled(True)
            self.message.clear()

    def accept(self):
        self._validate()
        if self.create.isEnabled():
            super().accept()


def folder_choices(nodes):
    """Active folder IDs and full paths, excluding cycles or missing parents."""
    by_id = {node['ID']: node for node in nodes
             if node.get('rb_local_deleted') == 0 and node.get('Attribute') == 1}
    result = [('Playlists', 'root', '')]
    for identity, node in by_id.items():
        names, seen, current = [], set(), node
        while current:
            if current['ID'] in seen:
                break
            seen.add(current['ID'])
            names.append(current['Name'])
            if current['ParentID'] == 'root':
                result.append((' / '.join(reversed(names)), identity, node.get('UUID', '')))
                break
            current = by_id.get(current['ParentID'])
    return result


class _TagChoices(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._pressed_item = None

    def mousePressEvent(self, event):
        self._pressed_item = self.itemAt(event.position().toPoint()) if event.button() == Qt.LeftButton else None
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        item = self.itemAt(event.position().toPoint())
        pressed = self._pressed_item
        self._pressed_item = None
        before = item.checkState() if item is not None else None
        super().mouseReleaseEvent(event)
        # The delegate already toggles an indicator click. A label click should
        # toggle once too, while keyboard Space keeps Qt's normal behavior.
        if event.button() == Qt.LeftButton and item is not None and item is pressed and item.checkState() == before:
            item.setCheckState(Qt.Unchecked if before == Qt.Checked else Qt.Checked)


class NewSmartPlaylistDialog(QDialog):
    def __init__(self, tags, nodes, *, parent_id='root', parent=None, parent_reason=None):
        super().__init__(parent)
        self.setWindowTitle('Create Smart Playlist')
        self.resize(420, 480)
        self._tags = tuple(tags)
        self._parent_reason = parent_reason
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        layout.addLayout(form)
        self.name = QLineEdit(self)
        self.name.setPlaceholderText('Playlist name')
        self.name.setMaxLength(255)
        form.addRow('Name', self.name)
        self.folder = ComboBox(self)
        for label, identity, uuid in folder_choices(nodes):
            self.folder.addItem(label, (identity, uuid))
            if identity == parent_id:
                self.folder.setCurrentIndex(self.folder.count() - 1)
        form.addRow('Folder', self.folder)
        layout.addWidget(QLabel('Include songs with any of these My Tags:', self))
        self.tags = _TagChoices(self)
        self.tags.setObjectName('SmartTagChoices')
        names = Counter(tag.name for tag in tags)
        for tag in tags:
            label = tag.name if names[tag.name] == 1 else f'{tag.name} · {tag.category}'
            item = QListWidgetItem(label, self.tags)
            item.setData(Qt.UserRole, (tag.tag_id, tag.source_uuid))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
        layout.addWidget(self.tags, 1)
        self.message = QLabel(self)
        self.message.setObjectName('Hint')
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel, self)
        self.create = buttons.addButton('Create', QDialogButtonBox.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.name.textChanged.connect(self._validate)
        self.tags.itemChanged.connect(self._validate)
        self.folder.currentIndexChanged.connect(self._validate)
        self._validate()

    def values(self):
        name = _name(self.name.text())
        tags = tuple(self.tags.item(index).data(Qt.UserRole) for index in range(self.tags.count())
                     if self.tags.item(index).checkState() == Qt.Checked)
        if not tags:
            raise ValueError('Choose at least one tag.')
        if any(not identity or not uuid for identity, uuid in tags):
            raise ValueError('Refresh the library before creating a smart playlist.')
        folder = self.folder.currentData()
        if not folder or folder[0] != 'root' and not folder[1]:
            raise ValueError('Refresh the destination folder before creating a smart playlist.')
        if self._parent_reason:
            reason = self._parent_reason(folder[0])
            if reason:
                raise ValueError(reason)
        return dict(name=name, folder=folder, tags=tags, match='any')

    def _validate(self):
        try:
            self.values()
        except ValueError as exc:
            self.create.setEnabled(False)
            self.message.setText(str(exc) if self.name.text().strip() else '')
        else:
            self.create.setEnabled(True)
            self.message.clear()

    def accept(self):
        self._validate()
        if self.create.isEnabled():
            super().accept()


class TagCreationMixin:
    def _publish_created_library(self, library):
        seat = self._browser_seat() if self.stack.currentIndex() == 0 else None
        self._on_refresh_done(library)
        if seat is not None and self.stack.currentIndex() == 0:self._restore_browser_seat(seat)
        self._finish_tag_refresh()
        self._finish_edit_status()

    def _new_tag(self):
        from soundbox import rekordbox_tag_definitions as definitions
        controller = self.tag_definition_controller
        reason = controller.reason()
        if reason:
            self._flash_status(reason, 10)
            return False
        library = self.lib
        context = str(library.source), library.source_library_id
        dialog = NewTagDialog(tuple(library.source_tags), tuple(library.source_tag_categories), self)
        try:
            if dialog.exec() != QDialog.Accepted:
                return False
            if not self.lib or (str(self.lib.source), self.lib.source_library_id) != context:
                self._flash_status('The library changed; create the tag again', 10)
                return False
            values = dialog.values()
            category = values['category']
            request = definitions.request_for(context[1], category.category_id, category.source_uuid, values['name'])
            return controller.enqueue(request)
        except (ValueError, KeyError, WriteProofError) as exc:
            self._flash_status(str(exc), 10)
            return False
        finally:
            dialog.deleteLater()

    def _delete_tag(self):
        from soundbox import rekordbox_tag_definitions as definitions
        controller = self.tag_definition_controller
        reason = controller.deletion_reason()
        if reason:
            self._flash_status(reason, 10)
            return False
        library = self.lib
        context = str(library.source), library.source_library_id
        dialog = DeleteTagDialog(tuple(library.source_tags), self)
        try:
            if dialog.exec() != QDialog.Accepted:
                return False
            if not self.lib or (str(self.lib.source), self.lib.source_library_id) != context:
                self._flash_status('The library changed; choose the tag again', 10)
                return False
            tag = dialog.value()
            current = [item for item in self.lib.source_tags
                       if (item.tag_id, item.source_uuid, item.category_id, item.category_uuid, item.name)
                       == (tag.tag_id, tag.source_uuid, tag.category_id, tag.category_uuid, tag.name)]
            if len(current) != 1:
                raise ValueError('The selected tag changed; refresh the library before deleting it.')
            assigned, references, unknown = tag_deletion_effects(self.lib, tag)
            rule_note = ('Some intelligent-playlist references could not be counted. ' if unknown else '')
            if QMessageBox.question(self, 'Delete My Tag',
                    f'Delete “{tag.name}” from {tag.category}?\n\n'
                    f'Remove this tag from {assigned} song(s). {len(references)} intelligent playlist(s) reference it. '
                    + rule_note + 'Intelligent-playlist rules remain unchanged and may match differently. '
                    'Songs and audio files are preserved.',
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return False
            if not self.lib or (str(self.lib.source), self.lib.source_library_id) != context:
                raise ValueError('The library changed; choose the tag again.')
            request = definitions.deletion_request_for(context[1], tag.category_id, tag.category_uuid,
                                                       tag.tag_id, tag.source_uuid, tag.name)
            return controller.enqueue_deletion(request)
        except (ValueError, KeyError, WriteProofError) as exc:
            self._flash_status(str(exc), 10)
            return False
        finally:
            dialog.deleteLater()

    def _new_smart_playlist(self, parent='root'):
        from soundbox.rekordbox_playlist_store import request_for
        from soundbox.rekordbox_playlist_plan import smart_parent_reason
        controller = self.playlist_controller
        reason = controller.smart_reason()
        if reason:
            self._flash_status(reason, 10)
            return False
        library = self._native_presentation()
        context = str(library.source), library.source_library_id
        tags = tuple(library.source_tags)
        catalog = library.source_playlist_catalog
        dialog = NewSmartPlaylistDialog(tags, catalog['nodes'], parent_id=parent, parent=self,
            parent_reason=lambda identity: ('This folder is not supported for smart playlist creation yet. Choose another folder.'
                                           if smart_parent_reason(catalog['nodes'], identity) else ''))
        try:
            if dialog.exec() != QDialog.Accepted:
                return False
            if not self.lib or (str(self.lib.source), self.lib.source_library_id) != context:
                self._flash_status('The library changed; create the smart playlist again', 10)
                return False
            values = dialog.values()
            selected = [tag for tag in tags if (tag.tag_id, tag.source_uuid) in values['tags']]
            if len(selected) != len(values['tags']):
                self._flash_status('The selected tag identities are ambiguous; refresh the library', 10)
                return False
            request = request_for(catalog, 'create_smart', name=values['name'],
                                  parent=values['folder'][0], tags=selected, match='any')
            return controller.enqueue(request)
        except (ValueError, KeyError, WriteProofError) as exc:
            self._flash_status(str(exc), 10)
            return False
        finally:
            dialog.deleteLater()
