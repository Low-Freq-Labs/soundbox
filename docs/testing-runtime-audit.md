# Test runtime audit — November 2, 2026

The November4–5 responsiveness gate passes all2795 cases in526.735s across its
test phases (2729 offscreen457.657s +66 Cocoa69.078s), zero skips/expected failures
and complete inventories. The interrupted inventory attempt and stale lazy-Settings
assertion failure remain distinct from the final pass; the corrected readiness
assertion also passes its457-case UI replay. Encryption, recovery matrices,
partitioning and thresholds remain unchanged. November5 documentation publication
reuses this gate only with all693 frozen runtime/test/build inputs exact; it does
not rerun or restamp the installed app for prose changes.

The preceding November4 whole-app audit retains the first2712-case offscreen attempt
with two ordered cue-style errors; its native phase did not run. The isolated
187-case prefix reproduces the error and passes after removing redundant manual
repolish; ten cue states remain pixel-exact. The second gate passes all2713offscreen cases in460.325s, with all three
partitions complete and zero skips/expected failures. After Sonny unlocked, fresh console/owned-process/source proof resumed the
same shell. Main render and all66Cocoa cases pass in68.917s. The complete2779-case
gate takes529.242s across its test phases, excluding the explicit display hold. Both signed profiles/code parity and installed isolated/normal-startup checks
pass; current delivery and preservation details are in HANDOFF.

This is the dated November2 runtime investigation and its implementation
checkpoints. The current discovered gate and installed provenance are in
[test-release-gate.md](test-release-gate.md) and [HANDOFF.md](HANDOFF.md); the
November3 final2531-case gate took686.389s across its test phases. The November4
2564-case delivery took641.675s across its accepted offscreen/Cocoa phases
(574.225s +67.450s); its low-battery cadence failure and unchanged-source AC rerun
are recorded separately in HANDOFF. Neither is the older22-minute baseline below.
The later November4 build163/source `cf2ef5b` gate passed2595cases in646.875s
across its test phases (579.369s offscreen +67.506s Cocoa), with zero skips or
expected failures. These remain dated release measurements, not fixed inventories
or proof of physical-device/firmware performance.

The later November4 iPod gate passes2695cases in635.237s across its accepted
test phases (567.523s offscreen +67.714s Cocoa), with complete inventories and
zero skips/expected failures. Its interrupted late-review attempt and one
failed test-host fixture remain separately retained; HANDOFF records the final
package/install and hardware limits.

At the audited baseline, growing encrypted transaction and crash-recovery
coverage explained most of the roughly22-minute runtime. Running independent offscreen modules in
three isolated processes reduces that cost without dropping cases, weakening
encryption or shortening recovery matrices. Cocoa drawable/playback tests remain
serial and run after every offscreen worker finishes.

At the November2 investigation, the preceding focused test audit was the
October1 waveform review. The
complete, zero-skip release gate was introduced October 2 (`72d06f7`); subsequent
delivery notes recorded complete passes and feature-specific regressions. This
review additionally measured every offscreen case and inspected suite isolation,
fixture costs and overlapping recovery coverage.

## Measured baseline

These are dated measurements, not an evergreen inventory count. The current
inventory is discovered afresh by `check.sh`.

| Measurement | Result |
| --- | ---: |
| Early November2 build154 gate, source42f22c… | 1,990 offscreen + 60 Cocoa cases |
| That offscreen phase | 1,242.093 s |
| That Cocoa phase | 63.527 s |
| Later timing audit, all 1,994 offscreen cases executed | 1,248.057 s |
| Timing-audit discovery | 1.471 s |
| Median audited case | 3.565 ms |
| Audited cases below 100 ms | 1,520 |
| Audited cases of at least one second | 241 |
| Eight slowest audited cases combined | 291.322 s |

The later timing audit had one assertion failure after a source-acquisition error
message changed. Its expectation was corrected and its targeted replay passed.
It provides complete timing evidence, **not** a passing release result.

A subsequent complete November2 integration gate passed all **2,105 cases** in
**538.817 seconds across its test phases**, with zero skips or expected failures.
That measured run includes the parallel runner and the performance fixes. The
user then changed Artists to a SoundBox-only virtual view. That intermediate
virtual-only update passed its own complete gate: 2107 cases, 2046 offscreen in
488.520s plus 61 Cocoa in 66.084s, 554.604s across test phases, zero skips or expected
failures. Native per-artist creation and its stopped migration workload are not
part of the release design.
Keep the general accepted playlist transaction/recovery coverage; tests must prove
that normal artist browsing and metadata changes enqueue no native artist work.
The pre-wrap evening November2 delivery (sourcebacf1b…) passed **2144 cases**: 2080 offscreen in
481.233s plus 64 Cocoa in 67.834s, **549.067s across test phases**, with zero skips or
expected failures. It includes the stable complete first-wave publication,
checksum-preserving spectral omission, exact signed-transfer lookup, grouped-draw
race regression and playlist-only icon choices. A separate morning gate failed
one obsolete sticky-error assertion; the corrected regression retained disk/state/
no-paint checks and proved a later gesture succeeds after storage recovery. That
failed run is retained as failure evidence, never substituted for a complete pass.
The wrap-up review additionally binds the cached waveform revision to the same
locked buffer snapshot. Its source6fa0ab… full gate passed all2144cases again:
2080offscreen480.603s plus64Cocoa67.725s,548.328s across test phases, zero skips or
expected failures. Exact installed provenance remains in [HANDOFF.md](HANDOFF.md).

Unittest case durations include `setUp`, `tearDown` and case cleanups. The
3.310-second difference between case durations and the phase duration covers
module/class fixtures and framework work; it does not measure all fixture cost.
New reports call this residual `outside_case_seconds`.

The costliest modules in that audit were:

| Module | Cases | Seconds |
| --- | ---: | ---: |
| `test_import_analysis_flow` | 25 | 104.135 |
| `test_initial_analysis_pilot` | 6 | 67.163 |
| `test_media_pilot` | 6 | 66.254 |
| `test_registration_pilot` | 10 | 61.718 |
| `test_component_analysis_pilot` | 9 | 59.961 |
| `test_source_playlists` | 20 | 59.734 |
| `test_rekordbox_grid_proof` | 10 | 53.193 |
| `test_cdj_integration` | 25 | 52.780 |

A profiled registration commit/replay case took 2.515 seconds inside unittest:
0.577 seconds in setup and 1.936 seconds in its test body. Its 31 encrypted
connections consumed 2.035 cumulative seconds; SQLCipher's `execute` consumed
1.844 seconds. Those figures overlap and must not be added together. Repeated
connection and transaction proofs dominate this representative case, rather than
test discovery or result printing.

## Coverage and isolation review

The slow source-domain suites overlap in recovery mechanics but exercise distinct
authorities, write allowlists, media/packet contracts and receipts. A playlist
recovery pass cannot replace registration, cue/grid, locator, removal or initial
analysis recovery tests. Process-exit, committed WAL, dirty-page spill, unknown
recovery and receipt-publication boundaries retain their existing coverage.

Test fixture databases, generated media, per-test caches and native shader
compiler outputs use unique temporary directories. Required iPod, CDJ and
waveform captures are read-only inputs copied before mutation. The fixed
`/Volumes` and real-home paths found in negative tests are either mocked or
rejected before access. UI tests use isolated MainWindow state or a patched
AppData root; connection tests provide fake services and forbid live credentials,
browser and network activity.

Each runner process now redirects Qt AppData, AppLocalData, Cache and AppConfig
to its own temporary root before test discovery. `HOME` stays unchanged. A
module's cases stay together in discovery order, preserving module/class fixture
lifetimes and avoiding concurrent access to its in-memory globals. Three workers
also bound concurrent encrypted work on the 16 GB release host. GPU arithmetic
tests still run offscreen; native Cocoa drawable/playback tests run afterward in
their own fresh process.

## Runner and gate changes

`scripts/test-module-costs.json` contains measured module scheduling hints.
`scripts/check_tests.py` balances modules across three workers by those costs.
New modules receive a default weight and are always included. `--workers 1`
retains the serial offscreen path for diagnosis.

Every worker discovers the complete inventory and validates its assigned IDs
against that inventory and its Qt phase. The parent requires each process to
report exactly its assignment, records its exit status, and reconciles disjoint
partitions into one offscreen report. The final verifier then reconciles that
aggregate with Cocoa. Missing, changed, duplicate, unrun, skipped, expected-failure
or failed cases reject the release. Previous report files are removed before a
new run can fail, so a crash cannot leave a reusable old green result.

Passing-test output stays buffered; per-worker logs, individual case timings,
discovery times, worker wall times and the aggregate remain under `build/check/`.
The runner's own regression launches three real subprocesses, proves they overlap
while owning separate Qt state, then repeats with a failing case at the same
report paths and requires the old green result to be replaced by failure.

## Controlled performance comparison

An immutable temporary source copy ran the same 39 cases from
`test_registration_store`, `test_playlist_transaction` and
`test_rekordbox_grid_store`, with the same cipher settings and recovery paths:

| Execution | Wall time | Result |
| --- | ---: | --- |
| Serial runner | 100.457 s | All 39 passed |
| Three isolated workers | 36.617 s | All 39 passed |

That is a measured 2.743× speedup and 63.5% lower wall time for this representative
encrypted subset. Worker case counts were 17, 10 and 12; all returned zero with no
skips or expected failures. The source copy was removed afterward. Full private
timings, logs, source hashes and assignment plans are retained with the delivery
evidence. This benchmark is not a whole-suite release pass, nor a promise that
every machine/workload achieves the same speedup.

The final current-source gate and installed-build provenance belong in
[HANDOFF.md](HANDOFF.md). The normal complete gate remains
[`./check.sh`](../check.sh); required fixtures and numerical/native limits are
documented in [test-release-gate.md](test-release-gate.md).
