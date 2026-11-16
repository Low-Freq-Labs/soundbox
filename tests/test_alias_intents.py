"""Offline frozen artist generations with fakes and encrypted TEMP source stores."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtWidgets import QApplication

from soundbox import alias_intents as alias,engine,metadata,source_truth as truth
from soundbox.library import Library,Track
from tests import test_metadata_controller as fixtures
from tests import test_rekordbox_metadata_store as native_fixtures
from desktop.metadata_controller import MetadataController


def library(count=1):
    return Library({str(i):Track(str(i),'Alpha - Song '+str(i),'',f'file:///synthetic/{i}.wav',
        source_uuid=f'00000000-0000-4000-8000-{i:012d}') for i in range(1,count+1)},[],
        Path('/synthetic/master.db'),source_kind='rekordbox-db',source_library_id='123')


class AliasPlanner(unittest.TestCase):
    def capture(self,lib,aliases,*,settings=None,artists=None,previous=None,owned=None):
        return alias.capture(lib,titles={},artists=artists or {},aliases=aliases,settings=settings or {},
                             visible={},owned=owned or {},previous=previous or {})

    def test_unaliased_automatic_artist_survives_native_title_cleanup_chain_unmerge_and_restart(self):
        lib=library();plan,targets,origins=alias.build(self.capture(lib,{'Alpha':'Beta','Beta':'Gamma'}))
        identity=plan['order'][0];self.assertEqual(targets[identity]['artist'],'Gamma')
        self.assertEqual(origins[identity]['automatic'],'Alpha')
        # Source publication and a fresh process lose the title prefix entirely.
        track=lib.tracks_by_id['1'];track.name=targets[identity]['title'];track.artist='Gamma'
        origins[identity]['native_artist']='Gamma'
        saved=json.loads(json.dumps({alias.ORIGINS:origins}))
        _,targets,origins=alias.build(self.capture(lib,{'Alpha':'Beta'},settings=saved))
        self.assertEqual(targets[identity]['artist'],'Beta')
        origins[identity]['native_artist']='Beta';track.artist='Beta'
        _,targets,_=alias.build(self.capture(lib,{},settings={alias.ORIGINS:origins}))
        self.assertEqual(targets[identity]['artist'],'Alpha')

    def test_independent_later_native_and_manual_artist_values_establish_current_origin(self):
        lib=library();plan,_,origins=alias.build(self.capture(lib,{'Alpha':'Beta'}));identity=plan['order'][0]
        lib.tracks_by_id['1'].name='Song 1';lib.tracks_by_id['1'].artist='Independent Native'
        _,targets,new=alias.build(self.capture(lib,{},settings={alias.ORIGINS:origins}))
        self.assertEqual(new[identity]['automatic'],'Independent Native')
        self.assertEqual(targets[identity]['artist'],'Independent Native')
        _,targets,_=alias.build(self.capture(lib,{},settings={alias.ORIGINS:new},artists={'1':'New Manual'}))
        self.assertEqual(targets[identity]['artist'],'New Manual')

    def test_large_batch_does_not_expand_into_the_256_job_fifo_and_preserves_unresolved_history(self):
        lib=library(300);missing=replace(lib.tracks_by_id['1'],track_id='missing',source_uuid='')
        lib.tracks_by_id['missing']=missing
        plan,targets,_=alias.build(self.capture(lib,{'Alpha':'Beta'}))
        self.assertEqual(len(plan['order']),300);self.assertEqual(len(plan['unresolved']),1)
        lib.tracks_by_id.pop('missing')
        newer,retained,_=alias.build(self.capture(lib,{'Alpha':'Gamma'},previous=targets))
        self.assertEqual(len(newer['order']),300);self.assertEqual(len(newer['unresolved']),1)
        self.assertEqual(retained[newer['unresolved'][0]]['local_id'],'missing')
        truth.validate(newer,retained)

    def test_own_inflight_artist_can_finish_after_capture_without_dropping_successor(self):
        lib=library();plan,targets,origins=alias.build(self.capture(lib,{'Alpha':'Beta'}));identity=plan['order'][0]
        lib.tracks_by_id['1'].artist='Alpha';origins[identity]['native_artist']='Alpha'
        _,targets,_=alias.build(self.capture(lib,{},settings={alias.ORIGINS:origins},owned={identity:['Beta']}))
        self.assertEqual(targets[identity]['artist'],'Alpha');self.assertIn('artist',targets[identity]['fields'])

    def test_missing_predecessor_keeps_the_registry_trust_that_resolved_its_original_pair(self):
        lib=library(2);lib.tracks_by_id['1'].name='Song - ZONDO'
        lib.tracks_by_id['2'].name='Other Song';lib.tracks_by_id['2'].artist='ZONDO'
        original=json.loads(truth.capture(lib,titles={},artists={},aliases={},settings={}))
        old_plan,old_targets=truth.build(truth._encode(original))
        old=next(row for row in old_targets.values() if row['local_id']=='1')
        self.assertEqual((old['title'],old['artist']),('Song','ZONDO'))
        lib.tracks_by_id.pop('1')
        present={alias.key(truth.scope(lib),track) for track in original['tracks'] if track['local_id']=='2'}
        inputs=alias.missing_inputs(original,present)
        plan,targets,_=alias.build(self.capture(lib,{},previous={'old':dict(source_raw=inputs)}))
        retained=targets[plan['unresolved'][0]]
        self.assertEqual((retained['title'],retained['artist']),(old['title'],old['artist']))
        self.assertEqual(len(plan['unresolved']),1)

    def test_relocation_never_adopts_ambiguous_or_independently_changed_origin(self):
        lib=library();plan,_,origins=alias.build(self.capture(lib,{}));identity=plan['order'][0]
        track=lib.tracks_by_id['1'];original=origins[identity]
        track.location_uri='file:///relocated/song.wav'
        self.assertNotEqual(str(track.path),original['path'])
        for changed in ('title','artist','uuid','duplicate-origin','duplicate-uuid','binding-issue'):
            with self.subTest(changed=changed):
                candidate=replace(track);current=replace(lib,tracks_by_id={'1':candidate})
                saved=deepcopy(origins)
                if changed=='title':candidate.name='Independent title'
                elif changed=='artist':candidate.artist='Independent artist'
                elif changed=='uuid':candidate.source_uuid='00000000-0000-4000-8000-000000000099'
                elif changed=='duplicate-origin':
                    extra={**original,'path':'/second-history/song.wav'};saved[alias.key(extra['scope'],extra)]=extra
                elif changed=='duplicate-uuid':current.tracks_by_id['2']=replace(candidate,track_id='2',source_native_id='2')
                else:current.source_binding_issues={'1':'Ambiguous binding'}
                self.assertEqual(alias.library_origins(current,saved),saved)

    def test_older_builder_cannot_resurrect_an_origin_moved_by_a_newer_gesture(self):
        lib=library();_,_,captured=alias.build(self.capture(lib,{}))
        lib.tracks_by_id['1'].location_uri='file:///relocated/song.wav'
        current=alias.library_origins(lib,captured)
        self.assertNotEqual(set(current),set(captured))
        self.assertEqual(alias.merge_origins(captured,captured,current),current)

    def test_older_frozen_inputs_without_origin_artist_map_remain_readable(self):
        raw=self.capture(library(),{'Alpha':'Beta'});older=json.loads(raw);older.pop('origin_artists')
        self.assertEqual(alias.build(raw,migration_id='d'*32),
                         alias.build(truth._encode(older),migration_id='d'*32))

    def test_retained_old_uuid_never_adds_or_removes_a_title_shield_on_its_replacement(self):
        lib=library();track=lib.tracks_by_id['1'];track.name='Song - Beta';track.artist='Alpha'
        identity=alias.key(truth.scope(lib),dict(local_id='1',native_id='1',uuid=track.source_uuid,path=str(track.path)))
        raw=alias.capture(lib,titles={},artists={},aliases={'Alpha':'Beta'},settings={},
            visible={identity:dict(title=track.name,artist=track.artist)},owned={},previous={})
        _,old_targets,old_origins=alias.build(raw);old_origins[identity]['title_shield']='Song - Beta'
        replacement=replace(track,source_uuid='00000000-0000-4000-8000-000000000099',
                            name='Independent Current',artist='Beta')
        current=replace(lib,tracks_by_id={'1':replacement})
        plan,targets,origins=alias.build(self.capture(current,{},settings={alias.ORIGINS:old_origins},previous=old_targets))
        self.assertIn(identity,plan['unresolved']);before=deepcopy(targets)
        for error in (targets[identity]['error'],''):
            for titles in ({},{'1':'Current explicit title'}):
                with self.subTest(error=error,titles=titles):
                    targets[identity]['error']=error
                    protected,retained=alias.protect_titles(targets,titles,origins,
                        current_bindings=alias.title_bindings(current))
                    self.assertEqual(protected,titles)
                    self.assertEqual(retained[identity]['title_shield'],'Song - Beta')
        targets[identity]['error']=before[identity]['error'];self.assertEqual(targets,before)


class AliasController(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.fixture=fixtures.MetadataPhases('test_artist_alias_and_title_cleanup_are_used_for_the_native_intended_value')
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.w=self.fixture.w;self.c=self.fixture.controller
        track=self.w.lib.tracks_by_id['local-song'];track.name='Alpha - Song';track.artist=''
        self.fixture.native.update(title=track.name,artist='')

    def build(self):
        with mock.patch.object(self.c,'schedule'):
            self.c.timer.stop();self.c._resume_alias_build()
            for worker in list(self.c.tasks._workers):self.assertTrue(worker.wait(5000))
            for _ in range(10):
                self.c.timer.stop();self.app.processEvents()
                if not self.c.tasks.pending:break
            self.c.timer.stop();self.assertEqual(self.c.tasks.pending,0)

    def turn(self):self.fixture.pump();self.fixture.deliver()

    def publish_manual_pair(self):
        self.assertTrue(self.c.enqueue('local-song',{'title':'Manual Title','artist':'Manual Artist'}))
        for _ in range(4):self.turn()
        self.w.lib.tracks_by_id={track.track_id:track for track in self.w.lib.tracks_by_id.values()}
        self.assertFalse(self.c.pending)

    def test_both_resets_follow_one_exact_acknowledged_relocation_after_json_restart(self):
        self.publish_manual_pair();old_key=next(iter(self.w.settings[alias.ORIGINS]))
        self.w.lib.tracks_by_id['local-song'].location_uri='file:///relocated/song.wav'
        self.c.stop();self.w.settings=json.loads(json.dumps(self.w.settings))
        self.c=MetadataController(self.w,store_factory=self.fixture.controller.factory,loader=self.fixture.load)
        self.addCleanup(self.c.stop)
        self.assertTrue(self.c.enqueue('local-song',{'title':'','artist':''}))
        self.assertEqual([(job['field'],job['value']) for job in self.c.pending],[('title','Song'),('artist','Alpha')])
        self.assertNotIn(old_key,self.w.settings[alias.ORIGINS]);self.assertEqual(len(self.w.settings[alias.ORIGINS]),1)
        origin=next(iter(self.w.settings[alias.ORIGINS].values()))
        self.assertEqual(origin['path'],'/relocated/song.wav')
        self.assertEqual(self.w.saved[-1]['settings'][alias.ORIGINS],self.w.settings[alias.ORIGINS])

    def test_relocation_moves_only_origin_and_never_a_prepared_request_or_operation(self):
        self.publish_manual_pair();self.assertTrue(self.c.enqueue('local-song',{'title':'Later Title'}));self.turn()
        original=deepcopy(self.c.pending[0]);self.assertIsNotNone(original['request'])
        self.w.lib.tracks_by_id['local-song'].location_uri='file:///relocated/song.wav'
        self.assertTrue(self.c.enqueue('local-song',{'artist':''}))
        self.assertEqual(self.c.pending[0],original)
        self.assertEqual(self.c.pending[0]['request']['path'],'/synthetic/song.wav')
        self.assertEqual([row['path'] for row in self.w.settings[alias.ORIGINS].values()],['/relocated/song.wav'])

    def test_failed_relocation_gesture_preserves_old_origin_and_exact_state_owners(self):
        self.publish_manual_pair();self.w.lib.tracks_by_id['local-song'].location_uri='file:///relocated/song.wav'
        owners=(self.w.settings,self.w.titles,self.w.overrides);before=deepcopy(self.w.settings)
        self.w.save_ok=False;self.assertFalse(self.c.enqueue('local-song',{'title':'','artist':''}))
        for actual,expected in zip((self.w.settings,self.w.titles,self.w.overrides),owners):self.assertIs(actual,expected)
        self.assertEqual(self.w.settings,before)

    def test_alias_unmerge_moves_provenance_with_its_atomic_frozen_inputs(self):
        self.assertTrue(self.c.change_alias('Alpha','Beta'));self.build()
        old=next(iter(self.w.settings[alias.ORIGINS].values()))
        # The separately proven source publisher/readback established this pair.
        old={**old,'native_title':'Song','native_artist':'Beta'}
        self.w.settings[alias.ORIGINS]={alias.key(old['scope'],old):old}
        track=self.w.lib.tracks_by_id['local-song'];track.name='Song';track.artist='Beta'
        track.location_uri='file:///relocated/song.wav'
        self.assertTrue(self.c.change_alias('Alpha',None))
        self.assertEqual([row['path'] for row in self.w.settings[alias.ORIGINS].values()],['/relocated/song.wav'])
        self.build();row=next(iter(self.w.settings[alias.TARGETS].values()))
        self.assertEqual((row['title'],row['artist']),('Song','Alpha'))

    def test_completed_old_path_targets_do_not_become_unresolved_after_relocation(self):
        self.assertTrue(self.c.change_alias('Alpha','Beta'));self.build();self.assertTrue(self.c._promote_alias())
        descriptor=self.w.settings[truth.MIGRATION]
        origin=next(iter(self.w.settings[alias.ORIGINS].values()))
        origin={**origin,'native_title':'Song','native_artist':'Beta'}
        self.w.settings[alias.ORIGINS]={alias.key(origin['scope'],origin):origin}
        track=self.w.lib.tracks_by_id['local-song'];track.name='Song';track.artist='Beta'
        track.location_uri='file:///relocated/song.wav'
        before=deepcopy(self.w.settings)
        for acked_active in (False,True):
            with self.subTest(acked_active=acked_active):
                self.w.settings=deepcopy(before);self.w.aliases={'Alpha':'Beta'}
                self.w.settings[truth.MIGRATION]={**descriptor,'cursor':0 if acked_active else len(descriptor['order']),
                    'active':dict(target=descriptor['order'][0],operations={}) if acked_active else None}
                self.assertTrue(self.c.change_alias('Alpha',None));self.build()
                self.assertFalse(self.w.settings[alias.NEXT]['plan']['unresolved'])

    def test_errored_acknowledged_pair_is_retained_if_its_identity_disappears(self):
        self.assertTrue(self.c.change_alias('Alpha','Beta'));self.build();self.assertTrue(self.c._promote_alias())
        descriptor=self.w.settings[truth.MIGRATION];identity=descriptor['order'][0]
        self.w.settings[truth.MIGRATION]={**descriptor,'active':dict(target=identity,operations={}),
                                        'error':'The native metadata pair changed'}
        current=self.w.lib.tracks_by_id['local-song']
        self.w.lib=replace(self.w.lib,tracks_by_id={'local-song':replace(current,
            source_uuid='00000000-0000-4000-8000-000000000099',name='Independent Current',artist='Beta')})
        self.assertTrue(self.c.change_alias('Alpha',None));self.build()
        self.assertIn(identity,self.w.settings[alias.NEXT]['plan']['unresolved'])
        self.assertEqual(self.w.settings[alias.TARGETS][identity]['title'],'Song')

    def test_alias_map_and_frozen_inputs_save_atomically_and_storage_failure_restores_exact_owners(self):
        settings=self.w.settings;aliases=self.w.aliases;self.w.save_ok=False
        self.assertFalse(self.c.change_alias('Alpha','Beta'))
        self.assertIs(self.w.settings,settings);self.assertIs(self.w.aliases,aliases)
        self.w.save_ok=True;self.assertTrue(self.c.change_alias('Alpha','Beta'))
        self.assertEqual(self.w.saved[-1]['settings'],self.w.settings)
        self.assertEqual(self.w.aliases,{'Alpha':'Beta'});self.assertFalse(self.c.pending)
        self.assertTrue(self.c.migration_pending);self.assertFalse(self.w.tag_controller.admitted)

    def test_bulk_aliases_apply_unmerges_first_and_save_one_atomic_successor(self):
        self.w.aliases={'Alpha':'Old','Remove':'Old'};before=self.w.settings;old=self.w.aliases
        self.w.save_ok=False
        self.assertFalse(self.c.change_aliases([('Alpha','Beta'),('Remove','Gamma')],['Remove','Alpha']))
        self.assertIs(self.w.settings,before);self.assertIs(self.w.aliases,old)
        self.w.save_ok=True;saves=len(self.w.saved)
        self.assertTrue(self.c.change_aliases([('Alpha','Beta'),('Remove','Gamma')],['Remove','Alpha']))
        self.assertEqual(self.w.aliases,{'Alpha':'Beta','Remove':'Gamma'})
        self.assertEqual(len(self.w.saved),saves+1);self.assertFalse(self.c.pending)
        self.assertEqual(len(self.w.settings[alias.TARGETS]),1)
        snapshot=deepcopy(self.w.settings)
        self.assertFalse(self.c.change_aliases([('Good','Valid'),('Invalid',None)],[]))
        self.assertEqual(self.w.settings,snapshot)

    def test_accepted_artist_batch_is_atomic_preserves_other_curation_and_rolls_back_exact_owners(self):
        self.w.lib=library(2);self.w.overrides={'retained-missing':'Keep'};self.w.aliases={'Alpha':'Beta'}
        pins=alias.artist_pins(self.w.lib,['1','2']);owners=(self.w.settings,self.w.overrides,self.w.aliases)
        self.w.save_ok=False
        self.assertFalse(self.c.change_artists([('1','First'),('2','Second')],pins))
        for actual,expected in zip((self.w.settings,self.w.overrides,self.w.aliases),owners):self.assertIs(actual,expected)
        self.w.save_ok=True;saves=len(self.w.saved)
        self.assertTrue(self.c.change_artists([('1','First'),('2','Second')],pins))
        self.assertEqual(self.w.overrides,{'retained-missing':'Keep','1':'First','2':'Second'})
        self.assertEqual(self.w.aliases,{'Alpha':'Beta'});self.assertEqual(len(self.w.saved),saves+1)
        self.assertFalse(self.c.pending);self.assertIn(alias.NEXT,self.w.settings)

    def test_artist_acceptance_rejects_every_stale_identity_atomically_but_allows_same_identity_refresh(self):
        original=self.w.lib;track=original.tracks_by_id['local-song'];pins=alias.artist_pins(original,['local-song'])
        native=replace(track,source_native_path='/synthetic/native.wav')
        cases=[None,replace(original,source=Path('/other/master.db')),
            replace(original,source_library_id='456'),
            replace(original,tracks_by_id={'local-song':replace(track,source_uuid='replacement')}),
            replace(original,tracks_by_id={'local-song':replace(track,source_native_id='2')}),
            replace(original,tracks_by_id={'local-song':replace(track,source_native_path='/changed/native.wav')}),
            replace(original,tracks_by_id={'local-song':track,'other':replace(track,track_id='other',source_native_id='2')}),
            replace(original,source_binding_issues={'local-song':'Unavailable'})]
        for candidate in cases:
            with self.subTest(library=candidate):
                before=deepcopy((self.w.settings,self.w.overrides,self.w.aliases));self.w.lib=candidate
                self.assertFalse(self.c.change_artists([('local-song','Suggested Artist')],pins))
                self.assertEqual((self.w.settings,self.w.overrides,self.w.aliases),before)
        local_lib=replace(original,tracks_by_id={'local-song':native});local_pins=alias.artist_pins(local_lib,['local-song'])
        self.w.lib=replace(local_lib,tracks_by_id={'local-song':replace(native,location_uri='file:///new/local.wav')})
        self.assertFalse(self.c.change_artists([('local-song','Suggested Artist')],local_pins))
        with self.assertRaises(ValueError):alias.artist_pins(None,['local-song'])
        self.w.lib=replace(original,tracks_by_id={'local-song':replace(track)})
        self.assertTrue(self.c.change_artists([('local-song','Suggested Artist')],pins))

    def test_artist_acceptance_never_applies_a_partial_or_uncaptured_subset(self):
        self.w.lib=library(2);pins=alias.artist_pins(self.w.lib,['1']);before=self.w.settings
        self.assertFalse(self.c.change_artists([('1','First'),('2','Uncaptured')],pins))
        self.assertFalse(self.c.change_artists([('1','First'),('1','Conflicting')],pins))
        self.assertIs(self.w.settings,before);self.assertEqual(self.w.overrides,{})

    def test_artist_acceptance_larger_than_fifo_limit_still_expands_only_one_pair(self):
        self.w.lib=library(300);ids=list(self.w.lib.tracks_by_id);pins=alias.artist_pins(self.w.lib,ids)
        self.assertTrue(self.c.change_artists([(identity,'Accepted Artist') for identity in ids],pins))
        self.assertEqual(len(self.w.overrides),300);self.assertFalse(self.c.pending);self.build()
        self.assertTrue(self.c._promote_alias());self.fixture.pump();self.c.timer.stop()
        self.assertEqual(len(self.c.pending),2);self.assertEqual(len(self.w.settings[truth.MIGRATION]['order']),300)

    def test_artist_acceptance_uses_prechange_trust_for_automatic_origin(self):
        track=self.w.lib.tracks_by_id['local-song'];track.name='A Song Featuring Suggested Artist'
        self.assertEqual(engine.track_row(track)['artist'],metadata.UNKNOWN_ARTIST)
        pins=alias.artist_pins(self.w.lib,['local-song'])
        self.assertTrue(self.c.change_artists([('local-song','Suggested Artist')],pins));self.build()
        origin=next(iter(self.w.settings[alias.ORIGINS].values()))
        self.assertEqual(origin['automatic'],metadata.UNKNOWN_ARTIST)
        self.assertEqual(next(iter(self.w.settings[alias.TARGETS].values()))['artist'],'Suggested Artist')

    def test_new_trusted_artist_changes_other_raw_name_rows_exactly_like_production_engine(self):
        self.w.lib=library(2);first=self.w.lib.tracks_by_id['1'];second=self.w.lib.tracks_by_id['2']
        first.name='Untitled upload';second.name='Song - ZONDO'
        before=metadata.build_registry([metadata.derive_artist(t.name,t.artist)[0] for t in self.w.lib.tracks_by_id.values()])
        self.w.lib.artist_registry=before
        self.assertEqual(engine.track_row(second,registry=before)['artist'],'Song')
        artists={'1':'ZONDO'}
        registry=metadata.build_registry([metadata.derive_artist(t.name,t.artist)[0] for t in self.w.lib.tracks_by_id.values()],
                                         artists.values())
        expected={t.track_id:engine.track_row(t,artists,registry=registry) for t in self.w.lib.tracks_by_id.values()}
        pins=alias.artist_pins(self.w.lib,['1']);self.assertTrue(self.c.change_artists([('1','ZONDO')],pins));self.build()
        for row in self.w.settings[alias.TARGETS].values():
            self.assertEqual((row['title'],row['artist']),
                             (expected[row['local_id']]['title'],expected[row['local_id']]['artist']))
        self.assertEqual((expected['2']['title'],expected['2']['artist']),('Song','ZONDO'))

    def test_accepted_artist_clears_only_its_legacy_overlay_even_if_override_already_matches(self):
        for already_matches in (False,True):
            with self.subTest(already_matches=already_matches):
                self.w.settings={'track_info':{'local-song':{'artist':'Older Artist','comments':'Preserve'},
                    'other':{'artist':'Other artist','rating':'3'}}}
                self.w.overrides={'local-song':'New Accepted'} if already_matches else {}
                pins=alias.artist_pins(self.w.lib,['local-song']);before=self.w.settings
                self.w.save_ok=False;self.assertFalse(self.c.change_artists([('local-song','New Accepted')],pins))
                self.assertIs(self.w.settings,before)
                self.w.save_ok=True;self.assertTrue(self.c.change_artists([('local-song','New Accepted')],pins));self.build()
                self.assertEqual(self.w.settings['track_info'],{'local-song':{'comments':'Preserve'},
                    'other':{'artist':'Other artist','rating':'3'}})
                row=next(row for row in self.w.settings[alias.TARGETS].values() if row['local_id']=='local-song')
                self.assertEqual(row['artist'],'New Accepted')

    def test_artist_acceptance_and_aliases_coalesce_without_changing_admitted_requests(self):
        self.assertTrue(self.c.enqueue('local-song',{'artist':'First Manual'}));self.turn()
        original=deepcopy(self.c.pending);pins=alias.artist_pins(self.w.lib,['local-song'])
        self.assertTrue(self.c.change_alias('First Manual','Beta'))
        self.assertTrue(self.c.change_artists([('local-song','Suggested Artist')],pins));self.build()
        self.assertEqual(self.c.pending,original)
        self.assertEqual(next(iter(self.w.settings[alias.TARGETS].values()))['artist'],'Suggested Artist')
        self.assertTrue(self.c.change_alias('Suggested Artist','Gamma'));self.build()
        self.assertEqual(self.c.pending,original)
        self.assertEqual(next(iter(self.w.settings[alias.TARGETS].values()))['artist'],'Gamma')

    def test_artist_edit_shields_same_native_desired_title_from_second_cleanup(self):
        track=self.w.lib.tracks_by_id['local-song'];track.name='Song - Beta';track.artist='Alpha'
        self.assertTrue(self.c.enqueue('local-song',{'artist':'Beta'}))
        self.assertEqual(self.w.titles,{'local-song':'Song - Beta'})
        origin=next(iter(self.w.settings[alias.ORIGINS].values()))
        self.assertEqual(origin['title_shield'],'Song - Beta')
        self.assertEqual(self.w.saved[-1]['titles'],self.w.titles)
        self.assertEqual([job['field'] for job in self.c.pending],['artist'])

    def test_unbound_import_title_shield_survives_verified_binding_and_native_ack(self):
        track=self.w.lib.tracks_by_id['local-song'];track.name='Song - Beta';track.artist='Alpha'
        track.source_uuid='';track.source_native_id='';track.imported=True
        self.fixture.native.update(title=track.name,artist=track.artist)
        self.assertTrue(self.c.enqueue('local-song',{'artist':'Beta'}));operation=self.c.pending[0]['id']
        self.assertEqual(self.w.titles,{'local-song':'Song - Beta'})
        registered=replace(track,source_uuid=fixtures.UUID,source_native_id='1',imported=False)
        self.w.lib=replace(self.w.lib,tracks_by_id={'local-song':registered});self.fixture.library=self.w.lib
        self.fixture.pump();self.c.timer.stop()
        self.assertEqual(self.c.pending[0]['id'],operation);self.assertEqual(self.c.pending[0]['track_uuid'],fixtures.UUID)
        self.turn();self.turn();self.assertFalse(self.c.pending)
        fresh=next(iter(self.w.lib.tracks_by_id.values()))
        self.assertEqual(engine.track_row(fresh,self.w.overrides,self.w.titles)['title'],self.fixture.native['title'])

    def test_alias_title_shield_survives_readback_and_reset_but_explicit_later_title_wins(self):
        track=self.w.lib.tracks_by_id['local-song'];track.name='Song - Beta';track.artist='Alpha'
        self.fixture.native.update(title=track.name,artist=track.artist)
        self.assertTrue(self.c.change_alias('Alpha','Beta'))
        self.assertEqual(self.c.display_fields(track)['title'],'Song - Beta')
        self.build();self.assertEqual(self.w.titles,{'local-song':'Song - Beta'})
        self.assertTrue(self.c._promote_alias());self.fixture.pump();self.c.timer.stop();self.turn();self.turn()
        self.w.lib.tracks_by_id={t.track_id:t for t in self.w.lib.tracks_by_id.values()}
        fresh=self.w.lib.tracks_by_id['local-song']
        self.assertEqual(engine.track_row(fresh,self.w.overrides,self.w.titles)['title'],self.fixture.native['title'])
        self.assertTrue(self.c.enqueue('local-song',{'title':''}))
        self.assertEqual(self.w.titles,{'local-song':'Song - Beta'})
        self.assertEqual(next(iter(self.w.settings[alias.ORIGINS].values()))['title_shield'],'Song - Beta')
        self.assertTrue(self.c.enqueue('local-song',{'title':'User Title'}))
        self.assertEqual(self.w.titles,{'local-song':'User Title'})
        self.assertEqual(next(iter(self.w.settings[alias.ORIGINS].values()))['title_shield'],'')

    def test_alias_plan_storage_failure_cannot_publish_generated_title_shields(self):
        track=self.w.lib.tracks_by_id['local-song'];track.name='Song - Beta';track.artist='Alpha'
        self.assertTrue(self.c.change_alias('Alpha','Beta'));titles=self.w.titles;self.w.save_ok=False;self.build()
        self.assertIs(self.w.titles,titles);self.assertEqual(self.w.titles,{})

    def test_explicit_title_after_alias_capture_is_never_replaced_by_a_generated_shield(self):
        track=self.w.lib.tracks_by_id['local-song'];track.name='Song - Beta';track.artist='Alpha'
        self.assertTrue(self.c.change_alias('Alpha','Beta'))
        self.assertTrue(self.c.enqueue('local-song',{'title':'User Title'}));self.build()
        self.assertEqual(self.w.titles,{'local-song':'User Title'})
        self.assertEqual(next(iter(self.w.settings[alias.ORIGINS].values()))['title_shield'],'')

    def test_rapid_coalescing_promotes_latest_while_retaining_active_operation_and_its_request(self):
        self.assertTrue(self.c.enqueue('local-song',{'artist':'First Manual'}));self.turn()
        original=deepcopy(self.c.pending[0]);self.assertIsNotNone(original['request'])
        self.assertTrue(self.c.change_alias('First Manual','Beta'));self.build()
        self.assertTrue(self.c.change_alias('First Manual','Gamma'));self.build()
        self.assertEqual(self.c.pending[0],original)
        self.assertFalse(self.c._promote_alias());self.turn();self.turn();self.turn()
        self.assertEqual(self.fixture.native['artist'],'First Manual')
        self.fixture.pump();self.c.timer.stop()
        self.assertNotIn(alias.NEXT,self.w.settings)
        self.assertEqual(next(iter(self.w.settings[truth.TARGETS].values()))['artist'],'Gamma')
        self.fixture.pump();self.c.timer.stop()
        self.assertEqual([job['value'] for job in self.c.pending],['Song','Gamma'])
        self.assertEqual(next(iter(self.w.settings[truth.TARGETS].values()))['title'],'Song')

    def test_title_only_without_migration_freezes_derived_artist_and_origin_before_title_sql(self):
        self.assertTrue(self.c.enqueue('local-song',{'title':'New Title'}))
        self.assertEqual([(job['field'],job['value']) for job in self.c.pending],[('title','New Title'),('artist','Alpha')])
        origin=next(iter(self.w.settings[alias.ORIGINS].values()))
        self.assertEqual(origin['automatic'],'Alpha')
        self.assertEqual(self.w.saved[-1]['settings'][alias.ORIGINS],self.w.settings[alias.ORIGINS])

    def test_artist_only_keeps_the_clean_display_title_and_queues_both_values_before_sql(self):
        self.assertTrue(self.c.enqueue('local-song',{'artist':'New Manual'}))
        self.assertEqual([(job['field'],job['value']) for job in self.c.pending],[('title','Song'),('artist','New Manual')])
        origin=next(iter(self.w.settings[alias.ORIGINS].values()))
        self.assertEqual((origin['automatic_title'],origin['native_title']),('Song','Alpha - Song'))
        self.assertEqual(self.w.saved[-1]['settings'][alias.ORIGINS],self.w.settings[alias.ORIGINS])

    def test_source_change_cannot_publish_alias_plan_or_queue_original_values_in_another_library(self):
        self.assertTrue(self.c.change_alias('Alpha','Beta'))
        original=deepcopy(self.w.settings)
        self.w.lib=replace(self.w.lib,source=Path('/different/master.db'),source_library_id='456')
        self.fixture.pump();self.c.timer.stop()
        self.assertEqual(self.w.settings,original);self.assertEqual(self.c.tasks.pending,0)
        self.assertFalse(self.w.tag_controller.admitted);self.assertFalse(self.c.pending)

    def test_another_source_cannot_replace_preserved_alias_or_metadata_intentions(self):
        original=self.w.lib
        for pending_alias in (False,True):
            with self.subTest(pending_alias=pending_alias):
                self.w.lib=original;self.w.settings={};self.w.titles={};self.w.overrides={};self.w.aliases={}
                if pending_alias:self.assertTrue(self.c.change_alias('Alpha','Beta'))
                else:self.assertTrue(self.c.enqueue('local-song',{'title':'Original source edit'}))
                snapshot=deepcopy((self.w.settings,self.w.titles,self.w.overrides,self.w.aliases))
                self.w.lib=replace(original,source=Path('/different/master.db'),source_library_id='456')
                self.assertFalse(self.c.change_alias('Alpha','Wrong Library'))
                self.assertFalse(self.c.enqueue('local-song',{'title':'Wrong Library'}))
                self.assertEqual((self.w.settings,self.w.titles,self.w.overrides,self.w.aliases),snapshot)
                self.w.lib=original;self.assertTrue(self.c.change_alias('Alpha','Latest original source'))

    def test_maintenance_and_publication_holds_rearm_without_source_or_builder_admission(self):
        self.assertTrue(self.c.change_alias('Alpha','Beta'))
        for field in ('_state_maintenance','_state_publication'):
            setattr(self.w,field,True);self.c.timer.stop();self.c.pump()
            self.assertTrue(self.c.timer.isActive());self.assertEqual(self.c.tasks.pending,0)
            self.assertFalse(self.w.tag_controller.admitted)
            self.assertFalse(self.c.change_alias('Alpha','Forbidden'))
            self.assertFalse(self.c.enqueue('local-song',{'title':'Forbidden'}));setattr(self.w,field,False)
        self.c.closing=True;self.c.timer.stop();self.c.pump();self.assertFalse(self.c.timer.isActive())
        self.c.closing=False;self.w._shutting_down=True;self.c.pump();self.assertFalse(self.c.timer.isActive())
        self.w._shutting_down=False;self.build();self.assertEqual(self.w.settings[alias.NEXT]['state'],'ready')

    def test_saved_alias_waits_for_initial_library_materialization(self):
        self.assertTrue(self.c.change_alias('Alpha','Beta'));library=self.w.lib;self.w.lib=None
        self.fixture.pump();self.c.timer.stop();self.assertTrue(self.c.migration_pending)
        self.assertEqual(self.c.tasks.pending,0);self.assertFalse(self.w.tag_controller.admitted)
        self.w.lib=library;self.build();track=library.tracks_by_id['local-song'];self.w.lib=None
        self.assertEqual(self.c.display_fields(track),{});self.fixture.pump();self.c.timer.stop()
        self.assertIn(alias.NEXT,self.w.settings)

    def test_unresolved_old_generation_promotes_successor_without_erasing_missing_song(self):
        track=self.w.lib.tracks_by_id['local-song'];track.source_uuid=''
        plan,targets=truth.build(truth.capture(self.w.lib,titles={},artists={},aliases={},settings={}))
        self.w.settings.update({truth.MIGRATION:plan,truth.TARGETS:targets})
        track.source_uuid=fixtures.UUID
        self.assertTrue(self.c.change_alias('Alpha','Beta'));self.build()
        self.assertTrue(self.c._promote_alias())
        descriptor=self.w.settings[truth.MIGRATION]
        self.assertEqual(len(descriptor['order']),1);self.assertEqual(len(descriptor['unresolved']),1)
        truth.validate(descriptor,self.w.settings[truth.TARGETS])

    def test_later_manual_artist_edit_wins_during_alias_build_and_after_ready_before_promotion(self):
        self.assertTrue(self.c.change_alias('Alpha','Beta'))
        self.assertTrue(self.c.enqueue('local-song',{'artist':'New Manual'}));self.build()
        row=next(iter(self.w.settings[alias.TARGETS].values()));self.assertEqual(row['artist'],'New Manual')
        self.assertTrue(self.c.enqueue('local-song',{'artist':'Newest Manual'}))
        row=next(iter(self.w.settings[alias.TARGETS].values()));self.assertEqual(row['artist'],'Newest Manual')

    def test_later_title_only_edit_does_not_cancel_alias_behind_an_existing_frozen_pair(self):
        descriptor,targets=truth.build(truth.capture(self.w.lib,titles={},artists={},aliases={},settings={}))
        self.w.settings.update({truth.MIGRATION:descriptor,truth.TARGETS:targets})
        self.c._source_truth_step();original=[job['id'] for job in self.c.pending]
        self.assertTrue(self.c.change_alias('Alpha','Beta'))
        self.assertTrue(self.c.enqueue('local-song',{'title':'New Title'}));self.build()
        row=next(iter(self.w.settings[alias.TARGETS].values()))
        self.assertEqual((row['title'],row['artist']),('New Title','Beta'))
        self.assertEqual([job['id'] for job in self.c.pending[:2]],original)

    def test_clearing_manual_artist_uses_prepublication_automatic_origin_after_native_ack(self):
        self.assertTrue(self.c.enqueue('local-song',{'artist':'New Manual'}))
        for _ in range(4):self.turn()
        self.w.lib.tracks_by_id={track.track_id:track for track in self.w.lib.tracks_by_id.values()}
        self.assertEqual(self.fixture.native['artist'],'New Manual')
        self.assertTrue(self.c.enqueue('local-song',{'artist':''}))
        self.assertEqual([job['value'] for job in self.c.pending if job['field']=='artist'],['Alpha'])
        self.assertEqual(self.w.overrides,{})

    def test_more_than_256_songs_expand_one_pair_at_a_time(self):
        self.w.lib=library(300)
        self.assertTrue(self.c.change_alias('Alpha','Beta'));self.build()
        self.assertTrue(self.c._promote_alias());self.fixture.pump();self.c.timer.stop()
        self.assertEqual(len(self.c.pending),2)
        self.assertEqual(len(self.w.settings[truth.MIGRATION]['order']),300)

    def test_many_unstarted_gestures_coalesce_without_retaining_recursive_snapshots(self):
        self.assertTrue(self.c.change_alias('Alpha','Beta'))
        original=len(truth._encode(self.w.settings[alias.TARGETS]))
        for i in range(30):self.assertTrue(self.c.change_alias('Alpha','Beta '+str(i)))
        self.assertLess(len(truth._encode(self.w.settings[alias.TARGETS])),original+100)
        self.assertFalse(self.c.pending);self.build()
        self.assertEqual(next(iter(self.w.settings[alias.TARGETS].values()))['artist'],'Beta 29')

    def test_promotion_storage_failure_preserves_both_generations_without_admitting_old_work(self):
        descriptor,targets=truth.build(truth.capture(self.w.lib,titles={},artists={},aliases={},settings={}))
        self.w.settings.update({truth.MIGRATION:descriptor,truth.TARGETS:targets})
        self.assertTrue(self.c.change_alias('Alpha','Beta'));self.build()
        before=self.w.settings;self.w.save_ok=False;self.fixture.pump();self.c.timer.stop()
        self.assertIs(self.w.settings,before);self.assertFalse(self.c.pending)
        self.assertFalse(self.w.tag_controller.admitted)

    def test_first_alias_promotion_storage_failure_retries_without_a_predecessor_migration(self):
        self.assertTrue(self.c.change_alias('Alpha','Beta'));self.build();self.w.save_ok=False
        self.assertNotIn(truth.MIGRATION,self.w.settings)
        for _ in range(2):self.fixture.pump();self.c.timer.stop()
        self.assertTrue(self.c.migration_pending);self.assertFalse(self.c.pending)
        self.w.save_ok=True;self.assertTrue(self.c.retry());self.fixture.pump();self.c.timer.stop()
        self.assertNotIn(alias.NEXT,self.w.settings);self.assertIn(truth.MIGRATION,self.w.settings)
        self.assertFalse(self.w.tag_controller.admitted)

    def test_build_storage_failure_is_retryable_even_while_original_head_is_pending(self):
        self.assertTrue(self.c.enqueue('local-song',{'artist':'First Manual'}))
        original=deepcopy(self.c.pending)
        self.assertTrue(self.c.change_alias('First Manual','Beta'));self.w.save_ok=False;self.build()
        self.assertTrue(self.w.settings[alias.NEXT]['error']);self.assertEqual(self.c.pending,original)
        self.w.save_ok=True;self.assertTrue(self.c.retry());self.build()
        self.assertEqual(self.w.settings[alias.NEXT]['state'],'ready');self.assertEqual(self.c.pending,original)

    def test_artist_origin_advances_only_with_the_durable_verified_operation_ack(self):
        self.assertTrue(self.c.enqueue('local-song',{'artist':'New Manual'}))
        for _ in range(3):self.turn()
        original=deepcopy(self.w.settings[alias.ORIGINS]);self.fixture.pump();self.w.save_ok=False;self.fixture.deliver()
        self.assertEqual(self.w.settings[alias.ORIGINS],original);self.assertTrue(self.c.pending[0]['error'])
        self.w.save_ok=True;self.assertTrue(self.c.retry());self.turn();self.turn()
        self.assertEqual(next(iter(self.w.settings[alias.ORIGINS].values()))['native_artist'],'New Manual')
        self.assertEqual(len([row for row in self.fixture.writes if row['field']=='artist']),1)

    def test_invalid_retirement_proof_does_not_advance_artist_origin_or_ack_the_head(self):
        factory=self.c.factory
        def broken(lib):
            store=factory(lib);store.retirement_entry=lambda _:{}
            return store
        self.assertTrue(self.c.enqueue('local-song',{'artist':'New Manual'}))
        for _ in range(3):self.turn()
        self.assertEqual(self.c.pending[0]['field'],'artist')
        self.c.factory=broken
        original=deepcopy(self.w.settings[alias.ORIGINS]);published=len(self.w.published);self.turn()
        self.assertEqual(self.w.settings[alias.ORIGINS],original);self.assertTrue(self.c.pending[0]['error'])
        self.assertEqual(len(self.w.published),published)

    def test_late_worker_after_source_change_cannot_project_into_the_new_library(self):
        self.assertTrue(self.c.change_alias('Alpha','Beta'));callbacks=[]
        self.w._invalidate_rows.reset_mock()
        self.c.tasks=SimpleNamespace(pending=0,adopt=lambda worker,callback:callbacks.append((worker,callback)),drain=lambda:None)
        with mock.patch('desktop.metadata_controller.OneShotTask') as worker_type:
            self.c._resume_alias_build();captured=deepcopy(self.w.settings)
            descriptor=captured[alias.NEXT]
            raw=truth._encode(captured[alias.TARGETS]['inputs:'+descriptor['id']]['raw'])
            worker=worker_type.return_value;worker.result=alias.build(raw,migration_id=descriptor['id'])
            self.w.lib=replace(self.w.lib,source=Path('/another/master.db'),source_library_id='456')
            callbacks[0][1](worker)
        self.assertEqual(self.w.settings,captured);self.w._invalidate_rows.assert_not_called()

    def test_late_builder_cannot_shield_same_id_replacement_or_relocated_current_song(self):
        original=self.w.lib;track=original.tracks_by_id['local-song']
        track.name='Song - Beta';track.artist='Alpha'
        for change in ('uuid','path','ambiguous-uuid','binding-issue'):
            with self.subTest(change=change):
                self.w.lib=replace(original,artist_registry=None,tracks_by_id={'local-song':replace(track)})
                self.w.settings={};self.w.aliases={};self.w.titles={}
                self.assertTrue(self.c.change_alias('Alpha','Beta'));callbacks=[]
                self.c.tasks=SimpleNamespace(pending=0,adopt=lambda worker,callback:callbacks.append((worker,callback)),drain=lambda:None)
                with mock.patch('desktop.metadata_controller.OneShotTask') as worker_type:
                    self.c._resume_alias_build();descriptor=self.w.settings[alias.NEXT]
                    raw=truth._encode(self.w.settings[alias.TARGETS]['inputs:'+descriptor['id']]['raw'])
                    worker=worker_type.return_value;worker.result=alias.build(raw,migration_id=descriptor['id'])
                    changed=replace(track,name='Independent Current',artist='Beta')
                    if change=='uuid':changed.source_uuid='00000000-0000-4000-8000-000000000099'
                    elif change=='path':changed.location_uri='file:///relocated/current.wav'
                    incoming=replace(original,tracks_by_id={'local-song':changed})
                    if change=='ambiguous-uuid':incoming.tracks_by_id['other']=replace(changed,track_id='other',source_native_id='2')
                    elif change=='binding-issue':incoming.source_binding_issues={'local-song':'Replaced native identity'}
                    self.w.lib=incoming;callbacks[0][1](worker)
                self.assertEqual(self.w.titles,{})
                retained=next(iter(self.w.settings[alias.TARGETS].values()))
                self.assertEqual((retained['uuid'],retained['path'],retained['title']),
                                 (track.source_uuid,str(track.path),'Song - Beta'))
                if change in ('uuid','path'):self.assertEqual(self.c.display_fields(changed),{})

    def test_initial_source_truth_records_automatic_origin_before_aliases_and_native_writes(self):
        self.w.aliases={'Alpha':'Beta'}
        with mock.patch.object(self.c,'schedule'):
            self.assertTrue(self.c.begin_source_truth())
            for worker in list(self.c.tasks._workers):self.assertTrue(worker.wait(5000))
            for _ in range(10):
                self.c.timer.stop();self.app.processEvents()
                if not self.c.tasks.pending:break
        origin=next(iter(self.w.settings[alias.ORIGINS].values()))
        self.assertEqual((origin['automatic'],origin['native_artist']),('Alpha',''))
        self.assertFalse(self.w.tag_controller.admitted)

    def test_encrypted_native_alias_publish_restart_and_unmerge_persist_without_local_overlay_dependency(self):
        native=native_fixtures.NativeMetadataStore('test_title_only_updates_measured_columns_and_counter_preserving_xml_other_rows_and_audio')
        native.setUp();self.addCleanup(native.doCleanups)
        native.mutate('UPDATE djmdContent SET Title=? WHERE ID=?',('Alpha - Song','1'))
        # Reusing this independently existing actor permits the separately
        # observed retirement window; combined new+orphan remains unproven.
        native.mutate('INSERT INTO djmdArtist VALUES (?,?,NULL,?,0,0,0,0,NULL,40,?,?)',
            ('2','Alpha','00000000-0000-4000-8000-000000000002',native_fixtures.CLOCK,native_fixtures.CLOCK))
        self.w.lib=replace(self.w.lib,source=native.database)
        self.w.tag_controller.data_root=native.root/'data'
        def load(_source):
            title,artist,path=native.mutate('SELECT c.Title,coalesce(a.Name,\'\'),c.FolderPath FROM djmdContent c LEFT JOIN djmdArtist a ON a.ID=c.ArtistID WHERE c.ID=?',('1',))[0]
            track=replace(self.w.lib.tracks_by_id['local-song'],name=title,artist=artist,location_uri=Path(path).as_uri())
            return replace(self.w.lib,tracks_by_id={'local-song':track})
        self.c.factory=lambda _:native.writer;self.c.loader=load
        def finish():
            with mock.patch.object(self.c,'schedule'):
                for _ in range(40):
                    self.c.timer.stop();before=len(self.w.tag_controller.admitted);self.c.pump()
                    if len(self.w.tag_controller.admitted)>before:self.fixture.deliver()
                    for worker in list(self.c.tasks._workers):self.assertTrue(worker.wait(5000))
                    for _ in range(10):
                        self.c.timer.stop();self.app.processEvents()
                        if not self.c.tasks.pending:break
                    self.c.timer.stop()
                    if not (self.c.pending or self.c.migration_pending or self.c.retirement_pending):return
            self.fail('Alias did not finish: '+str(self.w.settings))
        self.assertTrue(self.c.change_alias('Alpha','Beta'));finish()
        self.assertEqual(load(None).tracks_by_id['local-song'].artist,'Beta')
        self.assertEqual(load(None).tracks_by_id['local-song'].name,'Song')
        self.assertFalse(self.w.overrides);self.assertFalse(self.w.titles)
        self.c.stop();self.w.settings=json.loads(json.dumps(self.w.settings));self.w.aliases=json.loads(json.dumps(self.w.aliases))
        self.c=MetadataController(self.w,store_factory=lambda _:native.writer,loader=load);self.addCleanup(self.c.stop)
        self.assertTrue(self.c.change_alias('Alpha',None));finish()
        self.assertEqual(load(None).tracks_by_id['local-song'].artist,'Alpha')
        self.assertEqual(load(None).tracks_by_id['local-song'].name,'Song')
        self.assertEqual(native.mutate('SELECT Name FROM djmdArtist ORDER BY Name'),[('Alpha',)])
        self.assertFalse(self.w.overrides);self.assertFalse(self.w.titles);self.assertFalse(self.w.aliases)
        native.mutate('INSERT INTO djmdArtist VALUES (?,?,NULL,?,0,0,0,0,NULL,40,?,?)',
            ('3','Manual Artist','00000000-0000-4000-8000-000000000003',native_fixtures.CLOCK,native_fixtures.CLOCK))
        native.mutate('INSERT INTO djmdAlbum VALUES (?,?)',('shared-album','2'))
        pins=alias.artist_pins(self.w.lib,['local-song'])
        self.assertTrue(self.c.change_artists([('local-song','Manual Artist')],pins));finish()
        self.assertTrue(self.c.enqueue('local-song',{'title':'Manual Title'}));finish()
        self.assertEqual(load(None).tracks_by_id['local-song'].name,'Manual Title')
        self.assertEqual(load(None).tracks_by_id['local-song'].artist,'Manual Artist')
        native.mutate('UPDATE djmdContent SET FolderPath=? WHERE ID=?',('/relocated/song.wav','1'))
        self.w.lib=load(None)
        self.c.stop();self.w.settings=json.loads(json.dumps(self.w.settings))
        self.c=MetadataController(self.w,store_factory=lambda _:native.writer,loader=load);self.addCleanup(self.c.stop)
        self.assertTrue(self.c.enqueue('local-song',{'title':'','artist':''}));finish()
        self.assertEqual(load(None).tracks_by_id['local-song'].name,'Song')
        self.assertEqual(load(None).tracks_by_id['local-song'].artist,'Alpha')
        self.assertFalse(self.w.titles);self.assertFalse(self.w.overrides)
        self.assertEqual([row['path'] for row in self.w.settings[alias.ORIGINS].values()],['/relocated/song.wav'])


if __name__=='__main__':unittest.main()
