"""Render the app offscreen to a PNG for visual verification. Not part of the
app. Uses a SYNTHETIC library (no rekordbox, no network) and a sandboxed
AppData dir (QStandardPaths test mode) so it can never touch real user state.

Usage: PYTHONPATH=. .venv/bin/python -m desktop._shot [out.png] [zoom] [--loading-device]
       Add --analysis-paused, --sync-progress, --edit-status or --ready-device for status QA.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import tempfile
from types import SimpleNamespace
from unittest import mock

from PySide6.QtCore import QStandardPaths,Qt

QStandardPaths.setTestModeEnabled(True)  # sandbox saves; never the real library.json

from soundbox import engine, rekordbox_db
rekordbox_db.db_available = lambda *_args: False  # never auto-read the live DB here
def _deny_live_source(*_args, **_kwargs):
    raise AssertionError("Render harness cannot read a real rekordbox source")
rekordbox_db.load_live = _deny_live_source

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
    native_playlists = '--native-playlists' in sys.argv
    if native_playlists:sys.argv.remove('--native-playlists')
    analysis_menus = '--analysis-menus' in sys.argv
    if analysis_menus:sys.argv.remove('--analysis-menus')
    playlist_folders = '--playlist-folders' in sys.argv
    if playlist_folders:sys.argv.remove('--playlist-folders')
    track_menu = '--track-menu' in sys.argv
    if track_menu: sys.argv.remove('--track-menu')
    analyze_dialog = '--analyze-dialog' in sys.argv
    if analyze_dialog: sys.argv.remove('--analyze-dialog')
    all_columns = '--all-columns' in sys.argv
    if all_columns:sys.argv.remove('--all-columns')
    track_info = "--track-info" in sys.argv
    if track_info:sys.argv.remove("--track-info")
    format_usb = "--format-usb" in sys.argv
    if format_usb:sys.argv.remove("--format-usb")
    usb_page = None
    for flag, page in (('--usb-overview',0),('--usb-playlists',1),('--usb-category',2),('--usb-sort',3),
                       ('--usb-column',4),('--usb-color',5),('--usb-my-settings',6)):
        if flag in sys.argv:
            usb_page = page
            sys.argv.remove(flag)
    shortcut_unbind = '--shortcut-unbind' in sys.argv
    if shortcut_unbind:
        sys.argv.remove('--shortcut-unbind')
    edit_status = '--edit-status' in sys.argv
    if edit_status:
        sys.argv.remove('--edit-status')
    long_playback = '--long-playback' in sys.argv
    if long_playback:
        sys.argv.remove('--long-playback')
    text_menu = '--theme-text-menu' in sys.argv
    if text_menu:
        sys.argv.remove('--theme-text-menu')
    theme_preview = '--theme-preview' in sys.argv
    if theme_preview:sys.argv.remove('--theme-preview')
    accent = None
    text_color = None
    for argument in list(sys.argv):
        if argument.startswith('--accent='):
            accent = argument.split('=', 1)[1]
            sys.argv.remove(argument)
        elif argument.startswith('--theme-text='):
            text_color = argument.split('=', 1)[1]
            sys.argv.remove(argument)
    my_tags = "--my-tags" in sys.argv
    if my_tags:
        sys.argv.remove("--my-tags")
    cue_edits = '--cue-edits' in sys.argv
    if cue_edits:
        sys.argv.remove('--cue-edits')
    grid_edits = '--grid-edits' in sys.argv
    if grid_edits:
        sys.argv.remove('--grid-edits')
    quantize = '--quantize' in sys.argv
    if quantize:
        sys.argv.remove('--quantize')
    art_review = "--art-review" in sys.argv
    if art_review:
        sys.argv.remove("--art-review")
    settings_tab = 'Analysis' if analyze_dialog else None
    for name in ("General", "Analysis", "My Settings", "Keyboard", "Connections", "Graveyard"):
        flag = "--settings-" + name.lower().replace(' ','-')
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
    if settings_tab or analysis_menus or text_menu:
        win._ensure_settings_page()
    from . import theme
    win._apply_theme_color(accent or theme.DEFAULT_ACCENT, save=False)
    if text_color is not None:
        if text_color in ('white', 'black'):
            win._apply_theme_text(text_color, save=False)
        else:
            win._apply_theme_text('custom', text_color, save=False)
    win.resize(1240, 800)
    win.lib = synthetic_library()
    win._build_artist_registry()
    win._apply_settings_to_lib()
    win._populate_tree(engine.summary(win.lib))
    if playlist_folders:
        folder='c5b3690a-657b-4ad9-bdcf-baf008282fad'
        nested='53e697c6-f1aa-4df1-934a-909c9c931a33'
        win.custom={'Favorites':['1','2'],'Friday set':['2','3','4'],'Warmup':['5','6']}
        win.settings['custom_playlist_tree']=[{'playlist':'Favorites'},
            dict(folder=folder,name='DJ sets',children=[{'playlist':'Friday set'},
                dict(folder=nested,name='Openers',children=[{'playlist':'Warmup'}])])]
        win.settings['custom_playlist_expanded']=[folder,nested]
        win._refresh_custom()
    if native_playlists:
        from soundbox.rekordbox_playlist_plan import revision
        nodes=[]
        for identity,name,parent,attribute,seq in (
                ('10','CRATES','root',1,1),('1','SET ONE','10',0,1),
                ('2','SET TWO','10',0,2),('3','WARMUPS','root',0,2),
                ('11','Empty folder','root',1,3)):
            nodes.append(dict(ID=identity,UUID=f'00000000-0000-4000-8000-{int(identity):012d}',
                              Name=name,ParentID=parent,Attribute=attribute,Seq=seq,rb_local_deleted=0))
        members=[dict(ID=f'{p.pid}-{i}',PlaylistID=p.pid,ContentID=tid,TrackNo=i,rb_local_deleted=0)
                 for p in win.lib.playlists for i,tid in enumerate(p.keys,1)]
        win.lib.source_kind='rekordbox-db';win.lib.source_library_id='synthetic-playlists'
        win.lib.source_playlist_catalog=dict(nodes=nodes,members=members,revision=revision(nodes,members))
        win.tag_controller.verified=True
        win.playlist_controller.factory=_deny_live_source
        win._native_request=_deny_live_source
        win._populate_tree(engine.summary(win.lib))
    from .trees import NATIVE_TREE_ROLE
    root=next(win.tree.topLevelItem(i) for i in range(win.tree.topLevelItemCount())
              if win.tree.topLevelItem(i).data(0,NATIVE_TREE_ROLE))
    root.setExpanded(True)
    win.tree.setCurrentItem(root.child(0))
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
    if long_playback:
        pb._on_dur(3_723_000)
        pb._on_pos(83_000)
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
        if settings_tab=='My Settings':
            from soundbox import my_settings
            win._global_my_settings.set_profile(my_settings.values(my_settings.new_files({}).items()))
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
    if all_columns:
        from .model import COLUMNS
        for column in range(len(COLUMNS)):win.table.setColumnHidden(column,False)
        win._fit_browser_columns(fill=True);app.processEvents()
    if analysis_paused:
        win._on_analysis_tasks_ready(3)
        win._on_analysis_storage_paused(True, 19 * 1024 ** 3, 25 * 1024 ** 3)
        app.processEvents()
    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/soundbox.png"
    if text_menu:
        from pathlib import Path
        win._theme_text_mode.showPopup()
        app.processEvents()
        target = Path(out)
        win._theme_text_mode.view().window().grab().save(
            str(target.with_name(target.stem + '-text-menu.png')))
        win._theme_text_mode.hidePopup()
    if cue_edits:
        track = win.lib.tracks_by_id['1']
        track.source_uuid = 'synthetic-cue-track'
        track.source_cue_revision = 'a' * 64
        track.source_cues = cues
        win.lib.source_kind = 'rekordbox-db'
        win.lib.source_library_id = 'synthetic-cues'
        win.tag_controller._requires_cue_authority = False
        win.tag_controller._cue_store_factory = lambda *_: (_ for _ in ()).throw(
            AssertionError('Render harness cannot write rekordbox cues'))
        win.tag_controller.configure(win.lib, True)
        win._sync_cue_editing()
        pb.cue_panel.set_loaded(True)
        assert all(button.isEnabled() for button in pb.cue_panel.hot_slots)
    if grid_edits:
        # Presentation-only enabled state; every source job still fails closed.
        win.grid_controller.reason = lambda _tid, **_options: ''
        win.grid_controller.bpm_reason = lambda _tid: ''
        win.grid_controller.request = _deny_live_source
        pb._native_error = ''
        pb._pending_autoplay = False
        pb.cue_panel.set_loaded(True)
        win._sync_grid()
        assert all(button.isEnabled() for button in win.grid_edit_buttons)
    if quantize:
        win._set_quantize(True, save=False)
    if my_tags:
        from pathlib import Path
        from PySide6.QtCore import QItemSelectionModel, QPoint
        from soundbox.library import LibraryTag
        win.lib.source_kind = "rekordbox-db"
        win.lib.source_library_id = "synthetic-tags"
        win.lib.source_tags = [LibraryTag(str(i), name, "Genre", f"tag-{i}")
                               for i, name in enumerate(("Dubstep", "Riddim", "Deep Dub", "DnB",
                                   "Techno", "House", "Trap", "Brostep", "Tearout", "SONNY", "ZYKO"))]
        for i, track in enumerate(win.lib.tracks_by_id.values()):
            track.source_uuid = f"synthetic-{i}"
            track.source_tag_ids = ["0"] if i % 2 else []
        win.tag_controller._requires_authority = False
        win.tag_controller._ready = True
        win.tag_controller.probe = lambda: None
        win.tag_controller.configure(win.lib, True)
        # This render can show edit controls but can never acquire a real writer.
        win.tag_controller._store_factory = lambda _lib: (_ for _ in ()).throw(
            AssertionError("Render harness cannot write rekordbox"))
        selection = win.table.selectionModel()
        selection.clearSelection()
        for row in (0, 1):
            selection.select(win.model.index(row, 0),
                             QItemSelectionModel.Select | QItemSelectionModel.Rows)
        win._sync_tags()
        win.my_tags.popup.popup(win.my_tags.mapToGlobal(QPoint(0, win.my_tags.height())))
        app.processEvents()
        target = Path(out)
        win.my_tags.popup.grab().save(str(target.with_name(target.stem + "-my-tags.png")))
    # The offscreen backend has no running audio clock. Pin this synthetic
    # display value after event settling rather than treating its zero poll as
    # a playback reference.
    pb._emit_playback_time(83_000 if long_playback else 44_000)
    win.grab().save(out)
    if native_playlists:
        from pathlib import Path
        from PySide6.QtCore import QPoint
        menu=win._native_menu(win.tree.topLevelItem(2))
        menu.popup(win.tree.viewport().mapToGlobal(QPoint(90,180)))
        app.processEvents();target=Path(out)
        menu.grab().save(str(target.with_name(target.stem+'-native-menu.png')))
        win.grab().save(out);menu.close()
    if edit_status:
        from pathlib import Path
        target = Path(out)
        # Presentation fixtures only: no source edit or worker admission.
        for name, message in (('beatgrid', 'Updating beatgrid…'),
                              ('bpm', 'Updating BPM…'), ('hot', 'Updating hot cue…'),
                              ('memory', 'Updating memory cue…'),
                              ('tags', 'Updating My Tags…'), ('done', 'Done'),
                              ('ready', 'Ready')):
            win._set_status_text(message)
            app.processEvents()
            win.grab().save(str(target.with_name(target.stem + '-' + name + '.png')))
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
    if usb_page is not None:
        from pathlib import Path
        from desktop.devices import DeviceFacts
        from desktop.usb_device_view import UsbSnapshot
        from soundbox.cdj_usb import CdjLibrary, CdjPlaylist, CdjSong
        from soundbox import my_settings
        from .usb_preferences import USB_TABS
        preferences=dict(categories=[(i,i,name,i,visible) for i,name,visible in (
            (1,'GENRE',False),(2,'ARTIST',True),(3,'ALBUM',True),(4,'TRACK',True),(11,'KEY',True),
            (17,'PLAYLIST',True),(19,'HISTORY',True),(20,'SEARCH',True),(24,'FOLDER',True),(27,'MATCHING',True))],
            sorts=[(i,i,name,i,True) for i,name in ((25,'DEFAULT'),(26,'ALPHABET / TRACK NAME'),(5,'BPM'),(11,'KEY'),(22,'DATE ADDED'),(2,'ARTIST'))],
            sub_column=None,colors=list(enumerate(('Pink','Red','Orange','Yellow','Green','Aqua','Blue','Purple'),1)))
        profiles=tuple(('PIONEER/'+name,raw) for name,raw in my_settings.new_files({}).items())
        win.usb_device_pages.global_profile=my_settings.values(profiles)
        snapshot = UsbSnapshot(Path('/Volumes/SOUNDBOX USB'), 1,
            DeviceFacts(volume_uuid='SYNTHETIC-USB', protocol='USB', device_identifier='disk9s1',
                        filesystem_name='MS-DOS (FAT32)', capacity_bytes=256_000_000_000,
                        free_bytes=192_000_000_000, writable=True),
            native=CdjLibrary((
                CdjPlaylist('1', '0', 'CRATES', True, ()),
                CdjPlaylist('2', '1', 'SET ONE', False,
                    (('WODD — GHOST PROTOCOL', '/Contents/GHOST PROTOCOL.wav'),
                     ('Zero — Enter The Dragon', '/Contents/Enter The Dragon.wav'))),
                CdjPlaylist('3', '0', 'WARMUPS', False,
                    (('Aweminus — Sea Shanty', '/Contents/Sea Shanty.wav'),))),
                'SOUNDBOX USB', 8, 8, (('waveform_color', 'RGB'), ('waveform_position', 'LEFT'),
                ('overview_waveform', 'Half Waveform'), ('key_format', 'Alphanumeric')), 1, profiles, preferences=preferences,songs=(
                    CdjSong('1','/Contents/GHOST PROTOCOL.wav','GHOST PROTOCOL','WODD',14000,'8A',180,1411,artwork='/PIONEER/Artwork/1.jpg'),
                    CdjSong('2','/Contents/Enter The Dragon.wav','Enter The Dragon','Zero',14000,'2A',195,1411),
                    CdjSong('3','/Contents/Sea Shanty.wav','Sea Shanty','Aweminus',14000,'3A',160,1411))))
        win.usb_device_pages.set_snapshot(snapshot, name='SOUNDBOX USB',
            profile=SimpleNamespace(label='USB', kind='generic_usb', rockbox_export=True),
            free=192_000_000_000, drift=0)
        win.device_content_stack.setCurrentIndex(2)
        for i, button in enumerate(win.device_tab_buttons):
            button.setVisible(True);button.setText(USB_TABS[i])
        win._current_device_path = str(snapshot.target)
        win.dev_header.setText('SOUNDBOX USB')
        win.device_sync_btn.setText('Update Playlists')
        win._set_device_tab(usb_page)
        win.stack.setCurrentIndex(1)
        if usb_page==1:
            win.usb_device_pages.playlist_tree.topLevelItem(0).setExpanded(True)
            win.usb_device_pages.playlist_tree.topLevelItem(0).child(0).setExpanded(True)
        app.processEvents()
        win.grab().save(out)
    if shortcut_unbind:
        from pathlib import Path
        from desktop.keyboard import Keymap
        from desktop.keyboard_page import BindingDialog
        dialog = BindingDialog(Keymap(), 'cues.hot_page', win, old='9')
        dialog.show()
        app.processEvents()
        target = Path(out)
        dialog.grab().save(str(target.with_name(target.stem + '-unbind.png')))
        dialog.reject()
    if track_info:
        from pathlib import Path
        from .track_info import TrackInfoDialog
        from PySide6.QtWidgets import QTabWidget
        row=win._row_for(next(iter(win.lib.tracks_by_id.values())))
        row.update(album='GHOST PROTOCOL',label='SoundBox',comments='A synthetic track for UI review',year='2026',
            releaseDate='2026-10-29',sampleRate='44100',size='13900000',rating='4',playCount='20',hotCueAutoLoad='On')
        from PySide6.QtGui import QImage,QColor,QPainter
        art=QImage(320,320,QImage.Format_RGB32);art.fill(QColor('#202634'))
        painter=QPainter(art);painter.setPen(QColor(theme.PURPLE))
        painter.drawText(art.rect(),Qt.AlignCenter,'SOUNDBOX\nGHOST PROTOCOL');painter.end()
        art_path=Path(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation))/'info-fixture.png'
        art.save(str(art_path))
        row['location']='/Music/Example artist/Album name/Long title for the selected track.wav'
        dialog=TrackInfoDialog(row,artwork=lambda:art_path,save=lambda _changes:True,pick_art=lambda:None,parent=win)
        dialog.prepare_artwork()
        dialog.show();app.processEvents()
        target=Path(out)
        dialog.grab().save(str(target.with_name(target.stem+'-summary.png')))
        dialog.findChild(QTabWidget).setCurrentIndex(1);app.processEvents()
        from PySide6.QtWidgets import QScrollArea
        assert dialog.findChild(QScrollArea).verticalScrollBar().maximum()==0, 'Info must fit at its default size'
        dialog.grab().save(str(target.with_name(target.stem+'-info.png')))
        dialog.reject()
    if format_usb:
        from .usb_preferences import FormatUsbDialog
        from soundbox.usb_format import FormatTarget
        from pathlib import Path
        dialog=FormatUsbDialog(FormatTarget('/Volumes/SOUNDBOX USB',(1,2),'synthetic','disk99',(),256_000_000_000,('/Volumes/SOUNDBOX USB',)),win)
        dialog.show();app.processEvents()
        target=Path(out)
        dialog.grab().save(str(target.with_name(target.stem+'-format.png')))
        dialog.reject()
    if track_menu:
        from pathlib import Path
        menu = win._track_menu()
        menu.popup(win.track_menu_btn.mapToGlobal(win.track_menu_btn.rect().bottomLeft()))
        app.processEvents()
        target = Path(out)
        menu.grab().save(str(target.with_name(target.stem+'-menu.png')))
        colors = menu.actions()[0].menu()
        colors.popup(menu.mapToGlobal(menu.actionGeometry(menu.actions()[0]).topRight()))
        app.processEvents()
        colors.grab().save(str(target.with_name(target.stem+'-color-menu.png')))
        colors.close()
        menu.close(); menu.deleteLater()
        selector = pb.layout_selector
        if selector is not None:
            selector.showPopup(); app.processEvents()
            selector.view().window().grab().save(str(target.with_name(target.stem+'-layout-menu.png')))
            selector.hidePopup()
    if analysis_menus and settings_tab=='Analysis':
        from pathlib import Path
        _capture_analysis_menus(win._analysis_controls,Path(out),app)
    if playlist_folders:
        from pathlib import Path
        item=win.sb_tree.topLevelItem(3)
        menu=win._local_context_menu(item)
        menu.popup(win.sb_tree.viewport().mapToGlobal(win.sb_tree.visualItemRect(item).center()))
        app.processEvents();target=Path(out)
        menu.grab().save(str(target.with_name(target.stem+'-folder-menu.png')))
        menu.close();menu.deleteLater()
    if theme_preview:
        from pathlib import Path
        _capture_theme_preview(win, Path(out), app)
    win.shutdown()
    print("saved", out, "| devices:", win.device_tree.topLevelItemCount())


def _capture_analysis_menus(controls,target,app):
    for name,combo in (('bpm',controls.bpm_range),('process',controls.process)):
        combo.showPopup();app.processEvents()
        combo.view().window().grab().save(str(target.with_name(target.stem+'-'+name+'-menu.png')))
        combo.hidePopup();app.processEvents()


def _capture_theme_preview(win, target, app):
    """Exercise the actual macOS dialog signal path against synthetic state."""
    from copy import deepcopy
    import json
    from time import perf_counter
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QColorDialog
    from . import theme
    before = deepcopy(win.settings)
    observations = []; timings = []
    render = win._render_theme
    def measured(*args):
        start = perf_counter(); result = render(*args)
        timings.append((perf_counter()-start)*1000)
        return result
    dialog = QColorDialog(QColor(theme.PURPLE), win)
    # Cocoa's native helper can strand exec() after a programmatic reject.
    # Open the actual native panel asynchronously for scripted signal capture;
    # the production picker retains its ordinary user-driven modal lifecycle.
    def native_capture_exec():
        loop = QEventLoop()
        dialog.finished.connect(loop.quit)
        dialog.open()
        loop.exec()
        return dialog.result()
    def capture(name):
        observations.append(dict(name=name, accent=theme.PURPLE,
                                 settings_unchanged=win.settings==before))
        win.grab().save(str(target.with_name(target.stem+'-'+name+'.png')))
    QTimer.singleShot(0, lambda: dialog.setCurrentColor(QColor('#168fc0')))
    QTimer.singleShot(150, lambda: capture('live-blue'))
    QTimer.singleShot(200, lambda: dialog.setCurrentColor(QColor('#e27239')))
    QTimer.singleShot(350, lambda: capture('live-orange'))
    QTimer.singleShot(400, dialog.reject)
    with mock.patch('desktop.settings_page.QColorDialog', return_value=dialog), \
         mock.patch.object(dialog, 'exec', side_effect=native_capture_exec), \
         mock.patch.object(win, '_render_theme', side_effect=measured), \
         mock.patch.object(app, 'setStyleSheet', wraps=app.setStyleSheet) as global_style:
        win._choose_theme_color()
        assert [row['accent'] for row in observations]==['#168fc0','#e27239']
        assert all(row['settings_unchanged'] for row in observations)
        assert win.settings==before
        assert global_style.call_count==0
        lettering=[]
        for mode in ('black','white','black'):
            start=perf_counter();win._apply_theme_text(mode)
            lettering.append((perf_counter()-start)*1000)
    win.settings=before;win._apply_theme_color(before.get('theme_accent'))
    win.grab().save(str(target.with_name(target.stem+'-restored.png')))
    report=dict(observations=observations,preview_update_ms=timings,
                lettering_update_ms=lettering,global_stylesheet_rebuilds=0,
                cancelled_preferences_restored=True)
    target.with_name(target.stem+'-theme.json').write_text(json.dumps(report,indent=2)+'\n')


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
