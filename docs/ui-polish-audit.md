# UI polish review — November 3, 2026

Current personal build169 is installed and accepted. It includes eager Info and
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

1. Manual image-file choices still read bytes and normalize/publish canonical
   art from GUI callbacks (`MainWindow._art_pick`, `_apply_catalog_action`,
   `ArtCache.store_ok`). Large images or slow selected storage can pause input.
   Artwork review also captures exact canonical cover bytes synchronously on
   selection. Moving this work needs publication/restore generation guards and
   exact review-baseline retention, rather than an unguarded background write.
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

Evidence lives under `~/Library/Logs/Soundbox/ui-polish-2026-11-03/`, including
the per-file source review and frozen source/test manifest. Keep final check,
package and installation receipts there; never replay an earlier installer.

## November4 export follow-up

The Info wait cursor now colors only the native wheel's white pixels using the
active accent; black and transparent pixels/hotspot/shape remain exact. Generated
pixel proofs cover five accents; focused Cocoa preparation/lifetime checks pass.

The device header opens an Export selector with playlist/folder checkboxes left
and connected targets right. A sole target is preselected, several may be checked,
and one action queues them through the existing serialized worker lane. Selection
and target identities bind queued work; failed targets can report their result
while later targets continue, and cancellation/shutdown clear remaining work.
USB target results aggregate; iPod first-generation plan review remains intact.
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
