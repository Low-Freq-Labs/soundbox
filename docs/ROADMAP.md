# SoundBox roadmap

Updated October 26, 2026. Current priorities and remaining acceptance are below;
completed behavior is summarized separately. Historical experiments belong in
the linked audits. Installed provenance and final validation: [HANDOFF](HANDOFF.md).

## Next sequence — iPod acceptance, library management, CDJ USB export

Sonny confirmed this order on October 26. Build 129 stays available for ordinary
use while the next work is prepared; the completed artwork/Settings release below
is not a new implementation queue. These phases guide product work; the existing
correctness, lifecycle and public-distribution gates later in this document still
apply where relevant.

1. **Resume physical iPod testing when Sonny's iPod returns.** Record its actual
   identity, firmware and managed generation before choosing tests. Recheck
   reconnect during playback, unchanged Sync reads/writes/timing, overlapping
   playlist drops, cancel/retry and normal eject after success/failure. Use the
   existing [device follow-up](#device-follow-up--physical-acceptance-when-the-ipod-returns)
   and [sync audit](sync-performance-audit-2026-09.md); retain generation-58
   recovery until separately reviewed acceptance. No firmware-preference changes
   or restoration of a historical capture is implied.
2. **Refine music-library management through normal use.** Collect concrete
   friction around importing/refreshing tracks, title/artist identity, playlist
   order/membership, missing links, artwork decisions and backup/restore. Verify
   real rekordbox grid/cue edits on refresh/reopen. Preserve saved reviews/manual
   art, source files and read-only rekordbox access. Prioritize observed problems
   with Sonny rather than implementing every item in [IDEAS](IDEAS.md).
3. **Develop native rekordbox-compatible CDJ USB export.** Start from the retained
   [prototype and reference exports](cdj-usb-export.md), then test on Sonny's home
   CDJs. Target normal library browsing and performance features matching a
   rekordbox-exported control, including playlists, grids, hot/memory cues, loops,
   waveform modes and artwork. Audio playing in folder mode fails acceptance.
   Record model/firmware and required library format; test initial export,
   incremental updates, unchanged re-export, removals, interruption/recovery and
   linked-player use before production integration. Current generic USB export
   does not provide this. `/PIONEER` remains protected in the installed app;
   a dedicated writer/ownership/recovery contract must precede any product writes.

The CDJ phase is a requested product milestone after iPod/library work, not an
uncommitted UX idea or a shipped capability. It does not require completing every
optional refactor or public-distribution task before research can resume.

## Completed artwork, Settings and player release — October 26

Current release: Suggested now checks the whole active cover library, including
manual and unrecorded entries. The loading wheel precedes initial results; live
progress continues after the first comparisons become usable. Completed metadata
checks resume from a small disposable SQLite cache; thumbnails remain bounded in
memory and a separate 8-MiB / 128-entry preview LRU. Reopening restores saved contexts and comparisons before online work,
and progress counts only pending checks. Keep and accepted replacements retain exact context decisions. Failed
lookups are retriable, never cached misses; repeated failures pause the pass.
Main controls remain Keep/Replace/Next, All remains optional browsing, and no
scan changes images. Settings order is General, Keyboard, Graveyard, Connections.
A successful source refresh now updates authoritative cues for the loaded track
without audio reload/seek; queued stale manifests cannot put old cues back.
Grid corrections retain the existing metadata-only background refresh path.
Review Pause follows actual native playback even after transient state reports;
quick resume cancels a pending pause fade. The overview compositor preserves
quiet-section hue and uses resident high-precision audio geometry when compact
PWV5 height rounds to zero. True silence remains background, without cache recooking.
Review now has compact local Play/Pause, click/drag scrubbing and time beside the
decision buttons. It shares the native deck and cancels on row change/close.
Keep/Replace/Next advance downward from the selection without resetting to the
first suggestion. Saved queue metadata survives preview eviction.
Review titles support selection/copy. More offers a previewed artist-profile
choice and one Exclude Uploader manager for saved additions/removals and known
automatic blocks. The Undo menu item is removed; original recovery is retained.
The decision audit prompted duplicate-source filtering across SoundCloud tracking
links and equivalent resized/recompressed images. Source exclusions also cover
Plate Selling / plate-role variants reported during SPASS review. Saved decisions
are per-track protection; cross-track learning remains exploratory in
[IDEAS](IDEAS.md), with intentional artwork reuse distinct from song identity.
Version/artist matching, strict excluded-source profile fallback and recovery
remain as documented in HANDOFF. The wider audit is a human review process,
not a certificate that every cover is correct.

Sonny's October 24 report led to source changes for bracket-aware song matching,
accountable label credits, and an optional personal SoundCloud connection that
prioritizes liked tracks. Settings now separates General, Keyboard, Graveyard
and Connections. Keyboard mappings support multiple bindings,
conflict moves and safe held-cue routing; 1–8 use the active cue page, 9 selects
Hot Cue and 0 selects Memory Cue. The five title-cleanup defaults use the requested
concise labels and include exact `(Original Mix)` removal.
The follow-up layout centers the tabs across a single header with Back on the left
and removes the redundant Settings label,
recovering the separate tab row's height for content.
Connections retains its dedicated tab. New SoundCloud setup uses the app's
credentials and a public profile URL; no browser authorization or registered
redirect change is needed. Public likes help rank art matches while general
search continues supplying the broader candidate set.
Keyboard now shows only active-page cue bindings and page selectors; the separate
Direct Hot Cues and Direct Memory Cues sections/actions are retired.
Zoom uses `=` / `-` by default. General balances the columns with Cover Art beneath
Title Cleanup and a content-sized rule list, reclaiming the formerly empty space.
Connections now shows core rekordbox on the left and optional personal SoundCloud
on the right. The rekordbox card exposes active source/status, database location,
refresh, validated source selection/default reset and the existing XML fallback.

The preceding personal release passed the full offscreen/Cocoa release and render
gates. Native synthetic acceptance covers numeric
Hot/Memory cues, original-page release, Space latching and persisted remapping.
See HANDOFF for the exact installed source revision, build and validation. The live personal connection successfully completed
application-token exchange and a public-likes scan on October 25; current count
and restart verification are in HANDOFF. Offline tests do not certify that
external flow or private-account access.
See [HANDOFF](HANDOFF.md), [KEYBOARD](KEYBOARD.md), and
[SoundCloud setup](soundcloud-connection.md) for current provenance and setup.

Historical October 24 cleanup: Sonny requested retaining only the current
installed app. The older rollback,
36 empty historical app shells, duplicate dist build, packaging intermediates and
separately approved 3.5-GB diagnostic archive were removed. Required test/device
fixtures and curation backups remain. About 4.55 GB of logical data was removed;
free space was about 14 GiB afterward. Eleven tracks already lacked analysis caches
before that installation; preparation was paused by the existing low-space policy.
The low-space observations above belong to that installation, not a current
storage measurement. Do not lower the guard or trigger a manual cache rebuild.
The later build-129 installation/cleanup and retained paths are in
[PROJECT_LAYOUT](PROJECT_LAYOUT.md) and [HANDOFF](HANDOFF.md).

The accepted October 9 renderer, background preparation and playback-preserving
replacement remain foundations; the October 26 overview/transport corrections
above extend them. Further renderer retuning and cache rebuild remain unqueued;
device acceptance follows the sequence above. Earlier native-test timing observations remain
unresolved; passing reruns do not establish their cause. Exact rekordbox pixel/
3BAND calibration, profile approximation, cloud-provider latency, device physical
acceptance and public distribution clearance remain separate limits below.

## Completed player and preparation work — October 2–9

- Accepted RGB/BLUE motion and close detail, BLUE color integration, stable
  overlapping render tiles, and quiet RGB color recovery across the encoded
  palette. 3BAND retains its imported rows and existing layer policy. See the
  [zoom review](archive/waveform-zoom-review-2026-10.md),
  [color comparison](archive/waveform-reference-check-2026-10.md) and
  [test audit](archive/waveform-test-audit-2026-10.md).
- First-Play visual position no longer extrapolates before its launch point.
  Native cloud readiness failures explain the cause and permit Play to retry.
  The observed cold AAC/ALAC delay was SeaDrive hydration, confirmed by Sonny;
  a ready waveform does not make offloaded audio local.
- Eligible local legacy waveforms upgrade automatically in the background,
  retaining imported streams, manual grids and DB cues. AAC/ALAC compact detail
  uses native-rate Qt decoding when required. The completed library audit
  verified 2,712 upgrades with no checksum/grid/cue failures and no remaining
  eligible local work. Offloaded/incomplete records retain valid older caches.
  No whole-library invalidation or manual track-by-track loading is needed.
- Rapid loading avoids unused UI-thread detail expansion, redundant cue polish
  and scalar color-row walks; generation checks reject stale display uploads.
  The foreground probe made 72 requests across 24 local tracks. Its measured
  UI heartbeat bound is evidence for that workload, not a universal latency promise.
- Browser loads preserve ordinary playback: playing decks start the replacement
  at its first beat; paused/empty decks stay paused. Rapid replacements, pending
  media/grid, pause fades and momentary versus latched CUE have regressions.
  Previous/next remain explicit play commands. See [PLAYER](PLAYER.md).

## Product direction

One shared macOS app with nearly identical public/personal presentation. Optional
personal modules contribute selected actions and background providers; the core
owns state, playback, library and safe device operations. No public fork, hidden
safety bypass or separate library format. Preserve Sonny's existing capabilities.
The public product is a complete music manager from iPod/MP3 players to USB.
Guarded device file editing and confirmed generic-volume Erase remain shared.
Personal modules isolate current uncleared online scraping/discovery implementations.
Online features may be public when their service/content permissions and dependency
licenses allow the intended use. Sonny confirmed excluding the current SoundCloud
Unknown-Artist implementation; this is not a permanent exclusion of artist suggestions.
Future validated
rekordbox-compatible/CDJ USB export belongs to the shared product.
See the [feature allocation and module contract](release-boundaries.md).

Reliable supported-device management remains the release prerequisite. The
original MA446 workflow has physical acceptance; newer lifecycle/performance
corrections still have the gates below. The requested rekordbox-style CDJ export
phase follows iPod/library work; research must pass its own hardware and safe-write
gates before becoming a shared product feature.

## Device follow-up — physical acceptance when the iPod returns

User reports stable playback so far with database auto-update off and preferences
persisting after normal shutdown. Continue normal use; capture diagnostics and
isolate theme/state/firmware only if OOM returns. No runtime leak/root cause is
yet established. Sync preference ownership is fixed. Sonny reiterated zero
iPod settings changes on September 28; no new firmware experiment is authorized.

- [x] Install the September 28 loading-row/autoplay corrections: signed build
  114, source `f6013a9`. Bottom loading status remains; terminal snapshot ordering
  and cue-latched autoplay are corrected. The installed app passed two real Random
  transitions after seeking near track ends; installation evidence is retained
  in the build-114 handoff at Git revision `50a47ba`.
- [x] Bound macOS cloud-provider readiness retries without weakening source
  hashes; preserve source context and show actual composite transaction outcomes.
  Readiness-only build 115 recovered provider failures during planning and
  preflight with Finder closed; generation 62 completed at 21:57 on September
  28. This accepts that controlled run, not universal provider reliability;
  evidence is in the [sync audit](sync-performance-audit-2026-09.md).
- [x] Implement normal composite Sync using the committed device inventory,
  including unchanged local/cloud source contracts and existing-song playlist
  additions. Retained device audio/art needs no per-file stat or tree scan.
  Explicit Verify/Deep Verify read schema-3 ownership; Repair audits inventory
  and restores missing owned Rockbox files. Installed and relaunched at 21:58
  with unchanged curation after 983 tests/four skips and the full render gate.
  Those development builds both derived number 115 from the same dirty Git
  base; archived fingerprints distinguish them. The September 28 release handoff
  in Git records that installation. The first real one-title
  incremental run committed generation 63 in 26.76 s (18.30 planning + 8.46 commit),
  reading 69.56 MB and retaining 2880 unchanged songs. The preceding two-title
  run took 1942.83 s and read 40.75 GB. This accepts that edit path; no-op,
  playlist-only, cancel/retry and other physical gates remain open.
- [x] Historical September 28 transfer spacing correction prevented clipped byte
  totals. The September 29 presentation fix below supersedes its alternating
  byte/track counter: per-file details now live on hover, keeping the row stable.
- [ ] Accept the earlier intermittent stop and iPod reconnection behavior during
  normal use. Connected-device Random regression tests use synthetic discovery;
  the bounded installed smoke test does not establish the live failure's cause.
- The September 28 Sync badge reflected real title edits and a verified Rockbox
  append to OG. Sonny's subsequent Sync committed generation 61; never clear
  future drift by changing settings or resetting state. Historical context is in
  the [sync audit](sync-performance-audit-2026-09.md); current acceptance limits
  are in [HANDOFF.md](HANDOFF.md).

- [x] `_safe_copy` now fsyncs the staged payload before verification/publication;
  failure leaves the destination absent. Normal OS eject remains required.

Completed disk/firmware/export checks and remaining boundaries:
[Rockbox OOM investigation](rockbox-playlist-oom-2026-09.md).

## 1. Completed correctness groundwork

Findings and source evidence: [software audit](software-audit-2026-09.md).

- [x] September 29 connection presentation: cached kernel mount hints bypass
  attachment backoff; disabled Checking rows publish their real sync status on
  the first scan. Dashboard preload follows without another forced drift scan.
  Fresh physical Finder-to-SoundBox reconnect timing remains to be observed.

- [x] A2: staged/validated restore with exact rollback, startup recovery, bounded
  archive input and consistent backup snapshots. UI closes admission, drains
  existing writers with events alive, and resumes after worker completion.
- [x] A3: serialized artwork/image/index/default-photo publication through the
  common state owner; failure restores disk and in-memory state.
- [x] A8: Qt-independent state repository validates known schema, preserves
  unknown namespaces, handles recovery/rotation and exposes save errors. A
  persistent window-title warning survives transient statuses until a save works.
  A damaged library without a usable backup remains write-disabled until restore.
- [x] September 29 status presentation: per-file copy/verify details remain on
  hover while operation labels/counts stay stable. Static messages fit their
  text and retire old pulses; stale transient timers cannot clear newer messages.
  Background preparation/artwork yields to Verify and other device work, and
  disk-space pauses explain their cause and resume threshold. Rekordbox relocation
  continues to use stable IDs and refreshed paths; no database watcher was added.

These correctness changes are shipped. Future runtime changes retain the
deterministic failure/race tests, full release gate and safe installed-app
verification; physical acceptance remains listed separately below.

## 2. Finish lifecycle integration and measure performance

The five non-USB findings from the September 28
[repeated-work audit](archive/repeated-work-audit-2026-09.md) are implemented: selective
analysis watching, track-scoped row invalidation, preference-save coalescing and
identical-save elision, idle discovery admission, and stock-art completion reuse.
Synthetic probes establish eliminated I/O; they do not claim a whole-app speedup.
Generic USB/Rockbox export optimization is explicitly deferred at Sonny's request.
The lifecycle/controller work and physical acceptance below remain separate.

- [ ] A5: make the coordinator the actual confirmed-command queue used by desktop
  adapters; remove duplicate queue/ownership state only after behavior parity.
- [ ] A7: move remaining GUI-thread removable-filesystem checks into serialized
  workers and publish cached presentation facts without weakening write guards.
- [ ] A9/A10: measure source reads, proof/receipt reuse, transforms, device bytes,
  staging peak and phase durations for stable no-op, cloud drift, artwork-only
  change, single-track edit and overlapping playlist batches. Establish GUI and
  playback baselines before setting latency budgets.
- [ ] Physical acceptance: repeated reconnect/power-cycle with music playing,
  visible loading then readiness, clean badge without edits, and real drift
  after curation. Playback and discovery remain independent.
- [ ] Physical acceptance: unchanged follow-up Sync with measured source reads
  and writes; overlapping drops; cancel/retry; normal eject after success and
  failure. Preserve generation-58 recovery until separately reviewed acceptance.

Generation 59 completed, but read about 20.89 GB of canonical audio in its
preflight/commit metrics. Generation 60's zero-copy dialog does not establish
zero reads or zero database writes. The September 28 generation-61 run completed
in build 114, but planning plus commit read about 40.42 GB of canonical audio;
cloud timestamp rechecks remain a performance concern. The initial readiness-only build 115 added bounded
provider retries; it did not address that repeated-I/O cost. The new
incremental Sync implementation removes unchanged-source rehashes and retained
media inventory scans from the normal desktop path. Its synthetic no-read/no-stat
regressions alone do not establish a real-device timing improvement; the later
generation-63 one-title run above provides measured acceptance for that path. See
[ipod lifecycle evidence](ipod-lifecycle-audit-2026-09.md) and
[sync performance evidence](sync-performance-audit-2026-09.md).

## 3. Establish the shared-core / personal-module boundary

The existing-feature public/personal split is implemented with explicit profiles,
a typed first-party provider boundary, command/scheduler admission and package
inspection. Public excludes the existing SoundCloud implementation and `yt_dlp`;
personal preserves the original workflows. Both retain the same core and state.
See [the contract](release-boundaries.md) for composition and distribution
boundaries; [HANDOFF.md](HANDOFF.md) records the current personal installation.
Public distribution clearance remains a separate gate.

- [x] A4 storage boundary: `soundbox.artwork` owns local ArtCache/results/default
  photos and works without provider imports/network. Compatibility re-exports
  remain in `artfetch`; online matching lives in `soundbox_personal.discovery`.
- [x] Inject optional artwork and artist-suggestion providers; register their
  actions and worker lifetimes through the shared core. Gate startup scheduling
  and command execution as well as menus.
- [x] Preserve current personal discovery behavior in its optional module;
  prove detach/reattach preserves existing art, blocks and curation.
- [ ] Add read-only embedded-art extraction for local audio with explicit
  precedence protecting manual choices and sticky removals. This is not shipped;
  it is separate shared-feature work, not a prerequisite for the existing-feature
  public/personal split.
- [x] A11/A12: add explicit public/personal build composition, separate optional
  dependencies, one packaging authority and artifact-content/provenance checks.
- [x] Test both variants without a live library/device. Public must work with
  private packages absent and network denied; paired renders preserve the same
  main layout with only intentional action differences.

Initial attach/detach is explicit packaging plus restart. Arbitrary third-party
plugins, hot unloading and a plugin marketplace are not required.

## 4. Decompose the remaining ownership hotspots

- [ ] Move library load/refresh, row-cache coordination and curation/import
  operations out of MainWindow into typed controllers with narrow inputs.
- [ ] Move device orchestration and artwork orchestration into their respective
  owners; leave MainWindow responsible for widgets and signal composition.
- [ ] Extract playback navigation/autoplay independently from native transport.
- [ ] Split stock/composite planning, validation and transaction stages behind
  existing APIs. Retain firmware-specific publication/recovery order and all
  ownership/source contracts; no universal exporter rewrite.
- [ ] When justified by maintenance work, separate native audio/clock,
  rendering/shader and diagnostic units behind the unchanged C ABI. Likewise
  separate analysis codec/storage from algorithms only with format-level tests.

Prefer independently testable owners to mixins. No extraction is complete merely
because a file is shorter. Extend typing to new controllers. Preserve state,
manifest/cache schemas, native timing and visible behavior in each slice.

## 5. Public distribution gate

- [ ] Dependency/license/notice inventory and redistribution review, including
  the native/Qt stack and firmware database renderer. No clearance assumed.
- [ ] Public signing/notarization, clean-machine installation and upgrade test;
  explicit supported macOS/architecture, rekordbox and device matrix.
- [ ] Artifact inspection excludes personal modules/data and developer fixtures.
- [ ] Verify backup/restore, first launch without rekordbox, denied permissions,
  missing sources, unavailable providers and unsupported devices.
- [ ] Complete sections 1–3 and relevant device acceptance before public release.
  Section 4 proceeds incrementally; no speculative full rewrite is required.

## Deferred, explicitly outside the current release sequence

- SoundBox manual beat-grid editor: decide ownership, migration and export
  precedence first. Storage groundwork and imported cues/grids already exist;
  no editor currently ships. See [PLAYER.md](PLAYER.md).
- Schema-2 to schema-3 device migration: separate ownership/recovery design and
  acceptance. Existing refusal remains enabled.
- Additional stock iPod models/formats: per-model fixtures, database/hash/art
  validation and physical acceptance before broadening the MA446 allowlist.
- Windows: a real native-player/device/bookmark/packaging port. Not a prerequisite
  for the first macOS public release; validate rekordbox versions explicitly.
- Complete Rockbox Track ReplayGain, including WAV: upstream parser path first;
  reference-validated ReplayGain 2.0 / ITU-R BS.1770 gated analysis at -18 LUFS
  plus true peak, checked against independent scanners and official vectors;
  metadata only on disposable
  verified payloads, unchanged shared stock WAV and physical dual-firmware
  playback acceptance. No MP3-only promise or automatic private firmware fork.
- Uncommitted UX ideas stay in [the exploration backlog](IDEAS.md).

## Shipped foundations — not TODOs

Read-only rekordbox adapters and source-neutral model; additive curation/imports;
stable playlists and manifest drift; native player with accepted waveform/cue/
autoplay behavior; V5 analysis and bounded preparation; local/default artwork;
MA446 stock and single-copy dual export; guarded Rockbox appends; numbered
Rockbox playlist files; worker-finished device release; normal integrity-independent
eject; reconnect visibility; removal of Rockbox preference writes and settings-only sync drift.

A1 follow-up: artwork/suggestion dialogs and interactive waveform workers now
share exact completion ownership, responsive draining and closed admission during
shutdown. Future optional provider work must use the same contract.

Known limitations in the audit qualify these foundations: task families are not
all consolidated under one owner,
legacy automatic artwork still needs targeted quality review, and current
physical gates remain open. Do not rebuild accepted player/cover behavior as new
features. Existing invariants live in [ARCHITECTURE.md](ARCHITECTURE.md),
[DEVICES.md](DEVICES.md), [PLAYER.md](PLAYER.md), [GOTCHAS.md](GOTCHAS.md) and both
runtime instruction files.

- [ ] Complete the targeted legacy pre-provenance automatic-cover review, retaining
  manual selections, sticky removals and current conservative matching. This is
  curated-data QA, not permission for a bulk artwork reset or automatic refetch.
