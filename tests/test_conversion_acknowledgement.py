"""Copy-on-write acknowledgement preserves current drafts and rollback owners."""
from copy import deepcopy
from pathlib import Path
import unittest

from soundbox import analysis,analysis_jobs,media_conversion_jobs as media,prep_edits,shared_imports
from soundbox import wave_conversion as wave
from soundbox.library import Library,Track


def fixture():
    path='/synthetic/song.wav';epoch=[1,2,100,3,4]
    record=dict(engine=analysis.ENGINE,path=path,epoch=epoch,size=100,sha256='a'*64,
                duration=8,bpm=120,key='8A',first_cue=None,beats=[[1,0,120],[2,500,120]])
    record['grid_draft']=dict(origin='manual',base_sha256='a'*64,revision=2,bpm=120,beats=[[1,5,120],[2,505,120]])
    intent=dict(version=1,id='00000000-0000-4000-8000-000000000001',track_id='song',
        source='/synthetic/master.db',library_id='123',path=path,sha256='a'*64,epoch=epoch,
        duration=8,native_ready=False,grid=record['beats'],cues=[],operations=[],prepared=None,error='')
    intent=prep_edits.operation(intent,'cue','add',page='hot',slot=0,position=100)
    unknown={'large':[list(range(30)) for _ in range(100)]}
    settings=dict(analysis_results={analysis.result_key(path):record,'unrelated':deepcopy(record)},
                  pending_prep_edits=[intent],future=unknown,alias=unknown)
    imported={'song':dict(path=path,size=100,file_bookmark='old'), 'unrelated':{'keep':['unchanged']}}
    job=dict(id='00000000-0000-4000-8000-000000000002',source='/synthetic/master.db',
             library_id='123',path=path,ids=['song'],native=[],error='')
    receipt=dict(format=wave.FORMAT,operation_id=job['id'],track_id='song',path=path,
                 before_sha256='a'*64,after_sha256='b'*64,before_epoch=epoch,epoch=[1,2,120,5,6],
                 size=120,stage='/synthetic/.soundbox-wave-fixture',frames=352800,sample_rate=44100,
                 channels=2,peak=1,gain=1,gain_db=0,max_error=0,clipped_samples=0)
    return settings,imported,job,receipt


class ConversionAcknowledgement(unittest.TestCase):
    def check_owners(self,settings,imported,updated,records):
        self.assertIsNot(updated,settings);self.assertIsNot(records,imported)
        self.assertIs(updated['future'],settings['future'])
        self.assertIs(updated['alias'],settings['future'])
        self.assertIs(updated['analysis_results']['unrelated'],settings['analysis_results']['unrelated'])
        self.assertIs(records['unrelated'],imported['unrelated'])
        self.assertIsNot(records['song'],imported['song'])
        self.assertIsNot(updated['pending_prep_edits'][0],settings['pending_prep_edits'][0])

    def test_wave_replay_keeps_current_edits_inputs_and_unrelated_owners(self):
        settings,imported,job,receipt=fixture();before=deepcopy((settings,imported))
        updated,records=wave.acknowledge(settings,imported,[receipt,receipt])
        self.assertEqual((settings,imported),before);self.check_owners(settings,imported,updated,records)
        self.assertEqual(len(updated[wave.KEY]),1)
        result=updated['analysis_results'][analysis.result_key(job['path'])]
        self.assertEqual(result['grid_draft']['beats'],settings['analysis_results'][analysis.result_key(job['path'])]['grid_draft']['beats'])
        self.assertEqual(result['grid_draft']['base_sha256'],'b'*64)
        self.assertEqual(updated['pending_prep_edits'][0]['sha256'],'b'*64)
        self.assertEqual(wave.acknowledge(updated,records,[receipt]),(updated,records))

    def test_wave_later_invalid_receipt_leaves_every_input_unchanged(self):
        settings,imported,_job,receipt=fixture();before=deepcopy((settings,imported))
        with self.assertRaises(ValueError):wave.acknowledge(settings,imported,[receipt,{**receipt,'after_sha256':'bad'}])
        self.assertEqual((settings,imported),before)

    def test_media_relocation_rebinds_only_exact_tracks_and_preserves_pending_order(self):
        settings,imported,job,receipt=fixture();path=job['path'];target='/synthetic/song-converted.wav'
        track=Track('song','Song','',Path(path).as_uri(),imported=True)
        other=Track('other','Other','','file:///synthetic/other.wav',imported=True)
        lib=Library({'song':track,'other':other},[],job['source'],source_library_id=job['library_id'])
        settings[analysis_jobs.KEY]=analysis_jobs.admit([],lib,[other,track],{})
        settings[shared_imports.KEY]=shared_imports.admit([],lib,[other,track],dict(kind='collection'),{})
        old_detection=settings[analysis_jobs.KEY][0];old_import=settings[shared_imports.KEY][0]
        receipt.update(target=target,original='/recovery/original.wav',facts=dict(duration=8,sample_rate=44100))
        before=deepcopy((settings,imported))
        updated,records=media.acknowledge(settings,imported,job,receipt)
        self.assertEqual((settings,imported),before);self.check_owners(settings,imported,updated,records)
        self.assertIs(updated[analysis_jobs.KEY][0],old_detection)
        self.assertEqual(updated[analysis_jobs.KEY][1]['path'],target)
        self.assertIsNot(updated[shared_imports.KEY][0],old_import)
        self.assertEqual([t['path'] for t in updated[shared_imports.KEY][0]['tracks']],[str(other.path),target])
        self.assertEqual(updated['pending_prep_edits'][0]['path'],target)
        self.assertNotIn('file_bookmark',records['song'])
        self.assertNotIn(analysis.result_key(path),updated['analysis_results'])
        self.assertIn(analysis.result_key(target),updated['analysis_results'])

    def test_media_failure_after_analysis_rebind_never_changes_originals(self):
        settings,imported,job,receipt=fixture()
        settings['pending_prep_edits'][0]['sha256']='c'*64
        receipt.update(original='/recovery/original.wav',facts=dict(duration=8,sample_rate=44100))
        before=deepcopy((settings,imported))
        with self.assertRaisesRegex(ValueError,'cannot be rebound'):media.acknowledge(settings,imported,job,receipt)
        self.assertEqual((settings,imported),before)

    def test_relocation_does_not_rebind_another_librarys_same_id_and_audio_path(self):
        from uuid import uuid4
        settings,imported,job,receipt=fixture();target='/synthetic/song-converted.wav'
        track=Track('song','Song','',Path(job['path']).as_uri(),imported=True)
        lib=Library({'song':track},[],job['source'],source_library_id=job['library_id'])
        settings[analysis_jobs.KEY]=analysis_jobs.admit([],lib,[track],{})
        settings[shared_imports.KEY]=shared_imports.admit([],lib,[track],dict(kind='collection'),{})
        foreign={}
        for key in (prep_edits.KEY,analysis_jobs.KEY,shared_imports.KEY):
            other=deepcopy(settings[key][0]);other.update(id=str(uuid4()),source='/other/master.db',library_id='456')
            if key==prep_edits.KEY:settings[key]=[other]
            else:settings[key].append(other)
            foreign[key]=other
        receipt.update(target=target,original='/recovery/original.wav',facts=dict(duration=8,sample_rate=44100))
        before=deepcopy((settings,imported))
        updated,_records=media.acknowledge(settings,imported,job,receipt)
        self.assertEqual((settings,imported),before)
        for key,other in foreign.items():
            index=0 if key==prep_edits.KEY else 1
            self.assertIs(updated[key][index],other,key)
            self.assertEqual(updated[key][index],before[0][key][index],key)
        self.assertEqual(updated[analysis_jobs.KEY][0]['path'],target)
        self.assertEqual(updated[shared_imports.KEY][0]['tracks'][0]['path'],target)

    def test_unchanged_media_does_not_rebuild_or_add_namespaces(self):
        settings,imported,job,_receipt=fixture()
        updated,records=media.acknowledge(settings,imported,job,dict(unchanged=True))
        self.assertEqual(updated,settings);self.assertEqual(records,imported)
        self.assertIs(updated['analysis_results'],settings['analysis_results'])
        self.assertIs(records['song'],imported['song'])


if __name__=='__main__':unittest.main()
