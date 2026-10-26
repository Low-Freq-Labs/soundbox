"""One local manager for explicit uploader blocks and known automatic blocks."""
import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPushButton,
)
from soundbox.art_sources import account_name, excluded_source, profile_source


class UploaderExclusionsDialog(QDialog):
    changed = Signal()

    def __init__(self, cache, parent=None, *, selected_url="", observed=()):
        super().__init__(parent)
        self.cache = cache
        self.observed = set(observed)
        self.setWindowTitle("Exclude Uploader")
        self.resize(580, 440)
        layout = QVBoxLayout(self)
        note = QLabel("Blocked profiles are excluded from artwork searches. Seller/dealer accounts are also blocked automatically.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.accounts = QListWidget()
        self.accounts.currentItemChanged.connect(self._controls)
        layout.addWidget(self.accounts, 1)
        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("SoundCloud profile URL or @handle")
        selected = account_name(selected_url)
        if selected:
            self.input.setText(f"https://soundcloud.com/{selected}")
        self.input.returnPressed.connect(self._add)
        row.addWidget(self.input, 1)
        self.add = QPushButton("Add uploader")
        self.add.setAutoDefault(False)
        self.add.clicked.connect(self._add)
        row.addWidget(self.add)
        layout.addLayout(row)
        self.status = QLabel("Existing covers from blocked sources use artist-profile fallbacks; previous images stay recoverable.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        foot = QHBoxLayout()
        self.remove = QPushButton("Remove block")
        self.remove.clicked.connect(self._remove)
        foot.addWidget(self.remove)
        foot.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        foot.addWidget(close)
        layout.addLayout(foot)
        self._refresh()

    def _refresh(self):
        self.accounts.clear()
        custom = set(self.cache.source_exclusions())
        for account in sorted(set(self.cache.excluded_accounts()) | self.observed):
            automatic = excluded_source(url=f"https://soundcloud.com/{account}")
            item = QListWidgetItem(f"{account}  ·  {'Automatic seller/dealer block' if automatic else 'Blocked'}")
            item.setData(Qt.UserRole, (account, account in custom and not automatic))
            item.setToolTip(f"https://soundcloud.com/{account}")
            self.accounts.addItem(item)
        self._controls()

    def _controls(self, *_):
        current = self.accounts.currentItem()
        self.remove.setEnabled(bool(current and current.data(Qt.UserRole)[1] and not self.cache.read_only))
        self.input.setEnabled(not self.cache.read_only)
        self.add.setEnabled(not self.cache.read_only)

    def _add(self):
        if self.cache.read_only:
            return
        value = self.input.text().strip()
        handle = account_name(value)
        if not handle and re.fullmatch(r"@?[A-Za-z0-9_-]+", value):
            handle = value.lstrip("@").casefold()
        if not handle or not profile_source(f"https://soundcloud.com/{handle}"):
            self.status.setText("Enter a SoundCloud profile URL or @handle.")
            return
        try:
            self.cache.exclude_source(f"https://soundcloud.com/{handle}")
        except (OSError, ValueError) as exc:
            self.status.setText(str(exc))
            return
        self.input.clear()
        self._refresh()
        self.changed.emit()
        self.status.setText(f"{handle} is blocked. This list is saved for next time.")

    def _remove(self):
        item = self.accounts.currentItem()
        if item is None or self.cache.read_only or not item.data(Qt.UserRole)[1]:
            return
        account = item.data(Qt.UserRole)[0]
        try:
            self.cache.allow_source(account)
        except (OSError, ValueError) as exc:
            self.status.setText(str(exc))
            return
        self._refresh()
        self.changed.emit()
        self.status.setText(f"{account} is no longer blocked.")
