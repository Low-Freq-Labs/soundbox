#!/usr/bin/env python3
"""Render the default bundle icon from the same vector/color as first launch."""
from pathlib import Path
import argparse
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def build_icon(output):
    from PySide6.QtGui import QGuiApplication
    from desktop.app_icon import accented_image
    from desktop.theme import DEFAULT_ACCENT
    app = QGuiApplication.instance() or QGuiApplication([])
    # Retain the application through all SVG renders and image encodes.
    assert app is not None
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='soundbox-icon-') as temporary:
        iconset = Path(temporary) / 'Soundbox.iconset'
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                name = f'icon_{size}x{size}' + ('@2x' if scale == 2 else '') + '.png'
                if not accented_image(DEFAULT_ACCENT, size * scale).save(str(iconset / name)):
                    raise OSError('Could not encode the default bundle icon')
        subprocess.run(['/usr/bin/iconutil', '-c', 'icns', str(iconset), '-o', str(output)], check=True)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    print(build_icon(parser.parse_args().output))


if __name__ == '__main__':
    main()
