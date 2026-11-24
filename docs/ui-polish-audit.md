# UI polish review — November 3, 2026

## Current delivery boundary — reviewed November23

The November20–21 artwork worker, complete review-cover and duplicate-comparison
changes below are included in the November21 personal 174/runtime `7a5c` installation.
Complete selected artwork still precedes Info's first frame; ordinary desktop
controls remain prepared before interaction and device pages remain on demand.
The later BLUE/3BAND waveform changes have their own geometry/readiness contracts
in [PLAYER](PLAYER.md), not the unchanged-renderer scope of the November3 review.

Personal175 also scopes per-track cover/block ownership to the source and song,
preserves open Info drafts across compatible refreshes, and delivers accepted
automatic artwork through a prepared matching-target notification. Complete
selected artwork still precedes first show; unknown future fetches do not delay it.

Measurements, inventories and test totals remain dated evidence, not universal
latency guarantees or a new November23 full-gate result. Current runtime, gate
and retention facts belong in [HANDOFF](HANDOFF.md). Historical install/device
actions below confer no current permission, and unresolved risks remain explicit.
Completed captures/renders and installation snapshots follow the November23
cleanup; dated evidence paths may retain only small receipts.

## Image decoder concurrency — November24

A fresh full gate exposed a Qt image-plugin/GIL lock cycle between GUI preview
decoding and a worker using a Python QBuffer. Both Review and byte-backed Info
now use `desktop.image_decode`: Qt owns its native file reader, with one private
0600 scratch snapshot removed immediately after a byte decode. Existing path
reads stay direct. QImageReader format handling, pixels, scaling, first-frame
readiness and joined worker publication remain unchanged.

The old path deterministically deadlocked in a bounded synthetic subprocess;
the replacement passes that trap with real GUI timer heartbeats. Five new cases
cover exact pixels/scaling across supported formats, scratch cleanup/error and
interruption, and 75 affected review/Info/lifecycle cases passed. A test-development
TIFF comparison crash was corrected by retaining its QBuffer until reader teardown;
the production helper owns its device natively. These are focused checks, not a
complete release pass or installed-runtime claim; final status belongs in HANDOFF.

Generated 500px JPEG snapshots added about 0.3–0.4ms versus the unsafe byte reader;
a 2500px noise PNG added about 11–12ms. Its workers remained responsive over about
71–76ms with 70–78 GUI timer ticks. Ordinary stored-cover Info uses the direct
path and adds no scratch copy. These bounded measurements preserve quality and
complete reveal, not a universal latency guarantee. Compact proof:
`~/Library/Logs/Soundbox/final-doc-audit-20261124/image-decoder-fix.json`.

## Responsive artwork preparation — November20 source follow-up

Choosing a local image in Track Info or the artist catalog synchronously read and
normalized its full payload on the GUI thread. Online results likewise normalized
on GUI delivery. The existing artwork request worker now captures immutable bytes
and prepares their device-safe image before joined GUI publication through
`desktop.artwork_requests`. Its source/track/repository-generation and selected
artwork owners remain exact, and a newer request supersedes an older result.
Local image choices do not enter an online provider scope. The canonical artwork
writer, policy checks, atomic save and corrupt-image fallback remain unchanged.
Catalog batches capture one shared selection, split exact tokens per target and
publish only still-current artist/avatar targets. A newer choice for one target
does not discard the group's remaining choices. The profile query's extra artist
and exact external-byte snapshot guards are retained alongside the owner's tokens.

An accepted picker file/thumbnail/URL choice holds ordinary Quit, backup/restore
and source switching until completion. Opening Info waits only for that selected
track's latest accepted image, then keeps the complete-cover-before-show gate;
an unrelated pending image cannot delay it. Both tabs still exist before first
show, and an already-visible dialog retains its previous cover during preparation.
Missing/unreadable images, failed normalization, cancellation and stale contexts
retain explicit failure/ownership handling. This adds no source-write authority.

Generated Cocoa control: Pillow noise PNG3000×3000/18,667,911bytes,3300synthetic
art-index rows, five warmed samples per mode,2ms GUI heartbeat. The same selected
image and ordinary canonical writer were used in both modes:

| Measured component | Original synchronous action | Owned preparation worker |
| --- | ---: | ---: |
| GUI handler median |135.66ms|0.24ms|
| Median maximum GUI timer gap |136.83ms|4.54ms|
| Complete publication median |137.33ms|137.67ms|

The same generated catalog fixture with two selected avatar targets measured
134.60→0.24ms in its handler and135.36→4.81ms maximum GUI timer gap; full atomic
publication measured134.54→136.69ms. These are ordinary guarded cache writes,
without a new persistence path.

This removes a GUI stall; it does not claim faster durable completion or physical
compositor timing. A paired HEAD/current complete Info control (same static theme,
generated immutable selected bytes, constructor through first show, eight warmed
samples) measured35.82→37.69ms for500px JPEG and120.80→120.74ms for3000px PNG,
with both covers present in every first show. Normal Info's decoder and readiness
flow are unchanged; this is no claim of faster complete opening. That contract is
never replaced by an earlier empty dialog. The selected-pending regression also
requires the newly chosen blue cover in the first frame while an unrelated
green cover is still pending. These are isolated fixture results, not live-library
or hardware guarantees. Current source/install status belongs in [HANDOFF](HANDOFF.md).

Focused evidence: `tests/test_artwork_requests.py`, existing Info lifetime and
selection, provider/profile, Quit and maintenance tests; compact timings under
`~/Library/Logs/Soundbox/responsiveness-pass-20261120/artwork.json`.

## Complete review-cover preparation — November21 source follow-up

The selected review cover now full-decodes into a QImage through an owned
QImageReader worker. GUI pixmap conversion and the original smooth scaling remain
unchanged. Exact PNG/JPEG pixels match at1×/2×, including transparency, palette,
portrait/landscape, embedded sRGB ICC and EXIF orientation. A scaled JPEG decoder
had changed pixels and was rejected. Direct QImage.loadFromData also failed the
real responsiveness control: its pinned binding held the GIL, producing a93.18ms
GUI timer gap. Full QImageReader.read preserved pixels with a2.59ms gap in that
isolated decoder control; completed decode times were92.00/91.82ms respectively.

The original exact CoverSnapshot byte capture stays on its existing path. Its
digest is reused only within that immutable snapshot and primed off GUI; a new
snapshot still rereads and proves current bytes. Approval/Undo and external-byte
stale-cover guards remain exact. One active decode and one latest pending row
bound rapid navigation; canceled results cannot paint or release ownership early.
The prior complete cover/title/source remain together until selected preparation
finishes. Pending different-track status reports preparation, selected edit actions
wait, navigation/Close remain live, and the first comparison pane reveals only
after completion. Same-track audition and global Exclusions/last-changed Undo
retain their independent ownership.

Eight warmed paired Cocoa samples compare the prior GUI decode/per-access digest
with the complete owned-reader/snapshot-digest path. Both modes use the same
generated image, static theme and ready comparison-cache entry, with no provider:

| Selected image | Handler before → after | Maximum GUI gap before → after | Complete selected comparison before → after |
| --- | ---: | ---: | ---: |
|500px JPEG/102,494bytes|3.36→0.51ms|9.43→8.60ms|3.36→5.25ms|
|3000px PNG/18,678,483bytes|102.66→3.38ms|110.03→7.10ms|102.66→105.97ms|

This removes the large GUI freeze, with a small worker-dispatch/conversion cost;
it does not claim faster full decoding or physical compositor presentation. An
unmocked3000px PNG regression requires real navigation and multiple timer callbacks
while decoding, alongside held-worker first-reveal, coalescing and Close controls.
Other team CPU tests may overlap these generated measurements. Exact fixture and
compact reproduction receipt: `~/Library/Logs/Soundbox/code-speed-audit-20261120/`
`review-cover-reproducer.py` / `review-cover-preparation.json`.

That checkpoint left a separate GUI duplicate check: `_same_art` measured168.29ms
per comparison of that3000px PNG against a500px JPEG, including full Pillow decode
and fitting of the original. The next source follow-up below addresses it.
Focused selected-cover proof is in
`tests/test_review_cover_preparation.py`, existing artwork review/recovery and scan
tests. Current validation and installed identity belong in [HANDOFF](HANDOFF.md).

## Duplicate-art comparison — November21 next source follow-up

Cached, streamed and saved-preview suggestions now compare images on their
existing owned workers. Each batch prepares the selected original's exact64×64
RGB/fit bytes once and reuses that immutable sample. Colour difference, blur and
correlation operations, thresholds and candidate order are unchanged. An unreadable
original still permits valid replacement art. The regular picker keeps its
unfiltered human-choice behavior; its optional pure worker filter is used by review.

Cached suggestions finish comparison before the first complete comparison reveal.
Late cache or policy changes continue through the same bounded selected owner,
carrying its already decoded QImage/sample rather than decoding the cover again.
Streamed/saved-preview delivery requires the exact selected snapshot, cache/art
owner and generation, plus a fresh policy check. Finished stale searches cannot
record a restored target as an artless miss. Close, latest selection, transport,
global controls and exact byte/recovery guards retain their previous ownership.

Six warmed paired Cocoa samples compare hash-matched installed6f source copies
with this next source. The generated source is3000×3000 noise PNG/18,667,911bytes,
with three500px JPEG previews and a2ms GUI heartbeat. No network/provider account
or live-library inputs are used; other team generated CPU checks may overlap.

| Review path | Maximum GUI gap before → after | Complete comparison/candidates before → after |
| --- | ---: | ---: |
| Cached choices, including selected cover |517.22→8.78ms|619.61→287.92ms|
| Streamed search, selected cover already ready |514.47→7.00ms|520.20→184.73ms|
| Saved previews, selected cover already ready |514.72→4.75ms|517.87→190.70ms|

Final current/suggested pixels and candidate order/count match in every pair.
This measures complete GUI-ready comparisons, not physical presentation or live
network latency. Candidate pixmap decoding retains its existing GUI path; no
image-size, visual-quality or matching-threshold shortcut is introduced.

Nine added comparison cases cover the frozen installed classifier, source-sample
reuse on all three paths, actual navigation during large Pillow work, complete
first cached reveal, late-cache continuation without another reader, detached
payloads/picker defaults, fresh exclusions, restore/finished ownership and failed
policy-capture retry. Together with prior review/scan/provider cases65 Cocoa tests
pass;57 adjacent Info/manual-save/Quit/maintenance cases pass separately. These
focused scopes overlap earlier proof and are not a full release gate.
Reproducer, compact timing/freeze receipts and per-file audit depth are under
`~/Library/Logs/Soundbox/wide-speed-audit-20261121/`.

At the November8 cleanup checkpoint, personal build169 was installed and accepted. It includes eager Info and
Artists preparation plus explicit window-filter retirement, while retaining the
selected-cover-before-show contract. The November3–5 measurements below remain
dated evidence; [HANDOFF](HANDOFF.md) records current provenance and the measured
startup cost of ordinary readiness.

Sonny reported that the information window appeared before its cover. The source
follow-up prepares that one cover before modal presentation. Canonical artwork
resolution and display-sized decoding remain in an owned worker; Qt timers and
result delivery keep running while it prepares. The cursor reports the wait.
This trades an incomplete window for a complete first frame; a contended artwork
lock can still delay opening. It adds no artwork prefetch to track loading.

The November3 follow-up was installed as build160, runtime `b0ccb1ab…`. The
earlier November4 update is runtime `7bbe8577…` and includes the prior Export
selector/cleanup work recorded here. That USB/theme follow-up was delivered as build163 and retained in build165;
[HANDOFF](HANDOFF.md) records its exact replacement runtime.

## Responsiveness delivery — November5

At that checkpoint personal build167/runtime3fb0ff60… was installed. Sonny explicitly rejected
first-click lazy Settings: all six tabs and their controls now prepare before the
first window show. In five paired generated source fixtures, opening Settings
fell 561.45→12.50ms. The paired signed snapshot-startup median rises 1158.43→1783.13ms
to provide that readiness; startup and first-click latency are distinct measures.
Device pages/export backends retain their separate on-demand behavior.

The ready Connections section attaches to the window's sole automatic refresh
before its worker starts. Cached account likes remain shared, Cancel stays live
until retirement, and opening Settings later repeats neither refresh nor widget
construction. Isolated runs use disposable state or injected services and no
live Keychain/network.

The same delivery removes discarded browser-grid copies, repeated artist-catalog
policy reads and redundant layout-event processing. Browser draft validation,
native/import metadata precedence, held-key release, navigation preservation and
normal-quit save draining remain covered. Track Info still prepares its selected
complete image before showing; generated current-sized Cocoa openings measured
25.11ms median with every first-show image ready. Artwork-lock contention can add
delay, so this is neither a guaranteed opening deadline nor a live-session claim.

All 2,795 release cases pass, including 66 Cocoa cases. A complete offscreen
attempt exposed an old Settings-absence assertion. Its replacement checks saved
colors and controls before first show and retains the later persistence assertions.
The 457-case UI replay and the final complete gate pass without omitted tests.
[HANDOFF](HANDOFF.md) records exact installation/preservation evidence, while the
[current performance audit](performance-audit.md#bottleneck-and-responsiveness-follow-up--november4)
separates remaining synchronous saves and other measured costs.

## Earlier whole-app follow-up — later November4

The preceding build165/runtime7ffde716… included the broader
[whole-app audit](performance-audit.md#whole-app-audit--november4-after-ipod-review).
At that checkpoint Settings and device surfaces were built on demand, with current
saved preferences applied when opened. The November5 delivery above supersedes
that Settings policy. Cached likes and the window-owned automatic refresh were
already independent of first opening the Settings panel.

Repeated identical browser filters/sorts avoid resets. Inline title/artist edits
invalidate search/sort data and update duplicate memberships; background artwork
keeps browsing order until an explicit Art sort. Device drag highlights use stable
model indexes across discovery rebuilds. Standalone track export checks its own
target's busy state, and My Tags refreshes partial-membership tooltips without
rebuilding its open popup.

Full-suite ordering exposed a Qt stylesheet-style wrapper failure in redundant
cue-button repolish. The same ordered 187-case prefix reproduced it. Local
stylesheet changes already repolish filled-state transitions, so the duplicate
manual calls were removed. Ten private rendered states remain byte-identical;
the corrected prefix and final complete gate pass. Cue creation, holds, keyboard
routing and source publication retain their existing semantics. This adds no
hardware/player acceptance. The older179-file presentation inventory below remains
dated evidence, distinct from that checkpoint's 184-file full/targeted/prior-exact review.

## USB/theme follow-up — November4

The subsequent source fixes the reported export/progress/USB browser presentation:
export folders start collapsed, current/live theme colors reach their checkboxes,
and one through five connected devices divide the panel with larger labels and
USB/iPod outline icons. Progress paints the saved accent-text color only over
the filled region, with normal light text over the empty region. Playlist/folder
rows in USB/iPod track browsers expand/collapse without selection highlighting;
track selection, keyboard navigation, stock reorder and checked deletion remain.

A source-wide theme scan covered all60desktop Python files, with deeper inspection
of the16modules containing color/style-bearing runtime paths and the theme
controller. Geometry-only inline styles had bypassed dynamic theme rules in the
export selector, hex editor and duplicate dialog; those rules now live in the
central stylesheet. The artwork-review spinner also follows the accent. Remaining
fixed colors describe transport/cue/waveform data, native rekordbox colors, neutral
artwork/logo details, defaults or test fixtures; they are not accent overrides.
This is a theme-focused scan, not another complete algorithm/dependency review.

Isolated white/black progress renders at0/25/48/50/65/100percent, current/live
white/blue/orange controls, and one/two/five-device panels were inspected. The
full main-window render also preserves the selected white accent/black lettering.
Focused offscreen/Cocoa gestures cover label/branch clicks, keyboard expansion,
checkboxes and track-only highlighting. No input was sent to the running app.
These changes were delivered in build163/source `cf2ef5b` and retained in build165; HANDOFF records exact
runtime, full validation and curated-state preservation.

## Fixed presentation problems

| Trigger | Previous behavior | Source correction |
| --- | --- | --- |
| Open track information with a slow cover lookup | “No artwork” appeared, followed by the real cover | Prepare selected artwork before modal show; missing/corrupt art settles before show too |
| Choose online artwork from Info | Picker returned before the fetch saved, so Info could keep the old cover | Successful publication refreshes the matching open dialog; old cover stays visible during decoding |
| Publish another cover during an existing decode | An older decode could be shown or the newer refresh skipped | Coalesce a fresh read after the captured worker retires, without painting its obsolete result |
| View artwork on Retina | Review previews were reduced to logical pixels before display | Scale review/profile previews for display DPR; Info uses scaled image decoding |
| Display names resembling HTML | QLabel could format or hide parts of titles, artists, paths and account names | Explicit plain text on dynamic metadata/status surfaces |
| Long dropdown/menu text | Text could enter the right check gutter or shortcut area | Elide and clip within the reserved text rectangle; preserve Qt activation/navigation |
| Show a very long footer message | Fixed label width could enlarge the layout or crowd out controls | Shrinkable, elided status label; retain full text/tooltip and reserve pulse dots once |
| Navigate before a queued sort/scroll restore runs | Old callbacks could move the newer view or collapse a newer multiselection | Bind delayed callbacks to the current selection revision/view |
| Refresh Artists in place | Rebuilding the tree collapsed groups and lost current/selected rows | Retain expanded groups, surviving track selections, current column and scroll on refresh |
| Refresh default-photo rows | Rebuilding reset surviving selection and scroll | Restore stable photo IDs and scroll after population |

The info worker remains owned through queued `finished` delivery and join.
Closing while preparation is pending cancels/drains it and suppresses a later
show. Closed consumers ignore publication notifications. No source music,
native database, device authority or musical-analysis policy changes belong to
this pass.

## Review coverage

The November3 private inventory covered all 179 then-current first-party application files:
58 desktop Python files, 106 engine/personal Python files, the entrypoint and
14 native sources/headers. Each has a source hash and explicit review depth.
Repository-wide inventory/presentation-risk scans are distinct from deeper
control-flow tracing; this is not a fresh line-by-line algorithm, dependency,
research-script or physical-device audit.

| Area | Checks and depth |
| --- | --- |
| Info and artwork tools | Traced constructor/show order, path resolution, worker retirement, replacement publication, lazy fields, catalog rows, DPR and decode bounds |
| Browser/sidebar/Artists | Traced selection memory, deferred sort/scroll restoration, refreshes, lazy artist children, shared delegates and column geometry |
| Status/progress/device pages | Traced footer sizing, pulse reservation, last verified snapshots, loading/ready/error states and finished-before-completion ownership |
| Settings/connections/menus | Inspected dynamic labels, fixed controls, scrolling, theme preview, keyboard capture and popup geometry; existing input/authority rules remain |
| Playback/waveforms | Scanned presentation-facing paths and retained the existing complete-waveform publication and native cadence gate; renderer/transport code is unchanged |
| Core state/providers/exporters | Scanned presentation/error boundaries; inspected artwork/state locking and metadata validation. Existing source/recovery algorithms are unchanged and retain their regression gate |

Generated regressions cover delayed first show, event delivery during preparation,
missing/corrupt covers, worker-close ordering, lazy Info reuse, coalesced refresh,
matching-track notifications, literal metadata, Retina preview resolution,
dropdown gutters, long footer geometry, pulse position stability, deferred
navigation, Artists refresh and default-photo selection. Cocoa checks and
synthetic Summary/Info renders supplement the full release check. They do not
substitute for Sonny's actual interaction with the installed app.

A generated2400px JPEG with an injected120ms lookup reproduces the old/new
ordering on Cocoa: all six previous-dialog first paints lack a cover, and all
six new-dialog first paints contain it. Excluding the warm-up sample, median
first paint is19.7ms before and149.4ms after; the old cover arrives at179.5ms,
while the new image is ready before show at131.3ms. These widget-paint proxies
ran beside the full suite and illustrate the deliberate wait-before-show tradeoff,
not live compositor/playback latency or a general speed claim. Qt timer delivery
continues during preparation; a strict timer deadline is not asserted.

The November3 signed build160 candidates passed packaged Python/entrypoint/native-section
parity and isolated native deck/process/SQL startup:159personal/155public runtime
modules, with zero library/device/art work. The40distinct targeted Cocoa cases
passed. That November3 `./check.sh --render` passed all2547tests (2483offscreen and64
native Cocoa), with zero skips/expected failures, lint/types/native/offline
fixtures and inspected renders. Installed status remains in HANDOFF; the first
complete offscreen attempt exposed an old test that explicitly required opening
before artwork. That expectation was corrected to test hidden preparation and
worker ownership, and the complete gate was rerun successfully.

## Remaining polish risks requiring separate measured work

These are identified code paths, not assertions that Sonny has experienced every
symptom. Preserve their correctness contracts when moving work off the GUI.

1. The selected review's exact canonical CoverSnapshot capture and final guarded
   canonical publication still run on their existing GUI-owned paths. Large
   bytes, artwork-lock contention or slow storage can delay those boundaries.
   Manual file reads/image normalization, selected review decoding and duplicate
   comparison now prepare on owned workers as described above; they are no
   longer wholly synchronous GUI operations. Further changes must preserve exact
   review bytes, latest-choice/restore guards and complete first-frame artwork.
2. Catalog thumbnails intentionally decode visible rows one event turn at a
   time. Normal local JPEGs were previously measured in milliseconds, but a slow
   or abnormal file can delay a turn and progressively reveal images. A bounded
   worker/batch presentation is the next deeper artwork change; do not restore
   eager whole-catalog image decoding.
3. Explicit XML opening, library/state validation/serialization and some selected
   path/stat checks still occur on the GUI thread. Large libraries, cloud mounts
   or unavailable removable storage can expose stalls. The existing
   [performance audit](performance-audit.md) records related measured costs.
4. Dialog cancellation retains worker lifetime correctly, but opaque network
   connect/header/decoder waits can delay retirement. A guaranteed immediate
   cancel cannot be obtained by destroying a running QThread or weakening state
   guards; provider/process ownership needs a separate design.
5. Extreme native metadata lengths, small-screen geometry, multi-display DPR
   changes, physical-device disconnects and compositor behavior need broader
   scenario captures. This pass verifies the reported Info opening and selected
   generated regressions, not every possible UI state.

The original November3 evidence lives under
`~/Library/Logs/Soundbox/ui-polish-2026-11-03/`, including the per-file review and
frozen source/test manifest. Later sections identify their separate evidence
roots; current release receipts belong to the current HANDOFF. Never replay an
earlier installer or overwrite its dated proof with another release's results.

## November4 export follow-up

The Info wait cursor now colors only the native wheel's white pixels using the
active accent; black and transparent pixels/hotspot/shape remain exact. Generated
pixel proofs cover five accents; focused Cocoa preparation/lifetime checks pass.

The device header opens an Export selector with playlist/folder checkboxes left
and connected targets right. A sole target is preselected, several may be checked,
and one action queues them through the existing serialized worker lane. Selection
and target identities bind queued work; failed targets can report their result
while later targets continue, and cancellation/shutdown clear remaining work.
USB target results aggregate. The iPod first-generation plan review described
at that checkpoint was later superseded by direct Export/Sync gestures without a
second plan consent; current behavior is in [DEVICES](DEVICES.md).
Generated dialog renders and queue tests are retained under the November4 export
log directory. At this earlier checkpoint, read-only mount facts identified two
writable generic USB targets and no actual USB export was performed. The later
build163 follow-up formatted TEST-1/TEST-2 and exercised frozen-fixture export,
selected update and unchanged repeat; exact scope/timings are in the
[dated USB fixture record](performance-audit.md#physical-usb-fixture-timings--november4). Those
tests do not establish player/firmware or full-library performance acceptance.

The reported native merge error was reproduced from three small catalog copies,
with one extra Plus-only collection song and identical playlist memberships.
Selected merges preserve each format's unrelated baseline. Cleanup removes only
old members of the selected playlists that no remaining playlist in either
format references; shared assets and unrelated collection-only songs survive.
Exact removed payloads restore on pre-commit failure; after durable commit their
recovery copies are retired, with resumable interrupted cleanup. Peak USB space
for new copies is still required until commit. HANDOFF records release status.

The earlier November4 delivery passed installation/normal reopen, with2564reconciled
offscreen/Cocoa tests,39additional focused Cocoa cases,675frozen inputs and
160personal/156public packaged-module parity. A cadence check failed at4%battery
and passed focused/full native reruns on AC with unchanged source; exact scope
and evidence remain under `~/Library/Logs/Soundbox/export-polish-2026-11-04/`;
HANDOFF records the superseding installation. The dated179-file inventory above remains distinct
from review of the additional Export module and its changed backend paths.
