"""Full prep replacements freeze intended grids; detached views are no proof."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from soundbox import prep_edits as prep
from soundbox.state_repository import StateRepository,read_state


class FullPrepGridCapture(unittest.TestCase):
    def setUp(self):
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        base=dict(version=2,id=str(uuid4()),track_id='A',path='/generated/A.wav',
            source='/generated/master.db',library_id='generated',epoch=[1,2,20,4,5],sha256='a'*64,
            duration=10800,native_ready=True,grid=[[1,0,120],[2,500,120]],cues=[],operations=[],
            prepared=None,error='',native_id='A',source_uuid=str(uuid4()),native_path='/generated/A.wav')
        self.original=prep.operation(base,'cue','add',page='hot',slot=0,position=1000)
        self.repository=StateRepository(self.root/'library.json')
        self.repository.save({'settings':{prep.KEY:[self.original]}})

    def test_detached_changed_valid_gui_grid_is_persisted_with_the_full_cue_intention(self):
        before=deepcopy(self.original);before['grid']=[[1,0,119],[2,505,119]]
        after=prep.operation(before,'cue','add',page='hot',slot=1,position=2000)
        prep.validate([after])
        patch=self.repository.prepare_patch({('settings',prep.KEY):[after]})
        self.repository.commit_patch(patch)
        self.assertEqual(read_state(self.repository.path)['settings'][prep.KEY],[after])
        self.assertNotEqual(after['grid'],self.original['grid'])

    def test_later_in_place_gui_grid_mutation_cannot_change_the_frozen_durable_intention(self):
        after=prep.operation(self.original,'cue','add',page='hot',slot=1,position=2000)
        captured=deepcopy(after)
        patch=self.repository.prepare_patch({('settings',prep.KEY):[after]})
        after['grid'][0][2]=119;after['grid'][1][2]=119
        prep.validate([after])
        self.repository.commit_patch(patch)
        self.assertEqual(read_state(self.repository.path)['settings'][prep.KEY],[captured])
        self.assertNotEqual(after['grid'],captured['grid'])
