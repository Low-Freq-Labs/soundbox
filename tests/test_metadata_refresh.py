"""Sparse ACK/refresh ownership and raw derivation on disposable RAM fixtures."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import unittest
from unittest import mock

from PySide6.QtWidgets import QApplication

from soundbox import alias_intents as alias,metadata,metadata_provenance as provenance,source_truth as truth
from tests import test_metadata_controller as fixtures


class MetadataRefresh(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.fixture=fixtures.MetadataPhases('test_artist_alias_and_title_cleanup_are_used_for_the_native_intended_value')
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.w=self.fixture.w;self.c=self.fixture.controller

    def turn(self):self.fixture.pump();self.fixture.deliver()

    def settled(self):
        with mock.patch.object(self.c,'schedule'):
            self.c.timer.stop()
            for worker in list(self.c.tasks._workers):self.assertTrue(worker.wait(30000))
            for _ in range(20):
                self.app.processEvents();self.c.timer.stop()
                if not self.c.tasks.pending:break
        self.assertFalse(self.c.tasks.pending)

    def context(self):
        tracks=self.w.lib.tracks_by_id.values()
        bases=[metadata.derive_artist(track.name,track.artist)[0] for track in tracks]
        trusted=[track.artist for track in self.w.lib.tracks_by_id.values() if track.artist]+list(self.w.overrides.values())
        return tuple(metadata.freeze_registry(metadata.build_registry(bases,trusted,aliases)) for aliases in (self.w.aliases,{}))

    def publish(self,changes):
        self.assertTrue(self.c.enqueue('local-song',changes))
        while self.c.pending:
            self.assertLess(len(self.fixture.writes),10);self.turn()
        self.w.lib.tracks_by_id={track.track_id:track for track in self.w.lib.tracks_by_id.values()}

    def incoming(self,**changes):
        track=self.w.lib.tracks_by_id['local-song']
        return replace(self.w.lib,tracks_by_id={'local-song':replace(track,**changes)})

    def origin(self):return next(iter(self.w.settings[alias.ORIGINS].values()))

    def test_own_ack_retains_raw_recipe_and_verified_refresh_retires_only_fulfilled_visible_maps(self):
        self.publish({'title':'Manual Title','artist':'Manual Artist'})
        raw=self.origin()['provenance']['raw']
        self.assertEqual(raw,dict(title='Original Title',artist='Original Artist'))
        token=self.c.stage_native_refresh(self.w.lib,changed_ids=['local-song'])
        self.assertIsNone(token);self.assertEqual((self.w.titles,self.w.overrides),({},{'local-song':'Manual Artist'}))
        self.assertEqual(provenance.manual(self.origin(),'title'),'Manual Title')
        self.assertEqual(provenance.manual(self.origin(),'artist'),'Manual Artist')
        self.assertTrue(self.w._save_state())
        self.assertIsNone(self.c.stage_native_refresh(self.w.lib,changed_ids=['local-song']))

    def test_independent_native_change_supersedes_fulfilled_manual_base_across_restart(self):
        self.publish({'title':'Manual Title','artist':'Manual Artist'})
        incoming=self.incoming(name='Native Title',artist='Native Artist')
        self.assertIsNotNone(self.c.stage_native_refresh(incoming,changed_ids=['local-song']))
        self.assertEqual((self.w.titles,self.w.overrides),({},{}))
        saved=json.loads(json.dumps(self.w.settings));origin=next(iter(saved[alias.ORIGINS].values()))
        self.assertEqual(origin['provenance']['raw'],dict(title='Native Title',artist='Native Artist'))
        self.assertIsNone(provenance.manual(origin,'title'));self.assertIsNone(provenance.manual(origin,'artist'))
        # Returning to an old spelling cannot resurrect the fulfilled mask.
        result,_,_=provenance.reconcile(origin,title='Manual Title',artist='Manual Artist',titles={},artists={})
        self.assertIsNone(provenance.manual(result,'title'));self.assertIsNone(provenance.manual(result,'artist'))

    def test_same_spelling_later_local_gesture_has_new_ownership_and_is_never_retired_by_old_ack(self):
        self.publish({'title':'Manual Title'})
        old=self.origin();self.assertTrue(self.c.enqueue('local-song',{'title':'Manual Title'}))
        origin=self.origin();pending=deepcopy(self.c.pending)
        incoming=self.incoming(name='Independent Title')
        self.assertIsNotNone(self.c.stage_native_refresh(incoming,changed_ids=['local-song']))
        self.assertEqual(self.w.titles,{'local-song':'Manual Title'})
        self.assertEqual(self.c.pending,pending)
        self.assertNotEqual(old['provenance']['edits']['title'],origin['provenance']['edits']['title'])

    def test_pending_and_failed_ack_preserve_exact_request_and_curation(self):
        self.assertTrue(self.c.enqueue('local-song',{'title':'Manual Title'}));self.turn()
        request=deepcopy(self.c.pending[0]);self.fixture.pump();self.w.save_ok=False;self.fixture.deliver()
        self.assertTrue(self.c.pending[0]['error'])
        self.assertEqual(self.c.pending[0]['request'],request['request'])
        self.assertEqual(self.origin()['provenance']['acks'],{})
        self.assertIsNone(self.c.stage_native_refresh(self.incoming(name='Manual Title'),changed_ids=['local-song']))
        self.assertEqual(self.w.titles,{'local-song':'Manual Title'})

    def test_refresh_save_rollback_restores_exact_owners(self):
        self.publish({'title':'Manual Title'})
        owners=(self.w.settings,self.w.titles,self.w.overrides)
        token=self.c.stage_native_refresh(self.incoming(name='Independent Title'),changed_ids=['local-song'])
        self.assertIsNotNone(token);self.w.save_ok=False;self.assertFalse(self.w._save_state())
        self.c.rollback_source_scope(token)
        self.assertTrue(all(current is before for current,before in zip(
            (self.w.settings,self.w.titles,self.w.overrides),owners)))

    def test_unverified_wrong_source_and_replaced_uuid_cannot_retire_maps(self):
        self.publish({'title':'Manual Title'})
        before=(self.w.settings,self.w.titles,self.w.overrides)
        incoming=self.incoming(name='Native Title')
        self.assertIsNone(self.c.stage_native_refresh(incoming,changed_ids=['local-song'],verified=False))
        self.assertIsNone(self.c.stage_native_refresh(replace(incoming,source='/other/master.db'),changed_ids=['local-song']))
        track=incoming.tracks_by_id['local-song'];track.source_uuid='00000000-0000-4000-8000-000000000099'
        self.assertIsNone(self.c.stage_native_refresh(incoming,changed_ids=['local-song']))
        self.assertEqual((self.w.settings,self.w.titles,self.w.overrides),before)

    def test_noop_refresh_does_not_walk_library_origins_or_derive(self):
        class NoScan(dict):
            def values(self):raise AssertionError('Full library scan')
            def items(self):raise AssertionError('Full library scan')
            def __iter__(self):raise AssertionError('Full library scan')
        self.w.lib.tracks_by_id=NoScan(self.w.lib.tracks_by_id)
        with mock.patch.object(provenance,'reconcile',side_effect=AssertionError('No-op derivation')):
            self.assertIsNone(self.c.stage_native_refresh(self.w.lib,changed_ids=[]))
        self.assertFalse(self.w.tag_controller.admitted);self.assertFalse(self.w.saved)

    def test_ack_is_sparse_and_later_same_value_token_is_not_claimed(self):
        self.assertTrue(self.c.enqueue('local-song',{'title':'Manual Title'}))
        job=deepcopy(self.c.pending[0]);origin=self.origin()
        later=provenance.edit(origin,('title',));before=deepcopy(later)
        self.assertIs(provenance.acknowledge(later,job),later);self.assertEqual(later,before)
        with mock.patch('soundbox.metadata.derive_artist',side_effect=AssertionError('ACK derivation')):
            result=provenance.acknowledge(origin,job)
        self.assertEqual(result['provenance']['acks']['title']['operation'],job['id'])

    def test_fulfilled_manual_artist_remains_alias_unmerge_base_without_visible_override(self):
        self.publish({'artist':'Manual Artist'})
        self.c.stage_native_refresh(self.w.lib,changed_ids=['local-song'])
        raw=alias.capture(self.w.lib,titles=self.w.titles,artists=self.w.overrides,
            aliases={'Manual Artist':'Canonical'},settings=self.w.settings,visible={},owned={},previous={})
        plan,targets,_=alias.build(raw)
        self.assertEqual(targets[plan['order'][0]]['artist'],'Canonical')
        raw=json.loads(raw);raw['payload']['aliases']={}
        plan,targets,_=alias.build(truth._encode(raw))
        self.assertEqual(targets[plan['order'][0]]['artist'],'Manual Artist')

    def test_raw_title_recipe_restores_policy_words_after_own_publication_and_json_restart(self):
        track=self.w.lib.tracks_by_id['local-song'];track.name='Alpha - Song (Original Mix)';track.artist='Alpha'
        self.fixture.native.update(title=track.name,artist=track.artist)
        self.publish({'title':''});self.assertEqual(self.fixture.native['title'],'Song')
        self.c.stage_native_refresh(self.w.lib,changed_ids=['local-song'])
        self.w.settings=json.loads(json.dumps(self.w.settings))
        self.assertTrue(self.c.change_title_policy({'title_rules_off':['drop']}))
        self.c._resume_alias_build();self.settled()
        rows=list(self.w.settings[alias.TARGETS].values());self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['title'],'Song (Original Mix)')
        self.assertEqual(rows[0]['fields'],['title'])

    def test_policy_save_failure_restores_settings_maps_and_original_recipe(self):
        self.publish({'title':'Manual Title'})
        owners=self.w.settings,self.w.titles,self.w.overrides
        self.w.save_ok=False
        self.assertFalse(self.c.change_title_policy({'title_words':['Original']}))
        self.assertTrue(all(a is b for a,b in zip((self.w.settings,self.w.titles,self.w.overrides),owners)))
        self.assertNotIn(alias.NEXT,self.w.settings)

    def test_fresh_native_row_derives_on_worker_then_durably_stages_both_fields_without_sql(self):
        track=self.w.lib.tracks_by_id['local-song'];track.name='Alpha - Song';track.artist=''
        self.fixture.native.update(title=track.name,artist='')
        self.assertTrue(self.c.publish_derived(self.w.lib,changed_ids=['local-song'],context=self.context()))
        self.assertTrue(self.c._resume_derived());self.assertFalse(self.w.tag_controller.admitted)
        self.settled();plan=self.w.settings[alias.NEXT];targets=self.w.settings[alias.TARGETS]
        self.assertEqual(plan['state'],'ready');row=targets[plan['plan']['order'][0]]
        self.assertEqual((row['title'],row['artist'],row['fields']),('Song','Alpha',['title','artist']))
        self.assertEqual(self.w.saved[-1]['settings'][alias.TARGETS],targets)
        self.assertFalse(self.fixture.writes);self.assertFalse(self.c._derived_dirty)

    def test_continuous_noop_performs_no_registry_build_derivation_state_save_or_source_operation(self):
        context=self.context()
        with mock.patch.object(metadata,'freeze_registry',side_effect=AssertionError('No-op registry')):
            self.assertFalse(self.c.publish_derived(self.w.lib,changed_ids=[],context=context))
            self.assertFalse(self.c._resume_derived())
        self.assertFalse(self.w.saved);self.assertFalse(self.w.tag_controller.admitted)

    def test_later_local_edit_and_native_replacement_during_dirty_build_do_not_get_overwritten(self):
        track=self.w.lib.tracks_by_id['local-song'];track.name='Alpha - Song';track.artist=''
        for replacement in (False,True):
            with self.subTest(replacement=replacement):
                self.c.publish_derived(self.w.lib,changed_ids=['local-song'],context=self.context())
                self.assertTrue(self.c._resume_derived())
                if replacement:
                    self.w.lib=self.incoming(source_uuid='00000000-0000-4000-8000-000000000099',name='Independent')
                else:self.w.overrides={'local-song':'Later Manual'}
                self.settled();self.assertNotIn(alias.NEXT,self.w.settings)
                self.assertTrue(self.c._derived_dirty)
                self.w.overrides={}

    def test_sparse_refresh_copies_large_maps_at_most_once_and_does_not_scan_track_inventory(self):
        self.publish({'title':'Manual Title','artist':'Manual Artist'})
        original=self.origin();track=self.w.lib.tracks_by_id['local-song'];scope=truth.scope(self.w.lib)
        origins={};tracks={};titles={str(i):'Unrelated' for i in range(4000)};artists=dict(titles)
        for i in range(100):
            local='dirty'+str(i);new=replace(track,track_id=local,source_native_id=str(i+10),
                name='Independent Title',artist='Independent Artist',
                source_uuid=f'00000000-0000-4000-8000-{i+10:012d}')
            row={**deepcopy(original),'local_id':local,'native_id':new.native_id,'uuid':new.source_uuid}
            origins[alias.key(scope,row)]=row;tracks[local]=new;titles[local]='Manual Title';artists[local]='Manual Artist'
        self.w.titles=titles;self.w.overrides=artists
        self.w.settings[alias.ORIGINS]=origins
        self.w.lib=replace(self.w.lib,tracks_by_id=tracks)
        with (mock.patch.object(provenance,'reconcile',wraps=provenance.reconcile) as reconcile,
              mock.patch.object(provenance,'copy_values',wraps=provenance.copy_values) as copies,
              mock.patch.object(self.w,'_title_junk',wraps=self.w._title_junk) as junk):
            self.assertIsNotNone(self.c.stage_native_refresh(self.w.lib,changed_ids=list(tracks)))
            self.assertEqual(reconcile.call_count,100)
            self.assertEqual(copies.call_count,2)
            self.assertEqual(junk.call_count,1)
        self.assertEqual(len(self.w.titles),4000);self.assertEqual(len(self.w.overrides),4000)

    def test_generated_title_shield_without_title_write_yields_to_independent_native_title(self):
        track=self.w.lib.tracks_by_id['local-song'];track.name='Song - Beta';track.artist='Alpha'
        self.fixture.native.update(title=track.name,artist=track.artist)
        self.publish({'artist':'Beta'})
        self.assertEqual(self.w.titles,{'local-song':'Song - Beta'})
        self.assertNotIn('title',self.origin()['provenance']['acks'])
        self.assertIsNotNone(self.c.stage_native_refresh(self.incoming(name='Independent'),changed_ids=['local-song']))
        self.assertEqual(self.w.titles,{})
        self.assertEqual(self.origin()['provenance']['raw']['title'],'Independent')

    def test_manual_native_exact_title_becomes_generated_shield_then_yields_to_independent_change(self):
        self.publish({'title':'Original Artist - Deliberate Title'})
        self.assertEqual(self.w.titles,{'local-song':'Original Artist - Deliberate Title'})
        self.assertEqual(self.origin()['title_shield'],'Original Artist - Deliberate Title')
        self.c.stage_native_refresh(self.incoming(name='Independent'),changed_ids=['local-song'])
        self.assertEqual(self.w.titles,{});self.assertEqual(self.origin()['title_shield'],'')

    def test_every_later_independent_native_change_updates_raw_recipe_without_resurrecting_manual_input(self):
        self.publish({'title':'Manual Title','artist':'Manual Artist'})
        for title,artist in (('First Native','First Artist'),('Second Native','Second Artist')):
            self.c.stage_native_refresh(self.incoming(name=title,artist=artist),changed_ids=['local-song'])
            self.assertEqual(self.origin()['provenance']['raw'],dict(title=title,artist=artist))
            self.assertIsNone(provenance.manual(self.origin(),'title'))

    def test_late_track_info_policy_and_global_context_changes_requeue_without_stale_plan(self):
        track=self.w.lib.tracks_by_id['local-song'];track.name='Alpha - Song';track.artist=''
        for change in ('info','policy','context'):
            with self.subTest(change=change):
                self.w.settings={};self.c._derived_dirty={};context=self.context()
                self.c.publish_derived(self.w.lib,changed_ids=['local-song'],context=context)
                self.assertTrue(self.c._resume_derived())
                if change=='info':self.w.settings['track_info']={'local-song':{'artist':'Later Info'}}
                elif change=='policy':self.w.settings['title_words']=['Song']
                else:self.c.publish_derived(self.w.lib,changed_ids=['other'],context=self.context())
                self.settled();self.assertNotIn(alias.NEXT,self.w.settings)
                self.assertTrue(self.c.planning_pending)

    def test_unavailable_identity_becomes_durable_unresolved_instead_of_stranded_ram_planning(self):
        self.w.lib.tracks_by_id['local-song'].source_uuid=''
        self.c.publish_derived(self.w.lib,changed_ids=['local-song'],context=self.context())
        self.assertTrue(self.c._resume_derived());self.settled()
        self.assertFalse(self.c.planning_pending);self.assertTrue(self.c.migration_pending)
        descriptor=self.w.settings[alias.NEXT]['plan']
        self.assertEqual(descriptor['order'],[]);self.assertEqual(len(descriptor['unresolved']),1)
        self.assertTrue(self.w.settings[alias.TARGETS][descriptor['unresolved'][0]]['error'])
        self.assertFalse(self.w.tag_controller.admitted)

    def test_unregistered_local_import_keeps_registration_owner_without_spurious_unresolved_plan(self):
        track=self.w.lib.tracks_by_id['local-song'];track.source_uuid='';track.imported=True
        self.w.settings['native_import_jobs']=[{'id':'registration-owner'}]
        before=deepcopy(self.w.settings)
        self.c.publish_derived(self.w.lib,changed_ids=['local-song'],context=self.context())
        self.assertFalse(self.c._resume_derived());self.assertFalse(self.c.planning_pending)
        self.assertEqual(self.w.settings,before);self.assertNotIn(alias.NEXT,self.w.settings)

    def test_continuous_failed_save_retains_dirty_intent_and_retries_same_immutable_inputs(self):
        track=self.w.lib.tracks_by_id['local-song'];track.name='Alpha - Song';track.artist=''
        self.c.publish_derived(self.w.lib,changed_ids=['local-song'],context=self.context())
        before=self.w.settings;self.w.save_ok=False
        self.assertTrue(self.c._resume_derived());self.settled()
        self.assertIs(self.w.settings,before);self.assertTrue(self.c.planning_pending)
        self.assertFalse(self.w.tag_controller.admitted)
        self.w.save_ok=True;self.assertTrue(self.c._resume_derived());self.settled()
        self.assertFalse(self.c.planning_pending);self.assertIn(alias.NEXT,self.w.settings)

    def test_ack_and_noop_refresh_do_not_derive_walk_library_or_touch_source_after_publication(self):
        self.assertTrue(self.c.enqueue('local-song',{'title':'Manual Title'}));self.turn()
        with (mock.patch.object(metadata,'derive_artist',side_effect=AssertionError('ACK derivation')),
              mock.patch.object(metadata,'build_registry',side_effect=AssertionError('ACK registry')),
              mock.patch.object(provenance,'copy_values',wraps=provenance.copy_values) as copies):
            self.turn();self.assertEqual(copies.call_count,1)
        operations=len(self.w.tag_controller.admitted);writes=len(self.fixture.writes)
        self.assertFalse(self.c.publish_derived(self.w.lib,changed_ids=[],context=self.context()))
        self.assertEqual(len(self.w.tag_controller.admitted),operations);self.assertEqual(len(self.fixture.writes),writes)

    def test_raw_name_remains_untrusted_after_publication_and_later_genuine_trust_can_correct_it(self):
        track=self.w.lib.tracks_by_id['local-song'];track.name='Song - ZONDO';track.artist=''
        scope=truth.scope(self.w.lib);identity=alias.key(scope,dict(local_id=track.track_id,
            native_id=track.native_id,uuid=track.source_uuid,path=str(track.path)))
        raw=alias.capture(self.w.lib,titles={},artists={},aliases={},settings={},visible={},owned={},previous={})
        _,targets,origins=alias.build(raw);origin=origins[identity]
        self.assertEqual((targets[identity]['title'],targets[identity]['artist']),('ZONDO','Song'))
        published=replace(track,name='ZONDO',artist='Song')
        origin={**origin,'native_title':'ZONDO','native_artist':'Song'}
        self.assertEqual(provenance.display_registry_inputs(published,scope,origin),('Song - ZONDO','',None))
        registry=metadata.freeze_registry(metadata.build_registry(['Song','ZONDO'],['ZONDO']))
        row=dict(scope=scope,local_id=track.track_id,native_id=track.native_id,uuid=track.source_uuid,
            path=str(track.path),name='ZONDO',artist='Song',stem='song')
        title,artist,_shown,updated=provenance.derive(row,origin=origin,title_override=None,artist_override=None,
            info={},context=(registry,registry),junk=None)
        self.assertEqual((title,artist),('Song','ZONDO'));self.assertEqual(updated['automatic'],'ZONDO')
        self.assertEqual(updated['provenance']['title_base'],'Song')

    def test_malformed_display_origin_falls_back_to_actual_scalars_while_publisher_refuses_it(self):
        track=self.w.lib.tracks_by_id['local-song'];scope=truth.scope(self.w.lib)
        for origin in ('malformed',{},dict(scope=scope,local_id='local-song',native_id='1',uuid=track.source_uuid,
                path=str(track.path),provenance={'bad':'shape'})):
            with self.subTest(origin=origin):
                self.assertEqual(provenance.display_registry_inputs(track,scope,origin),(track.name,track.artist,None))
                with self.assertRaises(ValueError):alias.validate_origins({'bad':origin})

    def test_companion_repair_preserves_fulfilled_manual_title_base_and_original_request(self):
        self.publish({'title':'Manual Title','artist':'Manual Artist'})
        self.assertTrue(self.c.enqueue('local-song',{'artist':'Later Artist'}));self.turn()
        original=deepcopy(self.c.pending[0]);self.fixture.native['title']='Independent Native'
        self.turn();self.assertTrue(self.c.pending[0]['error'])
        self.assertEqual(self.c.pending[0]['request'],original['request']);self.assertTrue(self.c.retry())
        for _ in range(10):
            if not self.c.pending:break
            self.turn()
        self.assertFalse(self.c.pending);self.assertEqual(self.fixture.native['title'],'Manual Title')
        self.assertEqual(provenance.manual(self.origin(),'title'),'Manual Title')

    def test_editor_credit_does_not_override_acknowledged_manual_artist_in_display_or_export(self):
        track=self.w.lib.tracks_by_id['local-song'];track.name='Song (Other Remix)'
        self.fixture.native['title']=track.name
        self.publish({'artist':'Chosen'})
        fresh=self.w.lib.tracks_by_id['local-song']
        self.assertEqual(self.fixture.native['artist'],'Chosen');self.assertEqual(self.w.overrides,{'local-song':'Chosen'})
        from soundbox import engine
        self.assertEqual(engine.track_row(fresh,self.w.overrides,self.w.titles)['artist'],'Chosen')
        self.assertTrue(self.c.enqueue('local-song',{'artist':''}))
        self.assertEqual(self.c.pending[-1]['value'],'Other')
        while self.c.pending:self.turn()
        self.w.lib.tracks_by_id={track.track_id:track for track in self.w.lib.tracks_by_id.values()}
        self.assertEqual(self.fixture.native['artist'],'Other');self.assertEqual(self.w.overrides,{})
        self.publish({'artist':'Chosen'})
        self.c.stage_native_refresh(self.incoming(artist='Independent Native'),changed_ids=['local-song'])
        self.assertEqual(self.w.overrides,{})

    def test_legacy_track_info_artist_ack_retires_only_its_scalar_and_keeps_virtual_manual_base(self):
        self.w.settings['track_info']={'local-song':{'artist':'Legacy Artist','comments':'Keep'},'other':{'title':'Untouched'}}
        self.publish({'title':'New Title'})
        self.assertEqual(self.fixture.native,dict(title='New Title',artist='Legacy Artist'))
        self.assertEqual(self.w.settings['track_info'],{'local-song':{'comments':'Keep'},'other':{'title':'Untouched'}})
        self.assertEqual(provenance.manual(self.origin(),'artist'),'Legacy Artist')
        self.c.stage_native_refresh(self.incoming(artist='Independent'),changed_ids=['local-song'])
        self.assertIsNone(provenance.manual(self.origin(),'artist'))

    def test_acknowledged_legacy_artist_overlay_converts_to_exact_native_map_only_when_needed(self):
        track=self.w.lib.tracks_by_id['local-song'];track.name='Song (Other Remix)'
        self.fixture.native['title']=track.name
        self.w.settings['track_info']={'local-song':{'artist':'Legacy Chosen','comments':'Keep'}}
        self.w.overrides={'local-song':'Older Hidden Override'}
        self.publish({'title':track.name})
        self.assertEqual(self.w.settings['track_info'],{'local-song':{'comments':'Keep'}})
        self.assertEqual(self.w.overrides,{'local-song':'Legacy Chosen'})
        self.assertEqual(provenance.manual(self.origin(),'artist'),'Legacy Chosen')
        self.c.stage_native_refresh(self.incoming(artist='Independent'),changed_ids=['local-song'])
        self.assertEqual(self.w.overrides,{})

    def test_failed_overlay_ack_and_newer_overlay_preserve_exact_owners_and_prepared_request(self):
        self.w.settings['track_info']={'local-song':{'artist':'Legacy Artist','comments':'Keep'}}
        self.assertTrue(self.c.enqueue('local-song',{'title':'New Title'}))
        self.turn();self.turn();self.turn()
        self.assertEqual(self.c.pending[0]['field'],'artist');request=deepcopy(self.c.pending[0]['request'])
        owners=self.w.settings['track_info'],self.w.overrides,self.w.titles
        self.fixture.pump();self.w.save_ok=False;self.fixture.deliver()
        self.assertIs(self.w.settings['track_info'],owners[0]);self.assertIs(self.w.overrides,owners[1])
        self.assertIs(self.w.titles,owners[2]);self.assertEqual(self.c.pending[0]['request'],request)
        self.w.save_ok=True;self.w.settings['track_info']={'local-song':{'artist':'Newer Local','comments':'Keep'}}
        self.assertTrue(self.c.retry())
        for _ in range(4):
            if not self.c.pending:break
            self.turn()
        self.assertEqual(self.w.settings['track_info']['local-song']['artist'],'Newer Local')

    def test_one_ack_carries_validated_owner_only_after_validating_changed_row(self):
        self.assertTrue(self.c.enqueue('local-song',{'title':'Manual Title'}))
        original=self.origin();origins=dict(self.w.settings[alias.ORIGINS])
        for index in range(50):
            row={**deepcopy(original),'local_id':str(index+10),'native_id':str(index+10),
                 'uuid':f'00000000-0000-4000-8000-{index+10:012d}'}
            origins[alias.key(row['scope'],row)]=row
        self.w.settings[alias.ORIGINS]=origins;self.turn()
        self.fixture.pump()
        with mock.patch.object(alias,'validate_origins',wraps=alias.validate_origins) as validation:
            self.fixture.deliver()
            self.assertEqual([len(call.args[0]) for call in validation.call_args_list],[1])
            self.assertFalse(self.c.queue_error());self.assertEqual(validation.call_count,1)
        corrupt={**self.w.settings[alias.ORIGINS],'bad':'malformed'}
        self.w.settings[alias.ORIGINS]=corrupt
        self.assertTrue(self.c.queue_error());self.assertIsNot(self.c._validated_origins,corrupt)

    def test_legacy_title_companion_ack_retires_only_its_overlay_and_keeps_reset_provenance(self):
        self.w.settings['track_info']={'local-song':{'title':'Legacy Title','comments':'Keep'}}
        self.publish({'artist':'Chosen'})
        self.assertEqual(self.fixture.native,dict(title='Legacy Title',artist='Chosen'))
        self.assertEqual(self.w.settings['track_info'],{'local-song':{'comments':'Keep'}})
        self.assertEqual(provenance.manual(self.origin(),'title'),'Legacy Title')
        self.assertEqual(self.w.titles,{})

    def test_gui_ack_uses_worker_selected_pin_without_scanning_loaded_library(self):
        self.assertTrue(self.c.enqueue('local-song',{'title':'Manual Title'}));self.turn();self.fixture.pump()
        kind,work,completed=self.w.tag_controller.admitted[-1]
        class NoGuiScan(dict):
            def values(self):raise AssertionError('GUI scanned full library')
            def items(self):raise AssertionError('GUI scanned full library')
            def __iter__(self):raise AssertionError('GUI scanned full library')
        def completed_readback():
            result=work();result['library'].tracks_by_id=NoGuiScan(result['library'].tracks_by_id)
            return result
        self.w.tag_controller.admitted[-1]=(kind,completed_readback,completed)
        self.fixture.deliver();self.assertFalse(self.c.pending)
        self.assertEqual(self.fixture.native['title'],'Manual Title')

    def test_manual_unknown_artist_hold_survives_trusted_title_rescue_and_future_context_change(self):
        from soundbox import engine
        track=self.w.lib.tracks_by_id['local-song'];track.name='SONNY tune';track.artist=''
        self.fixture.native.update(title=track.name,artist='')
        self.w.lib.artist_registry=metadata.build_registry(['SONNY'],['SONNY'])
        self.publish({'artist':metadata.UNKNOWN_ARTIST})
        self.assertEqual(self.fixture.native['artist'],'')
        self.assertEqual(self.w.overrides,{'local-song':metadata.UNKNOWN_ARTIST})
        track=self.w.lib.tracks_by_id['local-song']
        registry=metadata.build_registry(['SONNY','tune'],['SONNY','tune'])
        self.assertEqual(engine.track_row(track,self.w.overrides,self.w.titles,registry=registry)['artist'],metadata.UNKNOWN_ARTIST)
        self.c.stage_native_refresh(self.incoming(artist='SONNY'),changed_ids=['local-song'])
        self.assertEqual(self.w.overrides,{})

    def test_default_readback_skips_media_resolution_and_reuses_only_verified_canonical_scalars(self):
        previous=self.w.lib;track=previous.tracks_by_id['local-song']
        track.source_native_path='/native/raw.wav';track.location_uri='file:///canonical/song.wav'
        fresh_track=replace(track,name='Fresh Native Title',location_uri='file:///native/raw.wav',info={'beats':[1,2,3]})
        beats=fresh_track.info['beats']
        fresh=replace(previous,tracks_by_id={'1':fresh_track})
        self.c.loader=None
        with (mock.patch('soundbox.rekordbox_db.load_live',return_value=fresh) as load,
              mock.patch('pathlib.Path.stat',side_effect=AssertionError('Media stat')),
              mock.patch('pathlib.Path.resolve',side_effect=AssertionError('Media resolution'))):
            result=self.c._readback(previous)
        load.assert_called_once_with(Path(previous.source),resolve_media_paths=False)
        self.assertIs(result,fresh);self.assertIs(result.tracks_by_id['1'],fresh_track)
        self.assertEqual(fresh_track.location_uri,'file:///canonical/song.wav')
        self.assertEqual(fresh_track.source_native_path,'/native/raw.wav')
        self.assertEqual(fresh_track.name,'Fresh Native Title');self.assertIs(fresh_track.info['beats'],beats)
        self.assertEqual(track.name,'Original Title')

    def test_rapid_title_policy_then_reset_persists_latest_raw_policy_before_builder_finishes(self):
        self.w._title_junk=lambda:metadata.junk_patterns(self.w.settings.get('title_rules_off'),self.w.settings.get('title_words',[]))
        track=self.w.lib.tracks_by_id['local-song'];track.name='Alpha - Song (Original Mix)';track.artist='Alpha'
        self.fixture.native.update(title=track.name,artist=track.artist)
        self.publish({'title':'Manual Title'})
        admitted=len(self.w.tag_controller.admitted)
        self.assertTrue(self.c.change_title_policy({'title_rules_off':['drop']}))
        self.assertEqual(self.w.settings[alias.NEXT]['state'],'building')
        self.assertTrue(self.c.enqueue('local-song',{'title':''}))
        self.assertEqual([(job['field'],job['value']) for job in self.c.pending],[('title','Song (Original Mix)')])
        self.assertEqual(self.w.saved[-1]['settings']['pending_metadata_edits'],self.c.pending)
        self.assertIsNone(self.c.pending[0]['request']);self.assertEqual(len(self.w.tag_controller.admitted),admitted)
        self.assertEqual(self.fixture.native['title'],'Manual Title')

    def test_malformed_relevant_refresh_origin_raises_attention_without_mutating_any_owner(self):
        self.publish({'title':'Manual Title'})
        identity=next(iter(self.w.settings[alias.ORIGINS]));self.w.settings[alias.ORIGINS]={identity:'bad'}
        owners=self.w.settings,self.w.titles,self.w.overrides
        with self.assertRaises(ValueError):self.c.stage_native_refresh(self.w.lib,changed_ids=['local-song'])
        self.assertTrue(all(a is b for a,b in zip((self.w.settings,self.w.titles,self.w.overrides),owners)))
