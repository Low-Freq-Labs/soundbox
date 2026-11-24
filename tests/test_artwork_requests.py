"""Manual image preparation stays responsive without losing accepted choices."""
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from threading import Event
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace

from PIL import Image
from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QThread, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from desktop.artwork_requests import ArtworkRequests
from desktop.track_info import TrackInfoDialog
from desktop.workers import _OneShotArt
from soundbox import artencode
from soundbox.artwork import ArtCache, ArtResult
from soundbox.library import Library, Track
from soundbox.state_repository import StateRepository
from tests.test_save_shutdown import _Window


def picture(color):
    output = BytesIO(); Image.new('RGB', (48, 48), color).save(output, 'PNG')
    return ArtResult(output.getvalue(), 'image/png', 1.0, 'file:///selected.png', 'Selected')


class ArtworkRequestOwnership(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.area = tempfile.TemporaryDirectory();self.addCleanup(self.area.cleanup)
        self.root = Path(self.area.name)
        self.window = _Window();w = self.window
        w.art_cache = ArtCache(self.root / 'art')
        w._state_repository = StateRepository(self.root / 'library.json')
        w._state_repository.save({'settings': {}})
        tracks = {tid: Track(tid, 'Title', 'Artist', 'file:///synthetic/' + tid + '.wav')
                  for tid in ('chosen', 'other')}
        w.lib = Library(tracks, [], Path('/synthetic/library.xml'))
        w.titles = {};w.overrides = {}
        w.providers = SimpleNamespace(artwork=None)
        w._art_requests = ArtworkRequests(w)
        w._background_status_available = lambda: True
        w._set_status_text = mock.Mock();w._refresh_model_art = mock.Mock()
        w._queue_stock_artwork = mock.Mock();w._backup_worker = None
        w._state_publication = False
        w.stack = SimpleNamespace(currentIndex=lambda: 0)
        w._selected_track = lambda: w.lib.tracks_by_id['chosen']
        w._row_for = lambda track: {'id': track.track_id, 'title': track.name}
        w.art_cache.store_ok('chosen', picture('red'))
        self.old = (w.art_cache.root / 'chosen.jpg').read_bytes()
        self.release = Event();self.addCleanup(self.close)

    def close(self):
        self.release.set()
        self.window._shutting_down = True
        self.window._art_requests.drain()
        self.window.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)

    def settle(self):
        owner = self.window._art_requests
        loop = QEventLoop();timer = QTimer();timer.setSingleShot(True)
        timer.timeout.connect(loop.quit);owner.drained.connect(loop.quit);timer.start(3000)
        if owner.pending:loop.exec()
        owner.drained.disconnect(loop.quit)
        self.assertEqual(owner.pending, 0)

    def held(self, result=None, *, tid='chosen', local=True):
        entered = Event();result = result or picture('blue')
        def fetch():
            entered.set();self.release.wait(2);return result
        self.window._run_oneshot(tid, fetch, local=local)
        self.assertTrue(entered.wait(1))
        return self.window._oneshots[-1]

    def test_local_picker_reads_and_normalizes_on_worker_then_joins_before_publication(self):
        w = self.window;payload = picture('blue');path = self.root / 'selected.png'
        provider_scope=mock.Mock(side_effect=AssertionError('Local image used an online provider'))
        w.providers.artwork=SimpleNamespace(operation_scope=provider_scope)
        path.write_bytes(payload.data);entered = Event();threads = [];ticks = []
        encode = artencode.ensure_device_safe;read = Path.read_bytes
        def normalized(data):
            if data == payload.data:
                threads.append(('normalize',QThread.currentThread()));entered.set();self.release.wait(2)
            return encode(data)
        def read_file(candidate):
            if candidate == path:threads.append(('read',QThread.currentThread()))
            return read(candidate)
        picker = mock.Mock(choice={'kind':'file','path':str(path)})
        published = [];store = w.art_cache.store_ok
        def saved(*args,**kwargs):
            worker = w._oneshots[-1]
            published.append((worker.isRunning(),QThread.currentThread()))
            return store(*args,**kwargs)
        heartbeat = QTimer();heartbeat.timeout.connect(lambda:ticks.append(1));heartbeat.start(5)
        with mock.patch('desktop.app.ArtPickerDialog',return_value=picker), \
                mock.patch.object(artencode,'ensure_device_safe',side_effect=normalized), \
                mock.patch.object(Path,'read_bytes',read_file), \
                mock.patch.object(w.art_cache,'store_ok',side_effect=saved):
            w._art_pick('chosen');self.assertTrue(entered.wait(1))
            self.assertEqual(read(w.art_cache.root/'chosen.jpg'),self.old)
            QTimer.singleShot(70,self.release.set);self.settle()
        heartbeat.stop()
        self.assertGreater(len(ticks),2)
        self.assertEqual([kind for kind,_thread in threads],['read','normalize'])
        self.assertTrue(all(thread is not self.app.thread() for _kind,thread in threads))
        self.assertEqual(published,[(False,self.app.thread())])
        provider_scope.assert_not_called()
        self.assertEqual((w.art_cache.root/'chosen.jpg').read_bytes(),encode(payload.data))
        self.assertEqual(w._oneshots,[]);self.assertFalse(w._art_requests.requests)

    def test_newer_choice_wins_even_if_old_fetch_finishes_last(self):
        w = self.window;self.held(picture('blue'),local=False)
        newer = picture('green');w._run_oneshot('chosen',lambda:newer,local=True)
        limit = 100
        while len(w._oneshots)>1 and limit:
            QTest.qWait(5);limit-=1
        self.assertEqual((w.art_cache.root/'chosen.jpg').read_bytes(),artencode.ensure_device_safe(newer.data))
        self.release.set();self.settle()
        self.assertEqual((w.art_cache.root/'chosen.jpg').read_bytes(),artencode.ensure_device_safe(newer.data))

    def test_bound_source_aba_discards_old_manual_completion_without_touching_new_choice(self):
        w = self.window
        original = w.lib
        a = w.art_cache.for_library(original)
        w.art_cache = a
        a.store_ok('chosen', picture('red'))
        self.held(picture('blue'))
        a.retire()
        other = replace(original, source=Path('/synthetic/other-library.xml'))
        w.lib = other
        b = a.for_library(other)
        w.art_cache = b
        w.lib = original
        w.art_cache = b.for_library(original)
        w.art_cache.store_ok('chosen', picture('green'))
        accepted = w.art_cache.cover_snapshot('chosen')
        self.release.set()
        self.settle()
        self.assertEqual(w.art_cache.cover_snapshot('chosen'), accepted)
        self.assertFalse(w._art_requests.requests)

    def test_accepted_manual_request_reserves_unfetched_song_against_older_auto_result(self):
        w = self.window
        w.art_cache.forget('chosen')
        self.held(picture('blue'))
        self.assertFalse(w.art_cache.store_auto_if_unfetched('chosen', picture('red')))
        self.assertIsNone(w.art_cache.status('chosen'))
        self.release.set()
        self.settle()
        self.assertEqual(w.art_cache.cover_snapshot('chosen').data,
                         artencode.ensure_device_safe(picture('blue').data))
        self.assertFalse(w.art_cache._manual_reserved('chosen'))

    def test_manual_capture_and_reservation_share_one_lock_against_auto_publisher(self):
        w = self.window
        w.art_cache.forget('chosen')
        captured = Event()
        attempted = Event()
        auto_done = Event()
        accepted = []
        from threading import Thread
        def auto():
            captured.wait(2)
            attempted.set()
            accepted.append(w.art_cache.store_auto_if_unfetched('chosen', picture('red')))
            auto_done.set()
        publisher = Thread(target=auto)
        publisher.start()
        selected = w.art_cache.selection_token
        def observe(*args, **kwargs):
            token = selected(*args, **kwargs)
            captured.set()
            self.assertTrue(attempted.wait(1))
            # The auto publisher cannot pass the state lock between capture
            # and reservation; no timing threshold is used for correctness.
            self.assertFalse(auto_done.is_set())
            return token
        try:
            with mock.patch.object(w.art_cache, 'selection_token', side_effect=observe):
                self.held(picture('blue'))
            publisher.join(2)
            self.assertFalse(publisher.is_alive())
            self.assertEqual(accepted, [False])
            self.release.set()
            self.settle()
            self.assertEqual(w.art_cache.cover_snapshot('chosen').data,
                             artencode.ensure_device_safe(picture('blue').data))
        finally:
            captured.set()
            self.release.set()
            publisher.join(2)

    def test_failed_manual_request_releases_auto_reservation(self):
        w = self.window
        w.art_cache.forget('chosen')
        self.held(RuntimeError('generated provider failure'), local=False)
        self.assertFalse(w.art_cache.store_auto_if_unfetched('chosen', picture('red')))
        self.release.set()
        self.settle()
        self.assertFalse(w.art_cache._manual_reserved('chosen'))
        self.assertTrue(w.art_cache.store_auto_if_unfetched('chosen', picture('green')))

    def test_default_only_manual_choice_survives_handoff_and_neighbor_rebalance(self):
        w = self.window
        previous = w.art_cache.for_library(w.lib)
        w.art_cache = previous
        paths = []
        for color in ('red', 'green', 'blue'):
            path = self.root / f'generic-{color}.png'
            path.write_bytes(picture(color).data)
            paths.append(path)
        previous.import_default_photos(paths)
        previous.configure_default_tracks(w.lib.tracks_by_id)
        self.held(picture('blue'))
        tracks = dict(w.lib.tracks_by_id)
        for at in range(5):
            tid = f'added-{at}'
            tracks[tid] = Track(tid, tid, '', f'file:///synthetic/{tid}.wav')
        w.lib = replace(w.lib, tracks_by_id=tracks)
        current = previous.for_library(w.lib)
        w._art_requests.handoff_cache(previous, current)
        previous.retire()
        w.art_cache = current
        current.configure_default_tracks(tracks)
        self.assertFalse(current.store_auto_if_unfetched('chosen', picture('red')))
        self.assertTrue(w._art_requests.manual_pending('chosen'))
        self.release.set()
        self.settle()
        self.assertEqual(current.cover_snapshot('chosen').data,
                         artencode.ensure_device_safe(picture('blue').data))
        self.assertFalse(current._manual_reserved('chosen'))

    def test_unrelated_track_add_or_remove_preserves_accepted_manual_choice(self):
        for remove in (False, True):
            with self.subTest(remove=remove):
                self.release.clear()
                w = self.window
                original = w.lib
                previous = w.art_cache.for_library(original)
                w.art_cache = previous
                previous.store_ok('chosen', picture('red'))
                self.held(picture('blue'))
                tracks = dict(original.tracks_by_id)
                if remove:
                    tracks.pop('other', None)
                else:
                    tracks['added'] = Track('added', 'New', 'Artist', 'file:///synthetic/added.wav')
                w.lib = replace(original, tracks_by_id=tracks)
                current = previous.for_library(w.lib)
                w._art_requests.handoff_cache(previous, current)
                previous.retire()
                w.art_cache = current
                self.assertTrue(w._art_requests.manual_pending('chosen'))
                self.release.set()
                self.settle()
                self.assertEqual(w.art_cache.cover_snapshot('chosen').data,
                                 artencode.ensure_device_safe(picture('blue').data))

    def test_handoff_rejects_selected_identity_or_cover_change(self):
        for changed_cover in (False, True):
            with self.subTest(changed_cover=changed_cover):
                self.release.clear()
                w = self.window
                previous = w.art_cache.for_library(w.lib)
                w.art_cache = previous
                previous.store_ok('chosen', picture('red'))
                self.held(picture('blue'))
                tracks = dict(w.lib.tracks_by_id)
                tracks['added'] = Track('added', 'New', 'Artist', 'file:///synthetic/added.wav')
                if not changed_cover:
                    tracks['chosen'] = replace(tracks['chosen'], source_uuid='replacement-uuid')
                w.lib = replace(w.lib, tracks_by_id=tracks)
                current = previous.for_library(w.lib)
                if changed_cover:
                    current.store_ok('chosen', picture('green'))
                w._art_requests.handoff_cache(previous, current)
                previous.retire()
                w.art_cache = current
                self.release.set()
                self.settle()
                if changed_cover:
                    self.assertEqual(current.cover_snapshot('chosen').data,
                                     artencode.ensure_device_safe(picture('green').data))
                else:
                    self.assertIsNone(current.canonical_image_path('chosen'))

    def test_explicit_remote_picker_choice_uses_the_same_accepted_manual_lane(self):
        w = self.window;entered = Event()
        def fetch(*_args):entered.set();self.release.wait(2);return picture('blue')
        w.providers=SimpleNamespace(artwork=SimpleNamespace(fetch_thumb=fetch),allows=lambda _action:True)
        picker=mock.Mock(choice={'kind':'thumb','cand':{
            'thumb':'https://synthetic.invalid/image','url':'https://synthetic.invalid/song','title':'Selected'}})
        with mock.patch('desktop.app.ArtPickerDialog',return_value=picker):
            w._art_pick('chosen')
        self.assertTrue(entered.wait(1));self.assertTrue(w._art_requests.manual_pending('chosen'))
        self.assertTrue(w._oneshots[-1].requires_save);self.assertFalse(w._oneshots[-1].local)
        self.release.set();self.settle()
        self.assertEqual((w.art_cache.root/'chosen.jpg').read_bytes(),artencode.ensure_device_safe(picture('blue').data))

    def test_changed_cover_source_or_restore_generation_never_overwrites_newer_intent(self):
        w = self.window
        mutations = (lambda:w.art_cache.block('chosen'),
                     lambda:setattr(w,'lib',replace(w.lib,source=Path('/different/library.xml'))),
                     lambda:setattr(w._state_repository.area,'generation',w._state_repository.area.generation+1))
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.release.clear();self.held();mutate()
                before = (w.art_cache.root/'chosen.jpg').read_bytes() if (w.art_cache.root/'chosen.jpg').exists() else None
                self.release.set();self.settle()
                after = (w.art_cache.root/'chosen.jpg').read_bytes() if (w.art_cache.root/'chosen.jpg').exists() else None
                self.assertEqual(after,before)
                w._flash_status.assert_called_with('Artwork target changed; choose the image again',8)
        rejected=mock.Mock(return_value=picture('blue'))
        w._run_oneshot('chosen',rejected,local=True)
        rejected.assert_not_called();self.assertFalse(w._art_requests.pending)

    def test_quit_backup_restore_and_source_switch_wait_for_accepted_manual_choice(self):
        w = self.window;self.held()
        event = QCloseEvent();w.closeEvent(event)
        self.assertFalse(event.isAccepted());w.shutdown.assert_not_called()
        self.assertIn('artwork',w._source_selection_reason())
        for restoring in (False,True):
            self.assertFalse(w._begin_state_maintenance(restoring=restoring))
            self.assertFalse(w._state_maintenance)
        self.release.set();self.settle();QTest.qWait(100)
        self.assertNotEqual((w.art_cache.root/'chosen.jpg').read_bytes(),self.old)
        w.shutdown.assert_called_once_with()

    def test_info_first_show_waits_for_latest_selected_manual_cover_only(self):
        w = self.window;self.held(picture('blue'))
        other_release = Event();other_entered = Event();self.addCleanup(other_release.set)
        def other():other_entered.set();other_release.wait(2);return picture('green')
        w._run_oneshot('other',other,local=True);self.assertTrue(other_entered.wait(1))
        shown = []
        class Dialog(TrackInfoDialog):
            def showEvent(dialog,event):
                color = dialog._image.pixelColor(0,0)
                shown.append((dialog._art_ready,color.blue()>240 and color.red()<10,
                    w._art_requests.manual_pending('other')))
                other_release.set();super().showEvent(event);QTimer.singleShot(0,dialog.reject)
        QTimer.singleShot(60,self.release.set)
        with mock.patch('desktop.track_info.TrackInfoDialog',Dialog):
            w._open_track_info()
        self.assertEqual(shown,[(True,True,True)])
        self.settle();self.assertIsNone(QApplication.overrideCursor())

    def test_failed_read_or_normalization_keeps_old_cover_and_reports_failure(self):
        w = self.window
        for failure in (OSError('image unavailable'),ValueError('decode preparation failed')):
            with self.subTest(failure=failure), \
                    mock.patch('desktop.app.QMessageBox.warning') as warning:
                if isinstance(failure,OSError):
                    def read():raise failure
                    w._run_oneshot('chosen',read,local=True);self.settle()
                else:
                    with mock.patch.object(artencode,'ensure_device_safe',side_effect=failure):
                        w._run_oneshot('chosen',lambda:picture('blue'),local=True);self.settle()
                warning.assert_called_once();self.assertEqual((w.art_cache.root/'chosen.jpg').read_bytes(),self.old)
                self.assertFalse(w._art_requests.manual_pending())

    def test_picker_source_change_cannot_retarget_the_selected_file(self):
        w = self.window;picker = mock.Mock(choice={'kind':'file','path':'/synthetic/chosen.png'})
        picker.exec.side_effect=lambda:setattr(w,'lib',replace(w.lib,source=Path('/different/library.xml')))
        with mock.patch('desktop.app.ArtPickerDialog',return_value=picker), \
                mock.patch.object(w,'_run_oneshot') as start:
            w._art_pick('chosen')
        start.assert_not_called()
        self.assertEqual((w.art_cache.root/'chosen.jpg').read_bytes(),self.old)
        w._flash_status.assert_called_with('Artwork target changed; choose the image again',8)

    def test_explicit_shutdown_cancels_and_joins_without_late_publication(self):
        w = self.window;self.held();w._shutting_down=True
        w._art_requests.stop();self.release.set();w._art_requests.drain()
        self.assertEqual(w._oneshots,[])
        self.assertEqual((w.art_cache.root/'chosen.jpg').read_bytes(),self.old)

    def avatars(self):
        for tid in ('chosen','other'):
            self.window.art_cache.store_ok(tid,replace(picture('red'),
                source_url='https://soundcloud.com/artist'))

    def test_catalog_file_prepares_off_gui_and_only_publishes_unchanged_avatars(self):
        w=self.window;self.avatars();path=self.root/'avatar.png';payload=picture('blue')
        path.write_bytes(payload.data);entered=Event();threads=[];ticks=[];done=mock.Mock()
        encode=artencode.ensure_device_safe
        def normalize(data):
            if data==payload.data:
                threads.append(QThread.currentThread());entered.set();self.release.wait(2)
            return encode(data)
        heartbeat=QTimer();heartbeat.timeout.connect(lambda:ticks.append(1));heartbeat.start(5)
        with mock.patch.object(artencode,'ensure_device_safe',side_effect=normalize):
            w._on_catalog_action(dict(kind='file',path=str(path),artist='Artist',tids=['chosen','other']),done)
            self.assertTrue(entered.wait(1));self.assertTrue(w._art_requests.manual_pending('other'))
            self.assertIn('artwork',w._pending_save_reason())
            w.art_cache.store_ok('chosen',picture('green'))
            QTimer.singleShot(50,self.release.set);self.settle()
        heartbeat.stop();self.assertGreater(len(ticks),2);self.assertEqual(len(threads),1)
        self.assertIsNot(threads[0],self.app.thread())
        self.assertEqual((w.art_cache.root/'chosen.jpg').read_bytes(),encode(picture('green').data))
        self.assertEqual((w.art_cache.root/'other.jpg').read_bytes(),encode(payload.data))
        done.assert_called_once_with(True,w.art_cache.image_path('other'))

    def test_partial_newer_choice_does_not_cancel_other_catalog_targets(self):
        w=self.window;self.avatars();entered=Event();done=mock.Mock()
        def fetch(_url):entered.set();self.release.wait(2);return picture('blue')
        w.providers=SimpleNamespace(artwork=SimpleNamespace(fetch_by_url=fetch),allows=lambda _action:True)
        w._on_catalog_action(dict(kind='url',url='https://synthetic.invalid/avatar',artist='Artist',
            tids=['chosen','other']),done)
        self.assertTrue(entered.wait(1));catalog_worker=w._oneshots[0]
        w._run_oneshot('chosen',lambda:picture('green'),local=True)
        self.assertFalse(catalog_worker._cancelled.is_set())
        QTest.qWait(30);self.release.set();self.settle()
        self.assertEqual((w.art_cache.root/'chosen.jpg').read_bytes(),artencode.ensure_device_safe(picture('green').data))
        self.assertEqual((w.art_cache.root/'other.jpg').read_bytes(),artencode.ensure_device_safe(picture('blue').data))
        done.assert_called_once_with(True,w.art_cache.image_path('other'))

    def test_catalog_filters_artist_changes_and_settles_normalization_failure(self):
        w=self.window;self.avatars();path=self.root/'avatar.png';payload=picture('blue');path.write_bytes(payload.data)
        entered=Event();done=mock.Mock();encode=artencode.ensure_device_safe
        def normalize(data):entered.set();self.release.wait(2);return encode(data)
        with mock.patch.object(artencode,'ensure_device_safe',side_effect=normalize):
            w._on_catalog_action(dict(kind='file',path=str(path),artist='Artist',tids=['chosen','other']),done)
            self.assertTrue(entered.wait(1));w.overrides['chosen']='Renamed Artist';self.release.set();self.settle()
        self.assertEqual((w.art_cache.root/'chosen.jpg').read_bytes(),self.old)
        done.assert_called_once_with(True,w.art_cache.image_path('other'))
        done.reset_mock()
        with mock.patch.object(artencode,'ensure_device_safe',side_effect=ValueError('decode failed')), \
                mock.patch('desktop.app.QMessageBox.warning') as warning:
            w._on_catalog_action(dict(kind='file',path=str(path),artist='Artist',tids=['other']),done);self.settle()
        done.assert_called_once_with(False,None);warning.assert_called_once()
        self.assertFalse(w._art_requests.manual_pending())

    def test_catalog_source_or_restore_change_settles_without_publication(self):
        w=self.window;self.avatars();entered=Event()
        def fetch(_url):entered.set();self.release.wait(2);return picture('blue')
        w.providers=SimpleNamespace(artwork=SimpleNamespace(fetch_by_url=fetch),allows=lambda _action:True)
        for mutate in (lambda:setattr(w,'lib',replace(w.lib,source=Path('/changed/library.xml'))),
                       lambda:setattr(w._state_repository.area,'generation',w._state_repository.area.generation+1)):
            self.release.clear();entered.clear();done=mock.Mock()
            w._on_catalog_action(dict(kind='url',url='https://synthetic.invalid/avatar',artist='Artist',
                tids=['chosen','other']),done)
            self.assertTrue(entered.wait(1));mutate();self.release.set();self.settle()
            done.assert_called_once_with(False,None)
            self.assertEqual((w.art_cache.root/'chosen.jpg').read_bytes(),self.old)
            self.assertEqual((w.art_cache.root/'other.jpg').read_bytes(),self.old)

    def test_superseded_catalog_cancels_without_a_late_error_prompt(self):
        w=self.window;self.avatars();entered=Event();done=mock.Mock()
        def fetch(_url):entered.set();self.release.wait(2);return picture('blue')
        w.providers=SimpleNamespace(artwork=SimpleNamespace(fetch_by_url=fetch),allows=lambda _action:True)
        with mock.patch('desktop.app.QMessageBox.warning') as warning:
            w._on_catalog_action(dict(kind='url',url='https://synthetic.invalid/avatar',artist='Artist',
                tids=['chosen','other']),done)
            self.assertTrue(entered.wait(1));worker=w._oneshots[0]
            for tid in ('chosen','other'):w._run_oneshot(tid,lambda:picture('green'),local=True)
            self.assertTrue(worker._cancelled.is_set());self.release.set();self.settle()
        warning.assert_not_called();done.assert_called_once_with(False,None)
        for tid in ('chosen','other'):
            self.assertEqual((w.art_cache.root/(tid+'.jpg')).read_bytes(),artencode.ensure_device_safe(picture('green').data))

    def test_profile_retains_exact_external_byte_change_protection(self):
        w=self.window;entered=Event()
        def fetch(_artist):entered.set();self.release.wait(2);return picture('blue')
        w.providers=SimpleNamespace(artwork=SimpleNamespace(fetch_profile_art=fetch),allows=lambda _action:True)
        w._art_artist_profile('chosen');self.assertTrue(entered.wait(1))
        externally_changed=b'external bytes changed during the lookup'
        (w.art_cache.root/'chosen.jpg').write_bytes(externally_changed)
        self.release.set();self.settle()
        self.assertEqual((w.art_cache.root/'chosen.jpg').read_bytes(),externally_changed)

    def test_worker_detaches_result_payload_and_preserves_corrupt_image_fallback(self):
        for result in (picture('blue'),ArtResult(b'not an image','image/png',.4,'file:///bad.png','Bad')):
            with self.subTest(result=result.matched_title):
                worker = _OneShotArt('chosen',lambda:result);worker.run()
                self.assertIsNot(worker.result,result)
                self.assertEqual(worker.result.data,artencode.ensure_device_safe(result.data))
                self.assertEqual(result.data,picture('blue').data if result.matched_title=='Selected' else b'not an image')
                self.assertEqual(worker.result.source_url,result.source_url)
                worker.deleteLater()


if __name__=='__main__':unittest.main()
