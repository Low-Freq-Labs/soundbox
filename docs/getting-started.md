# Getting started

SoundBox is a macOS desktop app for browsing/preparing a rekordbox library and exporting selected playlists to USB or supported iPods.

## Installer

The first-tester installer is an Apple Silicon macOS package; installation and
updates replace the app without copying anyone else's library or settings. See
[installer/signing scope](macos-installer.md). A new installation uses three short
[setup pages](first-launch.md): choose the library, choose cleanup or original
metadata, and independently opt into automatic artwork. Native writing on another
library/version still requires its own supported acceptance.

## Prerequisites for source builds

Apple Silicon, macOS 14+, Python 3.14, CMake 3.24+, Apple's command-line developer tools, and the pinned dependencies.
CDJ rendering/analysis uses the pinned isolated Rust toolchain. `SOUNDBOX_RUST_KIT` can select an equivalent kit outside the developer checkout; its `install/bin` holds cargo/rustc/rustdoc. The [release guide](test-release-gate.md) describes provisioning and separate private offline test fixtures. An installed app needs neither Python nor a Rust kit.

## Run from source

From the repository root:

```sh
python3.14 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
./build_native.sh
./build_cdj.sh
./.venv/bin/python soundbox_app.py
```

The `.venv` location is required by the build/check scripts. Native-deck failure disables playback with a diagnostic while library management remains available.
Source launch uses real AppData; development tests/renders must use the supplied isolated harnesses.

## Everyday workflow

1. Browse a stable rekordbox snapshot or import audio through the app.
2. Edit titles/artists, prep and organization through the accepted shared-native domains; artwork remains local where no native profile is accepted.
3. Double-click to load a song. Space toggles playback; C is momentary CUE. See [keyboard mappings](KEYBOARD.md).
4. Connect a supported destination and export selected playlists. Generic USBs use explicit Export; iPods retain firmware-specific Sync.
5. Use Eject before disconnecting. It starts no final verification scan.

A stock iPod also exposes an independent **USB** target for selected CDJ playlist
Export/Delete. It shares the physical device's busy and Eject guards; it has no
automatic Sync. An unconfigured writable USB offers **Configure** or **Not now**;
configuration creates the missing CDJ setup after consent without formatting it.

Regular playlists keep authored song order and allow exact-occurrence dragging;
Collection and smart playlists default newest first. Header sorts change only the view.

Supported stock writes use observed Video/Classic iTunesDB/signature profiles on writable FAT32 mounts, with validated MP3 or stereo PCM16/44.1-kHz WAV. Dual iTunes/Rockbox uses one coordinated transaction. See [device limits](DEVICES.md).
Ordinary playback/export preserves source audio; accepted recoverable conversion and confirmed Graveyard removal are separate domains.

## Verify and package

```sh
./check.sh --list-suites                        # areas and test levels
./check.sh --suite tags --level quick           # selected area
./check.sh --focus tests/test_sidebar_layout.py  # exact local regression
./check.sh --render                            # full gate before commit/push
./build_app.sh
./build_app.sh public
```

Run the gate directly: all discovered offscreen/Cocoa cases must pass without skips.
Personal output is `dist/personal/Soundbox.app`; public output is separate. Build does not install or authorize controlling the real app.
Follow the [state-preserving install contract](runtime-contracts.md#buildinstall-handoff) with current authorization.

Curated state lives under `~/Library/Application Support/LowFreqLabs/Soundbox/`; never wipe it to fix a cache or player problem.
Logs are under `~/Library/Logs/Soundbox/`. For missing media use the app's bounded Fix Missing Links workflow; don't guess replacements.
See [architecture](ARCHITECTURE.md), [provider boundaries](release-boundaries.md), and [current handoff](HANDOFF.md).
