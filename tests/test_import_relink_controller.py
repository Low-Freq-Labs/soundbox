"""Actual manual relink assembly on owned audio, canonical state and windows."""
from copy import deepcopy
from dataclasses import replace
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import wave

from PySide6.QtWidgets import QApplication,QMessageBox

from soundbox import imports,file_locations
from soundbox.library import Library
from soundbox.state_repository import StateRepository


class ManualRelinkController(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        from desktop.app import MainWindow
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        with mock.patch('soundbox.rekordbox_db.db_available',return_value=False), \
                mock.patch.object(MainWindow,'_refresh_devices'):
            self.window=MainWindow(isolated_dir=self.root/'state')
        self.addCleanup(self.close)
        original=self.root/'set.wav'
        with wave.open(str(original),'wb') as stream:
            stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(8000)
            stream.writeframes(b'\x00\x00'*16000)
        self.tid='sb-owned-relink'
        record=dict(path=str(original),size=original.stat().st_size,time=2,
                    added='2026-11-26',audio_sha256=hashlib.sha256(original.read_bytes()).hexdigest())
        self.bookmark=file_locations.create_bookmark(original)
        self.window.imported={self.tid:record}
        self.window.lib=Library({self.tid:imports.make_track(self.tid,record)},[],str(self.root/'source.xml'),
                               source_kind='rekordbox-xml',source_library_id='owned-relink')
        self.window._refresh_custom=mock.Mock();self.window._reload_current_view=mock.Mock()
        self.window._arm_waveform_rechecks=mock.Mock();self.window._maybe_fill_waveforms=mock.Mock()
        self.window._flash_status=mock.Mock();self.window._start_pulse=mock.Mock();self.window._stop_pulse=mock.Mock()
        self.assertTrue(self.window._save_state())
        self.candidate=self.root/'moved/set.wav';self.candidate.parent.mkdir();original.rename(self.candidate)
        self.old=str(original)

    def close(self):
        self.window.shutdown();self.window.close();self.window.deleteLater();self.app.processEvents()

    def capture(self):
        scope=self.window._capture_relink_scope(imports.missing(self.window.imported))
        self.window._relink_search=scope
        result=imports.find_moved(scope['records'],[self.candidate.parent])
        return scope,result

    def assert_not_published(self):
        self.assertEqual(self.window.imported[self.tid]['path'],self.old)
        self.assertEqual(str(self.window.lib.tracks_by_id[self.tid].path),self.old)
        self.window._refresh_custom.assert_not_called();self.window._reload_current_view.assert_not_called()
        self.window._arm_waveform_rechecks.assert_not_called();self.window._maybe_fill_waveforms.assert_not_called()
        self.assertFalse(any('✓' in str(call) for call in self.window._flash_status.call_args_list))

    def test_real_canonical_save_precedes_library_preparation_and_success(self):
        scope,result=self.capture();self.assertEqual(result,{self.tid:self.candidate})
        save=self.window._save_state;before=deepcopy(self.window.imported)
        def durably_saved():
            self.assertEqual(str(self.window.lib.tracks_by_id[self.tid].path),self.old)
            self.window._arm_waveform_rechecks.assert_not_called()
            return save()
        with mock.patch.object(self.window,'_save_state',side_effect=durably_saved), \
                mock.patch('desktop.app.QTimer.singleShot') as preparation:
            self.window._on_links_found(result,expected=scope)
        saved=StateRepository(self.window._state_repository.path).load()['imported']
        self.assertEqual(saved[self.tid]['path'],str(self.candidate))
        self.assertEqual(self.window.imported,saved)
        self.assertEqual(self.window.lib.tracks_by_id[self.tid].path,self.candidate)
        self.assertEqual(saved[self.tid]['audio_sha256'],before[self.tid]['audio_sha256'])
        self.window._arm_waveform_rechecks.assert_called_once();preparation.assert_called_once()
        self.window._flash_status.assert_called_with('Relinked 1 ✓',15)

    def test_false_save_restores_exact_records_and_canonical_state_without_prep_or_success(self):
        scope,result=self.capture();previous=self.window.imported;before=deepcopy(previous)
        canonical=self.window._state_repository.path.read_bytes()
        with mock.patch.object(self.window,'_save_state',return_value=False), \
                mock.patch('desktop.app.QMessageBox.question',side_effect=AssertionError('False success prompted retry')):
            self.window._on_links_found(result,expected=scope)
        self.assertIs(self.window.imported,previous);self.assertEqual(self.window.imported,before)
        self.assertEqual(self.window._state_repository.path.read_bytes(),canonical)
        self.assert_not_published()
        self.assertIn('NOT saved',self.window._flash_status.call_args.args[0])

    def test_real_storage_failure_preserves_previous_ui_and_records(self):
        scope,result=self.capture();before=deepcopy(self.window.imported)
        with mock.patch.object(self.window._state_repository,'save',side_effect=OSError('owned fixture full')):
            self.window._on_links_found(result,expected=scope)
        self.assertEqual(self.window.imported,before);self.assert_not_published()

    def test_late_search_cannot_overwrite_newer_accepted_record_or_song(self):
        scope,result=self.capture()
        newer=self.root/'accepted.wav';newer.write_bytes(b'newer explicit selection')
        self.window.imported[self.tid]['path']=str(newer)
        self.window.lib.tracks_by_id[self.tid]=imports.make_track(self.tid,self.window.imported[self.tid])
        self.assertTrue(self.window._save_state())
        with mock.patch.object(self.window,'_save_state') as save:
            self.window._on_links_found(result,expected=scope)
        save.assert_not_called()
        self.assertEqual(self.window.imported[self.tid]['path'],str(newer))
        self.assertEqual(self.window.lib.tracks_by_id[self.tid].path,newer)
        self.window._arm_waveform_rechecks.assert_not_called()

    def test_source_replacement_and_restore_epoch_expire_captured_search(self):
        scope,result=self.capture();owner=self.window.lib;repository=self.window._state_repository
        for change in ('source','generation','area'):
            with self.subTest(change=change),mock.patch.object(self.window,'_save_state') as save:
                if change=='source':
                    self.window.lib=replace(owner)
                    self.window._on_links_found(result,expected=scope);self.window.lib=owner
                else:
                    target=repository if change=='generation' else repository.area
                    with mock.patch.object(target,'generation',getattr(target,'generation')+1):
                        self.window._on_links_found(result,expected=scope)
                save.assert_not_called();self.assert_not_published()

    def test_replaced_candidate_and_changed_song_binding_expire_worker_proof(self):
        scope,result=self.capture()
        replacement=self.root/'replacement.wav';replacement.write_bytes(self.candidate.read_bytes())
        replacement.replace(self.candidate)
        with mock.patch.object(self.window,'_save_state') as save, \
                mock.patch.object(QMessageBox,'question',return_value=QMessageBox.No):
            self.window._on_links_found(result,expected=scope)
        save.assert_not_called();self.assert_not_published()
        scope,result=self.capture()
        self.window.lib.tracks_by_id[self.tid].source_uuid='new song identity'
        with mock.patch.object(self.window,'_save_state') as save:
            self.window._on_links_found(result,expected=scope)
        save.assert_not_called();self.assert_not_published()

    def test_native_binding_and_binding_issue_never_become_local_relink_success(self):
        scope,result=self.capture()
        self.window.lib.tracks_by_id[self.tid].imported=False
        self.window.lib.tracks_by_id[self.tid].source_native_id='42'
        with mock.patch.object(self.window,'_save_state') as save:
            self.window._on_links_found(result,expected=scope)
        save.assert_not_called();self.assert_not_published()
        self.assertEqual(self.window._capture_relink_scope(imports.missing(self.window.imported))['records'],{})
        self.window.lib.tracks_by_id[self.tid]=imports.make_track(self.tid,self.window.imported[self.tid])
        self.window.lib.source_binding_issues[self.tid]='native target missing'
        self.assertEqual(self.window._capture_relink_scope(imports.missing(self.window.imported))['records'],{})

    def test_legacy_suggestion_and_incomplete_search_do_not_claim_relink_success(self):
        self.window.imported[self.tid].pop(imports.AUDIO_SHA256_KEY)
        scope,result=self.capture()
        self.assertEqual(result,{});self.assertEqual(result.suggestions,{self.tid:self.candidate})
        with mock.patch.object(QMessageBox,'question',return_value=QMessageBox.No), \
                mock.patch.object(self.window,'_save_state') as save:
            self.window._on_links_found(result,expected=scope)
        save.assert_not_called();self.assert_not_published()
        result.complete=False
        self.window._on_links_found(result,expected=scope)
        self.assertIn('could not finish',self.window._flash_status.call_args.args[0])
        self.assert_not_published()

    def test_format_owner_refuses_new_search_and_rejects_manual_and_startup_path_publication(self):
        scope,result=self.capture();startup_scope,startup=self.startup_capture()
        with mock.patch.object(self.window,'_usb_format_device','/synthetic/formatting'), \
                mock.patch.object(self.window,'_save_state') as save, \
                mock.patch('desktop.app.QFileDialog.getExistingDirectory') as picker, \
                mock.patch('desktop.workers.OneShotTask') as task:
            self.window._fix_links()
            self.window._fix_links([self.candidate.parent])
            picker.assert_not_called();task.assert_not_called()
            self.assertFalse(self.window._relink_scope_current(scope))
            self.window._on_links_found(result,expected=scope)
            self.window._on_import_locations_repaired(startup,expected=startup_scope)
        save.assert_not_called();self.assert_not_published()

    def test_startup_location_repair_deferred_by_format_resumes_to_real_durable_bookmark_move(self):
        from PySide6.QtTest import QTest
        self.assertTrue(self.bookmark)
        self.window.imported[self.tid][file_locations.BOOKMARK_KEY]=self.bookmark
        with mock.patch.object(self.window,'_usb_format_device','/synthetic/formatting'), \
                mock.patch('desktop.workers.OneShotTask') as task:
            self.window._repair_import_locations();self.window._repair_import_locations()
            self.assertTrue(self.window._usb_format_import_repair_pending)
            self.assertIsNone(self.window._import_location_worker);task.assert_not_called()
        with mock.patch.object(self.window,'_refresh_devices'):
            self.window._finish_usb_format('/synthetic/formatting',None,cancelled=True)
            remaining=2000
            while (self.window.imported[self.tid]['path']==self.old
                   or self.window._import_location_worker is not None) and remaining>0:
                QTest.qWait(10);remaining-=10
        self.assertFalse(self.window._usb_format_import_repair_pending)
        self.assertIsNone(self.window._import_location_worker)
        self.assertEqual(self.window.imported[self.tid]['path'],str(self.candidate))
        self.assertEqual(self.window.lib.tracks_by_id[self.tid].path,self.candidate)
        self.assertEqual(StateRepository(self.window._state_repository.path).load()['imported'],self.window.imported)

    def test_actual_async_search_ignores_a_superseded_delivery(self):
        from PySide6.QtTest import QTest
        self.window._fix_links([self.candidate.parent])
        expected=self.window._relink_search
        self.assertEqual(expected['records'][self.tid]['path'],self.old)
        self.window._relink_search=dict(expected)
        deadline=1000
        while self.window._oneshots and deadline>0:QTest.qWait(10);deadline-=10
        self.assertFalse(self.window._oneshots)
        self.assert_not_published()

    def test_picker_cannot_retarget_records_captured_before_it_opened(self):
        def newer_link(*_args):
            self.window.imported[self.tid]['path']=str(self.root/'newer accepted.wav')
            return str(self.candidate.parent)
        with mock.patch('desktop.app.QFileDialog.getExistingDirectory',side_effect=newer_link), \
                mock.patch('desktop.workers.OneShotTask',side_effect=AssertionError('Stale picker started search')):
            self.window._fix_links()
        self.assertEqual(self.window.imported[self.tid]['path'],str(self.root/'newer accepted.wav'))
        self.window._start_pulse.assert_not_called()

    def test_candidate_replacement_during_bookmark_capture_does_not_reach_canonical_save(self):
        scope,result=self.capture()
        def replaced(_path):
            self.candidate.write_bytes(b'another song')
            return 'wrong new locator'
        with mock.patch('desktop.app.file_locations.create_bookmark',side_effect=replaced), \
                mock.patch.object(QMessageBox,'question',return_value=QMessageBox.No), \
                mock.patch.object(self.window,'_save_state') as save:
            self.window._on_links_found(result,expected=scope)
        save.assert_not_called();self.assert_not_published()

    def startup_capture(self):
        self.assertTrue(self.bookmark,'Owned macOS fixture requires actual Core Foundation bookmarks')
        self.window.imported[self.tid][file_locations.BOOKMARK_KEY]=self.bookmark
        scope=self.window._capture_relink_scope(self.window.imported)
        with mock.patch.object(file_locations,'spotlight_matches',side_effect=AssertionError('Same-entity bookmark searched')):
            result=file_locations.repair_locations(scope['records'])
        self.assertEqual(result.moved_count,1)
        return scope,result

    def test_startup_same_entity_bookmark_saves_before_publishing_and_retains_curation(self):
        scope,result=self.startup_capture()
        self.window.titles[self.tid]='Saved manual title';self.window.custom['Owned set']=[self.tid]
        save=self.window._save_state
        def before_publication():
            self.assertEqual(str(self.window.lib.tracks_by_id[self.tid].path),self.old)
            self.window._refresh_custom.assert_not_called()
            return save()
        with mock.patch.object(self.window,'_save_state',side_effect=before_publication), \
                mock.patch.object(self.window,'_build_artist_registry'):
            self.window._on_import_locations_repaired(result,expected=scope)
        saved=StateRepository(self.window._state_repository.path).load()
        self.assertEqual(saved['imported'][self.tid]['path'],str(self.candidate))
        self.assertEqual(self.window.lib.tracks_by_id[self.tid].path,self.candidate)
        self.assertEqual(saved['title_overrides'][self.tid],'Saved manual title')
        self.assertEqual(saved['custom']['Owned set'],[self.tid])
        self.window._flash_status.assert_called_with('Relinked 1 moved file(s) ✓',15)

    def test_startup_false_save_restores_exact_records_without_track_refresh_or_success(self):
        scope,result=self.startup_capture();previous=self.window.imported;before=deepcopy(previous)
        with mock.patch.object(self.window,'_save_state',return_value=False), \
                mock.patch.object(self.window,'_build_artist_registry') as registry:
            self.window._on_import_locations_repaired(result,expected=scope)
        self.assertIs(self.window.imported,previous);self.assertEqual(self.window.imported,before)
        registry.assert_not_called();self.assert_not_published()

    def test_startup_same_entity_move_keeps_original_bookmark_if_refresh_is_unavailable(self):
        self.assertTrue(self.bookmark)
        self.window.imported[self.tid][file_locations.BOOKMARK_KEY]=self.bookmark
        scope=self.window._capture_relink_scope(self.window.imported)
        with mock.patch.object(file_locations,'create_bookmark',return_value=None):
            result=file_locations.repair_locations(scope['records'])
        self.assertEqual(result.moved_count,1)
        self.window._on_import_locations_repaired(result,expected=scope)
        self.assertEqual(self.window.imported[self.tid]['path'],str(self.candidate))
        self.assertEqual(self.window.imported[self.tid][file_locations.BOOKMARK_KEY],self.bookmark)

    def test_startup_source_aba_restore_and_newer_record_expire_exact_capture(self):
        scope,result=self.startup_capture();owner=self.window.lib;repository=self.window._state_repository
        for change in ('source-aba','restore','record','binding'):
            self.window.lib=owner
            record=deepcopy(self.window.imported[self.tid])
            with self.subTest(change=change),mock.patch.object(self.window,'_save_state') as save:
                if change=='source-aba':
                    self.window.lib=replace(owner,source='other source');self.window.lib=replace(owner)
                    self.window._on_import_locations_repaired(result,expected=scope)
                elif change=='restore':
                    with mock.patch.object(repository.area,'generation',repository.area.generation+1):
                        self.window._on_import_locations_repaired(result,expected=scope)
                elif change=='record':
                    self.window.imported[self.tid]['time']=999
                    self.window._on_import_locations_repaired(result,expected=scope)
                else:
                    owner.tracks_by_id[self.tid].source_native_id='42'
                    self.window._on_import_locations_repaired(result,expected=scope)
                    owner.tracks_by_id[self.tid].source_native_id=''
                save.assert_not_called()
            self.window.imported[self.tid]=record;self.window.lib=owner
        self.assert_not_published()

    def test_startup_legacy_spotlight_suggestion_and_replaced_bookmark_target_never_publish(self):
        scope=self.window._capture_relink_scope(self.window.imported)
        with mock.patch.object(file_locations,'spotlight_matches',return_value={self.tid:self.candidate}):
            result=file_locations.repair_locations(scope['records'])
        self.assertEqual(result.updates,{})
        with mock.patch.object(self.window,'_save_state') as save:
            self.window._on_import_locations_repaired(result,expected=scope)
        save.assert_not_called();self.assert_not_published()

        scope,result=self.startup_capture()
        replacement=self.root/'replacement.wav';replacement.write_bytes(self.candidate.read_bytes())
        replacement.replace(self.candidate)
        with mock.patch.object(self.window,'_save_state') as save:
            self.window._on_import_locations_repaired(result,expected=scope)
        save.assert_not_called();self.assert_not_published()

    def test_startup_actual_async_owner_rejects_source_aba(self):
        from PySide6.QtTest import QTest
        self.assertTrue(self.bookmark)
        self.window.imported[self.tid][file_locations.BOOKMARK_KEY]=self.bookmark
        owner=self.window.lib
        with mock.patch.object(self.window,'_save_state') as save:
            self.window._repair_import_locations()
            self.assertIsNotNone(self.window._import_location_worker)
            self.window.lib=replace(owner,source='other source');self.window.lib=replace(owner)
            remaining=1000
            while self.window._import_location_worker is not None and remaining>0:QTest.qWait(10);remaining-=10
            self.assertIsNone(self.window._import_location_worker)
        save.assert_not_called();self.assert_not_published()

    def test_startup_healthy_bookmark_backfill_is_durable_and_false_save_does_not_leak_locator(self):
        self.window.imported[self.tid]['path']=str(self.candidate)
        self.window.lib.tracks_by_id[self.tid]=imports.make_track(self.tid,self.window.imported[self.tid])
        scope=self.window._capture_relink_scope(self.window.imported)
        result=file_locations.repair_locations(scope['records'])
        self.assertEqual(result.moved_count,0)
        self.assertEqual(result.updates[self.tid].identity,'existing-path')
        before=deepcopy(self.window.imported)
        with mock.patch.object(self.window,'_save_state',return_value=False):
            self.window._on_import_locations_repaired(result,expected=scope)
        self.assertEqual(self.window.imported,before)
        self.window._flash_status.assert_not_called();self.window._reload_current_view.assert_not_called()
        self.window._on_import_locations_repaired(result,expected=scope)
        self.assertTrue(self.window.imported[self.tid][file_locations.BOOKMARK_KEY])
        saved=StateRepository(self.window._state_repository.path).load()['imported']
        self.assertEqual(saved,self.window.imported)
        self.window._flash_status.assert_not_called();self.window._reload_current_view.assert_not_called()


if __name__=='__main__':unittest.main()
