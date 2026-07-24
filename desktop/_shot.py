"""Render the app offscreen to a PNG for visual verification. Not part of the
app. Uses a SYNTHETIC library (no rekordbox, no network) and a sandboxed
AppData dir (QStandardPaths test mode) so it can never touch real user state.

Usage: PYTHONPATH=. .venv/bin/python -m desktop._shot [out.png] [zoom]
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
    # Paint the transport in a representative loaded state without opening any
    # real audio file or starting QtMultimedia playback.
    pb = win.player_bar
    pb._track_id = "1"
    pb._src_path = "/tmp/synthetic.wav"
    pb._bpm = 140.0
    pb._dur = 150_000
    pb._cue_ms = 32_000
    pb.wave.set_active(True)
    cues = [
        {"kind": "hot", "slot": 0, "position_ms": 5_000, "end_ms": -1,
         "type": "single", "label": "Intro", "color": [255, 55, 111]},
        {"kind": "hot", "slot": 1, "position_ms": 16_000, "end_ms": -1,
         "type": "single", "label": "Build", "color": [69, 172, 219]},
        {"kind": "hot", "slot": 2, "position_ms": 21_000, "end_ms": -1,
         "type": "single", "label": "Drop", "color": [125, 193, 61]},
        {"kind": "hot", "slot": 3, "position_ms": 44_000, "end_ms": -1,
         "type": "single", "label": "Main drop", "color": [170, 114, 255]},
        {"kind": "hot", "slot": 4, "position_ms": 58_000, "end_ms": -1,
         "type": "single", "label": "", "color": [48, 210, 110]},
        {"kind": "memory", "slot": 0, "position_ms": 17_145,
         "end_ms": -1, "type": "single", "label": "", "color": [0, 0, 0]},
    ]
    pb.cue_panel.set_cues(cues)
    pb.now_playing_changed.emit("WODD — GHOST PROTOCOL")
    count = 150 * 150
    heights = [0.14 + ((i * 17) % 31) / 38 for i in range(count)]
    colors = []
    for i in range(count):
        phase = (i // 500) % 4
        colors.append(([1, 6, 7], [4, 2, 7], [12, 2, 2], [8, 5, 2])[phase])
    beats = []
    beat_ms = 60_000 / 140
    for i in range(int(150_000 / beat_ms)):
        beats.append([(i % 4) + 1, int(i * beat_ms), 140.0])
    pb.wave.set_analysis({
        "heights": heights, "colors": colors, "beats": beats,
        "_detail_bands": bytes(
            channel
            for height in heights
            for channel in (
                min(127, round(height * 127)),
                min(127, round(height * 91)),
                min(127, round(height * 73)),
            )
        ),
        "sample_rate": 150.0, "cues": cues,
    })
    if len(sys.argv) > 2:
        pb.set_waveform_zoom(float(sys.argv[2]), emit=False)
    if len(sys.argv) > 3 and sys.argv[3] == "memory":
        pb.cue_panel.memory_tab.click()
    # Center the synthetic D/drop cue so the render gate always exercises the
    # main-waveform box/stem seam as well as the whole-track marker.
    pb._on_pos(44_000)
    pb.set_track_navigation(True, True)
    win.show()
    for _ in range(3):
        app.processEvents()
    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/soundbox.png"
    win.grab().save(out)
    win.shutdown()
    print("saved", out, "| devices:", win.device_tree.topLevelItemCount())


if __name__ == "__main__":
    main()
