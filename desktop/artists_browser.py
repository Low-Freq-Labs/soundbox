"""Prepared artist groups over the desktop's existing track rows.

The window supplies derived rows when presentation changes. Opening a group
performs no metadata derivation or child construction. Updates reuse unaffected
items, so a finished song never reconstructs the complete Qt tree.
"""
from __future__ import annotations

from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem

from .model import ART_COL, COLUMNS, row_display_value, row_matches
from .trees import ARTIST_IDS_ROLE, ARTIST_ROLE, ARTIST_TRACK_ROLE


def artist_rows(rows: list[dict]) -> list[dict]:
    """Stable artist/title ordering, with Unknown Artist after named artists."""
    return sorted(rows, key=lambda row: (
        row['artist'] == 'Unknown Artist', row['artist'].casefold(),
        row['title'].casefold()))


def _identity(item):
    if item is None:
        return None
    track_id = item.data(0, ARTIST_TRACK_ROLE)
    return ('track', track_id) if track_id else ('artist', item.data(0, ARTIST_ROLE))


def _clean(row):
    return {key: value for key, value in row.items() if key != '_hay'}


class ArtistsBrowser:
    def __init__(self, view: QTreeWidget):
        self.view = view
        self.children: dict[str, list[dict]] = {}
        self._groups: dict[str, list[dict]] = {}
        self._headers: dict[str, QTreeWidgetItem] = {}
        self._tracks: dict[str, QTreeWidgetItem] = {}
        self._positions: dict[str, tuple[str, int]] = {}

    def _new_group(self, name, rows):
        item = QTreeWidgetItem([f'{name}  ({len(rows)})', *([''] * (len(COLUMNS) - 1))])
        item.setData(0, ARTIST_ROLE, name)
        item.setData(0, ARTIST_IDS_ROLE, [row['id'] for row in rows])
        children = []
        for row in rows:
            child = QTreeWidgetItem([
                str(row_display_value(row, key) or '') for key, _label, _hidden in COLUMNS])
            child.setData(0, ARTIST_TRACK_ROLE, row['id'])
            child.setFlags(child.flags() | Qt.ItemIsEditable)
            for at, (key, _label, _hidden) in enumerate(COLUMNS):
                if key == 'art':
                    child.setTextAlignment(at, Qt.AlignHCenter | Qt.AlignVCenter)
                elif key in {'bpm', 'time', 'bitrate'}:
                    child.setTextAlignment(at, Qt.AlignRight | Qt.AlignVCenter)
            self._tracks[row['id']] = child
            children.append(child)
        item.addChildren(children)
        return item

    def replace(self, rows: list[dict]) -> None:
        """Replace changed rows/groups while retaining the user's tree seat."""
        groups: dict[str, list[dict]] = {}
        for row in rows:
            groups.setdefault(row['artist'], []).append(_clean(row))
        if list(groups) == list(self._groups) and groups == self._groups:
            return
        view = self.view
        expanded = {name for name, item in self._headers.items() if item.isExpanded()}
        selected = {_identity(item) for item in view.selectedItems()}
        current = _identity(view.currentItem())
        column = view.currentColumn()
        scroll = view.verticalScrollBar().value()
        horizontal = view.horizontalScrollBar().value()
        blocked = view.blockSignals(True)
        updates = view.updatesEnabled()
        view.setUpdatesEnabled(False)
        try:
            changed = {name for name, old in self._groups.items()
                       if [row['id'] for row in old] != [row['id'] for row in groups.get(name, [])]}
            for name in changed:
                item = self._headers.pop(name)
                view.takeTopLevelItem(view.indexOfTopLevelItem(item))
                for row in self._groups[name]:
                    self._tracks.pop(row['id'], None)
            for index, (name, tracks) in enumerate(groups.items()):
                item = self._headers.get(name)
                if item is None:
                    item = self._new_group(name, tracks)
                    self._headers[name] = item
                    view.insertTopLevelItem(index, item)
                else:
                    # Most updates change just a BPM/key/art cell. Retain every
                    # existing item and edit only that song's changed text.
                    for old, row in zip(self._groups[name], tracks):
                        if old != row:
                            child = self._tracks[row['id']]
                            for at, (key, _label, _hidden) in enumerate(COLUMNS):
                                value = str(row_display_value(row, key) or '')
                                if child.text(at) != value:
                                    child.setText(at, value)
                    old_index = view.indexOfTopLevelItem(item)
                    if old_index != index:
                        view.takeTopLevelItem(old_index)
                        view.insertTopLevelItem(index, item)
                if (name in expanded
                        or any(('track', row['id']) in selected or ('track', row['id']) == current
                               for row in tracks)):
                    item.setExpanded(True)
            self._groups = groups
            self._positions = {row['id']: (name, at) for name, tracks in groups.items()
                               for at, row in enumerate(tracks)}
            # Search may add its disposable haystack to presentation rows;
            # keep the exact comparison snapshot separate from that cache.
            self.children = {name: [dict(row) for row in tracks] for name, tracks in groups.items()}
            def find(token):
                if token is None:
                    return None
                return (self._tracks if token[0] == 'track' else self._headers).get(token[1])
            current_item = find(current)
            view.setCurrentItem(current_item, max(0, column), QItemSelectionModel.NoUpdate)
            for token in selected:
                item = find(token)
                if item is not None:
                    item.setSelected(True)
            view.doItemsLayout()
            view.verticalScrollBar().setValue(scroll)
            view.horizontalScrollBar().setValue(horizontal)
        finally:
            view.setUpdatesEnabled(updates)
            view.blockSignals(blocked)

    def reset_navigation(self) -> None:
        """A fresh visit starts collapsed, matching the existing Artists view."""
        self.view.collapseAll()
        self.view.clearSelection()
        self.view.setCurrentItem(None)
        self.view.verticalScrollBar().setValue(0)
        self.view.horizontalScrollBar().setValue(0)

    def update_rows(self, rows: list[dict]) -> None:
        """Refresh fields of songs whose artist/title and order are unchanged."""
        blocked = self.view.blockSignals(True)
        try:
            for row in rows:
                name, at = self._positions[row['id']]
                item = self._tracks[row['id']]
                for column, (key, _label, _hidden) in enumerate(COLUMNS):
                    value = str(row_display_value(row, key) or '')
                    if item.text(column) != value:
                        item.setText(column, value)
                self._groups[name][at] = _clean(row)
                self.children[name][at] = dict(row)
        finally:
            self.view.blockSignals(blocked)

    def refresh_art(self, kinds: dict[str, str]) -> dict[str, str]:
        """Apply cheap artwork status without deriving any track metadata."""
        changed = {}
        blocked = self.view.blockSignals(True)
        try:
            for name, tracks in self._groups.items():
                for at, row in enumerate(tracks):
                    kind = kinds.get(row['id'], row.get('art', ''))
                    if kind == row.get('art', ''):
                        continue
                    row['art'] = kind
                    self.children[name][at]['art'] = kind
                    self._tracks[row['id']].setText(ART_COL, str(row_display_value(row, 'art') or ''))
                    changed[row['id']] = kind
        finally:
            self.view.blockSignals(blocked)
        return changed

    def filter(self, terms: list[str]) -> None:
        for name, item in self._headers.items():
            visible=False
            for row in self.children.get(name, ()):
                matches=not terms or row_matches(row,terms)
                self._tracks[row['id']].setHidden(not matches)
                visible=visible or matches
            item.setHidden(bool(terms) and not visible)

    def filter_children(self, item: QTreeWidgetItem, terms: list[str]) -> None:
        for row in self.children.get(item.data(0, ARTIST_ROLE), ()):
            self._tracks[row['id']].setHidden(bool(terms) and not row_matches(row,terms))
