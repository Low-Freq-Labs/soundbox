"""User-supplied vector wordmark in the original 270 × 55 header footprint."""
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QWidget

from . import theme


class Wordmark(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SoundboxWordmark")
        self.setAccessibleName("SoundBox")
        self.setFixedSize(270, 55)
        root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
        self._source = (root / 'assets/soundbox-wordmark.svg').read_bytes()
        self.renderer = QSvgRenderer(self)
        self.set_accent(theme.PURPLE)

    def set_accent(self, color):
        # Qt SVG does not resolve the web CSS custom properties in the supplied
        # asset. Resolve only its two named color layers in memory instead.
        svg = ET.fromstring(self._source)
        for child in list(svg):
            if child.tag.rsplit('}', 1)[-1] == 'style':
                svg.remove(child)
            elif child.get('id') == 'box':
                child.set('fill', color)
            elif child.get('id') == 'sound':
                child.set('fill', '#ffffff')
        if not self.renderer.load(QByteArray(ET.tostring(svg))):
            raise ValueError('SoundBox wordmark SVG could not be loaded')
        self.renderer.setViewBox(self.renderer.boundsOnElement('outline'))
        self.renderer.setAspectRatioMode(Qt.IgnoreAspectRatio)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        self.renderer.render(painter, QRectF(self.rect()))
        painter.end()
