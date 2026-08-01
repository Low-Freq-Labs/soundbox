# SoundBox device layouts

Current as of 2026-07-31. Read this with `GOTCHAS.md` and the write contract at
the top of `soundbox/sync.py` before changing device detection or export.

## The simple model

SoundBox classifies a mounted external volume by the folders already present.
Detection is read-only. The profile describes what SoundBox may safely do; it
does not make a device writable by itself.

| Profile | Existing layout | SoundBox behavior |
|---|---|---|
| Rockbox | `/.rockbox/` | Export `/Music`, `/Playlists`, and `/.soundbox` only when the filesystem is FAT32 |
| Stock iPod | `/iPod_Control/` with an iTunes database area | Read-only readiness by default; guarded playlist sync only for the explicit MA446/MP3/WAV allowlist |
| Dual boot | Both layouts | Rockbox-style export on FAT32; never touch the stock database |
| Generic USB | Neither layout | Use the same Rockbox-style folder/playlist export on FAT32 |

The mounted on-disk layout selects the export contract automatically; SoundBox
does not guess which firmware the user most recently booted. A `/.rockbox/`
marker wins operationally even when Apple's `iPod_Control` remains
on disk: SoundBox exports the complete source formats through the Rockbox path
and keeps the stock database read-only. Rockbox media under `/Music` is not
automatically indexed by stock firmware, so an Apple fallback boot cannot
encounter unsupported Rockbox files. A future shared-media dual-library mode
would require its own explicit design and validation; it must never emerge
accidentally from normal Rockbox export.

Non-FAT volumes remain browseable but do not accept the Rockbox-style drop.
Unknown or failed probes never grant extra capability.

## Rockbox layout

The current export is ordinary files, so it does not depend on the Rockbox
tag database:

```text
/
├── .rockbox/                 existing firmware, never edited by export
├── Music/Artist/Track/       copied audio plus cover.jpg
├── Playlists/*.m3u8          UTF-8, drive-root-absolute audio paths
└── .soundbox/manifest.json   v2 path ownership, hashes, membership, generation
```

The official Rockbox iPod manual describes a FAT32 iPod partition and the
firmware at the volume root. Its playlist catalogue setting defaults to
`/Playlists`. Users may play exported playlists through the file browser
without initializing Rockbox's optional tag database; database users can run
Rockbox's update/initialize workflow afterward.

Reference:
[Rockbox Manual for iPod Video](https://download.rockbox.org/daily/manual/rockbox-ipodvideo/rockbox-build.html).

## Approved dual-library design (not implemented)

Dual boot must expose the broad Rockbox library and a stock-safe subset without
duplicating the collection. The approved single-copy layout is:

| Track capability | One physical location | iTunesDB | Rockbox playlist |
|---|---|---|---|
| Stock-safe MP3 or PCM16/44.1-kHz stereo WAV | `iPod_Control/Music/Fxx/` | indexed | references the same file |
| Rockbox-supported but stock-unsafe | `/Music/...` | absent | referenced |
| Unsupported by both destination profiles | not exported; reported | absent | absent |

This requires a new composite planner and unified ownership manifest. Rockbox
playlists must span both path classes; safe tracks need Rockbox artwork keyed to
their scrambled physical filename; stock and Rockbox playlist ownership must be
reference-counted together; recovery, artwork, database-last publication, and
manifest-last publication must be one coordinated transaction. The current
implementation intentionally does none of this: `/.rockbox/` wins, uses the
ordinary Rockbox exporter, and leaves iTunesDB read-only. Never simulate the
future mode by running the stock and Rockbox exporters independently or by
copying all safe tracks twice.

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

SoundBox curation is resolved before that destination check. **Delete from
Collection** is a global export veto and **Remove from Playlist** is a veto for
that stable playlist ID; neither track reaches stock format inspection, artwork,
copy planning, or iTunesDB rendering. Consequently, the stock confirmation's
selected count is the effective SoundBox playlist—not raw Rekordbox membership—
and curated-away tracks do not appear misleadingly in the firmware-omission list.

Curated SoundBox JPEGs are also the stock-art source of truth. A background
worker pre-renders MA446's RGB565 formats 1028 (100×100) and 1029 (200×200)
into a content-addressed AppData cache, pausing during playback. Sync therefore
only hashes/assembles prepared pixels: identical images occupy one segment in
each `F1028_1.ithmb`/`F1029_1.ithmb`, while ArtworkDB has the per-track MHII
records stock firmware expects. SoundBox validates every iTunesDB MHIT link,
ArtworkDB song ID, MHNI format/offset/size, and ithmb boundary before showing
the confirmation. It never modifies tags or embeds images into source audio.

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
saves the original database/play-count bytes plus every replaced ArtworkDB and
ithmb file under `.soundbox/stock-ipod-recovery/`, atomically copies new media,
atomically replaces the artwork files, writes the validated iTunesDB last, and
archives consumed play counts. Failure restores the original database/artwork
set and removes only the exact new media files from that transaction. An
incomplete existing artwork set fails during the read-only plan instead of
being silently discarded. Sync itself remains additive: unrelated
tracks/playlists are preserved and no implicit orphan pruning occurs.

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
Deleting Videos removes only that MHSD-5 smart-playlist record: it contributes
no media candidates, leaves every track and regular playlist intact, and
validates all retained Apple smart rules before the plan can commit.

Clicking a stock-only iPod opens a read-only managed-device surface instead of
the raw FAT folders. Header-level **Device**, **Music**, and **Playlists** tabs
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

The stock dashboard layout is also intentional UI contract. Device details use
a 2×2 grid—Identity beside Storage, Database beside SoundBox—to avoid needless
vertical scrolling. Music shows exactly Title, Artist, Format, Size, Time,
Plays, Skips, and Pending; Album and internal Location are omitted, and Title
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
  `soundbox.stock_ipod` transaction. Dual-boot stock databases remain read-only.
- Existing unknown target files are conflicts and never claimed. A differing
  manifest-owned file is required repair work and is atomically replaced.
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
  normal macOS eject, and asks before any force-unmount.

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

## What remains hardware-gated

A real Rockbox iPod still needs checks for playback, theme artwork, playlist
order/folder nesting, database refresh, and full-library scale. The stock engine
has completed SoundBox-authored write/play/reconnect recovery, visible artwork
and metadata, destination-specific HOUSE playback without a reboot, full
orphan cleanup, and two-playlist deletion in one guarded transaction. Physical
Videos-system-list deletion is the sole remaining stock cleanup check. Other
iPod models, checksum families, destructive reconciliation, the composite
dual-library design above, and full-library scale remain future work.

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
`Play Counts` is absent, and eleven recovery points remain. The device was not
mounted at session wrap. Videos still exists as an Apple smart index until the
new System-playlist deletion UI is exercised on hardware; On-The-Go is generated
by firmware and is not present in iTunesDB.
