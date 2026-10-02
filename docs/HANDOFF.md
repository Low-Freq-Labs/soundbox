# SoundBox current handoff

Updated October 2, 2026, America/Chicago.

## Release state

Branch `main`; this handoff accompanies the coverage correction and strict release
gate commit following **57931c8f5b16377c0c2e9494e1ca0c67cfd135b6**. Use
`git rev-parse HEAD` for the containing repository revision. The earlier waveform,
BLUE palette, compact import-first analysis, provider/build composition and device
status changes remain included. The installed app's runtime fingerprint matches
these runtime sources; its true build provenance is recorded below.

The old final fragment replaced correct joint interval coverage with the minimum
of two separately averaged edges. That overfilled signed crossings. RGB/BLUE now
composite the joint coverage and visible color directly. Source extrema, mips,
LOD, palette, clocks and the one-pixel footprint are unchanged; no detail
smoothing was added. 3BAND rendering is unchanged.

Sonny accepted the earlier BLUE white-center correction as “B looks right now.”
For this additional coverage correction, his muted playback comparison feedback
was **“hard to tell but B looks good.”** The preview is closed and the real app
is running. This is visual feedback, not a claim of exact rekordbox parity.

## Validation

- Bare `./check.sh` passed, then final `./check.sh --native --render` passed on
  October 2: **1076 tests, zero skips, zero expected failures**. The final run
  includes two additional fixture-admission tests: 1035 offscreen + 41 Cocoa.
  Native build, pyflakes and engine mypy passed; the offscreen render was
  inspected. Build tools still emit ordinary compiler/PyInstaller warnings.
- Both Qt phases are now mandatory in the default gate. Every discovered test
  must run exactly once; inventories are reconciled. Missing fixtures, skipped
  tests, expected failures and omitted tests fail the gate. The gate itself has
  mutation tests for false success and incomplete/duplicate execution.
- The former numerical expected failure is now a passing final-fragment oracle.
  Independent quadrature covers signed crossings and random visible colors;
  restoring the old alpha override demonstrably fails. GPU/compiler failures
  remain real failures.
- The nine offline iPod cases run against preserved metadata copies. Three
  missing historical reference checks were replaced with a frozen current
  Nuclear source/cache and supplied-recording frames for all three modes.
  Hashes are pinned in `tests/fixtures/waveform-reference.json`. Private snapshots
  are required; a checkout without them fails preflight. No routine test reads
  the live library, source collection or devices. See
  [test-release-gate.md](test-release-gate.md) for provisioning and scope.
- Before/after native captures: **507 frames each**, all modes and thirteen zoom
  stops, three song positions, four subpixel phases and recenter. All **169 3BAND
  frames are pixel-identical**. Corrected recenter returns match exactly.
  Mean RGB/BLUE change is below 0.55 codes/channel at each zoom; localized edge
  differences reach 78 codes. Preview self-checks matched standalone captures.
- Evidence: ignored `build/clean-release/`; final phase reports also retained in
  the rollback directory's `coverage-check-reports/`. Earlier pulse/color evidence
  remains in `build/waveform-test-audit/` and `build/waveform-pulse-investigation/`.

## Installed app and curated state

`/Applications/Soundbox.app`: personal **build 121**, ABI 5, signed
**Soundbox Self-Signed**. Built from dirty source based on
**57931c8f5b16377c0c2e9494e1ca0c67cfd135b6**; do not restamp it as the later
release commit. Deep/strict signature verification passes. Installed executable
and native bytes match signed dist, and the source fingerprint matches current
runtime sources. Public artifact was not rebuilt for this correction.

- Runtime fingerprint: `3c93d989e580eba6cb20e0032e594d4f6ffb1e8e73a2dd708f7986f8f8542c39`
- Executable SHA-256: `449247bd251ea97060cb34ce656d2e1e2a6cf1688336204af0e55a566d1611d2`
- Native SHA-256: `6de2411a8a28e8f1860105a6b25a2b5dda36f0a2104b14107b992fbc2ad1deb5`
- Startup October 2 **16:35:18 CDT**, native CoreAudio/Metal active **16:35:19**.
  Fresh read-only rekordbox load: **3398 tracks / 75 playlists**. Reused 2889
  analysis receipts; **zero preparation tasks**. No library/cache rebuild.
- Verified installed provenance/state at **16:36:03 CDT**:
  `coverage-installed-build.json` in the rollback directory.
- Settings remain BLUE / 128. Prequit → postquit changes only
  `/settings/columns_state`; exact curation is unchanged. Postquit and postlaunch
  library bytes match: `8f724ab5dacb51bef596b699de9389462f6bf44def2e4c4b55d20a159cd783f6`.
  Prequit hash: `5605e815417d600f68b1aba4c700687383c4d79229e919238e47da245d858fd8`.
- Counts remain **2925 analysis / 3299 art / 7637 stock-art files**. All
  path/size/mtime inventories and small-index bytes match from prequit through
  postlaunch. Earlier preparation-index receipt renewal is disposable metadata,
  not an analysis recook.

## Rollback and remaining boundaries

Use only `/private/tmp/soundbox-lifecycle-rollback.18e3k4di/`.
`before-coverage.app` preserves build 119, fingerprint
`9b9d26debb84675ca361707730789b7d4b5b0b71afc769c92cc91e46508a0a9d`.
All earlier rollback apps and evidence remain preserved. This installation used
move + ditto after verified graceful exit. Apple quit returned “User canceled”
despite verified exit; no force quit or AppData replacement occurred.
Snapshots: `knots-coverage-before/`, `knots-coverage-prequit/`,
`knots-coverage-postquit/`, `knots-coverage-postlaunch/`.

Preserve generation-58 recovery **20260926-152340-47477cfa4a49** until separately
reviewed hardware acceptance. Pristine offline iPod capture:
`soundbox-ipod-ref/offline-lifecycle/20260926-163945/`. Never restore it to hardware
without a separately reviewed recovery plan.

No known failing/omitted test remains in the release gate. That does not establish
exact rekordbox pixel parity: BLUE radial-centroid and retained five-probe profile
approximations, exact 3BAND layer-height/interpolation calibration, broader
track/panel and long-session synchronization acceptance remain explicit in
[PLAYER.md](PLAYER.md) and [ROADMAP.md](ROADMAP.md). Preserve source detail if
further artifacts appear. Frozen-reference palette/integrity tests do not prove
rendered pixel equivalence.

Import-first compact V5 and recognized MPEG-1 LAME origins remain implemented;
old caches upgrade only on selected local load or explicit reanalysis. Full
fallback/encoder parity and exact zoom-window calibration remain open.
Public clearance/notarization/clean-machine acceptance and the device physical
reconnect/Sync/cancel/eject cases remain on ROADMAP. No device writes or firmware
experiments were performed here. Source audio, rekordbox/ANLZ and `/PIONEER`
remain read-only. Generic USB optimization and CDJ export are outside this change.
