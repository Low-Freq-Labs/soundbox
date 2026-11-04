"""Direct-import destination, identity and restart contracts without live I/O."""
from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path
import tempfile
import unittest
from uuid import UUID, uuid4

from soundbox import shared_imports as jobs, analysis_jobs
from soundbox.library import Library, Playlist, Track
from soundbox.rekordbox_write import WriteProofError


class ImportIntent(unittest.TestCase):
    def setUp(self):
        self.track=Track('sb-original','Song','','file:///canonical/song.wav',imported=True)
        self.uuid=str(uuid4())
        self.lib=Library({'sb-original':self.track},[Playlist('Set',[],[],'7')],'/explicit/master.db',
            source_kind='rekordbox-db',source_library_id='123',source_playlist_catalog=dict(
                nodes=[dict(ID='7',UUID=self.uuid,rb_local_deleted=0)],members=[],revision='raw'))
        self.destination=dict(kind='native',id='7',uuid=self.uuid)

    def queue(self):return jobs.admit([],self.lib,[self.track],self.destination,dict(automatic=False))

    def test_destination_and_preferences_survive_detached_admission_and_restart(self):
        import json
        queue=self.queue();self.destination['id']='8'
        self.assertEqual(queue[0]['destination']['id'],'7')
        self.assertFalse(queue[0]['preferences']['automatic'])
        self.assertEqual(jobs.validate(json.loads(json.dumps(queue))),queue)
        self.assertEqual(queue[0]['tracks'],[dict(id='sb-original',path='/canonical/song.wav')])

    def test_pending_native_membership_is_visible_without_mutating_verified_source(self):
        queue=self.queue();before=deepcopy(self.lib)
        display=jobs.present(self.lib,queue)
        self.assertEqual(display.playlists[0].keys,['sb-original'])
        self.assertEqual(self.lib,before);self.assertEqual(display.source_playlist_catalog,before.source_playlist_catalog)
        queue[0]['destination']['uuid']=str(uuid4())
        self.assertEqual(jobs.present(self.lib,queue).playlists[0].keys,[])
        queue=self.queue();queue[0]['error']='source changed'
        self.assertEqual(jobs.present(self.lib,queue).playlists[0].keys,[])

    def test_large_pending_membership_keeps_order_duplicates_and_linear_lookups(self):
        class CountedID(str):
            comparisons=0
            __hash__=str.__hash__
            def __eq__(self,other):
                CountedID.comparisons+=1
                return super().__eq__(other)

        tracks={str(i):Track(str(i),'Fixture','',f'file:///canonical/{i}.wav',imported=True)
                for i in range(200)}
        existing=[CountedID(str(i)) for i in range(100)]
        self.lib.tracks_by_id=tracks
        self.lib.playlists[0].keys=[*existing,existing[0]]
        pending=[*reversed(list(tracks.values())),Track('missing','Missing','','file:///canonical/missing.wav')]
        queue=jobs.admit([],self.lib,pending,self.destination,{})
        before=deepcopy(self.lib)
        CountedID.comparisons=0
        projected=jobs.present(self.lib,queue)
        comparisons=CountedID.comparisons
        self.assertLess(comparisons,1000)
        self.assertEqual(projected.playlists[0].keys,
                         [*self.lib.playlists[0].keys,*[str(i) for i in range(199,99,-1)]])
        self.assertEqual(self.lib,before)

    def test_registration_receipt_identity_checks_scale_linearly_and_reject_swapped_paths(self):
        from soundbox.native_media import MediaIdentity
        from soundbox.native_registration_plan import REQUEST_FORMAT
        class CountedTrack(dict):
            reads=0
            def __getitem__(self,key):
                CountedTrack.reads+=1
                return super().__getitem__(key)

        count=120
        tracks=[Track(str(i),'Fixture','',f'file:///canonical/{i}.wav',imported=True)
                for i in range(1,count+1)]
        queue=jobs.admit([],self.lib,tracks,dict(kind='collection'),{})
        job=queue[0]
        job['tracks']=[CountedTrack(value) for value in job['tracks']]
        job['request']=dict(format=REQUEST_FORMAT,operation_id=job['id'],source=str(self.lib.source),
                            library_id='123',schema_sha256='a'*64,tracks=[])
        for index,track in enumerate(tracks,1):
            uid=str(UUID(int=index,version=4));path=str(track.path)
            audio=MediaIdentity(path,(1,index,100,1,1),'a'*64,3,'wav','pcm_s16le',3,1,1)
            job['request']['tracks'].append(dict(local_id=track.track_id,audio=asdict(audio),
                native_id=str(index),native_uuid=uid,timestamp='2026-01-01 00:00:00.000 +00:00',day='2026-01-01'))
            job['bindings'].append(dict(local_id=track.track_id,source=str(self.lib.source),
                library_id='123',native_id=str(index),native_uuid=uid,canonical=path))
        CountedTrack.reads=0
        self.assertIs(jobs.validate(queue),queue)
        self.assertLess(CountedTrack.reads,20*count)
        job['bindings'][0]['canonical']=str(tracks[1].path)
        with self.assertRaisesRegex(WriteProofError,'another import'):
            jobs.validate(queue)

    def test_foreign_binding_or_destination_cannot_be_substituted(self):
        queue=self.queue()
        for change in ('duplicate','path','destination','unknown','bindings'):
            bad=deepcopy(queue)
            if change=='duplicate':bad.append(deepcopy(bad[0]))
            elif change=='path':bad[0]['tracks'][0]['path']='/a/../song.wav'
            elif change=='destination':bad[0]['destination']['uuid']='bad'
            elif change=='unknown':bad[0]['extra']=True
            else:bad[0]['bindings']=[dict(local_id='different',source='/explicit/master.db',library_id='123',
                native_id='42',native_uuid=str(uuid4()),canonical='/canonical/song.wav')]
            with self.subTest(change=change),self.assertRaises(WriteProofError):jobs.validate(bad)

    def test_native_binding_cannot_overwrite_another_logical_identity(self):
        binding=dict(local_id='sb-original',source='/explicit/master.db',library_id='123',native_id='42',
            native_uuid=str(uuid4()),canonical='/canonical/song.wav')
        self.assertEqual(jobs.remember([binding],[binding]),[binding])
        with self.assertRaises(WriteProofError):jobs.remember([binding],[{**binding,'local_id':'sb-other'}])
        with self.assertRaises(WriteProofError):jobs.remember([binding],[{**binding,'native_id':'43'}])
        with self.assertRaises(WriteProofError):jobs.guard_curation([binding],keyed=[{'42':'Saved title'}])
        with self.assertRaises(WriteProofError):jobs.guard_curation([binding],memberships=[['42']])
        jobs.guard_curation([binding],keyed=[{'sb-original':'Keep this title'}],memberships=[['sb-original']])

    def test_detection_intent_survives_identity_acquisition_but_not_replacement(self):
        with tempfile.TemporaryDirectory() as area:
            track=replace(self.track,location_uri=(Path(area)/'song.wav').as_uri())
            queue=analysis_jobs.admit([],self.lib,[track],dict(automatic=True))
            native=replace(track,imported=False,source_native_id='42',source_uuid=str(uuid4()))
            self.assertTrue(analysis_jobs.matches(queue[0],self.lib,native))
            self.assertFalse(analysis_jobs.matches(queue[0],self.lib,replace(native,source_analysis_path='/native/DAT')))
            self.assertFalse(analysis_jobs.matches(queue[0],self.lib,replace(native,location_uri='file:///different.wav')))


if __name__=='__main__':unittest.main()
