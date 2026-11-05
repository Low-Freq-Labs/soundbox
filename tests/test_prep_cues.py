"""Synthetic cue UI/controller checks. No live library, device or audio reads."""
from copy import deepcopy
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock

from PySide6.QtWidgets import QApplication

from desktop.player import CuePanel, PlayerBar
from desktop.tag_controller import TagEditController
from soundbox.library import Library, LibraryTag, Track
from soundbox.rekordbox_cues import CueSnapshot


def library():
    return Library({'1': Track('1', 'One', '', 'file:///synthetic/one.wav',
        source_uuid='track-one', source_cues=[], source_tag_ids=[],
        source_cue_revision='a' * 64, source_cue_refs=[])}, [], '/synthetic/master.db',
        source_kind='rekordbox-db', source_library_id='synthetic',
        source_tags=[LibraryTag('tag', 'Dubstep', source_uuid='tag-uuid')])


class CueEditingInterface(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait_for(self, condition):
        deadline = time.monotonic() + 5
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertTrue(condition())

    def test_empty_press_requests_creation_and_release_cannot_preview_new_cue(self):
        panel = CuePanel()
        panel.set_loaded(True)
        panel.set_editable(True)
        created, pressed, released = [], [], []
        panel.empty_pressed.connect(lambda *v: created.append(v))
        panel.cue_pressed.connect(lambda *v: pressed.append(v))
        panel.cue_released.connect(lambda *v: released.append(v))
        panel._press('hot', 0)
        self.assertEqual(created, [('hot', 0)])
        panel.set_cues([dict(kind='hot', slot=0, position_ms=10003)])
        panel._release('hot', 0)
        self.assertEqual(pressed, [])
        self.assertEqual(released, [])
        panel._press('hot', 0)
        panel.set_cues([])  # deletion/refresh while held must not strand release
        panel._release('hot', 0)
        self.assertEqual(released, [('hot', 0)])
        panel.deleteLater()

    def test_cue_fill_and_remove_repolish_through_stylesheets_without_borrowed_style(self):
        panel = CuePanel()
        self.addCleanup(panel.deleteLater)
        panel.set_loaded(True)
        panel.set_editable(True)
        # Qt replaces its private stylesheet-style object during these changes.
        # A borrowed wrapper can already be invalid when Python queries it.
        with mock.patch.object(panel.hot_slots[0], 'style',
                               side_effect=AssertionError('borrowed style queried')), \
                mock.patch.object(panel.memory_slots[1], 'style',
                                  side_effect=AssertionError('borrowed style queried')):
            panel.set_cues([dict(kind='hot', slot=0, position_ms=10003),
                            dict(kind='memory', slot=1, position_ms=22314)])
            self.assertTrue(panel.hot_slots[0].property('filled'))
            self.assertEqual(panel.hot_slots[0].text(), 'A  0:10')
            self.assertTrue(panel.memory_slots[1].property('filled'))
            self.assertTrue(panel.memory_slots[1].isEnabled())
            panel.set_cues([])
            self.assertFalse(panel.hot_slots[0].property('filled'))
            self.assertEqual(panel.hot_slots[0].text(), 'A   —')
            self.assertEqual(panel.hot_slots[0].styleSheet(), '')
            self.assertFalse(panel.memory_slots[1].property('filled'))
            self.assertTrue(panel.hot_slots[0].isEnabled())

    def test_cue_request_captures_revision_page_position_and_blocks_tag_lane(self):
        with tempfile.TemporaryDirectory() as td:
            lib = library()
            entered, release = threading.Event(), threading.Event()
            received = []
            class Store:
                def apply(self, request):
                    received.append(request)
                    entered.set()
                    release.wait(5)
                def snapshot(self):
                    return CueSnapshot(dict(ID='1', UUID='track-one', FolderPath='/synthetic/one.wav'), (), None)
            controller = TagEditController(Path(td), store_factory=lambda _lib: None,
                cue_store_factory=lambda _lib, _tid: Store(), loader=lambda _path: lib)
            self.addCleanup(lambda: (release.set(), controller.shutdown()))
            controller.configure(lib, True)
            self.assertTrue(controller.request_cue('1', 'memory', 0, 'add', 19021))
            lib.tracks_by_id['1'].source_cue_revision = 'b' * 64
            self.wait_for(entered.is_set)
            self.assertEqual(received[0].revision, 'a' * 64)
            self.assertEqual((received[0].page, received[0].slot, received[0].position_ms),
                             ('memory', 0, 19021))
            self.assertEqual(controller.status_text, 'Updating memory cue…')
            self.assertFalse(controller.request(['1'], 'tag', True))
            self.assertTrue(controller.request_cue('1', 'hot', 0, 'add', 1))
            self.assertEqual(len(controller.session_cues('1')), 2)
            # A deferred analysis-priority timer must not overlap the active save.
            controller._start_next_cue()
            self.app.processEvents()
            self.assertEqual(len(received), 1)
            release.set()
            self.wait_for(lambda: not controller.busy)
            self.assertEqual(controller.last_operation, 'cues')
            controller.shutdown()

    def test_logical_cue_selection_uses_native_sql_identity_and_emits_logical_saved_key(self):
        from dataclasses import replace
        from uuid import uuid4
        from soundbox import rekordbox_db
        from soundbox.rekordbox_cues import CueIdentities
        from tests.test_rekordbox_cues import snapshot, plan, advance
        with tempfile.TemporaryDirectory() as td:
            state=snapshot();state.track.update(ID='1',FolderPath='/synthetic/one.wav')
            lib=library();track=replace(lib.tracks_by_id['1'],track_id='sb-stable',source_native_id='1',
                source_uuid=state.track['UUID'],**rekordbox_db.cue_snapshot_fields(state))
            lib.tracks_by_id={'sb-stable':track};requests=[];bound=[];saved=[]
            class Store:
                def apply(self,request):
                    nonlocal state
                    requests.append(request)
                    state=advance(state,plan(state,request,identities=CueIdentities('100',str(uuid4()),str(uuid4()))))
                def snapshot(self):return state
            def factory(_lib,tid):bound.append(tid);return Store()
            controller=TagEditController(Path(td),cue_store_factory=factory)
            controller.configure(lib,True);controller.cue_saved.connect(lambda _lib,tid:saved.append(tid))
            try:
                self.assertTrue(controller.request_cue('sb-stable','hot',0,'add',10003))
                self.wait_for(lambda:not controller.busy)
                self.assertEqual(requests[0].track_id,'1');self.assertEqual(bound,['1'])
                self.assertEqual(saved,['sb-stable']);self.assertEqual(set(controller.lib.tracks_by_id),{'sb-stable'})
            finally:controller.shutdown()

    def test_session_is_immediate_and_queue_rebases_memory_and_native_delete_identities(self):
        from dataclasses import replace
        from uuid import uuid4
        from soundbox import rekordbox_db
        from soundbox.rekordbox_cues import CueIdentities
        from tests.test_rekordbox_cues import snapshot, plan, advance
        with tempfile.TemporaryDirectory() as td:
            lib = library()
            state = snapshot()
            state.track.update(ID='1', FolderPath='/synthetic/one.wav')
            lib.tracks_by_id['1'] = replace(lib.tracks_by_id['1'], source_uuid=state.track['UUID'],
                                            **rekordbox_db.cue_snapshot_fields(state))
            entered, release = threading.Event(), threading.Event()
            requests = []
            class Store:
                def apply(self, request):
                    nonlocal state
                    requests.append(request)
                    entered.set()
                    release.wait(5)
                    ids = CueIdentities(str(100 + len(requests)), str(uuid4()), str(uuid4()))
                    state = advance(state, plan(state, request, identities=ids))
                def snapshot(self):
                    return state
            controller = TagEditController(Path(td), cue_store_factory=lambda *_: Store())
            controller.configure(lib, True)
            changes, errors = [], []
            controller.cue_changed.connect(lambda tid: changes.append(controller.session_cues(tid)))
            controller.failed.connect(errors.append)
            try:
                self.assertTrue(controller.request_cue('1', 'hot', 0, 'add', 10003))
                self.assertEqual(changes[-1][0]['position_ms'], 10003)  # before event dispatch/write
                self.assertEqual(lib.tracks_by_id['1'].source_cues, [])
                self.wait_for(entered.is_set)
                self.assertTrue(controller.request_cue('1', 'memory', 0, 'add', 19021))
                self.assertTrue(controller.request_cue('1', 'memory', 1, 'add', 9003))
                self.assertEqual([c['position_ms'] for c in changes[-1] if c['kind'] == 'memory'],
                                 [9003, 19021])
                # Delete an uncommitted Memory cue by its session identity,
                # even though the displayed chronological slot has changed.
                self.assertTrue(controller.request_cue('1', 'memory', 0, 'delete'))
                self.assertTrue(controller.request_cue('1', 'hot', 0, 'delete'))
                self.assertTrue(controller.request_cue('1', 'hot', 0, 'add', 30003))
                self.assertEqual([(c['kind'], c['position_ms']) for c in changes[-1]],
                                 [('hot', 30003), ('memory', 19021)])
                release.set()
                controller.shutdown()  # must drain the entire accepted queue
                self.assertEqual(errors, [])
                self.assertEqual(len(requests), 6)
                self.assertEqual(len({r.revision for r in requests}), 6)
                self.assertEqual((requests[3].cue_id, requests[3].slot), ('103', 0))
                self.assertEqual(requests[4].cue_id, '101')
                self.assertEqual(controller.session_cues('1'),
                                 controller.lib.tracks_by_id['1'].source_cues)
                self.assertEqual([(c['kind'], c['position_ms']) for c in controller.session_cues('1')],
                                 [('hot', 30003), ('memory', 19021)])
                self.assertFalse(controller.owned)
            finally:
                release.set()
                controller.shutdown()

    def test_failed_session_reverts_overlay_cancels_tail_and_never_caches_unconfirmed_cues(self):
        with tempfile.TemporaryDirectory() as td:
            entered, release = threading.Event(), threading.Event()
            received = []
            class Store:
                def apply(self, request):
                    received.append(request)
                    entered.set()
                    release.wait(5)
                    raise RuntimeError('External library change')
            controller = TagEditController(Path(td), cue_store_factory=lambda *_: Store())
            controller.configure(library(), True)
            errors, saves = [], []
            controller.failed.connect(errors.append)
            controller.cue_saved.connect(lambda *a: saves.append(a))
            try:
                controller.request_cue('1', 'hot', 0, 'add', 10)
                self.wait_for(entered.is_set)
                controller.request_cue('1', 'hot', 1, 'add', 20)
                self.assertEqual(len(controller.session_cues('1')), 2)
                release.set()
                self.wait_for(lambda: not controller.owned)
                self.assertEqual(len(received), 1)
                self.assertEqual(saves, [])
                self.assertEqual(errors, ['External library change'])
                self.assertEqual(controller.session_cues('1'), [])
                self.assertEqual(controller.lib.tracks_by_id['1'].source_cues, [])
            finally:
                release.set()
                controller.shutdown()

    def test_success_updates_only_target_copy_and_never_reloads_whole_library(self):
        from tests.test_rekordbox_cues import existing, with_existing
        with tempfile.TemporaryDirectory() as td:
            lib = library()
            native = existing(1, 10003)
            native.update(ContentID='1', ContentUUID='track-one')
            state = with_existing([native])
            state.track.update(ID='1', UUID='track-one', FolderPath='/synthetic/one.wav')
            class Store:
                def apply(self, _request):
                    return True
                def snapshot(self):
                    return state
            loader = mock.Mock(side_effect=AssertionError('Cue saves must not reload the library'))
            controller = TagEditController(Path(td), cue_store_factory=lambda *_: Store(), loader=loader)
            controller.configure(lib, True)
            saved = []
            controller.cue_saved.connect(lambda *args: saved.append(args))
            self.assertTrue(controller.request_cue('1', 'hot', 0, 'add', 10003))
            self.wait_for(lambda: not controller.busy)
            loader.assert_not_called()
            self.assertEqual(len(saved), 1)
            updated, track_id = saved[0]
            self.assertEqual(track_id, '1')
            self.assertEqual(updated.tracks_by_id['1'].source_cues[0]['position_ms'], 10003)
            self.assertEqual(updated.tracks_by_id['1'].source_cue_revision, state.revision)
            self.assertEqual(lib.tracks_by_id['1'].source_cues, [])
            self.assertEqual(lib.tracks_by_id['1'].source_cue_revision, 'a' * 64)
            controller.shutdown()

    def test_delete_uses_captured_native_id_and_failure_does_not_publish(self):
        with tempfile.TemporaryDirectory() as td:
            lib = library()
            track = lib.tracks_by_id['1']
            track.source_cues = [dict(kind='memory', slot=0, position_ms=19021)]
            track.source_cue_refs = [dict(kind='memory', slot=0, id='77', uuid='cue-77')]
            before = deepcopy(track.source_cues)
            received, saved, failed = [], [], []
            class Store:
                def apply(self, request):
                    received.append(request)
                    raise RuntimeError('Close rekordbox')
            controller = TagEditController(Path(td), cue_store_factory=lambda _lib, _tid: Store())
            controller.configure(lib, True)
            controller.saved.connect(saved.append)
            controller.failed.connect(failed.append)
            self.assertTrue(controller.request_cue('1', 'memory', 0, 'delete'))
            self.wait_for(lambda: not controller.busy)
            self.assertEqual((received[0].cue_id, received[0].cue_uuid), ('77', 'cue-77'))
            self.assertEqual(track.source_cues, before)
            self.assertEqual(saved, [])
            self.assertEqual(failed, ['Close rekordbox'])
            controller.shutdown()

    def test_cache_import_missing_revision_and_shutdown_never_admit_writes(self):
        with tempfile.TemporaryDirectory() as td:
            lib = library()
            controller = TagEditController(Path(td), cue_store_factory=lambda *_: None)
            controller.configure(lib, False)
            self.assertFalse(controller.request_cue('1', 'hot', 0, 'add', 1000))
            controller.configure(lib, True)
            lib.tracks_by_id['1'].imported = True
            self.assertFalse(controller.request_cue('1', 'hot', 0, 'add', 1000))
            lib.tracks_by_id['1'].imported = False
            lib.tracks_by_id['1'].source_cue_revision = ''
            self.assertFalse(controller.request_cue('1', 'hot', 0, 'add', 1000))
            lib.tracks_by_id['1'].source_cue_revision = 'a' * 64
            controller.shutdown()
            self.assertFalse(controller.request_cue('1', 'hot', 0, 'add', 1000))

    def test_playhead_capture_synchronizes_native_position_and_rejects_source_swap(self):
        # Call the production method on a bounded fake deck; never instantiate
        # a native decoder or open the synthetic source path.
        from types import SimpleNamespace
        deck = SimpleNamespace(_track_id='1', _src_path='/synthetic/one.wav',
            _analysis_generation=1, _pending_autoplay=False, _native_error='',
            _dur=100000, cue_panel=SimpleNamespace(_loaded=True),
            player=mock.Mock())
        deck.player.position.return_value = 19021
        self.assertEqual(PlayerBar.capture_cue_position(deck), 19021)
        deck.player.synchronize.assert_called_once()
        deck.player.synchronize.side_effect = lambda: setattr(deck, '_analysis_generation', 2)
        self.assertIsNone(PlayerBar.capture_cue_position(deck))

    def test_app_targets_loaded_track_instead_of_browser_selection(self):
        from types import SimpleNamespace
        from desktop.app import MainWindow
        lib = library()
        panel = SimpleNamespace(_cues={'hot': [None] * 8, 'memory': [None] * 8})
        controller = mock.Mock()
        controller.cue_reason.return_value = ''
        controller.request_cue.return_value = True
        deck = SimpleNamespace(current_track=lambda: '1', _src_path='/synthetic/one.wav',
                               capture_cue_position=lambda: 10003, cue_panel=panel,
                               quantize_cue_position=lambda position: position)
        window = SimpleNamespace(lib=lib, player_bar=deck, tag_controller=controller,
            _refresh_worker=None, _state_maintenance=False, _shutting_down=False,
            _device_operation_owned=lambda: False, _selected_ids=lambda: ['another-track'],
            _flash_status=mock.Mock())
        window._local_cue_reason = lambda _tid: None  # this fixture exercises native cues
        self.assertTrue(MainWindow._edit_cue(window, 'hot', 0, 'add'))
        controller.request_cue.assert_called_once_with('1', 'hot', 0, 'add', 10003)

    def test_pending_other_track_recovers_before_new_cue_save(self):
        import json
        with tempfile.TemporaryDirectory() as td:
            lib = library()
            events = []
            controller = None
            class Store:
                def __init__(self, target):
                    self.target = target
                def snapshot(self):
                    if self.target == 'previous-target':
                        events.append('recover-previous')
                        (controller._write_root(lib) / 'transaction.json').unlink()
                    return CueSnapshot(dict(ID='1', UUID='track-one', FolderPath='/synthetic/one.wav'), (), None)
                def apply(self, _request):
                    events.append('apply-current')
            controller = TagEditController(Path(td), cue_store_factory=lambda _lib, tid: Store(tid))
            controller.configure(lib, True)
            journal = controller._write_root(lib) / 'transaction.json'
            journal.parent.mkdir(parents=True)
            journal.write_text(json.dumps(dict(format='soundbox-native-cues-v1', track_id='previous-target')))
            self.assertTrue(controller.request_cue('1', 'hot', 0, 'add', 10003))
            self.wait_for(lambda: not controller.busy)
            self.assertEqual(events, ['recover-previous', 'apply-current'])
            self.assertFalse(journal.exists())
            controller.shutdown()

    def test_startup_recovery_uses_prior_authority_without_trusting_cached_library(self):
        import json
        from dataclasses import asdict
        from soundbox.rekordbox_cue_store import CueLibraryAuthority
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            lib = library()
            permission = CueLibraryAuthority(str(lib.source), lib.source_library_id, 'a' * 64, True)
            (root / 'rekordbox-cue-authority.json').write_text(json.dumps(asdict(permission)))
            recovered = []
            class Store:
                def snapshot(self):
                    recovered.append('verified-prior-operation')
            factory = mock.Mock(return_value=Store())
            controller = TagEditController(root, cue_store_factory=factory)
            self.assertIsNone(controller.lib)  # startup has no trusted cache/library yet
            journal = controller._write_root(lib) / 'transaction.json'
            journal.parent.mkdir(parents=True)
            journal.write_text(json.dumps(dict(format='soundbox-native-cues-v1', track_id='previous-target')))
            controller.recover_before_read(Path(lib.source))
            self.assertEqual(recovered, ['verified-prior-operation'])
            self.assertEqual(factory.call_args.args[1], 'previous-target')
            factory.reset_mock()
            controller.recover_before_read(Path('/unaccepted/master.db'))
            factory.assert_not_called()
            controller.shutdown()

    def test_cbr_analysis_startup_recovery_requires_its_own_source_bound_authority(self):
        import json
        from types import SimpleNamespace
        from soundbox import rekordbox_cbr_analysis_store as cbr
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);lib=library();controller=TagEditController(root)
            authority=SimpleNamespace(library_id=lib.source_library_id)
            (root/'rekordbox-cbr-analysis-authority.json').write_text(json.dumps(dict(library_id=lib.source_library_id)))
            journal=controller._write_root(lib)/'transaction.json';journal.parent.mkdir(parents=True)
            journal.write_text(json.dumps(dict(format=cbr.FORMAT,plan_folder=str(root/'cbr-plan'))))
            original=journal.read_bytes()
            def permission(path,source,library_id):
                return authority if str(source)==str(lib.source) and library_id==lib.source_library_id else None
            with mock.patch.object(cbr,'load_authority',side_effect=permission), \
                    mock.patch.object(cbr,'CBRAnalysisStore') as store:
                controller.recover_before_read(Path(lib.source))
                store.assert_called_once_with(authority,str(root/'cbr-plan'))
                store.return_value.recover.assert_called_once_with()
                store.reset_mock();controller.recover_before_read(Path('/foreign/master.db'));store.assert_not_called()
            with mock.patch.object(cbr,'load_authority',return_value=None), \
                    mock.patch.object(cbr,'CBRAnalysisStore') as store, \
                    self.assertRaisesRegex(RuntimeError,'no accepted library permission'):
                controller._recover_pending(lib)
            store.assert_not_called();self.assertEqual(journal.read_bytes(),original);controller.shutdown()

    def test_analysis_startup_recovery_preserves_legacy_wav_dispatch_and_unknown_journal(self):
        import json
        from types import SimpleNamespace
        from soundbox import rekordbox_analysis_store as wav,rekordbox_cbr_analysis_store as cbr
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);lib=library();controller=TagEditController(root)
            authority=SimpleNamespace(library_id=lib.source_library_id)
            (root/'rekordbox-analysis-authority.json').write_text(json.dumps(dict(library_id=lib.source_library_id)))
            journal=controller._write_root(lib)/'transaction.json';journal.parent.mkdir(parents=True)
            journal.write_text(json.dumps(dict(format=wav.FORMAT,plan_folder=str(root/'legacy-plan'))))
            with mock.patch.object(wav,'load_authority',return_value=authority), \
                    mock.patch.object(wav,'AnalysisStore') as store, \
                    mock.patch.object(cbr,'CBRAnalysisStore') as cbr_store:
                controller.recover_before_read(Path(lib.source))
                store.assert_called_once_with(authority,str(root/'legacy-plan'))
                store.return_value.recover.assert_called_once_with();cbr_store.assert_not_called()
                journal.write_text(json.dumps(dict(format='unaccepted-analysis-profile')))
                original=journal.read_bytes();store.reset_mock()
                with self.assertRaisesRegex(RuntimeError,'Unrecognized rekordbox recovery'):
                    controller.recover_before_read(Path(lib.source))
                store.assert_not_called();self.assertEqual(journal.read_bytes(),original)
            controller.shutdown()

    def test_refresh_recovers_before_read_and_stops_when_recovery_is_unknown(self):
        from desktop.workers import RefreshWorker
        events = []
        worker = RefreshWorker('/synthetic/master.db')
        worker.preflight = lambda _source: events.append('recovery')
        with mock.patch('desktop.workers.rekordbox_db.load_live',
                        side_effect=lambda _source: events.append('snapshot') or library()):
            worker.run()
        self.assertEqual(events, ['recovery', 'snapshot'])
        worker = RefreshWorker('/synthetic/master.db')
        worker.preflight = mock.Mock(side_effect=RuntimeError('Unknown recovery; evidence preserved'))
        with mock.patch('desktop.workers.rekordbox_db.load_live') as reader:
            worker.run()
            reader.assert_not_called()
        self.assertIn('Unknown recovery', worker.error)
