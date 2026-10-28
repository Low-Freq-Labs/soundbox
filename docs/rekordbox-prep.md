# Rekordbox prep editing — approved plan and milestone gates

Approved October 26, 2026. This document separates the agreed product behavior
from implemented My Tag/cue work and remaining prep work. Sonny asked to
proceed slowly and review each milestone before continuing. Native tag and DB-only
cue add/delete acceptance passed October 26. Grid source acceptance passed October 27;
current build/install and user review status live in HANDOFF. Source intelligent-list
creation remains a subsequent milestone.

## Agreed first version

- A strip beneath the waveform: set the playhead as a bar's first beat, shift
  the whole grid left/right, double/halve BPM, and **My Tags** left of
  Settings/Search, without a separator. The BPM Value field follows the first-beat
  button. Originally display-only, it now accepts manual constant BPM after
  separate native acceptance (milestone 4). Deck previews update immediately;
  browser BPM confirms each verified background save.
  Zoom-scaled nudges: 50/25/10/5/3/2/1 ms from widest to closest; Shift triples
  the current step, retaining 1/3 ms at 1152–1500 points/s. Preserve variable-tempo structure,
  audio timing and saved cue positions; respect analysis locks.
- Numbers 1–8 use the active Hot/Memory page: paused populated cues retain
  held-preview/Space-latch behavior; playing hot cues jump and keep playing; empty slots create at the exact playhead
  by default, or the nearest beat with the subsequently requested Q toggle on.
  Creation does not start preview. Command-1–8 deletes the
  corresponding cue. Hot letters stay fixed; Memory cues sort chronologically.
  Capture identity at keypress, preserve overflow/loops/colors/labels, and keep
  text fields, page switches and releases safe. No loop authoring in v1.
- Flat existing tags, keeping their source IDs/order and underlying category
  structure. The dropdown acts on selected browser rows, independent of playback;
  mixed state -> checked assigns to all, unchecked removes from all. A selection
  containing non-rekordbox tracks is disabled rather than partially edited.
- New smart playlist: name, source folder, one existing My Tag. The simple
  "My Tag is X" UI stores rekordbox's tag-membership/contains condition. Create a
  real source intelligent playlist, preserve existing rules and ordering, and
  recompute membership after verified tag saves. No other condition types yet.
- Unchecked setting **Include rekordbox tracks that aren't in a playlist**.
  Off retains the current playlist-based scope and Sonny's ALL LOCAL workflow;
  on includes the broader active Collection. Preserve hides. Default-off excluded
  tracks do not enter background preparation. No separate Unfiled view.
- Automatic saves; no ordinary Save buttons, pending-edit chores, or Undo/Redo
  in v1. Group rapid nudges after 250 ms inactivity with a one-second maximum.
  Capture cue positions/targets immediately. Verify before declaring success.
  Definite failure restores the last verified presentation; uncertain completion
  blocks further edits until recovery resolves it. Never overwrite a conflict.
- Only grids, cues, tag assignments and new single-tag smart playlists write
  back. Existing title/artist overrides and ordinary SoundBox playlists retain
  current semantics. Tag-definition management and broad playlist editing wait.
- All live writes require rekordbox and its background agent closed. Sonny
  does not use rekordbox Cloud/Mobile Library Sync. Native checks use the actual
  library with explicit targets/backups, not a second macOS account/library.

## Milestone 1 — completed copy-only foundation

`soundbox.rekordbox_write.OfflineWorkspace` is Qt-independent and unintegrated.
It has no default source, app caller, live-write flag, or publish-to-source API.
Even its internal SQL connection helper rejects paths outside the OS temporary
directory. Opening a marked workspace requires its original private temporary
root; symlinks, hardlinks, relocated roots, sidecar redirects and source aliases
fail closed.

Capture copies an explicit source DB and matching WAL through the existing
stable snapshot reader, without opening the original with SQLCipher. It creates
encrypted canonical baseline/working copies, verifies identity/integrity, and
removes the extra raw capture. Failed initial capture cleans up its own files.
Referenced audio, analysis paths, companion XML, AppData and devices are never
opened by the proof.

Typed tag-assignment plans bind a workspace ID, whole logical baseline, stable
track/tag IDs and expected memberships. They reject missing/deleted endpoints,
category assignment, duplicate active associations, unexpected ordering and
unverified schema/version. The initial version check accepts the observed
`6000` DBVersion and exact tag-link columns; this is an **offline structural
check**, not a production compatibility allowlist for every rekordbox release.

The write protocol is:

1. Hold one exclusive workspace writer lock. Refuse unresolved recovery.
2. Back up the exact preceding state to an encrypted DB. Stage the entire proposed
   operation on another encrypted copy. Verify integrity, unchanged schema,
   every unrelated table, and exact expected rows/fields in the two allowed
   tables (`djmdSongMyTag`, `agentRegistry`). Trigger side effects fail closed.
3. Fsync the before/after recovery journal. Acquire SQLite's write transaction,
   recheck the full expected baseline, and apply the same verified row delta.
4. Check the complete expected result inside the transaction, commit with FULL
   synchronous durability, close, and independently read back before success.
5. Retire the recovery directory atomically before bounded cleanup. Retain the
   original encrypted workspace baseline and a small last-result receipt.

Removed assignments become tombstones; a later addition creates a distinct
assignment identity instead of reviving historical rows. Existing links are
never renumbered. Update counters advance only for actual changed assignments.
These offline metadata conventions remain **provisional** and differ from the
native-derived production rules below. Successful SQLite reads alone do not prove
rekordbox acceptance. Do not reuse the offline mutation algorithm on a live DB.

Recovery after a real process exit handles both SQLite WAL commits and hot
rollback journals. SQLite may need a writable connection to recover spilled
pages, which is permitted only on the guarded temporary working copy. The
journal's encrypted before/after DBs must still verify. Exact before means
rolled back; exact after means committed. Any other state or damaged evidence
is preserved and blocks further writes. There is no blind database restore.

Full-database staging/inventory is deliberately conservative for this offline
proof. It is not the production save algorithm. At this milestone, companion-file
transactions and cue/grid writing were separate work; this DB-only proof cannot
certify them. Their later source acceptance is documented below. XML stays read-only.

## Validation and private evidence

`tests/test_rekordbox_write.py` creates encrypted synthetic databases. Routine
tests never discover live libraries, audio, devices or AppData. Subprocess exits
exercise pre-journal, pre-commit, post-commit, verification, WAL and spilled-page
recovery. Other cases cover exact deltas, no-op byte preservation, history,
mixed selections, stale/cross-workspace plans, schema/identity ambiguity,
side effects, backup/fsync failures, competing writers and path redirects.

An explicitly invoked developer proof may read/hash only the supplied source DB
and WAL, exercise both membership directions and a no-op on a temporary copy,
and write private JSON under ignored `build/rekordbox-write-proof/`. All IDs and
the source/report paths are explicit:

```text
.venv/bin/python scripts/prove_rekordbox_tag_write.py \
  --source /explicit/rekordbox/master.db \
  --track-id CONTENT_ID --tag-id TAG_ID \
  --report build/rekordbox-write-proof/milestone-1.json
```

It has no live-write option. This command is separate from the routine release
gate and does not imply a source audio/ANLZ/XML audit. The private evidence
records library identity, candidate IDs, source DB/WAL hashes, exact changed
tables, encryption/baseline checks and repeated-operation results. The fresh
temporary databases are removed after the proof, leaving only the small report.
Actual final validation is recorded in [HANDOFF](HANDOFF.md).

## Milestone 2 — My Tags and native acceptance

The strip under the waveform matches the surrounding player panel (`#1b1d24`).
It shows the five grid controls at the left, enabled only for a ready accepted track, with a
**BPM Value** field directly after the first-beat button. Manual entry is described
in milestone 4 below. The field
shows `000.00` without a loaded track, otherwise its saved BPM with two decimals;
browser selection alone does not change it. Source refresh updates the loaded
track’s saved value. The right
grid-shift icon mirrors the left exactly. The **My Tags** button now sits left
of Settings/Search in the prep strip, without a separator; Search aligns beneath
the Hot Cue / Mem Cue section. The flat checklist uses existing
active definitions. Categories are retained internally and shown only to
resolve duplicate names. It acts on selected browser rows, supports mixed
assignments and preserves surviving selected rows after refresh. Imported or
unverified tracks disable editing. The popup matches the button width and uses
two columns for the current tags, reading down the left before down the right.
Longer labels fall back to one column, with truncation tooltips when needed.
Native rows have explicit geometry so labels/checkboxes never overlap; availability
messages wrap within the same width. A pending save shows its intended assignment
and blocks another action until verified. Opening the menu probes availability
off the GUI thread; a running rekordbox/agent produces a close-first explanation.

`rekordbox_tags.LiveTagStore` owns My Tag writes. It requires an explicitly
provisioned authority bound to canonical local source path, library ID, complete
schema/header digest and native version **6.8.7.0303**. Source selection and cache
loading never grant that authority. Only Sonny's accepted library is enabled;
other versions/libraries and unverified sync metadata fail closed. Cloud/Mobile
Library Sync is outside this acceptance.

The transaction checks current track/tag UUIDs, tag name, active state and
expected membership; holds SQLite's write transaction; rechecks process/file
identity; restricts writes to tag links and the local counter with an authorizer;
and reads back independently after commit. It maintains a verified encrypted
baseline, refreshed after external counter changes or on a new store session.
A durable journal binds that baseline to exact before/after rows and request
identities. Recovery recognizes only those exact outcomes and preserves conflicts;
it never automatically restores an older whole database. Saves, availability
probes, refresh, source changes, maintenance and shutdown serialize through the
desktop worker owner through finished delivery and join. No full-library SQL
inventory or staging runs on every checkbox action. A successful save reloads
smart memberships from a fresh source snapshot.

Native references established two distinct removal rules:

- A new unsynced assignment with NULL `usn` is hard-deleted.
- An older synced assignment is tombstoned (`rb_data_status=2`, deleted=1),
  preserving its sync flag, identity, creation timestamp and unrelated metadata.
- Adds receive independent new ID/UUID strings, NULL TrackNo/usn, zero local
  flags and matching millisecond UTC creation/update timestamps.
- Each changed assignment advances `localUpdateCount.int_1`; the counter row's
  timestamp stays unchanged. No-op requests change no counter or assignment.

Sonny performed all requested reference/acceptance UI actions in rekordbox.
References included new add/remove on **SAGZ x Jeffufu - No Trust** and removal
of an older Dubstep assignment on **Nuclear Bass Face**. Native changes affected
only `djmdSongMyTag` and `agentRegistry`. SoundBox then removed No Trust's tag;
Sonny reopened rekordbox and confirmed it unchecked. SoundBox restored it;
Sonny reopened and confirmed both the checkbox and **GENRES → DUBSTEP** membership,
then quit normally. Both control tracks finish with Dubstep assigned, as requested.
Full semantic comparisons preserved all unrelated tables/schema/header metadata;
control audio, ANLZ and protected companion XML hashes stayed unchanged.

Private references, encrypted recovery and exact identities remain in AppData
`rekordbox_recovery/native-tag-pilot-20261026/`; `native-acceptance.json` records
the checks. The general exact-library authority was provisioned only after those
checks. Routine tests use synthetic encrypted libraries, including real process
exit/recovery, stale identities, process/version guards, unauthorized side effects,
backup damage, selection changes and maintenance/shutdown ownership. No automated
release test operates on the real library or devices.

Rekordbox's published developer XML interface does not expose My Tag assignments.
pyrekordbox supplies useful models, decoding and SQLCipher access; SoundBox adds
this narrow native-derived transaction/recovery protocol. This is an independent
integration, not an official rekordbox write service.

## Remaining milestones and review

### Milestone 3 — cue editing and native acceptance

Native reference captures now cover WAV (No Trust), MP3 (Juice), AIFF (Back
Then), and an older synchronized aggregate containing existing cues (Jungle
Juice). Sonny operated every native UI action. The reference phase restored the
first three controls to cue-empty and Jungle Juice to its original A/B/D rows.
Control audio and DAT/EXT/2EX remained byte-identical. Encrypted baselines and
individual captures remain private under AppData `rekordbox_recovery/`.

Native additions/deletions change four DB tables: `djmdCue`, `contentCue`,
`djmdContent`, `agentRegistry`. New cues have unsigned decimal 32-bit IDs,
independent UUIDs, exact integer milliseconds, frame=floor(milliseconds×150/1000),
zero MPEG fields, NULL seek/microsecond fields, Color=-1 and OutMsec=-1. Kind 4
is reserved; D is 5 and H is 9. Each edit advances the track's string CueUpdated
counter and advances the global counter twice, for track then aggregate. The
registry timestamp remains unchanged. Deletion hard-removes only the targeted
cue. Aggregate identity and creation time survive; an empty aggregate remains
`[]`. Its JSON retains insertion order while the Memory panel sorts by time.
Older aggregate `rb_local_synced=1` is preserved, like the newly created flag 0;
other synchronization metadata remains unverified. Existing cue microseconds,
MPEG fields, loops, comments and colors must never be normalized to new defaults.

Native also touches the selected ALL LOCAL node's timestamp in
`masterPlaylists6.xml`, without changing `djmdPlaylist`. SoundBox's DB-only pilot
passed native creation/deletion acceptance with XML and ANLZ unchanged; that
timestamp update is unnecessary for the accepted integration. No SoundBox analysis
or companion-XML writer is justified.

`soundbox.rekordbox_cues` is a **pure row planner**, without SQL/file access or a
live-write API. It binds raw track/cue/aggregate revisions and exact deletion
IDs/UUIDs, retains hidden overflow and unknown JSON metadata, and rejects stale
or inconsistent records, ambiguous numbering and unverified formats/flags.
Duplicate-position Memory creation is a SoundBox no-op policy, not a claim about
native duplicate behavior. Tests use synthetic rows; native equivalence and
encrypted-copy replay are separate private checks.

`rekordbox_cue_store.PilotCueStore` implements a **one-track-bound** transaction
path. `CueLibraryAuthority`, provisioned only after native acceptance, binds an
explicit accepted library/schema/version to each requested track. Source selection
and cache loads never grant authority. The bound store
shares the tag store's exact recovery root, writer lock and pending-journal path;
an unresolved other-domain operation blocks admission. Exact snapshot revisions,
backup, four-table/column authorizer, atomic transaction and independent read-back
guard each operation. Journals bind the physical source file before recovery opens
SQLite, so a replaced DB fails closed. Recovery recognizes exact before/after cue snapshots plus
counter; it never restores a whole DB or rewrites audio/ANLZ/XML. Synthetic real
process exits cover rollback journals, WAL and spilled pages. A zero-header
inactive SQLite journal may remain after an unspilled crash; recovery records its
exact identity/hash in the shared receipt. Both domains may recognize only that
unchanged inactive file; neither deletes it or invents a data change to clean it.
SQLite retains responsibility for its journal. See [SQLite's recovery rules](https://www.sqlite.org/lockingv3.html#dealing_with_hot_journals).

Private proofs replay nine native changes with the row planner and exercise six
PilotCueStore operations on a temporary encrypted library copy, verifying every
unrelated row/table and independent read-back. A saved-library survey accepts
2,406 cue-bearing WAV/MP3/AIFF tracks; four older records have different sync
metadata and remain blocked. These are copy/reference checks, **not native
acceptance of a SoundBox-written source cue**; source acceptance followed separately.

After the full 1,331-test gate passed, the one-track source pilot wrote A at
10,003ms and Memory at 19,021ms on No Trust. Both transactions independently
verified, and a full semantic comparison proved only the intended four-table
rows/fields changed; audio, ANLZ and companion XML remained byte-identical.
Sonny reopened and confirmed both cues. Native retained their rows exactly.
SoundBox deleted only the two pilot-owned identities, then Sonny reopened and
confirmed both cue types empty and quit normally. Final capture restores the
original djmdCue table exactly; aggregate remains '[]', CueUpdated='14'. Normal
history/counters are preserved rather than rolled back to the original '10'.
Exact live state and recovery evidence: [HANDOFF](HANDOFF.md).

The desktop uses 1–8 to preview populated slots or create empty ones at the exact
native playhead; Command-1–8 deletes the corresponding active-page cue. Empty
buttons can also be clicked. Creation never starts preview, even if its save
finishes before key release. Original-page release, remapping, alias holds and
text-focus boundaries remain. New Command-number defaults yield to preexisting
explicit custom shortcuts. Preview remains usable without write authority.

Three raw snapshot queries capture source revisions and native cue identities
outside normalized display dictionaries. Instant-paint schema 7 reads older 4–6
caches, which remain unverified for editing until fresh source load. These fields
do not alter waveform fingerprints or force recooking. Cue saves publish only a
new target Track/Library view and live markers, without seeking/reloading audio,
rebuilding browser rows or reparsing the entire library. Tag saves still refresh
smart memberships. Source writes remain serialized until verification. Following
Sonny's October 27 interaction review, cue UI admission uses an immediate RAM
session overlay and FIFO: new markers/slots can be previewed while saving; queued
deletes bind stable identities across Memory renumbering. Each source operation
uses the preceding verified revision and native identities. Only verified state
is cached. Failure removes the unconfirmed tail and reconciles the source; normal
quit drains accepted edits before stopping workers. Abrupt termination can lose
not-yet-started RAM edits, never grant them durable success. Cue transport uses
short 3-ms clickless envelopes, one target seek, and no library-wide artwork scan
or device discovery on pause edges. Grid work is paused for player acceptance.

Startup/source refresh reconciles prior authorized work before copying the DB,
without trusting cached library metadata. Unknown recovery stops the read. The
snapshot reader rejects nonzero rollback journals rather than copying potentially
uncommitted pages without their recovery file; inactive zero headers remain untouched.

Rekordbox Quantize is global. During AIFF reference capture Sonny reported the initial pair
was made with Quantize on, deleted and recreated with it off. That combined
capture is not treated as a single-edit reference; its observed counters are
recorded without guessing the unobserved intermediate sequence. After acceptance
Sonny was invited to restore the preferred global setting; its final value is
user-owned and not inferred. Those native references predate SoundBox's separate
Q toggle; cue creation remains exact when Q is off.

Milestone 2 and its installed branding/theme UI pass were accepted by Sonny on
October 26. Cue editing, responsive session overlays and the Text color menu
correction preceded milestone 4. Single-tag intelligent-playlist creation and the
default-off Collection setting follow the grid review. Playlist-only scope is
unchanged. Ask Sonny for specific native UI actions when reference evidence is
required; do not drive rekordbox remotely.

## Milestone 4 — grid source acceptance and desktop controls

Native reference captures and actual-source acceptance passed October 27 on
rekordbox 6.8.7.0303, without Cloud/Mobile Library Sync. The first primary-grid
pilot set No Trust's downbeat to 10,009 ms; Sonny confirmed bar 4 with earlier bars
present. The production transaction then wrote 90→180→90 BPM; Sonny confirmed
both native views after reopening. The final entire primary DAT matches the
original backup exactly. Native counters/history and cleared EXT/PQT2 remain;
never restore an old secondary cache or roll history backward. These checks
certify the source protocol, separately from installed desktop interaction review.

### Native format and metadata contract

- Native references use 1-ms whole-grid nudges; SoundBox's later zoom-scaled UI
  dispatches exact integer deltas (50/25/10/5/3/2/1 ms, Shift×3). The writer
  and ordered clipping/recovery protocol are unchanged. Rows translated below
  zero are discarded without rephasing retained rows. Setting a downbeat moves
  the nearest beat to the playhead and rephases labels, retaining earlier beats.
  Opening partial bars are bar 0; the first full downbeat starts bar 1.
- Doubling interleaves floor(midpoint) timestamps and uses adjacent saved
  intervals to extrapolate boundary half-beats. It requires two rows. Complete
  PWV3 bounds EOF conservatively without audio decoding; an ambiguous final
  sample interval or unsupported extended lead-in is rejected.
- Halving retains source labels 1/3 and renumbers from the first full bar.
  Integer BPM hundredths truncate: native 90.03→45.01, not 45.02. Retained
  timestamps stay exact. Scaling variable-tempo grids remains blocked; translation
  preserves tempo structure and never changes headline DB BPM.
- Manual BPM entry preserves the first saved beat/label, rebuilds existing rows
  at the new tempo, then extends from the rounded rebuilt tail to native EOF
  bounds. This tail restart matters at fractional millisecond positions. Native
  references include 90→90.03, 45.01→90 and 90→91→90. Constant grids only.
  October28 STOP CAPPIN reference confirms a beat exactly at PCM EOF is included:
  138.00 ends at 128,750 ms, and the complete candidate DAT/EXT matches native.
  If an existing or proposed final row lies inside PWV3's uncertain EOF interval,
  the shared source worker may resolve it with a bounded read-only PCM WAVE
  header proof. This path accepts only exact integral-millisecond PCM endpoints
  agreeing with PWV3 bounds; unsupported/fractional/redirected/changed sources
  still fail closed. It skips audio payloads and performs no decode. The proof
  binds canonical path, file epoch, framing digest, frames and rate, is journaled,
  and is revalidated before publication/commit and recovery. Ordinary edits do
  not open audio. RAM-only previews may omit the disputed trailing row until
  the verified worker result arrives; previews never authorize source writes.
  Manual saves additionally increment AnalysisUpdated, update the exact primary
  DAT contentFile Hash (MD5), Size, rb_local_usn and updated_at, and advance
  the shared counter by four: file revision +2, track revision +4. All other file
  metadata stays exact; unknown sync states block saving. SoundBox uses one
  transaction timestamp for the file/track, whereas native deferred persistence
  can stamp those at separate times. Existing authorities default to manual-off.
- DAT/PQTZ changes preserve every other section byte. Native EXT/PQT2 invalidates
  to 56 bytes with zero anchors/count; other EXT sections, 2EX and XML stay intact.
  A changed PQTZ length moves later section offsets: preservation compares raw
  bytes/type/header/length, not absolute offsets.
- Nudges/downbeat never change DB rows. BPM saves change only target BPM,
  TrackInfoUpdated (+1), rb_local_usn and updated_at, plus agentRegistry int_1
  (+2). Registry timestamp, AnalysisUpdated and all other fields stay intact.
  Native AnalysisUpdated 1/2/5 and synced 0/1 with NULL usn were captured. Five
  unusual status257/non-NULL-usn records remain blocked. An earlier compounded
  half/double/double save had additional side effects; it is historical evidence,
  not the clean protocol. Analysis Lock's 0x80 bit blocks edits.

Native controls were No Trust/WAV, Juice/MP3 and historical Jungle Juice (A/B/D
preserved). Original primary grids were restored exactly. An approved No Trust
primary-PQTZ-only restore was used for rounding/reference cleanup; it is a narrow
historical recovery grant, not permission to replace other analysis. Native grid
edits may remain only in rekordbox memory until normal quit.

### Guarded source transaction

`rekordbox_grid` is the pure byte/metadata planner shared by preview and saving.
`rekordbox_grid_proof.OfflineGridWorkspace` proves complete logical inventory and
file preservation in disposable copies. `rekordbox_grid_store.PilotGridStore`
retains its pilot class name but is now the accepted track/UUID-bound source API.
`GridLibraryAuthority` requires explicit native acceptance for the exact canonical
DB path, library identity, complete schema hash and application version. Fresh
library reads and caches never grant permission. Desktop stores bind that
permission to one exact native track UUID and analysis directory.

Tag, cue and grid transactions share one recovery root, writer lock and journal.
Closed rekordbox/agent, stable physical DB identity, schema/version, native UUID
path, regular owner-controlled files and same-volume publication are mandatory.
An encrypted DB baseline and exact before/candidate DAT/EXT backups precede writes.
The source transaction publishes atomic files before committing SQL. Recovery
validates the complete before/after set before modifying any file, then resolves
according to committed SQL or the durable file-only decision. Unknown/damaged
states are preserved and block other edits. Verified completed receipts allow
bounded private cleanup, including interruption during cleanup. Desktop startup
recovers known grid journals and orphan completed payloads before taking a source
snapshot. Audio, XML and 2EX are never writer destinations.

Eight private native replays compare whole DAT/EXT, target track and counter on
temporary source-layout copies. Synthetic cases cover real process exits, WAL,
rollback journals, corrupt backups, triggers, redirects, replaced DB identity,
foreign metadata and source conflicts. Actual No Trust source BPM acceptance
independently confirmed only the intended two tables/four target fields plus
counter/PQTZ changed. Source data and recovery evidence live privately under
AppData `rekordbox_recovery/native-grid-pilot-20261027/`, with additional Juice
and Jungle Juice reference roots. Incomplete `*.pending` captures are never valid
baselines. Preserve encrypted baselines, reference files and receipts.

### Immediate deck behavior and background saves

`desktop.grid_controller` probes only the loaded accepted track through the
shared TagEditController TaskOwner lane. Buttons require loaded audio, matching
track/path identity, an unlocked valid saved grid and no conflicting library/device
operation. Manual SoundBox-owned cache rows remain protected. The BPM field accepts manual constant-tempo entry only with the separate
`manual_bpm_accepted` authority. Enter captures track/value and queues the edit;
Escape/focus loss cancels typing. Track switches discard unfinished input. Simultaneous source writes with rekordbox/agent running fail closed;
reopen rekordbox after saving to see changes.

Accepted edits update RAM rows and the native grid immediately without seeking,
changing audio rate, reloading audio or rebuilding waveforms. A generation-bound
native setter swaps only grid geometry and bar counters. Late overview/detail
uploads retain the current grid and live cues. A track change resets that overlay.
A fresh library application releases completed grid overlays/pins for external
rekordbox corrections. The native audio callback never enters the source writer
or waits on its lock.

Nudges coalesce after 250 ms of inactivity, with a one-second maximum group age.
Ordered offsets are retained, so crossing zero and reversing does not resurrect a
clipped beat. Discrete edits queue in order. A cue created while a grid save runs
is immediately usable and saves next through the same owner. Fresh cue revisions
and base-file bytes are checked before each transaction; own verified cue saves
rebase subsequent grid work without losing raw metadata. Old preparation cache
publications are fenced in the source worker before saving. Verified grid rows
update only V5 metadata, preserving waveform payloads. Browser BPM updates target
one track, reconciling active BPM sort/filter without restarting playback.

Failure drops the unconfirmed grid tail, restores the verified preview and requests
source reconciliation. Normal shutdown flushes accepted grid groups before cue
and TaskOwner shutdown. Abrupt exit can lose not-yet-started RAM edits; transactions
already started retain durable recovery. Passing synthetic tests or source pilots
alone does not certify installed interaction or physical device behavior.

Every user-facing milestone requires the full release/render gate, inspected UI,
safe build/install/relaunch and curated-state verification. Current validation,
installed provenance and remaining user review are in [HANDOFF](HANDOFF.md).
No commit/push without explicit permission or wrap up.


## Quantize follow-up — October 27

Sonny requested a Q button in the grid strip: red on, grey off. It follows the
five grid operations and uses one-beat nearest-row snapping for new Hot/Memory
cues and session-CUE relocation. Default off; state persists in SoundBox settings.
Existing cue triggers, grid editing and accepted source transaction protocols
stay exact. Snapping uses the in-memory live grid, resolves ties earlier, rejects
out-of-audio targets and falls back to the exact position without a grid. Cue
intents retain their captured timestamp even if Q changes before background save.
No live source pilot is needed for this client-side position choice; the accepted
cue writer still validates/writes the resulting timestamp. Full release/native
regression and safe installation remain required. Rekordbox's preference stays
user-owned and untouched. No loop authoring or quantize subdivision selector is
added in this request. Installed provenance and review status are in HANDOFF.


The same follow-up adds unassigned Keyboard → Beatgrid actions for the five grid
operations and Quantize; BPM Value has no action. Existing modifier, text-focus,
conflict and autorepeat routing applies. Sonny also reported first bar-start
clicks doing nothing on Juice, Follow the Leader and 50k, with a second click
working. Captured 50k still had its 33-ms first beat while its visible paused
cursor was zero and the button enabled, with no saved transaction for that click.
Paused bar-start capture now uses that visible cursor rather than forcing a
native decoder poll; playing placement still captures fresh native time. Source
identity/readiness/lock guards remain. A single-button regression covers a stale
native 39-ms position versus a visible zero and verifies one saved intent. Bounded
bar-start press/target/no-op logging helps confirm the installed interaction.
This changed the capture path; subsequent live review below showed that it did
not resolve the lost first click. The synthetic case alone was not acceptance. The pure Juice zero-position candidate
matches Sonny's native DAT/EXT correction exactly; investigation did not rewrite
his track. Current installed/review state is recorded in HANDOFF.

Live review of that capture change still failed on Shadows and Sweet Dreams
(RAWS). The failed clicks produced no button-press log; successful clicks on
Bullet Train and So Fuck Em reached the handler and committed zero-position
grids. A read-only accessibility timing trace caught the button disabling around
new track loads while SoundBox was active. The separate saved-grid probe can
outlast deck loading/seeking. The correction admits a captured grid intent during
that probe and previews immediately from the loaded RAM rows using the shared
native row arithmetic. That presentation base grants no authority and reaches no
Library/cache/source state. Saving requires an exact base match, source metadata,
Analysis Lock and the full native pure transform/end bounds. Before native end
metadata arrives, provisional doubling omits the final extrapolated half-beat;
the checked preview reconciles that trailing boundary without moving audio.
Source conflicts cancel the unconfirmed tail; normal quit drains accepted work,
even if a different track is selected. Hermetic tests hold the probe open while
sending one mouse click, then cover FIFO order, lock/identity rejection and quit.
This changes admission timing, not the accepted native grid write protocol.

The same review requested a new layout: the title ends at the waveform's right
edge; the taller cue panel spans header and waveform rows. My Tags moves left
along the prep strip, followed by Settings and Search beneath the cues. The first
grid button aligns with the waveform's left edge. The SVG wordmark keeps its
270×55 slot; waveform height and cue/transport behavior are unchanged.

### Scrolling after a grid save

Sonny confirmed immediate first-click grid changes in the installed build, then
reported browser scrolling lag while its background save ran. The live sample
ended before his test edits, so it is only an idle reference. A disposable clone
of the retained encrypted backup reproduced the writer's CPU path: live backup
creation unnecessarily entered the full offline semantic inventory and made an
unused working copy, while each closed guard walked processes in Python. The
correction keeps the stable encrypted DB/WAL capture, SQLite integrity/identity,
exact hash/fsync and recovery binding, and preserves every fresh closed check
using exact OS process names. Full research inventories remain in offline proofs.
Library-cache JSON now encodes one record at a time with bounded writes, retaining
its schema and atomic publication. No source write fields or recovery formats change.

The associated visual fixes remove the separator before My Tags, align Settings'
right edge with the waveform, and make cue-column/page backgrounds transparent
so only the buttons retain the dark fill. Installed provenance, comparison
limits and live scrolling review are recorded in HANDOFF.

The final requested layout polish matches the logo/title gap to the left window
inset and adds the 1 Player/2 Player/Simple/Browser dropdown above transport.
1 Player remains selected; other modes are disabled placeholders. The transport
column and its divider extend through the prep row without changing waveform
height, grid/control alignment or transport behavior.

Manual BPM saves include the bound primary contentFile row in revision/recovery
checks. Old journals without that row retain their original planning semantics.
Browser BPM cells update immediately on each verified save, even while another
edit is queued or the same song remains loaded; RAM-only previews never confirm
a browser value. BPM sorting/filtering and surviving selections reconcile then.

The October 27 manual-BPM extension passed four whole-DAT/EXT native replays
(including fractional rounding), complete copied SQL inventories and an actual
No Trust 90→91→90 source cycle. Sonny confirmed 91.00 and normal grid loading in
rekordbox. The reverse write was byte-verified against the native capture, then
the original primary grid was restored under the existing narrow approval.
No native counters/history were rolled back. Manual authority is provisioned
separately only after that acceptance; old authority JSON defaults to manual-off.
The small evidence root is ~/Library/Logs/Soundbox/manual-bpm-2026-10-27/.
