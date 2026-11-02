"""A row scrollbar over the last column, leaving its header fully resizable."""
from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer
from PySide6.QtWidgets import QScrollBar


class BrowserScrollBar(QObject):
    def __init__(self, view, header):
        super().__init__(view)
        self.view, self.header = view, header
        # Qt retains its normal range, wheel/keyboard and per-row scrolling.
        # Only its visible control moves over the edge instead of reserving
        # a strip alongside the column header.
        self.native = view.verticalScrollBar()
        view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.bar = QScrollBar(Qt.Vertical, view)
        view._soundbox_overlay_scrollbar=self.bar
        self.timer = QTimer(self); self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.sync)
        self.native.rangeChanged.connect(self.sync)
        self.native.valueChanged.connect(self.bar.setValue)
        self.bar.valueChanged.connect(self.native.setValue)
        for widget in (view, view.viewport(), header, view.horizontalScrollBar()):
            widget.installEventFilter(self)
        self.sync()

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Resize, QEvent.Show, QEvent.Hide, QEvent.LayoutRequest):
            self.timer.start(0)
        return False

    def sync(self, *_):
        previous = self.bar.blockSignals(True)
        try:
            self.bar.setRange(self.native.minimum(), self.native.maximum())
            self.bar.setPageStep(self.native.pageStep())
            self.bar.setSingleStep(self.native.singleStep())
            self.bar.setValue(self.native.value())
        finally:self.bar.blockSignals(previous)
        rect = self.view.viewport().geometry()
        top = max(rect.top(), self.header.mapTo(self.view, QPoint(0, self.header.height())).y())
        width = self.bar.sizeHint().width()
        self.bar.setGeometry(rect.right()-width+1, top, width, max(0, rect.bottom()-top+1))
        self.bar.raise_()
