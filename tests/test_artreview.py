"""Artwork review safety against generated images, never live AppData/network."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import tempfile
from threading import Event
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtWidgets import QApplication, QMessageBox

from soundbox.artwork import ArtCache, ArtResult

_app = None


def setUpModule():
    global _app
    _app = QApplication.instance() or QApplication([])
    from desktop.artreview import _ReviewScan
    unittest.enterModuleContext(patch.object(_ReviewScan, "DELAY", 0))


def image(color, fmt="JPEG"):
    output = io.BytesIO()
    Image.new("RGB", (80, 80), color).save(output, fmt)
    return output.getvalue()


def result(color="blue"):
    return ArtResult(image(color), "image/jpeg", .95,
                     "https://soundcloud.com/artist/song", "Artist - Song")


class DuplicateComparisons(unittest.TestCase):
    def test_resized_recompressed_art_is_redundant_but_other_design_and_colour_are_not(self):
        from PIL import ImageDraw, ImageOps
        from desktop.artreview import _same_art
        cover=Image.new("RGB",(600,600),"#531982")
        draw=ImageDraw.Draw(cover)
        for i in range(36):
            draw.ellipse((i*13,i*9,600-i*3,600-i*7),
                         outline=(i*7%256,i*31%256,i*53%256),width=7)
        draw.rectangle((90,400,510,540),fill="#15eac2")
        draw.text((120,450),"ORIGINAL ARTWORK",fill="black",font_size=30)
        original=io.BytesIO();cover.save(original,"JPEG",quality=95)
        small=io.BytesIO();cover.resize((90,90),Image.Resampling.LANCZOS).save(small,"JPEG",quality=20)
        self.assertTrue(_same_art(original.getvalue(),small.getvalue()))
        self.assertTrue(_same_art(small.getvalue(),original.getvalue()))
        other=io.BytesIO();cover.transpose(Image.Transpose.FLIP_TOP_BOTTOM).save(other,"JPEG")
        self.assertFalse(_same_art(original.getvalue(),other.getvalue()))
        recoloured=io.BytesIO();ImageOps.invert(cover).save(recoloured,"JPEG")
        self.assertFalse(_same_art(original.getvalue(),recoloured.getvalue()))
        self.assertFalse(_same_art(original.getvalue(),image("blue")))
        self.assertFalse(_same_art(original.getvalue(),b"not an image"))


class ReviewSafety(unittest.TestCase):
    def test_excluded_source_is_hidden_then_profile_replacement_preserves_exact_recovery(self):
        from desktop.workers import ArtFillWorker
        from soundbox.library import Library, Track
        old = self.cache.index["1"]
        old["src"] = "https://soundcloud.com/dubplate-seller/unreleased"
        (self.root / "index.json").write_text(json.dumps(self.cache.index))
        self.cache = ArtCache(self.root)
        before = self.cache.cover_snapshot("1")
        self.assertIsNone(self.cache.image_path("1"))
        avatar = ArtResult(image("purple"), "image/jpeg", 1,
                           "https://soundcloud.com/real-artist", "Real Artist (avatar)")
        class Provider:
            def fetch_profile_art(self, artist):
                self.artist = artist
                return avatar
        provider = Provider()
        lib = Library({"1": Track("1", "Unreleased", "Real Artist", "file:///not-read.wav")}, [], "synthetic")
        worker = ArtFillWorker(lib, self.cache, {}, {}, provider=provider)
        worker.DELAY = 0
        worker.run()
        self.assertEqual(provider.artist, "Real Artist")
        self.assertEqual(self.cache.status_kind("1"), "avatar")
        self.assertEqual(self.cache.index["1"]["fallback"], "excluded-source")
        self.assertEqual((self.root / "source-recovery/1.image").read_bytes(), before.data)
        self.assertEqual(json.loads((self.root / "source-recovery/1.json").read_text())["before"], before.entry)
        self.assertEqual(self.cache.retry_weak(), 0)

    def test_missing_profile_never_reveals_seller_art_and_late_manual_edit_wins(self):
        self.cache.index["1"]["src"] = "https://soundcloud.com/dubplate-dealer/unreleased"
        (self.root / "index.json").write_text(json.dumps(self.cache.index))
        self.cache = ArtCache(self.root)
        before = self.cache.cover_snapshot("1")
        self.assertIsNone(self.cache.image_path("1"))
        self.assertEqual(self.cache.cover_snapshot("1"), before)
        self.cache.store_ok("1", result("green"))
        changed = self.cache.cover_snapshot("1")
        avatar = ArtResult(image("purple"), "image/jpeg", 1, "https://soundcloud.com/artist", "Artist")
        self.assertFalse(self.cache.apply_excluded_profile("1", before, avatar, "Artist"))
        self.assertEqual(self.cache.cover_snapshot("1"), changed)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "art"
        self.cache = ArtCache(self.root)
        self.cache.store_ok("1", result("red"))

    def test_exact_undo_survives_restart_without_duplicating_library(self):
        # Legacy PNG and absent provenance must survive Undo byte for byte.
        data = image("red", "PNG")
        entry = {"status": "ok", "ext": "png", "src": "file:///original.png", "conf": 1}
        (self.root / "1.png").write_bytes(data)
        (self.root / "index.json").write_text(json.dumps({"1": entry}))
        self.cache = ArtCache(self.root)
        before = self.cache.cover_snapshot("1")
        self.cache.apply_review("1", before, result())
        self.assertFalse((self.root / "1.png").exists())
        self.assertEqual(self.cache.index["1"]["origin"], "manual")
        reopened = ArtCache(self.root)
        # Another approved suggestion keeps one original, not an image chain.
        reopened.apply_review("1", reopened.cover_snapshot("1"), result("green"))
        self.assertEqual(len(list((self.root / "review").iterdir())), 2)
        reopened.undo_review("1", reopened.cover_snapshot("1"))
        self.assertEqual(reopened.cover_snapshot("1"), before)
        self.assertEqual((self.root / "1.png").read_bytes(), data)
        self.assertFalse((self.root / "1.jpg").exists())
        self.assertFalse(reopened.has_review_undo("1"))

    def test_later_manual_edit_wins_over_stale_review_and_undo(self):
        before = self.cache.cover_snapshot("1")
        other = ArtCache(self.root)
        other.store_ok("1", result("green"))
        with self.assertRaisesRegex(ValueError, "changed"):
            self.cache.apply_review("1", before, result())
        current = self.cache.cover_snapshot("1")
        self.cache.apply_review("1", current, result())
        other.store_ok("1", result("yellow"))
        latest = other.cover_snapshot("1")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.cache.undo_review("1", latest)
        self.assertEqual(self.cache.cover_snapshot("1"), latest)

    def test_corrupt_undo_or_bad_download_cannot_replace_current(self):
        before = self.cache.cover_snapshot("1")
        bad = ArtResult(b"invalid", "image/jpeg", 1, "source", "bad")
        with self.assertRaisesRegex(ValueError, "decoded"):
            self.cache.apply_review("1", before, bad)
        self.assertEqual(self.cache.cover_snapshot("1"), before)
        self.cache.apply_review("1", before, result())
        approved = self.cache.cover_snapshot("1")
        (self.root / "review" / "1.image").write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "Undo copy changed"):
            self.cache.undo_review("1", approved)
        self.assertEqual(self.cache.cover_snapshot("1"), approved)

    def test_publication_failure_keeps_image_index_and_recovery_unchanged(self):
        before = self.cache.cover_snapshot("1")
        with patch.object(self.cache._area, "publish", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.cache.apply_review("1", before, result())
        self.assertEqual(self.cache.cover_snapshot("1"), before)
        self.assertFalse(self.cache.has_review_undo("1"))

    def test_rescan_preserves_manual_legacy_real_covers_and_blocks(self):
        avatar = ArtResult(image("purple"), "image/jpeg", .9,
                           "https://soundcloud.com/artist", "Artist")
        self.cache.store_ok("manual-avatar", avatar)
        self.cache.store_ok("legacy-avatar", avatar)
        self.cache.store_ok("automatic-avatar", avatar, origin="auto")
        self.cache.store_ok("automatic-cover", result(), origin="auto")
        self.cache.block("blocked")
        self.cache.store_none("miss")
        raw = json.loads((self.root / "index.json").read_text())
        del raw["legacy-avatar"]["origin"]
        (self.root / "index.json").write_text(json.dumps(raw))
        cache = ArtCache(self.root)
        protected = {tid: cache.cover_snapshot(tid) for tid in
                     ("1", "manual-avatar", "legacy-avatar", "automatic-cover")}
        self.assertEqual(cache.retry_weak(), 2)
        for tid, snapshot in protected.items():
            self.assertEqual(cache.cover_snapshot(tid), snapshot)
        self.assertEqual(cache.status("blocked"), "blocked")

    def test_inflight_background_success_or_miss_cannot_overwrite_manual_edit(self):
        from desktop.workers import ArtFillWorker
        from soundbox.library import Library, Track
        source = Path(self.temp.name) / "source.wav"
        source.write_bytes(b"source must stay unchanged")
        for fetched in (result(), None):
            self.cache.forget("2")
            cache = self.cache
            class Provider:
                def fetch_best(self, *_):
                    cache.store_ok("2", result("green"))
                    return fetched
            lib = Library({"2": Track("2", "Song", "Artist", source.as_uri())}, [], "synthetic")
            worker = ArtFillWorker(lib, cache, {}, {}, provider=Provider())
            worker.DELAY = 0
            worker.run()
            self.assertEqual(cache.index["2"]["origin"], "manual")
            self.assertEqual(cache.cover_snapshot("2").data, result("green").data)
        self.assertEqual(source.read_bytes(), b"source must stay unchanged")


class ReviewUI(unittest.TestCase):
    def wait_cover(self, dialog=None):
        dialog = dialog or self.dialog
        deadline = time.monotonic() + 3
        while (dialog._cover_worker is not None or dialog._cover_pending is not None) and time.monotonic() < deadline:
            _app.processEvents()
            time.sleep(.001)
        self.assertIsNone(dialog._cover_worker)
        self.assertIsNone(dialog._cover_pending)
        self.assertIsNotNone(dialog._snapshot)

    def test_blocking_one_uploader_preserves_other_ready_comparisons(self):
        from desktop.artreview import ArtReviewDialog
        self.dialog.shutdown()
        self.cache.store_ok("2",result("green"))
        def candidates(artist,title):
            return [dict(title=title,uploader="Artist",score=.95,thumb="thumb",
                         url="https://soundcloud.com/"+("bad-source" if title=="First" else "good-source")+"/song")]
        with patch.object(self.provider,"search_candidates",side_effect=candidates):
            dialog=ArtReviewDialog(self.cache,[("1","Artist","First","Manual"),("2","Artist","Second","Manual")],provider=self.provider)
            self.addCleanup(dialog.deleteLater)
            self.addCleanup(dialog.shutdown)
            dialog._scan()
            deadline=time.monotonic()+3
            while dialog._tasks.pending and time.monotonic()<deadline:
                _app.processEvents();time.sleep(.001)
            self.assertEqual(dialog._suggested,{"1","2"})
            remaining=dialog._metadata["2"]["candidates"][:]
            dialog._dismissed.add("earlier-next")
            self.cache.exclude_source("https://soundcloud.com/bad-source")
            dialog._exclusions_updated()
            self.assertEqual(dialog._metadata["2"]["candidates"],remaining)
            self.assertIn("2",dialog._cached)
            self.assertIn("2",dialog._suggested)
            self.assertNotIn("1",dialog._suggested)
            self.assertIn("earlier-next",dialog._dismissed)
            labels=[action.text() for action in dialog.more.menu().actions()]
            self.assertEqual([label for label in labels if "Exclud" in label],["Exclude Uploader…"])
            self.assertNotIn("Undo last change",labels)

    def test_artist_profile_previews_before_approval_and_preserves_undo(self):
        self.wait_idle()
        profile=ArtResult(image("purple"),"image/jpeg",.97,"https://soundcloud.com/artist","Artist (avatar)")
        def preview(box):
            self.assertEqual(self.cache.cover_snapshot("1"),self.before)
            self.assertFalse(box.iconPixmap().isNull())
            self.assertIn("https://soundcloud.com/artist",box.informativeText())
        with patch.object(self.provider,"fetch_profile_art",return_value=profile,create=True) as fetch, \
                patch.object(QMessageBox,"exec",preview), \
                patch.object(QMessageBox,"clickedButton",lambda box: next(b for b in box.buttons()
                    if box.buttonRole(b)==QMessageBox.AcceptRole)):
            self.dialog._controls()
            self.assertTrue(self.dialog._profile_action.isEnabled())
            self.dialog._use_profile()
            self.wait_idle()
        fetch.assert_called_once_with("Artist")
        self.assertEqual(self.cache.index["1"]["src"],profile.source_url)
        self.assertEqual(self.cache.index["1"]["origin"],"manual")
        self.assertTrue(self.cache.has_review_undo("1"))
        self.dialog._undo()
        self.assertEqual(self.cache.cover_snapshot("1"),self.before)

    def test_artist_profile_cancel_unavailable_and_wrong_track_source_never_change_cover(self):
        self.wait_idle()
        profile=ArtResult(image("purple"),"image/jpeg",.97,"https://soundcloud.com/artist","Artist (avatar)")
        for answer in (None,result("purple"),profile):
            with patch.object(self.provider,"fetch_profile_art",return_value=answer,create=True), \
                    patch.object(QMessageBox,"exec",lambda box: None), \
                    patch.object(QMessageBox,"clickedButton",return_value=None):
                self.dialog._controls()
                self.dialog._use_profile()
                self.wait_idle()
                self.assertEqual(self.cache.cover_snapshot("1"),self.before)
        self.assertFalse(self.cache.has_review_undo("1"))

    def test_finished_but_undelivered_artist_profile_does_not_apply_after_close(self):
        self.wait_idle()
        profile=ArtResult(image("purple"),"image/jpeg",.97,"https://soundcloud.com/artist","Artist (avatar)")
        with patch.object(self.provider,"fetch_profile_art",return_value=profile,create=True):
            self.dialog._controls()
            self.dialog._use_profile()
            worker=self.dialog._worker
            self.assertTrue(worker.wait(2000))
            self.dialog.reject()
            self.dialog.shutdown()
        self.assertEqual(self.cache.cover_snapshot("1"),self.before)

    def test_keep_replace_and_next_follow_selected_position_without_returning_to_first(self):
        from desktop.artreview import ArtReviewDialog
        self.dialog.shutdown()
        for tid in ("2","3","4","5"):
            self.cache.store_ok(tid,result("green"))
        dialog=ArtReviewDialog(self.cache,[(str(i),"Artist",f"Song {i}","Manual") for i in range(1,6)],provider=self.provider)
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.shutdown)
        def idle():
            deadline=time.monotonic()+3
            while time.monotonic()<deadline:
                _app.processEvents()
                if not dialog._tasks.pending:break
                time.sleep(.001)
            self.assertFalse(dialog._tasks.pending)
        dialog._scan();idle()
        dialog.tracks.setCurrentRow(2);idle()
        dialog._keep();idle()
        self.assertEqual(dialog._row[0],"4")
        dialog._approve();idle()
        self.assertEqual(dialog._row[0],"5")
        dialog._next();idle()
        dialog._filter()  # later preparation callbacks must not wrap the cursor
        self.assertIsNone(dialog._row)
        self.assertFalse(dialog.tracks.item(0).isHidden())
        dialog.tracks.setCurrentRow(0);idle()
        self.assertEqual(dialog._row[0],"1")

    def test_reopen_shows_saved_comparison_while_new_first_track_is_still_searching(self):
        from desktop.artreview import ArtReviewDialog
        self.wait_idle()
        self.dialog.shutdown()
        self.cache.store_ok("2",result("green"))
        entered,release=Event(),Event()
        def search(*args):
            self.assertEqual(args,("Artist","New Song"))
            entered.set()
            release.wait(3)
            return []
        with patch.object(self.provider,"search_candidates",side_effect=search):
            dialog=ArtReviewDialog(self.cache,[("2","Artist","New Song","Manual"),
                                               ("1","Artist","Song","Manual")],provider=self.provider)
            self.addCleanup(dialog.deleteLater)
            self.addCleanup(dialog.shutdown)
            self.addCleanup(release.set)
            dialog._scan()
            self.assertTrue(entered.wait(1))
            deadline=time.monotonic()+2
            while time.monotonic()<deadline:
                _app.processEvents()
                if dialog.candidates.count():break
                time.sleep(.001)
            self.assertIs(dialog._views.currentWidget(),dialog._review_page)
            self.assertEqual(dialog._row[0],"1")
            self.assertEqual(dialog._saved_count,1)
            self.assertEqual(dialog._last_progress,(0,1))
            self.assertIn("1 saved checks",dialog._library_progress.text())
            self.assertEqual(self.cache.cover_snapshot("1"),self.before)
            release.set()
            dialog.shutdown()

    def test_loading_wheel_precedes_results_and_empty_completion_is_explained(self):
        from desktop.artreview import ArtReviewDialog
        self.dialog.shutdown()
        entered,release=Event(),Event()
        def search(*_):
            entered.set()
            release.wait(2)
            return []
        with patch.object(self.provider,"search_candidates",search):
            dialog=ArtReviewDialog(self.cache,[("1","Artist","Song","Manual")],provider=self.provider)
            self.addCleanup(dialog.deleteLater)
            self.addCleanup(dialog.shutdown)
            self.assertIs(dialog._views.currentWidget(),dialog._loading_page)
            self.assertTrue(dialog._loading_spinner._timer.isActive())
            dialog._scan()
            self.assertTrue(entered.wait(1))
            self.assertIs(dialog._views.currentWidget(),dialog._loading_page)
            release.set()
            deadline=time.monotonic()+3
            while dialog._tasks.pending and time.monotonic()<deadline:
                _app.processEvents()
                time.sleep(.001)
            self.assertEqual(dialog._tasks.pending,0)
            self.assertIs(dialog._views.currentWidget(),dialog._empty_page)
            self.assertIn("1",dialog._empty_detail.text())
            self.assertFalse(dialog._loading_spinner._timer.isActive())
            self.assertEqual(self.cache.cover_snapshot("1"),self.before)
    def test_changing_rows_ignores_finished_but_undelivered_old_suggestions(self):
        from desktop.artreview import ArtReviewDialog
        self.cache.store_ok("2",result("green"))
        def candidates(artist,title):
            return [dict(title=title,uploader=artist,url=f"https://soundcloud.com/artist/{artist}",
                         thumb="thumb",score=.9)]
        with patch.object(self.provider,"search_candidates",side_effect=candidates):
            dialog=ArtReviewDialog(self.cache,[("1","first","First","Manual"),("2","second","Second","Manual")],provider=self.provider)
            self.addCleanup(dialog.deleteLater)
            self.addCleanup(dialog.shutdown)
            dialog.tabs.setCurrentIndex(1)
            self.wait_idle(dialog)
            dialog._search()
            self.assertTrue(dialog._worker.wait(2000))
            self.assertTrue(dialog.tracks.isEnabled())
            dialog.tracks.setCurrentRow(1)
            deadline=time.monotonic()+3
            while time.monotonic()<deadline:
                _app.processEvents()
                if not dialog._tasks.pending and dialog.candidates.count():
                    break
                time.sleep(.001)
            self.assertEqual(dialog.candidates.count(),1)
            candidate,_=dialog.candidates.item(0).data(256)
            self.assertEqual(candidate["uploader"],"second")

    def test_possible_match_is_selectable_and_keep_persists_without_changing_image(self):
        from desktop.artreview import _usable
        candidate = dict(score=.72,possible=True,title="Variant",uploader="Artist's Archive",url="https://soundcloud.com/archive/song")
        self.assertTrue(_usable(candidate,self.before,()))
        self.wait_idle()
        self.dialog._keep()
        self.assertEqual(self.cache.cover_snapshot("1"),self.before)
        self.assertEqual(self.cache.kept_covers()["1"]["digest"],self.before.digest)
    def setUp(self):
        from desktop.artreview import ArtReviewDialog
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = ArtCache(Path(self.temp.name) / "art")
        self.cache.store_ok("1", result("red"))
        self.before = self.cache.cover_snapshot("1")
        self.calls = []
        owner = self
        class Provider:
            def search_candidates(self, *_):
                owner.calls.append("search")
                return [dict(title="Artist - Song (Remix)", uploader="Artist", thumb="thumb",
                             url="https://soundcloud.com/artist/remix", score=.9, liked=True)]

            def preview_image(self, _):
                return image("blue")

            def fetch_thumb(self, *_):
                owner.calls.append("fetch")
                return result()
        self.provider = Provider()
        self.dialog = ArtReviewDialog(self.cache, [("1", "Artist", "Song", "Manual")],
                                      provider=self.provider)
        self.dialog.tabs.setCurrentIndex(1)  # deliberately browse a manual cover
        self.addCleanup(self.dialog.deleteLater)
        self.addCleanup(self.dialog.shutdown)

    def wait_idle(self, dialog=None):
        dialog = dialog or self.dialog
        _app.processEvents()  # dispatch an automatic selection search first
        deadline = time.monotonic() + 3
        while dialog._tasks.pending and time.monotonic() < deadline:
            _app.processEvents()
            time.sleep(.001)
        self.assertEqual(dialog._tasks.pending, 0)

    def test_open_search_compare_keep_and_close_never_change_art(self):
        self.assertEqual(self.calls, [])
        self.wait_idle()
        self.assertEqual(self.calls, ["search"])
        self.assertIn("public likes", self.dialog.evidence.text())
        self.assertTrue(self.dialog.use.isEnabled())
        self.dialog._next()
        self.dialog.reject()
        self.dialog.shutdown()
        self.assertEqual(self.cache.cover_snapshot("1"), self.before)
        self.assertFalse(self.cache.has_review_undo("1"))

    def test_individual_approval_and_undo_refresh_only_that_track(self):
        changed = []
        self.dialog.changed.connect(changed.append)
        self.dialog._search()
        self.wait_idle()
        with patch.object(QMessageBox, "question", side_effect=AssertionError("Replace is already explicit approval")):
            self.dialog._approve()
        self.wait_idle()
        self.assertNotEqual(self.cache.cover_snapshot("1"), self.before)
        self.assertTrue(self.dialog.undo.isEnabled())
        self.dialog._undo()
        self.assertEqual(self.cache.cover_snapshot("1"), self.before)
        self.assertEqual(changed, ["1", "1"])

    def test_close_with_finished_but_undelivered_fetch_does_not_apply(self):
        self.dialog._search()
        self.wait_idle()
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            self.dialog._approve()
        worker = next(iter(self.dialog._tasks._workers))
        self.assertTrue(worker.wait(2000))
        self.dialog.reject()
        self.dialog.shutdown()
        self.assertEqual(self.cache.cover_snapshot("1"), self.before)

    def test_repeated_nested_modals_retire_without_stale_approval(self):
        from types import SimpleNamespace
        from PySide6.QtCore import QCoreApplication, QEvent
        from PySide6.QtWidgets import QDialog
        from shiboken6 import isValid
        from desktop.artreview import ArtPickerDialog
        from desktop.uploader_exclusions import UploaderExclusionsDialog
        self.wait_idle()
        modals = []
        drained = []
        picker_shutdown = ArtPickerDialog.shutdown

        def drain_picker(dialog):
            picker_shutdown(dialog)
            drained.append(dialog)

        def picker_exec(dialog):
            modals.append(dialog)
            dialog.choice = {'kind': 'file', 'path': '/synthetic/cover.jpg'}
            return QDialog.Accepted

        def exclusions_exec(dialog):
            modals.append(dialog)
            return QDialog.Rejected

        def profile_exec(box):
            modals.append(box)
            return QMessageBox.Cancel

        profile = SimpleNamespace(result=result(), error='')
        with patch.object(ArtPickerDialog, 'exec', picker_exec), \
                patch.object(ArtPickerDialog, 'shutdown', drain_picker), \
                patch.object(UploaderExclusionsDialog, 'exec', exclusions_exec), \
                patch.object(QMessageBox, 'exec', profile_exec), \
                patch.object(self.dialog, '_start_replace') as replace, \
                patch.object(self.dialog, '_apply_retired') as apply:
            for _ in range(3):
                self.dialog._choose_other()
                self.dialog._manage_exclusions()
                self.dialog._profile_retired(profile, '1', 'Artist', self.before)
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                self.assertTrue(all(not isValid(dialog) for dialog in modals))
            self.assertEqual(replace.call_count, 3)
            self.assertEqual(len(drained), 3)
            apply.assert_not_called()

            def closed_profile(box):
                modals.append(box)
                self.dialog._tasks.stop()
                return QMessageBox.Ok

            with patch.object(QMessageBox, 'exec', closed_profile), \
                    patch.object(QMessageBox, 'clickedButton',
                                 lambda box: next(button for button in box.buttons()
                                                  if button.text() == 'Use profile')):
                self.dialog._profile_retired(profile, '1', 'Artist', self.before)
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            self.assertTrue(all(not isValid(dialog) for dialog in modals))
            apply.assert_not_called()
        self.assertEqual(self.cache.cover_snapshot('1'), self.before)

    def test_shutdown_drains_blocked_search_and_rejects_late_preview(self):
        from PySide6.QtCore import QTimer
        entered, release = Event(), Event()
        def search(*_):
            entered.set()
            release.wait(2)
            return []
        with patch.object(self.provider, "search_candidates", search):
            self.wait_cover()
            self.dialog._search()
            self.assertTrue(entered.wait(1))
            self.dialog.reject()
            QTimer.singleShot(10, release.set)
            self.dialog.shutdown()
        self.assertEqual(self.dialog.candidates.count(), 0)
        self.assertEqual(self.cache.cover_snapshot("1"), self.before)

    def test_suggested_is_focused_all_is_optional_and_titles_are_not_repeated(self):
        from desktop.artreview import ArtReviewDialog
        self.cache.store_ok("2", ArtResult(image("green"), "image/jpeg", 1,
            "https://soundcloud.com/artist", "Artist avatar"), origin="auto")
        dialog = ArtReviewDialog(self.cache, [
            ("1", "Artist", "Artist - Good Song", "Manual"),
            ("2", "AD & AKIRA", "AD & AKIRA - U MELT! PT.2", "Automatic"),
        ], provider=self.provider)
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.shutdown)
        dialog._scan()
        deadline = time.monotonic() + 3
        while dialog._tasks.pending and time.monotonic() < deadline:
            _app.processEvents()
            time.sleep(.001)
        self.assertEqual(dialog.tabs.currentIndex(), 0)
        self.assertFalse(dialog.tracks.item(0).isHidden())  # manual covers are included too
        self.assertFalse(dialog.tracks.item(1).isHidden())
        dialog.tracks.setCurrentRow(1)
        self.wait_cover(dialog)
        self.assertEqual(dialog.tracks.item(1).text(), "AD & AKIRA - U MELT! PT.2")
        self.assertEqual(dialog.title.text(), "AD & AKIRA - U MELT! PT.2")
        self.assertIn("Automatic", dialog.source.text())
        self.assertIn("artist picture", dialog.source.text())
        dialog.tabs.setCurrentIndex(1)
        self.assertFalse(dialog.tracks.item(0).isHidden())

    def test_selecting_a_track_checks_automatically_without_search_click(self):
        self.wait_idle()
        self.assertEqual(self.calls, ["search"])
        self.assertEqual(self.dialog.candidates.count(), 1)
        self.assertEqual(self.cache.cover_snapshot("1"), self.before)

    def test_current_source_same_pixels_sellers_and_weak_matches_are_omitted(self):
        choices = [
            dict(title="Song", uploader="Artist", thumb="same-url", url=self.before.entry["src"], score=1),
            dict(title="Song", uploader="Artist", thumb="same-image", url="https://soundcloud.com/artist/duplicate", score=1),
            dict(title="Song", uploader="Dubplate Dealer", thumb="blocked", url="https://soundcloud.com/hidden-name/song", score=1),
            dict(title="Wrong", uploader="Artist", thumb="weak", url="https://soundcloud.com/artist/weak", score=.3),
            dict(title="Song", uploader="Artist", thumb="good", url="https://soundcloud.com/artist/better", score=.9),
        ]
        with patch.object(self.provider, "search_candidates", return_value=choices), \
                patch.object(self.provider, "preview_image", side_effect=lambda c:
                             self.before.data if c["thumb"] == "same-image" else image("blue")):
            self.wait_idle()
        self.assertEqual(self.dialog.candidates.count(), 1)
        candidate, _ = self.dialog.candidates.item(0).data(256)
        self.assertEqual(candidate["thumb"], "good")

    def test_automatic_preparation_streams_suggestions_without_changing_art(self):
        from desktop.artreview import ArtReviewDialog
        for i in range(2, 38):
            self.cache.store_ok(str(i), ArtResult(image("red"), "image/jpeg", 1,
                "https://soundcloud.com/artist", "Artist avatar"), origin="auto")
        rows = [(str(i), "Artist", f"Song {i}", "Automatic") for i in range(2, 38)]
        dialog = ArtReviewDialog(self.cache, rows, provider=self.provider)
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.shutdown)
        before = {tid: self.cache.cover_snapshot(tid) for tid, *_ in rows}
        self.assertEqual(dialog.tabs.currentIndex(), 0)
        self.assertIsNone(dialog._row)
        dialog._scan()
        self.assertEqual(len(dialog._prepare_worker.rows), 36)
        deadline = time.monotonic() + 4
        while dialog._tasks.pending and time.monotonic() < deadline:
            _app.processEvents()
            time.sleep(.001)
        self.assertEqual(dialog._tasks.pending, 0)
        self.assertEqual(len(dialog._checked), 36)
        self.assertEqual(dialog.tabs.currentIndex(), 0)
        self.assertEqual(sum(not dialog.tracks.item(i).isHidden() for i in range(dialog.tracks.count())), 36)
        self.assertLessEqual(len(dialog._cached), 256)
        self.assertEqual([dialog.keep.text(),dialog.use.text(),dialog.next.text()], ["Keep","Replace","Next"])
        for tid, snapshot in before.items():
            self.assertEqual(self.cache.cover_snapshot(tid), snapshot)
        self.assertFalse((self.cache.root / "review").exists())

    def test_starting_background_scan_does_not_start_a_foreground_search(self):
        self.dialog.tabs.setCurrentIndex(0)
        self.dialog._generation += 1  # cancel deferred All-selection search
        self.dialog._scan()
        self.assertIsNone(self.dialog._worker)
        self.assertEqual(self.calls, [])

    def test_batch_close_drains_and_discards_late_results(self):
        from desktop.artreview import ArtReviewDialog
        from PySide6.QtCore import QTimer
        self.cache.store_ok("2", ArtResult(image("green"), "image/jpeg", 1,
            "https://soundcloud.com/artist", "Artist avatar"), origin="auto")
        dialog = ArtReviewDialog(self.cache, [("2", "Artist", "Song 2", "Automatic")], provider=self.provider)
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.shutdown)
        entered, release = Event(), Event()
        def search(*_):
            entered.set()
            release.wait(2)
            return []
        with patch.object(self.provider, "search_candidates", search):
            dialog._scan()
            self.assertTrue(entered.wait(1))
            dialog.reject()
            QTimer.singleShot(10, release.set)
            dialog.shutdown()
        self.assertEqual(len(dialog._cached), 0)
        self.assertEqual(dialog._checked, set())
