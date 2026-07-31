import subprocess
import unittest
from unittest.mock import patch

from soundbox import macos_defaults


class MacOSDefaultsTests(unittest.TestCase):
    def test_packaged_macos_sets_only_soundbox_bundle_default(self):
        with (patch.object(macos_defaults.sys, "platform", "darwin"),
              patch.object(macos_defaults.sys, "frozen", True, create=True),
              patch.object(macos_defaults.subprocess, "run") as run):
            macos_defaults.disable_packaged_press_and_hold()

        run.assert_called_once_with(
            [
                "/usr/bin/defaults", "write", "com.lowfreqlabs.soundbox",
                "ApplePressAndHoldEnabled", "-bool", "false",
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2.0,
        )

    def test_source_launch_does_not_mutate_user_defaults(self):
        with (patch.object(macos_defaults.sys, "platform", "darwin"),
              patch.object(macos_defaults.sys, "frozen", False, create=True),
              patch.object(macos_defaults.subprocess, "run") as run):
            macos_defaults.disable_packaged_press_and_hold()

        run.assert_not_called()

    def test_defaults_failure_does_not_block_launch(self):
        with (patch.object(macos_defaults.sys, "platform", "darwin"),
              patch.object(macos_defaults.sys, "frozen", True, create=True),
              patch.object(
                  macos_defaults.subprocess, "run",
                  side_effect=subprocess.TimeoutExpired("defaults", 2.0))):
            macos_defaults.disable_packaged_press_and_hold()
