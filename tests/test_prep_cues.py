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

    def _check_media_startup_recovery(self, kind, phase, mode='delete'):
        from contextlib import ExitStack
        from dataclasses import asdict
        import hashlib
        import json
        import subprocess
        import sys
        from uuid import uuid4
        from desktop.workers import RefreshWorker
        from soundbox import media_conversion_store as media, rekordbox_locator_store as locator
        from soundbox import rekordbox_cue_store as cues, rekordbox_tags as tags
        from soundbox.rekordbox_write import _connect, _state
        from tests.support.native_library import source_fixture
        from tests.support.audio import wave as pcm_wave
        from test_wave_conversion import wave as float_wave

        with tempfile.TemporaryDirectory() as td, ExitStack() as patches:
            root=Path(td).resolve();source=root/'source';source_fixture(source)
            data=root/'data';data.mkdir();database=source/'master.db';xml=source/'masterPlaylists6.xml'
            audio=root/'song.wav';original=root/'original.flac';original.write_bytes(b'owned original fixture')
            (float_wave if kind=='conversion' else pcm_wave)(audio)
            before_audio=audio.read_bytes();before_xml=xml.read_bytes()
            conn=_connect(database)
            try:
                conn.execute("UPDATE djmdContent SET UUID='uuid-one',FolderPath=?,FileType=?,FileNameL=?,rb_local_synced=1,usn=NULL WHERE ID='1'",
                             (str(audio) if kind=='conversion' else '/never/open.wav',11 if kind=='conversion' else 5,
                              'song.wav' if kind=='conversion' else 'song.flac'))
                schema=tags._schema(conn);before=_state(conn)
            finally:conn.close()
            before_db=database.read_bytes()
            for obj,key in ((cues,'_closed'),(tags,'_assert_closed')):
                patches.enter_context(mock.patch.object(obj,key,return_value=None))
            for obj in (cues,tags):
                patches.enter_context(mock.patch.object(obj,'_installed_version',return_value=tags.NATIVE_VERSION))
            if kind=='conversion':
                authority=media.ConversionAuthority(str(database),'123',schema,'0'*64,True)
                store=media.ConversionStore(authority,data)
            else:
                authority=locator.LocatorAuthority(str(database),'123',schema,'0'*64,('1',))
                store=locator.LocatorStore(authority,data)
            operation=str(uuid4())
            payload=dict(kind=kind,authority=asdict(authority),data=str(data),phase=phase,mode=mode,
                         audio=str(audio),operation=operation)
            if kind=='locator':
                payload['plan']=store.prepare([dict(id='1',uuid='uuid-one',before_path='/never/open.wav',
                    target=str(audio),original=str(original),before_sha256=locator._sha(original),after_sha256=locator._sha(audio))])
            (data/f'rekordbox-media-{kind}-authority.json').write_text(json.dumps(
                dict(format=f'soundbox-media-{kind}-authority-v1',**asdict(authority))))
            child=root/'child.json';child.write_text(json.dumps(payload))
            code='''import json,os,sys
from pathlib import Path
from soundbox import media_conversion_store as m,rekordbox_locator_store as l
from soundbox import rekordbox_cue_store as c,rekordbox_tags as t
b=json.loads(Path(sys.argv[1]).read_bytes());c._closed=lambda:None;t._assert_closed=lambda:None
c._installed_version=t._installed_version=lambda:t.NATIVE_VERSION
if b['kind']=='conversion':
 w=m.ConversionStore(m.ConversionAuthority(**b['authority']),b['data'])
 m._fault=lambda p:os._exit(73) if p==b['phase'] else None
 w.convert(b['audio'],b['operation'],native=[dict(id='1',uuid='uuid-one')])
else:
 a=b['authority'];a['track_ids']=tuple(a['track_ids']);w=l.LocatorStore(l.LocatorAuthority(**a),b['data'])
 old=w.lease._connect
 def connect():
  conn=old()
  if b['mode']=='wal':conn.execute('PRAGMA journal_mode=WAL')
  return conn
 w.lease._connect=connect;l._fault=lambda p:os._exit(73) if p==b['phase'] else None
 w.apply(b['plan'])
'''
            crashed=subprocess.run([sys.executable,'-c',code,str(child)],capture_output=True,text=True)
            self.assertEqual(crashed.returncode,73,crashed.stderr);self.assertTrue(store.journal.exists())
            controller=TagEditController(data)
            try:
                self.assertIsNone(controller.lib)
                def snapshot():
                    conn=_connect(database,readonly=True)
                    try:return _state(conn)
                    finally:conn.close()
                def reader(_source):
                    # This is the actual RefreshWorker reader boundary, before any source parse.
                    self.assertFalse(store.journal.exists())
                    self.assertEqual(xml.read_bytes(),before_xml)
                    expected=payload['plan']['after'] if kind=='locator' and phase=='committed' else before
                    self.assertEqual(snapshot(),expected)
                    if kind=='conversion':
                        result=json.loads((store._folder(operation)/'receipt.json').read_bytes())
                        self.assertEqual(Path(result['original']).read_bytes(),before_audio)
                        self.assertEqual(media._sha(audio),result['after_sha256'])
                        self.assertEqual(database.read_bytes(),before_db)
                    else:
                        self.assertEqual(audio.read_bytes(),before_audio)
                        self.assertEqual(original.read_bytes(),b'owned original fixture')
                    return library()
                worker=RefreshWorker(database);worker.preflight=controller.recover_before_read
                with mock.patch('desktop.workers.rekordbox_db.load_live',side_effect=reader) as load:
                    worker.run();load.assert_called_once_with(database)
                self.assertEqual(worker.error,'');self.assertIsNotNone(worker.result)
                def inventory():
                    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
                            for p in root.rglob('*') if p.is_file()}
                completed=inventory();controller.recover_before_read(database)
                self.assertEqual(inventory(),completed)
            finally:controller.shutdown()

    def test_conversion_authority_alone_recovers_real_interruption_before_startup_reader(self):
        for phase in ('journal','replaced'):
            with self.subTest(phase=phase):self._check_media_startup_recovery('conversion',phase)

    def test_locator_authority_alone_recovers_real_interruption_before_startup_reader_including_wal(self):
        for phase,mode in (('sql','delete'),('committed','delete'),('sql','wal'),('committed','wal')):
            with self.subTest(phase=phase,mode=mode):self._check_media_startup_recovery('locator',phase,mode)

    def test_each_media_authority_blocks_unknown_startup_journal_without_borrowing_foreign_permission(self):
        from dataclasses import asdict
        import json
        from sqlcipher3 import dbapi2 as sqlcipher
        from desktop.workers import RefreshWorker
        from soundbox import media_conversion_store as media,rekordbox_locator_store as locator
        for kind in ('conversion','locator'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as td:
                root=Path(td);lib=library();controller=TagEditController(root)
                try:
                    authority=(media.ConversionAuthority(str(lib.source),lib.source_library_id,'a'*64,'0'*64,True)
                               if kind=='conversion' else locator.LocatorAuthority(str(lib.source),lib.source_library_id,'a'*64,'0'*64,('1',)))
                    path=root/f'rekordbox-media-{kind}-authority.json'
                    path.write_text(json.dumps(dict(format=f'soundbox-media-{kind}-authority-v1',**asdict(authority))))
                    journal=controller._write_root(lib)/'transaction.json';journal.parent.mkdir(parents=True)
                    journal.write_bytes(b'{"format":"unknown-media-recovery"}');original=journal.read_bytes()
                    worker=RefreshWorker(Path(lib.source));worker.preflight=controller.recover_before_read
                    with mock.patch('desktop.workers.rekordbox_db.load_live') as reader, \
                            mock.patch.object(sqlcipher,'connect',side_effect=AssertionError('No source SQL')) as connection:
                        worker.run();reader.assert_not_called();connection.assert_not_called()
                        self.assertIn('Unrecognized rekordbox recovery state',worker.error)
                        with mock.patch.object(controller,'_recover_pending') as dispatch:
                            controller.recover_before_read(Path('/foreign/master.db'));dispatch.assert_not_called()
                        connection.assert_not_called()
                    self.assertEqual(journal.read_bytes(),original)
                    # Foreign and invalid permissions disable writes without blocking ordinary browsing.
                    body=json.loads(path.read_bytes());body['library_id']='another-library'
                    body['database']='/foreign/master.db'
                    for permission in (body,{'format':'invalid-authority-format'}):
                        path.write_text(json.dumps(permission))
                        with mock.patch('desktop.workers.rekordbox_db.load_live',return_value=lib) as reader:
                            worker=RefreshWorker(Path(lib.source));worker.preflight=controller.recover_before_read
                            worker.run();reader.assert_called_once_with(Path(lib.source));self.assertEqual(worker.error,'')
                    self.assertEqual(journal.read_bytes(),original)
                finally:controller.shutdown()

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
