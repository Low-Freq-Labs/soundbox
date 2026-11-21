"""Exact local overlay ACKs beside the accepted encrypted TEMP playlist writer."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from PySide6.QtWidgets import QApplication

from desktop.rekordbox_playlists import (NativePlaylistsMixin,PlaylistEditController,QUEUE,
    _capture_removal_overlay,_validate_removal_overlay,_ack_removal_overlay)
from desktop.state_commit import _checked_commit,apply_gui_patch
from soundbox.library import Library,Playlist,Track
from soundbox import rekordbox_playlist_store as store
from soundbox.state_repository import StateRepository,read_state
import test_source_playlists as source_fixtures
from test_tag_creation_controller import Window


class Host(Window,NativePlaylistsMixin):
    def _save_state(self):
        self.saved.append(deepcopy(dict(settings=self.settings,removals=self.removals)))
        if self.save_ok and hasattr(self,'_state_repository'):
            self._state_repository.save(dict(settings=self.settings,playlist_removals=self.removals))
        return self.save_ok

    def _commit_tag_state(self,replacements,removals,committed,failed,*,current,expected_queues=None):
        # Deliver synchronously in this controller unit fixture, but exercise
        # real selected-queue guards and both canonical publication roots.
        # Held worker/navigation cases live in test_playlist_sparse_state.
        if not current():failed(OSError('The source changed'));return True
        if not self.save_ok:failed(OSError('No space for playlist acknowledgement'));return True
        try:
            result=_checked_commit(self._state_repository,
                self._state_repository.prepare_patch(replacements,removals),expected_queues)
        except (OSError,ValueError) as exc:failed(exc);return True
        apply_gui_patch(self,result.patch)
        self.saved.append(deepcopy(dict(settings=self.settings,removals=self.removals)))
        committed();return True

    def _apply_settings_to_lib(self,**_kwargs):
        self.lib.removed_by_playlist={key:set(bag) for key,bag in self.removals.items()}


class PlaylistRemovalOverlay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        fixture=source_fixtures.SourcePlaylists('test_catalog_snapshot_reads_committed_wal_without_rewriting_source_or_rendering_backups')
        fixture.setUp();self.addCleanup(fixture.doCleanups)
        self.writer=store.PlaylistStore(replace(fixture.authority,retain_full_backups=True),fixture.recovery)
        root=Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.w=Host(root);self.addCleanup(self.w.deleteLater)
        self.track=Track('logical-one','One','Artist','file:///must-never-open/audio.wav',source_native_id='track',source_uuid='uuid')
        self.other=Track('track-two','Two','Artist','file:///must-never-open/other.wav',source_uuid='uuid-two')
        self.writer.apply(store.request_for(self.writer.snapshot(),'append',identity='1000',tracks=[self.track,self.other]))
        catalog=self.writer.snapshot()
        self.w.lib=Library({self.track.track_id:self.track,self.other.track_id:self.other},
            [Playlist('Existing',[],[self.track.track_id,self.other.track_id],'1000')],str(self.writer.database),
            source_kind='rekordbox-db',source_library_id=self.writer.authority.library_id,source_playlist_catalog=catalog)
        self.w.removals={'1000':{'logical-one':'captured-date','track-two':'other-date'},'unrelated':{'keep':'keep-date'}}
        self.w._state_repository=StateRepository(root/'library.json')
        self.w._state_repository.save(dict(settings=self.w.settings,playlist_removals=self.w.removals))
        self.w._apply_settings_to_lib();self.w._view_key=''
        self.w.tag_controller.playlist_update_status=''
        self.w.tag_controller.run_playlist_job=lambda work,done:self.w.tag_controller._run_source_job('playlists',work,done)
        self.w._native_pending_changed=mock.Mock();self.w._native_reconcile_source=mock.Mock();self.w._native_refresh_view=mock.Mock()
        self.c=PlaylistEditController(self.w,store_factory=lambda _:self.writer);self.w.playlist_controller=self.c
        self.c.schedule=mock.Mock();self.addCleanup(self.c.stop)
        self.enterContext(mock.patch('desktop.rekordbox_playlists.assert_rekordbox_closed'))

    def request(self,track=None):
        return store.request_for(self.w.lib.source_playlist_catalog,'remove',identity='1000',tracks=[track or self.track])

    def enqueue(self):
        request=self.request();self.assertTrue(self.c.enqueue(request,selected_tracks=[self.track]))
        return request

    def turn(self):
        before=len(self.w.tag_controller.admitted);self.c.pump()
        self.assertEqual(len(self.w.tag_controller.admitted),before+1)
        self.w.tag_controller.deliver();self.c.timer.stop()

    def test_exact_proof_is_durable_before_admission_native_request_is_unchanged_and_ack_clears_only_capture(self):
        original=deepcopy(self.w.removals);request=self.enqueue();saved=self.w.saved[-1]['settings'][QUEUE][0]
        self.assertFalse(self.w.tag_controller.admitted)
        self.assertEqual(saved['request'],self.c.pending[0]['request'])
        self.assertNotIn('removal_overlay',saved['request'])
        self.assertEqual(saved['removal_overlay']['entries'][0]['local_id'],'logical-one')
        self.assertEqual(saved['removal_overlay']['entries'][0]['value'],'captured-date')
        self.assertEqual(read_state(self.w._state_repository.path)['settings'][QUEUE][0],saved)
        self.turn();self.assertFalse(self.c.pending)
        self.assertEqual(self.w.removals,{'1000':{'track-two':'other-date'},'unrelated':{'keep':'keep-date'}})
        self.assertEqual(original['1000']['logical-one'],'captured-date')
        self.assertEqual(self.w.lib.removed_by_playlist,{'1000':{'track-two'},'unrelated':{'keep'}})
        self.assertEqual(self.w.saved[-1]['removals'],self.w.removals)
        self.assertFalse(any(row['ContentID']=='track' and row['rb_local_deleted']==0 for row in self.writer.snapshot()['members']))
        self.assertEqual(request['tracks'][0]['uuid'],'uuid')

    def test_newer_same_key_overlay_and_unrelated_keys_survive_ack(self):
        self.enqueue();self.w.removals={**self.w.removals,'1000':{**self.w.removals['1000'],'logical-one':'newer-date','later':'later-date'}}
        owner=self.w.removals;self.turn()
        self.assertIs(self.w.removals,owner);self.assertEqual(owner['1000']['logical-one'],'newer-date')
        self.assertEqual(self.w.lib.removed_by_playlist['1000'],set(owner['1000']))

    def test_failed_ack_restores_exact_map_and_library_mask_owners_then_restart_replays_same_request(self):
        self.enqueue();job=deepcopy(self.c.pending[0]);owners=self.w.removals,self.w.lib.removed_by_playlist
        self.w.save_ok=False;self.turn()
        self.assertIs(self.w.removals,owners[0]);self.assertIs(self.w.lib.removed_by_playlist,owners[1])
        self.assertEqual(self.c.pending[0]['request'],job['request']);self.assertTrue(self.c.ack.error)
        self.assertEqual(read_state(self.w._state_repository.path)['settings'][QUEUE][0],job)
        committed=self.writer.database.read_bytes(),self.writer.xml.read_bytes()
        self.w.settings=read_state(self.w._state_repository.path)['settings'];self.c.stop()
        self.c=PlaylistEditController(self.w,store_factory=lambda _:self.writer);self.w.playlist_controller=self.c
        self.addCleanup(self.c.stop);self.c.schedule=mock.Mock();self.w.save_ok=True
        self.c.retry();self.turn()
        self.assertEqual(self.c.pending[0]['request'],job['request'])
        self.assertEqual(self.c.pending[0]['removal_overlay'],job['removal_overlay'])
        self.turn();self.assertFalse(self.c.pending)
        self.assertNotIn('logical-one',self.w.removals['1000'])
        self.assertEqual((self.writer.database.read_bytes(),self.writer.xml.read_bytes()),committed)

    def test_legacy_job_without_proof_preserves_overlay_even_after_verified_remove(self):
        self.enqueue();self.c.pending[0].pop('removal_overlay');self.assertTrue(self.w._save_state())
        owner=self.w.removals
        self.assertFalse(self.c.queue_error());self.turn()
        self.assertIs(self.w.removals,owner);self.assertIn('logical-one',owner['1000'])

    def test_retry_renews_request_without_recapturing_a_newer_overlay(self):
        self.enqueue();captured=deepcopy(self.c.pending[0]);external=store.request_for(self.writer.snapshot(),'rename',identity='1000',name='External')
        self.writer.apply(external);self.turn();self.assertTrue(self.c.pending[0]['error'])
        self.w.removals={**self.w.removals,'1000':{**self.w.removals['1000'],'logical-one':'newer-date'}}
        self.c.retry();self.turn()
        self.assertNotEqual(self.c.pending[0]['request']['operation_id'],captured['request']['operation_id'])
        self.assertEqual(self.c.pending[0]['removal_overlay'],captured['removal_overlay'])
        self.turn();self.assertFalse(self.c.pending);self.assertEqual(self.w.removals['1000']['logical-one'],'newer-date')

    def test_batch_captures_each_remove_once_and_preserves_a_newer_sibling_overlay(self):
        catalog=self.w.lib.source_playlist_catalog
        request=store.batch_for(catalog,[self.request(self.track),self.request(self.other)])
        self.assertTrue(self.c.enqueue(request,selected_tracks=[self.track,self.other]))
        self.assertEqual(len(self.c.pending[0]['removal_overlay']['entries']),2)
        self.w.removals['1000']['track-two']='newer-date';self.turn()
        self.assertEqual(self.w.removals['1000'],{'track-two':'newer-date'})

    def test_invalid_or_retargeted_proof_is_attention_before_worker_and_native_effect(self):
        self.enqueue();before=self.writer.database.read_bytes(),self.writer.xml.read_bytes()
        self.c.pending[0]['removal_overlay']['entries'][0]['playlist_uuid']='different'
        self.assertTrue(self.c.queue_error());self.c.pump();self.assertFalse(self.w.tag_controller.admitted)
        self.assertEqual((self.writer.database.read_bytes(),self.writer.xml.read_bytes()),before)

    def test_selected_identity_replacement_or_changed_native_playlist_preserves_overlay(self):
        request=self.request();proof=_capture_removal_overlay(request,self.w.removals,self.w.lib,[self.track])
        owner=self.w.removals;uuid=request['playlist_uuid']
        self.assertIs(_ack_removal_overlay(owner,proof,{'1000':'replaced'},self.w.lib),owner)
        self.w.lib.tracks_by_id['logical-one']=replace(self.track,source_uuid='replaced')
        self.assertIs(_ack_removal_overlay(owner,proof,{'1000':uuid},self.w.lib),owner)

    def test_capture_and_ack_use_only_selected_track_keys_and_do_not_mutate_original_bags(self):
        class NoScan(dict):
            def values(self):raise AssertionError('Full library scan')
            def items(self):raise AssertionError('Full library scan')
            def __iter__(self):raise AssertionError('Full library scan')
        self.w.lib.tracks_by_id=NoScan(self.w.lib.tracks_by_id)
        request=self.request();before=deepcopy(self.w.removals)
        proof=_capture_removal_overlay(request,self.w.removals,self.w.lib,[self.track]);_validate_removal_overlay(proof,request)
        result=_ack_removal_overlay(self.w.removals,proof,{'1000':request['playlist_uuid']},self.w.lib)
        self.assertEqual(self.w.removals,before);self.assertNotIn('logical-one',result['1000'])

    def test_selected_logical_aliases_of_one_native_song_each_retire_only_their_captured_overlay(self):
        alias=replace(self.track,track_id='logical-alias')
        unselected=replace(self.track,track_id='unselected-alias')
        self.w.lib.tracks_by_id.update({alias.track_id:alias,unselected.track_id:unselected})
        self.w.removals['1000'].update({'logical-alias':'alias-date','unselected-alias':'keep-date'})
        request=store.request_for(self.w.lib.source_playlist_catalog,'remove',identity='1000',tracks=[self.track,alias])
        before=deepcopy(self.w.removals)
        proof=_capture_removal_overlay(request,self.w.removals,self.w.lib,[self.track,alias])
        _validate_removal_overlay(proof,request)
        self.assertEqual({row['local_id'] for row in proof['entries']},{'logical-one','logical-alias'})
        self.w.removals['1000']['logical-alias']='newer-alias-date'
        result=_ack_removal_overlay(self.w.removals,proof,{'1000':request['playlist_uuid']},self.w.lib)
        self.assertNotIn('logical-one',result['1000'])
        self.assertEqual(result['1000']['logical-alias'],'newer-alias-date')
        self.assertEqual(result['1000']['unselected-alias'],'keep-date')
        self.assertEqual(before['1000']['logical-alias'],'alias-date')

    def test_alias_removal_failed_ack_and_restart_replay_preserve_both_exact_captures_without_rewriting_native(self):
        alias=replace(self.track,track_id='logical-alias')
        self.w.lib.tracks_by_id[alias.track_id]=alias
        self.w.removals['1000'][alias.track_id]='alias-date';self.w._apply_settings_to_lib()
        self.assertTrue(self.w._save_state())
        self.writer.apply(store.request_for(self.writer.snapshot(),'append',identity='1000',
                                           tracks=[alias],allow_duplicate=True))
        self.w.lib.source_playlist_catalog=self.writer.snapshot();self.w.lib.playlists[0].keys.append(alias.track_id)
        request=store.request_for(self.w.lib.source_playlist_catalog,'remove',identity='1000',tracks=[self.track,alias])
        self.assertTrue(self.c.enqueue(request,selected_tracks=[self.track,alias]))
        job=deepcopy(self.c.pending[0]);owners=self.w.removals,self.w.lib.removed_by_playlist
        self.w.save_ok=False;self.turn()
        self.assertTrue(self.c.ack.error);self.assertIs(self.w.removals,owners[0])
        self.assertEqual(read_state(self.w._state_repository.path)['settings'][QUEUE][0],job)
        self.assertIs(self.w.lib.removed_by_playlist,owners[1])
        self.assertFalse(any(row['ContentID']=='track' and row['rb_local_deleted']==0 for row in self.writer.snapshot()['members']))
        committed=self.writer.database.read_bytes(),self.writer.xml.read_bytes()
        self.w.settings=read_state(self.w._state_repository.path)['settings'];self.c.stop()
        self.c=PlaylistEditController(self.w,store_factory=lambda _:self.writer);self.w.playlist_controller=self.c
        self.addCleanup(self.c.stop);self.c.schedule=mock.Mock();self.w.save_ok=True
        self.c.retry();self.turn();self.assertEqual(self.c.pending[0]['removal_overlay'],job['removal_overlay'])
        self.turn();self.assertFalse(self.c.pending)
        self.assertEqual(self.w.removals['1000'],{'track-two':'other-date'})
        self.assertEqual(self.w.lib.removed_by_playlist['1000'],{'track-two'})
        self.assertEqual((self.writer.database.read_bytes(),self.writer.xml.read_bytes()),committed)
