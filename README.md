# SoundBox

A native macOS (PySide6) music-library bridge from **rekordbox** to **Rockbox
iPods / USB players** — browse the live rekordbox library, curate it without
ever touching it, and drag playlists onto a device: exact order, per-track
cover art, honest sync semantics.

## Docs & roadmap

- **[`CLAUDE.md`](CLAUDE.md)** — orientation + the sacred safety contract (start here).
- **[`docs/ROADMAP.md`](docs/ROADMAP.md)** — status, scorecard, phases, product decisions.
- **[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)** — engine/desktop layout, data model, build.
- **[`docs/GOTCHAS.md`](docs/GOTCHAS.md)** — Qt/rekordbox/Rockbox traps (read before touching workers/sync).
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
exported as `cover.jpg` per track folder.

**Player.** Click a track, hit Space: waveform scrubber (libsndfile, with a
Qt/ffmpeg decode fallback for ALAC/AAC), per-track peaks cache for instant
redraws, output-device hot-swap (AirPods etc.), now-playing pill. Space
loads the highlighted track or toggles playback; type-ahead is disabled in
every list so typing never teleports the selection.

**Devices & sync.** Drop = export: threaded copy with one aggregating
progress bar, FINALIZING sweep (AppleDouble scrub), free-space precheck,
FAT32-safe names (length-budgeted artist/title), tree + flat `/Playlists`
layout, UTF-8 no-BOM `.m3u8` in exact order with `# MISSING:` comments.
A per-device manifest tracks what's on the stick: playlist edits/reorders/
deletes show as **⚠ N playlists out of date**; **Sync** resolves the diff
(copy new, rewrite m3u8s, prune only when needed). **Cancel is honest** —
partial m3u8s are playable and flagged so the next Sync resumes. Right-click
a device: Sync, **Verify export…** (re-reads every m3u8, confirms every file),
Eject. Click one for a full device file browser (rename, new folder, delete,
assign-artist). Non-FAT32 filesystems get flagged immediately (Rockbox
requirement).

**Persistence.** Everything user-made lives in one portable
`library.json` (overrides, playlists, imported records, hides, settings) at
`~/Library/Application Support/LowFreqLabs/Soundbox/` — keyed by stable
track IDs so a newer rekordbox library preserves all work. Atomic saves,
rotating backups, corruption auto-recovery, and one-click full backup/restore
`.zip` (library + history + art cache) in Settings.

## The safety contract (non-negotiable)

- rekordbox is **never** written to. The live `master.db` is read via a
  read-only temp snapshot; the XML export is a fallback.
- Source audio files are **never** modified, renamed, moved, or deleted.
- Every write lands **under an explicit export target** (the device), guarded
  so a target can never overlap the source library.
- Full contract with per-write commentary: top of `soundbox/sync.py`.

## Layout

    soundbox/   pure-Python engine (no Qt): rekordbox readers, data model,
                sync/resync + manifest drift, playlists (m3u8), metadata
                derivation, imported-file records + relink search, cover-art
                fetch/encode, backup — mypy-clean.
    desktop/    PySide6 app: MainWindow + mixins (settings, device browser),
                workers (all QThreads), trees, player, art picker, suggesters,
                artist catalog.
    tests/      317 tests: engine behavior, property-based (hypothesis),
                mocked live-DB reader, hermetic offscreen UI.

## Development

    ./check.sh            # pyflakes + mypy (engine) + full test suite
    ./check.sh --render   # + offscreen UI render to /tmp
    ./build_app.sh        # PyInstaller → dist/Soundbox.app (self-signed)

Python 3.14, deps in `requirements.txt` (venv at `.venv/`). Logs at
`~/Library/Logs/Soundbox/soundbox.log`. Waveform peaks cache in
`<app data>/waveforms/` (pure accelerator — safe to delete any time).
