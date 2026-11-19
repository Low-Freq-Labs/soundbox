"""Own metadata acknowledgements reuse preparation without concealing new media."""
from dataclasses import replace
import unittest

from desktop.native_publication import reuse_prepared_imports
from soundbox.library import Library, Playlist, Track


class NativePublication(unittest.TestCase):
    def library(self):
        return Library({'1': Track('1','Old','Artist','file:///missing/one.wav',
            source_uuid='uuid',source_analysis_path='/missing/one.DAT')},[],
            '/synthetic/master.db',source_kind='rekordbox-db',source_library_id='lib')

    def test_ready_imports_rejoin_without_replacing_fresh_cues_tags_or_smart_membership(self):
        previous=self.library()
        imported=Track('import','Set','DJ','file:///missing/set.m4a',imported=True)
        previous.tracks_by_id['import']=imported
        fresh=replace(previous,tracks_by_id={'1':replace(previous.tracks_by_id['1'],
            name='New',artist='New artist',source_cues=[{'time':2}],source_tag_ids=['tag'])},
            playlists=[Playlist('Smart',[],['1'])])
        self.assertTrue(reuse_prepared_imports(previous,fresh,{'import':{'path':'/missing/set.m4a'}}))
        self.assertIs(fresh.tracks_by_id['import'],imported)
        self.assertEqual(fresh.tracks_by_id['1'].name,'New')
        self.assertEqual(fresh.tracks_by_id['1'].source_cues,[{'time':2}])
        self.assertEqual(fresh.tracks_by_id['1'].source_tag_ids,['tag'])
        self.assertEqual(fresh.playlists[0].keys,['1'])

    def test_changed_identity_media_or_other_display_inputs_require_full_preparation(self):
        changes=(dict(source_uuid='different'),dict(source_native_id='different'),
            dict(source_native_path='/other.wav'),dict(location_uri='file:///other.wav'),
            dict(bpm='140'),dict(info={'native_file_type':11}),dict(genre='House'),
            dict(source_analysis_path='/other.DAT'),dict(size=123),dict(date_added='2026'))
        for change in changes:
            with self.subTest(change=change):
                previous=self.library()
                fresh=replace(previous,tracks_by_id={'1':replace(previous.tracks_by_id['1'],**change)})
                self.assertFalse(reuse_prepared_imports(previous,fresh,{}))

    def test_source_or_inventory_changes_cannot_partially_inject_imports(self):
        previous=self.library()
        previous.tracks_by_id['import']=Track('import','Set','DJ','file:///missing/set.wav',imported=True)
        candidates=(replace(self.library(),source='/other/master.db'),
            replace(self.library(),source_library_id='other'),
            replace(self.library(),tracks_by_id={}),
            replace(self.library(),tracks_by_id={**self.library().tracks_by_id,
                'new':Track('new','New','DJ','file:///missing/new.wav')}))
        for fresh in candidates:
            with self.subTest(source=fresh.source,keys=list(fresh.tracks_by_id)):
                before=dict(fresh.tracks_by_id)
                self.assertFalse(reuse_prepared_imports(previous,fresh,{'import':{}}))
                self.assertEqual(fresh.tracks_by_id,before)
        self.assertFalse(reuse_prepared_imports(None,self.library(),{}))

    def test_native_adopted_import_retains_its_saved_local_time_without_media_probing(self):
        previous=self.library()
        previous.tracks_by_id['1']=replace(previous.tracks_by_id['1'],
            source_native_id='123',added_ts=100)
        fresh=replace(previous,tracks_by_id={'1':replace(previous.tracks_by_id['1'],
            name='Saved',added_ts=200,source_cue_revision='fresh')})
        self.assertTrue(reuse_prepared_imports(previous,fresh,{'1':{'added_ts':100}}))
        self.assertEqual(fresh.tracks_by_id['1'].added_ts,100)
        self.assertEqual(fresh.tracks_by_id['1'].source_cue_revision,'fresh')

    def test_changed_or_unbound_import_time_and_other_media_changes_cannot_be_hidden(self):
        for change,record in (({},{}),({}, {'added_ts':99}),
                ({'source_native_id':''},{'added_ts':100}),
                ({'source_uuid':'replaced'},{'added_ts':100}),
                ({'size':123},{'added_ts':100})):
            with self.subTest(change=change,record=record):
                previous=self.library()
                previous.tracks_by_id['1']=replace(previous.tracks_by_id['1'],source_native_id='123',added_ts=100)
                fresh=replace(previous,tracks_by_id={'1':replace(previous.tracks_by_id['1'],added_ts=200,**change)})
                self.assertFalse(reuse_prepared_imports(previous,fresh,{'1':record}))
                self.assertEqual(fresh.tracks_by_id['1'].added_ts,200)

    def test_changed_display_ids_join_only_a_completely_compatible_inventory(self):
        previous=self.library()
        previous.tracks_by_id['2']=replace(previous.tracks_by_id['1'],track_id='2')
        fresh=replace(previous,tracks_by_id={
            '1':replace(previous.tracks_by_id['1'],name='Saved title'),
            '2':replace(previous.tracks_by_id['2'],artist='Saved artist')})
        changed={'earlier'}
        self.assertTrue(reuse_prepared_imports(previous,fresh,{},changed_ids=changed))
        self.assertEqual(changed,{'earlier','1','2'})
        fresh.tracks_by_id['2'].size=123
        changed={'earlier'}
        self.assertFalse(reuse_prepared_imports(previous,fresh,{},changed_ids=changed))
        self.assertEqual(changed,{'earlier'})

    def test_non_reflexive_scalar_retains_conservative_full_preparation(self):
        previous=self.library();previous.tracks_by_id['1'].total_time=float('nan')
        fresh=replace(previous,tracks_by_id={'1':replace(previous.tracks_by_id['1'])})
        self.assertFalse(reuse_prepared_imports(previous,fresh,{}))
