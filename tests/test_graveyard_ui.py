"""Actual window review/selection/durable acknowledgement without real disposal."""
from copy import deepcopy
import json
import hashlib
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

from PySide6.QtCore import QPoint,Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QAbstractItemView

from soundbox.library import Library,Track
from soundbox import graveyard
from soundbox import analysis
from desktop.removal import KEY,DONE,REVIEW


class GraveyardUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        from desktop.app import MainWindow
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name).resolve()
        with mock.patch('soundbox.rekordbox_db.db_available',return_value=False),mock.patch.object(MainWindow,'_refresh_devices'):
            self.w=MainWindow(isolated_dir=self.root/'state')
        self.w._ensure_settings_page()
        self.addCleanup(self.close);w=self.w
        self.path=self.root/'chosen.wav';self.path.write_bytes(b'private synthetic audio')
        tracks={tid:Track(tid,tid,'Artist',self.path.as_uri()) for tid in ('chosen','untouched')}
        w.lib=Library(tracks,[],self.root/'master.db',source_kind='rekordbox-db',source_library_id='123')
        w.hidden={'chosen','numeric'};w.hidden_dates={'chosen':'2026-11-01','numeric':'2026-11-01'}
        w.tag_controller.configure(w.lib,True);w._state_maintenance=False
        w.graveyard_controller.timer.stop();w.graveyard_controller.schedule=lambda *_:None
        self.entries={'chosen':dict(id='chosen',native_id='1',native=dict(uuid='u'),path=str(self.path),canonical=str(self.path),
            display=dict(title='Recovered name',artist='Artist'),exists=True,size=self.path.stat().st_size,allocated=4096,epoch=[1,2,3,4,5]),
            'numeric':dict(id='numeric',path='',error='No verified file path is available')}
        w.graveyard_controller.entries=deepcopy(self.entries);w._refresh_graveyard()
        self.confirm=mock.Mock(return_value=False);w.removal_controller.review=self.confirm
        self.plan=dict(selections=[dict(id='chosen',native_id='1',path=str(self.path),title='Recovered name',native_active=True)],
            audio=[dict(path=str(self.path),size=self.path.stat().st_size,sha256='a'*64)],playlists=['2'])
        self.folder=self.root/'prepared';self.folder.mkdir();(self.folder/'plan.json').write_text(json.dumps(self.plan))
        self.sha=hashlib.sha256((self.folder/'plan.json').read_bytes()).hexdigest()
        self.prepared=dict(folder=str(self.folder),sha256=self.sha,plan=self.plan)
        self.store=mock.Mock();self.store.prepare.return_value=self.prepared
        self.store.apply.return_value=dict(outcome='committed',operation_id='test-operation',plan_sha256=self.sha,trash={str(self.path):'/private/fake-trash/chosen.wav'})
        w.removal_controller.factory=lambda _lib:self.store
        self.enterContext(mock.patch.object(w.tag_controller,'_recover_pending'))
        self.enterContext(mock.patch.object(w,'_device_operation_owned',return_value=False))
        self.enterContext(mock.patch.object(w,'_after_track_edit'))
        self.enterContext(mock.patch.object(w,'_flash_status'))
        w._grave.selectAll()

    def close(self):self.w.shutdown();self.w.close();self.w.deleteLater();self.app.processEvents()
    def wait(self,predicate):
        until=time.monotonic()+5
        while not predicate() and time.monotonic()<until:QTest.qWait(5)
        self.assertTrue(predicate())

    def test_delete_menu_is_selection_scoped_and_cancel_never_saves_or_applies_disposal(self):
        w=self.w;menu=mock.Mock();actions=[];menu.addAction.side_effect=lambda title,*_:actions.append(title)
        with mock.patch('desktop.settings_page.QMenu',return_value=menu):w._grave_menu(QPoint())
        self.assertIn('Delete from Collection and disk…',actions)
        w.removal_controller.request();self.wait(lambda:self.confirm.called)
        self.assertEqual(self.confirm.call_args.args[1],['numeric']);self.store.apply.assert_not_called();self.assertNotIn(KEY,w.settings)
        self.assertTrue(self.path.exists());self.assertEqual(w.hidden,{'chosen','numeric'})

    def test_confirmation_persists_before_apply_then_only_committed_receipt_removes_presentation(self):
        w=self.w;self.confirm.return_value=True;w.titles['chosen']='Curated'
        record=dict(engine=analysis.ENGINE,path=str(self.path),epoch=analysis.identity(self.path.stat()),size=self.path.stat().st_size,
            sha256='a'*64,duration=4,bpm=120,key='8A',beats=[[1,0,120],[2,500,120],[3,1000,120]],first_cue=None)
        saved={analysis.result_key(self.path):record};w.settings['analysis_results']=deepcopy(saved)
        def check(*_):
            state=json.loads(w._data_file.read_bytes());self.assertEqual(state['settings'][KEY]['sha256'],self.sha)
            return self.store.apply.return_value
        self.store.apply.side_effect=check;w.removal_controller.request()
        self.wait(lambda:'chosen' in w.settings.get(DONE,{}))
        self.assertNotIn(KEY,w.settings);self.assertEqual(w.settings['analysis_results'],saved)
        self.assertEqual(w.titles['chosen'],'Curated');self.assertEqual(w.graveyard_controller.ids(),{'numeric'})
        self.assertTrue(self.path.exists());w._after_track_edit.assert_called_once()

    def test_failed_local_confirmation_save_never_reaches_apply_and_unrelated_queue_does_not_block(self):
        w=self.w;self.confirm.return_value=True;w.settings['analysis_jobs']=[dict(track_id='untouched')]
        self.assertEqual(w.removal_controller.reason(['chosen']),'')
        with mock.patch.object(w,'_save_state',return_value=False):
            w.removal_controller.request();self.app.processEvents()
        self.confirm.assert_not_called();self.store.prepare.assert_not_called()
        self.store.apply.assert_not_called();self.assertNotIn(KEY,w.settings)
        w.settings['pending_prep_edits']=[dict(track_id='chosen')]
        self.assertEqual(w.removal_controller.reason(['chosen']),'')
        w.removal_controller.request();self.assertTrue(w.settings[REVIEW]);self.store.prepare.assert_not_called()
        w.settings['pending_prep_edits']=[];w.removal_controller.pump();self.wait(lambda:self.confirm.called)

    def test_delete_queues_durably_while_conversion_runs_then_reviews_without_another_click(self):
        import threading
        w=self.w;entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        def converting():entered.set();release.wait(4)
        self.assertTrue(w.tag_controller.run_media_job(converting,lambda _job:None));self.wait(entered.is_set)
        w.removal_controller.request();self.assertEqual(w.removal_controller.reason(['chosen']),'')
        self.assertTrue(w.tag_controller.foreground_waiting);self.store.prepare.assert_not_called()
        saved=json.loads(w._data_file.read_bytes())['settings'][REVIEW]
        self.assertEqual(set(saved[0]['ids']),{'chosen','numeric'})
        w._grave.clearSelection();release.set();self.wait(lambda:self.confirm.called)
        self.assertFalse(w.settings.get(REVIEW));self.store.apply.assert_not_called()
        self.assertFalse(w.tag_controller.foreground_waiting)

    def test_unrelated_active_analysis_does_not_hold_delete_and_new_selection_replaces_failed_retry(self):
        from types import SimpleNamespace
        from uuid import uuid4
        w=self.w;worker=SimpleNamespace(tracks=[w.lib.tracks_by_id['untouched']],cancel=mock.Mock())
        w._analysis_worker=worker
        # A failed old confirmation must not hijack a different current selection.
        w.settings[KEY]=dict(ids=['old-selection'],error='previous Trash failure')
        w.settings[REVIEW]=[dict(id=str(uuid4()),source=str(w.lib.source),library_id='123',ids=['old-selection'],error='old prepare failure')]
        w.removal_controller.request();self.wait(lambda:self.confirm.called)
        self.assertEqual({e['id'] for e in self.store.prepare.call_args.args[0]},{'chosen'})
        self.store.completed.assert_not_called();worker.cancel.assert_not_called()
        w._analysis_worker=None

    def test_queued_review_resolves_metadata_even_when_graveyard_has_not_been_opened(self):
        w=self.w;w.graveyard_controller.entries={}
        with mock.patch('soundbox.graveyard.catalog',return_value=deepcopy(self.entries)) as catalog:
            w.removal_controller.request();self.wait(lambda:self.confirm.called)
        catalog.assert_called_once();self.store.apply.assert_not_called()

    def test_confirmed_delete_cancels_only_selected_analysis_and_keeps_other_jobs_and_saved_data(self):
        from types import SimpleNamespace
        w=self.w;self.confirm.return_value=True;cancel=mock.Mock(side_effect=lambda:setattr(w,'_analysis_worker',None))
        w._analysis_worker=SimpleNamespace(tracks=[w.lib.tracks_by_id['chosen']],cancel=cancel)
        self.addCleanup(lambda:setattr(w,'_analysis_worker',None))
        w.settings['analysis_jobs']=[dict(track_id='chosen'),dict(track_id='untouched')]
        w._analysis_queue=[(w.lib,w.lib.tracks_by_id[tid],{},tid) for tid in ('chosen','untouched')]
        w.removal_controller.request();self.wait(lambda:'chosen' in w.settings.get(DONE,{}))
        cancel.assert_called_once();self.assertEqual(w.settings['analysis_jobs'],[dict(track_id='untouched')])
        self.assertEqual([item[1].track_id for item in w._analysis_queue],['untouched'])

    def test_stats_use_selected_files_and_disposed_rows_stay_out_without_purging_curation(self):
        w=self.w;w.graveyard_controller.selection_changed()
        self.assertIn('1 files',w._grave_storage.text());self.assertIn('1 paths unresolved',w._grave_storage.text())
        self.assertEqual(graveyard.totals(self.entries)['size'],self.path.stat().st_size)
        w.settings[DONE]={'chosen':dict(path=str(self.path))};w._refresh_graveyard()
        self.assertEqual(w._grave.topLevelItemCount(),1);self.assertIn('chosen',w.hidden)

    def test_confirmed_absent_unresolved_entry_can_be_cleared_without_a_source_writer_or_file_operation(self):
        w=self.w;self.confirm.return_value=True
        w.graveyard_controller.entries['numeric'].update(native_absent=True,native_id='42',
            proof=dict(kind='native-absent-snapshot',database_sha256='a'*64))
        w._grave.clearSelection()
        from desktop.settings_page import _GRAVE_ROLE
        for i in range(w._grave.topLevelItemCount()):
            item=w._grave.topLevelItem(i)
            if item.data(0,_GRAVE_ROLE)[1]=='numeric':item.setSelected(True)
        original=self.path.read_bytes();w.removal_controller.request()
        self.wait(lambda:'numeric' in w.settings.get(DONE,{}))
        self.store.prepare.assert_not_called();self.store.apply.assert_not_called()
        self.assertEqual(self.path.read_bytes(),original)
        self.assertEqual(w.graveyard_controller.ids(),{'chosen'})
        self.assertIn('numeric',w.hidden)
        self.assertEqual(w.settings[DONE]['numeric']['kind'],'unresolved-entry-dismissal')

    def test_failed_receipt_save_preserves_durable_intent_for_idempotent_retry(self):
        w=self.w;job=dict(folder=str(self.folder),sha256=self.sha,selection_sha256='x',source=str(w.lib.source),library_id='123',ids=['chosen'],error='')
        w.settings[KEY]=deepcopy(job)
        with mock.patch.object(w,'_save_state',return_value=False):w.removal_controller.acknowledge(job,self.store.apply.return_value)
        self.assertEqual(w.settings[KEY],job);self.assertNotIn(DONE,w.settings)
        w.removal_controller.acknowledge(job,self.store.apply.return_value);self.assertIn('chosen',w.settings[DONE]);self.assertNotIn(KEY,w.settings)

    def test_global_search_matches_graveyard_metadata_and_clears_invisible_selection(self):
        w=self.w;w.overrides['chosen']='Custom Artist';self.entries['chosen']['display']['bpm']='140.00'
        w.graveyard_controller.entries=deepcopy(self.entries)
        w.search.setText('custom 140');self.app.processEvents()
        items={item.data(0,Qt.UserRole+11)[1]:item for item in (w._grave.topLevelItem(i) for i in range(w._grave.topLevelItemCount()))}
        self.assertFalse(items['chosen'].isHidden());self.assertTrue(items['numeric'].isHidden())
        self.assertFalse(items['numeric'].isSelected())
        w.search.setText('unfindable');self.assertTrue(all(item.isHidden() for item in items.values()))
        w.search.clear();self.assertFalse(any(item.isHidden() for item in items.values()))

    def test_restore_keeps_viewport_anchor_for_middle_and_first_visible_rows(self):
        w=self.w;g=w._grave
        tracks={f't{i:03}':Track(f't{i:03}',f'Track {i:03}','Artist',self.path.as_uri()) for i in range(200)}
        w.lib.tracks_by_id.update(tracks);w.hidden=set(tracks);w.hidden_dates={tid:'2026-11-01' for tid in tracks}
        w._refresh_graveyard();g.sortByColumn(0,Qt.AscendingOrder);g.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        w.show();w._open_settings()
        for i in range(w._settings_tabs.count()):
            if w._settings_tabs.tabText(i)=='Graveyard':w._settings_tabs.setCurrentIndex(i)
        self.app.processEvents();g.scrollToItem(g.topLevelItem(80),QAbstractItemView.PositionAtTop)
        selected=g.topLevelItem(85);g.setCurrentItem(selected);self.app.processEvents()
        anchor=g.itemAt(1,1).data(0,Qt.UserRole+11)[1];before=g.verticalScrollBar().value()
        with mock.patch.object(w,'_maybe_fill_waveforms'):w._grave_restore_selected()
        self.app.processEvents();self.assertEqual(g.itemAt(1,1).data(0,Qt.UserRole+11)[1],anchor)
        self.assertEqual(g.verticalScrollBar().value(),before)
        first=g.itemAt(1,1);following=g.itemBelow(first).data(0,Qt.UserRole+11)[1];g.setCurrentItem(first)
        with mock.patch.object(w,'_maybe_fill_waveforms'):w._grave_restore_selected()
        self.app.processEvents();self.assertEqual(g.itemAt(1,1).data(0,Qt.UserRole+11)[1],following)
