# Release test gate

Updated November2, 2026. `./check.sh` builds native deck/CDJ code, runs lint/types, validates the
required offline fixtures, then executes every discovered test exactly once.
Ordinary/UI/GPU modules use three isolated offscreen Qt processes; module fixtures
stay together and the complete inventory is reconciled across workers. All
`test_native_deck` cases then use one fresh, serial Cocoa process. Both phases
are mandatory. `--native` remains a compatible
alias; `--render` additionally saves the hermetic main-window render.

Each process records its complete discovered inventory and selected IDs under
`build/check/`. The final verifier rejects missing phases, duplicate IDs,
inventory changes, unrun tests, skips, expected failures and failed tests. A
missing compiler, encoder, Metal device or Cocoa session cannot produce a clean
release result. The supported release host is arm64 macOS 14+ with the project's
pinned developer requirements and native build tools; fixture AAC/MP3 encoding
also requires FFmpeg and LAME on PATH.

The [November2 runtime audit](testing-runtime-audit.md) records the complete
timing review, isolation checks and a controlled encrypted-subset comparison.
Each case still runs with its existing cipher/recovery settings. Module weights
affect scheduling only; new tests cannot be excluded by missing timing hints.

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
historical. Current RGB retains the monotonic faint-coverage shoulder below0.18
at every zoom, including384; its filled palette evaluates the same accepted
92pt/s song-clock reference at every stop. Geometry, LOD and motion remain
independent of that color policy. Current RGB uses logical-point increments at
576/864; the October31 visually accepted correction uses physical-pixel increments
at64/128/184. Other RGB stops and3BAND remain continuous. BLUE retains physical-pixel
increments through384 and logical points at576–1500. The production offset helper
is compiled independently across all modes, zooms, scales, phases and overlapping
tile origins; restoring fractional128/184 motion must violate the targeted contract.
Native drawables verify the matching hold/one-pixel advance and retained translation.
HANDOFF distinguishes current source/release/install evidence.
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


October28 adds the isolated Rust1.99 CDJ renderer build, pinned-vendor verification,
Rust pagination/confinement checks and Python integration/recovery checks. The
required offline fixture is `soundbox-cdj-ref/offline-integration-20261028/`, or
`SOUNDBOX_CDJ_REFERENCE`; `tests/fixtures/cdj-reference.json` pins all28 files.
It contains copied native metadata/profiles/analysis plus one captured WAV and an
explicit synthetic DTO. No harness opens live devices, source DBs or AppData.
Restore exact fixture bytes; missing or changed inputs fail instead of skipping.
Only the renderer binary and license notices are packaged, not these inputs.

October31 transport ring correction adds two production-QSS/controller pixel
checks under three accents: fixed-orange mouse/keyboard CUE, no idle/held/playing
hover difference, and exact release. Native Cocoa focused replay passes too.
These isolate the widget-local generic-rule precedence failure without sending
input to the user's app or changing transport/keyboard routing.

The combined new-SB-vector/ring correction passed1907 tests (1847 offscreen,
60 Cocoa), signed31-module/native/asset checks, isolated bundle smoke and real
safe installation/preservation. Original generic-hover mutation is rejected by
both new pixel tests. Eight modern PNG ICNS entries survive native readback
exactly; real NSApplication captures verify saved-color SB artwork. HANDOFF
records exact installed fingerprint and the separate user visual approval.

October31 approved shine/green-PLAY refinement extends the existing play pixel
check to five accents, requiring identical active pixels and a green ring, with
no hover difference. Independent vector comparison confines gray correction to
the upper shine streak (293 default/current-white pixels at1024px; all changed
pixels become exact#a8a8a9, all other pixels/alpha exact). Native ICNS readback
matches all eight PNG representations. Final1907-test gate, signed package/smoke
and actual installed preservation pass; final screenshot also shows green PLAY
under the user's off-white theme during his own playback. HANDOFF has provenance.


November1, 2026 (October31 prep/visual continuation): sourcebc3da7c6… passes all
1923 tests:1863 offscreen in1053.294s plus60 Cocoa in61.253s, zero skips or expected
failures. Render inspected.46 focused Cue/Grid/recovery tests also pass offscreen
and Cocoa, and the all-zoom native drawable motion case passes independently.
The initial offscreen pass found an older paused-CUE expectation of gray; that
assertion was corrected to the newer orange-while-held requirement. Two intermediate
runs were interrupted for receipt-admission/shutdown review. Only the final complete
passing pair is the release result. Package/install evidence lives in HANDOFF.

November1 final icon-size follow-up: sourceac92458f… passes the complete1923-test
render gate (1863 offscreen1016.014s +60 Cocoa62.000s), zero skips/expected failures.
Three focused icon tests pass both platforms; eight PNG ICNS representations
read back exactly.37 packaged Python modules and both native payloads equal the
prior bc3 bundle; only SVG/PNG/ICNS change. Signed package/smoke/install/relaunch
and latest93-import/nine-analysis preservation pass. HANDOFF records exact hashes.

## November2 final delivery

The final complete gate for source `bacf1b48…` passed 2144 cases: 2080 offscreen
in 481.233s and 64 Cocoa in 67.834s, zero skips or expected failures. These are dated
results, not a hard-coded inventory. New coverage includes complete first-visible
waveform publication, partial-cache repair without provisional paint, selected-load
valid-cache upgrades deferred to the next load, exhaustive 65536-input signed-transfer
parity, grouped native upload/presentation races, and playlist-only icon persistence and
fixed-row exclusions. Existing native all-zoom palette/motion tests still run.
Package/native smoke and actual installed curation verification subsequently
passed. Exact binary fingerprint, source revision and remaining acceptance limits
belong in [HANDOFF](HANDOFF.md).

November3 broader efficiency coverage adds rapid phased/phase-less scroll
reversals, two-position deferred USB view restoration, coalesced persistence and
maintenance flush, exact-byte state/corruption checks, import/queue identity and
retry parity, playlist duplicate/order/sequential-SQL parity, prep reference
rollback, artwork observation bounds, immutable native input lifetimes and pure
provider-cache fallback. CDJ builds pin RUSTDOC and run library AND binary tests,
doctests and adapter checks; --lib alone would omit required binary regressions.

November3 night adds exact-byte/epoch source-race faults; immutable-record/COW
rollback and full queue detachment tests; committed-edge and historical pin
proofs; failed-ack/restart/retirement/later-source-change cases; cue-reference
ordering under competing indexes; fake-socket provider decoding/EOF/pool/standard
stream/cancellation contracts; manifest counter/profile stale-plan faults; full
global tag revision tests; visible thumbnail bounds and modal-owner lifetime;
sequential bulk-art/default assignment and real publication rollback; randomized
state overlap guard parity and prefix work bounds. No network/source/device
inputs are made live by these tests. Both packaged profiles reject development
integrations and run native ORM/SQLCipher queries over disposable RAM in the
explicit isolated check. Clang static audit receipts are additional evidence,
not a replacement for the mandatory Cocoa/native gate.

Final night delivery for source `fc696312…` passes2531cases:2467offscreen in
618.725s and64Cocoa in67.664s, zero skips/expected failures. The full gate was
rerun after the catalog alternating-row contrast correction. Both signed public/
personal packages and isolated native ORM/SQLCipher probes pass; installed normal
startup and exact current library/authority/icon preservation pass. Dated hashes,
review scope and remaining hardware/network limits are in [HANDOFF](HANDOFF.md).
