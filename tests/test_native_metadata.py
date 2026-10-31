"""Pure metadata reconstruction cannot grant source or audio write access."""
from copy import deepcopy
import unittest

from research.native_library.metadata import construct
from soundbox.rekordbox_write import ConflictError, WriteProofError

CLOCK='2026-10-31 04:02:30.269 +00:00'
SEED=dict(ID='321',UUID='5d56cdb5-efe3-4906-b018-7eda718950fd',timestamp='2026-10-31 04:02:30.178 +00:00')


class NativeMetadataProof(unittest.TestCase):
    def setUp(self):
        self.target=dict(ID='123',UUID='2e47171e-a854-4b9c-be9b-128e28202cce',Title='Before',
            ArtistID=None,AlbumID=None,Analysed=105,TrackInfoUpdated='2',rb_local_usn=90,
            rb_data_status=0,rb_local_data_status=0,rb_local_deleted=0,rb_local_synced=0,usn=None,
            updated_at='2026-10-31 03:30:18.798 +00:00',BPM=12000,opaque=b'preserve',CueUpdated='7')
        self.counter=dict(registry_id='localUpdateCount',int_1=100,opaque=b'counter')

    def plan(self,*,field='title',value='After',artists=(),references=None,seed=None):
        return construct(self.target,self.counter,list(artists),references or {},field=field,value=value,
                         timestamp=CLOCK,artist_seed=seed)

    def test_title_preserves_musical_opaque_and_input_rows(self):
        before=deepcopy(self.target);counter=deepcopy(self.counter)
        p=self.plan();self.assertEqual(self.target,before);self.assertEqual(self.counter,counter)
        self.assertEqual(p['after']['BPM'],12000);self.assertEqual(p['after']['opaque'],b'preserve')
        self.assertEqual(p['after']['CueUpdated'],'7');self.assertIsNone(p['after']['AlbumID'])
        self.assertEqual(p['after']['rb_local_usn'],102);self.assertEqual(p['after']['TrackInfoUpdated'],'3')
        self.assertEqual(p['counter_after']['opaque'],b'counter')

    def test_artist_creation_reuse_clear_and_unshared_retirement_have_distinct_intents(self):
        p=self.plan(field='artist',value='New artist',seed=SEED);actor=p['artist_created']
        self.assertEqual(actor['rb_local_usn'],101);self.assertEqual(p['after']['rb_local_usn'],103)
        self.assertEqual(p['after']['AlbumID'],'');self.assertEqual(p['after']['ArtistID'],'321')
        existing=self.plan(field='artist',value='New artist',artists=[actor])
        self.assertIsNone(existing['artist_created']);self.assertEqual(existing['after']['rb_local_usn'],102)
        self.target=p['after'];self.counter=p['counter_after']
        shared=self.plan(field='artist',value='',artists=[actor],references={'321':2})
        unshared=self.plan(field='artist',value='',artists=[actor],references={'321':1})
        self.assertIsNone(shared['artist_removed']);self.assertEqual(shared['after']['rb_local_usn'],105)
        self.assertEqual(unshared['artist_removed'],actor);self.assertEqual(unshared['after']['rb_local_usn'],106)

    def test_missing_reference_inventory_and_ambiguous_artist_never_propose_a_save(self):
        actor=self.plan(field='artist',value='Artist',seed=SEED)['artist_created']
        self.target.update(ArtistID='321',AlbumID='')
        for refs in ({},{'321':0},{'321':True}):
            with self.assertRaises(ConflictError):self.plan(field='artist',value='',artists=[actor],references=refs)
        other={**actor,'ID':'322','UUID':'1d69d4ac-a57a-4bfa-b72a-2ce4511156b2'}
        with self.assertRaises(ConflictError):
            self.plan(field='artist',value='Artist',artists=[actor,other],references={'321':1})

    def test_unref_synced_artist_and_unobserved_combined_change_fail_closed(self):
        actor=self.plan(field='artist',value='Artist',seed=SEED)['artist_created']
        self.target.update(ArtistID='321',AlbumID='')
        with self.assertRaises(WriteProofError):
            self.plan(field='artist',value='',artists=[{**actor,'rb_local_synced':1}],references={'321':1})
        seed={**SEED,'ID':'322','UUID':'1d69d4ac-a57a-4bfa-b72a-2ce4511156b2'}
        with self.assertRaises(WriteProofError):
            self.plan(field='artist',value='Different',artists=[actor],references={'321':1},seed=seed)

    def test_unchanged_title_and_existing_artist_are_noops(self):
        self.assertEqual(self.plan(value='Before')['counter_after'],self.counter)
        actor=self.plan(field='artist',value='Artist',seed=SEED)['artist_created']
        self.target.update(ArtistID='321',AlbumID='')
        p=self.plan(field='artist',value='Artist',artists=[actor],references={'321':1})
        self.assertEqual(p['before'],p['after']);self.assertEqual(p['counter_before'],p['counter_after'])

    def test_unknown_text_state_or_extra_artist_seed_is_rejected_without_input_mutation(self):
        original=deepcopy(self.target)
        for value in ('',' leading','trailing ','line\nbreak','Unproved 東京'):
            with self.assertRaises(WriteProofError):self.plan(value=value)
        with self.assertRaises(WriteProofError):self.plan(seed=SEED)
        for change in (dict(rb_local_synced=1),dict(usn=9),dict(TrackInfoUpdated=None),dict(Analysed=0)):
            self.target={**original,**change}
            with self.assertRaises(WriteProofError):self.plan()
        self.target={**original,'AlbumID':'album-id'}
        with self.assertRaises(WriteProofError):self.plan(field='artist',value='Artist',seed=SEED)
        self.assertEqual(self.counter['int_1'],100)
