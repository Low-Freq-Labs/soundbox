"""Installed version observations cannot enable a new native SQL profile."""
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest import mock

from soundbox import rekordbox_installations as installations, rekordbox_tags


class RekordboxInstallations(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()

    def bundle(self, relative, version):
        path = self.root / relative / 'Contents';path.mkdir(parents=True)
        (path / 'Info.plist').write_bytes(plistlib.dumps({'CFBundleShortVersionString': version}))
        return path.parent

    def test_direct_and_version_container_locations_and_unknown_app_are_bounded(self):
        six = self.bundle('rekordbox 6/rekordbox.app', rekordbox_tags.NATIVE_VERSION)
        self.bundle('unrelated/nested/rekordbox.app', '7.0')
        found = installations.installed_rekordbox(application_roots=[self.root])
        self.assertEqual(found, (installations.RekordboxInstallation(six, rekordbox_tags.NATIVE_VERSION),))

    def test_single_7_observation_stays_unsupported_and_multiple_versions_fail_closed(self):
        seven = self.bundle('rekordbox.app', '7.2.0')
        observed = installations.installed_rekordbox(application_roots=[self.root])
        with mock.patch.object(installations, 'installed_rekordbox', return_value=observed):
            self.assertEqual(rekordbox_tags._installed_version(), '7.2.0')
            self.assertNotEqual(rekordbox_tags._installed_version(), rekordbox_tags.NATIVE_VERSION)
        six = self.bundle('rekordbox 6/rekordbox.app', rekordbox_tags.NATIVE_VERSION)
        with mock.patch.object(installations, 'installed_rekordbox', return_value=(
                installations.RekordboxInstallation(six, rekordbox_tags.NATIVE_VERSION),
                installations.RekordboxInstallation(seven, '7.2.0'))):
            self.assertEqual(rekordbox_tags._installed_version(), '')

    def test_corrupt_missing_or_invalid_version_does_not_invent_an_install(self):
        bundle = self.bundle('rekordbox.app', '')
        self.assertEqual(installations.installed_rekordbox(application_roots=[self.root]), ())
        (bundle / 'Contents' / 'Info.plist').write_bytes(b'not a plist')
        self.assertEqual(installations.installed_rekordbox(application_roots=[self.root]), ())
        self.assertEqual(installations.installed_rekordbox(application_roots=[self.root / 'absent']), ())
