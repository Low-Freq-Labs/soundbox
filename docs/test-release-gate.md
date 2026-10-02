# Release test gate

October 2, 2026. `./check.sh` builds native code, runs lint/types, validates the
required offline fixtures, then executes every discovered test exactly once.
Ordinary/UI/GPU tests use an offscreen Qt process; all `test_native_deck` cases
use a fresh Cocoa process. Both are mandatory. `--native` remains a compatible
alias; `--render` additionally saves the hermetic main-window render.

Each process records its complete discovered inventory and selected IDs under
`build/check/`. The final verifier rejects missing phases, duplicate IDs,
inventory changes, unrun tests, skips, expected failures and failed tests. A
missing compiler, encoder, Metal device or Cocoa session cannot produce a clean
release result. The supported release host is arm64 macOS 14+ with the project's
pinned developer requirements and native build tools; fixture AAC/MP3 encoding
also requires FFmpeg and LAME on PATH.

## Required private fixtures

No routine release test discovers live devices, reads the live library or scans
Desktop for references. Restore these existing private snapshots on a new host:

- iPod: `soundbox-ipod-ref/offline-lifecycle/20260926-163945/`, or set
  `SOUNDBOX_IPOD_CAPTURE` to the same preserved offline capture. Tests copy its
  metadata into temporary directories and mutate those copies only. Sparse
  placeholders stand in for omitted historical audio and reject reads; only
  generated new audio is read. The nine capture tests do not establish USB
  durability or historical-audio integrity.
- Waveform: `soundbox-waveform-ref/20261001/`, or set
  `SOUNDBOX_WAVEFORM_REFERENCE` to its restored location. The tracked
  `tests/fixtures/waveform-reference.json` pins every file's size and SHA-256.
  Required files are `source.mp3`, `analysis.sba5`, `RGB.png`, `BLUE.png`, and
  `3BAND.png`. Restore exact files; do not regenerate/rebaseline automatically.
  The local `manifest.json` is a convenience copy; the tracked manifest is the
  authority. Inputs on `/Volumes` or waveform symlink escapes are rejected.

Private audio, artwork and captured databases remain git-ignored. A fresh public
checkout without these private inputs fails preflight explicitly; it is not a
portable zero-fixture claim. The former three skipped checks for unavailable
Bad Decisions / High & Scared screenshots are replaced by current, frozen Nuclear
references in all three modes, including source/cache provenance and palette
checks. The archived frames were extracted during the October 1 review of the
user-supplied September 29/October 1 movies; the original MOVs are no longer at
their supplied Desktop paths. BLUE/3BAND frames retain their recorded sRGB
conversion; the RGB frame retains decoded BT.709 display codes. This is a new
reference corpus, not recovery of the absent old screenshots or pixel parity.

## Coverage correction

The old fragment replaced joint interval coverage with the minimum of two
separately integrated edges. At a signed crossing, those edges can cover different
horizontal parts of one pixel; taking the smaller average overfills it.
The fragment now composites the joint coverage and visible color returned by
`resolve_interval_surface`, with no later alpha replacement. Source extrema,
color mapping, clock, LOD, reconstruction lattice and one-pixel footprint stay
unchanged. No temporal or spatial detail smoothing was added.

The former expected failure is now an ordinary passing numerical assertion.
Tests execute the actual final fragment branch as well as its helper against
independent pointwise quadrature. A mutation reinstates separate-edge averaging
and must violate that oracle. Random profiles, sign crossings and tiny coverage
pieces complement native translation/recenter tests across all modes and zooms.

A 507-frame before/after comparison preserves all 169 3BAND frames exactly and
all recenter returns. Mean RGB/BLUE changes over the measured crop are below
0.55 display-code values per channel at every zoom, with localized edge differences
up to 78 codes. Evidence is in ignored `build/clean-release/`. These measurements
are not a guarantee about every track, refresh mode or physical display.
BLUE's radial-centroid color approximation and the accepted five-probe sampling
branches remain design approximations; zero skipped tests does not establish
perfect rekordbox equivalence. Final installed state and validation: [HANDOFF.md](HANDOFF.md).
