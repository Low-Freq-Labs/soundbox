# Efficiency audit — October 27, 2026

Historical baseline, candidate experiments and dated implementation checkpoints.
The build131/133 installation statements, per-zoom color policies and original
proposed changes below belong to those checkpoints. Current installed/source
provenance is in [HANDOFF](HANDOFF.md), player policy in [PLAYER](PLAYER.md),
measured follow-up costs in [performance-audit](performance-audit.md), and active
work in [ROADMAP](ROADMAP.md). These older candidates are not a current backlog.

Rechecked November4 against whole-app build165/runtime `7ffde716…`; build163/
source `cf2ef5b` is an earlier delivery. The scope-first reader
proposal and count/policy/receipt fixes below subsequently shipped; active native
Collection discovery is supported rather than a future opt-in. Generic USBs are
now explicit playlist-export targets with no idle catalog/drift comparison or
completion/eject read. Selected export capture queries bounded selected cue and
playlist memberships, skips unrelated smart-list evaluation and avoids unnecessary
Collection/Artists construction. Small physical FAT32/exFAT fixture exports passed;
the old deferred generic-export statements describe their dated checkpoints, not
an unimplemented build163 request. Full-library/player acceptance and iPod sync
performance remain separate. The later whole-app audit also removes unused
Settings/device construction, repeated regex/model/artwork/state work and private
Grid staging; its matched signed startup measurement is in performance-audit/
HANDOFF. Those results do not rewrite the dated measurements below.

Evidence disposition, October28: Sonny accepted the build and closed further
waveform comparison/tuning for now. After the directory audit, he authorized
removing the private comparison archive and obsolete experiment/test output.
`~/Library/Logs/Soundbox/development-archive-2026-10-28/` and the historical
`build/<investigation>` capture/harness paths below are deleted. Small release/
state records, canonical native acceptance/recovery and required fixtures remain;
see [PROJECT_LAYOUT](PROJECT_LAYOUT.md) for exact retention. Measurements below
are historical evidence, not a promise that raw comparisons can still be replayed.

Initial source baseline: `a25de73`, clean `main`; then-installed personal build 131.
Final implementation and build 133 installation are recorded at the end.
The initial audit below records measurements and disposable candidate experiments.
The implementation follow-up at the end distinguishes later changes from that
baseline. During the initial audit no runtime source, installed bundle, source media, rekordbox
database, device or canonical SoundBox state was changed by this audit. The audit
did not quit or relaunch the app. A separate installed-app restart appeared in
the production log at 19:55:38; the process sampled earlier was PID 26286, not
that later session.

The previous broad architecture review was September 27, followed by the
[September 28 repeated-work fixes](archive/repeated-work-audit-2026-09.md).
October 27 also removed library/artwork work from transport edges and reduced
backup/cache GIL contention. Those improvements remain present. The new evidence
points to browser preparation and source loading as the first useful targets.
Scope was a risk-directed trace of startup, browser/model work, persistence,
artwork/preparation, playback polling/native lifetime and device/sync admission,
with measurements on the leading candidates. This was not a line-by-line proof
of the entire codebase or a controlled end-to-end performance certification.

## Observed baseline

| Measurement | Result and limit |
| --- | --- |
| Accepted real launch, 19:31 | Starting logged 19:31:58; snapshot 19:32:00; current source 19:32:04. Whole-second log timestamps, not first-frame/input latency. |
| Later real launch, 19:55 | Starting 19:55:38; snapshot 19:55:40; source 19:55:44. Observed independently; not initiated by the audit. |
| Running process | Eight-second sample: main thread and decoder overwhelmingly waiting. Does not establish sustained-playback CPU or frame cadence. |
| CPU, 30-second observation | Process CPU time rose 0.72 s over 30.07 s, approximately 2.4% of one core. User activity was uncontrolled. |
| Memory | `sample`: 554.3 MB physical footprint, peak 601.1 MB. Later `vmmap`: 564.7 MB, including 198 MB of IOSurface regions. RSS differs from physical footprint. No leak or allocation owner is established by these snapshots. |
| Current library replay | 3,656 rekordbox playlist tracks plus 84 imports = 3,740; metadata UI replay displayed 3,228 after filters. |
| Read-only DB capture | Approximately 64 MB main DB; stable DB/WAL capture took 0.142 s in one warm measurement. |

The live log shows normal warm analysis passes reusing 3,237 receipts, zero
analysis tasks, and no idle discovery workers after the existing empty-mount
admission settled. Startup reuses 3,106 stock-art derivatives. This is not an
application repeatedly decoding the whole audio library while idle.

## 1. Sidebar counts perform full browser and artwork work

`MainWindow._refresh_custom` needs a Collection count and distinct artist count.
It calls `_artist_rows`, which creates every visible display row, annotates every
art status and sorts by artist/title. The caller uses only the artist values for
counting. This runs when the sidebar rebuilds, including startup, source refresh
and custom-playlist changes. On a cold row cache it also forces display-title
computation synchronously before the background warmer can help.

In a metadata-only replay of the current library, five warm sidebar rebuilds
averaged **147 ms**. A temporary candidate omitting artwork annotation and sorting
for this counting caller averaged **21 ms**. An earlier pair measured 154/23 ms.
These are scoped UI operations, not a sevenfold startup speedup.

**First change:** count effective artists without formatting unrelated columns,
artwork or sorting. Maintain a revision-bound count/index across playlist-only
edits. Preserve global invalidation when artist policy, aliases, source metadata
or visibility actually changes. An artist/title edit must still update the count
and derived identities correctly.

The same broad work appears in `_sync_playlists`, which is already cached.
However, `_save_state` clears that cache even for Q/zoom and other preference-only
saves. With a connected device, a later poll can therefore rebuild both full
exportable views on the GUI thread for a preference unrelated to those views.
Narrow that invalidation to inputs that change membership/order/display policy;
do not weaken durable curation saves.

## 2. Artwork status repeatedly checks a shared policy file

`ArtCache.status_kind → source_excluded → source_exclusions` guards and checks
`source-policy.json` separately for each successful artwork row. This includes
repeated path construction, symlink checks and existence checks when the policy
file is absent. If present, the implementation reads/parses it for each lookup.
The current replay's policy file was absent; no repeated JSON-read count is
claimed for the actual library.

The profiled first library application performed **6,456 art-status lookups**:
one pass for sidebar counting and another for Collection display. In a warm
unprofiled replay, freezing the unchanged policy tuple for one batch reduced
Collection row/annotation time from **145 ms to 62 ms**; sidebar refresh from
147 ms to 63 ms. The batch produced identical annotated rows. These improvements
overlap finding 1 and cannot be added together as independent startup savings.

**Change:** take one guarded policy snapshot per display batch, or use a cache
bound to state generation and policy identity/revision. A cache must refresh on
policy edits, restore, other repository writers and external replacement; an
inaccessible, malformed or symlinked policy must retain existing rejection
semantics. Keep authoritative artwork mutation checks fresh. Do not cache a
missing policy forever.

## 3. Source loading has an avoidable query fan-out

The reader gets all active content and then accesses ORM Artist/Key/Genre
relationships per row. Instrumentation on a disposable encrypted copy counted
**1,497 SQL statements**, including 1,206 referencing artist, 54 key and 216 genre
tables. These are local snapshot queries, not writes or audio reads.

A temporary eager-relationship candidate reduced the total to **21 statements**.
Alternating warm unprofiled loads in the final experiment measured:

| Candidate | Reader wall time | SQL statements |
| --- | --- | --- |
| Baseline, two trials | 1.942 / 1.944 s | 1,497 |
| Batched relationships | 1.690 s | 21 |
| Batched relationships plus one resolved share root per load | 1.589 / 1.632 s | 21 |

All five runs serialized to **identical 5,206,252-byte library snapshots**, including
playlist order, tags, display cues and cue edit identities. Earlier alternating
baseline/batched trials also matched exactly. This supports roughly 0.25 s for
relationship batching, or 0.31–0.35 s for the combined candidate, on this warm
reader workload. It does not prove end-to-end packaged-launch savings.

**Change:** batch relationship loading in the snapshot reader. Keep adapter mocks,
soft-deletion behavior, missing relationships and smart-list membership covered.
No new dependency, live SQL access or source-write permission is needed.

## 4. The reader prepares tracks that it subsequently discards

`load_live` normalizes **5,942 active source tracks**, resolves their analysis
paths and reads display cues, then builds playlists and reduces the result to
**3,656 referenced tracks**. About 38% of those normalized track objects do not
survive the current playlist-only scope. Cue identities subsequently scan raw
content/cue/aggregate tables again, although identities are built only for retained
supported tracks.

The instrumented reader made **130,725 `lstat` calls** in the disposable layout;
130,724 occurred during track normalization. `resolve_dat_path` resolves the same
share root for each track, then resolves the candidate path. Despite its docstring
saying it avoids thousands of stats, `Path.resolve` walks ancestors with `lstat`.
Counts depend on path depth/topology; this is not an exact installed-launch count.

**Change:** determine validated playlist scope before expensive Track construction,
batch raw cue reads for that scope, and resolve the shared root once per load.
Retain complete active-ID validation and authoritative intelligent-playlist
evaluation. Full-Collection opt-in, when implemented, needs a distinct scope.
Preserve symlink/traversal confinement; replacing resolved paths with unchecked
string joins is not an acceptable optimization. Root reuse has the disposable
candidate evidence above; scope-first loading remains an untested proposal.

## 5. Cached startup repeats the complete display transformation

Both cached instant paint and authoritative source completion call
`_apply_loaded_library`. Each injects imports, builds the artist registry, applies
settings/default assignments, clears computed rows, rebuilds sidebars and
repopulates the active view. First-load profiling attributed 2.157 s to this path;
the profiler adds substantial overhead. A later unprofiled application took
**0.787 s** in the metadata replay. Neither is packaged startup timing.

Repeated dynamic artist-specific title regex compilation was also visible:
2,600 regex compilations during first application, including the artist registry
and title cleanup. This is a secondary CPU target; exact attribution/safe cache
size needs a separate experiment.

**Change:** first remove findings 1/2, then preserve derived rows/registry/view
state when the fresh snapshot has the same complete display inputs. Only changed
rows should recompute. Keep source verification, source identity, cue/tag/grid
authority and startup recovery independent of display reuse. Never let cached
presentation authorize a source write or replace a fresh DB/WAL read by assumption.

## 6. Warm analysis checking is bounded but still library-wide

Every minute, the preparation worker stats source/cache/DAT/EXT/2EX identities
for every non-hidden track and hashes the small signature document. A read-only
replay of just that signature phase measured **19,179 stat calls across 3,238
tracks**, with 3,237 receipt hits, in **0.197 / 0.187 / 0.186 s**. No preparation
worker or task ran in this probe. It did not open source audio, V5 payloads or
ANLZ content; it read the small inventory and library metadata.

The policy signature also contains `int(time.time() // 1800)` for **every** track.
Each half-hour boundary invalidates all receipts, including fully imported ready
records, rather than only unresolved fallback records. The earlier live log at
19:30:34 shows zero reused receipts and zero tasks after a boundary. Such a pass
can reopen manifests and DAT grids without producing any useful work.

**Change:** scope fallback expiry to unresolved/failed imports. Keep source and
sidecar identity detection for all records, and preserve late-ANLZ, manual-grid,
cue, explicit-recook and cloud-placeholder behavior. A later event-driven dirty
set can reduce the minute scan, but needs a reconciliation fallback for missed
events. The measured 0.19 s/minute makes this lower priority than GUI/startup work.

## 7. Artwork startup schedules two warm whole-library passes

The production log at 19:32:08 and 19:32:10 shows two passes over **3,106 covers**,
both with zero preparation, all hits. Source refresh schedules one pass; the
automatic cover worker's finished handler schedules another regardless of whether
it changed artwork. That cover worker reported six never-indexed tracks but
ultimately zero artwork tasks/fetches. Excluded-source profile recovery also needs
to remain eligible, so suppressing all zero-fetch completions would be too broad.

**Change:** retain dirty track/image IDs and request only those on completion;
coalesce initial full warming. Track actual profile changes separately from
fetched counts. Hidden/ineligible never-indexed tracks should not repeatedly
admit empty jobs. Preserve missing-derivative recovery, exclusion fallback and
selected-export verification. These passes are off-thread and cached, so this is
metadata/scheduling waste, not evidence of thousands of JPEG decodes or searches.

## 8. Memory needs attribution, not speculative cache deletion

The observed half-gigabyte footprint merits an empty/loaded/playing/paused/repeated
track-switch comparison. The 198 MB IOSurface total is a useful lead for a native
allocation trace, but does not identify a SoundBox leak or reclaimable waveform
buffer. Native track replacement clears old analysis buffers; the renderer also
needs current geometry/mips and presentation resources. Python keeps current
analysis data and display rows; artwork review already has bounded preview caches.

**Next measurement:** attribute Python, native waveform/mip and compositor memory
separately with a controlled synthetic track-switch sequence, then verify whether
memory plateaus. Avoid whole-cache clears, lower waveform quality, reduced frame
rate or a rewrite based solely on one footprint number. No sustained-playback
power, allocator-growth or hardware-render performance claim is made here.

## Other boundaries checked

- Cue/grid saves update target metadata without reloading the whole source
  library or audio, but the disposable approximately 5 MB instant-paint snapshot
  is serialized again when the source queue drains. Chunked encoding is already
  present. Coalescing/checkpointing that derived snapshot is a later opportunity;
  native transaction backup/recovery remains mandatory.
- My Tag writes intentionally perform a fresh library read because intelligent
  playlist memberships may change. Optimizing the reader benefits that path too;
  patching only one checkbox locally cannot establish authoritative memberships.
- Ordinary settings saves already debounce for 400 ms and identical canonical
  saves avoid publication. They still validate/serialize the small curation
  document. Do not replace durable edit/device/backup boundaries with fire-and-forget.
- Empty-device discovery already uses mount hints/backoff; transport edges do not
  scan devices or art. Connected three-second drift remains independent of player
  state. It can be revisited with hardware metrics; no device was probed here.
- Composite Sync already retains committed source/device inventory. The generic
  filesystem exporter still acquires/prepares sources and hashes matching
  destinations on unchanged exports, as documented in the September audit.
  This remains a separate device-policy task, not a startup fix.
- The common background resource budget and device-command queue consolidation
  remain architecture debt. Large window/native files warrant cohesive extraction
  for reviewability, but smaller files alone do not establish lower runtime cost.

## Recommended implementation and acceptance

1. Remove count-only row/art/sort work and batch artwork-policy reads. Assert the
   absence of per-track policy I/O and unchanged sidebar/display/export semantics.
2. Batch DB relationships and eliminate repeated root resolution, then measure
   scope-first loading. Compare complete model snapshots, smart-list membership,
   exact order and cue revisions against the existing reader on disposable copies.
3. Preserve display work across unchanged source refreshes; narrow playlist-cache
   invalidation. Verify visibility, title/artist policy, curation and source edits.
4. Narrow half-hour fallback expiry and duplicate artwork warming. Count opens,
   stats and tasks for warm, changed, delayed-sidecar and missing-cache cases.
5. Run controlled allocation/playback/power and physical-device acceptance when
   their required runtime/hardware is available.

Each shipped slice needs focused behavior/I/O tests, `./check.sh`, applicable
render/native checks and safe install/state verification. Measure first usable
paint, fresh-source readiness and worst GUI callback separately. Establish a
before/after packaged-launch distribution; these few warm microbenchmarks do not
justify a promise of instant launch or a combined speed multiplier.

## Evidence and validation

Private scripts, JSON counters, cProfile output, process sample and vmmap summary:
`~/Library/Logs/Soundbox/efficiency-audit-2026-10-27/`.
The development working copies were originally ignored under
`build/efficiency-audit-2026-10-27/`; those scratch copies were subsequently
deleted under the October28 disposition above.
DB probes first copy a stable encrypted DB/WAL pair and only open disposable
copies. Candidate changes exist only as process-local monkeypatches; complete
serialized models matched. The UI probe copies metadata, substitutes disposable
media paths, disables devices/source refresh/import repair, uses public providers
and test-mode AppData, and executes no audio/analysis worker. The watcher probe
only computes signatures and cannot execute preparation or publish receipts.

All timings are warm, local, limited observations on Sonny's Mac. Profiling,
unprofiled probes, real log observations and untested proposals are labeled
separately. All 60 existing reader/cache/policy/inventory tests passed via
`python -m unittest tests.test_rekordbox_db tests.test_libcache tests.test_art_sources tests.test_preparation_inventory`.
Runtime instruction parity, changed-document relative links and `git diff --check`
were checked. No
runtime change, installation, full release-gate rerun, commit or push is claimed.

## Implementation follow-up — October 27

Sonny approved the reviewed performance/waveform/CDJ-foundation plan. Current
implementation removes full row/art/sort work from sidebar counting, batches
presentation policy reads, preserves export-view caches on debounced preference
saves, and reuses unchanged row/registry inputs across fresh source loads. Source
authority, cue/grid reconciliation and visibility application remain fresh.
The reader establishes playlist scope before Track construction, batches
relationships in 500-ID queries, and resolves its share root once per load.
Preparation retries expire only unresolved fallback receipts; complete receipts
survive half-hour boundaries. Artwork completion queues changed IDs instead of
a second global stock-art pass, and hidden tracks do not admit empty art jobs.

Measured local follow-up: 29 SQL statements versus 1,497, 47,538 `lstat` calls
versus 130,725 in the disposable layout. Complete `Library` equality passed
against the original reader on a stable encrypted source copy: 3,656 tracks,
75 playlists. Warm UI replay: sidebar ~20 ms; row/art annotation ~48 ms;
unchanged library application ~86 ms versus the earlier ~787 ms. These are
scoped warm measurements, not packaged launch results.

`scripts/benchmark_startup.py` measures external process launch through populated
rows, search/selection and a synchronous Qt paint proxy. Its default is synthetic
metadata; explicit `--metadata-from` copies and sanitizes state. It never loads
the live source library, bookmarks, media, accounts or devices. Its packaged
`--benchmark-startup` mode is separate from ordinary launch. This tests snapshot
browsing and includes process/bootstrap/import time, not source verification or
compositor presentation. It does not certify the current curated runtime.

`scripts/profile_player.py` verifies frozen offline fixtures, reconstructs only a
disposable cache and runs muted native memory/motion probes. One hundred repeated
loads plateaued at 184,992–185,072 KiB RSS after warm-up; the native detail buffer
remained 4,108,448 bytes. This standalone renderer measurement does not explain
all full-app/compositor allocations. RGB normal/flat-red/fixed-contour controls
were captured at 128/184/256/384 points/s. They do not establish a superior
palette or a renderer correction. Sonny supplied STOP CAPPIN (sonny. remix), first
15 seconds, as the specific current red/dark-red case; its focused probe uses a
stable local source acquisition and disposable cache, without source writes.

The research CDJ bridge now distinguishes display intermediates from packed
native streams and rejects invalid musical values. It is still neither an ANLZ
encoder nor a device writer. Future capture must use authoritative DB cues,
which can differ from stale ANLZ cues. `/PIONEER` remains excluded.

The first twenty packaged snapshot-browser launches after the initial changes
measured median 2,563 ms / p95 2,592 ms (first-run maximum 3,729 ms), missing the
target. Following the conditional plan, implementation adds a versioned,
checksummed prepared-row cache bound to exact display inputs. It contains no art
status or native authority. Theme restoration now precedes widget construction;
unchanged QSS is not reapplied globally. Input filtering attaches after restoration,
hidden tracks do not warm display rows, title-rescue regexes compile lazily and
the optional extractor imports only when needed. Repeated source-run warm probes
then measured roughly 1.36–1.38 s; these do not establish packaged acceptance.
The first diagnostic flat-red controls had the wrong byte order and are superseded
by the corrected STOP CAPPIN controls; no product rendering was changed by that
diagnostic correction. The 100-load memory observations remain valid separately.

Final gate, packaged timing, installation provenance and visual acceptance
are recorded in HANDOFF. No headline startup or exact waveform/CDJ
compatibility claim follows from these probes.

Earlier implementation checkpoint (20 launches, including the initial
prepared-row cache miss): median 1,536 ms, p95 1,559 ms, maximum 3,233 ms. The
1,500-ms target remains unmet. No fresh-source or installed-curated-runtime
acceptance was implied at that checkpoint. Its source gate passed 1,447 tests;
the matching package was built but not installed. The interactive native Python
A/B then switched a private continuous-motion experiment at the same source
position; product rendering was unchanged pending Sonny's subsequent review.

### Rejected waveform experiments and reference comparison

Sonny rejected continuous-motion RGB at384 paired with the old brightness boost: it looked nearly identical to the
current renderer and both were unacceptable. He also rejected changing detail,
including an authored-amplitude underlay, without reviewing that candidate. Keep
both experiments out of product code. Preserving waveform detail is mandatory;
no filled-envelope substitution, motion blur or detail-hiding treatment is an
accepted remedy. At that checkpoint the installed and tracked renderer were unchanged.

Read-only acquisition of STOP CAPPIN shows a dominant ~43.3-Hz bass in its two
red passages. This is a diagnostic observation, not proof that the current
complaint is inevitable or that rekordbox behaves the same. The earlier signed
carrier study in GOTCHAS is historical evidence, not permission to reduce detail.
A same-track/scale rekordbox comparison was still pending at that checkpoint;
Sonny subsequently supplied the zoom-sweep recordings and controls its UI.

Public rbxport source was inspected at revision
`b739d369665b492534dc2eae0733322c040a26d5`, as reference only. Its normal BLUE/RGB
canvas draws centered ANLZ amplitude columns, taking the loudest source column
within each output pixel. Its separate close PCM view draws interpolated stereo
min/max envelopes. These are different paths; smooth normal RGB does not itself
validate identical signed-detail rendering. Its playing waveform redraws from
raw bytes with exact device backing dimensions; bitmap caching belongs to row
previews. No third-party code/assets were incorporated, no rival application was
run against Sonny's library, and no actual visual parity was established.
Sources: [canvas](https://github.com/chrisle/rbxport/blob/b739d369665b492534dc2eae0733322c040a26d5/src/canvas/waveform.ts),
[player](https://github.com/chrisle/rbxport/blob/b739d369665b492534dc2eae0733322c040a26d5/src/views/player/WaveformDetail.tsx).

Further startup work removes Collection sorting from sidebar counts and returns
known add dates before media URI/path resolution. This also keeps independent
add dates for distinct records sharing one source path. Focused visibility,
sidebar, sort and export-view checks passed. The benchmark now reports its cold
prepared-row cache miss separately from twenty warm launches; OS-cold startup is
not inferred. Updated full gate and packaged measurements were still pending at that checkpoint.

### Accepted C and final refinement result

Sonny preferred exact pixel-area RGB coverage at 384 (B), finding it less bright
and easier on the eyes. Combining it with continuous subpixel translation (C)
removed the visible dark-red oscillation in his live review. Source samples,
extrema, LOD, colors, caches and audio/transport clocks are preserved. Scope is
RGB 384; the other rendering stops/modes retain their existing behavior. This
combination passed final release validation and is installed in personal build 133.
Earlier statements that product rendering was unchanged describe prior checkpoints.

At Sonny's request one further fixed-lattice minimum-stroke refinement was tested.
A synthetic periodic-stroke diagnostic reduced a ~4% phase-dependent coverage
range to numerical noise. Across 96 actual STOP CAPPIN frames at twelve positions
and eight phases, however, the largest difference was two display codes and the
largest changed-pixel fraction was ~0.0051%. It was not retained: no meaningful
actual-track improvement over accepted C was established. No extra visual trial,
source recook, full-library invalidation or data-format change was needed.

Production Metal helpers pass the focused 23-test headless suite, including
independent exact-area RGB 384 color/geometry cases and a mutation proving that
restoring the brightness boost violates the oracle. The native drawable suite
now checks source-frame-based continuous movement at RGB 384 alongside unchanged
other-stop raster policy, exact song translation and recentering.

The first packaged C run still missed the startup target (twenty warm launches,
median 1,519 / p95 1,581 ms; cold row-cache miss 3,485 ms). The final startup pass
moves Clickwheel artwork imports to actual conversion/rendering and libsndfile
imports to the stock WAV compatibility probe, keeping NumPy out of desktop
construction and unchanged derivative-cache warming. Grid glyphs use the known
system font instead of generic-font alias discovery. A clean subprocess import
regression enforces deferred conversion initialization; focused artwork/shadow
checks and engine types pass. Five source warm probes measured 1,244–1,352 ms; packaged acceptance was still
pending at that checkpoint and was measured separately below.

### Final packaged release and installed verification

Final `./check.sh --render` passed 1,450 tests, zero skips/expected failures,
including 56 Cocoa tests; inventories reconciled and render inspected. Personal
build 133 is signed and installed with tested runtime fingerprint
`b41fb2bf717491c96a8396f533bade0142e6cc435479b22f5f0071aaa666a585`.

Twenty warm packaged snapshot-browser launches: median 1,402 ms, p95 1,414 ms,
min 1,318 / max 1,435 ms. Every launch passed populated 3,228-row/search/selection/paint
checks with native availability and neither NumPy nor the stock converter loaded.
The separate first row-cache miss was 3,103 ms. The warm target is met; OS-cold,
compositor and fresh-source readiness are not inferred from this benchmark.

Actual curated runtime logged snapshot 3,740 tracks at 22:43:27 and fresh source
3,740 tracks / 75 playlists at 22:43:30 after the 22:43:25.106 launch request. These
whole-second logs are separate observations. The actual playing native waveform
and populated UI were inspected. Startup reused 3,106 stock-art derivatives and
fetched zero covers; one cue-metadata task ran, with zero audio analysis/compact
upgrades/grid tasks. Initial receipt-policy reconciliation is not a waveform recook.

Postquit/postlaunch checks preserved curation, all 4,041 art/review file hashes and
three source authority files exactly. Only persisted setting difference was zoom
1500→384; analysis record count remained 3,276. Backups, final state hashes,
installation time and the one prior-app rollback are in HANDOFF and the private
release evidence directory. No task-initiated source edit or device export occurred.
Sonny's installed-build acceptance and future physical iPod/CDJ gates remain
separate from automated checks. No third-party code/assets were incorporated.

### Player usability / faint-color follow-up

Sonny requested centered/larger cue contents with colored rims, intent-preserving
next/previous and playing hot cues, zoom-scaled grid nudges, faint-only brightness
and steadier scrolling. These later edits retain October27 coverage C's exact RGB 384 path (distinct from October28's rejected lift trial).
Other RGB stops replace the broad occupied-column opacity boost with a smooth,
monotonic shoulder below 18% coverage, adding at most 0.04 and leaving normal
coverage/color untouched. All RGB stops now use fractional translation. Detail,
footprints, source extrema, LOD, clocks and export data are unchanged.

Focused GPU 23 and UI 64 checks passed. Native tests cover rapid playing-hot taps,
playing/paused replacements and explicit Pause during a pending seek; that last
case caught and corrected a stale gain-completion race. Actual translated frames
and overlapping recenterings passed at all zooms/modes. Cue rendering was inspected.
Final full gate, package and install evidence follow in HANDOFF.

The muted frozen-fixture MainWindow probe displayed 3,740 synthetic rows and
captured 240 submitted drawables per zoom, comparing the installed C dylib with
this follow-up. At 1500 the maximum position discrepancy against the integer
source-frame ledger fell 1.034→0.034 physical pixels; RGB 384 stayed 0.00874.
This demonstrates removal of RGB pixel stepping. It does not demonstrate fewer
missed presentations: at 1500, actual mean presentation was 117.05/117.06 fps before/
after, maximum gap 16.667 ms in both, with 235/240 nonzero presentations in each.
The after trace also had one doubled display-link target interval. Audio underruns
were zero. Workload/display conditions were not controlled beyond the matching
harness; do not attribute all drops to one cause or claim this hitch is solved.
Private evidence: `cadence-before/`, `cadence-after/`, and `profile_full_app.py`
under the efficiency implementation evidence root. The probe uses verified offline
media and disposable AppData, with no live library/device/account access.

The player follow-up's final `./check.sh --render` passed 1,452 tests (zero skips/
expected failures). Tested personal build 133 fingerprint `5502c5931beac4980d32e0d39573a982bf04b6a724d2bf8896dfb2517e6be2a7`
was signed, installed and relaunched at 23:10:13 on October 27. Twenty warm packaged
launches measured median 1,438 ms / p95 1,464 ms (all under 1,500); the separate first
row-cache miss was 3,336 ms. All readiness/deferred-import checks passed. Actual
source load returned 3,740 tracks / 75 playlists, reused 3,237 preparation receipts and
scheduled zero tasks. Curation, 4,041 art/review hashes and all authorities stayed
exact; postquit/postlaunch library bytes match. See HANDOFF for exact receipts.
That release did not resolve occasional presentation gaps; verified continuous
RGB source translation is a separate result. Sonny subsequently closed further
comparison work as recorded in the current handoff.


### October28 selected RGB edge B and playback clock

Sonny selected original encoded-edge B and explicitly retained both the signed-
amplitude visibility shoulder and faint-pixel coverage lift. Main-lane RGB now
integrates display channels jointly with signed coverage, blends the encoded
background and decodes that final opaque pixel for Metal's sRGB attachment.
BLUE,3BAND,overview, source extrema, height transfer, LOD and clocks stay intact.
Later C/D/E lift-removal/direct-cell trials were set aside despite provisional
improvement reports. Full native appearance equivalence was not established;
Sonny later accepted the stopping point and closed comparison/tuning for now.

The production shader matched original private B byte-for-byte over39 diagnostic
frames (13zooms ×3positions, one display mip supplied to both at wide stops).
Independent GPU tests cover partial/full color, the retained lift and nonblack
backgrounds; restoring endpoint decoding violates that oracle. The full final
`./check.sh --render` passed1,457tests, zero skips/expected failures, with both
process inventories reconciled and renders inspected. Final combined source:
`764d16a732525befcf68572277e378444e632734feffe3239ecf4b457523f460`.

The prep strip gains a monospaced elapsed/total clock: `M:SS/M:SS`, or hours on
both sides for tracks at least an hour long. It reuses existing transport updates,
publishes only changed displayed seconds, preserves drag-owned preview text and
adds no polling/library/audio work. Four clock/UI tests cover hour boundaries,
long sets, immediate seek/cue-position updates, coalescing, preview ownership and
actual label wiring/room. Flexible gaps distribute the unchanged controls across
the row. Both short/hour render layouts were inspected. Installation, state
preservation, startup measurements and actual final provenance are in HANDOFF.


### October28 browser polish and manual BPM endpoint correction

The browser utility rail/scrollbar/footer follow-up retains existing footer label
styling and independent column dragging. Window resizing allocates width to Title;
shrinking it before child layout prevents temporary horizontal-scrollbar flashes.
Fixed Qt paint roles resolve once and Artists mirrors only changed sections.
A synthetic 3,000-row Cocoa resize comparison (240 steps, no cProfile) measured
median 46.931→36.265 ms and p95 60.522→45.186 ms; horizontal-bar Show/Hide events
fell from119/118 to0/0. This is programmatic resize/processEvents wall time,
not a compositor/live-gesture or120fps guarantee. Earlier populated traces still
show occasional missed presentations. Installed receipts are in HANDOFF.

Sonny rejected the thin black text-outline preview. It never changed production
source, launched a live viewer or reached an installed build.

The manual-BPM report on STOP CAPPIN exposed an endpoint proof failure rather
than a decimal parser failure. All138/138.0/138.00 inputs submit13800 hundredths.
PWV3 bounds EOF to128746–128754ms; its exact PCM end is128750ms. Native138.00
retains the terminal beat at that exact endpoint. A bounded PCM framing read
resolves only such disputed endpoints, without audio payload reads or decode;
ordinary edits do not open audio. Stable path/file epoch/framing/frame count/rate
proof is journaled and revalidated at publication/commit/recovery. Unsupported or
fractional endpoints remain blocked. RAM previews reconcile the terminal row after
verification. Both directions need this guard when a saved beat itself lies inside
the coarse EOF interval.

Private native138.00 DAT SHA:
`715850b961c1ae5caebc941bd390a9c87699ec51401d3521f168e7832312a872`.
An isolated reconstruction reproduced the previous138.01 receipt hash, then the
fixed138.00 transform matched complete native DAT/EXT bytes. Native source was
left at Sonny's138.00 edit; no historical source state was restored. Focused
regressions cover shorthand/retry, endpoint bounds, bounded framing-only reads,
malformed/changed/redirected files, WAL interruption on both sides of commit,
normal edits avoiding audio and a subsequent BPM change. Final gate and installed
curation evidence are recorded in HANDOFF.
