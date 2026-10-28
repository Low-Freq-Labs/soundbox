# Native CDJ USB export — research and acceptance plan

Reviewed October 26, 2026. **Not shipped in either SoundBox profile.** Sonny's
requested sequence is iPod hardware acceptance when the device returns, library
management refinement, then this export milestone. See [ROADMAP](ROADMAP.md).

## Product goal

Export SoundBox's curated playlists to USB with the library and performance
behavior of a rekordbox export on the supported CDJs: normal library browsing,
playlist order, metadata/artwork, beatgrids, usable hot/memory cues and loops,
and prepared waveforms. A USB that merely plays audio through folder browsing
does not meet the goal. This is the reduced-function fallback Sonny describes
as “limp mode,” not a formal device mode name.

The original research records Sonny's setup as four CDJ-3000s; confirm the current
models and firmware before the next campaign. Acceptance is against a real
rekordbox-exported control on those decks, not just our own parser's output.
The output need not reproduce irrelevant stale bytes, but all required structures,
references and observable features must work. Rekordbox's desktop database and
source audio remain read-only.

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
The current `build_file` assembler has one data page per table and is explicitly
for small libraries; full-library pagination/capacity remains unimplemented.

OneLibrary, formerly Device Library Plus (`exportLibrary.db`), is a different
compatibility target, not a universally optional cosmetic sidecar. AlphaTheta's
[January 7, 2026 notice](https://www.pioneerdj.com/en/news/2026/cdj-3000-firmware-ver330-important-notice/)
describes classic Device Library for CDJ-3000 and OneLibrary requirements for
CDJ-3000X and other named newer products. Consult the current
[USB export compatibility guide](https://rekordbox.com/en/support/usb-export/)
before each hardware campaign; record the exact model, firmware, rekordbox version,
filesystem and library format. No OneLibrary writer is implemented here. A
classic-only result must not be presented as support for every CDJ/XDJ.

The shipped player already imports rekordbox analysis and authoritative live-DB
cues; see [PLAYER](PLAYER.md). Those readers and SoundBox's canonical analysis
provide useful inputs, not proof of valid device files. The bridge expresses
PQTZ units, cue semantics and proposed waveform streams; complete container/tag
encoding, path consistency and actual on-deck behavior still need validation.
The October 27 strict research contract separates `DisplayWaveformFields` from
byte-preserving `NativeWaveformStreams`. It no longer labels three-byte display
RGB as packed PWV5 or averages spectral bands into a purported exact PWV7.
Invalid beat numbers, nonrepresentable tempos, cue identities, endpoints and
out-of-range values are rejected instead of substituted or clamped. These are
field/provenance boundaries, not a complete binary encoder or hardware proof.
Future export must capture fresh authoritative DB cues: accepted cue edits can
be DB-only, leaving ANLZ cue sections stale. Drain accepted saves before capture;
bind verified grids, original waveform sections and cue identities to the same
track/audio source. Display palettes, GPU mips and RAM-only previews confer no
device serialization authority. Unknown original sections remain preserved.

Current SoundBox candidate policy allows MP3, WAV, AIFF/AIF and FLAC; it excludes
AAC/ALAC/M4A/MP4 from future CDJ export. This is SoundBox's present policy in
`track_analysis.export_eligibility`, not a claim that CDJs cannot play those codecs.
A filename allowlist does not replace per-model codec/sample-rate/bit-depth checks.

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

The two synthetic ANLZ-bridge tests also passed. These checks confirm retained
research inputs/code, not a fresh CDJ run or an accepted generated library.

## Next experiment — scaffold and inject

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

## Acceptance before product integration

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

Before a product writer exists, define explicit target confirmation, mount identity,
source/target separation, ownership/coexistence with existing rekordbox exports,
space planning, recovery and final publication order. No existing production
exporter gains `/PIONEER` authority from this document. Any future exception needs
a dedicated reviewed contract, tests and hardware acceptance; ordinary stock,
dual and generic exporters retain their present guard. Do not borrow iPod recovery
as permission to modify a CDJ USB.

Once the declared device/feature matrix passes, wire the backend into the shared
app with honest capability reporting and the normal release/install gates.
Distribution review of any scaffold/spec/dependency is separate from personal
hardware acceptance. Manual grid editing and writing playlists into rekordbox's
desktop DB are separate features, not prerequisites silently included in this task.

Run research with the project's `.venv/bin/python`; see the
[prototype README](../research/cdj_export/README.md) for the pure test command.
