# Native runtime

Apple Silicon/macOS 14+ audio, rendering and process guards.

- `SoundboxDeck.h`: public C ABI; `SoundboxDeck.mm`: lifecycle, decode, audio and rendering.
- `WaveformShader.h`: exact Metal source, compiled during the existing deck initialization.
- `ProcessGuard.c`: native process observations consumed directly by the Python source guards.
- [cdj](cdj/README.md): separately built Rust catalog renderer/analysis helper.
- `CMakeLists.txt` defines the deck/process libraries; root `build_native.sh` builds them.

Preserve callback/thread ownership, timing, atomics, eager pipeline readiness and waveform output.
Never introduce allocation or blocking work into the realtime audio callback.

Run `./build_native.sh`, then the full `./check.sh` (Cocoa/native phase is mandatory).
Read [player contracts](../docs/PLAYER.md) and [native gotchas](../docs/GOTCHAS.md) before changes.
