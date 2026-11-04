# Project layout and retention


Reviewed November4,2026. This describes the checkout and retained evidence,
not the installed app's live library or waveform caches. Current app/source
provenance and exact rollback location are in [HANDOFF](HANDOFF.md).

The subsequent [UI polish review](ui-polish-audit.md) adds a179-file source
inventory, frozen runtime/test hashes, generated presentation regressions and
Cocoa renders under `~/Library/Logs/Soundbox/ui-polish-2026-11-03/`.
Its source/package/install status is separate from the dated retention and
documentation-only closure below; HANDOFF records the current state.

## Current retention — November4

Build163 (runtime42aefd35…) is installed and normally reopened. Its immediate
7bbe8577… rollback and the previously protected b0ccb1ab… base share one existing
private directory pending acceptance; exact paths are in HANDOFF. Current2595-test/
690-input package/install/state proofs and packaging-retirement.json are under
`~/Library/Logs/Soundbox/usb-simplification-2026-11-04/`.
The older generated fc696312…/d5cb4451… build157 apps were retired under standing
cleanup after Sonny used/positively accepted the prior build160 Info behavior.
Exact paths, hashes and retention resolution are in `older-rollbacks-retired.json`
under `~/Library/Logs/Soundbox/export-polish-2026-11-04/`. That earlier directory
retains its final2564-test reports,675-input freeze, subagent format review, native
battery/AC evidence, both package proofs and installation/state checks.
Redundant dist/COLLECT/PyInstaller output is retired only after verified install;
its exact record is `post-install-retirement.json`. Older November3 retirements
remain dated evidence, not current output/rollback inventories. Keep source,
venv, native/CDJ/check outputs, required fixtures, originals and native recovery.
No app-output cleanup authorizes removing music, source or acceptance material.

## Documentation consistency — November4

The follow-up covers all75tracked Markdown files:39current first-party guides,
plans/research notes,11archived records and25pinned upstream references. It reuses
the prior full-content review receipts for the38existing first-party documents,
checks their subsequent changes plus the additional UI-polish audit, and scans the
complete corpus for current status, old/new behavior, permissions, retention and
local links. Current source claims are checked against release4a3d29a/runtime
7bbe8577…; this is a documentation audit, not a new hardware or dependency audit.

Corrections reconcile Export/multi-device selection and removed-member cleanup,
per-format unrelated-catalog preservation, build/source pointers, superseded
validation counts, source-analysis/format postconditions, and the installed versus
published source distinction. Dated native-reference results and historical build
counts remain history rather than being rewritten as new proof. The roadmap and
ideas continue to distinguish delivered work, accepted source domains and open
hardware/codec/distribution gates.

All648local Markdown links were checked, including18anchor links. There are no
missing first-party/archive targets or anchors. The58remaining missing targets
are inside pinned upstream documentation and point outside the vendored subset;
all243pinned crate files remain exact. Those references are retained verbatim,
not silently repaired or treated as SoundBox runtime instructions. External URL
availability was not exhaustively revalidated. Per-file before/after hashes,
review basis, findings and link records are retained under
`~/Library/Logs/Soundbox/documentation-consistency-2026-11-04/`.

Runtime source, installed executable, AppData, device contents and the running
session are unchanged. No rebuild, reinstall, playback input or acceptance pilot
belongs to this documentation-only follow-up.

## Documentation consistency — November3

The repository-wide pass inventories74 tracked Markdown files:38 current
first-party guides/plans/research notes,11 archived records and25 pinned upstream
references. Current first-party files receive full-content review with code checks
for suspect claims. Archives retain dated evidence, with historical-scope, retained-
path and link checks; their original pending statements are not today's TODOs.
Vendored references retain upstream bytes and describe the pinned source subset,
not SoundBox's current feature/acceptance status. Links into unvendored upstream
material may not resolve locally. No upstream semantic audit is implied.
Per-file scope, findings, hashes and link results are retained under
`~/Library/Logs/Soundbox/correctness-efficiency-2026-11-03/documentation-consistency/`.
The pass corrects44documents. Project-local Markdown path/anchor checks pass;
58vendor-document links target omitted upstream material, with all243pinned
crate files verified unchanged. The installed binary is unchanged by this
documentation-only correction.

## Active checkout

| Location | Purpose |
| --- | --- |
| `soundbox/`, `desktop/`, `native/`, `soundbox_personal/` | Application source |
| `tests/`, `scripts/`, `research/` | Regression tests, maintained developer tools and scoped CDJ/native-library proofs |
| `assets/`, `profiles/` and root build/config files | Product assets and reproducible build inputs |
| `docs/` | Current behavior, handoff, roadmap, exploratory ideas and open audits |
| `docs/archive/` | Clearly dated completed audits and superseded experiments; not current instructions |
| `.venv/` | Active pinned development environment; installed SoundBox does not depend on it |
| `build/native/`, `build/check/` | Current development native library and latest release reports |
| `build/rekordbox-write-proof/` | Small private copy-only prep-write evidence; no live-write authority |
| AppData `rekordbox_recovery/` | Private native-reference evidence, encrypted baselines and shared Tag/Cue/Grid receipts; preserve unresolved recovery |
| AppData `rekordbox-*-authority.json` | Ten independent accepted domains: tags, cues, grid, playlists, registration, initial WAV analysis, separate CBR MP3 analysis, removal, media conversion and media locator. Manual BPM/reordering have additional gates; source selection never grants permission |
| `soundbox-ipod-ref/`, `soundbox-waveform-ref/` | Private read-only regression fixtures and recovery/research captures |
| `soundbox-cdj-ref/` | Private read-only CDJ research fixtures |

The three reference directories are intentionally retained, not disposable build
output. The release gate requires the offline iPod and frozen waveform fixtures.
Do not remove or replace them to make tests pass. Native source runs also need
`build/native/`; `./build_native.sh` can regenerate it.

CDJ work is already here: tracked codecs/field mapping live in
[`research/cdj_export/`](../research/cdj_export/README.md); the private
`soundbox-cdj-ref/` root retains `ref-usb-empty`, `ref-usb-full`, `stage-A-clone`
and the rejected `stage-B-scratch` candidate. The [CDJ plan](cdj-usb-export.md)
separates retained experiments from hardware acceptance still needed. These are
historical candidates, separate from the installed pilot under `native/cdj` and
`soundbox/cdj_*`; neither they nor frozen fixtures are disposable packaging output.

`./build_app.sh` recreates packaging work under `build/` and output under `dist/`.
Python, mypy and Hypothesis also recreate their ignored caches during development.
Ignored does not automatically mean safe to delete: diagnostic evidence may live
under `build/` until deliberately archived.

## Historical accepted retention — November2

The full wrap-up verified the final personal build154, exact signed Contents,
native startup and current curation before retiring seven known obsolete
SoundBox rollback bundles. The duplicate `dist/personal` distribution and obsolete
`build/personal` PyInstaller intermediates were also retired. Their exact owner,
profile, executable hashes and signatures were checked before removal; no app
rollback remains in the retired temporary root.

Removed16,535generated files (2,407,736,190logical bytes);
observed filesystem availability increased2,471,178,240bytes.
APFS sharing and unrelated activity can change available space independently.
The installed app, active checkout/venv, `build/native`, `build/check`, checked
CDJ release binary, all source/device/waveform reference roots, live AppData,
native recovery and exact conversion originals remain intact. No music or Trash
cleanup was performed. Small preflight/retirement/provenance/state receipts:
`~/Library/Logs/Soundbox/wrap-2026-11-02/`.

`./build_app.sh` recreates the retired packaging output when needed. The existing
installed binary remains valid without those duplicates. Exact installed/repository
provenance and final preservation facts are in [HANDOFF](HANDOFF.md).

## Cleanup — October31

Sonny explicitly requested reducing accumulated diagnostics, backup copies and
extra app/packaging storage. Removed22,117 generated files, all five obsolete app
bundles, duplicate dist/packaging, compiler/Python caches and loose scratch files.
At that October31 checkpoint the installed verified app remained and no rollback
bundle remained. Current retention is recorded in the November4 section and HANDOFF. Build retains
about18MiB of checked native/CDJ/analysis binaries, release reports and small proof
records. Source, unfinished research and regression tests are retained.

Seven additional encrypted captures now share APFS blocks independently;203 were
byte-verified before the separate retirement below.25 diagnostic files received
transparent filesystem compression, preserving readable bytes and paths. Current
recovery/acceptance archives remain complete; pending journals/runtime baselines
were excluded and the shared source lock held with native apps closed.

41 redundant full master.db checkpoints from the completed playlist-management
and playlist-analysis-ui diagnostic runs were retired explicitly. Their comparison
records, metadata and exact hash/retirement receipts remain; old raw replays at
those paths are unavailable. Prepared-plan payloads, native sidecars, active shared-
library references and required fixture roots remain intact. Logs are about3.6GiB,
reduced from6.7GiB; about6.8GiB of actual filesystem free space was reclaimed overall.
APFS shared-block directory sizes must not be summed as unique physical storage.
Receipts: `~/Library/Logs/Soundbox/cleanup-2026-10-31/`.

At the October31 cleanup snapshot,87 imports/1105 titles/490 artist overrides/
520hidden/4043canonical art files were preserved through cleanup/restart. Keep future diagnostics
bounded: reuse snapshots for inspection, retain full DB pairs only for required
acceptance/recovery, and retire generated packaging/scratch output after verified
installation. Current exact state/build/retention is in HANDOFF.

## Garbage collection — October30

Sonny explicitly requested reclaiming test/build space, thinning rekordbox backup
storage and bloated logs. This pass reclaimed8,635,621,376 available bytes
(~8.04GiB), with~29.97GiB free afterward. Exact inventories, hashes and completion
receipt: `~/Library/Logs/Soundbox/garbage-collection-2026-10-30/`.

95 immutable encrypted captures now use independent APFS clones with only changed
pages separately allocated. All164 inventoried captures retain their exact
bytes/paths (10.48GB logical); native acceptance archives and reference captures
were not deleted. Runtime baseline.db and unresolved journals were excluded, with
both apps closed and the shared writer lock held. All16 source archive receipts
and available/explicitly-retired payload hashes were checked afterward. Apparent
folder sizes still count shared extents repeatedly; they do not measure reclaimed
space. The complete independent recovery set must not be mistaken for duplicates
to delete just because its logical size still looks large.

Removed five superseded app bundles: analysis-rollbacks138/140/141/142 and the
older info-layout138 bundle. At that cleanup checkpoint, installed147 and
fallback146 remained; the subsequent release retention is recorded in HANDOFF. Rust debug/
dependency/build output and mypy cache were removed; build now retains16MiB of
native dylib, checked CDJ release binary, current check reports and copy-only
proof. Re-running the full gate regenerates its disposable compiler output.
The runtime log retains its latest2MiB; the complete previous21MiB is verified
in previous-runtime.log.gz at the cleanup root.47 large diagnostic JSON files
are transparently compressed with unchanged logical contents and paths.

Actual source DB/XML, library.json,4041 art/review files,3282 waveform records,
4 authorities, installed app and all required fixture manifests remain unchanged.
No runtime source changed during cleanup. The subsequent analysis/native-playlist
release passed its unlocked gate, installed/restarted build148 and reverified
curation. Post-install cleanup retains only fallback147 and the16MiB checked
binaries/reports;146 and regenerated packaging/compiler output are retired.
Final restart and retention facts are recorded in HANDOFF.

## Historical UI/waveform evidence — October30

The October29/30 alignment, choices and Graveyard release has fresh private QA
at `~/Library/Logs/Soundbox/choices-graveyard-waveform-2026-10-29/`: frozen
before/after phase frames, metrics, Cocoa controls, test logs and safe installation
records. It does not revive the deleted development archive. Protected source,
iPod and CDJ references remain separate. Keep the existing single rollback root
as reduced by the garbage collection above; exact installed/build/retention facts are in HANDOFF.
This follow-up retired `build/personal`, duplicate `dist/personal`, Rust debug/
dependency caches, mypy cache and the current arm64 PyInstaller binary cache after
verified installation. These are reproducible output, separate from retained
regression fixtures; the native library, checked CDJ binary and release reports
remain. Exact paths/free-space proof are in generated-retirement.json under the
October30 evidence root below.

The October30 live-theme/Graveyard follow-up evidence is under
`~/Library/Logs/Soundbox/live-theme-graveyard-2026-10-30/`: native live-picker
captures/timings, sidebar renders, complete release reports and state/install
receipts. The source playlist pilot's exact plan, approval and native-open/closed
acceptance stay in `~/Library/Logs/Soundbox/playlist-source-pilot-2026-10-29/`;
its encrypted paired recovery remains under AppData, separate from build output.
Reproducible Rust debug/dependency caches and duplicate PyInstaller output may
be retired after the installed binary and release proofs are verified. Retain
`build/native`, `build/check`, the checked `build/cdj/release/soundbox-cdj` binary,
all source/native/device fixtures, source recovery and the current rollback root.

## Historical retention — October28 efficiency/player release

Sonny requested wrap-up of the efficiency/player/browser changes and manual-BPM fix.
The final wrap-up retires superseded rollback apps and duplicate dist/personal
packaging, retaining the installed app, active checkout, build/native, build/check,
and small QA/install/state records. Protected native-source references, encrypted
recovery, frozen waveforms and iPod/CDJ fixtures stay intact. Exact current
provenance, validation and cleanup receipts are in [HANDOFF](HANDOFF.md).

## Final directory audit — October 28

At the earlier October28 directory audit, application source was unchanged.
The then-later CDJ/browser pilot has since been installed; HANDOFF records current status. Git integrity,
tracked/untracked inventory, branch synchronization, dependency health, required fixture manifests,
and the existing release-test report inventories were rechecked. No stray app
bundles, nested repositories, broken links or conflict markers were found outside
the protected dependency/reference roots; no tracked file exceeds 2 MiB. The
`.venv` and three reference roots are intentional local dependencies/fixtures,
remain excluded from Git, and are not duplicate installed applications.

The initial audit moved 2,315 generated investigation files outside the checkout
and verified them against exact hashes. Sonny subsequently closed waveform
comparison work for now and authorized deletion of the comparison archive and
confirmed disposable build/test leftovers. The former
`~/Library/Logs/Soundbox/development-archive-2026-10-28/` is **deleted**; its old
`build/<investigation>` paths and scratch harnesses no longer resolve. They are
historical references, not recoverable inputs or current build commands.

Before deletion, 27 small release/installation records and copied library-state
snapshots were retained and verified at:

`~/Library/Logs/Soundbox/retained-release-records-2026-10-28/`

They retain their original subpaths, listed in `retained-files.json`. These are
text/state records, not the removed raw frames, movies, images, scratch programs
or experimental binaries. All current release/native-acceptance receipts remain
in their separate original Logs/AppData locations. Exact deletion inventory,
authorization and preserved-record hashes are in
`~/Library/Logs/Soundbox/checkout-hygiene-2026-10-28/approved-deletion.json` and the
earlier audit inventories beside it.

Deletion removed 863,591,084 logical bytes; 1,166,015 bytes of records were kept,
for a net 862,425,069 logical bytes removed. This includes 18 abandoned synthetic
DB/UI test workspaces and an empty retired install directory. Experimental build
folders were part of the archive and counted once. Filesystem available-space
changes may differ because of APFS sharing/snapshots. The temporary
`soundbox-source-staging` receipts were left alone.

At that October28 checkpoint, only `build/native/`, `build/check/` and
`build/rekordbox-write-proof/` remained in active build output (about3MiB allocated). Generated caches and duplicate app/
packaging copies previously moved to Trash are now absent; Trash was already
empty when this deletion pass began. Maintained developer harnesses remain in
`scripts/`, `tests/` and `research/`. No whole-Trash cleanup was performed.

The installed app, canonical music, live AppData, rekordbox data, native recovery,
`.venv` and all three regression-reference roots were untouched. The installed
signature/source fingerprint was reverified. No runtime/UI changes require
another build or restart. Further visual comparison work reopens only on a new
user request; closure does not assert perfect native appearance or cadence.

## Completed retention — October 26 (historical)

The full wrap-up installed personal build 129 from release commit 81c0d6c and
verified its real curated state. Only `/Applications/Soundbox.app` and the active
source checkout remain. The two temporary prior apps, duplicate dist app and
collection, and obsolete build/personal packaging intermediates were retired
using exact-path/provenance guards. `build/native`, QA evidence, small state
snapshots, offline fixtures and device recovery remain protected. Installation
and cleanup records: `~/Library/Logs/Soundbox/wrap-2026-10-26/`.
The subsequent documentation-only repository commit does not restamp the binary.
See [HANDOFF](HANDOFF.md) for exact installed/runtime and state provenance.

## Accepted cleanup — October 24 (historical)

Accepted releases retain only the installed `/Applications/Soundbox.app`. After build
126 passed release/native/render checks and Sonny accepted the UI, he requested
removing app/source rollback clutter and separately approved deleting the old
diagnostic archive. Cleanup removed the preceding build-122 rollback, 36 empty
historical `.app` shells, duplicate `dist/` app, `build/personal/` packaging
intermediates, and `~/Library/Logs/Soundbox/development-archive-2026-10-09/`.
The former `/private/tmp/soundbox-lifecycle-rollback.18e3k4di/` and the empty
sync-performance rollback directory were removed. They are no longer recovery
locations. No other installed SoundBox copy was found in the checked project,
Downloads/Desktop/Applications/Trash locations and Spotlight results.

Removed logical bytes: **4,549,925,705**; observed available space rose from about
10.03 to 14.61 billion bytes (APFS availability can change independently).
The final installed app was reverified before/after cleanup. Active checkout,
`.venv`, `build/native`, current `build/check` and `build/artwork-review-oct24`
validation evidence remain. Required `soundbox-ipod-ref`, `soundbox-waveform-ref`,
`soundbox-cdj-ref`, generation-58 recovery and all live AppData remain protected.

Small installation/provenance records and the preinstall/postquit library-state
snapshots live in `~/Library/Logs/Soundbox/install-evidence-2026-10-24/`.
Those are curation/review material, not duplicate app/source trees. The cleanup
inventory is recorded there and under `build/artwork-review-oct24/cleanup.json`.
Source revision history remains in Git; future accepted installs should not retain
old apps or dist copies indefinitely.

The October 26 full wrap-up includes the later Settings/connections/artwork
follow-up. Its installation, scoped app/packaging cleanup and final retained paths
are recorded in [HANDOFF](HANDOFF.md). Keep small provenance/state snapshots and
protected fixtures/recovery separately; accepted releases retain only the current
installed app and active source checkout.

## Historical October 9 cleanup (archive deleted October 24)

Obsolete root/public/personal packaged apps, PyInstaller intermediates and
Python/tool caches were moved reversibly to:

`~/.Trash/Soundbox-project-cleanup-2026-10-09/`

The first Trash batch was already absent by final verification; this cleanup did
not empty Trash. Regenerated validation caches were subsequently moved into its
`after-validation/` subdirectory. Do not depend on Trash for recovery evidence;
all retained diagnostic evidence uses the separate archive below.

Historical diagnostic scripts, captures, comparison libraries and audit reports
were moved with their original `build/` subpaths into:

`~/Library/Logs/Soundbox/development-archive-2026-10-09/`

That archive originally contained `moves.json` with exact source/destination paths,
file counts and logical sizes. Sonny explicitly approved deleting the entire archive
on October 24; those generated files and its manifest are no longer retained.
Old `build/<investigation>/...` references in dated docs formerly resolved
under that archive and now identify deleted historical evidence. They are not
current operational paths. Required regression fixtures remain in the checkout.
Latest gate reports remain
at `build/check/`. Moving files out of this directory does not itself reclaim
volume space; Trash can be emptied separately when desired.

Current player guidance is in [PLAYER](PLAYER.md); experiments formerly embedded
there are in [player history](archive/player-history-2026-07-to-09.md). Completed
reports are indexed in [the archive](archive/README.md). Exploratory concepts are
in [IDEAS](IDEAS.md), separate from the committed [ROADMAP](ROADMAP.md).

The October 9 cleanup left the then-installed app, live SoundBox AppData, source
music, rekordbox data, required fixtures and generation-58 recovery untouched.
The installed app was later updated to build 126 on October 24 as recorded above.
Current installed provenance and recovery location remain in [HANDOFF](HANDOFF.md).


October28 native CDJ pilot: `native/cdj/` contains the runtime adapter, pinned
vendored Rust source/provenance and notices. `build/cdj/` is disposable compilation
output; the documented isolated toolchain remains in `soundbox-cdj-ref/`.
`offline-integration-20261028/` below that private root is a required immutable
release fixture, pinned by `tests/fixtures/cdj-reference.json`. Never replace it
with live device/source reads. The standalone fixture exporter and native control
captures remain preserved; generated integration candidates are derived evidence.
Current release/install receipts live under `~/Library/Logs/Soundbox/`; see HANDOFF.
USB `.soundbox-cdj/transactions/` and matching Mac-side `cdj-recovery/` receipts
are recovery material, not disposable build/app rollbacks.

October29 UI/analysis/playlist checkpoint: private native source reference captures,
22 supplied UI screenshots, release/state receipts and unshipped prototypes are
retained in `~/Library/Logs/Soundbox/playlist-analysis-ui-2026-10-29/`. Its
`unfinished-prototypes/resume.patch` passed a read-only apply check against the
checkpoint; review it before reuse. No private DB/audio/authority is committed.
See [next-session scope](analysis-playlists-next.md) and HANDOFF.
