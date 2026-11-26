"""Committed USB warning presentation, using RAM snapshots and no device reads."""
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

from desktop.cdj import CdjDeviceMixin
from desktop.usb_device_view import UsbPlaylist, UsbSnapshot
from soundbox.cdj_transaction import CommittedCleanupPending


class CdjCleanupFeedback(unittest.TestCase):
    def test_device_lifecycle_adapter_retains_committed_warning_and_recovery_outcome(self):
        from soundbox.device_operations import DeviceCommand, DeviceSessionIdentity, adapter_result
        manifest = {'generation': 'saved'}
        error = CommittedCleanupPending(manifest, OSError('cleanup unavailable'))
        outcome = adapter_result(DeviceCommand(DeviceSessionIdentity('/synthetic/USB', 71), 'commit-cdj'), error)
        self.assertTrue(outcome.committed)
        self.assertFalse(outcome.cancelled)
        self.assertEqual(outcome.recovery, 'committed')
        self.assertIn('Export saved', outcome.failure)
        self.assertIs(error.result, manifest)

    def test_saved_cleanup_warning_invalidates_stale_rows_and_retains_batch_warning_without_read(self):
        for batch in (None, {'active': 'synthetic USB'}):
            with self.subTest(batch=bool(batch)):
                dev = '/synthetic/USB'
                previous = UsbSnapshot(Path(dev), 71, None,
                    playlists=(UsbPlaylist('Old playlist', '/old.m3u8', ()),))
                result = CommittedCleanupPending({'generation': 'saved'}, OSError('cleanup unavailable'))
                host = SimpleNamespace(_exporting=True, _cancelling=True, _export_batch=batch,
                    _stop_pulse=mock.Mock(), cancel_btn=mock.Mock(), progress=mock.Mock(),
                    _sync_cue_editing=mock.Mock(), _export_target_result=mock.Mock(),
                    _flash_status=mock.Mock(), _update_device_action_state=mock.Mock(),
                    _usb_view_cache={dev: previous}, _usb_view_requests={dev},
                    _device_mount_tokens={dev: 71}, _current_device_path=dev,
                    _current_device_role='', _device_profiles={dev:SimpleNamespace(kind='generic_usb')},
                    _present_usb_device=mock.Mock())
                with mock.patch('desktop.cdj.QMessageBox.warning') as warning, \
                        mock.patch('desktop.cdj.device_facts', side_effect=AssertionError('completion device read')):
                    CdjDeviceMixin._finish_cdj(host, dev, result)
                self.assertFalse(host._exporting)
                self.assertFalse(host._cancelling)
                self.assertEqual(host._usb_view_cache[dev].playlists, ())
                self.assertIn('cleanup is pending', host._usb_view_cache[dev].notice)
                self.assertNotIn(dev, host._usb_view_requests)
                host._present_usb_device.assert_called_once_with(dev)
                self.assertIn('saved', host._flash_status.call_args.args[0])
                self.assertNotIn('stopped', host._flash_status.call_args.args[0])
                self.assertEqual(warning.called, not bool(batch))
                if batch: host._export_target_result.assert_called_once_with(dev, result)

    def test_precommit_failure_keeps_prior_catalog_and_reports_stopped(self):
        dev = '/synthetic/USB'
        previous = object()
        host = SimpleNamespace(_exporting=True, _cancelling=True, _export_batch=None,
            _stop_pulse=mock.Mock(), cancel_btn=mock.Mock(), progress=mock.Mock(),
            _sync_cue_editing=mock.Mock(), _flash_status=mock.Mock(),
            _update_device_action_state=mock.Mock(), _usb_view_cache={dev: previous})
        with mock.patch('desktop.cdj.QMessageBox.warning') as warning:
            CdjDeviceMixin._finish_cdj(host, dev, OSError('copy failed before commit'))
        self.assertIs(host._usb_view_cache[dev], previous)
        self.assertEqual(host._flash_status.call_args.args[0], 'CDJ USB operation stopped')
        self.assertIn('stopped', warning.call_args.args[1])
