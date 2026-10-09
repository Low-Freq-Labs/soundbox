# SoundBox current handoff

Updated October 9, 2026, America/Chicago.

## Current release and next action

Sonny accepted the installed build (“loving this build”) and requested wrap-up.
The October 2–9 waveform/startup/palette/background-detail/rapid-load and
playback-preserving browser-load work is complete in release commit
**126a5e3a39262dc86013c22e6d26937c71b6c217** on `main`, tracking `origin/main`.
This handoff follow-up changes documentation only; `git rev-parse HEAD` identifies
its containing repository revision. No diagnostic window remains open.

Next: Sonny plans one to two weeks of normal use. Preserve this accepted build;
respond to concrete new reports. No renderer experiment, manual cache rebuild,
cloud download or device experiment is queued. [ROADMAP](ROADMAP.md) separates
completed work from deferred implementation and physical/public-release gates.

The installed personal app is **build 122**, built from dirty source based on
`72d06f7c43abe51bb17a9939918a727b1f5e81b3`. Its signed source fingerprint is
**4c34677dd9c7a4006b8a0cf86cebee93efab080f3ea49cdba9ec4c622550b260**.
That fingerprint matches the released runtime sources. The repository release
and installed signed Git revision are different: wrap-up changed documentation
only, so the accepted app was retained without rebuilding or restamping it.

## Checkout cleanup — October 9

The follow-up folder cleanup changes documentation and local generated files only.
Historical player experiments and completed audits now live in `docs/archive/`;
`docs/IDEAS.md` replaces the dated root ideas file and corrects stale UI claims.
Current documentation links and both runtime instruction files follow those paths.
Obsolete packaging outputs/caches went reversibly to Trash. Diagnostic evidence
moved outside the checkout with original subpaths and a move manifest; see
[PROJECT_LAYOUT](PROJECT_LAYOUT.md) for exact locations. Earlier `build/` evidence
references below now resolve under that archive. Current `build/native/` and
`build/check/`, the development environment and required fixtures remain.
Installed app, AppData and protected recovery are unchanged. The cleaned checkout
passed bare `./check.sh`: 1,104 tests, zero skips/expected failures, plus native
build and lint/types. All 130 relative Markdown references/anchors, instruction
parity and diff whitespace checks passed. No UI/runtime source changed, so the
already inspected render and accepted installed binary remain applicable.
Generated tool/Python caches were moved to Trash again after validation.
The initial cleanup moved 5,435,468,547 logical bytes out of the project.
At final verification the first Trash batch was already absent; its removal was
not performed by this cleanup. All 13 diagnostic archive directories remain.
The external archive records this separately in `post-validation.json`.

## Validation

Final October 9 **`./check.sh --render` passed all 1,104 tests** (1,053 offscreen
+ 51 Cocoa), zero skips/expected failures, reconciled inventories, native build,
lint/types and visually inspected render. Runtime sources are unchanged from the
accepted installation. Instruction-file byte parity, relative Markdown files/
anchors, full intended diff review and `git diff --check` also pass.
Regressions cover all-zoom drawable/Metal contracts, codec readiness and compact
supplements, cancellation/generation rejection, automatic background upgrades,
and playing/paused/rapid browser replacement including pending grid and CUE.

The earlier pre-install run failed the existing resize physical-screen-pixel
assertion; its isolated rerun and the subsequent complete gate passed unchanged.
Cause was not established; no assertion/threshold was weakened or skipped.
Evidence: `build/playback-load-review/initial-native-failure.json` and
`resize-pixels.png`. Keep this observation if that test fails again.
The initial wrap-up gate also missed the terminal-edge event within its existing
one-second timeout. Forty unchanged isolated repetitions passed; root cause is
not established. Evidence: `build/wrap-oct9/initial-native-failure.json` and
`terminal-repeat.json`. The subsequent complete release gate passed unchanged;
no production/test code or threshold was changed.

## Installed runtime and state

Built with `./build_app.sh`; installed **2026-10-09T03:43:46.097655-05:00**, verified
**2026-10-09T03:44:05.582782-05:00**. At installation the strict/deep signature
passed (Soundbox Self-Signed), executable/native hashes matched signed dist, and
the source fingerprint matched runtime sources. The later directory cleanup
retired the redundant dist copy; installed hashes and source fingerprint still match
the recorded installation. Startup/native-deck/library and preparation log checks are
recorded in `build/playback-load-review/startup.log`: startup 03:43:48, native
deck active 03:43:49, 3,398 tracks / 75 playlists loaded 03:43:52, and zero
preparation tasks at 03:43:53 CDT. Native ABI remains 5.

- Executable SHA-256: `c9fe66384ecc915094740ea8ca10d118b02be604bf15d6c43797121bbb621d91`
- Native SHA-256: `b330164985ddc343bf261852582c3f9da5fb7af521b1134fc1fa849923109185`
- Prequit library SHA-256: `ee7de2b4691463a672d3f89adcd0faab540cc83c9a2706eeddd1797a60d5c0ad`
- Post-quit/postlaunch library SHA-256: `68de3760676aeaafb5ba7de999c38ff887f485276bd32efd71eef14cc3780995`
- **2,925 analysis / 3,300 art / 7,637 stock-art files**.
  Final library bytes and all three cache inventories are unchanged after
  relaunch. Only `/settings/columns_state` changed on graceful quit; curation
  remained intact. The user's preexisting zoom
  setting change since the prior verification is preserved. Exact quit-time
  differences are in `build/playback-load-review/quit-state-diff.json`.

Wrap-up reverified the running app at **2026-10-09T17:01:25-05:00**: PID 48341,
valid strict/deep signature, matching executable/native hashes and source
fingerprint. Library bytes still match the postlaunch baseline. Analysis/art/
stock-art inventories are unchanged except the disposable preparation receipt:
2,889 signature values refreshed, with no membership changes, consistent with
its half-hour policy bucket. Exact receipt differences and runtime evidence are
in ignored `build/wrap-oct9/`; snapshot: `knots-wrap-oct9/` in the rollback area.
Current logs continue to report zero preparation tasks.

## Completed preparation and remaining boundaries

The completed audit verified **2,712 compact upgrades**, no checksum/grid/cue
failures, and **4,679,089,073 bytes reclaimed**: **2,795 compact / 129 older V5**
records. The final eligible AAC record upgraded automatically at 02:14:25;
subsequent scans queued zero tasks. Remaining old records are offloaded or lack
complete imports; do not claim every cache converted. Evidence:
`build/rapid-load-review/final-audit.json`. All 89 observed cloud placeholders
stayed offloaded through that installation. Cold cloud-only playback still waits
for the provider; Sonny confirmed the observed delay was SeaDrive hydration.

Rapid-load foreground evidence: 72 requests across 24 local tracks, maximum
synchronous open 6.9 ms and maximum 5-ms UI heartbeat gap 45.4 ms. Earlier
non-foreground runs are not comparable. This is a measured workload, not a
universal latency guarantee. No further UI hesitation is currently reported.

Exact rekordbox/3BAND pixel calibration, five-probe profile approximation,
fallback/codec equivalence and wider physical-panel workloads remain limits.
Device lifecycle/controller extraction, further physical sync/reconnect/eject
acceptance and public distribution clearance remain deferred in ROADMAP; none
is newly certified by this player release.

## Rollback and safety

Use the existing rollback directory:
`/private/tmp/soundbox-lifecycle-rollback.18e3k4di/`.
`before-playback-load-oct9.app` is the immediately preceding installed app;
older rollback/evidence remains untouched. Current install record:
`playback-load-oct9-installed-build.json`. Original state snapshots:
`knots-playback-load-oct9-prequit/`, `-postquit/`, `-progress/`.
Installation verified graceful process exit before move + ditto; AppData was not
replaced. AppleScript may report “User canceled” despite actual exit; verify the
process before moving a bundle. Documentation-only wrap-up needed no reinstall.

Preserve generation-58 recovery **20260926-152340-47477cfa4a49** and offline capture
`soundbox-ipod-ref/offline-lifecycle/20260926-163945/` for separately reviewed
hardware acceptance. No source audio, rekordbox/ANLZ, device payload or `/PIONEER`
was written. Automated render/UI harnesses suppress live device discovery and
use synthetic or frozen offline fixtures.
