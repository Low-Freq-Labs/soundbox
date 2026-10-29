# Analysis, track menu and native playlists

Accepted scope October29, including Sonny's overnight correction: the three-line
menu is immediately **right of Q**, with the same evenly distributed toolbar
gaps. Analysis must ultimately work in either direction: importing/analyzing in
SoundBox first or rekordbox first must preserve the same musical data, independent
of author. The reverse source-publication acceptance below remains unfinished;
the current detection stage must not be described as the completed round trip.
This is a source candidate, not the installed release. The locked macOS session
blocks the required Cocoa presentation gate and installation. Actual installed
provenance and validation are in [HANDOFF](HANDOFF.md).

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
   Initial and incremental exports and interrupted standalone intent resumption
   are exercised with the compiled renderer on disposable targets.
4. Analyze Track.

## Implemented detection stage

Analysis is immediately right of General in Settings. BPM/Grid and Key run the
pinned rbxport constant-tempo/key engine in the optimized local helper. Phrase
is a disabled placeholder; no Normal/Dynamic mode or cloud controls. Automatic
analysis of newly imported SoundBox files is opt-in and initially off. The
first-analyzed-beat Memory Cue option is initially unchecked. Performance,
Normal and Power saving select child-process scheduling priority, preserving the
same DSP and quality. BPM ranges are 70–180 default, 48–95, 58–115, 68–135,
78–155, 88–175, 98–195, 108–215, 118–235 and 128–255.

One source snapshot is captured while hashing. The helper decodes at most300
seconds /32 million mono samples, checks rate/channel budgets before collecting,
and bounds every append. A constant grid extends to the declared complete file
end; without a declared end it remains bounded to the sampled span. A premature
end encountered while decoding is rejected; a capped sample does not prove the
unread tail. Source publication must separately establish complete endpoint and
sidecar consistency. Silence/low-confidence tempo does not become an invented grid. Cancellation terminates/joins the child;
TaskOwner retains the worker through finished delivery/join. Closing before
result delivery cannot save; failure preserves previous results. There is no
source audio retagging, waveform invalidation or audio reload.

Results persist inside backed-up `library.json` as `settings.analysis_results`.
Publication enforces the same64MiB ceiling as the reader before replacing any
canonical bytes; an oversized result preserves the preceding library. Each result
binds its canonical path, full audio SHA-256, stable filesystem identity and engine
provenance. A later rekordbox track ID at the same path can find the existing
SoundBox result. A key-only/grid-only run preserves the other result for the same
bytes. Playback validates the exact recorded filesystem identity in its existing
worker. A native/manual grid wins over detected fallback; native cues remain
source-authoritative. Detected values fill otherwise-empty browser BPM/key.
For direct imports without existing cues, the optional first-beat cue is applied
locally. This stage does **not** yet publish detected grids, Key or that optional
cue back to the source database, and native CDJ export continues to capture native
source analysis. These are temporary acceptance boundaries, not an author-based
product policy. A detected result never grants the manual-grid writer new powers.

## Remaining shared-analysis acceptance

The accepted manual-grid API does not cover registering a new detected analysis,
KeyID interning, initial analysis-file/contentFile registration or a direct-import
track's first native identity. Do not route detection through that API or adopt
upstream `Analysed=105`/counter assumptions without native6.8.7 evidence.
Upstream's writer deliberately omits contentFile/contentCue protocols; the accepted
SoundBox manual-BPM writer already proves that those details matter here.

Prepare separate frozen before/after native examples for an unanalysed imported
reference, BPM/Grid+Key analysis, Key-only analysis, and first-beat Memory Cue.
Bind the existing/new sidecar set, key table/content row, counters, contentFile,
contentCue, XML and all unrelated table inventories. Then implement a copy-only
candidate and recovery proof; extend the shared Tag/Cue/Grid source lane only
after native acceptance with an exact library/schema/version authority. Exercise
SoundBox-first import → native registration → reload and rekordbox-first import
→ SoundBox reuse, plus later manual edits from either app. Preserve existing manual
grids, cues, Analysis Lock and opaque metadata. No perpetual author-ownership fork.

## Ordinary source playlists — still gated

Accepted scope: create regular playlists, add/remove songs, delete regular
playlists. Collection tracks and source audio survive membership/playlist removal.
Intelligent playlists remain separate. No live ordinary-playlist publisher or
source authority is enabled yet.

`research/cdj_export/playlist_reference.py` compares explicit frozen encrypted
captures against their complete semantic inventories; it has no live defaults.
`playlist_membership.py` is a pure provisional unsynced append/remove planner,
with no SQL/publication API. Its native-reference replay matched the exact add
rows, physical membership removal, survivor UUID/created_at preservation,
TrackNo2→1, and both counter outcomes. An encrypted temporary-copy replay changed
only djmdSongPlaylist and the local counter, retaining schema/metadata and all
other rows. Synthetic tests reject unknown sync and nonordinary playlists.
This is useful proof, not native acceptance or complete playlist CRUD.

The create sample advances the counter twice and has distinct creation/update
timestamps; upstream's single create operation advances once. Separate the
native UI's create/name sequence before assuming those are equivalent. Native
XML uses hexadecimal playlist IDs and also changes during membership edits;
include its timestamp protocol in the future atomic transaction. Synced/cloud
memberships, creation/deletion and interrupted recovery remain unproven.

First pending user action: delete **only** SoundBox Playlist Test in rekordbox,
keep both test songs in Collection, quit normally, and reply done. Capture that
native-deleted snapshot before asking for another native edit. Test playlist:
ID4086406462, UUID4739cf09-58a3-4081-8280-a1695ded60c3. It last contained only
Gangster Tune18213989 after STOP CAPPIN228450310 was removed.

## Private evidence

Original captures and22 UI references:
`~/Library/Logs/Soundbox/playlist-analysis-ui-2026-10-29/`.
The prior unfinished-prototypes/resume.patch is historical untested work; current
source selectively incorporates and corrects it. Never overwrite newer files
with those full snapshots. Overnight replay reports, generated analysis reference
inputs, UI renders, release/install records and state baselines live in
`~/Library/Logs/Soundbox/overnight-analysis-2026-10-29/`.
