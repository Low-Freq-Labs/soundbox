"""Real background lane/queue/ack tests on private TEMP audio and native DBs."""
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import time
import unittest
from unittest import mock

from PySide6.QtCore import QObject
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from test_media_conversion_store import fixture,UUID
from desktop.media_conversion import MediaConversionController
from desktop.tag_controller import TagEditController
from soundbox import analysis,media_conversion_store as store,media_conversion_jobs as jobs
from soundbox import rekordbox_cue_store as cues,rekordbox_tags as tags
from soundbox.library import Library,Track
from soundbox.rekordbox_write import WriteProofError


class Host(QObject):
    def __init__(self,root,source,audio):
        super().__init__();self.settings={};self.imported={};self.hidden=set();self.removals={}
        self._shutting_down=False;self._state_maintenance=False;self._isolated=True;self._refresh_worker=None
        self.lib=Library({'1':Track('1','Song','',audio.as_uri(),source_uuid=UUID)},[],str(source/'master.db'),
                         source_kind='rekordbox-db',source_library_id='123')
        data=root/'data';data.mkdir();self.state=data/'library.json';self.fail_save=False
        self.tag_controller=TagEditController(data,self);self.tag_controller.lib=self.lib;self.tag_controller.verified=True
        self._device_operation_owned=lambda:False;self._prepare_grid_cache=mock.Mock();self._flash_status=mock.Mock()
        self._native_reconcile_source=mock.Mock()
        self._native_pending_changed=mock.Mock()
        self._analysis_queue=[];self._resume_analysis_jobs=mock.Mock()
        self.player_bar=SimpleNamespace(current_track=lambda:None,refresh_audio_detail=mock.Mock())
    def _save_state(self):
        if self.fail_save:return False
        self.state.write_text(json.dumps(dict(settings=self.settings,imported=self.imported)));return True


class BackgroundConversion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name).resolve()
        self.source,self.audio,schema=fixture(self.root);self.window=Host(self.root,self.source,self.audio)
        import numpy as np
        from test_wave_conversion import wave
        wave(self.audio,np.zeros(44100*2*5,dtype='<f4'))
        for obj,key in ((cues,'_closed'),(tags,'_assert_closed')):self.enterContext(mock.patch.object(obj,key,return_value=None))
        for obj in (cues,tags):self.enterContext(mock.patch.object(obj,'_installed_version',return_value=tags.NATIVE_VERSION))
        self.enterContext(mock.patch('desktop.media_conversion.assert_rekordbox_closed',return_value=None))
        authority=store.ConversionAuthority(str(self.source/'master.db'),'123',schema,'0'*64,True)
        self.writer=store.ConversionStore(authority,self.root/'data')
        self.controller=MediaConversionController(self.window,factory=lambda _:self.writer)
        self.addCleanup(self.cleanup)
    def cleanup(self):
        self.controller.stop();self.window.tag_controller.owner.drain();self.window.deleteLater();self.app.processEvents()
    def wait(self,predicate):
        end=time.monotonic()+30
        while not predicate() and time.monotonic()<end:QTest.qWait(10)
        self.assertTrue(predicate(),repr(self.controller.pending))

    def test_queue_precedes_admission_and_latest_manual_grid_cue_and_curation_survive_conversion(self):
        w=self.window;key=analysis.result_key(self.audio);raw=self.audio.read_bytes();before_db=(self.source/'master.db').read_bytes()
        record=dict(engine=analysis.ENGINE,path=str(self.audio),epoch=analysis.identity(self.audio.stat()),size=len(raw),
                    sha256=hashlib.sha256(raw).hexdigest(),duration=5,bpm=120,key='8A',first_cue=None,
                    beats=[[1,0,120],[2,500,120],[3,1000,120]])
        w.settings['analysis_results']={key:record};w.imported['1']=dict(path=str(self.audio),size=len(raw),added='keep')
        entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        original=self.writer.convert
        def held(*args,**kwargs):entered.set();release.wait(5);return original(*args,**kwargs)
        with mock.patch.object(self.writer,'convert',side_effect=held):
            self.controller.discover();self.wait(entered.is_set)
            persisted=json.loads(w.state.read_text());self.assertEqual(len(persisted['settings'][jobs.KEY]),1)
            self.assertEqual(self.audio.read_bytes(),raw)
            latest=w.settings['analysis_results'][key]
            latest['grid_draft']=dict(origin='manual',base_sha256=record['sha256'],revision=2,bpm=120,
                                      beats=[[1,1,120],[2,501,120]])
            latest['cue_draft']=dict(origin='manual',base_sha256=record['sha256'],revision=3,
                                     cues=[dict(kind='hot',slot=0,position_ms=25,type='single',end_ms=-1,label='')])
            grid=deepcopy(latest['grid_draft']['beats']);cue=deepcopy(latest['cue_draft']['cues'])
            w.settings['new_user_preference']='keep';w._save_state();release.set()
            self.wait(lambda:not self.controller.pending and not w.tag_controller.owned)
        after=w.settings['analysis_results'][key]
        self.assertEqual(after['grid_draft']['beats'],grid);self.assertEqual(after['cue_draft']['cues'],cue)
        self.assertEqual((after['bpm'],after['key'],after['beats']),(120,'8A',record['beats']))
        self.assertEqual(w.settings['new_user_preference'],'keep');self.assertEqual(w.imported['1']['added'],'keep')
        self.assertEqual(w.imported['1']['size'],self.audio.stat().st_size)
        self.assertEqual((self.source/'master.db').read_bytes(),before_db)
        self.assertEqual(json.loads(w.state.read_text())['settings']['analysis_results'][key],after)

    def test_queue_save_failure_never_admits_a_conversion(self):
        raw=self.audio.read_bytes();self.window.fail_save=True
        with mock.patch.object(self.writer,'convert') as convert:
            self.controller.discover();self.wait(lambda:self.window._flash_status.called)
            self.assertFalse(self.controller.pending);self.assertEqual(self.audio.read_bytes(),raw);convert.assert_not_called()

    def test_native_app_open_defers_without_error_then_uses_the_same_durable_intent(self):
        with mock.patch('desktop.media_conversion.assert_rekordbox_closed',side_effect=WriteProofError('Close rekordbox and its background agent before editing')):
            self.controller.discover();self.wait(lambda:bool(self.controller.pending));job=deepcopy(self.controller.pending[0])
            QTest.qWait(1200);self.assertEqual(self.controller.pending[0],job)
            self.assertEqual(self.controller.cache[str(self.audio)]['facts']['status'],'convert')
        self.controller.pump();self.wait(lambda:not self.controller.pending and not self.window.tag_controller.owned)
        self.assertEqual(self.window.settings[jobs.DONE][str(self.audio)]['operation_id'],job['id'])

    def test_new_high_rate_wave_becomes_compatible_before_registration_preparation(self):
        import numpy as np
        import soundfile as sf
        from desktop.shared_imports import SharedImportController
        from soundbox import shared_imports
        from soundbox.media_probe import read_media_header
        sf.write(self.audio,np.zeros((96000*5,2)),96000,subtype='PCM_16')
        w=self.window;w._native_pending_changed=mock.Mock();w._analysis_worker=None
        w.imported['1']=dict(path=str(self.audio),size=self.audio.stat().st_size,added='keep')
        config=dict(format=store.AUTHORITY_FORMAT,**asdict(self.writer.authority))
        (self.root/'data/rekordbox-media-conversion-authority.json').write_text(json.dumps(config))
        prepared=[]
        class Registration:
            def convert_imports(self,*_args,**_kwargs):return []
            def prepare(self,tracks,**_kwargs):
                header=read_media_header(Path(tracks[0]['path']))
                prepared.append((header.bits,header.sample_rate,tracks[0]['sha256']))
                raise RuntimeError('compatible preparation reached')
        controller=SharedImportController(w,registration_factory=lambda _:Registration())
        self.addCleanup(controller.stop)
        w.settings[shared_imports.KEY]=shared_imports.admit([],w.lib,[w.lib.tracks_by_id['1']],dict(kind='collection'),{})
        with mock.patch('desktop.shared_imports.assert_rekordbox_closed',return_value=None):
            controller.schedule();self.wait(lambda:bool(prepared) and not w.tag_controller.owned)
        self.assertEqual(prepared[0][:2],(24,48000))
        receipt=w.settings[jobs.DONE][str(self.audio)]
        self.assertEqual(prepared[0][2],receipt['after_sha256'])
        self.assertEqual(w.imported['1']['size'],self.audio.stat().st_size)

    def test_new_flac_rebinds_path_bookmark_and_destination_before_native_registration(self):
        import numpy as np
        import soundfile as sf
        from desktop.shared_imports import SharedImportController
        from soundbox import shared_imports
        from soundbox.media_probe import read_media_header
        path=self.audio.with_name('new-song.flac');sf.write(path,np.zeros((44100*5,2)),44100,subtype='PCM_24')
        w=self.window;tid='sb-new-flac';track=Track(tid,'New song','',path.as_uri(),imported=True)
        w.lib.tracks_by_id[tid]=track;w.imported[tid]=dict(path=str(path),size=path.stat().st_size,file_bookmark='old',added='keep')
        config=dict(format=store.AUTHORITY_FORMAT,**asdict(self.writer.authority))
        (self.root/'data/rekordbox-media-conversion-authority.json').write_text(json.dumps(config))
        prepared=[]
        class Registration:
            def convert_imports(self,*_args,**_kwargs):return []
            def prepare(self,tracks,**_kwargs):
                header=read_media_header(Path(tracks[0]['path']));prepared.append((tracks[0],header))
                raise RuntimeError('compatible preparation reached')
        controller=SharedImportController(w,registration_factory=lambda _:Registration());self.addCleanup(controller.stop)
        destination=dict(kind='custom',name='Keep my destination')
        w.settings[shared_imports.KEY]=shared_imports.admit([],w.lib,[track],destination,{})
        with mock.patch('desktop.shared_imports.assert_rekordbox_closed',return_value=None):
            controller.schedule();self.wait(lambda:bool(prepared) and not w.tag_controller.owned)
        target=path.with_suffix('.wav');self.assertEqual(w.imported[tid]['path'],str(target));self.assertNotIn('file_bookmark',w.imported[tid])
        self.assertEqual(w.settings[shared_imports.KEY][0]['destination'],destination)
        self.assertEqual(prepared[0][0]['path'],str(target));self.assertEqual(prepared[0][1].bits,24)
        self.assertFalse(path.exists());self.assertEqual(w.lib.tracks_by_id[tid].path,target)
