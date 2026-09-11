"""Build provenance stamping and verification."""

from __future__ import annotations

import plistlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import app_provenance


class AppProvenance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="sb-provenance-")
        self.root = Path(self.temp.name)
        self.app = self.root / "Soundbox.app"
        self.info = self.app / "Contents" / "Info.plist"
        self.info.parent.mkdir(parents=True)
        self.info.write_bytes(plistlib.dumps({"CFBundleName": "Soundbox"}))
        self.metadata = {
            "version": "1.2.3",
            "revision": "abc123",
            "build": "456",
            "dirty": False,
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_stamp_records_version_revision_build_and_source_state(self):
        with mock.patch.object(
                app_provenance, "source_metadata",
                return_value=self.metadata):
            app_provenance.stamp(self.app, self.root)
        with self.info.open("rb") as handle:
            info = plistlib.load(handle)
        self.assertEqual(info["CFBundleShortVersionString"], "1.2.3")
        self.assertEqual(info["CFBundleVersion"], "456")
        self.assertEqual(info["SoundboxSourceRevision"], "abc123")
        self.assertFalse(info["SoundboxSourceDirty"])

    def test_verify_rejects_dirty_or_wrong_revision(self):
        with mock.patch.object(
                app_provenance, "source_metadata",
                return_value=self.metadata), mock.patch.object(
                    app_provenance.subprocess, "run",
                    return_value=subprocess.CompletedProcess([], 0)):
            app_provenance.stamp(self.app, self.root)
            self.assertTrue(app_provenance.verify(self.app, self.root)[0])
            with self.info.open("rb") as handle:
                info = plistlib.load(handle)
            for key, value in (
                ("SoundboxSourceDirty", True),
                ("SoundboxSourceRevision", "other"),
                ("CFBundleShortVersionString", "0.0.0"),
                ("CFBundleVersion", "999"),
            ):
                with self.subTest(key=key):
                    self.info.write_bytes(plistlib.dumps({**info, key: value}))
                    self.assertFalse(app_provenance.verify(self.app, self.root)[0])

    def test_dirty_checkout_and_invalid_signature_cannot_verify(self):
        with mock.patch.object(
                app_provenance, "source_metadata", return_value=self.metadata):
            app_provenance.stamp(self.app, self.root)
        with mock.patch.object(
                app_provenance.subprocess, "run",
                return_value=subprocess.CompletedProcess([], 0)), mock.patch.object(
                    app_provenance, "source_metadata",
                    return_value={**self.metadata, "dirty": True}):
            ok, message = app_provenance.verify(self.app, self.root)
            self.assertFalse(ok)
            self.assertIn("checkout is dirty", message)
        with mock.patch.object(
                app_provenance.subprocess, "run",
                return_value=subprocess.CompletedProcess([], 1)):
            ok, message = app_provenance.verify(self.app, self.root)
            self.assertFalse(ok)
            self.assertIn("signature", message)


if __name__ == "__main__":
    unittest.main()
