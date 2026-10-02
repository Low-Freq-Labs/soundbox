"""Hermetic paired artwork controls; never opens a library, device or network."""
from __future__ import annotations

import io
import os
from pathlib import Path
import sys
import tempfile
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image
from PySide6.QtCore import QStandardPaths

QStandardPaths.setTestModeEnabled(True)

from desktop.app import make_app
from desktop.artpicker import ArtPickerDialog
from desktop.artistcatalog import ArtistCatalogDialog


class FixtureProvider:
    def search_candidates(self, artist, title):
        return [{"title": title, "uploader": artist, "url": "https://example.invalid/track",
                 "thumb": "https://example.invalid/image", "score": 1.0}]

    def preview_image(self, candidate):
        stream = io.BytesIO()
        Image.new("RGB", (150, 150), "#703bc4").save(stream, "PNG")
        return stream.getvalue()


def main():
    output = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/soundbox-provider-renders")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sb-provider-render-") as area, mock.patch.object(
            QStandardPaths, "writableLocation", return_value=area):
        app = make_app()
        for profile in ("public", "personal"):
            picker = ArtPickerDialog("Fixture Artist", "Fixture Track",
                                     provider=FixtureProvider() if profile == "personal" else None)
            if picker._worker is not None:
                picker._worker.wait()
            picker.show()
            for _ in range(3):
                app.processEvents()
            picker.grab().save(str(output / f"{profile}-picker.png"))
            picker.shutdown()
            picker.close()
            catalog = ArtistCatalogDialog([("Fixture Artist", ["1", "2"], None)],
                                          allow_url=profile == "personal")
            catalog.show()
            app.processEvents()
            catalog.grab().save(str(output / f"{profile}-catalog.png"))
            catalog.close()
    print(output)


if __name__ == "__main__":
    main()
