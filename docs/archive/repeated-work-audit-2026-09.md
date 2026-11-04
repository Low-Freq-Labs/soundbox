# Repeated-work audit — September 28, 2026

Historical evidence; dated status and instructions below apply only to their
original checkpoints. Current behavior and priorities are in [PLAYER](../PLAYER.md),
[ROADMAP](../ROADMAP.md) and [HANDOFF](../HANDOFF.md).
Original `build/` paths are historical evidence locators. Some private archives
have since been retired; [project layout](../PROJECT_LAYOUT.md) distinguishes
retained receipts from deleted material. Old paths do not guarantee retention
or authorize replay; inspect retained scripts before reuse.

The initial read-only audit followed generation 63's incremental-sync acceptance.
The original findings below describe the pre-change baseline at `50a47ba` plus
the incremental/spacing changes. Sonny subsequently authorized all five non-USB
fixes; those are implemented. Generic USB/Rockbox export optimization remains
explicitly deferred. All probes use disposable synthetic media/state; physical
acceptance and installation evidence belong in [HANDOFF.md](../HANDOFF.md).

## Implemented non-USB changes

| Area | Implemented behavior | Synthetic evidence |
| --- | --- | --- |
| Analysis watching | Persistent stat/input receipts, one shared header on a miss, 60-second identity checks and 30-minute fallback retry | Unchanged warm scan: zero record, DAT or audio opens; zero tasks. Cold ready track: one header instead of four. |
| Title edits | Track-scoped invalidation and missing-row warming; global registry/policy changes still invalidate all | One edit recomputes one row and preserves other cached objects. |
| State saves | Byte/identity guarded no-op saves; 400-ms player-preference debounce; immediate curation/maintenance/shutdown saves | Ten identical 40,979-byte saves: zero publications/fsyncs, versus 10/152 before; 0.0042 s in the warm synthetic probe. |
| Idle discovery | Watcher/root timestamp admission; failed-probe 3–30-second backoff; connected drift unchanged; old unmounted folders skipped after a 30-second attachment grace | 100 unchanged empty polls start zero discovery workers; mount/retry paths remain active. |
| Stock artwork | Persistent completion signatures covering source/profile/manifest/pixels; changed or missing entries prepare, no per-image sleeps | Ten warm worker passes: zero manifest/JPEG/pixel opens; deleted pixels rebuild. |

Receipts are disposable scheduling hints, never content-integrity or device
ownership proofs. Interactive analysis loads and selected export verification
retain their checks. Source audio, rekordbox and device formats are unchanged.
The analysis fallback retry bounds silent sidecar-parser failures without making
the minute-by-minute watcher reopen unchanged records. Invalid inventories fall
back to discovery; source/cache/sidecar mutations and explicit recooks invalidate
or bypass reuse. Backup/restore keeps the same journal, lock and generation guard.

Validation: full native/lint/type/test/render gate passed September 28 with
995 tests and four skips. Tests cover receipt invalidation, late sidecars, cue
edits, forced recooks, deleted art, idle admission and immediate save boundaries.
Before/after probes are `repeated-work-audit.json` / `repeated-work-after.json` in
`/private/tmp/soundbox-lifecycle-rollback.18e3k4di/`; scripts remain in `/tmp` and
never inspect live audio or mounted children. No whole-app latency multiplier
is claimed from these small control-flow fixtures.

## Installed runtime observations

Development build 115 (dirty base `50a47ba`, archived fingerprint in rollback) installed at
22:47:46 September 28. During **22:47:56–22:49:57**, no device discovery workers
ran. Startup and both minute checks reused **2891 analysis entries** with zero
preparation tasks. Both startup art passes reused **2767 covers**, zero preparation
or failures, each within one logged second. The initial receipt-building pass
at 22:42 took roughly three seconds, versus the earlier roughly 37-second warm.
Library curation and existing audio-analysis/artwork cache files were preserved.

Clean release build **116**, source **`47dd540`**, was installed at **22:57:11**.
Startup again reused 2891 analysis entries and 2767 covers with zero preparation;
curation remained byte-identical. Final provenance and observation evidence are
in [HANDOFF.md](../HANDOFF.md).

The first installed verification exposed an old unmounted `/Volumes/Data` folder
as a perpetual failed probe. The final build distinguishes ordinary same-filesystem
folders from mounted children, retaining a 30-second attachment grace for fresh
mountpoint directories. Synthetic tests cover both cases and probe backoff.
No physical mount/reconnect or device write was initiated for this verification.

## Original audit findings (historical baseline)

### 1. Analysis watching repeats metadata reads across the whole library

`desktop.app._maybe_fill_waveforms` schedules a new `WaveformFillWorker` every
60 seconds after the previous pass finishes. Each pass visits all visible
tracks. `is_ready`, `fallback_waveform_modes`, `grid_needs_sync` and
`imported_cues_need_sync` can reopen the same SoundBox analysis manifest.
The worker also calls `rekordbox_analysis.read_beats`, which reads the DAT
sidecar each pass even when its identity is unchanged. Fallback waveform
records may additionally reparse sidecars looking for newly published streams.

An unchanged synthetic record with imported cues and a synthetic DAT produced
**four analysis-record opens, one DAT open, zero canonical-audio opens and zero
queued tasks**. This is metadata waste, not evidence of full waveform decoding
or gigabytes of cache payloads being reread. The live log recorded nine zero-task
preparation passes during 22:10:01–22:19:25.

**Original proposal:** read each manifest once per pass, then persist a disposable compact
inventory of source/cache/ANLZ identities and cue/grid fingerprints. Revisit
changed or missing entries, with bounded fallback polling for delayed rekordbox
sidecars. Keep loaded-track validation, explicit reanalysis, atomic publication,
cloud placeholder behavior and eventual detection of manual grid/cue edits.
Do not simply turn the watcher off or declare an unverified source current.
Actual latency/CPU/battery savings remain unmeasured.

### 2. One title edit invalidates every computed display row

`desktop.app._on_cell_edited` sends title changes to `_invalidate_rows`, which
clears the entire row cache and schedules `_do_warm`. That worker recomputes
`engine.track_row` for every track, even though the affected track ID is known.
For the current loaded library that is 3397 candidates for a one-title edit.
The debounce coalesces bursts but does not make invalidation selective.

**Original proposal:** invalidate by track ID for title-only/local metadata changes; preserve
whole-library invalidation for title policy or artist-registry changes that
actually affect other rows. Patch visible rows and affected playlist summaries
without rebuilding unrelated views. This is source-confirmed redundant work;
no measured UI latency improvement is claimed yet.

### 3. Unchanged state is durably rewritten; UI settings share the same path

`_on_waveform_zoom_changed` and `_on_jump_mode_changed` call `_save_state` on the
GUI thread. `StateRepository.save` validates/deep-copies/serializes the whole
library, checks rotating backups and executes a recoverable publication even
if the document has identical bytes. The shared state lock also serializes
artwork reads/writes, so these writes can contend with UI artwork access.

Ten identical saves of a **40,979-byte synthetic document** produced **10
publications and 152 `fsync` calls**, with identical final bytes. Total measured
wall time on this warm local fixture was **0.0367 s**: confirmed excess I/O, not
an observed multi-second freeze. That includes backup bookkeeping and transaction
journal durability, not 152 copies of the library. Zoom changes emit on discrete
zoom steps, not every rendered frame.

**Original proposal:** skip truly identical saves; coalesce ordinary UI preferences separately
from curation. Keep immediate durable saves for user curation and before device
writes, plus explicit flush on safe shutdown/backup/restore. Do not remove the
journal or weaken backup/restore serialization to hide the overhead.

### 4. Discovery wakes every three seconds even without devices

Both `_DEVICE_POLL_ACTIVE_MS` and `_DEVICE_POLL_IDLE_MS` are 3000.
`_poll_devices` stats `/Volumes`, but an unchanged timestamp still falls through
to `_refresh_devices` when there is no eject-ready hold. This creates a worker,
re-enumerates `/Volumes` and checks children. Device facts/profiles are cached;
it does **not** run a fresh diskutil identity probe for every known mount on
every poll. With a known device, the worker additionally computes drift.

During the final installed app's **22:10:01–22:19:25** idle log window there were
**189 completed discovery workers**, totaling **9.016 seconds of worker wall
time**. This is neither CPU time nor nine seconds of UI blocking. The logs show
no mounted-device result in that interval.

**Original proposal:** use the existing mount event plus `/Volumes` timestamp for idle
admission; a retry/backoff must still handle failed mount probes and pending
notifications. For connected devices, split stable profile/inventory facts from
small playlist-change checks and source/library revisions. Preserve immediate
reconnect during playback, remount invalidation, worker ownership and error
recovery. Do not reuse stale mount authority merely to avoid discovery.

### 5. Generic USB/Rockbox export still repeats full reads on an unchanged export

The new incremental policy applies to the schema-3 composite iPod. In
`soundbox.sync.run`, the generic path captures/prepares each selected source
before comparing with the previous manifest, then hashes an existing destination.
Its preliminary matching `source_identity` digest is overwritten by the captured
hash. A manifest exists but does not prevent these full reads.

A synthetic unchanged two-file export reported **zero copy bytes**, while
performing **two source acquisitions and two existing destination hashes**.
The fixture used ten total bytes to establish control flow, not throughput.
Rockbox derivatives can add preparation work to that same path. This is the
closest remaining equivalent to the old iPod sync behavior, but it does not
explain costs on the now-optimized composite device.

**Original proposal:** introduce explicit inventory-trusting versus verification/repair
semantics for generic exports, with their own manifest/ownership tests. A generic
USB may have other writers; Sonny's exclusive-iPod policy must not silently
become a blanket trust assumption for every volume. No schema-2 migration or
stock-only optimization was implemented in this audit.

### 6. Stock-art cache warming scans every cover and sleeps on cache hits

Each authoritative library refresh schedules `StockArtworkPrepWorker`. It
collects all visible curated covers, calls `prepare(verify_cache=False)` for
each, and sleeps 10 ms unconditionally. The 2767-cover launch therefore includes
**27.67 seconds of intentional sleep** before considering its metadata I/O.
The observed log spans 22:09:24–22:10:01, roughly 37 seconds, for that background
pass. “Ready” does not distinguish cache hits from newly generated entries.

Ten unchanged synthetic cache lookups opened ten derivative manifests, **zero
canonical JPEGs and zero pixel payloads**. The cache already avoids image
conversion and large payload reads on a hit. The remaining opportunity is
scheduling/metadata, not regenerating every cover.

**Original proposal:** queue only dirty/new art for preparation, retain a compact completion
index, and apply pacing to real preparation work rather than validated hits.
Existing per-track art updates already have a pending-image queue. Preserve
canonical art, format/profile invalidation and recovery from deleted derivatives.

## Existing good reuse to preserve

- Waveform readiness reads manifest headers, not full compressed waveform
  payloads. Player loads still validate the sections actually used.
- The waveform widget already suppresses redundant Qt repaints when Metal owns
  rendering; do not treat it as an unfixed render-loop problem.
- Row-cache hits already avoid recomputing track display metadata during ordinary
  playlist browsing. The issue is broad invalidation.
- Stock-art hits reuse derivatives; artwork fetches remember misses. Startup's
  “new art tracks” count includes entries the worker can later reject as hidden
  or unavailable, so a six-track autostart followed by zero work is not evidence
  of six network searches.
- Live rekordbox refresh takes a read-only database/WAL snapshot on launch or
  explicit refresh. Its safety boundary is justified. A future unchanged-load
  shortcut must track both database and WAL identity; it cannot trust the UI
  snapshot indefinitely or write to rekordbox.

## Original audit evidence and proposed order (historical)

Temporary probe code: `/tmp/soundbox-repeated-work-audit.py`. Recorded results:
`/private/tmp/soundbox-lifecycle-rollback.18e3k4di/repeated-work-audit.json`.
The log window was bounded to the final app launch offset, avoiding older days'
identical wall-clock timestamps. Probe code does not load the live library or
inspect mounted children. No full application test gate was rerun: this audit
changes documentation only; installed runtime fingerprints remain unchanged.

The original proposed order was to prioritize selective analysis watching and
track-scoped row invalidation; remove idle discovery and preference-save waste
alongside those focused changes. Stock-art scheduling is another bounded win.
Generic-export parity remains a separate, deferred exporter task. None of these
findings establishes another 73× improvement.
