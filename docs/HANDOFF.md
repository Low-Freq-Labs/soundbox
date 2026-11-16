# SoundBox current handoff

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
candidate, or label local-only saves complete. See [audit](shared-library-audit-2026-11.md)
and [roadmap](ROADMAP.md). Public splitting/distribution remains deferred;
waveform appearance remains accepted.

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
business-profile acceptance follows. The dated [audit](shared-library-audit-2026-11.md)
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
[performance record](performance-audit.md) records exact component limits.

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
that form. Compression retired51,602,748bytes of duplicate raw text. No full user-library DB
payload is retained in this log archive. Bounded generated empty-schema fixtures
are separate offline test inputs, not copies of the user database. The shared recovery root
`~/Library/Application Support/LowFreqLabs/Soundbox/rekordbox_recovery/tag-writes/b496f6c142799c5315d33799`
retains the verified current baseline/latest pair and compact receipts. Keep
unresolved recovery, original conversions/audio, required immutable fixtures and
generation58device recovery. Retire obsolete diagnostic/package duplicates after
verified replacement; do not accumulate atomic/full-library backups.
