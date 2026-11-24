# Release test gate

Local validation workflow revised November20, 2026 at Sonny's request. Dated
results below retain their original scope. [HANDOFF](HANDOFF.md) records source
validation and installed identity separately.

Older sections are dated evidence, including their original retention statements.
Keep compact receipts and necessary unfinished-operation recovery. Once the
verified update is up and running, remove completed diagnostics/captures and
installation/migration snapshots under the November23 retention contract; required
offline test fixtures remain protected.
Historical paths do not imply all original full captures remain on disk.

## Fast local fixes and complete publication checks

- Small fix: run its regression and affected integration/lifecycle/error cases,
  plus relevant lint/types. Use Cocoa or an inspected render for UI changes when
  needed. Reuse passing evidence until inputs change or a concern remains.
- Local install: focused evidence is sufficient with the existing strict package,
  isolated startup and guarded state-preserving install/reopen checks.
- Before any commit/push or wrap-up: run `./check.sh --render` on the final tree.
  The complete suite and both Qt phases remain required. A broad change may need
  this earlier when a confident focused selection is not possible.

```sh
./check.sh --focus tests/test_sidebar_layout.py
./check.sh --focus test_prep_tags.TagInterface --cocoa
.venv/bin/python -m pyflakes desktop/prep_tags.py tests/test_prep_tags.py
./check.sh --render
```

## Choose an area and depth

```sh
./check.sh --list-suites
./check.sh --suite rb-db --level quick
./check.sh --suite tags playlists --level integration
./check.sh --suite player waveform --level quick
./check.sh --suite usb --level integration
./check.sh --suite usb --level recovery --plan
```

| Depth | Coverage |
| --- | --- |
| `quick` (default) | Small behavior/reader/guard checks and selected native smoke tests. |
| `integration` | Quick plus related controllers, workers, publication and lifecycle interactions. |
| `recovery` | Integration plus the area's transaction, durability, cancellation and process-loss proofs. |
| Complete gate | `./check.sh --render`: every discovered case, native/CDJ builds, fixtures and static checks. |

Choose independently or combine these maintained areas:

| Area | Main responsibility |
| --- | --- |
| `library` | Collection, browsing, imports, registration and retained organization. |
| `rb-db` | Rekordbox readers, identity, authority, source writers, readback and ACKs. |
| `metadata` | Titles, artists, aliases and curation protection. |
| `tags` | My Tags, rapid assignments, create/rename/delete and ordering. |
| `playlists` | Regular/smart lists, folders, membership, moves and presentation. |
| `prep` | Hot/Memory cues, grid, BPM and pending prep writes. |
| `player` | Playback, decode, seek, transport and keyboard gestures. |
| `waveform` | Waveform packets, geometry, palettes, zoom and Metal presentation. |
| `analysis` | BPM/key/grid detection, analysis packets and scheduling. |
| `artwork` | Source images, defaults, review, thumbnails and export derivatives. |
| `usb` | **Generic USB/CDJ core:** explicit exports, catalogs, settings, selected metadata, eject and filesystem writes. |
| `ipod` | **Separate iPod core:** stock/dual/Rockbox exports, sync/drift, playlists, deletion and recovery. |
| `state` | Canonical records, sparse saves, backup/restore, worker ownership and shutdown. |
| `ui` | Shared layout, theme, dialogs, browsing and saved preferences. |
| `providers` | Provider admission, connections, cancellation and socket boundaries. |
| `tooling` | Test routing, catalog, release inventory, packaging and provenance. |

Levels add coverage; they do not change assertions or fixture behavior. A quick
RB DB run is reader/guard smoke, not a substitute for affected source-write
recovery tests. Use `rb-db` plus the changed domain for native edits; use that
domain's recovery level when SQL, authority, journal or ACK logic changes. Combine
`player prep` for cue/grid edits during playback, `waveform analysis` for packet
changes, `usb artwork` or `ipod artwork` for exported covers, and add `state` for
cross-worker ownership/restore/quit changes. Exact `--focus` selection remains
available for smaller regressions; it can also supplement a named run.

`--plan` prints selectors and planned platforms; it runs no tests and imports no
Qt/test modules. Named groups automatically route native-deck cases to a separate
Cocoa process after offscreen finishes. `--cocoa` can explicitly run all selected
UI checks on Cocoa. Overlapping areas, modules and individual tests execute once
per run. Required platform/fixture prerequisites stay real; they never become
skips. Rebuild affected native/CDJ components separately when their code changes.

Named runs write `build/check/focused/suites.json` with requested areas, depth,
selectors, planned phases and actual results. Phase receipts/logs live alongside
it under `suites-phases/`; `--report PATH` selects another receipt and sibling
phase directory. Reports start failed/running, clear old aggregate/phase JSON,
and cannot become green with a failed/missing/mismatched child. Scope remains
focused, so complete-release verification rejects these results.

The editable registry is `scripts/test-suites.json`; `scripts/test_suites.py`
validates its static references without loading tests. The deepest union includes
every complete test module, enforced by the catalog test; new tests must be mapped.
The complete release gate still discovers independently, so the registry never
filters its inventory. Small runs are serial within each selected Qt phase; use
the parallel complete gate for whole-app validation rather than combining every
recovery area merely to emulate it. All source/device fixtures remain isolated;
these groups do not certify physical players or authorize live source/device tests.

## Exact selectors

Focused selectors accept `tests/test_name.py`, `test_name`, `test_name.Class` or
`test_name.Class.test_method`. Overlapping selections run once, with module/class
fixtures grouped. Only selected modules import; Qt AppData/Cache/Config remain
private. `--cocoa` runs the selected tests on Cocoa; it is required for native-deck
cases. Use the default offscreen platform for ordinary checks. Repeat on another
platform only when the behavior requires it, rather than automatically doubling
all checks.

Focused results go to `build/check/focused/offscreen.json` or `native.json`; use
`--report PATH` for an explicit receipt. They declare `scope="focused"`, fail on
skips/xfails/unrun cases, cannot overwrite the full aggregate paths and are rejected
by the full-release verifier. A failed selection removes its old report before
loading, so it cannot reuse an earlier green result. The command runs tests only:
run relevant static checks and rebuild changed native components separately.
It does not perform unrelated native/CDJ builds, global fixture preflight or a
full-suite discovery. No focused result grants source/device authority or permission
to weaken fixtures, recovery coverage or the install procedure.

## Complete gate

`./check.sh` checks authored-folder guides, local links
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

Device staging additionally exercises real FAT32 and exFAT through disposable
disk images using macOS `hdiutil`. Mounts are private temporary paths with
`nobrowse`; fixtures never enumerate or write connected physical devices.
Their cleanup detaches only the captured image session without force, preserving
the backing image if detach fails. Image setup/cleanup failures fail the gate;
APFS-only tests and skipped filesystem cases cannot substitute for this proof.

The [November2 runtime audit](testing-runtime-audit.md) records the complete
timing review, isolation checks and a controlled encrypted-subset comparison.
Each case still runs with its existing cipher/recovery settings. Module weights
affect scheduling only; new tests cannot be excluded by missing timing hints.
Refresh hints from a complete passing offscreen report with:

```sh
.venv/bin/python scripts/update_test_costs.py build/check/offscreen.json
```

The updater rejects focused/failed/incomplete timing evidence. It changes only
whole-module scheduling weights, not inventory or fixture behavior. The dated
[November20 audit](testing-runtime-audit.md#focused-workflow-and-scheduling-review--november20)
distinguishes the measured current imbalance from the projected scheduling gain.

## Complete wrap gate — November20

The final bare `./check.sh --render` passes5,083 discovered cases:5,015 offscreen
in744.538s and68 Cocoa in84.204s. Inventories reconcile exactly, with zero skips
or expected failures. All924 frozen inputs were exact at completion. Publication
normalizes one superfluous EOF newline in `test_playlist_sparse_state.py`, with
identical compiled code, AST and positions; the other923 inputs remain byte-exact.
Lint, layout/links/instruction parity, engine types, native/CDJ builds, pinned
vendor integrity and required offline fixtures pass. The synthetic12-song,
zero-device render was inspected. Reports and frozen inputs are retained under
`~/Library/Logs/Soundbox/wrapup-20261120/final-gate/`.

This also validates the new focused/named routing and complete area catalog on
the current source. Read-only installed verification matches233 runtime modules,
startup code and four native code sections to the same tested runtime88b6.
Ordinary deep signature verification passes; saved-color Finder/Dock metadata
outside signed Contents is preserved under the install contract. No rebuild,
restamp, live-source/device test or automated real-app control occurred during
this wrap. [HANDOFF](HANDOFF.md) records delivery and retention separately.

## Historical offline shared-library source gate — November13

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
`offline-followup-gate/` and `prep-domain-completion-review.json`. This validated
that source checkpoint, not native rekordbox business-profile acceptance or installation.
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

## Current waveform regression coverage

The November21 renderer retains RGB's exact encoded compositor and shared
BLUE/RGB signed interval coverage. Both modes use physical-pixel phase increments
at 64/128/184 points/s, logical-point increments at 576/864, and continuous motion
at the other stops. Independent PWV7 3BAND keeps continuous motion and its own
per-band attributes, max-mips and 1.35 gain. No cross-mode geometry substitution
is permitted for 3BAND.

RGB's original numerical oracles remain unchanged. Generated before/after frame
proof covers all 13 zooms, 1×/2× scales, three phases and signed detail present or
absent; optional BLUE mini metadata is included. The November21 protected
RGB/BLUE comparison has 624 exact frame/attribute cases, and all 312 constant or
varying-band 3BAND attribute cases remain exact. These are bounded comparisons,
not literal video equality or universal native color parity.

BLUE's native palette/encoded-gradient tests include near-black partial coverage,
unclassified spectral inputs and silence without an opacity floor. Its dense
color-integration oracle permits at most half an 8-bit encoded code step; exact
coverage and analytical controls retain their separate tighter bounds. Main
3BAND joint membership tests compare crossing profiles against independently
integrated vertical stripes, including absent bands and finite center pixels.
They reject false overlap colors and fabricated zero-energy center ink.

Optional previews have independent reader, grouped readiness and generation
tests. The actual C++ PWV6 producer is compiled against independent native
stack/global-normalization formulas; GPU sampling checks premultiplied bilinear
pixels, the opaque native row 23 baseline and transparent row 24. The mini's
native baseline never grants a main-waveform silence floor. Invalid/missing
preview metadata must produce a complete resident fallback before publication.
Routine tests use generated inputs and frozen fixtures, never live library,
audio or device discovery. [PLAYER](PLAYER.md) describes current rendering;
[HANDOFF](HANDOFF.md) separates focused evidence, full gates and installed identity.

## Historical coverage corrections — October 2026

The policies and measurements in this section describe their dated October
checkpoints. November21 supersedes BLUE's radial/linear-light palette and phase
rules and 3BAND's former product-of-averaged-masks compositor/derived mini. Current
coverage contracts are above; preserve the original RGB numerical oracles.

The old fragment replaced joint interval coverage with the minimum of two
separately integrated edges. At a signed crossing, those edges can cover different
horizontal parts of one pixel; taking the smaller average overfills it.
The October 2 correction made the fragment composite the joint coverage and
visible color returned by `resolve_interval_surface`. BLUE retains that exact
area contract. The October6 conservative occupied-column RGB presentation is
historical. Current RGB retains the monotonic faint-coverage shoulder below0.18
at every zoom, including384; its filled palette evaluates the same accepted
92pt/s song-clock reference at every stop. Geometry, LOD and motion remain
independent of that color policy. At that October checkpoint, RGB used logical-point
increments at576/864; the October31 accepted correction used physical-pixel
increments at64/128/184. Other RGB stops and3BAND remained continuous. BLUE then
retained physical-pixel increments through384 and logical points at576–1500. The production offset helper
is compiled independently across all modes, zooms, scales, phases and overlapping
tile origins; restoring fractional128/184 motion must violate the targeted contract.
Native drawables verify the matching hold/one-pixel advance and retained translation.
HANDOFF distinguishes current source/release/install evidence.
The October28 RGB edge update integrates encoded channels and decodes the final
encoded-space background blend for Metal; BLUE then remained linear-light. Both existing
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
