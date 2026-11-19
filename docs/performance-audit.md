# Performance audit — November 2–18, 2026

## Connected iTunes export — November18 observation

The installed personal172 build completed a user-operated large export while
Collection remained visible. Sonny confirmed that the selected song survived
completion too. The last stock plan copied587new files, writing and verifying
15,560,911,583bytes each. Its750-second interval from planning completion to
commit completion includes recovery setup and artwork, about20.75MB/s of new
audio. Commit-time local source capture took14.375seconds with no retries.
Passive one-second OS disk samples showed roughly19–22MiB/s of combined IO.
Root introduced no competing device-file reader or benchmark transfer.

These observations suggest device IO is the main cost; they do not establish
the hardware's maximum throughput or a before/after speedup. Stock-only copies
have no fixed per-file pause; the150ms pacing applies to dual iTunes/Rockbox
copies. A short process sample spent substantial time in file timestamp
preservation, which can wait for queued writes that the subsequent fsync would
otherwise drain. Removing that call has no measured gain yet. The source
preflight also hashes new sources before their later stable acquisition; any
change must preserve source-change/refusal and rollback semantics.

No runtime optimization was made from this observation. An eventual scoped
timing comparison must include copy, metadata, flush, publication and readback,
with identical data/durability checks. Ordinary Eject subsequently completed
in1.106seconds. Repeat Export/Sync and firmware playback remain separate checks.
Compact physical metrics and the code audit are in the private current gate's
`connected-ipod-workflow/` area; [HANDOFF](HANDOFF.md) records acceptance status.

## Repeated iTunes omissions — November18 source

Full export and incremental additions previously acquired an entire unsupported
WAV before rejecting its format. Omission-bearing playlists must reevaluate
eligibility on explicit Sync, so repeating this work can reread large collections
without exporting another song. Definite unsupported PCM/float WAVs now use a
bounded no-follow RIFF header rejection before acquisition: at most128chunk
headers and40format bytes, with stable canonical/descriptor/path identity.
Compatible, unknown, malformed or unstable headers retain ordinary complete
acquisition and hashing. The helper cannot admit a source or certify content.

The generated512MiB sparse-WAV control reads only the small header and no audio;
actual full/incremental planners acquire zero deliberately omitted payloads.
Repeat Sync retains unchanged supported sources, while a same-path PCM24→16
replacement receives a fresh full capture and is added. This is isolated source
evidence, not a physical USB throughput or firmware-playback measurement. Exact
release/check/install and connected-device acceptance status is in [HANDOFF](HANDOFF.md).

## Tag save readback — November16 source

Sonny reports similar long delays adding and removing one temporary House
assignment. Both writes and cleanup verify exactly; no other song/tag data or
schema/XML changed. The add's native row timestamp to committed-receipt mtime
was0.783s. This interval excludes gesture admission, complete library reload and
GUI publication; installed171 has no detailed command trace for that action.
Its log shows a3296-song reload, not enough to attribute total gesture latency.

Source tag readback now uses the same database-only reader as metadata saves.
It retains complete fresh tags, intelligent memberships, cues and metadata while
avoiding unrelated audio/share path resolution. Existing canonical scalars reuse
only exact unambiguous source/native ID/UUID/raw-path matches; fresh fields and
musical arrays are not copied from the old Library. Selected alias validation,
native authority/recovery/backup/SQL and fresh DB capture remain unchanged.
The app's general reader injection no longer overrides this default. A new
regression reproduces the old resolving call and verifies the fresh smart/cue/tag
result and selected guard; existing identity-conflict tests protect scalar reuse.
The complete render gate passes3,414cases with zero skips/expected failures and
all780 frozen inputs exact. No installed or end-to-end speedup is claimed.

## Shared-library completion constraints — November13

Correctness fixes must preserve the smaller-save, fair-worker and selected-export
work below. At this November13 checkpoint, the completion work remained uninstalled;
[handoff](HANDOFF.md) records the current source/install state and remaining
native-profile gaps.

Playlist-removal overlay capture groups selected logical aliases by native
ID/UUID and processes each playlist/native target once. Generated32/128/512/2048
alias captures each performed one direct native-key lookup, with acknowledgement
using one direct lookup per selected alias and full-library iteration refused.
The2048-alias capture took3.54ms in one observation during the full gate; this
is a helper measurement, not GUI/storage/native-save latency. No native SQL,
audio work or additional library inventory pass was added. Compact evidence is
`ordinary-overlay-alias-gate/selected-cost.json` under the private audit root.

Metadata refresh retains only native ID/UUID/raw path/title/artist scalars during
the existing browser inventory pass. Its existing read worker compares these
inputs; cue/tag/BPM-only changes do not schedule metadata derivation. Unchanged
refreshes and own acknowledgements start no derivation pass. Dirty batches reuse
the prepared artist registries and freeze them in their worker. Only a changed
global canonical/trusted-name context expands derivation to other rows, because
their effective names can change without a raw-row change. An explicit global
cleanup-policy or artist-merge gesture also needs a global plan.

Refresh reconciliation clones each changed override map at most once per batch;
it must not copy an entire map for each song. Source selection, scope changes
and refresh reconciliation commit together before publication. Existing title
rule/drop-word controls persist one policy/native-intention generation, then
refresh presentation; unchanged gestures perform no save or view rebuild.

Metadata acknowledgement and the dormant native-only Collection-removal route
use an explicit database-only read mode. They avoid unrelated audio/share path
resolution. A pure helper reuses already-known canonical path scalars only for
the exact same source/native ID/UUID/raw path, with no ambiguity or binding issue.
It copies no Library or musical arrays. Tests deny media filesystem operations
and exercise real generated encrypted native catalogs. Removal admission stays
off until lossless native Restore is established. Fresh DB/catalog, identity,
authority, writer, lock and recovery checks remain intact.

Generated RAM fixtures with4,000 scalar map entries measured the final100-dirty-
row batch at4.568ms median versus12.976ms for repeated map copying (about2.84×).
The final batch also validates exact affected origins and adopts raw provenance;
it copies two maps total versus200, preparing title rules once. No-op
reconciliation measured0.291µs and starts no scan, derivation, copy or source job.
One-title ACK helper measured23.209µs, including ownership and fulfilled-title
retirement; one-artist ACK helper measured4.250µs, retaining the manual map bound
to its exact ACK until independent native change. Each updates one origin, with
no artist derivation, registry build or new source job. The previous0.167µs ACK
helper only updated a scalar and performed less work; no ACK speedup is claimed.

These helper timings exclude the outer origins-map update, GUI publication,
storage saves, source worker/database and audio. They are not startup,
frame-latency or live-source timing claims. Compact evidence:
`native-fixture/metadata-refresh-performance.json` under the private November12
audit root listed in HANDOFF; the receipt binds final source/test hashes.

## Per-song saves, background fairness and export capture — November9

These are local source changes; [HANDOFF](HANDOFF.md) records their actual gate,
package and installed status. Earlier whole-state/global-priority/snapshot
findings below are dated baselines superseded within this bounded scope.

`StateRepository` moves dense saved analysis, prep edits and native-analysis jobs
into individually hashed canonical records, preserving queue order in the small
manifest. Changed records alone validate, detach, encode and publish under the
shared transaction; exact mutable-input comparison and fresh record epochs still
run. The64MiB logical-state ceiling, newest drafts, corruption recovery and restore
fence remain. Portable/rotating backups materialize full standalone JSON. Initial
migration and hourly/full backups still perform whole-state work; no O(1) save
or duration-independent storage claim follows.

| Generated workload | Previous median | Record-store median | Published bytes before → after |
| --- | ---: | ---: | ---: |
| Scalar save,500songs×500beats |331.13ms|171.86ms|19,642,036 → 88,508|
| One-song edit,same state |350.40ms|170.85ms|19,642,036 → 112,604|
| No-op,same state |134.12ms|142.93ms|0 → 0|
| Retire queue head,256jobs×500rows |148.89ms|92.57ms|Changed manifest/records only|

Five alternating paired warm samples exclude initial setup and rotating backup.
No-op adds fresh per-record observations; incoming deep equality still visits
all mutable records. Stable job identities preserve payload reuse after head
removal/reorder. Failure tests cover changed/retired records plus manifest and
commit boundaries, corrupt-live-state restore, source-independent backup exports
and stale writers. These are generated disposable-state measurements, not live
interaction/compositor or source-write latency.

The analysis/source scheduler offers bounded source turns between completed local
waveform/BPM/key phases while retaining each song's prerequisites and the one
source writer. Its bound is admission/phase based, not elapsed time: active DSP,
native ownership, recovery, authority and reserved prerequisite work still defer
ineligible jobs. Interactive Tags retain their honest active-phase busy state;
this change introduces no new durable tag-intent queue.

Explicit CDJ/Rockbox exports capture selected records into immutable bytes before
dispatch, retaining every source/audio/analysis path separately. Stock/dual/resync
need the complete lightweight identity/display and playlist inventory to resolve
managed device membership, but omit unused cue/reference/catalog graphs. Worker
objects are private. Separate drops retain separate captured generations; one
drop's selected playlists share one plan. Complete overlap guards survive through
planning and publication, including cleanup-only and no-copy work.

Seven alternating generated captures (4,000songs,100playlists,20,000memberships,
32cues+32references per song; select one200-member playlist) measured whole-Library
copy295.30ms versus selected capture25.25ms median. Traced allocation peak fell
77,929,872 → 3,154,289bytes while retaining all8,001source/analysis paths. This
measures capture only, excluding worker materialization, native DB reads, actual
copy/commit, device throughput and startup readiness. Concurrent development
work may share CPU; all wall/CPU samples remain retained. The unchanged1,500ms
startup target has no fresh passing result from these component improvements.

Private measurements/reviews are under
`~/Library/Logs/Soundbox/performance-improvements-2026-11-09/`. No fixture opens a
live source, device or AppData. Sonny accepted the current visuals and deferred
further public splitting; these changes preserve eager ordinary controls.

## Cleanup readiness — November 8

The cleanup prepares ordinary desktop dependencies, admitted provider code,
Settings and Artists before interaction. Track Info constructs both tabs and
resolves selected artwork before presentation; Graveyard starts its source-bound
worker after materialization/verification. Device pages/backends stay on demand.
No whole-library audio/image preload, source authority or state schema changes.

Signed personal 169 and installed 167 were measured serially on the same awake/AC
session with disposable 3,740-row metadata: 20 warm launches each, including process
bootstrap, selection/search and synchronous Qt paint. This excludes fresh-source,
compositor, actual audio loading and physical-device readiness.

| Proxy | Installed 167 | Prepared personal 169 |
| --- | ---: | ---: |
| Warm median | 1,902.77ms | 3,370.01ms |
| Warm p95 | 2,088.46ms | 3,474.54ms |
| First prepared-row cache miss | 2,236.95ms | 4,178.77ms |
| Peak RSS, median | not instrumented | 478.00MB |

Eager readiness adds startup/RAM work; it is not a startup-speed improvement.
Both cohorts fail the unchanged 1,500ms proxy target. The candidate passes every
readiness assertion with all 3,740 Artists rows built, ordinary dependencies warm,
and zero initialized device surfaces/backends/converters. A separate 20,000-row,
five-warm-launch candidate cohort passes readiness at 4,770.69ms median / 4,803.16ms
p95 and 691.03MB median peak RSS; its cache miss is 4,817.02ms. These are synthetic
package measurements, not live-library or hardware acceptance.
Exact reports, failed attempts and review receipts remain private; [HANDOFF](HANDOFF.md)
records source/package/installed status and the separate architecture work.

## Bottleneck and responsiveness follow-up — November4

Sonny requested a fresh whole-codebase bottleneck investigation with three agents,
including whether previous optimizations hurt everyday use. The follow-up targets
GUI/browser/Settings/artwork, source/persistence/analysis queues, device planners,
and player cache loading. A structural census records 184 first-party runtime
files, including the entrypoint; semantic review and fresh profiles are targeted,
not an every-line audit of all 76,000-plus lines or pinned dependencies. The
workloads below use generated disposable state or copies of retained offline
evidence. No real-app input, musical-source/device writes, or new native
authority belongs to these measurements. HANDOFF distinguishes source/package
validation from installed behavior.

### Largest costs and tradeoffs, ranked by interaction impact

1. **Whole-state saves on the GUI thread.** Every changed save validates,
   detaches and encodes the complete library, including saved grids. A retained
   November4 state copy (1.96MB,16 analysis records/5993 beats) measured28.37ms
   per changed save before this follow-up. Generated1000-song/500-beat state
   measured980.56ms, versus290.86ms for an unchanged save. These are repository
   timings, excluding the surrounding UI callback. The scalar-first comparison
   below helps, but per-song durable storage and ordered publication are the next
   architectural target. Preserve persist-before-presentation, rollback, restore
   fencing and exact source-intent ownership. The existing64MiB ceiling also
   bounds dense saved grids; raising the ceiling alone does not solve the stall.
2. **Settings first-use construction.** The previous demand-loading change moved
   work from launch to the first Settings click. Five alternating fresh-process
   offscreen samples measured858.57ms before this follow-up and579.89ms after
   event filtering alone. Repeat opens cost about4.5ms. Sonny explicitly rejected
   that first-click delay and requested Settings ready to use; the source now
   prepares it during startup. Device pages and export backends remain on demand.
3. **Global analysis queue priority delays unrelated source edits.** Since the
   November3 analysis-first change, any matching queued analysis can hold tags
   and other source jobs even while no detector worker runs. This implements the
   requested waveform→BPM→key order, but exposes a fairness cost for long batches.
   A future change should bind dependencies per song and measure bounded fairness
   while retaining accepted source ordering and the single writer/lock/journal.
   Local DSP's device-operation deferral is older; concurrency requires separate
   resource/storage evidence, not simply removing that gate.
4. **Explicit export still freezes an entire desktop library on the GUI.** A
   generated4000-song/100-playlist/20000-membership snapshot costs about102ms
   with8 display cues and8 native references per song,324ms with32 of each.
   USB-settings saves now capture only source paths. Playlist export still needs
   an explicit immutable selected snapshot plus complete source-overlap guards;
   moving a live mutable library copy into a worker would break that ownership.
5. **Large native work remains substantial in workers.** A verified encrypted
   backup measured574.93ms and full semantic inventory751.56ms on a retained
   private66.25MB source-copy fixture; SHA-256 alone36.50ms. These independent
   primitive measurements are not additive end-to-end timings. Stock planning
   still renders and semantically validates its whole native database. Preserve
   fresh app/source/authority/recovery guards rather than buying speed by
   removing them. Physical media throughput, cloud stalls, firmware and eject
   acceptance remain unmeasured in this pass.

### First implementation batch

| Workload | Before | After | Scope |
| --- | ---: | ---: | --- |
|3740 browser rows,500 manual drafts×480beats |214.50ms|78.81ms|Warmed row projection; full draft validation retained, discarded beat deep copies removed |
|Same browser,11 dense drafts×3200beats |44.43ms|22.66ms|No-draft13.68→14.11ms is near noise |
|Analysis acknowledgement,256 pending500-beat prep jobs |85.04ms|1.59ms|Persistence/paint stubbed; actual selected-job attachment and rollback exercised |
|Changed scalar save,retained state copy |28.66ms|21.50ms|Five alternating samples; nested no-op comparison deferred until after scalars |
|Changed scalar save,500 generated analyses |460.04ms|323.12ms|Full validation,detachment,encoding and atomic publication remain |
|Stock no-op plan,3000 empty playlists |744.58ms|512.10ms|Six alternating samples; remove only duplicate final-output parsing |
|Stock plan,50000 overlapping memberships |4013.40ms|1206.89ms|Four alternating samples against the preceding source; exact no-op/output bytes |
|USB settings GUI setup,4000songs/8cues+8references each |122.58ms|2.10ms|Eight alternating samples; traced peak29.35MB→0.87MB |
|Avatar groups,3740songs/300artists |296.80ms|49.75ms|Policy reads4040→301; selected image paths still check fresh policy |
|First Settings click,complete6tabs |561.45ms|12.50ms|Five alternating source/offscreen pairs; all controls ready before Show |
|One-hour generated compact waveform cache load |160.12ms|39.48ms|Seven samples per cohort; no audio decode,native upload or compositor |
|Same exact3.6M-cell range reduction |119.42ms|5.41ms|Ten alternating samples,all output sections byte-exact |

Range reduction reuses a finer level only when cell divisors divide exactly;
neutral extrema preserve partial song-end cells. Irregular levels, reordered
requests, signed extremes and odd/mismatched byte lengths retain their old
contract. No cache schema, DSP, waveform palette, clocks or geometry changes.
Analysis completion retains the previous immutable queue reference and detaches
only its selected job; failed persistence restores the original references and
newer cue/grid drafts survive. Stock validation consumes the renderer's parsed
final patched bytes, with every track/playlist/artwork semantic check retained.
The state no-op hint still distinguishes types, numeric representation, signed
zero and map order; current file identity and durable publication remain fresh.

Unchanged saves measured8.19→8.05ms on the retained state and140.97→128.66ms
on500 generated analyses. Earlier cohorts remain retained;
these samples vary with machine scheduling and are not a claimed fixed latency.
Eager Settings deliberately adds startup work: the same source/offscreen
3740-row Qt-paint proxy measured813.15→1382.48ms, excluding process bootstrap.
This supersedes optimizing startup by deferring its construction to the click.
No tabs are deferred until later use. Signed-package startup has separate scope.

Selected Track Info preparation is already bounded: a generated500px JPEG in
an isolated themed Cocoa dialog measured25.11ms median to first-show, with its
cover ready on every first frame. A deliberately held60ms shared state lock
raised it to83.18ms. A4096px test image cost166.57ms, but a read-only header census
found all3646 current cached JPEGs at most500px. The native wait cursor measured
0.29ms. The first dialog in a fresh QApplication cost159.20ms including cold
fonts/native setup; an already constructed MainWindow normally pays those earlier.
These do not measure actual user-click/compositor timing or excuse observed live
lag. No speculative placeholder, artwork pop-in or unsafe lock bypass was added.
The browser/event-filter improvements also apply to this existing dialog path.

Stock source observations are scoped to one planning phase and keyed by original
path; the later membership phase reobserves and rejects disappeared/redirected
sources. Acquisition, compatibility, source content/epochs and device commit
proofs stay fresh. Repeated playlist memberships and distinct source identities
retain their order and metadata. USB settings still capture every source/native
analysis path for overlap rejection, without unrelated musical data copies.

The confirmed recent responsiveness tradeoff is first-open Settings latency.
The earlier no-op state comparison also walked large analysis graphs before
discovering a changed scalar. Browser draft copies, whole-export snapshots and
stock planner repetition predate the last demand-loading release. These are
specific provenance findings, not a claim that every workflow is regression-free.
No blanket “quality of life unchanged” conclusion follows from fixture timings.

Lean probes, all retained samples, profiles and review receipts are private under
`~/Library/Logs/Soundbox/bottleneck-audit-2026-11-04/`. Failed and intermediate
attempts stay distinguishable from final validation. See HANDOFF for the final
gate, build and installation boundary.

The complete2795-case gate passes (2729 offscreen457.657s/66Cocoa69.078s),
with both signed profiles/entrypoints/native sections matching tested source and
both isolated native/process/RAM-SQLCipher probes passing. Populated/Settings/
packaged-empty renders were inspected. The interrupted inventory attempt and
stale lazy-Settings test failure remain retained; the latter now checks saved
controls/colors before first show and passes its457-case UI replay and final gate.

Fresh paired signed packages,20 warm launches each on3740 generated rows:
then-installed165 median1158.431ms/p951212.583ms versus the now-installed167
median1783.129ms/p951820.624ms; first row-cache miss1263.154→1944.436ms.
This intentionally adds about625ms median for ready Settings. The old1500ms
startup-proxy target is unchanged and reports false for build167. No slow
sample is discarded. Paint/search/selection/native readiness pass; Settings is
created before display, while device pages/backends, NumPy and stock converter
remain uninitialized. Fresh-source/compositor/persistent-icon/device readiness
and machine-cold launch remain outside this proxy. HANDOFF records installed
fingerprint3fb0ff60… and publication status. Sonny approved installation/reopen after
closing SoundBox on November5. Build167 was installed00:02:43.967, reopened
00:03:38.713 and finally verified00:07:51.543968 with exact fresh library/all10 authorities/
art indexes/white icon. Newer saved columns were preserved. Installed isolated
checks pass; normal startup loads3296 native tracks/85playlists, schedules0prep
and fetches0art. No real-app input/termination or musical source/device writes
were used. Six protected bases remain; redundant owned packaging is retired.

## Whole-app audit — November4 after iPod review

This is the preceding build165 checkpoint. Its lazy-Settings policy and startup
figures are historical; the explicit ready-at-launch requirement and build167
measurements above supersede them. Device pages remain on demand.

Sonny requested another multi-agent pass across startup, browser/artwork,
background/audio lifecycle and persistence/source transactions, with particular
attention to work performed without an attached USB/iPod. The earlier installed
runtime 26f15fb4… is the comparison baseline; HANDOFF records delivery of the
replacement. Generated fixtures exercise changes without controlling the real
apps or mutating real music, native libraries or attached devices.

| Finding | Correction and measured scope |
| --- | --- |
| Hidden Settings and device pages construct thousands of widgets during launch | Build Settings on first open and each device page on first requested view. Passive resize/theme/backup/shutdown paths inspect existing pages without constructing them. Export stays disabled without capable targets; detection stays active |
| Artist title cleanup repeatedly evicts its regular expressions from Python's shared cache | Bounded1024-artist edge-pattern cache preserves exact output;3600 rows/300artists compile7201→601patterns,0.7445→0.0733s in the isolated helper sample |
| Identical model filter/sort requests reset selection and redo work | Skip unchanged requests; edits refresh cached search/sort fields, including artwork sort after in-place refresh |
| Every artwork assignment deep-copies unrelated nested records | Copy namespace maps and replace changed entries while retaining publication snapshots, rollback and concurrency checks. One eligibility pass reads source policy once; publication still checks current policy |
| Repeated queue notifications traverse the whole native catalog | Coalesce GUI wakeups per event turn;100notifications/4000 tracks become one discovery,4000visits and two authority reads instead of 100discoveries/400000visits/200reads. Explicit admission remains fresh |
| Busy analysis resumes schedule overlapping retry chains | One owned single-shot timer; an empty queue performs no busy probe or retry |
| Idle decoder wakes every3ms forever | Event-epoch waits park unloaded/EOF/paused-full-ring states. Private paired one-second fixtures show245–272wait returns→0. Active buffer pacing, cloud cancellation and render callback remain unchanged |
| Unchanged state/history and already-published Grid packets are serialized/staged again | State no-op comparison preserves JSON types, numeric representation and map order under current file identity; Graveyard compares exact bytes inside its lock; Grid compares exact target bytes/mode/mtime and validates any existing staging evidence before skipping publication. Fresh recovery/source/apps/authority guards remain |
| Device import trusts equal destination size and overwrites a fallback collision | Acquire/hash source once, compare exact duplicates and publish with exclusive destination creation; preserve occupied names, races and cancellation |
| Conversion recovery can replace a newly changed original after its first hash | Bind the existing digest pass to stable source descriptor/path epochs and recheck after the fresh lease guard immediately before replacement/displacement; retain journal and original on conflict |
| Full native-reader classification repeats queries | Shared active/audio classification plus bounded scalar relationship loading reduces the generated reader fixture from 8to6SELECTs; ordered complete DTOs remain exact |
| Optional derived cache corruption aborts startup | Bound captures and validate historical library/browser shapes; malformed derived data falls back to reconstruction without changing canonical state |
| Standalone-track menu still uses global device busy state | Bind that final export action to its own candidate target, preserving unrelated-drive export during formatting |
| Lazy Settings could lose automatic connected-account refresh or cached likes | Lightweight provider root binding, lazy shared cached metadata and a window-owned cancellable initial refresh preserve behavior. Settings attaches to that worker/result; token rotation remains serialized and manual retry remains available |
| Ordered tests expose a borrowed cue-button style wrapper of the wrong Qt type | Remove manual unpolish/polish immediately after the local stylesheet already repolishes the changed filled state. The ordered prefix reproduces the original error; ten private empty/filled/pressed/add/remove frames are pixel-exact without the duplicate calls |

The unchanged60-second waveform-preparation metadata pass measured198.8ms wall,
195.1ms CPU for3740synthetic tracks, with zero header/readiness/sidecar/audio reads
and zero tasks. It remains: replacing it without a complete change-key proof would
trade correctness for a small periodic cost. Fresh source, native-app, payload,
recovery and atomic publication checks remain at their authority boundaries.
No removal of required atomic saves or native transaction stages is claimed.

The baseline signed app measured3117.471ms median and 3137.196ms p95 across 20 warm
launches of 3740 generated browser rows (4252.719ms first row-cache miss). An
intermediate source process measured1046.126ms median and 1074.753ms p95
(1146.722ms row-cache miss). These include process/bootstrap, imports, native
player initialization, selection/search and a synchronous Qt paint proxy. They
exclude fresh source verification, compositor presentation and physical-device
readiness. Source CLI isolation also excludes persistent Finder/Dock icon work;
the final signed-package comparison belongs in HANDOFF. All warm results are
retained, including outliers; no slow launches are discarded.

The final paired signed apps use the same 3740-row workload after all tests/builds,
with an unlocked console and 20 warm launches each:

| Metric | Prior signed26f15fb4… | Current signed7ffde716… |
| --- | ---: | ---: |
| Warm median |3151.935ms|1203.228ms|
| Warm p95 |3178.523ms|1237.419ms|
| First row-cache miss |4185.058ms|1452.147ms|

Median reduction 61.826%; all current readiness checks pass with no device surfaces/
backends, Settings, NumPy or stock-converter initialization. This remains a
snapshot-browser/Qt paint proxy, excluding fresh source, compositor/persistent
icon/device readiness and machine-cold launch. Normal actual reopen separately
loads3296 native tracks/85 playlists with 0prep/0art fetches. The complete2779-case
gate, both signed profiles/code parity and three isolated native/process/RAM-SQLCipher
probes pass. Installed 22:50:20, reopened 22:51:50, verified 22:55:01 with exact fresh
library/all 10 authorities/art indexes/white icon. Source was local/unpublished
at installation; Sonny's subsequent wrap-up authorizes publication without
changing the installed signed bytes or running session.

Private module-depth inventories, peer reviews, counters, failures, final gates
and package/startup receipts live under
`~/Library/Logs/Soundbox/soundbox-deep-audit-2026-11-04/`. This is a first-party
runtime audit with explicit review depth, not a new audit/acceptance of every
pinned dependency, codec, DSP algorithm, firmware or hardware configuration.
The source census covers 184 first-party Python/native files:129 reported full
reads,29 targeted reviews and 26same-day scoped USB/iPod reviews with matching
hashes. Large app/player/native-renderer/source-proof bodies marked targeted
remain targeted, not every-line correctness proofs. The first complete offscreen
attempt ran2712 cases with two cue-style errors; its native phase did not run.
That failed attempt and the shorter ordered reproduction remain retained.

The separate [UI polish review](ui-polish-audit.md) covers incomplete first
frames, artwork publication, text/DPR/layout and navigation resets. Its fixes
and remaining GUI-thread costs are separate from the dated measurements below;
consult HANDOFF for installation status.

## iPod deep audit — November4 after USB testing

Sonny requested the same agent-assisted inspection for stock, Rockbox-only and
composite iPods. He has no iPod available. Three agents inspected those backends
and cross-reviewed each other and the desktop changes. Validation uses generated
temporary fixtures and writable copies of retained offline captures; no actual
iPod, source library or running app is controlled. The source changes below were
delivered in the preceding personal build165/runtime26f15fb4… and remain in the
current whole-app build. Its source was local/unpublished at that iPod checkpoint;
the subsequent whole-app wrap-up authorizes publication.
All2695tests pass (2631offscreen/64Cocoa), both signed profiles match tested code,
and package/installed native+SQLCipher probes pass. Fresh state/all10authorities/
art indexes/white icon remained exact. Precise dated identities/times stay in
`~/Library/Logs/Soundbox/ipod-deep-audit-2026-11-04/`; HANDOFF records the current app.

| Finding | Correction and evidence |
| --- | --- |
| Stock dashboard parsed, read its header and hashed separate live database reads | Capture bounded native catalogs once per worker; parse/header/hash share captured bytes. Dashboard+status uses two complete DB reads instead of three plus a header. ArtworkDB uses two reads instead of one so same-epoch FAT edits are detected. Captures never become persistent freshness or commit authority |
| Every stock-only no-op reopened selected source audio | A matching native shadow can retain its accepted raw-source SHA, complete Mac epoch and MA446 compatibility result. Fixture no-op performs zero acquisitions/format probes and zero retained-media hashes; legacy shadow reads two sources once, one changed ctime reads one. Legacy no-op enriches only disposable Mac proof fields, preserving generation/commit time |
| Stock curated artwork was read once for segment hashes and again for concatenation | Verify segment and combined digests in the same read; two opens become one per segment |
| Equal-size composite dedup searched every earlier candidate | Lazy digest sets preserve first occurrence and exact byte semantics. At1024unique same-size synthetic files,523776pairwise comparisons become zero, with one hash per colliding source; a unique-size singleton needs no hash |
| Overlapping playlists repeatedly computed source presence, canonical paths and effective display | Per-operation observations/signature encoding reduce a20-playlist/60-membership fixture from300to182source `is_file` calls and120to4display-helper calls, preserving both source contracts and all ordered references |
| Composite historical identity and publication planning reread the same small M3U8 | Reuse the bounded captured bytes within that plan; fresh commit-time hash guards remain. A growing file cannot bypass the1MiB read cap |
| Partial Rockbox exports discarded other playlists' ownership of shared media/art | Union retained and selected owners; deletion preserves every remaining reference. Separate source identities with equal artist/title receive separate audio/artwork folders, including across separate exports |
| Different logical entries sharing one canonical source silently took the first display edits | Reject conflicting effective title/artist before writes; matching display still shares one payload. The first full-gate attempt was interrupted for this reachable last-review correction, then restarted |
| An adopted, unowned Rockbox file could be overwritten on a later edit | Refuse replacement without manifest ownership; keep unrelated files intact |
| Interrupted adoption trusted FAT timestamps and edge fingerprints | Freshly hash only the affected candidate with descriptor/path epochs and cancellation; middle edits cannot reuse old proof JSON, and no new per-file proof JSON is written |
| Explicit Rockbox Sync missed same-membership title/artist/source changes | Compare managed desired paths/display/profile and unique source metadata on explicit Sync, export affected playlists only, then prune only unreferenced owned paths. Unchanged explicit Sync uses source stats, zero payload reads and zero writes; idle badge remains a playlist-change hint |
| GUI admission read removable manifests, recovery and hardware facts | Use cached view/facts only for presentation; existing owned plan/read workers perform actual reads. Cached reorder/delete selection becomes a precondition compared against the planner's own single fresh manifest capture |
| A fast final scan could report success before queued retirement, or miss a newly connected finished handler | The already-connected scan owner publishes rows and retires before consuming finalization callbacks. At this dated checkpoint, unrelated iPods waited for the completion dialog; current routine completion is quiet and releases after owned finalization (HANDOFF) |
| The Info button on an iPod page could edit a stale desktop song | Keep the action within the displayed device context; iPod details remain in its device view |
| Parent aliases could redirect small metadata reads into reserved/foreign trees | Guard native/manifest/playlist paths before payload reads, use bounded pinned captures, reject aliases and preserve the PIONEER exclusion |

The dedup CPU sample measured0.458885s→0.020753s with preverified digests;
this isolates helper CPU and is not an end-to-end speedup claim. The remaining
182source-presence calls include shared stock/filesystem planning: this is not
zero metadata I/O. Normal composite committed-inventory reuse remains distinct
from a fresh integrity proof; stock-only ctime drift deliberately reacquires.
Changed media still materializes in planning and again at commit, one source plus
derivative at a time. Classic150ms pacing, fresh affected-file guards, recovery,
manifest/database-last publication and serialized iPod completion remain. iPod
polls still inspect their small native databases and selected source metadata;
this does not remove the independent iPod status semantics.

No physical throughput, firmware playback/artwork, cloud latency, reconnect/eject
or historical hardware failure gate is accepted by this audit. Existing source
immutability, MA446/codec gates, single-copy identities, native authorities and
firmware-owned config/TCD boundaries remain. Exact counters/test commands and
peer review are retained privately under
`~/Library/Logs/Soundbox/ipod-deep-audit-2026-11-04/`.

## USB deep audit — November4 later testing

Sonny reported an unfinished format, unrelated USB Export being unavailable and
missing covers in USB Track Info, then requested three agents across the generic
USB implementation. He separately authorized fresh FAT32/exFAT tests on the two
connected USBs while leaving SoundBox open. The following corrections are installed in personal build165; their source
was local and unpublished at that USB checkpoint. The subsequent whole-app
wrap-up authorizes publication.

| Finding | Correction and evidence |
| --- | --- |
| Format confirmation raised `AttributeError` on `dialog.Accepted`; erase never started and `_exporting` stayed set | Use the explicit QDialog enum and failure-safe dialog/worker retirement; direct regression reaches actual backend admission and covers reject/error/shutdown |
| One formatting drive blocked every export | Separate format ownership, reserve all captured physical-disk volumes, admit another USB into the ordinary export lane, and preserve its worker/progress during format completion |
| Native erase stages were hidden | Stream bounded diskutil output and show useful unmount/erase/mount stages; reap the process even after output-consumer failure, without killing a running erase |
| USB Info supplied no artwork loader | Capture only the selected native cover, capped at8MiB, decode off-thread and retire before presentation; no Mac-cache substitution |
| Path-indexed browser rows conflated native records sharing audio | Carry native membership IDs separately; retain distinct metadata/covers and native USB art badges; container selection cannot fall through to stale desktop Info |
| Cross-capture checks missed previously absent native companions | Add complete before/after presence checks for WAL/catalog/profile files and preserve exact byte rereads: actual FAT32 and exFAT probes proved changed contents can retain equal epochs |
| Local completed-proof maps rewrote all earlier assets per copy and trusted coarse file epochs | Remove redundant per-asset completed-proof writes; existing journal hashes and fresh affected-byte checks drive interrupted recovery/adoption; historical proof artifacts remain untouched |
| Incremental export cast logical imported IDs to integers and looked up manual art under native IDs | Use native IDs for catalog identity, logical IDs for curated artwork; full initial/repeat/update regression |
| Initial candidate audio was hashed in two consecutive passes | Bind captured size/SHA in the existing make-plan hash pass; corruption still fails before USB writes |
| Cancellation did not reach several metadata/native/baseline phases | Propagate checks into catalog inspection, settings/upgrade/initialization and chunked baseline hashing |
| Export target validation performed a mounted-path stat on the GUI thread | Use cached selection facts for admission; worker-side fresh physical/path checks remain authoritative |
| Unrelated remounts cancelled a waiting USB export | Bind scan deferral to the requested USB; its own remount still cancels |
| Reopening Export retained every closed dialog's widget tree | Delete accepted/rejected dialogs after detaching the selected values; repeated-close regression checks parent ownership |
| Desktop artwork publication could refresh a USB dialog with the same numeric ID | Mark USB dialogs outside desktop artwork refresh, preserving the USB read lane |

The former completed-map serialization for2000synthetic assets totals
344,710,388bytes; the final implementation performs zero per-asset completed-proof
writes. This is removed serialization, not a physical throughput benchmark. The
intermediate append ledger was discarded after cross-review established that its
stat-only reuse proof was unsafe on FAT filesystems. Actual FAT32 and exFAT each
allowed12/12same-size64-byte rewrites with equal device/inode/size/mtime/ctime.
Exact catalog byte rereads remain necessary; new regressions reject changed
catalogs and interrupted payloads even when all reported epochs stay equal.
Offscreen Summary/Info renders show the selected USB cover before first show.
An introduced browser-membership helper initially leaked into manifest views and
made the hardware repeat write a manifest; central projection and initial/merge/
settings/no-op regressions caught and corrected it. This was an audit regression,
not a claimed defect of build163; failed evidence remains retained.

Review covered generic-USB GUI admission/format/browser/info, device detection and
worker routing, `usb_format`, `cdj_usb`, `cdj_source`, `cdj_catalog`, `cdj_export`,
`cdj_incremental`, `cdj_transaction`, and native wrapper inspect/render/merge call
sites. Pinned vendor integrity remains independently gated; this is not an
exhaustive dependency/DSP audit or a new iPod/player-firmware acceptance.
Changed-set audio staging, two fresh desktop metadata captures, bounded small
catalog/manifest checks and affected-file recovery hashes remain. Two small
manifest reads could eventually reuse a captured model with the existing later
fresh guards; this audit does not claim every duplicate metadata read is removed. OS-blocked reads stop
only when the OS returns. Exact dated physical timings, tests and package status
remain in private `usb-deep-audit-2026-11-04` receipts; HANDOFF records the current
installation and limits. Both signed
profiles/code parity and package/installed isolated backend probes pass. All2639
tests pass:2575offscreen and64native after the unlocked, powered retry. Earlier
cadence/locked-screen failures remain retained and thresholds unchanged. Build165
was installed19:57:13, reopened19:59:02 and verified20:00:23 with exact fresh
library/authorities/art indexes/white icon. No source was published at that USB
installation checkpoint; later publication does not restamp the installed app.

## Generic USB follow-up — November4

The preceding build163 USB pass removed generic USB drift/status comparisons from
detection, idle polling and source-only edit events, along with their
export-playlist capture and native catalog freshness reads. USB catalogs load for explicit browser requests or the
small native-library merge required by selected export. Export is additive to
unrelated existing playlists, avoids Collection/Artists construction and removes
one duplicate catalog read in planning. Fresh source captures now query selected
cue and playlist-membership rows in bounded chunks and skip unrelated smart-list
evaluation; the complete small ancestry tree and active-track identity guards
remain. Explicit USB/tab clicks refresh once, including an already-checked tab;
internal restoration and row expansion do not. Successful writes clear stale song
rows with a browse notice while preserving settings drafts/view state. Completion
and Eject start no final USB read or post-result scan. Existing device worker
ownership and normal macOS eject remain intact;
iPod/Rockbox sync behavior is separate.

Formatting uses a quick same-filesystem erase when the current disk already has
a matching FAT32/exFAT partition type and one full-capacity first MBR partition.
Other layouts/filesystem changes still repartition. Physical/source checks and
postconditions remain; diskutil probe and erase phases now log their durations.
Fresh native initialization removes the local renderer lock from its candidate
inventory. Hardware timings are in the dated section below; HANDOFF records
current final validation/provenance. The older
synthetic measurements below do not benchmark this newer USB workflow.

### Physical USB fixture timings — November4

Sonny explicitly authorized formatting/export tests on TEST-1 and TEST-2. Both
were formatted, initialized and exercised through the real acquisition, native
renderer and transaction lane using frozen fixture metadata rather than a live
source publisher. These small synthetic payloads do not benchmark full libraries
or player playback. FAT32 TEST-1 used same-filesystem quick erase; exFAT TEST-2
used whole-disk erase because its prior partition did not meet the conservative
full-capacity reuse bound. Existing source/physical/postcondition guards remained.

| Phase | TEST-1 (FAT32) | TEST-2 (exFAT) |
| --- | ---: | ---: |
| Format including fresh guards/postconditions | 13.118s | 2.889s |
| Prepare empty native catalogs/settings | 1.401s | 1.455s |
| Publish empty native catalogs/settings | 2.141s | 0.098s |
| First fixture playlist: plan + commit | 2.495s + 6.389s | 2.507s + 0.198s |
| Selected update preserving second playlist: plan + commit | 1.089s + 3.159s | 1.672s + 0.192s |
| Unchanged selected repeat: plan + commit | 0.270s + 0.003s | 0.247s + 0.001s |

The macOS erase commands themselves took11.840s and1.812s respectively. There is
no matched old-code hardware timing, so these numbers establish current cost,
not a before/after speedup. During selected update and repeat, guards rejected
opening unselected source audio or retained USB audio. The unchanged repeat had
no writes, other playlist memberships survived, the final fixture payload hash
matched and no pending journal remained. The first hardware attempt exposed an
unpublished renderer lock during initialization; the corrected native helper and
new real initialization regression passed before these successful results.
Private exact receipts and commands live under
`~/Library/Logs/Soundbox/usb-simplification-2026-11-04/`. There was no desktop-source
write, real-app input or iPod
physical acceptance.

## Correctness and remaining-cost pass, November3 night

Sonny requested working from the remaining simple improvements upward, with
correctness review throughout. This pass inventories178 first-party Python/native
app-tree source files,39 changed since the preceding d5cb4451… installation, plus
the existing eight build/harness paths. Review depth varies and is recorded per
file; this is not an assertion that every dependency/research script or hardware
failure has been exhaustively proved. Three subagents implemented separate
areas, then reviewed each other's work. Root reviewed publication/replay guards,
UI ownership, source acquisition and output semantics. Clang static analysis of
the native deck and process guard reports zero diagnostics; the normal native
runtime gate remains required. That2531-test source was installed as runtime
fc696312… after both signed profiles and isolated RAM-only native ORM/SQLCipher
checks passed. Actual normal startup reused3280analysis records, scheduled zero
preparation tasks, and preserved exact library/authority/icon state. Personal
regular-file bytes shrink by11,666,423 (~11.7MB). Exact dated provenance, retention
and validation are in [HANDOFF](HANDOFF.md).

### Correctness findings and closure

- Expected-SHA acquisition previously could associate old captured bytes with a
  replacement file's after-stat. Known and unknown digests now both require a
  stable before/opened/closed/after identity; canonical redirects are rechecked.
  One bounded instability retry remains. Metadata drift before capture can still
  succeed with exact bytes. Inode replacement, same-size rewrite/restored mtime,
  ctime drift, leaf/parent redirects, cancellation and cleanup have fault tests.
- A committed import membership followed by failed local acknowledgement could
  leave later playlist intentions on a stale base after restart. The accepted
  writer now exposes a source-read-only committed-edge proof: freshly verify the
  exact receipt/request/authority and all retained payload hashes, reconstruct
  the allowed plan on a TEMP copy, and compare fresh current catalog/XML to its
  final state. Only that proven edge rebases still-unstarted local intentions.
  Source/process/recovery checks remain fresh; no new publisher was introduced.
- Folder-delete Collection retention now captures exact descendant ContentIDs/
  UUIDs in the common worker and persists them before deleting. Actual writer
  guards recheck the complete set and active UUIDs. Failed local acknowledgement
  and restart preserve those intentions even with an empty browser. Retained
  legacy before-pairs can reconstruct pins despite later unrelated native edits;
  this historical proof is separate from strict causal rebasing. Captured UUID
  intentions survive verified payload retirement with permanent membership
  receipts and fresh current UUID checks. Legacy retired evidence missing the
  original UUID remains guarded rather than guessed. Relocation with the same
  ID/UUID remains valid. No arbitrary100000-song deletion cap was introduced.
- Completion rollback now restores original namespace references, including an
  existing empty queue. Shared-import rebasing clones its nested namespace before
  publication, so failed persistence cannot mutate the original later request.
  Touched analysis/import/conversion records remain detached; newer drafts win.
- Selected SQL readback initially exposed an ordering regression during review:
  an index could reverse native cue-reference/tie order. Final queries retain
  table traversal with NOT INDEXED, then exact Python identity filtering. Actual
  cue-reference output is equal under descending composite indexes, reverse scan
  mode and equal-time Memory cues with distinct colors. No cue policy changed.
- USB source_revision now includes the complete rendered global MyTag tree.
  Unassigned category/name/parent/sequence/order changes between independent
  captures can no longer escape the selected-song proof. Unrelated native
  counters do not invalidate an otherwise identical rendered observation.
- USB manifest model and baseline bytes come from one bounded capture. Those
  original bytes propagate through export/settings/initialization and make_plan;
  a late no-op carries the validated plan's baseline. Concurrent newer counters
  now reject before publication instead of being adopted for an older model.
- Explicit USB settings patches retain the captured original profile hash even
  though that path is intentionally allowed to change. A concurrent profile
  change/deletion before preparation, or change before commit, rejects without
  overwriting newer recognized or opaque bytes. Scope remains the accepted44
  settings; no automatic global propagation or firmware setting write was added.
- Catalog/default-photo selection changes no longer reenable actions while an
  operation is pending. Late callbacks check closed state before widget access.
  Parented catalogs, review/picker/suggestion dialogs and Track Info release
  their widget/image trees after owned work is drained. Explicit artist groups
  use a fresh avatar-only check, preserving newer real covers/manual blocks.

### Measured component changes

| Generated workload | Before | After |
| --- | ---: | ---: |
| Conversion acknowledgement,3202 results+3202 imports/2.282MB settings |40.904ms|0.186ms|
| Existing-media acknowledgement,same fixture |39.573ms|0.171ms|
| Tag projection,3200songs/256tags/9600links |20.540ms|4.081ms|
| Ordered native readback,60000cue+60000aggregate rows |111.327ms|4.105ms|
| Validated native retry,16jobs×3200beats |70.361ms|10.562ms|
| GUI dispatch snapshot,500saved songs×500beats |161.485ms|0.00175ms|
| Same GUI snapshot,traced Python peak |44.77MB|13KB|
| Fuzzy artist matching,1000similar names |1.735s|0.620s|
| Same fuzzy workload,traced Python peak |8.29MB|0.211MB|
|10000failed jobs,reversed retry selection |2.125s|0.242s|
|10000-job terminal transition |269.05ms|141.22ms|
| Same terminal transition,traced Python peak |15.67MB|7.86MB|
|1000keymap lookups,300randomized maps compared exactly |166.6ms|92.6ms|
| Artist catalog constructor,64distinct512px JPEGs |53.26ms|0.709ms|
| Default-photo constructor,same images |51.87ms|0.713ms|
| Atomic artist store,64avatars/3200rows/3defaults |456.23ms|58.55ms|
| Atomic artist block,same fixture |536.04ms|93.73ms|
| CDJ poll,1000tracks/100playlists/50100memberships |770.6ms|42.5ms|
| Incremental stock-art renderer,1500changed/5000free slots per format |1622ms|237ms|
| Complete repeated offline CDJ plan |586.3ms|146.4ms|
| Selected metadata ORM,10selected/6000tracks |310.5ms|3.73ms|

These are separate CPU/helper/fixture workloads, not additive end-to-end gains.
Conversion figures exclude source publication/local save; native readback keeps
its SQL traversal and excludes mandatory encrypted capture/packet work. The
selected-record copy moves to the worker; it is not eliminated from total work.
Catalog show-to-visible-thumbnails costs another3.64ms/3.61ms respectively on the
Qt paint proxy, versus2.68ms/2.48ms after the old expensive constructors. This is
not a compositor or live-app latency claim. Stock-art uses reduced1×1pixel slots
to isolate indexing cost; complete rendered fields/ArtworkDB bytes are equal,
but actual-device throughput is unmeasured.

The necessary new deletion preflight costs119ms on a private two-song encrypted
fixture; committed-edge proof120ms. Both remain in the existing worker lane.
Waveform feed removes repeated channel allocations with99 complete output cases
byte-exact. Stereo feed+finish improves only940.8→929.3ms (near noise);8channel
1037.9→981.9ms, with unchanged traced peak. No peak-memory gain, palette, timing,
decoder, musical detector or native publication profile change is claimed.

### Ownership, reuse and remaining design limits

Conversion/prep acknowledgements clone only touched namespaces/records; untouched
published entries are shared read-only under audited replacement contracts.
Analysis dispatch snapshots the top mapping and detaches only its actual
canonical record in the worker, preserving aliases and newest-draft merge.
Fuzzy candidate scoring remains quadratic in the worst case; retained suggestions
are bounded by the number of names. General queue helpers still return fully
detached graphs, because callers may mutate their nested preferences/epochs. Terminal completion reuses its validated
input and avoids a redundant full copy/validation; nonterminal isolation remains.
Failed/stale admission uses insertion-ordered identity mapping with pop/reinsert,
retaining retry order and final duplicate-UUID rejection.

Thumbnails decode only visible rows, one per event-loop turn, at the actual
display size/DPR. The cache targets64 images, pinning any larger visible
viewport; oversized visible sets shrink on later cache misses or dialog closure.
Offscreen eviction clears icons and revisiting reloads. Hidden/deleted dialogs
stop pending work. Artist batch writes normalize once and publish atomically,
preserving path-dependent default-photo transitions exactly in RAM before one
serialization. State publication checks staged ancestor membership instead of
every path pair; escape/symlink/tree checks, fsync, journal and rollback remain.

CDJ no-op reconstruction requires exact current policy, complete catalog/order/
membership/track identities, unchanged captured inputs/tags, Plus presence and
fresh matching committed DB/profile bytes. Independent source captures and
make_plan's target/recovery checks remain. A player-profile-only change gets
one full preflight/render and a manifest-only verified inventory refresh during
explicit export. It never rewrites DB/media/profile bytes or causes a poll to
write; the next unchanged export becomes a true no-op. Changing renderer
semantics requires bumping the explicit catalog policy, not borrowing an old
reconstruction proof. No exclusive ownership of native USB catalogs is implied.

Provider operations now accept optional stop/deadline scopes (60s default per
lookup), with retryable cancellation/timeouts rather than durable misses. Known
CPython SocketIO responses check each receive while preserving the adapter's
decompression/error/close/stream semantics. Timeout/ref restoration occurs before
pool release, never against a later borrower. Unknown adapters retain a checked
one-byte fallback. On the pinned urllib adapter,256KiB costs0.049→0.085ms and2MiB
0.386→0.647ms; the unknown fallback costs169ms/256KiB. Initial DNS/connect/headers
and opaque decoder/retry waits can exceed cooperative cancellation budgets;
there is no force-kill or guaranteed hard wall-clock deadline. Tests use fake
clocks/sockets and actual pinned adapters, with no live network traffic.

Cross-phase decoded-PCM sharing was profiled rather than assumed beneficial.
For generated180s WAV/MP3, capture costs20/5ms; decode49/129ms; waveform about1s;
BPM205/396ms; key134/217ms; native packet129/215ms. A shared PCM cache would add
up to384MB staging plus decoder/version/endpoint/finite/SHA/epoch/cancellation
provenance. Different waveform/native midpoint policies also matter. Fresh phase
captures are retained as a measured design tradeoff. Header-only browser BPM
publication still needs its fresh observation on startup:1000checks120ms, needed
when browser cache is absent/source BPM zero. Fully detached large queue helpers,
current durable state serialization, recovery/payload checks, long network waits
and physical-device acceptance remain explicit costs or proof boundaries.

Runtime packaging excludes SQLAlchemy testing/mypy integrations, mypy and
ast_serialize while retaining mypy_extensions and core ORM/codec/encryption/
numerical dependencies. The former installed package held52development namespaces
and10.62MB of those two frameworks. Actual package contents and isolated RAM-only
native ORM/SQLCipher queries verify the new composition; this is disk trimming,
not a claim of10.62MB lower resident memory. Both profiles require artifact checks.
Private current receipts and the full inventory are retained under
`~/Library/Logs/Soundbox/correctness-efficiency-2026-11-03/`.

## Historical broader review checkpoint — November3 evening

Sonny requested a whole-codebase review by subagents and supplied a trackpad
recording. The review inventories all176 first-party Python/native source files
under the app trees, with explicit measured, static-review and deferred-profile
depths, plus eight build/harness paths. This includes development entrypoints;
it is not a count of packaged modules or a claim that all possible bottlenecks
were measured. Pinned dependencies were inspected at selected boundaries rather
than audited in full. Current package/install status is in [HANDOFF](HANDOFF.md).

The recording contains37 short gray interruptions (8.3–66.7ms, median25ms) while
the thumb remains visible. ScrollEnd previously removed the accent between
short gestures. Feedback now stays active through a120ms idle interval, restarted
by another gesture; phased updates retain activity until the end. An inactive
axis receiving only ScrollEnd stays inactive. The same idle interval applies to
phase-less wheels. Mouse dragging, geometry and palette remain unchanged.

USB playlist scrolling also previously walked the expanded tree and emitted a
full settings update on every tick. Scroll positions now update immediately as
scalars, with a120ms coalesced notification. Selection/expansion/page changes,
snapshot switching and shutdown still capture complete state; backup/restore
flushes pending view preferences before its save barrier.

| Generated component workload | Before | After |
| --- | ---: | ---: |
|12 rapid trackpad reversals, accent transitions |24|2|
| USB scroll bookkeeping,100 ticks/10000 rows |634.7ms|0.67ms|
| Durable changed state save,1.63MB |33.9ms|17.3ms|
| Import receipt validation,3200 tracks |882.1ms|145.7ms|
| Import presentation,3200 tracks |55.6ms|12.9ms|
| Native playlist pure plan,32 remove commands/6400 memberships |1267.5ms|200.5ms|
| Prep request CPU,1.366MB unrelated settings, save/paint stubbed |9.07ms|0.106ms|
| Artwork status batch,3200 fallback rows |488.5ms|19.0ms|
| Waveform Python bridge,one-hour/12-mip generated ranges |2.227ms|0.895ms|
| Same bridge, traced transient Python bytes |75.75MB|37.88MB|
| Fresh analysis admission,10000 tracks |5799ms|133ms|
| Detection queue helper pass,3200 tracks/100 pending jobs |2189.6ms|1.81ms|
| Stock no-op plan,3000 empty playlists |1218ms|755ms|
| Captured CDJ cue projection,DAT+EXT/8 rows |47.7ms|24.0ms|
| Classic CDJ inspect,1000 tracks/1000 playlists/20000 members |30.2ms|23.4ms|
| Same generated catalog,complete preserve-all merge |1029ms|909ms|
| Offline provider scoring,3000 candidates/first query |226.3ms|195.5ms|
| Same candidates/subsequent distinct queries |227.1ms|174.3ms|

These are independent component measurements and cannot be summed or presented
as end-to-end latency. Scroll bookkeeping excludes painting/device I/O; native
queue timings exclude acquisition/publication; waveform figures exclude native
copies and total load. No real source audio, live native database, device writes,
network requests or input into Sonny's apps were used for these measurements.
Receipts, scripts, the supplied reference and per-file review inventory are in
`~/Library/Logs/Soundbox/runtime-efficiency-2026-11-03/`.

### What changed and what remains fresh

- State backup still reads current bytes under its lock. Only exact equality to
  previously validated saved bytes avoids decoding/validating/copying again;
  changed or corrupt bytes take the full validation path. Durability and size
  limits remain. Import receipt/projection sets are local to one operation.
- Native playlist planning indexes rows while preserving order and exact before
  checks; duplicate IDs use the original first-row/list behavior. Prep rollback
  retains only the two namespaces actually replaced rather than deep-copying
  unrelated settings; failed saves restore the exact previous references.
- Artwork observations expire at the end of each locked batch. Standalone image
  and export reads remain fresh. Stock cache initialization is captured on the
  GUI thread before plan-worker creation, avoiding competing first construction.
- Borrowed immutable Python waveform buffers live through the synchronous native
  call, which copies them into owned storage before returning. A future retaining
  or asynchronous native API must revise this ownership contract.
- Analysis queues are validated and indexed per invocation, with source/library/
  track/path/UUID checks, FIFO, retry and durable checkpoints retained. Failed-job
  replacement still removes from a list; admission is not universally linear.
- Stock database/playlist lookup preserves first duplicate identities and opaque
  metadata. CDJ DAT/EXT sections parse once per extension while retaining layer
  order. Rust indexes IDs, physical row boundaries and playlist entries while
  preserving original output order, duplicate behavior and opaque bytes.
- Provider caches contain only pure normalization/title-cleaning results, capped
  at8192 builtin strings per helper. Current candidates, exclusions, ranking,
  credentials, account state and network responses are not cached. Non-string
  and string-subclass behavior follows the original uncached implementation.

Native comparison retained exact decoded JSON and all three classic merge
database bytes. A complete12-file render matched11 files byte-for-byte. The
encrypted exportLibrary.db uses randomized salt: two runs of the unchanged
baseline also differ. Independent decoded schema/tables/views/integrity/protocol
comparisons match baseline and changed renders; no ciphertext normalization is
claimed as a raw-byte match. Required fixtures remain exact. The Rust gate now
runs library and binary regressions, doctests and adapter checks with pinned
RUSTDOC, rather than omitting binary tests through --lib.

### Remaining work at the evening checkpoint (superseded by the night pass)

The night pass at the top of this document subsequently addressed items2–5:
acknowledgement/snapshot copies, fuzzy matching/thumbnails, overlapping USB and
selected metadata/no-op work, keymaps and cooperative provider body budgets.
Item1 was profiled and retained as a memory/provenance tradeoff; opaque network
waits and fresh guards remain. The list below preserves the original prioritization.

1. Repeated waveform→BPM→key→native-render source capture/decode needs a shared
   captured-context design preserving exact SHA identity, durable phase receipts,
   cancellation and source authority. It is a static high-cost candidate; total
   end-to-end savings are not yet measured.
2. Completion/acknowledgement handlers still copy entire settings or saved
   analysis dictionaries. A generated500-record/500-beat dispatch copy costs
   about163ms. Narrow transactions must preserve rollback, newer edits and
   committed-receipt replay before replacing those copies.
3. Fuzzy artist matching costs1.734s on1000 deliberately similar generated names;
   it already runs in a worker. Candidate indexing must preserve exact match,
   exclusion and tie semantics. Full dialog image decoding is another bounded
   UI candidate:64 distinct512px JPEGs cost59.4ms versus2.08ms without images.
4. CDJ status hints repeat overlapping membership work; separate source freshness
   boundaries reread cue/content/tag tables, and unchanged merges still render.
   Stock free-art-slot lookup and native single-plan copies merit profiling.
5. Provider requests remain serial and cancellation waits for the current
   blocking call. Network lifetime deadlines require separate design from rate
   pacing. Keymap normalization is measured but lower priority at0.169ms/lookup.
6. Graveyard snapshot/shared-file protection, cloud acquisition, exact changed
   media hashes, one-track commit rematerialization, recovery checks and classic
   USB pacing remain deliberate guards. Optimizing their safe implementation
   requires proof; removing the checks is not an efficiency improvement.

At that evening checkpoint, the corrected gate passed2406tests with zero skips/
expected failures, and signed package/native parity and isolated/normal startup
passed for runtime d5cb4451…. The night2531-test delivery supersedes that install;
exact current provenance/state checks are in HANDOFF.
Physical reconnect/power-cycle/export throughput, real compositor gesture
latency and the full failure matrix across hardware remain unmeasured here.

## Historical first export-efficiency checkpoint — November3

Sonny requested separate USB/iPod reviews and export work enabled only when a
target is detected. Three subagents reviewed USB catalogs, iPod/Rockbox planning
and desktop startup; the running build155, source music and live devices were
left untouched. Installed versus source status and validation are in HANDOFF.

Startup now imports the desktop without loading full CDJ, stock/dual iPod or
filesystem export backends. Cache indexes load on demand. Empty discovery does
not construct Collection/Artists export playlists or initialize the stock shadow.
At this historical checkpoint, detected targets requested a cancellable queued
GUI-thread capture before their first drift check. The November4 generic USB
workflow above supersedes that part; iPod/Rockbox capture remains. Stock-art warming waits for an attached stock-capable iPod,
stops on disconnect and retains its worker until finished delivery. The mount
watcher remains active, so a device already attached at launch or connected later
can still be recognized. Existing lightweight dashboard widgets remain composed;
lazy widget creation offered much smaller gains and would complicate input/view
state handling.

| Synthetic component | Before | After |
| --- | ---: | ---: |
| Import desktop.app, median of7 alternating fresh processes, both bytecode caches precompiled | 272ms | 217ms |
| USB adopt200 playlists ×100 memberships across3200 tracks, median of3 | 572ms | 6.05ms |
| USB reconcile2000 previous native playlist bindings, median of3 | 488ms | 8.29ms |
| iPod drift:800 unique paths,40 playlists,40000 memberships, median of5 | 1280ms | 78.9ms |
| Filesystem path plan:3200 unique sources, median of3 | 3282ms | 76.8ms |

These are separate local CPU/metadata workloads, not total startup or physical
export throughput, and their gains must not be summed. Import baseline code was
read from repository HEAD `d19f3eb` into a disposable directory; both source
trees had their bytecode caches explicitly precompiled before timing. Catalog outputs
were compared exactly. iPod/path fixtures use empty generated source placeholders;
no real audio or mounted devices are read. Raw small receipts and repeatable
fixture scripts are retained privately in
`~/Library/Logs/Soundbox/export-efficiency-2026-11-03/`.

USB adoption maintains name/parent/folder candidate indexes and a current
source-track membership set. Native bindings use full-path/type and exact-ID
indexes, preserving ambiguity and foreign-library tracks. iPod drift shares
availability by path and encoded display metadata by track ID only within one
poll: source existence/metadata derivations drop40000→800, and the next poll sees
new edits/deletions. Filesystem planning maintains one FAT-normalized destination
set; a100-source fixture drops5050→200 normalizations while preserving exact case/
Unicode collision behavior. Writer defaults retain their independent fresh checks.

At this first checkpoint, CDJ captures still scanned complete cue/tag/contentFile
tables and unchanged generations invoked the merge renderer. The later night
pass narrows selected metadata where ordering permits and skips rendering only
under its complete reconstruction proof; independent freshness captures remain. New/changed media is acquired and
verified during planning and rematerialized/reverified at commit to preserve
bounded one-track staging. Generic/schema2 verifies existing destination bytes;
stock render/parse validation and150ms classic USB pacing remain. This pass does
not weaken payload hashes, source/mount/ownership checks or transaction recovery,
and it does not claim physical reconnect, throughput or end-to-end acceptance.

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
47-second import now completes in any particular time. At that November2 checkpoint, native reopen/UI latency
was unobserved while Sonny kept the app closed. Subsequent startup verification
is recorded in HANDOFF; actual gesture timing is still not measured here.

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
| Durable crown-state save, earlier checkpoint | About27ms for a private copy of then-current state | Durability remains; the later broader pass adds exact-byte backup validation reuse, and night completion work narrows touched copies |
| Artwork defaults during browser preparation, earlier checkpoint | Repeated checks of a shared default-photo pool can scale with tracks | The later broader pass captures distinct-photo availability within each locked batch; independent export reads remain fresh |

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
uses120ms idle after `ScrollEnd`; phase-less wheels use the same interval.

## Persistence, artwork and source I/O

At measurement, a read-only copy of the then-current 1,357,507-byte crown state,
with nine saved analyses, 91 native bindings and its migration map, was saved only in a
disposable TEMP directory. Five real changed-state saves measured 26.715 ms
median and 28.162 ms maximum. The profile identified repeated validation and
deep copying in `StateRepository.save` and backup validation. The later broader
pass implements exact-byte reuse of validated backup input, with corruption,
external-edit and recovery tests. Per-edit durability and backups remain intact;
moving a durable intent behind its visible effect is not an acceptable speedup.

`ArtCache.status_kinds` already captures one exclusion-policy snapshot. The earlier default-photo
lookup checked assigned files per track; the later broader pass captures
availability once per distinct photo within each locked presentation batch. Keep actual
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
- iPod/Rockbox polling reuses prepared playlist inventory and projects current
  metadata only for managed members. Full membership/display changes may rebuild
  export-view ordering; ordinary UI preferences and selected canonical checkpoints
  do not need that work. Keep invalidation bound to actual inputs and preserve
  mount discovery and ordinary eject. Generic USB has no idle comparison.
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
roughly double-base-sized pyramid. At that morning checkpoint the upload path
additionally joined the Python range buffers, copied them into ctypes arrays,
built a C++ staging vector and copied that vector into shared Metal storage.
The table's transient row counts
the joined buffers, ctypes inputs and C++ range staging in addition to the final
native buffer already counted above. It excludes vector spare capacity, mode
construction workspaces, compressed read/decompression buffers, decoder-owned
allocations and old GPU commands still in flight; it is not an exact peak.
The November3 broader source pass above removes the ctypes duplication while
retaining the joined immutable inputs and synchronous native-owned copies.

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
the legitimate runtime packages. At that morning checkpoint, composition collected all SQLAlchemy
submodules, including development/testing integrations. mypy and
`ast_serialize` framework directories account for about **10.2 MiB** together;
additional archive code is separate. The later night pass excludes
those development integrations and verifies archive contents, both profiles and
isolated native ORM/SQLCipher behavior; current figures are at the top. Do not
remove required numerical, artwork, codec, encryption or Qt components on size
alone. No package dependency was trimmed at that morning checkpoint. The evening change omits
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

## Historical first performance delivery — November2,20:45

At this checkpoint the follow-up was installed at20:45in personal155, runtime
fingerprint8882e36f…; its dated private release receipts retain provenance.
HANDOFF records the superseding current app. All2219tests passed (2155offscreen/64Cocoa),
zero skips/expected failures; lint/types104core files, Rust/native/fixtures/render
passed. All152packaged Python modules and25deck/17CDJ-helper code/data sections
match tested builds. Fresh strict signature and installed normal deep signature
plus exact signed Contents pass. Installed isolated CoreAudio/Metal startup has
zero devices/no loaded library or artwork worker. Curation remains byte-exact,
all10authorities unchanged and queues empty. Sonny explicitly requested keeping
SoundBox closed; no normal real-library launch or UI latency result is claimed.

## Historical second-pass installation — November2

The second package was safely installed at22:56on November2 after Sonny quit
normally. Runtime33689af7…/executablecf86e80f…/personal155 belongs to that dated checkpoint;
HANDOFF records the superseding current app.
All2266tests passed (2202offscreen450.033s,64Cocoa67.534s), zero skips or expected
failures; lint/types105core files/Rust/native/fixtures/render pass. All154Python
modules and25deck/5process-guard/17CDJ code/data sections match tested builds.
Fresh strict and installed normal deep signatures plus exact signed Contents
pass. Installed isolated startup verifies native player AND fast process query
with zero discovery/no loaded library. Curation is byte-exact to fresh preinstall,
all10authorities and saved icon exact, queues empty; only legitimate window/column
UI settings differ from the earlier first-pass baseline. At that checkpoint the real app was left
closed; normal startup was subsequently verified, while new actual gesture timing
remains unmeasured. One prior first-pass app was retained then; obsolete CBR app and duplicate packaging
outputs were retired by exact scoped receipts, with source/recovery/audio intact.
