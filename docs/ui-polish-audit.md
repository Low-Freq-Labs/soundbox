# UI polish review — November 3, 2026

Sonny reported that the information window appeared before its cover. The source
follow-up prepares that one cover before modal presentation. Canonical artwork
resolution and display-sized decoding remain in an owned worker; Qt timers and
result delivery keep running while it prepares. The cursor reports the wait.
This trades an incomplete window for a complete first frame; a contended artwork
lock can still delay opening. It adds no artwork prefetch to track loading.

The November3 follow-up was installed as build160, runtime `b0ccb1ab…`. The
November4 update is runtime `7bbe8577…` and includes the export work below. [HANDOFF](HANDOFF.md) records exact validation and provenance.

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

The private inventory covers all 179 current first-party application files:
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
log directory. Read-only mount facts identify two writable generic USB targets;
no actual USB export was performed by the agent.

The reported native merge error was reproduced from three small catalog copies,
with one extra Plus-only collection song and identical playlist memberships.
Selected merges preserve each format's unrelated baseline. Cleanup removes only
old members of the selected playlists that no remaining playlist in either
format references; shared assets and unrelated collection-only songs survive.
Exact removed payloads restore on pre-commit failure; after durable commit their
recovery copies are retired, with resumable interrupted cleanup. Peak USB space
for new copies is still required until commit. HANDOFF records release status.

November4 delivery is installed and normally reopened, with2564reconciled
offscreen/Cocoa tests,39additional focused Cocoa cases,675frozen inputs and
160personal/156public packaged-module parity. A cadence check failed at4%battery
and passed focused/full native reruns on AC with unchanged source; exact scope
and evidence are in HANDOFF. The dated179-file inventory above remains distinct
from review of the additional Export module and its changed backend paths.
