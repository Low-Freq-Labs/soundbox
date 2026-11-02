"""Direct-import destination, identity and restart contracts without live I/O."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

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
