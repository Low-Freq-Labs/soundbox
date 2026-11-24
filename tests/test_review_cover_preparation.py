"""Exact review-cover rendering and bounded, interactive selection preparation."""
from io import BytesIO
from pathlib import Path
from threading import Event
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from PIL import Image, ImageCms, ImageDraw, ImageChops, ImageOps, ImageStat, ImageFilter
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from desktop import artreview
from desktop.artpicker import _CandidateSearch
from desktop.artreview import ArtReviewDialog, _CoverSelection, _ReviewCover, _PreviewComparison
from soundbox.artwork import ArtCache, ArtResult, CoverSnapshot


def picture(color, *, size=(80, 80), fmt='JPEG'):
    output = BytesIO()
    Image.new('RGB', size, color).save(output, fmt)
    return output.getvalue()


def original_same_art(original, preview):
    """Frozen installed6f full-decode oracle, before sample extraction/reuse."""
    try:
        with Image.open(BytesIO(original)) as a, Image.open(BytesIO(preview)) as b:
            a = ImageOps.fit(a.convert('RGB'), (64, 64), method=Image.Resampling.LANCZOS)
            b = ImageOps.fit(b.convert('RGB'), (64, 64), method=Image.Resampling.LANCZOS)
            if sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / 3 <= 3:
                return True
            a = a.resize((32, 32), Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(1))
            b = b.resize((32, 32), Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(1))
            if sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / 3 > 8:
                return False
            x, y = list(a.convert('L').get_flattened_data()), list(b.convert('L').get_flattened_data())
            mx, my = sum(x) / len(x), sum(y) / len(y)
            vx, vy = sum((v-mx)**2 for v in x), sum((v-my)**2 for v in y)
            if not vx or not vy:
                return False
            return sum((u-mx)*(v-my) for u, v in zip(x, y)) / (vx*vy)**.5 >= .985
    except (OSError, ValueError):
        return False


class _Player(QObject):
    playback_error = Signal(str)

    def __init__(self):
        super().__init__()
        self.pauses = 0
        self.requested = False

    def review_transport_state(self):
        return dict(available=True, track_id='1', requested=self.requested,
                    loading=False, duration=1000, position=0)

    def start(self, _tid):
        self.requested = True
        return True

    def pause(self):
        self.pauses += 1
        self.requested = False


class ReviewCoverPreparation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = ArtCache(Path(self.temp.name) / 'art')
        for tid, color in (('1', 'red'), ('2', 'green'), ('3', 'blue')):
            self.cache.store_ok(tid, ArtResult(picture(color), 'image/jpeg', 1,
                f'file:///synthetic/{tid}.jpg', 'Selected'))
        self.rows = [(str(i), 'Artist', f'Title {i}', 'Manual') for i in (1, 2, 3)]
        self.release = Event()
        self.dialogs = []
        self.addCleanup(self.close)

    def close(self):
        self.release.set()
        for dialog in self.dialogs:
            dialog.reject()
            dialog.shutdown()
            dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def dialog(self, **kwargs):
        dialog = ArtReviewDialog(self.cache, self.rows, **kwargs)
        self.dialogs.append(dialog)
        dialog.tabs.setCurrentIndex(1)
        self.settle(dialog)
        return dialog

    def settle(self, dialog):
        deadline = time.monotonic() + 3
        while dialog._tasks.pending and time.monotonic() < deadline:
            self.app.processEvents()
            QTest.qWait(1)
        self.assertEqual(dialog._tasks.pending, 0)
        self.assertIsNone(dialog._cover_worker)
        self.assertIsNone(dialog._cover_pending)

    def hold(self):
        entered = Event()
        run = _ReviewCover.run
        decoded = []
        def held(worker):
            decoded.append((worker.selection.row[0], QThread.currentThread()))
            if len(decoded) == 1:
                entered.set()
                self.release.wait(2)
            run(worker)
        return entered, decoded, mock.patch.object(_ReviewCover, 'run', held)

    def test_full_decode_preserves_exact_png_jpeg_alpha_icc_orientation_at_both_scales(self):
        fixtures = [('RGB', 'JPEG', (911, 613), None, False),
                    ('RGB', 'JPEG', (613, 911), 6, False),
                    ('RGB', 'JPEG', (911, 613), 8, True),
                    ('RGBA', 'PNG', (911, 613), None, False),
                    ('RGBA', 'PNG', (613, 911), None, True),
                    ('P', 'PNG', (700, 700), None, False)]
        for mode, fmt, size, orientation, icc in fixtures:
            image = Image.new('RGBA' if mode == 'RGBA' else 'RGB', size,
                (67, 29, 213, 64) if mode == 'RGBA' else '#431dd5')
            draw = ImageDraw.Draw(image)
            draw.rectangle((1, 1, 177, 203), fill=(236, 41, 5, 177) if mode == 'RGBA' else '#ec2905')
            draw.line((0, 0, *size), fill='white', width=7)
            if mode == 'P':
                image = image.convert('P')
            options = {}
            if orientation:
                exif = Image.Exif();exif[274] = orientation;options['exif'] = exif
            if icc:
                options['icc_profile'] = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
            output = BytesIO();image.save(output, fmt, **options);data = output.getvalue()
            selection = _CoverSelection(1, self.rows[0], None, None, CoverSnapshot({}, data))
            worker = _ReviewCover(selection);worker.start();self.assertTrue(worker.wait(2000))
            self.assertEqual((worker.image.width(), worker.image.height()), size)
            previous = QPixmap();self.assertTrue(previous.loadFromData(data))
            prepared = QPixmap.fromImage(worker.image)
            for ratio in (1, 2):
                with self.subTest(mode=mode, fmt=fmt, size=size, orientation=orientation, icc=icc, ratio=ratio):
                    expected = previous.scaled(300*ratio, 300*ratio, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    actual = prepared.scaled(300*ratio, 300*ratio, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    self.assertEqual(actual.toImage(), expected.toImage())
            worker.deleteLater()

    def test_rapid_real_navigation_keeps_old_comparison_and_coalesces_to_latest(self):
        dialog = self.dialog();dialog.show();self.app.processEvents()
        old_image = dialog.current.pixmap().toImage();old_source = dialog.source.text()
        entered, decoded, held = self.hold()
        with held:
            dialog.tracks.setFocus()
            QTest.keyClick(dialog.tracks, Qt.Key_Down)
            self.assertTrue(entered.wait(1))
            self.assertEqual(dialog._row[0], '2')
            self.assertEqual(dialog.title.text(), 'Title 1')
            self.assertEqual(dialog.source.text(), old_source)
            self.assertEqual(dialog.current.pixmap().toImage(), old_image)
            self.assertIn('Preparing this comparison', dialog.status.text())
            self.assertFalse(dialog.keep.isEnabled());self.assertFalse(dialog.use.isEnabled())
            self.assertFalse(dialog.options.isEnabled());self.assertFalse(dialog.candidates.isEnabled())
            for _ in range(15):
                QTest.keyClick(dialog.tracks, Qt.Key_Down)
                QTest.keyClick(dialog.tracks, Qt.Key_Up)
            QTest.keyClick(dialog.tracks, Qt.Key_Down)
            self.assertEqual(dialog._row[0], '3')
            self.assertEqual(dialog._tasks.pending, 1)
            self.assertEqual([tid for tid, _thread in decoded], ['2'])
            self.release.set();self.settle(dialog)
        self.assertEqual([tid for tid, _thread in decoded], ['2', '3'])
        self.assertTrue(all(thread is not self.app.thread() for _tid, thread in decoded))
        self.assertEqual(dialog.title.text(), 'Title 3')
        self.assertIn('3.jpg', dialog.source.text())
        self.assertEqual(dialog._snapshot, self.cache.cover_snapshot('3'))
        self.assertTrue(dialog.keep.isEnabled())

    def test_first_comparison_reveal_has_complete_selected_cover(self):
        shown = []
        class Dialog(ArtReviewDialog):
            def _reveal(inner):
                super()._reveal()
                if inner._views.currentWidget() is inner._review_page:
                    shown.append((inner.title.text(), inner._snapshot,
                                  not inner.current.pixmap().isNull()))
        entered, _decoded, held = self.hold()
        with held:
            dialog = Dialog(self.cache, self.rows);self.dialogs.append(dialog)
            dialog.tabs.setCurrentIndex(1);self.assertTrue(entered.wait(1))
            self.assertIsNot(dialog._views.currentWidget(), dialog._review_page)
            self.assertEqual(shown, [])
            self.release.set();self.settle(dialog)
        self.assertEqual(shown, [('Title 1', self.cache.cover_snapshot('1'), True)])

    def test_close_button_works_during_decode_and_late_result_never_paints(self):
        dialog = self.dialog();dialog.show();self.app.processEvents()
        old_image = dialog.current.pixmap().toImage()
        entered, _decoded, held = self.hold()
        with held:
            dialog.tracks.setCurrentRow(1);self.assertTrue(entered.wait(1))
            close = next(button for button in dialog._review_page.findChildren(type(dialog.keep))
                         if button.text() == 'Close')
            QTest.mouseClick(close, Qt.LeftButton)
            self.assertFalse(dialog.isVisible());self.assertTrue(dialog._tasks.stopping)
            self.assertFalse(self.release.is_set())
            self.release.set();dialog.shutdown()
        self.assertIsNone(dialog._snapshot)
        self.assertEqual(dialog.current.pixmap().toImage(), old_image)
        self.assertEqual(dialog._tasks.pending, 0)

    def test_finished_undelivered_decode_is_rejected_after_close(self):
        dialog = self.dialog();old_image = dialog.current.pixmap().toImage()
        dialog.tracks.setCurrentRow(1);worker = dialog._cover_worker
        self.assertTrue(worker.wait(2000));dialog.reject();dialog.shutdown()
        self.assertIsNone(dialog._snapshot)
        self.assertEqual(dialog.current.pixmap().toImage(), old_image)

    def test_same_track_refresh_preserves_actual_review_audition(self):
        player = _Player();dialog = self.dialog(player=player, play_track=player.start)
        dialog.audition._toggle();self.assertTrue(player.requested)
        entered, _decoded, held = self.hold()
        with held:
            dialog._selected(dialog.tracks.currentItem());self.assertTrue(entered.wait(1))
            self.assertEqual(player.pauses, 0);self.assertTrue(player.requested)
            self.assertEqual(dialog.audition._track, '1')
            self.release.set();self.settle(dialog)
        self.assertEqual(player.pauses, 0);self.assertTrue(player.requested)
        dialog.tracks.setCurrentRow(1);self.assertEqual(player.pauses, 1)
        self.settle(dialog)

    def test_cache_replacement_cover_change_and_restore_invalidate_delivery(self):
        mutations = (lambda dialog: self.cache.store_ok('2', ArtResult(picture('purple'),
            'image/jpeg', 1, 'file:///new.jpg', 'New')),
            lambda dialog: setattr(dialog, 'cache', ArtCache(Path(self.temp.name) / 'other')),
            lambda dialog: setattr(self.cache._area, 'generation', self.cache._area.generation+1))
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.release.clear();dialog = self.dialog();entered, _decoded, held = self.hold()
                with held:
                    dialog.tracks.setCurrentRow(1);self.assertTrue(entered.wait(1))
                    mutate(dialog);self.release.set();self.settle(dialog)
                self.assertIsNone(dialog._snapshot)
                self.assertIn('cover changed', dialog.status.text())
                self.assertFalse(dialog.keep.isEnabled())

    def test_global_exclusions_and_last_changed_undo_survive_missing_selection(self):
        expected = self.cache.cover_snapshot('1')
        self.cache.apply_review('1', expected, ArtResult(picture('purple'), 'image/jpeg',
            1, 'file:///replacement.jpg', 'Replacement'))
        dialog = self.dialog();dialog._last_changed = '1'
        dialog.filter.setText('no matching tracks')
        self.assertIsNone(dialog._row);self.assertIsNone(dialog._snapshot)
        self.assertTrue(dialog._exclude_action.isEnabled());self.assertTrue(dialog.undo.isEnabled())
        dialog._undo();self.assertEqual(self.cache.cover_snapshot('1'), expected)

    def test_external_bytes_changed_during_decode_keep_exact_recovery_guard(self):
        dialog = self.dialog();captured = self.cache.cover_snapshot('2')
        entered, _decoded, held = self.hold()
        external = picture('purple')
        with held:
            dialog.tracks.setCurrentRow(1);self.assertTrue(entered.wait(1))
            (self.cache.root / '2.jpg').write_bytes(external)
            self.release.set();self.settle(dialog)
        self.assertEqual(dialog._snapshot, captured)
        with self.assertRaisesRegex(ValueError, 'cover changed'):
            self.cache.apply_review('2', dialog._snapshot,
                ArtResult(picture('yellow'), 'image/jpeg', 1, 'file:///new.jpg', 'New'))
        self.assertEqual((self.cache.root / '2.jpg').read_bytes(), external)
        self.assertFalse(self.cache.has_review_undo('2'))

    def test_pending_cover_does_not_seed_global_exclusions_from_old_candidate(self):
        dialog = self.dialog()
        candidate = dict(title='Suggestion', uploader='Artist', score=.95,
                         url='https://soundcloud.com/artist/song')
        dialog._found(candidate, picture('blue'), comparison=dialog._selected_comparison())
        self.assertIsNotNone(dialog.candidates.currentItem())
        entered, _decoded, held = self.hold()
        modal = SimpleNamespace(changed=mock.Mock(), exec=mock.Mock(), deleteLater=mock.Mock())
        with held, mock.patch('desktop.uploader_exclusions.UploaderExclusionsDialog', return_value=modal) as create:
            dialog.tracks.setCurrentRow(1);self.assertTrue(entered.wait(1))
            self.assertTrue(dialog._exclude_action.isEnabled())
            dialog._manage_exclusions()
            self.assertEqual(create.call_args.kwargs['selected_url'], '')
            self.release.set();self.settle(dialog)

    def test_corrupt_or_unreadable_cover_settles_explicitly_before_first_reveal(self):
        (self.cache.root / '1.jpg').write_bytes(b'not an image')
        dialog = self.dialog()
        self.assertEqual(dialog.current.text(), 'Artwork unavailable')
        self.assertEqual(dialog._snapshot.data, b'not an image')
        self.assertIs(dialog._views.currentWidget(), dialog._review_page)
        with mock.patch.object(self.cache, 'cover_snapshot', side_effect=OSError('cannot read selected cover')):
            dialog.tracks.setCurrentRow(1)
        self.assertIsNone(dialog._snapshot)
        self.assertEqual(dialog.current.text(), 'Artwork unavailable')
        self.assertEqual(dialog.title.text(), 'Title 2')
        self.assertIn('cannot read selected cover', dialog.status.text())
        self.assertFalse(dialog.keep.isEnabled())

    def test_delivery_policy_read_failure_retires_without_a_partial_active_target(self):
        dialog = self.dialog();expected = self.cache.cover_snapshot('2')
        entered, _decoded, held = self.hold()
        with held:
            dialog.tracks.setCurrentRow(1);self.assertTrue(entered.wait(1))
            with mock.patch.object(self.cache, 'source_excluded', side_effect=OSError('policy unreadable')), \
                    mock.patch('sys.excepthook') as unhandled:
                self.release.set();self.settle(dialog)
        unhandled.assert_not_called()
        self.assertIsNone(dialog._snapshot)
        self.assertIn('policy unreadable', dialog.status.text())
        self.assertEqual(dialog.current.text(), 'Artwork unavailable')
        self.assertEqual(dialog.title.text(), 'Title 2')
        self.assertFalse(dialog.keep.isEnabled());self.assertFalse(dialog.use.isEnabled())
        self.assertEqual(self.cache.cover_snapshot('2'), expected)

    def test_actual_large_decode_keeps_gui_input_and_heartbeat_running(self):
        output = BytesIO()
        Image.effect_noise((3000, 3000), 32).convert('RGB').save(output, 'PNG')
        (self.cache.root / '2.png').write_bytes(output.getvalue())
        self.cache.index['2'] = {**self.cache.index['2'], 'ext': 'png'}
        dialog = self.dialog();dialog.show();self.app.processEvents();dialog.tracks.setFocus()
        active_ticks = [];navigation = []
        def decoding():
            return dialog._cover_worker is not None and dialog._cover_worker.isRunning()
        timer = QTimer();timer.setInterval(2)
        timer.timeout.connect(lambda: active_ticks.append(1) if decoding() else None)
        timer.start()
        QTest.keyClick(dialog.tracks, Qt.Key_Down)
        def navigate():
            navigation.append(decoding())
            QTest.keyClick(dialog.tracks, Qt.Key_Down)
        QTimer.singleShot(15, dialog, navigate)
        self.settle(dialog);timer.stop()
        self.assertEqual(navigation, [True])
        self.assertGreaterEqual(len(active_ticks), 3)
        self.assertEqual(dialog._row[0], '3')
        self.assertEqual(dialog.title.text(), 'Title 3')

    def test_sample_reuse_matches_original_equal_recompressed_different_corrupt_and_thresholds(self):
        def encoded(image, fmt='PNG', **kwargs):
            output = BytesIO();image.save(output, fmt, **kwargs);return output.getvalue()
        image = Image.new('RGB', (600, 600), '#531982');draw = ImageDraw.Draw(image)
        for i in range(36):
            draw.ellipse((i*13, i*9, 600-i*3, 600-i*7), outline=(i*7%256, i*31%256, i*53%256), width=7)
        draw.rectangle((90, 400, 510, 540), fill='#15eac2')
        original = encoded(image, 'JPEG', quality=95)
        pairs = [(original, original), (original, encoded(image.resize((90, 90)), 'JPEG', quality=20)),
                 (original, encoded(image.transpose(Image.Transpose.FLIP_TOP_BOTTOM))),
                 (original, encoded(ImageOps.invert(image))), (original, b'corrupt'),
                 (b'corrupt', original), (b'corrupt', b'corrupt')]
        gradient = Image.new('RGB', (64, 64))
        gradient.putdata([(64+x+y//2,)*3 for y in range(64) for x in range(64)])
        for offset in (0, 3, 4, 8, 9):
            shifted = gradient.point(lambda value: value+offset)
            pairs.append((encoded(gradient), encoded(shifted)))
        alpha = image.convert('RGBA');alpha.putalpha(64)
        pairs.extend([(encoded(image), encoded(alpha)), (encoded(alpha), encoded(image.convert('P')))])
        candidate = dict(score=.95, url='https://soundcloud.com/artist/song')
        for index, (current, preview) in enumerate(pairs):
            with self.subTest(pair=index):
                expected = original_same_art(current, preview)
                self.assertEqual(artreview._same_art(current, preview), expected)
                compare = _PreviewComparison(CoverSnapshot({'src':'file:///original'}, current), ())
                self.assertEqual(compare(candidate, preview), not expected)

    def test_cached_streamed_and_saved_previews_compare_once_per_original_off_gui(self):
        candidates = [dict(title=color, uploader='Artist', score=.95, thumb=color,
                           url=f'https://soundcloud.com/artist/{color}') for color in ('blue', 'green', 'purple')]
        images = {candidate['thumb']: picture(candidate['thumb']) for candidate in candidates}
        provider = SimpleNamespace(search_candidates=lambda *_args: candidates,
                                   preview_image=lambda candidate: images[candidate['thumb']])
        sample = artreview._art_sample
        for mode in ('cached', 'streamed', 'saved'):
            with self.subTest(mode=mode):
                dialog = self.dialog();snapshot = dialog._snapshot;calls = []
                def sampled(data):
                    calls.append((data == snapshot.data, QThread.currentThread()))
                    return sample(data)
                with mock.patch.object(artreview, '_art_sample', sampled):
                    if mode == 'cached':
                        dialog._remember('1', dict(entry=snapshot.entry, digest=snapshot.digest,
                            choices=[(candidate, images[candidate['thumb']]) for candidate in candidates]))
                        dialog._selected(dialog.tracks.currentItem())
                    else:
                        dialog.provider = provider
                        if mode == 'streamed':dialog._search()
                        else:dialog._load_preview(candidates)
                    self.settle(dialog)
                self.assertEqual(dialog.candidates.count(), 3)
                self.assertEqual(sum(original for original, _thread in calls), 1)
                self.assertTrue(all(thread is not self.app.thread() for _original, thread in calls))

    def test_late_cached_choices_continue_without_redecode_or_old_candidate_paint(self):
        dialog = self.dialog();snapshot = dialog._snapshot
        def cached(color):
            return dict(entry=snapshot.entry, digest=snapshot.digest, choices=[(
                dict(title=color, uploader='Artist', score=.95, url=f'https://soundcloud.com/artist/{color}'), picture(color))])
        dialog._remember('1', cached('blue'));entered, _decoded, held = self.hold()
        reader = artreview.QImageReader;sample = artreview._art_sample;originals = []
        def sampled(data):
            if data == snapshot.data:originals.append(1)
            return sample(data)
        with held, mock.patch.object(artreview, 'QImageReader', side_effect=reader) as readers, \
                mock.patch.object(artreview, '_art_sample', sampled), \
                mock.patch.object(dialog, '_paint_selected', wraps=dialog._paint_selected) as painted:
            dialog._selected(dialog.tracks.currentItem());self.assertTrue(entered.wait(1))
            dialog._remember('1', cached('green'))
            self.release.set();self.settle(dialog)
        self.assertEqual(readers.call_count, 1);self.assertEqual(originals, [1]);painted.assert_called_once()
        self.assertEqual(dialog.candidates.count(), 1)
        self.assertEqual(dialog.candidates.currentItem().data(Qt.UserRole)[0]['title'], 'green')

    def test_actual_large_cached_comparison_keeps_navigation_running_inside_pillow(self):
        output = BytesIO();Image.effect_noise((3000, 3000), 32).convert('RGB').save(output, 'PNG')
        data = output.getvalue();(self.cache.root/'2.png').write_bytes(data)
        self.cache.index['2'] = {**self.cache.index['2'], 'ext':'png'}
        dialog = self.dialog();snapshot = self.cache.cover_snapshot('2')
        candidate = dict(title='Repair', uploader='Artist', score=.95, url='https://soundcloud.com/artist/repair')
        dialog._remember('2', dict(entry=snapshot.entry, digest=snapshot.digest, choices=[(candidate, picture('purple'))]))
        entered = Event();sample = artreview._art_sample;navigation = [];ticks = []
        def sampled(payload):
            if payload == data:entered.set()
            return sample(payload)
        def poll():
            if entered.is_set():
                ticks.append(1)
                if not navigation:
                    navigation.append(dialog._cover_worker is not None and dialog._cover_worker.isRunning())
                    QTest.keyClick(dialog.tracks, Qt.Key_Down)
        dialog.show();self.app.processEvents();dialog.tracks.setFocus()
        timer = QTimer();timer.setInterval(2);timer.timeout.connect(poll);timer.start()
        with mock.patch.object(artreview, '_art_sample', sampled):
            QTest.keyClick(dialog.tracks, Qt.Key_Down);self.settle(dialog)
        timer.stop();self.assertEqual(navigation, [True]);self.assertGreaterEqual(len(ticks), 3)
        self.assertEqual(dialog.title.text(), 'Title 3')

    def test_picker_default_remains_unfiltered_and_optional_filter_detaches_payload(self):
        source = self.cache.cover_snapshot('1')
        candidate = dict(title='Same', uploader='Artist', score=.95, url='https://soundcloud.com/artist/same')
        provider = SimpleNamespace(search_candidates=lambda *_args: [candidate],
                                   preview_image=lambda _candidate: source.data)
        found = []
        worker = _CandidateSearch('Artist', 'Title', provider)
        worker.found.connect(lambda c, data: found.append((c, data)))
        worker.start();self.assertTrue(worker.wait(2000));self.app.processEvents();worker.deleteLater()
        self.assertEqual(found, [(candidate, source.data)])
        payload = bytearray(picture('blue'));original = bytes(payload);found.clear();captured = []
        provider.preview_image = lambda _candidate: payload
        def accept(c, data):
            captured.append((c is candidate, isinstance(data, bytes), QThread.currentThread()))
            return True
        worker = _CandidateSearch('Artist', 'Title', provider, accept_preview=accept)
        worker.found.connect(lambda c, data: found.append((c, data)))
        worker.start();self.assertTrue(worker.wait(2000));payload[:] = b'changed';candidate['title'] = 'Changed'
        self.app.processEvents();worker.deleteLater()
        self.assertEqual(found[0][1], original);self.assertEqual(found[0][0]['title'], 'Same')
        self.assertEqual(captured[0][:2], (False, True));self.assertIsNot(captured[0][2], self.app.thread())

    def test_first_cached_comparison_reveals_current_and_suggestion_together(self):
        source = self.cache.cover_snapshot('1');entered = Event();shown = []
        candidate = dict(title='Suggestion', uploader='Artist', score=.95, url='https://soundcloud.com/artist/song')
        class Dialog(ArtReviewDialog):
            def _reveal(inner):
                previous = inner._views.currentWidget()
                super()._reveal()
                if previous is not inner._review_page and inner._views.currentWidget() is inner._review_page:
                    shown.append((not inner.current.pixmap().isNull(), not inner.proposed.pixmap().isNull()))
        sample = artreview._art_sample
        def sampled(data):
            if data == source.data:entered.set();self.release.wait(2)
            return sample(data)
        dialog = Dialog(self.cache, self.rows);self.dialogs.append(dialog)
        dialog._remember('1', dict(entry=source.entry, digest=source.digest, choices=[(candidate, picture('blue'))]))
        with mock.patch.object(artreview, '_art_sample', sampled):
            dialog.tabs.setCurrentIndex(1);self.assertTrue(entered.wait(1))
            self.assertIsNot(dialog._views.currentWidget(), dialog._review_page);self.assertEqual(shown, [])
            self.release.set();self.settle(dialog)
        self.assertEqual(shown, [(True, True)])

    def test_streamed_policy_recheck_rejects_newly_excluded_candidate(self):
        dialog = self.dialog();source = dialog._snapshot;entered = Event()
        candidate = dict(title='Suggestion', uploader='Blocked', score=.95, thumb='thumb',
                         url='https://soundcloud.com/blocked/song')
        dialog.provider = SimpleNamespace(search_candidates=lambda *_args: [candidate],
                                          preview_image=lambda _candidate: picture('blue'))
        sample = artreview._art_sample
        def sampled(data):
            if data == source.data:entered.set();self.release.wait(2)
            return sample(data)
        with mock.patch.object(artreview, '_art_sample', sampled):
            dialog._search();self.assertTrue(entered.wait(1))
            self.cache.exclude_source('https://soundcloud.com/blocked')
            self.release.set();self.settle(dialog)
        self.assertEqual(dialog.candidates.count(), 0);self.assertFalse(dialog._busy)
        self.assertIs(dialog._snapshot, source)

    def test_finished_undelivered_streamed_comparison_cannot_record_restore_as_a_miss(self):
        dialog = self.dialog()
        candidate = dict(title='Suggestion', uploader='Artist', score=.95, thumb='thumb',
                         url='https://soundcloud.com/artist/song')
        dialog.provider = SimpleNamespace(search_candidates=lambda *_args: [candidate],
                                          preview_image=lambda _candidate: picture('blue'))
        dialog._search();worker = dialog._worker;self.assertTrue(worker.wait(2000))
        self.cache._area.generation += 1;self.settle(dialog)
        self.assertEqual(dialog.candidates.count(), 0);self.assertFalse(dialog._busy)
        self.assertNotIn('1', dialog._cached);self.assertNotIn('1', dialog._checked)
        self.assertIsNone(dialog._snapshot);self.assertIn('cover changed', dialog.status.text())

    def test_search_policy_capture_failure_keeps_prior_complete_view_and_retry_available(self):
        dialog = self.dialog();image = dialog.current.pixmap().toImage()
        dialog.provider = SimpleNamespace(search_candidates=mock.Mock())
        with mock.patch.object(self.cache, 'source_exclusions', side_effect=[(), OSError('policy unreadable')]):
            dialog._search()
        dialog.provider.search_candidates.assert_not_called()
        self.assertFalse(dialog._busy);self.assertIsNone(dialog._worker)
        self.assertEqual(dialog.current.pixmap().toImage(), image)
        self.assertIn('policy unreadable', dialog.status.text())


if __name__ == '__main__':
    unittest.main()
