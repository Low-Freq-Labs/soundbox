"""Durable metadata phases, late callbacks and honest native readback on fixtures."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtWidgets import QApplication,QWidget

from desktop.metadata_controller import ACKS,KEY,MetadataController
from desktop.app import MainWindow
from soundbox import alias_intents,engine,metadata_provenance,rekordbox_metadata_store as native,source_truth as truth
from soundbox.library import Library,Track
from tests.test_tag_creation_controller import Tags

UUID='00000000-0000-4000-8000-000000000001'


class Window(QWidget):
    def __init__(self,root):
        super().__init__();self.settings={};self.titles={};self.overrides={};self.aliases={}
        track=Track('local-song','Original Title','Original Artist','file:///synthetic/song.wav',
                    source_native_id='1',source_uuid=UUID)
        self.lib=Library({'local-song':track},[],'/synthetic/master.db',source_kind='rekordbox-db',source_library_id='123')
        self.tag_controller=Tags(root);self._shutting_down=False;self._state_maintenance=False;self._refresh_worker=None
        self._device_operation_owned=lambda:False;self._title_junk=lambda:None
        self._invalidate_rows=mock.Mock();self._build_artist_registry=mock.Mock(side_effect=lambda:MainWindow._build_artist_registry(self))
        self._flash_status=mock.Mock()
        self.saved=[];self.published=[];self.save_ok=True

    def _save_state(self):
        self.saved.append(deepcopy(dict(settings=self.settings,titles=self.titles,artists=self.overrides)))
        return self.save_ok

    def _publish_created_library(self,library):self.published.append(library);self.lib=library


class MetadataPhases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.area=self.enterContext(tempfile.TemporaryDirectory());self.w=Window(Path(self.area))
        self.addCleanup(self.w.deleteLater)
        self.enterContext(mock.patch('desktop.metadata_controller.assert_rekordbox_closed'))
        self.native={'title':'Original Title','artist':'Original Artist'};self.receipts=set();self.writes=[]
        self.captured=[];owner=self
        class Store:
            def capture_request(self,identity,uuid,path,field,value,*,operation_id):
                owner.captured.append(operation_id)
                return native.request_for('123',identity,uuid,path,field,value,owner.native[field],operation_id=operation_id)
            def apply(self,request):
                if request['operation_id'] not in owner.receipts:
                    owner.native[request['field']]=request['value'];owner.writes.append(deepcopy(request))
                    owner.receipts.add(request['operation_id'])
            def retry_request(self,request):return deepcopy(request)
        self.library=self.w.lib
        def load(_source):
            track=replace(self.library.tracks_by_id['local-song'],name=self.native['title'],artist=self.native['artist'])
            return replace(self.library,tracks_by_id={'1':track})
        self.load=load;self.controller=MetadataController(self.w,store_factory=lambda _:Store(),loader=load)
        self.addCleanup(self.controller.stop)

    def pump(self):self.controller.timer.stop();self.controller.pump()
    def deliver(self,*,error=''):self.w.tag_controller.deliver(error=error);self.controller.timer.stop()

    def begin(self):
        self.assertTrue(self.controller.enqueue('local-song',{'title':'Desired Title'}))
        self.pump();self.assertEqual(len(self.w.tag_controller.admitted),1)

    def prepared(self):
        self.begin();self.deliver()
        self.assertIsNotNone(self.controller.pending[0]['request']);self.assertFalse(self.writes)

    def test_quit_preserves_only_idle_unadmitted_metadata_without_authority_or_captured_native_requests(self):
        self.assertTrue(self.controller.enqueue('local-song',{'title':'Desired Title'}))
        self.controller.timer.stop();before=deepcopy(self.w.settings)
        self.assertFalse(self.controller.can_quit_with_unadmitted_metadata())
        self.controller.factory=None
        with mock.patch('desktop.metadata_controller.native.load_authority',return_value=None):
            self.assertTrue(self.controller.can_quit_with_unadmitted_metadata())
            self.w.tag_controller.busy=True
            self.assertFalse(self.controller.can_quit_with_unadmitted_metadata())
            self.w.tag_controller.busy=False
            with mock.patch.object(self.controller,'tasks',SimpleNamespace(pending=1)):
                self.assertFalse(self.controller.can_quit_with_unadmitted_metadata())
            job=self.controller.pending[0]
            job['request']=native.request_for('123','1',UUID,'/synthetic/song.wav','title',job['value'],
                'Original Title',operation_id=job['id'])
            self.assertFalse(self.controller.can_quit_with_unadmitted_metadata())
            job['request']=None
            self.assertTrue(self.controller.can_quit_with_unadmitted_metadata())
            queue=self.w.settings[KEY];self.w.settings[KEY]=[None]
            self.assertFalse(self.controller.can_quit_with_unadmitted_metadata())
            self.w.settings[KEY]=queue
        with mock.patch('desktop.metadata_controller.native.load_authority',return_value=object()):
            self.assertFalse(self.controller.can_quit_with_unadmitted_metadata())
        self.assertEqual(self.w.settings,before);self.assertFalse(self.writes)

    def test_disabled_metadata_quit_preserves_read_only_derivation_backlog_for_fresh_restart(self):
        self.assertTrue(self.controller.enqueue('local-song',{'title':'Desired Title'}))
        self.controller.timer.stop();self.controller.factory=None
        self.controller._derived_dirty={'local-song':7}
        self.w.metadata_controller=self.controller
        before=deepcopy(self.w.settings)
        with mock.patch('desktop.metadata_controller.native.load_authority',return_value=None):
            self.assertTrue(self.controller.planning_pending)
            self.assertTrue(self.controller.can_quit_with_unadmitted_metadata())
            self.assertEqual(MainWindow._pending_save_reason(self.w),'')
        self.assertEqual(self.controller._derived_dirty,{'local-song':7})
        self.assertEqual(self.w.settings,before);self.assertEqual(self.w.titles,{'local-song':'Desired Title'})
        self.assertFalse(self.writes)

    def test_intention_and_effective_local_curation_then_exact_request_persist_before_sql_and_ack_before_publish(self):
        self.begin()
        self.assertEqual(self.w.saved[0]['titles'],{'local-song':'Desired Title'})
        self.assertEqual(self.w.saved[0]['settings'][KEY][0]['value'],'Desired Title')
        self.assertIsNone(self.w.saved[0]['settings'][KEY][0]['request'])
        self.assertEqual(self.writes,[]);self.deliver()
        request=deepcopy(self.controller.pending[0]['request'])
        self.assertEqual(self.w.saved[-1]['settings'][KEY][0]['request'],request)
        self.pump();self.assertEqual(self.writes,[]);self.deliver()
        self.assertEqual(self.writes,[request]);self.assertEqual(self.w.saved[-1]['settings'][KEY],[])
        self.assertEqual(self.w.published[-1].tracks_by_id['1'].name,'Desired Title')
        self.assertEqual(self.w.titles,{})
        fresh=self.w.published[-1].tracks_by_id['1']
        self.assertEqual(engine.track_row(fresh,self.w.overrides,self.w.titles)['title'],'Desired Title')
        origin=next(iter(self.w.settings[alias_intents.ORIGINS].values()))
        self.assertEqual(metadata_provenance.manual(origin,'title'),'Desired Title')

    def test_initial_or_exact_request_storage_failure_never_publishes_metadata_and_restores_current_curation(self):
        self.w.save_ok=False
        self.assertFalse(self.controller.enqueue('local-song',{'title':'Desired Title'}))
        self.assertEqual(self.w.titles,{});self.assertNotIn(KEY,self.w.settings)
        self.assertFalse(self.w.tag_controller.admitted)
        self.w.save_ok=True;self.begin();self.w.save_ok=False;self.deliver()
        self.assertIsNone(self.controller.pending[0]['request']);self.assertTrue(self.controller.pending[0]['error'])
        self.assertFalse(self.writes);self.assertFalse(self.w.published)
        self.pump();self.assertEqual(len(self.w.tag_controller.admitted),1)

    def test_committed_write_with_failed_local_ack_keeps_error_and_retries_same_operation_without_duplicate_sql(self):
        self.prepared();request=deepcopy(self.controller.pending[0]['request'])
        self.pump();self.w.save_ok=False;self.deliver()
        self.assertEqual(self.writes,[request]);self.assertFalse(self.w.published)
        self.assertEqual(self.controller.pending[0]['request'],request);self.assertTrue(self.controller.pending[0]['error'])
        for _ in range(3):self.pump()
        self.assertEqual(len(self.w.tag_controller.admitted),2)
        self.assertFalse(self.controller.retry());self.w.save_ok=True;self.assertTrue(self.controller.retry())
        self.pump();self.deliver();self.pump();self.deliver()
        self.assertFalse(self.controller.pending);self.assertEqual(self.writes,[request]);self.assertEqual(len(self.w.published),1)

    def test_replaced_intention_rejects_late_error_and_success_callbacks(self):
        self.begin();original=deepcopy(self.controller.pending[0])
        replacement={**original,'id':'f'*32,'value':'Replacement Title'}
        for error in ('late source failure',''):
            self.w.settings[KEY]=[deepcopy(replacement)];self.deliver(error=error)
            self.assertEqual(self.controller.pending,[replacement]);self.assertFalse(self.w.published)

    def test_switching_library_preserves_original_native_receipt_and_intention_without_new_source_presentation(self):
        self.prepared();self.pump()
        switched=replace(self.w.lib,source='/other/master.db',source_library_id='456');self.w.lib=switched
        self.deliver()
        self.assertIs(self.w.lib,switched);self.assertEqual(len(self.writes),1)
        self.assertTrue(self.controller.pending[0]['error']);self.assertFalse(self.w.published)

    def test_prepared_prep_and_matching_analysis_dependencies_wait_without_consuming_the_source_lane(self):
        self.assertTrue(self.controller.enqueue('local-song',{'title':'Desired Title'}))
        self.w.tag_controller.prep_priority=True;self.pump();self.assertFalse(self.w.tag_controller.admitted)
        self.w.tag_controller.prep_priority=False
        self.w.settings['native_analysis_jobs']=[dict(source='/synthetic/master.db',library_id='123',local_id='local-song',error='')]
        self.w.native_analysis_controller=SimpleNamespace(schedule=mock.Mock())
        self.pump();self.assertFalse(self.w.tag_controller.admitted);self.w.native_analysis_controller.schedule.assert_called_once()
        self.w.settings['native_analysis_jobs']=[];self.pump();self.assertEqual(len(self.w.tag_controller.admitted),1)

    def test_first_registration_never_retargets_saved_metadata_to_a_reused_logical_id(self):
        original=self.w.lib.tracks_by_id['local-song'];original.source_uuid=''
        self.assertTrue(self.controller.enqueue('local-song',{'title':'Desired Title'}))
        intention=deepcopy(self.controller.pending[0])
        self.w.lib.tracks_by_id['local-song']=replace(original,source_uuid=UUID,
            source_native_id='2',location_uri='file:///synthetic/replacement.wav')
        self.pump()
        pending=self.controller.pending[0]
        self.assertTrue(pending['error'])
        self.assertEqual({**pending,'error':''},intention)
        self.assertEqual(self.controller.display_fields(self.w.lib.tracks_by_id['local-song']),{})
        self.assertFalse(self.w.tag_controller.admitted);self.assertFalse(self.writes)
        self.assertFalse(self.w.published)
        self.assertTrue(self.controller.enqueue('local-song',{'title':'Desired Title'}))
        self.assertEqual(len(self.controller.pending),2)
        self.assertEqual(self.controller.pending[1]['path'],'/synthetic/replacement.wav')
        self.assertEqual(self.controller.pending[1]['track_uuid'],UUID)

    def test_first_registration_preserves_verified_canonical_alias_then_persists_native_binding(self):
        original=self.w.lib.tracks_by_id['local-song'];original.source_uuid=''
        self.assertTrue(self.controller.enqueue('local-song',{'title':'Desired Title'}))
        operation=self.controller.pending[0]['id']
        self.w.lib.tracks_by_id['local-song']=replace(original,source_uuid=UUID,
            source_native_id='2',source_native_path='/synthetic/native-alias.wav')
        self.pump()
        pending=self.controller.pending[0]
        self.assertEqual((pending['id'],pending['native_id'],pending['track_uuid'],pending['path']),
                         (operation,'2',UUID,'/synthetic/native-alias.wav'))
        self.assertEqual(self.w.saved[-1]['settings'][KEY],[pending])
        self.assertFalse(pending['error']);self.assertFalse(self.w.tag_controller.admitted)
        self.assertEqual(self.controller.display_fields(self.w.lib.tracks_by_id['local-song']),{'title':'Desired Title'})

    def test_shutdown_acknowledges_the_committed_head_without_presentation_or_starting_its_successor(self):
        self.prepared();self.assertTrue(self.controller.enqueue('local-song',{'artist':'New Artist'}));self.pump()
        self.controller.stop();self.w._shutting_down=True;self.deliver()
        self.assertEqual(len(self.controller.pending),1);self.assertFalse(self.w.published)
        self.assertFalse(self.controller.timer.isActive())

    def test_artist_alias_and_title_cleanup_are_used_for_the_native_intended_value(self):
        self.w.aliases={'typo':'Canonical Artist'}
        self.assertTrue(self.controller.enqueue('local-song',{'artist':'typo'}))
        self.assertEqual(self.controller.pending[0]['value'],'Canonical Artist')
        self.assertEqual(self.w.overrides['local-song'],'typo')
        self.w.lib.tracks_by_id['local-song'].name='Canonical Artist - Clean Me'
        self.assertTrue(self.controller.enqueue('local-song',{'title':''}))
        self.assertEqual(self.controller.pending[-1]['value'],'Clean Me')

    def test_unsupported_track_info_fields_never_fall_back_to_a_private_only_success(self):
        self.assertFalse(self.controller.enqueue('local-song',{'album':'Not native yet','title':'Desired'}))
        self.assertEqual(self.w.titles,{});self.assertNotIn(KEY,self.w.settings)
        self.assertFalse(self.w.tag_controller.admitted)

    def freeze_pair(self):
        track=self.w.lib.tracks_by_id['local-song'];track.name='Artist - Song';track.artist=''
        self.native.update(title=track.name,artist='')
        descriptor,targets=truth.build(truth.capture(self.w.lib,titles={},artists={},aliases={},settings={}))
        self.w.settings.update({truth.MIGRATION:descriptor,truth.TARGETS:targets})
        return descriptor,targets

    def test_frozen_pair_expands_two_durable_intents_and_requires_each_exact_ack_before_cursor_advance(self):
        descriptor,targets=self.freeze_pair();self.pump()
        self.assertEqual([job['field'] for job in self.controller.pending],['title','artist'])
        self.assertEqual(len(self.w.saved[-1]['settings'][KEY]),2)
        self.assertEqual(self.controller.display_fields(self.w.lib.tracks_by_id['local-song']),dict(title='Song',artist='Artist'))
        for field in ('title','artist'):
            self.pump();self.deliver();self.pump();self.deliver()
            active=self.w.settings[truth.MIGRATION]['active']
            self.assertNotIn(field,active['operations'])
            self.assertEqual(self.w.settings[truth.MIGRATION]['cursor'],0)
        self.pump();self.assertEqual(self.w.settings[truth.MIGRATION]['cursor'],1)
        self.assertFalse(self.controller.migration_pending)
        self.assertEqual(self.native,dict(title='Song',artist='Artist'))
        self.assertEqual(self.w.settings[truth.TARGETS],targets)

    def test_mid_pair_title_edit_preserves_frozen_and_pending_artist(self):
        self.freeze_pair();self.pump();self.pump();self.deliver();self.pump();self.deliver()
        self.assertEqual(self.native,dict(title='Song',artist=''))
        # Loader deliberately projects the same identity under the local alias.
        native_track=self.w.lib.tracks_by_id['1']
        self.w.lib=replace(self.w.lib,tracks_by_id={'local-song':native_track})
        self.assertTrue(self.controller.enqueue('local-song',{'title':'New Title'}))
        self.assertEqual([(job['field'],job['value']) for job in self.controller.pending],
                         [('artist','Artist'),('title','New Title')])
        target=next(iter(self.w.settings[truth.TARGETS].values()))
        self.assertEqual((target['title'],target['artist']),('New Title','Artist'))

    def test_absent_pair_job_is_attention_not_an_acknowledgement(self):
        self.freeze_pair();self.pump();self.w.settings[KEY]=[];self.pump()
        descriptor=self.w.settings[truth.MIGRATION]
        self.assertEqual(descriptor['cursor'],0);self.assertTrue(descriptor['active']['operations'])
        self.assertIn('acknowledgements are incomplete',descriptor['error'])

    def test_pair_ack_storage_failure_keeps_operation_and_cursor_until_idempotent_retry(self):
        self.freeze_pair();self.pump();self.pump();self.deliver();self.pump()
        before=deepcopy(self.w.settings[truth.MIGRATION]);self.w.save_ok=False;self.deliver()
        self.assertEqual(self.w.settings[truth.MIGRATION],before)
        self.assertTrue(self.controller.pending[0]['error']);self.assertEqual(len(self.writes),1)
        self.w.save_ok=True;self.assertTrue(self.controller.retry())
        self.pump();self.deliver();self.pump();self.deliver()
        self.assertNotIn('title',self.w.settings[truth.MIGRATION]['active']['operations'])
        self.assertEqual(len(self.writes),1)

    def test_unresolved_frozen_values_survive_completed_available_work_and_cannot_be_replaced(self):
        track=self.w.lib.tracks_by_id['local-song'];track.source_uuid='';track.artist='';track.name='Missing Artist - Song'
        descriptor,targets=truth.build(truth.capture(self.w.lib,titles={},artists={},aliases={},settings={}))
        self.w.settings.update({truth.MIGRATION:descriptor,truth.TARGETS:targets});before=deepcopy(self.w.settings)
        self.assertTrue(self.controller.migration_pending);self.pump()
        self.assertFalse(self.controller.begin_source_truth())
        self.assertEqual(self.w.settings,before);self.assertFalse(self.w.tag_controller.admitted)
        self.assertIn('unresolved',self.w.tag_controller.metadata_update_status)

    def test_malformed_saved_migration_blocks_without_projection_or_pending_guard_crashes(self):
        for descriptor,targets in (([],{}),({},{}),({'format':'broken','order':[{}]},{}),
                                   ({'format':'soundbox-source-truth-building-v1','scope':None},{})):
            with self.subTest(descriptor=descriptor):
                self.w.settings={truth.MIGRATION:descriptor,truth.TARGETS:targets}
                self.assertTrue(self.controller.migration_pending);self.assertTrue(self.controller.queue_error())
                self.assertEqual(self.controller.display_fields(self.w.lib.tracks_by_id['local-song']),{})
                self.pump();self.assertFalse(self.w.tag_controller.admitted)

    def test_freeze_inputs_are_durable_before_worker_and_maintenance_cannot_start_build_or_expand(self):
        self.w.lib.tracks_by_id['local-song'].name='Artist - Song'
        self.w.lib.tracks_by_id['local-song'].artist=''
        self.w._state_maintenance=True;self.assertFalse(self.controller.begin_source_truth())
        self.w._state_maintenance=False
        with mock.patch.object(self.controller,'_resume_source_truth_build') as resume:
            self.assertTrue(self.controller.begin_source_truth());resume.assert_called_once()
        building=deepcopy(self.w.settings);self.assertEqual(self.w.saved[-1]['settings'],building)
        self.w._state_maintenance=True;self.controller._resume_source_truth_build();self.pump()
        self.assertEqual(self.controller.tasks.pending,0);self.assertEqual(self.w.settings,building)
        self.w._state_maintenance=False;self.w._state_publication=True
        self.controller._resume_source_truth_build();self.pump();self.assertEqual(self.controller.tasks.pending,0)
        self.w._state_publication=False;self.controller._resume_source_truth_build()
        self.assertEqual(self.controller.tasks.pending,1)
        self.assertTrue(next(iter(self.controller.tasks._workers)).wait(5000))
        self.w._state_maintenance=True;self.controller.tasks.drain()
        self.assertEqual(self.w.settings[truth.MIGRATION]['format'],'soundbox-source-truth-v1')
        self.assertFalse(self.controller.pending);self.pump();self.assertFalse(self.controller.pending)

    def test_old_build_completion_cannot_replace_new_generation(self):
        callbacks=[];tasks=SimpleNamespace(pending=0,adopt=lambda worker,callback:callbacks.append((worker,callback)),drain=lambda:None)
        self.controller.tasks=tasks
        with mock.patch('desktop.metadata_controller.OneShotTask') as worker_type:
            worker=worker_type.return_value
            self.assertTrue(self.controller.begin_source_truth());building=deepcopy(self.w.settings[truth.MIGRATION])
            record=self.w.settings[truth.TARGETS][building['input_key']]
            worker.result=truth.build(truth._encode(record['payload']),migration_id=building['id'])
            replacement={**building,'id':'f'*32};self.w.settings[truth.MIGRATION]=replacement
            callbacks[0][1](worker)
            self.assertIs(self.w.settings[truth.MIGRATION],replacement)
            self.assertEqual(self.w.settings[truth.TARGETS][building['input_key']],record)

    def test_malformed_build_later_edits_remain_attention_instead_of_crashing_the_projection(self):
        with mock.patch.object(self.controller,'_resume_source_truth_build'):
            self.assertTrue(self.controller.begin_source_truth())
        self.w.settings[truth.MIGRATION]['edits']={'local-song':{'uuid':UUID,'fields':None}}
        self.assertTrue(self.controller.queue_error());self.assertTrue(self.controller.migration_pending)
        self.assertEqual(self.controller.display_fields(self.w.lib.tracks_by_id['local-song']),{})
        self.pump();self.assertFalse(self.w.tag_controller.admitted)

    def test_source_curation_staging_has_no_save_and_exact_owner_rollback(self):
        self.w.custom={};self.w.removals={};self.w.playlist_additions={};self.w.hidden=set();self.w.hidden_dates={}
        self.w.titles={'local-song':'Saved Title'}
        owners={field:getattr(self.w,field) for field in ('settings','titles','overrides','aliases','custom',
                'removals','playlist_additions','hidden','hidden_dates')}
        token=self.controller.stage_source_scope(self.w.lib,previous=self.w.lib)
        self.assertFalse(self.w.saved);self.assertIn(truth.CURATION,self.w.settings)
        self.controller.rollback_source_scope(token)
        for field,owner in owners.items():self.assertIs(getattr(self.w,field),owner,field)

    def retirement_entries(self):
        store=self.controller.factory(self.w.lib)
        def entry(_store,request):
            return dict(source=str(self.w.lib.source),library_id='123',request=deepcopy(request),
                        proof_sha256='a'*64,marker_sha256='b'*64)
        type(store).retirement_entry=entry

    def test_ack_and_receipt_retirement_intent_persist_together_then_restart_compacts_without_native_work(self):
        self.retirement_entries();self.prepared();self.pump();self.deliver()
        self.assertFalse(self.controller.pending);self.assertEqual(len(self.w.settings[ACKS]),1)
        self.assertEqual(self.w.saved[-1]['settings'][ACKS],self.w.settings[ACKS])
        self.assertEqual(self.controller.tasks.pending,0)
        self.controller.stop()
        restarted=MetadataController(self.w,store_factory=self.controller.factory,loader=self.load)
        self.addCleanup(restarted.stop)
        before_admissions=len(self.w.tag_controller.admitted)
        with mock.patch('desktop.metadata_controller.native.retire_acknowledged_receipt') as compact:
            restarted.pump();self.assertEqual(restarted.tasks.pending,1)
            self.assertTrue(next(iter(restarted.tasks._workers)).wait(5000));restarted.tasks.drain()
            compact.assert_called_once()
        self.assertEqual(self.w.settings[ACKS],[])
        self.assertEqual(len(self.w.tag_controller.admitted),before_admissions)

    def test_failed_native_ack_does_not_admit_receipt_retirement_and_failed_compaction_retains_ledger(self):
        self.retirement_entries();self.prepared();self.pump();self.w.save_ok=False;self.deliver()
        self.assertNotIn(ACKS,self.w.settings);self.assertTrue(self.controller.pending)
        self.assertEqual(self.controller.tasks.pending,0)
        self.w.save_ok=True;self.assertTrue(self.controller.retry());self.pump();self.deliver();self.pump();self.deliver()
        with mock.patch('desktop.metadata_controller.native.retire_acknowledged_receipt',side_effect=OSError('disk full')):
            self.pump();self.assertTrue(next(iter(self.controller.tasks._workers)).wait(5000));self.controller.tasks.drain()
        self.assertTrue(self.w.settings[ACKS][0]['error']);self.assertFalse(self.controller.pending)
        self.assertEqual(self.w.saved[-1]['settings'][ACKS],self.w.settings[ACKS])

    def test_full_retirement_ledger_backpressures_native_work_but_preserves_new_user_intention(self):
        self.prepared();request=self.controller.pending[0]['request'];ledger=[]
        for index in range(256):
            captured={**request,'operation_id':f'{index:032x}'}
            entry=dict(source=str(self.w.lib.source),library_id='123',request=captured,
                       proof_sha256='a'*64,marker_sha256='b'*64)
            ledger.append(dict(entry=entry,error='Retirement needs storage attention'))
        self.w.settings[ACKS]=ledger
        self.assertTrue(self.controller.enqueue('local-song',{'artist':'New Artist'}))
        before=len(self.w.tag_controller.admitted);self.pump()
        self.assertEqual(len(self.w.tag_controller.admitted),before)
        self.assertEqual(self.controller.pending[-1]['value'],'New Artist')


if __name__=='__main__':unittest.main()
