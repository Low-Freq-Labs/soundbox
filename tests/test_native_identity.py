"""Identity bridge preserves curation keys while native data remains authoritative."""
from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path
import tempfile
import unittest

from soundbox import imports, libcache
from soundbox.library import Library, Playlist, Track
from soundbox.native_identity import Binding, project
from soundbox.rekordbox_write import WriteProofError


class IdentityProjection(unittest.TestCase):
    def setUp(self):
        self.track=Track('42','Native title','Artist','file://localhost/canonical/song.wav',
            bpm='140.00',key='6A',source_uuid='00000000-0000-4000-8000-000000000001',
            source_analysis_path='/native/ANLZ0000.DAT',source_cues=[{'position_ms':1234}],
            source_cue_revision='native-revision',source_cue_refs=[{'native_id':'cue'}],source_tag_ids=['tag'])
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


if __name__=='__main__':unittest.main()
