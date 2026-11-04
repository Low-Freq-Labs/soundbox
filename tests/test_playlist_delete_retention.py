"""Delete retention persists before source work and survives failed acknowledgements."""
from copy import deepcopy
import time
import threading
import unittest
from unittest import mock

from PySide6.QtCore import QEventLoop,QTimer

from desktop.rekordbox_playlists import PlaylistEditController,QUEUE,_remember_collection_pins
from soundbox.rekordbox_write import WriteProofError
import test_playlist_transition_proof as proofs
import test_ui_source_playlists as ui


class PlaylistDeleteRetention(unittest.TestCase):
    @classmethod
    def setUpClass(cls):ui.NativePlaylistGestures.setUpClass()

    def setUp(self):
        proof=proofs.PlaylistTransitionProof('test_folder_pins_capture_descendants_before_delete_and_reconstruct_legacy_replay')
        proof.setUp();self.addCleanup(proof.doCleanups);self.proof=proof
        self.request=proof.populated_folder();self.writer=proof.writer
        fixture=ui.NativePlaylistGestures('test_local_persistence_failure_does_not_admit_native_worker_or_hide_a_track')
        fixture.setUp();self.addCleanup(fixture.tearDown);self.ui=fixture
        self.window=fixture.window;self.controller=fixture.controller
        lib=self.window.lib;lib.source=str(self.writer.database);lib.source_library_id=self.writer.authority.library_id
        lib.source_playlist_catalog=self.writer.snapshot();lib.playlists=[]
        # These native songs need not already be materialized in the browser.
        lib.tracks_by_id={};self.controller.factory=lambda _lib:self.writer
        self.durable={};self.saved=[]
        def save():
            self.durable=deepcopy(self.window.settings);self.saved.append(deepcopy(self.window.settings));return True
        self.window._save_state=save

    def wait(self,predicate):
        end=time.monotonic()+60
        while not predicate() and time.monotonic()<end:
            loop=QEventLoop();QTimer.singleShot(10,loop.quit);loop.exec()
        self.assertTrue(predicate(),repr(self.controller.pending))

    def test_failed_retention_save_never_admits_delete_and_restores_original_owners(self):
        groups={'other-library':{'keep':'exact'}};opaque={'nested':['unchanged']}
        self.window.settings.update(rekordbox_collection_tracks=groups,future=opaque,alias=opaque)
        failed=False;original=self.window._save_state
        def save():
            nonlocal failed
            queue=self.controller.pending
            if queue and 'collection_pins' in queue[0] and not failed:failed=True;return False
            return original()
        self.window._save_state=save;before=self.writer.snapshot()
        self.assertTrue(self.controller.enqueue(self.request))
        self.wait(lambda:bool(self.controller.pending and self.controller.pending[0]['error']) and not self.window.tag_controller.owned)
        self.assertTrue(failed);self.assertEqual(self.writer.snapshot(),before)
        self.assertNotIn('collection_pins',self.controller.pending[0])
        self.assertIs(self.window.settings['rekordbox_collection_tracks'],groups)
        self.assertIs(self.window.settings['future'],opaque);self.assertIs(self.window.settings['alias'],opaque)
        self.assertFalse((self.writer.root/('playlist-'+self.request['operation_id'])).exists())

    def test_failed_delete_ack_restart_reuses_receipt_and_retains_hidden_browser_identities(self):
        failed=False;original=self.window._save_state
        def save():
            nonlocal failed
            if not self.controller.pending and not failed:failed=True;return False
            return original()
        self.window._save_state=save
        self.assertTrue(self.controller.enqueue(self.request))
        self.wait(lambda:bool(self.controller.pending and self.controller.pending[0]['error']) and not self.window.tag_controller.owned)
        expected={'track':'uuid','track-two':'uuid-two'};library=self.writer.authority.library_id
        admitted=[s for s in self.saved if s.get(QUEUE) and 'collection_pins' in s[QUEUE][0]]
        self.assertTrue(admitted);self.assertEqual(admitted[0]['rekordbox_collection_tracks'][library],expected)
        self.assertEqual(self.durable['rekordbox_collection_tracks'][library],expected)
        self.assertEqual(self.controller.pending[0]['request'],self.request)
        source_after=self.writer.database.read_bytes(),self.writer.xml.read_bytes()
        self.controller.stop();self.window.settings=deepcopy(self.durable)
        self.window.settings['newer_user_preference']={'keep':['latest']}
        self.controller=PlaylistEditController(self.window,store_factory=lambda _lib:self.writer)
        self.ui.controller=self.controller;self.window.playlist_controller=self.controller
        self.window._save_state=original
        self.controller.retry();self.wait(lambda:not self.controller.pending and not self.window.tag_controller.owned)
        self.assertEqual(self.window.settings['rekordbox_collection_tracks'][library],expected)
        self.assertEqual(self.window.settings['newer_user_preference'],{'keep':['latest']})
        self.assertEqual((self.writer.database.read_bytes(),self.writer.xml.read_bytes()),source_after)

    def test_conflicting_prior_identity_never_silently_retargets_saved_curation(self):
        groups={self.writer.authority.library_id:{'track':'older-uuid'}}
        settings={'rekordbox_collection_tracks':groups}
        with self.assertRaises(WriteProofError):_remember_collection_pins(settings,self.writer.authority.library_id,{'track':'uuid'})
        self.assertIs(settings['rekordbox_collection_tracks'],groups)

    def test_late_preflight_from_another_library_never_publishes_collection_pins(self):
        entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        original=self.writer.prepare_collection_pins;before=self.writer.snapshot()
        def capture(request):
            pins=original(request);entered.set();release.wait(10);return pins
        with mock.patch.object(self.writer,'prepare_collection_pins',side_effect=capture):
            self.assertTrue(self.controller.enqueue(self.request));self.wait(entered.is_set)
            self.window.lib.source_library_id='different-library';release.set()
            self.wait(lambda:bool(self.controller.pending and self.controller.pending[0]['error']) and not self.window.tag_controller.owned)
        self.assertNotIn('rekordbox_collection_tracks',self.window.settings)
        self.assertNotIn('collection_pins',self.controller.pending[0])
        self.assertEqual(self.writer.snapshot(),before)


if __name__=='__main__':unittest.main()
