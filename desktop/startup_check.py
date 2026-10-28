"""Explicit packaged startup measurement against disposable metadata only."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
import time

from PySide6.QtCore import QStandardPaths, QTimer


def run(state: Path, report: Path) -> None:
    from .app import MainWindow, make_app

    QStandardPaths.setTestModeEnabled(True)
    started = int(os.environ['SOUNDBOX_BENCHMARK_LAUNCH_NS'])
    app = make_app()
    win = MainWindow(isolated_dir=state)
    app.aboutToQuit.connect(win.shutdown)
    win.show()

    def exercise():
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
        }, indent=2) + '\n')
        # Normal source refresh checkpoints these derived rows. Isolated mode
        # has no source reader, so checkpoint explicitly after stopping the clock.
        win._cache_library()
        app.quit()

    QTimer.singleShot(0, lambda: QTimer.singleShot(0, exercise))
    app.exec()
