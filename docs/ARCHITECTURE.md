# SoundBox architecture

## Repo layout

    soundbox/   pure-Python engine (no Qt): source-neutral library model,
                rekordbox adapters, sync/resync + manifest drift,
                playlists (m3u8), metadata
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

`library` (source-neutral `Track` / `Playlist` / `Library` model) ·
`rekordbox_db` (live DB adapter) · `rekordbox_xml` (XML adapter) ·
`engine` (the API surface) · `sync` (export + resync; **safety
contract at the top of the file**) · `paths` (sanitizing, device paths,
length budgets) · `playlists` (m3u8) · `report` · `metadata` (artist/title
derivation, title-cleanup rules) · `artfetch` + `artencode` (cover art
fetch + baseline-JPEG encode) · `manifest` (per-device drift) ·
`track_analysis` (canonical V5 storage plus read-only V4 compatibility,
streaming RGB/grid
analysis, and export eligibility) · `rekordbox_analysis` (read-only
PQTZ/PCOB/PCO2/PWV5/PWV7 parser) · `device_profile` (read-only device
classification and stock-iPod preview planning) · `backup` · `imports`
(imported-audio plane) · `libcache` (instant-paint snapshot).

## Source-adapter boundary

The dependency direction is deliberate:

    rekordbox_db ──┐
                   ├──> library model ──> curation / player / sync / devices
    rekordbox_xml ─┘

`soundbox.library` owns the only domain types consumed downstream. A source
adapter reads its own system without modifying it, normalizes tracks and exact
playlist order into those types, and assigns a stable `source_kind`.
Provider-native playlist key formats never cross this boundary: for example,
the rekordbox XML adapter converts `KeyType=1` location keys to canonical track
IDs before constructing a `Playlist`.

Optional lazy analysis is exposed on `Track` as the provider-neutral
`source_analysis_path`, while cue rows are normalized into `source_cues`. The
current rekordbox adapter is the only implementation and the player still
understands rekordbox ANLZ sidecars. A future source with its own analysis
format should add a focused adapter/decoder at that edge; it should not teach
the library model, sync engine, or device exporters about that provider.

There is intentionally no speculative provider registry or abstract base class
yet. When a second real source is implemented, its actual discovery,
configuration, refresh, and error semantics should determine that small
interface. Until then, architecture tests keep the domain model free of
provider imports, and the existing rekordbox modules remain concrete adapters.

## Desktop modules (`desktop/`)

`app.py` = `MainWindow` core + orchestration. Mixins/modules: `devices`,
`trees`, `workers` (every QThread), `settings_page` (`SettingsPageMixin`),
`device_browser` (`DeviceBrowserMixin`), `artpicker`, `suggest`,
`artistcatalog`, `model` (`TrackModel`), `player`, `theme`.
`class MainWindow(SettingsPageMixin, DeviceBrowserMixin, QMainWindow)`.
`native_deck.py` adapts the native C ABI to PlayerBar's small transport
protocol.

`desktop/app.py` remains the largest general orchestration hotspot. Its
sections are explicit and cohesive mixins already own Settings and the device
browser, but future product work should move behavior only when it creates a
real independently testable controller/service boundary; splitting methods
among cosmetic mixins would reduce line count without reducing coupling.
`desktop/player.py`, `soundbox/track_analysis.py`, and the native deck are also
large, but their waveform/cache/realtime invariants are intentionally kept
close and should be separated only along tested protocol or data-format
boundaries.

## Player clocks

The complete player reference, including interaction semantics and committed
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
large absolute floats. Every display callback predicts the authoritative
CoreAudio position directly for its target presentation timestamp; waveform,
grid, cues, and playhead share that timestamp. A four-refresh visual-debt
smoother was rejected in installed-app testing because it turned one rare
omitted ProMotion callback into several visibly speed-modulated frames.
Maximum zoom therefore magnifies track detail, not clock or grid quantization.

Rekordbox ANLZ files stay read-only and are an analysis seed/fallback source.
Active `djmdCue` rows come from the same read-only live database snapshot as
the library. Their normalized fingerprint and the exact normalized PQTZ beat
rows update only the V5 manifest until SoundBox user data claims ownership;
deleted database cues and later Rekordbox beat-grid corrections therefore land
without touching the compressed analysis payload or decoding source audio.
SoundBox's canonical schema-V5 record under `track_analysis/` owns the result:
full grid/cues, exact-or-derived 150 Hz BLUE/PWV3, RGB/PWV5, and 3BAND/PWV7
display streams, 24 normalized spectral bands, export eligibility, provenance,
and exact signed-range renderer mips derived from an approximately
6144-window/s analysis. Each section is
independently Zstandard-compressed and SHA-256 verified inside one atomic
per-track container. Source identity invalidates only the changed track.
Current high-rate geometry is versioned as
`soundbox-polarity-range-mips-i16-v5`. V5 stores little-endian signed minimum
and maximum extrema at the exact song-anchored divisors selected by the
supported zoom/scale contract:
`8/13/18/25/35/50/66/99/132/192/256`. The compact overview keeps one symmetric
maximum. `waveform_model`, `range_encoding`, `range_mip_encoding`, and the
explicit divisor list version the payload. V4 remains read-only/playable
during migration; conversion and fresh analysis share one reduction routine,
and V4 is retired only after a complete V5 audit. A display-only `height^1.4`
curve,
0.84 ceiling, and continuous four-LSB visibility shoulder are applied after
loading; persisted amplitudes remain linear.

The dedicated RGB stream is versioned separately as
`soundbox-rgb-filterbank-v2`. Its timeline correction follows the decoder:
lossless PCM uses zero offset, while MP3 retains the measured seven-row /
46.7 ms correction. This prevents lossless kick/snare hue from landing on the
following transient without changing frequency-band semantics.

`display_waveform_model=rekordbox-blue-rgb-three-band-v1` versions the three
mode payloads together. A Rekordbox-backed track imports the original packed
PWV3/PWV5/PWV7 rows read-only; another source derives all three during the same
audio pass. The native deck retains three GPU pyramids and switches the
persistent Settings selection immediately, without reopening Rekordbox or
recooking the track.

Prepared loading is four-stage and generation-guarded: native source decode,
small manifest, packed RGB overview, then ultra detail/native peak pyramid.
The last three run outside the UI thread. Section reads seek directly to their
compressed byte ranges and validate only that section; `verify_complete()` is
reserved for audits. `begin_track()` clears every renderer buffer atomically,
so rapid A→B→C replacement cannot display a late A/B result.

The main scrolling waveform uses V5's prepared signed-range mip pyramid at
every supported zoom. Metal uploads those levels directly and still creates
the compact amplitude-weighted RGB mips.
Overview and scrolling lanes derive one footprint-matched ceiling LOD from
source samples per physical pixel and select one song-anchored level; there is
no adjacent-level blend. The native ABI snaps every requested zoom to the same
thirteen canonical stops exposed by the UI, so renderer policy never depends
on proximity to an unsupported scale. For the scrolling lane, a compute pass
reconstructs height and premultiplied RGB onto a fixed global song-space
attribute lattice.
Keeping color premultiplied makes its final one-pixel integral phase-stable.
Positive cubic reconstruction stabilizes 24–384 px/s. At 576–1536 px/s,
BLUE/RGB signed min/max intervals use direct linear reconstruction with the
literal physical-pixel footprint. At the five close UI stops
(384/576/864/1152/1536), those signed intervals select one native integer peak
level from a four-physical-pixel output footprint. The resulting
`35/25/18/13/8` divisors hold alternating extrema density to 0.20–0.26 cells
per physical pixel before the fixed song-space attribute lattice; it is not a
final-image blur. Because max pooling raises typical amplitude as divisors
grow, BLUE/RGB signed min/max are normalized from 64 px/s upward against the
50-cell row with a shallow divisor curve. The divisor gain is applied only
through each signed magnitude's remaining headroom,
`m + (gain - 1) * m * (1 - m)`, rather than as a clipping multiply. Narrow
64/92/128 compensation completes the far-to-close ladder. This keeps vertical
scale continuous across the 256→384 source-family boundary while fixing zero
and full scale and leaving horizontal cadence and cache data unchanged. Native
PWV7
retains its own display-cell timing, but the scrolling 3BAND lane begins at
the paired-row peak level:
PWV7 is stored on the common 150 Hz clock while Rekordbox's supplied close
references visibly author 75 Hz facets. Successive paired low/mid/high peaks
are connected linearly; a nearest-cell hold is still rejected.
Every zoom uses four attributes per Retina pixel for
continuous subpixel motion. The private buffer margin scales with that density
and is recentered from an absolute integer song coordinate. High-rate detail
promotes only geometry quality, while
the already-uploaded compact ~150 Hz RGB pyramid remains the time/color
authority.
The normalized 24-band master remains stored for long-term analysis but never
participates in renderer color. Metal receives integer sample anchors plus
small local phases for both geometry and color, retaining long-track subpixel
precision. The scrolling lane translates fixed height/hue attributes into each
final Retina drawable pixel. The fragment computes vertical silhouette
coverage and composites the final color exactly once. The accepted
quarter-pixel profile remains in use below 1152 px/s. At 1152–1536 px/s the
pixel endpoints plus every integer knot of the four-attributes-per-pixel
lattice form the complete piecewise-linear profile; integrating those true
segment spans avoids a hidden corner moving through a fixed probe interval.
There is no translated RGBA texture or second coverage/color filter; every
zoom retains fractional travel through the oversampled attribute lattice. The
antialias window is exactly one physical pixel. The small overview applies
continuous cubic vividness to true subpixel coverage; the scrolling lane uses
exact geometric coverage at every zoom so saturated edges are not
over-amplified.
For BLUE/RGB signed intervals, the fragment additionally integrates clipped
linear coverage moments and conditions color on the locally visible portion of
each quarter-pixel segment. PWV5 RGB and BLUE's center/width-dependent radial
field are accumulated in premultiplied linear light; a hidden horizontal sample
cannot tint a visible interval. The established continuous upper/lower boundary
coverage is reapplied exactly after the color resolve, preserving silhouette
geometry independently from color.
The canonical 64–256 px/s midrange family applies one same-LOD
half-physical-pixel `[1, 2, 1]` reconstruction pass before that final
integration. The 24–48 distant stops and 384–1536 close family bypass it; it
changes neither mip selection nor the continuous close-detail path.
Scrolling grid rectangles carry signed local line distance into the fragment,
which applies analytic physical-pixel box coverage instead of rasterizing a
hard two-pixel-wide Retina rectangle. The drawable is BGRA8Unorm_sRGB:
display-authored waveform/grid RGB is decoded before the fragment result,
coverage and fixed-function grid blending happen in linear light, and Metal
encodes exactly once on store. Full colors and the `#0d0e12` clear remain
unchanged while equal geometric edge phases retain equal displayed energy.
Downbeat grid lines composite as opaque red over the waveform and ordinary
beat lines as opaque gray. The grid is the final timing-reference layer; its
color and contrast do not inherit the waveform underneath.
There is no temporal shutter, so moving and paused contours have the same
sharpness. Actual
`MTLDrawable.presentedTime` callbacks, rather than display-link submissions,
drive cadence diagnostics and the grab handoff. Presentation latency is one
frame, and the newest committed source frame is held atomically so mouse-down
cannot rewind or change waveform thickness.
For temporal R&D, an explicit developer-only frame trace preallocates a bounded
shared Metal buffer and blits the final drawable crop after encoding. Its CSV
ledger correlates display-link callback/target times, source frame, drawable
ID, GPU interval, and actual presentation time with the exact BGRA pixels.
Nothing writes or encodes on the display/audio paths; export occurs only after
the stopped run under the caller's explicit directory. The orchestration entry
point is `scripts/capture_waveform_temporal.py`. While a trace is armed, two
small repeated Gray-code/parity strips at the main-lane edges encode its exact
sequence index into the photographed panel. A fixed-waveform calibration
segment uses those strips to recover the phase, exposure blending, and rolling
shutter relationship between a 60 fps camera and the 120 Hz display. Timing
summaries also mirror the deterministic close audio-detail mip selection and
record the selected divisor, signed-carrier lattice cells per physical
pixel/refresh, and Nyquist-folded panel/camera rates. This keeps a real source
carrier distinct from dropped presentation or camera sampling in later
sessions.
At close BLUE/RGB stops the renderer preserves the detailed signed contour and
uses the exact continuous coverage/color integral directly. Rejected
carrier-neutral, background-bed, coverage-sharpening, and RGB-transition
experiments are absent from the production ABI and shader. They either removed
accepted paused detail or steepened fractional pixels after the exact resolve,
making the real signed carrier easier to perceive as a pulse. 3BAND retains its
independent unsigned PWV7 geometry.
Ordinary pause captures the same exact frame at the end of its gain fade and
resumes from that fractional-millisecond position; the coarse Python transport
poll is never the pause endpoint.
Redundant inactive-preview updates are absorbed without waking the Metal run
loop. Render buffers are snapshotted under a short mutex and command encoding
happens after that mutex is released.
Prepared overview and ultra buffers are built off the UI thread and become
visible in one grouped native commit; the display link skips the incomplete
transaction so resolution promotion cannot alter a waveform already on screen.
Library preparation builds V5 incrementally, pauses during
playback, pauses full audio cooking below 20 GiB free until 25 GiB is
available, and
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
