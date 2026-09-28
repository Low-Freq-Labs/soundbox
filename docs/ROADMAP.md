# SoundBox roadmap

Updated September 28, 2026. Active work and acceptance only; completed work is
summarized below. Historical experiments and release diaries remain in Git and
specialist documents, not in the task queue. No numeric quality score substitutes
for evidence. For current validation and installed revision, see [HANDOFF.md](HANDOFF.md).

## Product direction

One shared macOS app with nearly identical public/personal presentation. Optional
personal modules contribute selected actions and background providers; the core
owns state, playback, library and safe device operations. No public fork, hidden
safety bypass or separate library format. Preserve Sonny's existing capabilities.
The public product is a complete music manager from iPod/MP3 players to USB.
Guarded device file editing and confirmed generic-volume Erase remain shared.
Personal modules add online scraping/discovery; dependency redistribution review
separately determines what can enter a public artifact. Future validated
rekordbox-compatible/CDJ USB export belongs to the shared product.
See the [feature allocation and module contract](release-boundaries.md).

Reliable supported-device management remains the release prerequisite. The
original MA446 workflow has physical acceptance; newer lifecycle/performance
corrections still have the gates below. Expansion toward a rekordbox export-mode
alternative remains a later direction, not permission to ship CDJ research.

## Device follow-up: observe playback; retain the durability finding

User reports stable playback so far with database auto-update off and preferences
persisting after normal shutdown. Continue normal use; capture diagnostics and
isolate theme/state/firmware only if OOM returns. No runtime leak/root cause is
yet established. Sync preference ownership is fixed. Sonny reiterated zero
iPod settings changes on September 28; no new firmware experiment is authorized.

- [ ] Install and accept the September 28 loading-row/autoplay corrections.
  Source fixes preserve the bottom loading status and handle native terminal
  snapshot ordering plus cue-latched autoplay. Connected-device Random tests use
  synthetic discovery; the exact reported live failure still needs acceptance.
  Installation is authorized; current installed provenance is in the handoff.
- The current Sync badge has real differences: two title changes and a verified
  Rockbox append to OG. Do not clear it by changing settings or resetting state.
  Details and read-only evidence are in [HANDOFF.md](HANDOFF.md).

- [x] `_safe_copy` now fsyncs the staged payload before verification/publication;
  failure leaves the destination absent. Normal OS eject remains required.

Completed disk/firmware/export checks and remaining boundaries:
[Rockbox OOM investigation](rockbox-playlist-oom-2026-09.md).

## 1. Close correctness gaps before structural extraction

Findings and source evidence: [software audit](software-audit-2026-09.md).

- [x] A2: staged/validated restore with exact rollback, startup recovery, bounded
  archive input and consistent backup snapshots. UI closes admission, drains
  existing writers with events alive, and resumes after worker completion.
- [x] A3: serialized artwork/image/index/default-photo publication through the
  common state owner; failure restores disk and in-memory state.
- [x] A8: Qt-independent state repository validates known schema, preserves
  unknown namespaces, handles recovery/rotation and exposes save errors. A
  persistent window-title warning survives transient statuses until a save works.
  A damaged library without a usable backup remains write-disabled until restore.

Exit: deterministic failure/race tests, full check gate and safe installed-app
verification for runtime changes. Do not mix these fixes with device-format or
renderer redesign.

## 2. Finish lifecycle integration and measure performance

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
zero reads or zero database writes. Tests are not hardware acceptance. See
[ipod lifecycle evidence](ipod-lifecycle-audit-2026-09.md) and
[sync performance evidence](sync-performance-audit-2026-09.md).

## 3. Establish the shared-core / personal-module boundary

- [x] A4 storage boundary: `soundbox.artwork` owns local ArtCache/results/default
  photos and works without provider imports/network. Compatibility re-exports
  remain in `artfetch`. Optional UI/provider/build wiring below is still pending.
- [ ] Inject optional artwork and artist-suggestion providers; register their
  actions and worker lifetimes through the shared core. Gate startup scheduling
  and command execution as well as menus.
- [ ] Preserve current personal discovery behavior in its optional module;
  prove detach/reattach preserves existing art, blocks and curation.
- [ ] Add read-only embedded-art extraction for local audio with explicit
  precedence protecting manual choices and sticky removals. This is not shipped.
- [ ] A11/A12: add explicit public/personal build composition, separate optional
  dependencies, one packaging authority and artifact-content/provenance checks.
- [ ] Test both variants without a live library/device. Public must work with
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
- CDJ export: retain separate [research plan](cdj-usb-export.md); no production
  `/PIONEER` ownership until independently designed and accepted.
- Complete Rockbox Track ReplayGain, including WAV: upstream parser path first;
  reference-validated ReplayGain 2.0 / ITU-R BS.1770 gated analysis at -18 LUFS
  plus true peak, checked against independent scanners and official vectors;
  metadata only on disposable
  verified payloads, unchanged shared stock WAV and physical dual-firmware
  playback acceptance. No MP3-only promise or automatic private firmware fork.
- Uncommitted UX ideas stay in [the exploration backlog](../new-ideas-7-22.md).

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
