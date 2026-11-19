"""Own unsaved inline text across publication without granting edit authority."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from functools import wraps
import weakref

from PySide6.QtCore import QEvent, QModelIndex, QObject, QPersistentModelIndex, Qt, Slot
from PySide6.QtWidgets import QAbstractItemDelegate, QApplication, QLineEdit, QWidget
from shiboken6 import isValid


@dataclass(frozen=True)
class Target:
    source: tuple
    local_id: str
    native_id: str
    uuid: str
    paths: tuple
    field: str
    occurrence: tuple


def target_for(window, view, index, *, ordinal=None):
    """Bind the song and occurrence independently of its current row number."""
    from .model import COLUMNS
    from .trees import ARTIST_TRACK_ROLE
    library = getattr(window, 'lib', None)
    if library is None or not index.isValid() or index.column() not in (0, 1):
        return None
    if view is getattr(window, 'table', None) and index.model() is window.model:
        if index.row() >= len(window.model._rows):
            return None
        row = window.model._rows[index.row()]
        identity = row.get('id')
        occurrence = window._native_row_identity(row)
        if occurrence[0] == 'track':
            if ordinal is None:
                ordinal = sum(r.get('id') == identity for r in window.model._rows[:index.row()])
            occurrence = (*occurrence, ordinal)
    elif view is getattr(window, 'artists_view', None) and index.model() is view.model():
        identity = index.siblingAtColumn(0).data(ARTIST_TRACK_ROLE)
        occurrence = ('artist-track', identity)
    else:
        return None
    track = library.tracks_by_id.get(identity)
    if track is None:
        return None
    return Target((str(library.source), library.source_kind, library.source_library_id),
                  track.track_id, track.native_id, track.source_uuid,
                  (str(track.path), str(track.native_path), track.source_native_path),
                  COLUMNS[index.column()][0], tuple(occurrence))


def _context(window):
    return (getattr(window, '_view_key', None),
            window.stack.currentIndex(), window.body_stack.currentIndex())


def _seat(window, view):
    selection = view.selectionModel()
    ordinals = {}
    if view is window.table:
        counts = {}
        for at, row in enumerate(window.model._rows):
            identity = row.get('id')
            ordinals[at] = counts.get(identity, 0)
            counts[identity] = ordinals[at] + 1
    return (target_for(window, view, view.currentIndex()),
            frozenset(target_for(window, view, index, ordinal=ordinals.get(index.row()))
                      for index in selection.selectedRows()))


@dataclass
class Draft:
    target: Target
    text: str
    cursor: int
    selection_start: int
    selection_length: int
    modified: bool
    caption: str
    revision: tuple

    def apply(self, editor):
        editor.setText(self.text)
        if self.selection_start >= 0 and self.selection_length:
            start, length = self.selection_start, self.selection_length
            if self.cursor == start:
                start, length = start + length, -length
            editor.setSelection(start, length)
        else:
            editor.setCursorPosition(self.cursor)
        editor.setModified(self.modified)


@dataclass
class Session:
    editor: weakref.ReferenceType
    view: object
    index: QPersistentModelIndex
    target: Target
    sequence: int
    caption: str
    seeded: bool = False
    ended: bool = False
    accepted: bool | None = None
    expected_value: str = ''
    focus_out_reason: object = None
    revision: int = 0


class InlineDrafts(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.sessions = {}
        self.retained: dict[Target, Draft] = {}
        self.sequence = 0
        self.gesture = 0
        self.depth = 0
        self.committing = None
        self.focus_revision = 0
        self.fallback_focus = None
        self.captured_sessions = ()
        self.inactive = False
        QApplication.instance().focusChanged.connect(self.focus_changed)
        window.destroyed.connect(self.detach)

    @Slot()
    def detach(self):
        application = QApplication.instance()
        if application is not None and isValid(application):
            application.focusChanged.disconnect(self.focus_changed)
        self.sessions.clear()
        self.retained.clear()
        self.captured_sessions = ()
        self.fallback_focus = None
        self.window = None

    @Slot(QWidget, QWidget)
    def focus_changed(self, previous, current):
        if not self.depth:
            return
        # Qt moves focus to the next toolbar control when a removed row takes
        # its editor away. That automatic fallback is not a new user choice.
        for session in self.captured_sessions:
            editor = session.editor()
            if (previous is editor and session.focus_out_reason in (Qt.TabFocusReason, Qt.BacktabFocusReason)
                    and (not session.index.isValid() or not editor.isVisible())):
                self.fallback_focus = current
                return
        self.focus_revision += 1

    def focus_out(self, editor, reason):
        session = self.sessions.get(id(editor))
        if session is None:
            session = next((value for value in self.captured_sessions if value.editor() is editor), None)
        if session is not None:
            session.focus_out_reason = reason

    def eventFilter(self, editor, event):
        if event.type() == QEvent.FocusOut:
            self.focus_out(editor, event.reason())
        return False

    def opened(self, editor, view, index, target):
        self.sequence += 1
        session = Session(weakref.ref(editor), view, QPersistentModelIndex(index), target,
                          self.sequence, str(index.siblingAtColumn(0).data(Qt.DisplayRole) or target.local_id))
        self.sessions[id(editor)] = session
        editor.installEventFilter(self)
        editor.textEdited.connect(lambda _text, value=session: setattr(value, 'revision', value.revision + 1))
        return session

    def matches(self, session, index=None):
        if index is None:
            index = QModelIndex(session.index)
        return target_for(self.window, session.view, index) == session.target

    def seed(self, editor, index):
        session = self.sessions.get(id(editor))
        if session is None:
            return True
        if session.seeded and editor.isModified():
            return False
        draft = self.retained.get(session.target)
        if not session.seeded and draft is not None and self.matches(session, index):
            draft.apply(editor)
            session.seeded = True
            return False
        session.seeded = True
        return True

    @staticmethod
    def capture(session):
        editor = session.editor()
        if editor is None or not isValid(editor) or not editor.isModified():
            return None
        return Draft(session.target, editor.text(), editor.cursorPosition(), editor.selectionStart(),
                     editor.selectionLength(), editor.isModified(), session.caption,
                     (session.sequence, session.revision))

    def keep(self, draft):
        previous = self.retained.get(draft.target) if draft is not None else None
        if draft is not None and (previous is None or previous.revision <= draft.revision):
            self.retained[draft.target] = draft

    def retire(self, session):
        previous = self.retained.get(session.target)
        if previous is not None and previous.revision <= (session.sequence, session.revision):
            self.retained.pop(session.target)

    def cancelled(self, editor):
        session = self.sessions.get(id(editor))
        if session is not None:
            session.ended = True
            self.retire(session)

    def closed(self, editor):
        session = self.sessions.pop(id(editor), None)
        if session is not None and not session.ended:
            self.keep(self.capture(session))

    @contextmanager
    def commit(self, editor, index):
        session = self.sessions.get(id(editor))
        if session is None:
            yield True
            return
        if not self.matches(session, index):
            self.keep(self.capture(session))
            session.ended = True
            self.window._flash_status('The song changed; your unsaved edit was kept', 10)
            yield False
            return
        session.expected_value = editor.text().strip()
        attempt = self.capture(session)
        session.ended = True
        previous, self.committing = self.committing, session
        try:
            yield True
        finally:
            self.committing = previous
            if session.accepted:
                self.retire(session)
            else:
                self.keep(attempt)

    def result(self, field, identity, value, accepted):
        from .model import _EDIT_MAX
        session = self.committing
        if (session is not None and (field, identity) == (session.target.field, session.target.local_id)
                and value == session.expected_value[:_EDIT_MAX[field]].rstrip()
                and self.same_song(session.target)):
            session.accepted = bool(accepted)

    def same_song(self, target):
        library = self.window.lib
        if library is None or (str(library.source), library.source_kind, library.source_library_id) != target.source:
            return False
        track = library.tracks_by_id.get(target.local_id)
        return bool(track and (track.native_id, track.source_uuid,
                    (str(track.path), str(track.native_path), track.source_native_path))
                    == (target.native_id, target.uuid, target.paths))

    def find(self, view, target):
        if view is self.window.table:
            for row, value in enumerate(self.window.model._rows):
                if value.get('id') == target.local_id:
                    index = self.window.model.index(row, 0 if target.field == 'title' else 1)
                    if target_for(self.window, view, index) == target and not view.isRowHidden(row):
                        return index
        else:
            item = self.window._artists_browser._tracks.get(target.local_id)
            if item is not None:
                parent = item
                while parent is not None:
                    if parent.isHidden():
                        return QModelIndex()
                    parent = parent.parent()
                index = view.indexFromItem(item, 0 if target.field == 'title' else 1)
                if target_for(self.window, view, index) == target:
                    return index
        return QModelIndex()

    @contextmanager
    def preserve(self):
        if self.depth:
            yield
            return
        captured = []
        for session in tuple(self.sessions.values()):
            draft = self.capture(session)
            if draft is not None and not session.ended:
                editor = session.editor()
                captured.append((session, editor, draft, _context(self.window), _seat(self.window, session.view),
                                 editor.hasFocus(), self.sequence, self.gesture, self.focus_revision))
        self.captured_sessions = tuple(value[0] for value in captured)
        self.fallback_focus = None
        self.depth += 1
        try:
            yield
        finally:
            self.depth -= 1
            for session, editor, draft, context, seat, focused, sequence, gesture, focus_revision in captured:
                if session.ended:
                    continue
                if editor is not None and isValid(editor) and self.matches(session):
                    continue
                self.keep(draft)
                if editor is not None and isValid(editor) and id(editor) in self.sessions:
                    session.ended = True
                    session.view.itemDelegate().closeEditor.emit(editor, QAbstractItemDelegate.NoHint)
                index = self.find(session.view, draft.target)
                if not index.isValid():
                    self.window._flash_status('Unsaved edit kept in the Unsaved edits menu', 10)
                focus = QApplication.focusWidget()
                if (not focused or not index.isValid() or self.sequence != sequence or self.gesture != gesture
                        or session.revision != draft.revision[1]
                        or self.inactive or QApplication.activeModalWidget() is not None
                        or QApplication.activePopupWidget() is not None
                        or self.focus_revision != focus_revision
                        or _context(self.window) != context or _seat(self.window, session.view) != seat
                        or focus not in (None, editor, session.view, session.view.viewport(),
                                         self.window, self.fallback_focus)):
                    continue
                # Retained text seeds the replacement editor through the same delegate.
                session.view.edit(index)
                for restored in self.sessions.values():
                    if restored.sequence > sequence and restored.target == draft.target:
                        replacement = restored.editor()
                        if replacement is not None and isValid(replacement):
                            draft.apply(replacement)
                        break
            self.captured_sessions = ()
            self.fallback_focus = None


def owner_for(view, index, editor):
    window = view.window()
    target = target_for(window, view, index)
    if target is None or not isinstance(editor, QLineEdit):
        return None
    owner = vars(window).get('_inline_drafts')
    if owner is None:
        owner = window._inline_drafts = InlineDrafts(window)
    owner.opened(editor, view, index, target)
    return owner


def preserve_inline_drafts(method):
    @wraps(method)
    def preserving(window, *args, **kwargs):
        owner = vars(window).get('_inline_drafts')
        if owner is None or not owner.sessions:
            return method(window, *args, **kwargs)
        with owner.preserve():
            return method(window, *args, **kwargs)
    return preserving


def observe_inline_commit(method):
    @wraps(method)
    def observing(window, field, identity, value):
        result = method(window, field, identity, value)
        owner = vars(window).get('_inline_drafts')
        if owner is not None:
            owner.result(field, identity, value, result)
        return result
    return observing


def add_recovery_actions(window, menu):
    owner = vars(window).get('_inline_drafts')
    if owner is None or not owner.retained:
        return
    recovery = menu.addMenu('Unsaved edits')
    for draft in tuple(owner.retained.values()):
        recovery.addAction(f'Copy {draft.target.field} for “{draft.caption}”',
                           lambda value=draft.text: QApplication.clipboard().setText(value))
