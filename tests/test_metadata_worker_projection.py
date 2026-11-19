"""Own metadata readback projects logical IDs off-thread with an exact owner proof."""
from dataclasses import asdict
from types import SimpleNamespace
import threading
import unittest
from unittest import mock

from PySide6.QtWidgets import QApplication

from soundbox import shared_imports
from soundbox.native_identity import Binding
from tests import test_metadata_controller as fixtures


class MetadataWorkerProjection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.phases=fixtures.MetadataPhases();self.phases.setUp();self.addCleanup(self.phases.doCleanups)
        self.w=self.phases.w;self.controller=self.phases.controller
        self.binding=asdict(Binding('local-song',str(self.w.lib.source),self.w.lib.source_library_id,
            '1',fixtures.UUID,'/synthetic/song.wav'))
        self.owner=[self.binding];self.w.settings[shared_imports.BINDINGS]=self.owner
        self.publications=[]
        self.w._publish_metadata_library=lambda library,ids,**proof:self.publications.append((library,ids,proof))

    def worker_result(self):
        self.phases.prepared();self.phases.pump()
        _kind,work,completed=self.w.tag_controller.admitted[-1]
        results=[];calls=[];project=shared_imports.project
        def checked(library,values,**kwargs):
            calls.append(threading.get_ident())
            self.assertIsNot(values,self.owner)
            return project(library,values,**kwargs)
        with mock.patch('desktop.metadata_controller.shared_imports.project',side_effect=checked):
            worker=threading.Thread(target=lambda:results.append(work()))
            worker.start();worker.join(5);self.assertFalse(worker.is_alive())
        self.assertEqual(len(calls),1);self.assertNotEqual(calls[0],threading.get_ident())
        self.assertEqual(len(results),1)
        self.w.tag_controller.busy=False
        return results[0],completed

    def test_worker_projects_logical_identity_and_publication_receives_only_matching_owner_proof(self):
        result,completed=self.worker_result()
        self.assertIn('local-song',result['library'].tracks_by_id)
        self.assertNotIn('1',result['library'].tracks_by_id)
        self.assertIs(result['projected_bindings_owner'],self.owner)
        completed(SimpleNamespace(error='',result=result));self.controller.timer.stop()
        self.assertFalse(self.controller.pending)
        self.assertEqual(len(self.publications),1)
        library,ids,proof=self.publications[0]
        self.assertEqual(ids,('local-song',));self.assertEqual(library.tracks_by_id['local-song'].name,'Desired Title')
        self.assertIs(proof['projected_bindings_owner'],self.owner)
        self.assertEqual(proof['projected_scope'],(str(self.w.lib.source),self.w.lib.source_library_id))

    def test_changed_binding_owner_retains_exact_native_request_without_ack_or_stale_alias_publication(self):
        result,completed=self.worker_result();request=self.controller.pending[0]['request']
        self.w.settings[shared_imports.BINDINGS]=list(self.owner)
        completed(SimpleNamespace(error='',result=result));self.controller.timer.stop()
        self.assertEqual(len(self.phases.writes),1)
        self.assertIs(self.controller.pending[0]['request'],request)
        self.assertIn('registration identities changed',self.controller.pending[0]['error'])
        self.assertFalse(self.publications)

    def test_unavailable_historical_unrelated_binding_preserves_issue_without_blocking_selected_save(self):
        self.owner.append(asdict(Binding('historical-import',str(self.w.lib.source),self.w.lib.source_library_id,
            '404','00000000-0000-4000-8000-000000000404','/synthetic/unavailable.wav')))
        result,completed=self.worker_result()
        self.assertIn('historical-import',result['library'].source_binding_issues)
        self.assertNotIn('local-song',result['library'].source_binding_issues)
        completed(SimpleNamespace(error='',result=result));self.controller.timer.stop()
        self.assertFalse(self.controller.pending);self.assertEqual(len(self.publications),1)
