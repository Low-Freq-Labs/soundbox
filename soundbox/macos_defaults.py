"""Soundbox-specific macOS defaults that must exist before Qt/AppKit starts."""

from __future__ import annotations

import subprocess
import sys


_BUNDLE_ID = "com.lowfreqlabs.soundbox"


def disable_packaged_press_and_hold() -> None:
    """Disable Cocoa's accent picker for held deck-control letters.

    Qt can enter the native text-input path during the first track-load focus
    transition before its widget event filters settle.  The preference is
    deliberately bundle-local, so accent entry remains unchanged everywhere
    outside Soundbox.  Run this before importing Qt in the packaged entrypoint.
    """
    if sys.platform != "darwin" or not getattr(sys, "frozen", False):
        return
    try:
        subprocess.run(
            [
                "/usr/bin/defaults", "write", _BUNDLE_ID,
                "ApplePressAndHoldEnabled", "-bool", "false",
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        # Keyboard handling still has the Qt-side guard; a defaults-service
        # failure must never prevent the library manager from launching.
        pass
