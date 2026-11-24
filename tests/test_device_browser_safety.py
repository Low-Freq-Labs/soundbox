"""Device confirmation boundaries use generated local folders, never devices."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from desktop import device_browser


class DeviceBrowserSafety(unittest.TestCase):
    def test_delete_checks_every_selected_path_before_reducing_exact_confirmation(self):
        from soundbox.sync import DeviceSession
        with tempfile.TemporaryDirectory() as area:
            target = Path(area).resolve() / 'device'; target.mkdir()
            a = target / 'Music' / 'A'; a.mkdir(parents=True)
            b = target / 'Music' / 'B'; b.mkdir()
            (a / 'first.wav').write_bytes(b'first')
            (b / 'second.wav').write_bytes(b'second')
            selected = [a / 'first.wav', b / 'second.wav', a, a / 'first.wav']
            checked = []; original = DeviceSession.guard
            owner = SimpleNamespace(_device_operation_owned=lambda: False,
                _defer_device_operation_until_scan_finishes=lambda _resume: False)
            def guard(session, path):
                checked.append(path)
                return original(session, path)
            def cancel(_owner, title, message):
                self.assertEqual(checked, selected)
                self.assertEqual((title, message), ('Delete',
                    'Permanently delete 2 item(s)?\n\nsecond.wav\nA'))
                return device_browser.QMessageBox.Cancel
            with (mock.patch.object(device_browser, '_on_external_device', return_value=True),
                  mock.patch.object(device_browser, '_in_external_product_tree', return_value=False),
                  mock.patch.object(device_browser, '_external_volume_of', return_value=target),
                  mock.patch.object(DeviceSession, 'guard', autospec=True, side_effect=guard),
                  mock.patch.object(device_browser.QMessageBox, 'question', side_effect=cancel) as confirmation,
                  mock.patch.object(device_browser, 'DeleteWorker', side_effect=AssertionError('Cancelled delete started'))):
                device_browser.DeviceBrowserMixin._delete_paths(owner, selected)
                confirmation.assert_called_once()
            self.assertEqual((a / 'first.wav').read_bytes(), b'first')
            self.assertEqual((b / 'second.wav').read_bytes(), b'second')

    def test_delete_guard_refuses_redirect_before_normalization_or_confirmation(self):
        with tempfile.TemporaryDirectory() as area:
            root = Path(area).resolve(); target = root / 'device'; target.mkdir()
            outside = root / 'outside.wav'; outside.write_bytes(b'outside original')
            redirected = target / 'redirect.wav'; redirected.symlink_to(outside)
            owner = SimpleNamespace(_device_operation_owned=lambda: False,
                _defer_device_operation_until_scan_finishes=lambda _resume: False)
            with (mock.patch.object(device_browser, '_on_external_device', return_value=True),
                  mock.patch.object(device_browser, '_in_external_product_tree', return_value=False),
                  mock.patch.object(device_browser, '_external_volume_of', return_value=target),
                  mock.patch.object(device_browser, 'top_level_paths', side_effect=AssertionError('Reduced unchecked paths')),
                  mock.patch.object(device_browser.QMessageBox, 'question', side_effect=AssertionError('Confirmed unchecked paths')),
                  mock.patch.object(device_browser.QMessageBox, 'critical') as refusal):
                device_browser.DeviceBrowserMixin._delete_paths(owner, [redirected])
                refusal.assert_called_once()
            self.assertEqual(outside.read_bytes(), b'outside original')

    def test_erase_confirmation_cannot_authorize_a_same_name_replacement_volume(self):
        with tempfile.TemporaryDirectory() as area:
            root = Path(area).resolve()
            target = root / 'device'
            target.mkdir()
            (target / 'confirmed-original').write_bytes(b'confirmed volume')
            replacement_file = target / 'replacement-original'
            workers = []
            owner = SimpleNamespace(
                _device_profiles={},
                _device_operation_owned=lambda: False,
                _defer_device_operation_until_scan_finishes=lambda _resume: False,
                _release_eject_ready=lambda: None,
                progress=mock.Mock(),
                _start_pulse=lambda _label: None,
                _start_device_worker=lambda *args: workers.append(args[0]),
                _finish_erase_device_worker=lambda *_args: None,
            )
            def replace_during_confirmation(*_args):
                target.rename(root / 'confirmed-device')
                target.mkdir()
                replacement_file.write_bytes(b'replacement original')
                return device_browser.QMessageBox.Yes
            def task(run):
                return SimpleNamespace(run=run, finished=mock.Mock())
            with mock.patch.object(device_browser, '_external_volume_of', return_value=target), \
                    mock.patch.object(device_browser.QMessageBox, 'question', side_effect=replace_during_confirmation), \
                    mock.patch.object(device_browser, 'OneShotTask', side_effect=task):
                device_browser.DeviceBrowserMixin._erase_device(owner, str(target))
            self.assertEqual(len(workers), 1)
            failure = None
            try:
                workers[0].run()
            except RuntimeError as exc:
                failure = exc
            self.assertTrue(replacement_file.exists(), 'replacement volume was erased')
            self.assertEqual(replacement_file.read_bytes(), b'replacement original')
            self.assertEqual((root / 'confirmed-device' / 'confirmed-original').read_bytes(),
                             b'confirmed volume')
            self.assertIsNotNone(failure)
