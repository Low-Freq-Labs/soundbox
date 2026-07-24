# SoundBox architecture

## Repo layout

    soundbox/   pure-Python engine (no Qt): rekordbox readers, data model,
                sync/resync + manifest drift, playlists (m3u8), metadata
                derivation, imported-file records + relink, cover-art
                fetch/encode, backup — mypy-clean, gated by ./check.sh.
    desktop/    PySide6 app: MainWindow + mixins, workers (all QThreads),
                trees, player, art picker, suggesters, artist catalog.
    native/     arm64 macOS deck dylib: CoreAudio render/decode engine +
                CAMetalDisplayLink waveform/grid renderer, exposed by a C ABI.
    tests/      engine behavior + property-based (hypothesis) + mocked live-DB
                reader + hermetic UI + native deck/shader contracts. Trust
                ./check.sh output for the live count.
    research/   R&D that is NOT part of the shipped app (kept out of the
                packaged bundle + mypy/tests). See research/cdj_export/.
    docs/       this documentation.

## Engine modules (`soundbox/`)

`rekordbox_db` (live DB reader) · `rekordbox_xml` (core data model + XML
parser) · `engine` (the API surface) · `sync` (export + resync; **safety
contract at the top of the file**) · `paths` (sanitizing, device paths,
length budgets) · `playlists` (m3u8) · `report` · `metadata` (artist/title
derivation, title-cleanup rules) · `artfetch` + `artencode` (cover art
fetch + baseline-JPEG encode) · `manifest` (per-device drift) ·
`track_analysis` (the sole V4 analysis format: storage, streaming RGB/grid
analysis, and export eligibility) · `rekordbox_analysis` (read-only
PQTZ/PCOB/PCO2/PWV5/PWV7 parser) · `device_profile` (read-only device
classification and stock-iPod preview planning) · `backup` · `imports`
(imported-audio plane) · `libcache` (instant-paint snapshot).

## Desktop modules (`desktop/`)

`app.py` = `MainWindow` core + orchestration. Mixins/modules: `devices`,
`trees`, `workers` (every QThread), `settings_page` (`SettingsPageMixin`),
`device_browser` (`DeviceBrowserMixin`), `artpicker`, `suggest`,
`artistcatalog`, `model` (`TrackModel`), `player`, `theme`.
`class MainWindow(SettingsPageMixin, DeviceBrowserMixin, QMainWindow)`.
`native_deck.py` adapts the native C ABI to PlayerBar's small transport
protocol.

## Player clocks

The complete player handoff, including interaction semantics and committed
next work, is in `PLAYER.md`.

Qt remains the application shell. Playback is native-only; Qt Multimedia is
used only by `QAudioDecoder` while analyzing AAC/ALAC. If the native deck cannot
start, SoundBox keeps the library available, disables player controls, and
shows a precise diagnostic instead of silently switching engines. The native
Audio Unit callback is the
authoritative sample clock. A background AVAudioFile decoder fills a lock-free
ring buffer; the callback never decodes, allocates, locks, or calls Python.
Metal receives waveform/grid buffers once per track and translates them from
the CoreAudio host/sample anchor on each CAMetalDisplayLink update. Python
polling updates coarse labels and buttons only—there is no Python repaint timer
for native scrolling. CoreAudio presentation timestamps feed a continuous
phase-locked visual clock: device-rate and phase errors are corrected through a
tiny velocity bias, never a callback-boundary position step. The predicted
position remains fractional through the Metal upload, and beat-grid vertices
use integer milliseconds plus a local fractional phase instead of subtracting
large absolute floats. Maximum zoom therefore magnifies track detail, not clock
or grid quantization.

Rekordbox ANLZ files stay read-only and are an analysis seed/fallback source.
Active `djmdCue` rows come from the same read-only live database snapshot as
the library. Their normalized fingerprint updates only the V4 manifest until
SoundBox user data claims cue ownership; deleted database cues therefore clear
cleanly without touching the compressed analysis payload.
SoundBox's canonical schema-V4 record under `track_analysis/` owns the result:
full grid/cues, 150 Hz RGB, 24 normalized spectral bands, export eligibility,
provenance, and an approximately 6144-window/s amplitude envelope. Each section
is independently Zstandard-compressed and SHA-256 verified inside one atomic
per-track container. Source identity invalidates only the changed track.

Prepared loading is four-stage and generation-guarded: native source decode,
small manifest, packed RGB overview, then ultra detail/native peak pyramid.
The last three run outside the UI thread. Section reads seek directly to their
compressed byte ranges and validate only that section; `verify_complete()` is
reserved for audits. `begin_track()` clears every renderer buffer atomically,
so rapid A→B→C replacement cannot display a late A/B result.

The main scrolling waveform uses V4's ultra envelope at every zoom. On load,
Metal creates peak-preserving geometry mips and amplitude-weighted RGB mips.
Overview and scrolling lanes derive one continuous LOD from source samples per
physical pixel and blend coverage/color from the adjacent levels; there is no
wide/close zoom branch. Ultra detail promotes only geometry quality, while the
already-uploaded compact ~150 Hz RGB pyramid remains the time/color authority.
The normalized 24-band master remains stored for long-term analysis but never
participates in renderer color. Metal receives integer sample anchors plus
small local phases for both geometry and color, retaining long-track subpixel
precision. The scrolling lane is rasterized into a bounded, integer
song-pixel-anchored Metal tile. Display frames translate that fixed surface;
margin refreshes ping-pong the textures, blit their unchanged overlap, and
render only the newly exposed strip. Full reprints occur only when render data,
zoom, Retina scale, or drawable geometry changes. Tile RGB is coverage
premultiplied in half-float RGBA so translation filtering preserves hue and
silhouette as one surface before final display conversion. Render buffers are
snapshotted under a short mutex and command
encoding happens after that mutex is released.
Library preparation builds V4 incrementally, pauses during
playback, pauses audio cooking below 5 GiB free until 7 GiB is available, and
can hot-swap finished analysis or cue metadata onto the current track.

Device export is pinned by `sync.DeviceSession` to one resolved mount identity
and revalidated before each write. Manifest v2 records normalized destination,
source identity, size, SHA-256, playlist membership, artwork/playlist kind,
ownership, and completed generation. Tracks, covers, playlists, and the
manifest use destination-filesystem temporaries plus atomic replacement.
Regular sync repairs only manifest-owned corruption; Deep Verify rehashes every
managed file. Pruning follows explicit ownership rows and never walks `/Music`
as authority. Unknown content and stock-iPod databases remain untouched.

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
(library + history + art cache). The settings payload also stores the last
closed window geometry, table columns, and Rekordbox/Soundbox/Devices splitter
heights plus waveform zoom and beat-jump mode; startup restores
the outer geometry before applying inner pane sizes.

## Build / install

    ./check.sh                 # + arm64 native build, then Python gates
    ./build_native.sh          # native dylib only
    ./build_app.sh             # PyInstaller → dist/Soundbox.app (self-signed)

Python 3.14, venv at `.venv/`. `build_app.sh` uses the stable local
`Soundbox Self-Signed` identity when present so macOS can retain TCC grants
across rebuilds; it falls back to ad-hoc signing. The app is not Apple-notarized,
so first launch may still require right-click → Open. Notarization is a public
distribution concern (Phase 5).

Installation is a state-preserving handoff, not a delete/copy shortcut:
gracefully quit SoundBox, back up/hash `library.json`, move the previous app
bundle to a temporary rollback directory, `ditto` the tested bundle into
`/Applications`, verify its signature, relaunch, and inspect the startup log.
Never use `rm -rf` for installation and never touch SoundBox AppData while
replacing the application bundle.

## Safety contract

The synchronized non-negotiable rules are in `AGENTS.md` and `CLAUDE.md`; the
authoritative per-write commentary is at the top of `soundbox/sync.py`.
