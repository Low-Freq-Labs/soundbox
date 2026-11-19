# SoundBox architecture

## Current source structure

Folder [guides](README.md) provide short entry points; dated sections below retain their original evidence.
`desktop.artists_browser` owns prepared artist rows and incremental tree updates, preserving navigation state.
`desktop.native_publication` recognizes own metadata snapshots that can reuse
prepared imports/services while fresh native cue/tag/smart state is published.
Media, source or inventory changes keep full loading; durable ACK precedes either route.
`desktop.state_commit` admits small immutable metadata deltas without filesystem
work on the GUI thread. Its ordered worker uses `StateRepository.commit_patch`
with the ordinary full validation, record guards, backups and atomic save. The
shared source lane remains reserved through joined delivery and the durable GUI
continuation; admission alone never acknowledges a source edit. Publication
merges only the committed leaves into current GUI owners, fencing repository
replacement/restore and preserving unrelated navigation preferences.
Ordinary UI preferences use the same ordered lane with an explicit leaf
allowlist and latest-value coalescing. Native/controller boolean save APIs still
mean durable success; they never report success merely because work was queued.
`desktop.ipod_export_capture` uses an ordered read-only checkpoint on that lane
to freeze a durably saved selected generation while bulk metadata work is paused
or running. It does not drain unrelated source queues or write a new canonical
generation. Exact selected values and absence are verified on the worker;
nonnegative integer indices are read-only assertions, never array mutation paths.
Source/repository/restore/mount changes refuse the request, while later ordinary
edits leave accepted output frozen for later Sync. Selected conversion, locator,
registration, structural and local-admission fences remain. Generic USB keeps
its separate native CDJ source-save barrier. Current delivery is in HANDOFF.
`export_snapshot` normalizes only typed playlist-exclusion memberships to sorted
JSON lists and restores private sets; playlist order/repeats and the complete
source-overlap boundary survive. Other malformed musical values still fail.
Legacy stock shadow-only requests without source-bound correspondence require
explicit playlist selection. Rockbox schema-2 Sync resolves its existing managed
keys against the current source and pins exact raw manifest bytes or absence;
it grants no historical source authority. If no managed key remains current,
the initial safe route asks for explicit selection rather than guessing deletion.
My Tags verifies all matching native aliases on the existing readback worker and
consumes that exact private Library/request proof once. Queue retirement and
smart-playlist recency share one ordered durable ACK; presentation waits for it.
Current source, repository epoch, queued request and affected order owners are
rechecked before admission/publication. A durable commit whose GUI owner changed
retains newer RAM and reports the publication conflict for explicit receipt Retry.
Initial intention admission, Retry and Discard still use durable boolean saves.
Sparse canonical and GUI patches share `apply_state_patch`, copying each affected
dictionary ancestor once rather than once per selected song. Whole recency-state
validation remains bounded by100,000ranks; it is a separately measured residual.
`soundbox.worker_process` owns eagerly prepared Qt-free cache/read helpers.
`cache_process` performs the existing snapshot encoders and display fingerprint;
`native_reader_process` runs unchanged fresh native snapshots. Dedicated inherited
pipes carry versioned primitive Library values; no Qt/ORM instance or executable
callable crosses the boundary. Source writers, recovery, exact authorities and
durable acknowledgement remain on the existing desktop source lane. Helpers
remain owned until actual joined exit; failed reads retain their intention and
existing Retry joins/replaces the failed actor before requesting a fresh read.
Frozen helper startup runs `packaged_worker_hook` before PyInstaller's Qt hook:
the exact worker flag skips only embedded Qt configuration, preventing its
QtCore import. Ordinary desktop launches keep the original hook unchanged.
Artifact inspection verifies this script's executable content and order before
Qt and the desktop entrypoint; the engine's Qt-free guard remains mandatory.
`desktop.inline_drafts` binds modified editor text to source/song/field and the
visible occurrence. Compatible refresh keeps typed text/caret/selection; reset
can reattach only to the exact current target without overriding newer input or
focus. An obsolete target keeps copyable text in the Unsaved edits menu rather
than saving it onto a replacement song.
Browser menu actions capture detached source/song identities; Edit also retains
its visible duplicate occurrence and resolves a fresh index after a nested menu.
Artists right-click uses the clicked row/group unless it belongs to the existing
multi-selection. Collection hide rechecks its exact reviewed songs after Yes,
saves hidden/date owners once, then refreshes/cleans analysis only after success;
failed storage restores the prior owners and reports no removal.
Background source publication also preserves the visible device surface: hidden
desktop render/fallback paths cannot select Collection over an iPod/USB page or
a newer navigation gesture. Intentional source changes, actual disconnects and
deleting the currently viewed playlist retain their explicit fallback behavior.
Metadata ACK curation uses immutable native-ID/UUID occurrences prepared during
the existing source-worker pass. They belong to that exact unpublished Library
and source, are consumed before publication, and retain all current logical pin,
unbound and playlist-rule checks. Ordinary source selection still verifies the
complete library; this one-handoff index is not a published-library cache.
Native sidebar reconciliation releases its local recursive helper cells in
`finally`. Otherwise their cycles keep borrowed Qt roots and old Library values
alive until cyclic GC, after their C++ owners may already have been destroyed.
`soundbox.mutation_domain` observes normal nested dict/list mutations with stable
revisions; validated worker snapshots and canonical selected-value proofs fence
metadata plan admission. GUI turns check revisions and small progress fields,
while native workers still validate complete detached plans against saved state.
These volatile proofs never grant native authority or replace source guards.
Compact slotted revision nodes and lazy weak parents retain mutation/lifetime
semantics without a WeakSet allocation for every JSON container. No global GC
thresholds or disabling are part of this design. Deliberate unbound builtin
mutators bypass subclass dispatch and are outside the supported container API.
An observed owner's immutable detached snapshot is reusable only for its exact
recursive revision. Normal mutators refuse on every frozen descendant; deepcopy
shares those read-only leaves while mutable progress still detaches. Each source
worker still validates the full plan and compares it to fresh canonical state;
snapshot reuse removes allocation, not verification. The original plain snapshot
API continues to return a fresh mutable copy.
Canonical selected-value comparison keeps bounded positive witnesses only for
the same exact Frozen JSON root and unexposed copy-on-write saved leaf. Mutable
inputs, replaced leaves and reload/restore cannot borrow a witness. Every call
still checks the current manifest, all referenced record epochs and final
revision/generation ownership. The controller similarly certifies an immutable
plan only after fresh canonical verification: exact frozen targets/order/scope,
typed static scalars and repository/source context must match. Each phase still
validates current cursor/active operations and fresh canonical state; direct
public plan validation always performs the full structural and progress checks.
Prepared browser publication compares detached scalar/provenance inputs, reuses
unchanged sidebar trees, and updates unique table rows with dataChanged when
filter/sort membership and order stay exact. Changed order or duplicate
occurrences retain the complete model reset. Disposable fingerprints serialize
in bounded chunks on the cache helper with the same original JSON hash.
Background metadata pause is a durable exact-source scheduling choice, separate
from accepted native permissions. Already admitted work drains; the frozen
backlog stays intact, manual accepted edits remain available, and export reports
the pause only where its source-save contract requires that generation to drain.
New stock, dual and Rockbox iPod exports use the selected checkpoint above;
generic USB retains its source-save barrier. Pause is not a new-acceptance refusal.
Routine metadata/canonical saves stay quiet in the footer; prepared progress,
Pause/Resume and retry live in PLAYLISTS controls. Foreground work and actionable
errors retain truthful status without relaxing persistence or Quit interlocks.
`soundbox.desktop_runtime` warms ordinary playback/analysis dependencies before interaction without preparing songs.
Admitted personal search modules warm through `providers`; device pages/backends remain on demand.
`native_transaction` supplies shared source admission/locking/connections; each writer retains its authority and recovery protocol.
`cache_io` owns disposable cache publication, `ipod_policy` shares hardware pacing, and `waveform_sections` owns the pure byte-preserving codec.
`device_staging` supplies shared descriptor-pinned device publication; callers retain mount/source/firmware authority and commit ordering.
The native deck includes unchanged Metal source from `native/WaveformShader.h`; test oracles share a source loader in `tests/support`.
`scripts/check_layout.py` checks short folder guides, links, instruction parity and static dependency directions.
Source changes do not imply installation; [HANDOFF](HANDOFF.md) records validation and installed identity separately.

## Responsiveness delivery — November4–5

The new source prepares every Settings tab during startup, following Sonny's
explicit rejection of first-click construction. This supersedes the former
Settings-on-demand policy in the dated delivery below. USB/iPod pages and export
backends remain demand-loaded. The window owns initial connected-account refresh;
its prepared Settings section attaches to that same worker/result and does not
start a second automatic refresh. Track Info retains complete selected artwork
before its first visible frame. That delivery was build167; HANDOFF records the
current installed build and its true signed source base.

Browser-only manual-grid projection validates but does not copy beat arrays;
playback and edit APIs retain their musical-data ownership. Analysis completion
keeps the previous published prep queue for rollback and replaces only the
selected job. State no-op comparisons check changed scalars before nested grids,
with unchanged exact JSON semantics and atomic publication. Stock planning shares
the parsed final rendered bytes, and phase-local source observations never cross
acquisition/commit proof boundaries. USB-settings work freezes complete source
paths without copying unrelated songs. Exact-divisor waveform range reductions
reuse finer aligned cells with byte-identical signed extrema. Full measurements
and remaining storage/queue/snapshot work are in the
[current audit](performance-audit.md#bottleneck-and-responsiveness-follow-up--november4).

## Whole-app demand and lifecycle audit — November4

This audit was delivered in personal build165/runtime7ffde716… before build167;
its2779 tests, both signed profiles/code parity and installed isolated/normal
startup checks pass.
The matched signed snapshot-browser median is 1.20s; fresh source/device readiness
remains separate. HANDOFF records exact provenance/state/retention.

The [whole-app audit](performance-audit.md#whole-app-audit--november4-after-ipod-review)
keeps USB/iPod page construction behind an explicit device view and export
caches/backends behind device demand. Mount detection remains available. Empty
stable stack slots preserve existing page indices; passive theme/layout/state
barriers/shutdown inspect already-created pages. At this delivered checkpoint,
Settings built once on first open; the explicit follow-up above supersedes it.
Background connected-account
refresh belongs to a window TaskOwner, independent of Settings construction;
the shared provider's cached likes remain usable and the section attaches to its
active worker or completed safe result. Closing Settings does not destroy work.

Native-analysis/import notifications coalesce per GUI turn. Analysis retry uses
one owned single-shot timer; explicit admission and source guards stay fresh.
TrackModel skips identical filters/sorts while its materialized order is current;
inline edits invalidate search data and update repeated track memberships. Art
updates preserve the current view and mark an Art sort dirty for explicit reuse.
Title cleanup retains at most 1,024 compiled artist-edge pattern pairs with keys
up to 256 characters; oversized inputs keep the same transform without residency.
This pure-text reuse changes no display derivation or browser-cache schema.
The decoder uses a generation-bound wake condition while unloaded, EOF or paused
with a full ring. Control transitions advance/wake that condition; the audio
callback retains its existing lock-free notification and active decoding retains
its short timed buffer wait. No render/DSP/sample/waveform algorithm changed.

Canonical state no-op detection retains exact JSON types/map order and current
file identity. Graveyard history compares locked exact bytes. Grid publication
skips private staging only for exact current bytes, mode and mtime, after checking
any existing staging evidence. Device imports compare exact bytes and create new
destinations exclusively. Conversion recovery binds its one source hash pass to
before/opened/closed/after epochs, then rechecks that same identity after the
fresh source lease guard before replacement. Required recovery, source authority,
atomic saves and database/manifest-last publication remain. HANDOFF records the
tested and installed artifact rather than inferring it from this source review.

The delivered later November4 [iPod audit](performance-audit.md#ipod-deep-audit--november4-after-usb-testing)
adds operation-local native/manifest captures, retained stock source contracts,
shared Rockbox ownership, source/display-aware explicit filesystem Sync and
GUI retirement fixes. It uses offline fixtures only; HANDOFF has artifact status.
Interrupted adoption always freshly hashes its affected candidates, superseding
older persistent timestamp/edge-proof reuse. Source/codec/firmware boundaries remain.

## Installed USB audit follow-up — November4

The preceding build165 [USB corrections](performance-audit.md#usb-deep-audit--november4-later-testing)
give formatting its own retained QThread and physical-disk volume reservation.
The ordinary device coordinator continues to serialize export operations, while
another USB can export during confirmed native erase. Discovery and general source
work remain held; format completion retires its old mount identities and never
clears another export's state. Shutdown retains native erase through process exit.
Selected USB Info artwork uses the existing TaskOwner decoder with a bounded
descriptor-pinned byte capture. Browser membership IDs stay outside publication
DTOs/manifests. Catalog snapshots recheck exact bytes, all epochs and
presence. Actual FAT32/exFAT probes demonstrated equal epochs after changed bytes;
recovery/adoption therefore hashes affected bytes freshly. Redundant per-asset
completed-proof writes are removed; existing historical artifacts stay untouched.
That USB build165 was installed and verified before the later whole-app delivery
above. Its source was local/unpublished at that checkpoint. Current repository,
runtime and protected-retention identities belong in HANDOFF.

## Duplicate membership confirmation

`desktop.duplicate_dialog` presents the existing Skip/Add Anyway decision in a
compact dialog with a literal playlist name, wrapped read-only title list capped
at six plus the remaining count, and bounded scrolling for long labels. Skip is
the default and Escape/window close reject. Accepted duplicates still use the
same caller and existing membership writers; presentation confers no source scope.

## Browser BPM projection

WaveformFillWorker emits disposable BPM snapshots for completed zero-anchor
fallback caches, including preparation-inventory hits. Bounded header reads and
stable source stat checks stay on that worker; no audio read/hash or GUI file I/O
is added. MainWindow keeps snapshots only for the exact Library/track binding,
overlays them below native, detector, manual and pending-grid values, and updates
one model row with sorting/filtering/selection preserved. Missing/stale snapshots
clear the display estimate. The estimate flag supplies a BPM tooltip and is not
part of the persisted computed base-row cache, so its schema stays unchanged.
Neither snapshots nor fallback grids provision initial native-analysis authority.
Playback resolves valid manual drafts even when base detection has no beats.

## Repo layout

Current work and remaining gates: [ROADMAP](ROADMAP.md) and [HANDOFF](HANDOFF.md).
The [September software audit](software-audit-2026-09.md) and
[October efficiency audit](efficiency-audit-2026-10.md) retain dated baseline
findings and implementation checkpoints; their original candidates are not an
evergreen outstanding-work list.
The [November performance audit](performance-audit.md),
[cloud identity audit](cloud-library-audit.md), and
[test-runtime audit](testing-runtime-audit.md) record the subsequent measured
changes and remaining costs.
The October implementation follow-up batches artwork policy reads and source relationships,
constructs playlist-scoped Track rows, counts visible membership without sorting,
and preserves unchanged display inputs
across source refreshes. These caches never replace fresh source/edit authority.
Developer startup/player probes use disposable state and explicitly separate
snapshot usability, current-source readiness and physical-player acceptance.
`browsercache.json` is a separate versioned, checksummed display-row cache keyed
to all effective metadata/curation/title-policy inputs. Rows are restored only
after rebuilding the current registry and applying current visibility settings;
artwork and edit authority remain separate. Missing/malformed/stale caches fall
back to reconstruction. Row invalidation retires the checkpoint key, preventing
new rows from being saved under an old input fingerprint. Cache publication is
serialized with the existing background snapshot lane and uses bounded row
encoding. Metadata derivation changes must bump `browser_cache.SCHEMA`.
Saved theme application precedes child-widget construction. Static QSS is
installed once; theme_controller applies small visible accent overrides and
refreshes hidden controls on Show, without repolishing the whole widget tree. The global input filter attaches after startup
restoration; the native waveform host keeps its geometry filter throughout.
Artist title-rescue regexes compile on first actual rescue. Admitted personal
provider search modules prepare at composition before desktop interaction;
preparation performs no search, network request or credential access.
Provider initialization/search exceptions remain retryable, never sticky misses.
Stock artwork converters/writers import only on actual conversion/rendering;
unchanged derivative-cache warming does not initialize them. Ordinary desktop
construction warms NumPy, SciPy signal/image helpers, libsndfile and Zstandard
before interaction. CLI/module imports remain lightweight; code warmup performs
no audio reads, DSP, network requests or song-sized allocations.
Grid glyphs use the system font directly
rather than triggering generic-family alias discovery.
The [shared-core / personal-module boundary](release-boundaries.md) uses
`soundbox.providers` for typed artwork/artist interfaces and startup admission.
The versioned `profiles/*.json` manifests select an allowlisted first-party module;
missing/incompatible/broken providers leave the local core available. Public
contains no current SoundCloud provider or `yt_dlp`; personal includes
`soundbox_personal.discovery`. Menus, direct commands and delayed scheduling all
use the resolved actions. Providers return results; core workers and artwork/state
owners retain publication, cancellation, shutdown and device authority.
Frozen apps resolve only the bundled manifest, ignoring source-profile environment
settings. Detach/reattach uses build composition plus restart; it does not migrate
or reset curation, provider namespaces, images, sticky removals or analysis.

    soundbox/   pure-Python engine (no Qt): source-neutral library model,
                rekordbox adapters, sync/resync + manifest drift,
                playlists (m3u8), metadata
                derivation, imported-file records + relink, cover-art
                fetch/encode, backup — mypy-clean, gated by ./check.sh.
    desktop/    PySide6 app: MainWindow + mixins, shared worker adapters,
                trees, player, art picker, suggesters, artist/default catalogs.
    native/     arm64 macOS deck/process-guard dylibs and CDJ Rust helper;
                CoreAudio decode/render and CAMetalDisplayLink use the deck C ABI.
    tests/      engine behavior + property-based (hypothesis) + mocked live-DB
                reader + hermetic UI + native deck/shader contracts. Trust
                ./check.sh output for the live count.
    research/   R&D excluded from the shipped app and engine mypy. Pyflakes
                checks it; synthetic tests cover the ANLZ bridge contract.
                See research/cdj_export/.
    docs/       this documentation.

The CDJ exporter, introduced as the October28 pilot, integrates the pinned Rust
renderer under `native/cdj`.
`soundbox.cdj_source` captures fresh native metadata/cues and verifies source
playlist order; `cdj_usb` copies bounded native metadata/profile files for local
parsing; `cdj_pdb` independently checks DeviceSQL pages/indexes; `cdj_export`
prepares an immutable selection/candidate; `cdj_transaction` owns authorized
publication and locally bound recovery. Both formats share one generation.
`desktop.cdj` uses the existing device coordinator/worker lane. Ordinary exporters
retain their PIONEER exclusion. Full contract and acceptance limits:
[CDJ guide](cdj-usb-export.md).

Current native catalogs are shared authority across exporters; cdj_catalog adopts
fresh metadata and maps source-local tag IDs without auditing retained media.
cdj_incremental changes selected records/memberships and preserves unrelated
collection-only songs. Removed selected members are pruned only after counting
references in every playlist across both native catalogs; shared assets stay.
Each format preserves its own unrelated baseline even when the formats already
differ. cdj_transaction checksums while copying, resumes completed payloads, and
lets a later coherent native export supersede an older interrupted catalog intent.
USB/global My Settings use44 logical fields and byte-preserving selected patches;
no automatic global propagation. usb_format revalidates the reviewed physical USB
before FAT32/exFAT MBR formatting, reusing a correctly typed full-capacity sole
first partition only for same-filesystem quick erase. Other layouts or filesystem
changes repartition; post-format physical/map/filesystem checks remain.
Track.info and retained settings.track_info overlays feed display/export;
browsercache schema4 and libcache schema13 preserve these fields without
analysis/art rebuilds. In tested source, native Title/Artist edits use
`desktop.metadata_controller` and an independently admitted native store;
unsupported native Track Info fields refuse rather than report a local-only save.
Legacy/XML overlays remain supported migration/display inputs. The narrow
metadata profile is default-off and does not complete broader Track Info or
artwork persistence; see HANDOFF and the shared-library audit. The ordinary desktop playlist API
shares the accepted Tag/Cue/Grid lane and has separate explicit authority. The
historical standalone pilot under research has only its exact-plan approval,
shared recovery root/lock/journal and no general authority or app loader; see HANDOFF.

`desktop.track_menu` supplies the evenly spaced button immediately right of Q.
Beat-count mode is persisted separately from waveform color and uses cached
memory positions plus the resident grid in both Qt and native ABI6. The same
native RAM resolver drives drawable text and diagnostics; changes request one
dirty frame. Standalone CDJ inputs join the existing collection union without
manufacturing a playlist; local recovery receipts retain their source IDs/UUIDs.

`desktop.choices` paints menu checks in a right-hand gutter and uses QListView
combo popups with matching right checks. Qt retains keyboard/action/accessibility
semantics; a shared application-owned proxy disables native Cocoa combo menus
without owning QApplication's platform style. Closed combo arrows sit 14 points
from the right edge. Sidebar leaf icons use the branch column, aligning labels
with folders at every depth. Both trees inset content/branch glyphs six more
points while preserving full selected-row backgrounds and hit/drag geometry.
Graveyard double-click/Load Track uses the existing deck with no autoplay capture.
Its NoTypeAhead tree routes standard playback and cue keys, with Load bound to its
own selection. Existing cues play; empty-slot creation/deletion remains disabled
until restoration or loading an ordinary browser track. Hidden auditions prepare a private temporary
record through the normal codec, upload the data, and remove the temporary
record before worker retirement; the canonical cache remains absent.
Summary uses a large cover beside inline caption/value rows.

`desktop.graveyard` prepares one stable native DB/WAL snapshot and distinct-file
storage observations in its worker after source materialization and verification,
independently of tab visits. Opening the tab reuses that source-bound snapshot or
its truthful loading/error state; stale-source completions are discarded. It
never reads audio payloads for storage or info.
Source/library-bound historical native rows retain exact provenance outside the
active Collection; a numeric label alone never authorizes a file path. The i
button uses this archive for native-deleted selections. Totals deduplicate inode
aliases and distinguish unresolved paths from missing/unavailable files.

`desktop.removal` prepares a concrete review on the common desktop source lane.
The confirmed plan hash/selection persist before mutation and survive restart.
`soundbox.rekordbox_removal_store` has a separate library/schema/version authority,
paired encrypted DB/XML evidence, exact row/native-resource guards and OS Trash
disposal with a bounded interruption tag. It models native local-0 physical
removal/local-1 tombstones and suffix gap closure, preserving unrelated rows and
SoundBox curation/analysis. Other active native references keep their shared audio;
this is explicit in the review rather than broadening the selection. Recovery
uses SQLite’s owned-journal rollback before classifying source state, restores
uncommitted files/resources/XML, and independently confirms committed disposal.
Only the latest completed full DB pair remains; older exact payloads retire
resumably while row/XML/native-resource receipts stay. Unknown state blocks other
domains. No broad orphan sweep, permanent unlink or title-based identity lookup.
See `graveyard-delete-plan.md` for native reference and installed-state boundaries.

The same store also has a dormant native-only preparation policy. Selected scalar
source/library/ID/UUID/raw-path pins enter the existing durable review queue;
global native identity checks and DB/XML work run on the shared source worker.
Its SQL/XML plan is identical to accepted Collection removal, but it never opens,
stats, hashes, scans, tags or disposes of audio, analysis, artwork or Trash paths.
Committed readback and retained source-bound Graveyard history precede local
acknowledgement. Production admission remains disabled: no lossless native Restore
composition is accepted yet, and current local Collection hide/Restore stays intact.

`desktop.analysis` owns the Analysis tab and cancellable TaskOwner jobs.
Manual and automatic analysis use the same background queue, without a dialog
or progress bar. Each song retires/joins and saves independently before the next
starts; later imports join the queue. Failed songs do not discard earlier results
or prevent subsequent analysis. Completion updates just that browser row and the
loaded grid/cues, preserving transport, waveform geometry and the current view.
`soundbox.analysis_jobs` persists bound source/library/track/path/UUID intentions
before worker admission. Active work remains queued until its merged result and
retirement save atomically. Restart waits for a matching loaded library and uses
the captured preferences; ordinary detector failures persist for explicit retry. These pending
intents contain no completed musical data or native write authority. Browser
Analyze Track uses this same queue and persists a waveform-rebuild intention.
`desktop.analysis_priority` captures and publishes the selected waveform before
DSP, guarded by its verified source epoch before atomic cache publication.
Required compatibility conversion checkpoints precede that capture; later drops
may preflight conversion while DB writes retain their original FIFO. Waveform
SHA/epoch and BPM/key phase progress save durably. BPM-only processing uses a
SoundBox projection of the pinned tempo/downbeat math, with exact parity tests;
key runs next over the same SHA-pinned bytes. The original preferences remain
captured, manual drafts survive, and native publication cannot consume intermediate
BPM paired with an older key. Joined, durably acknowledged local phases offer
one eligible source admission before the next local phase. Offers rotate among
tag-assignment, cue, grid, prep, playlist, tag-definition, metadata, registration,
native-analysis, removal and media heads;
each retains its FIFO, per-song prerequisites and fresh source guards. General
ownership still protects refresh/export/shutdown. Only the synchronously selected
source opportunity may pass deferred cue/grid logical holds, while physical work,
prepared-prep/recovery reservations and foreground prerequisites remain guarded.
There is no elapsed-time deadline. Interactive Tags remain busy during active
DSP; an admitted single-flight assignment persists before presentation and
remains until exact native readback and durable acknowledgement. Failed songs
retain their intentions while other local jobs continue. Captured
cue gestures remain immediately usable while canonical and native saves wait;
their owned overlays do not claim durable acknowledgement before verification.
Normal Quit keeps the window and source pumps usable while captured saves finish;
teardown then drains accepted cue jobs before stopping their owner.
Automatic detection off admits waveform-only prerequisites for native imports,
without invoking either detector or creating fabricated musical results. Explicit
waveform-only repair continues through its existing cache repair route.
Its three inset groups align labels and 30-point control rows; the section header
and preservation note retain the outer edge.
Each `soundbox.analysis` component invocation captures a hashed local audio
snapshot, then the optimized Rust helper reuses pinned rbxport DSP. Waveform,
BPM, key and native-packet rendering retain independent captures bound to the
same SHA; no cross-phase PCM cache is shared. Decoding checks bounds before collection
and bounds the analysis head to thirty minutes/96million mono samples. Automatic
BPM/key detection skips songs over900seconds, including imports whose duration
is learned from the captured header; saved/native/manual musical data and explicit
analysis remain available. The remaining tail is streamed, with finite/frame/format checks, before extending the
last measured tempo. The default adaptive/high-precision pipeline follows tempo
changes and uses upstream downbeat/key rules without a genre selector. Legacy
constant-v1 results remain valid; partial/inconclusive components preserve their
prior data/provenance for the same bytes. Results are durable
`settings.analysis_results`, keyed by canonical path with SHA-256, exact source
identity and engine provenance, allowing a direct import and later native ID to
share data. Playback overlays detected fallback grids in its existing worker;
native/manual grids and source cues win. Captured imports can now retain a
revisioned manual `grid_draft` beside the untouched detector record. StateRepository
commits it before browser/native RAM grid changes; late workers merge the newest
draft and preserve its BPM as well as its rows. `soundbox.local_cues` retains
revisioned captured-import cue drafts. The ordinary cue-only controller adds an
immediate exact-track overlay before canonical ACK; direct legacy draft APIs
retain their save-before-presentation semantics.
Playback revalidates timestamp drift through SourceVerificationCache and an exact
captured digest before overlaying completed detection; current native/manual data
still wins. Local grid edits use the saved result instead of vetoing a click because
the loaded waveform delivered an older fallback grid. EOF translations clip
trailing rows beyond the captured duration only in the unregistered draft route;
native prep v2 retains terminal rows beyond EOF. Native publication uses the
separate accepted registration/initial-analysis/Cue/Grid domains described below.
The current whole-v4 caller adds a nearby-integer
prior only after three-section onset and attack checks; it records raw estimates
and preserves contrary fractional/variable evidence and legacy results.
Broader codecs and existing-native-analysis replacement remain separate
acceptance gates; see [the accepted stage](analysis-playlists-next.md).

Shared-library registration research is separate from runtime writers:
`research/native_library` constructs the observed bare WAV row and proves
encrypted original-TEMP-only transactions/recovery. Its pure component contract
compares pending base/current/intended values without app-origin ownership. Core
workspaces have no source publisher; separate generated-WAV one-plan registration
and initial-analysis source pilots require their own approvals, closed apps and
the shared edit lock/journal. Initial analysis binds the already accepted bare
track, native-share inode and a previously absent three-file directory; exact
before/after recovery preserves unknown DB/XML/file states. Registration approval
does not authorize analysis, and source journal mode is never normalized.
It has no production authority or app integration. The old native interval still has
explicit unrelated registry differences; the newer startup-complete bare-import
reference now matches the full native inventory/XML exactly. Neither proof provisions
registration/analysis/metadata authority. See its README and HANDOFF.
The October31 multi-format workspace uses a distinct original-TEMP marker and
adapter. `soundbox.media_probe` reads technical facts from captured containers
with bundled header parsers, rather than a development ffprobe executable.
A separate ten-generated-file native registration batch passed its exact-plan
approval, publication and native reopen/normal-quit acceptance. The general
`soundbox.rekordbox_registration_store` has a separate authority loader, insert-
only content/counter authorizer, exact-path reuse, paired encrypted recovery and
ordinary bounded retention. TEMP replay matches the complete actual native AFTER.
The direct-import continuation adds `desktop.shared_imports` and pure
`soundbox.shared_imports` intentions. File and imported-track gestures durably
bind source/library, destination UUID, order, preferences and request/receipt
stages. They prepare, register/reuse, save bindings, prepare membership and verify
source readback on the existing TagEditController source lane. SQL requests
persist before execution; retries retain committed identities and renew only
proven uncommitted requests. Cancellation reconciles committed registration
receipts before retiring intentions; native rows and verified bindings survive.
Fresh own membership commits rebase later app playlist requests and their XML
clock order, without rebasing over independent native edits. Pending membership is a detached presentation and
CDJ capture waits for the import FIFO. The exact two-generated-WAV source test
passed complete planned AFTER/idempotence and native reopen/normal-quit acceptance.
Sonny separately approved conditional ordinary registration, and its authority is
provisioned for the exact library/schema/version and bare-format intersection.
Sonny’s November1 explicit playlist-unification request separately authorizes
existing visible-import backfill through this accepted API. It grants no broader
native analysis/metadata publication;
HANDOFF records installed workflow validation and provenance.
The initial November1 delivery adds the explicit import-only source-audio exception in
`soundbox.wave_conversion`. Before registration preparation, the shared source
lane/lock verifies the accepted native catalog and excludes every registered path
and alias. Float32 RIFF WAVE becomes same-path PCM24, preserving rate/frame count,
ordered metadata chunks and original gain; Sonny selected clipped over-range
peaks after auditioning original/clipped/reduced copies. Exact originals and
before/after receipts live under AppData/wave-conversion-originals. A verified
sibling stage replaces only the unchanged original atomically. Recovery accepts
only exact before/after hashes; unknown occupants never get overwritten. Each
conversion is acknowledged durably before touching another song or preparing
registration. Latest saved grid/cue drafts and pending prep intents transfer
through that exact sample-time mapping, with original analysis identity retained
as provenance. No detector rerun or audio reload is required. Old waveform
publishers are fenced; new interactive cooks use disposable state during the
replacement, and the loaded detail refreshes afterward. Native SQL/analysis
format authorities and all export immutability rules remain unchanged.
The later whole-Collection request broadens the semantic audio scope;
`cdj_compatibility` supplies actual-header checks and verified PCM24 conversion,
canonical PCM-header normalization and resampling. The separate file-only
`media_conversion_store` and desktop controller now queue each file durably on
the common lane/lock/journal, retain exact originals and recover atomic WAV
replacement or unregistered lossless→same-folder WAV. Native WAV acceptance
passed; native cached technical columns stay stale, so epoch-bound actual header
facts refresh only technical display/USB DTOs. New imports convert before bare
registration and transfer current musical drafts/destination/IDs without
redetection. The file-only WAV domain preserves native SQL and musical data.
Existing codec/path changes use the separate `rekordbox_locator_store` authority
and column allowlist: existing track path/name/type/inode, timestamp and local
counters only. Two FLAC→WAV records passed explicit source publication and native
reopen/load/normal-quit acceptance, preserving complete target rows afterward,
all packets/XML and all non-registry tables. General locator authority now binds
that profile. The source worker finishes conversion plus locator publication
automatically before acknowledging current drafts and RAM paths; no manual
Relocate step. Each phase has exact encrypted/file recovery and durable retry.
The supported automatic pipeline is installed:124 WAV conversions completed with
exact originals retained. Timing-unproven, cloud-deferred and unavailable files
remain explicit; see [compatibility limits](cdj-media-compatibility.md).
Stable logical IDs project with `native_identity` while `Track.native_id` retains
the native SQL ID. `Track.native_path` preserves a raw native alias separately
from its canonical playback/cache path. `native_paths` captures/rechecks alias
inode/epoch proofs. Cache11 retains these fields; cue/grid/tag/CDJ adapters translate
to native SQL IDs while UI/cache/curation retain logical IDs. Independent existing
native-ID curation blocks activation until a reviewed merge. Import injection and
late metadata probes cannot overwrite projected native data. Bare registration
retains local musical drafts/detection until native analysis has its own acceptance.
Read-only identity projection can isolate unavailable bindings per song instead
of invalidating the full native source. `Library.source_binding_issues` is transient;
`imports.inject` excludes those removed bound songs from Collection while retaining
their durable records/drafts. Raw unmatched native identities remain unchanged.
Transaction projection is strict by default, and affected import jobs stop before
SQL. This is not automatic identity adoption, rebinding or musical-data migration.
Collection discovery now includes all active supported native audio, excluding
FileType19 placeholders regardless of path. Source DB journal mode is unchanged;
only an exactly verified private TEMP working copy may normalize its own WAL
before retiring recovery.

The playlist research path now has a byte-preserving XML codec and a marked-TEMP
paired transaction proof. XML is durable before SQL commit; complete recorded
DB/XML prefixes determine rollback or completion. Evidence/unknown-state checks
precede recovery writes. The separate source pilot uses the existing guarded SQL
transport with its own approved create/name/two-track intent, target-only live
reads, snapshot inventories and restrictive SQL authorizer. Both apps must be
closed. Its exact October30 publication subsequently passed Sonny's native
inspection and normal-save comparison; SQL commit alone never establishes that
acceptance. That historical receipt grants no further operations. The broader
ordinary-playlist API subsequently passed its separate October30 native/source
acceptance. Registration and initial WAV/CBR MP3 analysis now have independent accepted
production domains; broader analysis/metadata remain gated.

## Engine modules (`soundbox/`)

`library` (source-neutral `Track` / `Playlist` / `Library` model) ·
`rekordbox_db` (live DB adapter) · `rekordbox_xml` (XML adapter) ·
`engine` (the API surface) · `sync` (export + resync; **safety
contract at the top of the file**) · `paths` (sanitizing, device paths,
length budgets) · `playlists` (m3u8) · `report` · `metadata` (artist/title
derivation, title-cleanup rules) · `artwork` (canonical local images, results, provenance and defaults) +
`artfetch` (local compatibility aliases) + `artencode` (baseline-JPEG encode) +
`providers` (optional discovery contracts and startup composition) · `manifest` (per-device drift) ·
`track_analysis` (canonical V5 storage plus read-only V4 compatibility,
streaming RGB/grid
analysis, and export eligibility) · `rekordbox_analysis` (read-only
PQTZ/PCOB/PCO2/PWV3/PWV5/PWV7 parser) · `rekordbox_grid` (shared pure
raw-section/metadata transforms) · `rekordbox_grid_proof` (unintegrated temporary-copy-only
multi-file/SQL recovery proof; no publish-to-source API) · `rekordbox_grid_store`
(accepted track-bound source transactions and explicit library authority) · `device_profile` (read-only device
classification and stock-iPod readiness planning) · `stock_ipod` (observed-format
two-phase iTunesDB/artwork render/validate/commit adapter) · `stock_artwork`
(content-addressed MA446 RGB565 preparation and ArtworkDB/ithmb layout) ·
`stock_ipod_shadow` (disposable Mac-side per-device delta baseline) ·
`stock_ipod_info` (strictly read-only stock/dual device snapshots) ·
`dual_ipod` (schema-3 single-copy stock/Rockbox plan, rollback, and ownership) ·
`rockbox_media` (verified picture-free/tag-normalized payload derivatives and
theme-sized art) · `rockbox_config` (read-only Rockbox theme/database observations) ·
`backup` · `imports`
(imported-audio plane) · `libcache` (instant-paint snapshot).

## Device lifecycle and source acquisition (September 2026)

November3 efficiency pass: desktop imports keep full stock/dual/CDJ/filesystem
export backends out of startup. Export caches initialize on first use. The
lightweight mount watcher and read-only discovery remain active. Empty discovery
does not build Collection/Artists export playlists; a detected iPod/Rockbox target requests
one GUI-thread capture of Library, extra playlists and title/artist overrides
before drift inspection. The worker waits cancellably for queued delivery;
capture failure retains unknown status, and stale/shutdown requests cancel.
Only stock-capable detection supplies the stock shadow. Stock artwork warming
starts after attachment, coalesces library-load requests and stops on disconnect,
retaining the worker through finished delivery. First ready rows still carry
actual iPod/Rockbox drift; deferred inputs never grant device-write authority.
Generic USB detection captures no export playlists and performs no source/native
catalog comparison. Its browser reads a bounded catalog snapshot only on request.

`device_operations` is the Qt-independent serialized I/O authority, with mount
identity, command, phase, cancellation and typed result records. The desktop
worker adapter acquires the lane before starting and releases it only at
QThread.finished. Discovery, dashboard, verification, export, Sync, deletion,
imports and browser mutations share that lane. Normal macOS eject owns it until
QProcess finishes. Engine DeviceSession guards still authorize every write;
the coordinator never grants firmware capability or ownership of a file.

`operation_control` scopes cooperative cancellation to a worker. Planning and
media acquisition/copy boundaries poll it; rollback does not. Queued exports
are cleared on cancellation/failure. MainWindow shutdown dispatches events until
its collected workers have stopped. `desktop.task_owner.TaskOwner` retains
artwork/suggestion dialog and interactive waveform workers through queued
`finished` delivery and final join. Window shutdown closes child-owner admission
before dispatching callbacks and drains them before native-player teardown.
Stopped owners reject new work and their consumers reject late results. This is
a lifetime primitive, not a replacement device queue. The coordinator owns the
active device lane, but desktop adapters still
retain their own queues rather than using its queue API (audit A5).

`source_acquisition` captures canonical audio into disposable local storage in
one streaming hash pass per attempt. Both known- and unknown-digest captures
require stable before/opened/closed/after filesystem identity and a stable
canonical path; matching expected bytes cannot bless a replacement file's epoch.
Metadata drift before capture can pass, and one bounded metadata-instability
retry remains. Rockbox media inspection, normalization and essence checks use captured
bytes. Stock planning consumes those technical facts or captures its own source;
stock execution also captures plain payloads before copying. The filesystem exporter
prepares/copies/discards per file and suppresses destructive cleanup on failure.
Ordinary temporary device payloads are explicitly fsynced and verified before
atomic replacement, with fresh mount checks. New, unindexed FAT32 iTunes audio
instead hashes the copying stream, fsyncs, publishes exclusively without replacing
an occupant, then reads the actual bytes through the original still-open output
descriptor. Its operation-local receipt must be consumed before acknowledgement;
iTunesDB still publishes last. This removes a separate staging hash and duplicate
device hash without relying on coarse FAT epochs. Rewrites and other exporters
retain their existing staged-replacement rules. Failed publication/readback
retires only proved own unindexed output; uncertain replacement/cleanup retains
evidence with a recovery-required outcome.

`source_read_retry` handles macOS CloudStorage ENEEDAUTH reads with at most two
cancellable three-second waits per verification/acquisition. Each retry opens
the source anew and starts its hash/snapshot from zero; the independent one-retry
limit for unstable complete captures remains. Persistent errors, EAUTH, ordinary
permissions and non-cloud sources fail without this readiness retry. Composite
preflight errors include the source path; the UI reports the engine outcome
(no device writes, completed rollback, recovery required, or committed cleanup
failure) instead of assuming every error attempted rollback.

`payload-receipts-v1` stores compact, checksummed derivative contracts bound to
canonical filesystem/inode/size/mtime/ctime observations. It survives derivative
cleanup and avoids repeating completed planning work. Lost/corrupt receipts
cause verification, never loss of ownership data. `transaction-outcomes-v1`
mirrors recovery outcomes locally; these records are evidence, never automatic
permission to resume writes. Device manifests retain their existing schemas.

See the lifecycle audit and HANDOFF for outstanding physical acceptance.

## Source-adapter boundary

The dependency direction is deliberate:

    rekordbox_db ──┐
                   ├──> library model ──> curation / player / sync / devices
    rekordbox_xml ─┘

`soundbox.library` owns the only domain types consumed downstream. A source
adapter reads its own system without modifying it, normalizes tracks and exact
playlist order into those types, and assigns a stable `source_kind`.
Provider-native playlist key formats never cross this boundary: for example,
the rekordbox XML adapter converts `KeyType=1` location keys to canonical track
IDs before constructing a `Playlist`.

Optional lazy analysis is exposed on `Track` as the provider-neutral
`source_analysis_path`, while cue rows are normalized into `source_cues`. The
current rekordbox adapter is the only implementation and the player still
understands rekordbox ANLZ sidecars. A future source with its own analysis
format should add a focused adapter/decoder at that edge; it should not teach
the library model, sync engine, or device exporters about that provider.

There is intentionally no speculative source-adapter registry or abstract base
class yet. The optional artwork/connection provider composition is separate.
When a second real source is implemented, its actual discovery,
configuration, refresh, and error semantics should determine that small
interface. Until then, architecture tests keep the domain model free of
provider imports, and the existing rekordbox modules remain concrete adapters.

## Desktop modules (`desktop/`)

`app.py` = `MainWindow` core + orchestration. Mixins/modules: `devices`,
`trees`, `workers` (shared QThread adapters), `settings_page` (`SettingsPageMixin`),
`device_browser` (`DeviceBrowserMixin`), `artpicker`, `suggest`,
`artistcatalog`, `connections` (owned account jobs), `keyboard` (action catalog,
versioned map and physical-key router), `keyboard_page` (capture/conflict editor),
`model` (`TrackModel`), `player`, `theme`, `stock_device_view` (iPod dashboard),
`usb_device_view` (seven USB pages and immutable native snapshots),
`usb_preferences` (USB/global controls), `export_dialog` (selected playlists and
one-to-five target choices), `progress` (accent-aware progress lettering),
`track_info` (Summary/Info).
MainWindow composes these mixins with TrackInfoMixin and QMainWindow.
`native_deck.py` adapts the native C ABI to PlayerBar's small transport
protocol.

USB presentation reuses the iPod detail-tree factory and playlist styling.
Rockbox's bounded reader opens only `/Playlists` through no-follow descriptors,
never referenced media or PIONEER. Generic USBs use the dedicated bounded native
reader when either native library exists; no live SQL is opened on the USB.
Native preference controls queue an explicit Save generation through the same
worker lane. The UI does no removable-device I/O in widget callbacks.
A USB playlist drop carries target-bound authorization through preparation into
commit without another dialog. The Export dialog selects playlists/folders on
the left and connected devices on the right. A source-bound queue captures each
checked target mount/UUID, rebinds selected playlist IDs for each target and uses
the existing serialized exporters. Cancellation/shutdown clear pending targets.
Generic USB terminal source errors and an explicit song-update pause finish its
waiting target with an actionable refusal. iPod targets instead capture their
durable selected effective generation and may proceed with bulk updates paused.
Earlier results and source intentions survive. A source wait owns no device progress controls; another
USB's export or format keeps its controls. An active batch target remains owned
between physical phases, so a repeated wait callback cannot admit another target.
Selected playlist replacement prunes only removed members unused by every other
USB playlist in both catalogs. Exact removed assets are rollback-backed until
durable commit, then their transient recovery bytes are retired to free space.
All generic USB exports are additive to unrelated existing playlists. They never
reconcile other source playlists simply because those playlists were exported
earlier. Export starts with collapsed folders and uses themed device rows with
USB/iPod icons. Playlist-browser containers toggle on click without selection;
track rows alone are highlighted.
cdj_incremental freezes only selected changed/new
inputs and merges copied native catalogs through native/cdj/src/merge.rs. Prior
payload contracts remain inventory, not a fresh audit. Input signatures/per-playlist
hints are separate from ownership. Newly captured audio is placed directly in the
local partial output; the helper accepts only that exact Contents file set before
building catalogs/analysis, and parent SHA-256 inventory proves unchanged audio.
The packaged helper comes from build/cdj/release, never the debug profile. Source/candidate preparation and publication remain
in the serialized device lane through QThread.finished. Generic USB catalog
snapshots refresh on explicit browser requests; connection/remount invalidates
old snapshots without reading catalogs. Generic USBs have no idle drift poll or
sync/pending-state UI. Source-only changes do not start generic-USB-only scans.
Successful writes replace stale visible song rows with an honest browse notice,
retaining settings drafts and the last real hierarchy/selection for a deliberate
USB/tab refresh. Failed, cancelled and unchanged operations retain their snapshot.
An already-checked tab click refreshes once; programmatic restoration never does.
Completion starts no final read. Eject submits the normal diskutil request after
any already-owned worker
finishes, and starts no verification or final read. Successful eject retires its
row and all mount caches in memory without starting a connection scan. Backend
UUID/mount authority is absent from the
Identity rows. No USB tab roots QFileSystemModel at the mount. See the CDJ guide
for staging space, current-native catalog adoption, profile preservation and recovery limits.
The shared device header is 26 points; TrackBrowserTree builds on BrowserDetailTree
to make folders/playlists nonselectable single-click containers while preserving
track selection, stock reorder and independent checkboxes. BrowserDetailTree
continues the regular
24-point alternating grid below real rows. USB song rows use COLUMNS and shared
browser header state. Immutable CdjSong records retain actual USB audio/analysis
references; explicit activation validates them in the device lane and uses a
separate device track ID, without source cue overlays/edit authority. USB view
mementos are keyed by volume UUID, with tab, hierarchy/selection and scroll saved
under settings.device_views; stock dashboards retain their per-device page state.
The keyboard capture dialog uses the same transactional map save for Unbind as
for keycap removal; failed saves restore the prior editor/runtime map.

`desktop/app.py` remains the largest general orchestration hotspot. Its
sections are explicit and cohesive mixins already own Settings and the device
browser, but future product work should move behavior only when it creates a
real independently testable controller/service boundary; splitting methods
among cosmetic mixins would reduce line count without reducing coupling.
`desktop/player.py`, `soundbox/track_analysis.py`, and the native deck are also
large, but their waveform/cache/realtime invariants are intentionally kept
close and should be separated only along tested protocol or data-format
boundaries.

## Player clocks

The complete player reference, including interaction semantics and committed
next work, is in `PLAYER.md`.

The browser extends to the window's right edge; the former 30-point utility rail
is removed. Its circle-i is a 44×28 bordered prep button
immediately before My Tags, using the same BG inner fill as Settings and the other
controls, with a matching three-point corner radius. Info and Settings use
centered vector icons, avoiding font bearings. The player-layout popup is sized
to the selector field after Qt lays it out. Info opens Summary/Info for the selected row, with artwork inside Info.
Tested source routes native Title/Artist changes through durable intentions and
exact source readback. Metadata authority defaults off for an unaccepted source;
Sonny's exact library has separately accepted v5 Title/Artist authority, recorded
in HANDOFF. Other native Track Info fields refuse unsupported saves.
Save dispatch includes only changed controls, so an unchanged unsupported field
does not block a Title-only edit. Save and artwork actions retain the exact
source/library, logical/native IDs, UUID and native/local paths captured before
artwork preparation or the modal event loop; a replacement requires reopening.
Existing local overlays remain preserved migration/display/export input, and
legacy/XML sources retain local editing. Native artwork persistence is unfinished.
USB songs and historical
native-deleted Graveyard rows open read-only information dialogs. TrackInfoDialog constructs
both Summary and Info before presentation; tab switching performs no control construction.
Before modal show, ArtworkWorker resolves the canonical art path and decodes
a display-sized QImage outside the GUI. Preparation dispatches Qt timers/results
with user input excluded and a wait cursor, then shows a complete first frame.
TaskOwner retains the worker through finished/join and drains before dialog close
or application shutdown. Successful art publication refreshes matching dialogs;
an in-flight refresh is coalesced without painting its obsolete result. The old
cover remains until its replacement is ready. No extra work is added to
track loading. Comments spans the Info row; Lyricist is omitted from that form.
Global My Settings and Keyboard put their scrollbars at the window's right edge.
Flexible prep
gaps redistribute around it. FooterBar draws a single full-width top separator at a fixed28-point height.
Its progress/count/Cancel controls fit inside without changing browser geometry.

`desktop.browser_scrollbar.BrowserScrollBar` overlays a visible row scrollbar
at the frame’s right edge in TrackTable, Artists and USB playlist views. Qt’s
hidden native bar retains range, wheel/keyboard and24-point row scrolling;
bidirectional value/range synchronization drives the visible control. Geometry
starts below the header and follows viewport resize/show, including horizontal
scrolling. Headers retain the full viewport width so FORMAT can be dragged to
the frame without automatic stretching. Headers stay Interactive with a shared bounded width
policy: manual drags change only the dragged section, clamped to the remaining
free width without borrowing from Title. Window resizing gives spare width to
Title; narrowing or showing extra columns can reduce other widths only as needed. Saved/default layouts fit under
a guarded section-resize loop. The visible viewport's Resize/Show events supply the
available width, including the row scrollbar’s overlaid strip. Ordinary layouts fit without horizontal
scrolling; if selected fields exceed even their minimum widths, all three browser
views allow horizontal scrolling so every field remains reachable. Saved layouts
record columns_keys: restoring an older ten-column Qt header must explicitly keep
new metadata fields hidden. Artists mirrors shared widths and visibility.
ScrollPerItem keeps 24-point rows aligned with the sidebar. TrackModel declines
unsupported paint roles early without adding a row cache or source reads.

Delayed sort-selection and playlist-scroll restoration bind the current view
and selection revision, so a newer selection/navigation invalidates older
callbacks. In-place Artists refresh retains expanded groups, surviving selected
tracks, current column and scroll; entering Artists normally retains its existing
navigation behavior. Default-photo repopulation similarly restores stable photo
IDs and scroll. Shared choice painters elide within the label/shortcut/check
rectangles. The footer's `ElidedLabel` can shrink within the available layout;
its full logical text remains available to status state checks and tooltips,
and pulse reservation keeps neighboring controls still.

Prep edit feedback uses the existing lower-left status label and status helpers.
`TagEditController.update_status_text` distinguishes accepted tag/cue/grid intents
from mere worker ownership; `GridEditController` supplies beatgrid/BPM intent
labels without changing shared-lane admission or recovery. Focus-return grid
checks remain silent. Verified save callbacks alone finish the edit status with
`Done`, then the existing generation-guarded timeout returns to `Ready` without
clearing a newer message. Failed edits retain an operation-specific error.

Qt remains the application shell. Playback is native-only; Qt Multimedia is
used only by `QAudioDecoder` while analyzing AAC/ALAC. If the native deck cannot
start, SoundBox keeps the library available, disables player controls, and
shows a precise diagnostic instead of silently switching engines. The native
Audio Unit callback is the
authoritative sample clock. A background AVAudioFile decoder fills a lock-free
ring buffer; the callback never decodes, allocates, locks, or calls Python.
Metal receives waveform/grid buffers once per track and translates them from
the CoreAudio host/sample anchor on each CAMetalDisplayLink update. Python
polling updates coarse labels and buttons only. The prep-strip elapsed/total
clock uses those existing updates and emits only when its seconds/duration signature
changes; waveform drag previews own that label during a grab. It adds no timer or
source/library reads. There is no Python repaint timer
for native scrolling. CoreAudio presentation timestamps feed a continuous
phase-locked visual clock: device-rate and phase errors are corrected through a
tiny velocity bias, never a callback-boundary position step. The predicted
position remains fractional through the Metal upload, and beat-grid vertices
use integer milliseconds plus a local fractional phase instead of subtracting
large absolute floats. Every display callback predicts the authoritative
CoreAudio position directly for its target presentation timestamp; waveform,
grid, cues, and playhead share that timestamp. A four-refresh visual-debt
smoother was rejected in installed-app testing because it turned one rare
omitted ProMotion callback into several visibly speed-modulated frames.
Maximum zoom therefore magnifies track detail, not clock or grid quantization.

Analysis preparation reads ANLZ only as a seed/fallback source. The separately
accepted grid API writes only DAT/PQTZ and native EXT/PQT2 invalidation, with
exact library/schema/version and per-track UUID authority; see
[the prep plan](rekordbox-prep.md).
Active `djmdCue` rows come from the same read-only live database snapshot as
the library. Their normalized fingerprint and the exact normalized PQTZ beat
rows update only the disposable V5 manifest, reconciling durable pending edits;
deleted database cues and later Rekordbox beat-grid corrections therefore land
without touching the compressed analysis payload or decoding source audio.
SoundBox's schema-V5 container owns the imported streams, grid/cues, source
identity and optional derived detail under `track_analysis/`. Sections are
independently Zstandard-compressed and SHA-256 verified, then atomically published.
The import-first path reads complete PWV3/PWV5/PWV7 plus grid data before selecting an analyzer.
It produces only exact 1-ms signed stereo-midpoint extrema, with no duplicate
filter bank, FFT or beat analysis, and builds its overview from PWV5.

Background eligibility discovery reads packed PWV3/PWV5/PWV7 rows and PQTZ
directly; it never expands whole-library per-sample color lists or generic ANLZ
objects just to schedule upgrades. Full cue/display parsing remains per-track.

Compact records use `soundbox-rekordbox-supplement-i16-v1` and
`rekordbox-pwv5-rgb-v1`, store divisor 1 only, and omit the 24-band master. Loading
builds `1/2/3/4/6/8/12/16/24/32/48/64` mips in memory. Incomplete imports and codecs
not supported by the supplemental decoder retain full fallback analysis. Old
V5 remains ready while the recurring background worker queues eligible local
records for compact conversion, one track at a time. Interactive playback still
loads old analysis immediately and can finish a selected upgrade ahead of the
queue. That upgrade is saved for the next load; it does not republish a different
waveform onto an already complete loaded deck.
The old record survives interruption or failure; manual metadata ownership and
DB cue authority survive replacement. Source changes, explicit Analyze Track and
late imports use the same preparation path. Dataless background reads remain
deferred and per-track preparation receipts still avoid repeated work. The receipt
policy includes the compact model so old ready receipts cannot hide pending
upgrades. Compact tasks never fall back to a full analyzer after decode failure
and recheck whether an interactive load already completed them. They use the
5/7-GiB migration reserve and run ahead of non-foreground full cooks, which retain
the 20/25-GiB reserve.

AAC/ALAC compact supplements use QAudioDecoder at the native source rate when
libsndfile cannot open the file. They retain imported streams/grid and do not run
the full fallback analyzer.

Loading remains generation-guarded: native decode, manifest, overview and detail.
All cache decoding and native waveform preparation stay off the UI thread.
The native host never expands an unused Qt envelope during detail delivery.
PWV5 carrier repair is vectorized without changing its output, and native
display-stream publication checks the generation under its render lock. The first visible
waveform waits for complete overview, display modes and signed contour, for every
duration; manifest/grid/cues still become ready first. Partial caches wait for
repair before painting a provisional surface. Interactive reads omit only unused
spectral_24; default/full APIs and whole-record audits retain it. A fixed128KiB
native lookup matches all65536 original signed-transfer outputs exactly, reducing
upload cost without changing geometry or palette. Grouped drawing rechecks its
upload guard under the render mutex before snapshotting buffers; skipped draws
retain the dirty flag and advertise no unsent presentation. Source
replacement rejects late generations. Native ABI 6 retains the tested
timeline load contract. Tagged MPEG-1/LAME header facts are read on the decode
thread, matched to the decoder's rate/valid-frame count, and restore leading
samples to both playback and supplemental analysis. Seek and duration use that
same clock. Unknown headers retain their decoded origin; mismatched recognized
headers fail closed. No blanket MP3 timestamp is used.

The renderer uses linear amplitude, a0.84 base ceiling and its existing continuous
quiet-signal shoulder. Main BLUE and3BAND apply their separately calibrated
1.06/1.20 height factors, bounded to the lane; palette transfer stays independent.
Close BLUE/RGB hold signed cells and interpolate full-range
PWV5 colors (14/7/7 channel divisors); 3BAND uses original 150-Hz rows, a one-row
sampling offset, and linear layers with the measured membership palette. Paired
75-Hz rows, the artificial eight-percent facet, four-pixel close extrema footprint
and divisor-dependent height compensation were removed after the September 29
reference comparison. The 13 UI/native zoom stops end at 1500 points/s.

One footprint-selected song-anchored mip feeds the fixed four-attributes-per-pixel
lattice. Every stop uses one physical pixel of source support except the
logical-pixel footprint at RGB 576/864. Wide BLUE/RGB use the same conservative
LOD and same-LOD reconstruction as the middle stops. The October 2 close-zoom follow-up
selects 2-ms compact detail at Retina 384; the native-reference follow-up also
selects a logical-pixel footprint (2 ms) at RGB 576/864. Other close cases retain
1-ms detail. Thin signed carriers use a
one-physical-pixel minimum before coverage, replacing the former fixed
3.6-point stroke. Color is
premultiplied before the final one-pixel integral, with no adjacent-LOD blend or
translated RGBA texture. Legacy/fallback spectral masters never recolor the
imported display modes. RGB color recovery treats zero compact height as missing
weighted evidence, using the existing authored color rather than black; positive
weights cancel exactly. The October 26 follow-up also makes the whole-track
overview divide every positive color weight exactly. A compact zero can be
height quantization rather than silence: BLUE/RGB overview coverage falls back
to the resident high-precision audio envelope while retaining authored hue.
True zero audio remains background, and PWV7 geometry remains independent.
These compositor corrections need no cache conversion.
Metal receives integer sample anchors plus
small local phases for both geometry and color, retaining long-track subpixel
precision. Attribute generation uses absolute 128-attribute clock blocks, with
CPU-double-derived anchors and phases, so overlapping tile rebuilds cannot
round a held cell onto a different source sample. The scrolling lane translates fixed height/hue attributes into each
final Retina drawable pixel. The fragment computes vertical silhouette
coverage and composites the final color exactly once. All modes and all 13 zooms
use the pixel endpoints plus every integer knot of the four-attributes-per-pixel
lattice to form the complete piecewise-linear profile; integrating those true
segment spans avoids a hidden corner moving through a fixed probe interval.
There is no translated RGBA texture or second coverage/color filter; every
zoom retains the oversampled attribute lattice. RGB 576/864 use one logical-point display step, matching the native reference;
RGB64/128/184 use one physical-pixel step after Sonny’s October31 moving
comparison approvals; other RGB stops and3BAND retain fractional travel.
RGB64–256 signed reconstruction retains the center-tap minimum/maximum,
preventing the middle-zoom smoothing pass from shaving peaks. Neither source
amplitudes nor display gain change; widest/close stops and BLUE/3BAND retain
their existing geometry. The
historical October31 build285163dc passed1900 tests and source/state
preservation; current installed provenance is in HANDOFF. BLUE rounds only its
display offset to physical pixels
through 384 points/s and logical points at 576–1500, bounding its waveform-only
error by half that increment. Audio, grid and seek clocks remain
continuous in every mode. The antialias window is exactly one physical pixel.
The RGB overview uses the same bounded faint-only shoulder as the main RGB lane;
BLUE/3BAND overviews retain continuous cubic vividness. The scrolling lane uses
mode-specific joint geometric coverage.
For BLUE/RGB signed intervals, the fragment integrates clipped linear coverage
moments and visible color over the same pointwise intersection. It does not
reapply the smaller of separately integrated upper/lower boundaries: that older
approximation overfilled narrow diagonal traces and was removed October 2.
PWV5 RGB accumulates premultiplied display-encoded channels; BLUE's center/width-
dependent radial field retains premultiplied linear light. BLUE splits at coverage and radial branch changes
and uses four-point Gauss-Legendre quadrature within each branch; evaluating
the nonlinear field only at the coverage centroid produced visible color error.
BLUE retains linear-light joint-area compositing. RGB applies a smooth monotonic
faint-detail shoulder consistently at every stop below18% coverage:
c′=c×(1+1.5×(1−c/0.18)²), with premultiplied color scaled by
c′/c. It adds at most 0.04 coverage, leaves zero empty and joins exact area at 18%.
November2 replaces the former zoom-specific color-occupancy corrections. RGB
uses the supplied92pt/s reference color field at every zoom; its reference
fine-area contrast is evaluated on the song clock independently of the displayed
geometry LOD. BLUE likewise samples its palette at the reference footprint;
three-band retains its fixed native palette. No source/cache bytes change.
The faint RGB edge shoulder applies consistently below18% coverage, including384.
RGB576/864 use logical-point motion; RGB64/128/184 use physical-pixel motion; other
RGB stops remain fractional. The broad occupied-column boost is
removed; normal coverage and hue are not brightened. Persisted
extrema, mip data and the final physical-pixel integration window remain unchanged.
BLUE/RGB at 24–256 px/s and 3BAND at 64–256 apply the same-LOD `[1, 2, 1]`
reconstruction before final integration. Wave/color taps sit half a physical
pixel either side; signed interval taps use 1.5 physical pixels in source space.
The 384–1500 close family and wide 3BAND bypass that pass. Final coverage remains
one physical pixel; close source detail and accepted RGB motion are unchanged.
Scrolling grid rectangles carry signed local line distance into the fragment,
which applies analytic physical-pixel box coverage instead of rasterizing a
hard two-pixel-wide Retina rectangle. The drawable is BGRA8Unorm_sRGB:
main-lane RGB blends its encoded color/coverage with the encoded background,
then decodes that final opaque pixel. The RGB overview now does the same, with
only the bounded faint-coverage shoulder below 18%; its old full-range cubic boost
is removed for RGB. BLUE, 3BAND, their overviews and fixed-function grid blending
retain linear light. Metal encodes exactly once on store. Full colors and the `#0d0e12` clear remain
unchanged. The October28 RGB edge update retains both visibility lifts and the
existing source/height/LOD/clock contracts; it does not promise equal linear-light
energy for different RGB edge-coverage distributions.
The grid is the final timing-reference layer. Red downbeats and gray ordinary
beats have density-controlled interior opacity and opaque edge ticks, with
continuous analytic line coverage.
There is no temporal shutter, so moving and paused contours have the same
sharpness. Actual
`MTLDrawable.presentedTime` callbacks, rather than display-link submissions,
drive cadence diagnostics and the grab handoff. Presentation latency is one
frame, and the newest committed source frame is held atomically so mouse-down
cannot rewind or change waveform thickness.
For temporal R&D, an explicit developer-only frame trace preallocates a bounded
shared Metal buffer and blits the final drawable crop after encoding. Its CSV
ledger correlates display-link callback/target times, source frame, drawable
ID, GPU interval, and actual presentation time with the exact BGRA pixels.
Nothing writes or encodes on the display/audio paths; export occurs only after
the stopped run under the caller's explicit directory. The orchestration entry
point is `scripts/capture_waveform_temporal.py`. While a trace is armed, two
small repeated Gray-code/parity strips at the main-lane edges encode its exact
sequence index into the photographed panel. A fixed-waveform calibration
segment uses those strips to recover the phase, exposure blending, and rolling
shutter relationship between a 60 fps camera and the 120 Hz display. Timing
summaries also mirror the deterministic close audio-detail mip selection and
record the selected divisor, signed-carrier lattice cells per physical
pixel/refresh, and Nyquist-folded panel/camera rates. This keeps a real source
carrier distinct from dropped presentation or camera sampling in later
sessions.
At close BLUE/RGB stops the renderer preserves the detailed signed contour and
uses joint coverage/color integration. RGB 576/864 now use logical-point motion
while the fixed reference color field remains independent of the displayed
geometry footprint. The October31 RGB64/128/184 phase corrections are described
above; other RGB stops remain continuous. The faint shoulder above applies at
every RGB stop, including384. The installed September 30 correction additionally
retains the original color-premultiplication weight through attribute interpolation;
reconstructing that weight from interpolated signed bounds spuriously brightens
polarity/dominant-boundary crossings. Geometry and coverage are unchanged.
The October 1 complete-corner correction at RGB/BLUE 256/384/576/864
resolved the remaining reported pulses in Sonny's live B comparison. The separate
BLUE palette/white-core correction was also accepted and installed. Broader track
and exact-reference parity were not established by those checks. Sonny closed
further visual comparison work initially on October28, then reopened the
Gunslinger case and accepted the targeted follow-up above. See the current
handoff; that acceptance does not claim exact native equivalence.
Rejected
carrier-neutral, background-bed, coverage-sharpening, and RGB-transition
experiments are absent from the production ABI and shader. They either removed
accepted paused detail or steepened fractional pixels after the exact resolve,
making the real signed carrier easier to perceive as a pulse. 3BAND retains its
independent unsigned PWV7 geometry.
Ordinary pause captures the same exact frame at the end of its gain fade and
resumes from that fractional-millisecond position; the coarse Python transport
poll is never the pause endpoint.
Redundant inactive-preview updates are absorbed without waking the Metal run
loop. Render buffers are snapshotted under a short mutex and command encoding
happens after that mutex is released.
Prepared overview and ultra buffers are built off the UI thread and become
visible in one grouped native commit; the display link skips the incomplete
transaction so resolution promotion cannot alter a waveform already on screen.
Library preparation builds V5 incrementally, pauses during
playback, pauses full audio cooking below 20 GiB free until 25 GiB is
available. Grid/cue metadata can update the current track without replacing its
waveform. An upgrade performed by an interactive valid-cache load waits for the
next load before presentation; recurring source/analysis refreshes remain explicit
preparation deliveries. Analyze Track remains an intentional waveform rebuild.
`preparation_inventory` persists disposable scheduling receipts for analysis and
stock art. Analysis polls stat identities every 60 seconds; unchanged entries
skip record/ANLZ reads. Changed entries share one header; a 30-minute fallback
retries sidecar parse failures. These receipts never grant device ownership or
replace interactive checks. Title edits invalidate one computed row; registry
and policy changes invalidate all rows. Background warms compute only misses.

The shared status row reserves the operation label's animated width and the
counter's complete total-width, so repeated updates do not move the controls.
Per-file acquisition/copy/verification phases and spaced byte totals live in
the status tooltip; the operation callback alone owns visible progress. Static
messages resize to fit and stop any old pulse. Transient expiry is bound to the
presentation generation. Analysis/artwork use separate counters, yield to device
ownership (including Verify), and restore a still-active pause after returning
to idle. These are presentation rules; transaction progress and completion
boundaries are unchanged.

Device export is pinned by `sync.DeviceSession` to one resolved mount identity
and revalidated before each write. Stock/composite plans retain that identity
through planning and commit, so a same-name replacement mount cannot inherit
a plan made for the prior device. Filesystem-browser rename, move, folder, and
delete operations use the same pinned boundary; confirmed deletion carries the
already-pinned session into its worker. Manifest v2 records normalized destination,
source identity, size, SHA-256, playlist membership, artwork/playlist kind,
ownership, and completed generation. Tracks, covers, playlists, and the
manifest use destination-filesystem temporaries plus atomic replacement.
Composite normal Sync trusts the committed inventory; Verify checks references
and sizes, and Deep Verify hashes all managed files using the schema-3 reader.
Explicit composite Repair audits inventory and restores missing owned Rockbox
audio/art while refusing unexpected occupants. The legacy filesystem exporter
retains its manifest-owned repair policy; generic CDJ USBs expose no Repair/Verify
workflow. Pruning follows explicit ownership rows
and never walks `/Music`
as authority. Unknown content remains untouched. Stock iPod writes are a
separate additive transaction: a pinned Clickwheel version renders only in a
temporary tree; SoundBox validates the output, writes recovery bytes,
atomically copies new media/artwork, and commits `iTunesDB` last. Curated
baseline JPEGs remain the shared Rockbox/stock source of truth. A low-priority,
playback-aware worker prepares content-addressed MA446 RGB565 100×100 and
200×200 payloads under AppData ahead of sync only while a stock-capable iPod is
attached. A locator index skips unchanged JPEGs, and manual art changes queue
only affected tracks while connected. Successful commits
atomically publish a Mac-side `stock_ipod_state/` shadow keyed by library
persistent ID plus volume UUID. Exact database/artwork fingerprints activate
the fast path; mismatch performs full reconciliation. Because a valid shadow can
be playlist-scoped, selected sources absent from its acceleration index are
narrowly reconciled against size/suffix candidates in live device media using
exact content hashes before any copy is planned. Incremental art preserves image
IDs, compares current RGB565 slices when a local cache key is absent, writes only
currently unreferenced fixed-size slots, and consumes a pixel-identical free slot
without writing it before publishing ArtworkDB and iTunesDB.
Planning still audits every MHIT→MHII→MHNI offset/size; source media and tags
are read-only. The writer
originally used observed MA446 iTunesDB v25/checksum0. Current Video/Classic format
and signing capabilities are independently guarded by `stock_ipod_format` and
the [device guide](DEVICES.md). A remembered model or display label cannot grant
signing identity. New stock audio remains MP3/PCM16 WAV.
Catalog Sync preserves indexed containers without opening retained audio.
`device_profile` derives the export pipeline from the mounted layout. Stock-only
with the supported observed catalog routes to the guarded database planner; Rockbox-only FAT32 routes
to the filesystem planner; generic FAT32/exFAT USBs use the dedicated CDJ planner;
and a FAT32 iPod with that supported catalog containing both `.rockbox` and
`iPod_Control` routes exclusively to `dual_ipod`. The shipped composite planner
places stock-safe media once under `iPod_Control/Music/Fxx`, indexes it in
iTunesDB, and references the same file from Rockbox playlists. Stock-unsafe but
Rockbox-supported media lives once under `/Music`. Schema-3 ownership spans
both path classes, so playlist reconciliation, reference-counted cleanup,
prepared artwork, recovery, commit ordering, and rollback are one transaction,
never two exporters run back-to-back. The MA446-safe subset is validated MP3
plus stereo PCM16/44.1-kHz WAV; that conservative stock rule never filters a
file supported by Rockbox. Media unsupported by both profiles is omitted and
reported. Schema-3 playlist reconciliation also performs a bounded reverse
identity lookup for append-only Rockbox M3U8 edits: device path → owned media
row → hash-authenticated historical playlist position → stable `Track.track_id`
(with canonical-source lookup available when no historical proof exists).
That proof also rebinds moved sources in a disposable planning view, keeping
original manifest bytes for recovery and verifying source hashes before reuse.
Proven new membership enters only SoundBox custom playlists, using an exact
before/after membership transition saved before the device transaction.
Same-track/source and byte-identical-file duplicates retain their first
occurrence; distinct recordings remain separate. Existing source-playlist
overlays remain readable for compatibility, but iPod sync creates no new ones.
Unknown paths never create tracks or choose an import destination.

Source verification uses a disposable `source-verification-v1/` journal under
Rockbox staging. Each tiny record is written after a complete stable SHA-256
read, keyed by canonical source path and bound to filesystem/inode identity,
size, mtime, and ctime. Planning retries reuse exact proofs without rehydrating
the audio; no audio copies are retained. Cache loss or malformed records mean
full verification when a fresh proof is needed. The journal never grants device
ownership. Normal composite Sync uses the committed inventory's unchanged
path/size/mtime/display/policy contract, retaining original timestamps across
ctime-only churn for local and cloud sources. It does not scan retained device
media/art or publish a fresh source proof. New/relocated/changed sources still
require exact-byte evidence; their preflight timestamp refresh requires matching
the planned source hash. Refreshed source baselines flow into the stock copy/
rewrite contracts and
final unified manifest without altering the caller's plan or recovery bytes.
Different bytes or repeatedly changing sources fail before writes.

Stock compatibility probes are unique-source scoped. A matching verified
composite payload already in the stock subset reuses that classification,
retaining database/path identity checks. Live-size checks apply to affected
paths and explicit Repair; unchanged manifest inventory needs no per-file stat.
Initial accepted dual drops and
contiguous generic-device queue entries batch all available playlists, so
shared media is planned once per batch. Stage timing and source-proof counters
are logged; see `docs/sync-performance-audit-2026-09.md` for measured findings
and remaining performance limits.

Stock device presentation is a separate read-only path. `stock_ipod_info`
creates immutable identity/track/playlist snapshots, merges pending playback
records only in memory, and exposes no mutation API. The desktop device page
renders them as Device, Songs, and Playlists tabs. On a schema-3 dual device,
Songs and Playlists merge manifest ownership with the parsed iTunesDB and label
each row iTunes/Rockbox, iTunes, or Rockbox. Rockbox-only and USB targets use the shared-style
Overview/Playlists surface and cached native or M3U/M3U8 snapshots instead of the
former raw filesystem browser. Connection presentation separates visibility from readiness. Debounced `/Volumes`
notifications and a 250-ms cached kernel mount-table check request discovery even
during playback or an eject-ready hold. The latter uses Darwin
`getfsstat64(MNT_NOWAIT)` without opening mounted paths, so mounting over an
existing directory cannot strand detection behind probe backoff. These are hints,
never external-device/write authority. A busy lane retains the request and retries
on worker retirement. The three-second local `/Volumes` timestamp fallback remains;
unchanged mount/timestamp state starts no idle discovery worker. Failed probes
retain 3–30-second backoff, bypassed by mount changes. Connected iPod/Rockbox drift stays on its
three-second poll. Generic-USB-only idle/source events start no mounted-path scan.
Mount changes, retryable discovery or an explicit browser request may refresh
connection/storage facts, without a source comparison or native-catalog status
check. Ordinary unmounted folders retain the 30-second attachment grace.
Verified external mounts appear disabled as `NAME · Checking…`. The first iPod/Rockbox scan
computes sync drift before publishing the selectable row at QThread.finished;
unknown/failed iPod/Rockbox checks say `Sync status pending`, never imply a clean result.
Generic USB rows become usable after mount/filesystem detection with no sync badge.
Stock/dual dashboard preload follows in the same serialized device lane, with
loading only in the bottom status after the row becomes ready. Successful preload
clears that status directly, without another immediate drift scan. A cached
dashboard is presentation-only; selecting while it loads uses the same serialized
preload. Writes stay guarded by operation ownership and firmware capabilities.
Remounts invalidate stale views.

Post-operation stock readback refreshes the target's cached native snapshot even
when Collection or another device is visible. Only a visible target gets loading
presentation; native publication never changes navigation. That read joins before
the sidebar/drift scan starts, avoiding competing device readers and a stale
catalog on the next action.

The disposable iPod catalog witness preserves separately exported playlist
bindings against exact old/new native catalogs. Source folder labels and complete
source occurrences remain separate from the actual rendered label and compatible
iTunes subset. Device-only Delete rebases surviving associations and the matching
fast-sync shadow after readback, using a generation-checked cache replacement.
These caches confer no content ownership. Header actions consume cached pending
state; ordinary Export/Sync have quiet success, and Delete obtains one short
consent before planning. Full details stay in diagnostics; actual failure dialogs
show bounded issues. See [device behavior](DEVICES.md).


Device transactions share one UI ownership contract. Custom result signals may
record a worker result, but only `QThread.finished` clears the owning attribute
and may advance the UI. Composite planning maps to 0–30%, device
mutation/verification to 30–80%, and its post-operation iPod dashboard, sidebar/drift,
and ownership release to
80–100%. Stock-only planning uses checked entries through15%, current-file
checks15–25%, media25–65%, artwork65–75%, database75–78% and cleanup78–80%.
New copies report actual bytes written and read back within that media allocation;
the tooltip identifies transfer-plus-verification work rather than implying all
of those bytes are new audio. Verified-song totals advance only after a matching
content readback. Events are throttled with real phase endpoints, never driven by
elapsed time; empty phases invent no work. Stock finalization owns the remaining
80–100%. `100%` and the quiet completion summary describe terminal state; device row
visibility separately describes connection state. A failed required final read
shows a final-refresh failure instead of running the success callback. Generic USB
export has no required post-commit dashboard/catalog read; worker retirement is
its completion boundary. Formatting reuses a correct full-capacity first MBR
partition for same-filesystem erase only when the partition type also matches;
otherwise it repartitions. Fresh physical/source guards and postconditions remain,
with per-probe/erase timing logs. Native initialization removes its local renderer
lock before exposing the candidate file set.
Preloads are scoped to path plus the mount root's filesystem device token, so a
same-name remount invalidates and reprobes path-keyed disk/profile facts. Model
confirmation persists against the iPod library persistent ID plus macOS volume
UUID; Apple/SoundBox rewrites may change the MHBD `DatabaseID`, so it is display
information only and never a device key. On macOS, `desktop.devices` performs
bounded SysInfo, exact mounted-device IORegistry/Finder records and, when needed,
a separate read-only endpoint0 extended-SysInfo helper. Its mounted root, BSD,
volume UUID and exact USB ancestry must agree; complete replies cache only for
that context, negative replies back off, and cached-only queries do no I/O.
Fresh conflict observations prevent remembered refill. No USB Open/Seize,
interface claim or device file write is used. Observed serial suffixes may select a guarded
model-catalog entry, and the verified model may identify the DAC. USB/SCSI
revision remains separately labeled transport data. Every derived value carries
provenance in the snapshot and ambiguous matches produce no value. The device page
renders the DAC as a concise component name and filters its Playlists tab to
regular user-created playlists plus the exact Apple Videos smart index. Videos
is labeled `System playlist` and may enter the guarded delete planner without
owning or deleting media. When Apple mirrors Videos in both ordinary MHLP and
canonical MHSD-5 datasets under different IDs, the snapshot exposes the
canonical identity and the planner removes both representations together.
Music, Movies, TV Shows, and Audiobooks remain immutable; On-The-Go is
firmware-generated and absent from iTunesDB.

### Loaded-track cue refresh

After a successful source load/refresh, MainWindow resolves the matching loaded
track/source's authoritative DB cues together with its exact pending cue overlay
before updating PlayerBar. Empty resolved lists clear
removed cues; absent authority and mismatched identities leave the deck alone.
This updates the live cue override, panel, and both waveform markers without
seeking or reloading audio. Later queued cache manifests use that latest override.
Grid metadata continues through the existing fingerprinted, atomic background
refresh, preserving compressed waveform payloads.

## Data model

- **Copy-only prep proof stays isolated.** The
  `rekordbox_write.OfflineWorkspace` accepts an explicit source for stable
  read/copy, then permits SQL only beneath a marked private temporary workspace.
  Its typed tag plans bind a workspace identity and complete logical baseline;
  encrypted staging, exact-delta checks, SQLite transactions, durable journal and
  restart reconciliation are exercised without opening the real source with
  SQLCipher. It cannot publish back. This offline full-database verification is
  not the production save path. Its flag conventions are provisional, superseded
  for live tags by native reference evidence. Companion-file transactions for
  grids are now handled by the separate accepted source protocol in
  [rekordbox prep editing](rekordbox-prep.md).
- **My Tags use a separate live transaction API.** `rekordbox_tags.LiveTagStore`
  requires an explicit per-library/schema/version authority and closed native
  app/agent. It checks expected source UUIDs, tag names and memberships, maintains
  an encrypted recovery baseline, journals exact row deltas, limits writes with
  a SQL authorizer, commits atomically and independently verifies affected rows.
  Native acceptance covers rekordbox 6.8.7.0303 with no Cloud/Mobile Library Sync.
  `desktop.tag_controller` owns saves/readiness probes through TaskOwner retirement
  and join; refresh, source selection, backup/restore and shutdown serialize with
  that owner. A successful save reloads the source snapshot and smart memberships.
  `smart_playlist_order` orders default smart-list rows by active matching
  native tag-assignment creation dates, including pre-update history. The reader
  captures dates in the existing membership pass; missing/invalid dates stay
  unknown. Only positive rule tags contribute. Verified local display ranks
  remain a fallback for unknown dates/ties, bound to source/library, tag,
  track/native UUID, playlist UUID and exact rule hash. Newer source dates take
  precedence. Removal/no-op/failure creates no local promotion. Header sorts
  override the default; native rules/memberships and exports keep their order.
  `LibraryTag`, source UUIDs/memberships, per-tag dates and library identity
  travel through instant-paint cache schema13 (older4–12 remain readable).
  Export snapshots omit this display-only date graph. Cached data cannot grant
  write authority.
- **Tag and smart deletion retain independent native admission.** Local tag
  deletion shares definition transaction mechanics but has a separately marked
  authority, receipt namespace and exact assignment/rule dependencies. Its FIFO
  persists before admission and verifies definition/assignment absence before
  acknowledgement. Intelligent rules remain opaque and unchanged. Local smart
  deletion uses a distinct opcode and default-off playlist capability/profile;
  it captures exact target/rule/parent and following-sibling clocks, retains
  native XML and projects only the selected computed-list removal. Native
  unobserved historical shapes and production activation remain separate gates.
- **Tag definitions and new smart playlists have independent admission.**
  `rekordbox_tag_definitions` owns unassigned leaf creation through its marked
  authority; `desktop.tag_creation_controller` persists its own FIFO on the shared
  source owner. `desktop.tag_creation` supplies the full-width My Tags header
  button and creation forms, defaulting smart conditions to My Tag/ANY. Smart
  creation requires the playlist authority's explicit capability and profile,
  exact tag/category/parent pins and full source readback before presentation.
  The accepted root/nonempty smart-only placement windows preserve existing
  rules and relative order; empty/mixed folders and existing smart rename/rule/reorder changes
  remain guarded. Deletion has its separate capability above. SQL/XML phase clocks and renewed shared-parent clocks persist
  before admission. The November12 pilot/native reopen and installed package
  complete this scope; no cache or prior ordinary-playlist authority enables it.
- **Cue planning is pure; the accepted writer binds one track.** `rekordbox_cues` produces exact
  four-table deltas from raw snapshots, separate from normalized display cues.
  Full revisions and explicit cue IDs/UUIDs guard stale chronological deletion;
  aggregate insertion order, legacy fields and overflow remain intact. Native
  WAV/MP3/AIFF and historical-aggregate references inform its rules. It has no
  SQL/file API. `rekordbox_cue_store.CueLibraryAuthority` is provisioned only after
  native add/delete acceptance and binds `PilotCueStore` to an explicit track.
  It shares
  the tag store's exact root/lock/journal, with operation-specific row/column
  restrictions. One desktop TaskOwner owns tag/cue operations; pending operations
  reconcile before another domain/track is admitted. Native DB-only add/delete
  acceptance passed on No Trust, with audio/ANLZ/XML untouched. Cue saves publish
  a new target Track and Library view without audio reload, browser rebuild or
  full source read. Raw revisions/native IDs stay outside display cue hashes.
  `desktop.cue_session` supplies RAM overlay helpers without mutating that
  verified Library. Ordinary cue-only native and captured-import gestures publish
  immediately usable, track-bound overlays, then admit canonical intentions
  through `prep_edits`/`desktop.prep_state` and the shared native FIFO. Grid
  intentions retain their separate durable admission order. Exact receipt/readback proofs
  precede acknowledgement or retry, and failures/restart retain durable intentions
  and newer edits. Normal shutdown drains accepted saves before TaskOwner.stop.
  The older RAM-only session route remains nondurable; do not mistake its tail
  cancellation behavior for the ordinary durable prep workflow.
  Recovery receipts bind the physical source-file identity and may recognize an
  exact inactive zero-header SQLite journal without changing/deleting that file.
  Replaced DB files fail before SQLite recovery opens them.
- **Reads the LIVE rekordbox DB** (default `~/Library/Pioneer/rekordbox/master.db`)
  via a read-only temp copy through `pyrekordbox`; XML export is a fallback.
  Connections can validate/select another DB; `settings.rekordbox_db_path`
  persists only after read and save succeed. The explicit path travels through
  RefreshWorker, snapshot acquisition and relative analysis-path resolution;
  the module's default path is never mutated.
  Refresh first reconciles any prior authorized transaction using its provisioned
  authority, even before a cached library has been verified. Source selection
  never creates permission. Unknown recovery stops the read; the snapshot reader
  rejects nonzero rollback journals so spilled uncommitted pages cannot masquerade
  as a valid DB/WAL snapshot. Zero-header inactive files are left untouched.
- **The temp snapshot is a verified DB/WAL pair.** SoundBox copies only
  `master.db` and its WAL, compares both source identities before and after,
  and retries a concurrent checkpoint/update. SQLite's process-local SHM is
  never copied. A database that remains busy fails visibly instead of yielding
  a mixed-time library.
- **Smart playlists mirror active rekordbox state.** Rekordbox retains removed
  My Tag associations as soft-deleted history, so the adapter requires the tag
  definition, association, and content to all be active while preserving
  compound ALL/ANY rules. Historical tags and deleted songs can never
  resurrect a playlist membership.
- **Playlist reads fail closed on structural ambiguity.** Active node types,
  parents, sibling `Seq`, membership `TrackNo`, stable IDs, and display paths
  are validated before publishing a library. Normal playlists use exact
  `TrackNo`; smart playlists use their stored criteria; unreachable or
  unverified structures make refresh fail visibly rather than return a
  plausible partial tree.
- **Refresh results are monotonic.** A refresh click during an active database
  read queues one immediate follow-up snapshot, and instant-paint cache writes
  are serialized so an older result can never overwrite a newer result on disk.
  Results and errors publish only after QThread.finished retires the captured
  worker. Failed source reads/selections preserve the active library and curation.
- **Verified source plus local state.** SoundBox retains its local title/artist
  overrides, imported records, hidden/disposed curation and legacy custom data
  alongside the verified native library. Local display/export overlays remain
  distinct from source authority. Ordinary registration, playlist, Tag/Cue/Grid,
  initial WAV/CBR analysis, locator and confirmed disposal operations use their
  independently accepted, narrowly scoped native domains on the common lane.
  A local membership veto alone is not a native playlist save. Accepted
  migrations preserve the original custom records and stable device identities.
- **Imported duration is source-header metadata.** `soundbox.imports` uses
  libsndfile first and Mutagen's container metadata fallback for ALAC/AAC;
  legacy zero-time records are backfilled and persisted on load. Canonical
  duration remains integer seconds; tables render hour-plus audio as H:MM:SS.
  Imported tracks remain categorically exempt from rekordbox cue/placeholder
  hiding, even if a damaged file still has unknown duration.
- **Imported locations use persistent identity, not startup crawling.** The
  saved path is the constant-time healthy fast path. Each direct import also
  carries opaque Core Foundation bookmark data that can resolve the same file
  after a file or parent-folder move. A background startup repair touches only
  records with a missing path or legacy missing bookmark. Legacy/unresolvable
  records receive one batched Spotlight query constrained by exact filename
  and recorded size; only a unique live result is accepted and immediately
  receives a fresh bookmark. Ambiguous/unindexed cases remain missing until
  the user chooses an explicit folder for the bounded `Fix Missing Links…`
  scan. It prunes hidden subtrees and bounds all visited entries. Multiple
  same-name/size candidates require identical stable bytes before selecting a
  duplicate; unique candidates need no payload read. Stable SoundBox track IDs keep playlists, edits, art, and analysis
  attached through every successful relocation. The explicit scan still has
  confirmed sole-candidate size and truncated-inventory uniqueness gaps in the
  [critical ledger](critical-audit-2026-11.md); this intended identity contract is
  not a claim those queued defects are fixed.
- **Storage-provider timestamp drift is content-verified.** V5 source identity
  normally remains the cheap exact size/mtime gate. If a provider move changes
  only that identity, SoundBox verifies the cache's recorded boundary
  fingerprint and persists a cache-bound xattr proof instead of decoding or
  rewriting waveform data. Dataless File Provider placeholders retain an
  existing same-size cache without hydration; verification waits until a user
  action makes the source local. Real size/content changes still recook only
  the affected track.
- **Playlist identity = rekordbox `pid`** (`DjmdPlaylist.ID`), stable across
  rename/move → it is the device-sync identity. Rename/move = same pid (syncs);
  a genuinely new playlist = new pid = fresh export.
- **Device filenames belong to the destination contract.** Rockbox/filesystem
  paths derive from sanitized, length-budgeted effective titles; stock uses
  scrambled Fxx names, and native CDJ placement preserves/reconciles its own
  catalog-bound audio paths. Effective title/artist live in the appropriate
  metadata/database fields. Export never renames source files.
- **Browser default order = newest-ADDED first**, with recent matching My Tag
  assignments promoted in smart playlists. Collection/ordinary playlists retain
  one timeline across both planes:
  `Track.added_ts` = rekordbox `created_at` for mirror tracks, SoundBox import
  time for imported audio (falls back to file birthtime when unknown). See the
  GOTCHAS caching note: `added_ts` must survive `libcache`; known dates do not
  resolve media paths. Only unknown-date birthtime fallback uses `_landed_cache`.
- **Drag-to-add works on the sidebar AND the main browser.** Dropping tracks or
  Finder audio onto the open view adds to the playlist it shows; Collection
  imports files only; Artists is always an ephemeral SoundBox grouping and
  never a source playlist destination. Regular native rekordbox views
  accept registered tracks through the accepted source worker. Direct imports
  also capture Finder files and unregistered imported tracks into the
  durable registration FIFO, gated by its separate native authority. Its installed
  and native-acceptance state is recorded in HANDOFF.
  `TrackTable.resolve_drop` binds each drop to the active view.
- **One Playlists tree, with durable native unification.**
  `soundbox.playlist_unification` describes only the original local custom hierarchy; `desktop.playlist_unification` sequences the accepted registration and
  playlist FIFOs. Exact source/library and generated ID/UUID mappings persist
  with each admitted operation. Custom display cuts over only after native
  memberships (including duplicate order) verify. Original local dictionaries
  remain preserved; the native playlist’s `export_key` retains its old custom
  device identity. That binding never grants source or device-adoption authority.
  Artists instead groups effective display metadata inside SoundBox. Fresh native
  libraries create no migration state, artist folder, per-artist playlists or
  reconciliation queue. Existing completed custom migrations return immediately
  before scanning tracks or probing source authority. The discontinued internal
  native-artist attempt is archived by exact IDs/UUIDs for recovery; those exact
  generated rows were removed through the accepted playlist delete transaction,
  preserving Collection, audio, user playlists and XML history. An unrelated
  user-native folder named Artists remains ordinary and reorderable.
  Legacy custom/visible-import migration is a one-time internal upgrade, with no
  normal UI command or inferred backfill merely from source selection.
  The unified sidebar has one selection and scrollbar; folder navigation leaves
  the active browser view alone. Legacy local-only sources retain bounded
  `custom_playlist_tree` organization. Source membership/export order stays exact.
  Collection and virtual Artists remain fixed; ordinary native siblings reorder
  through an independently accepted exact-library capability. Each durable request
  binds selected/anchor IDs, UUIDs, parent and revision before source publication.
- **Playlist icons are local presentation.** An icon-only right click offers Star,
  Music note and Vinyl crate for playlist rows, including intelligent playlists.
  Native preferences bind source path/library ID/playlist ID/UUID; stale menus
  reject changed identities. The delegate uses a catalog-scoped row index and
  dictionary lookup, and a successful local save only repaints the viewport.
  Folder icons, Collection home and Artists person remain fixed. Legacy local
  rename/delete moves or removes only its own preference in the same state save.
- **Track/display preparation is bounded.** Path decoding is cached against its
  exact URI without stat/resolve;
  conversion facts use an ID index rather than a collection scan per displayed
  song. Grid arrival has its own readiness signal, independent of decoder timing.

## Settings, keyboard and optional account state

`desktop.theme` resolves the saved `theme_accent` into base/light/dark/subtle
roles. `theme_text_mode` (White/Black/Custom) and `theme_text_custom` independently
control text on accent-colored selections/buttons; White is the default and
custom colors survive mode/accent changes. `ThemeProgressBar` paints the selected
accent-text role only over its fill and normal light lettering over the empty
region, so a white accent with black text remains readable at every percentage.
General Settings applies changes across
small widget-local accent declarations, vector wordmark and custom-painted
chrome without reloading a track or resetting QApplication's stylesheet. Native
color-dialog signals feed a16ms coalescing preview; previews never mutate saved
preferences. OK commits once, Cancel restores the original accent/text roles. State load/restore reapplies both preferences. `desktop.wordmark.Wordmark`
resolves the supplied SVG color layers in memory and occupies the original
270×55 slot. The SVG is packaged data and part of the source fingerprint.

The approved October31 `desktop.app_icon` path applies the committed accent to
the running application icon after OK and at startup, independently of the16ms
picker preview. It renders Sonny's supplied `soundbox-original-sb-icon.svg`,
resolving its named accent/ink/highlight roles and border color for Qt while
preserving authored letter outlines, clipping, dark tile and alpha. The approved
project SVG removes one tiny ink cutout so its overlaid gray shine has no white
streak; the supplied original remains untouched. The exact SVG is bundled
and fingerprinted. Default PNG/ICNS derive from the same vector; eight modern PNG
ICNS representations round-trip exactly through native iconutil readback.
One normalized color per application process avoids repeated rendering for text-only or
repeated settings application. Qt's Cocoa application-icon API changes the running
Dock/Command-Tab image. A normal packaged launch/commit also persists the saved
icon through NSWorkspace custom-icon metadata at the app root, preserving signed
Contents bytes; isolated/development runs do not customize the installed app.
Each fresh frozen launch still renders/encodes and reapplies that metadata;
there is no persisted exact-icon no-op proof across launches. Snapshot-browser
startup measurements exclude persistent icon readiness.
Cancel does not touch the icon. Headless transaction/image checks and native
NSApplication icon captures cover the commit path; HANDOFF gives installed status.

Settings orders its tabs General, Analysis, My Settings, Keyboard, Graveyard, Connections; Graveyard
uses the full width. A QTabBar is centered across the 40-pixel header, with Back on the left
and no redundant Settings label. Equal outer grid columns keep its center
independent of the Back button's width. It selects a
QStackedWidget below it, leaving the remaining height for page content.
General uses equal expanding columns capped at 600 pixels: Player/Library/Backup
on the left, Title Cleanup/Cover Art on the right. The cleanup list sizes to its
rows up to eight visible entries, then scrolls.
Connections uses equal columns with core rekordbox source controls on the left
and the optional personal SoundCloud card on the right. Cached source presentation
is explicitly unverified until this session's actual read succeeds; the status
card never probes the database merely to display it. Public keeps Connections
and rekordbox while omitting the SoundCloud card/provider.
The five title-cleanup defaults include the exact `(Original Mix)`
tag; old custom copies remain in saved settings but belong to the default toggle.

Keyboard routing and the editor share one action catalog. The portable
`settings.keyboard` overlay supports multiple modifier combinations per action,
explicit unbound lists and conflict moves, preserving unknown fields/actions.
Only active-page cue slots and page selectors are offered; retired direct
Hot/Memory action IDs survive saved-state round trips but cannot execute.
Waveform zoom defaults to `=` / `-`; existing explicit overrides are preserved.
Physical key ownership retains the actual release callback across focus,
modifier and cue-page changes. Held-control aliases share their release boundary;
application deactivation, shutdown and successful remapping release them safely.
Text/dialog/navigation handling stays with Qt. See [KEYBOARD](KEYBOARD.md).

The personal connection factory is admitted by `connections.soundcloud` in the
versioned provider manifest. It receives only an AppData root, initially bound
without I/O. A window TaskOwner owns the connected account's initial refresh;
the prepared Settings section attaches to that worker or its safe completed result
and owns explicit account jobs. Both paths share one service and its serialized
credential/token operations. Cached same-account likes load locally at service
demand without a credential/network request. New connections
use the client-credentials grant with HTTP Basic authentication, resolve a public
profile, and read its public likes without a browser or callback. Existing website
redirects and authorizations are unchanged; public disconnect clears local state
without revocation. Legacy user-authorized tokens retain backend refresh/revocation
compatibility. Client credentials and rotating tokens live in Keychain; disposable
profile/liked-track metadata lives
under `connections/soundcloud/`, outside library backup/restore. Complete
same-account scans publish atomically and canceled/failed scans preserve the old
index. Unknown-profile likes never carry across a reconnect. Discovery reads an
in-memory index before general search; the manual picker merges both result sets
with eligible artist-name-matched uploads first, then likes within the same
tier, then score. No provider gains source/device authority.
See [SoundCloud setup](soundcloud-connection.md). Public omits this factory and UI.

`desktop.artreview` automatically checks every cover listed in All, regardless
of provenance or old confidence, and prepares comparisons in the background. Suggested contains only ready different alternatives, All permits
individual browsing, and foreground selection queries can be cancelled/coalesced
without assigning stale results to the next row. Main actions are Keep, Replace,
Next. Keep persists an exact entry/image-SHA plus artist/title stamp in
`art/review-kept.json`; Next dismisses only this session. Replace itself is
individual approval, with no second modal. The title supports mouse selection
and normal copying, matching the source/evidence labels without a text caret.
More contains a preview-before-approval artist-profile
choice, a single Exclude Uploader manager, refresh/source controls and manual
image/URL selection; no Undo menu item. The block manager lists saved custom and
known automatic accounts, permits explicit additions/removals, and preserves
unrelated prepared comparisons when preferences change. The initial loading page has an animated wheel and checked/total progress; the
first usable comparison reveals the review page while an inline indicator keeps
the full scan visible. Completed empty/error states are explicit. Browsing All
can prioritize a pending track. The session preview cache is bounded to 256 entries /
16 MiB, each preview to 512 KiB. A separate confirmed
fetch retires through TaskOwner before publication.
`soundbox.art_review_scan` checkpoints disposable metadata in AppData
`art_review_scan/checks.sqlite3`, outside canonical art/ and full backups. It
stores candidate descriptions plus a separate LRU of small comparison previews
(8 MiB / 128 images, at most 512 KiB each), never audio. Preview eviction cannot
remove suggestion metadata or canonical art; final replacement still uses a
separate guarded fetch. Context stamps bind
entry/image SHA, artist/title, exclusions and matching-policy version; unchanged
checks (including misses) resume without online work. A fresh pass advances its
epoch without touching curated data. Failed requests are not cached as misses;
five consecutive failures pause with resumable progress. Kept/accepted decisions
remain canonical art state and are backed up. On a cached proposal, only its
small preview is fetched if absent from the bounded memory cache. New online
checks are paced; cached and saved decisions incur no delay.
Reopening first validates and restores all saved contexts locally, including
ready comparisons later in the list, before starting any online search. Only
uncompleted or changed contexts enter the pending queue. Progress counts this
remaining queue and shows saved/reviewed totals separately. A decision made while
the queue runs is checked again before its row can start a request. Local image
SHA validation and exact publication baselines remain mandatory; resuming is not
authority to reuse changed bytes. No matching-policy bump is needed for this
scheduling/presentation change.
`desktop.reviewplayer` adds compact local audition controls beside the decision
buttons, reusing PlayerBar/CoreAudio through a read-only snapshot and clickless
pause/resume/seek APIs. The compact snapshot includes actual native playback,
so a transient PAUSED/refill report cannot leave running audio with an inert
Play button. Resume can cancel a pending pause fade. Selecting a row alone is
silent; moving rows or closing
review cancels owned playback, including pending grid/source changes. Review
audition does not capture/advance playlist autoplay, and shuts down before native
teardown. Keep/Replace/Next retain the next visible position below the selection;
reaching the end does not implicitly wrap to the first remaining suggestion.
Review normalizes SoundCloud source identity across tracking links and mobile/
web aliases before fetching a preview. A bounded coarse colour/layout comparison
also suppresses equivalent resized/recompressed artwork. These checks do not
infer publisher trust. Keep/Replace persist per-track protection, not a learned
global ranking model; deliberate cross-song artwork choices do not establish
song identity.
`ArtCache.apply_review` checks exact displayed metadata/image SHA under the state
owner and atomically preserves one original per changed track in `art/review/`.
Backend recovery survives restart and refuses changed/corrupt state. An accepted replacement
also stamps its new cover/context as reviewed in the same transaction. Browsing/searching never
publish images; manual artist overrides remain authoritative.

Matching uses the same `metadata.editor_credits` parser as artist derivation:
named Edition/Version credits outrank original tags, generic Deluxe/Radio/etc.
descriptors do not. Unicode identities use normalization/accent folding. The
requested version must agree; the original or a different named edit cannot
receive an own-artist rescue. Exact song cores of three or more letters can
match one contributor of a collaboration. Review additionally admits labeled
possibilities supported by an artist word in the uploader identity and strong
song-word evidence (including colloquial in/ing forms). These never enter
unattended selection. Queries include version creators and bounded wording
variants; no account-specific aliases are hard-coded.

Pure `art_sources` rules and core-owned `art/source-policy.json` fence every
discovery path and new publication. `canonical_image_path`/`image_path` quarantine
historical excluded-source art, while `cover_snapshot` can still read its exact
bytes for recovery. The authorized excluded-source policy uses only strictly
matched artist-profile images (confidence >= .90, profile URL), captured through
the ordinary artwork worker ownership. It compares the old snapshot again, so
intervening manual changes win. Original bytes/index and any previous review
recovery are atomically preserved under `art/source-recovery/`. Intentional
profile defaults carry `fallback=excluded-source` and effective artist identity;
they do not remain Suggested issues or get cleared by normal re-scans. Missing
profiles leave excluded images hidden. No source audio/rekordbox write occurs.
New uncached artwork still uses `store_auto_if_unfetched` to avoid overwriting
intervening manual edits/misses/blocks. The runtime provider receives immutable
source preferences and never writes them. Custom exclusions can be reversed.
This is not complete reseller detection or proof of account authenticity.

## Persistence

Library curation lives in `library.json` plus its referenced `.state-records/`
payloads (overrides, playlists, imported records, hides/removals, settings) at
`~/Library/Application Support/LowFreqLabs/Soundbox/`, keyed by stable logical
track IDs. Tested source binds native curation to source path/library and native
ID/UUID pins through `settings.source_curation`; playlist pins also retain type
and smart-rule identity. Source selection stages curation and selection in one
save, rolls back exact RAM owners on failure, and preserves separate source
partitions. Reused logical/native IDs cannot silently adopt another song's work.
`state_records` stores `analysis_results`, `pending_prep_edits`,
`native_analysis_jobs`, `source_truth_targets`, `source_truth_alias_targets` and
`source_truth_artist_origins` as individually
hashed records; the small versioned manifest preserves exact map/queue order.
Legacy full JSON remains readable and migrates on a successful save with heavy
records. The shared state journal publishes changed records, manifest and verified
old-record retirement together before acknowledgement. Unchanged records retain
their private validated ownership; mutable incoming data still receives exact
comparison and changed records receive validation, detachment and encoding.
Fresh manifest/record identities fence stale writers and external edits. The
64MiB logical-state ceiling remains; unknown evidence is never swept.
Use `read_state` for logical reads and `portable_state` under the area lock for a
complete standalone JSON snapshot. A canonical manifest alone is insufficient
for copying or downgrading; older readers reject its sentinel. Rotating history
and full exports materialize complete standalone JSON. Atomic saves, rotating
backups, corruption auto-recovery, one-click full `.zip` backup/restore
(library + history + art cache, including managed default photos). Track-specific
art stays in `art/index.json`; the independent `art/defaults/` pool holds
content-addressed baseline JPEG copies plus its assignment seed/map. Successful
track covers/avatars take precedence. Naturally missing and explicitly blocked
tracks receive a deterministic-random, exact-balanced pool assignment without
changing their underlying cache state. Rebalancing preserves valid assignments
and moves only the minimum required, preventing unrelated device-art drift. The
settings payload also stores the last
closed window geometry, table columns, and Playlists/Devices splitter
heights plus waveform zoom and beat-jump mode; startup restores
the outer geometry before applying inner pane sizes.

The source-validated metadata migration freezes effective Title AND Artist from
one immutable SoundBox snapshot before either native field can change. Cleanup,
derivation, aliases, manual overrides and legacy visible Track Info overlays all
contribute; native identity/path checks supply concurrency guards, not replacement
curation. A durable build descriptor and sharded inputs precede worker planning.
`settings.source_truth_migration` tracks bounded pair expansion into
`pending_metadata_edits`; unchanged frozen target records are reused across ACKs.
Both intended fields stay visible until their exact operations acknowledge, and
later edits update the retained intended pair. Missing identities remain frozen
and need attention; they cannot be resurrected or discarded by starting a new
migration. General exact-library v5 Title/Artist activation and the preserved
backfill were separately approved; the source-scoped scheduling pause does not
revoke that authority. Queue drain and complete native/curation verification
remain required before calling migration finished. HANDOFF records delivery.
The original profile sequence below preserves its narrower scopes: legacy v1/v2 profiles
retain their ASCII/analysed/empty-album scope, with v2 also preserving synced0/1
Content. Independently marked v3 adds Unicode, title-only album preservation and
read-only reuse of synced0/1 artists without a remote USN, preserving NULL local
artist clocks too. Analysed0/105 and NULL/0/1 metadata revisions follow the native
integer-conversion/increment sequence, with independent static and component
proofs. Those profiles do not themselves admit remote/status257 records, artist
edits with albums or unproved orphan changes. The narrowly approved DESIGNER v2 pair was written and fully
verified; Sonny confirmed both fields after reopening rekordbox himself. This
grants no bulk migration authority.

The independently marked v4 extends this with separately captured old-artist retirement:
local DELETE or synced status2/deleted1 history, one cleanup counter step after
the Content-field step and before its revision step, plus optional earlier new
artist creation. Actor identity/opaque columns and unrelated unused definitions
stay intact. Its request extension contains an independently captured retirement
clock; legacy profiles refuse it. Artist UPDATE permission is limited to the
four tombstone/clock columns under v4/v5 only. Full profile-bound recovery and
compact-ACK reconstruction remain mandatory. V4 itself supplies no album/cloud
permission; the later separately accepted v5 scope is described below.

The `alias_intents` path shares one successor-generation path
for single/bulk alias merge and unmerge and accepted existing artist suggestions.
It performs no provider request. One save retains the changed curation, immutable
paired inputs and source-bound origins before worker planning. Active requests,
operation IDs and receipts remain unchanged; an unstarted successor coalesces
later gestures. Only one row pair enters the bounded native FIFO at a time, so
a batch is not limited to256 accepted songs. Missing predecessors retain their
full frozen derivation context; only unfinished or unresolved intentions carry
forward. Completed old-path rows do not become new unresolved work.

Origins retain the unaliased automatic artist and title plus each field's last
acknowledged native value. Planning distinguishes pre-change trust from desired
artist overrides, preserves raw NAME derivation for newly trusted cross-track
rescues, and removes only accepted rows' conflicting legacy artist overlays.
Both unedited display counterparts freeze before a gesture; later explicit
fields supersede only those fields. Blank resets use the retained automatic
values after native publication and restart. Redundant title overrides can hold
the identical desired native title against another cleanup pass; their
`title_shield` origin marker distinguishes generated protection from a manual
title choice. New per-origin scalar provenance also captures raw title/artist,
the title base before cleanup, local edit tokens and exact ACK ownership. It
retains fulfilled manual bases for reset/unmerge while retiring only fulfilled
title masks; naturally different rendering may retain the same native-valued
title mask. Manual artist maps remain bound to their exact own ACK against later
editor-credit/rescue/global-context changes. Exact older Track Info title/artist
overlays retire in that same ACK save, preserving other fields/newer inputs.
A later independent native value supersedes old ownership, so a return to
an old spelling cannot reactivate it. Raw NAME guesses remain untrusted even
after their inferred artist was materialized into the native Artist field.

Existing title cleanup controls stage their policy and frozen successor together
before presentation. Fresh native refresh stages sparse ACK reconciliation in
the same save as source selection/curation, then publishes the Library. The
existing read worker compares detached scalar native inputs retained during the
browser's existing inventory pass. Dirty-row derivation reuses the prepared
display/unaliased registries, freezing their lookup data in its TaskOwner.
Only a changed global canonical/trusted-name context expands the dirty set;
cue/tag/BPM-only changes and unchanged later refreshes do no metadata work.
New registration readback supplies its exact binding IDs. Every process's first
verified native read checks all rows even with a warm cache, covering a crash
before a derived plan became durable. A browser cache grants no publication
checkpoint. Frozen paired targets save before source admission, and planning
participates in source switching, quit and export barriers. Existing failed
saves retain attention instead of being hidden behind a finishing message.
Initial SoundBox curation remains the authoritative migration input; broader
native profiles and guarded delivery remain separate unfinished work.

Generated title protection requires the exact current source/local/native
ID/UUID/path binding with no ambiguity or binding issue. Retained unresolved
targets and late builder results cannot change a replacement song's title map.
Direct imported-song edits may protect their unique provisional binding before
registration; the existing verified registration transfers provenance without
rebinding an already captured request.

Origin relocation moves one record, never a queued request: exact source,
library, logical/native ID and UUID must be unique, with no binding issue and
both current native fields equal their last acknowledged values. Ambiguity or
independent field changes do not adopt old provenance. Artist-acceptance dialogs
also pin native and local paths before presentation. Pending work for another
source rejects new metadata gestures while permitting return to its source.
Maintenance/publication holds rearm without admission; shutdown stops wakeups.

Alias graph resolution reaches a stable terminal without a depth cap. Legacy
cycles use a deterministic representative, and folded-key conflicts no longer
depend on JSON key order. Browser-cache schema4 retires pre-change derived rows.
The November13 offline comparison reproduced all3449 frozen current/retained
Title/Artist pairs byte for byte. Focused fixture and peer validation remain
separate from the full gate and runtime handoff; HANDOFF records their status.

The separately accepted metadata v5 profile covers existing-album artist saves through
an exact unique active Album/name/UUID and companion-artist lookup. It preserves
the name, surviving roles and opaque fields, with a persisted Album clock and
bookkeeping counter before scoped artist retirement and metadata revision.
Only its artist authorizer adds the four measured Album UPDATE columns. Active
artist lookup ignores same-name deleted history while retaining those rows.
Observed cloud status257 rows preserve exact remote USNs and synchronization
flags. V1–v4 retain their request/journal semantics; cloud-album transitions and
remote orphan retirement remain guarded. No generated/copy proof enables source
authority. HANDOFF records native acceptance and actual installed identity.

Metadata shares the one native source worker/lock/journal, with its own exact
authority and recovery format. Verified native readback, queue removal and pair
progress save atomically with the bounded `metadata_receipt_acks` retirement
ledger. A separate TaskOwner then compacts only acknowledged full semantic
receipts into permanent exact-operation markers, using their recorded proof and
marker hashes; it never opens the source DB. Pending/error proofs stay intact.
Restart drains retained retirement work before more metadata admission. Restored
older pending operation IDs reconcile markers against fresh target/artist
identity, value and counter guards and never replay SQL. Restore/shutdown drain
the metadata TaskOwner before state publication or teardown. These contracts do
not broaden native-field acceptance; current installed provenance is in HANDOFF.
Completion verifies both frozen fields, including a companion that initially
needed no write. A changed companion retains the original request and operation
ID with attention. Explicit Retry reserves a fresh existing-field repair; the
idempotent original ACK and helper insertion save atomically without exceeding
the256-job FIFO. A later explicit same-bound companion takes precedence.
An acknowledged generation cannot advance its cursor over a mismatched pair;
Retry captures new field operations while preserving old receipts. Snapshot-only
startup cannot advance that final check before native source verification.
`state_repository` owns schema validation, recovery, rotating history and saves,
retaining unknown extension namespaces. Invalid shapes enter recovery just like
invalid JSON; without a usable backup, normal saves remain disabled. Save failures
return false at the UI adapter and retain a visible window-title warning.
An identical candidate first compares exact JSON types, map order and values
against the validated saved document, plus its current filesystem identity under
that same lock. A hit skips validation copies, encoding, backup/publication and
fsync; changed candidates validate and encode their small inline state and
changed per-song records. Equality still visits mutable incoming records, and
fresh record epoch checks add no-op overhead. Missing/replaced state or restored generations cannot
reuse this shortcut. Zoom/jump/display/autoplay
preferences debounce for 400 ms; curation, backup/restore and shutdown save
immediately and cancel any pending preference timer.

`state_io` serializes library/artwork/backup access within the app. The executable
also holds a `QLockFile` for its AppData directory to reject a second writer app.
`artwork` owns local ArtCache/results/defaults without importing online discovery;
`artfetch` retains compatibility aliases. Artwork mutations stage image/index/default
changes together, publish under the area lock, and revert memory on failure.
Mutation detaches the top-level index and photo/assignment maps; changed rows are
replaced, and untouched nested metadata is shared read-only. New in-place nested
mutation would violate rollback ownership. Public cover/review snapshots remain
defensive copies. Artwork eligibility observes exclusions once per worker pass;
publication still checks the fresh policy and exact cover baseline under its owner.

Backup copies a consistent snapshot under that lock, then compresses outside it
and atomically publishes the zip. Restore validates bounded archive/folder input,
rejects links/unsafe members, stages the legacy merge of library/history/art and
retains originals in a write-ahead journal through publication. Startup rolls back
an interrupted transaction; committed or rolled-back journals are atomically
retired before recursive cleanup. Failed rollback preserves recovery material and
blocks further publication. This is a recoverable multi-path transaction, not a
single filesystem-atomic swap visible to arbitrary external readers.
The separate historical Graveyard identity archive is not yet included in a full
backup; the [critical ledger](critical-audit-2026-11.md) retains that known gap.
Do not infer lossless native-deleted identity recovery from a library/art ZIP alone.

The backup/restore UI temporarily disables editing, closes background admission,
drains existing direct-window writers while Qt events remain alive, saves the final
state, then starts its worker. Result handling/reload happens after `finished` and
join. Restoring increments the area generation so stale repositories/caches cannot
write. Active device operations prevent entry. Derived waveform/device cache
directories are excluded; saved analysis records, drafts and durable intentions
inside the logical state remain part of the canonical backup/restore. Restore
accepts portable JSON or a complete record-store directory, archives damaged live
physical state when it cannot materialize, and preserves unverifiable records.
Only verified formerly referenced records retire with a successful restore.
No live AppData
fault injection is used for validation.

The sibling `stock_artwork/` directory is disposable derived data, not curated
state: it can always be regenerated from the canonical baseline JPEG art cache.
A completion inventory binds source, derivative-manifest and pixel-file stat
identities to the format profile. Warm hits skip preparation and pacing; changed
or absent files receive ordinary preparation. Selected sync still verifies hashes.

## Build / install

    ./check.sh                 # native deck/process/CDJ builds and complete gates
    ./build_native.sh          # deck + process-guard dylibs
    ./build_app.sh             # PyInstaller → dist/personal/Soundbox.app (self-signed)

`./build_app.sh public` separately writes `dist/public/Soundbox.app`.
The tracked `Soundbox.spec` delegates profile content to
`scripts/build_composition.py`; the shell only drives compilation, stamping,
signing and artifact inspection. `requirements-core.txt` excludes `yt-dlp`;
`requirements-personal.txt` adds it, and `requirements.txt` selects the full
personal/developer environment. Artifact checks inspect the executable PYZ and
bundle files for provider exclusion and fixture leaks. Signed profile/module and
source-fingerprint fields accompany Git provenance; dirty builds remain labeled
as such. `--isolated-check OUTPUT_DIRECTORY` uses fresh temporary state, disables
live library/device discovery and production logging, renders and exits. This
checks packaged startup without testing real sources/devices.

Python 3.14, venv at `.venv/`. `build_app.sh` uses the stable local
`Soundbox Self-Signed` identity when present so macOS can retain TCC grants
across rebuilds; it falls back to ad-hoc signing. The app is not Apple-notarized,
so first launch may still require right-click → Open. Notarization is a public
distribution concern (see the ROADMAP public distribution gate). Before signing,
the build stamps the project
version, Git revision, commit-count build number, and clean/dirty source state
into `Info.plist`; `scripts/app_provenance.py /Applications/Soundbox.app`
checks the bundle signature and its declared revision/version against a clean
checkout. A dirty checkout or bundle, bad signature, or mismatched metadata
fails verification. This is signed build metadata captured at stamping time,
not independent proof that packaged code was compiled from those sources.
Both stable and ad-hoc signing happen after all plist edits. This strict
clean-HEAD helper can legitimately reject an already verified installed build
whose signed base is older/dirty or whose root has the saved Finder/Dock icon.
Use HANDOFF's exact frozen-source, packaged-code, signed-Contents and normal deep
signature evidence for that installed identity; do not restamp, reinstall, strip
custom icon metadata or re-sign a running app merely to satisfy the helper.

An explicitly authorized installation is a state-preserving handoff. If SoundBox
is running, Sonny quits it normally; the agent sends no real-app input or
termination. Freshly check absence before staging and before the swap. Copy/hash
the current library, all accepted authorities, art indexes and custom icon facts
into private preservation evidence. Stage the tested bundle under a new absent
name, verify its signature and exact Contents, then move the previous app into one
temporary rollback directory and install the stage. Restore the old bundle if
the stage handoff fails while the application path is absent, including a receipt
write failure. Verify installed signature/Contents/icon and unchanged live-state
bytes before an authorized normal relaunch and startup inspection. Retain that one
previous app only until verified user acceptance, then retire it; do not accumulate
app rollback bundles. Musical originals and transaction recovery remain protected.
Never use `rm -rf`, replay an older installer, or write/restore
AppData merely to replace the app; fresh read-only preservation copies are allowed.

## Safety contract

The synchronized orientation is in `AGENTS.md` and `CLAUDE.md`; detailed rules
are in [runtime contracts](runtime-contracts.md). Per-write commentary remains at the top of `soundbox/sync.py`.

`device_identity.remember_identity` enriches read-only dashboard snapshots from
`settings.stock_ipod_identity`, keyed by volume UUID/library persistent ID.
Original provenance persists; fresh observations win and serial conflicts reset
the association. This cache cannot authorize writes or identify a new mount.


Explicit composite Repair plans missing manifest-owned Rockbox audio and per-track
artwork as verified new copies when the guarded destination is absent. Normal
Sync trusts retained manifest inventory and does not look for missing files.
Existing occupants, symlinks,
and inaccessible paths are not treated as missing; commit rechecks vacancy and
rollback is intended to retire only transaction-created copies. The current
copied-media rollback still has a replacement-ownership gap recorded
in the [critical ledger](critical-audit-2026-11.md); session/cancellation fixes do
not establish safe disposal of a foreign replacement at a copied pathname. A fully
reconciled stock library with no semantic changes retains its original iTunesDB bytes even when
the disposable stock shadow is absent.


The bounded composite drift poll treats source size/mtime changes as Sync-needed
hints, but not ctime alone: cloud eviction/hydration and filesystem metadata edits
can change ctime immediately after a successful commit. This UI status is not a
content-integrity certificate. Source proofs/receipts still include ctime.
Normal composite Sync also retains unchanged committed contracts across
ctime-only drift, without renewing their
original proof or manifest timestamps. New/relocated/changed sources and actual
payload acquisition retain exact-byte verification. Verify checks device
references/sizes; Deep Verify also hashes managed files; Repair explicitly audits
inventory and restores missing owned Rockbox payloads/art. None of these modes
turns a clean badge into a fresh source-content integrity certificate.

Playback and iPod management are independent. Device discovery and ordinary
status polling must not check player.is_playing as a suppression condition.
Only device operation ownership pauses competing device reads/writes. Reconnect,
Rockbox tag-cache updates, unrelated firmware preferences and ctime-only cloud
metadata changes do not themselves create managed-library Sync drift.

September 27: the actual reconnected generation-60 device had no managed library
or playlist drift, only the absent unsupported `sort playlists` setting. Rockbox
4.0's [settings table](https://raw.githubusercontent.com/Rockbox/rockbox/v4.0-final/apps/settings_list.c)
does not define that key. SoundBox no longer inserts or requires it. This corrects
the prior documented assumption; no device configuration was rewritten for the fix.

Post-sync polling is held through owned finalization and any actual attention
dialog. Quiet completion releases the hold immediately so subsequent SoundBox
edits can update drift without reconnecting.

### Playback startup readiness (October 8)

Native CloudStorage admission preserves POSIX read errors before AVAudioFile can
obscure them. One-byte probes run only on the decode thread; ENEEDAUTH gets at
most two cancellable three-second waits. This is transient readiness, not a
content proof. Failed audio sources can reopen on Play while resident waveform
buffers remain intact. PlayerBar cancels pending transport/cue/gain intentions on
error; the bottom status exposes the cause. Visual prediction is bounded below
by the current Play/seek frame until its CoreAudio presentation time arrives,
preventing the initial backward jump without changing steady-state rendering.


Grid editing uses `desktop.grid_controller` for immediate RAM rows, ordered
250-ms/one-second grouped nudges and discrete FIFO saves. It shares the tag/cue
TaskOwner and recovery lane. A generation-bound native grid setter updates only
geometry/bar counters; late analysis retains the newer grid/cue overlays. Saved
rows update V5 metadata and one browser BPM row, preserving waveform/audio state.
Old preparation publications are fenced before source saving, off the GUI thread.
Known grid recovery runs before source snapshots. Unknown payloads fail closed.
Normal shutdown flushes accepted grid intents before draining the shared owner.


The prep Q button stores `settings.quantize` (default false) in SoundBox state.
PlayerBar finds the nearest current grid row by binary search over its existing
beat list; the GUI captures the resulting cue timestamp before enqueueing.
No native timing, database protocol or source Quantize preference changes.
Existing cue playback remains immediate and unsnapped. State load/restore updates
both the button and player without re-saving or moving transport.
Six unassigned actions in the shared keyboard catalog dispatch the five grid
operations and Q through their existing admission paths. BPM Value has no action.
Paused bar-start capture reads the visible waveform cursor after loaded-source
guards; playing capture uses fresh native time. Neither path quantizes grid edits.
Grid intents may enter the same FIFO before the initial source probe completes.
They retain track/UUID/path and captured milliseconds. A RAM-only base previews
the shared row arithmetic immediately; it cannot reach Library/cache/source.
The head requests its probe before saving, requires that loaded base to match
the source grid exactly, and validates native metadata. Failed checks cancel the
tail and restore its base; shutdown drains admitted probe-bound work. Provisional
doubling omits final half-beat extrapolation until native end bounds arrive.
Manual-BPM terminal-row ambiguity similarly stays provisional in RAM. The source
worker alone may resolve `AmbiguousGridEnd` using `pcm_endpoint`'s bounded PCM
WAVE framing proof: exact integral milliseconds, native-bound agreement, stable
canonical file identity and header digest. No audio payload is read or decoded.
The optional journal proof is revalidated before publication, commit and recovery;
unsupported endpoints fail closed. Exact-EOF inclusion matches the October28
STOP CAPPIN native control. Ordinary edits retain their existing source reads
and do not open audio.

MainWindow places the cue panel beside both header and deck rows; standalone
PlayerBar instances keep their embedded cue panel. The title frame shares the
waveform's right edge, and the first grid button shares its left edge. My Tags,
Settings and Search occupy the prep strip's right end, with Search beneath the
cue panel. The wordmark retains its original 270×55 slot.
The logo/title gap equals the left inset. MainWindow mounts the transport deck
beside both waveform and prep rows; standalone PlayerBar keeps its embedded
transport. The default 1 Player dropdown exposes disabled 2 Player/Simple/Browser
placeholders and stores no setting or mode transition.

October30 accepted ordinary playlists:
`rekordbox_playlist_plan` is a pure row/counter planner, `_xml` preserves lexical
native tokens, and `_store` stages encrypted DB/XML pairs under the shared native
edit root/lock/journal. Source SQL is limited to playlist/membership targets and
localUpdateCount; exact before/after affected states govern recovery. The separate
explicit library/schema/version authority was provisioned after11 exact native
replays,12 actual CRUD operations and Sonny's native inspection/normal quit.
`desktop.rekordbox_playlists` persists immutable FIFO intents before admission to
the Tag/Cue/Grid TaskOwner, waits for closed native apps, verifies source saves,
and persists renewed retry identities/clocks before publication. A committed
operation replays unchanged. Unstarted intents survive restart. UI close/Cmd+Q
keeps the app, input and pumps open while captured import/detection/source saves
remain; the next Quit proceeds when they finish. Explicit teardown still retains
unstarted durable intents. Durable matching-source/UUID delete intents project
onto the sidebar immediately without changing the verified catalog/revision;
source failure/discard restores the newest catalog presentation. Persisted create,
rename and membership intents also project immediate feedback, including nested
pending creations. Collection UUID pins retain affected native songs
without granting registration or analysis authority. Raw playlist catalogs/clocks
and empty folders travel in cache13; older snapshots paint read-only until verified.
Bounded transaction envelopes compose at most64 accepted create/append/remove
commands and16384 memberships through the same writer/lock/journal/paired backup.
Exact child identities and clocks persist before admission. Pure row/counter
chains and initial/final revisions reproduce sequential DB/XML effects; source,
authority, payload and closed-app guards stay fresh. Immutable XML parsing and
one instance-local verified semantic reconstruction may be reused within bounded
lifetimes; registration and reorder never enter these envelopes.
Catalog arrays encode bounded rows. CDJ capture also waits for durable playlist
intents, rebinds native selections by PID, and rejects deleted selections or source
changes before export. The ordinary backup-retirement pointer binds exact
committed receipts and resumes deletion of only older completed DB payloads;
pending acceptance/recovery retains required full payloads. Completed diagnostic
duplicates retire under the November10 retention rule; permanent compact proofs
remain. Scope: [prep guide](analysis-playlists-next.md).

Live Tag/Cue/Grid backups use `rekordbox_write.encrypted_backup`: a stable raw
DB/WAL copy, one encrypted checkpointed copy with SQLite integrity/identity
checks, and existing exact-hash/fsync/recovery publication. SQL still opens only
private copies for this capture. `OfflineWorkspace.capture` retains its separate
full semantic inventory and editable working copy for research proofs; live
backup creation does not need those Python allocations. Source SQL/publication
and recovery formats remain unchanged. Fresh closed-app checks query exact
rekordbox/agent process names through the OS, without a Python process walk or
liveness cache. Library-cache serialization stays atomic but encodes/writes
bounded records so the C JSON encoder cannot monopolize the GIL for a full library.

Native manual BPM uses the same immediate grid/FIFO path with a separate acceptance flag
on library and track-bound authorities. Its native protocol additionally owns the
primary contentFile hash/size/revision and AnalysisUpdated. Whole-row recovery
binds those changes with DAT/EXT and SQL. Enter captures exact hundredths plus
track identity; browser row confirmation follows the saved signal, never RAM
preview. The player layout field uses a rounded custom painter with an inset
chevron and the shared Qt choice popup; transport row/column gaps both use 5 px.

October30 upstream tuning: [the audit](rbxport-reference-audit-2026-10.md) pins the
current public source and compares analysis options and waveform semantics. The
3BAND overview now integrates independent existing PWV7 layer profiles with the
same seven membership colors as the main lane. Its former dominant-band function
is removed. No cache/schema/clock/transport change is required. Native before/after
captures preserve all39 checked main drawables across three modes/13zooms. BLUE
and RGB palette/geometry and the accepted 3BAND main lane remain unchanged.


Native playlist presentation now derives create, rename, append, remove and delete
feedback from persisted FIFO requests. The view retains generated target identities,
exact order and the verified base revision; source catalogs and normalized playlists
remain unchanged until commit. A newer source revision, failed head or foreign
library stops projection. Retry/discard rebuild against the current source. Sidebar
and browser use only this view; native caches and device capture retain the verified
source and existing save barrier. Nested pending creations can receive later edits;
background completion no longer steals navigation to a newly created playlist.


### Ordinary initial saved-analysis publication

The installed automatic path is `native_analysis_plan` + `rekordbox_analysis_store`,
with `desktop.native_analysis` coordinating persisted intents on the existing
Tag/Cue/Grid/Playlist/Registration worker. The separate analysis authority binds
library/schema/version and accepted component profile. It permits initial full
BPM/Grid+Key and fresh waveforms on bare stereo PCM16/24 WAV44.1/48kHz, with no
gain, cue, audio, playlist or XML mutations. Unsupported/existing musical states
fail closed rather than being overwritten. Native applications remain closed at
write guards; owner waits back off and unstarted intents survive explicit teardown. Record,
logical/native identity and prepared plan must match exactly before source SQL.

The separate `native_mp3_analysis_plan` + `rekordbox_cbr_analysis_store` domain
admits initial full saved grid/key/waveforms only on bare unlocked MPEG-1 CBR/
no-Info/Xing stereo44.1kHz MP3. Its own accepted authority, plan/journal formats
and retention anchor share the existing worker/lock/root. Independent frame
scanning constructs PVBR and verifies renderer sample count; native Length and
all musical data outside the accepted components remain preserved. Old WAV jobs
and wire formats keep their original profile. VBR/Info/gapless/mono/48kHz and
replacement remain separate gates. See [MP3 evidence](mp3-shared-analysis.md).

Prepared WAV/CBR loaders use `immutable_plan_proof` only after fresh raw record,
path/authority/profile and ALL payload digest checks. Its4entries/30seconds
retain proof keys, not plans/connections/live observations. The desktop checks
`store.plan` against the durable job rather than loading a second proof. Complete
source snapshots, publication guards and readback remain fresh. Registration and
locator use one exact immutable-pair proof per store after fresh record/source/
payload checks. Collection completion uses one fresh full library observation
and strict binding projection, saving bindings and intent completion atomically.
Durable prep reuses at most8source-inode/native-ID/UUID/path/authority-bound cue
handles; every actual snapshot/apply retains its guards. See the measured
[native command audit](performance-audit.md).

The helper's `saved-analysis` command captures no detector decision: it receives
saved beat rows, decodes only an explicit private local capture and renders the
accepted native packet profile. The first two real-song accepted packet sets are
byte-identical to the earlier explicit-reference renderer. Source publication
stages a directory before its narrowly authorized DB commit, retaining encrypted
paired DB/XML and packet evidence. Shared journal recovery verifies exact prefixes;
a wholly unstarted stale plan can renew on current source without restoring old
native data. Retention keeps the first ordinary reference and latest full pair;
confirmed duplicate/older payload retirement is exact-hash checked and resumable.

Only verified source readback updates the finished Track, cue identity, browser
row and current RAM grid. Expected derived grid metadata may be promoted while
all waveform payload bytes remain exact; different/newer manual data is retained.
Captured detector records and manual drafts remain provenance and are never used
as source authority. Existing analysis replacement/other codecs require their own compatible
profiles. The initial-analysis payload excludes cue drafts; active prep transport
queues them separately through the accepted cue API after analysis exists. Every
packet beat number/tempo/time must equal the complete saved grid and endpoint;
the headline BPM does not require every adaptive row to share one tempo. Failed
songs retain their own operation IDs while healthy imports continue. The legacy
uniform-grid refusal is revalidated once without an input veto or repeated loop.
Ordinary BREATHE publication passed native acceptance; its dated actual-source
readback is recorded in [the accepted-stage plan](analysis-playlists-next.md).
HANDOFF records the current release and retained private evidence roots.


Captured-import and ordinary native prep intentions use `soundbox.prep_edits` and
`desktop.prep_controller`. Grid intentions retain StateRepository persistence
before presentation; cue-only gestures publish an exact usable overlay first
while their owned canonical admission and native saves finish in the background.
Their per-track working component rows are temporary intentions, not source
authority. The existing TagController lane prepares an exact accepted Cue/Grid
request, persists it, then applies it and verifies component readback. Prepared
requests reserve source priority from controller construction, including Tags and
central Cue admission, until a matching committed native receipt is acknowledged;
unknown receipts/source switches retain intentions and stop publication. Unrelated
busy work and background-analysis errors do not block new local clicks. Ctime-only
source drift retains gestures immediately against the original epoch/SHA while the
existing worker performs a pinned exact-byte check; only verified bytes renew
identity. Actual changes preserve intentions and fail publication. Late callbacks
and continued gestures recheck source/library/path bindings, including legitimate
first native-ID acquisition, before updating presentation or appending a gesture.
Transient pre-intent refusals do not latch an enduring input veto. Initial
analysis strips cue drafts and
acknowledges only captured grid-operation IDs, preserving newer edits. No new
analysis codec/replacement or native deletion authority follows.

ColorButton preserves QPushButton’s idle rendering while splitting swatch and hex
hit targets. Hex editing commits validated six-digit RGB through the same settings
transaction as picker OK. PlaylistFolderTree’s delegate opens plain-name inline
editors; the configured browser.rename action dispatches sidebar focus before
table/catalog rename, with existing local/native durable save routes.

### Native command latency and fresh process guards

The second November2 performance pass audits each native domain independently.
`soundbox.mac_process` loads the separately compiled/bundled
`libsoundbox_process_guard.dylib`; only that function interface is retained. Every
source guard takes a fresh bounded kernel process snapshot and validates its
shape, string termination and own-process identity before accepting absence.
Unknown results use the existing strict pgrep path. It requires no player/Metal
initialization, GUI/Accessibility input or process-state cache. Source guards keep
their exact accepted library/schema/version, media/SQL/recovery checks. C source
and CMake recipes participate in package provenance; all native code/data sections
are compared with tested builds, and isolated startup verifies the fast interface.

`encrypted_backup` still copies a stable DB/WAL pair with hot-journal refusal.
Only self-contained absent/empty-WAL private captures can avoid a second
SQLCipher re-encryption after full integrity/identity/byte-stability checks and
fsync. Nonempty WAL keeps SQLite folding. Analysis snapshots may reuse only the
pure inventory of those exact freshly checked private bytes; every source capture
remains fresh. Locator preparation seeds proof only after archive DB/XML transfers
match known hashes bracketed around their complete proofs. Removal cache hits
freshly verify raw plan/authority/source and all payload/resource bytes.

Prep scheduling distinguishes general ownership deferral from native-owner
backoff. Genuine lane release promotes a delayed wake to the next event turn;
a monotonic native-open deadline prevents repeated source probes until the
existing two-second retry period passes. `desktop.native_timing` logs fixed
phase labels with per-command dispatch, execution and GUI acknowledgement, without
paths or musical values. Unknown durable-FIFO wait is explicit rather than being
reported as zero. See [the per-command measurements](performance-audit.md).

## November3 broader efficiency boundaries

USB view state separates immediate scalar scroll capture from120ms-coalesced
preference notification. Full tree capture still occurs on structural/view changes,
snapshot switches and shutdown; maintenance flushes before its backup/restore
save barrier. Deferred restoration suppresses recording while both positions are
applied. Scroll accent uses120ms idle after ScrollEnd (or phase-less ticks).

State backups may reuse validation only when freshly read bytes exactly equal
the repository's previously validated saved payload. Queue/import/playlist indexes
and artwork observations are scoped to one operation/batch. Prep rollback retains
the exact old references for only the two replaced namespaces. Immutable Python
waveform input bytes are borrowed only for the synchronous native copy; native
ownership completes before the setter returns. Provider LRUs cache only pure text
helpers,8192 builtin strings each. Every source/authority/process/hash/recovery
boundary remains fresh. See performance-audit.md for scopes and remaining costs.

## November3 correctness and remaining-cost pass

Captured audio SHA and returned filesystem epoch must describe one stable
before/opened/closed/after observation even when the expected digest is known.
Canonical redirects reject; one bounded metadata-instability retry remains.
Independent native/USB source captures stay independent. Rendered global MyTag
definitions participate in USB source_revision alongside selected DTO/playlist
proofs. Small USB manifest models and their expected raw bytes share one bounded
capture; settings patches also bind original profile hashes through commit.

Native playlist completion uses separate proof strengths: exact reconstructed
committed catalog edge plus current equality for causal FIFO rebasing; historical
retained pair plus fresh ContentID/UUIDs for Collection retention. Worker-side
folder-delete preflight persists exact descendant pins before writer admission.
Ordinary membership removal also saves a separate local-overlay proof outside
the native request. ACK retires only its captured value for matching playlist
and track identities; later overlays survive. Failed storage restores the prior
map/mask owners, and retry/restart retain that proof. Legacy jobs without it
preserve local overlays. Capture and ACK use selected keys, without another
source query or GUI library scan. Smart-list exclusions need their own native
representation and cannot enter the ordinary-member route by inference.
Captured UUID intentions survive receipt-backed retirement; legacy absent UUID
proof remains guarded. The existing source lane/authority/lock/journal and SQL
scope remain unchanged. Receipt reconstruction executes mutation SQL only on
TEMP copies and grants no source publication.

Acknowledgement COW shares untouched published records read-only and detaches
every affected object. General analysis-queue APIs retain full detached return
graphs; terminal transition avoids duplicate copying/validation. GUI analysis
capture copies only the top map; selected canonical-record detachment happens in
the worker and completion still merges newest drafts. SQL filtering retains table
traversal to preserve exact cue references/ties. Fuzzy matching retains original
asymmetric pair/rank order while reusing matcher indexes and bounded best rows.

Catalog thumbnails retain only64 or the visible row count at display DPR, loading
one visible image per event turn. Closed callbacks avoid widget access; modal
owners drain before deleteLater. Artist bulk artwork actions normalize and
publish once, preserving sequential default assignments and newer real covers.
State publication overlap checks use proper-ancestor set lookup; path/tree/fsync/
recovery checks remain. Optional provider scopes check actual receives on supported
owned SocketIO adapters without bypassing top-level decoding/error methods. Their
cooperative budgets do not forcibly interrupt DNS/pre-response opaque work.
See performance-audit.md for measurements, exact limits and retained tradeoffs.
