"""Three first-launch pages, with joined read-only work before any app services."""
from pathlib import Path
import tempfile
import os
import sys

from PySide6.QtCore import QThread, QTimer, Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QPushButton,
    QRadioButton, QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget, QHeaderView,
)

from soundbox import onboarding, rekordbox_db
from soundbox.native_reader_process import NativeReaderProcess
from soundbox.rekordbox_metadata_store import load_authority
from .task_owner import TaskOwner



def configured_artwork():
    """Read packaged capabilities without importing/starting an online provider."""
    from soundbox.providers import profile_path, read_manifest
    path = (Path(sys._MEIPASS) / 'soundbox-build.json' if getattr(sys, 'frozen', False)
            else profile_path(os.environ.get('SOUNDBOX_PROFILE', 'personal')))
    try:
        manifest = read_manifest(path)
    except (OSError, ValueError):
        return False
    return any('artwork.search' in module['actions'] for module in manifest['modules'])


def read_preview(path):
    with tempfile.TemporaryDirectory(prefix='soundbox-setup-reader-') as root:
        reader = NativeReaderProcess(Path(root))
        try:
            return onboarding.preview(reader.read_preview(path))
        finally:
            reader.close()


def cleaning_status(root, library):
    authority = load_authority(Path(root) / 'rekordbox-metadata-authority.json',
                               library.source, library.source_library_id)
    if authority is None:
        return False, 'You can open this library. Cleanup needs a compatibility check before it can save changes.'
    return True, ''


class _PreviewWorker(QThread):
    def __init__(self, path, reader):
        super().__init__()
        self.path, self.reader = path, reader
        self.result, self.error = None, False

    def run(self):
        try:
            self.result = self.reader(self.path)
        except Exception:
            # User-facing setup never exposes native SQL, credentials or traces.
            self.error = True


class FirstLaunchDialog(QDialog):
    def __init__(self, root, *, reader=read_preview, status=cleaning_status,
                 candidate=None, commit=None, artwork_available=True, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Welcome to SoundBox')
        self.setMinimumSize(610, 520)
        self.root, self.reader, self.status_reader = Path(root), reader, status
        self.commit = commit
        self.artwork_available = artwork_available
        self.preview = None
        self._closed = False
        self._worker = None
        self._tasks = TaskOwner(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 24, 30, 24)
        layout.setSpacing(16)
        self.step = QLabel()
        self.step.setObjectName('SettingsSub')
        layout.addWidget(self.step)
        self.pages = QStackedWidget()
        layout.addWidget(self.pages, 1)
        self._library_page()
        self._cleanup_page()
        self._artwork_page()
        self.error = QLabel()
        self.error.setTextFormat(Qt.PlainText)
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        row = QHBoxLayout()
        self.cancel_button = QPushButton('Cancel')
        self.cancel_button.clicked.connect(self.reject)
        row.addWidget(self.cancel_button)
        row.addStretch()
        self.back_button = QPushButton('Back')
        self.back_button.clicked.connect(lambda: self._show_page(self.pages.currentIndex() - 1))
        row.addWidget(self.back_button)
        self.next_button = QPushButton('Next')
        self.next_button.setDefault(True)
        self.next_button.clicked.connect(self._next)
        row.addWidget(self.next_button)
        layout.addLayout(row)
        self._show_page(0)
        self.candidate = Path(candidate) if candidate is not None else rekordbox_db.REKORDBOX_DB
        if candidate is not None or self.candidate.is_file():
            QTimer.singleShot(0, self, lambda: self._read(self.candidate))

    def _page(self, title, description):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        heading = QLabel(title)
        font = heading.font(); font.setPointSize(font.pointSize() + 8); font.setBold(True)
        heading.setFont(font)
        layout.addWidget(heading)
        body = QLabel(description)
        body.setWordWrap(True)
        layout.addWidget(body)
        self.pages.addWidget(page)
        return layout

    def _library_page(self):
        layout = self._page('Your rekordbox library',
                            'SoundBox uses your library for music and playlists. Your audio files stay untouched.')
        self.library_status = QLabel('Let’s find your library.')
        self.library_status.setTextFormat(Qt.PlainText)
        self.library_status.setWordWrap(True)
        layout.addWidget(self.library_status)
        self.library_location = QLabel()
        self.library_location.setTextFormat(Qt.PlainText)
        self.library_location.setObjectName('SettingsSub')
        self.library_location.setWordWrap(True)
        layout.addWidget(self.library_location)
        self.choose_button = QPushButton('Choose library…')
        self.choose_button.clicked.connect(self._choose)
        layout.addWidget(self.choose_button, 0, Qt.AlignLeft)
        layout.addStretch()

    def _cleanup_page(self):
        layout = self._page('Tidy titles and artists?',
                            'Remove leftover file labels and tidy artist names. Changes save back to rekordbox.')
        self.keep_original = QRadioButton('Keep my titles and artists as they are')
        self.keep_original.setChecked(True)
        self.clean = QRadioButton('Clean titles and artists')
        layout.addWidget(self.keep_original)
        layout.addWidget(self.clean)
        self.clean.toggled.connect(self._show_examples)
        self.cleanup_note = QLabel()
        self.cleanup_note.setWordWrap(True)
        layout.addWidget(self.cleanup_note)
        self.examples = QTableWidget(0, 2)
        self.examples.setHorizontalHeaderLabels(['Before', 'After'])
        self.examples.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.examples.verticalHeader().hide()
        self.examples.setEditTriggers(QTableWidget.NoEditTriggers)
        self.examples.setSelectionMode(QTableWidget.NoSelection)
        self.examples.setWordWrap(True)
        self.examples.setMaximumHeight(215)
        layout.addWidget(self.examples)
        layout.addStretch()

    def _artwork_page(self):
        layout = self._page('Add cover art?' if self.artwork_available else 'Cover artwork',
                            'Find missing covers online. Artwork fills in the background while you use SoundBox.'
                            if self.artwork_available else
                            'Your existing artwork stays available. You can choose local cover images in SoundBox.')
        self.artwork = QCheckBox('Find cover art for my music')
        self.artwork.setChecked(False)
        self.artwork.setEnabled(self.artwork_available)
        self.artwork.setVisible(self.artwork_available)
        layout.addWidget(self.artwork)
        note = QLabel('This is separate from title cleanup. You can also choose or fetch artwork yourself later.'
                      if self.artwork_available else
                      'You can change a track’s artwork any time.')
        note.setWordWrap(True)
        note.setObjectName('SettingsSub')
        layout.addWidget(note)
        layout.addStretch()

    def _show_page(self, index):
        self.pages.setCurrentIndex(index)
        self.step.setText(('1 of 3 · Library', '2 of 3 · Cleanup', '3 of 3 · Artwork')[index])
        self.back_button.setVisible(index > 0)
        self.next_button.setText('Start SoundBox' if index == 2 else 'Next')
        self.next_button.setEnabled(self.preview is not None and self._worker is None)

    def _show_examples(self):
        self.examples.setVisible(self.clean.isChecked() and bool(self.preview and self.preview.examples))

    def _choose(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Choose your rekordbox library',
                                             str(self.candidate.parent), 'rekordbox library (master.db *.db)')
        if path:
            self._read(Path(path))

    def _read(self, path):
        if self._closed or self._worker is not None:
            return
        self.preview = None
        self.clean.setChecked(False)
        self.keep_original.setChecked(True)
        self.error.clear()
        self.candidate = Path(path)
        self.library_status.setText('Reading your library…')
        self.library_location.setText(str(path))
        self.choose_button.setEnabled(False)
        self.next_button.setEnabled(False)
        worker = _PreviewWorker(Path(path), self.reader)
        self._worker = worker

        def retired(captured):
            if self._worker is not captured:
                return
            self._worker = None
            if self._closed:
                return
            self.choose_button.setEnabled(True)
            if captured.error or captured.result is None:
                self.library_status.setText('Couldn’t read this library. Open rekordbox once, close it, then try choosing the library again.')
                return
            result = captured.result
            try:
                enabled, reason = self.status_reader(self.root, result.library)
            except Exception:
                enabled, reason = False, 'Cleanup isn’t available for this library yet.'
            self.preview = result
            self.library_status.setText(f'{result.library.track_count:,} tracks · {len(result.library.playlists):,} playlists')
            self.clean.setEnabled(enabled)
            self.cleanup_note.setText(reason if not enabled else
                'Close rekordbox while cleanup runs. Large libraries take a while; you can keep using SoundBox.')
            self.examples.setRowCount(len(result.examples))
            for row, example in enumerate(result.examples):
                for column, value in enumerate((
                    f'{example.before_title}\n{example.before_artist or "Unknown artist"}',
                    f'{example.after_title}\n{example.after_artist or "Unknown artist"}')):
                    self.examples.setItem(row, column, QTableWidgetItem(value))
                self.examples.setRowHeight(row, 60)
            self._show_examples()
            self.next_button.setEnabled(True)

        self._tasks.adopt(worker, retired)
        worker.start()

    def _next(self):
        if self.preview is None or self._worker is not None:
            return
        if self.pages.currentIndex() < 2:
            self._show_page(self.pages.currentIndex() + 1)
        else:
            if self.commit is not None:
                try:
                    self.commit(self.preview.library, self.clean.isChecked() and self.clean.isEnabled(),
                                self.artwork.isChecked() and self.artwork_available)
                except (OSError, ValueError):
                    self.error.setText('Couldn’t save your choices. Check free space and try again.')
                    return
            self.accept()

    def done(self, result):
        self._closed = True
        self._tasks.drain()
        super().done(result)


def run_first_launch(repository, previous, *, dialog_factory=FirstLaunchDialog):
    saved = []

    def commit(library, clean, artwork):
        updated = onboarding.setup_state(previous, library, clean=clean, artwork=artwork)
        repository.save(updated)
        saved[:] = [updated]

    dialog = dialog_factory(repository.path.parent, commit=commit,
                            artwork_available=configured_artwork())
    try:
        if dialog.exec() != QDialog.Accepted or dialog.preview is None:
            return None
        return saved[0] if saved else None
    finally:
        dialog.deleteLater()
