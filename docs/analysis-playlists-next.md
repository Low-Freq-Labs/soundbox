# Analysis, track menu and native playlists

Accepted scope October29, including Sonny's overnight correction: the three-line
menu is immediately **right of Q**, with the same evenly distributed toolbar
gaps. Analysis must ultimately work in either direction: importing/analyzing in
SoundBox first or rekordbox first must preserve the same musical data, independent
of author. Direct native registration and initial shared full BPM/Grid+Key/
waveforms on bare stereo PCM16/24 WAV44.1/48kHz now have separate accepted
production authorities and are installed. Bare unlocked MPEG-1 CBR/no-Info/Xing
stereo44.1kHz MP3 initial full analysis has its own independently accepted domain;
see [the MP3 work record](mp3-shared-analysis.md). Other profiles, partial-component and
existing-analysis replacement/gain/metadata remain gated. The dated native
references below retain their original scope; they are not current missing-work
claims or permission to broaden a writer.
Actual installed provenance and validation are in [HANDOFF](HANDOFF.md).

## Implemented toolbar and USB behavior

The 44×28 hamburger button uses the existing toolbar layout. Menu order:

1. Change waveform color: the existing Blue/RGB/3Band setting, also reflected
   in General settings; no waveform recook.
2. Beat Count Display: Current Position (Bars), Count to next MEMORY CUE (Bars),
   Count to next MEMORY CUE (Beats). Native and Qt use resident grid/cue data.
   Off-grid cues use fractional positions in the adjacent actual beat intervals;
   remaining partial beats round up. Bars use four beats plus remainder. At a cue,
   the next strictly later Memory Cue becomes the target. Outside the supported
   grid, or without a next cue, the countdown is blank. No new timer, polling,
   waveform preparation or transport command is introduced.
3. Export to USB: the loaded native song enters the selected USB collection with
   no synthetic visible playlist. The action freezes mount token/UUID through
   discovery/source-save deferrals. It uses the dedicated CDJ transaction,
   preserves current songs/playlists and is a true no-op when unchanged.
   Its choices use cached presentation for compatible attached FAT32/exFAT USBs;
   each action requires a loaded native song, writable identity and an available
   lane for that device. Device pages/export caches initialize on demand; the
   worker retains fresh target/source/recovery checks.
   Initial and incremental exports and interrupted standalone intent resumption
   are exercised with the compiled renderer on disposable targets.
4. Analyze Track.

## Implemented detection stage

Analysis is immediately right of General in Settings. Its controls span the
page in three aligned, inset groups; Memory Cue and automatic imports sit below.
The section header and note retain the outer edge. Menus/dropdowns use right-hand
checks and inset arrows, including Analysis Settings and My Settings. BPM/Grid and Key run the
pinned rbxport adaptive/high-precision tempo/key pipeline in the optimized local helper. Phrase
is a disabled placeholder; no Normal/Dynamic mode or cloud controls. Automatic
analysis of newly imported SoundBox files is opt-in and initially off. The
current manual/automatic paths run silently in the background without an analysis
window or progress bar. Required conversion precedes waveform preparation;
BPM is saved next, then key, with full-SHA component checkpoints and restart
resumption. New native DB saves follow local preparation; they never publish an
intermediate BPM paired with an older key. This is local staging, not permission
for partial native-component publication.
Folder imports enter Collection before header/bookmark probing. Captured imports
admit BPM/grid and Hot/Memory Cue drafts after detection, including already-added
songs whose unchanged audio acquired a newer cloud metadata timestamp. The
first-analyzed-beat Memory Cue option is initially unchecked. Performance,
Normal and Power saving select child-process scheduling priority, preserving the
same DSP and quality. BPM ranges are 70–180 default, 48–95, 58–115, 68–135,
78–155, 88–175, 98–195, 108–215, 118–235 and 128–255.

Each local stage captures a source snapshot while hashing, pinned to the waveform's
full audio SHA after the initial waveform capture. The helper retains up to thirty
minutes /96million mono samples, checks rate/channel budgets before allocation,
and bounds every append. It streams the remaining tail through EOF, checking
finite samples, stable format and declared duration even after the head fills.
Tracks within those bounds are analyzed completely; beyond the head the last measured
tempo extends through the checked duration. The saved analysis records describe
their measured span rather than claiming unmeasured transitions. Source publication
must still separately prove endpoint and sidecar consistency. Silence/low-confidence
tempo does not become an invented grid; unchecked or inconclusive components retain
previous results for the same bytes. Legacy constant-v1 records remain valid, with
component provenance retained on partial reanalysis. Cancellation terminates/joins
the child; TaskOwner retains it through finished delivery/join. Failure preserves
previous results. Source audio is never retagged, and detection does not invalidate
waveforms or reload audio. The [upstream audit](rbxport-reference-audit-2026-10.md)
records exact options, limits and comparison evidence.

Results persist inside backed-up `library.json` as `settings.analysis_results`.
Publication enforces the same64MiB ceiling as the reader before replacing any
canonical bytes; an oversized result preserves the preceding library. Each result
binds its canonical path, full audio SHA-256, stable filesystem identity and engine
provenance. A later rekordbox track ID at the same path can find the existing
SoundBox result. A key-only/grid-only run preserves the other result for the same
bytes. Playback validates the exact recorded filesystem identity in its existing
worker. A native/manual grid wins over detected fallback; native cues remain
source-authoritative. Detected values fill otherwise-empty browser BPM/key.
For direct imports without existing cues, the optional first-beat cue is captured
as a durable cue intention. Initial supported full WAV analysis publishes through
its own authority; cue drafts use the separate accepted Cue API. Native CDJ export
captures verified native source analysis. A detector result never grants the
manual-grid writer new powers or authorizes replacing existing native analysis.

## November2 import and prep correction

Initial full supported WAV publication compares every packet beat's number,
timestamp and tempo to the complete saved grid, including its exact endpoint.
The headline BPM need not equal every adaptive beat's tempo. The redundant
constant-tempo assumption rejected Sonny's valid155→150BPM import; the installed
correction preserves the exact saved rows and all existing source/authority/SQL
limits. Actual readback verifies449beats and the saved key for that import.

Cue/grid intentions remain immediately usable while the native save is pending
or another song's analysis fails. Ctime-only cloud drift stores the exact gesture
against original bytes before a SHA-pinned worker check renews identity; actual
byte changes retain the intention and fail publication. Source/library/path checks
also protect late callbacks and presentation. Analysis errors retain their own
operation IDs while healthy imports continue. UI close/Cmd+Q stays open until
captured imports/detection/source saves finish, with editing and source pumps
active; ordinary read-only preparation does not block Quit. Full release and
actual installed evidence are recorded in [HANDOFF](HANDOFF.md).

## Shared-analysis acceptance boundaries

The initial full supported WAV and bare-registration gates described below have
passed via independent production domains. Preserve these original native
references; broader codecs, existing-analysis replacement and partial-component
publication still require their own evidence. Do not repeat generated imports
into Sonny's real library as a routine acceptance test.

The accepted manual-grid API does not cover registering a new detected analysis,
KeyID interning, initial analysis-file/contentFile registration or a direct-import
track's first native identity. Do not route detection through that API or adopt
upstream `Analysed=105`/counter assumptions without native6.8.7 evidence.
Upstream's writer deliberately omits contentFile/contentCue protocols; the accepted
SoundBox manual-BPM writer already proves that those details matter here.

The October29 reference plan called for separate frozen before/after examples for
an unanalysed imported
reference, BPM/Grid+Key analysis, Key-only analysis, and first-beat Memory Cue.
Those captures bound the existing/new sidecar set, key table/content row,
counters, contentFile,
contentCue, XML and all unrelated table inventories. Copy-only candidates, recovery
proofs and separately accepted ordinary registration/WAV/CBR domains followed;
the captured references never directly provisioned them. The original round-trip
acceptance matrix called for
SoundBox-first import → native registration → reload and rekordbox-first import
→ SoundBox reuse, plus later manual edits from either app. Preserve existing manual
grids, cues, Analysis Lock and opaque metadata. No perpetual author-ownership fork.

The unanalysed import reference is captured and inventory-verified. Track102012532
has BPM0, KeyID0, Analysed0, empty AnalysisDataPath and no contentFile/contentCue
registrations or predicted UUID analysis directory. Only the new djmdContent row
and agentRegistry changed; counter352236→352237, all existing content unchanged.
The source WAV still matches its generated full SHA-256.

October30 bounded follow-up closes the earlier unrelated-registry comparison gap:
Sonny opened rekordbox, then imported a separate copy of the generated32-second
WAV with analysis off. The startup-complete snapshot and immediate after-import
snapshot differ by exactly the independently constructed content row plus the +1
counter (352390→352391). `research/native_library` reproduces the complete native
semantic inventory and byte-identical XML without ignoring globals. This remains
a copy-only/native-reference proof, not source-writer acceptance or app integration.
HANDOFF records the exact track/evidence and subsequent initial-analysis check.

The BPM/Grid+Key reference is also captured, including all three registered
DAT/EXT/2EX files. Native analysis matches SoundBox's120 BPM,8B and249ms first
beat, with64 beats ending31749ms and no cues. Only agentRegistry,djmdContent,
contentFile and djmdMixerParam changed; all other tracks, XML and source audio
are unchanged. The counter advanced352237→352257 over this session; do not
generalize that to a writer formula. Native sets Analysed105,AnalysisUpdated1,
TrackInfoUpdated2 and Length31 for the exact32-second fixture. Initial analysis
also creates a gain/peak row and three contentFile rows. Those protocols and the
duration discrepancy require proof before a reverse publisher can be accepted.

Key-only recheck is now captured and confirms Sonny's explanation: it preserves
the existing BPM/grid/waveform analysis. DAT/EXT/2EX remain byte-identical;
contentFile rows, grid, cues, KeyID and AnalysisUpdated are unchanged. Only target
TrackInfoUpdated2→3, rb_local_usn and updated_at change, plus agentRegistry.
The session counter advances352257→352265; source audio, XML, other tracks and
all other tables remain unchanged. This is recheck evidence, not a fresh
Key-only registration protocol.

First-beat option reanalysis is now captured: native creates one DB-only Memory
Cue at249ms with its matching contentCue JSON aggregate. DAT/EXT/2EX and their
contentFile rows remain unchanged. Only agentRegistry,contentCue,djmdContent and
djmdCue change; other tracks, XML and audio stay unchanged. AnalysisUpdated1→2,
CueUpdatedNULL→1,TrackInfoUpdated3→5; counter352265→352283 is the session delta,
not an isolated cue-write formula. Existing accepted cue rules remain scoped;
this reference does not authorize the detected-analysis publisher.

Fresh Key-only import+analysis is captured together as native-key-only-new-track.
Sonny explicitly confirms he selected only Key. New track109556371 nevertheless
has120 BPM,8B,Analysed105,64 grid beats and all three DAT/EXT/2EX files. Every
sidecar section matches the full-analysis reference except PPTH, which stores
the new filename. Initial registration includes three contentFile rows and one
gain/peak row, without cues. Only agentRegistry,contentFile,djmdContent and
djmdMixerParam change; existing tracks, XML and audio remain unchanged. Session
counter352283→352304 includes both import and analysis; no intermediate capture
isolates those operations. Preserve the distinction between initial analysis
and a Key-only recheck of existing analysis when implementing selection semantics.
The third WAV is an exact verified copy of the generated reference; SoundBox-first
remained unimported at this historical checkpoint. Subsequent independent
registration/initial-WAV-analysis proofs and authorities completed those supported
production paths. This reference supplies no broader publication authority.

## Ordinary source playlists — accepted October30

Regular local playlist/folder create, rename and delete plus registered-track
append/remove passed actual-source acceptance on the exact current library,
schema andrekordbox6.8.7.0303. Eleven frozen native DB/XML/rollback transitions
match, followed by12 guarded source CRUD operations. Sonny verified native
names/order,120BPM/8B and the existing Memory Cue, then quit normally. Native
opening and final save retained the exact playlist/counter/XML result; content,
cue and analysis tables were unchanged. A separate explicit production authority
was provisioned afterward. The installed build/status remains in HANDOFF.

`soundbox.rekordbox_playlist_plan`, `_xml` and `_store` now own ordinary scoped
row/counter planning, lexical XML and encrypted paired recovery. Only playlist/
membership rows and localUpdateCount may change. Native and agent must be closed;
unknown cloud/sync states, stale identities and foreign recovery fail closed.
Tag/Cue/Grid/Playlist share one worker, lock and journal. Acceptance archives
retain full pairs; normal editing retains the latest full encrypted pair plus
permanent row/XML/operation receipts, retiring only verified older completed DB
payloads with resumable progress.

`desktop.rekordbox_playlists` persists intents before admission, waits while
rekordbox is open and updates membership after verified source save. Retry
renews an explicitly retried stale/rolled-back operation's ID/revision/clock;
committed receipts replay unchanged. Discard never reverses a saved source edit.
Normal UI Quit drains captured work; explicit teardown preserves any unstarted
durable intents for restart. CDJ capture
waits for pending playlist edits and rebinds selected native IDs afterward;
deleted selections or changed libraries stop before export.

Native unsynced rows delete physically. Older locally synced rows retain native
tombstones: playlist/folder group first, members next when present, sibling-order
group last even when there are no following siblings. Native empty deletion
advances2 groups; nonempty advances3. Deletion retains all XML, including a missing
legacy node. Single-song removal updates every active survivor in one additional
group even when TrackNo is unchanged. Bulk selection composes proven single-entry
operations rather than assuming an unobserved bulk counter formula. First legacy
ordinary edits can add a missing XML node at zero; later edits touch its clock.

The original playlist milestone used cache9; current `libcache` schema11 retains
raw catalogs/clocks and empty folders. Collection now reads all active supported
native audio independently of membership. Exact UUID pins remain compatible
retention metadata after a final playlist reference disappears;
cache data and pins grant no registration/edit authority. Only the user-requested
October30 local veto3604899226/109556371 was cleared after its verified native
removal; unrelated older local vetoes remain. The acceptance's own temporary
folder was removed through the accepted writer afterward, preserving Collection,
cues/analysis and XML. Intelligent/cloud mutations remain separate. New-file
registration and initial supported WAV analysis later passed independent acceptance
and are installed; broader analysis replacement/metadata remain gated.

November3 recovery hardening captures folder-delete descendant ContentIDs/UUIDs
on the common worker and durably saves retention before writer admission. Failed
local acknowledgement/restart uses captured intentions or an exact retained
historical receipt pair with fresh current UUID checks. Historical pin recovery
may coexist with later native playlist edits; it never restores an old catalog.
Causal rebasing of later pending app edits instead requires an independently
reconstructed committed edge matching the current catalog/XML. Retired legacy
evidence without the original UUIDs fails closed. These are correctness proofs
inside the existing accepted writer, not additional source permissions.

## Historical prototype and first exact-plan pilot

The following records describe the earlier isolated proof, before the broader
October30 acceptance above. Their original exact approvals remain bounded to
those operations; they are never a general permission loader.

`research/cdj_export/playlist_reference.py` compares explicit frozen encrypted
captures against their complete semantic inventories; it has no live defaults.
`playlist_membership.py` is a pure provisional unsynced append/remove planner,
with no SQL/publication API. Its native-reference replay matched the exact add
rows, physical membership removal, survivor UUID/created_at preservation,
TrackNo2→1, and both counter outcomes. An encrypted temporary-copy replay changed
only djmdSongPlaylist and the local counter, retaining schema/metadata and all
other rows. Synthetic tests reject unknown sync and nonordinary playlists.
This is useful proof, not native acceptance or complete playlist CRUD.

The earlier combined create/name sample advanced the counter twice. The new
isolated native captures prove those are two operations: root creation makes
1672996042/62c5eea3-f32f-43ba-9cbc-ab818d7fd3b9 as Untitled Playlist with
Seq8, equal creation/update timestamps and counter352307→352308. Renaming it to
SoundBox Bridge Test preserves identity/creation/order and changes only Name,
updated_at and rb_local_usn, with counter352308→352309. Native XML creates
Id63B7E4CA at Timestamp0, then advances Timestamp on rename.

Adding reference tracks102012532 and109556371 appends native memberships1/2,
counter352309→352311. Removing the first physically deletes its membership,
retains the survivor's UUID/created_at, renumbers2→1 and advances the counter twice
to352313. Other database tables, schema and library identity remain unchanged.

`playlist_lifecycle.py` models root creation, separate ordinary unsynced renaming
and the observed last-root single-member deletion as pure plans.
`playlist_replay.py` replays creation, rename, append and removal only on
disposable encrypted copies. All four operations match complete
native database semantics, restore the complete baseline on SQL rollback, and
match again after commit/reopen. Frozen inputs and live sources are never modified.
This earlier DB-only replay does not establish paired publication or source
authority. The paired copy proof and exact accepted source pilot are recorded below;
synced/cloud cases remain unproven. Native XML timestamps
must be captured as independent values, not inferred from SQL updated_at.

Sonny confirms normal Auto Analysis and BPM/Grid+Key settings restored. He deleted
SoundBox Bridge Test and quit rekordbox normally. The isolated deletion removes
playlist1672996042 and its remaining membership physically, counter352313→352315.
Both reference tracks remain active; the entire djmdContent table is unchanged.
The XML is byte-identical before deletion, immediately after it and after normal
quit: its63B7E4CA node survives with its previous Timestamp1793325835677. The old
combined capture's timestamp advance must not be attributed to deletion itself.

This historical deletion replay was deliberately narrower than general CRUD:
only the last active root playlist with exactly one unsynced member. Empty/
multiple-member deletion, folders and sibling renumbering were unproven there;
the separate ordinary API acceptance above subsequently covered them. Its reference also contains one
independent `notificationNextFetch.updated_at` change. Default strict full-state
replay rejects that difference. The explicit `--audit-notification-timestamp`
mode verifies all other database state exactly, preserves the original registry
metadata in the candidate, reports that single residual difference and keeps
`complete_semantic_match=false`. SQL rollback and commit/reopen pass. It never
normalizes frozen inputs or writes unrelated registry rows. This is an audited
partial match, not full native acceptance or source-publication authority.
The closed after-capture adds no DB or XML changes. No further native UI action
is pending for this reference pass. Future creation must also reject identity
collisions with retained XML entries, even when their SQL row has been deleted.

Earlier combined-session deletion evidence (superseded for isolated counter/XML
behavior): playlist4086406462 and its last membership were physically removed. Both test songs
remain active in Collection, with the entire djmdContent table unchanged. The
session counter advanced352231→352236; this is an observed session delta, not
yet a proven isolated deletion formula. The XML node F3919D3E survives with an
advanced Timestamp. A future publisher must prove that protocol rather than
assuming an XML-node deletion. No source-write authority follows from this capture.

Frozen playlist sequence under the evidence root below:

| Capture | Local counter |
|---|---:|
| native-playlist-create-before | 352307 |
| native-playlist-created-default | 352308 |
| native-playlist-renamed | 352309 |
| native-playlist-two-added | 352311 |
| native-playlist-one-removed | 352313 |
| native-playlist-deleted-isolated | 352315 |
| native-playlist-deleted-closed | 352315 |

Explicit offline replay, with capture directories substituted for BEFORE/AFTER:

```sh
PYTHONPATH=. .venv/bin/python -m research.cdj_export.playlist_replay BEFORE AFTER create 1672996042
```

Operations are create, rename, append, remove and delete-last. Only the captured
delete-last comparison needs the explicit notification-timestamp audit flag;
its strict default rejection is retained as evidence. Full replay reports,
frozen-input hashes and the1626-test gate are retained in
`~/Library/Logs/Soundbox/playlist-lifecycle-proof-2026-10-29/`.

## Recoverable paired copy publication and exact source pilot

`playlist_xml.py` patches only the planned local NODEs and preserves every other
byte. It reproduces all five native XML captures exactly, including retained
64-bit Lib_Type1 entries. New local IDs cannot collide with retained XML history.

`playlist_transaction.py.OfflinePlaylistWorkspace` remains original-private-TEMP
only, with no source publication route. It stages verified encrypted before/after
DBs and exact XML, journals before canonical writes, publishes XML durably inside
the SQL transaction and uses the SQL commit as the decision point. Exact before
DB plus after XML rolls XML back; exact after DB plus after XML completes. Unknown
DB/XML or an impossible after-DB/before-XML prefix preserves all evidence. The
whole pair and payloads are verified before any recovery mutation. WAL headers
are consolidated only on new disposable backup copies; source files are untouched.
Real subprocess exits cover preparation, journal, SQL, XML, commit, receipt and
retirement, including WAL and genuinely spilled rollback journals.

The replay command now accepts --paired. All five frozen cases pass paired
copy publication with byte-identical XML. The deletion reference retains its
explicit notification-only audit residual; transaction recovery itself has
no metadata exception. No record may normalize a source registry clock.

scripts/prove_rekordbox_playlist_pilot.py has prepare/show/publish/recover commands,
all with explicit paths. Preparation only snapshots a closed source and builds a
private encrypted plan. Publication requires a separate approval file bound to
its plan SHA, source and operation ID; nothing creates that approval automatically.
The unintegrated playlist_pilot.py uses the existing shared source root/lock/
journal and guarded SQL connection lease. It regenerates exactly create/name/two
existing unsynced additions, authorizes only the two playlist tables and counter
int_1, and denies trigger/DDL/other-table mutations. Reads against the live writer
are identity/target reads; complete inventories run on verified private snapshots.
Both SoundBox and rekordbox/agent must be closed. All unrelated source state and
full XML bytes remain bound; there is no source notification-timestamp exception.
Before/after encrypted DB/XML backups remain archived after completion. Unknown
state, changed approval/payloads, competing domains and redirects fail closed.

The exact source pilot was explicitly approved, published and natively accepted
October30. Private plan:
`~/Library/Logs/Soundbox/playlist-source-pilot-2026-10-29/plan/`.
It created SoundBox Bridge Pilot3604899226, then added102012532 and109556371 in that
order; native counter352315→352319. Source629922790/schema/version bind exactly.
Plan digest: ab2331c468bfa152726df43e699f4fe72afb75b6f01014a3febe42dde90033c9.
The exact approval.json and operation5ec931cd8d814c8baaf636bf68cdbc07 receipt remain
private. Both apps were closed for guarded publication; all four encrypted DB/XML
backup hashes were verified, the committed receipt archived and journal retired.
Sonny verified the playlist/order, both120BPM/8B and the first track's existing
Memory Cue. Normal rekordbox quit preserved playlist/membership rows and exact
published XML bytes. Content/cue/analysis inventories remained unchanged.
Native startup changed agentRegistry authentication/notification bookkeeping;
readable reports retain only credential-redacted deltas. Encrypted snapshots
remain intact. Do not normalize native registry changes to manufacture equality.
Native-open/native-closed comparisons and acceptance.json are under sibling
`native-acceptance/`. SoundBox's curation/art/authorities stayed exact; the two
newly playlist-scoped references now enter its read-only browser (3742 tracks,
76 playlists, Collection3230). This is scoped native acceptance, not production
playlist, registration or analysis authority.

The separate ordinary API acceptance above later completed general CRUD, other
deletion shapes and sibling order. Direct registration and initial supported WAV
analysis subsequently passed their independent production acceptance; the old
exact-plan pilot grants no operations in those domains.
Never reuse this approval for a different operation or refresh a stale plan.

## Private evidence

Original captures and22 UI references:
`~/Library/Logs/Soundbox/playlist-analysis-ui-2026-10-29/`.
The prior unfinished-prototypes/resume.patch is historical untested work; current
source selectively incorporates and corrects it. Never overwrite newer files
with those full snapshots. Overnight replay reports, generated analysis reference
inputs, UI renders, release/install records and state baselines live in
`~/Library/Logs/Soundbox/overnight-analysis-2026-10-29/`.
