# SoundBox architecture and release audit

September 27, 2026. Source baseline: `97fc023`, initially clean `main`.
This is a source audit and implementation plan, not a completed refactor or a
public-release certification. No application behavior or installed bundle changed during the initial audit.
Implementation follow-ups below are separate from that baseline.

## Assessment

SoundBox has useful engine boundaries and unusually explicit device safety
contracts, but it still has monoliths. The largest problem is ownership spread
across the window, workers, dialogs and caches, not simply long files. Recent
device lifecycle work improved one subsystem without completing the same
transition for every background task. Public/personal separation does not exist
yet: online discovery, local artwork state, UI construction and packaging are
coupled.

Keep one shared product. Add narrow optional providers and action contributions,
with the public application fully functional when none are installed. Keep
transaction safety and state ownership in core. The complete user-confirmed feature
allocation is in [release-boundaries.md](release-boundaries.md); the executable
sequence belongs in [ROADMAP.md](ROADMAP.md).

## Scope and evidence

Reviewed the source/module inventory, window and worker ownership, device
coordinator and transaction entrypoints, source capture, state save/restore,
artwork network/cache/UI paths, player shutdown and native callback boundaries,
build/dependency configuration, representative regression suites and current
documentation. This was risk-directed inspection across subsystems, not a claim
that every line or every hardware interleaving has been proved correct.

`./check.sh` passed: native build, pyflakes, engine mypy (36 files), 941 tests,
13 skipped, 14.653 seconds in the test runner. The optional private capture was
not enabled. No live-device transaction, source-library scan, network-artwork
request, installation, commit or push was performed for this audit.

A temporary-directory fault injection into `backup.restore_backup` failed an
artwork copy after library publication. It reproduced a new `library.json` with
incomplete restored artwork; the pre-restore library backup survived. No actual
curated state was used. Remaining findings below distinguish source-confirmed
structure from runtime risks that still need targeted reproductions.

## What is working well

- `soundbox.library` separates domain types from the read-only rekordbox DB/XML
  adapters. Do not replace this with a speculative integration framework.
- Device writes use explicit target guards, pinned sessions, ownership manifests,
  plan/commit boundaries and recovery. Stock/dual transactions preserve different
  firmware obligations; a generic exporter abstraction must not erase them.
- `source_acquisition` captures and hashes local bytes once, with bounded retry
  and one-track staging. Disposable proofs and receipts are not write authority.
- Device worker ownership lasts through `finished`; normal eject is independent
  of library integrity. Main-window shutdown dispatches events while waiting.
- Native audio and rendering stay outside Python's timing loop. The inspected
  audio callback consumes the ring buffer; decoding stays on its own thread.
  Keep the C ABI and sample-clock contract stable during any source split.
- Stable track/playlist IDs, additive curation, local art provenance, cache
  checksums and metadata-only analysis updates avoid destructive source edits.
- Tests include transaction failure injection, property checks, synthetic UI and
  native contracts. The check script reports slow tests and fails visibly.

## Findings, ordered by risk

### A1 — High at audit: unsafe timed shutdown waits (corrected)

`desktop/artpicker.py:ArtPickerDialog.shutdown` and
`desktop/suggest.py:SuggestDialog.shutdown` call `wait(8000)` and ignore its
return. `desktop/player.py:PlayerBar.shutdown_waveforms` similarly ignores
`wait(5000)`. Their workers are owned by child objects/lists, outside the main
window's direct-attribute QThread collection. Thus window shutdown's stronger
contract does not cover all workers.

The artwork downloader allows a 20-second request; search extraction has no
explicit timeout/retry budget in its local options. A timed wait is not proof
that either operation stopped. Source confirms the unsafe boundary; a fresh
crash was not induced. Introduce a common task owner that retains the exact
worker through `finished`, keeps events dispatching, rejects late delivery and
waits safely without freezing the GUI. Include dialogs, interactive waveforms,
backup and optional providers. Test slow operations exceeding the old timeout,
dialog cancellation, app close, replacement and late callbacks.

Follow-up implementation: `desktop.task_owner.TaskOwner` now owns these three
worker families through queued `finished` delivery and final join. Shutdown
closes admission before dispatching events, stops/drains child owners from the
window, rejects late results and closes native resources afterward. Search
socket timeouts/retries are explicit and suggestion pacing is interruptible.
Regressions exercise finished-but-undelivered ownership, responsive dialog
draining, late-result rejection, waveform admission closure and window child
ownership. This does not migrate all task families or finish the device queue;
new providers should use the owner instead of introducing another timed wait.

### A2 — High: restore is a partial overwrite, not a transaction

`soundbox/backup.py:restore_backup` copies `library.json` into place before
history/art, with no rollback of the combined state. The temporary-fixture
experiment reproduced failure after publication. The old JSON backup is useful
recovery material, but does not make the operation atomic or preserve the old
artwork index as a coordinated baseline.

`desktop/settings_page.py:_restore_from_backup` gates only its own backup worker;
it does not quiesce other state/art writers or disable curation. Backup also
reads live changing files and ignores `_save_state()` failure. Concurrent mixed
snapshots are a source-confirmed possibility, not a reproduced user loss.
Stage and validate the complete restore, acquire one state-write owner, retain
exact rollback material, and publish/reload coherently. Bound archive expansion
and validate folder/symlink inputs. Test disk-full and failures at every publish
step, plus a simultaneous artwork completion and user edit.

### A3 — High: artwork persistence has multiple unsynchronized writers

`soundbox/artfetch.py:ArtCache._save` explicitly serves background and GUI
writers, but uses the same `index.tmp`, shared mutable dictionaries and no
serialization. `os.replace` makes a single replacement atomic; it does not
serialize competing writers or provide a consistent multi-file art snapshot.
Errors are silently swallowed. Default-photo updates add another worker path.
Potential lost writes/temp-file races need a deterministic barrier test; this
audit does not claim observed artwork loss. Move mutations/publication to one
artwork repository owner, with explicit failure results and versioned snapshots.

### A4 — High for distribution: personal discovery is part of core startup

`ArtCache` and `ArtResult` live with online fetching in `soundbox/artfetch.py`.
The core window constructs that cache and autostarts discovery for unseen tracks.
Even choosing a local cover opens a dialog that immediately starts online
candidate search. `build_app.sh` and `Soundbox.spec` collect `yt_dlp`
unconditionally. There is no public profile or provider absence test.

Extract local artwork storage/results first, then inject optional discovery.
Guard both commands and startup scheduling, not only visible buttons. Verify the
public artifact has no private provider/dependency and runs with network access
denied. Keep existing curated art and sticky blocks when the provider is absent.

### A5 — Medium/high: device queue consolidation is incomplete

`DeviceOperationCoordinator.enqueue/next_batch` have tests but no desktop callers.
Actual intent lives in `ExportWorker._jobs`, `_dual_sync_queue`, pending view
lists and `_pending_device_operation`. The coordinator does serialize active
heavy work, but is not yet the sole command queue. Generic workers also use their
own cancellation flag rather than the coordinator Event.

Integrate one confirmed-command queue with mount identity, FIFO batching and
cancellation, then remove obsolete storage. Preserve pending discovery and
finalization semantics. Test real adapter wiring, including multi-target jobs,
remount at the same path, overlapping drops, failure, cancel and shutdown.
Do not call the independently tested queue API a completed GUI migration.

### A6 — Medium: real monoliths impede review and isolation

Measured source lines (not complexity scores):

| File | Lines | Responsibility concentration | Useful extraction boundary |
|---|---:|---|---|
| `desktop/app.py` | 6,399 | 276 function definitions including nested callbacks; state, library, playback, devices, art, UI | State repository; library/curation controller; device controller; artwork service; playback navigation |
| `native/SoundboxDeck.mm` | 6,004 | Audio, decode, display clock, renderer, embedded Metal, trace diagnostics, C ABI | Internal audio/clock, renderer/shader and diagnostic units behind unchanged ABI |
| `soundbox/stock_ipod.py` | 3,194 | Planner, renderer adaptation, validation, commit, rollback/delete | Immutable plan types, pure reconciliation, database renderer/validator, transaction execution |
| `soundbox/dual_ipod.py` | 2,991 | Membership adoption, planning, ownership, multi-phase commit/recovery | Membership reconciliation, pure plan construction, coordinated transaction/recovery |
| `desktop/player.py` | 2,459 | Widgets, cue/transport orchestration, waveform workers | Worker ownership and transport controller, preserving native timing |
| `soundbox/track_analysis.py` | 2,264 | Analysis algorithms and V4/V5 record lifecycle | Codec/storage versus analysis algorithm, only if separately tested |
| `desktop/workers.py` | 1,183 | Unrelated task families | Task-owner adapter plus domain-specific workers |
| `soundbox/artfetch.py` | 851 | Network, matching, canonical art and defaults | Core artwork repository plus optional provider |

The stock planner is 1,015 lines; its commit is 530. The composite planner is
785; its commit is 615. These deserve transaction-stage decomposition even
though they are domain-focused. Extract cohesive functions with explicit inputs
and outputs, not arbitrary line chunks, mixins or services holding MainWindow.
Preserve old public imports during migration so each change is reviewable.

### A7 — Medium: some removable-filesystem checks remain on the GUI thread

The window still performs `Path(dev).resolve()` and manifest `.exists()` in
dashboard/deletion paths (`_read_stock_device_view`, `_on_stock_device_view`,
`_begin_stock_playlist_delete`). Browser confirmation setup also constructs and
guards sessions synchronously. Heavy mutations are already threaded, but small
filesystem calls can still block on an unhealthy mount. Move readiness facts to
worker-produced snapshots and perform authoritative revalidation in the worker.
No latency number is claimed without a blocked-I/O test or instrumented run.

### A8 — Medium: state persistence is coupled to UI and weakly validated

`MainWindow._load_state/_save_state` own schema interpretation, recovery, backup,
whole-file serialization and widget restoration. Valid JSON with an invalid
top-level type is not routed through corruption recovery. Saves reconstruct a
fixed set of top-level keys, so future extension data placed there would be lost.
Many callers ignore the returned failure boolean. Whole-file JSON and backup
reads also run synchronously on edits.

Extract a typed state repository with schema validation, preserved unknown
extension namespaces, explicit save outcomes and one writer. Separate UI settings
from curation logically before considering file-format migration. Do not weaken
the required synchronous durability boundary before device writes; ordinary UI
settings can be coalesced independently. Test failed saves and public/personal
round trips before enabling extension state.

### A9 — Medium: no demonstrated unchanged-sync performance acceptance

Existing hardware evidence records generation 59 reading 20,885,090,143 canonical
bytes / 166 proofs during preflight/commit, with 21 transforms. Generation 60's
zero-copy dialog does not measure source reads. Strict proofs legitimately
invalidate on ctime; the cheaper badge intentionally does not. Do not clear or
relax proofs to manufacture a fast no-op.

Measure unchanged local, timestamp-drift, cloud-placeholder, artwork-only and
single-track-edit cases separately. Record canonical bytes, proof/receipt hits,
transforms, destination bytes, staging peak and phase durations. Investigate
duplicate reads using these counters before changing caching or adding threads.
Zero source-audio reads is the stable-baseline target, not permission to trust
invalid proofs after cloud changes. See [sync-performance-audit-2026-09.md](sync-performance-audit-2026-09.md).

### A10 — Medium: optional work lacks one shared resource budget

Device I/O is serialized, but waveform prep, network art, local stock-art prep,
default-photo work, backup and interactive loads have separate lifetimes and
policies. Playback deferral is already present in several workers. Audit their
combined CPU/disk/network use before adding more background features. Provider
jobs need bounded concurrency, cancellation, timeouts and explicit shutdown
registration. Preserve playback/device independence; do not suppress discovery
because music is playing.

### A11 — Medium: release packaging has no separation or artifact gate

One dependency file mixes development/runtime/optional packages. The build script
generates PyInstaller configuration while a tracked spec duplicates collection
choices. Local signing is verified, but is not a public distribution pipeline.
There is no checked-in release profile selecting provider packages and rejecting
personal artifacts. Add explicit dependency/build profiles, a single packaging
authority, packaged-content tests and provenance including enabled modules.
Distribution also needs a dependency/license/notice inventory and a verified
public signing/notarization workflow; this audit makes no legal clearance claim.

### A12 — Medium: tests do not establish every advertised boundary

Engine mypy excludes desktop; the largest orchestration surface relies on runtime
tests. Some UI tests assert source strings instead of executing behavior. Optional
capture tests are skipped without local fixtures. Keep those tests but add
behavioral races for A1–A5 and a public-profile suite without provider imports.
Extend typing to extracted controllers rather than attempting a whole-window
annotation campaign. Public UI parity needs hermetic paired renders. Hardware
reconnect/cancel/eject and real playback acceptance remain separate gates.

### A13 — Medium: documentation overstated completeness

The prior roadmap was 1,113 lines, including a numeric quality scorecard,
completed-phase diaries, obsolete retries, a public fork and a blanket generic
USB block. README still claimed a fourth Rockbox setting after its removal.
The roadmap now contains only active sequence, acceptance and explicit deferred
work; history remains in Git and dated specialist evidence. Initially corrected the README
to the then-implemented three settings; the later user-requested preference
ownership fix removed all sync setting writes. Do not substitute scores or passing tests
for unobserved hardware acceptance.

## Performance verification plan

| Scenario | Measurement and acceptance |
|---|---|
| Warm startup, existing prepared library | First usable paint; zero unnecessary audio cooking and online work in public profile |
| Playback plus library browsing | GUI event latency, native presented-frame gaps and underruns; compare paired runs in the actual app |
| Reconnect during playback | Time to loading row and ready dashboard; no overlapping device readers or playback stop |
| Stable unchanged Sync | Zero canonical audio reads, transforms and media copies; report any DB/manifest write separately |
| Cloud metadata drift | Exact proof/reacquisition costs reported honestly; no weakened byte contract |
| Overlapping playlist drops | One plan per confirmed batch; one payload per canonical source identity |
| Very long track | Staging bounded to one source plus working derivative; bounded cancellation checkpoints |
| Large artwork/library edit | No unnecessary full-library processing; measure GUI stalls and serialized state publication |
| Missing provider | No imports, worker startup, network attempts or lost stored curation |
| Quit during each worker family | No timed-wait teardown; exact-instance completion and event processing |

These are acceptance targets, not new measured speed claims. Do not invent
absolute latency budgets until representative runs establish a baseline.

## Change strategy

Fix ownership and persistence defects before moving large transaction bodies.
Extract local artwork to establish the first genuine optional-module boundary.
Migrate one owner at a time, keeping behavior, state schema, C ABI, device format
and visible layout stable. Each runtime slice needs focused failure tests, the
full source gate and safe installed-app verification. Renderer work additionally
needs the established visual/reference gates. A documentation-only audit needs
neither a rebuild nor a new installed revision.
