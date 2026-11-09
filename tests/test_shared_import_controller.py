"""Real registration + membership stores on a synthetic encrypted TEMP source."""
from copy import deepcopy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QFrame

from desktop.shared_imports import SharedImportController
from desktop.tag_controller import TagEditController
from soundbox import shared_imports as jobs, imports, rekordbox_cue_store as cues, rekordbox_tags as tags
from soundbox.library import Library, Playlist, Track
from soundbox.rekordbox_registration_store import RegistrationAuthority, RegistrationStore
from soundbox.rekordbox_playlist_store import PlaylistAuthority, PlaylistStore
from soundbox.rekordbox_write import _connect, _rows, _state
from tests.support.native_library import source_fixture
from tests.support.audio import wave
from test_playlist_transaction import fixture as playlist_fixture


class SharedImports(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();source_fixture(self.root/'source')
        extra=self.root/'playlist-schema';extra.mkdir();playlist_fixture(extra)
        self.database=self.root/'source/master.db'
        origin=_connect(extra/'master.db');conn=_connect(self.database)
        try:
            for table in ('djmdPlaylist','djmdSongPlaylist'):
                sql=origin.execute('SELECT sql FROM sqlite_master WHERE name=?',(table,)).fetchone()[0]
                conn.execute('DROP TABLE '+table);conn.execute(sql)
            # The registration fixture's adversarial BLOB counter extension is
            # outside the native playlist counter profile. Both domains retain
            # the independent opaque table in this combined native-shaped DB.
            conn.execute('ALTER TABLE agentRegistry DROP COLUMN opaque')
            row=_rows(origin,'djmdPlaylist')[0]
            conn.execute('INSERT INTO djmdPlaylist VALUES ('+','.join('?' for _ in row)+')',list(row.values()))
            self.schema=tags._schema(conn)
        finally:origin.close();conn.close()
        self.database.with_name('masterPlaylists6.xml').write_bytes((extra/'masterPlaylists6.xml').read_bytes())
        self.recovery=self.root/'recovery'
        for obj,name in ((cues,'_closed'),(tags,'_assert_closed')):
            patch=mock.patch.object(obj,name,return_value=None);patch.start();self.addCleanup(patch.stop)
        for obj in (cues,tags):
            patch=mock.patch.object(obj,'_installed_version',return_value=tags.NATIVE_VERSION);patch.start();self.addCleanup(patch.stop)
        patch=mock.patch('desktop.shared_imports.assert_rekordbox_closed');patch.start();self.addCleanup(patch.stop)
        self.registration=RegistrationStore(RegistrationAuthority(str(self.database),'123',self.schema,'0'*64,True),self.recovery)
        self.playlist=PlaylistStore(PlaylistAuthority(str(self.database),'123',self.schema,True),self.recovery)
        self.window=QFrame();w=self.window
        w.lib=self.load(self.database);w.settings={};w.imported={};w._shutting_down=False
        w._state_maintenance=False;w._refresh_worker=None;w._device_operation_owned=lambda:False
        w.playlist_controller=SimpleNamespace(pending=[]);self.saves=[];self.save_hook=None
        def save():
            self.saves.append(deepcopy(w.settings))
            return self.save_hook(w.settings) if self.save_hook else True
        w._save_state=save;w._native_pending_changed=mock.Mock();w._native_reconcile_source=mock.Mock()
        w.tag_controller=TagEditController(self.root/'state',w,store_factory=lambda _:None)
        w.tag_controller._recover_pending=mock.Mock();w.tag_controller.configure(w.lib,True)
        def loaded(lib):
            w.lib=jobs.project(lib,w.settings.get(jobs.BINDINGS,[]))
            imports.inject(w.lib,w.imported,repair_missing=False);w.tag_controller.configure(w.lib,True)
        w._on_refresh_done=loaded
        self.controller=self.controller_for_window();w.shared_import_controller=self.controller
        self.addCleanup(self.close)

    def controller_for_window(self):
        return SharedImportController(self.window,registration_factory=lambda _:self.registration,
            playlist_factory=lambda _:self.playlist,loader=self.load)

    def close(self):
        self.controller.stop();self.window.tag_controller.shutdown()
        self.window.deleteLater();self.app.processEvents()

    def load(self,source):
        conn=_connect(source,readonly=True)
        try:rows=_rows(conn,'djmdContent');nodes=_rows(conn,'djmdPlaylist');members=_rows(conn,'djmdSongPlaylist')
        finally:conn.close()
        tracks={r['ID']:Track(r['ID'],r['Title'],'',Path(r['FolderPath']).as_uri(),source_uuid=r['UUID']) for r in rows}
        from soundbox.rekordbox_playlist_plan import revision
        catalog=dict(nodes=nodes,members=members,revision=revision(nodes,members))
        playlists=[Playlist(r['Name'],[],[m['ContentID'] for m in sorted(members,key=lambda m:m['TrackNo'])
            if m['PlaylistID']==r['ID'] and m['rb_local_deleted']==0],r['ID']) for r in nodes if r['rb_local_deleted']==0]
        return Library(tracks,playlists,source,source_kind='rekordbox-db',source_library_id='123',source_playlist_catalog=catalog)

    def wait(self,predicate):
        loop=QEventLoop();poll=QTimer();poll.setInterval(5);poll.timeout.connect(lambda:loop.quit() if predicate() else None)
        limit=QTimer();limit.setSingleShot(True);limit.timeout.connect(loop.quit)
        poll.start();limit.start(15000)
        if not predicate():loop.exec()
        poll.stop();limit.stop();self.assertTrue(predicate(),self.window.tag_controller.registration_update_status)

    def admit(self,names=('one','two'),*,native=True):
        tracks=[]
        for name in names:
            audio=self.root/(name+'.wav');wave(audio)
            track=Track('sb-'+name,name,'',audio.as_uri(),imported=True);tracks.append(track)
            self.window.imported[track.track_id]=dict(path=str(audio),time=1,size=audio.stat().st_size)
            self.window.lib.tracks_by_id[track.track_id]=track
        dest=(dict(kind='native',id='1000',uuid=self.window.lib.source_playlist_catalog['nodes'][0]['UUID'])
              if native else dict(kind='collection'))
        self.window.settings[jobs.KEY]=jobs.admit([],self.window.lib,tracks,dest,dict(automatic=False))
        return tracks

    def test_native_drop_registers_before_membership_preserving_order_and_stable_logical_ids(self):
        tracks=self.admit();audio={t.path:t.path.read_bytes() for t in tracks}
        self.controller.schedule();self.wait(lambda:not self.controller.pending and not self.window.tag_controller.owned)
        self.assertEqual(self.window.lib.playlists[0].keys,['sb-one','sb-two'])
        self.assertEqual(len(self.load(self.database).tracks_by_id),3)
        self.assertEqual([b['local_id'] for b in self.window.settings[jobs.BINDINGS]],['sb-one','sb-two'])
        self.assertTrue(any(s[jobs.KEY] and s[jobs.KEY][0]['request'] and not s[jobs.KEY][0]['bindings'] for s in self.saves))
        self.assertTrue(any(s[jobs.KEY] and s[jobs.KEY][0]['membership'] for s in self.saves))
        self.assertTrue(all(not s[jobs.KEY] or not s[jobs.KEY][0]['preferences']['automatic'] for s in self.saves))
        self.assertEqual({p:p.read_bytes() for p in audio},audio)
        self.assertFalse((self.recovery/'transaction.json').exists())
        # A later ordinary playlist edit admitted after membership preparation
        # must follow this app's own commit without requiring a stale retry.
        self.admit(('three',))
        from soundbox.rekordbox_playlist_store import request_for
        def enqueue_later(settings):
            pending=settings.get(jobs.KEY,[])
            if pending and pending[0]['membership'] and not settings.get('rekordbox_playlist_edits'):
                request=request_for(self.window.lib.source_playlist_catalog,'rename',identity='1000',name='Later rename')
                request['xml_timestamp']=pending[0]['membership']['xml_timestamp']
                settings['rekordbox_playlist_edits']=[dict(source=str(self.database),library_id='123',request=request,error='',retry=False)]
            return True
        self.save_hook=enqueue_later;self.controller.schedule()
        self.wait(lambda:not self.controller.pending and not self.window.tag_controller.owned)
        later=self.window.settings['rekordbox_playlist_edits'][0]['request']
        self.assertEqual(later['revision'],self.window.lib.source_playlist_catalog['revision'])
        self.assertGreater(later['xml_timestamp'],next(s[jobs.KEY][0]['membership']['xml_timestamp']
            for s in reversed(self.saves) if s.get(jobs.KEY) and s[jobs.KEY][0]['membership']))
        self.playlist.apply(later)
        self.assertEqual(self.playlist.snapshot()['nodes'][0]['Name'],'Later rename')

    def test_membership_ack_failure_restores_original_later_playlist_request_and_clock(self):
        from soundbox.rekordbox_playlist_store import request_for
        self.admit(('rebase-fault',));captured={};failed=False
        def save(settings):
            nonlocal failed
            pending=settings.get(jobs.KEY,[])
            if pending and pending[0]['membership'] and not captured:
                request=request_for(self.window.lib.source_playlist_catalog,'rename',identity='1000',name='Later rename')
                request['xml_timestamp']=pending[0]['membership']['xml_timestamp']
                queue=[dict(source=str(self.database),library_id='123',request=request,error='',retry=False)]
                captured.update(queue=queue,request=request,before=deepcopy(request))
                settings['rekordbox_playlist_edits']=queue
            if captured and not pending and not failed:
                failed=True;return False
            return True
        self.save_hook=save;self.controller.schedule()
        self.wait(lambda:bool(self.controller.pending and self.controller.pending[0]['error']) and not self.window.tag_controller.owned)
        self.assertTrue(failed)
        self.assertIs(self.window.settings['rekordbox_playlist_edits'],captured['queue'])
        self.assertIs(self.window.settings['rekordbox_playlist_edits'][0]['request'],captured['request'])
        self.assertEqual(captured['request'],captured['before'])
        self.controller.retry();self.wait(lambda:not self.controller.pending and not self.window.tag_controller.owned)
        self.assertEqual(captured['request'],captured['before'])
        # The verified retained committed pair supplies its exact causal edge
        # on retry; a current native catalog alone never grants this rebase.
        self.assertNotEqual(self.window.settings['rekordbox_playlist_edits'][0]['request']['revision'],captured['before']['revision'])

    def test_collection_import_is_native_without_analysis_or_playlist_membership(self):
        self.admit(('one',),native=False);self.controller.schedule()
        self.wait(lambda:not self.controller.pending and not self.window.tag_controller.owned)
        self.assertEqual(self.window.lib.playlists[0].keys,[])
        self.assertFalse(self.window.lib.tracks_by_id['sb-one'].imported)
        conn=_connect(self.database,readonly=True)
        try:row=next(r for r in _rows(conn,'djmdContent') if r['ID']!='1')
        finally:conn.close()
        self.assertEqual((row['BPM'],row['KeyID'],row['Analysed']),(0,'0',0))

    def test_conversion_readiness_is_durable_before_analysis_and_registration_prepare(self):
        self.admit(('priority',),native=False)
        before=self.database.read_bytes();self.window._analysis_priority_pending=lambda:True
        with mock.patch.object(self.registration,'prepare',wraps=self.registration.prepare) as prepare, \
                mock.patch.object(self.registration,'apply',wraps=self.registration.apply) as apply:
            self.controller.schedule()
            self.wait(lambda:self.controller.pending[0].get('media_ready') and not self.window.tag_controller.owned)
            self.assertIsNone(self.controller.pending[0]['request'])
            self.assertEqual(self.database.read_bytes(),before)
            prepare.assert_not_called();apply.assert_not_called()
            self.assertTrue(any(s[jobs.KEY][0].get('media_ready') for s in self.saves))
            self.window._analysis_priority_pending=lambda:False
            self.controller.schedule();self.wait(lambda:not self.controller.pending and not self.window.tag_controller.owned)
            prepare.assert_called_once();apply.assert_called_once()
        self.assertEqual(len(self.load(self.database).tracks_by_id),2)

    def test_queued_cues_and_grid_do_not_deadlock_compatibility_prerequisite(self):
        self.admit(('queued-source-intents',),native=False)
        self.window._analysis_priority_pending=lambda:True
        tags=self.window.tag_controller
        tags.analysis_priority=self.window._analysis_priority_pending
        self.window._analysis_source_busy=lambda:bool(tags.busy or tags.checking or tags.admission_paused)
        tags._cue_queue=[SimpleNamespace(page='hot')];tags.grid_waiting=True
        before=self.database.read_bytes()
        try:
            with mock.patch.object(self.registration,'prepare',side_effect=AssertionError('No DB preparation')), \
                    mock.patch.object(self.registration,'apply',side_effect=AssertionError('No DB publication')):
                self.controller.schedule()
                self.wait(lambda:self.controller.pending[0].get('media_ready') and not tags.busy)
            self.assertEqual(self.database.read_bytes(),before)
            self.assertTrue(tags._cue_queue);self.assertTrue(tags.grid_waiting)
        finally:tags._cue_queue=[];tags.grid_waiting=False

    def test_later_drop_conversion_bypasses_only_head_analysis_wait_and_preserves_db_fifo(self):
        from soundbox import analysis_jobs
        for first_failed in (False,True):
            with self.subTest(first_failed=first_failed):
                first=self.admit(('head-'+str(first_failed),),native=False)[0]
                head=deepcopy(self.controller.pending[0]);head['media_ready']=True
                second=self.admit(('later-'+str(first_failed),),native=False)[0]
                later=deepcopy(self.controller.pending[0])
                self.window.settings[jobs.KEY]=[head,later]
                pending=analysis_jobs.admit([],self.window.lib,[second],dict(bpm=True,key=True))
                if first_failed:
                    pending=analysis_jobs.admit(pending,self.window.lib,[first],dict(bpm=True,key=True))
                    next(value for value in pending if value['track_id']==first.track_id).update(
                        state='failed',error='Key detection failed')
                self.window.settings[analysis_jobs.KEY]=pending
                self.window._analysis_priority_pending=lambda:True
                before=self.database.read_bytes()
                with mock.patch.object(self.registration,'prepare',side_effect=AssertionError('No DB preparation')), \
                        mock.patch.object(self.registration,'apply',side_effect=AssertionError('No DB publication')):
                    self.controller.schedule()
                    self.wait(lambda:self.controller.pending[1].get('media_ready') and not self.window.tag_controller.owned)
                self.assertEqual([value['id'] for value in self.controller.pending],[head['id'],later['id']])
                self.assertTrue(all(value['request'] is None for value in self.controller.pending))
                self.assertTrue(all(not value['error'] for value in self.controller.pending))
                self.assertEqual(self.window.settings[analysis_jobs.KEY],pending)
                self.assertEqual(self.database.read_bytes(),before)

    def test_later_conversion_failure_marks_only_its_own_intent(self):
        first=self.admit(('head-conversion-failure',),native=False)[0]
        head=deepcopy(self.controller.pending[0]);head['media_ready']=True
        later=self.admit(('later-conversion-failure',),native=False)[0]
        self.window.settings[jobs.KEY]=[head,deepcopy(self.controller.pending[0])]
        self.window._analysis_priority_pending=lambda:True
        with mock.patch.object(self.registration,'convert_imports',side_effect=ValueError('Compatibility failed')):
            self.controller.schedule()
            self.wait(lambda:bool(self.controller.pending[1]['error']) and not self.window.tag_controller.owned)
        self.assertFalse(self.controller.pending[0]['error'])
        self.assertEqual(self.controller.pending[0]['tracks'][0]['id'],first.track_id)
        self.assertEqual(self.controller.pending[1]['tracks'][0]['id'],later.track_id)
        self.assertIn('Compatibility failed',self.controller.pending[1]['error'])

    def test_failed_media_readiness_save_retries_conversion_before_any_registration(self):
        self.admit(('readiness-save',),native=False);before=self.database.read_bytes();failed=[]
        self.window._analysis_priority_pending=lambda:True
        def save(settings):
            queue=settings.get(jobs.KEY,[])
            if queue and queue[0].get('media_ready') and not failed:
                failed.append(True);return False
            return True
        self.save_hook=save
        with mock.patch.object(self.registration,'prepare',wraps=self.registration.prepare) as prepare:
            self.controller.schedule()
            self.wait(lambda:bool(self.controller.pending[0]['error']) and not self.window.tag_controller.owned)
            self.assertFalse(self.controller.pending[0].get('media_ready',False))
            self.assertEqual(self.database.read_bytes(),before);prepare.assert_not_called()
            self.controller.retry()
            self.wait(lambda:self.controller.pending[0].get('media_ready') and not self.window.tag_controller.owned)
            prepare.assert_not_called()
            self.window._analysis_priority_pending=lambda:False
            self.controller.schedule();self.wait(lambda:not self.controller.pending and not self.window.tag_controller.owned)
        self.assertEqual(len(self.load(self.database).tracks_by_id),2)

    def test_media_receipt_pin_survives_ready_checkpoint_restart_without_detector_results(self):
        import hashlib
        from uuid import UUID,uuid5
        from soundbox import media_conversion_jobs
        track=self.admit(('converted-byte-pin',),native=False)[0]
        job=self.controller.pending[0];job['media_ready']=True
        digest=hashlib.sha256(track.path.read_bytes()).hexdigest()
        self.window.settings[media_conversion_jobs.DONE]={str(track.path):dict(
            operation_id=str(uuid5(UUID(job['id']),str(track.path))),target=str(track.path),after_sha256=digest)}
        self.assertFalse(self.window.settings.get('analysis_results'))
        self.controller.stop();self.controller=self.controller_for_window()
        self.window.shared_import_controller=self.controller
        with mock.patch.object(self.registration,'prepare',wraps=self.registration.prepare) as prepare, \
                mock.patch.object(self.registration,'convert_imports',side_effect=AssertionError('Already prepared media')):
            self.controller.schedule();self.wait(lambda:not self.controller.pending and not self.window.tag_controller.owned)
        self.assertEqual(prepare.call_args.args[0],[dict(id=track.track_id,path=str(track.path),sha256=digest)])

    def test_changed_converted_bytes_after_readiness_cannot_register_without_detector_results(self):
        import hashlib
        from uuid import UUID,uuid5
        from soundbox import media_conversion_jobs
        track=self.admit(('changed-converted-byte-pin',),native=False)[0]
        job=self.controller.pending[0];job['media_ready']=True
        digest=hashlib.sha256(track.path.read_bytes()).hexdigest()
        self.window.settings[media_conversion_jobs.DONE]={str(track.path):dict(
            operation_id=str(uuid5(UUID(job['id']),str(track.path))),target=str(track.path),after_sha256=digest)}
        raw=bytearray(track.path.read_bytes());raw[-1]^=1;track.path.write_bytes(raw)
        before=self.database.read_bytes()
        self.controller.schedule()
        self.wait(lambda:bool(self.controller.pending[0]['error']) and not self.window.tag_controller.owned)
        self.assertEqual(self.database.read_bytes(),before)
        self.assertIsNone(self.controller.pending[0]['request'])
        self.assertIn('captured bytes differ',self.controller.pending[0]['error'])

    def test_invalid_converted_receipt_never_drops_the_expected_byte_pin(self):
        from uuid import UUID,uuid5
        from soundbox import media_conversion_jobs
        track=self.admit(('invalid-converted-byte-pin',),native=False)[0]
        job=self.controller.pending[0];job['media_ready']=True
        self.window.settings[media_conversion_jobs.DONE]={str(track.path):dict(
            operation_id=str(uuid5(UUID(job['id']),str(track.path))),target=str(track.path),after_sha256=None)}
        before=self.database.read_bytes()
        with mock.patch.object(self.registration,'prepare',side_effect=AssertionError('Unproven byte pin')):
            self.controller.pump()
        self.assertIn('receipt is invalid',self.controller.pending[0]['error'])
        self.assertEqual(self.database.read_bytes(),before)
        self.assertIsNone(self.controller.pending[0]['request'])

    def test_analysis_priority_defers_an_uncommitted_prepared_registration(self):
        track=self.admit(('prepared-priority',),native=False)[0]
        request=self.registration.prepare([dict(id=track.track_id,path=str(track.path))])
        self.controller.pending[0]['request']=request;before=self.database.read_bytes()
        self.window._analysis_priority_pending=lambda:True
        with mock.patch.object(self.registration,'committed_bindings',wraps=self.registration.committed_bindings) as reconcile, \
                mock.patch.object(self.registration,'apply',wraps=self.registration.apply) as apply:
            self.controller.schedule()
            self.wait(lambda:reconcile.called and not self.window.tag_controller.owned)
            apply.assert_not_called();self.assertEqual(self.database.read_bytes(),before)
            self.assertEqual(self.controller.pending[0]['request'],request)
            self.window._analysis_priority_pending=lambda:False
            self.controller.schedule();self.wait(lambda:not self.controller.pending and not self.window.tag_controller.owned)
            apply.assert_called_once()

    def test_analysis_priority_does_not_starve_committed_registration_acknowledgement(self):
        track=self.admit(('committed-priority',),native=False)[0]
        request=self.registration.prepare([dict(id=track.track_id,path=str(track.path))])
        self.registration.apply(request)
        self.controller.pending[0]['request']=request
        self.window._analysis_priority_pending=lambda:True
        with mock.patch.object(self.registration,'apply',side_effect=AssertionError('Must reconcile receipt only')):
            self.controller.schedule();self.wait(lambda:not self.controller.pending and not self.window.tag_controller.owned)
        self.assertEqual(len(self.load(self.database).tracks_by_id),2)
        self.assertEqual(self.window.settings[jobs.BINDINGS][0]['local_id'],track.track_id)

    def test_media_ready_validation_is_optional_for_legacy_jobs_and_requires_boolean(self):
        self.admit(('validation',),native=False)
        queue=deepcopy(self.controller.pending);queue[0].pop('media_ready')
        self.assertEqual(jobs.validate(queue),queue)
        for invalid in (None,0,1,'yes'):
            queue[0]['media_ready']=invalid
            with self.subTest(invalid=invalid), self.assertRaisesRegex(Exception,'media preparation'):
                jobs.validate(queue)

    def test_failed_local_analysis_keeps_import_intent_and_still_allows_conversion_prerequisite(self):
        from soundbox import analysis_jobs
        track=self.admit(('failed-local-analysis',),native=False)[0]
        pending=analysis_jobs.admit([],self.window.lib,[track],dict(bpm=True,key=True))
        pending[0].update(state='failed',error='Key detection failed')
        self.window.settings[analysis_jobs.KEY]=pending
        self.window._analysis_priority_pending=lambda:False
        before=self.database.read_bytes()
        with mock.patch.object(self.registration,'prepare',wraps=self.registration.prepare) as prepare:
            self.controller.schedule()
            self.wait(lambda:self.controller.pending[0].get('media_ready') and not self.window.tag_controller.owned)
            self.controller.pump()
            self.assertIn('retry the failed song',self.window.tag_controller.registration_update_status)
            prepare.assert_not_called();self.assertEqual(self.database.read_bytes(),before)
            self.assertEqual(self.window.settings[analysis_jobs.KEY],pending)
            self.assertIsNone(self.controller.pending[0]['request'])

    def test_analysis_priority_defers_uncommitted_membership_but_acknowledges_committed_membership(self):
        from soundbox.rekordbox_playlist_store import request_for
        track=self.admit(('membership-priority',))[0]
        request=self.registration.prepare([dict(id=track.track_id,path=str(track.path))])
        values=self.registration.apply(request)
        binding=[{k:v for k,v in value.items() if k!='registered'} for value in values]
        job=self.controller.pending[0];job.update(request=request,bindings=binding)
        self.window.settings[jobs.BINDINGS]=deepcopy(binding)
        self.window._on_refresh_done(self.load(self.database))
        job['membership']=request_for(self.playlist.snapshot(),'append',identity='1000',tracks=[
            SimpleNamespace(track_id=value['native_id'],source_uuid=value['native_uuid']) for value in binding])
        self.window._analysis_priority_pending=lambda:True
        self.assertFalse(self.controller.source_priority)
        with mock.patch.object(self.playlist,'recover',wraps=self.playlist.recover) as recover, \
                mock.patch.object(self.playlist,'apply',wraps=self.playlist.apply) as apply:
            self.controller.schedule();self.wait(lambda:recover.called and not self.window.tag_controller.owned)
            apply.assert_not_called();self.assertTrue(self.controller.pending)
            self.assertEqual(self.load(self.database).playlists[0].keys,[])
            self.playlist.apply(job['membership'])
            before=self.database.read_bytes()
            self.controller.schedule();self.wait(lambda:not self.controller.pending and not self.window.tag_controller.owned)
            self.assertEqual(self.database.read_bytes(),before)
        self.assertEqual(self.window.lib.playlists[0].keys,[track.track_id])

    def test_collection_completes_with_one_fresh_library_read_and_atomic_binding_ack(self):
        self.admit(('one',),native=False);request_id=self.controller.pending[0]['id']
        loader=mock.Mock(wraps=self.load);self.controller.loader=loader
        self.controller.schedule();self.wait(lambda:not self.controller.pending and not self.window.tag_controller.owned)
        loader.assert_called_once_with(self.database)
        self.assertEqual(self.window.settings[jobs.BINDINGS][0]['local_id'],'sb-one')
        acknowledged=[s for s in self.saves if s.get(jobs.BINDINGS)]
        self.assertTrue(acknowledged);self.assertEqual(acknowledged[0][jobs.KEY],[])
        prepared=[s[jobs.KEY][0]['request'] for s in self.saves if s.get(jobs.KEY) and s[jobs.KEY][0]['request']]
        self.assertTrue(prepared);self.assertEqual(prepared[0]['operation_id'],request_id)

    def test_collection_failed_completion_save_replays_native_receipt_after_restart(self):
        self.admit(('one',),native=False);failed=[False]
        def save(settings):
            if not failed[0] and settings.get(jobs.BINDINGS) and not settings.get(jobs.KEY):
                failed[0]=True;return False
            return True
        self.save_hook=save;self.controller.schedule()
        self.wait(lambda:bool(self.controller.pending and self.controller.pending[0]['error']) and not self.window.tag_controller.owned)
        request=deepcopy(self.controller.pending[0]['request'])
        self.assertEqual(self.controller.pending[0]['bindings'],[])
        self.assertEqual(len(self.load(self.database).tracks_by_id),2)
        self.controller.stop();self.controller=self.controller_for_window();self.window.shared_import_controller=self.controller
        self.controller.retry();self.wait(lambda:not self.controller.pending and not self.window.tag_controller.owned)
        self.assertEqual(len(self.load(self.database).tracks_by_id),2)
        self.assertEqual(self.window.settings[jobs.BINDINGS][0]['native_id'],request['tracks'][0]['native_id'])

    def test_collection_changed_native_identity_during_readback_keeps_intent_and_native_edit(self):
        self.admit(('one',),native=False)
        original=self.load;changed=[]
        def externally_rebound(source):
            conn=_connect(source)
            try:
                row=conn.execute("SELECT ID FROM djmdContent WHERE ID!='1'").fetchone()
                if row and not changed:
                    changed.append(row[0]);conn.execute("UPDATE djmdContent SET UUID='externally-rebound' WHERE ID=?",row)
            finally:conn.close()
            return original(source)
        self.controller.loader=externally_rebound;self.controller.schedule()
        self.wait(lambda:bool(self.controller.pending and self.controller.pending[0]['error']) and not self.window.tag_controller.owned)
        self.assertTrue(changed);self.assertIn('verified binding',self.controller.pending[0]['error'])
        self.assertEqual(self.controller.pending[0]['bindings'],[])
        self.assertFalse(self.window.settings.get(jobs.BINDINGS))
        self.assertEqual(original(self.database).tracks_by_id[changed[0]].source_uuid,'externally-rebound')

    def test_unavailable_prior_binding_blocks_only_that_import_before_any_source_sql(self):
        self.admit(('one',),native=False)
        self.window.lib.source_binding_issues={'sb-one':'Native song was removed'}
        before=self.database.read_bytes()
        with mock.patch.object(self.registration,'prepare') as prepare, \
                mock.patch.object(self.registration,'apply') as apply:
            self.controller.pump()
        prepare.assert_not_called();apply.assert_not_called()
        self.assertIn('native identity changed',self.controller.pending[0]['error'])
        self.assertEqual(self.database.read_bytes(),before)
        self.assertTrue(self.window.tag_controller.verified)

    def test_restart_after_source_commit_and_failed_local_save_reuses_one_native_row(self):
        self.admit(('one',));failed=False
        def fail_once(settings):
            nonlocal failed
            queue=settings.get(jobs.KEY,[])
            if not failed and queue and queue[0]['bindings']:
                failed=True;return False
            return True
        self.save_hook=fail_once;self.controller.schedule()
        self.wait(lambda:bool(self.controller.pending[0]['error']) and not self.window.tag_controller.owned)
        self.assertEqual(len(self.load(self.database).tracks_by_id),2)
        self.controller.stop();self.controller=self.controller_for_window();self.window.shared_import_controller=self.controller
        self.controller.retry();self.wait(lambda:not self.controller.pending and not self.window.tag_controller.owned)
        self.assertEqual(len(self.load(self.database).tracks_by_id),2)
        self.assertEqual(self.window.lib.playlists[0].keys,['sb-one'])

        # Cancellation after a committed INSERT and failed GUI save must retain
        # the native binding before dropping the unstarted membership intention.
        self.admit(('cancelled',));failed=False;self.controller.schedule()
        self.wait(lambda:bool(self.controller.pending[0]['error']) and not self.window.tag_controller.owned)
        from PySide6.QtWidgets import QMessageBox
        with mock.patch('desktop.shared_imports.QMessageBox.question',return_value=QMessageBox.Yes):
            self.controller.discard()
        self.wait(lambda:not self.controller.pending and not self.window.tag_controller.owned)
        self.assertEqual(len(self.load(self.database).tracks_by_id),3)
        self.assertEqual(self.window.lib.playlists[0].keys,['sb-one'])
        self.assertFalse(self.window.lib.tracks_by_id['sb-cancelled'].imported)
        self.assertIn('sb-cancelled',{b['local_id'] for b in self.window.settings[jobs.BINDINGS]})

    def test_source_lane_and_original_destination_are_not_retargeted(self):
        self.admit(('one',));self.window.tag_controller.busy=True
        with mock.patch.object(self.registration,'prepare') as prepare:self.controller.pump();prepare.assert_not_called()
        self.window.tag_controller.busy=False
        from soundbox.rekordbox_write import WriteProofError
        with mock.patch('desktop.shared_imports.assert_rekordbox_closed',
                side_effect=WriteProofError('Close rekordbox and its background agent before editing')) as closed:
            self.controller.pump();self.wait(lambda:self.controller.not_before>0 and not self.window.tag_controller.owned)
            self.controller.pump();self.controller.pump()
            self.assertEqual(closed.call_count,1)  # idle delivery cannot spin the worker
        self.controller.timer.stop();self.controller.not_before=0
        conn=_connect(self.database)
        try:conn.execute("UPDATE djmdPlaylist SET UUID='replaced' WHERE ID='1000'")
        finally:conn.close()
        self.controller.schedule();self.wait(lambda:bool(self.controller.pending[0]['error']) and not self.window.tag_controller.owned)
        self.assertIn('destination',self.controller.pending[0]['error'])
        self.assertEqual(self.load(self.database).playlists[0].keys,[])

        from PySide6.QtWidgets import QMessageBox
        with mock.patch('desktop.shared_imports.QMessageBox.question',return_value=QMessageBox.Yes):
            self.controller.discard()
        self.wait(lambda:not self.controller.pending and not self.window.tag_controller.owned)
        self.admit(('unstarted',),native=False)
        previous=deepcopy(self.controller.pending)
        with mock.patch('desktop.shared_imports.QMessageBox.question',return_value=QMessageBox.No):
            self.controller.discard()
        self.assertEqual(self.controller.pending,previous)
        def confirm_with_later_admission(*_args):
            self.assertTrue(self.window.tag_controller.admission_paused)
            self.assertFalse(self.window.tag_controller.run_playlist_job(lambda:None,lambda _worker:None))
            self.controller.pump()  # the modal event loop cannot publish this queue
            self.window.settings[jobs.KEY]=jobs.admit(self.controller.pending,self.window.lib,
                [self.window.lib.tracks_by_id['sb-unstarted']],dict(kind='collection'),dict(automatic=False))
            return QMessageBox.Yes
        with mock.patch('desktop.shared_imports.QMessageBox.question',side_effect=confirm_with_later_admission), \
                mock.patch.object(self.controller,'_registration',side_effect=AssertionError('Unstarted cancellation needs no source')):
            self.controller.discard()
        self.assertEqual(len(self.controller.pending),1)
        self.assertNotEqual(self.controller.pending[0]['id'],previous[0]['id'])
        self.assertFalse(self.window.tag_controller.admission_paused)
        self.assertIn('sb-unstarted',self.window.imported)

    def test_preparation_is_copy_only_and_renewed_retry_never_replaces_a_committed_song(self):
        tracks=self.admit(('one',));conn=_connect(self.database,readonly=True)
        try:before=_state(conn)
        finally:conn.close()
        request=self.registration.prepare([dict(id=t.track_id,path=str(t.path)) for t in tracks])
        conn=_connect(self.database,readonly=True)
        try:self.assertEqual(_state(conn),before)
        finally:conn.close()
        renewed=self.registration.prepare_retry(request);self.assertNotEqual(renewed['operation_id'],request['operation_id'])
        self.registration.apply(renewed)
        self.assertEqual(self.registration.prepare_retry(renewed),renewed)

    def test_preparation_refreshes_one_post_capture_metadata_change_with_the_same_audio_hash(self):
        import soundbox.rekordbox_registration_store as module
        track=self.admit(('one',),native=False)[0]
        database=self.database.read_bytes();capture=module.capture_media;captured=[]
        def metadata_after_capture(*args,**kwargs):
            value=capture(*args,**kwargs);captured.append(value)
            if len(captured)==1:track.path.chmod(track.path.stat().st_mode ^ 0o100)
            return value
        with mock.patch.object(module,'capture_media',side_effect=metadata_after_capture):
            request=self.registration.prepare([dict(id=track.track_id,path=str(track.path))])
        self.assertEqual(len(captured),2)
        self.assertEqual(captured[0].sha256,captured[1].sha256)
        self.assertNotEqual(captured[0].epoch,captured[1].epoch)
        self.assertEqual(request['tracks'][0]['audio']['sha256'],captured[0].sha256)
        self.assertEqual(self.database.read_bytes(),database)
        self.registration._media_current(request)

    def test_import_ctime_proof_keeps_exact_request_before_one_registration(self):
        track=self.admit(('one',),native=False)[0]
        request=self.registration.prepare([dict(id=track.track_id,path=str(track.path))])
        self.controller.pending[0]['request']=request
        track.path.chmod(track.path.stat().st_mode ^ 0o100)
        with mock.patch.object(self.registration,'prepare_retry',side_effect=AssertionError('Do not reprepare for ctime')):
            self.controller.schedule();self.wait(lambda:not self.controller.pending and not self.window.tag_controller.owned)
        self.assertEqual([p for p in self.recovery.glob('registration-*') if p.is_dir()],
                         [self.recovery/('registration-'+request['operation_id'])])
        self.assertEqual(len(self.load(self.database).tracks_by_id),2)
        self.assertEqual(len(self.window.settings[jobs.BINDINGS]),1)

    def test_metadata_refresh_rejects_changed_bytes_even_with_original_size_and_mtime(self):
        import os
        track=self.admit(('one',),native=False)[0]
        request=self.registration.prepare([dict(id=track.track_id,path=str(track.path))])
        self.controller.pending[0]['request']=request;before=self.database.read_bytes();info=track.path.stat()
        raw=bytearray(track.path.read_bytes());raw[-1]=1;track.path.write_bytes(raw)
        os.utime(track.path,ns=(info.st_atime_ns,info.st_mtime_ns))
        self.controller.schedule();self.wait(lambda:bool(self.controller.pending[0]['error']) and not self.window.tag_controller.owned)
        self.assertEqual(self.database.read_bytes(),before)
        self.assertEqual(self.window.settings.get(jobs.BINDINGS,[]),[])

    def test_recurring_ctime_is_verified_at_fresh_guards_without_a_permanent_error(self):
        track=self.admit(('one',),native=False)[0]
        request=self.registration.prepare([dict(id=track.track_id,path=str(track.path))])
        self.controller.pending[0]['request']=request;check=self.registration._media_current;observations=[]
        def changing_metadata(current):
            track.path.chmod(track.path.stat().st_mode ^ 0o100)
            observations.append(track.path.stat().st_ctime_ns)
            return check(current)
        with mock.patch.object(self.registration,'_media_current',side_effect=changing_metadata):
            self.controller.schedule();self.wait(lambda:not self.controller.pending and not self.window.tag_controller.owned)
        self.assertGreaterEqual(len(set(observations)),3)
        self.assertEqual(len(self.load(self.database).tracks_by_id),2)
        self.assertEqual(len(self.window.settings[jobs.BINDINGS]),1)

    def test_unstable_metadata_keeps_import_pending_without_error_or_plan_changes(self):
        from soundbox.native_media import METADATA_PENDING
        from soundbox.rekordbox_write import ConflictError
        track=self.admit(('metadata-pending',),native=False)[0]
        request=self.registration.prepare([dict(id=track.track_id,path=str(track.path))])
        self.controller.pending[0]['request']=request;before=deepcopy(self.controller.pending)
        with mock.patch.object(self.registration,'apply',side_effect=ConflictError(METADATA_PENDING)) as apply:
            self.controller.schedule()
            self.wait(lambda:apply.called and not self.window.tag_controller.owned)
        self.assertEqual(self.controller.pending,before)
        self.assertEqual(self.window.tag_controller.registration_update_status,'Verifying audio…')
        self.assertGreater(self.controller.not_before,0)

    def test_float_import_converts_before_registration_and_preserves_saved_grid_and_key(self):
        import numpy as np
        import soundfile as sf
        import hashlib
        from soundbox import analysis, wave_conversion
        tracks=self.admit(('floating',));track=tracks[0]
        sf.write(track.path,np.tile([[-.5,.5]],(44100*8,1)),44100,subtype='FLOAT')
        before=track.path.read_bytes();self.window.imported[track.track_id]['size']=len(before)
        record=dict(engine=analysis.ENGINE,path=str(track.path),sha256=hashlib.sha256(before).hexdigest(),
            epoch=analysis.identity(track.path.stat()),size=len(before),duration=8.,bpm=120.,key='1A',
            beats=[[i%4+1,i*500,120.] for i in range(16)],first_cue=None,source_context={})
        self.window.settings['analysis_results']={analysis.result_key(track.path):record}
        self.window._prepare_grid_cache=mock.Mock()
        pb=self.window.player_bar=SimpleNamespace(current_track=lambda:track.track_id,
            _analysis_parts={},refresh_audio_detail=mock.Mock())
        self.controller.schedule();self.wait(lambda:not self.controller.pending and not self.window.tag_controller.owned)
        rebound=self.window.settings['analysis_results'][analysis.result_key(track.path)]
        self.assertEqual(rebound['beats'],record['beats']);self.assertEqual(rebound['key'],'1A')
        self.assertEqual(sf.info(track.path).subtype,'PCM_24')
        self.assertEqual(self.window.lib.playlists[0].keys,[track.track_id])
        self.assertEqual(len(self.window.settings[wave_conversion.KEY]),1)
        originals=list((self.root/'state/wave-conversion-originals').rglob('original.wav'))
        self.assertEqual(len(originals),1);self.assertEqual(originals[0].read_bytes(),before)
        conn=_connect(self.database,readonly=True)
        try:row=next(r for r in _rows(conn,'djmdContent') if r['ID']!='1')
        finally:conn.close()
        self.assertEqual((row['FileType'],row['FileSize']),(11,track.path.stat().st_size))
        self.window._prepare_grid_cache.assert_called_with(track.track_id)
        pb.refresh_audio_detail.assert_called_once_with(str(track.path))
        self.assertEqual(pb._analysis_parts['_detected_sha256'],rebound['sha256'])
        self.assertFalse(self.controller.converting(track.track_id))

    def test_float_conversion_save_failure_replays_same_file_and_native_intention(self):
        import numpy as np
        import soundfile as sf
        from soundbox import wave_conversion
        track=self.admit(('floating',))[0]
        sf.write(track.path,np.zeros((44100*8,2)),44100,subtype='FLOAT')
        self.window.imported[track.track_id]['size']=track.path.stat().st_size
        originals=self.window.imported;opaque={'nested':['preserve alias']}
        self.window.settings.update(future=opaque,alias=opaque)
        before=deepcopy(self.controller.pending);failed=False
        def fail_once(settings):
            nonlocal failed
            if settings.get(wave_conversion.KEY) and not failed:failed=True;return False
            return True
        self.save_hook=fail_once;self.controller.schedule()
        self.wait(lambda:bool(self.controller.pending[0]['error']) and not self.window.tag_controller.owned)
        self.assertEqual(len(self.load(self.database).tracks_by_id),1)
        self.assertEqual(self.controller.pending[0]['id'],before[0]['id'])
        self.assertFalse(self.window.settings.get(wave_conversion.KEY));inode=track.path.stat().st_ino
        self.assertIs(self.window.imported,originals)
        self.assertIs(self.window.settings['future'],opaque)
        self.assertIs(self.window.settings['alias'],opaque)
        self.controller.stop();self.controller=self.controller_for_window();self.window.shared_import_controller=self.controller
        self.controller.retry();self.wait(lambda:not self.controller.pending and not self.window.tag_controller.owned)
        self.assertEqual(track.path.stat().st_ino,inode)
        self.assertEqual(len(self.load(self.database).tracks_by_id),2)
        self.assertEqual(self.window.lib.playlists[0].keys,[track.track_id])

    def test_float_overs_use_approved_same_gain_clipping_and_registered_float_is_never_converted(self):
        import numpy as np
        import soundfile as sf
        track=self.admit(('overs',))[0]
        sf.write(track.path,np.tile([[-1.33,1.2]],(44100*8,1)),44100,subtype='FLOAT')
        before=track.path.read_bytes();self.window.imported[track.track_id]['size']=len(before)
        self.window.settings[jobs.KEY][0]['error']='Only integer PCM WAVE is supported'
        self.controller.schedule()
        self.wait(lambda:not self.controller.pending and not self.window.tag_controller.owned)
        self.assertEqual(sf.info(track.path).subtype,'PCM_24')
        receipt=self.window.settings['wave_conversions'][0]
        self.assertEqual(receipt['gain'],1);self.assertGreater(receipt['clipped_samples'],0)
        self.assertEqual(next((self.root/'state/wave-conversion-originals').rglob('original.wav')).read_bytes(),before)
        # A pre-existing native float record has no conversion authority.
        track=self.admit(('registered-float',))[0]
        sf.write(track.path,np.tile([[-1.33,1.2]],(44100*8,1)),44100,subtype='FLOAT')
        before=track.path.read_bytes()
        conn=_connect(self.database)
        try:conn.execute('UPDATE djmdContent SET FolderPath=? WHERE ID=\'1\'',(str(track.path),))
        finally:conn.close()
        self.assertEqual(self.registration.convert_imports(self.controller.pending[0]['tracks'],self.root/'recovery-audio',
            self.controller.pending[0]['id'],reduce_gain=True),[])
        self.assertEqual(track.path.read_bytes(),before)

    def test_later_file_error_cannot_strand_an_already_converted_song_or_its_order(self):
        import numpy as np
        import soundfile as sf
        tracks=self.admit(('floating','broken'))
        sf.write(tracks[0].path,np.zeros((44100,2)),44100,subtype='FLOAT')
        sf.write(tracks[1].path,np.full((44100,2),np.nan),44100,subtype='FLOAT')
        for track in tracks:self.window.imported[track.track_id]['size']=track.path.stat().st_size
        self.controller.schedule()
        self.wait(lambda:bool(self.controller.pending[0]['error']) and not self.window.tag_controller.owned)
        self.assertIn('non-finite',self.controller.pending[0]['error'])
        self.assertEqual(sf.info(tracks[0].path).subtype,'PCM_24')
        self.assertEqual(self.window.imported[tracks[0].track_id]['size'],tracks[0].path.stat().st_size)
        self.assertEqual(len(self.window.settings['wave_conversions']),1)
        inode=tracks[0].path.stat().st_ino
        wave(tracks[1].path)
        self.controller.retry();self.wait(lambda:not self.controller.pending and not self.window.tag_controller.owned)
        self.assertEqual(tracks[0].path.stat().st_ino,inode)
        self.assertEqual(self.window.lib.playlists[0].keys,[t.track_id for t in tracks])


if __name__=='__main__':unittest.main()
