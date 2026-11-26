"""Fresh setup choices and worker ownership on disposable libraries only."""
from pathlib import Path
import tempfile
from threading import Event
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QDialog

from desktop.onboarding import FirstLaunchDialog, configured_artwork, run_first_launch
from soundbox import library_codec, metadata_cleanup_policy, onboarding
from soundbox.library import Library, Track
from soundbox.native_reader_process import VERSION
from soundbox.native_reader_worker import serve
from soundbox.state_repository import StateRepository


def library():
    track = Track('1', 'Artist - Song (EP Version)', 'Artist', 'file:///fixture/song.wav')
    return Library({'1': track}, [], '/fixture/master.db', source_kind='rekordbox-db',
                   source_library_id='fixture-library')


class FirstLaunchState(unittest.TestCase):
    def test_cleanup_and_artwork_are_independent_and_preserve_unrelated_state(self):
        original = {'custom': {'Keep': ['1']}, 'settings': {'theme_accent': '#123456'}}
        for clean in (False, True):
            for artwork in (False, True):
                with self.subTest(clean=clean, artwork=artwork):
                    result = onboarding.setup_state(original, library(), clean=clean, artwork=artwork)
                    self.assertEqual(metadata_cleanup_policy.choice_for(result['settings'], library()),
                                     'clean' if clean else 'original')
                    self.assertIs(result['settings']['auto_artwork'], artwork)
                    self.assertEqual(result['custom'], original['custom'])
                    self.assertEqual(result['settings']['theme_accent'], '#123456')
                    self.assertEqual(original, {'custom': {'Keep': ['1']}, 'settings': {'theme_accent': '#123456'}})

    def test_preview_is_pure_and_does_not_treat_ep_as_the_artist(self):
        source = library()
        before = source.tracks_by_id['1'].to_row()
        result = onboarding.preview(source)
        self.assertEqual(result.library, source)
        self.assertEqual(source.tracks_by_id['1'].to_row(), before)
        self.assertTrue(all(example.after_artist != 'EP' for example in result.examples))

    def test_empty_start_and_cancel_are_fresh_but_recovery_and_dangling_state_are_not(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        repo = StateRepository(root / 'library.json')
        repo.load()
        self.assertFalse(onboarding.has_existing_state(root))
        for name in ('library.json', '.state-records', '.state-transaction', 'backups', 'art'):
            path = root / name
            path.symlink_to(root / 'missing-original')
            self.assertTrue(onboarding.has_existing_state(root), name)
            path.unlink()
        self.assertFalse(onboarding.has_existing_state(root))

    def test_public_setup_does_not_resolve_an_online_provider(self):
        with mock.patch.dict('os.environ', SOUNDBOX_PROFILE='public'), \
                mock.patch('soundbox.providers.resolve', side_effect=AssertionError('No provider startup')):
            self.assertFalse(configured_artwork())
        with mock.patch.dict('os.environ', SOUNDBOX_PROFILE='personal'), \
                mock.patch('soundbox.providers.resolve', side_effect=AssertionError('No provider startup')):
            self.assertTrue(configured_artwork())

    def test_unknown_display_label_does_not_invent_a_native_cleanup_change(self):
        source = library()
        source.tracks_by_id['1'].name = 'Plain Song'
        source.tracks_by_id['1'].artist = ''
        result = onboarding.preview(source)
        self.assertEqual((result.changed_tracks, result.examples), (0, ()))

    def test_setup_does_not_create_a_native_authority(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        repo = StateRepository(root / 'library.json')
        previous = repo.load()
        repo.save(onboarding.setup_state(previous, library(), clean=True, artwork=False))
        self.assertFalse(list(root.glob('*authority*')))
        self.assertEqual(repo.load()['settings']['first_launch']['scope']['library_id'], 'fixture-library')


class FirstLaunchUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def dialog(self, **kwargs):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        dialog = FirstLaunchDialog(root, candidate='/fixture/master.db',
                                  reader=lambda _path: onboarding.preview(library()),
                                  status=lambda _root, _library: (True, ''), **kwargs)
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.reject)
        ready = QSignalSpy(dialog._tasks.drained)
        loop = QEventLoop()
        dialog._tasks.drained.connect(loop.quit)
        deadline = QTimer(); deadline.setSingleShot(True); deadline.timeout.connect(loop.quit)
        deadline.start(4000)
        loop.exec()
        deadline.stop()
        self.assertEqual(ready.count(), 1, 'The preview did not retire through its owner')
        return dialog

    def test_exact_three_pages_default_to_original_and_artwork_off(self):
        dialog = self.dialog()
        self.assertEqual(dialog.pages.count(), 3)
        self.assertTrue(dialog.keep_original.isChecked())
        self.assertFalse(dialog.clean.isChecked())
        self.assertFalse(dialog.artwork.isChecked())
        dialog._next()
        self.assertEqual(dialog.step.text(), '2 of 3 · Cleanup')
        dialog.clean.setChecked(True)
        dialog._next()
        self.assertEqual(dialog.step.text(), '3 of 3 · Artwork')
        self.assertFalse(dialog.artwork.isChecked())
        dialog.artwork.setChecked(True)
        dialog.back_button.click()
        self.assertTrue(dialog.clean.isChecked())
        self.assertTrue(dialog.artwork.isChecked())

    def test_unavailable_online_artwork_cannot_be_enabled_by_setup(self):
        saved = mock.Mock()
        dialog = self.dialog(artwork_available=False, commit=saved)
        self.assertFalse(dialog.artwork.isEnabled())
        dialog.artwork.setChecked(True)  # Even programmatic checkbox state grants no capability.
        dialog._show_page(2)
        dialog._next()
        self.assertIs(saved.call_args.args[2], False)

    def test_failed_durable_save_keeps_choices_and_last_page_open(self):
        commit = mock.Mock(side_effect=OSError('fixture full disk'))
        dialog = self.dialog(commit=commit)
        dialog.clean.setChecked(True)
        dialog.artwork.setChecked(True)
        dialog._show_page(2)
        dialog._next()
        self.assertFalse(dialog._closed)
        self.assertTrue(dialog.clean.isChecked())
        self.assertTrue(dialog.artwork.isChecked())
        self.assertIn('Couldn’t save', dialog.error.text())
        commit.side_effect = None
        dialog._next()
        self.assertEqual(dialog.result(), QDialog.Accepted)

    def test_cancel_waits_for_real_worker_and_never_publishes_its_late_result(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        started, released = Event(), Event()

        def read(_path):
            started.set()
            released.wait()
            return onboarding.preview(library())

        dialog = FirstLaunchDialog(root, reader=read, candidate='/fixture/master.db')
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(released.set)
        self.app.processEvents()
        self.assertTrue(started.wait(2))
        QTimer.singleShot(10, released.set)
        dialog.reject()
        self.assertEqual(dialog._tasks.pending, 0)
        self.assertIsNone(dialog.preview)
        self.assertFalse((root / 'library.json').exists())

    def test_finish_saves_before_success_and_cancel_does_not_save(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        repo = StateRepository(root / 'library.json')
        previous = repo.load()

        class FakeDialog:
            preview = onboarding.preview(library())
            def __init__(self, _root, commit, **kwargs): self.commit = commit
            def exec(self):
                self.commit(self.preview.library, False, True)
                return QDialog.Accepted
            def deleteLater(self): pass

        result = run_first_launch(repo, previous, dialog_factory=FakeDialog)
        self.assertEqual(repo.load(), result)
        self.assertTrue(result['settings']['auto_artwork'])


class FirstLaunchArtwork(unittest.TestCase):
    def window(self, automatic):
        from desktop.app import MainWindow
        from soundbox.providers import Providers
        window = SimpleNamespace(
            providers=Providers('personal', artwork=object(), actions=('artwork.search',)),
            _shutting_down=False, _state_maintenance=False,
            settings={'auto_artwork': automatic}, lib=library(),
            art_cache=SimpleNamespace(index={}, status_kinds=lambda _: {}),
            overrides={}, titles={}, _art_worker=None,
            _state_repository=SimpleNamespace(writable=True),
            player_bar=SimpleNamespace(is_playing=lambda: False))
        window._start_art_fill = lambda: MainWindow._start_art_fill(window)
        return window

    def editable_window(self, automatic, *, native=True):
        window = self.window(automatic)
        if automatic is None:
            window.settings.pop('auto_artwork')
        if not native:
            window.lib.source_kind = 'rekordbox-xml'
        window.metadata_controller = (SimpleNamespace(enqueue=mock.Mock(return_value=True))
                                      if native else None)
        window.providers = SimpleNamespace(allows=lambda _: True, artwork=SimpleNamespace(
            configure_source_exclusions=mock.Mock(), fetch_best=mock.Mock()))
        window.art_cache.status_kind = mock.Mock(return_value='miss')
        window.art_cache.source_exclusions = lambda: ('generated blocked source',)
        window.art_cache.source_excluded = mock.Mock(return_value=True)
        window._art_fetch_one = mock.Mock()
        window._start_art_fill = mock.Mock()
        window._refresh_model_art = mock.Mock()
        window._save_state = mock.Mock(return_value=True)
        window._invalidate_rows = mock.Mock()
        window._build_artist_registry = mock.Mock()
        return window

    def test_native_metadata_edit_honors_optout_without_changing_admission(self):
        from desktop.app import MainWindow
        for automatic in (False, True, None):
            for field in ('title', 'artist'):
                with self.subTest(automatic=automatic, field=field):
                    window = self.editable_window(automatic)
                    self.assertTrue(MainWindow._on_cell_edited(window, field, '1', 'Generated edit'))
                    window.metadata_controller.enqueue.assert_called_once_with('1', {field: 'Generated edit'})
                    self.assertEqual(window._art_fetch_one.call_count, int(automatic is not False))
                    if automatic is False:
                        window.art_cache.status_kind.assert_not_called()

    def test_local_metadata_edit_honors_optout_and_still_saves(self):
        from desktop.app import MainWindow
        for automatic in (False, True, None):
            for field in ('title', 'artist'):
                with self.subTest(automatic=automatic, field=field):
                    window = self.editable_window(automatic, native=False)
                    self.assertTrue(MainWindow._on_cell_edited(window, field, '1', 'Generated edit'))
                    window._save_state.assert_called_once()
                    self.assertEqual((window.titles if field == 'title' else window.overrides)['1'],
                                     'Generated edit')
                    self.assertEqual(window._art_fetch_one.call_count, int(automatic is not False))
                    if automatic is False:
                        window.art_cache.status_kind.assert_not_called()

    def test_configuring_exclusions_refreshes_local_view_but_honors_optout(self):
        from desktop.app import MainWindow
        for automatic in (False, True, None):
            with self.subTest(automatic=automatic):
                window = self.editable_window(automatic)
                MainWindow._configure_art_sources(window)
                window.providers.artwork.configure_source_exclusions.assert_called_once_with(
                    ('generated blocked source',))
                window._refresh_model_art.assert_called_once()
                self.assertEqual(window._start_art_fill.call_count, int(automatic is not False))
                if automatic is False:
                    window.art_cache.source_excluded.assert_not_called()
                else:
                    window._start_art_fill.assert_called_once_with(restart=True)

    def test_explicit_single_fetch_still_dispatches_with_optout(self):
        from desktop.app import MainWindow
        window = self.editable_window(False)
        window._run_oneshot = mock.Mock()
        MainWindow._art_fetch_one(window, '1')
        window._run_oneshot.assert_called_once()
        window._run_oneshot.call_args.args[1]()
        window.providers.artwork.fetch_best.assert_called_once()

    def test_explicit_rescan_still_dispatches_with_optout(self):
        from desktop.app import MainWindow
        window = self.editable_window(False)
        window.art_cache.retry_weak = mock.Mock(return_value=2)
        window._flash_status = mock.Mock()
        MainWindow._rescan_covers(window)
        window.art_cache.retry_weak.assert_called_once()
        window._start_art_fill.assert_called_once_with(restart=True)

    def test_artwork_opt_out_blocks_automatic_fetch_but_not_an_explicit_fetch(self):
        from desktop.app import MainWindow
        window = self.window(False)
        with mock.patch('desktop.app.ArtFillWorker') as worker:
            MainWindow._maybe_autostart_art_fill(window)
            worker.assert_not_called()
            MainWindow._start_art_fill(window)
            worker.assert_called_once()
            worker.return_value.start.assert_called_once()

    def test_enabled_automatic_artwork_starts_for_new_music_only(self):
        from desktop.app import MainWindow
        window = self.window(True)
        with mock.patch('desktop.app.ArtFillWorker') as worker:
            MainWindow._maybe_autostart_art_fill(window)
            worker.assert_called_once()
        window._art_worker = None
        window.art_cache.index = {'1': {'status': 'ok'}}
        with mock.patch('desktop.app.ArtFillWorker') as worker:
            MainWindow._maybe_autostart_art_fill(window)
            worker.assert_not_called()


class FirstLaunchReader(unittest.TestCase):
    def test_preview_protocol_explicitly_disables_media_resolution(self):
        source = library()
        nonce = '1' * 32
        batches = list(library_codec.batches(source))
        messages = [('begin', VERSION, nonce, 'preview', (str(source.source),), (('retained', 0),)),
                    ('read', VERSION, nonce), ('ack', VERSION, nonce, 0)]
        messages.extend(('ack', VERSION, nonce, index) for index in range(1, len(batches) + 1))
        messages.append(('stop',))

        class Channel:
            def __init__(self): self.messages = iter(messages); self.sent = []
            def receive(self): return next(self.messages), 0
            def send(self, value): self.sent.append(value)

        channel = Channel()
        with mock.patch('soundbox.native_reader_worker.rekordbox_db.load_live', return_value=source) as read:
            serve(channel, Path('/fixture/private-state'))
        read.assert_called_once_with(Path('/fixture/master.db'), resolve_media_paths=False)
        self.assertEqual(channel.sent[-1], ('stopped', 'native-read'))
