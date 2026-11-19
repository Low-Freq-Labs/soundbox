"""Explicit packaged startup measurement against disposable metadata only."""
from __future__ import annotations

import json
import os
import resource
import sys
from pathlib import Path
import time

from PySide6.QtCore import QStandardPaths, QTimer


def runtime_crypto_check() -> bool:
    """Exercise the bundled AES backend with NIST SP800-38A's first CBC block."""
    from Crypto.Cipher import AES

    key = bytes.fromhex('2b7e151628aed2a6abf7158809cf4f3c')
    iv = bytes.fromhex('000102030405060708090a0b0c0d0e0f')
    plain = bytes.fromhex('6bc1bee22e409f96e93d7e117393172a')
    cipher = bytes.fromhex('7649abac8119b246cee98e9b12e9197d')
    return (AES.new(key, AES.MODE_CBC, iv).encrypt(plain) == cipher
            and AES.new(key, AES.MODE_CBC, iv).decrypt(cipher) == plain)


def runtime_sql_check() -> bool:
    """Exercise bundled native ORM/SQLCipher entirely in disposable RAM."""
    from pyrekordbox.db6 import tables
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session
    from sqlcipher3 import dbapi2 as sqlcipher

    connection = sqlcipher.connect(':memory:')
    engine = None
    try:
        if not connection.execute('PRAGMA cipher_version').fetchone():
            raise RuntimeError('SQLCipher runtime is unavailable')
        connection.execute('CREATE TABLE djmdPlaylist (ID TEXT PRIMARY KEY)')
        connection.execute("INSERT INTO djmdPlaylist VALUES ('isolated')")
        connection.commit()
        engine = create_engine('sqlite://', module=sqlcipher, creator=lambda: connection)
        with Session(engine) as session:
            return session.execute(select(tables.DjmdPlaylist.ID)).scalars().all() == ['isolated']
    finally:
        if engine is not None:
            engine.dispose()
        connection.close()


def run(state: Path, report: Path) -> None:
    from .app import MainWindow, make_app

    QStandardPaths.setTestModeEnabled(True)
    started = int(os.environ['SOUNDBOX_BENCHMARK_LAUNCH_NS'])
    app = make_app()
    win = MainWindow(isolated_dir=state)
    app.aboutToQuit.connect(win.shutdown)
    win.show()

    def exercise():
        from soundbox.desktop_runtime import DESKTOP_MODULES

        count = win.model.rowCount()
        if not count:
            raise RuntimeError('Startup benchmark requires populated rows')
        win.table.setCurrentIndex(win.model.index(0, 0))
        win.model.set_filter(str(win.model._rows[0].get('title', '')).split()[0])
        matched = win.model.rowCount() > 0
        win.model.set_filter('')
        win.table.setCurrentIndex(win.model.index(0, 0))
        # A synchronous Qt paint proxy; does not claim compositor presentation.
        painted = not win.grab().isNull()
        report.write_text(json.dumps({
            'scope': 'disposable-snapshot-browser',
            'elapsed_ms': (time.monotonic_ns() - started) / 1_000_000,
            'rows': count, 'search_matched': matched, 'painted': painted,
            'selected': win.table.currentIndex().isValid(),
            'source_verified': win._rekordbox_source_verified,
            'native': bool(win.player_bar.player.is_native),
            'numpy_loaded': 'numpy' in sys.modules,
            'stock_converter_loaded': 'clickwheel.ipod.artworkdb_writer.rgb565' in sys.modules,
            'device_surfaces': [name for name in ('usb_device_pages', 'stock_ipod_pages')
                                if name in vars(win)],
            'device_backends': [name for name in ('soundbox.sync', 'soundbox.stock_ipod',
                                'soundbox.dual_ipod', 'soundbox.cdj_export', 'soundbox.rockbox_media')
                                if name in sys.modules],
            'settings_created': hasattr(win, '_settings_tabs'),
            'settings_ready': (win._settings_tabs.count() == 6
                               and len(win._global_my_settings.controls) == 44),
            'artists_ready': getattr(win, '_artists_generation', None) is not None,
            'artists_rows': sum(win.artists_view.topLevelItem(i).childCount()
                                for i in range(win.artists_view.topLevelItemCount())),
            'collection_rows': len(win._visible_collection_tracks()),
            'desktop_runtime_missing': [name for name in DESKTOP_MODULES if name not in sys.modules],
            'desktop_runtime_errors': list(win._desktop_runtime_errors),
            'provider_diagnostic': win.providers.diagnostic,
            # macOS reports ru_maxrss in bytes; this app targets macOS only.
            'peak_rss_bytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        }, indent=2) + '\n')
        # Normal source refresh checkpoints these derived rows. Isolated mode
        # has no source reader, so checkpoint explicitly after stopping the clock.
        win._cache_library()
        app.quit()

    QTimer.singleShot(0, lambda: QTimer.singleShot(0, exercise))
    app.exec()
