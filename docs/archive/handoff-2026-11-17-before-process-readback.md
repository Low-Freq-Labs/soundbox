> Historical handoff checkpoint. Dated approvals grant no new app control,
> source/device writes, installation, replay, restoration or cleanup.
> Current guidance and installed identity: [HANDOFF](../HANDOFF.md) and [runtime contracts](../runtime-contracts.md).

# SoundBox current handoff

Sonny now requests ongoing parallel critical-path audits alongside focused fixes.
Keep three independent tracks active and record reproducible findings in
[the audit ledger](../critical-audit-2026-11.md). Unrelated audits may continue while
the current release is tested/delivered. Their findings do not grant source/app/
device action or native-profile authority; add only validated changes to updates.

## Current runtime — November17 scrolling still fails

Personal172/source`4caf9787b512b755d0aead3b34d430779db3a2a4787afb297582d339368788dd`
is installed at `/Applications/Soundbox.app`, executable
`cb7a3250a94c86da5232a84e06a71a2b226c6dd15bd215b4563c985df799b162`.
Installation completed09:33:53UTC; background normal launch began09:34:29UTC,
PID46166. This is uncommitted source based on HEAD`d6b504e`, not a new commit.

The fresh bare `./check.sh --render` passes all3,632 cases:3,566offscreen in
618.427s and66Cocoa in71.170s, zero skips/expected failures, exact800 frozen
non-Markdown inputs. The first attempt's real1ms heartbeat failure is preserved;
only final event dispatch was added after the original10/15ms waits, with all
oracles unchanged. Signature, first-party code/entrypoint and25native TEXT
sections match. Render and packaged isolated images were inspected; isolated
native deck/process guard/query/private SQL pass with zero library/devices/art.
Evidence is `continuous-scroll-final-gate-20261117/` under the private base below.
Sonny approved the brief synthetic Cocoa windows; no real-app input followed.

Sonny resumed updates and rejected this candidate: Collection still stops,
possibly more often. Do not repeat its scrolling check or label it accepted.
The same authority SHAe707861f… remains installed. At the verified launch the
backlog was PAUSED at97/3,121, activeNone, with zero fields/receipt ACKs pending;
it was resumed and then paused again by Sonny for read-only diagnosis. The
current verified pause is240/3,121, activeNone, with no pending metadata/receipt
ACKs. Read current progress rather than restoring any diagnostic checkpoint. White icon,
3,298raw tracks/85playlists and zero analysis/preparation tasks are verified.
Normal Quit returned Apple Event -128 but the app then exited naturally; no
force termination occurred. Complete current manifest/records/portable state
(10,257,638bytes) was captured at09:33:16UTC before replacement.

First verified refresh retired only two old fulfilled title masks, on104117801
and104675063 (frozen indexes46/54). Their original saved ACKs and fresh copied-DB
native titles match, and actual registry/engine computation preserves ALL3,298
effective title/artist pairs. Only those two title_shields cleared; all other
origins, protected roots, frozen targets/order/progress, analyses, authority and
art metadata remain exact. Initial strict-difference/diagnostic failures are
retained separately. No native SQL write was needed for this verification.

GUI publication measured~35–42ms versus the earlier~99ms; repeated plan copies
are removed and steady canonical commits measure~125ms on the copied checkpoint.
The~50ms reachable-heap GC pause remains OPEN: causal raw45.57ms versus
observed/frozen48.67ms does not justify removing mutation guards. Do not claim
smooth scrolling from tests or component timings. A consistent disposable current
checkpoint with continuous scrollbar input advances176→179 with zero GUI saves,
unchanged artist-registry semantics and1–2ms Artists preparation. Its main-thread
timer nevertheless pauses up to161.5ms during successive cache phases, with
separate125–158ms gaps during readback; GC reaches59.4ms.
The earlier600ms Artists result paired historical97 state with a newer database
and does not identify this current route. Split readback profiling is underway.
Uninstalled source now queues exact UI preference leaves and stages curation pins
inside the durable metadata ACK, removing a separate hidden GUI save. Focused
tests pass; these changes have not passed a new full gate or been installed.
Three agents are auditing write/persistence, playback/artwork and device/export/
recovery UX; browsing/editing receives a separate review. Confirmed failures and
unproven leads remain distinct in the audit ledger.

The wider pass has produced additional isolated wrong-selection/load, lost inline
draft, duplicate-navigation, stale-drag, export-wait/eject/cleanup and stock
rollback-ownership failures. Scripts, source hashes and proposed fix scopes are
retained privately; none of those queued fixes is installed. The control-to-writer
matrix distinguishes supported native paths from known extra-info/art/analysis
profile gaps.

Actual cache-only controls isolate GIL contention: main paint10–12ms CPU stretches
to~150ms wall while the existing background encoder runs. Own-PID native stacks
show PySide model callbacks waiting in take_gil, without GC in those gaps. The
cue cleanup-tail hypothesis is disproved (max3.725ms). Explicit scheduling trials
preserve exact outputs but trade frequent waits against cache time: zero-sleep
does not help, and the best0.5ms CPU/0.5ms handoff reaches24.44ms maximum at~2.35x
wall time. These changes and their prototype tests were removed from production;
exact prior cache hashes are independently verified. A native-table paint-only
prototype reaches50.25ms with more medium gaps and takes2.4s to prepare; it is not
a production fix. The explicitly authorized synthetic Cocoa comparison is now
complete: the same3,298-row/five-cache workload reaches147.88ms maximum with the
old worker,42.04ms with a whole-message helper and19.05ms with bounded typed
batches. The last variant has zero gaps above20ms; cache bytes/fingerprints match.
All synthetic windows and helpers closed after57.8s; the real app remained paused.
This is a cache-specific causal result, not complete or installed smooth scrolling.

The uninstalled candidate now prepares Qt-free cache and native-read helpers
before ordinary interaction. Cache encoding and unchanged fresh native reads run
in those processes; the existing source QThread retains native write authority,
recovery, guards and durable ACK ownership. Read-helper failure retains the saved
operation and supports joined replacement through existing Retry. Ordinary source
refresh uses the same read helper after recovery. Inline title/artist drafts now
retain exact song/field/occurrence identity, text/caret/selection through compatible
publication; changed targets retain recoverable unsaved text instead of applying
it elsewhere. Focused regression suites pass. Review caught new accidental native
record/aggregate transfer ceilings; bounded multipart framing now preserves larger
records/headers/inputs, with actual encrypted TEMP parity coverage and unchanged
ordinary wire messages. There is no new raw native read size ceiling.
The first production three-pair/27-preference combined proof preserves all3,298
effective pairs/full raw facts, advances only TEMP240→243, closes both helpers and
performs zero GUI saves. Across1,506scroll steps its maximum gap is83.553ms,
p95~8ms. Remaining worst gaps align with complete canonical comparisons/full-plan
validation; parent default-GC still reaches51.58ms and GUI publication32–47ms.
Exact immutable/private canonical comparison witnesses and controller static-plan
proof now pass focused and independent review; fresh manifest/all-record/owner
guards remain mandatory and every phase checks dynamic progress. Fixed128
transport batching removes a redundant sizing walk: capture CPU38.4→2.6ms,
total parent CPU68.9→31.9ms and cache-only max gap37.6→14.25ms, exact outputs.
The subsequent combined three-pair/27-preference proof again preserves all facts
and curation. Only one full plan scan runs; later canonical verification overlaps
at most20.083ms gaps, versus the earlier~79ms. The cold first scan still reaches
94.29ms, GC48.883ms and GUI publication31–45ms. Do not call this zero-stutter or
hide the cold phase. Both helpers join with exit0 and real inputs stay exact.

Source`6dafcde76c29406af22f771a0a73e02f3557a754f191e4b0dc5e5b8442991eb2`
was frozen with817non-Markdown inputs for the complete invisible offscreen
preflight under `process-readback-gate-20261117/invisible-source-checks/`.
Native/CDJ builds, engine types and protected fixture checks pass. This is not
the required complete bare release gate: a new explicit question is pending for
its final~70sCocoa windows. The earlier one-minute diagnostic grant is fulfilled.
All3,667offscreen cases ran exactly once, zero skips/expected failures and zero
frozen-input changes. The run failed: one strict GUI-ACK NoGuiScan regression and
three playlist-hover MIME/event errors. The first is a real new curation-stage
GUI scan; its correction prepares complete identity occurrences in the existing
source worker and keeps the original oracle unchanged. The hover errors pass
alone but reproduce after the inline-draft suite. Direct weakref controls prove
`_populate_tree`'s recursive reconcile closure retains the borrowed Qt root until
GC; its native address can be reused by still-live MIME while the old tree-item
wrapper remains registered. Real-QDrag ownership and runner-list changes do not
fix it. Clearing the three synchronous helper cells in the existing finally
releases roots/old libraries immediately;23production draft/unchanged-hover/
lifetime cases pass. Original hover and draft tests remain byte-identical.
Invalid cleanup/native-inspection diagnostic controls are explicitly untrusted
and archived, never real-app actions. Preserve the failed run as failed.
Current source also refuses XML source switching during accepted cue/grid
ownership, and maintenance cancels callable workers before considering stop
flags. A separately confirmed maintenance cue/analysis coordination deadlock now
has a narrow early cue/grid refusal, with actual queue/drain/retry controls;
global scheduling and durable analysis intentions are unchanged. This latest
source supersedes the failed frozen candidate and needs a fresh complete gate.
Source`254c380955794c5bce34f0d5b0cff86e298b316cb39ef0c3d36cb16d152dca33`
is now frozen with820inputs for the complete second invisible sweep under
`invisible-source-checks-2/`. The post-ACK-index full-size copied run preserves
all3,298effective pairs/full raw facts and completes TEMP240→243. Max gap80.594ms
is still the cold check; worker GC reaches47.113ms, with no large GUI GC in this
run. This is not physical smooth-scrolling acceptance. The invisible render also
passes and was inspected. Required complete bare/Cocoa release validation and
the new window-phase answer remain pending.
No new candidate has been installed.
The second invisible sweep PASSED all3,678selected cases in685.645s with zero
skips/expected failures and all820inputs exact. This is source validation,
not the required complete bare release gate. The invisible render is also
byte-identical to the inspected image after the tree-lifetime fix.

The first signed254c package failed its windowless worker startup: PyInstaller's
Qt runtime hook imported QtCore before the ordinary entrypoint, correctly
triggering the unchanged Qt-free guard(exit2). That failure is retained.
A version-pinned custom-first runtime hook now suppresses ONLY embedded Qt
configuration for the exact internal worker flag; normal desktop initialization,
other PyInstaller hooks, worker dispatch and graceful exit remain unchanged.
Thirty focused bootstrap/cache/reader checks and16package/provenance checks pass.
The standard artifact inspector now verifies actual startup-script order and
exact first-party hook/entry code, in addition to PYZ/native/signature checks.

The CURRENT UNINSTALLED personal172 candidate is source
`ca5491fc2e3c3a4389403f8557b872ac8dcb2342636ae189edfbcf0f74fd9880`,
executable`6afa2400f115fb23b23862958d162a5a15242c28eeeb4d93120cddebef52ce8f`,
at `dist/personal/Soundbox.app`, with822frozen inputs under
`process-readback-gate-20261117/bootstrap-candidate/`. Source remains dirty
HEADd6b504e; no commit/push. Its strict signature, source/PYZ/entry/hook code and
25native TEXT sections match. Actual frozen cache+native-reader roles start
without Qt/windows, preserve exact3,298-row cache bytes and full3,296-native-track
facts, leave the TEMP source unchanged, stop/join with exit0 and retain strict
signature validity. Temporary full smoke inputs are retired.
Private evidence is `preliminary-package-verification.json` and
`packaged-helper-check.json` in that gate root. Final complete bare/Cocoa gate,
packaged desktop smoke and install/relaunch are still pending the NEW question
for~70seconds of synthetic windows. Do not use the fulfilled diagnostic grant,
silently infer approval or label this candidate installed. The running4caf app
and the SAME single171 rollback remain untouched; current pause/progress must
be captured fresh before any later handoff.
Keep app/source pause and the one exact retained240 TEMP fixture for this
diagnosis; no real-app input or source/device test is granted. The brief Cocoa
diagnostic grant above is fulfilled, not permission for more test windows.
Compact evidence is under `critical-write-audit-20261117/continuous-scroll-input-diagnosis/`
in the private base below. Separate browsing/device evidence roots are under
`~/Library/Logs/Soundbox/critical-{browsing,device}-ux-audit-20261117/`.
Keep the SAME single171 rollback and candidate/dist until actual acceptance;
do not restore an older cursor checkpoint or replay completed pilots.
General metadata migration remains unfinished until the full queue and final
independent native/curation verification drain. Read-only critical audits and
unapplied proposals remain in the ledger; no experimental DTO/CoreRows/engine
cleanup or broader native protocol was included.

## Previous runtime — November17 scrolling regression follow-up

Sonny has now tested installeded9db9fc: scrolling can still stutter, and he
noticed background admission deferring while scrolling hard. This update is NOT
accepted; keep171 rollback/dist. He successfully paused through the PLAYLISTS
heading's right-click menu (the initial R-menu instruction was wrong). The writer
is drained at97/3,121 with no active pair, field queue or receipt ACK; accepted
authority remains installed. Current uninstalled work moves full-plan validation
to workers, retains model owners, streams read-only catalog hashing and reuses
private unchanged canonical record specifications. The actual default copied pair
now measures~36ms GUI publication and125–152ms steady worker commits, but a
~50ms normal-GC pause remains. These measurements do not pass the scrolling check.
The focused agents completed the frozen-plan allocation and presentation work.
The fresh bare `./check.sh --render` gate is running under
`continuous-scroll-final-gate-20261117/`, source`4caf9787b512b755d0aead3b34d430779db3a2a4787afb297582d339368788dd`,
with800 frozen non-Markdown inputs.
The first `continuous-scroll-gate-20261117/` attempted all3,566 offscreen cases;
only one heartbeat assertion failed (no real1ms tick before stopping immediately
after qWait10). Qt's wait loop may finish with sleep rather than a final event
dispatch. Independent review approved two explicit final processEvents calls;
the original1ms interval,10/15ms waits and every assertion remain unchanged.
Both affected cases pass in0.269s. Failed reports/logs remain separately retained;
no production code changed for this test-driver correction.
Revision-bound immutable snapshots now remove~8.68MB of repeated target copying
per validation phase in isolated measurement. Complete semantic validation and
fresh canonical filesystem guards still run. An experimental fused presentation
pass delivered no steady total-GUI gain and is being reverted independently of
the other changes. Its three-pair diagnostic also retained an unbounded list of
cheap observe-call timing objects, so its GC/initial580ms stall is contaminated
measurement, not established production behavior. Preserve a compact failed-run
receipt and rerun with bounded instrumentation; never claim that result as a pass.
Clean normal-GC/default-cache proof advances97→100 with zero GUI saves and exact
protected AppData. GUI publication is~35–42ms versus the earlier~99ms, but the
~50–60ms reachable-heap GC pause remains open. A separate hidden-window causal
comparison measures raw45.57ms versus observed/frozen48.67ms (both collected0),
so removing mutation guards is not justified. The final candidate retains the
proven publication flow plus the small exact-NE comparison optimization; no
experimental DTO, smart-row projection or engine cleanup is included. This is
the next test candidate, not accepted smooth scrolling. Three agents continue
light read-only broader audits without competing probes or source edits.
Do not treat input deferral as the responsiveness fix or claim zero stalls.

Personal172/source`ed9db9fc1a3bac860927151045928c63453a7639cc53df6bfe5f5971c5639488`
is installed at `/Applications/Soundbox.app`, executable
`426fbd915f07fea48d4e97884e8c34b9f7c072f26d777931edf4aa6454dd8bbe`.
Installation completed07:07:22UTC; normal launch began07:07:56UTC, PID38556.
This is dirty source based on HEAD`d6b504e`, not a new publication commit.
The fresh bare `./check.sh --render` passes all3,558 tests:3,492offscreen in
600.882s and66Cocoa in71.611s, zero skips/expected failures, exact795 frozen
non-Markdown inputs. Signature, first-party Python/entrypoint and25native TEXT
sections match. Isolated packaged startup passes native deck/process/query/private
SQL, with zero library/devices/art worker. Both isolated/render images were inspected.
Evidence is `async-metadata-browser-final-gate-20261117/` under the private base
`~/Library/Logs/Soundbox/tag-smart-deletion-2026-11-12/`.

The SAME accepted v5 authority (SHAe707861f…) is restored to AppData and the
already approved backlog is running. Native SQL/profile bytes match the approved
candidate. No new permission is needed. Startup retains the white icon, loads
3,298 raw tracks/85playlists, and queues zero analysis/preparation tasks.
Complete current canonical manifest/records and portable state were captured at
cursor44 before replacement. The normal Quit Apple Event returned -128 while
shutdown drained; subsequent process absence and complete checkpoint are verified.
No force termination occurred. All protected imported/custom/hidden/removal/
addition/alias roots and frozen targets/order/scope remain exact after launch;
only current-pair metadata overlays and expected source progress/provenance change.
Native readback at cursor60 confirms94planned fields saved (37title/57artist),
4,996remaining, exact Content identities/paths/other columns and all unrelated
native tables/schema. Its older dated proposal required two exact supersessions
for171415640, already acknowledged in the immutable preinstall checkpoint:
Title2THESTARS / ArtistPAPAJAY & ZUZE. The diagnostic now binds these to their own
saved manual edit tokens/operation IDs; the initial stale-proposal failure remains
recorded, not concealed. This new build did not create those preexisting edits.

The release separates accepted permission from a durable exact-source background
pause, commits selected metadata leaves on an ordered worker, and preserves
prepared browser/model state across compatible native readbacks. Source ownership
lasts through durable acknowledgement and joined GUI continuation. Complete
validation, fresh drift/restore guards and source SQL protocol remain intact.
An unrelated unavailable historical import cannot veto a healthy selected song;
selected identity verification stays strict. Background canonical phases wait for
other source owners, and receipt compaction results wait for their safe ACK turn.

Full-size disposable-copy controller/source-QThread/coordinator proof advances
44→45 in6.637s without ordinary GUI `_save_state` calls. Steady durable worker
commits take214–243ms with12–13ms timer gaps; sparse GUI publication still takes
~99ms and full frozen-plan validation~25ms. These are remaining measured latency,
not zero-stall or accepted installed scrolling. Sonny has been asked to scroll
Collection for20seconds during updates; his acceptance is pending. Prior installed
9c25e622 failed his actual scrolling check. Keep the SAME single171 rollback until
he accepts this update; do not retire it or duplicate dist yet. Never restore the
old cursor31 checkpoint or replay pilots. General migration remains unfinished
until its entire queue and final independent source/curation verification drain.

The first async combined gate attempted all3,492offscreen cases and failed one old
menu stub missing the new pause property plus one retry-timer assertion (0rather
than1callback after600ms). The timer passes unchanged in isolation and the fresh
full run. Only the stub gained the required property; every original oracle stays.
Failed reports are preserved under `async-metadata-browser-gate-20261117/`.
Broader read-only audits and private unapplied next-update proposals are recorded
in the ledger; none of those proposed fixes is included in this installed binary.

The following runtime paragraphs are dated implementation history, superseded by
the current runtime above.

The combined scrolling/write-path candidate is source9c25e622. Its bare release
gate is running under `browsing-write-audit-gate-20261117/`, with787 frozen
non-Markdown inputs. SoundBox remains closed at frozen cursor31. The current
installed app is still172/sourceed679963; the same single171 rollback is retained.
Three broader read-only audits have already queued isolated relink/export-race/
independent-recovery findings in the ledger; these are separate follow-up fixes.
The first combined gate attempted all3,425 offscreen cases and caught five
signature errors across three older test doubles missing the new publication
keyword. Their callbacks now assert that keyword and retain original occurrence,
smart-order/save-failure and Collection selection checks; all31 affected focused
cases pass. No production source changed. The fresh bare complete gate is now
running under `browsing-write-audit-final-gate-20261117/`. The complete cursor31
preinstall checkpoint moved there together; the failed gate retains a relocation
receipt and failed worker reports. Preserve those as failures, not acceptance.

## Earlier runtime — November17 metadata backfill follow-up

The first presentation fix passed3,461 tests and was installed as personal172,
sourceed679963/executable2bd5a010 at04:45UTC. Live verification found a missing
compatibility case:15 adopted imports retain their original SoundBox added_ts.
The conservative check therefore correctly fell back to full preparation. The
writer reached cursor30/3,121 without source errors, then was paused again and
SoundBox quit normally after the final progress ACK advanced to31. Its authority is now held at
`metadata-publication-gate-20261117/runtime-handoff/authority-paused-for-import-time-fix.json`.
Installeded679963 is closed; it is not the current source below.

Sonny reports Collection scrolling choking during backlog processing and
explicitly requested subagents to audit all rekordbox-write actions. Three
delegated audits cover prep/cues/analysis, tags/playlists, and imports/removal/
canonical persistence. Current source adds exact saved import-time reuse,
unchanged Collection/custom row retention, interaction deferral for unstarted
frozen migration work, prepared tag/smart/import publication, correct default
production readback, coalesced discovery/scheduling and bounded record-directory
checks. A disposable copy of the6,433-record current checkpoint measured scalar
save latency0.511–0.514s before versus0.278–0.283s after the first state changes.
Fresh per-record drift/hash checks and prepublication fencing remain mandatory.
The combined release gate/build/install has not yet run for these later edits.
See the [write-path audit](../shared-library-audit-2026-11.md) for scope/limitations.

Sonny approved general exact-library v5 metadata activation, the5,090 remaining
title/artist fields, and normal SoundBox quit/install/relaunch with “yesssir sonds
good”. This supersedes the dated pending-approval statements below. Personal172
source72d6b1d7/executable1457ebf9 is installed and its exact v5 authority was
activated. The first19 frozen song pairs completed without a source error.
Each acknowledgement incorrectly repeated full library preparation/device/art
loading. The authority is temporarily held outside AppData in the current gate's
`runtime-handoff/authority-paused-for-refresh-fix.json`; all progress and pending
intentions are preserved. SoundBox quit normally after its active work drained.
Do not restore pre-backfill state or replay completed pilot helpers.

Current uninstalled source separates own metadata acknowledgement publication
from full loading. Compatible snapshots retain prepared imports and services,
refresh native cue/tag identities and smart memberships, and invalidate affected
browser rows (all rows only when artist lookup context changes). Source, media,
inventory or other scalar display changes retain full preparation. Native fresh
guards/readback and durable acknowledgement remain unchanged. Validation and
reinstallation are in progress; do not restamp installed72d6b1d7 as this source.
One immediate previous171 app is retained at the path recorded in
`album-cloud-gate-20261116/runtime-handoff/install-completed.json`; no second app
rollback generation may be created. The older169 path is absent.

Updated November16,2026 for Sonny’s requested reboot/wrap-up. Checkout branch
is `main`; use Git for the publication revision. The installed artifact was built
from dirty source based on `82d9d1faf052c057302426cd8852a4012280cade`, independently
identified below. A documentation/publication commit does not restamp that binary.

## Active work: complete shared-library behavior

Sonny requires every feature currently offered by SoundBox to persist correctly
in the shared rekordbox library, including older/historical native records.
Quitting or abandoning SoundBox must retain the supported edits in rekordbox.
**The current effective SoundBox library is the intended truth**: cleaned titles,
artist names/merges, organization and other saved edits must be preserved before
native refresh/migration. Native records provide identity and concurrency checks;
they must not replace that intended curation. Do not limit migration to raw
manual override maps: automatic title cleaning and artist derivation/aliases also
change what the user sees. Freeze title AND artist together before any writes.

Sonny resumed work after the pause while heading home. The bounded native test
route is now stopped after the November13 shutdown crash described below.
The passing source and preserved metadata checkpoint remain intact;
Sonny has now separately approved installing/relaunching the compatible test
candidate below; that handoff is installed and mechanically verified, pending
his visual acceptance. Native source-writing/profile acceptance remains held.
The previous layout/deletion candidate is superseded; its test result does not
validate these later changes. Do not enable incomplete profiles, install the old
candidate, or label local-only saves complete. See [audit](../shared-library-audit-2026-11.md)
and [roadmap](../ROADMAP.md). Public splitting/distribution remains deferred;
waveform appearance remains accepted.

## Resumed SoundBox completion — November16, after the manual iPod workaround

Sonny completed the Apple Music transfer himself and reports hearing music. That
is user-reported Apple-path playback, not a SoundBox export acceptance. He asked
to resume SoundBox implementation and leave the iPod/Music workflow alone. The
previous completion snapshot was published as `d6b504e`; current changes are
uncommitted. Installed839fc142 remains the exact package described below.

Sonny explicitly approved one real-source test: only Title
`8B@LL x KONSENT - DESIGNER` and Artist `8B@LL x KONSENT` on native148797669,
UUID3f60cd1c-1a73-4392-8755-61a097e87a9e. The installed/source-matching v2 writer
saved both through its normal guards/shared lock/journal at22:33UTC. Its temporary
in-memory authority restricts two exact operation IDs; no production authority
file was installed. Fresh full comparison proves counter+5/revision+2, one new
artist, exact other Content/existing artists/tables/schema/XML and unchanged
SoundBox state/intentions. No audio/device/Music input occurred. Before/after
compact exact-row/table-hash proof is retained under
`tag-smart-deletion-2026-11-12/metadata-designer-pilot-20261116/`. A failed initial
read preflight correctly refused SQL outside TEMP; copying the retained baseline
to TEMP completed inspection without weakening that guard. Sonny opened rekordbox himself and confirmed “Both fields match”; the v2
source/reopen pilot is accepted. Its completed full DB/portable/XML payloads were
retired after compact verification under the November10 retention rule
(142,903,662bytes); current shared recovery is untouched. General activation is not approved.

The native component matrix now passes28 generated cases under the identical
node-only root/policy/binary used previously: Unicode codepoints/combining marks/
emoji/255-character text, title-only existing-album preservation, Unicode artist
creation/link and reuse of an unchanged synced artist. Full other-row/column and
counter assertions pass; network/outside-read/outside-write are denied. This is
business-component evidence, not Main/transport/native reopen. The generated DB
was retired after exact receipts; original source/profile/audio were inaccessible.
`native-fixture/metadata-protocol-20261116/expanded-completed.json` binds the proof.

New source adds independently marked `unicode-active-metadata-v3`. V1/v2 retain
their original semantics, including reconstruction of old numeric-string
revisions. V3 preserves Unicode codepoints, AlbumID on title-only edits and
unchanged synced0/1 artist rows without remote USNs, including NULL local clocks.
It admits Analysed0/105 and canonical metadata revisions0/1/upward or NULL. Static
Main inspection traces selectUpdatedInfo → String::getIntValue/atoi → increment
→ modifyTrackInfoUpdated; NULL starts at0. Two generated business-component
matrices (10 and28 cases) prove the separate content/revision steps, Unicode
artist creation/link and unchanged artist reuse on bare and analysed rows. Every
unrelated table/row/column and opaque counter field remains exact. These are
component/static proofs, not v3 native reopen acceptance. The83 bare tracks now
have an implemented candidate route. Cloud/status257, artist-album transitions,
synced orphan retirement and combined artist creation/retirement remain guarded.
Low-level deletion probes observe local hard-delete/counter0 versus synced
tombstone/counter1. They do not establish Main's complete retirement sequence;
failed generic-route capture attempts grant no extra writer scope. A corrected
probe invokes the exported model factory before capturing the actual deletion
handler; both generated local/synced cases pass with the same model semantics.
Independent static Main tracing now locates its different cleanup: one next-USN
step for the selected batch, local DELETE and synced/status-aware UPDATE using
a separately captured UTC clock. This explains the low-level counter difference;
it does not implement or activate synced/combined retirement. Exact primary
evidence is `metadata-protocol-20261116/artist-retirement-next-proof.json`.

Thirty-four encrypted-TEMP writer tests pass, including lost-commit/compact-ACK
compatibility for old profile journals. The earlier full gate was interrupted after two workers passed
when a window complaint was misrouted to this agent. Sonny explicitly clarified
“wrong agent” and resumed SoundBox work; no foreground interference by this
agent was established. Preserve that interruption receipt as nonacceptance.
A fresh bare `./check.sh --render` passes all3,435 cases:3,369offscreen in575.129s
and66Cocoa in74.889s, zero skips/expected failures and exact inventories. All780
frozen non-Markdown inputs remain exact, source742874e3; lint/layout/types,
native/CDJ builds,12Rust cases,243vendor files and required fixtures pass.
The12-song/zero-device render was inspected. Evidence is under
`unicode-metadata-final-gate-20261116/`. The personal build172 is packaged and
verified at `dist/personal/Soundbox.app`: source742874e3, executable SHA256
`be2d89a4b21537e694142cf3eb19a9727aa0ccfecbcca89154d95469f7636a22`.
Strict signature,185 first-party modules/entrypoint and25 native TEXT sections
match the frozen source/build. Isolated packaged startup passes native deck,
process guard/query and private SQL/ORM, with zero devices/library/art workers.
It is not installed; `/Applications/Soundbox.app` remains839fc142. The bounded v3
native pilot below is now accepted; installation and broader publishing remain
pending. Never use v3 to
reinterpret v2 pilot receipts or silently enable bulk migration. The separate
iPod/Finder agent owns mounting and OS integration; do not interfere with it.

Fresh read-only audit of the actual3,121 queued pairs admits2,111 title and2,904
artist changes under v3;1,139 fields already match. The remaining88 field changes
are39 old-artist retirements,35 artist-album transitions,4 combined creation/
retirement,8 remote/status257 fields and2 unsupported artist-reuse states. Those
counts refer to the current queue, not the older all-record snapshot. Five
representative actual queued records pass the production writer on one encrypted
TEMP copy: album title, synced actor reuse, analysed NULL revision pair, bare
artist and Unicode title/artist. Complete unrelated Content/artist/table/schema/
XML assertions and compact-ACK replay pass. The copy/recovery were removed;
fresh native state and full logical SoundBox state remain exact. Evidence is
`unicode-metadata-final-gate-20261116/current-target-copy-result.json`.
Sonny then explicitly approved only four native records/six fields: DEAD DAW
Title; Payday -Shadow Puppets (Yami Flip) Title/ArtistYAMI; ALLEYCVT - Live @ Lost
Lands2024 ArtistALLEYCVT; and 不明 Title/ArtistUNOWN. All six normal writer saves
completed at23:22:52UTC with counter+13 and one new artist. Complete unrelated
Content/old artist/table/schema/registry/XML and logical SoundBox state/pending
intentions stay exact. Temporary in-memory authority restricts the six persisted
operation IDs; no general authority file, audio/device write or native input.
All four manual native reopen checks pass by Sonny's screenshots: DEAD DAW /
HOL!, Payday / YAMI, the unanalysed ALLEYCVT set, and Unicode 不明 / UNOWN. For
artists with many songs, ask for the exact title search. Fresh encrypted-TEMP
readback confirms all four pairs/UUIDs/paths and the unchanged schema/logical
SoundBox state. Only agentRegistry changed after native open; no other native
table differs from the applied proof. No general authority was activated.
The completed pilot's142,903,662bytes of DB/portable/XML payloads are retired;
selected rows, complete table hashes, approval/reopen and exact retirement receipts
remain. Unknown/current shared recovery remains protected. Exact approval,
requests and results are in `unicode-metadata-final-gate-20261116/approved-four-record-pilot/`.

## Artist retirement follow-up — source candidate

The newer working tree adds independently marked
`unicode-active-artist-retirement-v4`; the built742874e3 candidate remains v3.
V4 preserves every v1/v2/v3 reconstruction and handles last-reference local
deletion, synced status2/deleted1 history and combined artist creation/retirement.
The synced actor's persisted `retirement_clock` is separate from final Content
clock; only v4 accepts that request shape and artist UPDATE columns. Its cleanup
counter follows the Content-field step and precedes TrackInfoUpdated; Main's
signed-int counter bound is explicit. Unrelated unused actors and shared roles
remain intact; no global cleanup runs.

Four generated shipped-component/literal-Main-SQL cases support that composition.
The initial reference incorrectly used Agent `nowString()` (12-hour hour text)
for Main's independently traced gmtime24-hour clock; the first43-case run failed
its combined synced clock ordering. The original bytes remain pinned in a
refusal regression. An independently captured UTC matrix retains every full-row/
opaque/counter assertion; all44 metadata tests now pass in63.208s. No production
guard/threshold was relaxed, required private fixture substituted or test skipped.

Copied-library verification on the exact current3,121 queued pairs passes local
combined cleanup, synced retirement, Unicode/bare/NULL/album-title/reuse cases,
unrelated native parity and compact-ACK replay. The candidate admits43 additional
artist changes;45 fields remain guarded (35 artist-album transitions,8 cloud
Content fields,2 actor-reuse states). Current counts:2,108 admitted title changes,
2,944 admitted artist changes and1,145 already-equal fields. All actual source
and logical SoundBox state stay exact; TEMP copy/recovery were removed.

The fresh bare `./check.sh --render` passes all3,445 tests:3,379offscreen in586.852s
and66Cocoa in68.677s, zero skips/expected failures and exact inventories. All782
frozen inputs remain exact, source5e42fab8; lint/layout/types, native/CDJ builds,
12Rust cases,243vendor files, immutable private fixtures and inspected12-song/
zero-device render pass. `artist-retirement-gate-20261116/completed.json` binds it.

The signed personal build172 at `dist/personal/Soundbox.app` now contains5e42fab8,
executable SHA256 `0453b4e9fcd832557987b221d7127f27e2bdfcc67164ffe168596e2beefa5d22`.
185first-party modules/entrypoint and25native TEXT sections match; strict signing
and isolated packaged native/process/SQL startup pass with zero real library,
devices or art workers. It supersedes the uninstalled742874e3 candidate; do not
restamp either binary. `/Applications/Soundbox.app` remains839fc142.

The exact proposed two-record helper procedure passes an additional encrypted
TEMP rehearsal: bbno$ - it boy (Zoey808 Remix) ArtistZoey808 plus only its unshared
local bbno$ definition removal; Four Speed ArtistSPASS plus only its unshared
synced Spass tombstone. Counter+7, one new artist and complete unrelated native/
curated parity pass. Script hashes bind `pilot-rehearsal-result.json`; TEMP data
were removed. The original source and real approval file remain untouched.
Sonny separately approved these two saves/cleanup with “yes you are good”.
`live-source-approval.json` retains that exact reply and proposal binding. The
helper's approval parser also accepts this explicit free-text reply; its updated
exact script hashes pass another encrypted TEMP rehearsal. The actual two source
saves complete at01:32:06UTC November17 with counter+7, one new Zoey808 definition,
only the approved unused bbno$ definition removed and only the approved Spass row
tombstoned. Complete unrelated Content/artist/table/schema/registry/XML and logical
SoundBox state/intentions remain exact. No production authority file, audio/device
action or app control occurred. Both native reopen checks pass: Sonny's screenshot
confirms it boy Zoey808 Remix / Zoey808, and “ohhhh yeahhhhhh :)” confirms Four
Speed / SPASS. Fresh encrypted-TEMP readback verifies both pairs/UUIDs/paths and
the exact removed/tombstoned prior artists. Only agentRegistry changed on native
open; schema and logical SoundBox state remain exact. This bounded v4 pilot is
accepted. Its completed142,903,662bytes of DB/portable/XML payloads are now
retired after an exact selected-row/table-hash retirement receipt; current shared
recovery stays protected.
Evidence: `artist-retirement-gate-20261116/approved-two-artist-pilot/`.
General metadata activation and actual app installation remain pending.

Further generated Album-update component research passes four local/synced
windows. Even unchanged Compilation0/empty AlbumArtistID advances one counter
and its Album clock; NULL normalizes to0/empty. This is not Main lookup/composition
or native reopen proof. Artist-album edits cannot merely drop the current guard.
`metadata-protocol-20261116/album-update-component-completed.json` retains it;
the generated DB is retired and production SQL/profile scope stays unchanged.

## Album, cloud and active-history follow-up — v5 source candidate

Current uncommitted source adds `unicode-album-cloud-metadata-v5`. It models
unique active album/name/UUID/role lookup, preserves album names and surviving
artist roles, and captures the separate Album bookkeeping clock/counter before
old-artist cleanup/revision. Its artist-only Album SQL is limited to Compilation,
AlbumArtistID, rb_local_usn and updated_at. Cloud status257/synced1/local status0or2
and exact positive remote USNs stay unchanged. Active desired-artist lookup
preserves same-name deleted history. Cloud-album and remote-artist retirement
remain guarded. All older profile/request/receipt meanings stay intact.

Twenty-four generated Album compositions and sixteen cloud update stages pass
under the unchanged node-only root/policy/binary. Static Main lookup/parent calls
support the composition; this does not execute Main or grant native acceptance.
The pinned new fixture retains exact SQL TEXT casts for large remote INTEGERs.
The initial53-case focused run failed because the old generic registration test
schema gives usn TEXT affinity. The new cloud-only fixture uses native BIGINT;
the strict production integer guard was not weakened. All53 focused tests pass
in72.899s. The subsequent bare `./check.sh --render` passes all3,454 tests:
3,388offscreen in591.077s and66Cocoa in70.951s, zero skips/expected failures and
exact inventories. All783 frozen non-Markdown inputs remain exact, source
`72d6b1d7717979eb157844d8852733a4a5758973f86014cdd44b4f1bfd7af2cc`.
Lint/layout/types/native/CDJ/fixtures pass; the12-song/zero-device render was
inspected. The offscreen render correctly has no Cocoa native deck; its separate
Cocoa drawable/playback gate passes.

Read-only audit of all3,121 current queued pairs now admits5,095 changed fields
(2,112titles/2,983artists);1,147 already match and none are profile-refused in that
cohort. All41 formerly guarded records pass56 paired field saves on one encrypted
TEMP copy, counter+183, complete exact Content/Artist/Album expectations, other
tables/schema/XML and compact ACK replay. Original musical/native counter and
logical SoundBox state remain exact; TEMP copy/recovery are removed. Evidence:
`album-cloud-gate-20261116/current-target-copy-result.json`. The two old actor
refusals are CROMATIK's active/deleted name pair, not remote actor states.

The signed personal build172 at `dist/personal/Soundbox.app` now contains72d6b1d7,
executable SHA256 `1457ebf916988425568cc6e39460e0d77082c5dba43c9a3209aae41cb14cacac`.
All185 first-party modules/entrypoint and25 native TEXT sections match the tested
inputs. Strict signature and isolated Cocoa/native/process/SQL startup pass,
with zero real devices/library/art workers; the empty render was inspected.
It supersedes the uninstalled5e42fab8 candidate. Fresh inspection confirms
`/Applications/Soundbox.app` is still171/source839fc142/executable24e23be2.

The exact proposed three-record/five-field helper passes another encrypted TEMP
rehearsal: BRAIN DUB ArtistSMITHWORKS (preserve WAVELENGTH name/album artist,
update only its bookkeeping clocks and remove the unshared old local artist),
SFOGLIATELLE Title/ArtistKILO!, DOOM SEQUENCE Title/ArtistCROMATIK with its deleted
history preserved. Counter+14/new artists2 and complete unrelated native/curated
parity pass. Original source has no changed tables; TEMP data are removed.
`pilot-rehearsal-result.json` binds both exact helper hashes. Sonny subsequently approved only those three records/five fields and related
bookkeeping with “yesssirrrr”. All783 frozen inputs and both rehearsed helpers
remain exact; rekordbox and its agent were already closed. The actual five saves
now complete through the normal shared guarded writer with counter+14 and two
new artists. Complete unrelated Content/Artist/Album/table/schema/registry/XML
and full logical SoundBox state/intentions remain exact. Only the old unshared
local SMITHWORKS definition is removed; the WAVELENGTH name/album artist and both
existing CROMATIK active/deleted definitions are preserved. No audio/device
write, production authority file or actual app control occurred.

All three manual native reopen checks pass: BRAIN DUB / SMITHWORKS,
SFOGLIATELLE / KILO!, DOOM SEQUENCE / CROMATIK. Sonny's final reply is “yesssir
good to go”. Fresh encrypted-TEMP readback confirms complete target rows/UUIDs/
paths, created actors, preserved Album name/role/bookkeeping, removed old local
artist, exact active/deleted CROMATIK history, counter, schema/XML and complete
logical SoundBox state. The v5 bounded native pilot is accepted.

The first reopen comparison correctly stopped on an additional agentNotification
delta. Inspection pins only row329's public promotional URL and updated_at:
`https://rekordbox.com/enplan/` → `https://rekordbox.com/en/plan/`. Every other
notification cell/row stays exact. Source publication itself left that table
unchanged; this later native-open refresh plus agentRegistry are the only table
deltas. The initial refusal and exact later comparison remain recorded; no
production authorizer/profile or general comparison exclusion was broadened.

The completed142,903,662bytes of DB/portable/XML payloads are retired after
independent TEMP verification and compact exact-row/table-hash proof. Current
shared writer recovery stays protected. Do not replay these completed helpers.
Evidence: `album-cloud-gate-20261116/approved-three-record-pilot/`.

A new read-only activation proposal covers the exact current3,121 frozen pairs:
1,152 fields already match;5,090 changed fields remain (2,110titles/2,980artists),
with no profile refusals and no extra Track Info overlays in this cohort. Current
pending metadata request is uncaptured; descriptor/canonical curation are intact.
`current-metadata-activation-proposal.json` contains every proposed old/new field.
`metadata-authority-candidate.json` is only a private draft, not the AppData file.
All783 frozen inputs still match the tested72d6b1d7 package. Installed171 and
production authority remain unchanged. Next ask explicit permission to close/
install/relaunch SoundBox and enable this exact-library writer/backfill; the
three-record test grant alone does not authorize those actions. After approval,
Sonny must close rekordbox himself before any backfill. Preserve exact canonical
manifest plus referenced records/portable state and the current white app icon;
use the full guarded install/relaunch procedure and the one retained169 rollback.
Do not create another app rollback generation or overwrite newer curation.

## Current delivery and reboot handoff — November16

Sonny requested reboot before reconnecting the new iPod, then said “lets wrap up”.
The updated personal app is installed at `/Applications/Soundbox.app`, build171,
tested source fingerprint
`839fc142b99a9387122b7416f683aab7112d2b77b7f09a5e05d14dfacd7cea26`, executable SHA256
`24e23be2b1787411a2ff18a5faaf98dcb9e012860d866d0f12d3cb7970c5c5e1`.
Its bare render gate passed all3,426 tests exactly once:3,360 offscreen in550.126s
and66 Cocoa in69.249s, zero skips/expected failures,780 frozen inputs unchanged
during the gate. Final staged whitespace checks removed trailing blank lines from
three test files with exact compiled code/positions/constants comparison; runtime
source and package are unchanged. `test-eof-normalization.json` records that check.
Lint/layout/types/native/CDJ/fixtures passed; the12-song/zero-device render was
inspected. Strict signature,185 first-party Python modules plus entrypoint and25
native TEXT sections match source/builds. Isolated packaged startup passes with
native audio/graphics/process guard and SQL/ORM, zero real devices/library/artwork.

The update ends export waits on terminal source-save/admission errors, retaining
all intentions. OS-identified iPods retry a provisional generic-USB profile until
stock folders appear; ordinary USBs gain no idle profile probes. Normal Quit can
retain uncaptured metadata intentions and unstarted read-only derivation when
metadata authority is absent. Active workers, captured requests, receipt ACKs,
malformed queues and other source saves retain their existing drain gates.
No native authority or device-write profile was expanded.

The prior7f658325 package passed3,425 tests but refused actual normal Quit due
to the read-only backlog. Its stuck PID1442 received one recorded SIGTERM only
after a complete exact manifest/record-store/portable checkpoint, verified empty
other source queues, no captured metadata request/common transaction, absent
metadata authority and ejected device. No SIGKILL or second rollback was used.
The new package’s actual normal Quit exited PID4170 without forced termination,
then reopened PID4202/birth1794860560.359688 at20:22:40UTC. Native deck is active;
3,298 tracks/85 playlists load,3,291 analyses are reused, zero prep/art fetches run.
All curation/intentions,3,296 displayed pairs,3,449 complete title/artist pairs,
authority/art JSON and all42 native tables/schema/counter/XML stay exact. Only
`settings.columns_state` changed during normal window serialization. New logical
baseline SHA256 is
`e216ba2d80ac1b86a6d3fdcc94a113a146492009d1324b8ab8614b999035e62a`.
The white Finder/Dock icon and signed Contents remain exact. Sonny’s screenshot
shows Collection3296 and Favorites212. Keep the single pending169 rollback until
accepted verification; packaging retirement is also pending. Do not claim full
feature completion or physical iPod acceptance from this handoff.

The new approximately1TB iPod was repeatedly unmounted or disappeared while Sonny
independently restored/reformatted it. Finder firmware reached100% before HFS
format/check; spinner alone never proved completion. A later mounted HFS iPod had
stock folders but no iTunesDB/CDB. Sonny then independently formatted FAT32,
name SONNY-AIF’S, UUID44E5874B-FB57-3C5C-88B3-43488C1D03EF. Read-only mount failed;
read-only fsck_msdos -n passed. Its partition-map capacity note did not authorize
repair. Standard non-forced ejects passed, most recently20:11:12UTC after fresh
identity checks. Sonny confirms the unplugged device boots to Apple’s normal
main menu. No further formatting, restore, repair, Rockbox install or device music
write was performed or requested by us. After reboot, reconnect once and inspect
fresh mount/model/database identity before selecting a playable stock-iPod route.
Never reuse an old disk number/UUID or treat a generic USB copy as iPod playback.

Favorites contains212 locally present tracks:39MP3/173WAV,6,876,312,388bytes.
An optional local M3U8 is prepared only in the prior gate’s
`handoff/favorites-for-local-transfer/`; Music/Finder were not automated. Native
metadata authority remains absent. The visible “Native metadata editing requires
separate acceptance for this library” message is a real remaining title/artist
publishing blocker, not a resolved save. Existing source publishing/profile and
other feature-audit gaps remain open; preserve all current curation and intentions.

Current evidence root is the private logs directory
`tag-smart-deletion-2026-11-12/device-export-quit-backlog-gate-20261116/`:
`inputs.json`, `completed.json`, `package-verification.json`, `isolated/runtime.json`,
`reconnected-ipod-normal-eject.json`, and `handoff/` complete checkpoint, stop/install
receipts, normal-Quit result, startup/relaunch logs and `runtime-verification.json`.
Older3,418/3,425-case package/install descriptions below are dated history and are
superseded by this identity. No source/device write acceptance follows from wrap-up.

## Work ownership and current source changes

- Tag/cue/grid agent: durable single-flight tag assignment with exact readback,
  restart/acknowledgement recovery, bare-track DB-only cues, and production prep
  admission/dependency audit. Native codec/row-state gaps remain explicit.
- Playlist agent: duplicate membership identities and selection, exact dialog
  target pins, ordinary sibling projection, composed folder deletion, and
  transaction retry/recovery. Mixed/smart reorder and move remain audit gaps.
- Metadata/import/removal agent: title/artist source publisher/controller,
  curation save-failure rollback, conversion/removal callback identity, and frozen
  source-truth migration with source-scoped curation. The narrow metadata proof
  does not cover the real library's complete metadata/state/Unicode range yet.
- Root: composition, source selection fencing, shared recovery, fair scheduling,
  migration integration, independent review and disposable native-proof research.

Source selection now fences physical work and source-bound intentions, permits
returning to their original source, and persists selection before publishing it.
Tag assignments and metadata join quit-draining and independently routed shared
recovery. Fair source scheduling includes metadata and durable tag assignments;
source-specific reservations and normal native guards still apply.

The superseded first November16 candidate passed the3,418-case render gate, including the
November16 tag-readback correction and separately admitted synced-metadata
candidate. All780 frozen inputs remain exact. An earlier attempt failed the
Cocoa phase with the display asleep; after Sonny woke/unlocked it, the fresh bare
gate passed every case. The failed receipt remains separate. That compatible test
package was installed under Sonny’s explicit approval; current identity is above;
native profile and continuous-parity gaps remain open. Source validation does not
activate those capabilities.

The first audit gate ran all3,165 offscreen cases: two failures, both retained
and corrected (tag readback fixture/direct timing and exact-membership menu
routing). Cocoa/render were not reached. The second bare `./check.sh --render`
passed all3,232cases (3,166offscreen+66Cocoa), zero skips/expected failures, plus
render, lint, layout, engine types, native/CDJ builds and immutable fixtures.
That765-input gate predates the follow-up correctness fixes below and must not
be reused for their changed inputs. Evidence:
`shared-library-gate/` contains `inputs.json`, `offscreen.json`, `native.json`
and `completed.json`. Outstanding native profiles remain unfinished.

## Current follow-up: existing features, offline verification

Sonny reaffirmed the scope: fully flesh out what SoundBox already offers, with
older records and failure/restart edges; no product expansion or online features.
Keep Wi-Fi on. The disconnect/relaunch request is withdrawn. Native automation
remains stopped; request only concrete manual existing-feature checks one at a
time when needed. A reproduced selected-alias playlist-removal correction is now
in source: capture all selected logical IDs sharing one native song, preserve
unselected/newer markers, and process each native target group once. Its two new
regressions pass individually, including encrypted TEMP failed-ACK/restart replay.
The preceding bare `./check.sh --render` passed all3,413 discovered cases:
3,347offscreen in592.515s and66Cocoa in70.206s, zero skips/expected failures,
780 unchanged frozen non-Markdown inputs and all required lint/type/build/fixture
checks. The12-song/zero-device render was inspected; all3,449 frozen title/artist
pairs remain exact. `ordinary-overlay-alias-gate/inputs.json` and `completed.json`
bind this result. This supersedes the3,411-case source gate below; installed171
and native authority/profile acceptance are unchanged.

Installed171 manual steps1–7 passed ordinary empty-playlist creation/rename,
root-folder and child-playlist creation, normal quit/reopen, scoped folder/child
removal and final cleanup. Sonny confirmed the first menu choice was New Playlist;
there is no folder/menu bug from that action. Zero active test objects remain.
Every non-counter native table matches the pre-step1 baseline; the cycle advanced
the counter9 times and retained three expected native XML node entries. Do not
purge those entries or restore old XML. Exact per-step receipts and retired
ownership are under `pending-native-batch/manual-soundbox-step*-result.json` and
`manual-ownership-after-step7.json`. Reopened PID76067 retains installed171's
exact executable; no new package or authority was installed/activated.

Manual step8 passed older-tag display for `8B@LL x KONSENT - DESIGNER`, native
ID148797669, UUID `3f60cd1c-1a73-4392-8755-61a097e87a9e`: Sonny confirms Dubstep
and Trap checked, matching fresh SQL associations created in2024. Eight bounded
production Tag-writer checks on a disposable encrypted copy also pass no-op,
rollback, committed-retry idempotence, tombstone/history, reassignment/replay,
local-row removal and stale-UUID refusal. Original tables/schema/selected rows,
XML and the live authority stay exact; the copy/recovery were removed. Evidence:
`manual-soundbox-step8-result.json`, `manual-soundbox-step8-track-tags.json` and
`manual-soundbox-step8-historical-tag-copy-result.json` in that pending batch.

Manual step9 passes Track Info display: Sonny confirms the intended Title
`8B@LL x KONSENT - DESIGNER` and Artist `8B@LL x KONSENT`. Its native persistence
is still unfinished. Exact-row copied-store tests confirm changed title/artist
requests are refused because this Content row has `rb_local_synced=1`, while the
accepted metadata profile requires0. Already-equal values are no-ops. The raw
native title still ends in `(FINAL_)` and ArtistID is NULL; do not call visible
values a shared-metadata save pass. Original DB/XML and the copy stayed unchanged;
the copy/recovery were removed. Compact proof:
`manual-soundbox-step9-result.json`, `manual-soundbox-step9-metadata-copy-result.json`.

Manual step10 passed by Sonny's screenshot:155.00 BPM and all A–H times match
the fresh native rows. The reference DAT has552 primary beats. Step11 paused
preview also passes by user report: hold A plays, release stops at A. Complete
before/after native capture is identical, including rows/counter/schema/XML.
Bounded receipts are `manual-soundbox-step10-result.json` and
`manual-soundbox-step11-result.json` in the same pending batch.

Copied cue/grid checks on that exact record also pass: new Memory-cue commit/
recovery, stale-revision refusal, duplicate-position no-op and deleting only the
new cue preserve all eight original Hot rows. Grid shifts+1/-1ms leave SQL/BPM
untouched; double/halve returns155 BPM and all552 primary beats. Every opaque
DAT/EXT section preserves exact bytes/order, including duplicate PCOB blocks.
Original DB/XML/analysis/authority remain exact and the temporary copy/recovery
are removed. `manual-soundbox-selected-cue-grid-copy-completed.json` binds these
component checks. Retained failed harness receipts explain the wrong recovery
API, unique-kind opaque-section assumption and CRLF text comparison; the corrected
checks keep all row/file assertions and final verification repeats no grid edits.

Manual step12 passes by Sonny's November16 confirmation: the playing jump to
filled B at about0:37 continues playback, followed by pause. The full native
capture still matches the pre-step12 tables/counter/schema/XML exactly, with no
active test objects. `manual-soundbox-step12-result.json` and
`manual-checkpoint-20261116.json` bind completion. No cue instruction is pending;
the next metadata work is the older synced title/artist persistence gap. Wi-Fi stays on.
An earlier new-tag
creation instruction was prepared but never issued because its independent live
deletion authority is absent. These checks grant no new Native UI/profile
acceptance and do not close the older synced metadata gap.

November16 generated-only inspection locates the shipped Agent's Content update
handler. Six business-component cases pass Unicode title and separate
TrackInfoUpdated updates on local, synced and historical/nonempty-album records;
each changes only the selected field/local clock/USN and the counter by1, with
all unrelated rows/columns exact. The handler consumes normalized `request.xp`,
which this component supplies directly. Missing-token wrapper control rejects401;
no account/token was provisioned. Request normalization, transport, Main/UI,
artist construction/retirement and source reopen remain unproven. Failed harness
attempts remain recorded, including wrong payload placement; none is acceptance.
The existing node-only sandbox was reused byte-for-byte, with networking and real
profiles blocked. Full original native capture remains exact; nine generated DBs
were retired after compact proof. `content-route-inspection-20261116/component-completed.json`
binds the result. No production profile/source/app change follows.

Sonny separately approved the temporary House assignment/removal check on that
same song. Steps13/14 now pass native persistence and cleanup: exactly one new
local assignment was created then removed, both historical Dubstep/Trap links
stay exact, every non-registry native table/schema/XML matches the pre-test
baseline, and the cycle advances the counter by2. No test assignment remains.
Both actions felt slow; **performance is not accepted**. The add's native row
clock-to-committed-receipt interval was0.783s, excluding admission, the full
library reload and GUI publication; it is not total gesture timing. Installed171
has no detailed phase trace for this action. The log shows a full3296-song reload.
`manual-house-tag-approval.json`, step13/14 result receipts and
`manual-temporary-house-tag-preflight.json` bind scope/identity/baseline. No manual
instruction is pending, and no automated input or authority change occurred.

The source correction keeps the complete fresh native tag/smart/cue/metadata
readback while skipping unrelated media/share resolution. Tag and metadata saves
share `rekordbox_db.load_metadata_readback`; existing unambiguous canonical path
scalars reuse only exact source/native ID/UUID/raw-path matches. Selected aliases
retain their fresh pre-write guard; fresh other fields and musical arrays are
preserved. The app no longer injects the general media-resolving reader into Tag
editing. The new regression reproduces the old default-path call, then passes
with exact fresh smart membership/cues/times and unchanged prior Library. Its
fixture was corrected to model the real saved-signal consumer's reconfiguration;
all data assertions remain. Shared metadata readback regression passes too.
The fresh bare `./check.sh --render` passes all3,414 discovered cases:
3,348offscreen in554.941s and66Cocoa in69.472s, zero skips/expected failures,
exact disjoint phase inventories, all780 frozen inputs unchanged, and required
lint/types/build/vendor/fixture checks. The12-song/zero-device render is inspected
and all3,449 frozen title/artist pairs stay exact. Current evidence is
`tag-readback-gate-20261116/inputs.json` and `completed.json`; this supersedes the
3,413-case gate for those inputs. The later candidate below needs its own complete
gate. Installed171's exact executable stays unchanged;
do not claim an installed latency improvement or broader native acceptance.

November16 older metadata follow-up adds a separately admitted
`ascii-active-analysed-empty-album-v2` candidate. It preserves synced0/1 Content
with no remote USN; the earlier v1 remains local-only. Printable ASCII, analysed105,
empty-album and existing artist-state limits remain. Recovery journals, replay
and compact acknowledgements bind the exact profile; an older authority cannot
reinterpret v2 evidence. No production metadata authority was enabled.

Four new regressions pass paired saves, failed delivery/restart/idempotent replay,
profile-bound recovery and refusal of unproved states. A disposable encrypted
copy of the selected DESIGNER row persists its frozen clean title AND artist,
including a lost artist delivery and fresh-store recovery. Its counter advances5;
all unrelated rows/tables/schema/XML stay exact, and the copy/recovery are removed.
Original native data remains exact. Ten generated shipped-Agent business-component
cases provide additional local/synced evidence; direct normalized requests do not
prove transport, Main/UI or native reopen. Static Main inspection also finds
global unused Artist/Album cleanup in its artist setter. Do not reproduce that
setter on the real library or retire unrelated orphan definitions.

Installed171 lacks the new source-truth record namespaces and metadata recovery
routing in its archived Python code. A live pilot therefore needs a compatible
validated package first; do not write new canonical records/journals and reopen171.
No live metadata pilot was run. Current proof is under
`native-fixture/metadata-protocol-20261116/`. The fresh bare `./check.sh --render`
passes all3,418 cases:3,352offscreen in560.700s and66Cocoa in68.693s, zero skips or
expected failures and exact disjoint inventories. All780 frozen inputs, lint,
layout/links/parity,124engine type checks, native/CDJ builds,12Rust tests,
243pinned vendor files and immutable fixtures pass. The12-song/zero-device render
is inspected; all3,449 frozen title/artist pairs remain exact. Evidence is
`synced-metadata-gate-20261116/inputs.json` and `completed.json`; the earlier
failed-display receipt stays separate. Installation was held until the separate
approval recorded below.

Historical first November16 install (superseded by the current delivery above):
the compatible personal test candidate was built at
`dist/personal/Soundbox.app` and was installed as recorded below, source fingerprint
`211eeacb7e71a276f5cb46e444619770c3c4fd4d92d0ae605b39b4292c8df444`,
executable SHA256
`b45d34a841ef49b193a9d1c40471b1bdd5209f2a656f07118969843163589f76`.
Its build number remains171 because the unchanged Git history is still dirty;
identify it by these new hashes, never by that number alone. Strict signature,
185 first-party Python modules plus entrypoint and25 native TEXT sections match
the tested source/builds. Its explicit isolated check exits normally with native
audio/graphics/process guard and in-memory SQL/ORM active, zero devices, no real
library and no artwork worker. The empty-window screenshot is inspected. Compact
`package-verification.json` and `isolated/runtime.json` are in the current gate.
Sonny then explicitly approved this install/relaunch: “yes please :) you are
good to go!!!”. That current scope supersedes the install hold only; native
title/artist writing stays disabled. Normal quit returned AppleEvent cancellation
while exiting, then the fresh kernel check confirmed SoundBox closed; no forced
termination was used. A complete current physical record-store plus portable
state checkpoint preserves100 imports,1,112 titles,490 artists,24 aliases,
10 custom playlists,524 hidden tracks and16 saved analyses. This includes newer
user edits; never replace it with the older1,109-title checkpoint below.

At that first install the candidate occupied `/Applications/Soundbox.app`, with both hashes
above and exact2,039 signed Contents entries. The reviewed atomic bundle
replacement preserves the existing single169 rollback; no second app rollback
was created. The white Finder/Dock icon and its resource/Finder metadata remain
exact. Native audio/graphics initialize in PID97122 (birth1794854926.38267), loading
3,298 tracks/85 playlists. Startup reuses3,291 analyses and prepares zero tasks;
art fill fetches zero. Current canonical curation and all3,296 cached display
pairs/3,449 complete frozen pairs remain exact. All42 native tables/schema/counter
and playlist XML match the fresh preinstall baseline; authority/art JSON hashes
remain exact and metadata authority is absent. Only new source-bound metadata
intention/provenance settings appear; no source metadata was published.

Current receipts and complete state checkpoint are under
`synced-metadata-gate-20261116/handoff/`: `approval.json`, `preinstall.json`,
`state-before/`, `install-completed.json`, `startup-log.txt` and
`runtime-verification.json`. The initial capture wrapper used unavailable
`os.listxattr`; the already complete snapshot was reverified and the receipt
finished using `/usr/bin/xattr`, without source writes or restoration.
That first install’s manual step16 asked Sonny to confirm the reopened Collection/playlists/window.
Keep169 and packaging output until his visual acceptance, then retire the prior
app/duplicate packaging under the existing bounded retention rule. This installed
test handoff does not complete the older metadata source/reopen pilot, broader
profiles, all other audit gaps or measured end-to-end tag latency.

The latest ordinary-playlist correction captures the exact local removal overlay
before saving/enqueuing a native removal. ACK retires only that captured value
for the same playlist/track UUIDs; newer overlays and legacy jobs without proof
remain intact. Failed local saves restore the prior map and library-mask owners;
restart/retry retain the proof without adding native SQL or full-library scans.
The preceding bare `./check.sh --render` passed all3,411 discovered cases:
3,345offscreen in554.383s and66Cocoa in69.373s, zero skips/expected failures,
780 unchanged frozen non-Markdown inputs, exact phase inventories and all other
required lint/type/build/fixture gates. The12-song/zero-device render was inspected;
all3,449 frozen effective title/artist pairs remain exact. Its evidence is
`ordinary-overlay-gate/inputs.json` and `completed.json`; this superseded the
3,402-case gate below. Its immutable completion receipt remains bound by the
earlier native launch reviews; do not overwrite it with the newer source result.

Sonny explicitly approved one bounded offline original-rekordbox session for
nonce `20261113-8506032c`: only its new tag/folders/playlists/generated silent WAV,
creation/removal, normal quit/reopen and cleanup. This is the sole exception to
the real-app restriction below. No sign-in, credential/profile copy, outside
networking, existing song/tag/playlist edits, source publisher SQL or authority
activation is permitted. The old fixture-clone/account route remains closed.
The private `pending-native-batch/approved-scope.json` and byte-bound plan retain
the actual approval. Containment, bootstrap and exact item ownership must be
proven before input; a passing software canary alone is insufficient.

The bounded batch stopped before test setup. The final attempt launched Original
Main PID73309, whose same-executable children73353/73374 fell outside the admitted
helper roles. The supervisor stopped admission and retained ownership until all
actors exited. Main then crashed during shutdown: the report Sonny supplied
identifies `CFRelease(NULL)` in `RemovableDeviceDetector::Pimpl::~Pimpl()`.
The restrictions may have contributed; the crash alone does not establish the
initialization failure. No Agent was started in that attempt, no UI input was
sent, and no forced termination was used. All observed actors have exited.
`pending-native-batch/main-first-stopped-outcome.json` binds the final evidence:
the complete source/table/schema/content capture and playlist XML remain exact,
and zero test objects exist. Stop this launch approach; do not broaden permissions
or repeat native launches on the strength of software canaries. No native
business-profile acceptance follows. The dated [audit](../shared-library-audit-2026-11.md)
records the earlier bootstrap failures and component proofs.

Read-only cleanup preflight also found46 existing orphan lookup definitions.
Native song removal can globally retire unused Artist/Album/Genre/Label/Key rows,
so Root omitted the generated WAV entirely; it was never imported or assigned.
The reduced proposed moves would have covered empty objects only. This is not
permission to delete those existing definitions or restore a whole old DB.
No extra full DB was retained, installed171 remains unchanged, and no authority
was activated. Current native/UI gaps remain open; the failing contained launch
is a verification blocker, not a SoundBox compatibility result.

The retained supervisor checks TCP+UDP ownership across all interfaces
before/during a session and pins every owner birth/executable. Apple SBPL
`localhost` also admitted this machine's nonloopback addresses at the same port;
the strict-loopback oracle failed, and explicit numeric-host filters did not
compile. Keep those failures; the practical same-machine transport scope and
actual Main/Agent endpoints must be distinguished from a literal-loopback claim.
No outside-machine network probe or global network setting change was performed.
Do not admit arbitrary executables or unconfined replacements. Targeted mouse
delivery did not produce the intended owned-window action. A separate public
Accessibility trial passed button press, focus and text value on an owned AppKit
canary, including wrong-birth/stale-bounds rejection; actual Native JUCE exposure
remains unverified. The prepared read-only Native inspector was never executed.
No global mouse/tap experiment or further native setup is authorized by those
component results.
Current compact evidence is `native-fixture/startup-investigation/` and
`pending-native-batch/`; production inputs and installed171 remain unchanged.

Sonny clarified the scope again: tags, playlists, intelligent playlists, title/
artist updates, grids, cues and existing saved prep must work fully, including
older records and failed-save/restart cases. No new product features or online
functionality are requested. Keep the test route offline; no account sign-in,
credential copy, fabricated activation or real-profile experiment is authorized
by this follow-up. Do not convert unsupported-profile refusals into completion.

November13 efficiency follow-up is now source-validated. Reconciliation and
ordinary saves remain sparse; registries reuse equal prepared owners and global
derivation expands only for a real canonical/trust/policy change or the first
verified source read after process startup. Worker-supplied native metadata
deltas, atomic source-selection/refresh commit, raw scalar/ACK ownership,
continuous publication and existing cleanup-policy controls are implemented.
Manual artist maps remain ACK-bound against later editor/rescue/context changes;
independent native changes retire only the matching older owner. Exact legacy
Track Info title/artist overlays retire in the same ACK save, retaining other
fields/newer edits and fulfilled reset/unmerge bases. Rapid policy-change/reset
uses the newest rule even before the builder finishes. Metadata readback avoids
unrelated media path resolution; fresh DB/catalog/source guards remain.

Native-only Collection removal has a tested dormant DB-only backend/controller,
with no media/analysis/artwork/Trash I/O and no production cutover before lossless
native Restore is proven. Independent remote/local USN validators no longer
reject valid read-only categories or supported historical tag targets. Source
SQL, query count and authority scope stay unchanged. No speculative v3 Tag
renumber profile was added. No online behavior, source authority activation,
live user data write or installed171 update occurred. The
[performance record](../performance-audit.md) records exact component limits.

The preceding bare `./check.sh --render` passed all3,402 discovered cases:
3,336offscreen in496.257s and66Cocoa in69.154s; zero skips/expected failures,
exact inventories,124engine files under mypy, lint/layout/links/parity, native/CDJ
builds,12Rust tests,243pinned vendor files and required offline fixtures pass.
All779 frozen non-Markdown inputs remain exact. The12-song/zero-device render was
visually inspected; all3,449 frozen current/retained title-artist pairs remain
byte-identical. `offline-efficiency-gate/inputs-callback-fixed.json` and
`completed.json` bind this result. Its first run was interrupted for a reviewed
rapid-policy/reset fix. The next ran all3,336offscreen cases but failed two old
mocked refresh boundaries; updated tests exercise the real atomic commit while
retaining retirement/order/one-save/rollback assertions. Independent review and
15focused cases passed before the final complete run. Interrupted/failed receipts
are separate and grant no acceptance. This supersedes the3,325-case source gate
below, which remains dated evidence for its own unchanged inputs.

Remaining native proof is real work: opaque renumber and Content-update Agent
endpoints, synced cue/Hot Cue Bank composition, smart/mixed placement, broader
metadata/Track Info/artwork/analysis profiles and lossless native Restore.
Actual shipped-model and Main static traces are component evidence, not automatic
whole-route acceptance. Compact current receipts include
`tag-cue-grid-analysis-closeout-matrix.json`,
`native-fixture/metadata-refresh-performance.json`,
`native-fixture/node-tag-model-probe/title-setter-composition-static.json` and
`native-fixture/node-schema-probe/playlist-move-static-review.json`.

Concrete source fixes after the3,232-case gate:

- Cue/Grid saves use only their own accepted write authority; their fresh
  companion snapshots are read-only. Missing/corrupt ANLZ cannot veto DB-only
  cues or replace their private grid cache.102 focused cases pass.
- A committed playlist head cannot silently rebase its tail over independent
  native edits after failed local acknowledgement. Exact committed-edge proof
  gates the rebase.163 focused cases and8 retained native windows pass.
- Metadata intentions/dialogs pin source/library/native identity and path;
  first registration cannot retarget an old intention. Single and bulk artist
  merges and accepted artist values retain durable paired intentions, automatic
  title/artist origins, restart/reset/unmerge and verified same-row relocation.
  Existing artist acceptance captures song identities before its dialog, removes
  only the accepted older artist overlay, and retains a single bulk generation
  beyond the ordinary FIFO limit. New trusted artists preserve raw-name derivation
  on affected other rows. Generated title-map copies, marked in origin provenance,
  keep the exact desired native title from being cleaned again after readback;
  explicit newer titles win and failed storage restores the prior owners. Later
  generations do not rewrite admitted requests; missing work and storage failures
  survive. Focused fixtures cover these paths; the complete gate below validates
  their final integration.
- Long alias chains reach a stable canonical name; legacy cycles are bounded
  and deterministic. All3,449 frozen current/retained title-artist pairs remain
  byte-identical (raw SHA256813add1b0214e0b741972a558a8225f014dc82913cedb4a7d1080fb589a8dd1b).
  Small UI/source-selection and canonical-record regressions cover integration.

Independent review closed two additional source bugs: title protection now
requires the exact current identity (unresolved/replaced rows cannot change local
maps), and native title/artist completion verifies the untouched counterpart too.
Conflicts retain the original operation and a durable pair proof; explicit Retry
records consent before atomically acknowledging the original and adding a fresh
existing-field correction. Later edits win, storage failures retain prior owners,
and restored proofs preserve the reverse precondition without native SQL scope
changes. Provisional imports retain their verified registration path.

The first follow-up gate was interrupted for these correctness fixes; its exact
owned group exited and it grants no acceptance. The next attempt ran all3259
selected offscreen cases: three real SQLCipher prep fixtures exceeded their
inherited fake-store polling helper. Measured real worker completion and exact
source readback passed. Only that test-local helper changed to join the two real
workers, deliver genuine callbacks, verify the prepared request on disk and keep
all source/permission assertions; native timing thresholds and production code
are unchanged. Independent review and the focused3-case pass approve the helper.
Its compact proof is `prep-domain-completion-review.json`; the failed attempt is
retained in `offline-followup-gate/failed-prep-gate.json`.

The final bare `./check.sh --render` passes all3325 discovered cases:3259
isolated offscreen cases in537.626s and66Cocoa cases in69.070s, with zero skips
or expected failures and exact inventories. Lint, layout/links/parity,123 engine
files under mypy, native/CDJ builds,12 Rust tests,243 pinned vendor files and
required immutable fixtures pass. All772 frozen non-Markdown source/build/test
inputs remain unchanged. The12-song/zero-device main render and two-column My
Tags/header/Delete control render were inspected without visual tuning.
`offline-followup-gate/inputs-verified.json` and `completed.json` bind this result;
failed/interrupted attempts remain separately labeled and grant no acceptance.
No updated package/install
or native authority activation has occurred. The offline audit receipts retain
real gaps: synced tag deletion without an active synced association batch;
synced following-tag renumbering; independent remote/local USN envelopes;
individual synced cue deletion; smart/mixed reorder and empty/mixed smart-create
placement; broader metadata/Track Info/artwork and analysis/reanalysis profiles.
Changing title-cleanup policy after destructive native cleanup still needs raw-title
provenance and native integration; generated title shields do not complete it.
Acknowledgement-aware refresh must distinguish fulfilled own values from newer
local edits before reconciling later independent native renames; newly imported
rows/ongoing derivation also need continuous publication beyond the one-time plan.
Current title/artist proof still covers only its measured local ASCII/empty-album
window. No manual repair in rekordbox is an accepted substitute for finishing.

The disposable GUI attempt is closed. Both owned actors exited and were reaped:
PID43922 required recorded controlled SIGTERM when the first input bridge could
not service JUCE's manual event loop; PID44469 aborted in CoreAnimation Metal
shader creation. Its exact kernel denial was `com.apple.MTLCompilerService`.
Neither initialized a library DB. Network and actual source/music/profile access
stayed denied; all real curation maps and Track Info were read-only verified
unchanged. The original rekordbox executable remains unchanged. The software-only
APFS fixture clone was verified and retired (1,316,658,088 logical bytes; logical
size is not physical space reclaimed); no spare app generation remains there.
Normal AppKit and manual-loop owned input controls passed, but this grants no
native business-profile acceptance. Do not repeat the GUI/account route.

Current private evidence: `native-fixture/`,
`tag-cue-grid-offline-followup-2026-11-13.json`,
`offline-playlist-compact-replay-2026-11-13.json` and
`preinstall/source-truth-alias-chain-fixedpoint-comparison.json`.
A contained shipped-handler experiment stopped after a V8 cached-code SIGTRAP;
its generated schema DB remained byte-identical. No native acceptance follows.
Preserve compact receipts and the needed current generated inputs, not failed
software copies or a new trail of full-library backups.

## Prior installed app and superseded candidate

Before the November16 authorized handoff above, `/Applications/Soundbox.app`
was personal build171 from the earlier
creation/performance delivery, with source fingerprint
`dbe355e4b6a314ab21cd05b6486e8840a3677c9d78d4b0bf4ef73a1a84db1dff`
and executable SHA256
`18dab88c1c45cbee22119cdd012cddd08435ed2a3de39e13536832b03cfb45ae`.
It passed its dated3,025-case gate and installed curated-state verification.
It did not include the ongoing shared-library audit fixes. The newly installed
test build also reports171 because Git history is unchanged; use its distinct
fingerprint/executable hashes above for current identity.

The now-retired uninstalled deletion candidate `dist/personal/Soundbox.app`, fingerprint
`eae956a1972718ea11a12bcaef5042eac1f22dd8d3278c9a71bdbb7151e23bc3`,
passed3,088cases (3,022offscreen+66Cocoa), zero skips/expected failures,
748 frozen inputs, strict pristine signing,176 module/entrypoint and25 native
TEXT comparisons plus isolated startup. Those inputs are now changed. Its exact
identity was verified before retiring it and `build/personal` intermediates
(340,389,157 logical bytes); compact evidence remains in
`superseded-candidate-retirement.json`. Do not restamp/reuse its validation.

Only one app rollback remains protected pending accepted newer delivery:
`/var/folders/rf/fhvnltd519d1jf6kj6jxj8yh0000gn/T/soundbox-performance-install-bdzhrvgl/Soundbox-before-performance.app`
(build169). Do not create another rollback generation or launch169 against the
record manifest. Preserve the installed custom white Finder/Dock icon.

## Native source and completed scoped actions

Exact source: `/Users/sonnypiccolo/Library/Pioneer/rekordbox/master.db`, library
`629922790`, rekordbox6.8.7.0303, schema SHA256
`c3255e959e179e46cd15e6ae2da50543427c03c8399b11b7a6809515f06e8c08`.
All publishers require independent acceptance and the common writer/root/lock/
journal. No further real-library experiments or automated real-app input are
part of this audit. Private synthetic fixtures/read-only research are allowed.
Do not repeat native reference requests already completed by Sonny.

The accidental ZYKO deletion was repaired under its separate explicit scope and
accepted by Sonny after native reopen. Its original identity and nine active
assignments were restored; six historical links remained exact. SONNY was later
restored by Sonny himself. Current tags must both survive independently:

- `1495448942` **Sonny**, Seq10, twelve active assignments.
- `698726429` **Zyko**, Seq11, nine active assignments.
- PROJECTS/SONNY smart playlist `1557018342` references the restored Sonny tag.

The approved four-test-playlist cleanup completed at
2026-11-13T04:23:20.949563Z, preserving all tag/association tables, music, cues,
analysis, other organization and SoundBox curation. Removed IDs:1420568892,
541807528,280307446,2624677584. The test Acceptance tag was already absent;
no tag SQL was performed in this cleanup. Do not replay either completed tool.
Production deletion authorities remain off: source pilot evidence and an older
creation acceptance do not complete every historical deletion case.

## State, recovery and evidence retention

Canonical `library.json` and referenced `.state-records/` are inseparable. Read
logical data through `read_state`; portable snapshots use `portable_state` under
the shared area lock. Never open live AppData with a mutating repository merely
to inspect it. Preserve every current user edit; never restore a whole old library.
The current checkpoint retains100 imported tracks,1,109 title overrides,490 artist
overrides,24 aliases,10 custom playlists and16 saved analyses. Fresh read-only
inspection also finds524 hidden tracks and one legacy playlist-removal map.
These are migration inputs, not permission to resurrect absent songs.

Before migration, read-only current metadata inputs were frozen for3,298 rows in
`preinstall/source-truth-inputs.json.gz` (325,777bytes; raw SHA256
`85231e166375cb168e4faca03aa96eb8cd1909068cce9d521b60e216f8356ea2`).
Independent production-row comparison verifies the displayed title/artist pairs.
The offline plan has3,121 resolved targets and153 unresolved retained identities;
neither this plan nor default-off metadata authorities have been activated.

Current complete physical/portable checkpoint, compact native references and
proof/install records are in
`~/Library/Logs/Soundbox/tag-smart-deletion-2026-11-12/`.
Key files: `preinstall/`, `zyko-repair-completed.json`,
`zyko-repair-user-acceptance.json`, `user-restored-sonny-preservation.json`,
`deletion-pilot-completed.json`, `package-verification.json`, `gate-final/`.
Completed private `repair_zyko.py` and `apply_deletion_pilot.py` must not replay.
`activate_deletion.py` and `install_verified_candidate.py` were NOT run.

Most large row references are losslessly `rows.json.gz`; compact scripts support
that form. Compression retired51,602,748bytes of duplicate raw text. Routine
diagnostics retain no completed full user-library DB payload. The accepted
DESIGNER payloads are compacted; only the current pending four-record pilot keeps
its bounded before/after proof pair. Bounded generated empty-schema fixtures
are separate offline test inputs, not copies of the user database. The shared recovery root
`~/Library/Application Support/LowFreqLabs/Soundbox/rekordbox_recovery/tag-writes/b496f6c142799c5315d33799`
retains the verified current baseline/latest pair and compact receipts. Keep
unresolved recovery, original conversions/audio, required immutable fixtures and
generation58device recovery. Retire obsolete diagnostic/package duplicates after
verified replacement; do not accumulate atomic/full-library backups.
