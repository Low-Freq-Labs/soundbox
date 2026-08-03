# SoundBox

A native macOS (PySide6) music-library bridge from **rekordbox** to **stock or
Rockbox iPods / USB players** — browse the live rekordbox library, curate it without
ever touching it, and drag playlists onto a device: exact order, per-track
cover art, honest sync semantics.

## Docs & roadmap

- **[`AGENTS.md`](AGENTS.md) / [`CLAUDE.md`](CLAUDE.md)** — synchronized
  runtime orientation + sacred safety contract (start here).
- **[`docs/HANDOFF.md`](docs/HANDOFF.md)** — exact current device/app state,
  rollback facts, validation, and next-session sequence.
- **[`docs/ROADMAP.md`](docs/ROADMAP.md)** — status, scorecard, phases, product decisions.
- **[`docs/PLAYER.md`](docs/PLAYER.md)** — native deck behavior, architecture,
  caches, read-only cue panel, non-regression rules, QA, and pending grid work.
- **[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)** — engine/desktop layout, data model, build.
- **[`docs/DEVICES.md`](docs/DEVICES.md)** — Rockbox, stock iPod, dual-boot,
  and generic-USB layouts, capabilities, and validation rules.
- **[`docs/GOTCHAS.md`](docs/GOTCHAS.md)** — Qt/native-player/rekordbox/Rockbox
  traps (read before touching workers, player, or sync).
- **[`new-ideas-7-22.md`](new-ideas-7-22.md)** — exploratory QOL backlog; UX
  workshop before roadmap commitment.
- **[`docs/cdj-usb-export.md`](docs/cdj-usb-export.md)** — CDJ USB export R&D (prototype in `research/cdj_export/`).

## What it does

**Library.** Reads the live rekordbox database (`master.db`, via a read-only
temp snapshot — rekordbox can stay open) or an XML export as fallback; ↻
Refresh any time. On top of the rekordbox mirror, SoundBox keeps its own
plane: **import audio files directly** (drag from Finder onto a SoundBox
playlist or Collection, or right-click → Import Audio Files…). Imported files
are referenced in place — never copied or re-tagged — and become first-class
tracks: edits, art, waveforms, export. Reorganized your files on disk?
Right-click → **Fix Missing Links…** relocates them (filename + size match,
never guesses; folder-picker fallback).

**Sidebar.**
- **⌂ Collection** — every track (rekordbox + imported), newest first.
- **♪ Artists** — the same pool grouped A→Z by effective artist, expandable
  per artist.
- Both are pinned views *and* draggable playlists: drop one on a device to
  export the whole pool in that view's order.
- **★ SoundBox playlists** — your own: drag tracks in, drag the list to
  reorder, rename/delete via right-click. Duplicate adds prompt
  Add Anyway/Skip (Skip default). Names `Collection`/`Artists` are reserved.
- **⛃ Devices** — external drives only, auto-detected; drag any playlist
  (rekordbox, SoundBox, or a pinned view) onto one to export immediately.

**Curation.** Inline Title/Artist edits (double-click or right-click) become
per-track overrides that rename files on the device — sources untouched.
Every override teaches the artist deriver (trusted-name gating for reversed
"Title - Artist" flips, collab-aware splitting, Unknown rescue from titles).
Batch tools: **Suggest artist merges…** (fuzzy spelling dedup with in-dialog
Unmerge), **Suggest artists for Unknowns…**, named **Title Cleanup** rules
(built-ins by name + custom words or validated regex), neat-title
edge-artist stripping. Deletes are two-plane: **Delete from Collection**
(global hide, restorable from the dated **Graveyard** in Settings) vs
**Remove from Playlist** (per-playlist veto). Both drift synced devices;
rekordbox never changes.

**Cover art.** Background auto-fetch with an accuracy-first matcher (real
title match → track cover, else artist avatar; wrong art is treated as worse
than no art), resumable and scoped to new tracks only. Manual per-track
picker (search results, image file, or URL — an artist profile URL yields
the avatar), an **Artist Pictures catalog** for avatar-wearing artists, and
per-track blocking ("Remove cover art" is sticky). Everything in the cache is
normalized to **baseline JPEG** as canonical app state. The Rockbox exporter
derives 8-bit paletted BMPs per track: baseline 160×160/116×116 fast paths,
exact square sizes discovered from the active WPS/SBS, and a lightweight
dimensionless 160×160 fallback. Exact sizes are retained as themes change. All
are siblings of the audio file. The exporter also
removes embedded pictures only from its verified device-side audio copy, so a
huge source APIC cannot outrank the curated cover or exhaust a classic iPod.
Source audio and canonical curated JPEGs remain byte-identical. In parallel, a low-priority
worker pre-generates content-addressed MA446 RGB565 derivatives for stock iPods.
Stock sync assembles those prepared pixels into `F1028_1.ithmb` (100×100),
`F1029_1.ithmb` (200×200), and a validated `ArtworkDB`; identical curated
images share one pixel payload while every track retains its own database link.
Source audio is never retagged or used as the artwork store.

**Player.** The default native deck uses CoreAudio sample-clock playback while
Metal scrolls the RGB waveform/grid at display refresh (120 Hz on ProMotion).
Three compact header toggles provide playlist-relative autoplay: up, a
no-repeat shuffle bag, or down. The source playlist is frozen when a track is
deliberately started, so browsing elsewhere does not redirect playback.
It includes a whole-song overview, thirteen detail zoom levels from 24–1536
px/s, bar/beat
counting, previous/next, selectable beat/bar jumps, momentary CUE, clean
play/pause/seek ramps, and precise pause-preview-resume scrubbing. Double-click
is the only browser load gesture and loads paused; Space only toggles the
loaded deck, and C is momentary CUE. Grabbing the playing waveform pauses it
immediately; releasing without moving resumes from the exact held timestamp.
A native atomic hold uses the frame actually visible under the fixed playhead,
not a lagging UI poll. Prepared overview and ultra buffers become visible
together, so long sets never inflate when detail finishes loading.
A newly loaded track starts on its first analyzed beat. SoundBox V5 owns the
prepared grid/cues, RGB/24-band
analysis, export eligibility, and single-silhouette ultra waveform. Valid
Rekordbox analysis seeds missing data, while cues refresh by fingerprint from
a read-only live-DB snapshot until SoundBox user data claims ownership.
Stored hot-cue colors appear as black-outlined A–H boxes on both the
whole-track overview and scrolling waveform; memory cues use one consistent
red upward arrow in both scales.
Waveform color comes from SoundBox's dedicated overlapping time-domain
filterbank: low-pass 100 Hz, band-pass 300–3000 Hz, and high-pass 1500 Hz,
followed by frequency-dependent peak holds and a local RGB energy transfer.
The separate 24-band master remains long-term analysis data and never
recolors or overrides that RGB stream in either renderer.
Rekordbox and source audio remain read-only. Native-deck failure disables
playback with a diagnostic while library management remains available.

**Devices & sync.** Drop = export: threaded copy with one aggregating
progress bar, FINALIZING sweep (AppleDouble scrub), free-space precheck,
FAT32-safe names (length-budgeted artist/title), catalogue-visible flat
`/Playlists` names for dual iPods, and UTF-8 no-BOM `.m3u8` in exact order with
`# MISSING:` comments. Dual-iPod Rockbox filenames receive zero-padded order
prefixes while SoundBox and stock-firmware titles stay clean. Generic
Rockbox/USB exports may additionally mirror the playlist tree for file-browser use.
A per-device ownership manifest tracks normalized paths, source identity,
hashes, playlist membership, artwork, and completed generations. Playlist
edits/reorders/deletes show as **⚠ Sync needed**; **Sync** resolves
the diff, atomically repairs owned corruption, and prunes only explicitly owned
paths. **Cancel is honest** —
partial m3u8s are playable and flagged so the next Sync resumes. Right-click
a device: Sync, **Verify export…**, **Deep Verify managed files…**,
**Erase all files from device…**, Eject. Whole-device erase names the exact
volume in a destructive confirmation, removes the SoundBox manifest last, and
normally ejects only after a complete result. Stock-only MA446 iPods have a
separate MP3/WAV two-phase path: read-only database plan, explicit confirmation,
recovery archive, atomic media/artwork copies, and `iTunesDB` committed last.
Its confirmation reports linked tracks, unique images, and artwork bytes. Other
stock models remain read-only. The connected layout chooses the destination
rules automatically: stock-only devices use the conservative database path;
Rockbox-only/generic FAT32 devices use the broader filesystem path; and a FAT32
MA446 containing both layouts enters the composite single-copy workflow below.
Selecting a stock or dual-boot iPod opens Device, Songs, and Playlists tabs backed by a
read-only database snapshot rather than exposing its internal FAT folders. A
confirmed model is remembered per persistent iPod-library/volume identity. On
macOS the Device dashboard also separates Apple-reported firmware and hardware
serial, serial-derived model, model-derived DAC, and SCSI transport revision so
inferred hardware is never presented as a direct probe. The DAC row stays concise,
while its catalog provenance remains in the snapshot. The Playlists tab shows
user-created playlists plus the narrowly deletable Apple **Videos** system
index; the library root and all other built-in smart lists stay protected.
On-The-Go is generated by stock firmware and cannot be deleted through iTunesDB.
Normal eject flushes and verifies references; force-unmount always asks first.
The device browser can copy selected audio/M3U8 entries in order into
`~/Music/SoundBox Media/Unknown Artist/` without changing the device. Click one
for rename, new folder, delete, and assign-artist. Non-FAT32 filesystems get
flagged immediately (Rockbox
requirement).

Dual-boot sync is deliberately single-copy. A complete no-write
plan classifies each selected source: validated MA446-safe MP3 or stereo
PCM16/44.1-kHz WAV lives once under `iPod_Control/Music`, is indexed by iTunesDB,
and is referenced by Rockbox; stock-unsafe but Rockbox-supported media lives
once under `/Music`; unsupported-by-both and missing sources are reported.
Every shared or Rockbox-only payload gets its own sibling
`filename.160x160.bmp`, `filename.116x116.bmp`, and universal `filename.bmp`, so
tracks sharing a folder cannot steal one another's artwork. Those Rockbox-only derivatives use the
theme-native uncompressed 8-bit paletted BMP representation, reducing device
reads by roughly two thirds versus 24-bit BMP while leaving canonical JPEGs and
stock-iPod artwork unchanged.
SoundBox asks before expensive planning begins, archives exact stock recovery,
publishes the
Apple database before Rockbox playlists, and writes one schema-3 ownership
manifest last. A later-phase failure rolls back the committed stock phase,
restores managed Rockbox rewrites, and removes only files created by that plan.
Existing schema-3 audio is not streamed during planning: SoundBox validates its
ownership/path/size baseline, verifies the exact desired Mac-side derivative,
then replaces one file at a time with a transient same-volume rollback rename.
Old managed-audio bytes are discarded after success; unowned paths are untouched.
If USB remounts mid-generation, the last committed library remains authoritative.
Re-queueing the playlist verifies and adopts only exact completed leftovers;
Mac-side proof journaling makes that verification resumable too. Additional
playlist drops made while a dual-iPod sync is running are queued and deduplicated,
then planned against the newly committed media instead of recopied.
Sync, playlist export, and managed-playlist deletion all follow the same visible
contract: immediate Yes/No, background Planning, guarded device writes, then one
completion. Planning occupies the first 30% of the same progress bar and commit
the remaining 70%, so the bar never disappears or jumps backward between phases
or queued playlist jobs.
The first generation requires a device without an existing SoundBox manifest;
later playlist drops accumulate safely, and an identical repeat is zero-write.
Thereafter the normal Sync action uses the same stable playlist identities and
composite planner to reconcile rekordbox renames, moves, order, membership, and
removal across both firmware views. Effective title/artist is also part of that
baseline, so a metadata edit marks the device Sync needed and repairs both the
stock database and Rockbox-visible payload metadata. Removed playlist files are recovery-backed;
media and artwork are removed only when no remaining managed playlist references
them. Managed audio requires an ownership/path/size baseline, while art and
playlists retain live hash checks. Orphans are renamed into transient same-volume
recovery and restored exactly on failure; the temporary bytes are discarded
only after the schema-3 manifest publishes successfully. Cleanup therefore does
not depend on regenerating an older cover with the current multiview profile.
Schema-2 migration remains disabled and fails closed.

Composite sync also manages four narrow Rockbox settings: automatic tag-cache
updates, playlist-viewer titles from tags, alphabetic catalogue sorting, and a
minimum 10,000-entry playlist buffer while preserving any larger user value.
Every unrelated `config.cfg` line is preserved, and the config update
participates in the same recovery-backed transaction. Rockbox itself remains
the sole writer of its rebuildable
`database_*.tcd` files; after eject, its next boot reconciles changed and removed
tracks instead of SoundBox guessing at the binary cache format.

On a schema-3 dual device, **Songs** and **Playlists** are unified views rather
than stock-only tabs. Their Type column reports `Both`, `Stock`, or `Rockbox`
from the validated ownership manifest and parsed iTunesDB. Playlist expansion
keeps the verified M3U8 order and labels every child track the same way. Apple
smart indexes remain protected internal state and are not presented as managed
dual-library metrics or playlists. `Syncing iPod...` remains animated while a
separate `#/#` counts
audio files actually copied, replaced, or removed; commits with no audio work
hide that counter. Successful sync/deletion refreshes the open Device, Songs,
and Playlists snapshot before the sidebar free-space/drift scan, so navigating
away and back is never required. The status label reserves only the active
animation width, keeping the count beside it. Approval is intentionally concise
and precedes the expensive exact plan; completion reports the result without
exposing the transaction's internal checklist.
Before that first composite generation, deleting old stock playlists removes
their now-unreferenced iTunesDB records and linked device media after the guarded
database commit. Source-library audio and unrelated Rockbox paths stay untouched.

**Persistence.** Everything user-made lives in one portable
`library.json` (overrides, playlists, imported records, hides, settings) at
`~/Library/Application Support/LowFreqLabs/Soundbox/` — keyed by stable
track IDs so a newer rekordbox library preserves all work. Atomic saves,
rotating backups, corruption auto-recovery, and one-click full backup/restore
`.zip` (library + history + art cache) in Settings. Window size/position,
columns, and sidebar pane proportions reopen exactly from the last clean close.

## The safety contract (non-negotiable)

- rekordbox is **never** written to. The live `master.db` is read via a
  read-only temp snapshot; the XML export is a fallback.
- Source audio files are **never** modified, renamed, moved, or deleted.
- Every write lands **under an explicit export target** (the device), guarded
  so a target can never overlap the source library.
- Writes below `iPod_Control` go only through the guarded MA446 stock adapter.
  On a dual layout, only the composite planner may coordinate that adapter with
  Rockbox media/playlists and schema-3 ownership; independent exporters are
  forbidden.
- Full contract with per-write commentary: top of `soundbox/sync.py`.

## Layout

    soundbox/   pure-Python engine (no Qt): rekordbox readers, data model,
                sync/resync + manifest drift, playlists (m3u8), metadata
                derivation, imported-file records + relink search, cover-art
                fetch/encode, backup — mypy-clean.
    desktop/    PySide6 app: MainWindow + mixins (settings, device browser),
                workers (all QThreads), trees, player, art picker, suggesters,
                artist catalog.
    native/     arm64 macOS CoreAudio decode/render deck + Metal waveform/grid.
    tests/      engine, property-based, live-DB mocks, hermetic UI, native deck,
                and Metal shader contracts; trust ./check.sh for the live count.

## Development

    ./check.sh            # pyflakes + mypy (engine) + full test suite
    ./check.sh --render   # + offscreen UI render to /tmp
    ./build_native.sh     # CoreAudio/Metal dylib only
    ./build_app.sh        # PyInstaller → dist/Soundbox.app (self-signed)

Python 3.14, deps in `requirements.txt` (venv at `.venv/`). Logs at
`~/Library/Logs/Soundbox/soundbox.log`. Player accelerators live under AppData:
`track_analysis/` contains the canonical player-analysis format: atomic,
checksummed V5 per-track records; V4 is read-only migration input. Derived
analysis and the content-addressed `stock_artwork/` RGB565 cache are rebuildable,
but `library.json` and the curated `art/` cache are
not; cache maintenance must never touch it, Rekordbox, or source audio.
