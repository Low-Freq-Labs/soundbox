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
normalized to **baseline JPEG** — the only flavor older Rockbox renders — and
exported as `cover.jpg` per Rockbox track folder. In parallel, a low-priority
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
FAT32-safe names (length-budgeted artist/title), tree + flat `/Playlists`
layout, UTF-8 no-BOM `.m3u8` in exact order with `# MISSING:` comments.
A per-device ownership manifest tracks normalized paths, source identity,
hashes, playlist membership, artwork, and completed generations. Playlist
edits/reorders/deletes show as **⚠ N playlists out of date**; **Sync** resolves
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
`/.rockbox/` devices receive the broader Rockbox filesystem export while any
coexisting stock database remains read-only.
Selecting a stock-only iPod opens Device, Music, and Playlists tabs backed by a
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

Current dual-boot operation is deliberately fail-closed: Rockbox receives the
full Rockbox-compatible library and stock iTunesDB is untouched. The approved
future dual-library design is single-copy rather than duplicate: stock-safe
files live once under `iPod_Control/Music`, are indexed by iTunesDB, and are
referenced by Rockbox playlists; stock-unsafe but Rockbox-supported files live
once under `/Music` and never enter iTunesDB. That composite planner/manifest is
not implemented yet.

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
  A mounted `.rockbox` currently selects Rockbox export and keeps iTunesDB
  read-only; the future dual-library planner must remain single-copy.
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
