"""Real encrypted TEMP receipts must prove controller FIFO causal rebasing."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from PySide6.QtWidgets import QApplication

from desktop.rekordbox_playlists import PlaylistEditController,QUEUE
from soundbox.library import Library,Playlist
from soundbox import rekordbox_playlist_store as store
import test_source_playlists as source
from test_tag_creation_controller import Window


class PlaylistControllerRebase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        fixture=source.SourcePlaylists('test_catalog_snapshot_reads_committed_wal_without_rewriting_source_or_rendering_backups')
        fixture.setUp();self.addCleanup(fixture.doCleanups)
        self.writer=store.PlaylistStore(replace(fixture.writer.authority,retain_full_backups=True),fixture.writer.root)
        area=Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.window=Window(area);self.addCleanup(self.window.deleteLater)
        self.window.tag_controller.playlist_update_status=''
        initial=self.writer.snapshot()
        self.window.lib=Library({},[Playlist('Existing',[],[],'1000')],str(self.writer.database),
            source_kind='rekordbox-db',source_library_id=self.writer.authority.library_id,
            source_playlist_catalog=initial)
        self.window._native_pending_changed=mock.Mock();self.window._native_reconcile_source=mock.Mock()
        self.window._native_refresh_view=mock.Mock()
        def publish(catalog,request):
            self.window.lib.source_playlist_catalog=deepcopy(catalog)
            node=next(row for row in catalog['nodes'] if row['ID']=='1000')
            self.window.lib.playlists=[Playlist(node['Name'],[],[],'1000')]
        self.window._native_apply_catalog=publish
        tags=self.window.tag_controller
        tags.run_playlist_job=lambda work,done:tags._run_source_job('playlists',work,done)
        self.controller=PlaylistEditController(self.window,store_factory=lambda _:self.writer)
        self.addCleanup(self.controller.stop);self.controller.schedule=mock.Mock()
        self.enterContext(mock.patch('desktop.rekordbox_playlists.assert_rekordbox_closed'))

    def rename(self,name):
        return store.request_for(self.window.lib.source_playlist_catalog,'rename',identity='1000',name=name)

    def turn(self):
        before=len(self.window.tag_controller.admitted)
        self.controller.pump();self.assertEqual(len(self.window.tag_controller.admitted),before+1)
        self.window.tag_controller.deliver();self.controller.timer.stop()

    def native_name(self):
        return next(row['Name'] for row in self.writer.snapshot()['nodes'] if row['ID']=='1000')

    def test_failed_ack_then_external_native_edit_preserves_tail_until_its_own_explicit_retry(self):
        first=self.rename('First');second=self.rename('Queued Second')
        # A durable XML clock can lead wall time after queued clock chaining.
        # That timestamp must never substitute for a proven catalog transition.
        second['xml_timestamp']+=60000
        self.assertTrue(self.controller.enqueue(first));self.assertTrue(self.controller.enqueue(second))
        captured_tail=deepcopy(self.controller.pending[1]['request'])
        self.window.save_ok=False;self.turn()
        self.assertTrue(self.controller.pending[0]['error']);self.assertEqual(self.native_name(),'First')
        self.window.save_ok=True
        external=store.request_for(self.writer.snapshot(),'rename',identity='1000',name='Independent Native Rename')
        self.writer.apply(external)
        self.assertIsNone(self.writer.committed_transition(first))
        source_bytes=self.writer.database.read_bytes(),self.writer.xml.read_bytes()
        self.controller.retry();self.turn();self.turn()
        self.assertEqual(len(self.controller.pending),1)
        self.assertEqual(self.controller.pending[0]['request'],captured_tail)
        self.assertIn('outside this saved operation',self.controller.pending[0]['error'])
        self.assertEqual(self.window.saved[-1][QUEUE],self.controller.pending)
        self.controller.pump();self.assertEqual(self.native_name(),'Independent Native Rename')
        self.assertEqual((self.writer.database.read_bytes(),self.writer.xml.read_bytes()),source_bytes)
        # Explicitly retrying THIS preserved tail renews its CAS before SQL.
        self.controller.retry();self.turn()
        renewed=deepcopy(self.controller.pending[0]['request'])
        self.assertNotEqual(renewed['operation_id'],captured_tail['operation_id'])
        self.assertEqual(self.window.saved[-1][QUEUE][0]['request'],renewed)
        self.turn();self.assertFalse(self.controller.pending)
        self.assertEqual(self.native_name(),'Queued Second')

    def test_real_committed_edge_advances_happy_fifo_tail_and_persists_it_before_admission(self):
        first=self.rename('First');second=self.rename('Second')
        self.assertTrue(self.controller.enqueue(first));self.assertTrue(self.controller.enqueue(second))
        with mock.patch.object(self.writer,'committed_transition',wraps=self.writer.committed_transition) as prove:
            self.turn();self.assertEqual(self.native_name(),'First')
            tail=self.controller.pending[0]
            self.assertEqual(tail['request']['revision'],self.writer.snapshot()['revision'])
            self.assertEqual(tail['error'],'');self.assertEqual(self.window.saved[-1][QUEUE],[tail])
            self.turn();self.assertFalse(self.controller.pending)
            self.assertEqual(self.native_name(),'Second');self.assertEqual(prove.call_count,2)

    def test_unchanged_revision_noop_needs_no_receipt_and_keeps_following_edit_runnable(self):
        first=self.rename('Existing');second=self.rename('Second')
        self.assertTrue(self.controller.enqueue(first));self.assertTrue(self.controller.enqueue(second))
        before=self.writer.database.read_bytes(),self.writer.xml.read_bytes()
        with mock.patch.object(self.writer,'committed_transition',wraps=self.writer.committed_transition) as prove:
            self.turn();prove.assert_not_called()
            self.assertEqual((self.writer.database.read_bytes(),self.writer.xml.read_bytes()),before)
            self.assertFalse((self.writer.root/('playlist-'+first['operation_id'])).exists())
            self.assertEqual(self.controller.pending[0]['error'],'')
            self.turn();self.assertEqual(prove.call_count,1)
        self.assertFalse(self.controller.pending);self.assertEqual(self.native_name(),'Second')


if __name__=='__main__':unittest.main()
