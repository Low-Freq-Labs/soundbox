# Native playlist ordering and bounded organization

November2 source/reference base, reconciled November20 with the separately
accepted smart/node/creation profiles. Current installed-build provenance belongs
to [HANDOFF.md](HANDOFF.md); the native reference and acceptance sequence is below.

## Ordering

Collection and the virtual Artists view stay at the top of SoundBox's sidebar.
Their rows cannot be moved. Artists groups the current Collection inside
SoundBox; it creates no native folder or per-artist playlist, source database jobs,
or ongoing reconciliation. Ordinary native playlists and folders support dragging
before or after a sibling. Dropping above the fixed rows clamps below them. The operation changes
native sibling order; cross-parent transfers use the independent Move profile
described below. Neither changes song membership or the newest-first track-table
display policy.

Folder rows have three drop zones: the upper quarter inserts before, the middle
half appends inside, and the lower quarter inserts after. Inside shows both row
edge lines in the same accent as ordinary insertion, without filling the label
background. This works for regular playlists, smart playlists and folders, with
the same bottom-append behavior whether the destination is collapsed or expanded.
Expanded folders also allow exact placement between their children. Native folder
attributes must reach both newly built and reused tree items; bare widget rows
with manually supplied kinds do not establish that production boundary. Nested folders use the same
behavior; a folder cannot be dropped onto itself or any descendant. Cross-parent
insertion beside a playlist captures that playlist's actual parent and anchor,
rather than reordering an ancestor in the original parent. Blank space below
the tree moves a nested node back to the top level. These gestures never let Qt
move source items before their native intention is admitted.

Internal native reorder metadata is separate from the existing export MIME text.
Dragging a native folder or playlist to a device retains its normal export
identity. Empty native folders can also start an internal reorder drag.
Smart playlists add a small gear to the chosen note, star or crate icon. The
modifier follows exact native kind4 and keeps the original row/icon hit geometry,
theme colors and selected background; names never determine playlist type.

The source request captures both selected and anchor IDs/UUIDs, the exact parent,
the catalog revision, and before/after intent. Captured intentions project pending
sidebar placement immediately; source publication waits for their durable queue
save. Failed persistence retains the intention and returns the projection to the
verified source order. Source
work uses the existing common worker, source lock, closed-native-app guards,
encrypted DB/XML pairs, journal, exact receipt replay, and native readback.

November3 retention/retry hardening keeps this same authority and lane. Folder
deletion captures exact descendant ContentIDs/UUIDs and persists Collection
retention before writer admission; late callbacks and fresh writer guards reject
changed bindings. Captured intentions or exact retained historical receipts
support failed-acknowledgement restart without guessing deleted browser membership.
Historical pin recovery may coexist with later native playlist edits, while
rebasing later pending app commands requires a strict proven committed edge
matching the current catalog/XML. Retired legacy evidence missing original UUIDs
remains guarded. No reorder, source-write or hardware acceptance is broadened.

Reordering requires the independent `PlaylistAuthority.reorder_accepted` flag.
It defaults to false, including when loading existing CRUD authorities. Neither
choosing a source nor replaying a private fixture grants it.

Right-click creation captures the clicked row as an exact sibling anchor. New
ordinary/smart playlists and folders default directly below it; clicking a folder
therefore creates a sibling, while the separate plus menu retains its folder
defaults. An explicit smart-dialog folder change clears that anchor. The saved
create job carries a validated placement sidecar before source publication.
After verified creation, the same durable acknowledgement that retires that job
prepends its real, independently accepted Move. A failed acknowledgement retains
the original create/sidecar and receipt; replay never duplicates the creation.
Changed anchors stay in attention rather than claiming creation-only success.
Pending views use logical identity/position only, without invented native row
history. Rapid smart creation filters only proven earlier future-created rows
from constructor metadata and renews their clocks on the FIFO's actual causal
edge. A not-yet-created destination folder still waits for its own source save.

## Intelligent playlist metadata and separate capabilities

These profiles remain default-off for other libraries. Current-library accepted
activation and native reopen evidence belong in HANDOFF; references and TEMP
proofs alone never grant a capability.

Nonempty-folder intelligent creation has a separate, default-off
`create_smart_append` profile. Its independently marked append profile leaves
every existing sibling unchanged, appends one new local intelligent row, and
uses the observed constructor +1 then combined name/rule +2 counter windows.
The parent XML token receives its own clock; existing child XML stays exact.
Local regular, folder and intelligent siblings are preserved. Root creation and
the accepted smart-only folder/front profile retain their existing guards;
empty folders remain outside the append candidate. Retained native compact
rows/counters/XML reconstruct the append window exactly in encrypted TEMP SQL,
but do not grant source acceptance. Publication requires the separately approved
exact-library source pilot and user-operated native verification.

`PlaylistStore.apply_with_transition` returns a fresh commit's causal edge from
the same writer lease and independently verified final catalog, selected source
identities and XML. It avoids reconstructing that newly completed operation a
second time. An older operation replay still uses the full retained-pair
`committed_transition` proof; independent later native edits cannot supply an
edge. The ordinary `apply` catalog-returning API remains unchanged.

Intelligent rename and mixed reorder use separate `rename_smart` and
`reorder_smart` intents. Old CRUD, reorder and smart-create authorities default
both new capabilities off. The desktop rejects an ordinary rename/reorder that
targets or crosses an intelligent row before saving a FIFO entry, so an
unsupported gesture cannot block later work with a doomed source request.
New smart creation also acknowledges through a background canonical queue patch.
Its source reservation lasts until durable acknowledgement, and only then does
its verified native readback publish. A later create staged during source work
keeps its ID/UUID and rebases only through that operation's proven causal edge.
Storage failure retains the original durable source intention for explicit
acknowledgement retry; it never repeats the native write merely to save local
confirmation. Ordinary playlist acknowledgement keeps its existing path.

The local smart rename profile preserves its complete rule and identity.
Its standalone inline-name window advances the local counter by one and changes
only Name, the local USN/update clock, and the selected XML clock. The earlier
criteria-definition editor has a separate +2 window used by smart creation.
The current inline candidate follows the November19 native row/counter/XML frame.
Publication requires its own exact-library acceptance, independent of creation.

The independently marked mixed reorder profile follows the November19 native
reference: update the dragged row first, then each displaced sibling once in
ascending original sequence, advancing the counter once per changed row. Each
row has an independent SQL clock. Only the dragged XML token and, inside a
folder, that parent folder's independent XML clock change. Displaced ordinary
and intelligent XML tokens remain exact. This differs from ordinary ordering's
previously accepted composition of adjacent swaps. Both directions and long
moves across ordinary/intelligent siblings have complete private DB/XML TEMP
oracles; root ordering and folder neighbors have separate reference captures.
Smart rows preserve their measured local history (`rb_local_synced` 0 or 1,
zero status/deletion flags, no cloud USN); ordinary neighbors retain existing
local guards. Cross-parent moves remain outside this operation. Native observations
and TEMP proofs do not activate any source capability.

Cross-parent moves use the separate default-off `move_smart` profile. Folder
center drops append into an exact ordinary folder, including empty and nested
folders; root drops append at the top level. The request freezes target UUID,
opaque rule, original/destination parent UUIDs and independent SQL/XML clocks.
Source sibling positions remain exact, including native Seq gaps. Destination
positions normalize in their existing relative order; only numerically changed
rows receive their own counter/SQL clock. The moved row advances the counter
once. XML changes only its parent/clock and the destination folder clock.
Native references cover root→historical folder, folder→empty nested folder and
historical intelligent folder→root; a two-gesture frame remains explicitly
composed evidence. Ordinary cross-parent moves are outside this profile.

The broader `move` profile has a separate default-off `node_move_accepted`
capability and exact profile. It covers local regular/smart playlists and folders,
including complete nested subtrees, root moves, exact before/after anchors and
same-parent ordering across unique native Seq gaps. Requests pin target kind,
opaque rule, parent/anchor UUIDs and every descendant identity/parent/kind/rule.
Subtree memberships and descendant rows remain unchanged; only the moved row,
numerically displaced destination siblings, local counter and exact XML tokens
may change. Painted source/destination/anchor bindings must still match the
current catalog before admission. Duplicate positions and cycles fail closed.
Accepted smart append and contiguous sibling-order paths keep their existing
profiles. The generic profile requires its own exact-library source/native
acceptance; the existing smart-only flag never grants it. Installation and
acceptance status belong in HANDOFF.

Gapped ordinary playlist creation has its own default-off `create_normalized`
profile. It composes the accepted root constructor, explicit normalized placement
and changed siblings, then the existing name window. Root append uses ordinal
positions, rather than guessing `max(Seq)+1`. The pure model matches both the
retained native contiguous root constructor and the new native gapped insertion,
including every playlist row, complete registry hashes and byte-exact XML.
Existing creation/migration opcodes keep their original behavior; they never
inherit this capability or silently upgrade saved intentions. Other legacy
operations retain their existing gap guards. Each new capability still requires
separate exact-library source/native acceptance before activation.

Two further default-off profiles keep new creation semantics separate from old
receipts. `create_smart_normalized` follows the actual gapped ROOT reference:
the raw smart constructor starts after the greatest ROOT Seq, then explicit
placement normalizes destination order, and the combined name/rule window
advances its own clocks/counters. The retained113-row/all42-table/XML oracle
matches exactly, including eight displaced peers and twelve counter steps.
`create_folder_normalized` composes the accepted ordinary-folder constructor,
normalized placement and ordinary naming; it does not infer the smart-specific
maximum-Seq constructor. Its separate authority grants no smart capability.
Both preserve existing identities, memberships, unrelated native data and old
profile reconstruction. Explicit Retry may return a newly marked smart intention
only with its accepted new capability and after committed-receipt handling; the
caller must durably save it before dispatch. Future ID/UUID/name/tag/parent pins
survive. Direct application of an old opcode never silently upgrades it.

The November19 organization delivery completed that separate acceptance for
Sonny's current library: the existing RAP smart playlist moved into GENRES with
its rule/membership intact, and a disposable normalized root append passed native
reopen before user cleanup. Only the four corresponding authority fields changed;
other libraries keep their existing defaults. See [HANDOFF](HANDOFF.md) for current
source/install identities and bounded recovery retention.

Native verification is user operated: create disposable ordinary and intelligent
lists in one parent; capture a stable read-only DB/WAL and XML baseline; rename
the intelligent list; capture; move it across one adjacent ordinary sibling;
capture; delete only that disposable intelligent list; capture again. Compare
each independent window and replay only encrypted TEMP copies before a separately
authorized source pilot and native reopen. Captures never enable authorities.
Private current reference and replay receipts belong outside this repository.

## Playlist icons

Right-click directly on a playlist's icon to choose Star, Music note or Vinyl
crate. The choice is a local SoundBox presentation preference, keyed by exact
native source path, library ID, playlist ID and UUID; it grants no source edit
capability and does not write rekordbox. Stale menus reject a changed identity.
Changing an icon preserves the active playlist/track and sidebar objects.
Existing music-note/custom-star defaults remain until changed. Legacy local
playlist rename/delete moves or removes only its own scoped preference in the
same local state save.

Only playlist rows are configurable. Collection stays home, Artists stays person,
and folder expansion icons stay fixed. Right-clicking row text retains the
ordinary playlist menu. Current installed status remains in [HANDOFF](HANDOFF.md).

## Native reference and proof

Sonny moved ALL LOCAL directly above COOKING.AIF in rekordbox and quit normally.
The captured adjacent move changes only the following:

| Record | Fields | Counter group |
| --- | --- | --- |
| Dragged playlist | `Seq`, `updated_at`, `rb_local_usn` | Previous counter +1 |
| Adjacent sibling | `Seq`, `updated_at`, `rb_local_usn` | Previous counter +2 |
| `localUpdateCount` | `int_1` | Previous counter +2 |
| Dragged XML node | Independent `Timestamp` | Captured XML clock |

The sibling XML token remains unchanged. Unknown columns, native sync states,
UUIDs, creation times, parent IDs, history, memberships, Collection, analysis,
cues, tags and audio remain preserved. Cloud USNs, ambiguous ordering and
intelligent targets fail closed.

Long moves compose this observed adjacent primitive within one transaction. Each
step records the exact intermediate rows and local counter. This avoids guessing
a separate native multi-position counter protocol. Recovery checks the complete
ordered change sequence and the exact before/final state.

A TEMP-only replay through `PlaylistStore.apply` matches all 99 native playlist
rows, the complete counter row and the XML bytes from the captured native result.
All 41 non-registry table hashes match; unrelated session-registry rows remain
exactly those of the before snapshot. Only hashes and nonsecret summaries are
reported. Protected reference DB/XML pairs remain unchanged.

This reference replay alone conferred no source authority. The separately
approved source pilot moved FLAVORS below FAVORITES; Sonny confirmed its native
position and expansion, then normally quit rekordbox and its agent. Exact native
row/XML/unrelated-domain readback passed November2 at04:20. The accepted writer
restored every original sibling sequence and provisioned only `reorder_accepted`
for the exact accepted library. All memberships and audio remain unchanged.

## One-time custom migration

The explicitly requested legacy custom migration composes existing accepted commands
in a bounded envelope on the same playlist writer. Each envelope has at most 64
commands and 16,384 selected memberships. Larger groups continue in later
envelopes. Future node IDs and the complete command envelope are saved atomically
before worker admission. Restart retains those IDs and the exact operation
identities; matching a native name alone never adopts a row.

The original custom hierarchy remains visible until its entire native membership
is verified. A partially completed custom playlist keeps its source-order prefix;
duplicate custom memberships remain intentional. The completed migration is
terminal: future song imports and ordinary playlist gestures use their accepted
native APIs directly. Fresh native libraries create no migration state or jobs.
Unresolved native identities and changed bindings retain their evidence for review.

Sonny canceled the earlier native per-artist organization work on November 2.
Existing artist bindings remain archived recovery evidence, never an instruction
to recreate them. SoundBox hides only their exact native ID/UUID matches after
the separately authorized source cleanup removed the generated hierarchy:1527
playlists plus their root and3163owned memberships, with recovery retained.
Native user folders named Artists are not adopted, hidden by name or pinned.

One envelope produces one catalog/UI refresh. Existing folder objects, expanded
state, selected tracks and scroll position survive. Unrelated Collection rows
and artwork inputs are left untouched. Real source publication, crash-prefix,
retention and sequential-equivalence proofs are separate from UI projection.
