"""Fresh readback pin facts keep ACK verification sparse and source-bound."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import unittest

from soundbox import source_truth as truth
from soundbox.library import Library, Track
from tests import test_metadata_controller as fixtures


class NoBrowse(dict):
    def values(self):raise AssertionError('GUI scanned full library')
    def items(self):raise AssertionError('GUI scanned full library')
    def __iter__(self):raise AssertionError('GUI scanned full library')


def library():
    return Library({str(i):Track(str(i),'Song','Artist',f'file:///synthetic/{i}.wav',
        source_native_id=str(i),source_uuid='uuid-'+str(i)) for i in (1,2)},[],
        '/synthetic/master.db',source_kind='rekordbox-db',source_library_id='123')


class PreparedCurationPinFacts(unittest.TestCase):
    def setUp(self):
        self.lib=library()
        self.document=truth.stage_scope({'title_overrides':{'1':'Curated one','2':'Curated two'},
            'settings':{}},self.lib,bootstrap=True)

    def test_current_curation_refresh_matches_full_verifier_without_inventory_walk(self):
        fresh=replace(self.lib,tracks_by_id=dict(self.lib.tracks_by_id))
        index=truth.prepare_pin_index(fresh)
        current=deepcopy(self.document)
        current['title_overrides'].pop('1')
        current['custom']={'New current playlist':['1']}
        expected=truth.stage_scope(current,fresh,previous=self.lib)
        fresh.tracks_by_id=NoBrowse(fresh.tracks_by_id)
        actual=truth.stage_scope(current,fresh,previous=self.lib,pin_index=index)
        self.assertEqual(actual,expected)
        self.assertEqual(set(actual['settings'][truth.CURATION]['active']['pins']),{'1','2'})
        current['custom']={}
        actual=truth.stage_scope(current,fresh,previous=self.lib,pin_index=index)
        self.assertEqual(set(actual['settings'][truth.CURATION]['active']['pins']),{'2'})

    def test_index_is_immutable_complete_and_bound_to_exact_library_and_scope(self):
        index=truth.prepare_pin_index(self.lib)
        with self.assertRaises(TypeError):index.native['1']=('replacement',)
        with self.assertRaises(FrozenInstanceError):index.library=library()
        with self.assertRaisesRegex(ValueError,'another readback'):
            truth.stage_scope(self.document,replace(self.lib),previous=self.lib,pin_index=index)
        self.lib.source='/other/master.db'
        with self.assertRaisesRegex(ValueError,'another readback'):
            truth._verify_pins(self.document['settings'][truth.CURATION]['active'],self.lib,index)
        self.lib.source='/synthetic/master.db'
        with self.assertRaisesRegex(ValueError,'incomplete'):
            truth.prepare_pin_index(self.lib,rows=[('1',self.lib.tracks_by_id['1'])])
        with self.assertRaisesRegex(ValueError,'inventory changed'):
            truth.prepare_pin_index(self.lib,rows=[('1',self.lib.tracks_by_id['1'])]*2)

    def test_unrelated_curated_replacement_and_duplicate_same_uuid_still_refuse(self):
        fresh=replace(self.lib,tracks_by_id={**self.lib.tracks_by_id,
            '2':replace(self.lib.tracks_by_id['2'],source_uuid='foreign')})
        index=truth.prepare_pin_index(fresh);fresh.tracks_by_id=NoBrowse(fresh.tracks_by_id)
        with self.assertRaisesRegex(ValueError,'logical track ID'):
            truth.stage_scope(self.document,fresh,previous=self.lib,pin_index=index)
        original=self.lib.tracks_by_id['2']
        fresh=replace(self.lib,tracks_by_id={'1':self.lib.tracks_by_id['1'],
            'native-alias-a':original,'native-alias-b':replace(original,track_id='alias')})
        index=truth.prepare_pin_index(fresh)
        self.assertEqual(index.native['2'],('uuid-2','uuid-2'))
        fresh.tracks_by_id=NoBrowse(fresh.tracks_by_id)
        with self.assertRaisesRegex(ValueError,'native identity'):
            truth.stage_scope(self.document,fresh,previous=self.lib,pin_index=index)

    def test_new_native_unbound_occupant_and_changed_playlist_pin_are_not_skipped(self):
        document=deepcopy(self.document)
        header=document['settings'][truth.CURATION]['active']
        header['unbound']=['new-native'];header['unbound_playlists']=['new-playlist']
        fresh=replace(self.lib,tracks_by_id={**self.lib.tracks_by_id,
            'alias':Track('alias','','','file:///synthetic/new.wav',source_native_id='new-native',source_uuid='new')})
        index=truth.prepare_pin_index(fresh);fresh.tracks_by_id=NoBrowse(fresh.tracks_by_id)
        with self.assertRaisesRegex(ValueError,'unbound saved track'):
            truth.stage_scope(document,fresh,previous=self.lib,pin_index=index)
        header['unbound']=[]
        fresh.source_playlist_catalog={'nodes':[{'ID':'new-playlist','UUID':'new','Attribute':0,'rb_local_deleted':0}]}
        with self.assertRaisesRegex(ValueError,'unbound playlist'):
            truth.stage_scope(document,fresh,previous=self.lib,pin_index=index)
        header['unbound_playlists']=[]
        header['playlist_pins']={'new-playlist':dict(uuid='old',attribute=0,rule_sha256='')}
        with self.assertRaisesRegex(ValueError,'native playlist'):
            truth.stage_scope(document,fresh,previous=self.lib,pin_index=index)


class MetadataSelectedPathFacts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):fixtures.MetadataPhases.setUpClass()

    def setUp(self):
        self.fixture=fixtures.MetadataPhases();self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.w=self.fixture.w;self.c=self.fixture.controller
        self.assertTrue(self.c.enqueue('local-song',{'title':'Manual title'}))
        self.fixture.pump();self.fixture.deliver();self.fixture.pump()

    def test_unrelated_path_is_never_decoded_by_selected_pin_search(self):
        class Unrelated(Track):
            @property
            def path(self):raise AssertionError('Unrelated path decoded')
        load=self.fixture.load
        def fresh(source):
            result=load(source)
            result.tracks_by_id['unrelated']=Unrelated('unrelated','','','unavailable-uri',
                source_native_id='99',source_uuid='unrelated-uuid')
            return result
        self.c.loader=fresh
        kind,work,completed=self.w.tag_controller.admitted[-1];results=[]
        def captured():
            result=work();results.append(result);return result
        self.w.tag_controller.admitted[-1]=(kind,captured,completed)
        self.fixture.deliver()
        self.assertFalse(self.c.pending)
        self.assertEqual(self.fixture.native['title'],'Manual title')
        self.assertNotIn('curation_pin_index',results[0])

    def test_every_selected_candidate_still_checks_its_path_and_ambiguity(self):
        load=self.fixture.load
        def duplicate(source):
            result=load(source)
            result.tracks_by_id['alias']=replace(result.tracks_by_id['1'],track_id='alias')
            return result
        self.c.loader=duplicate
        self.fixture.deliver()
        self.assertTrue(self.c.pending[0]['error'])
        self.assertIsNotNone(self.c.pending[0]['request'])
        self.assertFalse(self.w.published)
