"""Frozen metadata pairs cannot complete over independent companion changes."""
from copy import deepcopy
from dataclasses import replace
import unittest

from soundbox import source_truth as truth
from desktop.metadata_controller import KEY
from tests import test_alias_intents as fixtures
from tests import test_rekordbox_metadata_store as native_fixtures
from unittest import mock


class MetadataCompanions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):fixtures.AliasController.setUpClass()

    def setUp(self):
        self.case=fixtures.AliasController('test_title_only_without_migration_freezes_derived_artist_and_origin_before_title_sql')
        self.case.setUp();self.addCleanup(self.case.doCleanups)
        self.c=self.case.c;self.w=self.case.w;self.native=self.case.fixture.native

    def turn(self):self.case.turn()

    def conflict(self,field='artist',*,generation=False):
        track=replace(next(iter(self.w.lib.tracks_by_id.values())),name='Song - Beta' if field=='artist' else 'Original Title',
            artist='Alpha',source_uuid=fixtures.fixtures.UUID,source_native_id='1',source_native_path='',
            location_uri='file:///synthetic/song.wav')
        self.w.lib=replace(self.w.lib,tracks_by_id={'local-song':track},artist_registry=None)
        self.w.settings={};self.w.titles={};self.w.overrides={};self.w.aliases={}
        self.native.update(title=track.name,artist=track.artist)
        if generation:
            self.assertTrue(self.c.change_alias('Alpha','Beta'));self.case.build();self.assertTrue(self.c._promote_alias())
            self.case.fixture.pump();self.c.timer.stop()
        else:self.assertTrue(self.c.enqueue('local-song',{field:'Beta' if field=='artist' else 'New Title'}))
        self.assertEqual(len(self.c.pending),1);self.turn();original=deepcopy(self.c.pending[0])
        published=len(self.w.published)
        self.native['title' if field=='artist' else 'artist']='Independent Native Value'
        self.turn()
        self.assertTrue(self.c.pending[0]['error']);self.assertEqual(self.c.pending[0]['request'],original['request'])
        self.assertEqual(self.c.pending[0]['id'],original['id'])
        self.assertFalse(self.c.pending[0]['companion_conflict']['accepted'])
        self.assertEqual(len(self.w.published),published)
        return original

    def finish_retry(self,original):
        self.assertTrue(self.c.retry());self.turn();self.turn()
        repair=deepcopy(self.c.pending[0])
        self.assertNotEqual(repair['id'],original['id']);self.assertNotEqual(repair['field'],original['field'])
        self.assertEqual(self.w.saved[-1]['settings'][KEY],self.c.pending)
        self.turn();self.turn()
        self.assertFalse(self.c.pending)
        return repair

    def test_direct_title_and_artist_companion_conflicts_require_explicit_retry_new_helper(self):
        for field in ('title','artist'):
            with self.subTest(field=field):
                original=self.conflict(field)
                self.assertEqual(self.native['artist' if field=='title' else 'title'],'Independent Native Value')
                self.finish_retry(original)
                self.assertEqual(self.native,{'title':'New Title' if field=='title' else 'Song - Beta',
                                              'artist':'Alpha' if field=='title' else 'Beta'})
                self.assertEqual(sum(row['operation_id']==original['id'] for row in self.case.fixture.writes),1)

    def test_generation_actor_only_never_finishes_with_a_drifted_shielded_title(self):
        original=self.conflict(generation=True)
        descriptor=deepcopy(self.w.settings[truth.MIGRATION]);self.assertTrue(self.c.migration_pending)
        for _ in range(3):self.case.fixture.pump();self.c.timer.stop()
        self.assertEqual(self.w.settings[truth.MIGRATION],descriptor)
        self.finish_retry(original);self.case.fixture.pump();self.c.timer.stop()
        self.assertFalse(self.c.migration_pending);self.assertEqual(self.native,{'title':'Song - Beta','artist':'Beta'})

    def test_failed_ack_does_not_drop_original_head_or_admit_undurable_helper(self):
        original=self.conflict();self.assertTrue(self.c.retry());self.turn()
        before=deepcopy(self.c.pending);self.case.fixture.pump();self.w.save_ok=False;self.case.fixture.deliver()
        self.assertEqual(self.c.pending[0]['id'],original['id']);self.assertEqual(len(self.c.pending),1)
        self.assertEqual(self.c.pending[0]['request'],original['request'])
        self.assertEqual(sum(row['field']=='title' for row in self.case.fixture.writes),0)
        self.assertEqual({**self.c.pending[0],'error':''},{**before[0],'error':''})
        self.w.save_ok=True;self.finish_retry(original)

    def test_restored_conflict_without_origins_preserves_both_directions_of_the_pair(self):
        original=self.conflict()
        self.w.settings.pop(fixtures.alias.ORIGINS)
        self.assertEqual(self.c.queue_error(),'')
        self.assertTrue(self.c.retry());self.turn();self.turn()
        helper=deepcopy(self.c.pending[0])
        self.assertNotEqual(helper['id'],original['id']);self.assertEqual(helper['field'],'title')
        self.assertIsNone(helper['request'])
        self.assertEqual(helper['companion_conflict'],dict(field='artist',value='Beta',accepted=False))
        self.assertEqual(self.w.saved[-1]['settings'][KEY],self.c.pending)
        self.turn();prepared=deepcopy(self.c.pending[0])
        self.native['artist']='Changed while the restored helper was pending'
        self.turn()
        self.assertTrue(self.c.pending[0]['error'])
        self.assertEqual(self.c.pending[0]['id'],helper['id'])
        self.assertEqual(self.c.pending[0]['request'],prepared['request'])
        self.assertFalse(self.c.pending[0]['companion_conflict']['accepted'])
        admitted=len(self.w.tag_controller.admitted)
        self.case.fixture.pump();self.c.timer.stop()
        self.assertEqual(len(self.w.tag_controller.admitted),admitted)
        self.assertTrue(self.c.retry());self.turn();self.turn()
        final=deepcopy(self.c.pending[0])
        self.assertNotIn(final['id'],{original['id'],helper['id']})
        self.assertEqual((final['field'],final['value']),('artist','Beta'))
        self.turn();self.turn();self.assertFalse(self.c.pending)
        self.assertEqual(self.native,{'title':'Song - Beta','artist':'Beta'})
        for identity in (original['id'],helper['id']):
            self.assertEqual(sum(row['operation_id']==identity for row in self.case.fixture.writes),1)

    def test_later_explicit_companion_keeps_its_id_and_wins_over_older_conflict(self):
        original=self.conflict();self.assertTrue(self.c.enqueue('local-song',{'title':'New User Title'}))
        later=deepcopy(self.c.pending[1]);self.assertTrue(self.c.retry());self.turn();self.turn()
        self.assertEqual(self.c.pending,[later]);self.turn();self.turn()
        self.assertEqual(self.native['title'],'New User Title')
        self.assertEqual(sum(row['operation_id']==original['id'] for row in self.case.fixture.writes),1)

    def test_same_id_uuid_or_path_replacement_cannot_accept_repair_for_another_song(self):
        for field,value in (('source_uuid','00000000-0000-4000-8000-000000000099'),
                            ('location_uri','file:///replacement/song.wav')):
            with self.subTest(field=field):
                original=self.conflict();track=self.w.lib.tracks_by_id['local-song']
                self.w.lib=replace(self.w.lib,tracks_by_id={'local-song':replace(track,**{field:value})})
                before=deepcopy(self.c.pending);self.assertFalse(self.c.retry())
                self.assertEqual(self.c.pending,before);self.assertEqual(self.c.pending[0]['request'],original['request'])

    def test_reserved_helper_never_exceeds_fifo_limit_and_keeps_all_later_intentions(self):
        original=self.conflict();base=deepcopy(self.c.pending[0]);later=[];tracks=dict(self.w.lib.tracks_by_id)
        for number in range(2,257):
            local='other-'+str(number);path='/synthetic/'+local+'.wav';uuid=f'00000000-0000-4000-8000-{number:012d}'
            row={**base,'id':f'{number:032x}','local_id':local,'native_id':str(number),'track_uuid':uuid,
                 'path':path,'request':None,'retry':False,'error':'','value':'Pending Artist'}
            row.pop('companion_conflict',None);later.append(row)
            tracks[local]=replace(tracks['local-song'],track_id=local,source_native_id=str(number),source_uuid=uuid,
                                  location_uri='file://'+path,name='Other title',artist='Other artist')
        self.w.lib=replace(self.w.lib,tracks_by_id=tracks);current=self.w.lib
        def load(_source):
            track=replace(current.tracks_by_id['local-song'],name=self.native['title'],artist=self.native['artist'])
            return replace(current,tracks_by_id={**tracks,'local-song':track})
        self.c.loader=load;self.w.settings[KEY]=[base,*later]
        self.assertEqual(self.c.queue_error(),'');self.assertTrue(self.c.retry());self.turn();self.turn()
        self.assertEqual(len(self.c.pending),256);self.assertEqual(self.c.pending[1:],later)
        self.assertEqual(self.c.pending[0]['field'],'title');self.assertEqual(self.w.saved[-1]['settings'][KEY],self.c.pending)
        self.assertEqual(sum(row['operation_id']==original['id'] for row in self.case.fixture.writes),1)

    def test_acknowledged_generation_rechecks_full_pair_and_retry_uses_new_operations(self):
        descriptor,targets=truth.build(truth.capture(self.w.lib,titles={},artists={},aliases={},settings={}))
        self.w.settings.update({truth.MIGRATION:descriptor,truth.TARGETS:targets});self.c._source_truth_step()
        original_ids={row['id'] for row in self.c.pending}
        for _ in range(4):self.turn()
        self.assertFalse(self.c.pending);self.assertFalse(self.w.settings[truth.MIGRATION]['active']['operations'])
        track=next(iter(self.w.lib.tracks_by_id.values()));self.native['title']='Changed after ACK'
        self.w.lib=replace(self.w.lib,tracks_by_id={'local-song':replace(track,name=self.native['title'])})
        self.case.fixture.pump();self.c.timer.stop();failed=deepcopy(self.w.settings[truth.MIGRATION])
        self.assertTrue(failed['error']);self.assertEqual(failed['cursor'],0);self.assertTrue(self.c.migration_pending)
        self.assertTrue(self.c.retry());self.case.fixture.pump();self.c.timer.stop()
        self.assertEqual({row['field'] for row in self.c.pending},{'title','artist'})
        self.assertFalse(original_ids&{row['id'] for row in self.c.pending})
        for _ in range(4):self.turn()
        self.case.fixture.pump();self.c.timer.stop();self.assertFalse(self.c.migration_pending)
        self.assertEqual(self.native,{'title':'Song','artist':'Alpha'})

    def test_unverified_snapshot_cannot_advance_an_acknowledged_pair(self):
        descriptor,targets=truth.build(truth.capture(self.w.lib,titles={},artists={},aliases={},settings={}))
        identity=descriptor['order'][0]
        self.w.settings.update({truth.MIGRATION:{**descriptor,'active':dict(target=identity,operations={})},truth.TARGETS:targets})
        self.w.tag_controller.verified=False;before=deepcopy(self.w.settings)
        self.case.fixture.pump();self.c.timer.stop();self.assertEqual(self.w.settings,before)

    def test_failed_generation_retry_restores_exact_owners_and_new_pair_is_durable_before_admission(self):
        original=self.conflict(generation=True)
        self.finish_retry(original)
        self.assertFalse(self.w.settings[truth.MIGRATION]['active']['operations'])
        self.assertEqual(next(iter(self.w.settings[truth.TARGETS].values()))['fields'],['artist'])
        track=next(iter(self.w.lib.tracks_by_id.values()))
        self.native['title']='Changed after both acknowledgements'
        self.w.lib=replace(self.w.lib,tracks_by_id={'local-song':replace(track,name=self.native['title'])})
        self.case.fixture.pump();self.c.timer.stop()
        descriptor=self.w.settings[truth.MIGRATION];targets=self.w.settings[truth.TARGETS]
        self.assertTrue(descriptor['error']);self.assertEqual(descriptor['cursor'],0)
        state=deepcopy(self.w.settings);writes=deepcopy(self.case.fixture.writes)
        admitted=len(self.w.tag_controller.admitted);old_ids=set(self.case.fixture.receipts)
        for _ in range(3):self.case.fixture.pump();self.c.timer.stop()
        self.assertEqual(self.w.settings,state);self.assertEqual(self.case.fixture.writes,writes)
        self.assertEqual(len(self.w.tag_controller.admitted),admitted)
        self.w.save_ok=False
        self.assertFalse(self.c.retry())
        self.assertIs(self.w.settings[truth.MIGRATION],descriptor)
        self.assertIs(self.w.settings[truth.TARGETS],targets)
        self.assertEqual(self.w.settings,state);self.assertTrue(self.c.migration_pending)
        self.assertEqual(self.case.fixture.writes,writes)
        self.w.save_ok=True;self.assertTrue(self.c.retry())
        self.assertIsNone(self.w.settings[truth.MIGRATION]['active'])
        self.assertEqual(next(iter(self.w.settings[truth.TARGETS].values()))['fields'],['title','artist'])
        self.assertEqual(self.w.saved[-1]['settings'],self.w.settings)
        self.assertEqual(len(self.w.tag_controller.admitted),admitted)
        self.case.fixture.pump();self.c.timer.stop()
        self.assertEqual({job['field'] for job in self.c.pending},{'title','artist'})
        self.assertFalse(old_ids&{job['id'] for job in self.c.pending})
        self.assertTrue(all(job['request'] is None for job in self.c.pending))
        self.assertEqual(self.w.saved[-1]['settings'][KEY],self.c.pending)
        for _ in range(4):self.turn()
        self.case.fixture.pump();self.c.timer.stop()
        self.assertFalse(self.c.pending);self.assertFalse(self.c.migration_pending)
        self.assertEqual(self.native,{'title':'Song - Beta','artist':'Beta'})

    def test_unknown_artist_manual_intent_remains_native_empty_during_title_save(self):
        track=self.w.lib.tracks_by_id['local-song'];track.name='Untitled';track.artist=''
        self.native.update(title='Untitled',artist='');self.w.overrides={'local-song':'Unknown Artist'}
        self.assertTrue(self.c.enqueue('local-song',{'title':'New Title'}))
        self.assertEqual([row['field'] for row in self.c.pending],['title'])
        self.turn();self.turn();self.assertFalse(self.c.pending)
        self.assertEqual(self.native,{'title':'New Title','artist':''})

    def test_real_encrypted_companion_drift_holds_then_explicit_retry_repairs_using_existing_title_api(self):
        native=native_fixtures.NativeMetadataStore('test_title_only_updates_measured_columns_and_counter_preserving_xml_other_rows_and_audio')
        native.setUp();self.addCleanup(native.doCleanups)
        native.mutate('UPDATE djmdContent SET Title=?,ArtistID=? WHERE ID=?',('Song - Beta','2','1'))
        for identity,name in (('2','Alpha'),('3','Beta')):
            native.mutate('INSERT INTO djmdArtist VALUES (?,?,NULL,?,0,0,0,0,NULL,40,?,?)',
                (identity,name,f'00000000-0000-4000-8000-{int(identity):012d}',native_fixtures.CLOCK,native_fixtures.CLOCK))
        track=self.w.lib.tracks_by_id['local-song'];track.name='Song - Beta';track.artist='Alpha'
        self.w.lib=replace(self.w.lib,source=native.database);self.w.tag_controller.data_root=native.root/'data'
        def load(_source):
            title,artist=native.mutate('SELECT c.Title,a.Name FROM djmdContent c JOIN djmdArtist a ON a.ID=c.ArtistID WHERE c.ID=?',('1',))[0]
            return replace(self.w.lib,tracks_by_id={'local-song':replace(track,name=title,artist=artist)})
        self.c.factory=lambda _:native.writer;self.c.loader=load
        self.assertTrue(self.c.change_alias('Alpha','Beta'));self.case.build();self.assertTrue(self.c._promote_alias())
        self.case.fixture.pump();self.c.timer.stop()
        native.mutate('UPDATE djmdContent SET Title=? WHERE ID=?',('Independent Native Title','1'))
        self.turn();original=deepcopy(self.c.pending[0]['request']);self.turn()
        self.assertTrue(self.c.pending[0]['companion_conflict']);self.assertEqual(native.mutate(
            'SELECT Title FROM djmdContent WHERE ID=?',('1',)),[('Independent Native Title',)])
        self.assertTrue(self.c.retry());self.turn();self.turn()
        self.assertNotEqual(self.c.pending[0]['id'],original['operation_id'])
        self.assertEqual(self.c.pending[0]['field'],'title')
        with mock.patch.object(self.c,'schedule'):
            for _ in range(40):
                before=len(self.w.tag_controller.admitted);self.c.timer.stop();self.c.pump()
                if len(self.w.tag_controller.admitted)>before:self.case.fixture.deliver()
                for worker in list(self.c.tasks._workers):self.assertTrue(worker.wait(5000))
                for _ in range(10):
                    self.c.timer.stop();self.case.app.processEvents()
                    if not self.c.tasks.pending:break
                self.c.timer.stop()
                if not (self.c.pending or self.c.migration_pending or self.c.retirement_pending):break
        self.assertFalse(self.c.pending);self.assertFalse(self.c.migration_pending)
        self.assertEqual(native.mutate('SELECT c.Title,a.Name FROM djmdContent c JOIN djmdArtist a ON a.ID=c.ArtistID WHERE c.ID=?',('1',)),
                         [('Song - Beta','Beta')])
        self.assertEqual(original['operation_id'],next(row['entry']['request']['operation_id'] for saved in self.w.saved
            for row in saved['settings'].get('metadata_receipt_acks',[]) if row['entry']['request']['field']=='artist'))


if __name__=='__main__':unittest.main()
