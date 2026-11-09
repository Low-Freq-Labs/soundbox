"""Device confirmation boundaries use generated local folders, never devices."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from desktop import device_browser


class DeviceBrowserSafety(unittest.TestCase):
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
