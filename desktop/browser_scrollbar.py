"""Shared overlay thumb for browser rows, dialog lists and scrolling hosts."""
from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer
from PySide6.QtWidgets import QScrollBar


class BrowserScrollBar(QObject):
    def __init__(self, view, header=None):
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
            if widget is None:continue
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
        top = rect.top()
        if self.header is not None and not self.header.isHidden():
            top = max(top, self.header.mapTo(self.view, QPoint(0, self.header.height())).y())
        width = self.bar.sizeHint().width()
        self.bar.setGeometry(rect.right()-width+1, top, width, max(0, rect.bottom()-top+1))
        self.bar.setVisible(self.view.isVisible() and self.native.maximum() > self.native.minimum())
        self.bar.raise_()


def ensure_scrollbar(view):
    """Use one thumb owner; bespoke combo popups retain their custom painter."""
    if getattr(view, '_soundbox_overlay_scrollbar', None) is not None:return
    if view.property('soundboxThemePainted') or view.inherits('QHeaderView'):return
    header = (view.horizontalHeader() if hasattr(view, 'horizontalHeader') else
              view.header() if hasattr(view, 'header') else None)
    view._edge_scrollbar = BrowserScrollBar(view, header)
