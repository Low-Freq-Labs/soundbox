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
