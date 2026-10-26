"""Render the app offscreen to a PNG for visual verification. Not part of the
app. Uses a SYNTHETIC library (no rekordbox, no network) and a sandboxed
AppData dir (QStandardPaths test mode) so it can never touch real user state.

Usage: PYTHONPATH=. .venv/bin/python -m desktop._shot [out.png] [zoom] [--loading-device]
       Add --analysis-paused, --sync-progress or --ready-device for status QA.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import tempfile
from types import SimpleNamespace
from unittest import mock

from PySide6.QtCore import QStandardPaths

QStandardPaths.setTestModeEnabled(True)  # sandbox saves; never the real library.json

from soundbox import engine, rekordbox_db
rekordbox_db.db_available = lambda *_args: False  # never auto-read the live DB here

from desktop.app import MainWindow, make_app
from soundbox.library import Library, Playlist, Track


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
    return Library(tracks_by_id=tracks, playlists=pls, source="synthetic")


def _render() -> None:
    art_review = "--art-review" in sys.argv
    if art_review:
        sys.argv.remove("--art-review")
    settings_tab = None
    for name in ("General", "Keyboard", "Connections", "Graveyard"):
        flag = "--settings-" + name.lower()
        if flag in sys.argv:
            settings_tab = name
            sys.argv.remove(flag)
    credentials = "--credentials" in sys.argv
    if credentials:
        sys.argv.remove("--credentials")
    ready_device = "--ready-device" in sys.argv
    if ready_device:
        sys.argv.remove("--ready-device")
    sync_progress = "--sync-progress" in sys.argv
    if sync_progress:
        sys.argv.remove("--sync-progress")
    analysis_paused = "--analysis-paused" in sys.argv
    if analysis_paused:
        sys.argv.remove("--analysis-paused")
    loading_device = "--loading-device" in sys.argv
    if loading_device:
        sys.argv.remove("--loading-device")
    app = make_app()
    # Suppress the constructor scan as well as timer-triggered scans for the
    # whole harness lifetime. Never probe real removable devices beside the app.
    MainWindow._refresh_devices = lambda self, **kwargs: None
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
    win._set_autoplay_mode("random", save=False)
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
    if loading_device or ready_device:
        win._on_devices_discovered([
            ("IPOD", "/synthetic/IPOD", 1000,
             SimpleNamespace(has_stock_ipod=True))])
    if ready_device:
        win._stock_view_cache["/synthetic/IPOD"] = (object(), None)
        with mock.patch("desktop.app.device_fs", return_value="msdos"), \
                mock.patch.object(win, "_device_tip", return_value="Fixture sync needed"):
            win._on_devices_scanned([
                ("IPOD", "/synthetic/IPOD", 1000, 1,
                 SimpleNamespace(has_stock_ipod=True))])
    win.show()
    for _ in range(3):
        app.processEvents()
    if settings_tab:
        win.hidden = {"2", "4", "7"}
        win.hidden_dates = {"2": "2026-10-24 06:30", "4": "2026-10-23 15:22", "7": "2026-10-22 09:04"}
        win._refresh_graveyard()
        win.body_stack.setCurrentIndex(1)
        for index in range(win._settings_tabs.count()):
            if win._settings_tabs.tabText(index) == settings_tab:
                win._settings_tabs.setCurrentIndex(index)
        app.processEvents()
    if sync_progress:
        from threading import Event
        from soundbox.device_operations import Phase

        worker = object()
        win._on_dual_ipod_progress(14, 37, "fixture.wav")
        win.cancel_btn.setVisible(True)
        app.processEvents()
        positions = tuple(widget.geometry() for widget in (
            win.status_label, win.status_count_label, win.progress, win.cancel_btn))
        active = SimpleNamespace(worker=worker, cancellation=Event(), phase=Phase.EXECUTING)
        with mock.patch.object(win._device_coordinator, "active", active):
            for stage in ("Acquiring source", "Copying to device", "Verifying device copy") * 20:
                win._on_device_phase_progress(worker, stage, 14_500, 14_500, "fixture.bmp")
                app.processEvents()
                assert positions == tuple(widget.geometry() for widget in (
                    win.status_label, win.status_count_label, win.progress, win.cancel_btn))
    if analysis_paused:
        win._on_analysis_tasks_ready(3)
        win._on_analysis_storage_paused(True, 19 * 1024 ** 3, 25 * 1024 ** 3)
        app.processEvents()
    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/soundbox.png"
    win.grab().save(out)
    if art_review:
        import io
        from pathlib import Path
        from PIL import Image, ImageDraw
        from desktop.artreview import ArtReviewDialog
        from soundbox.artwork import ArtResult
        from PySide6.QtCore import QObject, Signal

        class ReviewDeck(QObject):
            playback_error = Signal(str)
            def __init__(self):
                super().__init__()
                self.state=dict(track_id=None,position=42000,duration=175000,requested=False,
                                loading=False,available=True)
            def review_transport_state(self):
                return self.state
            def pause(self):
                self.state["requested"]=False
            def resume(self):
                self.state["requested"]=True
            def seek_ms(self, ms):
                self.state["position"]=ms
            def start(self, tid):
                self.state.update(track_id=tid,requested=True)
                return True
        review_deck=ReviewDeck()

        def cover(color, text):
            image = Image.new("RGB", (400, 400), color)
            ImageDraw.Draw(image).text((35, 180), text, fill="white", font_size=32)
            output = io.BytesIO()
            image.save(output, "JPEG")
            return output.getvalue()

        win.art_cache.store_ok("review-fixture", ArtResult(
            cover("#48315f", "CURRENT COVER"), "image/jpeg", 1,
            "https://soundcloud.com/wodd", "Current cover"), origin="auto")
        win.art_cache.store_ok("review-manual", ArtResult(
            cover("#322a4d", "MANUAL COVER"), "image/jpeg", 1,
            "file:///Pictures/my-chosen-cover.jpg", "Manual cover"))
        candidate = dict(title="WODD - GHOST PROTOCOL (Original Mix)", uploader="WODD",
                         thumb="synthetic", url="https://soundcloud.com/example/ghost-protocol",
                         liked=True, score=.95)
        preview = cover("#155b65", "SUGGESTED COVER")
        from threading import Event
        release = Event()
        provider = SimpleNamespace(search_candidates=lambda *_: [candidate] if release.wait(3) else [],
                                   preview_image=lambda _: preview,
                                   fetch_profile_art=lambda _: ArtResult(cover("#38502f","ARTIST PROFILE"),
                                       "image/jpeg",.98,"https://soundcloud.com/example-artist","Example artist"))
        dialog = ArtReviewDialog(win.art_cache, [
            ("review-fixture", "WODD", "WODD - GHOST PROTOCOL (Original Mix)", "Automatic"),
            ("review-manual", "Example", "Example - Manually chosen cover", "Manual"),
        ], win, provider=provider, player=review_deck,play_track=review_deck.start)
        # Real queue preparation against deterministic local providers only.
        import time
        dialog._scan()
        dialog.show()
        app.processEvents()
        target = Path(out)
        dialog.grab().save(str(target.with_name(target.stem + "-art-review-loading.png")))
        release.set()
        deadline = time.monotonic() + 3
        while dialog._tasks.pending and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.001)
        assert not dialog._tasks.pending
        dialog.show()
        app.processEvents()
        dialog.audition._toggle()
        dialog.grab().save(str(target.with_name(target.stem + "-art-review.png")))
        dialog.tabs.setCurrentIndex(1)
        app.processEvents()
        dialog.grab().save(str(target.with_name(target.stem + "-art-review-all.png")))
        menu=dialog.more.menu()
        menu.show()
        app.processEvents()
        menu.grab().save(str(target.with_name(target.stem+"-art-review-menu.png")))
        menu.hide()
        from PySide6.QtWidgets import QMessageBox
        def profile_preview(box):
            box.show()
            app.processEvents()
            box.grab().save(str(target.with_name(target.stem+"-artist-profile.png")))
            box.reject()
            return 0
        with mock.patch.object(QMessageBox,"exec",profile_preview):
            dialog._use_profile()
            deadline=time.monotonic()+3
            while dialog._tasks.pending and time.monotonic()<deadline:
                app.processEvents()
                time.sleep(.001)
            assert not dialog._tasks.pending
        dialog.reject()
        dialog.shutdown()
        win.art_cache.keep_review("review-fixture",win.art_cache.cover_snapshot("review-fixture"),
                                  "WODD","WODD - GHOST PROTOCOL (Original Mix)")
        def unexpected_search(*_):
            raise AssertionError("Reopening completed checks must not search")
        provider.search_candidates=unexpected_search
        resumed=ArtReviewDialog(win.art_cache,[
            ("review-fixture","WODD","WODD - GHOST PROTOCOL (Original Mix)","Automatic"),
            ("review-manual","Example","Example - Manually chosen cover","Manual"),
        ],win,provider=provider,player=review_deck,play_track=review_deck.start)
        resumed._scan()
        resumed.show()
        deadline=time.monotonic()+3
        while resumed._tasks.pending and time.monotonic()<deadline:
            app.processEvents()
            time.sleep(.001)
        assert not resumed._tasks.pending
        assert resumed._saved_count==2 and resumed._reviewed_count==1
        assert resumed._last_progress==(0,0)
        app.processEvents()
        resumed.grab().save(str(target.with_name(target.stem+"-art-review-resumed.png")))
        resumed.reject()
        resumed.shutdown()
        from desktop.uploader_exclusions import UploaderExclusionsDialog
        win.art_cache.exclude_source("https://soundcloud.com/example-repost")
        manager=UploaderExclusionsDialog(win.art_cache,win,selected_url=candidate["url"],observed=("plate-selling",))
        manager.show()
        app.processEvents()
        manager.grab().save(str(target.with_name(target.stem+"-uploader-exclusions.png")))
        manager.close()
    if credentials:
        from pathlib import Path
        from desktop.connections import CredentialsDialog
        dialog = CredentialsDialog(parent=win)
        dialog.show()
        app.processEvents()
        target = Path(out)
        dialog.grab().save(str(target.with_name(target.stem + "-credentials.png")))
        dialog.close()
    win.shutdown()
    print("saved", out, "| devices:", win.device_tree.topLevelItemCount())


def main() -> None:
    original_location = QStandardPaths.writableLocation
    with tempfile.TemporaryDirectory(prefix="soundbox-render-") as area:
        with mock.patch.object(
                QStandardPaths, "writableLocation",
                side_effect=lambda kind: area
                if kind == QStandardPaths.AppDataLocation
                else original_location(kind)):
            _render()


if __name__ == "__main__":
    main()
