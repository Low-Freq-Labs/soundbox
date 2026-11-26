"""Generated stock-delete UI ownership, cancellation and shutdown delivery."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, get_ident
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtCore import QCoreApplication, QEvent, QTimer, Qt
from PySide6.QtWidgets import (QApplication, QLabel, QMessageBox, QProgressBar,
                              QPushButton, QTreeWidgetItem, QWidget)

from desktop.app import MainWindow
from desktop.devices import DeviceFacts
from desktop.export_dialog import ExportSelectionMixin
from desktop.metadata_controller import MetadataController
from desktop.stock_device_view import StockIpodPages
from desktop.tag_controller import TagEditController
from desktop.usb_setup import UsbSetupController
from desktop.workers import OneShotTask
from soundbox.device_operations import (DeviceOperationCoordinator,
                                       DeviceSessionIdentity, Phase)
from soundbox.operation_control import OperationCancelled
from soundbox.stock_ipod import StockIpodDeletePlan, StockIpodDeleteResult


class _StatusLabel(QLabel):
    def reserve(self, _text):
        pass


class StockDeleteLifecycle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.area = TemporaryDirectory(prefix='soundbox-delete-lifecycle-')
        self.addCleanup(self.area.cleanup)
        self.root = Path(self.area.name).resolve()
        self.target = self.root / 'generated-ipod'
        self.target.mkdir()
        self.dev = str(self.target)
        self.token = self.target.stat().st_dev
        self.release = Event()
        self.started = Event()
        self.workers = []
        self.pages = StockIpodPages()
        item = QTreeWidgetItem(self.pages.playlist_tree,
                              ['Favorites', '212', 'iTunes'])
        item.setData(0, Qt.UserRole, '812')
        QTreeWidgetItem(item, ['Song without a Mac association', '', 'iTunes'])
        item.setCheckState(0, Qt.Checked)
        self.pages._playlist_delete_available = True
        self.pages._update_delete_playlist_button()
        self.tags = TagEditController(self.root / 'generated-state')
        self.metadata_status = SimpleNamespace(tags=self.tags, _attention_message='')
        self.coordinator = DeviceOperationCoordinator()
        self.coordinator.observe(DeviceSessionIdentity(self.dev, self.token))
        self.pulse = QTimer()
        self.pulse.setInterval(1000)
        self.status = _StatusLabel()
        self.progress = QProgressBar()
        self.cancel = QPushButton('Cancel')
        self.eject = QPushButton('Eject')
        self.sync = QPushButton('Sync')
        self.questions = []
        self.commits = []
        snapshot = SimpleNamespace(target=self.target, identity_key='generated-device',
            playlists=(SimpleNamespace(playlist_id=812,title='Favorites'),))
        profile = SimpleNamespace(kind='stock_ipod', model='MA446')
        self.host = SimpleNamespace(
            _current_device_path=self.dev, _stock_snapshot=snapshot,
            _stock_view_cache={self.dev: (snapshot, None)},
            _stock_catalog_witness_cache={}, lib=None,
            _device_profiles={self.dev: profile},
            _device_mount_tokens={self.dev: self.token},
            _device_coordinator=self.coordinator, _stock_view_warming=set(),
            _stock_view_pending={}, _pending_device_operation=None,
            _exporting=False, _deleting=False, _cancelling=False,
            _shutting_down=False, _state_maintenance=False,
            _stock_delete_plan_worker=None, _stock_delete_commit_worker=None,
            _dev_scan_worker=None, _eject_verify_worker=None,
            _EJECT_BLOCKING_WORKERS=('_dev_scan_worker', '_stock_delete_plan_worker',
                                     '_stock_delete_commit_worker', '_phase_probe_worker'),
            _usb_format_device='', _usb_format_volumes=(), _export_batch=None,
            _cdj_waiting=None, _source_sync_waiting=None, _dual_sync_queue={},
            _device_finalization_refreshing=False,
            _device_mount_refresh_pending=False, _drift_cache={},
            _prep_update_message='', _preparing_library=False,
            _deferred_edit_status='',
            _refresh_worker=None, _pulse_word=None, _pulse_timer=self.pulse,
            _pulse_dots=0, status_label=self.status, progress=self.progress,
            cancel_btn=self.cancel, eject_btn=self.eject, device_sync_btn=self.sync,
            tag_controller=self.tags,
            settings={'pending_metadata_edits': [{'id': 'unrelated-backlog', 'error': ''}]},
            metadata_controller=SimpleNamespace(
                pending=[{'id': 'unrelated-backlog', 'error': ''}],
                migration_pending=True, queue_error=lambda: '', stop=mock.Mock()),
            stock_art_cache=object(), stock_catalog_witness_store=SimpleNamespace(load=lambda *_:None),
            stock_ipod_shadow_store=None,
            _set_status_count=mock.Mock(),
            _release_eject_ready=mock.Mock(), _stock_write_access=lambda *_: object(),
            _refresh_devices=mock.Mock(), _export_target_result=mock.Mock(),
            _commit_stock_playlist_delete=lambda dev, plan: self.commits.append((dev, plan)),
            _flash_status=mock.Mock(), _artists_refresh=QTimer(),
            _save_columns=mock.Mock(), grid_controller=mock.Mock(),
            findChildren=lambda _kind: [], _state_committer=None)
        self.host.usb_setup_controller = UsbSetupController(self.pages)
        self.host.usb_setup_controller.window = self.host
        for name in (
                '_device_operation_owned', '_stock_has_sync_source',
                '_update_device_action_state', '_defer_device_operation_until_scan_finishes',
                '_start_device_worker', '_finish_worker', '_finish_device_result_worker',
                '_set_device_progress', '_set_status_text', '_start_pulse', '_stop_pulse',
                '_sync_edit_status', '_finish_edit_status', '_begin_stock_playlist_delete',
                '_on_stock_playlist_delete_plan', '_cancel_export',
                '_background_status_available', '_edit_status_deferred',
                '_restore_edit_status', '_finish_stock_delete_worker',
                '_publish_ipod_export_witness', '_device_completion_message'):
            self.bind(name)
        self.host._maybe_finish_export_target = lambda: (
            ExportSelectionMixin._maybe_finish_export_target(self.host))
        self.tags.changed.connect(self.host._sync_edit_status)
        self.pages.delete_playlists_requested.connect(self.host._begin_stock_playlist_delete)
        self.plan = StockIpodDeletePlan(
            self.target, (self.token, self.target.stat().st_ino), 'MA446',
            Path('iPod_Control/iTunes/iTunesDB'), '0' * 64, None, b'generated',
            playlist_ids=(812,), playlist_names=('Favorites',),
            existing_track_count=212, final_track_count=0, deleted_track_count=212)
        self.planner = self.patch('soundbox.stock_ipod.plan_stock_ipod_playlist_delete',
                                  side_effect=self.held_plan)
        self.patch('desktop.app.device_fs', return_value='msdos')
        self.patch('desktop.app.device_facts', return_value=DeviceFacts(
            writable=True, volume_uuid='generated-uuid', usb_family='iPod Video'))
        self.question = self.patch('desktop.app.QMessageBox.question',
                                   side_effect=self.confirm)
        self.warning = self.patch('desktop.app.QMessageBox.warning')
        self.critical = self.patch('desktop.app.QMessageBox.critical')
        self.source_pending = self.patch('desktop.cdj._source_edits_pending',
            side_effect=AssertionError('Source backlog must not gate stock Delete'))
        self.source_error = self.patch('desktop.cdj._source_edits_error',
            side_effect=AssertionError('Source backlog must not gate stock Delete'))
        no_windows = mock.patch.object(
            QWidget, 'show', side_effect=AssertionError('Visible test window'))
        no_windows.start()
        self.addCleanup(no_windows.stop)
        self.addCleanup(self.retire)

    def bind(self, name):
        setattr(self.host, name, lambda *args, _name=name, **kwargs:
                getattr(MainWindow, _name)(self.host, *args, **kwargs))

    def patch(self, target, **kwargs):
        patcher = mock.patch(target, **kwargs)
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def held_plan(self, target, selected, **_options):
        self.assertEqual((target, selected), (self.dev, [812]))
        self.started.set()
        if not self.release.wait(3):
            raise RuntimeError('Generated planner release timed out')
        return self.plan

    def confirm(self, *args):
        self.questions.append(args)
        self.assertIsNone(self.host._stock_delete_plan_worker)
        self.assertIsNone(self.coordinator.active)
        self.assertFalse(self.host._exporting)
        self.assertEqual(args[-1], QMessageBox.Cancel)
        return QMessageBox.Yes

    def admit_delete(self):
        self.assertTrue(self.pages.delete_playlist_button.isEnabled())
        self.pages.delete_playlist_button.click()
        worker = self.host._stock_delete_plan_worker
        self.assertIsNotNone(worker)
        self.workers.append(worker)
        self.assertTrue(self.started.wait(2))
        self.assertTrue(self.host._device_operation_owned())
        self.assertEqual(self.progress.value(), 0)
        self.assertEqual(self.host._pulse_word, 'Planning iPod')
        return worker

    def drain(self, attr='_stock_delete_plan_worker'):
        self.release.set()
        deadline = time.monotonic() + 3
        while getattr(self.host, attr, None) is not None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.001)
        self.assertIsNone(getattr(self.host, attr, None))
        self.assertIsNone(self.coordinator.active)

    def retire(self):
        self.release.set()
        self.host.usb_setup_controller.stop()
        for worker in self.workers:
            try:
                worker.cancel()
                self.assertTrue(worker.wait(3000), 'Generated worker did not retire')
            except RuntimeError:
                pass  # its finished owner already deleted the C++ object
        self.app.processEvents()
        self.tags.busy = False
        self.tags.owner.stop()
        self.pulse.stop()
        for widget in (self.pages, self.status, self.progress,
                       self.cancel, self.eject, self.sync):
            widget.close()
            widget.deleteLater()
        self.tags.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()

    def test_stock_delete_admits_independently_of_unrelated_source_backlog(self):
        self.admit_delete()
        self.source_pending.assert_not_called()
        self.source_error.assert_not_called()
        self.drain()
        self.assertEqual(len(self.questions), 1)
        self.assertFalse(self.host._device_operation_owned())
        self.assertEqual(self.commits, [(self.dev, self.plan)])

    def test_metadata_changed_does_not_replace_active_delete_progress(self):
        self.admit_delete()
        before = (self.status.text(), self.status.toolTip(), self.host._pulse_word,
                  self.pulse.isActive(), self.progress.value(),
                  self.host._set_status_count.call_count)
        MetadataController.status(self.metadata_status, 'Metadata saves pending')
        self.assertEqual(self.tags.update_status_text, 'Metadata saves pending')
        self.assertEqual((self.status.text(), self.status.toolTip(), self.host._pulse_word,
                          self.pulse.isActive(), self.progress.value(),
                          self.host._set_status_count.call_count), before)
        self.assertTrue(self.host._device_operation_owned())
        self.drain()

    def test_source_save_completion_does_not_flash_done_over_delete(self):
        self.admit_delete()
        MetadataController.status(self.metadata_status, 'Metadata saves pending')
        before = (self.status.text(), self.status.toolTip(),
                  self.host._pulse_word, self.progress.value())
        self.tags.metadata_update_status = ''
        self.host._finish_edit_status()
        self.host._flash_status.assert_not_called()
        self.assertEqual((self.status.text(), self.status.toolTip(),
                          self.host._pulse_word, self.progress.value()), before)
        self.drain()

    def test_source_notifications_preserve_foreground_device_phase_owners(self):
        for intent, phase, label in (
                ('export', Phase.EXECUTING, 'Exporting'),
                ('commit-stock', Phase.EXECUTING, 'Syncing iPod'),
                ('plan-delete', Phase.PLANNING, 'Planning iPod'),
                ('eject', Phase.EJECTING, 'Ejecting')):
            with self.subTest(intent=intent):
                self.started.clear()
                self.release.clear()
                self.tags.metadata_update_status = ''
                self.tags.busy = False
                self.tags._busy_kind = ''
                self.host._prep_update_message = ''
                def held():
                    self.started.set()
                    if not self.release.wait(3):
                        raise RuntimeError('Generated phase release timed out')
                worker = OneShotTask(held)
                self.workers.append(worker)
                self.host._phase_probe_worker = worker
                worker.finished.connect(lambda w=worker:
                    self.host._finish_worker('_phase_probe_worker', w))
                self.host._start_pulse(label)
                self.host._start_device_worker(worker, self.dev, intent)
                self.coordinator.active.phase = phase
                self.host._set_device_progress(410)
                self.assertTrue(self.started.wait(2))
                try:
                    before = (self.status.text(), self.status.toolTip(),
                              self.host._pulse_word, self.progress.value())
                    MetadataController.status(self.metadata_status, 'Metadata saves pending')
                    self.assertEqual((self.status.text(), self.status.toolTip(),
                                      self.host._pulse_word, self.progress.value()), before)
                    self.tags.busy = True
                    self.tags._busy_kind = 'tags'
                    self.tags.changed.emit()
                    self.assertEqual(self.tags.update_status_text, 'Updating My Tags…')
                    self.assertEqual((self.status.text(), self.status.toolTip(),
                                      self.host._pulse_word, self.progress.value()), before)
                finally:
                    self.tags.busy = False
                    self.tags._busy_kind = ''
                    self.drain('_phase_probe_worker')
                    self.host._stop_pulse()

    def test_planning_cancel_retires_without_refusal_or_stale_busy_state(self):
        worker = self.admit_delete()
        self.host._cancel_export()
        self.assertTrue(worker.cancellation.is_set())
        self.drain()
        self.assertIsInstance(worker.result, OperationCancelled)
        self.assertFalse(self.host._exporting)
        self.assertFalse(self.host._cancelling)
        self.assertTrue(self.cancel.isHidden())
        self.assertTrue(self.progress.isHidden())
        self.assertTrue(self.host._background_status_available())
        self.assertEqual(self.host.settings['pending_metadata_edits'],
                         [{'id': 'unrelated-backlog', 'error': ''}])
        self.question.assert_called_once()
        self.warning.assert_not_called()
        self.critical.assert_not_called()
        self.assertEqual(self.commits, [])

    def test_retired_device_keeps_routine_metadata_quiet_and_preserves_terminal_notice(self):
        for terminal in (False, True):
            with self.subTest(terminal_notice=terminal):
                self.started.clear()
                self.release.clear()
                self.tags.metadata_update_status = ''
                self.host._prep_update_message = ''
                self.host._deferred_edit_status = ''
                signals = []
                changed = lambda: signals.append(True)
                self.tags.changed.connect(changed)
                def held():
                    self.started.set()
                    if not self.release.wait(3):
                        raise RuntimeError('Generated replay release timed out')
                worker = OneShotTask(held)
                self.workers.append(worker)
                self.host._phase_probe_worker = worker
                terminal_text = 'iPod deletion cancelled — nothing written'
                def completed(_result):
                    # The device presenter retires its own progress; routine
                    # metadata no longer supplies a foreground completion.
                    self.host._stop_pulse()
                    if terminal:
                        self.host._set_status_text(terminal_text)
                    else:
                        self.host._set_status_text('Ready')
                worker.finished.connect(lambda w=worker:
                    self.host._finish_device_result_worker(
                        '_phase_probe_worker', w, completed))
                self.host._start_pulse('Checking iPod')
                self.host._start_device_worker(worker, self.dev, 'inspect')
                self.assertTrue(self.started.wait(2))
                try:
                    MetadataController.status(self.metadata_status, 'Metadata saves pending')
                    self.assertEqual(self.status.text(), 'Checking iPod')
                    self.drain('_phase_probe_worker')
                    self.assertEqual(signals, [True])
                    if terminal:
                        self.assertEqual(self.status.text(), terminal_text)
                        self.host._set_status_text('Ready')
                    self.assertEqual(self.tags.update_status_text, 'Metadata saves pending')
                    self.assertEqual(self.tags.footer_status_text, '')
                    self.assertEqual(self.status.text(), 'Ready')
                    self.host._flash_status.assert_not_called()
                    self.assertIsNone(self.host._pulse_word)
                    self.assertEqual(signals, [True])
                finally:
                    self.drain('_phase_probe_worker')
                    self.tags.changed.disconnect(changed)

    def test_disconnected_planning_owner_retires_without_promotion_or_busy_flags(self):
        self.admit_delete()
        self.coordinator.disconnect(self.dev)
        self.drain()
        self.assertFalse(self.host._exporting)
        self.assertFalse(self.host._cancelling)
        self.assertFalse(self.host._device_operation_owned())
        self.assertTrue(self.progress.isHidden())
        self.assertTrue(self.cancel.isHidden())
        self.question.assert_called_once()
        self.warning.assert_not_called()
        self.critical.assert_not_called()
        self.assertEqual(self.commits, [])

    def test_confirmation_cancel_never_commits_or_leaves_cancel_controls(self):
        def decline(*args):
            self.confirm(*args)
            return QMessageBox.Cancel
        self.question.side_effect = decline
        self.pages.delete_playlist_button.click()
        worker = self.host._stock_delete_plan_worker
        if worker is not None:
            self.workers.append(worker)
        self.assertIsNone(worker, 'Declining must not start read-only device preparation')
        self.assertFalse(self.started.is_set())
        self.assertEqual(len(self.questions), 1)
        self.assertEqual(self.commits, [])
        self.assertFalse(self.host._exporting)
        self.assertFalse(self.host._cancelling)
        self.assertTrue(self.progress.isHidden())
        self.assertTrue(self.cancel.isHidden())
        self.warning.assert_not_called()
        self.critical.assert_not_called()

    def test_only_explicit_yes_commits_the_exact_read_only_plan(self):
        def yes(*args):
            self.confirm(*args)
            return QMessageBox.Yes
        self.question.side_effect = yes
        self.admit_delete()
        self.drain()
        self.assertEqual(self.commits, [(self.dev, self.plan)])
        self.question.assert_called_once()
        self.warning.assert_not_called()
        self.critical.assert_not_called()

    def test_confirmation_does_not_adopt_replaced_mount_or_playlist(self):
        for changed in ('mount', 'playlist'):
            with self.subTest(changed=changed):
                self.questions.clear(); self.question.reset_mock()
                snapshot = self.host._stock_snapshot
                snapshot.playlists = (SimpleNamespace(playlist_id=812,title='Favorites'),)
                def accepted(*args):
                    self.confirm(*args)
                    if changed == 'mount':
                        self.coordinator.disconnect(self.dev)
                        self.coordinator.observe(DeviceSessionIdentity(self.dev,self.token))
                    else:
                        snapshot.playlists = (SimpleNamespace(playlist_id=812,title='Replaced playlist'),)
                    return QMessageBox.Yes
                self.question.side_effect = accepted
                self.host._begin_stock_playlist_delete((('812','Favorites'),))
                self.assertIsNone(self.host._stock_delete_plan_worker)
                self.assertFalse(self.started.is_set())
                self.assertEqual(self.commits, [])
                self.question.assert_called_once()

    def test_confirmed_plan_does_not_delete_a_replaced_playlist_title(self):
        self.admit_delete()
        self.plan = replace(self.plan,playlist_names=('Replaced playlist',))
        self.drain()
        self.assertEqual(self.commits, [])
        self.question.assert_called_once()
        self.warning.assert_called_once()

    def test_renewed_delete_witness_saves_off_gui_and_cache_failure_stays_committed(self):
        from soundbox.stock_ipod_catalog_state import StockCatalogWitness
        witness = StockCatalogWitness(('/generated/master.db','rekordbox-db','generated'),
            'generated-device','1'*64,())
        self.plan = replace(self.plan,catalog_witness=witness)
        result = StockIpodDeleteResult(self.target/'.soundbox/generated-recovery',
            ('Favorites',),1,1,100,2,1,'1'*64,catalog_witness=witness,
            catalog_notice='Routine association diagnostic')
        self.bind('_commit_stock_playlist_delete')
        self.bind('_on_stock_playlist_delete_commit')
        self.host._stock_device_key = lambda *_:'generated-device'
        self.host._finalize_ipod_operation = lambda _dev, complete:complete()
        for fail in (False, True):
            with self.subTest(cache_failure=fail):
                self.warning.reset_mock()
                saved = []
                def save(value):
                    saved.append((value,get_ident()))
                    if fail:raise OSError('Generated cache save failure')
                self.host.stock_catalog_witness_store = SimpleNamespace(save=save)
                with mock.patch('soundbox.stock_ipod.commit_stock_ipod_playlist_delete',return_value=result):
                    self.host._commit_stock_playlist_delete(self.dev,self.plan)
                    worker = self.host._stock_delete_commit_worker
                    self.workers.append(worker)
                    self.drain('_stock_delete_commit_worker')
                self.assertEqual(len(saved),1)
                self.assertIs(saved[0][0],witness)
                self.assertNotEqual(saved[0][1],get_ident())
                self.assertIs(self.host._stock_catalog_witness_cache[self.dev],witness)
                self.assertTrue(self.coordinator.last_result.committed)
                self.assertEqual(self.host._last_device_completion['attention'],fail)
                self.assertIn('Routine association diagnostic',self.host._last_device_completion['text'])
                self.assertEqual(self.warning.call_count,int(fail))
                if fail:
                    self.assertIn('cache save failure',self.warning.call_args.args[2])
                    self.assertNotIn('Routine association diagnostic',self.warning.call_args.args[2])
                self.critical.assert_not_called()

    def test_shutdown_drains_held_delete_before_closing_dependencies(self):
        self.admit_delete()
        self.host.usb_setup_controller.timer.start()
        closed = []
        def close_dependencies(_host):
            self.assertTrue(self.host.usb_setup_controller.closing)
            self.assertFalse(self.host.usb_setup_controller.timer.isActive())
            self.assertIsNone(self.host._stock_delete_plan_worker)
            self.assertIsNone(self.coordinator.active)
            closed.append(True)
        QTimer.singleShot(0, self.release.set)
        with mock.patch.object(MainWindow, '_close_library_processes', side_effect=close_dependencies), \
                mock.patch.object(MainWindow, '_flush_settings_preferences'):
            MainWindow.shutdown(self.host)
        self.app.processEvents()
        self.assertTrue(self.host._shutting_down)
        self.assertEqual(closed, [True])
        self.assertIsNone(self.host._stock_delete_plan_worker)
        self.assertIsNone(self.coordinator.active)
        self.question.assert_called_once()
        self.warning.assert_not_called()
        self.critical.assert_not_called()
        self.assertEqual(self.commits, [])

    def test_cancel_after_database_publication_reports_committed_cleanup_result(self):
        result = StockIpodDeleteResult(
            recovery_directory=self.target / '.soundbox/generated-recovery',
            playlists=('Favorites',), deleted_track_records=212,
            deleted_media_files=210, deleted_bytes=8192,
            remaining_tracks=0, remaining_playlists=0,
            database_sha256='1' * 64,
            cleanup_failures=(
                'iPod_Control/Music/F00/UNKNOWN1.wav: cleanup cancelled',
                'iPod_Control/Music/F00/UNKNOWN2.wav: cleanup cancelled'),
            cleanup_cancelled=True)
        def committed(_plan):
            self.started.set()
            if not self.release.wait(3):
                raise RuntimeError('Generated committed cleanup release timed out')
            return result
        self.bind('_commit_stock_playlist_delete')
        self.bind('_on_stock_playlist_delete_commit')
        self.host._finalize_ipod_operation = lambda _dev, complete: complete()
        with mock.patch('soundbox.stock_ipod.commit_stock_ipod_playlist_delete',
                        side_effect=committed):
            self.host._commit_stock_playlist_delete(self.dev, self.plan)
            worker = self.host._stock_delete_commit_worker
            self.workers.append(worker)
            self.assertTrue(self.started.wait(2))
            self.host._cancel_export()
            self.drain('_stock_delete_commit_worker')
        self.assertIs(worker.result, result)
        self.assertTrue(self.coordinator.last_result.committed)
        self.assertFalse(self.coordinator.last_result.cancelled)
        self.assertFalse(self.host._exporting)
        self.assertFalse(self.host._cancelling)
        self.critical.assert_not_called()
        self.question.assert_not_called()
        self.assertEqual(self.warning.call_count, 1)
        _host, title, body = self.warning.call_args.args
        self.assertIn('attention', title.lower())
        self.assertNotIn('212', body)
        self.assertNotIn('210', body)
        self.assertIn('UNKNOWN1.wav', body)
        self.assertIn('UNKNOWN2.wav', body)
        self.assertNotIn('rollback', body.lower())
        diagnostic = self.host._last_device_completion
        self.assertIn('212', diagnostic['text'])
        self.assertIn('210', diagnostic['text'])
        footer = self.host._flash_status.call_args.args[0]
        self.assertIn('deleted', footer.lower())
        self.assertIn('cleanup', footer.lower())
