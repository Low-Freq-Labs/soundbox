"""Dormant native-only removal uses real TEMP SQL and owned source callbacks."""
from copy import deepcopy
from dataclasses import replace
import json
import hashlib
from pathlib import Path
import time
import unittest
from unittest import mock

from PySide6.QtTest import QTest

from desktop.removal import RemovalController,KEY,REVIEW,DONE
from soundbox import analysis,graveyard,rekordbox_removal_store as store
from soundbox.library import Library,Track
from soundbox.state_repository import read_state
from tests import test_graveyard_ui as ui_fixture,test_removal_store as source_fixture


class NativeOnlyController(unittest.TestCase):
    @classmethod
    def setUpClass(cls):ui_fixture.GraveyardUI.setUpClass()

    def setUp(self):
        self.source=source_fixture.RemovalStore();self.source.setUp();self.addCleanup(self.source.doCleanups)
        self.ui=ui_fixture.GraveyardUI();self.ui.setUp();self.addCleanup(self.ui.doCleanups)
        self.w=self.ui.w;self.c=self.w.removal_controller
        track=Track('chosen','Current title','Artist',self.source.audio.as_uri(),source_native_id='1',source_uuid=source_fixture.UUID)
        self.w.lib=Library({'chosen':track},[],self.source.writer.database,source_kind='rekordbox-db',source_library_id='123')
        self.w.hidden=set();self.w.hidden_dates={};self.w.graveyard_controller.entries={}
        self.w.tag_controller.configure(self.w.lib,True)
        self.writer=store.RemovalStore(self.source.authority,self.w.tag_controller.data_root)
        self.c.factory=lambda _lib:self.writer;self.c.collection_enabled=True
        self.c.review=mock.Mock(return_value=False)
        class SelectedReadback(dict):
            def values(self):raise AssertionError('No complete Collection traversal during acknowledgement')
        def load(_source):return replace(self.w.lib,tracks_by_id=SelectedReadback(),playlists=[])
        self.c.collection_loader=load;self.loader=load
        self.enterContext(mock.patch.object(self.w,'_publish_created_library',side_effect=lambda library:setattr(self.w,'lib',library)))
        self.enterContext(mock.patch.object(store,'_audio_regular',side_effect=AssertionError('No audio inspection')))
        self.enterContext(mock.patch.object(store,'acquire_source',side_effect=AssertionError('No audio reads')))
        self.enterContext(mock.patch.object(self.writer,'_guard_trash',side_effect=AssertionError('No Trash access')))

    def wait(self,predicate):
        until=time.monotonic()+30
        while time.monotonic()<until:
            self.ui.app.processEvents()
            if predicate():return
            for worker in tuple(self.w.tag_controller.owner._workers):
                self.assertTrue(worker.wait(30_000),'Source worker did not join')
            self.ui.app.processEvents();QTest.qWait(1)
        self.fail('Native-only controller did not settle: '+repr(self.w.settings.get(KEY)))

    def test_production_default_keeps_native_only_request_disabled_until_restore_is_proven(self):
        self.c.collection_enabled=False;before=self.source.state()
        captured=self.c.capture_collection(['chosen'])
        self.assertFalse(self.c.request_collection(captured));self.assertFalse(self.w.settings.get(REVIEW))
        self.assertEqual(self.source.state(),before);self.assertTrue(self.source.audio.exists())

    def test_selection_is_durable_before_review_and_cancel_never_applies_native_removal(self):
        captured=self.c.capture_collection(['chosen']);before=self.source.state()
        self.assertTrue(self.c.request_collection(captured))
        persisted=read_state(self.w._data_file)['settings'][REVIEW][0]
        self.assertEqual(persisted['entries'],captured['entries']);self.assertTrue(persisted['native_only'])
        with mock.patch.object(self.writer,'apply',wraps=self.writer.apply) as apply:
            self.c.pump();self.wait(lambda:self.c.review.called and not self.w.settings.get(REVIEW))
            apply.assert_not_called()
        self.assertEqual(self.source.state(),before);self.assertFalse(self.w.hidden)
        self.assertTrue(self.source.audio.exists());self.assertTrue(self.source.resource.exists())

    def test_success_ack_retains_history_and_all_private_curation_without_disposal_receipts(self):
        self.c.review.return_value=True;self.w.titles={'chosen':'My curated title'}
        self.w.overrides={'chosen':'My curated artist'}
        audio=self.source.audio;data=audio.read_bytes()
        record=dict(engine=analysis.ENGINE,path=str(audio),epoch=analysis.identity(audio.stat()),size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),duration=4,bpm=120,key='8A',first_cue=None,
            beats=[[1,0,120],[2,500,120],[3,1000,120]])
        self.w.settings['analysis_results']={analysis.result_key(audio):record}
        preserved=deepcopy(self.w.settings['analysis_results'])
        raw=(self.source.audio.read_bytes(),self.source.resource.read_bytes())
        self.assertTrue(self.c.request_collection(self.c.capture_collection(['chosen'])))
        self.c.pump();self.wait(lambda:'chosen' in self.w.hidden and KEY not in self.w.settings)
        self.assertEqual((self.source.audio.read_bytes(),self.source.resource.read_bytes()),raw)
        self.assertFalse(self.w.settings.get(DONE));self.assertEqual(self.w.titles,{'chosen':'My curated title'})
        self.assertEqual(self.w.overrides,{'chosen':'My curated artist'})
        self.assertEqual(self.w.settings['analysis_results'],preserved)
        archive=json.loads(graveyard.archive_path(self.w.tag_controller.data_root,self.writer.database,'123').read_bytes())
        remembered=archive['tracks']['chosen']
        self.assertEqual((remembered['id'],remembered['uuid'],remembered['path']),('1',source_fixture.UUID,str(self.source.audio)))
        self.assertEqual(remembered['display']['title'],'My curated title')
        self.assertFalse(self.w.lib.tracks_by_id)

    def test_source_uuid_or_path_changes_before_click_cannot_retarget_the_selection(self):
        captured=self.c.capture_collection(['chosen']);original=self.w.lib;track=original.tracks_by_id['chosen']
        for library in (replace(original,source=Path('/synthetic/other.db')),
                        replace(original,tracks_by_id={'chosen':replace(track,source_uuid='00000000-0000-4000-8000-000000000099')}),
                        replace(original,tracks_by_id={'chosen':replace(track,source_native_path='/synthetic/other.wav')})):
            with self.subTest(source=library.source):
                self.w.lib=library;self.assertFalse(self.c.request_collection(captured))
                self.assertFalse(self.w.settings.get(REVIEW));self.assertFalse(self.w.hidden)
        self.w.lib=original

    def test_worker_retains_canonical_alias_only_for_exact_unique_surviving_native_identity(self):
        old=Track('logical-survivor','Keep','Artist','file:///canonical/keep.wav',source_native_id='2',
                  source_uuid='00000000-0000-4000-8000-000000000002',source_native_path='/raw/keep.wav')
        stale=Track('old-replaced','Replace','Artist','file:///canonical/replaced.wav',source_native_id='3',
                    source_uuid='00000000-0000-4000-8000-000000000003',source_native_path='/raw/replaced.wav')
        self.w.lib=replace(self.w.lib,tracks_by_id={**self.w.lib.tracks_by_id,old.track_id:old,stale.track_id:stale})
        fresh=replace(old,track_id='2',location_uri='file:///raw/keep.wav',source_native_path='')
        replaced=replace(stale,track_id='3',source_uuid='00000000-0000-4000-8000-000000000099',
                         location_uri='file:///raw/replaced.wav',source_native_path='')
        library=replace(self.w.lib,tracks_by_id={'2':fresh,'3':replaced},playlists=[])
        self.c.collection_loader=lambda _source:library;self.c.review.return_value=True
        self.assertTrue(self.c.request_collection(self.c.capture_collection(['chosen'])))
        self.c.pump();self.wait(lambda:'chosen' in self.w.hidden and KEY not in self.w.settings)
        self.assertEqual((str(fresh.path),fresh.source_native_path),('/canonical/keep.wav','/raw/keep.wav'))
        self.assertEqual((str(replaced.path),replaced.source_native_path),('/raw/replaced.wav',''))

    def test_failed_ack_restart_replays_committed_native_removal_without_another_sql_write(self):
        self.c.review.return_value=True;save=self.w._save_state
        def fail_ack():
            return False if 'chosen' in self.w.hidden and KEY not in self.w.settings else save()
        with mock.patch.object(store,'_apply',wraps=store._apply) as apply:
            with mock.patch.object(self.w,'_save_state',side_effect=fail_ack):
                self.assertTrue(self.c.request_collection(self.c.capture_collection(['chosen'])))
                self.c.pump();self.wait(lambda:bool(self.w.settings.get(KEY,{}).get('error')))
                self.assertFalse(self.w.hidden);self.assertTrue(self.source.audio.exists())
                old=deepcopy(self.w.settings[KEY]);writes=apply.call_count;source_bytes=self.writer.database.read_bytes()
            self.c.stop();self.w.settings=deepcopy(read_state(self.w._data_file)['settings'])
            self.c=RemovalController(self.w,factory=lambda _lib:self.writer,review=mock.Mock(),collection_enabled=True,
                                     collection_loader=self.loader)
            self.w.removal_controller=self.c;self.c.pump()
            self.assertEqual(self.w.settings[KEY],old);self.assertFalse(self.w.hidden)
            self.c.pump(retry=True)
            self.wait(lambda:'chosen' in self.w.hidden and KEY not in self.w.settings)
            self.assertEqual(apply.call_count,writes);self.assertEqual(self.writer.database.read_bytes(),source_bytes)
            self.assertEqual(old['source'],str(self.writer.database));self.c.review.assert_not_called()


if __name__=='__main__':unittest.main()
