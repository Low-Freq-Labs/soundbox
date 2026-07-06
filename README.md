# SoundBox

A native macOS (PySide6) bridge from **rekordbox** to **Rockbox iPods / USB
players**: browse the live rekordbox library, curate titles/artists/cover art,
and drag playlists onto a device — exact order, per-track cover art, honest
sync semantics. Built-in player with real waveforms for every format the app
can play (libsndfile + a Qt/ffmpeg fallback for ALAC/AAC, cached per track);
an Artists browser with the same inline editing as the Collection; per-view
place memory so switching playlists never loses your spot.

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
                derivation, cover-art fetch/encode, backup — mypy-clean.
    desktop/    PySide6 app: MainWindow + mixins (settings, device browser),
                workers (all QThreads), trees, player, art picker, suggesters.
    tests/      279 tests: engine behavior, property-based (hypothesis),
                mocked live-DB reader, hermetic offscreen UI.

## Development

    ./check.sh            # pyflakes + mypy (engine) + full test suite
    ./check.sh --render   # + offscreen UI render to /tmp
    ./build_app.sh        # PyInstaller → dist/Soundbox.app (self-signed)

Python 3.14, deps in `requirements.txt` (venv at `.venv/`). The app stores its
portable library (overrides, playlists, settings) at
`~/Library/Application Support/LowFreqLabs/Soundbox/library.json` — atomic
saves, rotating backups, full-cache .zip backup/restore from Settings.
Waveform peaks are cached beside it in `waveforms/` (pure accelerator —
safe to delete any time).
