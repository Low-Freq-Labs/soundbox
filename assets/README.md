# Application assets

Tracked app icon and SVG wordmark; bundled through `Soundbox.spec`.

- [Soundbox.icns](Soundbox.icns): original authored reference; builds render the default white icon with [build_assets.py](../scripts/build_assets.py).
- [Icon template](soundbox-original-sb-icon.svg): recolored by [app_icon.py](../desktop/app_icon.py).
- [License texts](licenses/README.md): pinned dependency texts and build-time notices.
- [Wordmark](soundbox-wordmark.svg): rendered by [wordmark.py](../desktop/wordmark.py).

Keep the wordmark's existing header footprint and theme only its BOX layer.
Saved Finder/Dock color customization is user state outside signed Contents; preserve it.
No personal photos, credentials or reference captures belong here.

Use affected visual/Cocoa checks locally; run the complete gate and check both package profiles before publication.
See [theme/player behavior](../docs/PLAYER.md) and [architecture](../docs/ARCHITECTURE.md).
