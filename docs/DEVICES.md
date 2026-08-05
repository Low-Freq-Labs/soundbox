# SoundBox device layouts

Current as of 2026-08-03. Read this with `GOTCHAS.md` and the write contract at
the top of `soundbox/sync.py` before changing device detection or export.

## The simple model

SoundBox classifies a mounted external volume by the folders already present.
Detection is read-only. The profile describes what SoundBox may safely do; it
does not make a device writable by itself.

| Profile | Existing layout | SoundBox behavior |
|---|---|---|
| Rockbox | `/.rockbox/` | Export `/Music`, `/Playlists`, and `/.soundbox` only when the filesystem is FAT32 |
| Stock iPod | `/iPod_Control/` with an iTunes database area | Read-only readiness by default; guarded playlist sync only for the explicit MA446/MP3/WAV allowlist |
| Dual boot | Both layouts | Repeatable reference-counted, single-copy playlist sync on FAT32 MA446 |
| Generic USB | Neither layout | Use the same Rockbox-style folder/playlist export on FAT32 |

The mounted on-disk layout selects the export contract automatically; SoundBox
does not guess which firmware the user most recently booted. A `/.rockbox/`
marker without `iPod_Control` selects ordinary Rockbox export. When both layouts
exist, the MA446/FAT32 gate selects the separate composite planner; neither
ordinary exporter is allowed to approximate it. Rockbox media under `/Music`
is not automatically indexed by stock firmware, which is why only the validated
shared subset enters Apple's database.

Non-FAT volumes remain browseable but do not accept the Rockbox-style drop.
Unknown or failed probes never grant extra capability.

## Rockbox layout

The current export is ordinary files, so it does not depend on the Rockbox
tag database:

Generated path components are FAT-safe without destroying the curated display
metadata. SoundBox compatibility-normalizes punctuation, transliterates Latin
lookalikes and accents to readable ASCII (`CØSTA` → `COSTA`), preserves
non-Latin scripts, removes decorative FAT-forbidden `*`/`?`, and protects DOS
reserved names. The SoundBox UI, stock iTunesDB text, and source tags remain
unchanged.

```text
/
├── .rockbox/                 firmware plus narrowly managed config.cfg
├── Music/Artist/Track/       audio plus exact and universal per-track BMPs
├── Playlists/*.m3u8          UTF-8, drive-root-absolute audio paths
└── .soundbox/manifest.json   v2 path ownership, hashes, membership, generation
```

The curated JPEG cache remains canonical. Managed Rockbox artwork uses
uncompressed 8-bit paletted BMP derivatives: baseline 160×160/116×116 fast
paths, bounded exact square sizes read from the active WPS/SBS, and a
lightweight dimensionless 160×160 fallback. Exact sizes are retained when a
later theme adds another request. Each is a per-track sibling of its audio file, preventing folder-level art
collisions; the paletted payload reduces device I/O by roughly two thirds versus
24-bit BMP and avoids JPEG decode work on the PortalPlayer. Rockbox prefers embedded pictures over external art, so selected audio with embedded art is
copied first to bounded Mac staging, has only its picture records removed,
and is accepted only when container-specific audio essence plus technical
properties remain identical. The source file is never changed. Planning records
the exact canonical-source baseline and exact derivative size/hash, then
discards that payload before inspecting the next track. Commit rematerializes
and rechecks one payload at a time, copies and verifies it on the device, and
immediately discards its staging entry. Peak Mac staging is therefore bounded
by the largest individual track rather than the selected library.

The per-playlist manifest records this export profile. A playlist last written
by the older embedded-art-preserving profile reports drift once and is repaired
through ordinary managed-file hash/ownership rules; unowned device files are
never overwritten or claimed.

The official Rockbox iPod manual describes a FAT32 iPod partition and the
firmware at the volume root. Its playlist catalogue setting defaults to
`/Playlists` and includes only playlist files directly inside that directory.
Composite sync therefore writes every M3U8 at the catalogue root. Its visible
title is the concise rekordbox leaf name, such as `HOUSE`; the full
`GENRES / HOUSE` hierarchy remains in the ownership manifest. If two managed
playlists share the same leaf name, SoundBox adds folder context only to those
colliding titles and refuses any still-ambiguous result. The same chosen title
is used by Rockbox and the stock iTunesDB. Users may
play exported playlists without initializing Rockbox's optional tag database;
database users can run Rockbox's update/initialize workflow afterward. Composite sync enables
Rockbox's own automatic tag-cache update and tells the playlist viewer to show
titles from tags. It also raises `max files in playlist` to the documented
10,000 default when the user's value is lower, while preserving any larger
value; this prevents a full Collection M3U8 from exhausting a stale 2,000-entry
buffer. Rockbox requires a full restart before that limit takes effect. The
Playlist Catalogue is configured to sort alphabetically. Because physical
Rockbox kept presenting that order despite controlled FAT timestamps, each
Rockbox-only M3U8 filename receives a zero-padded sequence such as
`01 - HOUSE.m3u8`. Stock iTunesDB titles and SoundBox device rows remain clean
and unnumbered. The Device Playlists tab can move managed rows and explicitly
publish that one order to both firmware paths without duplicating media;
reordering renames only the tiny owned Rockbox playlist files.
This setting is Rockbox-specific. Stock playlists are MHYP/MHIP reference
lists with 32-bit entry counts; the pinned offline renderer has a regression
proof for 10,000 entries and there is no equivalent stock-firmware preference
for SoundBox to set.
SoundBox preserves all unrelated `config.cfg` lines and the file's existing
LF/CRLF style, and never writes `database_*.tcd` directly;
Rockbox reconciles its rebuildable cache on
the next firmware boot. The dashboard reports `Rockbox restart pending` when
the index predates the current SoundBox manifest/config generation; that signal
never authorizes SoundBox to edit or delete the cache.

Reference:
[Rockbox Manual for iPod Video](https://download.rockbox.org/daily/manual/rockbox-ipodvideo/rockbox-build.html).

## Composite dual-library sync

Dual boot must expose the broad Rockbox library and a stock-safe subset without
duplicating the collection. The approved single-copy layout is:

| Track capability | One physical location | iTunesDB | Rockbox playlist |
|---|---|---|---|
| Stock-safe MP3 or PCM16/44.1-kHz stereo WAV | `iPod_Control/Music/Fxx/` | indexed | references the same verified picture-free device payload |
| Rockbox-supported but stock-unsafe | `/Music/...` | absent | referenced |
| Unsupported by both destination profiles | not exported; reported | absent | absent |

`soundbox.dual_ipod` implements repeatable playlist drops for a validated FAT32
MA446 containing both layouts. A new or changed source is fully normalized and
verified during planning, which retains only its reproducible source/output
contract. An unchanged committed source instead reuses the unified manifest's
payload identity after source size/mtime/ctime, effective title/artist, and
per-file media policy match. That steady-state proof is stat-only: it does not
reopen thousands of unchanged audio files to rediscover embedded art or display
tags already represented by the committed policy. A row from the narrower
legacy metadata profiles takes one fully verified migration through the current
title/artist-only derivative contract; after that, unchanged rows return to the
stat-only path. Composite drift compares that per-file profile directly, so an
older payload policy requests Sync needed even when playlist membership,
source stats, and effective title/artist are otherwise unchanged. Planning writes nothing to
the device, uses the exact matching stock shadow only as a disposable
accelerator, and produces exact copy/reuse/rewrite/omission/byte counts. Commit
recreates at most one planned audio derivative at a time and refuses any source
or output hash mismatch. For existing schema-3 audio, planning validates manifest ownership,
path, and size without streaming every old device file; it still verifies the
exact desired source derivative on the Mac when that source or its policy
changed. Commit rechecks every planned source stat baseline before its first
write, then copies new Rockbox files
first, renames each old managed audio file within the device for transient
rollback, lands and verifies its replacement one track at a time, then runs the
recovery-first stock transaction with
`iTunesDB` last inside that phase, writes Rockbox playlists spanning both path
classes, and publishes the unified schema-3 ownership manifest last. A failure
after the stock phase restores the old database, playback delta, artwork, and
any rewritten managed media from its transient rename, then removes only this
plan's new Rockbox files. After a successful manifest-last commit, old managed
audio payloads are discarded rather than retained as recovery material;
unowned paths are never overwritten. The sole adoption exception is a failed
generation whose recovery directory is bound to the still-current manifest:
a fresh plan may identify only an overlapping regular file at the freshly
derived deterministic path and expected size. Full SHA-256 verification is
deferred from the confirmation-blocking plan into commit preflight, where every
candidate must match the freshly derived payload before the first device write.
Those proofs are recorded in a disposable Mac-side interrupted-verification
journal keyed by the baseline manifest hash; an additional USB reset resumes
proof from the first unverified file rather than rereading every completed
payload. Recovery verification contributes to visible sync progress. The
device ownership manifest still changes only at the final successful
publication.

Every dual-iPod Sync, playlist drop, and managed-playlist deletion receives an
immediate concise Yes/No confirmation before expensive planning begins. An
explicit Delete approval is authoritative: SoundBox removes the checked
identities, reconciles other currently available managed-playlist changes in
the same transaction, and retains an unrelated unavailable playlist from its
last committed manifest snapshot. It never requires a separate Sync before the
requested deletion. Confirmed drops are
identity-deduplicated FIFO jobs. Playlists accepted while a prior job is busy
are coalesced into the next guarded plan/commit batch, avoiding repeated
iTunesDB parsing and repeated validation of the same baseline. SoundBox carries
one cumulative progress bar across the queue and shows one combined completion
summary only after the queue drains. Each follow-up batch reuses the
just-committed Collection media and ordinarily writes only playlist membership
or genuinely new audio. A failure cancels every unseen queued job and reports
that cancellation; SoundBox never surprises the user by reviving an old write
after a later drop.

The planner emits bounded media, retained-playlist, stock-library, Rockbox,
artwork, playlist, and ownership stages. Planning maps to the first 30% of the
same operation bar and commit maps to the remaining 70%; the displayed value is
monotonic across the phase transition and across queued jobs. The phase-local
`#/#` remains hidden during planning because planner units and committed audio
units are intentionally different; it appears for actual audio mutation work.

The open Device, Songs, and Playlists pages retain one immutable last-verified
snapshot while planning, syncing, deleting, or performing the post-operation
read. A successful read replaces all three surfaces together. A transient read
failure keeps that snapshot visible and reports that it is the last verified
state; it must never clear individual tabs into a misleading blank view.
The device header exposes Sync at all times, including when the last background
drift scan said the device was current; the context menu offers the same manual
recheck. Applying playlist order adds device-presentation fields to schema 3.
The next explicit Delete treats that reordered generation as its baseline and
reconciles any other source-side playlist drift in the same confirmed plan;
presentation changes and unrelated source drift cannot create a Sync-first
dead end.

Every Rockbox-visible payload has per-track siblings such as
`Fxx/ABCD.160x160.bmp`, `Fxx/ABCD.116x116.bmp`, and `Fxx/ABCD.bmp`. The first
two match known exact-size lookups; the dimensionless file is a theme-independent
fallback without a shared-folder collision. A shared stock-safe source with embedded art uses the same verified
picture-free payload and never reintroduces the hazardous embedded picture.
Finder-generated `._*` AppleDouble files and abandoned `*.sbpart` temporaries
below `/Music` and `/Playlists` are planned as exact-baseline transactional
cleanup rows. This includes directory sidecars left after the final managed
track disappears; after commit, only empty child directories are pruned. Empty
schema-3 libraries remain valid cleanup plans rather than stranding that clutter.
AppleDouble files are opportunistic cleanup, not library drift, because Finder
may recreate them immediately after a successful export. Only an abandoned
`*.sbpart` independently requests reconciliation as evidence of an interrupted
SoundBox write.

Every composite audio payload also carries the effective SoundBox title and
artist. WAV payloads use the RIFF `LIST/INFO` fields Rockbox actually parses
(`INAM` and `IART`), not ID3-in-WAV. SoundBox places those two fields in a small
canonical INFO list immediately after `fmt` and removes unrelated source INFO
containers entirely. MP3/AIFF ID3, FLAC comments, and MP4 tags are likewise
reduced to one effective SoundBox title/artist pair. Album, year, comment,
embedded-ID3-in-WAV, Beatport receipt, and other competing display fields cannot
win Rockbox's parser or a theme fallback. Missing or stale source tags therefore
cannot make Rockbox display an Apple-scrambled filename such as `ABCD.wav`, a
purchase receipt, or a year as the song title. The canonical source remains
untouched; the same normalized payload is safe for the stock database because
stock title/artist come from iTunesDB.
Tagging occurs only in the same
content-addressed disposable copy used for embedded-picture safety; container
audio essence and technical properties are reverified, and source bytes remain
unchanged. For MP3, tag-excluded MPEG essence is the authoritative content
proof: Mutagen's tag-size-dependent CBR duration estimate is deliberately not
treated as an audio change when canonical ID3 replacement alters only padding.
The schema-3 profile migration accepts the previous manifest as
ownership evidence, then rewrites only managed payloads whose verified bytes
need the new metadata. Each schema-3 playlist also stores a signature of its
effective ordered title/artist values, so either edit appears as Sync needed
even when playlist membership is unchanged.

Rockbox artwork derivatives have a persistent canonical-JPEG stat/hash index.
An unchanged cover whose verified Mac variant identity matches the ownership
manifest is retained without hashing the corresponding device BMP during every
plan. A new/changed source, rewrite, or deletion still takes the full live-byte
verification path. This avoids thousands of tiny no-op reads that can reset a
classic iPod's USB transport without weakening any planned mutation.

The dual device dashboard treats the schema-3 manifest as the library authority:
it reports total managed tracks, the stock-visible subset, and the Rockbox-only
remainder separately. Songs and Playlists unify the manifest and iTunesDB views
and label each managed row by firmware visibility. A live managed M3U8 that no
longer matches its committed hash does not invalidate the read-only stock
identity/database snapshot: Device details remain available, the affected
playlist is labeled `Sync needed`, and its changed entries stay hidden until the
confirmed composite planner validates them.
During commit, animated `Syncing iPod...` status is followed by a separate
`#/#` field and percentage bar. The count covers audio files actually copied,
replaced, or removed; a 37-track sync-to-zero therefore advances 0/37 through
37/37, while a database/playlist-only commit hides the inapplicable counter
instead of showing 0/0. After a successful commit, the selected Device, Songs,
and Playlists snapshot refreshes before the sidebar free-space/drift scan under
an explicit `Finalizing iPod...` state. The completion dialog appears only after
both workers finish. Polling remains suppressed while that modal dialog is open,
then stays paused under an explicit eject-ready latch after OK. Eject therefore
does not cancel or complete dashboard work—the finalization scanner is already
done. A new explicit device action releases the hold and refreshes normally.
All displayed surfaces still update without a navigation round trip.
The status text
reserves only its current animation width, and the confirmation shows the
Both/Rockbox split plus concise add/update/remove totals.

The stock fast-sync shadow records canonical source size/mtime separately from
the verified device-payload size/hash. This matters whenever embedded pictures
are removed: the picture-free payload is smaller by design and must not make a
cleanly committed source appear locally changed. Older shadows without the
separate source-size field use their canonical source mtime until the next
successful commit upgrades the row.

Library identity is source-path based, not content-hash based. Repeated playlist
references to one canonical source share its one stock record/file. Two distinct
source paths retain separate iTunesDB records, effective metadata, artwork, and
scrambled destinations even if their audio bytes happen to match. The first
full-library dry-run exposed 19 such pairs, including six with intentionally
different labels; collapsing them would not be an honest library migration.

The first generation requires no existing `.soundbox/manifest.json`. Existing
folder-prefixed or nested managed M3U8 names migrate to concise catalogue-root
titles with exact recovery and without rewriting audio. Each later
drop accumulates the new or explicitly updated playlist with prior manifest
snapshots, increments schema-3 ownership, preserves stable media/playlist paths,
repairs managed audio from exact source derivatives, and produces a true zero-write
plan when nothing changed. It never silently applies drift from an unrelated
playlist. Once schema 3 exists, the ordinary Sync affordance uses the original
manifest stable-ID/signature/drift machinery and routes back through this same
composite planner. Rename, folder move, order/membership change, SoundBox veto,
and source-playlist removal reconcile the Rockbox m3u8 and matching iTunesDB
playlist together. A size change to an owned Rockbox M3U8 also surfaces the
ordinary Sync-needed state. Confirmed planning then reads that bounded playlist
and accepts only append-only paths that resolve to existing schema-3 managed
media. A new membership is saved in SoundBox's playlist state (as an append
overlay for a read-only rekordbox playlist) and published to both firmware
views without recopying audio. If Rockbox appended a track already in the
playlist, Sync removes only that newly appended duplicate; older intentional
duplicates remain untouched. Unknown/unowned paths are not imported, and
device-side deletion/reorder edits remain a closed conflict. The proven local
membership is persisted before target writes, so a failed device phase remains
a truthful retry against the still-present/restored Rockbox append.
Superseded/removed m3u8 files have exact recovery; orphaned
track/media/art bytes are removed only when no remaining managed playlist uses
them. Schema-3 media cleanup requires its committed ownership/path/size baseline
while art and playlists being rewritten or deleted retain exact live-byte
checks; unchanged retained artwork reuses its matching manifest/cache identity
without rereading device bytes. Every orphan is renamed
into transient same-volume recovery before final manifest publication; rollback
restores those exact bytes on failure and successful cleanup discards them.
Deletion therefore does not regenerate an obsolete media/art derivative.
Shared stock records also honor ordinary stock user-playlist references. Migration
from a Rockbox schema-2 manifest remains disabled. Ordinary Rockbox export refuses
schema 3. Never run the stock and Rockbox exporters independently or copy all safe
tracks twice.

A dual layout with no schema-3 manifest may use the guarded stock playlist-delete
UI. It removes the selected stock playlist records, any track records no longer
referenced by another user playlist, and their linked stock-device media after
the database commit. Source audio and unrelated/unowned Rockbox paths remain
untouched. A narrowly gated recovery repair can clear orphaned index records and
linked media only when the device is still pre-composite and already has zero
user playlists. This produces a genuinely empty stock baseline before the first
composite generation.

Compatibility belongs to the destination profile, not to a global SoundBox
allowlist. The current MA446 stock validator controls only which tracks may
enter iTunesDB; it must not suppress a stock-unsafe file from Rockbox when the
Rockbox firmware supports it. Conversely, a file unsupported by both profiles
is omitted once and reported honestly by the composite plan.

## Stock iPod layout

Apple's classic database layout is not a folder-copy target. Typical stock
iPods store renamed media below `iPod_Control/Music/Fxx` and index it through
`iPod_Control/iTunes/iTunesDB` or a related database. Database generations and
some models require model-specific checksums. Copying a normally named song
into that tree without a correct database update does not create an honest,
usable stock-iPod export.

SoundBox now has one deliberately narrow stock writer: MA446 iPod Video 5.5G,
an uncompressed `iTunesDB` with checksum scheme zero, and validated MP3 plus
stereo PCM16/44.1-kHz WAV sources. The
Clickwheel 0.19.1 dependency supplies the MIT-licensed pure-Python parser/writer,
but it never receives the live mount as its sync target. `soundbox.stock_ipod`
parses the live state, folds an exactly matching `Play Counts` delta into the
track records, renders to a temporary device tree, restores one opaque Apple
smart-rule header word that Clickwheel normalizes, reparses the final bytes, and
compares existing playback state, track identities, selected order, and built-in
smart rules before a commit is even possible.

The format check is destination-specific. Rockbox/generic exports continue to
receive original FLAC/AIFF/24-bit/float/high-rate media. The MA446 planner alone
omits unsupported extensions and WAV/MP3 encodings from its rendered playlist,
reports the exact omitted count, and previews affected titles/reasons before a
Cancel-default confirmation. It never edits the source playlist. This boundary
was added after the physical 5.5G reproducibly rebooted when asked to play
`Hostage Situation - Loot`, a 24-bit/44.1-kHz PCM WAV; the proven working WAVs
are stereo PCM16/44.1-kHz. A read-only full-library audit found 722 PCM24/44.1,
166 PCM24/48, 57 float WAVs, and additional high-rate WAV variants, so unsafe
files may never be treated as isolated exceptions.

SoundBox curation is resolved before that destination check. **Remove from
Collection** is a global export veto and **Remove from Playlist** is a veto for
that stable playlist ID. Playlist menus expose both scopes; Collection exposes
only its global scope. Neither curated-away track reaches stock format inspection,
artwork, copy planning, or iTunesDB rendering. Consequently, the stock
confirmation's selected count is the effective SoundBox playlist—not raw
Rekordbox membership—and curated-away tracks do not appear misleadingly in the
firmware-omission list.

Curated SoundBox JPEGs are also the stock-art source of truth. A background
worker pre-renders MA446's RGB565 formats 1028 (100×100) and 1029 (200×200)
into a content-addressed AppData cache, pausing during playback. A source
locator skips unchanged startup entries; one manual cover change queues only
that JPEG. Initial reconciliation deduplicates identical images into one segment in
each `F1028_1.ithmb`/`F1029_1.ithmb`, while ArtworkDB has the per-track MHII
records stock firmware expects. SoundBox validates every iTunesDB MHIT link,
ArtworkDB song ID, MHNI format/offset/size, and ithmb boundary before showing
the confirmation. It never modifies tags or embeds images into source audio.

Successful commits publish a disposable Mac-only `stock_ipod_state/` shadow,
keyed by library persistent ID plus FAT volume UUID. Reconnect compares identity,
the small iTunesDB/ArtworkDB hashes, and ithmb size/mtime without writing. Exact
matches reuse proven media identity; mismatch performs full reconciliation. A
true no-op creates no recovery point and shows no write confirmation.

Fast-path RGB565 changes use only slots not referenced by the current ArtworkDB.
Free holes are reused before append, existing image IDs stay stable, and the old
firmware view therefore remains valid until database publication. One changed
cover writes 20 KB + 80 KB of pixels rather than every ithmb byte. Playlist
deletion remains the explicit full-compaction path.

A stock drop is two-phase. The first background phase is a no-write plan and
shows exact reused/new tracks and bytes. Because the real refurb has an empty
`SysInfo`, the first use originally required its About-screen model; unknown
models fail closed. The read-only device surface can now independently recover
the connected unit's Apple hardware serial and firmware from macOS's classic-iPod
device record, and the serial suffix `V9M` maps through the guarded libgpod-style
catalog to MA446. IORegistry's SCSI revision `1.62` is shown separately as a
transport revision, never mislabeled as stock firmware `1.3`. The verified MA446
catalog entry identifies the audio codec/DAC as Wolfson WM8758BG and labels that
value as model-derived in the immutable snapshot rather than directly probed; the
dashboard presents only the concise component name. Ambiguous cache records or
serial mappings return no claim. SoundBox also remembers confirmation using its stable
library persistent ID plus FAT volume UUID, and can migrate the already-proven
model from its own recovery plan. It deliberately does not use `DatabaseID`,
which Apple changes during normal rewrites. The confirmation defaults to
Cancel. Commit pins the mount and rechecks
the database, pending play counts, every source hash, and every destination. It
saves the original database/play-count bytes plus every replaced ArtworkDB and,
for full compaction only, ithmb file under `.soundbox/stock-ipod-recovery/`.
Incremental art records verified ranges and never overwrites a referenced slot.
It atomically copies new media, publishes artwork, writes iTunesDB last, and
archives consumed play counts. Failure restores the original database/artwork
set and removes only the exact new media files from that transaction. An
incomplete existing artwork set fails during the read-only plan instead of
being silently discarded. A playlist drop remains additive, while ordinary Sync
uses the committed shadow to remove only exact managed track records/media that
no remaining user playlist references. Stale shadow DBIDs must match their live
device path and hash before they can authorize cleanup.

Playlist deletion is a separate, explicit two-phase transaction from the
managed **Playlists** tab. Regular playlist rows and the exact Videos system
index have independent checkboxes so one guarded plan can delete any checked
set; right-click still offers an explicit single-playlist action. The read-only
plan removes selected regular user playlists, then reference-counts the union of
their tracks against every
remaining regular user playlist. The master library and Apple's built-in smart
playlists are derived indexes and deliberately do not count as ownership. Only
selected-playlist tracks with zero remaining owners lose their database record
and device media; pre-existing loose tracks are never swept. Thus an exported
`Collection` playlist naturally retains every song while deleting smaller
organizational playlists. Remaining artwork is compacted and all identity,
technical, rating, bookmark, and merged playback state survives.
The database/artwork recovery set is written first, the new iTunesDB is
published before orphan files are unlinked, and the Mac/source library is never
modified. Deleted device media is intentionally not retained in on-device
recovery because doing so would not reclaim storage; the confirmation says so
and defaults to Cancel.
Deleting Videos removes the canonical MHSD-5 smart-playlist record plus any
matching ordinary-MHLP smart alias Apple stored under a second ID. The device
snapshot always exposes the canonical identity. Both representations contribute
no media candidates, so the operation leaves every track and regular playlist
intact and validates all retained Apple smart rules before it can commit.

Clicking a stock-only iPod opens a read-only managed-device surface instead of
the raw FAT folders. Header-level **Device**, **Songs**, and **Playlists** tabs
show identity/model/generation, storage and database health, merged playback
totals, recovery count, the exact indexed music records, and ordered regular or
smart playlists. The Playlists tab renders user-created regular playlists plus
Apple's narrowly allowlisted **Videos** smart index. The library root and every
other built-in smart list remain in the snapshot for database health and
preservation checks. Videos can be selected and deleted through the same guarded
transaction without removing track records or media; Music, Movies, TV Shows,
and Audiobooks remain immutable. A validated writable MA446 exposes
**Delete checked playlists…** beneath that list; the context menu retains an
explicit single-playlist delete. Device refreshes preserve the active tab for repeated
operations. The playlist table intentionally omits Artist so its expandable
playlist/track title column receives all spare width. Identity values preserve
their provenance: mounted metadata,
Apple's local device record, serial-derived model, or model-derived hardware
catalog. Parsing runs off the UI thread and eject refuses to race it.
Rockbox and generic USB devices retain the existing filesystem browser.

On a schema-3 dual device, Songs and Playlists are composite rather than
stock-prefixed views. The worker verifies the small manifest-owned M3U8 payloads
and combines them with the parsed iTunesDB without reading managed audio. Songs
and playlist rows report `Both`, `Stock`, or `Rockbox` according to which
firmware database recognizes them; expanded playlist children retain exact M3U8
order and carry the same Type label. Stock-only legacy rows remain visible but
cannot bypass schema-3 ownership deletion. Apple smart indexes remain protected
internally and are omitted from the composite metrics/list.

The stock dashboard layout is also intentional UI contract. Device details use
a 2×2 grid—Identity beside Storage, Database beside SoundBox—to avoid needless
vertical scrolling. Songs shows exactly Title, Artist, Format, Size, Time,
Plays, Skips, and Type; Album and internal Location are omitted, and Title
receives all additional window width. Playlists shows only Playlist/Track,
Tracks, and Type; there is no Artist column, and Playlist/Track stretches. Long
stock planning/commit status text must remain readable rather than being clipped.
Redundant hover text is prohibited globally; a tooltip is reserved for hidden
diagnostics or genuinely additional information.

Classic stock firmware itself inserts an **All** level beneath each Artists
entry before showing that artist's songs. SoundBox owns clean metadata and the
artist index, not that firmware navigation layer; the accepted behavior must not
be “fixed” by damaging album or artist fields.

Research references:

- [libgpod overview](https://sources.debian.org/src/libgpod/0.8.3-17/README.overview)
- [libgpod model and serial-suffix catalog](https://github.com/strawberrymusicplayer/strawberry-libgpod/blob/master/src/itdb_device.c)
- [libgpod file-layout API](https://tmz.fedorapeople.org/docs/libgpod/libgpod-File-handling-functions.html)
- [iPod Video WM8758BG teardown identification](https://www.eetimes.com/teardown-finds-few-changes-to-new-video-ipod/)
- [Apple iTunes Library framework](https://developer.apple.com/documentation/ituneslibrary)
- [Clickwheel source](https://github.com/pdugan20/clickwheel)

The Apple framework is useful for reading a Mac's iTunes/Music library. It is
not a classic-iPod database writer, so it does not remove the need for a
separately validated `iTunesDB` implementation.

## Safety and validation

- Resolve the mount and confirm it is external before every write/delete.
- Pin a `DeviceSession` to the resolved mount identity and reject a disappear,
  remount, symlink escape, source overlap, or case/Unicode destination collision.
- Refuse any target that overlaps a source file or source-library parent.
- Never write below `iPod_Control` except through the guarded MA446-only
  `soundbox.stock_ipod` transaction. On dual boot it may be invoked only by the
  composite planner with coordinated recovery, rollback, and manifest-last
  publication.
- Existing unknown target files are conflicts and never claimed except through
  the baseline-bound, exact path/size/SHA-256 interrupted-generation recovery
  above. A differing manifest-owned file is required repair work and is
  atomically replaced.
- Copies land as `.sbpart` and atomically rename only after completion.
- Normal Sync cleanup is limited to manifest-owned SoundBox paths. A separate
  **Erase all files from device…** command is the only exception: it requires
  explicit confirmation naming the exact mounted volume, removes every child
  beneath that volume (including unknown content and the stale manifest), and
  then performs verified normal eject. The manifest is removed last, so a
  partial erase remains visibly invalid and cannot masquerade as success.
  Stock and dual-boot iTunes/iPod volumes never expose this command.
- Normal Verify checks managed sizes/references; Deep Verify rehashes every
  managed track, cover, and playlist.
- Eject flushes and rereads managed references after writers finish, requests
  normal macOS eject, and asks before any force-unmount. It begins only after
  finalization has completed every dashboard/sidebar read, and suppresses new
  polling through verification and the unmount outcome.

Unknown devices also expose a threaded import action. Selected audio or M3U8
entries copy in exact playlist order to
`~/Music/SoundBox Media/Unknown Artist/` by default, then register those owned
copies in SoundBox. The device and original Finder/Downloads files are never
moved, modified, or automatically removed.

The hermetic profile tests build all four layouts and compare every pre-existing
byte before and after detection/stock preview. The removable-volume fixture test
additionally snapshots device content, exports generated audio, verifies every
playlist path, and removes only the fixture trees. Volatile `.Spotlight-V100`
and `.fseventsd` indexes are expected to update whenever macOS observes a
filesystem write; they are not edited or cleaned by SoundBox. Every
nonvolatile pre-existing entry—including the full Pioneer tree in this
fixture—is hash-compared before normal eject.

## Remaining hardware boundaries

The MA446 path is physically accepted in both stock and dual-boot use: guarded
stock database/artwork writes, playback/reconnect recovery, destination-specific
format filtering, playlist/system-index deletion, Rockbox picture-free playback,
theme-native external art, firmware-owned database refresh, full-Collection
commit, incremental Sync, and exact visible playlist order have all run on the
real device. Rockbox's non-recursive catalogue intentionally remains flat;
zero-padded filenames carry the user-applied order while stock titles stay clean.

Future hardware gates are different iPod models/checksum families and any
separately designed schema-2 ownership migration. Those boundaries remain
read-only/fail-closed; they do not block normal use of the validated MA446.

## Real stock-hardware baseline (2026-07-31)

The first real target is an Enhanced 5th-generation black iPod Video
(`MA446LL`, originally 30 GB / 32 MB RAM), flash-modified to a 256.4 GB physical
disk and reporting 238 GB under stock firmware 1.3. It is Windows-formatted
FAT32 and currently has no `.rockbox` tree.

SoundBox's live read-only probe correctly classified `/Volumes/IPOD` as
`stock_ipod` and withheld Rockbox export. The refurb's
`iPod_Control/Device/SysInfo` is zero bytes, so the model string is blank until
the user confirms the physical About-screen value; this is never inferred from
capacity. The live library preview found 3,244 present audio files
and 19 missing: 2,624 WAV, 520 MP3, 82 AAC/M4A, 17 AIF, and 1 AIFF, totaling
125.9 GB.

Before any Apple/Finder sync, the complete pre-Finder `iPod_Control`, `Contacts`,
`Calendars`, and `Notes` trees were copied to the local git-ignored
`soundbox-ipod-ref/2026-07-31-pristine-stock-ma446ll-256gb/` fixture and
verified by path, size, and SHA-256: 13 files, 259,471,521 bytes, with no
missing files, extras, or content mismatches. A four-format oracle was staged;
the conservative first hardware write used MP3 and WAV through Apple/Finder.
SoundBox itself made no hardware write during the oracle capture.

This is an Apple-database baseline, not an untouched whole-volume baseline:
rekordbox was open at the first connection and automatically created its normal
root `PIONEER/rekordbox` export tree. Sonny deleted it immediately before the
first inspection, leaving about 720 KB of settings in `.Trashes/501/PIONEER/`.
The volume remained FAT32, and inspection found no Rekordbox artifacts beneath
`iPod_Control`.

Apple/Finder subsequently generated a three-track stock playlist from protected
SoundBox-derived copies. The iPod displayed and played the exact requested
order: GodLovesUgly (WAV), Scream Saver VIP (MP3), then Check It WIP (WAV).
Every renamed `Fxx` media file matched its input SHA-256, no accidental media
remained, and the reconnect capture's 180-byte `Play Counts` file recorded one
play only for GodLovesUgly with zero skips. The complete post-play fixture lives
under `soundbox-ipod-ref/2026-07-31-stock-first-play-reconnected/`.

The new SoundBox renderer parsed all four Apple-written database generations.
On a cloned post-play fixture it preserved the exact GodLovesUgly → Scream Saver
VIP → Check It WIP playlist order, folded GodLovesUgly's pending play into the
database, retained Apple smart-playlist rules (including their opaque header
word), and left every non-database fixture file byte-identical. A production
dry-run against the read-only real capture content-reused all three staged
sources, planned zero media copies, and changed no fixture bytes.

The first physical SoundBox commit followed the same conservative zero-copy
plan. Music.app was closed before planning; the live FAT32 MA446 database had
already absorbed GodLovesUgly's pending play and contained no `Play Counts`
file. The guarded commit content-reused all three media files, wrote the
database last, and created recovery copies both on-device and off-device. A
fresh parse of the physical iPod proved three tracks, exact GodLovesUgly →
Scream Saver VIP → Check It WIP order, GodLovesUgly play count 1, a database
hash matching the staged bytes, and byte-identical media. The volume then
ejected cleanly. Sonny then used the stock firmware to browse the SoundBox
playlist and deliberately play and skip its tracks. The reconnect database
remained exactly the SoundBox-written hash, both SoundBox and Apple playlists
retained the requested order, and the firmware emitted a matching 180-byte
`Play Counts`: Scream Saver VIP had one play and one skip, Check It WIP had two
plays, and GodLovesUgly had one new play. A final production dry-run folded
those deltas to totals of 1/2/2 plays respectively (and Scream's one skip),
reused all media, planned zero copies, and left the live database and play-count
file byte-identical before another clean eject. The complete ignored acceptance
fixture is `soundbox-ipod-ref/2026-07-31-soundbox-first-write-post-play-reconnected/`.

Selected tracks that match existing iPod media by content are also eligible for
a narrow metadata enrichment. SoundBox replaces only their title and artist
with its effective curated values; it does not recopy the audio or change dbid,
location, technical fields, ratings, bookmarks, or playback history. The
read-only confirmation reports the exact count before approval. Against the
preserved fixture this produces the natural four-track artist index `GRiZ`,
`DENNETT`, `Subtronics`, and `Zeds Dead, Subtronics`.

Later physical passes linked and displayed the curated stock artwork, enriched
artist/title records, and proved the format guard on Rekordbox `GENERES / HOUSE`:
the six included safe tracks played without a reboot while `Loot` and
`DONTTRUSTME` were omitted. Two checked playlists were then deleted together in
one successful guarded operation. The iPod is now a clean stock baseline with
zero indexed tracks and zero regular user playlists; the last verified iTunesDB
SHA-256 is
`c235f57378793d1db96d3f5b059bdb01e1262e04b9b54375a2c78cce6770123c`,
`Play Counts` is absent, and eleven recovery points remain. A later Favorites
export added 73 safe tracks. The first Videos deletion attempt removed only
Apple's ordinary-MHLP smart alias because the real database used a different ID
for the canonical MHSD-5 record. The corrected snapshot now prefers the
canonical ID and the planner removes both representations together. The
2026-08-01 physical retry deleted canonical ID `6873052597665276463`, merged 26
pending play-count entries, removed zero track records and zero media files,
and preserved all 73 Favorites tracks plus Audiobooks, Movies, Music, and TV
Shows. The resulting iTunesDB SHA-256 is
`87bf84a025e18b52ee7991f424b461ee12ca7b32f414bab95132b3206fe644b0`,
`Play Counts` is absent, and seventeen recovery points remain. Later syncs
continued to parse no Videos record; the final overnight stock-firmware soak and
post-soak reconnect capture remain. On-The-Go is generated by firmware and is
not present in iTunesDB.

The 2026-08-01 final stock pass subsequently reached 100 indexed tracks and
exactly 100 physical media files totaling 2,681,783,526 bytes: Favorites 73,
SONNY.AIF 19, and Indie 10, with no Videos or `Play Counts`. A partial-shadow
failure that duplicated 18 SONNY files was repaired recovery-first; exact hashes
proved every duplicate before removal. The corrected planner reuses selected
device media missing from a playlist-scoped shadow. Two final Spaceman cover
changes then proved both a genuine 100,000-byte RGB565 update and zero-write
reuse of matching pixels left in free slots. The last verified iTunesDB SHA-256
is `201655d4b65b5c95dd626640c2bcceca2b7bb7e481cf8fc86072771539c4ed9e`;
ArtworkDB is `35150373971f15831fc9fbedfdbb1af8028d26efbffab3079aba1dc34a38b935`.
Shadow generation 5 matched the complete state before a clean unmount for the
overnight stock-firmware soak.
