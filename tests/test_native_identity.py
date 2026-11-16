"""Identity bridge preserves curation keys while native data remains authoritative."""
from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path
import builtins
import io
import os
import tempfile
import unittest
from unittest import mock

from soundbox import imports, libcache
from soundbox.library import Library, Playlist, Track
from soundbox.native_identity import Binding, project,canonical_path_updates
from soundbox.rekordbox_write import WriteProofError


class IdentityProjection(unittest.TestCase):
    def setUp(self):
        self.track=Track('42','Native title','Artist','file://localhost/canonical/song.wav',
            bpm='140.00',key='6A',source_uuid='00000000-0000-4000-8000-000000000001',
            source_analysis_path='/native/ANLZ0000.DAT',source_cues=[{'position_ms':1234}],
            source_cue_revision='native-revision',source_cue_refs=[{'native_id':'cue'}],source_tag_ids=['tag'],
            source_tag_times={'tag':1794251437.42})
        self.other=Track('43','Other','Artist','file://localhost/canonical/other.wav')
        self.lib=Library({'42':self.track,'43':self.other},
            [Playlist('Set',[],['42','43','42'],'7',added_keys=['42'])],
            '/explicit/master.db',source_kind='rekordbox-db',source_library_id='123',
            source_playlist_catalog={'members':[{'ContentID':'42'}]})
        self.binding=Binding('sb-existing','/explicit/master.db','123','42',self.track.source_uuid,'/canonical/song.wav')

    def test_native_musical_fields_and_exact_playlist_order_survive_logical_projection(self):
        before=deepcopy(self.lib)
        result=project(self.lib,[self.binding])
        self.assertEqual(asdict(self.lib),asdict(before))
        self.assertEqual(set(result.tracks_by_id),{'sb-existing','43'})
        track=result.tracks_by_id['sb-existing']
        self.assertEqual((track.track_id,track.native_id,track.imported),('sb-existing','42',False))
        expected=asdict(self.track);expected.update(track_id='sb-existing',source_native_id='42')
        self.assertEqual(asdict(track),expected)
        self.assertEqual(result.playlists[0].keys,['sb-existing','43','sb-existing'])
        self.assertEqual(result.playlists[0].added_keys,['sb-existing'])
        self.assertEqual(result.source_playlist_catalog,self.lib.source_playlist_catalog)
        self.assertEqual([t.track_id for t in result.resolve(result.playlists[0])],
                         ['sb-existing','43','sb-existing','sb-existing'])

    def test_import_injection_cannot_replace_native_analysis_with_old_local_record(self):
        lib=project(self.lib,[self.binding])
        record={'path':'/canonical/song.wav','time':30,'size':100,'added_ts':100}
        imports.inject(lib,{'sb-existing':record,'sb-unregistered':{**record,'path':'/canonical/new.wav'}},repair_missing=False)
        track=lib.tracks_by_id['sb-existing']
        self.assertEqual((track.bpm,track.key,track.source_cue_revision),('140.00','6A','native-revision'))
        self.assertEqual(track.source_tag_times,self.track.source_tag_times)
        self.assertTrue(lib.tracks_by_id['sb-unregistered'].imported)
        self.assertEqual(len(lib.tracks_by_id),3)

    def test_identity_and_source_data_survive_cache_restart(self):
        # Raw native catalog is independently tested/validated in libcache tests.
        self.lib.source_playlist_catalog={}
        projected=project(self.lib,[self.binding])
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'cache.json';libcache.dump(projected,path)
            got=libcache.load(path)
            self.assertIsNotNone(got)
            self.assertEqual(asdict(got.tracks_by_id['sb-existing']),asdict(projected.tracks_by_id['sb-existing']))
            self.assertEqual(got.playlists[0].keys,projected.playlists[0].keys)

    def test_reject_conflicts_before_mutating_any_library_or_migrating_curation(self):
        before=asdict(self.lib)
        for change in (dict(source='/other/master.db'),dict(library_id='124'),dict(native_id='43'),
                       dict(native_uuid='00000000-0000-4000-8000-000000000002'),dict(canonical='/moved/song.wav'),
                       dict(local_id='43')):
            with self.subTest(change=change),self.assertRaises(WriteProofError):
                project(self.lib,[replace(self.binding,**change)])
            self.assertEqual(asdict(self.lib),before)
        for bindings in ([self.binding,self.binding],[self.binding,replace(self.binding,local_id='sb-other')]):
            with self.assertRaises(WriteProofError):project(self.lib,bindings)
            self.assertEqual(asdict(self.lib),before)

    def test_legacy_native_id_is_unchanged_without_a_binding(self):
        self.assertEqual(self.track.native_id,'42')
        self.assertEqual(project(self.lib,[]).tracks_by_id,self.lib.tracks_by_id)
        with self.assertRaises(WriteProofError):project(replace(self.lib,source_kind='rekordbox-xml'),[self.binding])

    def test_native_removal_does_not_disable_other_bindings_or_resurrect_local_collection(self):
        missing=replace(self.binding,local_id='sb-removed',native_id='44',canonical='/canonical/removed.wav')
        before=deepcopy(self.lib);issues={}
        visible=project(self.lib,[self.binding,missing],unavailable=issues)
        self.assertEqual(visible.tracks_by_id['sb-existing'].native_id,'42')
        self.assertEqual(visible.tracks_by_id['43'],self.other)
        self.assertEqual(set(issues),{'sb-removed'})
        self.assertEqual(visible.source_binding_issues,issues)
        self.assertEqual(visible.source_playlist_catalog,before.source_playlist_catalog)
        records={'sb-removed':dict(path='/canonical/removed.wav',time=193,size=10)}
        originals=deepcopy(records)
        imports.inject(visible,records,repair_missing=False)
        self.assertNotIn('sb-removed',visible.tracks_by_id)
        self.assertEqual(records,originals)
        self.assertEqual(self.lib,before)
        with self.assertRaises(WriteProofError):project(self.lib,[self.binding,missing])

    def test_browsing_never_adopts_replacement_uuid_or_swallows_structural_conflicts(self):
        changed=replace(self.track,source_uuid='00000000-0000-4000-8000-000000000002')
        library=replace(self.lib,tracks_by_id={**self.lib.tracks_by_id,'42':changed})
        issues={};visible=project(library,[self.binding],unavailable=issues)
        self.assertNotIn('sb-existing',visible.tracks_by_id)
        self.assertEqual(visible.tracks_by_id['42'],changed)
        self.assertIn('sb-existing',issues)
        for bindings in ([self.binding,self.binding],[replace(self.binding,source='/other/master.db')]):
            with self.assertRaises(WriteProofError):project(library,bindings,unavailable={})

    def test_alias_projection_retains_native_path_and_cache_identity_separately(self):
        binding=replace(self.binding,canonical='/current/song.wav',native_path='/canonical/song.wav')
        self.lib.tracks_by_id['42']=replace(self.track,location_uri='file:///current/song.wav',
                                           source_native_path='/canonical/song.wav')
        projected=project(self.lib,[binding]);track=projected.tracks_by_id['sb-existing']
        self.assertEqual(str(track.path),'/current/song.wav')
        self.assertEqual(str(track.native_path),'/canonical/song.wav')
        self.assertEqual(track.native_id,'42')
        self.lib.source_playlist_catalog={};projected.source_playlist_catalog={}
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'cache.json';libcache.dump(projected,path)
            self.assertEqual(asdict(libcache.load(path).tracks_by_id['sb-existing']),asdict(track))


class CanonicalReuse(unittest.TestCase):
    def setUp(self):
        self.old=Track('logical','Old Title','Old Artist','file:///canonical/song.wav',source_native_id='42',
            source_uuid='00000000-0000-4000-8000-000000000042',source_native_path='/raw/song.wav')
        self.new=replace(self.old,track_id='42',source_native_id='',location_uri='file:///raw/song.wav',
            source_native_path='',name='New Native Title',artist='New Native Artist',source_cues=[{'position':900}])
        self.previous=Library({'logical':self.old},[],'/synthetic/master.db',source_kind='rekordbox-db',source_library_id='123')
        self.incoming=replace(self.previous,tracks_by_id={'42':self.new})

    def test_exact_projected_alias_returns_only_path_scalars_without_io_or_input_mutation(self):
        class NoCopy(list):
            def __deepcopy__(self,memo):raise AssertionError('No musical-array copies')
        self.new.source_cues=NoCopy(self.new.source_cues)
        before_old=dict(vars(self.old));before_new=dict(vars(self.new))
        with (mock.patch.object(Path,'stat',side_effect=AssertionError('No stat')),
              mock.patch.object(Path,'resolve',side_effect=AssertionError('No resolve')),
              mock.patch.object(os,'stat',side_effect=AssertionError('No stat')),
              mock.patch.object(os,'lstat',side_effect=AssertionError('No lstat')),
              mock.patch.object(os,'scandir',side_effect=AssertionError('No scan')),
              mock.patch.object(os,'listdir',side_effect=AssertionError('No scan')),
              mock.patch.object(os,'open',side_effect=AssertionError('No open')),
              mock.patch.object(builtins,'open',side_effect=AssertionError('No open')),
              mock.patch.object(io,'open',side_effect=AssertionError('No open'))):
            updates=canonical_path_updates(self.incoming,self.previous)
        self.assertEqual(updates,(('42','file:///canonical/song.wav','/raw/song.wav'),))
        self.assertEqual(vars(self.old),before_old);self.assertEqual(vars(self.new),before_new)
        self.assertEqual((self.new.name,self.new.artist),('New Native Title','New Native Artist'))
        self.assertIs(self.new.source_cues,before_new['source_cues'])

    def test_foreign_scope_replacement_raw_path_or_binding_issue_never_reuses(self):
        incoming=[replace(self.incoming,source='/other/master.db'),replace(self.incoming,source_library_id='456'),
            replace(self.incoming,source_kind='xml'),replace(self.incoming,source='relative.db'),
            replace(self.incoming,source_binding_issues={'42':'Conflict'})]
        for changes in (dict(source_uuid='00000000-0000-4000-8000-000000000099'),
                        dict(location_uri='file:///new/native-path.wav'),dict(imported=True)):
            incoming.append(replace(self.incoming,tracks_by_id={'42':replace(self.new,**changes)}))
        for library in incoming:
            with self.subTest(source=library.source):self.assertEqual(canonical_path_updates(library,self.previous),())
        self.assertEqual(canonical_path_updates(self.incoming,replace(self.previous,source_binding_issues={'logical':'Conflict'})),())

    def test_ambiguous_old_or_fresh_native_ids_or_uuids_never_adopt_canonical_paths(self):
        for side,base,track in (('old',self.previous,self.old),('new',self.incoming,self.new)):
            for native_id in ('42','43'):
                duplicate=replace(track,track_id='duplicate',source_native_id=native_id,
                    source_uuid='00000000-0000-4000-8000-000000000099' if native_id=='42' else track.source_uuid)
                library=replace(base,tracks_by_id={**base.tracks_by_id,'duplicate':duplicate})
                with self.subTest(side=side,native_id=native_id):
                    self.assertEqual(canonical_path_updates(self.incoming,library) if side=='old'
                                     else canonical_path_updates(library,self.previous),())

    def test_already_resolved_readback_needs_no_update_or_copy(self):
        native=replace(self.new,location_uri=self.old.location_uri,source_native_path=self.old.source_native_path)
        incoming=replace(self.incoming,tracks_by_id={'42':native})
        self.assertEqual(canonical_path_updates(incoming,self.previous),())


if __name__=='__main__':unittest.main()
