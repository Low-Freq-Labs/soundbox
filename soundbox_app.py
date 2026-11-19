"""Entry point for the packaged Soundbox.app (PyInstaller bundles this)."""

import sys

# Windowed bootloaders do not promise usable standard streams. Internal engine
# helpers use inherited dedicated descriptors and never initialize the desktop.
if sys.argv[1:2] == ['--soundbox-worker']:
    from soundbox.worker_entry import main as worker_main
    raise SystemExit(worker_main(sys.argv[2:]))

# Cocoa consults this preference before Qt gets a chance to filter the first
# held C. Configure it before importing desktop.app (and therefore PySide6).
from soundbox.macos_defaults import disable_packaged_press_and_hold

disable_packaged_press_and_hold()

from desktop.app import main

if __name__ == "__main__":
    main()
