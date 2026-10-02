# SoundBox current handoff

Updated October 2, 2026, America/Chicago.

## Release state

Branch `main`; runtime release **66aa2b7072d8b885c34e3620a65cf29e26899f9e** is
committed and pushed to `origin/main`. This handoff update is a documentation-only
follow-up; use `git rev-parse HEAD` for its containing repository revision.
This release includes the accepted waveform/color corrections,
compact import-first analysis, provider/build composition, device mount/status
presentation, regression gates and reconciled documentation. The installed app
already contains these runtime sources; only documentation changed during the
October 2 wrap-up, so no redundant rebuild/install or provenance restamp was done.

Full access restored the blocked Git, Metal/Cocoa and signing services. Final
October 2 validation passes: bare `./check.sh`, then `./check.sh --native --render`:
**1033 tests, 13 platform/fixture skips, one known expected failure**; the separate
Cocoa gate passes **41 tests with no skips**. The earlier restricted-session
failures were infrastructure failures, not accepted test results. Deep/strict
verification of the installed app signature now passes unchanged.

Main/status/low-space/device and paired public/personal artwork renders were
inspected. Harnesses suppress real device/library discovery and use disposable
state; native tests use generated audio. Runtime files have byte parity, relative
Markdown file links resolve, and `git diff --check` passes. The developer
requirements now pin the numerical test/tool SciPy dependency; `pip check` passes.
Git publication used the existing `~/.ssh/lowfreqlabs_key` explicitly because
the default SSH agent had no loaded identities; no remote/config changes were needed.

Final installed-source fingerprint, executable/native hashes and live-library
hash were rechecked October 2 at **16:03:18 CDT**. All match the accepted install
below. The installed process is still running; recent preparation passes report
zero tasks. Evidence: `wrap-oct02-final-verification.json` in the existing rollback
directory. Earlier inventory comparison found unchanged analysis payloads and art;
only disposable `preparation-index.json` receipt metadata changed. Counts remain
2925 / 3299 / 7637; evidence: `wrap-oct02-readonly-state.json`.

## Current result

Sonny reported BLUE still mostly white at maximum zoom-out. The previous
installed arithmetic/sampling corrections did not fix its palette. **The BLUE
color correction is now built, installed and relaunched.** Sonny found the first
candidate better but still too white; after narrowing the white center further,
he accepted the muted live comparison: **“B looks right now.”** The preview is
closed and the real app is running.

PWV3's edge selector now covers a wider blue/cyan range. The interior transitions
from cyan to white with normalized signed-interval width, and its radial blend
starts at zero instead of retaining a broad white plateau. This is a color-only
change: signed bounds, detail, source rates, LOD, one-pixel footprint, clocks and
Sonny's accepted complete-corner sampling B remain unchanged. No cache recook.
See `docs/waveform-reference-check-2026-10.md` for fit evidence and limitations.

The gamut expected failure is retired. Actual Metal tests now require thin
centers to reach cyan and broad intervals to avoid the former white plateau;
mutations restore both bugs and must violate those requirements. The independent
legacy signed-coverage approximation remains a named expected failure. Exact
rekordbox pixel parity is not claimed. 3BAND imports remain fully intact at
150 Hz; its exact layer heights/interpolation remain separately unverified.

## Repository and installed app

The repository release and installed provenance are distinct. The installed app
was built from dirty source based on **417748fc0c0a7d500996fa23bfe6c7ac5270eedb**;
its runtime fingerprint matches this release exactly. Public artifact was not
rebuilt for the latest waveform corrections.

`/Applications/Soundbox.app`: personal **build 119**, source base 417748f,
**Soundbox Self-Signed**; deep/strict signature verified.

- Source fingerprint: `9b9d26debb84675ca361707730789b7d4b5b0b71afc769c92cc91e46508a0a9d`
- Executable SHA-256: `807fb21112b979dc071bc0e474479b018e2a9dd03865a2d6fa68584a7c7330f1`
- Installed native SHA-256: `301948118ce3c2edec6503fc8c8978235810ed4799f3cab707dde5bc02f5a90e`
- ABI 5. Installed executable/native bytes match signed dist. Native executable
  code and shader constant sections match the tested build despite signing's
  metadata changes. Signed source fingerprint matches current runtime sources.
- Startup October 1 **01:22:24**, native CoreAudio/Metal active **01:22:25**.
  Snapshot and fresh read-only rekordbox load: **3398 tracks / 75 playlists**.
  Reused 2889 analysis receipts, zero preparation tasks. Main window is visible.
- Preserved settings: BLUE / **128**. Preview zooms did not change app settings.

## Validation and limits

- October 1 pre-install gates passed and the October 2 final gates above confirm
  the same result. Twelve headless GPU tests execute actual production Metal
  helpers. Compiler/GPU failures cannot be swallowed by expected-failure handling.
- Offscreen render gate passed and its output was inspected during this change;
  the final radial-only refinement was then rechecked through the native gate.
  Automated native tests use generated audio/state, not real devices/sources.
- Final **507 real-track native frames** cover all modes/stops, three positions,
  four subpixel phases and recenter. All **338 RGB/3BAND frames are pixel-identical**
  to the preceding installed renderer. BLUE recenter returns match exactly at
  every stop. Shared geometry and detail remain untouched.
- Sampled max-zoom-out near-white waveform pixels fall from **39.1% to 20.5%**.
  This threshold metric demonstrates less washout; Sonny's final B acceptance is
  separate evidence. It does not prove every track or exact reference pixels.
- Previous stable-moment audit and synthetic all-stop translation tests remain
  intact; see `docs/waveform-test-audit-2026-10.md`. No new paired phone/rekordbox,
  audible/long-session synchronization or zero-drop physical-panel claim.
- Evidence and read-only native previews live under ignored
  `build/waveform-test-audit/` and `build/waveform-pulse-investigation/`.
  Diagnostic profile setters and joint-coverage experiment stay outside production.

## Curated state and rollback

Use only `/private/tmp/soundbox-lifecycle-rollback.18e3k4di/`.
`before-blue-color.app` is the immediately preceding installed stable-moment app,
fingerprint `f5169fd881fed97f1d8629eb4bdced4b9b5a5e5c2250e1a7fefeae2cb400adca`.
Earlier `before-stable-moments.app`, `before-complete-corners.app`,
`before-color-pulse.app` and their evidence remain preserved. Installation used
move + ditto after verified graceful process exit. Apple quit again reported
“User canceled” despite verified exit; no force quit or AppData replacement.

Current snapshots: `knots-blue-before/`, `knots-blue-prequit/`,
`knots-blue-postquit/`, `knots-blue-postlaunch/`. Counts stay **2925 analysis /
3299 art / 7637 stock-art files**. All path/size/mtime inventories and exact
small-index bytes match across installation. Library changed only
`/settings/columns_state` on quit; postquit/postlaunch are byte-identical:
`5605e815417d600f68b1aba4c700687383c4d79229e919238e47da245d858fd8`.
Prequit: `9d836b17df84e01c4079ee5cc870a2b93729d445f1c905ba95b00604ec575c24`.
All curation is unchanged. Provenance: `blue-installed-build.json`.

Before this install's baseline, only Sonny's mode/zoom differed from the last
install: RGB/256 → BLUE/128. The preparation index periodically renews receipts
at the half-hour worker policy boundary; this is not an analysis recook. The
new install's exact index bytes and inventories stayed unchanged.

Preserve generation-58 recovery `20260926-152340-47477cfa4a49` until separately
reviewed hardware acceptance. Pristine offline capture remains
`soundbox-ipod-ref/offline-lifecycle/20260926-163945/`; never restore it to hardware
without a separately reviewed recovery plan.

## Remaining boundaries and next checks

- Observe accepted BLUE on additional tracks. Preserve signed detail and accepted
  sampling if further artifacts appear. Exact palette/radial parity and separate
  3BAND layer-height/interpolation calibration remain open; do not reduce full-rate
  detail to disguise a mismatch or silently promote joint coverage.
- Import-first compact V5 and known MPEG-1 LAME origins remain implemented.
  Old caches upgrade only on selected local load or explicit reanalysis. Manual
  cues/grids and ready old records remain preserved. Full fallback/encoder parity,
  exact zoom windows and long-session synchronization remain open.
- Public/personal composition and provider admission remain implemented; public
  clearance/notarization/clean-machine acceptance remain open.
- Device status/mount work remains installed. Physical reconnect timing, A5/A7,
  no-op/playlist-only Sync, overlapping drops, cancel/retry and eject acceptance
  remain on ROADMAP. No device write or firmware experiment was performed here.
- Source audio, rekordbox/ANLZ and `/PIONEER` remain read-only. Generic USB
  optimization, CDJ export and embedded-art extraction are outside this change.
