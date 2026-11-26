"""Source-specific opt-out preserves raw metadata and manual curation."""
from copy import deepcopy
from dataclasses import replace
import unittest

from soundbox import metadata, metadata_cleanup_policy as policy, metadata_provenance, source_truth
from tests.test_source_truth import library


class MetadataCleanupPolicy(unittest.TestCase):
    def setUp(self):
        self.lib = library()
        self.lib.tracks_by_id['1'].name = 'Artist - Song (Original Mix)'
        self.lib.tracks_by_id['1'].artist = ''

    def test_missing_legacy_policy_preserves_clean_and_choice_is_exact_source_bound(self):
        self.assertEqual(policy.choice_for({}, self.lib), 'clean')
        settings = policy.with_choice({}, self.lib, 'original')
        self.assertEqual(policy.choice_for(settings, self.lib), 'original')
        other = replace(self.lib, source='/other/master.db', source_library_id='456')
        next_settings = policy.with_choice(settings, other, 'clean')
        self.assertEqual(policy.choice_for(next_settings, other), 'clean')
        self.assertEqual(policy.choice_for(next_settings, self.lib), 'original')
        self.assertEqual(policy.choice_for(settings, other), 'original')

    def test_choice_detaches_and_malformed_policy_does_not_opt_in(self):
        settings = {'unrelated': {'value': 1}}
        selected = policy.with_choice(settings, self.lib, 'clean')
        before = deepcopy(selected)
        changed = policy.with_choice(selected, self.lib, 'original')
        changed[policy.KEY]['choices'].clear()
        self.assertEqual(selected, before)
        for bad in (None, {}, {'format': policy.FORMAT, 'choices': {'wrong': {'scope': policy.source_scope(self.lib), 'mode': 'clean'}}}):
            self.assertEqual(policy.choice_for({policy.KEY: bad}, self.lib), 'original')
        with self.assertRaises(ValueError):policy.with_choice(settings, self.lib, True)

    def build(self, settings, **values):
        raw = source_truth.capture(self.lib, titles=values.get('titles', {}), artists=values.get('artists', {}),
            aliases=values.get('aliases', {}), settings=settings)
        descriptor, targets = source_truth.build(raw, include_equal=True)
        return descriptor, next(iter(targets.values()))

    def test_original_truth_neither_strips_title_nor_invents_artist_or_native_work(self):
        selected = policy.with_choice({}, self.lib, 'original')
        descriptor, row = self.build(selected)
        self.assertEqual((row['title'], row['artist'], row['fields']), ('Artist - Song (Original Mix)', '', []))
        self.assertEqual(descriptor['counts'].get('title', 0), 0)
        self.assertEqual(self.build({})[1]['title'], 'Song')

    def test_original_keeps_explicit_alias_override_and_track_info(self):
        self.lib.tracks_by_id['1'].artist = 'Variant'
        settings = policy.with_choice({}, self.lib, 'original')
        _, row = self.build(settings, aliases={'Variant': 'Explicit Artist'})
        self.assertEqual((row['artist'], row['title']), ('Explicit Artist', 'Artist - Song (Original Mix)'))
        settings['track_info'] = {'1': {'artist': 'Info Artist', 'title': 'Info Title', 'rating': '5'}}
        _, row = self.build(settings, titles={'1': 'Manual'}, artists={'1': 'Artist Override'})
        self.assertEqual((row['title'], row['artist'], row['info']), ('Info Title', 'Info Artist', {'rating': '5'}))

    def test_original_provenance_does_not_restore_old_automatic_recipe(self):
        registry = metadata.build_registry(['Artist'], ['Artist'])
        row = dict(scope=source_truth.scope(self.lib), local_id='1', native_id='1', uuid='uuid-one',
            path='/synthetic/song.wav', name='Native Current (Original Mix)', artist='', stem='Misleading Artist - File')
        args = dict(origin=None, title_override=None, artist_override=None, info={}, context=(registry, registry), junk=[])
        title, artist, shown, origin = metadata_provenance.derive(row, **args, cleanup=False)
        self.assertEqual((title, artist, shown), (row['name'], '', ''))
        origin['provenance']['raw']['title'] = 'Historical Artist - Original'
        origin['provenance']['title_base'] = 'Original'
        args.update(origin=origin, title_override='Manual Title', artist_override='Manual Artist')
        self.assertEqual(metadata_provenance.derive(row, **args, cleanup=False)[:2], ('Manual Title', 'Manual Artist'))

    def test_invalid_frozen_mode_refuses_before_build(self):
        import json
        raw = source_truth.capture(self.lib, titles={}, artists={}, aliases={}, settings={})
        value = json.loads(raw); value['cleanup_mode'] = 'automatic-any-source'
        with self.assertRaises(ValueError):source_truth.build(source_truth._encode(value))

    def test_original_unknown_artist_placeholder_is_not_a_literal_native_artist(self):
        registry=metadata.build_registry([],[])
        row=dict(scope=source_truth.scope(self.lib),local_id='1',native_id='1',uuid='uuid-one',
            path='/synthetic/song.wav',name='Native title',artist='',stem='Ignored artist')
        title,artist,shown,_=metadata_provenance.derive(row,origin=None,title_override=None,
            artist_override=metadata.UNKNOWN_ARTIST,info={},context=(registry,registry),junk=[],cleanup=False)
        self.assertEqual((title,artist,shown),('Native title','',metadata.UNKNOWN_ARTIST))
        settings=policy.with_choice({},self.lib,'original')
        _,target=self.build(settings,artists={'1':metadata.UNKNOWN_ARTIST})
        self.assertNotIn('artist',target['fields']);self.assertEqual(target['artist'],'')

    def test_original_literal_unknown_artist_native_tag_is_preserved_verbatim(self):
        self.lib.tracks_by_id['1'].artist=metadata.UNKNOWN_ARTIST
        _,target=self.build(policy.with_choice({},self.lib,'original'))
        self.assertEqual((target['artist'],target['fields']),(metadata.UNKNOWN_ARTIST,[]))
        row=dict(scope=source_truth.scope(self.lib),local_id='1',native_id='1',uuid='uuid-one',
            path='/synthetic/song.wav',name='Raw title',artist=metadata.UNKNOWN_ARTIST,stem='Ignored')
        registry=metadata.build_registry([],[],cleanup_enabled=False)
        self.assertEqual(metadata_provenance.derive(row,origin=None,title_override=None,artist_override=None,
            info={},context=(registry,registry),junk=[],cleanup=False)[:2],('Raw title',metadata.UNKNOWN_ARTIST))

    def test_original_incremental_and_full_plan_share_only_explicit_aliases(self):
        self.lib.tracks_by_id['1'].artist='Explicit Variant'
        aliases={'Explicit Variant':'Chosen Artist','Chosen Artist':'Chosen final'}
        settings=policy.with_choice({},self.lib,'original')
        _,target=self.build(settings,aliases=aliases)
        registry=metadata.build_registry(['CHOSEN FINAL']*8,['CHOSEN FINAL'],aliases,cleanup_enabled=False)
        row=dict(scope=source_truth.scope(self.lib),local_id='1',native_id='1',uuid='uuid-one',
            path='/synthetic/song.wav',name=self.lib.tracks_by_id['1'].name,
            artist='Explicit Variant',stem='Ignored Artist - File')
        result=metadata_provenance.derive(row,origin=None,title_override=None,artist_override=None,
            info={},context=(metadata.freeze_registry(registry),metadata.freeze_registry(registry)),junk=[],cleanup=False)
        self.assertEqual((result[0],result[1]),(target['title'],target['artist']))
        self.assertEqual(result[1],'Chosen final')

    def test_clean_feature_pair_is_frozen_before_title_save_loses_artist_evidence(self):
        track=self.lib.tracks_by_id['1'];track.name='Headliner feat. Guest - Song';track.artist=''
        raw=source_truth.capture(self.lib,titles={},artists={},aliases={},settings={})
        track.name='Song'
        descriptor,targets=source_truth.build(raw)
        target=targets[descriptor['order'][0]]
        self.assertEqual((target['title'],target['artist']),('Song (feat. Guest)','Headliner'))
        self.assertEqual(target['fields'],['title','artist'])

    def test_clean_rebuilds_old_automatic_feature_losing_recipe_but_preserves_manual_title(self):
        row=dict(scope=source_truth.scope(self.lib),local_id='1',native_id='1',uuid='uuid-one',
            path='/synthetic/song.wav',name='Song',artist='Headliner feat. Guest',stem='Ignored')
        origin=dict(scope=row['scope'],**{key:row[key] for key in ('local_id','native_id','uuid','path')},
            automatic='Headliner feat. Guest',native_artist=row['artist'],automatic_title='Song',
            native_title='Song',title_shield='')
        origin=metadata_provenance.seed(origin,title='Headliner feat. Guest - Song',artist='',display_artist='Headliner feat. Guest')
        origin['provenance']['title_base']='Song'
        registry=metadata.build_registry(['Headliner'],['Headliner'])
        args=dict(origin=origin,title_override=None,artist_override=None,info={},context=(registry,registry),junk=[])
        self.assertEqual(metadata_provenance.derive(row,**args)[:2],('Song (feat. Guest)','Headliner'))
        args['title_override']='User title'
        self.assertEqual(metadata_provenance.derive(row,**args)[:2],('User title','Headliner'))
        self.assertEqual(origin['provenance']['raw'],{'title':'Headliner feat. Guest - Song','artist':''})

    def test_original_never_moves_feature_credits_between_raw_fields(self):
        track=self.lib.tracks_by_id['1'];track.name='Raw title';track.artist='Headliner feat. Guest'
        _,target=self.build(policy.with_choice({},self.lib,'original'))
        self.assertEqual((target['title'],target['artist'],target['fields']),('Raw title','Headliner feat. Guest',[]))

    def test_feature_reverse_keeps_raw_artist_inference_until_trusted_registry_is_available(self):
        from soundbox.library import Track
        track=self.lib.tracks_by_id['1'];track.name='EASY TO LOVE Ft. LIA - WODD';track.artist=''
        self.lib.tracks_by_id['2']=Track('2','Trusted recording','WODD','file:///synthetic/second.wav',source_uuid='uuid-two')
        descriptor,targets=source_truth.build(source_truth.capture(self.lib,titles={},artists={},aliases={},settings={}))
        target=next(row for row in targets.values() if row['local_id']=='1')
        self.assertEqual((target['title'],target['artist']),('EASY TO LOVE Ft. LIA','WODD'))
        row=dict(scope=source_truth.scope(self.lib),local_id='1',native_id='1',uuid='uuid-one',path='/synthetic/song.wav',
            name='EASY TO LOVE Ft. LIA',artist='WODD',stem='Ignored')
        origin=dict(scope=row['scope'],**{key:row[key] for key in ('local_id','native_id','uuid','path')},
            automatic='WODD',native_artist='WODD',automatic_title=row['name'],native_title=row['name'],title_shield='')
        origin=metadata_provenance.seed(origin,title=track.name,artist='',display_artist='WODD')
        origin['provenance']['title_base']='WODD (feat. LIA)'
        registry=metadata.build_registry(['EASY TO LOVE Ft. LIA','WODD'],['WODD'])
        self.assertEqual(metadata_provenance.derive(row,origin=origin,title_override=None,artist_override=None,
            info={},context=(registry,registry),junk=[])[:2],(row['name'],row['artist']))
