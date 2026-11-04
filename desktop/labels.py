"""Single-line status text that can shrink without widening its window."""
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QFrame, QLabel


class ElidedLabel(QLabel):
    def __init__(self, text='', parent=None):
        super().__init__(text, parent)
        self.setTextFormat(Qt.PlainText)
        self.reserve(text)

    def reserve(self, text):
        # A pulse reserves all its dots once, so adjacent controls stay still.
        width=self.fontMetrics().horizontalAdvance(text)+8
        self.setMaximumWidth(width)
        self.resize(width,self.height())
        self.updateGeometry()

    def sizeHint(self):
        return QSize(self.maximumWidth(),super().sizeHint().height())

    def minimumSizeHint(self):
        return QSize(0,super().minimumSizeHint().height())

    def paintEvent(self, event):
        QFrame.paintEvent(self,event)
        painter=QPainter(self)
        rect=self.contentsRect().adjusted(self.margin(),0,-self.margin(),0)
        text=self.fontMetrics().elidedText(self.text(),Qt.ElideRight,max(0,rect.width()))
        self.style().drawItemText(painter,rect,self.alignment()|Qt.TextSingleLine,
            self.palette(),self.isEnabled(),text,self.foregroundRole())
        painter.end()
