"""Hermetic connection UI: fake service, no Keychain, browser or network access."""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from threading import Event
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QStandardPaths, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit

from desktop.connections import CredentialsDialog, SoundCloudSection
from soundbox.providers import ConnectionFailure

_app = None


def setUpModule():
    global _app
    QStandardPaths.setTestModeEnabled(True)
    _app = QApplication.instance() or QApplication([])


class Service:
    def __init__(self):
        self.state = {"configured": False, "connected": False}
        self.saved = ("", "", "")
        self.connects = []

    def status(self):
        return dict(self.state)

    def credentials(self):
        return self.saved

    def connect_public(self, client_id, secret, profile, stop, progress):
        self.saved = client_id, secret, profile
        self.connects.append(self.saved)
        self.state = {"configured": True, "connected": True, "mode": "public", "username": "Test user", "count": 42}

    def refresh(self, stop, progress):
        raise AssertionError("unexpected startup refresh")

    def disconnect(self, stop, progress):
        self.saved = ("", "", "")
        self.state = {"configured": False, "connected": False}


class ConnectionsUI(unittest.TestCase):
    def setUp(self):
        self.service = Service()
        self.section = SoundCloudSection(self.service, isolated=True)
        self.section.show()
        self.addCleanup(self.section.deleteLater)
        self.addCleanup(self.section._tasks.drain)

    def wait_idle(self):
        deadline = time.monotonic() + 3
        while self.section._tasks.pending and time.monotonic() < deadline:
            _app.processEvents()
            time.sleep(0.001)
        self.assertEqual(self.section._tasks.pending, 0)

    def test_initial_refresh_uses_shared_once_hook_but_manual_refresh_stays_explicit(self):
        calls = []
        self.service.refresh_initial = lambda stop, progress: calls.append('initial')
        self.service.refresh = lambda stop, progress: calls.append('explicit')
        self.section._refresh_initial()
        self.wait_idle()
        self.section.refresh()
        self.wait_idle()
        self.assertEqual(calls, ['initial', 'explicit'])

    def test_window_managed_section_does_not_schedule_its_own_automatic_refresh(self):
        self.service.state = dict(connected=True, configured=True, count=7)
        with patch('desktop.connections.QStandardPaths.isTestModeEnabled', return_value=False), \
                patch.object(SoundCloudSection, '_refresh_initial') as refresh:
            section = SoundCloudSection(self.service, auto_refresh=False)
            self.addCleanup(section.deleteLater)
            self.addCleanup(section._tasks.drain)
            _app.processEvents()
            refresh.assert_not_called()
        self.assertIsNone(section._startup_worker)
        self.assertIsNone(section._worker)
        self.assertIn('7 liked tracks', section.status.text())

    def test_window_refresh_remains_busy_cancelable_until_matching_owner_retires(self):
        from unittest.mock import Mock
        worker = Mock()
        section = SoundCloudSection(self.service, isolated=True, auto_refresh=False)
        section.attach_startup(worker)
        self.addCleanup(section.deleteLater)
        self.addCleanup(section._tasks.drain)
        self.assertFalse(section.refresh_button.isEnabled())
        self.assertFalse(section.cancel_button.isHidden())
        calls = []
        self.service.refresh = lambda stop, progress: calls.append('explicit')
        section.refresh()
        self.assertEqual(section._tasks.pending, 0)
        section._cancel()
        worker.cancel.assert_called_once_with()
        section.finish_startup(object())
        self.assertFalse(section.refresh_button.isEnabled())
        section.finish_startup(worker, ConnectionFailure('Connection offline'))
        self.assertTrue(section.refresh_button.isEnabled())
        self.assertEqual(section.status.text(), 'Connection offline')
        section.refresh()
        deadline = time.monotonic() + 3
        while section._tasks.pending and time.monotonic() < deadline:
            _app.processEvents()
            time.sleep(.001)
        self.assertEqual(section._tasks.pending, 0)
        self.assertEqual(calls, ['explicit'])

    def test_completed_window_attempt_does_not_repeat_and_keeps_safe_failure_for_settings(self):
        self.service.state.update(connected=True, count=1)
        for result, message in ((ConnectionFailure('Safe offline error'), 'Safe offline error'),
                                (ValueError('private unexpected contents'),
                                 'Could not complete the SoundCloud connection. Try again.'),
                                (None, 'Connected as SoundCloud · 1 liked tracks')):
            with self.subTest(result=result), \
                    patch('desktop.connections.QStandardPaths.isTestModeEnabled', return_value=False), \
                    patch('desktop.connections.QTimer.singleShot') as timer:
                section = SoundCloudSection(self.service, startup_complete=True, startup_result=result)
                self.addCleanup(section.deleteLater)
                self.addCleanup(section._tasks.drain)
                timer.assert_not_called()
                self.assertEqual(section.status.text(), message)
                self.assertTrue(section.refresh_button.isEnabled())

    def test_connect_save_then_edit_prefilled_credentials_and_disconnect(self):
        rounds = [("client-1", "secret-1", "https://soundcloud.com/test-user"),
                  ("client-2", "secret-2", "https://soundcloud.com/other-user")]
        for index, values in enumerate(rounds):
            def dialog(*credentials, **kwargs):
                self.assertEqual(credentials, self.service.saved)
                instance = CredentialsDialog(*credentials, **kwargs)
                instance.client_id.setText(values[0])
                instance.secret.setText(values[1])
                instance.profile.setText(values[2])
                QTimer.singleShot(0, instance.accept)
                return instance
            with self.subTest(index=index), patch("desktop.connections.CredentialsDialog", side_effect=dialog), \
                    patch("desktop.connections.QDesktopServices.openUrl", side_effect=AssertionError("public likes must not open a browser")):
                self.section._edit()
                self.wait_idle()
            self.assertEqual(self.service.saved, values)
            self.assertFalse(self.section.edit_button.isHidden())
            self.assertIn("42 liked tracks", self.section.status.text())
            self.assertIn("Public likes for", self.section.status.text())
        self.section.disconnect_button.click()
        self.wait_idle()
        self.assertEqual(self.service.saved, ("", "", ""))
        self.assertFalse(self.section.connect_button.isHidden())

    def test_cancelling_edit_keeps_existing_credentials_and_connection(self):
        self.service.saved = ("existing-id", "existing-secret", "https://soundcloud.com/existing")
        self.service.state = {"configured": True, "connected": True}
        def dialog(*credentials, **kwargs):
            instance = CredentialsDialog(*credentials, **kwargs)
            instance.client_id.setText("discarded")
            QTimer.singleShot(0, instance.reject)
            return instance
        with patch("desktop.connections.CredentialsDialog", side_effect=dialog):
            self.section._edit()
            self.wait_idle()
        self.assertEqual(self.service.saved, ("existing-id", "existing-secret", "https://soundcloud.com/existing"))
        self.assertEqual(self.service.connects, [])

    def test_owner_retains_cancelled_job_and_rejects_late_browser_open(self):
        entered, release = Event(), Event()
        def operation(stop, progress, browser):
            entered.set()
            release.wait(3)
            browser("https://secure.soundcloud.com/authorize")
        with patch("desktop.connections.QDesktopServices.openUrl") as browser:
            self.section._run(operation)
            self.assertTrue(entered.wait(1))
            captured = self.section._worker
            self.section._tasks.stop()
            self.assertTrue(captured.cancelled.is_set())
            self.assertEqual(self.section._tasks.pending, 1)
            release.set()
            self.wait_idle()
            browser.assert_not_called()
            self.assertIsNone(self.section._worker)

    def test_credential_dialog_requires_credentials_and_profile_and_masks_secret(self):
        instance = CredentialsDialog()
        self.addCleanup(instance.deleteLater)
        self.assertEqual(len(instance.findChildren(QLineEdit)), 3)
        self.assertEqual(instance.secret.echoMode(), QLineEdit.Password)
        self.assertFalse(instance.save_button.isEnabled())
        instance.client_id.setText("id")
        self.assertFalse(instance.save_button.isEnabled())
        instance.secret.setText("secret")
        self.assertFalse(instance.save_button.isEnabled())
        instance.profile.setText("https://soundcloud.com/test-user")
        self.assertTrue(instance.save_button.isEnabled())
        instance.reject()
        self.assertEqual(instance.result(), QDialog.Rejected)
