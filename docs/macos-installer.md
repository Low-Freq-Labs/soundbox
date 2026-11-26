# macOS beta installer

The personal beta is distributed as `Soundbox-macOS-arm64.pkg` for Apple Silicon
Macs running macOS 14 or later. The same package handles a first install and an
upgrade. Use the official GitHub release download; the application’s update check
opens that installer instead of replacing a running app.

In Settings → General → Updates, **Check for Updates…** requests the official
latest stable release. A newer personal build changes the button to **Download
installer**; clicking it opens the exact official package in your browser.
There is no startup update request, background installer download, automatic
installation or automatic quit. Public builds do not use the personal channel.
The check uses macOS's system trust store and accepts only stable tags of the
form `v1.0.0-build.N` with the unique uploaded `Soundbox-macOS-arm64.pkg` asset
under `Low-Freq-Labs/soundbox`. `N` is the app's stamped numeric build, not a
new timestamp or a restamp of an older binary.

Quit Soundbox normally and allow its background work to finish before opening the
installer. Install on the running Mac’s startup volume, then open Soundbox from
Applications after Installer finishes. First launch asks which rekordbox library
to use, whether to clean titles/artists, and whether to fetch artwork. Recipient
libraries need their own supported native write admission; the package contains
no Sonny-specific library permissions or saved account credentials.

This beta has a locally signed application, an unsigned installer and no Apple
notarization. It is not a Developer ID release. macOS may block installation or
first opening. If you trust the official download, review the named item in
System Settings → Privacy & Security and follow Apple’s manual approval flow.
The installer never disables Gatekeeper or removes quarantine attributes. See
[Apple’s guidance](https://support.apple.com/en-us/102445).

The payload contains only `/Applications/Soundbox.app`. Installer replaces that
bundle, including obsolete app code, while preserving AppData, musical curation,
artwork, credentials, media, conversions and unfinished recovery outside the
bundle. It never starts or terminates Soundbox. A native preinstall check refuses
an upgrade while an actual Soundbox bundle or native helper is running; an
unverifiable process snapshot also refuses. App launch during installation is
unsupported: keep Soundbox closed until Installer finishes.

## Build and verify

After the complete release gate and a personal app build:

```sh
PYTHONPATH=. .venv/bin/python scripts/package_macos.py \
  --app dist/personal/Soundbox.app \
  --output dist/installer/Soundbox-macOS-arm64.pkg
```

Use the actual app path printed by `build_app.sh`; the example does not build it.
The tool checks the strict application signature, package profile and every
first-party runtime module against the current source. It stages one app,
compiles the recipient’s native preflight checker, builds with Apple’s
`pkgbuild`/`productbuild`, expands the finished package and compares the entire
app payload. Existing output paths are refused. The adjacent JSON receipt records
package/executable hashes, source identity, build and explicit beta signing scope.
Neither building nor expanding a package installs it or grants source writes.

Publish the reviewed package under its stable asset name on the official GitHub
release. `/releases/latest/download/Soundbox-macOS-arm64.pkg` remains the download
path across versions. Publish only after the full gate, artifact and isolated
startup checks; keep source validation separate from any actual Mac handoff.
The explicit check is implemented by `soundbox/release_updates.py` and the
Settings page; release metadata follows GitHub's
[latest release API](https://docs.github.com/en/rest/releases/releases#get-the-latest-release).

Checks: `./check.sh --focus tests/test_macos_installer.py` uses generated app and
recipient-state fixtures, a real package build/expand, and an owned native helper
process. It never invokes Installer or writes `/Applications` or live AppData.

The native package format and replacement behavior follow Apple’s installed
`pkgbuild(1)`/`productbuild(1)` manuals and
[distribution reference](https://developer.apple.com/library/archive/documentation/DeveloperTools/Reference/DistributionDefinitionRef/Chapters/Distribution_XML_Ref.html).
