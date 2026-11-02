"""The existing swatch button, with hex editing directly over its label."""
import re

from PySide6.QtCore import QEvent, QRect, Qt, Signal
from PySide6.QtGui import QPainter, QRegion
from PySide6.QtWidgets import QLineEdit, QPushButton, QStyle, QStyleOptionButton, QToolTip


class ColorButton(QPushButton):
    hex_committed = Signal(str)

    def __init__(self, text='', parent=None):
        super().__init__(text, parent)
        self.editor = QLineEdit(self)
        self.editor.setFrame(False)
        self.editor.setStyleSheet('QLineEdit {background:transparent; border:none; padding:0;}')
        self.editor.setAccessibleName('Hex color')
        self.editor.hide()
        self.editor.installEventFilter(self)
        self.editor.returnPressed.connect(self._commit)
        self.editor.editingFinished.connect(self._commit)

    def _label_rect(self):
        width = self.fontMetrics().horizontalAdvance(self.text())
        icon = self.iconSize().width()+4 if not self.icon().isNull() else 0
        left = (self.width()-width-icon)//2+icon
        return QRect(left, 1, max(width+4, self.width()-left-8), self.height()-2)

    def _start_edit(self):
        self.editor.setFont(self.font())
        self.editor.setGeometry(self._label_rect())
        self.editor.setText(self.text())
        self.editor.show(); self.editor.setFocus(); self.editor.selectAll(); self.update()

    def _commit(self):
        if self.editor.isHidden():return
        value = self.editor.text().strip()
        self.editor.hide(); self.update()
        if not re.fullmatch(r'#?[0-9a-fA-F]{6}', value):
            QToolTip.showText(self.mapToGlobal(self.rect().bottomLeft()), 'Use six hex digits, such as #7B2FBE', self)
            return
        value = '#'+value.lstrip('#').lower()
        if value != self.text().lower():self.hex_committed.emit(value)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._label_rect().contains(event.position().toPoint()):
            self._start_edit(); event.accept(); return
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_F2:
            self._start_edit(); event.accept(); return
        super().keyPressEvent(event)

    def eventFilter(self, obj, event):
        if obj is self.editor and event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape:
            self.editor.hide(); self.setFocus(); self.update(); return True
        return super().eventFilter(obj, event)

    def paintEvent(self, event):
        if self.editor.isHidden():return super().paintEvent(event)
        option = QStyleOptionButton(); self.initStyleOption(option)
        painter = QPainter(self)
        self.style().drawControl(QStyle.CE_PushButtonBevel, option, painter, self)
        painter.setClipRegion(QRegion(self.rect()).subtracted(QRegion(self.editor.geometry())))
        self.style().drawControl(QStyle.CE_PushButtonLabel, option, painter, self)
        painter.end()
