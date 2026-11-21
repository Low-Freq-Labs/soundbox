"""Held canonical workers and encrypted TEMP playlists exercise ordinary gestures."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest import mock

from desktop.rekordbox_playlists import NativePlaylistsMixin,QUEUE
from soundbox.library import Playlist,Track
from soundbox.rekordbox_playlist_store import request_for,batch_for,command_requests
from soundbox import rekordbox_playlist_store as stores
from soundbox.state_repository import read_state
from tests import test_source_intent_admission as admission
import test_source_playlists as sources


class PlaylistSparseState(unittest.TestCase):
    @classmethod
    def setUpClass(cls):admission.SourceIntentAdmission.setUpClass()

    def setUp(self):
        self.case=admission.SourceIntentAdmission();self.case.setUp();self.addCleanup(self.case.doCleanups)
        self.window=w=self.case.window;self.controller=self.case.playlist;self.tags=self.case.tags
        source=sources.SourcePlaylists();source.setUp();self.addCleanup(source.doCleanups)
        source.reorder_fixture();self.writer=source.writer
        self.writer.authority=replace(self.writer.authority,retain_full_backups=True)
        tracks={identity:Track(identity,identity,'Artist','file:///synthetic/'+identity+'.wav',source_uuid=uuid)
                for identity,uuid in (('track','uuid'),('track-two','uuid-two'))}
        w.lib=replace(w.lib,source=str(source.database),source_library_id=source.authority.library_id,
            tracks_by_id=tracks,source_playlist_catalog=self.writer.snapshot(),playlists=[Playlist('Existing',[],[],'1000'),
                Playlist('Node 2',[],[],'1002')])
        self.tags.lib=w.lib;self.controller.factory=lambda _:self.writer
        w.removals={};w._view_key='sb:Collection'
        w._native_view_playlist=lambda path:next((p for p in w.lib.playlists if p.display_path==path),None)
        w._apply_settings_to_lib=lambda **_:setattr(w.lib,'removed_by_playlist',deepcopy(w.removals))
        w._native_apply_catalog=lambda catalog,request:NativePlaylistsMixin._native_apply_catalog(w,catalog,request)
        w._native_refresh_view=mock.Mock()

    def request(self,operation,**kwargs):return request_for(self.window.lib.source_playlist_catalog,operation,**kwargs)

    def deliver(self,index=-1):
        _,work,completed=self.case.native[index]
        try:result,error=work(),''
        except Exception as exc:result,error=None,str(exc)
        self.tags.busy=False;completed(SimpleNamespace(error=error,result=result))
        self.tags.changed.emit();self.tags.idle.emit()

    def admit(self,request,**kwargs):
        count=len(self.case.native)
        self.assertTrue(self.controller.enqueue(request,**kwargs))
        self.case.wait(lambda:len(self.case.native)>count)

    def settle(self):self.case.wait(lambda:not self.controller.admission_pending)

    def test_create_rename_reorder_append_remove_feedback_and_ack_do_not_serialize_gui(self):
        tracks=list(self.window.lib.tracks_by_id.values())
        requests=[lambda:self.request('create',name='Fresh'),lambda:self.request('rename',identity='1000',name='Renamed'),
            lambda:self.request('reorder',identity='1002',anchor_id='1000'),
            lambda:self.request('append',identity='1000',tracks=tracks),
            lambda:self.request('remove',identity='1000',tracks=tracks[:1])]
        for make in requests:
            with self.subTest(operation=make):
                request=make();before=self.writer.database.read_bytes(),self.writer.xml.read_bytes()
                count=len(self.case.native)
                with self.case.held() as (entered,release):
                    self.assertTrue(self.controller.enqueue(request));self.assertTrue(entered.wait(2))
                    self.assertEqual(self.controller.pending[-1]['request'],request)
                    self.assertEqual(len(self.case.native),count)
                    self.assertEqual((self.writer.database.read_bytes(),self.writer.xml.read_bytes()),before)
                    self.window._native_pending_changed.assert_called();release.set()
                self.case.wait(lambda:len(self.case.native)>count)
                catalog=deepcopy(self.window.lib.source_playlist_catalog)
                with self.case.held() as (entered,release):
                    self.deliver();self.assertTrue(entered.wait(2))
                    self.assertEqual(self.window.lib.source_playlist_catalog,catalog)
                    self.assertTrue(self.tags.source_job_owned);release.set()
                self.settle();self.assertFalse(self.controller.pending)
                self.assertEqual(self.window.lib.source_playlist_catalog,self.writer.snapshot())
        self.window._save_state.assert_not_called()
        self.assertEqual(self.window._native_refresh_view.call_count,len(requests))

    def test_collection_retention_is_durable_before_delete_sql_and_ack_retry_never_repeats_sql(self):
        self.admit(self.request('append',identity='1000',tracks=list(self.window.lib.tracks_by_id.values())))
        self.deliver();self.settle()
        self.admit(self.request('delete',identity='1000'));count=len(self.case.native)
        before=self.writer.database.read_bytes(),self.writer.xml.read_bytes()
        with self.case.held() as (entered,release):
            self.deliver();self.assertTrue(entered.wait(2))
            self.assertEqual((self.writer.database.read_bytes(),self.writer.xml.read_bytes()),before)
            self.assertNotIn('collection_pins',read_state(self.window._state_repository.path)['settings'][QUEUE][0])
            release.set()
        self.case.wait(lambda:len(self.case.native)>count)
        saved=read_state(self.window._state_repository.path)['settings']
        self.assertEqual(saved[QUEUE][0]['collection_pins'],{'track':'uuid','track-two':'uuid-two'})
        self.assertEqual(saved['rekordbox_collection_tracks'][self.window.lib.source_library_id],saved[QUEUE][0]['collection_pins'])
        with mock.patch.object(self.window._state_repository,'commit_patch',side_effect=OSError('Disk full')):
            self.deliver();self.case.wait(lambda:bool(self.controller.ack.error))
        after=self.writer.database.read_bytes(),self.writer.xml.read_bytes()
        self.assertNotEqual(after,before)
        self.assertTrue(self.controller.retry());self.settle()
        self.assertEqual((self.writer.database.read_bytes(),self.writer.xml.read_bytes()),after)
        self.assertFalse(self.controller.pending);self.window._save_state.assert_not_called()

    def test_remove_ack_clears_only_captured_overlay_and_preserves_unrelated_curation(self):
        track=self.window.lib.tracks_by_id['track']
        self.admit(self.request('append',identity='1000',tracks=[track]));self.deliver();self.settle()
        self.window.removals={'1000':{'track':'captured','track-two':'other'},'foreign':{'track':'keep'}}
        self.window.lib.removed_by_playlist=deepcopy(self.window.removals)
        self.window._state_repository.save({'settings':deepcopy(self.window.settings),
            'playlist_removals':deepcopy(self.window.removals),'title_overrides':{'track':'Curated'},
            'artist_overrides':{},'artist_aliases':{}})
        self.admit(self.request('remove',identity='1000',tracks=[track]),selected_tracks=[track])
        self.deliver();self.settle()
        expected={'1000':{'track-two':'other'},'foreign':{'track':'keep'}}
        self.assertEqual(self.window.removals,expected);self.assertEqual(self.window.lib.removed_by_playlist,expected)
        saved=read_state(self.window._state_repository.path)
        self.assertEqual(saved['playlist_removals'],expected);self.assertEqual(saved['title_overrides'],{'track':'Curated'})
        self.window._save_state.assert_not_called()

    def test_staged_second_ordinary_request_survives_first_ack_with_exact_causal_identity(self):
        first=self.request('rename',identity='1000',name='First');self.admit(first)
        second=self.request('rename',identity='1000',name='Second')
        self.assertTrue(self.controller.enqueue(second));self.assertTrue(self.controller.admission.staged)
        with self.case.held() as (entered,release):
            self.deliver();self.assertTrue(entered.wait(2));self.assertEqual(len(self.case.native),1);release.set()
        self.case.wait(lambda:len(self.case.native)==2)
        retained=self.controller.pending[0]['request']
        self.assertEqual(retained['operation_id'],second['operation_id'])
        self.assertEqual(retained['playlist_uuid'],second['playlist_uuid'])
        self.assertEqual(retained['revision'],self.writer.snapshot()['revision'])
        self.deliver();self.settle();self.assertFalse(self.controller.pending)
        self.assertEqual(next(row['Name'] for row in self.writer.snapshot()['nodes'] if row['ID']=='1000'),'Second')

    def test_failed_paired_batch_admission_preserves_future_bindings_and_restart_durability(self):
        request=batch_for(self.window.lib.source_playlist_catalog,[self.request('create',name='Migrated')])
        command=command_requests(request)[0]
        mapping={'nodes':{'custom:Migrated':{'id':command['playlist_id'],'uuid':command['playlist_uuid']}}}
        before=self.writer.database.read_bytes(),self.writer.xml.read_bytes()
        with mock.patch.object(self.window._state_repository,'commit_patch',side_effect=OSError('Disk full')):
            self.assertTrue(self.controller.enqueue(request,paired_settings={'playlist_unification':mapping}))
            self.case.wait(lambda:bool(self.controller.admission.error))
        self.assertFalse(self.case.native);self.assertNotIn('playlist_unification',self.window.settings)
        self.assertNotIn('playlist_unification',read_state(self.window._state_repository.path)['settings'])
        self.assertEqual((self.writer.database.read_bytes(),self.writer.xml.read_bytes()),before)
        self.assertTrue(self.controller.retry());self.case.wait(lambda:bool(self.case.native))
        saved=read_state(self.window._state_repository.path)['settings']
        self.assertEqual(saved['playlist_unification'],mapping);self.assertEqual(saved[QUEUE][0]['request'],request)
        self.assertEqual(self.window.settings['playlist_unification'],mapping)
        self.deliver();self.settle();self.assertFalse(self.controller.pending)

    def test_smart_rename_mixed_reorder_and_delete_share_sparse_ack_without_whole_library_reload(self):
        import test_smart_playlist_metadata as fixtures
        fixture=fixtures.SmartMetadataCandidate();fixture.setUp();self.addCleanup(fixture.doCleanups)
        authority=replace(fixture.case.authority,smart_rename_accepted=True,smart_rename_profile=stores.SMART_RENAME_PROFILE,
            smart_reorder_accepted=True,smart_reorder_profile=stores.SMART_REORDER_PROFILE,
            smart_delete_accepted=True,smart_delete_profile=stores.SMART_DELETE_PROFILE,retain_full_backups=True)
        self.writer=stores.PlaylistStore(authority,fixture.case.root/'desktop-recovery')
        catalog=self.writer.snapshot();w=self.window
        w.lib=replace(w.lib,source=str(fixture.database),source_library_id=authority.library_id,
            source_playlist_catalog=catalog,playlists=[Playlist(row['Name'],['Proof folder'],[],row['ID'])
                for row in catalog['nodes'] if row['Attribute'] in (0,4)])
        self.tags.lib=w.lib
        self.controller.factory=None
        with mock.patch('desktop.rekordbox_playlists.load_authority',return_value=authority), \
                mock.patch('desktop.rekordbox_playlists.PlaylistStore',return_value=self.writer):
            for operation,kwargs in (('rename_smart',{'name':'Updated smart'}),
                    ('reorder_smart',{'anchor_id':'1000'}),('delete_smart',{})):
                with self.subTest(operation=operation):
                    request=self.request(operation,identity='2000',**kwargs)
                    self.admit(request);before=deepcopy(w.lib.source_playlist_catalog)
                    with self.case.held() as (entered,release):
                        self.deliver();self.assertTrue(entered.wait(2))
                        self.assertEqual(w.lib.source_playlist_catalog,before);release.set()
                    self.settle();self.assertFalse(self.controller.pending)
                    self.assertEqual(w.lib.source_playlist_catalog,self.writer.snapshot())
        self.tags.readback_library.assert_not_called();w._save_state.assert_not_called()

    def test_lost_admission_ack_keeps_mapping_and_queue_atomic_until_exact_retry(self):
        request=self.request('create',name='Uncertain migration')
        mapping={'nodes':{'custom:Uncertain migration':{'id':request['playlist_id'],'uuid':request['playlist_uuid']}}}
        commit=self.window._state_repository.commit_patch
        def lost(patch):
            commit(patch)
            raise OSError('Lost completion after durable publication')
        with mock.patch.object(self.window._state_repository,'commit_patch',side_effect=lost):
            self.assertTrue(self.controller.enqueue(request,paired_settings={'playlist_unification':mapping}))
            self.case.wait(lambda:bool(self.controller.admission.error))
        canonical=read_state(self.window._state_repository.path)['settings']
        self.assertEqual(canonical['playlist_unification'],mapping)
        self.assertEqual(canonical[QUEUE][0]['request'],request)
        self.assertNotIn('playlist_unification',self.window.settings)
        self.assertFalse(self.case.native)
        self.assertTrue(self.controller.retry());self.case.wait(lambda:bool(self.case.native))
        self.assertEqual(self.window.settings['playlist_unification'],mapping)
        self.assertEqual(len(read_state(self.window._state_repository.path)['settings'][QUEUE]),1)
        self.deliver();self.settle();self.assertFalse(self.controller.pending)
