# Performance audit — November 2, 2026

Artists is a SoundBox-only derived browser view. It creates no rekordbox folders,
per-artist playlist records or recurring source-write jobs. The native pipeline
handles genuine user playlist/Collection changes and the one-time legacy custom
playlist upgrade, rather than maintaining derived artist groups in the DB.

The earlier source-backed Artists experiment exposed a major interactive cost:
rebuilding the entire pending native playlist presentation repeatedly on the
main thread. A small playlist could take over 100 ms to prepare while unrelated
playlists were saving. Reusing that presentation and carrying one captured view
through a sidebar update removes that repeated work for genuine native edits
while keeping writers and their proofs separate.

This review covers browser rows, scrolling, sidebar updates, pending playlist
projection, library/artist/artwork refreshes, state persistence, cache writes,
cloud acquisition, and the source/device worker boundaries. It is an inventory
of concrete costs and contracts, not a claim that every possible workload has
been measured or that another feature can bypass those boundaries.

## Native command audit, November2 evening

Sonny reported slow adding/updating and slow native commands generally. During
the first pass he kept SoundBox closed; he later confirmed improvement and
requested the dedicated second pass below. Private timings use
existing frozen66,248,704-byte native database copies, with6017content rows,
12386cues and20166contentFile rows. Generated media and disposable source paths
isolate writes and avoid cloud hydration. Frozen originals are hash-verified
unchanged. No real app inputs or source writes are used for performance tests.

The common source lane makes a slow registration/analysis/locator transaction
visible as latency for other queued commands. Repeated full reconstruction of
unchanged encrypted recovery copies is the largest measured avoidable cost.
Full source snapshots and fresh process/version/authority/path/payload checks
remain required. A full encrypted backup costs about0.924s; complete logical
inventory about0.753s including connection; SHA-256 of the DB about0.032s;
fresh closed-app query about0.025s. These are private-copy measurements, not
native UI or cloud-download timings.

The implementation removes a duplicate desktop analysis-plan load and a second
Collection-import full-library refresh. The latter now strictly verifies native
bindings against the same fresh full library observation and atomically saves
bindings plus intent completion. Failed local persistence still replays the
committed receipt on restart. Tag saves retain their fresh full library reload
because intelligent-playlist membership depends on the complete observation.

WAV/CBR plan loaders reuse only a completed immutable reconstruction, keyed by
exact raw plan, authority, profile and folder, bounded to4entries/30seconds.
Every call still validates paths/profile/authority and hashes ALL payloads.
No source/process/transaction/readback check is memoized. Paired proofs inventory
each database once rather than scanning the before-copy twice. Registration and
locator use the existing playlist pattern: one exact completed immutable-pair
proof per store, after fresh source identity, operation record and payload hashes.
Non-WAV imports skip the inapplicable float-WAVE conversion snapshot. Durable
cue preparation retains at most8source-inode/native-identity/authority-bound
store handles so its existing counter-bound verified backup can be reused;
source/app/recovery checks still run on every snapshot/apply. Logs record native
command kind, execution time and completion/error without track paths or contents.
These log durations exclude queued wait and GUI acknowledgement.

Plain private-copy measurements on the M1 Pro (no profiler overhead):

| Operation | Before | After |
| --- | ---: | ---: |
| Registration workflow, median of2 alternating runs per variant | 10.387s | 4.780s |
| Locator prepare/apply, median of2 alternating runs per variant | 11.838s | 5.459s |
| Repeated exact CBR prepared-plan proof, median of3 | 2.480s full proof | 0.067s reuse |

Registration and locator each improve about54%. Payload-hash calls remain
31→31 and35→35 respectively; locator integrity checks remain2→2. Avoided
checks reconstruct the same exact immutable pair, rather than proving new source
state. The132,497,408-byte paired plan still gets fully hashed on every reuse.
One complete private CBR workflow measured19.104s:8.606s preparation,0.070s
constructor,9.185s apply,0.124s retention and1.118s independent readback. It uses
accepted packets through a fixture renderer and excludes detection/GUI work.

These figures exclude UI/decoder/cloud time and do not claim an end-to-end
47-second import now completes in any particular time. Native reopen/UI latency
of the new package remains unobserved while Sonny keeps the app closed.

Existing tag/cue/grid source writes measured about0.30–0.35s warm and1.3–1.4s
cold in the initial profiled audit. Playlist create/rename were about3s; private
file conversion about2.4s and removal preparation about2.36s without a profiler.
Those samples identify remaining proof/refresh costs, not after-change promises.
Removal tests prepare only generated selections; no user music is deleted.

Small receipts remain under native-command-performance-2026-11-02 and
mp3-analysis-2026-11-02/registration-locator-costs-20261102-202917.json in the
private Logs root. Frozen inputs are unchanged and temporary DB copies are
removed. Existing acceptance/backup/crash tests remain required; unchanged
domains are retained when their fresh proof is the measured cost.

## Dedicated command reviews — second pass

Sonny requested a dedicated subagent for each native command. Ten domain reviews
covered every operation below in batches of three workers, with a separate shared
backup audit and independent cross-reviews. Real SoundBox remained open; reviews
used private native-sized TEMP sources and generated audio only. No real app
input/source/AppData/music/Trash/device writes were used for profiling. Frozen
DB/XML/packet hashes remain exact, and temporary database copies are removed.

| Command review | Wasted work and disposition |
| --- | --- |
| Tags: add/remove/no-op | Warm source mutation about0.3s, full intelligent-membership refresh about1.3s. Preserve the full fresh read; raw cue display avoids redundant ORM timestamp conversion. Logs separate execution/dispatch/phases/GUI acknowledgement. |
| Hot/Memory cue add/delete | Repeated process-query subprocesses dominate warm writes. Header equality skips rewriting unchanged derived waveforms; externally changed grids still refresh. Retrigger remains transport-only. |
| Grid: nudge/unshift/bar-start/BPM/double/half | Fresh process probes dominate; pure planning is about10ms. Preserve distinct durable prepare/apply/readback and Analysis Lock. A2ms temporary packet staging cost does not justify recovery changes. |
| Playlist/folder create/rename/delete, append/remove, reorder, accepted batch | Identical XML was parsed13–17times within one operation. Existing bounded exact-byte parser session reduces this to1–3while all38payload hashes and fresh reads remain. |
| Registration/import/reuse | Ordinary PCM16/24 WAV imports took a conversion-only DB snapshot before discovering no conversion. Known canonical integer header/no receipt now returns no action; every real conversion/recovery/symlink/unknown state keeps the old guards and registration captures freshly. |
| Locator prepare/apply/replay | First apply re-proved exactly the pair already proved by preparation. Seed only after complete before/after proof AND archive DB/XML transfers match known verified input hashes. Fresh hashes/media/source/SQL guards remain; fresh stores reconstruct. |
| WAV/lossless conversion | Same source paths were resolved twice within a fresh codec-binding check. Reuse only that invocation's normalization map. Original/output byte and independent sample proofs, alias checks and fresh snapshots remain. Cloud-provider captures stay unchanged. |
| Confirmed Collection/disk Delete | Independent native plan/proof, shared-file protection and user confirmation remain. Every semantic-cache hit now freshly hashes raw plan/DB/XML/resource backups; no Delete speedup is claimed. No actual user music or Trash is tested. |
| WAV/CBR initial analysis | Full tables were materialized for one target, and before inventories repeated. Narrow target SQL keeps all historical file/mixer identity collisions; paired proof returns both inventories. Each source snapshot still captures/checks integrity/identity/hashes freshly; only its exact-byte inventory may be reused. |
| Refresh/queue/GUI acknowledgement | A delayed prep retry suppressed genuine lane-release wakeups for nearly2seconds. Promote wakeups to the next event turn; a separate monotonic native-open deadline preserves the2-second backoff and prevents worker spin. Warm isolated GUI acknowledgement remains about0.20s. |

Shared process guards use a small separate C dylib and fresh
`sysctl(KERN_PROC_ALL)` snapshots. Bounds/size/string/own-PID+name checks must pass
before absence is accepted; uncertainty uses the unchanged strict pgrep path.
Only the loaded function interface is cached, never process presence/absence.
Direct prototype queries measured about0.28ms versus24.5ms for subprocess pgrep.
An in-process libproc-name walk was rejected because other-process permission
failures could not prove absence. No real-app-named test processes are spawned.
Native tests use a uniquely named disposable child and injected kernel responses.

Shared encrypted-backup optimization retains stable DB/WAL capture, hot-journal
refusal, full integrity+identity, unchanged raw bytes and fsync. Only absent/zero
WAL can adopt its self-contained private image without re-encryption; any nonempty
WAL (even header-only) keeps SQLite folding. Preliminary alternating66MB backups
measured0.965→0.577s. No source connection or journal-mode normalization is added.

Final serialized **plain** checks on native-sized TEMP copies compare only the
process-query backend, with the other current optimizations active in both:

| Source operation | pgrep | Fresh native query |
| --- | ---: | ---: |
| Hot cue delete, warm apply | 0.344s | 0.144s |
| Memory cue add, warm apply | 0.350s | 0.145s |
| Memory cue delete, warm apply | 0.348s | 0.141s |
| Grid unshift, full durable observation/apply/readback model | 1.186s | 0.522s |
| Set bar start, same model | 1.198s | 0.525s |
| Manual BPM, same model | 1.213s | 0.522s |

Cue complete observation/apply/readback (no GUI) fell about0.63→0.28s; the first
cold cue apply1.025→0.794s. Grid workflows needing a backup fell about1.87–1.88→
1.18–1.22s. Every source boundary still performs its fresh check.

Other isolated findings, whose scopes differ and must not be summed: prep
lane-release confirmation1.932→0.0133s (two alternating runs per variant);
complete fixture-rendered CBR workflow19.104→12.030s (no detector/GUI);
PCM-WAV import conversion no-op0.916→0.001s; locator prepare/apply4.771→3.345s
(two alternating runs, native process helper active in both). Playlist parsing
alone saved8–12% across eight operations; all hashes unchanged. Codec conversion
path reuse reduced33,280→16,669resolutions in its profiled fixture; profiler
wall times are not plain latency claims. Header cache check avoids a synthetic
32MiB rewrite25.9ms→0.073ms. Complete reader output remains field-exact across
3295tracks/85playlists and all cues/tags/native revisions.

Individual receipts and repeatable fixture scripts are in private
`native-command-performance-2026-11-02/pass2/`; `final-serialized/` distinguishes
plain final measurements. `command-review-matrix.json` identifies the reviewer
for every domain. Shared source guards, exact encrypted recovery and all acceptance
boundaries remain. Direct timed user-interface actions on the new package are
still unobserved; current release-gate/install status belongs in HANDOFF.

## Measurement scope

Private offscreen fixtures contain 3,200 or 10,000 generated track records,
1,676 native playlists, and respectively 16,400 or 30,000 native membership rows.
They include 1,575 native group fixtures from the now-discontinued Artists
experiment, 100 small playlists and a complete-library playlist. These remain
a useful large-catalog stress workload, not the release Artists architecture.
All paths point at nonexistent synthetic media. No real source DB,
media or device is read or written by those fixtures. Five repetitions measure
most operations; three cover full scroll sweeps and some larger operations.

Measurements use the M1 Pro release host while the normal app remains available.
They measure Qt/Python work, not physical display latency, audio underruns or
CloudStorage download speed. Raw timings, profiles and final source provenance
remain with the private delivery evidence. Current release/install
validation belongs in [HANDOFF.md](HANDOFF.md).

## Ranked findings and changes

| Cost | Before | After / disposition |
| --- | --- | --- |
| Repeated pending catalog projection | Full recursive catalog copy and projection for each caller; three projections within one sidebar refresh | One bounded cached structure, independent native row maps, value-based invalidation and fresh Library metadata on reuse |
| Source-backed artist initialization, discontinued | Separate publication work for derived per-artist create/append operations | Artists is now virtual and produces zero native maintenance jobs; bounded batches remain available for accepted genuine commands/legacy custom migration |
| Native commit browser work | Row-cache invalidation, full table reload, sidebar recreation, artwork/device/Graveyard refresh | Preserve cached rows and native tree items; unrelated Collection stays untouched; refresh only affected domains |
| Long cached waveform delivery | A transient state bypassed valid caches; compact-first preview then visibly resized on full-detail promotion | Reuse valid canonical stages and atomically publish complete geometry once; omit unused spectral payload, preserve immediate manifest readiness and automatic BPM/key skip above15minutes |
| Snapshot JSON serialization, inspected baseline | A whole-library C encoder call would hold the GIL across one large record set | Existing library/browser cache writers already encode bounded records/chunks in their worker; retained unchanged in this work |
| Durable crown-state save | About 27 ms for a private copy of the current state | Keep persistence-before-presentation and backup/recovery; duplicate validation/copies are a bounded follow-up |
| Artwork defaults during browser preparation | Repeated checks of a shared default-photo pool can scale with tracks | Existing batch status API retained; distinct-photo availability snapshot is a follow-up |

The playlist batch does not add SQL tables, fields or musical-data authority.
Source guards, accepted per-command identities/counters, encrypted before/after
pairs, the common lock/journal, exact native readback and replay remain required.
Reorder remains a separate accepted capability. The corresponding recovery and
sequential-equivalence tests are not optional performance costs.

The cache-writer row describes an existing implementation verified during this
audit, not a new change or a newly measured before/after serialization speedup.

## Browser and pending-update results

The controlled pending workload contains 32 create/append pairs from the earlier
source-backed experiment. It measures pending native command handling, not
ongoing artist maintenance. The retained batch API remains confined to accepted
ordinary commands; the current Artists view never enqueues those pairs. These
before/after measurements include the projection cache and passing a single
captured presentation through the sidebar path.

| Operation, median | 3,200 tracks before | 3,200 after | 10,000 before | 10,000 after |
| --- | ---: | ---: | ---: | ---: |
| Prepare a cached 100-song playlist while batch is pending | 66.130 ms | 3.700 ms | 113.710 ms | 5.890 ms |
| Refresh the sidebar while batch is pending | 212.974 ms | 21.687 ms | 368.909 ms | 27.045 ms |

The uncached projection still performs real structural work: measured medians
were 46.152 ms and 86.617 ms. Those are once-per-changed-intent/baseline costs,
not a cost paid again by every song click or grid-preview update.

The cache retains only one pending projected catalog/playlists and a comparison
baseline. It checks source rows and durable jobs by value, so even in-place native
UUID or nested-intent changes invalidate it. Logical/native track identities and
intelligent-playlist memberships also participate. It rewraps the current Library
each time: hidden IDs, filters, registry, tags and source identity do not become
stale. Shared-import work bypasses reuse. Empty, drained or invalid queues release
the cache. Presentation data never feeds a source writer or grants export
authority before the accepted source FIFO drains.

Other measured main-thread preparation costs after the earlier commit fixes:

| Operation, median | 3,200 tracks | 10,000 tracks |
| --- | ---: | ---: |
| Prepare cached Collection rows, sort and artwork status | 27.803 ms | 86.564 ms |
| Prepare a cached 100-song playlist with no pending update | 0.791 ms | 0.686 ms |
| Build the native sidebar summary | 2.436 ms | 3.607 ms |
| Refresh an unchanged sidebar | 12.142 ms | 14.683 ms |
| Unrelated native commit UI while Collection remains selected | 12.504 ms | 14.950 ms |
| Rebuild artist registry when its inputs change | 27.611 ms | 85.209 ms |

The fixture's 100-step top-to-bottom scroll sweep took approximately 0.61 seconds
at either library size. It delivered and painted Qt events at each step; the
roughly constant cost reflects visible-row work rather than rebuilding every
track. This is not a physical scrolling/FPS guarantee. A separate regression
requires native commits to preserve current track/column, multiple selected
tracks, top-row anchor, expanded folders and scroll position. Trackpad feedback
clears on `ScrollEnd`; phase-less mouse wheels use a short fallback.

## Persistence, artwork and source I/O

At measurement, a read-only copy of the then-current 1,357,507-byte crown state,
with nine saved analyses, 91 native bindings and its migration map, was saved only in a
disposable TEMP directory. Five real changed-state saves measured 26.715 ms
median and 28.162 ms maximum. The profile identifies repeated validation and
deep copying in `StateRepository.save` and backup validation. Per-edit durability
and backups remain intact. A future narrow change can reuse proven state or avoid
an unnecessary copy of freshly parsed backup input, with explicit corruption,
external-edit and recovery tests; moving an uncommitted intent behind its visible
effect is not an acceptable speedup.

`ArtCache.status_kinds` already captures one exclusion-policy snapshot. Default
photo lookup still checks assigned files per track; availability could instead
be captured once per distinct photo for that presentation batch. Keep actual
cover/derivative/export verification on its own path. Native playlist saves now
skip whole-library default assignment and artwork work when track membership and
curation are unchanged. The empty/defaultless fixture's default assignment setup
measured under 1 ms at 3,200 tracks and about 3 ms at 10,000; it does not estimate
the user's populated cover/default pool.

Cloud paths must remain absent from paint and scroll work. Browser facts and
saved-analysis lookups use already captured metadata; actual compatibility,
decode, hash and registration work belongs in its worker. A dataless or offline
source can still incur real provider latency. Valid cache availability should not
force another decode or full cloud read, while changed/unverified bytes still
require the accepted acquisition and identity proofs. See the
[source/cloud audit](cloud-library-audit.md) and current handoff for the SeaDrive
findings and verification limits.

## Boundaries for future features

- Display changes should invalidate affected rows. Global registry/title rules
  can rebuild globally when their actual inputs change; transport edges and
  unrelated playlist membership must not trigger that work.
- Avoid an entire-library computation in paint, wheel, cue or transport handlers.
  A thread is insufficient if one giant C JSON call holds the GIL; use bounded
  serialization for derived snapshots and retain completion ownership.
- Three-second device polling reuses its synthetic playlist inventory. A save
  currently invalidates that inventory; the next rebuild can still prepare the
  Collection and Artists ordering on the main thread. Bind that cache to actual
  inventory/metadata generations rather than every incidental state save in a
  future measured change. Do not suppress mount discovery or ordinary eject.
- Normal device planning trusts the unchanged committed ownership inventory.
  New/changed sources, explicit repair and recovery keep their existing exact
  byte/path/volume checks. A speedup must not introduce retained-media hashing
  on every poll, skip ownership proofs or broaden cleanup.
- Every cache needs explicit inputs, invalidation and an ordinary safe fallback.
  Keep presentation caches disposable and separate from source authority,
  curation, recovery receipts and native edit identities.

## Morning long-set memory and package-size snapshot

Two existing AAC/ALAC set caches were inspected by reading only their small
headers/manifests and file metadata. No audio or compressed waveform payload was
read, decompressed or uploaded for this calculation. Their legacy V5 metadata
describes 6,144-Hz base coordinates with stored divisors 8–256 and approximately
150 compact waveform rows per second. The following are byte-count calculations
from those section lengths and the morning native structures, not measured heap
allocations or a process-memory upper bound. The retained Python totals include
spectral_24; the evening playback change below omits that unused field. These
original figures are a dated baseline, not the final delivery's retained totals.

| Known component | 2:06:57 set | 1:46:41 set |
| --- | ---: | ---: |
| Compressed `.sba5` file on disk | 97.78 MiB | 83.51 MiB |
| Retained packed Python waveform/detail sections | 107.37 MiB | 90.24 MiB |
| Native signed-range Metal buffer | 138.54 MiB | 116.43 MiB |
| Native BLUE/RGB/3BAND pyramids together | 104.51 MiB | 87.83 MiB |
| Sum of those steady waveform components | 350.43 MiB | 294.50 MiB |
| Additional known min/max upload temporaries | 277.09 MiB | 232.86 MiB |

Packed minimum/maximum ranges use four bytes per mip sample in Python. Native
`AudioEnvelopeSample` retains upper/lower plus signed minimum/maximum, eight
bytes per sample. Each mode's `WaveSample` uses four floats, 16 bytes, across its
roughly double-base-sized pyramid. The upload path additionally joins the Python
range buffers, copies them into ctypes arrays, builds a C++ staging vector and
copies that vector into shared Metal storage. The table's transient row counts
the joined buffers, ctypes inputs and C++ range staging in addition to the final
native buffer already counted above. It excludes vector spare capacity, mode
construction workspaces, compressed read/decompression buffers, decoder-owned
allocations and old GPU commands still in flight; it is not an exact peak.

The CoreAudio PCM ring is bounded independently of track duration: two channels
of 1,048,576 floats total **8 MiB**, plus bounded decoder chunks and platform
decoder storage. It does not retain two hours of decoded PCM. Song-space GPU
attributes scale with drawable width and a fixed margin rather than song length.
The current generation's `_analysis_parts` retains packed payloads; native Qt
presentation already avoids expanding those into millions of Python color lists
or an unused Qt envelope. Old waveform workers retain ownership until completion
and receive interruption on replacement. A historical eight-track-LRU comment
does not correspond to an implemented eight-track analysis-payload cache in the
current player, and should not be used to multiply these figures by eight.

The larger process has other allocations. Two read-only `ps` snapshots of the
then-running installed build showed approximately **1.16–1.33 GiB RSS** while source
work and the user's session were active. That is a whole-process observation,
not an attribution to either set, a leak diagnosis, or the final installed build's
memory result. macOS virtual address-space size is not physical residency.

The unused `_spectral_24` playback field was subsequently removed from interactive
reads, with the canonical master, default full-load API and full audit unchanged.
The measured evening omission and validation are recorded below.

Remaining concrete follow-ups:

- After a generation-checked native upload succeeds, investigate retaining only
  grid/cue/source metadata in Python instead of another full packed waveform.
  Verify replacement, fallback rendering, late signals and audio-detail refresh
  before dropping any owned payload.
- A four-byte native signed-range representation could derive the currently
  duplicated unsigned bounds and reduce that buffer's storage, but requires exact
  all-zoom geometry/color and native drawable tests. Likewise, allocating fewer
  inactive mode pyramids trades memory against immediate mode switching. Neither
  is an accepted improvement merely because the byte arithmetic looks favorable.
- Compact imported-analysis caches store one 1-ms range tier on disk and expand
  renderer tiers at load. That reduces persistent duplication; it does not make
  resident memory duration-independent. A future byte budget/cancellation review
  should cover rapid replacement of long sets, not only steady single-track load.

Read-only `du` of the then-installed morning build reported approximately **249 MiB** for the
app, including **197 MiB** of frameworks and **24 MiB** of resources. Major
framework groups were PySide6 90 MiB, SciPy 33 MiB, native payloads 13 MiB,
Pillow 12 MiB, mypy 7.8 MiB, NumPy 6.6 MiB and SQLCipher 6.3 MiB. Native payloads
are primarily the CDJ renderer; the deck library itself is about 364 KiB. These
are installed-bundle disk figures, not amounts all loaded into RAM at startup.

That inspected Python archive contained38 `sqlalchemy.testing` namespaces, seven
`sqlalchemy.ext.mypy` namespaces, six `mypy` namespaces and `ast_serialize`, alongside
the legitimate runtime packages. The composition currently collects all SQLAlchemy
submodules, including its development/testing integrations. mypy and
`ast_serialize` framework directories account for about **10.2 MiB** together;
additional archive code is separate. A focused packaging follow-up can exclude
those development-only integrations and verify actual archive contents, public/
personal composition, native source-read/edit paths and bundle smoke. Do not
remove required numerical, artwork, codec, encryption or Qt components on size
alone. No package dependency was trimmed during this audit. The evening change omits
an unused decoded playback field; it does not delete canonical cache sections.

The related [test-runtime audit](testing-runtime-audit.md) measures encrypted test
costs and the isolated runner improvement. It preserves the complete inventory,
zero-skip gate and recovery matrices while changing execution scheduling.

## November2 evening stable waveform load

The complete waveform now publishes once, including long AAC/ALAC sets. A
provisional compact surface caused the visible resize; withholding it avoids
replacing geometry or palette after first paint. Manifest/grid/cues still become
ready first. A selected-load worker saves its own valid-cache upgrade for the
next load; separate fill completions and explicit reanalysis retain targeted
loaded-deck refresh paths.

Interactive detail reads omit only unused spectral_24; default/full APIs and
whole-record audits retain it. Three alternating warm reads of two actual caches
measured detail155.11→112.30ms and137.28→100.47ms. Retained decoded payload fell
by26.13MiB and24.92MiB respectively. Displayed sections still pass SHA checks.

Native signed-range upload reuses a fixed128KiB table rather than recomputing the
same transfer per sample. All65536 signed scalar outputs are bit-exact against
the old implementation. A private Cocoa backend's2M-range/16MB upload median
fell25.11→6.07ms over five runs, with7.16ms first-call cost. This changes cost,
not the accepted waveform geometry, zoom palette or source data. Grouped drawing
also rechecks upload depth under its render mutex before snapshotting buffers;
skipped draws retain the dirty bit and advertise no nonexistent presentation.

Playlist icon preferences perform a bounded dictionary lookup during painting.
Their row map follows catalog-object/library identity, and setting an icon only
repaints the viewport after a local state save. It does not rebuild the sidebar,
reload a playlist, enqueue a native save or inspect source audio. Installed
provenance and current gate status remain in [HANDOFF](HANDOFF.md).

## First performance delivery verification,20:45

The follow-up is installed at20:45in personal155, runtime fingerprint8882e36f…;
HANDOFF retains exact provenance. All2219tests passed (2155offscreen/64Cocoa),
zero skips/expected failures; lint/types104core files, Rust/native/fixtures/render
passed. All152packaged Python modules and25deck/17CDJ-helper code/data sections
match tested builds. Fresh strict signature and installed normal deep signature
plus exact signed Contents pass. Installed isolated CoreAudio/Metal startup has
zero devices/no loaded library or artwork worker. Curation remains byte-exact,
all10authorities unchanged and queues empty. Sonny explicitly requested keeping
SoundBox closed; no normal real-library launch or UI latency result is claimed.

## Second-pass installed verification

The second package was safely installed at22:56on November2 after Sonny quit
normally. Runtime33689af7…/executablecf86e80f…/personal155 is exact in HANDOFF.
All2266tests passed (2202offscreen450.033s,64Cocoa67.534s), zero skips or expected
failures; lint/types105core files/Rust/native/fixtures/render pass. All154Python
modules and25deck/5process-guard/17CDJ code/data sections match tested builds.
Fresh strict and installed normal deep signatures plus exact signed Contents
pass. Installed isolated startup verifies native player AND fast process query
with zero discovery/no loaded library. Curation is byte-exact to fresh preinstall,
all10authorities and saved icon exact, queues empty; only legitimate window/column
UI settings differ from the earlier first-pass baseline. The real app is left
closed; normal real-library launch and new actual gesture timing remain unmeasured.
One prior first-pass app is retained; obsolete CBR app and duplicate packaging
outputs were retired by exact scoped receipts, with source/recovery/audio intact.
