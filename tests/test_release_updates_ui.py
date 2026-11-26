"""Explicit update checks own workers and leave installation to the user."""
import threading
import time
import unittest
from unittest import mock

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

from soundbox import release_updates as updates
from tests import test_inline_drafts as fixtures


class ReleaseUpdatesUI(unittest.TestCase):
    setUpClass = classmethod(fixtures.InlineDraftPublication.setUpClass.__func__)
    settle = fixtures.InlineDraftPublication.settle

    def setUp(self):
        self.latest = self.enterContext(mock.patch.object(updates, 'latest_release', return_value=None))
        self.enterContext(mock.patch.object(updates, 'current_build', return_value=updates.Build('1.0.0', 179, 'personal')))
        fixtures.InlineDraftPublication.setUp(self)

    def wait(self, predicate):
        until = time.monotonic()+5
        while not predicate() and time.monotonic()<until:
            QTest.qWait(1)
        self.assertTrue(predicate())

    def test_settings_construction_does_no_update_request_and_button_explicitly_checks(self):
        w = self.window
        self.latest.assert_not_called()
        QTest.mouseClick(w.settings_btn, Qt.LeftButton)
        self.latest.assert_not_called()
        w._update_check_button.click()
        self.wait(lambda:getattr(w,'_update_check_worker',None) is None)
        self.latest.assert_called_once_with(updates.Build('1.0.0', 179, 'personal'))
        self.assertTrue(w._update_check_button.isEnabled())
        self.assertEqual(w._update_check_status.text(), 'No newer release is available.')

    def test_check_offers_download_and_only_a_separate_click_opens_official_installer(self):
        w = self.window
        url = updates.REPOSITORY+'/releases/download/v1.0.0-build.180/'+updates.ASSET
        self.latest.return_value=updates.Release('1.0.0',180,
            updates.REPOSITORY+'/releases/tag/v1.0.0-build.180',url)
        with mock.patch.object(QMessageBox,'question') as question, \
                mock.patch.object(QDesktopServices,'openUrl',return_value=True) as browser:
            w._check_for_updates()
            self.wait(lambda:getattr(w,'_update_check_worker',None) is None)
            question.assert_not_called()
            browser.assert_not_called()
            self.assertEqual(w._update_check_button.text(),'Download installer')
            self.assertIn('quit Soundbox normally',w._update_check_status.text())
            w._update_check_button.click()
            browser.assert_called_once()
            self.assertEqual(browser.call_args.args[0].toString(),url)
            self.assertEqual(w._update_check_button.text(),'Check for Updates…')
            self.latest.assert_called_once()
        self.assertFalse(w._shutting_down)

    def test_public_profile_refuses_personal_channel_before_worker_admission(self):
        w = self.window
        with mock.patch.object(updates,'current_build',return_value=updates.Build('1.0.0',179,'public')):
            w._check_for_updates()
        self.latest.assert_not_called()
        self.assertIsNone(getattr(w,'_update_check_worker',None))
        self.assertIn('personal builds',w._update_check_status.text())

    def test_repeated_clicks_coalesce_and_real_shutdown_drains_without_result_dialog(self):
        w = self.window
        started,release=threading.Event(),threading.Event()
        self.addCleanup(release.set)
        def check(_build):
            started.set()
            if not release.wait(5):raise RuntimeError('Generated update worker was not released')
            return updates.Release('1.0.0',180,'unused','unused')
        self.latest.side_effect=check
        with mock.patch.object(QMessageBox,'question') as question, \
                mock.patch.object(QDesktopServices,'openUrl') as browser:
            w._check_for_updates()
            self.wait(started.is_set)
            w._check_for_updates()
            self.latest.assert_called_once()
            self.assertFalse(w._update_check_button.isEnabled())
            QTimer.singleShot(0,release.set)
            w.shutdown()
            question.assert_not_called()
            browser.assert_not_called()
        self.assertEqual(w._update_check_tasks.pending,0)

    def test_request_failure_restores_button_and_keeps_error_inline(self):
        w = self.window
        self.latest.side_effect=OSError('generated offline request')
        with mock.patch.object(QMessageBox,'question') as question:
            w._check_for_updates()
            self.wait(lambda:getattr(w,'_update_check_worker',None) is None)
        question.assert_not_called()
        self.assertTrue(w._update_check_button.isEnabled())
        self.assertEqual(w._update_check_status.text(),'Could not check for updates. Try again later.')


if __name__ == '__main__':
    unittest.main()
