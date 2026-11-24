# Runtime contracts

Detailed first-party safety and lifecycle requirements. Read with [AGENTS.md](../AGENTS.md)
and the [current handoff](HANDOFF.md). Dated examples never grant new actions.

## Whole-app responsiveness without QOL tradeoffs — November20

Performance work preserves complete, usable first frames and the current visuals.
Track Info opens with the latest accepted selected artwork and both tabs ready;
an empty/stale image that catches up after opening is a regression. A loaded deck
must not release audible Play/autoplay or advertise complete readiness while its
complete waveform is still preparing. Decoder, metadata and waveform preparation
may overlap internally; measure time to the complete usable deck as well as input
responsiveness, never an earlier partial callback as the speedup. Unsupported or
failed work retains truthful error/retry ownership rather than silent success.

Preserve selection, multi-selection, scroll, focus and typed drafts through
refresh; the latest load/transport/artwork choice wins over older callbacks.
Captured cues remain immediately usable and durable admissions/ACKs retain order,
restart, Quit and backup ownership. Ordinary pages/services stay prepared before
their first interaction; device surfaces stay on demand. Moving work to a thread
must actually improve GUI responsiveness, not introduce GIL stalls, missing first
frames, new confirmation flows or canceled accepted work. Caches reuse only valid
inputs and never replace source freshness/authority or recovery checks.

## Main-lane waveform modes — explicit November21 user requirement

RGB is the accepted exact visual reference. At the same song, zoom and position,
BLUE must use its main-lane outline, height, thickness, footprint/LOD,
phase, motion and faint-coverage shoulder without changing any RGB pixel.
Geometry uses signed audio detail when present and the resident RGB display
stream otherwise. BLUE palette membership cannot supply a separate outer mask.
The latest user clarification preserves3BAND's original PWV7 shape/detail, own
layer shapes and continuous motion. Main display gain changes1.20→1.35 with its
existing ceiling; its seven-color edges use exact joint encoded region areas,
not products of averaged masks/linear-light blends. Zero-height main bands are
absent. Do not substitute RGB/audio
geometry for3BAND. This supersedes the earlier all-mode common-contour proposal.
Mode switches
reuse complete resident buffers, without new analysis, source reads or cache
changes. BLUE/RGB geometry stays protected;3BAND mini now follows dedicated PWV6.
This targeted reopening supersedes BLUE's historical independent height/motion
policies; it preserves coordinated waveform/audio readiness and all
source-format, transport and device gates. See [PLAYER](PLAYER.md) for details.

November21 BLUE palette follow-up uses four native LUT families and three
encoded-gradient layers over the resident raw PWV5 song-color field, sampled at
the fixed92pt/s reference without RGB's25% occupancy correction. PWV5 is a
spectral proxy, not the native physical filter amplitudes; PWV7 is not its source
and exact native BLUE pixel parity is not claimed. Keep RGB output and BLUE's shared
outline/LOD/motion/coverage unchanged. The native base blue for unclassified
audible data adds no opacity floor; zero geometry remains empty. The mini BLUE
palette uses optional PWV4's signed eight-class selector, white underlay and alpha
shading, preloaded by the selected worker before the first complete surface.
ABI9 sets/clears it within grouped generation fences. No new DSP, cache format,
migration, reanalysis or source-write authority belongs to this presentation work.

The November21 mini correction reads all1200×3 seven-bit PWV6 rows before the
first grouped frame, alongside PWV4. Native global normalization/cosine transfer
produces bottom-stacked opaque blue/orange/white paints in a CPU-prepared1200×25
premultiplied encoded image. Source baseline24 leaves row23 blue and row24
transparent, a mini-only one-source-pixel floor. Missing/invalid optional preview
data uses a complete resident derived fallback before grouped readiness; there
is no late preview promotion. It has no seven-overlap palette;
main PWV7 attributes/gain1.35/motion remain separate. Whole-frame and final mini
resampling quality parity require captures and are not established by formulas.

## Prompt native saves — November20 follow-up

Eligible source edits dispatch on the next event turn when the shared writer
becomes available; stale contention timers must not add a fixed wait. Actual
native-owner/failure deadlines retain their retry backoff. Successful saves reuse
only pure work already proved from identical inputs: bounded in-memory SQLCipher
passphrase derivation by exact salt/KDF settings, and the current playlist call's
verified plan after every archived transfer matches its proved bytes. No pages,
source identities, process absence, authority, recovery or SQL/readback checks are
cached. Cold recovery independently reconstructs the playlist plan. Journals and
durable intentions remain in place before source mutation so interrupted writes
retain their exact recovery and retry behavior. Installed status stays in HANDOFF.

GridStore may retain one successful pure-transformation key, bound to complete
authority and journal inputs apart from the independently checked decision.
Every use still freshly verifies source identity, encrypted backup, payload
generation/destinations and all archived bytes. Exact audio-end acquisition stays
fresh; cold owners and changed inputs reconstruct independently. This reuse never
supplies authority or a live-source observation.

## Fast local loading — explicit November17 user requirement

Preserve the currently accepted fast local-song load and first complete waveform
as release requirements. Source readiness and complete waveform visibility are
separate measurements; provisional/partial geometry is not a faster success.
Cue admission, canonical saves and native publication must not delay the loaded
deck. Keep selected load work bounded and independent of unrelated queues.
Report unpinned/cloud-set acquisition separately from ordinary local songs.

## Immediate cue placement — explicit November17 user requirement

Show and enable a captured Hot/Memory cue immediately, including while playing;
canonical and rekordbox saves continue behind that pending per-track view.
Switching songs retains each song's captured positions/identities and queued
writes. This supersedes earlier marker-after-canonical-ACK presentation rules,
not native writer authority, durable-intention-before-source-write or success
truth. Bind pending views to exact source/track/UUID/path/repository generation.
An older refresh or ACK cannot erase newer cues. Admission failure retains usable
pending cues and explicit retry/error ownership; Quit/source switch/restore must
drain or honestly retain/refuse unsaved work. Never claim RAM-only native success.
Finished detector results wait behind queued cue admission and active canonical
publication as owned Python payloads, without retaining a live QThread. They
keep source/track/job and repository-generation checks; a busy save cannot erase
markers, lose a valid result or trigger duplicate detection. Queued gestures
preserve independently renewed SHA/epoch proof and cleared identity holds.

November23 loaded Hot/Memory cues must also appear on the first complete mini
waveform frame. Decoder timeline publication wakes the paused renderer; until
then resident waveform count/rate may project the mini only. That presentation
fallback never supplies audio duration/readiness or delays other preparation.

## Capture/reuse boundaries after correctness review

Routine automatic song metadata updates do not occupy the foreground footer.
Prepared progress and the source-scoped Pause/Resume/Retry actions belong in the
PLAYLISTS controls; drawing them must not scan or validate the backlog. Real
save/storage/identity failures retain an attention notice and menu state, including
while paused. A device operation defers that notice without losing it. Lifecycle
ownership, durable acknowledgements and DB-before-Quit drains remain independent
of this quiet presentation.

iPod export captures a durably saved, detached generation of the selected effective
metadata and playlist membership while automatic metadata updates are paused or
running. A short ordered canonical checkpoint waits for its own admission, not the
complete native metadata backlog. Selected actual source replacement, locator,
registration, structural and non-cue local admission transitions still settle
first. A dormant queued/failed CDJ compatibility policy is not a source mutation
and never decides iPod format eligibility. Read-only worker observations guard
actual recovery/staging; source acquisition and each iPod backend decide media
admission independently. Non-native sources never borrow native conversion guards.
Source,
repository, restore and mount changes invalidate the request; later ordinary edits
cannot mutate accepted output, and later Sync sees their new values. Generic USB
retains its separate native CDJ export capture and source-save barriers.

Actual audio acquisition pairs expected SHA/size with a stable canonical path and
before/opened/closed/after epoch, with one bounded instability retry. A known hash
never blesses an unverified replacement epoch. This does not require reacquiring
unchanged retained export inventory. USB source revision binds rendered global
MyTag definitions. Manifest models and original bytes share one bounded capture;
explicit settings changes retain the captured profile-byte precondition too.
Artifact profiles exclude development integrations and their isolated native
ORM/SQLCipher probe uses only disposable RAM. These checks grant no new source
publisher, codec/profile acceptance, app input or general process termination.

New, unindexed iTunes audio on the admitted FAT32 target uses one hashing copy
and one actual device readback. Exclusive publication cannot replace an occupant;
the original output descriptor stays open across rename, content verification
and immediate receipt consumption. No iTunesDB reference, verified-song progress
or acknowledgement precedes that readback. A failed copy retires only its proved
own unindexed entity; uncertain ownership retains evidence and requires recovery.
This narrow path does not change ordinary staged replacement, rewrites or generic
USB export. FAT epochs alone never substitute for checking the actual bytes.
Measured copy/readback byte counts drive progress within large files; phase
allocation is not a time estimate. Only joined final publication/readback owns100%.

Ordinary iPod Export and Sync require no second plan consent or success modal.
Delete asks one concise question before its worker plans the captured selection.
Format omissions and optional association limits stay nonmodal; actual failures
show bounded useful details while full outcome/recovery diagnostics remain logged.
Verified playlist associations survive separate exports and device-only deletion,
including folder labels and deliberate format omissions. They never grant audio
ownership or permission to remove unknown songs. Explicit Sync reevaluates omitted
sources; a stable bounded WAV header can prove rejection only, never admission.
Finalization refreshes the completed target's cached native catalog even offpage,
preserving current navigation and serial device ownership. Header Export/Sync
reads prepared pending state; it must not perform song, filesystem or DB scans.

The iPod owns observed playback history, ratings, resume positions, firmware
preferences and actual device contents. Playback-only changes refresh SoundBox's
read-only snapshot, preserving selection, scroll and checked playlists; they do
not request Sync or write the device. Unrelated externally added playlists,
songs and artwork are observed and preserved without pending Sync. Compare
current linked playlist identities, order, membership and supported musical
metadata; those changes may request Sync. Device-side playlist deletion does not
authorize recreation. Advisory comparison never renews persisted catalog hashes
or weakens exact writer, source, recovery and publication proofs.

## November19 iPod interaction requirements

The header and playlist-footer primary actions mirror the same prepared state:
Export when clean, Sync only for actual linked pending changes. An invisible
current/focused playlist is never an implicit update target. Tag deletion belongs
to the exact tag's context menu, with captured source/identity rechecks and one
existing confirmation. The My Tags panel has no dedicated deletion footer.
Tag rename also belongs to that exact tag's context menu. It retains the tag's
native identity, assignments, category and smart-rule references; naming a newly
created tag does not authorize renaming an existing one. Cross-folder smart
playlist moves retain the original playlist identity and rule, with their own
accepted native move protocol rather than ordinary sibling-order permission.

New My Tags become visible and usable immediately from their captured creation
intention. Pending assignment and smart-playlist gestures bind the exact source,
library, future tag/category identities and creation operation. The parent
intention becomes durable before its children; native creation and verified
acknowledgement/publication precede their source dispatch. Pending views never
change verified Library/export truth. Keep general edit ownership while yielding
only the dispatch reservation to the prerequisite. Failures, restore, restart and
normal Quit retain or fence exact dependent gestures; retries never adopt a
replacement creation. Uncreated definitions cannot be renamed, deleted or reordered.

Device IO on a known stock, dual or Rockbox iPod does not block independently
authorized tag/cue/grid, metadata and playlist source saves. Source writers remain serialized under their
existing shared lease/journal; actual source/recovery/maintenance ownership stays
blocking. Accepted export membership and metadata remain detached from later
ordinary edits. Generic or unknown device targets keep their separate barriers.

Playlist names are unique under trimmed Unicode-NFC case-insensitive comparison
across ordinary/smart native playlists, custom playlists and queued name claims.
Folders are not playlist names; reject new collisions without renaming existing
external curation. iPod-visible titles use the leaf alone. Migration must preserve
foreign contents and exact owned-path retirement proofs through failed retries.
Completed native migration keeps custom memberships as recovery evidence, not
live name claims. A deleted playlist frees its name for ordinary or smart reuse;
active native owners and pending create/rename intentions still reserve names.

## Runtime instruction parity

`AGENTS.md` and `CLAUDE.md` are runtime equivalents and intentionally contain
the same shared project guidance. Whenever shared instructions, safety rules,
documentation routes, build steps, or handoff facts change, update **both files
in the same change**. Runtime-specific guidance must be clearly labeled and
must not silently contradict the other file.

At the start of every session, read this file and `docs/HANDOFF.md` before the
relevant documents below. Do not rely on a prior agent's memory when the
repository can answer the question.

## Sacred safety contract (NON-NEGOTIABLE)

- November12 shared-library completion requirement: every currently offered
  musical/library edit with a native counterpart must persist in the shared
  rekordbox DB and survive leaving SoundBox. Current effective SoundBox titles,
  artists/aliases, organization and saved prep are authoritative migration input.
  Freeze both intended title and artist before changing either native field;
  native identity/revision checks must not overwrite that curation. Local-only
  drafts, unsupported-profile guards and synthetic tests do not complete an
  offered feature. Preserve newer edits, unresolved identities and restored
  Sonny/Zyko independently. This requirement does not remove independent native
  acceptance or authorize real-app/source experiments; implementation and proof
  gaps remain open in the [current audit](shared-library-audit-2026-11.md).

- Native metadata v1/v2 profile markers keep their original restrictions. V3 is
  independently marked for Unicode text, existing-album title preservation and
  unchanged synced-artist reuse. Static native integer conversion plus generated
  content/revision sequences support its bare NULL/0/1 revision handling with
  Analysed0/105. This does not infer a cloud sequence, artist-album transition or
  orphan cleanup from a normalized component update. Recovery and compact
  receipts must retain the exact profile and legacy reconstruction semantics. A bounded
  source pilot grants only its approved operation IDs; no general authority file
  or bulk migration follows from copied/native component proof or one source save.

- Metadata v4 separately models Main's selected old-artist cleanup, including
  combined new-artist creation. Local rows hard-delete; synced rows retain exact
  tombstone/history identity with status2/deleted1 and one cleanup counter step.
  Preserve unrelated unused definitions and every surviving artist role. A
  synced retirement requires its own persisted UTC clock, preceding the final
  Content clock; old profiles cannot consume that request extension. Only v4
  artist saves may UPDATE the four proven artist tombstone/clock columns.
  Its counter stays inside Main's signed-int range. Legacy v1/v2/v3 journals and
  compact ACKs keep their original meanings. Generated component/literal-SQL
  proof does not execute Main, select global orphans or authorize a source test.
  Artist-album transitions and cloud rows remain outside v4.

- Metadata v5 independently models existing-album artist saves: exact unique
  active album/name/UUID and album-artist lookup, preserved name/roles/opaque
  fields, separate persisted Album clock and one bookkeeping counter before
  scoped artist retirement/revision. NULL AlbumArtistID/Compilation normalize
  only in that observed artist-save sequence. Only v5 artist SQL may UPDATE the
  four proven Album role/compilation/clock columns; no Album insert/delete/name
  mutation. It selects the unique active desired artist while preserving
  same-name deleted history. Its separately proved cloud Content profile retains
  status257/synced1/local status0or2 and exact positive remote USNs; remote/local
  clocks are independent. Unobserved cloud-album and remote-artist retirement
  remain guarded. V1–v4 requests/journals retain their original reconstruction.
  Component/static/copied proofs grant no source pilot or general activation.

- Existing artist merges/unmerges and title/artist resets retain paired native
  intentions before presentation. Freeze automatic origins before native updates;
  clearing an override must not mistake the last acknowledged native value for
  the original automatic value. Single/bulk alias edits publish one durable next
  generation, preserving admitted operation identities and newer explicit edits.
  Source/library/local/native IDs, UUID and path pin dialogs, builders and work.
  Only a unique same-row relocation with matching last acknowledged title AND
  artist may move an origin; it cannot retarget queued writes. Alias targets and
  origins are canonical per-record state and belong in portable snapshots.
  Generated title protection requires the exact current binding; unresolved or
  replaced identities cannot mutate a local title map. Completion checks both
  saved fields. A changed counterpart retains its operation and attention;
  explicit Retry durably records consent before composing a new existing-field
  correction with the original acknowledgement. This grants no broader SQL scope.
  Raw scalar recipes and per-field edit/ACK ownership preserve cleanup/reset
  inputs without making own inferred names trusted native tags. Retire only
  fulfilled exact-own masks; newer local gestures and unrelated fields survive.
  Later independent native values supersede old fulfilled bases. Existing title
  policy changes save one frozen successor generation before display. Fresh
  refresh reconciliation joins source selection/curation in one save; dirty
  derivation runs on the existing worker with prepared registry context. Only a
  real global trust/canonical/policy change expands the affected row set. Each
  process's first verified source read checks all rows even after a warm browser
  cache, because a presentation cache is no publication checkpoint. Own ACKs
  and unchanged later refreshes start no derivation pass. Batch map clones are
  bounded to one per changed map; keep ordinary save/fairness/export gains.
  These source fixes grant no broader metadata profile or authority acceptance.
  No online functionality, sign-in or account/profile-copy route belongs to this
  supported-feature audit; verification remains offline.

- Rekordbox and SoundBox are equal USB exporters. Users may switch between them
  at any time. Another rekordbox export is normal, never an ownership conflict,
  a reason to clear a library, or a reason to demand a verification workflow.
  Start from the current small native catalogs, preserve other playlists and
  media, and apply the requested changes. SoundBox's manifest records only its
  own file ownership and playlist bindings; it is not exclusive library authority.
  Generic USBs never display or compute automatic sync/pending status. Explicit
  export updates only selected playlists; normal export never scans/hashes
  retained media just because rekordbox wrote the catalogs. Mount/source
  separation and supported-format guards still apply.

- Desktop rekordbox reads use a read-only DB/WAL snapshot; XML is a fallback.
  The October 26 prep plan authorizes narrowly scoped My Tag and Hot/Memory Cue
  writes through `soundbox.rekordbox_tags` and `soundbox.rekordbox_cue_store`,
  bound to explicit library/schema/version authorities,
  verified backup, exact expected track/tag identities and closed rekordbox/agent.
  Native tag acceptance passed October 26 on Sonny’s actual library; its exact
  authority enables selected-row assignments only on rekordbox 6.8.7.0303. A
  different library/schema/version requires separate acceptance. The separate
  `soundbox.rekordbox_write` proof remains temporary-copy-only, without a
  publish-to-source API. Native DB-only cue creation/deletion passed on No Trust;
  WAV/MP3/AIFF and older-aggregate references inform the accepted protocol.
  `CueLibraryAuthority` is provisioned only after native acceptance and binds each
  store to one explicit track. Both domains share the exact recovery root/lock/
  journal and one desktop worker owner. Pending operations reconcile before a
  different edit; unknown recovery or sync metadata fails closed. Cue identity
  revisions and native IDs are separate from waveform/display cue dictionaries.
  SoundBox’s Q setting optionally snaps newly captured cue points against the
  current RAM grid; existing cue triggers stay exact. Never read or change
  rekordbox’s global Quantize preference.
  Each cue/grid save needs only its own independently accepted write authority.
  Its fresh companion snapshot is read-only under the same recovery/lease guard;
  a missing/corrupt analysis packet cannot veto DB-only cues or overwrite their
  unrelated private grid cache. Read access grants no companion write API.
  Cue session overlays are immediately usable while a FIFO saves in background;
  only verified target metadata reaches Library/cache, without reloading audio or
  the whole library. Normal shutdown drains accepted cue intents before stopping
  TaskOwners. Ordinary native prep now persists per-song intentions before
  presentation and retains them on failure/restart; source reconciliation and
  exact receipt proofs precede retry. Legacy RAM-only overlays are not durable. Transport state edges must not
  scan library artwork or trigger device discovery. Grid native references and
  actual-source downbeat/BPM acceptance passed October 27. New My Tag definitions
  and tag-based intelligent-playlist creation passed separate November12 native
  acceptance under the independently bound domains below.
  Sonny normally operates the rekordbox UI for reference/acceptance actions.
  His October31 no-controls instruction supersedes the earlier generated-fixture
  input grant: do not automate inputs into either real app. Private fixtures and
  read-only evidence remain allowed; historical grants never broaden later
  source authority. Sonny subsequently approved the current disposable isolated
  native test session only; its real-database/music/profile access must remain
  blocked. That scoped exception grants no real-profile input, live-source
  experiments or authority activation. Exact scope/status is in HANDOFF.
  See `docs/rekordbox-prep.md`.
- Ordinary desktop playlist/folder create, rename and delete plus registered-track
  append/remove passed native acceptance October30 through
  `soundbox.rekordbox_playlist_store`. Its separate explicit authority binds the
  exact source/library/schema/6.8.7.0303 version; selecting a source or replaying
  an old pilot never provisions it. Allow regular local0/1 sync states with no
  cloud USN and verified UUID/local counters. Source SQL is confined to playlist/
  membership rows and localUpdateCount; Collection/content/cue/analysis/audio are
  preserved. Deletions retain native history/XML and sibling ordering. It shares
  Tag/Cue/Grid root/lock/journal and the one desktop source worker, requires
  rekordbox/agent closed, stages encrypted DB/XML pairs, and validates exact
  recovery prefixes before another edit. Current/unresolved native acceptance retains its required full pairs; completed
  historical copies follow the November10 bounded-retention rule below. Ordinary
  completed backups retain the latest full pair plus permanent
  row/XML/operation receipts with verified, resumable older-payload retirement.
  Pending UI intents persist before worker admission and survive restart; no
  local veto substitutes for a source save. Retry renews stale/rolled-back intent
  identity/revision/clock, while committed receipts replay idempotently. Normal
  UI Quit/close remains open while captured import/analysis/source saves remain,
  with input and source pumps live; the original Close/Cmd+Q completes after
  safe saves finish. November19 normal Close/Cmd+Q may retain failed source
  intentions after an exact fresh canonical checkpoint verifies their complete
  queue values. Errors and operation identities survive unchanged for the next
  launch; no discard or native success is implied. Active source/native writers
  still finish and join. Unsaved RAM admissions, failed storage and unresolved
  canonical recovery cannot authorize closing. A changed/restored generation or
  newer queue invalidates the close proof. Explicit teardown still retains any
  unstarted durable intents. Normal Quit may
  also retain validated, idle metadata intentions when the exact verified source
  has no metadata authority, every native request is still uncaptured, and no
  worker/receipt retirement remains. Unstarted read-only derivation can be
  reconstructed on the next verified source read from preserved curation and
  frozen intentions. No edit is acknowledged or removed;
  captured/active metadata and other source domains keep their drain gates.
  Export waits report terminal saved/admission errors and stop polling; they
  preserve every source intention and another device owner's progress. Accepted
  progressing edits still finish and rebind before export capture.
  CDJ capture waits for these edits and rebinds native selections by stable ID;
  deleted selections or changed sources cancel before export. Exact UUID pins
  retain affected tracks in SoundBox Collection without registering new files.
  Folder deletion captures exact descendant ContentIDs/UUIDs on the same worker
  and durably saves retention before writer admission; fresh guards recheck it.
  Failed acknowledgement/restart uses captured intentions or exact retained
  historical receipt pairs, never the now-deleted browser membership. Causal
  rebasing additionally requires the independently reconstructed committed edge
  to equal the current catalog/XML. Legacy retired evidence missing original
  UUIDs remains guarded. These proofs add no publisher/SQL scope.
  Bounded envelopes compose at most64 already accepted create/append/remove
  commands and16384 memberships through this SAME writer/lock/journal/paired
  backup. Exact child identities/clocks persist before admission; pure row/counter
  chains and initial/final revisions reproduce sequential DB/XML effects. Every
  guard still hashes recovery payloads and checks source/authority/apps freshly;
  only exact immutable XML parsing and previously verified semantic reconstruction
  may be reused within their bounded lifetimes. Registration and sidebar node-
  reorder opcodes do not enter these envelopes. Song reposition composes only
  accepted remove/append children; `membership-replay-v1` derives their identical
  final rows/counter/history/XML with a compact plan. Actual unmarked saved
  requests retain their exact original reconstruction. Sidebar reorder has an independent default-false capability and native
  reference proof; its source-to-native reopen pilot passed November2 and the
  exact-library general capability is provisioned.
  Existing intelligent/cloud mutations remain separate gates; new-track registration uses
  its separate accepted domain below.
- Native My Tag definition creation uses `rekordbox_tag_definitions` with its own
  marked exact-library/schema/version authority and registered constructor/name
  profile. It creates an unassigned leaf in an exact active category; it grants
  no definition rename/delete or song-assignment API. Tag-based smart creation
  uses the existing playlist writer only with its separate explicit capability
  and exact profile. November12 source creation and native reopen preserved the
  complete target rows/XML and all musical tables; both test playlists evaluated
  to752tracks and the new tag stayed unassigned. Production authorities are
  provisioned only for that accepted6.8.7.0303 library. Older files default off.
  The form starts with My Tag and ANY for one or multiple conditions. Root and
  nonempty local smart-only folders are supported; empty/mixed folders remain
  guarded. Creation-scoped sibling shifts change only Seq/local counter/update
  clock, preserving existing rules/names/UUIDs and relative order. SQL and XML
  clocks are independent. Persist intents/renewed clock chains before admission,
  verify full fresh computed membership before acknowledgement, and share the
  closed-native-app worker/root/lock/journal. Existing intelligent/cloud editing
  remains outside this capability; another library/schema/version needs its own
  acceptance. Ordinary native recovery keeps its bounded latest full pair and
  permanent compact receipts under the November10 retention rule.
- Intelligent rename and mixed reorder have separately marked, default-off
  capabilities; ordinary CRUD/reorder and smart creation never grant them.
  The local inline-name candidate follows the November19 native +1 window
  while preserving its rule, identity, memberships and unrelated XML; the
  criteria-definition editor's +2 creation window stays separate. The November19
  mixed-order reference updates the dragged row first, then each displaced row
  in ascending old sequence, with one local-counter increment and independent
  SQL clock per row. Only target and non-root parent XML clocks change. Full
  private DB/XML replay covers both directions, long moves, mixed intelligent/
  regular neighbors and root folder neighbors. Intelligent targets and displaced
  intelligent rows preserve measured local history 0 or 1, zero status/deletion
  flags and absent cloud USNs; other histories fail closed. These rename/reorder
  profiles admit neither cross-parent moves nor rule edits. Unsupported
  ordinary gestures involving intelligent rows fail before durable FIFO
  admission. Read-only native references and encrypted TEMP replay do not
  provision a source authority; publication/native reopen acceptance remains
  independently required.
- Tag-definition deletion has an independently marked authority and measured
  `native-local-leaf-delete-v1` profile. Current implementation covers unsynced
  local definitions and unsynced local assignments: following local tag siblings
  compact in source order, each advancing its local counter, then the deletion
  advances the counter once. Assigned local links hard-delete without another
  increment. Intelligent rules and XML stay byte-identical, including rules
  referencing the removed tag; confirmation describes their changed matching.
  Historical/synced definitions, links and shifted tag siblings remain guarded.
  Smart-list deletion has its own default-false capability/profile on the shared
  playlist authority. The measured unsynced local target hard-deletes; following
  local siblings compact with one shared new counter and independent captured
  SQL clocks. The parent and retained XML stay exact. Exact UUID/rule/parent,
  source revision, recovery and receipt checks precede acknowledgement/retry.
  Both publishers share the existing worker/root/lock/journal; native references
  and TEMP replays do not provision the production authorities. Independent
  source/native acceptance remains required for each capability; HANDOFF records
  completed approved pilots and exact-library activation after the fresh
  gate/package checks. Accepted local v1 profiles grant no older-history scope.
  Separate combined v2 markers additionally cover observed local synced smart
  tombstones and synced tag targets/association history. The earlier local-only
  markers remain strict. The retained isolated synced-smart reference writes
  its tombstone at C+1 and one following survivor/global counter at C+2, with
  independent SQL clocks and exact retained rules/XML. It does not establish
  multiple-survivor grouping. The November19 zero-survivor observation also
  contains an unrelated native playlist creation and remains a compound frame.
  Historical smart deletion still needs its own source/native acceptance; a
  disposable unsynced deletion pilot cannot provision that capability.
  Synced tag deletion owns the first counter step, then shifts later
  local siblings and uses one final shared step for active synced assignments;
  pre-existing deleted history remains exact. Surviving synced tag siblings,
  synced tags without active synced assignments and unobserved metadata remain
  guarded. The compound two-tag oracle labels its overwritten intermediate clock
  as unobserved; its final whole-pool match is not isolated first-window proof.
- Native My Tag order has its own default-off, exact-library authority. The
  accepted bounded primitive reorders adjacent leaves within one category;
  larger gestures compose only that proved primitive. Full category/tag
  ID/UUID/name/order, source/version/schema, independent clocks, recovery and
  fresh readback remain required. Definitions, assignments and other categories
  stay exact. November19 source/native acceptance and the fresh gate/package
  checkpoint completed; the exact-library authority was separately activated.
  HANDOFF distinguishes that activation from installed-app verification.
- Native tag rename and cross-parent smart-playlist moves are separate
  default-off capabilities. Existing tag creation/order/deletion and playlist
  rename/reorder/creation permissions do not admit them. Rename captures exact
  tag/category UUIDs and old name; smart moves capture target UUID/rule and both
  parent UUIDs, using append-only destination order. The desktop may project a
  pending move but cannot publish it as native/export truth before durable ACK.
  Source sibling sequence gaps remain exact; destination ordering comes from
  the separate pure move context, never ordinary sibling-compaction assumptions.
  Persisted SQL/XML clocks and their renewal remain engine-owned. Independent
  native/source acceptance is required before enabling either exact-library
  profile; current activation status belongs in HANDOFF.
- Generic node placement has its own `node_move_accepted` flag and exact
  `rekordbox-6.8.7.0303-local-node-placement-v1` profile; smart append/move or
  sibling reorder permission never substitutes. It moves local regular/smart
  playlists and ordinary folders by exact parent/anchor identities, including
  nested folders. Reject cycles, self/descendant destinations, ambiguous sibling
  order and unsupported row histories. Preserve descendants, song memberships,
  rules/names/UUIDs and source-parent sequence gaps; only captured changed
  destination positions receive their independent counter/clock windows.
  Center drops append inside collapsed or expanded folders; before/after drops
  retain their explicit anchor. This separately accepted exact-library path
  shares the existing worker/lock/journal and fresh readback/ACK guards.
- Normalized ordinary, folder and smart constructors have separate default-off
  flags and exact profiles; the older smart root/front/append restrictions above
  keep their original meanings. The normalized paths admit observed unique
  sequence gaps and empty/mixed local destinations while rejecting ambiguous
  order, unsupported histories and changed identities. Smart construction starts
  after the maximum root Seq, then applies its captured placement/definition
  windows; sibling normalization changes only exact affected rows and clocks.
  Right-click creation captures placement below the clicked sibling and transfers
  that sidecar to an accepted move in the same durable ACK. Pending projections
  grant no native/export success before verified publication. Current exact-library
  acceptance/activation is documented in the handoff and historical receipts;
  another library/schema/version needs independent acceptance.
- Sonny's November12 accidental ZYKO deletion has a one-off, explicitly scoped
  recovery record. Its exact pre-error ID/UUID/assignment rows were restored
  with compact tag order through the shared lock/journal and verified encrypted
  baseline after TEMP proof/review. SONNY's intended deletion and all unrelated
  source/curation data were preserved; the current counter was not rewound.
  The marked receipt only recognizes its exact inactive SQLite journal. This
  adds no general source restore API; retain unknown/pending repair evidence and
  use the exact private resumable tool listed in HANDOFF.
- Ordinary explicit file imports and imported-track playlist drops use the
  separately accepted `soundbox.rekordbox_registration_store` authority, bound to
  the exact native library/schema/rekordbox6.8.7.0303. Sonny approved its two-WAV
  source test and conditional activation October31; native reopen/normal-quit
  acceptance passed. Formats are MP3, AAC/ALAC M4A, FLAC and PCM16/24 WAV/AIFF
  (native types1/4/5/6/11/12). Source SQL may only insert a bare content row and
  advance localUpdateCount; existing content, XML, cues/analysis, metadata and
  source audio remain untouched. Native playlist membership uses its own accepted
  playlist domain after registration. Exact canonical/raw path, native ID/UUID
  and durable alias/inode/epoch claims guard reuse; independent curation under
  both logical/native IDs requires review. Queue source/destination/order/options
  before admission, exact SQL requests before execution and verified bindings
  before presentation. Restart/retry uses committed receipts; cancellation first
  reconciles source effects and never deletes completed native songs. This domain
  shares the existing source worker/root/lock/journal/closed-native-app guards and
  encrypted paired recovery. Current/unresolved acceptance retains required payloads and compact receipts;
  completed historical copies and ordinary transactions use bounded
  receipt-backed retention. A file gesture
  authorizes its own native registration/membership without a separate save step.
  November1 follow-up: Sonny explicitly requested backfilling his existing visible
  SoundBox-only Collection and migrating all custom folders/playlists plus native
  Artists/artist playlists into this exact library. November2 supersedes that
  artist portion: Artists is virtual-only, the exact generated hierarchy is
  removed through accepted playlist deletion, and no native artist jobs recur.
  The real custom/Collection upgrade is a durable, source-bound
  composition of the accepted registration and playlist APIs, not permission for
  new source publishers or broader musical-data migration. Preserve completed
  musical results/drafts and hidden/disposed songs. Sonny also requested showing
  native divider/test entries: disable hide-untracked for this merge.
- Initial shared analysis uses the separately accepted `rekordbox_analysis_store`
  authority for this exact library/schema/version. Only initial full BPM/Grid+Key
  and native waveforms on bare stereo PCM16/24 WAV44.1/48kHz are accepted. Render
  from captured bytes and saved rows, persist intentions and exact requests, and
  publish via the common closed-native-app worker/lock/journal with encrypted
  DB/XML and packet recovery. Preserve cues/gain/audio/XML/playlists and unrelated
  rows/sections. Native readback precedes acknowledgement; newer manual drafts
  survive. Other codecs, existing native analysis replacement, gain and broader
  metadata require independent acceptance. Cue drafts use the separate cue API.
  November2 separate MP3 acceptance: `rekordbox_cbr_analysis_store` admits only
  initial full saved grid/key/waveforms on bare unlocked MPEG-1 CBR/no-Info/Xing
  stereo44.1kHz MP3. It independently scans captured frames and constructs PVBR,
  preserves native Length/gain/cues/audio/XML/playlists, and keeps original durable
  results through exact-SHA ctime renewal. Authority/plan/journal/retention are
  separate from WAV v1; the worker/root/lock/closed-native-app guards are shared.
  Legacy jobs keep their original WAV profile, with no implicit MP3 reinterpretation.
  Research wrappers/fixtures never grant source authority. Other MP3 profiles,
  source analysis replacement, gain and metadata remain separate gates.
- Other rekordbox analysis is read-only outside the accepted October27 grid API.
  `soundbox.rekordbox_grid_store.PilotGridStore` requires an explicitly accepted
  exact library/schema/version authority, bound per operation to the track/UUID,
  with rekordbox/agent closed. Only DAT/PQTZ and native EXT/PQT2 invalidation may
  change; preserve all other sections, cues/audio/XML/2EX. BPM double/half alone
  may change the four native djmdContent fields and local counter. Manual BPM
  requires separate manual_bpm_accepted authority; it additionally updates
  AnalysisUpdated and only the primary DAT contentFile hash/size/history, with
  the native +4 counter sequence. Its recovery binds that exact metadata row.
  Ambiguous manual-BPM terminal rows may use a bounded read-only PCM WAVE
  header proof in that worker: exact integral milliseconds within native PWV3
  bounds, stable canonical identity/framing, journaled and revalidated through
  publication/commit/recovery. Exact-EOF inclusion passed native reference
  October28; unsupported endpoints fail closed. No audio payload read or decode. Respect
  Analysis Lock, preserve unknown metadata, reject unverified tempo/sync states,
  and retain encrypted DB plus exact analysis before/after backups. Shared
  Tag/Cue/Grid lock/journal recovery validates the whole set before publication;
  unknown state blocks all domains. Desktop previews and grouped saves share one
  worker lane; loaded-deck clicks may queue during the initial source probe,
  and preview from loaded RAM rows immediately. Saving requires a matching
  verified source base; RAM previews grant no authority. Normal quit drains those
  captured intents. Live backups retain verified encrypted DB/WAL/integrity/hash
  capture; full semantic inventories remain offline proof work. Closed-app checks
  stay fresh at every guard boundary. No audio reload, waveform recook or transport wait. Derived V5
  grid updates stay in SoundBox AppData. Intelligent creation has its separate
  accepted domain above; existing intelligent edits remain outside grid authority.
  See `docs/rekordbox-prep.md` for exact protocol and limits.
  Historical No Trust primary-PQTZ restores were explicitly granted only for its
  verified native-test baseline with closed rekordbox and native-saved 90 BPM;
  preserve those receipts and native counters/history. That grant does not
  authorize restoring another track or any secondary analysis cache.
- Ordinary reads, playback and exporters preserve source audio. Only the
  explicitly authorized recoverable conversion and confirmed Delete domains
  below may replace or move desktop originals; neither grants general cleanup.
  November1 separate Graveyard exception: the user requested an actual confirmed
  Collection-and-disk Delete button and will choose the targets himself. Only
  `rekordbox_removal_store` with its separate accepted library/schema/version
  authority, exact prepared selection/path confirmation, common source worker/
  lock/journal and recoverable macOS Trash may remove those originals. Never
  agent-delete real music during implementation/testing. Preserve files referenced
  by unselected native entries and state this in the review; keep unresolved paths
  instead of guessing. Keep SoundBox curation/analysis and exact coordinated native
  recovery. No permanent unlink, Trash emptying, table purge or general orphan
  cleanup is authorized. Existing exporter/source-immutability rules remain.

  November1 explicit import-only exception: Sonny requested automatic float32
  WAVE to24-bit integer WAVE at the same path, auditioned original/clipped/
  reduced copies and selected same-gain clipping. Only new/unregistered desktop
  imports may use `soundbox.wave_conversion`, on the shared source lane/lock,
  with exact recoverable originals and verified atomic replacement. Keep sample
  rate/frame timing, metadata, stable IDs, grids/key/cues and origin provenance.
  This was the original import-only scope. Sonny subsequently authorized checking
  the entire existing Collection for original CDJ-2000/CDJ-3000 compatibility and
  recoverable replacement in place (November1). Two existing WAV audio references
  are now converted with exact originals retained: Kaivon66027819 (24/44.1) and
  Ichor9368146 (24/48). No source SQL, cues or analysis changed. Native Reload Tags/
  load/normal-quit acceptance passed: actual headers are read correctly while
  native cached technical columns stay stale. No existing-record technical-column
  rewrite is granted; do not borrow registration or initial-analysis authority.
  A separate accepted file-only conversion authority and durable controller are
  implemented for same-path existing WAVE and unregistered lossless→WAVE; no
  native SQL/analysis/cue writes belong to this domain. Actual header facts may
  refresh epoch-bound technical display/USB DTOs, never musical fields. The
  installed automatic batch completed124 WAV conversions with originals retained;
  deferred/unavailable files and the32kHz MP3 error remain explicit in HANDOFF.
  November1 direct-locator correction: Sonny explicitly instructed SoundBox to
  replace the audio and point the existing DB records at the updated files,
  rejecting manual Relocate. This supersedes the pending chooser workflow.
  The separate `rekordbox_locator_store` column-restricted domain now preserves
  existing IDs/UUIDs, BPM/key, cues, analysis, playlists/history/tags and XML.
  Its authority is independent of bare registration. The two named FLAC records
  (12592463/138854425) now point to their verified same-folder WAV replacements
  through this narrowly authorized source transaction. Encrypted before/after
  pairs, a shared recovery journal and verified originals remain. No whole-DB
  restore occurred. Native reopen/load/normal-quit acceptance now preserves both
  entire target rows, packets, XML and all non-registry tables; separate general
  locator authority is provisioned and installed. Source integration automatically
  finishes file conversion plus locator save on the common source worker; no
  manual Relocate step is part of that workflow.
  Device originals and all exporters remain immutable. No automatic gain reduction or
  agent-performed Graveyard deletion is granted.
- Every export write lands **under an explicit device target**, guarded so the
  target can never overlap the source library.
- `soundbox.stock_ipod` is the only stock-firmware path allowed below
  `iPod_Control`. Following Sonny’s explicit November17 instruction, writer admission
  uses a writable FAT32 mount and the observed uncompressed little-endian
  iTunesDB version25or117/checksum0 (Video) or version117/HASH58 (Classic),
  independently of remembered model or device UUID permissions. Classic signing
  requires the actual per-BSD-mount USB/FireWire ID, verification of the captured
  HASH58 and any existing HASH72, then regenerated HASH72 before final HASH58
  after every render patch. Never copy an old signature, downgrade a version,
  consult a global device, or write device HashInfo/SysInfo to gain admission.
  Unknown database formats remain read-only. Newly
  copied stock media remains validated MP3 or stereo PCM16/44.1-kHz WAV. The
  pinned Clickwheel database engine is only an offline renderer; validate the
  rendered database and every artwork link, saves recovery material, copies
  media/artwork safely, and commits `iTunesDB` last. A disposable Mac-side
  `stock_ipod_state/` shadow may accelerate only an exact matching library
  identity/database/artwork baseline; mismatch always falls back to full
  reconciliation. A valid shadow can contain only playlist-scoped source
  mappings; missing selected sources must be narrowly content-reconciled against
  live media, never assumed new. Curated baseline JPEGs remain canonical;
  `stock_artwork/` under AppData stores disposable, content-addressed RGB565
  derivatives in separate Video/Classic profile namespaces. Artwork admission separately requires the observed compatible
  Video1028/1029 or Classic1055/1060/1061 descriptors (including the observed
  55-pixel canvas/56-pixel stride variant), or actual compatible observed USB
  family; never infer it
  from a saved model permission. Missing artwork capability must not block
  supported metadata updates. Explicit selected stock-playlist Sync recognizes
  a unique normalized name/count, then keeps a source/native-UUID-to-device-
  playlist-ID binding across changed count/order/name. Cached song links permit
  title, artist, album, genre, comments, year and non-estimated integer BPM updates
  on existing indexed containers without opening their media. Reuse retained
  audio, copy only proven new supported sources, preserve foreign files/master/
  other playlists/history, and report ambiguous memberships without replacing
  them. This disposable catalog witness is not media ownership or content proof
  and cannot relax recovery or atomic small-database publication. Stock catalog
  Sync uses current durable displayed values without waiting for an unrelated
  native migration backlog. New iPod exports use the durable selected-generation
  checkpoint above rather than the global metadata drain. Dual catalog
  Sync changes metadata/art only, preserving composite manifest bytes/absence.
  Incremental art writes may touch only currently
  unreferenced fixed-size ithmb slots; pixel-identical free slots are relinked
  without a write before publishing ArtworkDB and iTunesDB. Never embed art into
  or otherwise retag source audio. Rockbox export may remove embedded pictures
  only from a verified disposable Mac-side payload. Composite payloads also
  receive the effective SoundBox title/artist so Rockbox never falls back to a
  scrambled stock filename. WAV copies use Rockbox's native RIFF
  `LIST/INFO` `INAM`/`IART` fields rather than ID3-in-WAV. Those display fields
  live in a small canonical INFO list immediately after `fmt`; competing source
  INFO/ID3/BWF/XMP metadata containers are removed only from the disposable
  derivative so vendor metadata cannot override or hide the intended display.
  Audio essence and technical properties remain verified unchanged. Effective
  title/artist is part of the composite playlist baseline, so either edit must
  surface Sync needed. The container audio essence and technical
  properties must remain identical, and the canonical source remains
  byte-identical. Full-library planning must never retain the complete set of
  transformed audio: it records an exact source/output hash contract and
  discards each derivative immediately. Commit rematerializes, verifies, copies,
  and discards one track at a time so Mac-side staging is bounded by the largest
  individual payload. Managed
  Rockbox covers are per-track uncompressed 8-bit paletted BMP derivatives:
  exact-size 160×160 and 116×116 fast paths, bounded exact square sizes read
  from the active WPS/SBS and retained across later theme changes, plus a
  lightweight dimensionless 160×160 fallback. The curated JPEG cache remains
  canonical and stock artwork is untouched. SoundBox never writes Rockbox firmware
  preferences during sync, including
  `.rockbox/config.cfg` and its `.new`/`.old` companions. Database auto-update,
  playlist capacity, tag display, theme and all other settings remain user-owned;
  they never independently signal managed-library drift or invalidate a plan.
  Active theme dimensions may be read to size exported artwork. Rockbox
  remains the only writer of `database_*.tcd`; SoundBox never manufactures or
  directly edits the binary tag cache. SoundBox may report that the index
  predates its latest manifest/config generation so an enabled updater awaiting
  Rockbox restart is not mistaken for a clean cache. The connected layout selects
  the exporter:
  stock-only uses the guarded stock planner; Rockbox-only uses the Rockbox
  filesystem planner; generic FAT32/exFAT USB uses the dedicated CDJ boundary
  below, not the filesystem exporter. A writable FAT32 iPod with the observed
  stock database profile and both `.rockbox/` and
  `iPod_Control/` uses only `soundbox.dual_ipod`, never two exporters. The
  first composite generation requires no existing SoundBox device manifest,
  builds one no-target-write plan, then commits under the explicit Export/Sync
  gesture without another approval dialog. It is single-copy:
  stock-safe verified payloads live under `iPod_Control/Music`, enter iTunesDB,
  and are referenced by Rockbox playlists; stock-unsafe Rockbox-supported files
  live only under `/Music`; unsupported-by-both media is omitted and reported.
  Distinct canonical source paths retain distinct iTunesDB records, metadata,
  artwork, and device paths even when their audio bytes match; repeated playlist
  references to the same source path share its one record.
  For MA446, stock-safe means validated MP3 or stereo PCM16/44.1-kHz WAV; that
  restriction applies only to the iTunesDB-visible subset. Rockbox external art
  for every audio file is its sibling `filename.160x160.bmp` and
  `filename.116x116.bmp`, plus theme-independent `filename.bmp`, preventing
  folder-level art collisions. Rockbox files land
  before the recovery-first stock transaction, playlists follow the validated
  stock commit, and the unified schema-3 ownership manifest publishes last; any
  later-phase failure coordinates stock rollback with restoration of managed
  Rockbox rewrites and removal of only new files. For schema-3 managed audio,
  normal planning trusts unchanged manifest inventory; affected audio paths
  require live ownership/path/size checks without streaming their old payload.
  The exact changed derivative is verified on the Mac, the old live
  file is renamed within the device only for transaction rollback, and the
  replacement is atomically copied and verified one track at a time. Successful
  commits discard those transient old managed-audio bytes. Unowned paths remain
  untouched except for interrupted-generation recovery: a recovery directory
  bound to the still-current manifest may authorize adoption only when the
  freshly planned path, size, and SHA-256 match exactly. Interrupted candidates require fresh whole-file hashes on each adoption/preflight;
  FAT epochs and edge hashes cannot refresh an old proof. Historical Mac-side
  proof journals remain retained, but no new per-file proof JSON is written. Later playlist drops accumulate through new schema-3 generations,
  reuse exact managed payloads, source-authoritative repairs, and become true
  no-ops when unchanged. Composite iPod Sync also recognizes append-only edits that
  Rockbox made to an owned M3U8. The frequent device poll uses only the bounded
  playlist-size baseline to surface the standard Sync-needed state; confirmed
  planning reads the changed small M3U8 and accepts only device paths that map
  unambiguously to existing schema-3 managed media. A genuinely new membership
  is persisted only into a SoundBox-owned custom playlist, then reconciled
  into both firmware views. Rekordbox playlists do not adopt iPod additions;
  device reconciliation never writes rekordbox. Exact hash-verified historical M3U8 prefixes
  bind device paths to stable track IDs even after local source removal or
  relocation. The planning view may rebind those proven IDs to their current
  canonical Mac sources while retaining original device ownership/recovery
  baselines. Reusing a moved source's derivative requires matching source
  SHA-256; a metadata change during cloud hydration permits one full re-hash
  against a new stable stat baseline, never acceptance of the old digest with
  unverified new timestamps. Repeated change fails closed. Completed source
  proofs may persist only in disposable SoundBox state and are reusable only
  for the same canonical path, filesystem/inode identity, size, mtime, and
  ctime. Normal composite Sync treats the committed manifest as its inventory:
  unchanged canonical path, size/mtime, effective display and media policy retain
  the original source/payload contract across ctime-only drift, for local and
  cloud files alike. This is not a fresh integrity proof and never refreshes
  stored proof timestamps. New, relocated or changed sources retain exact-byte
  verification; preflight may refresh their changed timestamps only after exact
  source SHA-256 equality against a stable baseline. Unchanged verified stock
  payloads reuse compatibility and DBID/path identity; live-size/ownership checks
  run for affected paths and explicit Repair, not every retained file. Changed bytes use
  normal verified replacement. Multiple old copies
  may converge only when their recorded verified source hashes are identical
  and their current canonical source is one file; surplus copies remain under
  ordinary reference-counted cleanup. Custom playlist reconciliation retains the first occurrence of
  each track, canonical source, or byte-identical source file, including older
  duplicates; distinct recordings are never merged by title/artist. Membership
  is baseline-checked and saved before device writes, and M3U8 changes are
  recovery-backed. Unknown/unowned paths are never imported
  automatically, and deletion/reorder edits fail closed. The proven app-side
  membership is saved before device writes so a failed device phase remains an
  honest retry. The common iPod Sync action
  must route schema 3 back through that same composite planner: stable playlist
  IDs drive rename/move/order/membership/removal drift, old Rockbox playlist
  files are deleted only with exact recovery, and matching iTunesDB playlists
  reconcile in the same transaction. Sync reference-counts the complete managed
  playlist set: a track removed from SoundBox is pruned only when no remaining
  playlist uses it. Schema-3 media cleanup candidates require manifest
  ownership/path/size; art and playlists being rewritten or deleted retain
  exact live-byte checks, while unchanged retained artwork may reuse its
  matching manifest/cache identity without rereading device bytes. Each
  orphan is renamed into transient same-volume transaction recovery before the
  manifest publishes last, restored byte-for-byte if any later phase fails,
  and discarded only after success. Cleanup never depends on regenerating an
  older media or artwork profile from its source. Shared stock
  records additionally survive any remaining ordinary stock playlist reference.
  The top-level `/PIONEER` tree is reserved for rekordbox/CDJ. Ordinary stock,
  dual and Rockbox/filesystem code may include its bytes only in whole-volume
  capacity; it must not claim, scan, verify, clean, repair, import, edit, delete
  or write below it. Case aliases and symlink redirects fail closed. The dedicated
  `soundbox.cdj_usb`, `cdj_source`, `cdj_export` and `cdj_transaction` boundary
  is the scoped exception authorized October28/29: generic writable FAT32/exFAT
  USBs, explicit mount/volume identity and source separation, bounded metadata
  reads, off-device catalog preparation and manifest-last publication. Display
  reads may include stable WAL pages in a local copy, never SHM/live USB SQL;
  this grants no publication authority over active SQLite companions. Playlist
  drops, Export and explicit Save to USB authorize publication without
  another prepared-plan question. Preserve current native collections/playlists
  across either exporter's writes. Never treat a changed native catalog alone as
  an ownership conflict or erase it. November4 selected-export policy supersedes
  the old blanket collection-retention rule: replacing a selected playlist removes
  its old memberships and prunes those removed tracks only when no remaining USB
  playlist in either native catalog uses them. Shared songs/assets and unrelated
  collection-only records stay. Retire exact removed-media rollback bytes after
  durable commit so cleanup frees USB space; restore them on pre-commit failure.
  No whole-drive orphan sweep is authorized. Bind drag intent through
  deferrals to mount/UUID. New/changed audio retains exact-byte verification;
  retained native inventory needs no media audit. Internal recovery resumes
  incomplete work; a later coherent native export supersedes older pending
  catalogs without rollback over the new export. Local receipts remain required.
  No stock/Rockbox markers or hidden `.PIONEER` layouts are admitted. Unknown
  source/schema/cue states and redirects fail closed. Browser cache and player
  overlays confer no authority. Source tag/cue/grid, native-playlist, registration,
  local/native analysis, tag-definition and metadata intentions drain before
  capture. Frozen source-truth work and unresolved save attention retain the
  barrier for generic USB. Stock/dual/Rockbox iPod commands use their separate
  durable selected-generation checkpoint and rebind waiting native playlist
  selections by source/ID/UUID before device writes.
  USB-song playback validates actual device audio/analysis in the read lane and
  uses a separate track ID without desktop source-edit authority.
  Overview/Playlists/Category/Sort/Column/Color/My Settings share the USB header.
  My Settings has44 recognized player/mixer controls, a SoundBox-global logical
  profile seeded from the active USB, and explicit Save to Global / Load Global.
  Existing opaque bytes survive edits; global changes do not auto-propagate.
  The USB menu is Export, Import My Settings, Reformat USB, Eject. Export selects
  playlists on the left and connected devices on the right; one action queues
  checked targets through the existing serialized device lane. Capture source
  binding and each target mount/UUID, rebind selected playlist IDs before each
  target, and cancel remaining targets on request/shutdown. Each target retains
  its exporter-specific source, identity and recovery guards; normal iPod Export
  adds no second plan review.
  Reformat requires concrete destructive confirmation, real physical USB identity,
  source separation and FAT32/exFAT MBR; stock/dual never expose it. HFS+ remains
  excluded. Ordinary iPod menu/erase rules are unchanged. See the CDJ guide.
  Finder-created `._*` AppleDouble files and abandoned `*.sbpart` temporaries
  below `/Music` or `/Playlists` are exact-baseline cleanup rows during explicit
  Repair or journal-backed interrupted recovery; successful cleanup removes only
  now-empty child directories. Normal Sync/polling never walks these trees just
  to find clutter. Finder sidecars never independently signal drift. Recovery
  journals signal interrupted work; unjournaled `*.sbpart` files are discovered
  by explicit Verify/Repair.
  An empty schema-3 library remains plannable so cleanup can never become
  unreachable after the last playlist is deleted.
  Schema-2 migration is not enabled. The old Rockbox exporter must still fail
  closed on schema 3. Never approximate dual boot with independent exporters or
  a duplicate collection.
  Rockbox's Playlist Catalogue sees only direct playlist files under
  `/Playlists` and sorts them alphabetically. Composite sync must therefore keep
  every managed M3U8 at that top level and prefix only its Rockbox filename with
  a zero-padded manifest sequence (`01 - NAME.m3u8`). SoundBox rows and stock
  iTunesDB titles use the unique concise playlist leaf. New normalized duplicate
  playlist names are rejected; folder context is not added to managed titles.
  The manifest retains the full hierarchy, and foreign native playlists remain
  outside managed rename/cleanup scope.
- Stock playlist deletion may remove regular user playlists and the exact Apple
  **Videos** smart index only. Apple databases may mirror that index in the
  ordinary MHLP and canonical MHSD-5 datasets under different IDs; the device
  UI must select the canonical ID and deletion must remove both matching
  representations as one zero-media operation. Music, Movies, TV Shows, and
  Audiobooks remain immutable. On-The-Go is firmware-generated, not an iTunesDB
  playlist. Before a dual device has schema-3 ownership, the same guarded UI may
  delete those stock playlists with the same reference-counted cleanup: each
  track record unused by every remaining user playlist is removed from iTunesDB,
  and its linked device media is deleted only after the database commit. Source
  audio remains immutable; unowned Rockbox paths remain outside that transaction.
- `soundbox.stock_ipod_info` may parse stock identity, tracks, playlists, and
  playback state for the device UI, but is strictly read-only. Remembered model
  display facts are keyed by library persistent ID + volume UUID and grant no
  write permission; the ordinary
  iTunesDB `DatabaseID` is rewrite-volatile and must never identify hardware.
  On macOS, hardware serial/firmware may come from Apple's read-only classic-iPod
  device record and USB/SCSI facts from IORegistry. Serial-derived model and
  model-derived DAC must retain provenance in the immutable snapshot, though the
  dashboard may show the concise component name; a SCSI transport revision is
  never firmware. Ambiguous device records and model mappings fail closed.
- Dry-run by default where the workflow supports it; ask before anything
  irreversible; never bulk-delete or orphan-clean without an explicit target
  and confirmation. Do not assume paths—resolve and verify them.
- Device activity and library integrity are separate. An idle device may
  always request normal macOS eject without Sync, Verify, or Deep Verify.
  Never force-unmount automatically. Device worker ownership lasts through
  QThread.finished; cancellation clears pending work and stops active work at
  a safe boundary. Shutdown must keep Qt events alive until workers stop;
  an expired timed wait never permits destroying a worker.
- Canonical audio acquisition captures one local snapshot while hashing. An
  expected digest binds captured bytes, so exact bytes may survive cloud
  eviction or timestamp drift before capture. Every capture, with or without a
  digest, requires stable before/opened/closed/after identity and a canonical path,
  with at most one instability retry. macOS CloudStorage reads returning ENEEDAUTH may
  restart from byte zero after at most two cancellable three-second waits;
  partial reads never publish proofs or snapshots. Persistent errors still
  fail closed with source context. This readiness budget is separate from
  the single unstable-capture retry. Inspection/transformation/copy use local captured
  bytes; retain at most one source and working derivative. Disposable receipts
  and source proofs never confer device ownership. Preserve generation-58
  recovery until separately reviewed hardware acceptance succeeds.
- `library.json` and its referenced `.state-records/` are crown-jewel user state.
  The versioned manifest is not a standalone portable snapshot. Use `read_state`
  for logical reads and `portable_state` under the shared area lock for complete
  backups; preserve exact physical manifest/records too before migration or
  installation. Legacy full JSON remains readable; successful heavy-record saves
  migrate through the existing journal. Ordered changed-record publication,
  exact incoming comparisons, fresh record epochs, the64MiB logical ceiling and
  newest-draft/restore fencing remain required. Rotating/full backup exports are
  complete standalone JSON; older apps require that portable form, never a bare
  manifest. Corrupt-live-state restore retains damaged evidence and only retires
  verified old record references after successful publication.
  Never wipe AppData or trigger a
  full cache rebuild casually. Back it up before installation or risky state
  work and verify curated data afterward.
- Full per-write export contract lives at the top of `soundbox/sync.py`.


Explicit composite Repair plans missing manifest-owned Rockbox audio and per-track
artwork as verified new copies when the guarded destination is absent. Normal
Sync trusts retained manifest inventory and does not look for missing files.
Existing occupants, symlinks,
and inaccessible paths are not treated as missing; commit rechecks vacancy and
rollback is intended to retire only transaction-created copies. Acknowledged-copy
rollback still has the replacement-ownership gap recorded in the
[critical audit](critical-audit-2026-11.md); these guards do not establish safe
disposal of a foreign replacement. A fully reconciled stock
library with no semantic changes retains its original iTunesDB bytes even when
the disposable stock shadow is absent.


The bounded composite drift poll treats source size/mtime changes as Sync-needed
hints, but not ctime alone: cloud eviction/hydration and filesystem metadata edits
can change ctime immediately after a successful commit. This UI status is not a
content-integrity certificate. Source proofs/receipts still include ctime.
Normal composite Sync also retains unchanged committed contracts across
ctime-only drift, without renewing their
original proof or manifest timestamps. New/relocated/changed sources and actual
payload acquisition retain exact-byte verification. Verify checks device
references/sizes; Deep Verify also hashes managed files; Repair explicitly audits
inventory and restores missing owned Rockbox payloads/art. None of these modes
turns a clean badge into a fresh source-content integrity certificate.

Connection presentation separates visibility from readiness. Debounced `/Volumes`
notifications and a 250-ms cached kernel mount-table check request discovery even
during playback or an eject-ready hold. The latter uses Darwin
`getfsstat64(MNT_NOWAIT)` without opening mounted paths, so mounting over an
existing directory cannot strand detection behind probe backoff. These are hints,
never external-device/write authority. A busy lane retains the request and retries
on worker retirement. The three-second local `/Volumes` timestamp fallback remains;
unchanged mount/timestamp state starts no idle discovery worker. Failed probes
retain 3–30-second backoff, bypassed by mount changes. Only an OS-identified iPod
with a provisional generic-USB profile rechecks classification while its firmware
folders are appearing. Ordinary generic USBs gain no idle profile probes/retries;
this never grants filesystem/model/database write capabilities. Connected stock/Rockbox drift stays on its
three-second poll; generic USBs have no idle source/catalog comparison. Ordinary unmounted folders retain the 30-second attachment grace.
Verified external mounts appear disabled as `NAME · Checking…` during physical
identification. Generic USB rows show name, USB type and total capacity, with
available space and warnings in the tooltip; they have no Sync-needed,
pending-change or source/catalog comparison. Their native browser snapshot loads only on request,
with no idle metadata-stamp reads. Stock/Rockbox mounts retain their independent
sync drift before publishing the selectable row at QThread.finished; their
unknown/failed checks say `Sync status pending`, never imply a clean result.
Stock/dual dashboard preload follows in the same serialized device lane, with
loading only in the bottom status after the row becomes ready. Successful preload
clears that status directly, without another immediate drift scan. A cached
dashboard is presentation-only; selecting while it loads uses the same serialized
preload. Writes stay guarded by operation ownership and firmware capabilities.
Remounts invalidate stale views.


Playback and iPod management are independent. Device discovery and ordinary
status polling must not check player.is_playing as a suppression condition.
Only device operation ownership pauses competing device reads/writes. Reconnect,
Rockbox tag-cache updates, unrelated firmware preferences and ctime-only cloud
metadata changes do not themselves create managed-library Sync drift.

Rockbox 4.0 does not support `sort playlists`; never add or require that key.
Its absence after firmware saves preferences is not Sync drift. Preserve existing
unknown config lines; retain the established numbered playlist filenames.

October29/30 installed detection stage (see HANDOFF for current provenance): the track-menu
button is immediately right of Q, with the
existing equal flexible toolbar gaps. It offers waveform color, three resident
beat-count modes, standalone collection-song CDJ export and Analyze Track.
Explicit detected BPM/Grid+Key results persist in backed-up library.json with
path/audio digest/exact identity and can follow a later native track ID. Native
and manual grids/source cues remain authoritative. Sonny requires analysis shared
both ways independent of which app imports first. Initial supported full WAV
analysis/Key publication now uses its independently accepted authority; broader
profiles remain gated and never borrow the manual-grid API. Ordinary native playlist management now has its own
October30 authority after11 exact native DB/XML/rollback replays and12 actual
source CRUD operations followed by Sonny's native inspection/normal save. The
current permission is separate from the earlier exact-plan Bridge Pilot approval
and the two specifically approved synthetic NEW-row reference seeds. Those old
receipts never authorize arbitrary future source tests or changing existing
sync flags. Preserve compact native reference/approval/recovery receipts. Full payloads
remain only for current proof, unresolved acceptance or runtime recovery under
the November10 bounded-retention rule.
Current implementation/installed status lives in HANDOFF; the broader scope and
remaining analysis-format/replacement gates are in docs/analysis-playlists-next.md.

October30 upstream audit uses pinned rbxport b739d369 (also current upstream at
audit time). Explicit detection defaults to its adaptive/high-precision pipeline,
with a bounded thirty-minute/96M-sample head and a full streamed tail check.
Legacy constant-v1 results remain valid; component provenance survives partial or
inconclusive reanalysis. Native/manual grids and existing cues stay authoritative;
this grants no new source analysis-write permission. At the October30 checkpoint,
the3BAND overview kept seven PWV7 layer-overlap colors. The November21 dedicated
PWV6 stacked mini supersedes that policy. Current main3BAND retains its own PWV7
contours at gain1.35 with joint encoded-region coverage; BLUE shares RGB geometry
while keeping its own palette. Protect RGB output and unrelated mode behavior
with before/after native drawables. See PLAYER for current preview/readiness rules
and the dated reference audit for historical values and validation limits.

The sidebar uses one Playlists tree and one scrollbar, plus Devices. Its shared
left column resizes horizontally without hiding either pane; section heights
remain independently resizable. Save `settings.sidebar_width` through the
ordinary debounced preference lane and restore it after window geometry.
Narrow rows elide text; resizing never changes full names or device identity.
Existing local organization remains in `settings.custom_playlist_tree` and `custom` as
preserved migration input. Explicit `settings.playlist_unification` binds the
requested merge to one source/library and records exact created native identities
alongside the existing persisted FIFOs. Register visible old imports first; create
custom folders/playlists with exact order/duplicates, then cut over their display
only after verified readback. Artists remains a virtual SoundBox grouping of
Collection; it creates no native folder, per-artist playlist or maintenance job.
Native names alone never authorize adoption. Unresolved identities, source changes
and unknown recovery retain the intentions and fail closed. No deletion/restoration
of user music belongs to migration.
Original custom device keys remain export bindings after native adoption; native
playlists never adopt device-side additions or publish them back to rekordbox.
This is a one-time internal upgrade, not a release-user workflow. No merge command
belongs in the UI. Fresh native libraries create no migration state or artist
organization job; source selection alone never activates old-import/custom
backfill. New playlists/folders use the existing accepted native writer. Legacy local-only
sources retain local folder handling. Folder clicks expand/collapse without changing
the active track view. Up/Down after playlist selection browse its songs.
November21 ordering requirement: only Collection and smart playlists default
newest first; ordinary native/XML/local playlists retain authored occurrence
order, including repeated songs. Explicit header sorts remain view-only, and
clearing the sort restores authored order. Manual native song movement must
persist through the shared accepted membership writer, not a local display sort
or sidebar node-order authority. Preserve hidden occurrences, duplicate selection,
inline drafts, removal masks and complete queue/recovery ownership.
New verified My Tag assignments promote matching smart-playlist browser members
by locally persisted recency, bound to exact source/library, track/native UUID,
playlist UUID and rule hash. This is display-only, never native rule/membership or
export ordering authority. User header sorts override the default; removals,
no-ops and failed writes promote nothing. Preserve newest selection on readback.
Ordinary membership ACK may retire a local removal overlay only when its exact
value and playlist/track identities were durably captured with that job. Preserve
newer values and legacy jobs without proof; failed saves restore prior owners.
This proof never broadens native SQL or represents smart-playlist exclusions.

## Documentation map

| Doc | What's in it |
|-----|--------------|
| `docs/ipod-lifecycle-audit-2026-09.md` | Lifecycle inventory, failure matrix, implementation and acceptance boundaries |
| `docs/rockbox-playlist-oom-2026-09.md` | Read-only USB findings, firmware comparison and pending runtime OOM isolation |
| `docs/HANDOFF.md` | Single current session-resume note: live state, unfinished work, exact next checks, validation, and rollback facts |
| `docs/PROJECT_LAYOUT.md` | Checkout contents, external evidence archive, disposable outputs and retained fixtures |
| `docs/ROADMAP.md` | Active sequence, acceptance gates, public release and deferred work |
| `docs/sync-performance-audit-2026-09.md` | Composite inventory reuse, source-read counters and physical acceptance |
| `docs/archive/repeated-work-audit-2026-09.md` | Completed non-USB optimizations and before/after evidence |
| `docs/software-audit-2026-09.md` | Architecture, lifecycle, persistence and performance findings with evidence |
| `docs/release-boundaries.md` | Public/personal scope, provider contract and distribution boundaries |
| `docs/KEYBOARD.md` | Keymap editor, action catalog, default numeric cues and held-key routing |
| `docs/soundcloud-connection.md` | Personal OAuth/Keychain setup, likes index and acceptance boundary |
| `docs/PLAYER.md` | Player behavior, native architecture, cache formats, cue/transport/render invariants, QA references, and deferred grid work |
| `docs/archive/waveform-reuse-audit-2026-09.md` | Rekordbox-first waveform implementation, storage audit, diagnostic previews and remaining acceptance |
| `docs/DEVICES.md` | Rockbox/stock/dual-boot/generic layouts, capability boundaries, and validation |
| `docs/ARCHITECTURE.md` | Engine/desktop/native layout, data model, persistence, build/install |
| `docs/GOTCHAS.md` | Hard-won Qt/QThread/native-player/rekordbox/Rockbox traps |
| `docs/testing-runtime-audit.md` | Complete test timing, isolation and three-worker gate |
| `docs/cloud-library-audit.md` | Native/SoundBox/SeaDrive identity, cache and hydration friction |
| `docs/performance-audit.md` | Measured UI/background work and remaining performance costs |
| `docs/native-playlist-order.md` | Persisted reorder proof and independent acceptance boundary |
| `docs/IDEAS.md` | Exploratory quality-of-life concepts that require UX workshop before commitment |
| `docs/cdj-usb-export.md` | CDJ USB R&D and `research/cdj_export/` status |
| `docs/archive/storage-audit-2026-07.md` | Resolved storage audit; full backups remain deliberate |
| `README.md` | User/dev product tour |

Read `docs/GOTCHAS.md` before touching workers, playlists, sync, removable
devices, caching, or the player. Read `docs/PLAYER.md` before changing waveform
rendering, clocks, transport behavior, analysis loading, or native code.

## Documentation discipline

- Documentation is part of the change. Update relevant docs before ending a
  session when behavior, architecture, invariants, setup, or status changed.
- Put committed/sequenced work in `docs/ROADMAP.md`. Put uncommitted concepts
  and UX explorations in `docs/IDEAS.md`.
- Keep shipped behavior, future work, and historical validation clearly
  separated. Never leave a completed feature presented as a TODO.
- On every **wrap up**, replace/update `docs/HANDOFF.md` with the single current
  handoff. Keep it concise and operational rather than appending a session
  diary: current branch/commit, installed/runtime state, unfinished work,
  exact next checks, validation performed, and any rollback that must remain.
  Remove facts that are no longer current.
- **“Wrap up” is a completion command, not a progress report.** Finish every
  in-scope pending task that can be completed in the current session, complete
  the repository-wide documentation audit below, run the required final
  validation, and—when the work is user-facing—build, safely install, relaunch,
  and verify the real curated runtime state. Complete authorized cleanup under
  the retention rules below, preserving pending-acceptance rollback bases.
  Then update `docs/HANDOFF.md`, commit and push the intended
  completed changes, and verify the worktree is clean and the current branch is
  published before saying the wrap-up is complete. Do not defer any of these
  steps for Sonny to request again. If a genuine blocker prevents completion,
  report the exact blocker and leave an operational handoff instead of claiming
  the session is wrapped. A complete wrap-up includes both the release closure
  and the full stale-documentation review; Sonny must not have to ask separately
  to check the rest of the docs afterward.
- **Repository-wide documentation audit (required on every wrap-up):**
  - Inventory all project documentation, including root instructions/README,
    `docs/`, native/research guides, archives and vendored references. Use the
    actual current inventory rather than a hard-coded file count. Include
    referenced setup/dependency manifests and license files where relevant.
  - Read every current first-party document in full and reconcile its claims
    against current implementation, delivered runtime and dated release evidence.
    Reviewing only changed files, keyword matches or links is insufficient.
    Correct stale behavior, commands, prerequisites, status, plans, build pointers,
    permissions, safety/authority boundaries and retention/recovery instructions.
    Search for contradictory copies throughout the inventory, including unchanged
    files, and distinguish shipped behavior from future work and historical proof.
  - Review every archive for historical scope, permission/restore/replay/cleanup
    hazards and links. Preserve dated evidence without treating old approvals or
    pilots as current authorization. Verify pinned upstream documents/licenses
    remain exact and clearly scoped; do not silently rewrite vendor material.
    Check documented dependency/toolchain claims against manifests and available
    evidence without making unrequested upgrades.
  - Keep a per-file review receipt covering unchanged documents too: review basis,
    depth and hashes, plus findings/fixes and explicit limitations. Current
    first-party full-content review, archive scope review and pinned-upstream
    integrity checks are different evidence; never claim a complete semantic,
    external-link, legal or hardware audit from a narrower check.
  - Validate local documentation links and anchors across the full inventory,
    instruction-file parity and the final diff. Fix first-party errors and record
    known omitted upstream targets explicitly. Finish corrections and record the
    audit scope/results in the current handoff before final publication.
  - Reuse prior release validation only when its exact source/test/build inputs
    remain unchanged; rerun the required checks when implementation changes.
    Documentation-only corrections do not justify app inputs, termination,
    rebuilding, reinstalling, restamping or device writes. Preserve newer user
    edits, protected originals/recovery and pending-acceptance rollback bases.
- **Wrap-up consistency gate (required before commit/push and final response):**
  - Review the complete intended diff, including work inherited from an earlier
    session. For each changed behavior, search the README, both runtime files,
    handoff, roadmap, architecture, device/player guidance as applicable, and
    gotchas for the old behavior as well as the new wording. Updating one current
    section does not fix contradictory instructions elsewhere. Correct current
    guidance; label historical evidence explicitly rather than rewriting history.
  - Check documentation claims against implementation and tests. Audit recovery,
    eject, worker ownership, source safety and acceptance claims especially;
    passing tests do not prove unobserved hardware behavior or complete a partial
    architecture migration. Keep outstanding work explicit in the handoff.
  - Check `AGENTS.md`/`CLAUDE.md` byte parity, relative Markdown links, and
    `git diff --check`. Verify developer harnesses do not read live device/source
    state merely because their AppData is isolated. Run the relevant render gate
    for UI/harness changes and inspect its output, in addition to `./check.sh`.
  - Record the actual final installed build/revision, launch verification and
    post-quit/postlaunch curated-state baseline. Never copy an intermediate
    timestamp, hash or build number into the final handoff. If state changed,
    inspect the exact differences before establishing a new baseline; preserve
    the earlier evidence and distinguish UI-setting serialization from curation.
  - Distinguish repository HEAD from the installed app revision. Documentation
    and developer-harness-only changes do not require reinstalling an unchanged
    application. State that distinction explicitly; do not stamp an older binary
    as a newer revision to satisfy a provenance check. User-facing/runtime changes
    still require the full safe build/install/relaunch procedure below.
  - Finish documentation before the release commit where possible. Installation
    evidence added afterward may be a documentation-only follow-up; retain the
    true installed source revision instead of creating a rebuild/documentation
    loop. Remove stale pending/uncommitted statements before publishing.
  - After push, verify the remote branch resolves to local HEAD and the intended
    worktree is clean. The final response must separate completed release work
    from remaining physical acceptance or implementation limitations. Never claim
    that everything is correct merely because the automated suite passed.
- Do not hard-code a "current" test count as evergreen truth; trust
  `./check.sh`. Dated validation records may include the observed count.

Waveform preparation is import-first for complete PWV3/PWV5/PWV7 streams plus grid.
Compact V5 stores one 1-ms signed-detail supplement and builds zoom mips in
memory; incomplete imports retain the full fallback analyzer. Old V5 remains
ready while eligible local records upgrade one at a time in background preparation
or on selected-track load. The selected-load worker keeps its already-valid surface while saving its own
upgrade for the next load. Separate preparation/reanalysis notifications retain
their targeted refresh behavior. First waveform publication waits
for complete geometry, while manifest/grid/cues remain immediate. Dataless sources
defer until local; never invalidate the whole library at startup. Preserve manual grid/cue
ownership and authoritative DB cues. MP3 source-origin changes must couple
analysis, audible playback, duration and seeks; never shift only the waveform.
Playback-origin header probes belong to the native decode thread; the bounded
manual-grid PCM endpoint proof uses the shared source worker. See `docs/PLAYER.md`.

## Development

### Focused local fixes — explicit November20 user requirement

Default small-fix validation is the relevant regression plus affected integration,
source-dispatch, lifecycle and error/recovery cases. Choose by the dependencies
changed, not a fixed test-count budget. Use existing meaningful coverage, relevant
lint/types, Cocoa for macOS appearance/input and an inspected render when useful.
A plain divider adjustment does not require unrelated encrypted crash matrices,
encoders or filesystem-image tests. Native writer/transaction changes still need
their affected authority, identity, journal/recovery and acknowledgement tests.
Broaden the run when scope or evidence makes the focused selection insufficient.

Named areas are selectable independently and composable with
`./check.sh --suite AREA... --level quick|integration|recovery`. Levels are
cumulative: fast behavior checks, adjacent integration/lifecycle, then affected
transaction/process-loss recovery. USB/CDJ and iPod are separate core areas;
library, RB DB, metadata, tags, playlists, prep, player, waveform, analysis, artwork,
state, UI, providers and tooling are also available. `--list-suites` lists them;
`--plan` previews selectors and Qt routing without importing tests or Qt. Named
runs use separate private processes, offscreen before Cocoa; native-deck selectors
route to Cocoa automatically. Combined areas and overlapping selectors execute
each selected ID once. A source writer/SQL/journal/ACK change requires affected
recovery coverage, not the quick RB reader smoke. See the maintained
[test catalog](test-release-gate.md#choose-an-area-and-depth) for selection guidance.

`./check.sh --focus SELECTOR... [--cocoa]` imports only the named test modules,
classes or methods in one private Qt-state process. It does not build unrelated
native/CDJ code or run global fixture preflight. Rebuild affected native code and
provision its required fixtures when that work is actually changed. Focused reports
have a separate path and explicit scope; full-release verification rejects them.
Every selected case must pass without skips/xfails. Do not repeat passing focused
runs unless code, failures or unresolved concerns justify another run.

Routine local installs can follow these focused checks, strict package/composition
and isolated runtime checks, then the existing state-preserving install/reopen
procedure. They do not require the complete suite after every small fix. Record
what was tested and leave full-suite validation pending explicitly. The complete
bare `./check.sh --render` remains mandatory before any commit/push or wrap-up,
on the intended final source/test/build inputs; its full inventory, both Qt phases,
zero-skip rule and exact report reconciliation remain unchanged. Earlier full proof
is reusable only for unchanged covered inputs. Focused validation grants no native
source authority, real-app control, live-device tests or weaker state safeguards.
Developer-tooling/documentation-only changes do not require reinstalling the app.


Background local waveform/BPM/key phases keep per-song dependency order. After a
joined phase and durable acknowledgement, one eligible source task gets a turn
before local work resumes. Source domains rotate across their existing heads,
without reordering each FIFO or bypassing same-song/native receipt prerequisites.
Only the selected synchronous opportunity may pass deferred cue/grid logical
holds; physical ownership, prepared-prep/recovery reservations, closed-native-app
guards and exact authorities remain. This bounds eligible admissions by phase,
not wall time. Interactive Tags capture selected gestures into an immediate RAM
FIFO while DSP or earlier saves finish. Sparse canonical acknowledgement precedes
native dispatch; checkbox previews grant no source authority. Exact native
readback and durable head acknowledgement preserve newer queued gestures.
Metadata uses the same fair source lane
with its own durable intentions and independent authority. Refresh/export/shutdown
keep full ownership. These audit changes are source-validated; HANDOFF records
the installed app separately.

Prepared browser rows live only in disposable `browsercache.json`, bound to
complete display inputs and a versioned checksum. They never supply native edit
or device-export authority. Bump `soundbox.browser_cache.SCHEMA` whenever display
derivation semantics change. Invalidating rows must also retire their checkpoint
key; artwork policy/visibility and source authority remain independently fresh.

Desktop accent roles come from `desktop.theme`, persisted as `settings.theme_accent`.
Use those roles for UI chrome rather than fixed purple values. Semantic transport
feedback stays fixed: green PLAY and orange held CUE, with gray idle rings. The supplied SVG
wordmark stays in its 270×55 header slot; only BOX follows the accent. Waveform
and cue data colors retain their musical/source meaning. `desktop.theme_controller`
installs static QSS once, then changes only visible controls' accent declarations;
hidden controls catch up on Show. Color pickers preview coalesced live colors
without saving preferences; OK commits once and Cancel restores the saved theme.
Never reapply the global stylesheet for each color-wheel/text-color change.

Checkout hygiene and historical evidence locations: `docs/PROJECT_LAYOUT.md`.
Sonny closed waveform comparison work for now on October28 and authorized
removing its private diagnostic archive and obsolete experiment/test output.
`~/Library/Logs/Soundbox/development-archive-2026-10-28/` is deleted; old paths
there and historical build investigation paths no longer resolve. Small retained
release/state records are listed in PROJECT_LAYOUT. Do not resume visual tuning
unless Sonny asks; collect fresh references if it is reopened. Sonny has now
reopened targeted Gunslinger red-glisten work at RGB 576/864 and supplied a new
native recording. He accepted the combined RGB 576/864 motion/footprint/occupancy
comparison. Sonny has now reopened stability across all zoom levels. Wide
BLUE/RGB use conservative physical-pixel LOD and the middle stops' song-space
reconstruction; every mode/zoom integrates every lattice corner. November2 now
supersedes the historical zoom-dependent RGB color/occupancy corrections: evaluate
the supplied92pt/s palette on the song clock at every zoom. Geometry, footprints
and motion remain independent; do not reinstate per-zoom palette gains. Current release/install state and
fresh frozen phase comparisons are in HANDOFF. Sonny accepted the visuals on
November9,2026, then explicitly reopened main-lane mode geometry on November21
under the requirement above. Other appearance tuning still requires his request.
Device/firmware and source-format acceptance stays separate.
Mini RGB brightness matching retains the main RGB faint-only shoulder and
encoded-space blend. The November21 BLUE policy above supersedes its older
PWV3/linear-light palette. The later PWV6 stacked mini supersedes October30's
seven-overlap3BAND overview. Preserve required offline test inputs and small
acceptance/parity receipts; retire completed captures under the November23 policy. Active
`build/native`, `build/check`, required reference roots and native recovery stay
in place. Closure does not claim perfect native appearance or presentation cadence.
Current guidance stays in `docs/`; superseded experiments belong in
`docs/archive/` and never override the current handoff or roadmap.

    ./check.sh --focus tests/test_sidebar_layout.py  # local named regression checks
    ./check.sh --focus tests/test_prep_tags.py --cocoa  # selected macOS UI checks
    ./check.sh            # full pre-publication gate; lint/build/types + both Qt phases
    ./check.sh --render   # + hermetic offscreen UI render
    ./check.sh --native   # compatibility alias; Cocoa is always required
    ./build_native.sh     # arm64 deck + fresh process-guard dylibs
    ./build_cdj.sh        # pinned local Rust CDJ renderer; --test adds Rust checks
    ./build_app.sh        # personal → dist/personal/Soundbox.app, then stable signing

- The release gate routes every discovered test exactly once into three isolated
  offscreen workers followed by the serial Cocoa process, preserving module
  fixtures/order and reconciling exact worker/phase inventories. Skips, expected failures,
  missing fixtures and unrun tests fail the gate. Headless Metal oracles and
  native drawable/playback tests always run on the supported arm64 Mac.
  Required offline iPod, frozen waveform and CDJ snapshots are private, read-only
  inputs; see `docs/test-release-gate.md` for provisioning. Never read live
  devices/AppData or silently replace reference fixtures to make a gate pass.
- Run `./check.sh` **bare**—never pipe it through `tail`/`grep` in an `&&`
  chain because that can mask a failing gate.
- Python 3.14, pinned dependencies in `requirements.txt`, venv at `.venv/`.
- Native deck requires arm64 macOS 14+. Qt Multimedia is analysis-only
  (`QAudioDecoder` for AAC/ALAC); native-deck failure disables playback with a
  diagnostic while library management remains available.
- The offscreen render harness must suppress device discovery before creating
  MainWindow and throughout rendering; test-mode AppData alone does not isolate
  real removable mounts.
- Logs: `~/Library/Logs/Soundbox/soundbox.log`.
- Git uses local `main`, tracking `origin/main` on the Low Freq Labs GitHub
  repository. Inspect and preserve the dirty worktree. Do not commit or push
  unless Sonny explicitly asks. Sonny saying **“wrap up”** is standing explicit
  permission to run the final validation, commit the intended completed
  worktree changes, and push the current branch; never push a failing build.

## Build/install handoff

For user-facing app changes, a passing source tree is not the handoff.
“Completed” means the tested app has been built, safely installed, relaunched,
and verified against the real curated state unless Sonny explicitly asks for
source-only work. Keep rollback proportional: use one temporary rollback
directory for the install, not a new trail of persistent backups.
Keep at most one immediate previous app during installation. Sonny's November8
space-saving instruction rejects accumulated rollback generations; retire the
temporary app after accepted verification. This does not retire musical originals,
curation, transaction recovery or required offline fixtures.

1. For a small local fix, pass its focused/affected checks above; before
   commit/push or wrap-up, pass the complete bare `./check.sh --render`.
   Build with `./build_app.sh`, then verify the signed composition and isolated
   packaged runtime. Record whether source validation was focused or complete.
2. Back up and hash complete SoundBox state: preserve exact `library.json` plus
   referenced `.state-records/` and materialize portable JSON under the area lock.
   A manifest alone is insufficient. Record cache counts when relevant.
3. Quit SoundBox gracefully and verify its process exited.
4. Move the old `/Applications/Soundbox.app` to a temporary rollback directory;
   install with `ditto`. **Never use `rm -rf` for app installation.**
5. Verify the signature, relaunch, and confirm the native-deck startup log,
   library load, zero unnecessary preparation, and curated-state integrity.
   Verify fresh packages strictly before first launch. Saved-color Finder/Dock
   customization adds OS-owned Icon\r/Finder metadata outside Contents: ordinary
   deep verification and exact signed-Contents/identity checks must still pass.
   Strict metadata rejection alone is not an invalid signed payload; never strip
   the saved icon or re-sign a running app just to hide that distinction.

After the installed app passes verification and Sonny is up and running, remove
the temporary previous app, duplicate distributions/packaging output, completed
diagnostics, recordings, screenshots, copied databases and installation/migration
state snapshots as part of completing the update. Sonny's November23 instruction
is standing authorization for this cleanup; do not ask again or retain another
full archive under a different name. Keep only small install/result receipts.
Live curated state, original audio/conversions, required offline fixtures and
recovery for unfinished operations remain protected. Ordinary atomic saves and
in-flight transaction recovery are separate from completed diagnostic archives.

Moving/replacing the `.app` bundle must never touch
`~/Library/Application Support/LowFreqLabs/Soundbox/`.

## Diagnostic retention — November23 cleanup supersedes older archive retention

Once an updated build is verified and Sonny is up and running, completed diagnostic
payloads must go, including original/post-migration installation snapshots and
accepted visual captures. Retain only compact result/provenance receipts, never
whole state/DB/media copies just to substantiate a completed check. One temporary
previous app is allowed during installation and is retired at the same point.
This supersedes older instructions to keep those full payloads after delivery.
Do not recreate deleted snapshots or demand a new full capture for routine status
checks. Preserve the live library and referenced records, musical originals,
required offline test inputs and necessary recovery for unfinished operations;
this cleanup does not change runtime atomic publication or source-write guards.

The November10 and October31 rules below are historical context under this policy.

Sonny explicitly requested removing accumulated agent-created backups again on
November10. Completed diagnostic DB copies and old archival pilot snapshots may
be retired after their proof receipts are preserved; they are not permanent full
library archives. This supersedes older instructions to retain every completed
native acceptance payload. Keep current runtime recovery, unresolved/pending
acceptance, required offline fixtures, original music/conversions and the one
pending app rollback. Never alter current source data or restore old state during
cleanup. Ordinary writers retain their bounded, receipt-backed recovery rules.
Read-only status/reference captures keep compact row/XML/hash receipts by default;
full DB retention must have a concrete current proof/recovery need. Retire that
extra payload when the need ends. See HANDOFF for the completed cleanup and exact
private retirement manifests.

The October31 request below remains historical context:

Sonny explicitly requested cleanup of excessive backup/diagnostic storage and
extra apps/packaging. Completed redundant playlist diagnostic DB checkpoints and
obsolete app/build/scratch copies were retired; exact receipts are in HANDOFF.
Current recovery/acceptance archives, active shared-library references and required
fixtures remain protected. Keep future diagnostics lean: reuse existing snapshots
for inspections, retain full DB pairs only when acceptance/recovery requires them,
and remove generated packaging/scratch output after verified installation. Do not
create a new full-library capture for every status/UI check. Preserve small
comparison/approval/install receipts. An earlier retained delivery checkpoint had95 imports,1107 titles,490 artist
overrides,10 custom playlists,519 hidden tracks, eleven saved analyses,93 native
bindings, ten independent authorities and515 user disposal receipts. Current dated
counts are in HANDOFF; newer user actions supersede every checkpoint. Inspect
current state rather than restoring a checkpoint. Conversion
originals, required references and unresolved recovery remain protected.

## Private / out of scope

- The personal liveset/SoundCloud download pipeline is private and must never be
  documented or inferred in this repository.
- CDJ reference USB snapshots and large audio fixtures live in the local,
  git-ignored `soundbox-cdj-ref/` workspace at the project root; only prototype
  code under `research/cdj_export/` belongs in version control.
- Stock/Rockbox iPod captures and oracle-sync media live in the local,
  git-ignored `soundbox-ipod-ref/` workspace. Treat pristine captures as
  read-only research/recovery fixtures; never restore one to hardware without
  an explicit, separately reviewed recovery plan.

Artwork worker ownership also lasts through delivery of its `finished` callback.
Completion retires the captured instance, never whatever currently occupies a
worker attribute; shutdown callbacks cannot start replacement artwork passes.

Dashboard serial and stock-firmware facts may persist under the exact volume UUID
plus library persistent ID, with provenance. Fresh observations supersede stored
facts; conflicting serials discard the old association. Remembered facts are
presentation-only and never grant mount/write authority. A sole historical Apple
record without an exact device match is not sufficient identification. Recovery
counts and database digests stay in backend snapshots, not dashboard rows.

For stock/dual/Rockbox iPod sync, polling stays held through owned finalization
and any actual attention dialog. Routine completion is nonmodal and releases
the hold immediately so later edits can update iPod drift. Generic USB exports
never start sync polling or a completion read; only a deliberate USB/tab click
requests a fresh catalog view.

Artwork/suggestion dialogs and interactive waveform workers use
`desktop.task_owner.TaskOwner`: adopt before start, close admission on stop,
retain through queued QThread.finished delivery and final join, and dispatch
Qt events while draining. Window shutdown stops child owners before callbacks
can start more work and drains them before native-player teardown. Stopped
consumers reject late results. Socket/retry budgets are not a total cancellation
deadline and never authorize destruction of a running worker.

Canonical library/artwork persistence shares `soundbox.state_io` ownership.
Metadata background deltas freeze only selected dict leaves on admission and
commit on an ordered worker through the ordinary repository validation/save.
Native source ownership remains reserved until durable joined GUI delivery.
Repository/generation/revision fences reject stale results; merge only committed
leaves so unrelated navigation preferences survive. Source switching, restore,
export and quit retain the publication interlock. Shutdown flushes any deferred
ordinary preferences after all accepted commit owners have drained.
An exact-source background pause is independent of native authority. Preserve
the accepted permission and frozen backlog; drain admitted requests/receipt ACKs,
allow supported direct edits, and report the pause honestly. An idle paused
backlog can survive normal Quit. Generic USB export retains its source-save gate;
iPod export captures saved selected values without requiring that backlog to resume.
Use `state_repository` for library schema/recovery/saves and `soundbox.artwork`
for local art/results/defaults. Backup
snapshots and restore publication share the same lock. Restore closes UI admission,
drains existing writers/results before final save, publishes staged validated
state, and reloads only after worker finished/join. Old area generations cannot
write after restore. Preserve `.state-transaction` if rollback fails; startup
recovers it. `.state-retired` is disposable only after commit/rollback retirement.
The app entrypoint holds an AppData instance lock. Never fault-inject live state.

Public/personal composition uses the tracked `Soundbox.spec` and
`scripts/build_composition.py`. `./build_app.sh public` writes
`dist/public/Soundbox.app`; no argument builds personal. Only install the personal
artifact on Sonny's Mac. Frozen profiles come from the signed bundle manifest;
user settings/environment cannot enable an excluded provider. Source runs may use
`SOUNDBOX_PROFILE=public`. Existing SoundCloud discovery is in
`soundbox_personal.discovery`; core `soundbox.providers` owns typed interfaces and
explicit module/action admission. Core retains canonical library/artwork state and device authority.
Online functionality is public-eligible only after service/content and dependency
clearance; personal packaging does not waive those terms.
`--isolated-check OUTPUT_DIRECTORY` launches an empty disposable-state packaged
smoke check, suppresses library/device discovery, writes a screenshot/report and
exits. It is not live-library or physical-device acceptance.


Keyboard mappings use the shared `desktop.keyboard` action catalog and physical
press/release router. Preserve text-focus boundaries, press-time cue page/slot,
modifier-independent release, alias hold ownership and release on deactivation,
shutdown or successful remap. Save before replacing the live map; restore reloads
both router and editor. See `docs/KEYBOARD.md` before changing keyboard transport.

Personal SoundCloud credentials and rotating OAuth tokens live only in Keychain.
Never log or persist them in library state, backups, source or build output.
The optional connection's disposable metadata/index is separate from curation;
connecting never clears manual art, misses or blocks. Account jobs use TaskOwner,
and test/isolated harnesses must never read live Keychain or auto-refresh accounts.
