# Release test gate

Updated November8, 2026. `./check.sh` checks authored-folder guides, local links
and static dependency directions, builds native deck/process-guard/CDJ code, runs lint/types, validates the
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

## Offline shared-library source gate — November13

The final bare `./check.sh --render` passes3325 discovered cases:3259 offscreen
in537.626s and66 Cocoa in69.070s, with exact inventories and zero skips or
expected failures. All772 frozen source/build/test inputs remain exact. Lint,
layout/links/parity,123 engine files under mypy, native/CDJ builds,12 Rust tests,
243 pinned vendor files and required offline fixtures pass. The main12-song,
zero-device render and separate two-column My Tags render were inspected.

Independent review corrected stale-identity title protection and complete-pair
acknowledgement/retry before this result. An intervening full offscreen attempt
ran all3259 selected cases with three real-prep fixture failures: its inherited
fixed-count QTest polling starved/budgeted the two actual SQLCipher worker phases.
Measured completion and exact readback succeeded. Only that test-local helper
changed: join both workers, deliver genuine callbacks, verify preparation on disk,
and retain all data/permission assertions. Production behavior and native timing
thresholds were unchanged. The corrected helper has independent review.

Compact receipts: `~/Library/Logs/Soundbox/tag-smart-deletion-2026-11-12/` under
`offline-followup-gate/` and `prep-domain-completion-review.json`. This validates
current source, not native rekordbox business-profile acceptance or installation.
The [feature audit](shared-library-audit-2026-11.md) retains older-record, metadata,
artwork, analysis and ongoing-parity gaps; [HANDOFF](HANDOFF.md) records build171
separately. No live-source writes, authority activation or runtime handoff occurred.

## Historical tag chronology correction — November9

The fresh bare `./check.sh --render` passes2,955cases:2,889offscreen in490.884s
and66Cocoa in69.590s, zero skips/expected failures and exact inventories. All736
frozen source/test/build inputs remain exact; lint/types/native builds/fixtures
and inspected12-song/zero-device render pass. This adds13cases to the original
performance delivery: existing-history ordering without local ranks, matching
positive/signed tag rules, date validation/fallback, source/cache/native identity
preservation and one-query raw-offset date capture across DST fall-back. Peer
review corrected timezone loss and fallback tag-UUID reuse. Native rules/source
writers/audio and immutable reference fixtures are unchanged.
Evidence: `~/Library/Logs/Soundbox/smart-tag-order-2026-11-09/`.
Package/install identity is separate; see [HANDOFF](HANDOFF.md).

## Performance and tagging source gate — November9

The final bare `./check.sh --render` passes2,942discovered cases:2,876offscreen
in489.680s and66Cocoa in69.700s, zero skips/expected failures, with exact worker
inventories. Lint/types117engine modules, layout, native/CDJ builds and immutable
fixtures pass. All736source/test/build inputs remain exact; the12-song/zero-device
render was inspected. [HANDOFF](HANDOFF.md) separates this source result from
the package and installed app.

Coverage adds per-song migration/portable backup, stable-ID queue reuse,
corruption and process-loss recovery, exact size budgets and stale record-store
owners; rotating background source turns with prepared/pause/bootstrap and
same-song guards; immutable selected export capture with complete overlap paths;
and My Tags dismissal/shortcut scope plus exact-identity smart-list recency.
Five Cocoa-window popup cases also pass independently. A private native-pointer
diagnostic verifies actual opening-button release, tagging and one-double-click
loading; reinstating the old popup grab fails the first-click regression.

The first full offscreen attempt is retained with three Graveyard failures.
Corrections preserve existing explicit Delete semantics, guard analysis-owner
bootstrap and adapt the persisted-before-apply oracle to the complete logical
state reader. Every original assertion remains; no case/fixture/threshold was
omitted or weakened. A fresh full gate follows those fixes. Evidence is under
`~/Library/Logs/Soundbox/performance-improvements-2026-11-09/`.

## Cleanup verification — November8

The final bare `./check.sh --render` passes all 2,886 discovered cases: 2,820
offscreen in 501.435s and 66 Cocoa in 69.011s, with zero skips/expected failures
and exact inventories. Lint/types/layout, native/CDJ builds and immutable fixtures
pass; all 729 publication inputs remain exact after EOF-only normalization.
Compiled code and positions are unchanged; current source matches all 170
installed first-party modules. Both original signed-profile proofs and installed
isolated checks remain retained. [HANDOFF](HANDOFF.md) separates publication's
raw-source fingerprint from the unchanged signed runtime and latest app state.

Shared test helpers live in `tests/support/`; moving a builder does not move its
test cases or alter their release routing. Source/math tests read the production
Metal literal through one shared loader. That literal now lives in
`native/WaveformShader.h`, with exact extracted bytes and unchanged eager pipeline
compilation, native C ABI and rendering math.

Focused verification passes91 shader/source/GPU/color/profile/motion cases,
44 encrypted registration proof/pilot/store cases and4 waveform-preview cases,
with zero skips. Native compilation and243 pinned upstream integrity checks pass.
The preview checks compare all65536 encoded heights with the actual compiled
native transfer after correcting a stale diagnostic-only exponent. The new
double-versus-float32 comparison permits one normalized float32 epsilon; existing
native/display thresholds remain unchanged. These focused results do not replace
the complete release gate or establish a new installed build. Current complete
validation and installation status belong in [HANDOFF](HANDOFF.md).

A synthetic local Rust regression reproduces a truncated classic tag row that
previously panicked during Plus upgrade. The adapter now rejects missing fields
with an error and preserves its captured catalog; pinned upstream files remain
unchanged. The locked CDJ library, binary and fixture-adapter suites pass. This
uses generated TEMP catalogs and grants no new USB or source-write authority.

Disposable stock shadows and stock/Rockbox derivative caches also reject malformed
metadata as cache misses. Fixture regressions preserve canonical audio/art and
exact valid derivative bytes while repairing only disposable entries. Stock
ArtworkDB parsing rejects impossible dataset counts and zero-length chunks before
they can drive unbounded work. Ownership manifests, recovery receipts and source
proofs retain their stricter admission rules.

Exporter writes share descriptor-pinned staging. TEMP regressions cover old
staging symlinks, exclusive creation, parent/root replacement, owned-stage
replacement, cancellation, verification and flush failures. In-place artwork
ranges reject links and verify through one held descriptor. Whole-device erase
confirmation binds the mount identity before its dialog. These tests use only
generated local folders; they grant no physical-device acceptance or erase replay.
Model detection reads at most1MiB+1 per SysInfo candidate. Manifest publication
retains byte-exact JSON/schema/filtering through the shared writer; the macOS
descriptor fast-copy path and unsupported-filesystem fallback remain covered.
Optional AppleDouble/partial-file cleanup uses held directory descriptors and
rejects redirected Music/Playlists roots or replaced parents before unlinking.
Generated regressions preserve outside originals and existing device files.
Cancellation, resync and manifest fixtures now retire their own temporary folders
through unittest cleanup without changing their cases or assertions.

## Responsiveness delivery — November4–5

The final bare `./check.sh --render` passes all2795 discovered cases:2729 offscreen
in457.657s and66 Cocoa in69.078s, zero skips/expected failures and exact inventories.
Lint/types, native/CDJ builds,243 pinned vendor files and required offline fixtures
pass. New coverage verifies eager Settings controls/saved colors before Show,
one owned account refresh, event routing, browser metadata precedence, newest-draft
rollback, exact JSON comparison, stock final-byte semantic validation/fresh source
phases, USB settings source separation and signed-range byte parity.

Retain the interrupted inventory attempt (a test landed after discovery), the
complete offscreen run's stale lazy-Settings assertion failure, and the457-case
UI replay after updating that assertion to require eager readiness. The final
complete gate follows those attempts; no cases, phases or thresholds were removed.
Both signed profiles/code parity and package/installed isolated native/process/
RAM-SQLCipher checks pass. Renders and authorized normal startup were verified.

The November5 documentation-only wrap-up reuses these proofs only after verifying
all693 frozen source/test/build/asset inputs remain exact. It changes no runtime,
packaging profile, test inventory, required fixture or installed bytes; no rebuild,
restamp or real-app controls are part of publication. HANDOFF records current
provenance, preserved state and independent hardware/firmware acceptance limits.

## Whole-app delivery — later November4

The final bare `./check.sh --render` reconciles2779 cases:2713 offscreen in 460.325s
and 66 Cocoa in 68.917s, zero skips/expected failures. All 694 frozen inputs remain
exact. The first offscreen cue-style failure and its ordered reproduction remain
retained; the corrected prefix and ten rendered states pass. When the console
locked, only the owned shell was held before render/Cocoa; Sonny unlocked, fresh
console/process/source proof passed, and the same shell resumed. No locked-display
failure was accepted and no thresholds changed. Both signed profiles match tested
Python/entrypoint/native code; package and installed native/process/RAM-SQLCipher
probes pass. Fresh actual state/authorities/art/icon remain exact through normal
reopen. This is dated release evidence; HANDOFF records current identity/retention
and the independent physical-device/firmware limits.

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

Final November3 night delivery for runtime fingerprint `fc696312…` (published
application source `d08ee91…`; installed signed base `d19f3eb…`, dirty=true)
passes2531cases:2467offscreen in
618.725s and64Cocoa in67.664s, zero skips/expected failures. The full gate was
rerun after the catalog alternating-row contrast correction. Both signed public/
personal packages and isolated native ORM/SQLCipher probes pass; installed normal
startup and exact current library/authority/icon preservation pass. Dated hashes,
review scope and remaining hardware/network limits are in [HANDOFF](HANDOFF.md).

## Earlier November4 build160 delivery and source publication

The earlier installed build160/runtime7bbe8577… and source release4a3d29a… retained the
November3 coverage and add Info first-frame/publication lifetime, multi-target
Export selection/identity/cancellation, selected-catalog preservation and
reference-counted removal/retirement recovery, plus format source/postcondition
checks. Reconciled reports pass2564cases:2500offscreen and64Cocoa, zero skips or
expected failures. Both signed profiles match675frozen source/test/build inputs;
160personal/156public packaged modules and native code sections match the tested
source/builds. Installed isolated startup and exact curated-state/icon checks pass.

A120Hz cadence check failed at4%battery (65presentations against a108minimum).
After AC was connected, its focused check and the complete64-case native phase
passed with unchanged source and thresholds. Retain both runs; do not silently
accept the battery failure or omit the case. Stable display/power conditions
matter for this timing gate. The native rerun and complete report inventories
were verified together. Current provenance and physical acceptance limits remain
in [HANDOFF](HANDOFF.md); documentation publication does not restart the app.

## November4 build163 USB/theme delivery

Tested source `cf2ef5b` passed the final bare gate:2595cases, comprising2531
offscreen in579.369s and64Cocoa in67.506s, zero skips/expected failures. Both signed
package profiles match all690frozen source/test/build inputs, with161personal/
157public first-party Python modules plus both entrypoints. Installed build163
has runtime fingerprint `42aefd35…`; exact bytes and preservation are in HANDOFF.

New regressions cover explicit-only generic-USB catalog reads, no idle drift or
completion/eject scan, discovery retirement before eject, selected source-capture
bounds, same-filesystem format reuse and layout/source guards, renderer-lock
exclusion from initialization, collapsed Export folders, current/live accents,
one/two/five-device layout, filled-region progress lettering and track-only browser
selection. A prior full run exposed an iPod tab-navigation regression and an old
USB browse expectation; corrected focused checks and the final complete gate
passed. Keep the failed reports as failure evidence, not an accepted release.

Separate TEST-1/TEST-2 format/export/update/repeat receipts exercise small frozen
fixtures on FAT32/exFAT hardware. They do not certify players, broad performance,
iPod physical behavior or public distribution. No new native-library or codec
authority follows from this delivery. The documentation review at that checkpoint
did not rerun the gate, rebuild/reinstall its unchanged runtime or interact with
the real app.
