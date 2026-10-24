"""Compact, searchable keyboard editor with adjacent controls and explicit conflicts."""
from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from .keyboard import BY_ID, COMMANDS, Keymap, display, event_binding


class BindingDialog(QDialog):
    def __init__(self, keymap, action, parent=None):
        super().__init__(parent)
        self.keymap, self.action = keymap, action
        self.binding = ""
        self.move = False
        self.setWindowTitle("Shortcut · " + BY_ID[action].label)
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        self.prompt = QLabel("Press a key or modifier combination")
        self.prompt.setAlignment(Qt.AlignCenter)
        self.prompt.setMinimumHeight(70)
        self.prompt.setObjectName("ShortcutCapture")
        layout.addWidget(self.prompt)
        self.feedback = QLabel("Esc cancels. Modifier-only keys are ignored.")
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.save = buttons.button(QDialogButtonBox.Save)
        self.save.setEnabled(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.setFocusPolicy(Qt.StrongFocus)
        # Capture before buttons/text fields or a native shortcut can consume it.
        from PySide6.QtWidgets import QApplication
        QApplication.instance().installEventFilter(self)
        self.finished.connect(lambda _result: QApplication.instance().removeEventFilter(self))

    def eventFilter(self, obj, event):
        if event.type() not in (QEvent.ShortcutOverride, QEvent.KeyPress, QEvent.KeyRelease):
            return False
        if event.type() != QEvent.KeyPress:
            event.accept()
            return True
        if event.key() == Qt.Key_Escape:
            self.reject()
            return True
        if event.isAutoRepeat():
            return True
        binding = event_binding(event)
        if binding:
            self.binding = binding
            self.prompt.setText(display(binding))
            conflicts = self.keymap.conflicts(binding, self.action)
            self.move = bool(conflicts)
            if conflicts:
                self.feedback.setText("Assigned to " + ", ".join(BY_ID[item].label for item in conflicts)
                                      + ". Move this shortcut to " + BY_ID[self.action].label + "?")
                self.save.setText("Move shortcut")
            else:
                self.feedback.setText("Ready to assign to " + BY_ID[self.action].label)
                self.save.setText("Save")
            self.save.setEnabled(True)
        return True


class KeyboardPage(QWidget):
    def __init__(self, keymap, save, parent=None):
        super().__init__(parent)
        self.keymap, self.save = keymap, save
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 20)
        head = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find an action or shortcut…")
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(400)
        self.search.textChanged.connect(self._filter)
        head.addWidget(self.search)
        head.addStretch()
        reset = QPushButton("Reset all…")
        reset.clicked.connect(self._reset_all)
        head.addWidget(reset)
        layout.addLayout(head)
        note = QLabel("Click a shortcut to change it. Add more than one per action. Hold actions release with the key.")
        note.setObjectName("SettingsSub")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.message = QLabel()
        self.message.setObjectName("SettingsSub")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        layout.addWidget(self.scroll, 1)
        self._render()

    def set_keymap(self, keymap):
        self.keymap = keymap
        self._render()

    def _render(self):
        position = self.scroll.verticalScrollBar().value()
        host = QWidget()
        columns = QHBoxLayout(host)
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(18)
        sides = []
        for _ in range(2):
            side = QVBoxLayout()
            side.setSpacing(8)
            sides.append(side)
            columns.addLayout(side, 1)
        self.rows, self.groups = [], {}
        group = None
        group_index = -1
        for command in COMMANDS:
            if group != command.group:
                group = command.group
                group_index += 1
                card = QWidget()
                box = QVBoxLayout(card)
                box.setContentsMargins(0, 0, 0, 0)
                box.setSpacing(5)
                sides[group_index % 2].addWidget(card)
                label = QLabel(group.upper())
                label.setObjectName("SettingsSection")
                box.addWidget(label)
                self.groups[group] = card
            row = QWidget()
            row.setObjectName("ShortcutRow")
            line = QHBoxLayout(row)
            line.setContentsMargins(10, 4, 10, 4)
            line.setSpacing(6)
            title = QLabel(command.label)
            title.setFixedWidth(175)
            title.setToolTip("Hold to preview; release to return" if command.hold else "Press once")
            line.addWidget(title, 0, Qt.AlignTop)
            controls = QGridLayout()
            controls.setContentsMargins(0, 0, 0, 0)
            controls.setSpacing(5)
            line.addLayout(controls)
            keys = self.keymap.bindings(command.id)
            for index, key in enumerate(keys):
                chip = QWidget()
                chip_line = QHBoxLayout(chip)
                chip_line.setContentsMargins(0, 0, 0, 0)
                chip_line.setSpacing(3)
                button = QPushButton(display(key))
                button.setObjectName("ShortcutKey")
                button.setMinimumWidth(60)
                button.setToolTip("Change " + display(key))
                button.clicked.connect(lambda _checked=False, action=command.id, old=key: self._capture(action, old))
                chip_line.addWidget(button)
                remove = QPushButton("×")
                remove.setObjectName("ShortcutRemove")
                remove.setFixedWidth(25)
                remove.setToolTip("Remove " + display(key))
                remove.clicked.connect(lambda _checked=False, action=command.id, old=key:
                                       self._change(lambda: self.keymap.remove(action, old)))
                chip_line.addWidget(remove)
                controls.addWidget(chip, index // 2, index % 2, Qt.AlignLeft)
            add = QPushButton("Add shortcut…" if not keys else "+")
            add.setToolTip("Add a shortcut for " + command.label)
            add.clicked.connect(lambda _checked=False, action=command.id: self._capture(action))
            controls.addWidget(add, len(keys) // 2, len(keys) % 2, Qt.AlignLeft)
            if command.id in self.keymap.overrides:
                reset = QPushButton("Reset")
                reset.clicked.connect(lambda _checked=False, action=command.id:
                                      self._change(lambda: self.keymap.reset(action)))
                controls.addWidget(reset, (len(keys) + 1) // 2, (len(keys) + 1) % 2, Qt.AlignLeft)
            line.addStretch()
            row.setEnabled(self.keymap.editable)
            box.addWidget(row)
            self.rows.append((command, row))
        for side in sides:
            side.addStretch()
        old = self.scroll.takeWidget()
        if old:
            old.deleteLater()
        self.scroll.setWidget(host)
        self._filter()
        self.scroll.verticalScrollBar().setValue(position)
        if not self.keymap.editable:
            self.message.setText("This keymap uses a newer format. Existing settings were preserved.")

    def _filter(self):
        query = self.search.text().casefold().strip()
        visible = set()
        for command, row in self.rows:
            text = " ".join((command.group, command.label, *self.keymap.bindings(command.id),
                             *(display(key) for key in self.keymap.bindings(command.id)))).casefold()
            match = all(word in text for word in query.split())
            row.setVisible(match)
            if match:
                visible.add(command.group)
        for group, label in self.groups.items():
            label.setVisible(group in visible)

    def _change(self, mutation):
        before = self.keymap.dump()
        try:
            mutation()
        except ValueError as exc:
            self.message.setText(str(exc))
            return
        if not self.save(self.keymap.dump()):
            self.keymap = Keymap(before)
            self.message.setText("Shortcuts were not saved. The previous mappings are still active.")
        else:
            self.message.clear()
        self._render()

    def _capture(self, action, old=""):
        dialog = BindingDialog(self.keymap, action, self)
        if dialog.exec() == QDialog.Accepted and dialog.binding:
            self._change(lambda: self.keymap.assign(action, dialog.binding, replace=old, move=dialog.move))
        dialog.deleteLater()

    def _reset_all(self):
        if QMessageBox.question(self, "Reset keyboard shortcuts", "Reset all customized shortcuts to the defaults?",
                                QMessageBox.Reset | QMessageBox.Cancel, QMessageBox.Cancel) == QMessageBox.Reset:
            self._change(lambda: self.keymap.reset())
