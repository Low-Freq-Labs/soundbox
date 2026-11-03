"""Merge recovery uses the real accepted stores on an encrypted TEMP library."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtCore import QEventLoop,QTimer

import test_shared_import_controller as fixture
from desktop.playlist_unification import PlaylistUnificationController
from desktop.rekordbox_playlists import PlaylistEditController,QUEUE
from soundbox import playlist_unification as merge,shared_imports,manifest
from soundbox.rekordbox_write import _connect,_state


class UnifiedPlaylists(unittest.TestCase):
    @classmethod
    def setUpClass(cls):fixture.SharedImports.setUpClass()

    def setUp(self):
        self.fixture=fixture.SharedImports();self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        w=self.window=self.fixture.window
        self.tracks=self.fixture.admit(native=False)
        for track in self.tracks:w.imported[track.track_id]['added_ts']=1234.5
        w.settings[shared_imports.KEY]=[]
        w.hidden=set();w.custom={'Favorites':[t.track_id for t in self.tracks]}
        w.settings['custom_playlist_tree']=[dict(folder='00000000-0000-4000-8000-000000000010',
            name='Flavors',children=[dict(playlist='Favorites')])]
        w._visible_collection_tracks=lambda:list(w.lib.tracks_by_id.values())
        w._row_for=lambda track:dict(artist='Artist '+track.track_id[-1])
        w._apply_settings_to_lib=mock.Mock();w._populate_tree=mock.Mock();w._view_key=''
        w._flash_status=mock.Mock()
        w._native_refresh_view=mock.Mock();w.native_analysis_controller=SimpleNamespace(pending=[])
        def apply_catalog(catalog,request):
            w.lib=shared_imports.project(self.fixture.load(self.fixture.database),w.settings.get(shared_imports.BINDINGS,[]))
            from soundbox import imports
            imports.inject(w.lib,w.imported,repair_missing=False)
            w.tag_controller.configure(w.lib,True)
        w._native_apply_catalog=apply_catalog
        w.playlist_controller=PlaylistEditController(w,store_factory=lambda _:self.fixture.playlist)
        self.addCleanup(w.playlist_controller.stop)
        self.enterContext(mock.patch('desktop.rekordbox_playlists.assert_rekordbox_closed',return_value=None))
        self.controller=PlaylistUnificationController(w)
        self.addCleanup(self.controller.stop)
        self.controller.timer.setInterval(5)
        w.settings[merge.KEY]=merge.new(w.lib.source,w.lib.source_library_id)

    def state(self):
        conn=_connect(self.fixture.database,readonly=True)
        try:return _state(conn)
        finally:conn.close()

    def wait(self,predicate):
        loop=QEventLoop();poll=QTimer();poll.setInterval(5)
        def errors():
            return ([self.window.settings.get(merge.KEY,{}).get('error','')]
                    +[job.get('error','') for key in (QUEUE,shared_imports.KEY)
                      for job in self.window.settings.get(key,[])])
        poll.timeout.connect(lambda:loop.quit() if predicate() or any(errors()) else None)
        limit=QTimer();limit.setSingleShot(True);limit.timeout.connect(loop.quit)
        poll.start();limit.start(30000)
        if not predicate():loop.exec()
        poll.stop();limit.stop()
        self.assertTrue(predicate(),repr([error for error in errors() if error]))

    def complete(self):
        w=self.window;state=w.settings[merge.KEY]
        return (not state['error'] and state['customs_done'] and len(state['nodes'])==2
            and not w.playlist_controller.pending and not w.shared_import_controller.pending
            and not w.tag_controller.owned and not self.controller.timer.isActive())

    def test_existing_imports_and_custom_hierarchy_are_receipt_backed_without_artist_jobs(self):
        w=self.window;before=self.state();custom=deepcopy(w.custom)
        self.controller.schedule();self.wait(self.complete)
        state=w.settings[merge.KEY];by_id={p.pid:p for p in w.lib.playlists}
        favorites=by_id[state['nodes']['custom:Favorites']['id']]
        self.assertEqual(favorites.keys,custom['Favorites'])
        self.assertEqual(w.custom,custom)
        self.assertTrue(all(t.source_uuid for t in w.lib.tracks_by_id.values()))
        self.assertTrue(all(w.lib.tracks_by_id[t.track_id].added_ts==1234.5 for t in self.tracks))
        self.assertTrue(any(merge.KEY in save and save.get(QUEUE) for save in self.fixture.saves))
        after=self.state()
        for table in before['tables']:
            if table not in ('djmdContent','djmdPlaylist','djmdSongPlaylist','agentRegistry'):
                self.assertEqual(after['tables'][table],before['tables'][table],table)
        committed=self.state();self.controller.stop()
        restarted=PlaylistUnificationController(w);self.addCleanup(restarted.stop)
        restarted.pump()
        self.assertEqual(self.state(),committed)
        alias=replace(favorites,export_key='cust:Favorites')
        self.assertEqual(manifest.playlist_key(alias),'cust:Favorites')
        self.assertFalse(any(key=='artists' or key.startswith('artist:') for key in state['nodes']))
        w._row_for=mock.Mock(side_effect=AssertionError('Artist changes must stay virtual'))
        with mock.patch.object(w.playlist_controller,'enqueue') as enqueue:
            restarted.schedule();restarted.pump()
        enqueue.assert_not_called()
        self.assertFalse(restarted.timer.isActive())
        self.assertEqual(self.state(),committed)
        self.assertEqual(next(p.keys for p in w.lib.playlists if p.pid==favorites.pid),custom['Favorites'])

    def test_failed_intention_save_admits_no_source_work(self):
        before=self.state();settings=deepcopy(self.window.settings)
        self.fixture.save_hook=lambda _:False
        self.controller.pump()
        self.assertEqual(self.window.settings,settings)
        self.assertEqual(self.state(),before)
        self.assertFalse(self.window.tag_controller.owned)

    def test_source_switch_does_not_retarget_the_merge(self):
        before=self.state();self.window.settings[merge.KEY]['library_id']='456'
        self.controller.pump()
        self.assertEqual(self.state(),before)
        self.assertEqual(self.window.settings[shared_imports.KEY],[])

    def test_same_named_native_playlist_is_preserved_for_review(self):
        # No song matching, playlist adoption or source overwrite by name.
        self.window.custom={'Existing':[]};self.window.settings.pop('custom_playlist_tree')
        self.window._visible_collection_tracks=lambda:[]
        before=self.state();self.controller.pump()
        self.assertIn('conflicts',self.window.settings[merge.KEY]['error'])
        self.assertEqual(self.state(),before)

    def test_duplicate_custom_membership_is_retained(self):
        ids=self.window.custom['Favorites'];ids.insert(1,ids[0])
        self.controller.schedule();self.wait(self.complete)
        identity=self.window.settings[merge.KEY]['nodes']['custom:Favorites']['id']
        self.assertEqual(next(p.keys for p in self.window.lib.playlists if p.pid==identity),ids)

    def test_fresh_native_library_never_creates_artist_state_or_source_jobs(self):
        w=self.window;w.settings.pop(merge.KEY);w.settings.pop('custom_playlist_tree')
        w.custom={};w.imported={};w.lib=self.fixture.load(self.fixture.database)
        before=self.state();settings=deepcopy(w.settings)
        w._visible_collection_tracks=mock.Mock(side_effect=AssertionError('No migration scan for a fresh native library'))
        with mock.patch.object(w.playlist_controller,'enqueue') as enqueue:
            self.controller.schedule();self.controller.pump()
        enqueue.assert_not_called()
        self.assertNotIn(merge.KEY,w.settings)
        self.assertEqual(w.settings,settings)
        self.assertFalse(self.controller.timer.isActive())
        self.assertEqual(self.state(),before)

    def test_source_selection_never_silently_activates_legacy_backfill(self):
        self.window.settings.pop(merge.KEY)
        before=self.state();settings=deepcopy(self.window.settings)
        self.controller.pump()
        self.assertEqual(self.window.settings,settings)
        self.assertEqual(self.state(),before)
        self.assertEqual(self.window.settings[shared_imports.KEY],[])

    def test_custom_batch_admission_is_bounded_and_future_bindings_survive_restart(self):
        from soundbox.rekordbox_playlist_store import MAX_BATCH_COMMANDS,command_requests
        w=self.window;w.imported={};w.settings.pop('custom_playlist_tree')
        original=next(t for t in w.lib.tracks_by_id.values() if t.source_uuid)
        w._visible_collection_tracks=lambda:[original]
        w.custom={f'Custom {i}':[original.track_id] for i in range(70)}
        before=self.state();self.controller.pump()
        w.tag_controller.busy=True
        try:
            pending=deepcopy(w.playlist_controller.pending)
            self.assertEqual(len(pending),1)
            commands=command_requests(pending[0]['request'])
            self.assertEqual(len(commands),MAX_BATCH_COMMANDS)
            creates=[r for r in commands if r['operation']=='create']
            state=w.settings[merge.KEY]
            self.assertTrue(all(key.startswith('custom:') for key in state['nodes']))
            self.assertEqual({node['id'] for node in state['nodes'].values()},{r['playlist_id'] for r in creates})
            self.assertTrue(any(saved.get(QUEUE)==pending and saved.get(merge.KEY)==state for saved in self.fixture.saves))
            restarted=PlaylistUnificationController(w);self.addCleanup(restarted.stop)
            restarted.pump()
            self.assertEqual(w.playlist_controller.pending,pending)
            self.assertEqual(self.state(),before)
        finally:w.tag_controller.busy=False

    def test_failed_batch_save_keeps_all_future_bindings_and_source_unchanged(self):
        w=self.window;w.custom={};w.imported={};w.settings.pop('custom_playlist_tree')
        original=next(t for t in w.lib.tracks_by_id.values() if t.source_uuid)
        w.custom={'Migrated':[original.track_id]}
        w._visible_collection_tracks=lambda:[original]
        before=self.state();settings=deepcopy(w.settings)
        self.fixture.save_hook=lambda _:False
        self.controller.pump()
        self.assertEqual(w.settings,settings)
        self.assertEqual(self.state(),before)
        self.assertFalse(w.tag_controller.owned)

    def test_large_custom_membership_splits_without_artist_work_or_losing_songs(self):
        from soundbox.rekordbox_playlist_store import command_requests
        w=self.window
        original=next(t for t in w.lib.tracks_by_id.values() if t.source_uuid)
        w.custom['Favorites'].append(original.track_id)
        wanted=list(w.custom['Favorites'])
        with mock.patch('desktop.playlist_unification._BATCH_TRACK_LIMIT',2):
            self.controller.schedule();self.wait(self.complete)
        binding=w.settings[merge.KEY]['nodes']['custom:Favorites']
        self.assertEqual(next(p.keys for p in w.lib.playlists if p.pid==binding['id']),wanted)
        requests=[job['request'] for saved in self.fixture.saves for job in saved.get(QUEUE,[])]
        self.assertTrue(all(sum(len(command['tracks']) for command in command_requests(request))<=2 for request in requests))
        appends={command['operation_id']:command for request in requests for command in command_requests(request)
                 if command['operation']=='append' and command['playlist_id']==binding['id']}
        self.assertEqual(sorted(len(command['tracks']) for command in appends.values()),[1,2])
        self.assertNotIn('artists',w.settings[merge.KEY]['nodes'])

    def test_two_thousand_artists_and_archived_bindings_never_schedule_native_work(self):
        w=self.window;state=w.settings[merge.KEY];state['customs_done']=True
        state['nodes']['artists']=dict(id='90000',uuid='00000000-0000-4000-8000-000000090000')
        for index in range(2000):
            identity=100000+index
            state['nodes'][merge.artist_key('Artist '+str(index))]=dict(id=str(identity),
                uuid=f'00000000-0000-4000-8000-{identity:012d}')
        before=self.state();settings=deepcopy(w.settings)
        w._visible_collection_tracks=mock.Mock(side_effect=AssertionError('Completed migration must not scan artists'))
        w._row_for=mock.Mock(side_effect=AssertionError('Artist grouping is virtual'))
        with mock.patch.object(w.playlist_controller,'enqueue') as enqueue, \
                mock.patch.object(w.shared_import_controller,'schedule') as imports:
            for _ in range(3):self.controller.schedule();self.controller.pump()
        enqueue.assert_not_called();imports.assert_not_called()
        self.assertFalse(self.controller.timer.isActive())
        self.assertEqual(w.settings,settings)
        self.assertEqual(self.state(),before)
        self.assertEqual(merge.hidden_native_roots(state),{node['id'] for node in state['nodes'].values()})
