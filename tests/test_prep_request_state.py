"""Prep gesture rollback preserves original namespace ownership without live UI."""
from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from desktop.prep_controller import PrepEditController
from soundbox import analysis, prep_edits as edits
from soundbox.library import Library,Track


class _RequestHost:
    _new=PrepEditController._new
    _record=PrepEditController._record
    _job_matches=staticmethod(PrepEditController._job_matches)
    job=PrepEditController.job
    grid_view=PrepEditController.grid_view

    @property
    def pending(self):
        return self.window.settings.get(edits.KEY,[])

    def reason(self,*_args,**_kwargs):
        return ''

    def refresh(self,_tid):
        self.events.append('refresh')

    def schedule(self):
        self.events.append('schedule')


class PrepRequestState(unittest.TestCase):
    def setUp(self):
        area=tempfile.TemporaryDirectory()
        self.addCleanup(area.cleanup)
        self.path=Path(area.name)/'fixture.wav'
        self.path.write_bytes(b'fixture identity, never decoded')
        self.track=Track('fixture','Fixture','',self.path.as_uri(),imported=True)
        record=dict(engine=analysis.ENGINE,path=str(self.path),epoch=analysis.identity(self.path.stat()),
            size=self.path.stat().st_size,sha256=hashlib.sha256(self.path.read_bytes()).hexdigest(),
            duration=100,bpm=120,key='8A',first_cue=None,beats=[[1,0,120],[2,500,120]])
        self.results={analysis.result_key(self.path):record}
        self.opaque={'nested':{'values':[1,2,3]}}
        self.settings={'analysis_results':self.results,'future':self.opaque,'alias':self.opaque}
        self.host=_RequestHost();self.host.events=[]
        self.host.window=SimpleNamespace(
            settings=self.settings,lib=Library({'fixture':self.track},[],'synthetic'),
            player_bar=SimpleNamespace(current_track=lambda:'fixture',_dur=100000,
                _analysis_parts={'beats':record['beats']},cue_panel=SimpleNamespace(_loaded=True)),
            _analysis_result=lambda track:analysis.saved_result(self.settings.get('analysis_results',{}),track),
            _flash_status=lambda *_args:self.host.events.append('status'),
            _sync_grid=lambda:self.host.events.append('grid'),
            _sync_cue_editing=lambda:self.host.events.append('cues'))
        self.save_result=True
        def save():
            self.host.events.append('save')
            if isinstance(self.save_result,Exception):raise self.save_result
            return self.save_result
        self.host.window._save_state=save

    def request(self,position=100):
        return PrepEditController._request(self.host,'fixture','cue','add',page='hot',slot=0,position=position)

    def test_save_failure_restores_original_queue_analysis_and_unrelated_aliases(self):
        for existing in (False,True):
            for failure in (False,OSError('no space')):
                self.settings[edits.KEY]=[]
                seed=edits.operation(self.host._new(self.track),'cue','add',page='hot',slot=1,position=50)
                queue=[seed] if existing else []
                with self.subTest(existing=existing,failure=failure):
                    self.check_failed_save(queue,failure)

    def check_failed_save(self,queue,failure):
        self.settings[edits.KEY]=queue
        old_job=queue[0] if queue else None
        old_cues=old_job['cues'] if old_job else None
        before=deepcopy(self.settings);self.save_result=failure
        self.assertFalse(self.request())
        self.assertIs(self.settings[edits.KEY],queue)
        if queue:
            self.assertIs(queue[0],old_job)
            self.assertIs(queue[0]['cues'],old_cues)
        self.assertIs(self.settings['analysis_results'],self.results)
        self.assertIs(self.settings['future'],self.opaque)
        self.assertIs(self.settings['alias'],self.opaque)
        self.assertEqual(self.settings,before)
        self.assertNotIn('refresh',self.host.events)
        self.assertNotIn('schedule',self.host.events)

    def test_failed_new_intent_removes_both_previously_missing_namespaces(self):
        self.settings.pop('analysis_results')
        self.save_result=False
        self.assertFalse(self.request())
        self.assertNotIn(edits.KEY,self.settings)
        self.assertNotIn('analysis_results',self.settings)
        self.assertIs(self.settings['future'],self.opaque)
        self.assertIs(self.settings['alias'],self.opaque)

    def test_validation_failure_preserves_namespace_references_and_never_saves(self):
        queue=[];self.settings[edits.KEY]=queue
        before=deepcopy(self.settings)
        self.assertFalse(self.request(position=200000))
        self.assertEqual(self.settings,before)
        self.assertIs(self.settings[edits.KEY],queue)
        self.assertIs(self.settings['analysis_results'],self.results)
        self.assertIs(self.settings['future'],self.opaque)
        self.assertNotIn('save',self.host.events)
        self.assertNotIn('refresh',self.host.events)

    def test_success_persists_replacement_before_presentation_and_keeps_old_records(self):
        queue=[];self.settings[edits.KEY]=queue
        old_record=next(iter(self.results.values()));before=deepcopy(old_record)
        self.assertTrue(self.request())
        self.assertEqual(self.host.events,['save','refresh','schedule'])
        self.assertIsNot(self.settings[edits.KEY],queue)
        self.assertEqual(queue,[])
        self.assertIsNot(self.settings['analysis_results'],self.results)
        self.assertIs(next(iter(self.results.values())),old_record)
        self.assertEqual(old_record,before)
        self.assertNotIn('cue_draft',old_record)
        self.assertIs(self.settings['future'],self.opaque)
        self.assertIs(self.settings['alias'],self.opaque)


if __name__=='__main__':
    unittest.main()
