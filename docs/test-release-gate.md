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
The October 2 correction made the fragment composite the joint coverage and
visible color returned by `resolve_interval_surface`. BLUE retains that exact
area contract. The October6 conservative occupied-column RGB presentation is
historical: current RGB384 uses exact area, other stops retain only the monotonic
faint-coverage shoulder below0.18. RGB now translates continuously at every zoom;
BLUE retains physical-pixel increments through384 and logical points at576–1500.
The October28 RGB edge update integrates encoded channels and decodes the final
encoded-space background blend for Metal; BLUE remains linear-light. Both existing
height/coverage visibility lifts, source extrema/colors, LOD, clocks and one-pixel
footprint stay intact. No temporal blur or source smoothing was added.

The former expected failure is now an ordinary passing numerical assertion.
Tests execute the actual final fragment branch as well as its helper against
independent pointwise quadrature and the explicit RGB column policy. A mutation
reinstates separate-edge averaging in BLUE and must violate the exact-area oracle.
Another removes the retained faint-coverage lift and must fail its bounded-
shoulder contract. Encoded RGB edge tests exercise every zoom and a nonblack
background; restoring endpoint decoding must violate their independent oracle.
Thin-stroke and empty-pixel checks prevent overbroad implementations. Random profiles, sign crossings and tiny coverage
pieces complement native translation/recenter tests across all modes and zooms.

The historical October2 507-frame before/after comparison preserved all169
3BAND frames exactly and
all recenter returns. Mean RGB/BLUE changes over the measured crop are below
0.55 display-code values per channel at every zoom, with localized edge differences
up to 78 codes. Evidence is in ignored `build/clean-release/`. These measurements
are not a guarantee about every track, refresh mode or physical display.
The October 2 close-zoom follow-up replaces BLUE's radial-centroid color
approximation with branch-split quadrature, checked against an independent dense
color oracle and subdivision invariance. The accepted five-probe sampling
branches remain approximations; zero skipped tests does not establish
perfect rekordbox equivalence. Final installed state and validation: [HANDOFF.md](HANDOFF.md).

October 8 playback regressions add native AAC/ALAC immediate Play and seek,
retrying a failed source after it becomes readable, and nonzero-start visual
monotonicity before the first audible timestamp. A pure C++ admission test injects
CloudStorage ENEEDAUTH, permanent read errors and cancellation without contacting
live cloud providers. UI tests require failed loads to cancel pending Play/cue/
gain intentions and expose the failure in status plus its detailed tooltip.
These tests do not certify cloud-service availability; the local Riddim reproduction and cloud-delay diagnosis were separate
read-only investigations, not fixtures for this gate.

The October 8 color-only regression exhausts all 512 PWV5 RGB codes at every
zoom with zero, tiny and ordinary color weights. Native drawables require the
same signed shape and color for zero/full compact height across primaries,
secondary colors, white and the reported quiet green. The previous renderer
fails this regression. This protects against fabricated black in valid quiet
colors without changing source-detail or coverage expectations.

### October 9 background compact-detail scheduling

Hermetic worker regressions seed a valid legacy V5 with complete imported
streams, then require upgrade without any player selection. Later scans and a
first interactive load must not decode it again. Additional cases cover cloud
deferral and eviction after waiting, cancellation/decode failure preserving the
old bytes, DB cues/manual grids surviving replacement, migration-reserve routing,
and an interactive upgrade completing while queued. Existing checks retain
source immutability, hidden-track handling and bounded preparation receipts.
The packed discovery-reader fixture verifies exact grid/stream bytes while
forbidding generic ANLZ parsing, so the eligibility scan cannot accidentally
expand the entire library into per-sample Python colors.


October 9 rapid-load tests forbid unused Qt envelope expansion during native
four-minute detail delivery, compare vectorized carrier repair with an independent
scalar reference, reject late generation uploads, and exercise cancelled reads.
Generated AAC/ALAC supplements preserve imported bytes and native sample rate;
ALAC extrema match the generated WAV reference exactly. Muted native browser-load
tests cover playing/paused replacement, rapid latest-request selection, pending
grid/media, transient PAUSED reports, explicit pause fades and CUE latch policy.
