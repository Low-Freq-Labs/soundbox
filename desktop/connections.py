"""Settings connection controls. Core owns all worker lifetimes and UI delivery."""
from __future__ import annotations

from threading import Event

from PySide6.QtCore import QStandardPaths, QThread, QTimer, QUrl, Signal, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QVBoxLayout, QWidget,
)

from soundbox.providers import ConnectionFailure
from .task_owner import TaskOwner


class RekordboxSection(QWidget):
    """Core source controls; showing this card never probes a database."""
    def __init__(self, choose, default, xml, refresh, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        heading = QLabel("REKORDBOX")
        heading.setObjectName("SettingsSection")
        layout.addWidget(heading)
        text = QLabel("Read-only library snapshots. Your rekordbox database and source audio are never changed.")
        text.setWordWrap(True)
        layout.addWidget(text)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.path = QLabel()
        self.source = QLabel()
        for label in (self.path, self.source):
            label.setObjectName("SettingsSub")
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            layout.addWidget(label)
        self.controls = []
        buttons = QHBoxLayout()
        for title, callback in (("Choose database…", choose), ("Default location", default),
                                ("Open XML…", xml), ("Refresh", refresh)):
            button = QPushButton(title)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(callback)
            self.controls.append(button)
            buttons.addWidget(button)
        buttons.addStretch()
        layout.addLayout(buttons)

    def set_state(self, status, database_path, source, *, busy=False, custom=False):
        self.status.setText(status)
        self.path.setText("Database: " + database_path)
        self.source.setText("Loaded source: " + source if source else "No source loaded")
        for button in self.controls:
            button.setEnabled(not busy)
        self.controls[1].setEnabled(not busy and custom)


class CredentialsDialog(QDialog):
    def __init__(self, client_id: str = "", secret: str = "", profile: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Connect SoundCloud")
        self.setMinimumWidth(490)
        layout = QVBoxLayout(self)
        text = QLabel("Read your public likes for cover-art matching. Keep your existing SoundCloud app and website redirect URL.")
        text.setWordWrap(True)
        layout.addWidget(text)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.client_id = QLineEdit(client_id)
        self.client_id.setObjectName("SoundCloudClientID")
        self.client_id.setPlaceholderText("Client ID")
        self.secret = QLineEdit(secret)
        self.secret.setObjectName("SoundCloudClientSecret")
        self.secret.setPlaceholderText("Client Secret")
        self.secret.setEchoMode(QLineEdit.Password)
        form.addRow("Client ID", self.client_id)
        form.addRow("Client Secret", self.secret)
        self.profile = QLineEdit(profile)
        self.profile.setObjectName("SoundCloudProfile")
        self.profile.setPlaceholderText("https://soundcloud.com/your-name")
        self.profile.setMinimumWidth(320)
        form.addRow("Profile URL", self.profile)
        layout.addLayout(form)
        note = QLabel("Credentials are saved in macOS Keychain. No browser sign-in or redirect change is needed. Only public liked tracks are included.")
        note.setWordWrap(True)
        note.setObjectName("SettingsSub")
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.save_button = buttons.button(QDialogButtonBox.Save)
        self.client_id.textChanged.connect(self._validate)
        self.secret.textChanged.connect(self._validate)
        self.profile.textChanged.connect(self._validate)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._validate()

    def _validate(self):
        self.save_button.setEnabled(bool(self.client_id.text().strip() and self.secret.text().strip()
                                         and self.profile.text().strip()))


class _ConnectionJob(QThread):
    progress = Signal(str)
    browser = Signal(str)

    def __init__(self, operation):
        super().__init__()
        self.operation = operation
        self.cancelled = Event()
        self.result = None
        self.error = ""

    def stop(self):
        self.cancelled.set()

    def run(self):
        try:
            self.result = self.operation(self.cancelled, self.progress.emit, self.browser.emit)
        except ConnectionFailure as exc:
            self.error = str(exc)
        except Exception:
            self.error = "Could not complete the SoundCloud connection. Try again."


class SoundCloudSection(QWidget):
    def __init__(self, service, parent=None, *, isolated=False):
        super().__init__(parent)
        self.service = service
        self._tasks = TaskOwner(self)
        self._worker = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        heading = QLabel("SOUNDCLOUD PUBLIC LIKES")
        heading.setObjectName("SettingsSection")
        layout.addWidget(heading)
        text = QLabel("Optional: prioritize confident matches from your public likes, then search SoundCloud when needed.")
        text.setWordWrap(True)
        layout.addWidget(text)
        self.status = QLabel()
        self.status.setObjectName("SettingsSub")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.connect_button = QPushButton("Connect")
        self.connect_button.setObjectName("SoundCloudConnect")
        self.edit_button = QPushButton("Edit…")
        self.refresh_button = QPushButton("Refresh likes")
        self.disconnect_button = QPushButton("Disconnect")
        self.cancel_button = QPushButton("Cancel")
        for button in (self.connect_button, self.edit_button, self.refresh_button,
                       self.disconnect_button, self.cancel_button):
            button.setCursor(Qt.PointingHandCursor)
            buttons.addWidget(button)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.connect_button.clicked.connect(self._edit)
        self.edit_button.clicked.connect(self._edit)
        self.refresh_button.clicked.connect(self.refresh)
        self.disconnect_button.clicked.connect(lambda: self._run(
            lambda stop, progress, browser: service.disconnect(stop, progress)))
        self.cancel_button.clicked.connect(self._cancel)
        self._sync()
        if not isolated and not QStandardPaths.isTestModeEnabled() and service.status().get("connected"):
            QTimer.singleShot(0, self.refresh)

    def _sync(self):
        state = self.service.status()
        busy = self._worker is not None
        connected, configured = state.get("connected", False), state.get("configured", False)
        self.connect_button.setVisible(not connected)
        self.edit_button.setVisible(configured)
        self.refresh_button.setVisible(connected)
        self.disconnect_button.setVisible(configured)
        self.cancel_button.setVisible(busy)
        for button in (self.connect_button, self.edit_button, self.refresh_button, self.disconnect_button):
            button.setEnabled(not busy)
        if not busy:
            if connected:
                label = "Public likes for" if state.get("mode") == "public" else "Connected as"
                self.status.setText(f"{label} {state.get('username', 'SoundCloud')} · "
                                    f"{state.get('count', 0):,} liked tracks")
            else:
                self.status.setText("Credentials saved · Connect to read public likes" if configured else "Not connected")

    def _cancel(self):
        if self._worker is not None:
            self._worker.stop()
            self.status.setText("Cancelling…")

    def _run(self, operation, completed=None):
        if self._tasks.stopping or self._worker is not None:
            return
        worker = _ConnectionJob(operation)
        self._worker = worker
        worker.progress.connect(self._progress)
        worker.browser.connect(self._open_browser)

        def retired(captured):
            if self._worker is captured:
                self._worker = None
            if self._tasks.stopping:
                return
            self._sync()
            if captured.error:
                self.status.setText(captured.error)
                self.connect_button.setText("Reconnect" if self.service.status().get("connected") else "Connect")
                self.connect_button.show()
            elif not captured.cancelled.is_set() and completed:
                completed(captured.result)

        self._tasks.adopt(worker, retired)
        self._sync()
        self.status.setText("Working…")
        worker.start()

    def _progress(self, message):
        if not self._tasks.stopping and self._worker and not self._worker.cancelled.is_set():
            self.status.setText(message)

    def _open_browser(self, url):
        if not self._tasks.stopping and self._worker and not self._worker.cancelled.is_set():
            if not QDesktopServices.openUrl(QUrl(url)):
                self._worker.stop()
                self.status.setText("Could not open your browser. Try connecting again.")

    def _edit(self):
        def show(credentials):
            dialog = CredentialsDialog(*credentials, parent=self)
            if dialog.exec() == QDialog.Accepted and not self._tasks.stopping:
                client_id, secret = dialog.client_id.text().strip(), dialog.secret.text().strip()
                profile = dialog.profile.text().strip()
                self._run(lambda stop, progress, browser:
                          self.service.connect_public(client_id, secret, profile, stop, progress))
            dialog.secret.clear()
            dialog.deleteLater()
        self._run(lambda stop, progress, browser: self.service.credentials(), show)

    def refresh(self):
        self._run(lambda stop, progress, browser: self.service.refresh(stop, progress))
