"""Render the app offscreen to a PNG for visual verification. Not part of the
app. Uses a SYNTHETIC library (no rekordbox, no network) and a sandboxed
AppData dir (QStandardPaths test mode) so it can never touch real user state.

Usage: PYTHONPATH=. .venv/bin/python -m desktop._shot [out.png]
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys

from PySide6.QtCore import QStandardPaths

QStandardPaths.setTestModeEnabled(True)  # sandbox saves; never the real library.json

from soundbox import engine, rekordbox_db
rekordbox_db.db_available = lambda: False  # never auto-read the live DB here

from desktop.app import MainWindow, make_app
from soundbox.rekordbox_xml import Library, Playlist, Track


def synthetic_library() -> Library:
    names = ["GHOST PROTOCOL", "Enter The Dragon", "Sea Shanty", "Nuclear Bass Face",
             "GUTTER", "Corrosion VIP", "Wook Flu", "Maniac", "Ferity VIP",
             "Scream Saver", "Walk In The Spot", "I Got That"]
    tracks = {str(i): Track(str(i), n, ["WODD", "Zero", "Aweminus"][i % 3],
                            f"file://localhost/tmp/{i}.wav", bpm="140.00",
                            key=f"{(i % 11) + 1}A", total_time=150 + 13 * i)
              for i, n in enumerate(names, 1)}
    ids = list(tracks)
    pls = [Playlist("SET ONE", ["CRATES"], keys=ids[:8], pid="1"),
           Playlist("SET TWO", ["CRATES"], keys=ids[4:], pid="2"),
           Playlist("WARMUPS", [], keys=ids[::2], pid="3")]
    return Library(tracks_by_id=tracks, playlists=pls, source_xml="synthetic")


def main() -> None:
    app = make_app()
    win = MainWindow()
    win.resize(1240, 800)
    win.lib = synthetic_library()
    win._build_artist_registry()
    win._apply_settings_to_lib()
    win._populate_tree(engine.summary(win.lib))
    win.tree.topLevelItem(0).setExpanded(True)
    win.tree.setCurrentItem(win.tree.topLevelItem(0).child(0))
    win.show()
    for _ in range(3):
        app.processEvents()
    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/soundbox.png"
    win.grab().save(out)
    win.shutdown()
    print("saved", out, "| devices:", win.device_tree.topLevelItemCount())


if __name__ == "__main__":
    main()
