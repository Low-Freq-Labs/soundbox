# Native playlist ordering and bounded organization

November 2, 2026 source implementation. Current installed-build provenance and
actual acceptance status belong to [HANDOFF.md](HANDOFF.md).

## Ordering

Collection and the virtual Artists view stay at the top of SoundBox's sidebar.
Their rows cannot be moved. Artists groups the current Collection inside
SoundBox; it creates no native folder or per-artist playlist, source database jobs,
or ongoing reconciliation. Ordinary native playlists and folders support dragging
before or after a sibling. Dropping above the fixed rows clamps below them. The operation changes
native sibling order; it does not transfer a node to another parent, change song
membership, or change the newest-first track-table display policy.

Internal native reorder metadata is separate from the existing export MIME text.
Dragging a native folder or playlist to a device retains its normal export
identity. Empty native folders can also start an internal reorder drag.

The source request captures both selected and anchor IDs/UUIDs, the exact parent,
the catalog revision, and before/after intent. A durable queue save precedes
sidebar feedback. Failed persistence leaves the visible order unchanged. Source
work uses the existing common worker, source lock, closed-native-app guards,
encrypted DB/XML pairs, journal, exact receipt replay, and native readback.

Reordering requires the independent `PlaylistAuthority.reorder_accepted` flag.
It defaults to false, including when loading existing CRUD authorities. Neither
choosing a source nor replaying a private fixture grants it.

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
