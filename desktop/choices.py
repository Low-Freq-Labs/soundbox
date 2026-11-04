"""Shared choice controls: left-aligned labels and a right-hand check gutter.

Qt still owns action activation, keyboard navigation, accessibility and popup
lifetime. Only the presentation differs from Cocoa's left-check menus.
"""
from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (QApplication, QComboBox as BaseComboBox, QListView,
    QMenu as BaseMenu, QProxyStyle, QStyle, QStyledItemDelegate, QStyleOptionViewItem,
    QStyleOptionMenuItem, QWidgetAction)

from . import theme


def _mark(painter, x, y, *, check=False):
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(painter.pen().color(), 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    points = ((-4, 0), (-1, 3), (5, -4)) if check else ((-4, -2), (0, 2), (4, -2))
    painter.drawPolyline(QPolygonF([QPointF(x + dx, y + dy) for dx, dy in points]))


class Menu(BaseMenu):
    def addMenu(self, *args):
        if len(args) == 1 and isinstance(args[0], str):
            menu = Menu(args[0], self)
            super().addMenu(menu)
            return menu
        return super().addMenu(*args)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setClipRegion(event.region())
        painter.fillRect(self.rect(), QColor(theme.PANEL))
        painter.setPen(QColor(theme.BORDER))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        for action in self.actions():
            if not action.isVisible() or isinstance(action, QWidgetAction):
                continue
            rect = self.actionGeometry(action)
            if action.isSeparator():
                painter.setPen(QColor(theme.BORDER))
                painter.drawLine(rect.left()+8, rect.center().y(), rect.right()-8, rect.center().y())
                continue
            option = QStyleOptionMenuItem()
            self.initStyleOption(option, action)
            selected = bool(option.state & QStyle.State_Selected)
            if selected:
                painter.fillRect(rect, QColor(theme.PURPLE))
            painter.setFont(option.font)
            color = (theme.ON_ACCENT if selected else theme.TEXT) if action.isEnabled() else theme.TEXT_DIM
            painter.setPen(QColor(color))
            text_rect = QRectF(rect).adjusted(14, 0, -32, 0)
            if not action.icon().isNull():
                action.icon().paint(painter, int(text_rect.left()), rect.center().y()-8, 16, 16)
                text_rect.adjust(24, 0, 0, 0)
            label, _, shortcut = option.text.partition('\t')
            if shortcut:
                shortcut_width=min(text_rect.width(),painter.fontMetrics().horizontalAdvance(shortcut))
                shortcut_rect=QRectF(text_rect)
                shortcut_rect.setLeft(shortcut_rect.right()-shortcut_width)
                painter.drawText(shortcut_rect, Qt.AlignRight | Qt.AlignVCenter, shortcut)
                text_rect.setRight(shortcut_rect.left()-20)
            painter.save()
            painter.setClipRect(text_rect)
            label=painter.fontMetrics().elidedText(label,Qt.ElideRight,max(0,int(text_rect.width())))
            painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter | Qt.TextShowMnemonic, label)
            painter.restore()
            x, y = rect.right()-16, rect.center().y()
            if action.isChecked():
                _mark(painter, x, y, check=True)
            elif action.menu() is not None:
                painter.setRenderHint(QPainter.Antialiasing)
                painter.setPen(QPen(QColor(color), 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                painter.drawPolyline(QPolygonF([QPointF(x-2, y-4), QPointF(x+2, y), QPointF(x-2, y+4)]))
        painter.end()


class _PopupStyle(QProxyStyle):
    def styleHint(self, hint, option=None, widget=None, returnData=None):
        if hint in (QStyle.SH_ComboBox_UseNativePopup, QStyle.SH_ComboBox_Popup):
            return 0
        return super().styleHint(hint, option, widget, returnData)


class _ChoiceDelegate(QStyledItemDelegate):
    def __init__(self, combo):
        super().__init__(combo)
        self.combo = combo

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        return QSize(size.width()+42, max(26, size.height()+6))

    def paint(self, painter, option, index):
        option = QStyleOptionViewItem(option)
        self.initStyleOption(option, index)
        painter.save()
        selected = bool(option.state & QStyle.State_Selected)
        painter.fillRect(option.rect, QColor(theme.PURPLE if selected else theme.PANEL))
        enabled = bool(option.state & QStyle.State_Enabled)
        painter.setPen(QColor((theme.ON_ACCENT if selected else theme.TEXT) if enabled else theme.TEXT_DIM))
        painter.setFont(option.font)
        rect = QRectF(option.rect).adjusted(10, 0, -30, 0)
        if not option.icon.isNull():
            option.icon.paint(painter, int(rect.left()), option.rect.center().y()-8, 16, 16)
            rect.adjust(24, 0, 0, 0)
        text=painter.fontMetrics().elidedText(option.text,Qt.ElideRight,max(0,int(rect.width())))
        painter.save()
        painter.setClipRect(rect)
        painter.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, text)
        painter.restore()
        if index.row() == self.combo.currentIndex():
            _mark(painter, option.rect.right()-15, option.rect.center().y(), check=True)
        painter.restore()


class ComboBox(BaseComboBox):
    popup_matches_field = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        app = QApplication.instance()
        if not hasattr(app, '_choice_popup_style'):
            # This proxy owns its own platform style, never QApplication's style.
            app._choice_popup_style = _PopupStyle()
            app._choice_popup_style.setParent(app)
        self.setStyle(app._choice_popup_style)
        view = QListView(self)
        view.setProperty('soundboxThemePainted', True)
        self.setView(view)
        self.setItemDelegate(_ChoiceDelegate(self))

    def showPopup(self):
        if self.popup_matches_field:
            # A forced narrow popup must shrink its view too. Otherwise the
            # content-width minimum leaves the right-hand check offscreen.
            self.view().setFixedWidth(max(0, self.width()-2))
        else:
            self.view().setMinimumWidth(max(self.width(), self.view().sizeHintForColumn(0)+4))
        super().showPopup()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(theme.PURPLE_LIGHT if self.hasFocus() else theme.BORDER), 1))
        painter.setBrush(QColor(theme.BG))
        painter.drawRoundedRect(QRectF(.5, .5, self.width()-1, self.height()-1), 4, 4)
        painter.setPen(QColor(theme.TEXT if self.isEnabled() else theme.TEXT_DIM))
        rect = QRectF(11, 0, self.width()-37, self.height())
        if not self.itemIcon(self.currentIndex()).isNull():
            self.itemIcon(self.currentIndex()).paint(painter, 11, self.height()//2-8, 16, 16)
            rect.adjust(24, 0, 0, 0)
        text = self.fontMetrics().elidedText(self.currentText(), Qt.ElideRight, max(0, int(rect.width())))
        painter.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, text)
        _mark(painter, self.width()-14, self.height()/2)
        painter.end()
