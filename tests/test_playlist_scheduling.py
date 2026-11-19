"""Playlist status storms coalesce without losing queued or reentrant work."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from desktop.rekordbox_playlists import PlaylistEditController, QUEUE
from soundbox.rekordbox_playlist_store import request_for
from tests import test_tag_creation_controller as fixtures


class PlaylistScheduling(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        root=Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.window=fixtures.Window(root)
        self.addCleanup(self.window.deleteLater)
        self.tags=self.window.tag_controller
        self.tags.playlist_update_status=''
        self.tags.run_playlist_job=lambda work,done:self.tags._run_source_job('playlists',work,done)
        self.window._native_pending_changed=mock.Mock()
        self.window.lib.source_playlist_catalog=dict(nodes=[dict(ID='1000',
            UUID='00000000-0000-4000-8000-000000001000',Name='Existing',ParentID='root',
            Seq=1,Attribute=0,rb_local_deleted=0)],members=[],revision='a'*64)
        self.controller=PlaylistEditController(self.window,store_factory=lambda _:SimpleNamespace())
        self.addCleanup(self.controller.stop)

    def enqueue(self,name):
        request=request_for(self.window.lib.source_playlist_catalog,'rename',identity='1000',name=name)
        self.assertTrue(self.controller.enqueue(request))
        return request

    def events(self):
        for _ in range(4):self.app.processEvents()

    def test_changed_idle_storm_validates_and_admits_the_current_head_once(self):
        self.enqueue('Saved rename')
        before=deepcopy(self.controller.pending)
        with (mock.patch.object(self.controller,'pump',wraps=self.controller.pump) as pump,
              mock.patch.object(self.controller,'queue_error',wraps=self.controller.queue_error) as validate):
            for _ in range(100):
                self.tags.changed.emit()
                self.tags.idle.emit()
            pump.assert_not_called()
            validate.assert_not_called()
            self.events()
        pump.assert_called_once_with()
        self.assertEqual(validate.call_count,3)  # wake, pump and admission reason
        self.assertEqual(len(self.tags.admitted),1)
        self.assertEqual(self.controller.pending,before)
        self.assertEqual(self.window.saved[-1][QUEUE],before)

    def test_reentrant_wakeup_runs_again_with_the_new_durably_saved_head(self):
        self.enqueue('First')
        self.enqueue('Second')
        delivered=[]
        def pump():
            delivered.append(self.controller.pending[0]['request']['name'])
            if len(delivered)==1:
                self.window.settings[QUEUE]=self.controller.pending[1:]
                self.assertTrue(self.window._save_state())
                for _ in range(100):self.tags.changed.emit()
        with mock.patch.object(self.controller,'pump',side_effect=pump) as called:
            self.events()
        self.assertEqual(delivered,['First','Second'])
        self.assertEqual(called.call_count,2)
        self.assertEqual(self.window.saved[-1][QUEUE],self.controller.pending)
        self.assertFalse(self.tags.admitted)

    def test_stop_or_deletion_cancels_posted_wake_without_dropping_the_intention(self):
        self.controller.stop()
        for action in ('stop','delete'):
            with self.subTest(action=action):
                controller=PlaylistEditController(self.window,store_factory=lambda _:None)
                callback=mock.Mock()
                controller.pump=callback
                request=request_for(self.window.lib.source_playlist_catalog,'rename',identity='1000',name='Preserved')
                saved=[dict(error='',request=request,source=str(self.window.lib.source),
                    library_id=self.window.lib.source_library_id,retry=False)]
                self.window.settings[QUEUE]=saved
                self.assertTrue(self.window._save_state())
                controller.schedule()
                if action=='stop':
                    controller.stop()
                    controller.schedule()
                else:
                    controller.deleteLater()
                    QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
                self.events()
                callback.assert_not_called()
                self.assertIs(self.window.settings[QUEUE],saved)
                if action=='stop':controller.deleteLater()
