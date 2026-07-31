"""Entry point for the packaged Soundbox.app (PyInstaller bundles this)."""

# Cocoa consults this preference before Qt gets a chance to filter the first
# held C. Configure it before importing desktop.app (and therefore PySide6).
from soundbox.macos_defaults import disable_packaged_press_and_hold

disable_packaged_press_and_hold()

from desktop.app import main

if __name__ == "__main__":
    main()
