# Native CDJ USB export

The November8 cleanup was accepted as personal build169, including both Track Info
tabs prepared before presentation. That checkpoint and dated build167 USB evidence
below retain their original scope; [HANDOFF](HANDOFF.md) records current source,
package and installed identities separately, plus latest observed app state and
validation. A source candidate does not identify the installed application.
The personal app prepares classic Device Library and
Device Library Plus together. Sonny reports successful player playback, artwork
and waveforms from his export. Exact firmware, the wider performance/control
matrix and newer OneLibrary hardware remain separate acceptance. Installed
provenance and validation are recorded in [HANDOFF](HANDOFF.md).

## Dedicated CDJ write contract

rekordbox and SoundBox are equal USB exporters. Switching between them is normal.
SoundBox reads the current small native catalogs, preserves existing collection
records, other playlists, media and settings, and applies the requested playlist
changes. Its manifest binds its own files and playlist selections; it does not
confer exclusive authority over the native library. A changed native catalog is
not an ownership error or a reason to clear the collection or hash old songs.
Generic USBs have no Sync-needed, pending-change or database-status badge.
Connection and idle polling do not compare the desktop library with USB contents
or read native catalogs. Opening the USB browser requests its bounded metadata
snapshot; explicit export reads only the catalogs and affected paths needed to
publish the selected playlists while preserving the existing native library.
Native track identities include the desktop library ID. Tag IDs are mapped into
the current USB namespace so different source libraries cannot replace each
other's definitions or memberships.

The desktop admits verified writable FAT32 or exFAT generic USBs. CdjSession pins
mount/inode and volume UUID, excludes sources, stock/Rockbox layouts, hidden
`.PIONEER`, symlinks and ambiguous FAT aliases. Ordinary stock/dual/Rockbox and
explicit filesystem exporters retain their PIONEER exclusion. Supported Plus
schema/version1000 and classic page/index checks run on bounded local catalog
copies. Display reads include a stable local DB/WAL copy when rekordbox leaves
pending pages; no live SQL is opened and SHM is never copied. This does not grant
write authority: publication rejects active/unreconciled SQLite companions.
Unsupported schemas preserve data. Classic-only libraries acquire Plus
from their native records and cue metadata, without rebuilding their media.

`cdj_source` captures the read-only desktop DB/WAL snapshot, selected IDs/UUIDs,
metadata/counters, current DB cues, source playlist order and the complete rendered
global My Tag tree. The revision binds these exact captured inputs, including
their order; selected content-file, cue and tag membership queries share that
snapshot. Playlist membership queries are bounded to the explicit selections,
with unrelated smart-list evaluation skipped. Structural/ancestor and active-ID
checks remain; an optimization never substitutes cached browser membership.
Accepted source tag/cue/grid, native-playlist, registration, local/native analysis,
tag-definition and metadata intentions drain before capture; frozen SourceTruth
work and unresolved save attention also retain this CDJ barrier. A second
capture detects concurrent changes. Native playlist selections rebind by stable
ID after that drain, so a rename/membership save is reflected and a deleted
selection or switched source stops before USB writes. Pending playlist intents
are not their own worker ownership; the waiting request leaves the source lane
free to finish them. Current stock, dual and Rockbox iPod exports instead capture
a selected durable effective-metadata checkpoint, so unrelated metadata backlog
may remain paused or running. Selected structural, registration, physical-media
and local-grid transitions still drain, and waiting native playlist choices
rebind by source/ID/UUID while preserving their device export keys. The captured
metadata and exact source/target fences remain current through publication;
see [the iPod checkpoint contract](DEVICES.md). This does not relax CDJ source
capture or accepted database-write Quit drains. Source or target changes refuse
the command before device writes. Browser
cache and player overlays grant no export authority. SoundBox title/artist/info
overrides and a locked artwork snapshot apply without editing source audio or
adding a desktop database write domain. Imported/XML tracks or missing required
native analysis fail explicitly; there is no silent omission/transcoding.

A playlist drop authorizes its pinned USB and continues straight through to
publication. Export opens a two-column selector: playlists/folders on the left,
connected USBs/iPods on the right. Folders start collapsed and open on demand.
A sole target is preselected; multiple devices
can be checked. One action queues each checked target through the existing
serialized device lane, with source identity/playlist rebinding and target
mount/UUID guards before each target. Cancellation clears remaining targets;
iPod exports likewise continue through their own guarded planning/commit path
without an extra first-generation plan confirmation. USB Export and drops
are additive to the existing library: only the selected playlists are reconciled,
without applying later source edits to unrelated previously exported playlists;
Save to USB applies explicit preference edits. None adds a second prepared-plan
confirmation. Drops prepare selected new/changed inputs only. Unchanged native
records may be adopted by source identity, counters and metadata, without reading
USB media. Existing audio placement survives display-only changes. Replacing a selected
playlist removes its old memberships. A removed song is pruned only when no
remaining playlist in either native catalog uses it; shared audio/analysis/artwork
remain. Unrelated collection-only songs and other playlists stay untouched.
Existing unrelated differences between classic and Plus are preserved separately
and do not block a selected export. Selected records and exact memberships are
validated in both formats. There is no whole-drive orphan sweep.
A repeated unchanged export writes nothing. Pure catalog reconstruction may be
reused only under the current render policy, exact captured input signatures and
fresh matching committed catalog/profile bytes; source, target and recovery
guards still run. An external player-profile edit can require one manifest-only
inventory refresh during explicit export, preserving all live catalog, media
and profile bytes. These are explicit-export optimizations, never idle sync checks.
The track menu immediately right of Q, introduced in the October29 stage,
also exports the loaded native song directly to the collection, including on
an empty USB, with no synthetic playlist. Its mount token/UUID survives discovery
and source-save deferrals; locally bound recovery remembers interrupted standalone
track IDs/UUIDs. Missing/changed source identities fail closed on resumption.

Retained native/manifest inventory is not a fresh integrity certificate. Normal
export does not walk/hash retained audio, analysis or artwork. New audio is
captured once into its exact local output placement and verified against its
source contract. The optimized release Rust helper receives only an explicit
local workspace. It merges copied native catalogs and never opens live source or
USB paths. Staging currently retains the changed set locally; its required free
space is checked. Large-library staging efficiency remains a future improvement.

Publication uses a small local receipt and USB journal, retains affected originals,
copies new payloads with a streaming checksum, then publishes the catalogs and
manifest. There is no second full audio readback. Cancellation/interruption keeps
completed new payloads available for a retry and restores earlier small metadata
where needed. Export resumes the saved selection against fresh source
state. A later coherent rekordbox export supersedes an interrupted SoundBox
catalog generation: keep the newer native catalogs and all media, retire the
older pending intent, and plan from current native records. This exceptional
check reads only the small catalog copies. Unknown/mismatched journals and actual
unsupported/corrupt states preserve evidence. Recovery is internal to the normal
workflow, with no separate user-facing recovery/verification menu. Original
metadata recovery remains. Exact assets removed by the selected export are
retired from same-volume recovery only after durable manifest/journal commit;
interrupted retirement resumes, reclaiming USB space. New copies still require
peak transaction free space before commit. Worker ownership
lasts through QThread.finished; shutdown drains active work with Qt events alive.
Completion invalidates the browser cache without starting a final USB read.
After a successful write, stale visible song rows are cleared with a browse
notice while settings drafts and view state survive. A deliberate USB/tab click
loads current metadata once; internal tab restoration does not. Failed, cancelled
and unchanged operations retain their previous view. An idle device can always
request normal macOS eject. Successful eject removes its row/caches in memory,
without starting another scan. Eject submits only the
normal macOS request; it never reads catalogs, verifies files or computes sync
state. An already-running device worker retains ownership until it finishes,
and no new inspection is started for eject. macOS refusal is reported without
force-unmount.

## USB controls and track information

Tabs are Overview, Playlists, Category, Sort, Column, Color, My Settings. Overview
uses a compact two-column General form plus Identity/Storage/Playlists/SoundBox
panels. Native name differs from the real macOS volume name. Shared headers use
26 points; song/detail rows and empty stripes match the 24-point regular browser.
Actual USB-song activation validates selected device audio/analysis in the shared
read lane and loads a separate device ID without substituting Mac audio or source
cue overlays. Views retain per-volume tab, hierarchy, selection and scroll.
In the playlist browser, clicking a folder or playlist expands/collapses it;
only song rows receive selection highlighting. Checkbox actions remain separate.
Native playlist membership IDs keep distinct records' metadata and covers
separate even when those records reference the same audio file. Artwork badges
describe the USB catalog reference rather than the current Mac artwork cache.

Category and Sort support enabling, disabling and reordering their lists while
retaining mandatory native entries. Column sets the default native right-hand
column. Color edits the eight comment titles. Explicit saves patch requested
classic and Plus rows, preserving unknown flags and unrelated catalog content.
The sort menu never rewrites playlist membership order. Active/inactive lists
have contrasting panels and visible borders. Color inputs sit directly beside
their dots without a duplicate colored name.

My Settings edits 44 player/mixer fields directly on the USB. Global My Settings
in SoundBox Settings seeds once from the active recognized USB profile. Save to
Global deliberately promotes a saved USB profile; Load Global deliberately copies
the global values onto that USB. New formatted USBs receive the global profile.
Global edits do not automatically change connected USBs; named presets and the
older Follow Global/Custom proposal are deferred. Unknown bytes and fields remain
local to their device. Logical global values never clone another device's opaque
bytes. Ordinary playlist export preserves player/mixer files byte for byte.
rekordbox desktop global Quantize is never read or changed.

USB settings admission captures the same complete desktop library/audio/analysis
path set for source separation before its worker starts. It no longer deep-copies
unrelated songs, cues, playlists or curation for a settings-only save. Later RAM
library changes cannot alter those captured paths; fresh worker target/source,
profile-byte, manifest and recovery guards still apply. Selected playlist export
retains its separate frozen library/curation snapshot. On a generated4000-song
fixture, settings GUI setup measured122.58→2.10ms; this excludes native settings
preparation and physical USB publication.

Native complete-file comparisons cover tempo, Mic Echo 1/2/3⁄4, Headphones B
None/Stereo/Mono Split, Auto Cue Level Hot Cue and Talkover −∞, including reverse
changes. Mic Echo is a numerator/denominator pair; Talkover −∞ uses two separated
bytes, preserving intervening mixer fields. The five sample files were restored
byte for byte after the final comparison. The October29 reference ended at ±10;
the earlier ±16 capture is historical. Neither observation states the user's
current preference. Other choices come from the bounded format catalogue;
this is not a claim that every choice has been exercised on hardware.

The information button prepares its cover off-thread before Summary appears;
its first visible frame contains the settled cover or missing-art state. The
wait wheel replaces only white pixels with the selected accent and keeps black
details intact. Summary and Info controls are built before presentation; switching
tabs requires no deferred field construction. Artwork
editing is centered inside Info; Summary centers its artwork/title group and
anchors selectable technical details in a bordered panel. Lyricist is omitted
from Info and Comments spans the full row. Global My Settings and Keyboard scroll
at the right window edge.
Album Artist, Track Number, Disc Number, Original Artist, Composer, Remixer and
Mix Name are omitted. Native Title/Artist edits use durable source intentions
and verified native readback under the independently accepted exact-library v5
metadata authority. That activation does not provision other libraries or fields.
Broader native Track Info and artwork persistence remain
unfinished; unsupported native field saves refuse. Retained local overlays still
feed migration/display/export, and legacy/XML sources retain local edits. These
boundaries do not imply broader native field support; current installed identity
and validation remain in [HANDOFF](HANDOFF.md).
BPM remains tied to the beatgrid controls. USB-song Track Info is read-only and
offers no desktop source-edit authority. Its cover comes from the selected
native USB artwork reference, captured through pinned no-follow descriptors with
an8MiB limit and decoded from those bytes off-thread. It never substitutes the
Mac artwork cache or reads other covers, catalogs or audio for the information
request. Discovery finishes before that selected read, and device admission is
held until its decoder retires. Info fields also appear as optional
browser columns. These display inputs were introduced with prepared row cache
schema3/library cache11 and remain in current schemas4/13 without resetting curation, analysis or artwork. New optional
fields stay hidden when older header state is restored; deliberate selections
that exceed the viewport can scroll. Progress/count/Cancel share a fixed-height
footer and cannot resize the browser.

The generic USB menu is Export, Import “My Settings”, Reformat
USB…, Eject. Import promotes recognized USB settings into SoundBox Global settings.
Reformat presents the real volume name, capacity/affected mounted partitions and
FAT32/exFAT choice, then requires its destructive confirmation. It revalidates the
physical external USB and source separation (including native analysis paths).
A same-filesystem quick erase reuses an existing full-capacity, single first MBR
partition only when its partition type matches FAT32 or exFAT. Other layouts and
filesystem changes rebuild the MBR map. It verifies the resulting physical
identity/map/filesystem and initializes both
native catalogs with Global My Settings. HFS+ is not offered: its hidden native
layout remains outside the current writer boundary. Probe/erase timings are logged
separately. Fresh native initialization excludes the renderer's workspace lock
from the candidate file set. Dated hardware timing and bounded acceptance are
recorded in the [November4 USB fixture audit](performance-audit.md#physical-usb-fixture-timings--november4)
and [later USB audit](performance-audit.md#usb-deep-audit--november4-later-testing).
HANDOFF records current runtime/gates; older mock-only format validation describes
the earlier release.

The later November4 audit fixes the confirmation-dialog exception that prevented
erase from starting. These corrections were installed in personal build165 and
remain in build167. Source was local and
unpublished at that installation checkpoint; the subsequent whole-app wrap-up
authorizes publication without changing its signed bytes. The dated USB audit
receipts retain that165 signed provenance; HANDOFF records the actual current
installed provenance, separately from those historical builds.
Formatting owns a separate worker
and reserves every mounted partition on its physical disk. Once confirmation is
accepted, another USB can open Export and use the ordinary export lane. Discovery
waits while erase runs; format completion never clears an unrelated export's
worker, progress or Cancel control. Old partition identities retire before fresh
discovery. Native unmount/erase/mount stages update the formatting device row.
Cancellation is checked before native erase starts; an already-running macOS
erase is retained and reaped before shutdown can continue. Fresh target/source
and final physical/map/filesystem checks remain.

This audit also retains exact cross-catalog byte checks and adds complete before/after
identity and presence checks, removes redundant per-asset completed-proof
writes, and removes a duplicate initial candidate audio hash pass. Actual FAT32
and exFAT tests showed changed same-size bytes retaining identical epochs, so
interrupted recovery/adoption freshly hashes affected payloads. Normal retained
manifest inventory remains unaudited. Logical imported IDs retain their separate numeric native IDs
and artwork lookup. Browser-only membership IDs preserve different records that
share an audio path and are excluded from publication manifests. See the
[audit findings and limits](performance-audit.md#usb-deep-audit--november4-later-testing).

`native/cdj` contains the pinned upstream runtime/dev closure; vendor verification
checks243 original crate files. Runtime changes live in SoundBox's wrapper modules.
GPL dependency/distribution review remains open. The release gate compiles the
helper, runs Rust checks and hash-pinned offline fixtures. Tests/render harnesses
must suppress live source, device and AppData discovery before construction.

## Historical October28 reference campaign

October 28: Sonny requested parallel research for the building agent, targeting
**both classic Device Library and OneLibrary**, with his own export UI. The
[backend handoff](../research/cdj_export/RBXPORT_HANDOFF.md) identifies the pinned
`rbxport` source kit, an independently reproduced defect in our old PDB prototype,
and the native classic→Plus comparison he offered. He confirms CDJ-3000 and
rekordbox 6; the fresh capture records installed 6.8.7.0303, while exact CDJ
firmware remains to record. New-format hardware
acceptance cannot be supplied by his 3000s and stays a separate pending gate.
That research phase preceded the dedicated pilot contract above; ordinary exporters remain excluded.

The initial four native states are preserved in the private reference root: empty and
playlist exports on exFAT, a repeated five-track **smol test** export after a
FAT32 reformat, and the same FAT32 export after Sonny accepted an explicit
Device Library Plus override. Both databases were populated before override,
but override rebuilt Plus with version **10000 → 1000**, filled counters and
normalized defaults. The resulting version matches the pinned upstream builder;
the earlier version difference is no longer an unresolved constant mismatch.
A fifth capture isolates Sonny's native ±10→±16 tempo change. The full resulting
148-byte profile matches the precomputed codec output; this does not accept
other settings mappings or live SoundBox publication. The [developer pickup
note](../research/cdj_export/RBXPORT_HANDOFF.md#developer-pickup--start-here)
collects the reproduction command, original implementation sequence and evidence.

Classic `export.pdb` changed only its header sequence; all 15 analysis companions
and five settings/profile files stayed byte-identical through the override.
Copy-only SQLCipher integrity and structural audits pass. Native Plus's SQL cue
table is empty even though ANLZ contains 19 decoded hot cues. Memory cues, loops
and artwork remain unrepresented by this fixture. The initial capture campaign
is complete. A compiled standalone dual-format backend now generates the five-track
fixture and passes Sonny's approved offline gate: **25/28 files byte-identical,
all compared database records/schema/indexes equal, no extra/missing files**.
Raw database differences remain reported; literal whole-export byte identity is
false. See [export parity](../research/cdj_export/EXPORT_PARITY.md) for a repeatable
build/export/compare command. The original research handoff left application DTO/pagination/publication open.
Those integration pieces are now implemented in the production contract above.
Build163 also passed the bounded FAT32/exFAT fixture-write/update/repeat campaign
in the [November4 USB timing record](performance-audit.md#physical-usb-fixture-timings--november4);
the wider on-player/model/firmware matrix remains separate.

## Product goal

**One selected export maintains both classic Device Library and OneLibrary.**
Users manage one playlist tree and one track order. There is no format choice,
Plus enable/conversion step, separate selection or second sync operation.
For the requested selection, both encoders consume the same frozen generation,
preserve its hierarchy/order/membership and reference one shared set of
audio/artwork/analysis files.
Requested selected-playlist renames, moves, reorders, additions and removals
reconcile in both views. Unselected playlists retain their own USB baselines;
ordinary export does not propagate unrelated source changes.
Publication/recovery must coordinate the complete generation, and failure of
either database cannot produce a success result. Exact format-specific encoding
and acceptance evidence remain backend concerns. This was the October28 research target; the production contract above now implements both formats.

The October28 settings proposal included named presets and automatic Follow
Global/Custom propagation. October29's implemented controls use explicit Save to
Global / Load Global, as described above. The [staged design](../research/cdj_export/MY_SETTINGS_PLAN.md)
is historical design context, not current automatic-write behavior. The early
single-field ±10→±16 proof has been supplemented by the native comparisons above.

Export SoundBox's curated playlists to USB with the library and performance
behavior of a rekordbox export on the supported CDJs: normal library browsing,
playlist order, metadata/artwork, beatgrids, usable hot/memory cues and loops,
and prepared waveforms. A USB that merely plays audio through folder browsing
does not meet the goal. This is the reduced-function fallback Sonny describes
as “limp mode,” not a formal device mode name.

The original research records Sonny's setup as four CDJ-3000s; he reconfirmed
CDJ-3000 on October 28. Record exact firmware before the next campaign.
Acceptance is against a real rekordbox-exported control on those decks, not just
our own parser's output.
The output need not reproduce irrelevant stale bytes, but all required structures,
references and observable features must work. This exporter reads the desktop
database and source audio without changing them. Independently accepted desktop
edit/conversion domains are separate; see [the prep contract](rekordbox-prep.md).

## Where the existing work lives

| Location | What exists | Limit |
| --- | --- | --- |
| [research/cdj_export](../research/cdj_export/README.md) | Standalone DeviceSQL reader/writer, string/simple-row and track-row codecs, small-file assembler | Excluded from installed apps; not a production exporter |
| `research/cdj_export/anlz_bridge.py` | Pure mapping of canonical beats, cues and waveform fields | Synthetic tests; no complete ANLZ file writer or hardware proof |
| `soundbox-cdj-ref/ref-usb-empty/` | Retained empty rekordbox USB scaffold | Private, read-only reference |
| `soundbox-cdj-ref/ref-usb-full/` | Three-track rekordbox export and Test playlist | Private, read-only control |
| `soundbox-cdj-ref/stage-A-clone/` | Byte-identical control database | Historical experiment |
| `soundbox-cdj-ref/stage-B-scratch/` | Generated compact database rejected in the recorded CDJ test | Historical experiment, not an accepted export |

All of these locations are in this checkout. The reference root is git-ignored;
do not commit audio/captures, mutate pristine references or overwrite a working
performance USB to run an experiment.

## Library formats and analysis

Classic Device Library uses `/PIONEER/rekordbox/export.pdb`; newer extensions
include `exportExt.pdb`. Database rows connect metadata, playlists, artwork and
audio/analysis paths. ANLZ `.DAT`, `.EXT` and `.2EX` containers carry grid, cue and
waveform data. Settings files are separate from library acceptance. Primary
reverse-engineering references: Deep-Symmetry's [database structures](https://djl-analysis.deepsymmetry.org/rekordbox-export-analysis/exports.html)
and [analysis structures](https://djl-analysis.deepsymmetry.org/rekordbox-export-analysis/anlz.html).
These describe structures; they are not certification of our writer.

The local classic references have 20 tables and 4,096-byte pages. The prototype
preserves a 40-byte page header, upward-growing row heap, backward row indexes
and original padding/deleted-row bytes on round-trip. Track rows have a 136-byte
header and 21 string offsets. The observed index/data page arrangement is a
property of the small reference exports, not a universal two-page-per-table rule.
The historical Python `build_file` assembler has one data page per table and is
for small research libraries. The integrated `native/cdj` renderer and shared
fixture adapter implement scalable pagination; the Python prototype is not the
production writer.

OneLibrary, formerly Device Library Plus (`exportLibrary.db`), is a different
compatibility target, not a universally optional cosmetic sidecar. AlphaTheta's
[January 7, 2026 notice](https://www.pioneerdj.com/en/news/2026/cdj-3000-firmware-ver330-important-notice/)
describes classic Device Library for CDJ-3000 and OneLibrary requirements for
CDJ-3000X and other named newer products. Consult the current
[USB export compatibility guide](https://rekordbox.com/en/support/usb-export/)
before each hardware campaign; record the exact model, firmware, rekordbox version,
filesystem and library format. SoundBox integrates both writers through
`native/cdj`, with pinned crate sources in its vendor subtree. The extracted
private source kit in the historical handoff records the earlier research stage.
A classic-only result must not be presented as support
for every CDJ/XDJ.

The shipped player already imports rekordbox analysis and authoritative live-DB
cues; see [PLAYER](PLAYER.md). Those readers and SoundBox's canonical analysis
provide useful inputs, not proof of valid device files. The research bridge
expresses PQTZ units, cue semantics and proposed waveform streams; it is not the
integrated binary encoder. The runtime renderer has offline container, metadata
and path checks; wider on-deck behavior remains a separate acceptance matrix.
The October 27 strict research contract separates `DisplayWaveformFields` from
byte-preserving `NativeWaveformStreams`. It no longer labels three-byte display
RGB as packed PWV5 or averages spectral bands into a purported exact PWV7.
Invalid beat numbers, nonrepresentable tempos, cue identities, endpoints and
out-of-range values are rejected instead of substituted or clamped. These are
field/provenance boundaries, not a complete binary encoder or hardware proof.
Export captures fresh authoritative DB cues: accepted cue edits can
be DB-only, leaving ANLZ cue sections stale. Drain accepted saves before capture;
bind verified grids, original waveform sections and cue identities to the same
track/audio source. Display palettes, GPU mips and RAM-only previews confer no
device serialization authority. Unknown original sections remain preserved.

The current CDJ planners admit `.mp3`, `.wav`, `.aif`, `.aiff`, `.flac` and `.m4a`
source suffixes. They still require verified native source metadata/analysis and
captured media; this allowlist does not certify every contained codec or player.
`track_analysis.export_eligibility` governs an analysis path, not this exporter
allowlist. The original CDJ-2000 and CDJ-3000 have different lossless-format
profiles; see [media compatibility](cdj-media-compatibility.md). Filename checks
never replace actual format checks or model/firmware acceptance.

## What the recorded experiments establish

Historical work round-tripped the reference PDBs byte-identically, decoded and
regenerated track/simple rows, and built a compact PDB readable by our own reader.
The recorded CDJ-3000 test played audio but rejected that generated database with
“no rekordbox database found”: playlists were absent, cue markers were inert and
the deck used its fallback waveform behavior. **Native library acceptance failed.**

A control using rekordbox's own PDB without the Plus database reportedly restored
playlists, working cues and prepared color waveforms on that tested setup. This
isolates the generated classic PDB as the next investigation target for that
fixture. It does not prove that all media-copy paths are correct, that no other
writer bugs exist, or that Plus/OneLibrary is optional on other hardware.
Device name/background-color behavior was not independently isolated; do not
assume the encrypted database is purely cosmetic.

October 26 read-only metadata recheck (no USB/device or source-audio access):

| Retained database | Size | Recheck |
| --- | --- | --- |
| Empty reference | 167,936 bytes / 41 pages | Byte-identical reader/writer round-trip |
| Full reference and A control | 167,936 bytes / 41 pages each | Same SHA-256; three track string-codec round-trips each |
| B generated candidate | 122,880 bytes / 30 pages | Byte-identical reader/writer and three track string-codec round-trips |

The synthetic ANLZ-bridge tests also passed. These dated checks confirm retained
bytes/code, not a fresh CDJ run or an accepted generated library. The October 28
independent audit found that the old reader/writer share a wrong byte order for
the page count field: native three-row bytes are `03 60 00`, whereas the failed
candidate has `00 18 03`. Its own round-trip therefore does not validate native
row interpretation. See the handoff for exact reports and remaining audit targets.

## Historical proposed experiment — scaffold and inject

Before using the historical writer again, correct/replace its count-field and
page-construction foundation. The pinned dual-format upstream pipeline provides
another concrete baseline to compare with native exports. At the time of this
proposal it was not yet built or accepted in SoundBox; the dedicated pilot above
now supersedes that integration status. The following is retained experiment
history, not the current next task.

Start from a copy of the known-good small rekordbox scaffold. Preserve structural
pages, pre-allocation and row-index bookkeeping, and change only a bounded set
of library rows. Compare it with an untouched control on a separately selected
test USB. If accepted, vary one structural detail at a time to find what matters.
Pre-allocation/index handling is a hypothesis prompted by the rejected compact
file, not an established explanation. A scaffold is an experiment, not yet the
chosen production architecture or an approved redistributable asset.

Record candidate hashes, intended row/path changes, model/firmware and the exact
observed results. A parser round-trip or successful audio load cannot close the
database-acceptance gate. After small-fixture acceptance, design scalable page
allocation, IDs/references, artwork and complete ANLZ publication before testing
larger collections and updates.

## Hardware acceptance matrix — original pre-integration plan

Use matching content and settings on a rekordbox control and candidate USB.
Record pass/fail and any unsupported feature explicitly for the declared device.

| Gate | Required evidence |
| --- | --- |
| Library recognition | Normal library browsing, playlists/folders, exact order, artist/BPM/key metadata and supported search/sort; no folder-mode fallback |
| Prepared analysis | Expected overview/detail and supported color/3BAND modes; grid/downbeats match the control without fallback analysis |
| Performance controls | Hot/memory cue positions, slots, colors/comments where supported, saved loops, quantize and beat-sync behavior match the control |
| Media and artwork | Correct recording/duration/seeks, readable artwork, Unicode/long paths, duplicate references and honest reporting of unsupported media |
| Linked decks and persistence | USB sharing/loading over PRO DJ LINK, supported phase/waveform views, normal eject/reinsert and deck restart retain the library |
| Export lifecycle | First export, additions, changed grids/cues/art/metadata, playlist reorder/removal, shared-track retention and unchanged re-export behave as planned without duplicates or stale references |
| Failure and recovery | Cancellation, full target, interrupted publication/remount and retry preserve or recover the last accepted library; unrelated files remain untouched |

The original pre-integration checklist required explicit target confirmation, mount identity,
source/target separation, ownership/coexistence with existing rekordbox exports,
space planning, recovery and final publication order. No existing production
exporter gained `/PIONEER` authority from this historical checklist. The dedicated
CDJ contract above now defines the scoped exception and remaining hardware gates;
ordinary stock, dual and Rockbox/filesystem exporters retain their guard. Do not borrow iPod recovery
as permission to modify a CDJ USB.

The shared app integration and normal release/install gates are complete for the
current personal release; the wider device/feature matrix remains open. Keep
capability reporting bounded to the recorded evidence.
Distribution review of any scaffold/spec/dependency is separate from personal
hardware acceptance. Manual grid editing and writing playlists into rekordbox's
desktop DB are separate features, not prerequisites silently included in this task.

Run research with the project's `.venv/bin/python`; see the
[prototype README](../research/cdj_export/README.md) for the pure test command.
