"""Apply the saved accent to the running application's icon, once per color."""
from __future__ import annotations

import logging
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt, QTimer
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from . import theme

LOG = logging.getLogger('soundbox')


def accented_image(value: str, size: int = 1024) -> QImage:
    """Render Sonny's vector icon with only its named accent shapes recolored."""
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
    svg = ET.fromstring((root/'assets/soundbox-original-sb-icon.svg').read_bytes())
    color = theme.normalize_accent(value)
    # Resolve the supplied CSS roles explicitly, like the wordmark adapter.
    # Qt SVG does not support the web :root/currentColor cascade consistently.
    roles = {'ink': {'fill': '#08080c'}, 'accent': {'fill': color},
             'shade': {'fill': '#050508', 'opacity': '.25'},
             'shine': {'fill': '#fff', 'opacity': '.65'}}
    for child in list(svg):
        if child.tag.rsplit('}', 1)[-1] == 'style':
            svg.remove(child)
    for node in svg.iter():
        for role in node.get('class', '').split():
            for name, resolved in roles.get(role, {}).items():
                node.set(name, resolved)
        for name, authored in list(node.attrib.items()):
            if authored == 'currentColor':
                node.set(name, color)
    renderer = QSvgRenderer(QByteArray(ET.tostring(svg)))
    if not renderer.isValid():
        raise OSError('SoundBox application icon SVG is invalid')
    image = QImage(size, size, QImage.Format_RGBA8888)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    renderer.render(painter)
    painter.end()
    return image


def apply_saved_icon(app, value: str, *, persist=True) -> bool:
    """Commit running and persistent icons; picker previews never call this."""
    color = theme.normalize_accent(value)
    if app is None:
        return False
    from soundbox.mac_icon import application_bundle, set_custom_icon, refresh_running_icon
    bundle = application_bundle() if persist else None
    changed=getattr(app,'_soundbox_icon_accent',None)!=color
    if not changed and (bundle is None or getattr(app,'_soundbox_icon_persisted',None)==color):return False
    try:
        image = accented_image(color)
    except OSError as exc:
        LOG.warning('Application icon was not updated: %s', exc)
        return False
    if changed:app.setWindowIcon(QIcon(QPixmap.fromImage(image)))
    app._soundbox_icon_accent = color
    if bundle is not None:
        try:
            encoded = QByteArray()
            buffer = QBuffer(encoded)
            buffer.open(QIODevice.WriteOnly)
            if not image.save(buffer, 'PNG'):
                raise OSError('Could not encode saved application icon')
            buffer.close()
            png=bytes(encoded)
            set_custom_icon(bundle,png)
            app._soundbox_icon_persisted=color
            # Cocoa can replace an early Qt icon while finishing application
            # launch. Commit the native Dock image once the event loop is live,
            # then after launch settles. A newer saved color cancels both calls.
            def refresh():
                if getattr(app,'_soundbox_icon_accent',None)!=color:return
                try:refresh_running_icon(png)
                except OSError as exc:LOG.warning('Saved Dock icon could not be refreshed: %s',exc)
            QTimer.singleShot(0,refresh);QTimer.singleShot(750,refresh)
        except OSError as exc:
            LOG.warning('Saved application icon could not be persisted: %s', exc)
    LOG.info('Application icon: saved accent %s', color)
    return True
