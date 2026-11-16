"""Frozen effective metadata and source curation identity guards, no source IO."""
from copy import deepcopy
from dataclasses import replace
import unittest

from soundbox import metadata,source_truth as truth,shared_imports
from soundbox.library import Library,Track
from soundbox.native_identity import Binding


def library(identity='1',uuid='uuid-one',source='/synthetic/master.db',library_id='123'):
    track=Track(identity,'Artist - Song DROP','Artist','file:///synthetic/song.wav',source_uuid=uuid,
                info=dict(nativeFileType=11,nativeAnalysisFlags=105))
    return Library({identity:track},[],source,source_kind='rekordbox-db',source_library_id=library_id)


def document():
    return dict(title_overrides={'1':'Saved Title'},artist_overrides={'1':'Saved Artist'},
        artist_aliases={'variant':'Canonical'},hidden_tracks={'1':'2026-11-12'},custom={'My Set':['1']},
        playlist_removals={'100':{'1':'2026-11-12'}},playlist_additions={},settings={'track_info':{'1':{'rating':'5'}}})


class FrozenSourceTruth(unittest.TestCase):
    def test_both_fields_are_frozen_before_native_title_cleanup_can_remove_the_artist_evidence(self):
        lib=library();lib.tracks_by_id['1'].artist=''
        raw=truth.capture(lib,titles={},artists={},aliases={},settings={'title_words':['DROP']})
        lib.tracks_by_id['1'].name='Song'
        descriptor,targets=truth.build(raw);row=targets[descriptor['order'][0]]
        self.assertEqual(row['title'],'Song');self.assertEqual(row['artist'],'Artist')
        self.assertEqual(set(row['fields']),{'title','artist'})
        self.assertEqual(row['uuid'],'uuid-one');self.assertEqual(row['path'],'/synthetic/song.wav')

    def test_exact_visible_overrides_aliases_rules_and_legacy_track_info_title_artist_win(self):
        lib=library();settings=dict(title_words=['DROP'],track_info={'1':{'title':'Visible Legacy Title',
            'artist':'Visible Legacy Artist','rating':'5'}})
        raw=truth.capture(lib,titles={'1':'Manual Title'},artists={'1':'variant'},aliases={'variant':'Canonical'},settings=settings)
        descriptor,targets=truth.build(raw);row=targets[descriptor['order'][0]]
        self.assertEqual((row['title'],row['artist']),('Visible Legacy Title','Visible Legacy Artist'))
        self.assertEqual(row['info'],{'rating':'5'})
        settings['track_info'].clear();self.assertEqual(truth.build(raw)[1][next(iter(targets))]['title'],'Visible Legacy Title')

    def test_unknown_ids_remain_unresolved_and_no_native_identity_is_invented(self):
        lib=library();lib.tracks_by_id['1'].source_uuid=''
        descriptor,targets=truth.build(truth.capture(lib,titles={'missing':'Historical'},artists={},aliases={},settings={}))
        self.assertEqual(descriptor['order'],[]);self.assertEqual(len(descriptor['unresolved']),2)
        self.assertEqual({row['local_id'] for row in targets.values()},{'1','missing'})
        self.assertTrue(all(not row['uuid'] for row in targets.values()))

    def test_rule_disabling_and_artist_registry_have_the_same_effect_as_normal_browser_policy(self):
        lib=library();track=lib.tracks_by_id['1'];track.name='Artist - Song (Original Mix)'
        settings={'title_rules_off':[name for name,_label,_pattern in metadata.BUILTIN_TITLE_RULES]}
        descriptor,targets=truth.build(truth.capture(lib,titles={},artists={},aliases={},settings=settings))
        row=targets[descriptor['order'][0]]
        self.assertEqual(row['title'],'Song (Original Mix)')

    def test_real_native_projection_keeps_one_logical_alias_and_one_frozen_target(self):
        lib=library(uuid='00000000-0000-4000-8000-000000000001')
        binding=Binding(source=str(lib.source),library_id=lib.source_library_id,local_id='local-song',native_id='1',
                        native_uuid=lib.tracks_by_id['1'].source_uuid,canonical='/synthetic/song.wav')
        projected=shared_imports.project(lib,[binding.__dict__])
        self.assertEqual(set(projected.tracks_by_id),{'local-song'})
        descriptor,targets=truth.build(truth.capture(projected,titles={'local-song':'Clean'},artists={},aliases={},settings={}))
        self.assertEqual(len(descriptor['order']),1);self.assertEqual(len(targets),1)
        self.assertEqual(next(iter(targets.values()))['native_id'],'1')


class SourceCurationScopes(unittest.TestCase):
    def setUp(self):
        self.lib=library();self.lib.source_playlist_catalog={'nodes':[dict(ID='100',UUID='playlist-one',Attribute=0,rb_local_deleted=0)]}

    def test_legacy_foreign_scope_or_same_numeric_id_different_uuid_never_adopts_or_clears_work(self):
        state=document();before=deepcopy(state)
        foreign=library(uuid='replacement',source='/other/master.db',library_id='456')
        with self.assertRaises(ValueError):truth.stage_scope(state,foreign,previous=self.lib)
        self.assertEqual(state,before)
        scoped=truth.stage_scope(state,self.lib,previous=self.lib)
        with self.assertRaises(ValueError):truth.stage_scope(scoped,foreign,previous=self.lib)
        self.assertEqual(state,before)

    def test_known_partitions_restore_all_curation_and_new_sources_do_not_erase_old_work(self):
        original=document();scoped=truth.stage_scope(original,self.lib,bootstrap=True)
        other=library(identity='2',uuid='uuid-two',source='/other/master.db',library_id='456')
        switched=truth.stage_scope(scoped,other,previous=self.lib)
        self.assertEqual(switched['title_overrides'],{})
        self.assertIn(truth.scope_key(truth.scope(self.lib)),switched['settings'][truth.CURATION]['partitions'])
        switched['title_overrides']['2']='Second library title'
        restored=truth.stage_scope(switched,self.lib,previous=other)
        for key,value in original.items():
            if key!='settings':self.assertEqual(restored[key],value,key)
        self.assertEqual(restored['settings']['track_info'],original['settings']['track_info'])
        self.assertEqual(restored['settings'][truth.CURATION]['partitions'][truth.scope_key(truth.scope(other))]
                         ['curation']['top']['title_overrides'],{'2':'Second library title'})

    def test_same_source_track_or_playlist_uuid_replacement_is_refused_but_same_uuid_relocation_is_safe(self):
        state=truth.stage_scope(document(),self.lib,bootstrap=True)
        replaced=replace(self.lib,tracks_by_id={'1':replace(self.lib.tracks_by_id['1'],source_uuid='new')})
        with self.assertRaises(ValueError):truth.stage_scope(state,replaced,previous=self.lib)
        moved=replace(self.lib,tracks_by_id={'1':replace(self.lib.tracks_by_id['1'],location_uri='file:///synthetic/moved.wav')})
        self.assertEqual(truth.stage_scope(state,moved,previous=self.lib)['title_overrides'],{'1':'Saved Title'})
        changed=replace(self.lib,source_playlist_catalog={'nodes':[dict(ID='100',UUID='different',Attribute=0,rb_local_deleted=0)]})
        with self.assertRaises(ValueError):truth.stage_scope(state,changed,previous=self.lib)

    def test_temporarily_missing_curated_native_row_keeps_its_original_uuid_pin(self):
        state=truth.stage_scope(document(),self.lib,bootstrap=True)
        missing=replace(self.lib,tracks_by_id={})
        retained=truth.stage_scope(state,missing,previous=self.lib)
        self.assertEqual(retained['settings'][truth.CURATION]['active']['pins']['1']['uuid'],'uuid-one')
        self.assertEqual(truth.stage_scope(retained,self.lib,previous=missing)['title_overrides'],{'1':'Saved Title'})

    def test_logical_alias_cannot_adopt_another_native_row_while_original_identity_still_exists(self):
        state=truth.stage_scope(document(),self.lib,bootstrap=True)
        original=self.lib.tracks_by_id['1']
        changed=replace(self.lib,tracks_by_id={
            'old-logical':replace(original,track_id='old-logical',source_native_id='1'),
            '1':replace(original,source_native_id='2',source_uuid='uuid-two')})
        before=deepcopy(state)
        with self.assertRaisesRegex(ValueError,'logical track ID'):truth.stage_scope(state,changed,previous=self.lib)
        self.assertEqual(state,before)

    def test_malformed_partition_headers_are_refused_without_mutating_curation(self):
        state=truth.stage_scope(document(),self.lib,bootstrap=True)
        for malformed in (None,[],{'scope':{}},{'scope':truth.scope(self.lib),'pins':[]}):
            with self.subTest(malformed=malformed):
                broken=deepcopy(state);broken['settings'][truth.CURATION]['active']=malformed;before=deepcopy(broken)
                with self.assertRaises(ValueError):truth.stage_scope(broken,self.lib,previous=self.lib)
                self.assertEqual(broken,before)


if __name__=='__main__':unittest.main()
