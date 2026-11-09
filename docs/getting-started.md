# Getting started

SoundBox is a macOS desktop app for browsing/preparing a rekordbox library and exporting selected playlists to USB or supported iPods.

## Prerequisites

Apple Silicon, macOS 14+, Python 3.14, CMake 3.24+, Apple's command-line developer tools, and the pinned dependencies.
CDJ rendering/analysis uses the existing isolated Rust toolchain; the [release guide](test-release-gate.md) describes provisioning and private offline fixtures.

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

1. Browse the read-only rekordbox snapshot or import audio through the app.
2. Curate local title/artist/artwork; accepted native edits save through separately guarded domains.
3. Double-click to load a song. Space toggles playback; C is momentary CUE. See [keyboard mappings](KEYBOARD.md).
4. Connect a supported destination and export selected playlists. Generic USBs use explicit Export; iPods retain firmware-specific Sync.
5. Use Eject before disconnecting. It starts no final verification scan.

Supported stock writes remain MA446 MP3/supported WAV only. Dual stock/Rockbox uses one coordinated transaction. See [device limits](DEVICES.md).
Ordinary playback/export preserves source audio; accepted recoverable conversion and confirmed Graveyard removal are separate domains.

## Verify and package

```sh
./check.sh --render
./build_app.sh
./build_app.sh public
```

Run the gate directly: all discovered offscreen/Cocoa cases must pass without skips.
Personal output is `dist/personal/Soundbox.app`; public output is separate. Build does not install or authorize controlling the real app.
Follow the [state-preserving install contract](runtime-contracts.md#buildinstall-handoff) with current authorization.

Curated state lives under `~/Library/Application Support/LowFreqLabs/Soundbox/`; never wipe it to fix a cache or player problem.
Logs are under `~/Library/Logs/Soundbox/`. For missing media use the app's bounded Fix Missing Links workflow; don't guess replacements.
See [architecture](ARCHITECTURE.md), [provider boundaries](release-boundaries.md), and [current handoff](HANDOFF.md).
