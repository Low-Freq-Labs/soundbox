# SoundBox gotchas & niche facts

Hard-won + researched. **Read before touching workers, playlists, sync, art, or
the iPod path.** Related: `ARCHITECTURE.md`, `ROADMAP.md`.

## Repo hygiene
- **NEVER pipe `./check.sh` through `tail`/`grep` in a `&&` chain** — the
  pipeline exit code is `tail`'s `0`, so a FAILING gate gets masked and the
  build/commit proceeds. (Real incident: a syntax-broken module shipped + got
  committed.) Run `./check.sh` bare (or `> file 2>&1; echo $?`), then build.
- Project is under git (local `master`, no remote). **Commit at the end of every
  working session.** `dist/`, `build/`, `.venv/` are ignored.

## Qt / PySide6 (these bite HARD)
- **QThread "destroyed while running" → SIGABRT.** Never let the last Python ref
  to a worker QThread drop *mid-run*. Slots on a worker's CUSTOM signals
  (`finished_all`/`done`/`failed`) run WHILE `run()` is still finishing — nulling
  `self.x_worker` there GCs the thread on its own thread → abort. FIX (in place):
  drop the ref only on the built-in `finished` signal via
  `MainWindow._finish_worker(attr, w)` (main thread, after `run()` returns). Used
  for export/refresh/resync workers + `PlayerBar._wf`. Don't regress it.
- Accessing a `deleteLater()`'d worker raises `RuntimeError: Internal C++ object
  already deleted`. Guard with the `_running(w)` helper.
- `shutdown()` (from `closeEvent` + `app.aboutToQuit`) must stop/wait every
  worker BEFORE Qt teardown.
- **QAudioOutput binds the default output device AT CONSTRUCTION** and never
  follows macOS device switches (AirPods/DAC) → audio goes to a dead device.
  FIX (player.py): keep a `QMediaDevices` INSTANCE alive (its signals never fire
  otherwise), rebind on `audioOutputsChanged` + before every `play_track`.
- Custom QWidget with layout `stretch=1` may not expand — also set
  `setSizePolicy(Expanding, …)`.
- QSS: `QLabel { background: transparent }` or you get dark boxes behind text.
- **NEVER do synchronous removable-USB I/O on the main thread** (`statvfs`,
  reading the manifest, `walk`/`rglob`, `rmtree`, drift diff). macOS fskit
  BLOCKS these for SECONDS when the stick is busy → whole-app beachball + frozen
  animations. Thread it (`DeviceScanWorker`/`DeleteWorker`), show the lower-left
  progress bar, cache drift in `_drift_cache`. Any new device op must be threaded.

## API traps
- **Qt slots SWALLOW exceptions** — a crash inside a signal handler prints to
  stderr and keeps going, so a UI test toggling a widget can PASS while the slot
  is broken. Test slot logic by DIRECT call too. (This hid a real `is_untracked`
  crash that only surfaced in the packaged app.)
- **Rapid track loads pile up waveform decoders** → hang + latent GC-mid-run
  crash. Any "load X, user can rapid-switch" worker needs interrupt-previous +
  a strong-ref set (`_wf_pending`), never a single `self.x` reassignment;
  cache-hit paints on the UI thread with zero workers; `shutdown_waveforms()`
  interrupts + waits all.
- **Computed-row cache:** effective title/artist is cached per track_id, warmed
  in background (debounced, generation-guarded), invalidated on
  override/alias/registry/title-junk change. `_row_for` returns a COPY. If you
  add anything that changes effective title/artist, call `_invalidate_rows()`.
- **Worker convention:** `OneShotTask` and `_OneShotArt` emit result-OR-Exception
  — done-handlers MUST isinstance-check. Never map an exception to a "nothing
  there" answer (a working art URL once flashed "No art found" with no log trace).
- `Track.is_untracked(*, nokey, short, short_secs)` is **KEYWORD-ONLY**.
- `engine.export_playlists(lib, [Playlist_objects], target)` takes Playlist
  **OBJECTS**, not display-path strings. `SyncReport` has no `files_copied`
  (use `.playlists` / `.bytes_to_copy`).
- `Playlist.display_path` joins with `" / "` (SPACES around slash), not `"/"`.
- Settings page lives on `body_stack` (index 1); track table / device browser on
  `stack` (0/1).
- Splitting files with a script bypasses the Edit tool's read-tracking — Read
  again before Edit after any scripted rewrite.

## Cancel semantics (sync.py — don't regress)
- `sync.run` on cancel: writes HONEST partial m3u8s (only on-device entries +
  "# CANCELLED: N …" footer) and records incomplete playlists with **sig="partial"**
  → drift flags them → next Sync completes. A cancelled export must NEVER look
  fully synced.
- `sync.resync` on cancel: stale-m3u8 unlink still runs (cheap), but prune +
  forget are SKIPPED (re-polls `should_cancel` directly); next Sync finishes.
  Tests: `tests/test_cancel.py`.

## Type-checking + test-honesty
- `./check.sh` gates: pyflakes + **mypy on `soundbox/` (must stay 0 errors)** +
  the full suite. `desktop/` is NOT mypy-gated yet (PySide6 stub gaps).
- Never `assertX(...) and assertY(...)` (short-circuits the second). Never test
  dict/py semantics against themselves — drive the real app helper. A
  failure-tolerance test must create a REAL failure (e.g. squat a file where a
  dir must go), not assert its own monkeypatch.

## Property-test findings (keep hypothesis in the loop)
- `str.split()` does NOT cover everything `str.splitlines()` splits on
  (`\x1c-\x1e`, `\x85`, ` / `) — text in line-oriented formats (m3u8!)
  must be flattened via `ch.isprintable()`.
- `sanitize_component` turns ALL non-printables into `-` (they once split m3u8
  path lines on-device). Any new filename-ish surface must use it.
- Hypothesis tests are `derandomize=True` — deterministic; a failure is REAL.

## Offscreen UI tests can dirty the REAL config
- Constructing `MainWindow()` offscreen and firing a widget handler runs the real
  slot → `_save_state()` → WRITES the real `library.json`. For render/smoke
  tests, set state via `win.settings[...] = …` + `win._sync_settings_controls()`
  (blockSignals, NO save), or point AppDataLocation at a temp dir.
- `desktop/_shot.py` is the SELF-CONTAINED reference render harness (synthetic
  lib + qttest sandbox): `PYTHONPATH=. .venv/bin/python -m desktop._shot`.
- **A failed `open_library` pops a MODAL QMessageBox** → headless/offscreen
  scripts hang FOREVER. (The old Desktop test XML is deleted — smokes must use a
  synthetic Library or the live DB.)
- `tests/test_resync.py` (and others) end with a `__main__` guard — appending
  test methods via heredoc lands them AFTER it = silently uncollected. Verify the
  test COUNT went up.
- unittest runs classes alphabetically; UI tests that mutate lib/view state must
  fully restore (incl. `_reload_current_view()`) or later classes see stale rows.

## Metadata / veto plumbing
- Title pipeline order: effective ARTIST is computed from the RAW/overridden name
  FIRST, then `effective_title = override | tidy(neat(name, artist), junk)`. Junk
  rules: `metadata.BUILTIN_TITLE_RULES` + user words; `Library.title_junk=None`
  means defaults.
- `manifest._excluded(pl, hidden, removals)` is the single source of veto truth
  for sig/entry/diff/prune — any new "don't export this track" feature must flow
  through it or devices won't drift.
- Artist aliases: `build_registry(..., aliases)` applies them TWICE so chains
  resolve (yeschef→YESCHEF!→YES CHEF). `suggest_merges` is O(n²) with quick-ratio
  prefilters — keep it off the main thread.
- Row alignment: QTreeView items QSS-locked to `height:24px`; table + rb tree use
  `ScrollPerItem` — don't reintroduce pixel scrolling or padding-based heights.

## Python 3.14 / build
- **`aifc`, `audioop`, `sunau` REMOVED in Python 3.13+** → can't decode AIFF with
  stdlib. Use `soundfile` (libsndfile) — reads WAV/AIFF/FLAC/MP3 (NOT ALAC/.m4a).
- **ALAC/AAC waveforms use `qt_peaks()` (QAudioDecoder = same ffmpeg backend that
  plays the file).** THREE traps, don't regress: (1) an unreadable file reports
  its error SYNCHRONOUSLY inside `dec.start()` — check `done`/`dec.error()` BEFORE
  `loop.exec()` or the worker strands until process exit → SIGABRT; (2)
  `dec.stop()` is async — bounded `isDecoding()` wait before the object dies; (3)
  `drain()` checks `isInterruptionRequested()` each buffer. Must run off the UI
  thread.
- Build: `./build_app.sh` (PyInstaller) collects pyrekordbox, sqlcipher3,
  sqlalchemy, yt_dlp, soundfile, numpy, `--collect-submodules PySide6.QtMultimedia`,
  `--collect-all PIL`.
- App is UNSIGNED → Gatekeeper. Logs at `~/Library/Logs/Soundbox/soundbox.log`
  (Python errors here; C++ Qt fatals show as macOS crash reports).

## rekordbox DB (pyrekordbox) — SACRED, read-only temp copy ONLY
- **`db.get_playlist_contents()` returns tracks sorted by ContentID, NOT playlist
  order.** For track order use `DjmdSongPlaylist.TrackNo`. (Sonny's order is
  sacred — this was a real bug.)
- **SMART playlists (Attribute 4) have ZERO `DjmdSongPlaylist` rows** (computed) →
  for them use `get_playlist_contents()`. Attribute: 0=normal, 1=folder, 4=smart,
  -128=cloud(skip).
- `rb_local_deleted` truthy = soft-deleted → skip (tracks AND playlists).
- Collection has ~5244 tracks but only ~2940 are in playlists; load only
  playlist-referenced tracks.
- **`DjmdPlaylist.ID` is STABLE across rename + move** → the device-sync identity.
- rekordbox reorders → manifest sig is order-sensitive → reorder = drift = resync.
- rekordbox auto-writes a `PIONEER/` folder onto any USB it touches (not us —
  leave it alone).

## Cover art (SoundCloud scrape + Rockbox rendering)
- **Paste an ARTIST PROFILE URL into the art picker → you get the AVATAR**
  (og:image of a profile page IS the avatar; lands baseline-JPEG via `store_ok`).
- SoundCloud deep extract 403s → use FLAT search `thumbnails` (t500x500) for
  covers; artist avatar via the page `<meta og:image>`, NOT yt-dlp's user API.
- Rule: real title match → track cover, ELSE artist avatar (never a different
  track's cover).
- yt-dlp = gray area → PUBLIC fork must NOT bundle it.
- **⚠ Rockbox can't render progressive/multi-scan JPEG, RLE BMP, or PNG.**
  HANDLED: `soundbox/artencode.py` normalizes ALL cached art to baseline JPEG;
  `store_ok` enforces at the cache boundary. Any NEW art path (manual picker!)
  must go through `ArtCache.store_ok` or `artencode.ensure_device_safe`.
- Rockbox art search order in a track folder: `<trackname>.{jpg,bmp}` →
  `<albumtitle>.{jpg,bmp}` → `cover.{jpg,bmp}` → `folder.jpg`. We write per-track
  `cover.jpg` → matches. The theme/WPS must support album art or nothing shows.

## Rockbox / iPod (Phase 3 — hardware)
- **iPod MUST be FAT32, NOT HFS+.** Rockbox does not support HFS+.
- m3u8: UTF-8 **no BOM**; absolute-from-drive-root paths (`/Music/Artist/...`);
  Playlist Catalogue default dir = `/Playlists`.
- Rockbox DB is OPTIONAL (playlists play via File Browser); DB mode needs
  "Update now" after dumping files.
- Device sync manifest lives at `<device>/.soundbox/manifest.json`.
- UNVALIDATED until hardware: theme renders cover.jpg; exact order/nesting holds;
  no choke at ~2900 tracks; DB refresh flow. (USB is a good proxy for everything
  EXCEPT on-device rendering/playback.)
