# Native runtime

Apple Silicon/macOS 14+ audio, rendering and process guards.

- `SoundboxDeck.h`: public C ABI; `SoundboxDeck.mm`: lifecycle, decode, audio and rendering.
- `WaveformShader.h`: exact Metal source, compiled during the existing deck initialization.
- Main BLUE uses native LUT/encoded gradients over a resident raw PWV5 spectral proxy; exact native pixel parity is unclaimed.
- BLUE shares protected RGB geometry;3BAND keeps its own PWV7 contours/detail with main-only gain1.20→1.35 and continuous motion.
- Auxiliary BLUE inner colors are lattice attributes, not a translated RGBA texture; RGB output stays protected.
- ABI9 groups optional PWV4/PWV6 preview packets (1200×6/×3 bytes), preloaded on the selected worker; no cache/DSP/source mutation.
- Main3BAND uses exact joint encoded seven-color coverage; PWV6 mini is a static1200×25 premultiplied encoded opaque-stack image with a mini-only baseline floor.
- Native mini formulas do not establish whole-frame or final resampling parity; keep main gain1.35, BLUE and RGB protected.
- Mini cues use the resident waveform timeline before decoder duration; timeline publication wakes paused rendering.
- `ProcessGuard.c`: native process observations consumed directly by the Python source guards.
- `IpodIdentity.c`: bounded, non-seizing USB IN identity reads; exact mount/BSD/USB guards and canonical plist stdout, no device files.
- [cdj](cdj/README.md): separately built Rust catalog renderer/analysis helper.
- `CMakeLists.txt` defines the libraries/helper; root `build_native.sh` builds them.

Preserve callback/thread ownership, timing, atomics, eager pipeline readiness and waveform output.
Never introduce allocation or blocking work into the realtime audio callback.

Rebuild changed native code, then affected player/waveform checks; full `./check.sh --render` before publication.
Read [player contracts](../docs/PLAYER.md) and [native gotchas](../docs/GOTCHAS.md) before changes.
