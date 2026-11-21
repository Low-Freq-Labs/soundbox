# Native runtime

Apple Silicon/macOS 14+ audio, rendering and process guards.

- `SoundboxDeck.h`: public C ABI; `SoundboxDeck.mm`: lifecycle, decode, audio and rendering.
- `WaveformShader.h`: exact Metal source, compiled during the existing deck initialization.
- `ProcessGuard.c`: native process observations consumed directly by the Python source guards.
- `IpodIdentity.c`: bounded, non-seizing USB IN identity reads; exact mount/BSD/USB guards and canonical plist stdout, no device files.
- [cdj](cdj/README.md): separately built Rust catalog renderer/analysis helper.
- `CMakeLists.txt` defines the libraries/helper; root `build_native.sh` builds them.

Preserve callback/thread ownership, timing, atomics, eager pipeline readiness and waveform output.
Never introduce allocation or blocking work into the realtime audio callback.

Rebuild changed native code, then affected player/waveform checks; full `./check.sh --render` before publication.
Read [player contracts](../docs/PLAYER.md) and [native gotchas](../docs/GOTCHAS.md) before changes.
