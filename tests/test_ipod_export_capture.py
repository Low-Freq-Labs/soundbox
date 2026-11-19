"""Current durable iPod exports while bulk native metadata work remains queued."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock
from uuid import uuid4

from PySide6.QtCore import QObject
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from desktop.export_wait import cancel_source_syncs,defer_source_sync
from desktop.ipod_export_capture import ChooseIpodPlaylists,effective_rows,request_ipod_export
from desktop.metadata_controller import MetadataController,PAUSE_KEY
from desktop.prep_controller import PrepEditController
from desktop.state_commit import StateCommitter
from desktop.tag_controller import TagEditController
from soundbox import analysis,engine,media_conversion_jobs,prep_edits,source_truth
from soundbox.library import Library,Playlist,Track
from soundbox.state_repository import StateRepository,read_state


class IpodExportCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.area=self.enterContext(tempfile.TemporaryDirectory());self.root=Path(self.area).resolve()
        self.path=self.root/'one.wav';self.path.write_bytes(b'generated path identity only; no planner reads')
        self.other=self.root/'other.wav';self.other.write_bytes(b'unrelated')
        self.w=QObject();w=self.w
        w.lib=Library({'one':Track('one','Artist - Song','',self.path.as_uri(),source_native_id='1',source_uuid='uuid-one'),
            'other':Track('other','Other Artist - Other Song','',self.other.as_uri(),source_native_id='2',source_uuid='uuid-other')},
            [Playlist('Favorites',[],['one'],'42')],str(self.root/'native/master.db'),source_kind='rekordbox-db',
            source_library_id='123',source_playlist_catalog={'nodes':[dict(ID='42',UUID='playlist-uuid',rb_local_deleted=0)]})
        w.settings={};w.titles={};w.overrides={};w.aliases={};w.imported={};w.custom={}
        w.hidden=set();w.hidden_dates={};w.removals={};w.playlist_additions={}
        descriptor,targets=source_truth.build(source_truth.capture(w.lib,titles={},artists={},aliases={},settings={}))
        w.settings.update({source_truth.MIGRATION:descriptor,source_truth.TARGETS:targets})
        w._state_repository=StateRepository(self.root/'state/library.json');w._state_repository.load()
        w._shutting_down=False;w._state_maintenance=False;w._state_publication=False
        w._source_sync_waiting=None;w._export_batch=None;w._device_mount_tokens={'/generated/ipod':9}
        w._device_operation_owned=lambda:False;w._stock_view_cache={};w._stock_catalog_witness_cache={}
        w._start_pulse=mock.Mock();w._stop_pulse=mock.Mock();w.cancel_btn=mock.Mock();w._flash_status=mock.Mock()
        w._export_playlists=lambda **options:list(w.lib.playlists)
        w._sync_playlists=lambda:[]
        w.art_cache=SimpleNamespace(selection_token=lambda ids:tuple(ids),selection_current=lambda token:True,
            split_selection=lambda token:{tid:(tid,) for tid in token})
        w.tag_controller=TagEditController(self.root/'state',w,store_factory=lambda _:None)
        w._state_committer=StateCommitter(w)
        self.metadata=SimpleNamespace(window=w,queue_error=lambda:'',_migration_error=lambda:'',pending=[],_truth_index=None)
        def row(track):
            value=engine.track_row(track,w.overrides,w.titles)
            value.update(w.settings.get('track_info',{}).get(track.track_id,{}))
            value.update(MetadataController.display_fields(self.metadata,track));return value
        w._row_for=row;self.ready=mock.Mock();self.failed=mock.Mock()
        self.uuid='generated-volume'
        self.enterContext(mock.patch('desktop.ipod_export_capture.device_facts',
            side_effect=lambda *a,**kw:SimpleNamespace(volume_uuid=self.uuid)))
        self.addCleanup(self.close);self.save()

    def save(self):
        self.w._state_repository.save(dict(settings=self.w.settings,title_overrides=self.w.titles,
            artist_overrides=self.w.overrides,artist_aliases=self.w.aliases,imported=self.w.imported,
            custom=self.w.custom,hidden_tracks={tid:self.w.hidden_dates.get(tid,'') for tid in self.w.hidden},
            playlist_removals=self.w.removals,playlist_additions=self.w.playlist_additions))

    def close(self):
        cancel_source_syncs(self.w)
        self.w._state_committer.owner.drain();self.w.tag_controller.owner.drain();self.w.deleteLater()
        self.app.processEvents()

    def until(self,condition):
        end=time.monotonic()+4
        while not condition() and time.monotonic()<end:self.app.processEvents();QTest.qWait(1)
        self.assertTrue(condition(),'Export capture did not settle')

    def request(self,kind='stock',selected='default'):
        return request_ipod_export(self.w,kind,'/generated/ipod',
            [self.w.lib.playlists[0]] if selected=='default' else selected,ready=self.ready,failed=self.failed)

    def test_paused_and_running_bulk_backlog_both_export_before_it_drains(self):
        for paused in (True,False):
            with self.subTest(paused=paused):
                self.ready.reset_mock();self.failed.reset_mock()
                if paused:self.w.settings[PAUSE_KEY]=[source_truth.scope(self.w.lib)]
                else:self.w.settings.pop(PAUSE_KEY,None)
                self.save();before=self.w._state_repository.path.read_bytes()
                self.w.tag_controller.busy=True
                with mock.patch('desktop.cdj._source_edits_pending',side_effect=AssertionError('Global backlog barrier')):
                    self.request();self.app.processEvents();self.ready.assert_not_called()
                    self.w.tag_controller.busy=False;self.w.tag_controller.idle.emit()
                    self.until(lambda:self.ready.called or self.failed.called)
                self.failed.assert_not_called()
                capture=self.ready.call_args.args[0];inputs=capture.materialize()
                self.assertEqual((inputs.display_rows['one']['title'],inputs.display_rows['one']['artist']),('Song','Artist'))
                self.assertEqual(capture.track_total,1);self.assertEqual(capture.selected_playlists[0].keys,['one'])
                self.assertEqual(self.w.settings[source_truth.MIGRATION]['cursor'],0)
                self.assertEqual(len(self.w.settings[source_truth.MIGRATION]['order']),2)
                self.assertEqual(self.w._state_repository.path.read_bytes(),before)
                self.assertTrue(capture.require_current(self.w,'/generated/ipod'))

    def legacy_wait(self):
        w=self.w
        w.metadata_controller=SimpleNamespace(pending=[{'error':''}],queue_error=lambda:'',migration_pending=False)
        w._begin_dual_ipod_sync=mock.Mock()
        callbacks=[]
        self.enterContext(mock.patch('desktop.export_wait.device_facts',return_value=SimpleNamespace(volume_uuid=self.uuid)))
        self.enterContext(mock.patch('desktop.export_wait.QTimer',
            singleShot=lambda _ms,callback:callbacks.append(callback)))
        return callbacks

    def test_new_capture_then_legacy_wait_each_has_its_own_completion_poll(self):
        callbacks=self.legacy_wait();self.request()
        self.assertTrue(defer_source_sync(self.w,'dual','/generated/ipod',[self.w.lib.playlists[0]],reconcile=True))
        self.until(lambda:self.ready.called or self.failed.called);self.failed.assert_not_called()
        self.assertEqual(len(self.w._source_sync_waiting),1)
        callback=callbacks.pop(0);callback();self.w._begin_dual_ipod_sync.assert_not_called()
        self.w.metadata_controller.pending=[];callbacks.pop(0)()
        self.w._begin_dual_ipod_sync.assert_called_once()
        self.assertTrue(self.w._begin_dual_ipod_sync.call_args.kwargs['_source_ready'])
        self.assertIsNone(self.w._source_sync_waiting)
        callback();self.w._begin_dual_ipod_sync.assert_called_once()

    def test_legacy_head_then_new_capture_does_not_consume_the_new_request_schema(self):
        callbacks=self.legacy_wait()
        self.assertTrue(defer_source_sync(self.w,'dual','/generated/ipod',[self.w.lib.playlists[0]],reconcile=True))
        self.request();self.w.metadata_controller.pending=[];callbacks.pop(0)()
        self.w._begin_dual_ipod_sync.assert_called_once()
        self.until(lambda:self.ready.called or self.failed.called);self.failed.assert_not_called()
        self.assertIsNone(self.w._source_sync_waiting)

    def test_legacy_error_retires_only_itself_and_preserves_new_capture(self):
        callbacks=self.legacy_wait();self.w._background_notice=mock.Mock()
        defer_source_sync(self.w,'dual','/generated/ipod',[self.w.lib.playlists[0]],reconcile=True)
        self.request();self.w.metadata_controller.pending[0]['error']='Unrelated native save refused'
        callbacks.pop(0)()
        self.w._begin_dual_ipod_sync.assert_not_called();self.w._background_notice.assert_called_once()
        self.w._stop_pulse.assert_not_called()
        self.until(lambda:self.ready.called or self.failed.called);self.failed.assert_not_called()
        self.assertIsNone(self.w._source_sync_waiting)

    def test_paused_legacy_rejection_cannot_clear_an_existing_new_capture(self):
        callbacks=self.legacy_wait();self.w.metadata_controller.background_paused=True
        self.request()
        self.assertTrue(defer_source_sync(self.w,'dual','/generated/ipod',[self.w.lib.playlists[0]],reconcile=True))
        self.assertEqual(callbacks,[]);self.assertEqual(len(self.w._source_sync_waiting),1)
        self.until(lambda:self.ready.called or self.failed.called);self.failed.assert_not_called()
        self.w._begin_dual_ipod_sync.assert_not_called();self.w._stop_pulse.assert_not_called()

    def test_later_saved_metadata_edit_does_not_mutate_accepted_generation(self):
        self.request();self.until(lambda:self.ready.called or self.failed.called);self.failed.assert_not_called()
        capture=self.ready.call_args.args[0];before=capture.snapshot.payload
        self.w.titles['one']='Later title';self.save()
        self.assertTrue(capture.require_current(self.w,'/generated/ipod'))
        self.assertEqual(capture.snapshot.payload,before)
        self.assertEqual(capture.materialize().display_rows['one']['title'],'Song')
        self.assertEqual(read_state(self.w._state_repository.path)['title_overrides']['one'],'Later title')

    def test_stock_and_dual_capture_accept_unrelated_nonempty_exclusion_sets(self):
        self.w.lib.removed_by_playlist={'4016338146': {'other'}}
        self.w.removals={'4016338146': {'other': '2026-11-18T14:00:00'}}
        self.save()
        self.w._stock_view_cache['/generated/ipod']=(SimpleNamespace(identity_key='device'), None)
        before=self.w._state_repository.path.read_bytes()
        for kind in ('stock','dual'):
            with self.subTest(kind=kind):
                self.ready.reset_mock();self.failed.reset_mock()
                self.request(kind);self.until(lambda:self.ready.called or self.failed.called)
                self.failed.assert_not_called();capture=self.ready.call_args.args[0]
                inputs=capture.materialize()
                self.assertEqual(inputs.library.removed_by_playlist, {'4016338146': {'other'}})
                self.assertEqual([track.track_id for track in inputs.library.resolve(inputs.playlists[0])], ['one'])
                inputs.library.removed_by_playlist['4016338146'].clear()
                self.assertEqual(self.w.lib.removed_by_playlist, {'4016338146': {'other'}})
                self.assertEqual(capture.materialize().library.removed_by_playlist, {'4016338146': {'other'}})
        self.assertEqual(self.w._state_repository.path.read_bytes(),before)

    def test_shared_source_aliases_verify_one_analysis_record_without_duplicate_read_paths(self):
        self.w.lib.tracks_by_id['alias']=replace(self.w.lib.tracks_by_id['one'],track_id='alias',
            source_native_id='3',source_uuid='uuid-alias')
        self.w.lib.playlists[0].keys=['one','alias','one']
        for saved in (False,True):
            with self.subTest(saved_analysis=saved):
                self.ready.reset_mock();self.failed.reset_mock()
                if saved:self.w.settings['analysis_results']={analysis.result_key(self.path):
                    dict(path=str(self.path),size=self.path.stat().st_size,sha256='a'*64,
                        epoch=analysis.identity(self.path.stat()),engine=analysis.ENGINE,bpm=140.0,key='9A',duration=20.0)}
                self.save();self.request();self.until(lambda:self.ready.called or self.failed.called)
                self.failed.assert_not_called()
                capture=self.ready.call_args.args[0]
                self.assertEqual(capture.selected_playlists[0].keys,['one','alias','one'])
                self.assertEqual(set(capture.materialize().display_rows),{'one','alias'})

    def test_unsaved_visible_override_is_refused_without_saving_it_as_a_side_effect(self):
        self.w.titles['one']='Unsaved';before=self.w._state_repository.path.read_bytes()
        self.request();self.until(lambda:self.ready.called or self.failed.called)
        self.ready.assert_not_called();self.failed.assert_called_once()
        self.assertEqual(self.w._state_repository.path.read_bytes(),before)

    def media_job(self,path=None,ids=None,error=''):
        return dict(id=str(uuid4()),source=str(self.w.lib.source),library_id='123',
            path=str(Path(path or self.path).resolve()),ids=ids or ['one'],native=[dict(id='1',uuid='uuid-one')],error=error)

    def test_unrelated_failed_metadata_conversion_registration_and_structural_jobs_do_not_block(self):
        self.w.settings['pending_metadata_edits']=[dict(local_id='other',error='failed unrelated native metadata')]
        self.w.settings[media_conversion_jobs.KEY]=[{**self.media_job(self.other,['other'],'unrelated conversion'),
            'native':[dict(id='2',uuid='uuid-other')]}]
        self.w.settings['shared_import_jobs']=[dict(source=str(self.w.lib.source),library_id='123',
            tracks=[dict(id='different',path=str(self.other))],destination=dict(kind='native',id='99'),error='unrelated import')]
        self.w.settings['rekordbox_playlist_edits']=[dict(source=str(self.w.lib.source),library_id='123',
            request=dict(operation='rename',playlist_id='99'),error='unrelated playlist')]
        self.save();self.request();self.until(lambda:self.ready.called or self.failed.called)
        self.failed.assert_not_called();self.ready.assert_called_once()

    def test_selected_conversion_waits_then_uses_exact_post_ack_state(self):
        self.w.media_controller=SimpleNamespace(active={'one'})
        self.w.settings[media_conversion_jobs.KEY]=[self.media_job()];self.save()
        self.request();QTest.qWait(20);self.ready.assert_not_called();self.failed.assert_not_called()
        self.assertFalse(self.w._state_committer.pending)
        self.w.media_controller.active.clear()
        self.w.settings[media_conversion_jobs.KEY]=[];self.save()
        self.until(lambda:self.ready.called or self.failed.called);self.failed.assert_not_called()

    def test_selected_alias_conversion_is_found_by_worker_canonical_path_without_local_id_overlap(self):
        link=self.root/'alias.wav';link.symlink_to(self.path)
        alias=replace(self.w.lib.tracks_by_id['one'],track_id='alias',location_uri=link.as_uri(),
            source_native_id='3',source_uuid='uuid-alias')
        self.w.lib.tracks_by_id['alias']=alias;self.w.lib.playlists[0].keys=['alias']
        job=self.media_job();self.w.settings[media_conversion_jobs.KEY]=[job]
        folder=self.w.tag_controller.data_root/'media-conversion-originals'/job['id']
        folder.mkdir(parents=True);self.save()
        self.request()
        self.until(lambda:any('conversion' in str(call) for call in self.w._start_pulse.call_args_list))
        self.ready.assert_not_called();self.failed.assert_not_called()
        self.until(lambda:not self.w._state_committer.pending)
        folder.rmdir();self.w.settings[media_conversion_jobs.KEY]=[];self.save()
        self.until(lambda:self.ready.called or self.failed.called);self.failed.assert_not_called()

    def test_failed_selected_conversion_reports_attention_instead_of_hiding_it_as_unrelated_work(self):
        job=self.media_job(error='Locator save needs attention')
        self.w.settings[media_conversion_jobs.KEY]=[job]
        folder=self.w.tag_controller.data_root/'media-conversion-originals'/job['id']
        folder.mkdir(parents=True);(folder/'receipt.json').write_text('retained unfinished locator receipt')
        self.save()
        self.request();self.until(lambda:self.failed.called)
        self.ready.assert_not_called();self.assertIn('needs attention',str(self.failed.call_args.args[0]))
        self.assertIn(self.path.name,str(self.failed.call_args.args[0]))

    def test_reused_numeric_ids_with_different_native_uuid_and_path_are_unrelated(self):
        self.w.settings[media_conversion_jobs.KEY]=[{**self.media_job(self.other,['one'],'old source failure'),
            'native':[dict(id='1',uuid='different-original-uuid')]}]
        self.w.settings['rekordbox_playlist_edits']=[dict(source=str(self.w.lib.source),library_id='123',
            request=dict(operation='rename',playlist_id='42',playlist_uuid='different-playlist-uuid'),error='old row')]
        self.save();self.request();self.until(lambda:self.ready.called or self.failed.called)
        self.failed.assert_not_called();self.ready.assert_called_once()

    def test_dormant_cdj_policy_jobs_do_not_decide_any_ipod_export_eligibility(self):
        _target,rockbox=self.rockbox_target(None)
        self.w._stock_view_cache['/generated/ipod']=(SimpleNamespace(identity_key='device'),None)
        for kind in ('stock','dual','rockbox'):
            for error in ('','This compressed format needs a verified timing-preserving conversion; original retained',
                          'Another CDJ-only compatibility refusal'):
                with self.subTest(kind=kind,error=error):
                    self.ready.reset_mock();self.failed.reset_mock()
                    self.w.settings[media_conversion_jobs.KEY]=[self.media_job(error=error)];self.save()
                    before=self.w._state_repository.path.read_bytes();audio=self.path.read_bytes()
                    dev=rockbox if kind=='rockbox' else '/generated/ipod'
                    with mock.patch('soundbox.cdj_compatibility.inspect',side_effect=AssertionError('CDJ policy leaked')):
                        request_ipod_export(self.w,kind,dev,[self.w.lib.playlists[0]],ready=self.ready,failed=self.failed)
                        self.until(lambda:self.ready.called or self.failed.called)
                        self.failed.assert_not_called();self.ready.assert_called_once()
                        capture=self.ready.call_args.args[0]
                        self.assertTrue(capture.require_current(self.w,dev));capture.verify_worker()
                    self.assertEqual(self.w._state_repository.path.read_bytes(),before)
                    self.assertEqual(self.path.read_bytes(),audio)
                    self.assertEqual(self.w.settings[media_conversion_jobs.KEY][0]['error'],error)

    def test_physical_transition_appearing_after_checkpoint_is_refused_on_planner_worker(self):
        job=self.media_job(error='CDJ-only format refusal')
        self.w.settings[media_conversion_jobs.KEY]=[job];self.save()
        self.request();self.until(lambda:self.ready.called or self.failed.called);self.failed.assert_not_called()
        capture=self.ready.call_args.args[0]
        folder=self.w.tag_controller.data_root/'media-conversion-originals'/job['id']
        folder.mkdir(parents=True);(folder/'receipt.json').write_text('unacknowledged source replacement')
        with self.assertRaisesRegex(RuntimeError,'one.wav.*CDJ-only format refusal'):capture.verify_worker()
        self.assertTrue(self.path.is_file());self.assertTrue((folder/'receipt.json').is_file())

    def test_xml_source_without_native_conversion_domain_keeps_ipod_export(self):
        self.w.lib=replace(self.w.lib,source=str(self.root/'collection.xml'),
                           source_kind='rekordbox-xml',source_library_id='')
        self.save();before=self.w._state_repository.path.read_bytes()
        with mock.patch('soundbox.media_transition.capture_media_transitions',
                        side_effect=AssertionError('Native conversion domain borrowed by XML')):
            self.request();self.until(lambda:self.ready.called or self.failed.called)
            self.failed.assert_not_called();self.ready.assert_called_once()
            capture=self.ready.call_args.args[0]
            capture.verify_worker();self.assertTrue(capture.require_current(self.w,'/generated/ipod'))
        self.assertEqual(self.w._state_repository.path.read_bytes(),before)

    def test_device_only_order_checkpoint_does_not_borrow_cdj_global_save_barrier(self):
        baseline={'playlists':{'42':dict(name='Favorites',folder_path=[],tracks=['one'],pid='42')},'playlist_order':['42']}
        self.w._stock_view_cache['/generated/ipod']=(SimpleNamespace(identity_key='device'),SimpleNamespace(manifest=baseline))
        self.w.settings['pending_metadata_edits']=[dict(local_id='other',error='Unrelated metadata needs attention')]
        self.save();before=self.w._state_repository.path.read_bytes()
        with mock.patch('desktop.cdj._source_edits_pending',side_effect=AssertionError('CDJ global barrier')), \
             mock.patch('desktop.cdj._source_edits_error',side_effect=AssertionError('CDJ global error')):
            request_ipod_export(self.w,'dual','/generated/ipod',[self.w.lib.playlists[0]],
                options=dict(action='order',expected_manifest=baseline),ready=self.ready,failed=self.failed)
            self.until(lambda:self.ready.called or self.failed.called)
        self.failed.assert_not_called();capture=self.ready.call_args.args[0]
        self.assertEqual([p.pid for p in capture.selected_playlists],['42'])
        self.assertTrue(capture.require_current(self.w,'/generated/ipod'))
        self.assertEqual(self.w._state_repository.path.read_bytes(),before)

    def test_delete_last_device_playlist_checkpoint_ignores_removed_song_conversion(self):
        baseline={'playlists':{'42':dict(name='Favorites',folder_path=[],tracks=['one'],pid='42')},'playlist_order':['42']}
        self.w._stock_view_cache['/generated/ipod']=(SimpleNamespace(identity_key='device'),SimpleNamespace(manifest=baseline))
        job=self.media_job(error='Unrelated to deleting the device playlist')
        self.w.settings[media_conversion_jobs.KEY]=[job]
        folder=self.w.tag_controller.data_root/'media-conversion-originals'/job['id'];folder.mkdir(parents=True)
        self.save();before=self.w._state_repository.path.read_bytes()
        request_ipod_export(self.w,'dual','/generated/ipod',[],options=dict(action='delete',expected_manifest=baseline),
            ready=self.ready,failed=self.failed)
        self.until(lambda:self.ready.called or self.failed.called);self.failed.assert_not_called()
        capture=self.ready.call_args.args[0]
        self.assertEqual(capture.selected_playlists,());self.assertEqual(capture.track_total,0)
        self.assertEqual(capture.materialize().display_rows,{})
        self.assertTrue(folder.is_dir());self.assertEqual(self.w._state_repository.path.read_bytes(),before)

    def test_device_delete_preserves_exact_remaining_catalog_without_inventing_source_playlist(self):
        remaining=Playlist('Retained device playlist',[],['other'],'43')
        baseline={'playlists':{'43':dict(name=remaining.name,folder_path=[],tracks=['other'],pid='43')},'playlist_order':['43']}
        self.w._stock_view_cache['/generated/ipod']=(SimpleNamespace(identity_key='device'),SimpleNamespace(manifest=baseline))
        self.save()
        request_ipod_export(self.w,'dual','/generated/ipod',[remaining],options=dict(action='delete',expected_manifest=baseline),
            ready=self.ready,failed=self.failed)
        self.until(lambda:self.ready.called or self.failed.called);self.failed.assert_not_called()
        capture=self.ready.call_args.args[0]
        self.assertEqual(capture.selected_playlists[0].keys,['other'])
        self.assertEqual(set(capture.materialize().display_rows),{'other'})
        self.assertEqual([p.pid for p in self.w.lib.playlists],['42'])
        self.ready.reset_mock();self.failed.reset_mock()
        wrong=replace(remaining,keys=['one'])
        with self.assertRaisesRegex(RuntimeError,'confirmed catalog'):
            request_ipod_export(self.w,'dual','/generated/ipod',[wrong],options=dict(action='delete',expected_manifest=baseline),
                ready=self.ready,failed=self.failed)

    def test_delete_never_drops_unavailable_members_of_a_retained_device_playlist(self):
        remaining=Playlist('Retained',[],['missing-source-member'],'43')
        baseline={'playlists':{'43':dict(name='Retained',folder_path=[],tracks=['missing-source-member'],pid='43')},
                  'playlist_order':['43']}
        self.w._stock_view_cache['/generated/ipod']=(SimpleNamespace(identity_key='device'),SimpleNamespace(manifest=baseline))
        self.save();before=self.w._state_repository.path.read_bytes()
        with self.assertRaisesRegex(RuntimeError,'device contents were preserved'):
            request_ipod_export(self.w,'dual','/generated/ipod',[remaining],options=dict(action='delete',expected_manifest=baseline),
                ready=self.ready,failed=self.failed)
        self.ready.assert_not_called();self.failed.assert_not_called()
        self.assertIsNone(self.w._source_sync_waiting)
        self.assertFalse(self.w._state_committer.pending)
        self.assertEqual(baseline['playlists']['43']['tracks'],['missing-source-member'])
        self.assertEqual(self.w._state_repository.path.read_bytes(),before)

    def test_retained_member_disappearing_while_checkpoint_waits_refuses_device_delete(self):
        remaining=Playlist('Retained',[],['other'],'43')
        baseline={'playlists':{'43':dict(name='Retained',folder_path=[],tracks=['other'],pid='43')},'playlist_order':['43']}
        self.w._stock_view_cache['/generated/ipod']=(SimpleNamespace(identity_key='device'),SimpleNamespace(manifest=baseline))
        self.save();before=self.w._state_repository.path.read_bytes();self.w.tag_controller.busy=True
        request_ipod_export(self.w,'dual','/generated/ipod',[remaining],options=dict(action='delete',expected_manifest=baseline),
            ready=self.ready,failed=self.failed)
        self.app.processEvents();self.ready.assert_not_called();self.failed.assert_not_called()
        self.w.lib.tracks_by_id.pop('other')
        self.w.tag_controller.busy=False;self.w.tag_controller.idle.emit()
        self.until(lambda:self.ready.called or self.failed.called)
        self.ready.assert_not_called();self.failed.assert_called_once()
        self.assertIn('device contents were preserved',str(self.failed.call_args.args[0]))
        self.assertEqual(baseline['playlists']['43']['tracks'],['other'])
        self.assertEqual(self.w._state_repository.path.read_bytes(),before)

    def test_selected_registration_and_playlist_changes_wait_for_their_ack(self):
        for key,row in (
            ('shared_import_jobs',dict(source=str(self.w.lib.source),library_id='123',tracks=[],
                destination=dict(kind='native',id='42'),error='')),
            ('rekordbox_playlist_edits',dict(source=str(self.w.lib.source),library_id='123',
                request=dict(operation='rename',playlist_id='42'),error=''))):
            with self.subTest(key=key):
                self.ready.reset_mock();self.failed.reset_mock();self.w.settings[key]=[row];self.save()
                self.request();QTest.qWait(20);self.ready.assert_not_called()
                self.w.settings[key]=[];self.save();self.until(lambda:self.ready.called or self.failed.called)
                self.failed.assert_not_called()

    def test_selected_local_grid_admission_waits_but_cue_only_admission_does_not(self):
        lane=SimpleNamespace(queue=[dict(kind='admit',tid='one',cue_only=False)],failed=[])
        self.w.prep_controller=SimpleNamespace(state_lane=lane)
        self.request();QTest.qWait(20);self.ready.assert_not_called()
        lane.queue[0]['cue_only']=True
        self.until(lambda:self.ready.called or self.failed.called);self.failed.assert_not_called()

    def test_selected_saved_long_grid_proves_only_headline_and_identity_without_serializing_beats(self):
        grid=[[index%4+1,index*500,142.0] for index in range(10000)]
        epoch=self.path.stat()
        job=dict(version=1,id=str(uuid4()),track_id='one',source=str(self.w.lib.source),library_id='123',
            path=str(self.path),epoch=[epoch.st_dev,epoch.st_ino,epoch.st_size,epoch.st_mtime_ns,epoch.st_ctime_ns],
            native_ready=False,sha256='',duration=7200,grid=grid,cues=[],prepared=None,error='',
            operations=[dict(id=str(uuid4()),kind='grid',operation='bpm',value=142,before='0'*64,after='1'*64)])
        prep_edits.validate([job]);self.w.settings[prep_edits.KEY]=[job];self.save()
        class NoBeatTraversal(list):
            def __iter__(self):raise AssertionError('Export copied or serialized the complete beat grid')
        job['grid']=NoBeatTraversal(grid)
        prep=SimpleNamespace(window=self.w,job=lambda tid:job if tid=='one' else None,
            _job_matches=PrepEditController._job_matches)
        original=self.w._row_for
        def row(track):
            result=original(track)
            if track.track_id=='one':result['bpm']=str(PrepEditController.grid_view(prep,'one')[1])
            return result
        self.w._row_for=row
        self.request();self.until(lambda:self.ready.called or self.failed.called);self.failed.assert_not_called()
        self.assertEqual(self.ready.call_args.args[0].materialize().display_rows['one']['bpm'],142)

    def test_stock_none_resolves_only_current_source_bound_uuid_association(self):
        self.w._stock_view_cache['/generated/ipod']=(SimpleNamespace(identity_key='device'),None)
        binding=SimpleNamespace(key='42',native_id='42',uuid='playlist-uuid')
        self.w._stock_catalog_witness_cache['/generated/ipod']=SimpleNamespace(device_key='device',
            source=(str(self.w.lib.source),self.w.lib.source_kind,'123'),bindings=[binding])
        self.request(selected=None);self.until(lambda:self.ready.called or self.failed.called);self.failed.assert_not_called()
        capture=self.ready.call_args.args[0];self.assertTrue(capture.requested_none)
        self.assertEqual(capture.selected_playlists[0].pid,'42')
        self.ready.reset_mock();self.failed.reset_mock();binding.uuid='different-uuid'
        self.request(selected=None);self.until(lambda:self.ready.called or self.failed.called)
        self.ready.assert_not_called();self.failed.assert_called_once()

    def test_cancel_after_checkpoint_before_ready_delivery_does_not_start_export(self):
        deferred=[];original=QTest.qWait
        from desktop import ipod_export_capture as module
        single_shot=module.QTimer.singleShot
        def schedule(*args):
            callback=args[-1]
            if args[0]==0 and getattr(callback,'__name__','')=='deliver':deferred.append(callback)
            else:single_shot(*args)
        with mock.patch.object(module.QTimer,'singleShot',side_effect=schedule):
            self.request();self.until(lambda:bool(deferred))
        self.ready.assert_not_called();self.assertTrue(cancel_source_syncs(self.w))
        deferred[0]();original(1)
        self.ready.assert_not_called();self.failed.assert_not_called()

    def test_cancel_locator_preflight_stops_before_the_next_selected_path(self):
        self.w.lib.playlists[0].keys=['one','other']
        entered=threading.Event();release=threading.Event();resolved=[];original=Path.resolve
        def resolve(path,*args,**options):
            result=original(path,*args,**options)
            if path in {self.path,self.other}:
                resolved.append(path)
                if len(resolved)==1:entered.set();release.wait(3)
            return result
        before=self.w._state_repository.path.read_bytes()
        with mock.patch('desktop.ipod_export_capture.Path.resolve',new=resolve):
            self.request()
            try:
                self.until(entered.is_set);self.assertTrue(cancel_source_syncs(self.w))
                release.set();self.until(lambda:not self.w._state_committer.pending)
            finally:release.set()
        self.ready.assert_not_called();self.failed.assert_not_called();self.assertEqual(len(resolved),1)
        self.assertEqual(self.w._state_repository.path.read_bytes(),before)
        self.assertTrue(self.path.is_file());self.assertTrue(self.other.is_file())

    def test_cancel_during_checkpoint_never_delivers_ready_or_changes_canonical_state(self):
        entered=threading.Event();release=threading.Event();original=self.w._state_repository.verify_selected_values
        def held(*args,**kwargs):
            entered.set()
            if not release.wait(3):raise RuntimeError('Generated verification timed out')
            return original(*args,**kwargs)
        before=self.w._state_repository.path.read_bytes()
        with mock.patch.object(self.w._state_repository,'verify_selected_values',side_effect=held):
            self.request();self.until(entered.is_set)
            self.assertTrue(cancel_source_syncs(self.w));release.set()
            self.until(lambda:not self.w._state_committer.pending)
        self.ready.assert_not_called();self.failed.assert_not_called()
        self.assertEqual(self.w._state_repository.path.read_bytes(),before)

    def test_wrong_source_restore_or_mount_before_delivery_never_borrows_capture(self):
        for action in ('source','restore','mount'):
            with self.subTest(action=action):
                self.ready.reset_mock();self.failed.reset_mock();self.w.tag_controller.busy=True
                self.request()
                old=self.w.lib
                if action=='source':self.w.lib=replace(old,source_library_id='replacement')
                if action=='restore':self.w._state_repository.area.generation+=1
                if action=='mount':self.uuid='replacement'
                self.w.tag_controller.busy=False;self.w.tag_controller.idle.emit()
                self.until(lambda:self.ready.called or self.failed.called)
                self.ready.assert_not_called();self.failed.assert_called_once()
                self.w.lib=old;self.uuid='generated-volume'
                if action=='restore':self.w._state_repository.load()

    def test_dual_projection_includes_only_exact_cached_managed_retained_playlists(self):
        other=Playlist('Retained',[],['other'],'43')
        self.w.lib.playlists.append(other)
        self.w.lib.source_playlist_catalog['nodes'].append(dict(ID='43',UUID='retained-uuid',rb_local_deleted=0))
        baseline={'playlists':{'43':{'name':'Retained'}},'playlist_order':['43']}
        self.w._stock_view_cache['/generated/ipod']=(SimpleNamespace(identity_key='device'),SimpleNamespace(manifest=baseline))
        self.request('dual');self.until(lambda:self.ready.called or self.failed.called);self.failed.assert_not_called()
        capture=self.ready.call_args.args[0]
        self.assertEqual(set(capture.materialize().display_rows),{'one','other'})
        self.assertEqual(capture.expected_manifest,baseline)
        baseline['playlists']['43']['name']='Changed device baseline'
        with self.assertRaisesRegex(RuntimeError,'managed iPod catalog changed'):capture.require_current(self.w,'/generated/ipod')

    def test_dual_preload_is_owned_once_and_observed_absence_is_preserved(self):
        self.w._device_profiles={'/generated/ipod':SimpleNamespace(kind='dual_boot')}
        def read(dev,profile):self.w._stock_view_cache[dev]=(SimpleNamespace(identity_key='device'),None)
        self.w._read_stock_device_view=mock.Mock(side_effect=read)
        self.request('dual');self.until(lambda:self.ready.called or self.failed.called);self.failed.assert_not_called()
        capture=self.ready.call_args.args[0]
        self.assertTrue(capture.expected_manifest_absent)
        self.assertIsNone(capture.expected_manifest)
        self.w._read_stock_device_view.assert_called_once()
        self.assertTrue(capture.require_current(self.w,'/generated/ipod'))

    def rockbox_target(self,payload):
        target=self.root/'generated-rockbox';target.mkdir()
        if payload is not None:
            (target/'.soundbox').mkdir();(target/'.soundbox/manifest.json').write_bytes(payload)
        dev=str(target);self.w._device_mount_tokens[dev]=9
        return target,dev

    def test_rockbox_managed_sync_resolves_current_keys_and_keeps_exact_raw_manifest(self):
        baseline={'schema':2,'playlists':{'42':{'name':'Older name'},'missing':{'name':'Removed'}},
            'playlist_order':['missing','42'],'files':{},'generation':0,'completed_generation':0}
        payload=json.dumps(baseline,indent=3).encode()+b'\n'
        _target,dev=self.rockbox_target(payload)
        request_ipod_export(self.w,'rockbox',dev,None,ready=self.ready,failed=self.failed)
        self.until(lambda:self.ready.called or self.failed.called);self.failed.assert_not_called()
        capture=self.ready.call_args.args[0]
        self.assertEqual([p.pid for p in capture.selected_playlists],['42'])
        self.assertEqual(capture.expected_manifest_payload,payload)
        self.assertIsNone(capture.expected_manifest)  # Dual's normalized dictionary is a separate contract.
        self.assertFalse(capture.expected_manifest_absent)
        self.assertEqual(set(capture.materialize().display_rows),{'one'})
        self.assertTrue(capture.require_current(self.w,dev))

    def test_rockbox_observed_absence_is_pinned_and_later_manifest_is_refused(self):
        target,dev=self.rockbox_target(None)
        request_ipod_export(self.w,'rockbox',dev,[self.w.lib.playlists[0]],ready=self.ready,failed=self.failed)
        self.until(lambda:self.ready.called or self.failed.called);self.failed.assert_not_called()
        capture=self.ready.call_args.args[0]
        self.assertTrue(capture.expected_manifest_absent);self.assertIsNone(capture.expected_manifest_payload)
        (target/'.soundbox').mkdir()
        (target/'.soundbox/manifest.json').write_text(json.dumps({'schema':2,'playlists':{},'files':{}}))
        with self.assertRaisesRegex(RuntimeError,'Rockbox catalog changed'):capture.verify_worker()

    def test_rockbox_manifest_change_while_canonical_checkpoint_waits_refuses_capture(self):
        payload=json.dumps({'schema':2,'playlists':{'42':{'name':'Favorites'}},'files':{}}).encode()
        target,dev=self.rockbox_target(payload);self.w.tag_controller.busy=True
        request_ipod_export(self.w,'rockbox',dev,None,ready=self.ready,failed=self.failed)
        self.until(lambda:bool(self.w._state_committer._checkpoints))
        (target/'.soundbox/manifest.json').write_bytes(payload+b' ')
        self.w.tag_controller.busy=False;self.w.tag_controller.idle.emit()
        self.until(lambda:self.ready.called or self.failed.called)
        self.ready.assert_not_called();self.failed.assert_called_once()
        self.assertIn('Rockbox catalog changed',str(self.failed.call_args.args[0]))

    def test_rockbox_unknown_malformed_manifest_and_no_managed_selection_refuse(self):
        for payload in (b'{broken',b'{"schema":1,"playlists":{"42":{}},"files":{}}',
                b'{"schema":2,"playlists":{},"files":{}}'):
            with self.subTest(payload=payload):
                self.ready.reset_mock();self.failed.reset_mock()
                folder=self.root/'generated-rockbox'
                if folder.exists():
                    (folder/'.soundbox/manifest.json').unlink();(folder/'.soundbox').rmdir();folder.rmdir()
                target,dev=self.rockbox_target(payload)
                request_ipod_export(self.w,'rockbox',dev,None,ready=self.ready,failed=self.failed)
                self.until(lambda:self.ready.called or self.failed.called)
                self.ready.assert_not_called();self.failed.assert_called_once()
                self.assertEqual((target/'.soundbox/manifest.json').read_bytes(),payload)

    def test_cover_guard_uses_only_one_preindexed_selected_token(self):
        self.request();self.until(lambda:self.ready.called or self.failed.called);self.failed.assert_not_called()
        capture=self.ready.call_args.args[0]
        cache=SimpleNamespace(selection_current=mock.Mock(return_value=True))
        self.assertTrue(capture.validate_artwork(cache,'one'))
        cache.selection_current.assert_called_once_with(('one',))
        with self.assertRaisesRegex(RuntimeError,'Selected artwork changed'):capture.validate_artwork(cache,'not-captured')
        with self.assertRaises(TypeError):capture.artwork_by_id['one']=('replaced',)

    def test_later_physical_conversion_is_guarded_but_later_membership_edit_keeps_snapshot(self):
        self.request();self.until(lambda:self.ready.called or self.failed.called);self.failed.assert_not_called()
        capture=self.ready.call_args.args[0]
        self.w.settings['rekordbox_playlist_edits']=[dict(source=str(self.w.lib.source),library_id='123',
            request=dict(operation='append',playlist_id='42'),error='')]
        self.assertTrue(capture.require_current(self.w,'/generated/ipod'))
        self.w.settings[media_conversion_jobs.KEY]=[self.media_job()]
        with self.assertRaisesRegex(RuntimeError,'source transition'):capture.require_current(self.w,'/generated/ipod')

    def test_unbound_legacy_none_requests_explicit_selection_without_reading_shadow(self):
        self.request(selected=None);self.until(lambda:self.failed.called)
        self.ready.assert_not_called();self.assertIsInstance(self.failed.call_args.args[0],ChooseIpodPlaylists)
        self.assertTrue(self.failed.call_args.args[0].choose_playlists)

    def test_effective_projector_only_requests_the_given_rows(self):
        original=self.w._row_for;calls=[]
        self.w._row_for=lambda track:(calls.append(track.track_id),original(track))[1]
        self.assertEqual(set(effective_rows(self.w,['one','one'])),{'one'})
        self.assertEqual(calls,['one'])
