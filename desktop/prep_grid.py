"""Grid controls; the window admits edits only for a verified ready track."""
import math
from decimal import Decimal, InvalidOperation

from PySide6.QtCore import QLocale, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QDoubleValidator, QFont, QIcon, QPainter, QPen, QPixmap, QPolygonF, QTransform
from PySide6.QtWidgets import QLineEdit, QToolButton

from . import theme


def _grid_icon(action: str) -> QIcon:
    pixmap = QPixmap(72, 48)
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    mirrored = action == "right"
    if mirrored:
        action = "left"
    if action == "downbeat":
        painter.setPen(QPen(QColor(theme.TEXT_HI), 2))
        painter.drawLine(QPointF(18, 5), QPointF(18, 12))
        painter.setPen(QPen(QColor("#ff3748"), 2))
        painter.drawLine(QPointF(18, 12), QPointF(18, 20))
    else:
        xs = (16, 20, 24) if action == "left" else (6, 10, 14)
        painter.setPen(QPen(QColor(theme.TEXT_DIM), 1))
        for x in xs:
            painter.drawLine(QPointF(x, 6), QPointF(x, 19))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme.TEXT_HI))
        if action == "left":
            points = ((7, 12), (12, 8), (12, 16))
            painter.drawPolygon(QPolygonF([QPointF(x, y) for x, y in points]))
        else:
            painter.setPen(QColor(theme.TEXT_HI))
            font = QFont()
            font.setPixelSize(12)
            font.setWeight(QFont.DemiBold)
            painter.setFont(font)
            if action == "double":
                painter.drawText(QRectF(16, 0, 20, 24), Qt.AlignCenter, "×2")
            else:
                painter.drawText(QRectF(15, 0, 12, 24), Qt.AlignCenter, "×")
                font.setPixelSize(9)
                painter.setFont(font)
                painter.drawText(QRectF(26, 1, 9, 10), Qt.AlignCenter, "1")
                painter.drawLine(QPointF(27, 12), QPointF(34, 12))
                painter.drawText(QRectF(26, 13, 9, 10), Qt.AlignCenter, "2")
    painter.end()
    if mirrored:
        pixmap = pixmap.transformed(QTransform().scale(-1, 1))
    icon = QIcon()
    # Keep glyphs legible while a track is absent or source admission is blocked.
    icon.addPixmap(pixmap, QIcon.Normal)
    icon.addPixmap(pixmap, QIcon.Disabled)
    return icon


class BpmValue(QLineEdit):
    """Track-bound BPM entry. Enter submits; losing focus or Escape cancels."""

    requested = Signal(str, int)  # Captured track ID and exact BPM hundredths.
    invalid = Signal(str)

    def __init__(self, parent=None):
        super().__init__("000.00", parent)
        self.setObjectName("PrepBpmValue")
        self.setAccessibleName("BPM Value")
        self.setReadOnly(True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setCursor(Qt.ArrowCursor)
        self.setAlignment(Qt.AlignCenter)
        self.setFixedSize(76, 28)
        self.setToolTip("Saved BPM of the loaded track")
        validator = QDoubleValidator(0.01, 655.35, 2, self)
        validator.setNotation(QDoubleValidator.StandardNotation)
        validator.setLocale(QLocale.c())
        self.setValidator(validator)
        self._track_id = ''
        self._display = '000.00'

    def set_bpm(self, value, *, track_id=''):
        try:
            bpm = float(value or 0)
        except (TypeError, ValueError):
            bpm = 0.0
        changed_track = track_id != self._track_id
        self._track_id = track_id
        self._display = f"{bpm:.2f}" if math.isfinite(bpm) and bpm > 0 else "000.00"
        if changed_track:
            self.clearFocus()
        if changed_track or not self.hasFocus() or not self.isModified():
            self.setText(self._display)

    def set_editable(self, enabled, reason=''):
        if not enabled and self.hasFocus():
            self.clearFocus()
        self.setReadOnly(not enabled)
        self.setFocusPolicy(Qt.StrongFocus if enabled else Qt.NoFocus)
        self.setCursor(Qt.IBeamCursor if enabled else Qt.ArrowCursor)
        self.setToolTip('Enter 0.01–655.35 BPM and press Enter to save. Escape cancels.' if enabled
                        else 'BPM Value' + ('\n' + reason if reason else ''))

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.setText(self._display)
            self.clearFocus()
            event.accept()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if not self.isReadOnly() and self._track_id:
                if not self.hasAcceptableInput():
                    self.invalid.emit('Enter 0.01–655.35 BPM, with up to two decimal places')
                    self.selectAll()
                    event.accept()
                    return
                try:
                    hundredths = Decimal(self.text()) * 100
                    if hundredths == hundredths.to_integral_value() and 1 <= hundredths <= 65535:
                        self.setModified(False)
                        self.requested.emit(self._track_id, int(hundredths))
                        self.clearFocus()
                except InvalidOperation:
                    pass
            event.accept()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event):
        self.setText(self._display)
        super().focusOutEvent(event)


def grid_edit_buttons(parent=None) -> list[QToolButton]:
    actions = (
        ("downbeat", "Set the first beat of a bar to the current position."),
        ("left", "Shift the whole Beatgrid left"),
        ("right", "Shift the whole Beatgrid right"),
        ("double", "Double the BPM value"),
        ("halve", "Halve the BPM value"),
    )
    buttons = []
    for action, description in actions:
        button = QToolButton(parent)
        button.setObjectName("GridEditButton")
        button.setAccessibleName(description)
        button.setIcon(_grid_icon(action))
        button.setIconSize(QSize(36, 24))
        button.setFixedSize(44, 28)
        button.setFocusPolicy(Qt.NoFocus)
        button.setToolTip(description + "\nLoad an analyzed rekordbox track to edit its grid.")
        button.setEnabled(False)
        buttons.append(button)
    return buttons


def quantize_button(parent=None) -> QToolButton:
    button = QToolButton(parent)
    button.setObjectName('QuantizeButton')
    button.setText('Q')
    button.setAccessibleName('Quantize')
    button.setCheckable(True)
    button.setFixedSize(44, 28)
    button.setFocusPolicy(Qt.NoFocus)
    button.setToolTip('Quantize: snap new Hot Cues, Memory Cues and the session CUE point '
                      'to the nearest beat. Off uses the exact position.')
    return button
