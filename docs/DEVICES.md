# SoundBox device layouts

Device, song and playlist labels use **iTunes**, **Rockbox**, or
**iTunes/Rockbox** for their database availability. Internal stock/dual profile
identities and the separate generic-USB Export contract remain unchanged.
Sidebar rows use the connected name, layout and cached total volume capacity:
`SONNY · iTunes iPod · 1.0 TB`, `Rockbox iPod`, `iTunes/Rockbox iPod`, or `USB`.
Sync status, filesystem warnings and available space remain in the tooltip.
An unavailable capacity is omitted; free space is never substituted for it.

November9 source captures immutable export inputs before worker admission.
Explicit CDJ/Rockbox drops include selected musical records; stock/dual/resync
retain the complete lightweight identity/display and playlist inventory needed
for managed-device reconciliation. Every snapshot separately retains all source,
audio and analysis paths, including hidden/unselected songs, through planning and
commit overlap checks. Workers materialize private objects from captured bytes;
later GUI edits cannot change queued intent. Separate drops keep their separate
captured generations; playlists within one drop still share a plan. Native
readback, device identity and firmware recovery order remain.
Existing stock-catalog Sync captures selected native playlist ID, UUID and
deletion-state scalars. Full iPod capture retains those lightweight headers for
other playlists too, so a later export can preserve their verified associations
without copying the native membership graph. Generic
USB capture and its explicit Export workflow are unchanged; Sync belongs to
stock/Rockbox iPods.

iPod exports work with automatic metadata updates paused or running. An ordered
canonical checkpoint verifies saved selected metadata/membership before admitting
the frozen generation. Only selected actual source replacement/locator, registration/membership,
structural and local grid admission work delays that capture; unrelated metadata
and cue queues do not. Source/repository/restore/mount changes still reject stale
requests. Later ordinary changes remain saved for the next Sync. Stock exports
use all seven supported Apple metadata fields; Rockbox derivatives retain their
accepted title/artist profile. A no-op still verifies its current device readback
before saving an optional Sync association. Partial or blocked output reports
attention rather than a successful completion.
CDJ compatibility intentions do not determine iPod media eligibility. Dormant
queued/failed policy jobs with no physical effects do not block iPod export,
including selected sources on another external volume;
fresh worker checks retain actual replacement/locator recovery fences. Dual
device Delete/Order also uses its own saved checkpoint and exact confirmed
manifest rather than the CDJ-wide metadata barrier. Successful cancellation is
quiet and clears its own controls only after joined worker retirement; failures
and recovery warnings still require attention.
The read-only external-source probe preserves native/AppData and recovery guards;
it grants no external source-replacement permission. Its isolated correction is
recorded in the [critical audit](critical-audit-2026-11.md#external-source-transition-probe).
Rockbox-only Sync resolves the existing schema-2 managed keys against the current
source and pins the observed manifest bytes/absence. If every managed playlist
is missing locally, it requests explicit selection; removal-only Sync still needs
its own durable removal fences and is not inferred from an empty selection.
Unmatched existing iPod entries keep Sync pending even after matched metadata
and artwork are clean. Without a validated membership witness they remain
preserved, with an explicit warning; equal names/counts do not authorize removal.
This source change does not establish a new installed or physical acceptance;
see [HANDOFF](HANDOFF.md).

Reviewed November8,2026 against the completed cleanup and its preserved device
contracts. Accepted personal build169 includes the source safeguards below;
[HANDOFF](HANDOFF.md) records exact signed provenance and latest observed app state.
The earlier November5 responsiveness evidence describes build167
(runtime3fb0ff60…, signed base0075ab1/dirty=true). Build165/runtime7ffde716…
and build163/published USB source `cf2ef5b` are earlier deliveries.
Installed identity and remaining gates are in HANDOFF. Dated physical fixture
timings are in the [November4 USB audit](performance-audit.md#physical-usb-fixture-timings--november4).
Older audits and hardware observations retain their
dated scope; current installation and acceptance are in `HANDOFF.md`.
Read this with `GOTCHAS.md` and the write contract at
the top of `soundbox/sync.py` before changing device detection or export.

Current source shares descriptor-pinned device publication: exclusive temporary
files remain open through verification, flush/fsync and atomic replacement;
cleanup removes only the operation's own stage. In-place artwork ranges reject
symlinks and multiply linked targets. Optional Music/Playlists clutter cleanup
also rejects redirected directories, and filesystem-device erase binds the mount
identity before confirmation. Malformed disposable shadows/derivatives become
cache misses; model detection bounds each SysInfo read. These checks preserve
existing ownership, source separation and firmware commit order. Validation and
installed status are recorded separately in [HANDOFF](HANDOFF.md).

FAT32 may assign a different file ID when an empty temporary first receives data.
Publication ownership must use the current still-open descriptor and no-follow
named entry, including cleanup; its empty-file ID is not a stable witness.
Disposable FAT32/exFAT image checks complement copied-catalog tests on APFS.
They do not establish physical firmware playback or hardware Sync acceptance.

## The simple model

SoundBox classifies a mounted external volume by the folders already present.
Detection is read-only. The profile describes what SoundBox may safely do; it
does not make a device writable by itself.

| Profile | Existing layout | SoundBox behavior |
|---|---|---|
| Rockbox | `/.rockbox/` | Export `/Music`, `/Playlists`, and `/.soundbox` only when the filesystem is FAT32 |
| iTunes | `/iPod_Control/` with an iTunes database area | Guarded Sync on writable FAT32 with observed Video v25or117/checksum0 or signed Classic v117/HASH58; new audio uses MP3/PCM16 WAV |
| iTunes/Rockbox | Both layouts | Reference-counted single-copy sync on FAT32 with the same observed stock database profile |
| USB | Neither firmware layout | Recoverable classic + Plus playlist/collection export on FAT32/exFAT; hardware limits below |

The new-copy iTunes profile above is narrower than Classic hardware decoder
support; it does not currently admit AAC/ALAC/AIFF, which
[Apple lists for Classic](https://support.apple.com/en-gb/112601).
Excluded containers are omitted
before whole-file acquisition or cloud hydration. This is a bounded application
profile, not a claim that Classic hardware cannot play those formats. Accepted
MP3/WAV still require actual stable byte/header proof. PCM24 WAV remains outside
iTunesDB and may enter the Rockbox side of a dual export. Adding another format
also needs correct native track fields and versioned retained-payload promotion.

The mounted on-disk layout selects the export contract automatically; SoundBox
does not guess which firmware the user most recently booted. A `/.rockbox/`
marker without `iPod_Control` selects ordinary Rockbox export. When both layouts
exist, observed stock-format/FAT32 admission selects the separate composite planner; neither
ordinary exporter is allowed to approximate it. Rockbox media under `/Music`
is not automatically indexed by stock firmware, which is why only the validated
shared subset enters Apple's database.

Non-FAT volumes remain browseable but do not accept the Rockbox-style drop.
Unknown or failed probes never grant extra capability.

Device-specific export work waits for detection. Launch keeps the mount watcher
and read-only discovery active, while full export backends and artwork cache
indexes load on demand. Device pages are constructed only for a requested device
view; passive resizing, theme changes, backup and shutdown never create them.
Export stays disabled without a capable attached target. Standalone-track menu
actions use that target's busy state, including during another USB's format.
The common multi-device Export queue applies each target's own capture rules.
A generic USB source-save refusal cannot veto a ready iPod target. Earlier device
results remain visible, pending source edits remain saved, and another
export/format retains its progress UI. Cancel retires only its request and batch.
Empty scans do not construct export playlists. After
detection of an iPod/Rockbox target, the worker obtains GUI-captured inputs before
checking drift; capture failure remains `Sync status pending` for those profiles.
Generic USB detection captures no export playlists and reads no native catalog
for drift. Canceled/stale captures cannot start
dashboard preload. Already loaded modules and reusable local caches may remain
in memory after disconnect; export artwork preparation stops cooperatively.

### Later iPod efficiency/correctness work

The [November4 iPod audit](performance-audit.md#ipod-deep-audit--november4-after-usb-testing)
covers stock, Rockbox-only and dual-boot paths using generated/private offline
fixtures. It reuses operation-local catalogs/source contracts, preserves shared
playlist ownership, isolates colliding artwork folders, freshly verifies
interrupted candidates and moves remaining device reads out of GUI admission.
Explicit Rockbox Sync checks source metadata/display even with unchanged playlist
membership; its idle badge remains playlist-based. Retained-media inventory is
not a fresh integrity certificate. Existing codec, firmware, recovery and source
boundaries remain. No iPod hardware was available. Its preceding runtime26f15fb4…
passed2695tests; the subsequent whole-app runtime7ffde716… passed2779tests.
That build167 checkpoint passed2795tests with exact fresh state/icon. HANDOFF has precise
delivery identities.

The November4 responsiveness follow-up removes one redundant parse of the final
patched stock database: sync and deletion reuse that exact parsed result for all
their existing semantic checks. Stock source presence/canonical paths are shared
only within each membership phase. The second phase observes every unique path
freshly after acquisition/reconciliation and rejects disappearance or redirection;
source acquisition, format/content proofs and commit guards remain independent.
Full rendering still runs for semantic no-ops before retaining original bytes.
Generated50000-membership planning measured4013.40→1206.89ms; this is local CPU/
filesystem fixture evidence, not iPod throughput or firmware acceptance.

### Native CDJ USB export

The later November4 [USB audit](performance-audit.md#usb-deep-audit--november4-later-testing)
was delivered in personal build165 and remains in build167. It fixes format
confirmation, permits
another USB to export during a confirmed format, streams native erase stages and
loads USB Info covers from their selected device references. Formatting reserves
every captured partition of its physical disk; discovery waits until the native
erase worker retires. The ordinary export lane remains serialized and retains
fresh target/source and transactional guards. The preceding USB pass closed2639tests and both package
profiles; the later iPod pass above retains these fixes. That audit records its
dated state/icon preservation; HANDOFF records the later current delivery.

October29: the dedicated generic-USB exporter maintains classic Device Library
and Plus together on writable FAT32/exFAT. It preserves unrelated native records
and playlists across rekordbox/SoundBox switching, including existing differences
between the formats. Drops export selected new/changed inputs directly. Export
opens playlist/folder checkboxes on the left and connected devices on the right;
one target is preselected and several targets may be checked. One action queues
those targets through the existing serialized exporters, with source/playlist and
mount/UUID checks before each target. Cancellation/shutdown clear pending targets;
iPod planning and commit stay guarded without an extra normal-action approval.
Folders start collapsed. Large themed device rows show USB/iPod icons. Generic
USBs have no Sync-needed, pending-change or database-state badges. Explicit
exports update the selected playlists only; unrelated source changes are not
propagated into previously exported playlists. Native catalogs are read only as
needed for the selected update or an explicit browser visit.

Replacing a selected USB playlist removes its old memberships and prunes removed
songs only when no remaining playlist in either native format uses them. Shared
assets and unrelated collection-only songs stay. Removed assets are rollback-backed
until durable commit, then their exact backup payloads retire to free USB space;
interrupted retirement resumes. There is no whole-drive orphan sweep. New copies
still need peak transaction free space. Retained songs are inventory, not an audit.
Interruption resumes internally, and newer coherent native exports supersede old
pending catalogs. Fresh source metadata/cues and exact new
audio copies remain required. Ordinary stock/dual/Rockbox PIONEER guards remain.

USB tabs are Overview, Playlists, Category, Sort, Column, Color, My Settings.
General is a compact two-column form. Category/Sort edit active lists/order;
Column controls the default right column; Color edits eight titles. My Settings
has44 player/mixer fields and explicit Save to Global / Load Global, with the
global profile in SoundBox Settings seeded from the actual USB. Unrelated exports
preserve profiles/unknown bytes. Native name differs from the real volume name.
The generic USB context menu is Export, Import My Settings, Reformat USB, Eject.
Reformat confirms the physical external USB, real name and FAT32/exFAT MBR choice.
Source separation includes native analysis locations; post-erase checks verify the
physical disk, MBR, one first data partition, filesystem, volume and mount before
initialization. A same-filesystem quick erase reuses an existing full-capacity
single first MBR partition only when its partition type also matches. Other
layouts or filesystem changes repartition. Per-probe/erase timings are logged.
HFS+ remains excluded. Dated physical format evidence is in the
[November4 USB timings](performance-audit.md#physical-usb-fixture-timings--november4)
and [later USB audit](performance-audit.md#usb-deep-audit--november4-later-testing);
HANDOFF records current runtime/gates. The earlier release's format tests were mock-only.

Explicit USB settings saves capture the complete desktop source/analysis path
set before worker admission, preserving the overlap boundary without copying
unrelated songs, cues or playlist catalogs. Selected playlist export still freezes
its library/curation snapshot separately. Neither change permits automatic
settings propagation or weakens the worker's fresh target/source checks.

Headers use26 points; detail/song rows and empty stripes match the regular24-point
browser. USB columns share browser state. Activation validates the selected actual
USB audio/analysis in the device lane and uses a separate device track ID without
Mac audio/source cue overlays or desktop write authority. Views remember per-volume
tab, hierarchy, selection and scroll. Folder/playlist clicks expand or collapse;
only track rows receive selection highlighting. Explicit browser requests refresh
native snapshots when switching exporters. Display-only snapshots include stable
WAL pages copied locally, without live USB SQL or SHM locks. Publication still
rejects active/unreconciled companions. Worker ownership lasts through finished.
Sonny reports successful playback, artwork and waveforms; exact firmware/full
control matrix/newer OneLibrary hardware and distribution gates remain. See the
[CDJ contract](cdj-usb-export.md) for source guards, staging and recovery limits.

Connection presentation separates visibility from readiness. Debounced `/Volumes`
notifications and a 250-ms cached kernel mount-table check request discovery even
during playback or an eject-ready hold. The latter uses Darwin
`getfsstat64(MNT_NOWAIT)` without opening mounted paths, so mounting over an
existing directory cannot strand detection behind probe backoff. These are hints,
never external-device/write authority. A busy lane retains the request and retries
on worker retirement. The three-second local `/Volumes` timestamp fallback remains;
unchanged mount/timestamp state starts no idle discovery worker. Failed probes
retain 3–30-second backoff, bypassed by mount changes. An OS-identified iPod can
mount before its firmware folders appear: its provisional generic-USB profile
rechecks through that backoff until the existing layout identifies it. This adds
no profile probes or idle retries to ordinary generic USBs and grants no write
capability. Connected iPod/Rockbox drift stays on its
three-second poll. Known generic-USB-only connections have no recurring mounted-path
scan or source-event scan. Mount changes, retryable discovery and deliberate browser
requests may refresh connection/storage facts; they do not compute sync state.
Ordinary unmounted folders retain the 30-second attachment grace.
Verified external mounts appear disabled as `NAME · Checking…`. The first iPod/Rockbox scan
computes sync drift before publishing the selectable row at QThread.finished;
unknown/failed iPod/Rockbox checks say `Sync status pending`, never imply a clean result.
Generic USBs become usable after mount/filesystem detection with no sync badge.
Stock/dual dashboard preload follows in the same serialized device lane, with
loading only in the bottom status after the row becomes ready. Successful preload
clears that status directly, without another immediate drift scan. A cached
dashboard is presentation-only; selecting while it loads uses the same serialized
preload. Writes stay guarded by operation ownership and firmware capabilities.
Remounts invalidate stale views.

Background library reads and title/tag/database acknowledgements retain the open
device, its selected sidebar row, active tab, selection and scroll. They prepare
desktop data without reopening Collection. Retaining that page starts no new device
read or backend. A newer device choice wins over an older read result.
Disconnecting the active device or explicitly choosing a different desktop source
still selects the complete Collection view.

## Lifecycle correction under hardware acceptance

An idle mount is ejectable regardless of missing media, drift, or an earlier
failed Sync. Eject performs no library verification or final catalog read; it releases the browser
root and asks macOS for normal eject. A background read finishes first, with
replacement polls suppressed. Active writes remain owned until their safe
completion/rollback boundary. A macOS refusal is reported without a forced
unmount fallback.

Queued idle Eject retains the exact mount session through reader retirement and
the OS process outcome. A replacement using the same volume path cannot inherit
that request or lose its sidebar row to an old process result. Post-write
finalization owns its dashboard/sidebar reads separately from idle inspection;
Eject becomes available after that terminal refresh. Refresh continuations are
once-only and bound to their original mount and finalization owner. Disconnect
or remount reports refresh failure without claiming an old Sync succeeded for
the replacement, and an older completion cannot replace a newer result or its
progress. Physical commit truth remains separate from refresh success.

Cancel stops queued work immediately and cooperatively stops the active plan
or transaction. Device copy, hashing and Rockbox preparation check cancellation
between bounded chunks; an interrupted generic export keeps its verified partial
playlist/manifest so a later explicit export can resume. Cancellation does not
start another completion scan or claim a successful Sync. Captured source/catalog
waits retire their own request without cancelling another device owner or any
pending library edits. Foreground progress retains priority over metadata status.
Database publication and rollback finish their critical section before cancellation
returns. Normal Quit still drains accepted database writes.
Composite/stock failures retain recovery evidence; composite
journal states now explicitly distinguish executing, committed, rolled back,
and recovery required. Cancellation after successful publication leaves the
committed result intact. Final refresh failure does not undo that commit.

Local source acquisition tolerates cloud eviction only when exact captured
bytes match the expected digest. Stock firmware restrictions, source
immutability, guarded publication order, and the reserved PIONEER tree remain
unchanged. Generic exports retain verified completed files after an interrupted
batch and defer destructive cleanup until a successful retry.

The current lifecycle correction still requires the physical gates in
`ipod-lifecycle-audit-2026-09.md`; earlier hardware acceptance below does not
certify these new changes.

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
├── .rockbox/                 firmware and user-owned preferences
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
database users can run Rockbox's update/initialize workflow afterward. Sync never
writes Rockbox preferences or requires auto-update, tag display or a minimum
playlist limit. Those settings and their memory tradeoffs belong to the user.
Changing them does not signal managed-library drift. The Playlist Catalogue sorts
alphabetically. Because physical
Rockbox kept presenting that order despite controlled FAT timestamps, each
composite Rockbox M3U8 filename receives a zero-padded sequence such as
`01 - HOUSE.m3u8`. Stock iTunesDB titles and SoundBox device rows remain clean
and unnumbered. The Device Playlists tab can move managed rows and explicitly
publish that one order to both firmware paths without duplicating media;
reordering renames only the tiny owned Rockbox playlist files. A rename that
changes only capitalization, such as `DnB` to `DNB`, is still published with
the requested spelling: SoundBox recovery-backs the owned FAT entry, removes
that one old spelling, and writes the renamed playlist transactionally.
Stock playlists are MHYP/MHIP reference lists with 32-bit entry counts; the
pinned offline renderer has a regression proof for 10,000 entries and there is no
corresponding stock-firmware preference for SoundBox to set.
SoundBox never writes `config.cfg`, its staged/old companions, or `database_*.tcd`.
The dashboard observes the saved auto-update preference without asking to enable
it. When enabled, a stale index can indicate a pending Rockbox restart; with it
off, database maintenance remains an optional manual Rockbox operation.

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
iPod with the supported observed stock database containing both layouts. A new or changed source is fully normalized and
verified during planning, which retains only its reproducible source/output
contract. An unchanged committed source instead reuses the unified manifest's
payload identity after canonical path, source size/mtime, effective title/artist,
and per-file media policy match. Normal Sync ignores ctime-only metadata churn
for both local and cloud files, retaining the original proof timestamps rather
than certifying those bytes anew. This steady-state decision is stat-only: it does not
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
or output hash mismatch. Normal Sync validates schema-3 inventory/DBID/path
identity in metadata, retaining unchanged device audio/art without per-file
stats or tree walks. Affected paths retain live ownership/size checks; desired
changed derivatives receive exact Mac-side verification. Explicit Repair audits
the full inventory and restores absent owned Rockbox audio/art, while refusing
unexpected occupants. Verify checks references/sizes; Deep Verify additionally
hashes every managed file using the schema-3 inventory. Commit rechecks every planned source stat baseline before its first
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
deferred from the initial plan into commit preflight, where every
candidate must match the freshly derived payload before the first device write.
Every adoption/preflight freshly hashes each affected candidate with stable
descriptor/path identities and cancellation checks. Same-size FAT edits can
retain all reported epochs, so old interrupted-verification JSON cannot
authorize reuse after a USB reset. Historical proof files remain untouched;
no new per-file proof JSON is written. Recovery verification contributes to visible sync progress. The
device ownership manifest still changes only at the final successful
publication.

For ordinary stock, dual and Rockbox/filesystem operations, device-root
`/PIONEER` is an external-ownership boundary. Native CDJ libraries may coexist
there and their bytes contribute to volume capacity, but these exporters do not
descend into that tree for drift, accept it into schema-2 or schema-3
ownership, verify or clean it, import from it, or mutate it through the device
browser or a transaction. A stale or tampered manifest row beneath `/PIONEER`
is discarded in memory without inspecting the payload. FAT-style case variants,
lexical aliases, and symlink redirects into the tree fail closed. Consequently,
rekordbox changes below `/PIONEER` cannot independently surface **Sync needed**.
The dedicated CDJ pilot above has its own scoped contract. The legacy filesystem
erase backend still requires explicit whole-volume confirmation; the native USB
menu exposes Reformat USB instead. Stock and dual iPods expose neither operation.

Normal dual-iPod Export/Sync and playlist drops authorize their guarded plan
and commit directly; no second confirmation or routine success modal is shown.
A separately requested destructive Delete retains its initial concise consent.
That explicit Delete approval is authoritative: SoundBox removes the checked
identities, reconciles other currently available managed-playlist changes in
the same transaction, and retains an unrelated unavailable playlist from its
last committed manifest snapshot when its retained members remain available.
Unresolved retained members refuse rather than disappearing from the rebuilt
playlist. It never requires a separate Sync before the requested deletion.
Accepted drops are identity-deduplicated FIFO jobs. Every playlist accepted in the initial
drop starts in one generation. Playlists accepted while a prior job is busy
are coalesced into the next guarded plan/commit batch, avoiding repeated
iTunesDB parsing and repeated validation of the same baseline. SoundBox carries
one cumulative progress bar across the queue and shows one combined completion
nonmodal summary only after the queue drains. Each follow-up batch reuses the
just-committed Collection media and ordinarily writes only playlist membership
or genuinely new audio. A failure cancels every unseen queued job and reports
that cancellation; SoundBox never surprises the user by reviving an old write
after a later drop.

Completed source hashes survive failed planning in a disposable Mac-side proof
journal. Reuse requires exact canonical path, filesystem/inode, size, mtime,
and ctime; cache loss falls back to a complete read when a fresh proof is
required. Normal Sync's unchanged manifest contracts do not require that fresh
proof, including for dedup of already-owned sources. Relocated, new and changed
sources still do. Their timestamp drift between planning and commit receives a
bounded exact-hash check before any device write, refreshing timestamps only
when planned bytes match. Unchanged retained contracts tolerate ctime-only drift
without refreshing their evidence. A content change or repeatedly changing baseline refuses.
An unchanged, verified stock payload reuses its known format compatibility
without reopening the source; new/changed payloads retain full format checks.

The planner emits bounded media, retained-playlist, stock-library, Rockbox,
artwork, playlist, and ownership stages. Composite planning maps to 0–30%,
guarded device work maps to 30–80%, and required dashboard/sidebar/drift finalization owns
80–100%. The displayed value is monotonic across every phase transition and
queued job; it cannot reach 100% while a SoundBox worker still owns the target.
Internal planner units drive only the 0–30% percentage bar; planning presents
the animated `Planning iPod...` label without a `#/#`, because those units are
neither tracks nor device writes. The counter appears only under the explicit
`Syncing audio` label for committed audio units. Confirmed multi-job
queues give every job a complete proportional slice rather than leaving gaps
between planning and publication.

The open Device, Songs, and Playlists pages retain one immutable last-verified
snapshot while planning, syncing, deleting, or performing the post-operation
read. A successful read replaces all three surfaces together. A transient read
failure keeps that snapshot visible and reports that it is the last verified
state; it must never clear individual tabs into a misleading blank view.
The shared device-header action opens Export for a clean or unassociated iPod;
pending changes to a current linked iTunes playlist select Sync. Managed
Rockbox/composite changes retain their separate pending checks.
iPod context menus always retain an explicit Export playlists action. A stock
Sync action needs a current regular playlist association; deleted bindings and
system indexes alone cannot force Sync. Other managed iPod layouts retain their
explicit recheck. Generic USB always uses its separate Export selector.
Applying playlist order adds device-presentation fields to schema 3.
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
cleanup rows during explicit Repair or journal-backed interrupted recovery. This includes directory sidecars left after the final managed
track disappears; after commit, only empty child directories are pruned. Empty
schema-3 libraries remain valid cleanup plans rather than stranding that clutter.
AppleDouble files are opportunistic cleanup, not library drift, because Finder
may recreate them immediately after a successful export. Normal polling and
Sync do not walk these trees for clutter; recovery journals identify interrupted
transactions. Explicit Verify/Repair discovers unjournaled `*.sbpart` files.

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
The index pins device/inode, size and both change clocks. Cache misses capture
one stable image epoch and use those exact bytes for both hashing and encoding;
one concurrent replacement retries once. The v7 profile rebuilds potentially
misassociated older derivatives without modifying curated images.
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
guarded composite planner validates them.
During commit, animated `Syncing audio...` status is followed by a separate
`#/#` field and percentage bar. The count covers audio files actually copied,
replaced, or removed; a 37-track sync-to-zero therefore advances 0/37 through
37/37, while a database/playlist-only commit hides the inapplicable counter
instead of showing 0/0. After a successful commit, the selected Device, Songs,
and Playlists snapshot refreshes before the sidebar free-space/drift scan under
an explicit `Finalizing iPod...` state. Routine completion is nonmodal only after
both workers finish/join and their owning attributes are cleared. At that
terminal boundary SoundBox releases operation ownership, enables Eject, retains
concise included/skipped counts and releases polling. Genuine failure/committed
warning dialogs retain the hold only while attention is presented. Eject does
not cancel or complete dashboard work—the finalization scanner is already done.
An up-to-date/no-write Sync and already-absent Delete use the same terminal
boundary without an informational OK. All displayed surfaces update without a
navigation round trip; status animation width remains stable.

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
ordinary Sync-needed state. Accepted planning then reads that bounded playlist
and accepts only append-only paths that resolve to existing schema-3 managed
media. Incoming additions apply only to SoundBox-owned custom playlists;
rekordbox playlists remain managed by rekordbox and an iPod edit to one produces
a conflict rather than a local overlay. A SHA-256-verified historical M3U8
prefix binds device paths to the manifest's stable track IDs, so removal or
relocation of an old Mac source does not mislabel an existing iPod song as a
new import. Newly added membership still requires a current available source.
The same authenticated IDs rebind old source paths in the planning view while
preserving original device path/DBID/hash ownership and recovery bytes. A moved
source is hashed on the Mac before reusing an unchanged derivative. If cloud
hydration changes its metadata during that read, one bounded re-hash verifies
the newly downloaded file against a stable stat baseline. A continuously
changing file still fails closed; changed bytes take the normal verified
replacement path. Byte-identical old copies that
now resolve to one canonical source reuse one managed record, leaving surplus
records to ordinary reference-counted cleanup. Differing old hashes fail closed.
macOS CloudStorage ENEEDAUTH reads receive at most two cancellable three-second
waits to let a waking provider load its account. Every retry restarts the complete
read/hash; it never accepts partial bytes or a stale source proof. Persistent
unavailable/authentication-blocked sources stop with their path and a cloud-drive
availability hint. A planning or commit-preflight failure precedes device writes;
later failures report the actual coordinated rollback or committed outcome.
SoundBox does not open Finder; the retry does not repair a server outage or
expired account credentials.

Custom playlist reconciliation keeps the first occurrence of each track ID,
canonical source path, or byte-identical source file. This also removes older
custom duplicates. Normal Sync reuses matching committed source hashes for
already-owned size-matched candidates; other candidates are hashed on the Mac.
Titles and artists are never used to merge distinct versions. Both firmware playlists
receive the resulting sequence. Unknown/unowned paths are not imported, and
device-side deletion/reorder edits remain a closed conflict. The exact local
membership transition is checked against concurrent edits and durably saved
before target writes. Save failure restores the in-memory list and stops the
device phase; device failure leaves the saved membership as a truthful retry
against the still-present/restored Rockbox append.

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
allowlist. The stock validator controls only which tracks may
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

SoundBox admits its stock writer from the connected writable FAT32 mount and
observed uncompressed little-endian `iTunesDB`: Video version25or117/checksum0 or
Classic version117/HASH58. Both profiles remain supported. Classic verification
binds the actual USB ancestor of this mount’s BSD node; another connected iPod’s
ID cannot sign its database. Capture verification includes existing HASH72, and
final rendering regenerates HASH72 then HASH58 without device HashInfo writes.
A remembered
model or device-specific permission is not required; unknown formats show their
actual refusal reason. New audio remains validated MP3 plus stereo PCM16/44.1-kHz
WAV. Existing indexed AAC/ALAC or opaque containers can receive metadata/art
updates without reacquiring audio or granting a new codec-export capability. The
Clickwheel 0.19.1 dependency supplies the MIT-licensed pure-Python parser/writer,
but it never receives the live mount as its sync target. `soundbox.stock_ipod`
parses the live state, folds an exactly matching `Play Counts` delta into the
track records, renders to a temporary device tree, restores one opaque Apple
smart-rule header word that Clickwheel normalizes, reparses the final bytes, and
compares existing playback state, track identities, selected order, and built-in
smart rules before a commit is even possible.

The format check is destination-specific. Rockbox/filesystem exports retain their
supported FLAC/AIFF/24-bit/float/high-rate formats under that destination policy.
Generic CDJ USB export has its own source/native-analysis and player compatibility
boundaries in the CDJ guide; the broader Rockbox list does not certify CDJ media.
Disposable artwork/display-metadata derivatives preserve audio essence and
technical properties; this does not promise byte-identical exported containers.
The stock planner
omits unsupported extensions and WAV/MP3 encodings from its rendered playlist,
reports the exact omitted count and affected titles/reasons in the operation
details, and proceeds from the original Export/Sync gesture. It never edits the
source playlist. This boundary
was added after the physical 5.5G reproducibly rebooted when asked to play
`Hostage Situation - Loot`, a 24-bit/44.1-kHz PCM WAV; the proven working WAVs
are stereo PCM16/44.1-kHz. A read-only full-library audit found 722 PCM24/44.1,
166 PCM24/48, 57 float WAVs, and additional high-rate WAV variants, so unsafe
files may never be treated as isolated exceptions.

SoundBox curation is resolved before that destination check. **Remove from
Collection** excludes the selected song from SoundBox exports. Ordinary native
**Remove from Playlist** persists an identity-bound edit through the separately
accepted native playlist writer; it is not a new local-only veto. Local custom
playlists remove their actual local membership, while legacy stored exclusions
remain honored. Neither curated-away track reaches stock format inspection,
artwork, copy planning, or iTunesDB rendering. Consequently, the stock
plan's selected count is the effective SoundBox playlist—not raw
Rekordbox membership—and curated-away tracks do not appear misleadingly in the
firmware-omission list.

Curated SoundBox JPEGs are also the stock-art source of truth. Video RGB565
formats1028(100×100)/1029(200×200) and Classic1055(128×128)/1060(320×320)/
1061(small thumbnail) have separate derivative profiles. The observed Classic
small-thumbnail variant uses a55-pixel canvas and56-pixel stride (6160bytes);
it is not silently treated as a56×56 image. Preserve opaque existing image
headers/children when updating incremental art. A background worker pre-renders
the attached compatible profile
into a content-addressed AppData cache only with a stock-capable iPod attached,
pausing during playback and stopping on disconnect. Artwork changes without
an iPod do not queue derivative work; attachment checks current curated images.
Source indexes bind device/inode/size/mtime/ctime and recheck a warm hit without
rereading unchanged JPEG bytes. Misses hash and convert the same descriptor-
bracketed buffer, with one instability retry. Old incomplete index rows go cold;
correct content-addressed pixel payloads remain reusable under the same profile.
A source locator plus completion inventory skips unchanged preparation entries without
opening derivative manifests or sleeping. Source/profile/derivative identity
changes invalidate the receipt; deleted derivatives rebuild. Selected sync still
verifies pixel hashes. One manual cover change queues only that JPEG. Existing
playlist Sync pins selected cover and fallback owners plus effective visibility
through planning and commit admission. Replacing a selected image invalidates
the request even if its stored metadata is equal. Unrelated cover updates do
not cancel it. These in-memory checks do no GUI image/file work; actual image
acquisition and byte checks remain on the worker.
Initial reconciliation deduplicates identical images into one segment in
each `F1028_1.ithmb`/`F1029_1.ithmb`, while new independent track images receive
their own MHII records. Native Music-created catalogs may share one explicit
image ID across tracks. Preserve that original physical song ID, record order
and exact captured track/image/owner links, including after owner removal.
A changed shared cover splits into a new image; untouched references and native
crops/opaque children remain. The optional shadow indexes the actual referring
tracks rather than assuming the physical owner is the only user. SoundBox
retains unchanged MHIT artwork count/size independently from MHII source size;
Music-created catalogs can encode different values for those fields. SoundBox
validates every iTunesDB MHIT link, captured native alias, MHNI format/offset/size,
and ithmb boundary before admitting commit. It never modifies tags or embeds
images into source audio.

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
prepares exact reused/new tracks and bytes. Routine completion reports included/
omitted counts nonmodally. The original MA446 hardware pilot used explicit About-screen confirmation
because the refurb had an empty `SysInfo`. Current writer admission follows the
observed database and mount capabilities, so multiple compatible iPods need no
remembered model permission. The read-only device surface reads existing bounded
`SysInfoExtended`/`SysInfo` metadata, then supplements serial/firmware from the
exact macOS iPod device record matched to the mounted BSD node's USB ancestor or
a freshly observed hardware serial. A sole historical record supplies no facts.
Current concrete model fields take precedence over ProductType family labels
and old model confirmations. Missing display facts are retried rather than
cached forever. When those observations are incomplete, the worker can read
extended identity directly over endpoint0 using the isolated native helper.
It binds the current mount, volume UUID, BSD node and exact USB ancestor, then
issues bounded vendor IN requests without opening/seizing the device or claiming
an interface. It never creates SysInfo files. Apple's array-key XML is decoded
by CoreFoundation and emitted as a binary dictionary for strict Python parsing;
the returned FireWireGUID must match the currently observed USB identity.
Complete replies are cached per mounted USB identity; failures use bounded retry
backoff, and cached-only UI callers perform no discovery or USB requests.
The display catalog covers Video and Classic models and only
globally unique serial suffixes: `V9M` identifies MA446 and `9ZU` identifies
MC297. Conflicting observations suppress model-derived claims and remembered
fallbacks. IORegistry's SCSI revision remains a transport revision, never stock
firmware. Video's Wolfson WM8758 (WM8758BG for verified MA446) and Classic's
Cirrus Logic CS42L55 are explicitly model-derived facts, not direct DAC probes.
The dashboard presents concise values with provenance in its immutable snapshot.
The catalog follows [libgpod's device tables](https://github.com/gtkpod/libgpod/blob/master/src/itdb_device.c),
[Apple's model guide](https://support.apple.com/en-us/103823) and
[Rockbox's Classic hardware target](https://github.com/Rockbox/rockbox/blob/master/firmware/export/config/ipod6g.h).
SoundBox remembers observed display facts using the stable library persistent
ID plus FAT volume UUID, with serial/transport/family conflict checks; it never
restores a signing identifier from display memory. It can also recover a known
model from its own prior sync plan. This display catalog grants no write
authority. It deliberately does not key display memory by `DatabaseID`, which
Apple changes during normal rewrites. Normal Export/Sync adds no plan question.
Commit pins the mount and rechecks
the database, pending play counts, every source hash, and every destination. It
saves the original database/play-count bytes plus every replaced ArtworkDB and,
for full compaction only, ithmb file under `.soundbox/stock-ipod-recovery/`.
Incremental art records verified ranges and never overwrites a referenced slot.
It atomically copies new media, publishes artwork, writes iTunesDB last, and
archives consumed play counts. Failure attempts to restore the original database/
artwork set and retire that transaction's new media. The later rollback of
acknowledged copies still needs the foreign-replacement ownership correction
tracked in the [critical audit](critical-audit-2026-11.md); the new-file copy
helper's scoped failure cleanup does not complete that later transaction boundary. An
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
Delete planning captures the current indexed file entities, sizes, parents and
mount identity without reading song audio. An unassociated device song is not a
source-save dependency. Commit rechecks the captured entities and claims each
orphan by exclusive rename before disposal; a foreign replacement is restored
without overwriting another file or retained with an explicit cleanup warning.
This is indexed-file deletion authority, not a reusable content certificate for
ordinary Sync. Cancel before database publication leaves playlists unchanged;
after publication it stops remaining orphan cleanup and reports that the playlist
was deleted, with unresolved files and recovery evidence retained.
Deleting Videos removes the canonical MHSD-5 smart-playlist record plus any
matching ordinary-MHLP smart alias Apple stored under a second ID. The device
snapshot always exposes the canonical identity. Both representations contribute
no media candidates, so the operation leaves every track and regular playlist
intact and validates all retained Apple smart rules before it can commit.

Clicking a stock-only iPod opens a read-only managed-device surface instead of
the raw FAT folders. Header-level **Device**, **Songs**, and **Playlists** tabs
show identity/model/generation, storage and database health, merged playback
totals, the exact indexed music records, and ordered regular or
smart playlists. The Playlists tab renders user-created regular playlists plus
Apple's narrowly allowlisted **Videos** smart index. The library root and every
other built-in smart list remain in the snapshot for database health and
preservation checks. Videos can be selected and deleted through the same guarded
transaction without removing track records or media; Music, Movies, TV Shows,
and Audiobooks remain immutable. A supported writable stock catalog exposes
**Delete checked playlists…** beneath that list; the context menu retains an
explicit single-playlist delete. Device refreshes preserve the active tab for repeated
operations. The playlist table intentionally omits Artist so its expandable
playlist/track title column receives all spare width. Identity values preserve
their provenance: mounted metadata,
Apple's local device record, serial-derived model, or model-derived hardware
catalog. Parsing runs off the UI thread and eject refuses to race it.

Source saves and background readback preserve the visible device, tab, selected
song/playlist, expansion and scroll. They update desktop data without rendering
the hidden desktop page. A pending desktop-playlist deletion cannot steal the
device page. Collection is the fallback when the playlist actually being viewed
is deleted, the selected device disconnects, or the user deliberately changes
source libraries. Returning to a desktop page uses the latest saved data.

**Sync playlist…** updates the selected existing Apple playlist, including one
manually transferred through Music/Finder. Unique names use trimmed Unicode-NFC,
case-insensitive matching; duplicate normalized names remain ambiguous. Matching
name/count bootstraps an association, even if only some songs can be linked.
Blank-artist device records may use a unique source filename stem and positive
cached duration agreement; both sides must be unique and the target unclaimed.
This never proves audio bytes or grants foreign file ownership. A saved source/native-UUID/device-playlist-ID
binding survives later additions, removals, order changes and renames. A unique
name with some unambiguous song links can still receive partial metadata/art
updates; unknown entries remain intact and are reported instead of re-exported.

Sync captures the current displayed title, artist, album, genre, comments, year
and saved non-estimated integer BPM. It updates those supported Apple fields and
saved covers, changes safe selected membership/order, reuses retained device
songs and copies only verified new supported audio. It never walks the retained
media tree or reacquires its files. Foreign audio, the master playlist, other
playlists and playback/history fields remain intact. The optional Mac-side
`stock_catalog_state/` witness stores correspondence, not audio ownership or
content-integrity claims. Small catalog freshness, recovery and atomic publication
remain required. Incompatible or ambiguous records stay unchanged with a clear
summary. Unknown artwork formats do not block supported metadata updates.

Separate exports preserve verified associations for previously exported playlists
against the actual old and newly rendered catalogs. Bindings retain the actual
device label separately from the source folder label, and the complete source
occurrence identities separately from the compatible songs included on iTunes.
Intentional omissions therefore do not create perpetual pending additions.
Explicit Sync on a playlist with omissions reevaluates its current eligibility;
a supported replacement at the same source path can be added. Definite unsupported
PCM/float WAVs need only a bounded stable header read to be omitted; compatible,
unknown or unstable headers still use ordinary complete acquisition. No header
alone admits a song or supplies a content-integrity proof.

Delete asks one short question before planning. After native publication and
readback, surviving associations and fast-sync state are renewed against the
actual remaining songs/artwork. Unprovable legacy acceleration is retired only
if its exact old cache generation still exists; it cannot revive a deleted list.
Optional association limits stay in diagnostics. Success and expected format
omissions are nonmodal; real failures show bounded relevant details. Finalization
refreshes the exported iPod's cached catalog even while Collection or another
page is visible, without navigating away from that page.

The header changes Export to Sync only for observed pending changes to a linked
iTunes playlist; the explicit playlist Sync action remains available. The
binding must still point to a current regular device playlist and its exact
source identity; stale/deleted or conflicting bindings do not borrow a same-name
replacement. A unique unbound name is only an affordance hint: the actual request
checks correspondence and opens Export if none remains. Empty/system-only iPods
therefore offer Export. The Playlists footer and context action also offer Export
unless a regular playlist is selected; Apple's Videos index stays separately
deletable and never enters playlist Sync. Busy, missing-source and read-only
states disable both paths. Fresh cached dashboard format observations can finish
discovery readiness while current mount identity, writability and signing guards
remain required.
For a managed iPod with a verified shadow and catalog witness, background iTunes
status compares the seven current effective fields. GUI capture projects only cached linked song IDs;
the worker validates source/device/catalog/song identities and compares values
without opening audio. Missing or stale projections remain status-pending.
Scalar dashboard updates preserve its tables, current tab, selection and scroll.
The common Export selection and playlist drops also use incremental stock Sync when
all selected playlists already correspond. These paths use durable displayed
values without waiting for an unrelated native metadata backlog. New iPod exports
use the same durable effective values through their selected checkpoint and media
transition checks. A dual iPod uses its composite
transaction for catalog metadata/art only and preserves exact ownership-manifest
bytes or pre-composite absence; stock incremental membership is not a dual claim.
Source changes, selected values/membership, remounts and stale catalog baselines
fence every modal/worker phase. Completion retains the current page and uses the
same serialized final refresh/eject lifecycle.

Non-stock USB/Rockbox devices now use **Overview** and **Playlists** tabs in the
same device header and dashboard styling. Overview reuses the iPod's alternating
2×2 detail panels: Identity, Storage, Playlists and SoundBox. Identity/storage
facts come from the verified mount-probe cache; free space uses the latest
completed requested connection scan, not an idle generic-USB storage poll.
Rockbox sync status remains separate. Generic USBs show
export readiness without sync or pending-change status. Read errors remain explicit.

Generic USBs prefer the bounded native CDJ snapshot described above. Otherwise,
Playlists displays M3U/M3U8 files below `/Playlists`, including nested folders.
Those rows expand on demand into ordered title/path-derived labels, with unknown
metadata left blank. Snapshot discovery does not open referenced audio. Explicit
song activation validates the chosen device path before handing it to the player.
The M3U reader never traverses `/PIONEER` and omits references into it or outside
the volume. Empty copy explicitly names `/Playlists`. Symlinked playlist files or
folders and remount/changed-file reads produce an unavailable state.

Reads run only in the existing serialized DeviceScanWorker. Rockbox snapshots
may load on connection/remount; generic USB catalogs load only for an explicit
browser request, never for connection or idle polling. Successful generic USB writes replace stale song rows with a browse notice
while preserving settings drafts/view state, without a post-operation read.
Rockbox polls reuse immutable snapshots and refresh mount/storage facts; generic
USBs have no ordinary content poll or source-event scan. Cache publication waits
for QThread.finished and stale mount tokens retire snapshots. Expanding rows and
internal page restoration perform no removable-file I/O. Deliberate USB/tab
clicks request one serialized metadata refresh. Explicit song loading uses the same
serialized device lane and read-only path validation; it grants no write authority.
Native Export/internal recovery follow the dedicated CDJ contract.
Reformat has its own destructive confirmation; stock/dual/Rockbox guards remain separate.
The former raw filesystem browser is no longer a USB tab. Its guarded backend
mutation/import paths remain, but the new playlist tree exposes no such actions.

On a schema-3 dual device, Songs and Playlists are composite rather than
stock-prefixed views. The worker verifies the small manifest-owned M3U8 payloads
and combines them with the parsed iTunesDB without reading managed audio. Songs
and playlist rows report `iTunes/Rockbox`, `iTunes`, or `Rockbox` according to which
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
  Preserve that identity from plan through commit and any destructive consent; do not
  reacquire authority from a reused `/Volumes/<name>` path.
  Schema-2 Rockbox keeps the same session through its first manifest read,
  nested export, final ownership prune and manifest save. Deferred engine/CLI
  and growable-batch cleanup compares the original report's mount identity
  before inspecting payloads; a batch with no written folders does no traversal.
- Refuse any target that overlaps a source file or source-library parent.
  The boundary includes the source catalog and hidden/unselected media,
  analysis and native-location paths, including direct engine/CLI callers.
- Never write below `iPod_Control` except through the guarded observed-format
  `soundbox.stock_ipod` transaction. On dual boot it may be invoked only by the
  composite planner with coordinated recovery, rollback, and manifest-last
  publication.
- Unrelated unknown target files remain untouched. A planned output colliding
  with a different unowned file is a conflict; matching existing media can be
  referenced without claiming ownership. Only the baseline-bound exact
  path/size/SHA-256 interrupted-generation proof above can adopt an unowned
  survivor. File replacement/repair retains its destination-specific ownership
  and rollback checks; discovery never grants mutation authority.
- Copies land as `.sbpart` and atomically rename only after completion.
- Filesystem/stock/composite normal Sync cleanup is limited to manifest-owned
  SoundBox paths. The separate filesystem-device **Erase all files from device…**
  command is the explicit whole-volume exception for that workflow: it requires
  explicit confirmation naming the exact mounted volume, removes every child
  beneath that volume (including unknown content and the stale manifest), and
  then requests normal macOS eject. The manifest is removed last, so a
  partial erase remains visibly invalid and cannot masquerade as success.
  Stock and dual-boot iTunes/iPod volumes never expose this command. Generic CDJ
  USBs instead use the separately confirmed **Reformat USB…** action described in
  [the CDJ contract](cdj-usb-export.md); they do not expose filesystem-device Erase.
- Normal Verify checks managed sizes/references; Deep Verify rehashes every
  managed track, cover, and playlist.
- Idle Eject requests normal macOS eject without library verification. macOS
  handles flushing; a refusal is reported without a force-unmount fallback.
  A finishing background read queues Eject behind QThread.finished and suppresses
  replacement polls. Active writes must finish or stop safely first. Required
  iPod finalization reads remain serialized; generic USB completion starts none.
  Failed integrity does not prevent idle
  eject. The post-sync polling hold ends at quiet completion or after genuine
  attention is dismissed.

The retained filesystem-browser backend also supports an explicit threaded
import action; the current generic USB tabs/menu do not expose that old raw-browser
action. Selected audio or M3U8 entries in that backend copy in exact playlist order to
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

The original MA446 path passed stock and dual-boot hardware acceptance: guarded
stock database/artwork writes, playback/reconnect recovery, destination-specific
format filtering, playlist/system-index deletion, Rockbox picture-free playback,
theme-native external art, firmware-owned database refresh, full-Collection
commit, incremental Sync, and exact visible playlist order have all run on the
real device. Rockbox's non-recursive catalogue intentionally remains flat;
zero-padded filenames carry the user-applied order while stock titles stay clean.

The September lifecycle corrections still require the repeat reconnect/playback,
unchanged-Sync performance, overlap, cancellation/retry and failure-eject gates
in `HANDOFF.md`. Earlier acceptance does not complete these newer gates.

The current source adds observed Video/Classic format and signing profiles,
with copied-catalog validation; its new physical Sync/playback acceptance remains
separate in HANDOFF. Other unsupported database/checksum families and any
separately designed schema-2 ownership migration retain explicit refusal.

## Real stock-hardware baseline (2026-07-31)

The first real target is an Enhanced 5th-generation black iPod Video
(`MA446LL`, originally 30 GB / 32 MB RAM), flash-modified to a 256.4 GB physical
disk and reporting 238 GB under stock firmware 1.3. It is Windows-formatted
FAT32 and had no `.rockbox` tree at that July 31 baseline. Later dual-boot
acceptance does not change what this stock-only capture contained.

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
one successful guarded operation. At that historical checkpoint the iPod had a
clean stock baseline with zero indexed tracks and zero regular user playlists.
That checkpoint's iTunesDB SHA-256 was
`c235f57378793d1db96d3f5b059bdb01e1262e04b9b54375a2c78cce6770123c`,
`Play Counts` was absent, and eleven recovery points remained. A later Favorites
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

### Remembered iPod identity

The dashboard retains observed serial and stock-firmware values in SoundBox
settings under the exact volume UUID/library persistent ID. Fresh matched device
facts replace older values; a conflicting serial clears the old association.
Missing details under Rockbox can reuse this record, with firmware marked last
observed. No matching prior observation means the value remains unavailable;
SoundBox does not guess. This is display continuity, never export authority.
Apple's historical device records require an exact device-ID match even when
only one record exists. Recovery counts and iTunesDB digests remain backend
snapshot fields and are omitted from both stock and dual dashboard rows.

Historical September 26 acceptance: the drift-fix relaunch at 18:34:28 verified
the curated library and caches. The observed Rockbox USB identity could not seed
a remembered serial from Apple's unmatched historical record. That observation
required a verified stock-mode connection before boot-mode fallback could
populate the unknown fields; see HANDOFF.md for current runtime evidence.

For offline lifecycle work, the dated [lifecycle audit](ipod-lifecycle-audit-2026-09.md)
describes the private September26 metadata capture and exact omissions. That
capture contains real database/recovery/config
baselines but excludes source audio and PIONEER. Keep originals read-only and use
disposable test copies. Metadata-only fixtures cannot validate real USB or
firmware playback. A user-requested fresh menu/tag-display configuration was
written and normally ejected at that checkpoint; it grants no automatic firmware
preference writes under the current user-owned-settings policy.


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

Playback and iPod management are independent. Device discovery and ordinary
status polling must not check player.is_playing as a suppression condition.
Only device operation ownership pauses competing device reads/writes. Reconnect,
Rockbox tag-cache updates, unrelated firmware preferences and ctime-only cloud
metadata changes do not themselves create managed-library Sync drift.

September 27: the actual reconnected generation-60 device had no managed library
or playlist drift, only the absent unsupported `sort playlists` setting. Rockbox
4.0's [settings table](https://raw.githubusercontent.com/Rockbox/rockbox/v4.0-final/apps/settings_list.c)
does not define that key. SoundBox no longer inserts or requires it. This corrects
the prior documented assumption; no device configuration was rewritten for the fix.

Post-sync polling is held through owned finalization and any actual attention
dialog. Quiet completion releases the hold immediately so subsequent SoundBox
edits can update drift without reconnecting.

September 28 durability follow-up: `_safe_copy` explicitly fsyncs its staged
payload before verification and atomic publication. Flush failure leaves the final
path absent and cleans only the transaction temporary under valid authority.
Normal macOS eject remains required; this fixture-tested correction is not new
physical disconnect/power-loss acceptance.

## November3 efficiency follow-up

Managed schema-2 Rockbox Sync compares local cover identity/policy separately
from retained audio. A cover-only change publishes affected owned covers and
playlist state without opening or hashing retained music. Shared cover references
and temporarily unavailable local images remain preserved. Changed unowned covers
are explicit conflicts; matching committed derivative hashes retain existing USB
covers without reading them. This does not adopt foreign M3Us or apply schema-2
rules to composite iPods or generic CDJ exports.

USB overview/playlist scrolling stores both positions immediately and coalesces
preference notification over120ms. Tree selection/expansion capture is reserved
for structural/view changes and lifecycle barriers. Restoring both scrollbars
suppresses intermediate recording, preserving the other saved position.

Stock planning indexes database/playlist identities while retaining first-duplicate
and opaque metadata behavior. CDJ cue sections parse once per captured extension;
Rust row boundaries and playlist entries are indexed without changing original
row ordering or bytes. No live device/source proof is cached by these changes.
Generated component measurements and physical acceptance limits are in
performance-audit.md; HANDOFF records the current installed provenance.

## November3 source/manifest correctness follow-up

USB planning binds global MyTag definitions as well as selected song/playlist
inputs across its independent source captures. Small manifest model/raw bytes
share one bounded read and propagate as the exact commit precondition. Explicit
My Settings patches verify original profile hashes even for allowed changed paths;
concurrent changes reject without overwriting new opaque bytes.

A profile-only change during explicit export refreshes only verified manifest
inventory after full preflight; it never rewrites DB/media/profile files or causes
poll writes. The next unchanged plan can reuse the exact current-policy native
catalog reconstruction. External exporter changes still use fresh current small
catalogs; SoundBox has no exclusive ownership. Incremental stock-art indexing
preserves first matching/first free slots and transient-read retry behavior.
Manifest model/digest pairing for composite planning/polling is likewise one fresh
captured payload. Measurements are in performance-audit.md; physical acceptance
remains separate from generated/offline fixtures.
