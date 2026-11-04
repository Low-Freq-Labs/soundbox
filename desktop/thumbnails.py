"""Bounded catalog thumbnails, decoded only when rows become visible."""
from collections import OrderedDict

from PySide6.QtCore import QEvent, QObject, QPoint, QSize, Qt, QTimer
from PySide6.QtGui import QIcon, QImageReader, QPixmap


class CatalogThumbnails(QObject):
    def __init__(self, tree, *, capacity=64):
        super().__init__(tree)
        self.tree = tree
        self.capacity = capacity
        self._rows = {}
        self._loaded = OrderedDict()
        self._ratio = tree.devicePixelRatioF()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._load_one)
        tree.viewport().installEventFilter(self)
        tree.verticalScrollBar().valueChanged.connect(self.schedule)

    def clear(self):
        self._timer.stop()
        self._rows.clear()
        self._loaded.clear()

    def set_image(self, item, path):
        key = id(item)
        self._loaded.pop(key, None)
        item.setIcon(0, QIcon())
        if path is None:
            self._rows.pop(key, None)
        else:
            self._rows[key] = (item, str(path))
        self.schedule()

    def remove(self, item):
        key = id(item)
        self._rows.pop(key, None)
        self._loaded.pop(key, None)
        self.schedule()

    def schedule(self, *_):
        if self.tree.isVisible() and not self._timer.isActive():
            self._timer.start(0)

    def eventFilter(self, _object, event):
        if event.type() in (QEvent.Show, QEvent.Resize, QEvent.DevicePixelRatioChange):
            self.schedule()
        elif event.type() == QEvent.Hide:
            self._timer.stop()
        return False

    def _visible(self):
        viewport = self.tree.viewport()
        item = self.tree.itemAt(QPoint(1, 0))
        if item is None:
            item = self.tree.topLevelItem(0)
        result = []
        while item is not None:
            rect = self.tree.visualItemRect(item)
            if rect.top() > viewport.height():
                break
            if rect.intersects(viewport.rect()):
                result.append(item)
            item = self.tree.itemBelow(item)
        return result

    def _load_one(self):
        if not self.tree.isVisible():
            return
        ratio = self.tree.devicePixelRatioF()
        if ratio != self._ratio:
            for item in self._loaded.values():
                item.setIcon(0, QIcon())
            self._loaded.clear()
            self._ratio = ratio
        visible = self._visible()
        visible_keys = {id(item) for item in visible}
        for item in visible:
            key = id(item)
            if key in self._loaded:
                self._loaded.move_to_end(key)
                continue
            row = self._rows.get(key)
            if row is None:
                continue
            reader = QImageReader(row[1])
            side = max(1, round(self.tree.iconSize().width() * ratio))
            bounds = QSize(side, side)
            size = reader.size()
            if size.isValid():
                reader.setScaledSize(size.scaled(bounds, Qt.KeepAspectRatio))
            image = reader.read()
            if not image.isNull():
                if image.width() > side or image.height() > side:
                    image = image.scaled(bounds, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                image.setDevicePixelRatio(ratio)
                item.setIcon(0, QIcon(QPixmap.fromImage(image)))
            # Failed reads also settle this presentation. Explicit replacement
            # or revisiting an evicted row can retry; no busy missing-file loop.
            self._loaded[key] = item
            # Visible rows stay pinned even on unusually tall windows. Retain
            # at most the viewport or the fixed budget, whichever is larger.
            while len(self._loaded) > max(self.capacity, len(visible)):
                old = next(key for key in self._loaded if key not in visible_keys)
                evicted = self._loaded.pop(old)
                evicted.setIcon(0, QIcon())
            self.schedule()
            break
