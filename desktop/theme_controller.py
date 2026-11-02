"""Update accent declarations on visible controls without repolishing the app.

The large static stylesheet owns geometry and fixed colors. Small, widget-local
overrides own accent colors. Hidden widgets catch up when shown; custom painters
read the same theme roles. Existing inline styles (including cue colors) stay
with their owning component.
"""
import re

from PySide6.QtCore import QEvent, QObject,QTimer
from PySide6.QtWidgets import QWidget,QAbstractScrollArea

from . import theme
from .choices import ComboBox, Menu


def _accent_rules():
    source = re.sub(r'/\*.*?\*/', '', theme._BASE_QSS, flags=re.S)
    tokens = ('#7b2fbe', '#a855f7', '#5b21b6', '#28173a', '@on_accent@')
    rules = []
    for selectors, body in re.findall(r'([^{}]+)\{([^{}]*)\}', source):
        declarations = [part.strip() for part in body.split(';')
                        if any(token in part.lower() for token in tokens)]
        if not declarations:
            continue
        for selector in selectors.split(','):
            selector = selector.strip()
            # All shipped selectors are flat or descendant selectors. Match the
            # terminal widget; Qt still evaluates the original full selector.
            target = selector.split()[-1]
            match = re.match(r'(?P<type>[A-Za-z]\w*)?(?:#(?P<name>[\w-]+))?', target)
            if match and (match['type'] or match['name']):
                rules.append((selector, '; '.join(declarations)+';', match['type'], match['name']))
    return tuple(rules)


class ThemeController(QObject):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.rules = _accent_rules()
        self._cache = {}
        self._updating = False
        self._signature = None
        app.installEventFilter(self)

    def _sheet(self, widget):
        key = (widget.metaObject().className(), widget.objectName(),
               theme.PURPLE, theme.ON_ACCENT)
        if key not in self._cache:
            chosen = []
            for selector, declarations, kind, name in self.rules:
                if widget.objectName() in ('CueBtn', 'DeckPlayBtn') and not name:
                    # Widget-local generic hover rules outrank the application
                    # sheet's dedicated cue/play states. Keep their rings tied
                    # exclusively to explicit cue/transport intent.
                    continue
                if name and widget.objectName() != name:
                    continue
                if kind and not widget.inherits(kind):
                    continue
                chosen.append(selector+' {'+declarations+'}')
            sheet = '\n'.join(chosen)
            for old, new in (('#7B2FBE', theme.PURPLE), ('#A855F7', theme.PURPLE_LIGHT),
                             ('#5B21B6', theme.PURPLE_DARK), ('#28173a', theme.SUBTLE),
                             ('@ON_ACCENT@', theme.ON_ACCENT)):
                sheet = sheet.replace(old, new)
            self._cache[key] = sheet
        return self._cache[key]

    def polish(self, widget):
        if isinstance(widget, (ComboBox, Menu)) or widget.property('soundboxThemePainted'):
            return  # These surfaces already paint current theme roles directly.
        previous = getattr(widget, '_soundbox_theme_sheet', '')
        current = widget.styleSheet()
        if current and current != previous:
            return  # Another component owns this widget's inline style.
        sheet = self._sheet(widget)
        if sheet != current:
            widget._soundbox_theme_sheet = sheet
            widget.setStyleSheet(sheet)

    def update(self):
        signature = (theme.PURPLE, theme.ON_ACCENT)
        if signature == self._signature:
            return
        self._signature = signature
        self._cache.clear()  # bounded to this color and the visible widget kinds
        self._updating = True
        try:
            for widget in self.app.allWidgets():
                if widget.isVisible():
                    self.polish(widget)
                    widget.update()
        finally:
            self._updating = False

    def eventFilter(self, obj, event):
        if event.type()==QEvent.Wheel and isinstance(obj,QWidget):
            area=obj
            while area is not None and not isinstance(area,QAbstractScrollArea):area=area.parentWidget()
            if area is not None:
                vertical=bool(event.pixelDelta().y() or event.angleDelta().y())
                horizontal=bool(event.pixelDelta().x() or event.angleDelta().x())
                if vertical:
                    self._scroll_feedback(getattr(area,'_soundbox_overlay_scrollbar',area.verticalScrollBar()))
                if horizontal:self._scroll_feedback(area.horizontalScrollBar())
        if (not self._updating and event.type() == QEvent.Show
                and isinstance(obj, QWidget)):
            self._updating = True
            try:
                self.polish(obj)
            finally:
                self._updating = False
        return False

    @staticmethod
    def _scroll_feedback(bar):
        if bar.maximum()<=bar.minimum():return
        def active(value):
            if bool(bar.property('wheelScrolling'))==value:return
            bar.setProperty('wheelScrolling',value)
            bar.style().unpolish(bar);bar.style().polish(bar);bar.update()
        timer=getattr(bar,'_soundbox_scroll_feedback',None)
        if timer is None:
            timer=QTimer(bar);timer.setSingleShot(True);timer.setInterval(300)
            timer.timeout.connect(lambda:active(False));bar._soundbox_scroll_feedback=timer
        active(True);timer.start()


def install(app):
    if not hasattr(app, '_soundbox_theme_controller'):
        # The application stylesheet is installed once. Changing an accent
        # must never invalidate every hidden settings control and layout.
        app.setStyleSheet(theme.STATIC_QSS)
        app._soundbox_theme_controller = ThemeController(app)
    return app._soundbox_theme_controller
