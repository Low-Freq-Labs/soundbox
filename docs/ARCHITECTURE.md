# SoundBox architecture

## Repo layout

    soundbox/   pure-Python engine (no Qt): rekordbox readers, data model,
                sync/resync + manifest drift, playlists (m3u8), metadata
                derivation, imported-file records + relink, cover-art
                fetch/encode, backup — mypy-clean, gated by ./check.sh.
    desktop/    PySide6 app: MainWindow + mixins, workers (all QThreads),
                trees, player, art picker, suggesters, artist catalog.
    tests/      engine behavior + property-based (hypothesis) + mocked
                live-DB reader + hermetic offscreen UI. (~317 tests — trust
                ./check.sh output, not any number written down.)
    research/   R&D that is NOT part of the shipped app (kept out of the
                packaged bundle + mypy/tests). See research/cdj_export/.
    docs/       this documentation.

## Engine modules (`soundbox/`)

`rekordbox_db` (live DB reader) · `rekordbox_xml` (core data model + XML
parser) · `engine` (the API surface) · `sync` (export + resync; **safety
contract at the top of the file**) · `paths` (sanitizing, device paths,
length budgets) · `playlists` (m3u8) · `report` · `metadata` (artist/title
derivation, title-cleanup rules) · `artfetch` + `artencode` (cover art
fetch + baseline-JPEG encode) · `manifest` (per-device drift) · `waveform` ·
`backup` · `imports` (imported-audio plane) · `libcache` (instant-paint
snapshot).

## Desktop modules (`desktop/`)

`app.py` = `MainWindow` core + orchestration. Mixins/modules: `devices`,
`trees`, `workers` (every QThread), `settings_page` (`SettingsPageMixin`),
`device_browser` (`DeviceBrowserMixin`), `artpicker`, `suggest`,
`artistcatalog`, `model` (`TrackModel`), `player`, `theme`.
`class MainWindow(SettingsPageMixin, DeviceBrowserMixin, QMainWindow)`.

## Data model

- **Reads the LIVE rekordbox DB** (`~/Library/Pioneer/rekordbox/master.db`) via
  a read-only temp copy through `pyrekordbox`; XML export is a fallback.
- **Two planes.** rekordbox is king (the mirror); SoundBox keeps its own plane
  on top: imported audio (referenced in place, never copied), per-track
  title/artist overrides, custom playlists, and deletes/removals that act as
  *vetoes* over the mirror. Both planes drift synced devices; rekordbox is never
  changed.
- **Playlist identity = rekordbox `pid`** (`DjmdPlaylist.ID`), stable across
  rename/move → it is the device-sync identity. Rename/move = same pid (syncs);
  a genuinely new playlist = new pid = fresh export.
- **Device filenames = SoundBox effective titles**, always (sanitized +
  length-budgeted; source files never renamed).
- **Collection order = newest-ADDED first**, one timeline across both planes:
  `Track.added_ts` = rekordbox `created_at` for mirror tracks, SoundBox import
  time for imported audio (falls back to file birthtime when unknown). See the
  GOTCHAS caching note — the field must ride through `libcache` + `_landed_cache`.
- **Drag-to-add works on the sidebar AND the main browser.** Dropping tracks or
  Finder audio onto the open view adds to the playlist it shows; Collection
  imports files only; Artists (tag-derived) and read-only rekordbox views reject
  every drop (`TrackTable.resolve_drop` gates it — rekordbox is never written).

## Persistence

Everything user-made lives in one portable `library.json` (overrides,
playlists, imported records, hides/removals, settings) at
`~/Library/Application Support/LowFreqLabs/Soundbox/`, keyed by stable track
IDs so a newer rekordbox library preserves all work. Atomic saves, rotating
backups, corruption auto-recovery, one-click full `.zip` backup/restore
(library + history + art cache).

## Build / install

    ./check.sh                 # gate: pyflakes + mypy(engine) + suite
    ./build_app.sh             # PyInstaller → dist/Soundbox.app (self-signed)
    # install:
    rm -rf /Applications/Soundbox.app && cp -R dist/Soundbox.app /Applications/ \
      && xattr -dr com.apple.quarantine /Applications/Soundbox.app

Python 3.14, venv at `.venv/`. App is self-signed (unsigned for Gatekeeper →
first open needs right-click → Open, or the `xattr` above). Notarization
(Apple Developer, $99/yr) is a public-distribution concern (Phase 5).

## Safety contract

The non-negotiable rules are in `CLAUDE.md`; the authoritative per-write
commentary is at the top of `soundbox/sync.py`.
