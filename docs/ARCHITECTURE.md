# SoundBox architecture

## Repo layout

Current debt and staged extraction plan: [September software audit](software-audit-2026-09.md).
Measured startup/browser/recurring-work findings and unshipped candidates:
[October efficiency audit](efficiency-audit-2026-10.md).
Its implementation follow-up batches artwork policy reads and source relationships,
constructs playlist-scoped Track rows, counts visible membership without sorting,
and preserves unchanged display inputs
across source refreshes. These caches never replace fresh source/edit authority.
Developer startup/player probes use disposable state and explicitly separate
snapshot usability, current-source readiness and physical-player acceptance.
`browsercache.json` is a separate versioned, checksummed display-row cache keyed
to all effective metadata/curation/title-policy inputs. Rows are restored only
after rebuilding the current registry and applying current visibility settings;
artwork and edit authority remain separate. Missing/malformed/stale caches fall
back to reconstruction. Row invalidation retires the checkpoint key, preventing
new rows from being saved under an old input fingerprint. Cache publication is
serialized with the existing background snapshot lane and uses bounded row
encoding. Metadata derivation changes must bump `browser_cache.SCHEMA`.
Saved theme application precedes child-widget construction, and unchanged QSS
does not repolish every widget. The global input filter attaches after startup
restoration; the native waveform host keeps its geometry filter throughout.
Artist title-rescue regexes compile on first actual rescue, and the optional
extractor is presence-checked at composition but imports only on first search.
Provider initialization/search exceptions remain retryable, never sticky misses.
Stock artwork converters/writers import only on actual conversion/rendering;
unchanged derivative-cache warming does not initialize them. The WAV compatibility
probe imports libsndfile only for WAV inspection. Ordinary desktop construction
therefore avoids those NumPy dependencies. Grid glyphs use the system font directly
rather than triggering generic-family alias discovery.
The [shared-core / personal-module boundary](release-boundaries.md) uses
`soundbox.providers` for typed artwork/artist interfaces and startup admission.
The versioned `profiles/*.json` manifests select an allowlisted first-party module;
missing/incompatible/broken providers leave the local core available. Public
contains no current SoundCloud provider or `yt_dlp`; personal includes
`soundbox_personal.discovery`. Menus, direct commands and delayed scheduling all
use the resolved actions. Providers return results; core workers and artwork/state
owners retain publication, cancellation, shutdown and device authority.
Frozen apps resolve only the bundled manifest, ignoring source-profile environment
settings. Detach/reattach uses build composition plus restart; it does not migrate
or reset curation, provider namespaces, images, sticky removals or analysis.

    soundbox/   pure-Python engine (no Qt): source-neutral library model,
                rekordbox adapters, sync/resync + manifest drift,
                playlists (m3u8), metadata
                derivation, imported-file records + relink, cover-art
                fetch/encode, backup — mypy-clean, gated by ./check.sh.
    desktop/    PySide6 app: MainWindow + mixins, workers (all QThreads),
                trees, player, art picker, suggesters, artist/default catalogs.
    native/     arm64 macOS deck dylib: CoreAudio render/decode engine +
                CAMetalDisplayLink waveform/grid renderer, exposed by a C ABI.
    tests/      engine behavior + property-based (hypothesis) + mocked live-DB
                reader + hermetic UI + native deck/shader contracts. Trust
                ./check.sh output for the live count.
    research/   R&D excluded from the shipped app and engine mypy. Pyflakes
                checks it; synthetic tests cover the ANLZ bridge contract.
                See research/cdj_export/.
    docs/       this documentation.

The October28 CDJ pilot integrates the pinned Rust renderer under `native/cdj`.
`soundbox.cdj_source` captures fresh native metadata/cues and verifies source
playlist order; `cdj_usb` copies bounded native metadata/profile files for local
parsing; `cdj_pdb` independently checks DeviceSQL pages/indexes; `cdj_export`
prepares an immutable selection/candidate; `cdj_transaction` owns authorized
publication and locally bound recovery. Both formats share one generation.
`desktop.cdj` uses the existing device coordinator/worker lane. Ordinary exporters
retain their PIONEER exclusion. Full contract and acceptance limits:
[CDJ guide](cdj-usb-export.md).

Current native catalogs are shared authority across exporters; cdj_catalog adopts
fresh metadata and maps source-local tag IDs without auditing retained media.
cdj_incremental changes selected records/memberships and preserves collection
songs. cdj_transaction checksums while copying, resumes completed payloads, and
lets a later coherent native export supersede an older interrupted catalog intent.
USB/global My Settings use44 logical fields and byte-preserving selected patches;
no automatic global propagation. usb_format revalidates the reviewed physical USB
before FAT32/exFAT MBR formatting. Track.info and SoundBox-local settings.track_info
overrides feed display/export; browsercache schema2 and libcache schema8 preserve
these fields without analysis/art rebuilds. No additional source DB write API exists.

`desktop.track_menu` supplies the evenly spaced button immediately right of Q.
Beat-count mode is persisted separately from waveform color and uses cached
memory positions plus the resident grid in both Qt and native ABI6. The same
native RAM resolver drives drawable text and diagnostics; changes request one
dirty frame. Standalone CDJ inputs join the existing collection union without
manufacturing a playlist; local recovery receipts retain their source IDs/UUIDs.

`desktop.analysis` owns the Analysis tab/dialog and cancellable TaskOwner jobs.
`soundbox.analysis` captures one hashed local audio snapshot, then the optimized
Rust helper reuses pinned rbxport DSP. Decoding checks bounds before collection
and caps the buffer at300seconds/32million mono samples. Results are durable
`settings.analysis_results`, keyed by canonical path with SHA-256, exact source
identity and engine provenance, allowing a direct import and later native ID to
share data. Playback overlays detected fallback grids in its existing worker;
native/manual grids and source cues win. No waveform-cache rewrite or source
DB/ANLZ publication is enabled. Full two-way analysis remains the explicit next
acceptance gate; see [the accepted stage](analysis-playlists-next.md).

## Engine modules (`soundbox/`)

`library` (source-neutral `Track` / `Playlist` / `Library` model) ·
`rekordbox_db` (live DB adapter) · `rekordbox_xml` (XML adapter) ·
`engine` (the API surface) · `sync` (export + resync; **safety
contract at the top of the file**) · `paths` (sanitizing, device paths,
length budgets) · `playlists` (m3u8) · `report` · `metadata` (artist/title
derivation, title-cleanup rules) · `artwork` (canonical local images, results, provenance and defaults) +
`artfetch` (local compatibility aliases) + `artencode` (baseline-JPEG encode) +
`providers` (optional discovery contracts and startup composition) · `manifest` (per-device drift) ·
`track_analysis` (canonical V5 storage plus read-only V4 compatibility,
streaming RGB/grid
analysis, and export eligibility) · `rekordbox_analysis` (read-only
PQTZ/PCOB/PCO2/PWV3/PWV5/PWV7 parser) · `rekordbox_grid` (shared pure
raw-section/metadata transforms) · `rekordbox_grid_proof` (unintegrated temporary-copy-only
multi-file/SQL recovery proof; no publish-to-source API) · `rekordbox_grid_store`
(accepted track-bound source transactions and explicit library authority) · `device_profile` (read-only device
classification and stock-iPod readiness planning) · `stock_ipod` (MA446-only
two-phase iTunesDB/artwork render/validate/commit adapter) · `stock_artwork`
(content-addressed MA446 RGB565 preparation and ArtworkDB/ithmb layout) ·
`stock_ipod_shadow` (disposable Mac-side per-device delta baseline) ·
`stock_ipod_info` (strictly read-only stock/dual device snapshots) ·
`dual_ipod` (schema-3 single-copy stock/Rockbox plan, rollback, and ownership) ·
`rockbox_media` (verified picture-free/tag-normalized payload derivatives and
theme-sized art) · `rockbox_config` (read-only Rockbox theme/database observations) ·
`backup` · `imports`
(imported-audio plane) · `libcache` (instant-paint snapshot).

## Device lifecycle and source acquisition (September 2026)

`device_operations` is the Qt-independent serialized I/O authority, with mount
identity, command, phase, cancellation and typed result records. The desktop
worker adapter acquires the lane before starting and releases it only at
QThread.finished. Discovery, dashboard, verification, export, Sync, deletion,
imports and browser mutations share that lane. Normal macOS eject owns it until
QProcess finishes. Engine DeviceSession guards still authorize every write;
the coordinator never grants firmware capability or ownership of a file.

`operation_control` scopes cooperative cancellation to a worker. Planning and
media acquisition/copy boundaries poll it; rollback does not. Queued exports
are cleared on cancellation/failure. MainWindow shutdown dispatches events until
its collected workers have stopped. `desktop.task_owner.TaskOwner` retains
artwork/suggestion dialog and interactive waveform workers through queued
`finished` delivery and final join. Window shutdown closes child-owner admission
before dispatching callbacks and drains them before native-player teardown.
Stopped owners reject new work and their consumers reject late results. This is
a lifetime primitive, not a replacement device queue. The coordinator owns the
active device lane, but desktop adapters still
retain their own queues rather than using its queue API (audit A5).

`source_acquisition` captures canonical audio into disposable local storage in
one streaming hash pass. Exact expected bytes take precedence over cloud stat
drift. Unknown bytes require a stable capture, with one bounded hydration
retry. Rockbox media inspection, normalization and essence checks use captured
bytes. Stock planning consumes those technical facts or captures its own source;
stock execution also captures plain payloads before copying. Generic export
prepares/copies/discards per file and suppresses destructive cleanup on failure.
Temporary device payloads are explicitly fsynced and verified before atomic replacement and the mount is
revalidated at that publication boundary.

`source_read_retry` handles macOS CloudStorage ENEEDAUTH reads with at most two
cancellable three-second waits per verification/acquisition. Each retry opens
the source anew and starts its hash/snapshot from zero; the independent one-retry
limit for unstable complete captures remains. Persistent errors, EAUTH, ordinary
permissions and non-cloud sources fail without this readiness retry. Composite
preflight errors include the source path; the UI reports the engine outcome
(no device writes, completed rollback, recovery required, or committed cleanup
failure) instead of assuming every error attempted rollback.

`payload-receipts-v1` stores compact, checksummed derivative contracts bound to
canonical filesystem/inode/size/mtime/ctime observations. It survives derivative
cleanup and avoids repeating completed planning work. Lost/corrupt receipts
cause verification, never loss of ownership data. `transaction-outcomes-v1`
mirrors recovery outcomes locally; these records are evidence, never automatic
permission to resume writes. Device manifests retain their existing schemas.

See the lifecycle audit and HANDOFF for outstanding physical acceptance.

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
`artistcatalog`, `connections` (owned account jobs), `keyboard` (action catalog,
versioned map and physical-key router), `keyboard_page` (capture/conflict editor),
`model` (`TrackModel`), `player`, `theme`, `stock_device_view` (iPod dashboard),
`usb_device_view` (seven USB pages and immutable native snapshots),
`usb_preferences` (USB/global controls), `track_info` (Summary/Info).
MainWindow composes these mixins with TrackInfoMixin and QMainWindow.
`native_deck.py` adapts the native C ABI to PlayerBar's small transport
protocol.

USB presentation reuses the iPod detail-tree factory and playlist styling.
Rockbox's bounded reader opens only `/Playlists` through no-follow descriptors,
never referenced media or PIONEER. Generic USBs use the dedicated bounded native
reader when either native library exists; no live SQL is opened on the USB.
Native preference controls queue an explicit Save generation through the same
worker lane. The UI does no removable-device I/O in widget callbacks.
A USB playlist drop carries target-bound authorization through preparation into
commit without another dialog. cdj_incremental freezes only selected changed/new
inputs and merges copied native catalogs through native/cdj/src/merge.rs. Prior
payload contracts remain inventory, not a fresh audit. Input signatures/per-playlist
hints are separate from ownership. Newly captured audio is placed directly in the
local partial output; the helper accepts only that exact Contents file set before
building catalogs/analysis, and parent SHA-256 inventory proves unchanged audio.
The packaged helper comes from build/cdj/release, never the debug profile. Source/candidate preparation and publication remain
in the serialized device lane through QThread.finished. Connection/remount or an
explicit request refreshes cached snapshots; ordinary polling uses a small
manifest/RAM-input drift hint. Backend UUID/mount authority is absent from the
Identity rows. No USB tab roots QFileSystemModel at the mount. See the CDJ guide
for staging space, current-native catalog adoption, profile preservation and recovery limits.
The shared device header is 26 points; BrowserDetailTree continues the regular
24-point alternating grid below real rows. USB song rows use COLUMNS and shared
browser header state. Immutable CdjSong records retain actual USB audio/analysis
references; explicit activation validates them in the device lane and uses a
separate device track ID, without source cue overlays/edit authority. USB view
mementos are keyed by volume UUID, with tab, hierarchy/selection and scroll saved
under settings.device_views; stock dashboards retain their per-device page state.
The keyboard capture dialog uses the same transactional map save for Unbind as
for keycap removal; failed saves restore the prior editor/runtime map.

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

The browser extends to the window's right edge; the former 30-point utility rail
is removed. Its circle-i is a 44×28 bordered prep button
immediately before My Tags, using the same BG inner fill as Settings and the other
controls, with a matching three-point corner radius. Info and Settings use
centered vector icons, avoiding font bearings. The player-layout popup is sized
to the selector field after Qt lays it out. Info opens Summary/Info for the selected row, with artwork inside Info. Supported
metadata edits are SoundBox-local and feed USB export. TrackInfoDialog constructs
its Summary without artwork locks/decoding and lazily builds Info on selection.
After the dialog opens, ArtworkWorker resolves the canonical art path and decodes
a bounded QImage outside the GUI; TaskOwner retains it through finished/join and
drains before dialog close or application shutdown. No extra work is added to
track loading. Comments spans the Info row; Lyricist is omitted from that form.
Global My Settings and Keyboard put their scrollbars at the window's right edge.
Flexible prep
gaps redistribute around it. FooterBar draws a single full-width top separator at a fixed28-point height.
Its progress/count/Cancel controls fit inside without changing browser geometry.

TrackTable and Artists own persistent vertical-scrollbar gutters. An inert
26-point header-height widget reserves the top, keeping the real scrollbar and
hit area below the headers. Headers stay Interactive with a shared bounded width
policy: manual drags change only the dragged section, clamped to the remaining
free width without borrowing from Title. Window resizing gives spare width to
Title; narrowing or showing extra columns can reduce other widths only as needed. Saved/default layouts fit under
a guarded section-resize loop. The visible viewport's Resize/Show events supply the
available width, excluding the gutter. Ordinary layouts fit without horizontal
scrolling; if selected fields exceed even their minimum widths, all three browser
views allow horizontal scrolling so every field remains reachable. Saved layouts
record columns_keys: restoring an older ten-column Qt header must explicitly keep
new metadata fields hidden. Artists mirrors shared widths and visibility.
ScrollPerItem keeps 24-point rows aligned with the sidebar. TrackModel declines
unsupported paint roles early without adding a row cache or source reads.

Prep edit feedback uses the existing lower-left status label and status helpers.
`TagEditController.update_status_text` distinguishes accepted tag/cue/grid intents
from mere worker ownership; `GridEditController` supplies beatgrid/BPM intent
labels without changing shared-lane admission or recovery. Focus-return grid
checks remain silent. Verified save callbacks alone finish the edit status with
`Done`, then the existing generation-guarded timeout returns to `Ready` without
clearing a newer message. Failed edits retain an operation-specific error.

Qt remains the application shell. Playback is native-only; Qt Multimedia is
used only by `QAudioDecoder` while analyzing AAC/ALAC. If the native deck cannot
start, SoundBox keeps the library available, disables player controls, and
shows a precise diagnostic instead of silently switching engines. The native
Audio Unit callback is the
authoritative sample clock. A background AVAudioFile decoder fills a lock-free
ring buffer; the callback never decodes, allocates, locks, or calls Python.
Metal receives waveform/grid buffers once per track and translates them from
the CoreAudio host/sample anchor on each CAMetalDisplayLink update. Python
polling updates coarse labels and buttons only. The prep-strip elapsed/total
clock uses those existing updates and emits only when its seconds/duration signature
changes; waveform drag previews own that label during a grab. It adds no timer or
source/library reads. There is no Python repaint timer
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

Analysis preparation reads ANLZ only as a seed/fallback source. The separately
accepted grid API writes only DAT/PQTZ and native EXT/PQT2 invalidation, with
exact library/schema/version and per-track UUID authority; see
[the prep plan](rekordbox-prep.md).
Active `djmdCue` rows come from the same read-only live database snapshot as
the library. Their normalized fingerprint and the exact normalized PQTZ beat
rows update only the V5 manifest until SoundBox user data claims ownership;
deleted database cues and later Rekordbox beat-grid corrections therefore land
without touching the compressed analysis payload or decoding source audio.
SoundBox's schema-V5 container owns the imported streams, grid/cues, source
identity and optional derived detail under `track_analysis/`. Sections are
independently Zstandard-compressed and SHA-256 verified, then atomically published.
The import-first path reads complete PWV3/PWV5/PWV7 plus grid data before selecting an analyzer.
It produces only exact 1-ms signed stereo-midpoint extrema, with no duplicate
filter bank, FFT or beat analysis, and builds its overview from PWV5.

Background eligibility discovery reads packed PWV3/PWV5/PWV7 rows and PQTZ
directly; it never expands whole-library per-sample color lists or generic ANLZ
objects just to schedule upgrades. Full cue/display parsing remains per-track.

Compact records use `soundbox-rekordbox-supplement-i16-v1` and
`rekordbox-pwv5-rgb-v1`, store divisor 1 only, and omit the 24-band master. Loading
builds `1/2/3/4/6/8/12/16/24/32/48/64` mips in memory. Incomplete imports and codecs
not supported by the supplemental decoder retain full fallback analysis. Old
V5 remains ready while the recurring background worker queues eligible local
records for compact conversion, one track at a time. Interactive playback still
loads old analysis immediately and can finish a selected upgrade ahead of the queue.
The old record survives interruption or failure; manual metadata ownership and
DB cue authority survive replacement. Source changes, forced Reanalyze Track and
late imports use the same preparation path. Dataless background reads remain
deferred and per-track preparation receipts still avoid repeated work. The receipt
policy includes the compact model so old ready receipts cannot hide pending
upgrades. Compact tasks never fall back to a full analyzer after decode failure
and recheck whether an interactive load already completed them. They use the
5/7-GiB migration reserve and run ahead of non-foreground full cooks, which retain
the 20/25-GiB reserve.

AAC/ALAC compact supplements use QAudioDecoder at the native source rate when
libsndfile cannot open the file. They retain imported streams/grid and do not run
the full fallback analyzer.

Loading remains generation-guarded: native decode, manifest, overview and detail.
All cache decoding and native waveform preparation stay off the UI thread.
The native host never expands an unused Qt envelope during detail delivery.
PWV5 carrier repair is vectorized without changing its output, and native
display-stream publication checks the generation under its render lock. A complete
native upload presents overview and all three display modes together. Source
replacement rejects late generations. Native ABI 6 retains the tested
timeline load contract. Tagged MPEG-1/LAME header facts are read on the decode
thread, matched to the decoder's rate/valid-frame count, and restore leading
samples to both playback and supplemental analysis. Seek and duration use that
same clock. Unknown headers retain their decoded origin; mismatched recognized
headers fail closed. No blanket MP3 timestamp is used.

The renderer uses linear amplitude, a 0.84 ceiling and its existing continuous
quiet-signal shoulder. Close BLUE/RGB hold signed cells and interpolate full-range
PWV5 colors (14/7/7 channel divisors); 3BAND uses original 150-Hz rows, a one-row
sampling offset, and linear layers with the measured membership palette. Paired
75-Hz rows, the artificial eight-percent facet, four-pixel close extrema footprint
and divisor-dependent height compensation were removed after the September 29
reference comparison. The 13 UI/native zoom stops end at 1500 points/s.

One footprint-selected song-anchored mip feeds the fixed four-attributes-per-pixel
lattice. The three widest stops use a half-pixel source footprint; 64–1500 use
one physical pixel except the logical-pixel footprint at RGB 576/864. The October 2 close-zoom follow-up
selects 2-ms compact detail at Retina 384; the native-reference follow-up also
selects a logical-pixel footprint (2 ms) at RGB 576/864. Other close cases retain
1-ms detail. Thin signed carriers use a
one-physical-pixel minimum before coverage, replacing the former fixed
3.6-point stroke. Color is
premultiplied before the final one-pixel integral, with no adjacent-LOD blend or
translated RGBA texture. Legacy/fallback spectral masters never recolor the
imported display modes. RGB color recovery treats zero compact height as missing
weighted evidence, using the existing authored color rather than black; positive
weights cancel exactly. The October 26 follow-up also makes the whole-track
overview divide every positive color weight exactly. A compact zero can be
height quantization rather than silence: BLUE/RGB overview coverage falls back
to the resident high-precision audio envelope while retaining authored hue.
True zero audio remains background, and PWV7 geometry remains independent.
These compositor corrections need no cache conversion.
Metal receives integer sample anchors plus
small local phases for both geometry and color, retaining long-track subpixel
precision. Attribute generation uses absolute 128-attribute clock blocks, with
CPU-double-derived anchors and phases, so overlapping tile rebuilds cannot
round a held cell onto a different source sample. The scrolling lane translates fixed height/hue attributes into each
final Retina drawable pixel. The fragment computes vertical silhouette
coverage and composites the final color exactly once. The accepted
quarter-pixel profile remains in use at 64–184 px/s for RGB/BLUE and below
1152 px/s for 3BAND. RGB/BLUE at 24–48 and 256–1500, and 3BAND at
1152–1500, use the pixel endpoints plus every integer knot of the four-attributes-per-pixel
lattice to form the complete piecewise-linear profile; integrating those true
segment spans avoids a hidden corner moving through a fixed probe interval.
There is no translated RGBA texture or second coverage/color filter; every
zoom retains the oversampled attribute lattice. RGB 576/864 use one logical-point display step, matching the native reference;
other RGB stops and 3BAND retain fractional travel. BLUE rounds only its display offset to physical pixels
through 384 points/s and logical points at 576–1500, bounding its waveform-only
error by half that increment. Audio, grid and seek clocks remain
continuous in every mode. The antialias window is exactly one physical pixel.
The RGB overview uses the same bounded faint-only shoulder as the main RGB lane;
BLUE/3BAND overviews retain continuous cubic vividness. The scrolling lane uses
mode-specific joint geometric coverage.
For BLUE/RGB signed intervals, the fragment integrates clipped linear coverage
moments and visible color over the same pointwise intersection. It does not
reapply the smaller of separately integrated upper/lower boundaries: that older
approximation overfilled narrow diagonal traces and was removed October 2.
PWV5 RGB accumulates premultiplied display-encoded channels; BLUE's center/width-
dependent radial field retains premultiplied linear light. BLUE splits at coverage and radial branch changes
and uses four-point Gauss-Legendre quadrature within each branch; evaluating
the nonlinear field only at the coverage centroid produced visible color error.
BLUE retains linear-light joint-area compositing. RGB384 keeps the geometric
coverage of Sonny's October27 C, with the selected October28 encoded-color blend. Other RGB stops apply only a smooth monotonic faint-detail shoulder
below 18% coverage: c′=c×(1+1.5×(1−c/0.18)²), with premultiplied color scaled by
c′/c. It adds at most 0.04 coverage, leaves zero empty and joins exact area at 18%.
At RGB 576/864, attribute preparation also measures occupancy: the overlapping
original physical-footprint intervals' minimum-stroke widths divided by the
coarse interval's width, weighted by source span and bounded to 0–1. Encoded
contrast against the background is weighted before existing height
premultiplication, preserving fine painted area rather than treating all peak
bounds as occupied. No additional buffer, cache schema or per-frame source
read is needed. If the physical/logical selections agree, color is unchanged.
RGB 576/864 use logical-point motion; other RGB stops remain fractional. The broad occupied-column boost is
removed; normal coverage and hue are not brightened. Persisted
extrema, mip data and the final physical-pixel integration window remain unchanged.
The canonical 64–256 px/s midrange family applies one same-LOD
half-physical-pixel `[1, 2, 1]` reconstruction pass before that final
integration. The 24–48 distant stops and 384–1500 close family bypass it; it
changes neither mip selection nor close source detail.
Scrolling grid rectangles carry signed local line distance into the fragment,
which applies analytic physical-pixel box coverage instead of rasterizing a
hard two-pixel-wide Retina rectangle. The drawable is BGRA8Unorm_sRGB:
main-lane RGB blends its encoded color/coverage with the encoded background,
then decodes that final opaque pixel. The RGB overview now does the same, with
only the bounded faint-coverage shoulder below 18%; its old full-range cubic boost
is removed for RGB. BLUE, 3BAND, their overviews and fixed-function grid blending
retain linear light. Metal encodes exactly once on store. Full colors and the `#0d0e12` clear remain
unchanged. The October28 RGB edge update retains both visibility lifts and the
existing source/height/LOD/clock contracts; it does not promise equal linear-light
energy for different RGB edge-coverage distributions.
The grid is the final timing-reference layer. Red downbeats and gray ordinary
beats have density-controlled interior opacity and opaque edge ticks, with
continuous analytic line coverage.
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
uses joint coverage/color integration. RGB 576/864 now use logical-point motion
and the occupancy-weighted logical footprint above; other RGB stops remain
continuous. RGB 384 keeps exact area; other RGB stops use only the faint shoulder described above. The installed September 30 correction additionally
retains the original color-premultiplication weight through attribute interpolation;
reconstructing that weight from interpolated signed bounds spuriously brightens
polarity/dominant-boundary crossings. Geometry and coverage are unchanged.
The October 1 complete-corner correction at RGB/BLUE 256/384/576/864
resolved the remaining reported pulses in Sonny's live B comparison. The separate
BLUE palette/white-core correction was also accepted and installed. Broader track
and exact-reference parity were not established by those checks. Sonny closed
further visual comparison work initially on October28, then reopened the
Gunslinger case and accepted the targeted follow-up above. See the current
handoff; that acceptance does not claim exact native equivalence.
Rejected
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
`preparation_inventory` persists disposable scheduling receipts for analysis and
stock art. Analysis polls stat identities every 60 seconds; unchanged entries
skip record/ANLZ reads. Changed entries share one header; a 30-minute fallback
retries sidecar parse failures. These receipts never grant device ownership or
replace interactive checks. Title edits invalidate one computed row; registry
and policy changes invalidate all rows. Background warms compute only misses.

The shared status row reserves the operation label's animated width and the
counter's complete total-width, so repeated updates do not move the controls.
Per-file acquisition/copy/verification phases and spaced byte totals live in
the status tooltip; the operation callback alone owns visible progress. Static
messages resize to fit and stop any old pulse. Transient expiry is bound to the
presentation generation. Analysis/artwork use separate counters, yield to device
ownership (including Verify), and restore a still-active pause after returning
to idle. These are presentation rules; transaction progress and completion
boundaries are unchanged.

Device export is pinned by `sync.DeviceSession` to one resolved mount identity
and revalidated before each write. Stock/composite plans retain that identity
across the confirmation modal, so a same-name replacement mount cannot inherit
a plan made for the prior device. Filesystem-browser rename, move, folder, and
delete operations use the same pinned boundary; confirmed deletion carries the
already-pinned session into its worker. Manifest v2 records normalized destination,
source identity, size, SHA-256, playlist membership, artwork/playlist kind,
ownership, and completed generation. Tracks, covers, playlists, and the
manifest use destination-filesystem temporaries plus atomic replacement.
Composite normal Sync trusts the committed inventory; Verify checks references
and sizes, and Deep Verify hashes all managed files using the schema-3 reader.
Explicit composite Repair audits inventory and restores missing owned Rockbox
audio/art while refusing unexpected occupants. The generic exporter retains its
existing manifest-owned repair policy. Pruning follows explicit ownership rows
and never walks `/Music`
as authority. Unknown content remains untouched. Stock iPod writes are a
separate additive transaction: a pinned Clickwheel version renders only in a
temporary tree; SoundBox validates the output, writes recovery bytes,
atomically copies new media/artwork, and commits `iTunesDB` last. Curated
baseline JPEGs remain the shared Rockbox/stock source of truth. A low-priority,
playback-aware worker prepares content-addressed MA446 RGB565 100×100 and
200×200 payloads under AppData ahead of sync. A locator index skips unchanged
JPEGs, and manual art changes queue only affected tracks. Successful commits
atomically publish a Mac-side `stock_ipod_state/` shadow keyed by library
persistent ID plus volume UUID. Exact database/artwork fingerprints activate
the fast path; mismatch performs full reconciliation. Because a valid shadow can
be playlist-scoped, selected sources absent from its acceleration index are
narrowly reconciled against size/suffix candidates in live device media using
exact content hashes before any copy is planned. Incremental art preserves image
IDs, compares current RGB565 slices when a local cache key is absent, writes only
currently unreferenced fixed-size slots, and consumes a pixel-identical free slot
without writing it before publishing ArtworkDB and iTunesDB.
Planning still audits every MHIT→MHII→MHNI offset/size; source media and tags
are read-only. The writer
is restricted to stock-only MA446, scheme-zero iTunesDB, and MP3/WAV.
`device_profile` derives the export pipeline from the mounted layout. Stock-only
MA446 routes to the guarded database planner; Rockbox-only/generic FAT32 routes
to the filesystem planner; and a FAT32 MA446 containing both `.rockbox` and
`iPod_Control` routes exclusively to `dual_ipod`. The shipped composite planner
places stock-safe media once under `iPod_Control/Music/Fxx`, indexes it in
iTunesDB, and references the same file from Rockbox playlists. Stock-unsafe but
Rockbox-supported media lives once under `/Music`. Schema-3 ownership spans
both path classes, so playlist reconciliation, reference-counted cleanup,
prepared artwork, recovery, commit ordering, and rollback are one transaction,
never two exporters run back-to-back. The MA446-safe subset is validated MP3
plus stereo PCM16/44.1-kHz WAV; that conservative stock rule never filters a
file supported by Rockbox. Media unsupported by both profiles is omitted and
reported. Schema-3 playlist reconciliation also performs a bounded reverse
identity lookup for append-only Rockbox M3U8 edits: device path → owned media
row → hash-authenticated historical playlist position → stable `Track.track_id`
(with canonical-source lookup available when no historical proof exists).
That proof also rebinds moved sources in a disposable planning view, keeping
original manifest bytes for recovery and verifying source hashes before reuse.
Proven new membership enters only SoundBox custom playlists, using an exact
before/after membership transition saved before the device transaction.
Same-track/source and byte-identical-file duplicates retain their first
occurrence; distinct recordings remain separate. Existing source-playlist
overlays remain readable for compatibility, but iPod sync creates no new ones.
Unknown paths never create tracks or choose an import destination.

Source verification uses a disposable `source-verification-v1/` journal under
Rockbox staging. Each tiny record is written after a complete stable SHA-256
read, keyed by canonical source path and bound to filesystem/inode identity,
size, mtime, and ctime. Planning retries reuse exact proofs without rehydrating
the audio; no audio copies are retained. Cache loss or malformed records mean
full verification when a fresh proof is needed. The journal never grants device
ownership. Normal composite Sync uses the committed inventory's unchanged
path/size/mtime/display/policy contract, retaining original timestamps across
ctime-only churn for local and cloud sources. It does not scan retained device
media/art or publish a fresh source proof. New/relocated/changed sources still
require exact-byte evidence; their preflight timestamp refresh requires matching
the planned source hash. Refreshed source baselines flow into the stock copy/
rewrite contracts and
final unified manifest without altering the caller's plan or recovery bytes.
Different bytes or repeatedly changing sources fail before writes.

Stock compatibility probes are unique-source scoped. A matching verified
composite payload already in the stock subset reuses that classification,
retaining database/path identity checks. Live-size checks apply to affected
paths and explicit Repair; unchanged manifest inventory needs no per-file stat.
Initial confirmed dual drops and
contiguous generic-device queue entries batch all available playlists, so
shared media is planned once per batch. Stage timing and source-proof counters
are logged; see `docs/sync-performance-audit-2026-09.md` for measured findings
and remaining performance limits.

Stock device presentation is a separate read-only path. `stock_ipod_info`
creates immutable identity/track/playlist snapshots, merges pending playback
records only in memory, and exposes no mutation API. The desktop device page
renders them as Device, Songs, and Playlists tabs. On a schema-3 dual device,
Songs and Playlists merge manifest ownership with the parsed iTunesDB and label
each row Both, Stock, or Rockbox. Rockbox-only and USB targets use the shared-style
Overview/Playlists surface and cached native or M3U/M3U8 snapshots instead of the
former raw filesystem browser. Connection presentation separates visibility from readiness. Debounced `/Volumes`
notifications and a 250-ms cached kernel mount-table check request discovery even
during playback or an eject-ready hold. The latter uses Darwin
`getfsstat64(MNT_NOWAIT)` without opening mounted paths, so mounting over an
existing directory cannot strand detection behind probe backoff. These are hints,
never external-device/write authority. A busy lane retains the request and retries
on worker retirement. The three-second local `/Volumes` timestamp fallback remains;
unchanged mount/timestamp state starts no idle discovery worker. Failed probes
retain 3–30-second backoff, bypassed by mount changes. Connected drift stays on its
three-second poll. Ordinary unmounted folders retain the 30-second attachment grace.
Verified external mounts appear disabled as `NAME · Checking…`. The first scan
computes sync drift before publishing the selectable row at QThread.finished;
unknown/failed checks say `Sync status pending`, never imply a clean result.
Stock/dual dashboard preload follows in the same serialized device lane, with
loading only in the bottom status after the row becomes ready. Successful preload
clears that status directly, without another immediate drift scan. A cached
dashboard is presentation-only; selecting while it loads uses the same serialized
preload. Writes stay guarded by operation ownership and firmware capabilities.
Remounts invalidate stale views.


Device transactions share one UI ownership contract. Custom result signals may
record a worker result, but only `QThread.finished` clears the owning attribute
and may advance the UI. Composite planning maps to 0–30%, device
mutation/verification to 30–80%, and post-operation dashboard, sidebar/drift,
and ownership release to
80–100%. `100%` and a completion dialog describe terminal operation state; device row
visibility separately describes connection state. A failed required final read
shows a final-refresh failure instead of running the success callback.
Preloads are scoped to path plus the mount root's filesystem device token, so a
same-name remount invalidates and reprobes path-keyed disk/profile facts. Model
confirmation persists against the iPod library persistent ID plus macOS volume
UUID; Apple/SoundBox rewrites may change the MHBD `DatabaseID`, so it is display
information only and never a device key. On macOS, `desktop.devices` performs
bounded IORegistry and classic-iPod preference probes: Apple's device record is
the firmware/hardware-serial authority, serial suffixes may select a guarded
model-catalog entry, and the verified model may identify the DAC. USB/SCSI
revision remains separately labeled transport data. Every derived value carries
provenance in the snapshot and ambiguous matches produce no value. The device page
renders the DAC as a concise component name and filters its Playlists tab to
regular user-created playlists plus the exact Apple Videos smart index. Videos
is labeled `System playlist` and may enter the guarded delete planner without
owning or deleting media. When Apple mirrors Videos in both ordinary MHLP and
canonical MHSD-5 datasets under different IDs, the snapshot exposes the
canonical identity and the planner removes both representations together.
Music, Movies, TV Shows, and Audiobooks remain immutable; On-The-Go is
firmware-generated and absent from iTunesDB.

### Loaded-track cue refresh

After a successful source load/refresh, MainWindow passes the matching loaded
track/source's authoritative DB cues directly to PlayerBar. Empty lists clear
removed cues; absent authority and mismatched identities leave the deck alone.
This updates the live cue override, panel, and both waveform markers without
seeking or reloading audio. Later queued cache manifests use that latest override.
Grid metadata continues through the existing fingerprinted, atomic background
refresh, preserving compressed waveform payloads.

## Data model

- **Copy-only prep proof stays isolated.** The
  `rekordbox_write.OfflineWorkspace` accepts an explicit source for stable
  read/copy, then permits SQL only beneath a marked private temporary workspace.
  Its typed tag plans bind a workspace identity and complete logical baseline;
  encrypted staging, exact-delta checks, SQLite transactions, durable journal and
  restart reconciliation are exercised without opening the real source with
  SQLCipher. It cannot publish back. This offline full-database verification is
  not the production save path. Its flag conventions are provisional, superseded
  for live tags by native reference evidence. Companion-file transactions for
  grids are now handled by the separate accepted source protocol in
  [rekordbox prep editing](rekordbox-prep.md).
- **My Tags use a separate live transaction API.** `rekordbox_tags.LiveTagStore`
  requires an explicit per-library/schema/version authority and closed native
  app/agent. It checks expected source UUIDs, tag names and memberships, maintains
  an encrypted recovery baseline, journals exact row deltas, limits writes with
  a SQL authorizer, commits atomically and independently verifies affected rows.
  Native acceptance covers rekordbox 6.8.7.0303 with no Cloud/Mobile Library Sync.
  `desktop.tag_controller` owns saves/readiness probes through TaskOwner retirement
  and join; refresh, source selection, backup/restore and shutdown serialize with
  that owner. A successful save reloads the source snapshot and smart memberships.
  `LibraryTag`, source UUIDs/memberships and library identity travel through
  instant-paint cache schema 7 (older 4–6 remain readable). A cached source cannot grant write authority.
- **Cue planning is pure; the accepted writer binds one track.** `rekordbox_cues` produces exact
  four-table deltas from raw snapshots, separate from normalized display cues.
  Full revisions and explicit cue IDs/UUIDs guard stale chronological deletion;
  aggregate insertion order, legacy fields and overflow remain intact. Native
  WAV/MP3/AIFF and historical-aggregate references inform its rules. It has no
  SQL/file API. `rekordbox_cue_store.CueLibraryAuthority` is provisioned only after
  native add/delete acceptance and binds `PilotCueStore` to an explicit track.
  It shares
  the tag store's exact root/lock/journal, with operation-specific row/column
  restrictions. One desktop TaskOwner owns tag/cue operations; pending operations
  reconcile before another domain/track is admitted. Native DB-only add/delete
  acceptance passed on No Trust, with audio/ANLZ/XML untouched. Cue saves publish
  a new target Track and Library view without audio reload, browser rebuild or
  full source read. Raw revisions/native IDs stay outside display cue hashes.
  `desktop.cue_session` overlays queued edits in RAM without mutating that
  verified Library. The deck sees pending cues immediately; the shared controller
  executes their FIFO in background, rebasing revisions and resolving temporary
  identities to native IDs after each read-back. Failure drops the unconfirmed
  tail and refreshes source state. Normal window shutdown drains the queue before
  generic TaskOwner.stop; abrupt exit can lose not-yet-started RAM intents.
  Recovery receipts bind the physical source-file identity and may recognize an
  exact inactive zero-header SQLite journal without changing/deleting that file.
  Replaced DB files fail before SQLite recovery opens them.
- **Reads the LIVE rekordbox DB** (default `~/Library/Pioneer/rekordbox/master.db`)
  via a read-only temp copy through `pyrekordbox`; XML export is a fallback.
  Connections can validate/select another DB; `settings.rekordbox_db_path`
  persists only after read and save succeed. The explicit path travels through
  RefreshWorker, snapshot acquisition and relative analysis-path resolution;
  the module's default path is never mutated.
  Refresh first reconciles any prior authorized transaction using its provisioned
  authority, even before a cached library has been verified. Source selection
  never creates permission. Unknown recovery stops the read; the snapshot reader
  rejects nonzero rollback journals so spilled uncommitted pages cannot masquerade
  as a valid DB/WAL snapshot. Zero-header inactive files are left untouched.
- **The temp snapshot is a verified DB/WAL pair.** SoundBox copies only
  `master.db` and its WAL, compares both source identities before and after,
  and retries a concurrent checkpoint/update. SQLite's process-local SHM is
  never copied. A database that remains busy fails visibly instead of yielding
  a mixed-time library.
- **Smart playlists mirror active rekordbox state.** Rekordbox retains removed
  My Tag associations as soft-deleted history, so the adapter requires the tag
  definition, association, and content to all be active while preserving
  compound ALL/ANY rules. Historical tags and deleted songs can never
  resurrect a playlist membership.
- **Playlist reads fail closed on structural ambiguity.** Active node types,
  parents, sibling `Seq`, membership `TrackNo`, stable IDs, and display paths
  are validated before publishing a library. Normal playlists use exact
  `TrackNo`; smart playlists use their stored criteria; unreachable or
  unverified structures make refresh fail visibly rather than return a
  plausible partial tree.
- **Refresh results are monotonic.** A refresh click during an active database
  read queues one immediate follow-up snapshot, and instant-paint cache writes
  are serialized so an older result can never overwrite a newer result on disk.
  Results and errors publish only after QThread.finished retires the captured
  worker. Failed source reads/selections preserve the active library and curation.
- **Two planes.** rekordbox is king (the mirror); SoundBox keeps its own plane
  on top: imported audio (referenced in place, never copied), per-track
  title/artist overrides, custom playlists, and deletes/removals that act as
  *vetoes* over the mirror. Both planes drift synced devices. Only explicit My Tag
  assignment actions cross into the accepted rekordbox writer.
- **Imported duration is source-header metadata.** `soundbox.imports` uses
  libsndfile first and Mutagen's container metadata fallback for ALAC/AAC;
  legacy zero-time records are backfilled and persisted on load. Canonical
  duration remains integer seconds; tables render hour-plus audio as H:MM:SS.
  Imported tracks remain categorically exempt from rekordbox cue/placeholder
  hiding, even if a damaged file still has unknown duration.
- **Imported locations use persistent identity, not startup crawling.** The
  saved path is the constant-time healthy fast path. Each direct import also
  carries opaque Core Foundation bookmark data that can resolve the same file
  after a file or parent-folder move. A background startup repair touches only
  records with a missing path or legacy missing bookmark. Legacy/unresolvable
  records receive one batched Spotlight query constrained by exact filename
  and recorded size; only a unique live result is accepted and immediately
  receives a fresh bookmark. Ambiguous/unindexed cases remain missing until
  the user chooses an explicit folder for the bounded `Fix Missing Links…`
  scan. Stable SoundBox track IDs keep playlists, edits, art, and analysis
  attached through every successful relocation.
- **Storage-provider timestamp drift is content-verified.** V5 source identity
  normally remains the cheap exact size/mtime gate. If a provider move changes
  only that identity, SoundBox verifies the cache's recorded boundary
  fingerprint and persists a cache-bound xattr proof instead of decoding or
  rewriting waveform data. Dataless File Provider placeholders retain an
  existing same-size cache without hydration; verification waits until a user
  action makes the source local. Real size/content changes still recook only
  the affected track.
- **Playlist identity = rekordbox `pid`** (`DjmdPlaylist.ID`), stable across
  rename/move → it is the device-sync identity. Rename/move = same pid (syncs);
  a genuinely new playlist = new pid = fresh export.
- **Device filenames = SoundBox effective titles**, always (sanitized +
  length-budgeted; source files never renamed).
- **Collection order = newest-ADDED first**, one timeline across both planes:
  `Track.added_ts` = rekordbox `created_at` for mirror tracks, SoundBox import
  time for imported audio (falls back to file birthtime when unknown). See the
  GOTCHAS caching note: `added_ts` must survive `libcache`; known dates do not
  resolve media paths. Only unknown-date birthtime fallback uses `_landed_cache`.
- **Drag-to-add works on the sidebar AND the main browser.** Dropping tracks or
  Finder audio onto the open view adds to the playlist it shows; Collection
  imports files only; Artists (tag-derived) and read-only rekordbox views reject
  every drop (`TrackTable.resolve_drop` gates it; drops never write rekordbox).

## Settings, keyboard and optional account state

`desktop.theme` resolves the saved `theme_accent` into base/light/dark/subtle
roles. `theme_text_mode` (White/Black/Custom) and `theme_text_custom` independently
control text on accent-colored selections/buttons; White is the default and
custom colors survive mode/accent changes. General Settings applies changes across
QSS, vector wordmark and custom-painted chrome immediately, without reloading a
track. State load/restore reapplies both preferences. `desktop.wordmark.Wordmark`
resolves the supplied SVG color layers in memory and occupies the original
270×55 slot. The SVG is packaged data and part of the source fingerprint.

Settings orders its tabs General, My Settings, Keyboard, Graveyard, Connections; Graveyard
uses the full width. A QTabBar is centered across the 40-pixel header, with Back on the left
and no redundant Settings label. Equal outer grid columns keep its center
independent of the Back button's width. It selects a
QStackedWidget below it, leaving the remaining height for page content.
General uses equal expanding columns capped at 600 pixels: Player/Library/Backup
on the left, Title Cleanup/Cover Art on the right. The cleanup list sizes to its
rows up to eight visible entries, then scrolls.
Connections uses equal columns with core rekordbox source controls on the left
and the optional personal SoundCloud card on the right. Cached source presentation
is explicitly unverified until this session's actual read succeeds; the status
card never probes the database merely to display it. Public keeps Connections
and rekordbox while omitting the SoundCloud card/provider.
The five title-cleanup defaults include the exact `(Original Mix)`
tag; old custom copies remain in saved settings but belong to the default toggle.

Keyboard routing and the editor share one action catalog. The portable
`settings.keyboard` overlay supports multiple modifier combinations per action,
explicit unbound lists and conflict moves, preserving unknown fields/actions.
Only active-page cue slots and page selectors are offered; retired direct
Hot/Memory action IDs survive saved-state round trips but cannot execute.
Waveform zoom defaults to `=` / `-`; existing explicit overrides are preserved.
Physical key ownership retains the actual release callback across focus,
modifier and cue-page changes. Held-control aliases share their release boundary;
application deactivation, shutdown and successful remapping release them safely.
Text/dialog/navigation handling stays with Qt. See [KEYBOARD](KEYBOARD.md).

The personal connection factory is admitted by `connections.soundcloud` in the
versioned provider manifest. It receives only an AppData root; the core section
owns its worker admission, progress and TaskOwner retirement. New connections
use the client-credentials grant with HTTP Basic authentication, resolve a public
profile, and read its public likes without a browser or callback. Existing website
redirects and authorizations are unchanged; public disconnect clears local state
without revocation. Legacy user-authorized tokens retain backend refresh/revocation
compatibility. Client credentials and rotating tokens live in Keychain; disposable
profile/liked-track metadata lives
under `connections/soundcloud/`, outside library backup/restore. Complete
same-account scans publish atomically and canceled/failed scans preserve the old
index. Unknown-profile likes never carry across a reconnect. Discovery reads an
in-memory index before general search; the manual picker merges both result sets
with eligible artist-name-matched uploads first, then likes within the same
tier, then score. No provider gains source/device authority.
See [SoundCloud setup](soundcloud-connection.md). Public omits this factory and UI.

`desktop.artreview` automatically checks every cover listed in All, regardless
of provenance or old confidence, and prepares comparisons in the background. Suggested contains only ready different alternatives, All permits
individual browsing, and foreground selection queries can be cancelled/coalesced
without assigning stale results to the next row. Main actions are Keep, Replace,
Next. Keep persists an exact entry/image-SHA plus artist/title stamp in
`art/review-kept.json`; Next dismisses only this session. Replace itself is
individual approval, with no second modal. The title supports mouse selection
and normal copying, matching the source/evidence labels without a text caret.
More contains a preview-before-approval artist-profile
choice, a single Exclude Uploader manager, refresh/source controls and manual
image/URL selection; no Undo menu item. The block manager lists saved custom and
known automatic accounts, permits explicit additions/removals, and preserves
unrelated prepared comparisons when preferences change. The initial loading page has an animated wheel and checked/total progress; the
first usable comparison reveals the review page while an inline indicator keeps
the full scan visible. Completed empty/error states are explicit. Browsing All
can prioritize a pending track. The session preview cache is bounded to 256 entries /
16 MiB, each preview to 512 KiB. A separate confirmed
fetch retires through TaskOwner before publication.
`soundbox.art_review_scan` checkpoints disposable metadata in AppData
`art_review_scan/checks.sqlite3`, outside canonical art/ and full backups. It
stores candidate descriptions plus a separate LRU of small comparison previews
(8 MiB / 128 images, at most 512 KiB each), never audio. Preview eviction cannot
remove suggestion metadata or canonical art; final replacement still uses a
separate guarded fetch. Context stamps bind
entry/image SHA, artist/title, exclusions and matching-policy version; unchanged
checks (including misses) resume without online work. A fresh pass advances its
epoch without touching curated data. Failed requests are not cached as misses;
five consecutive failures pause with resumable progress. Kept/accepted decisions
remain canonical art state and are backed up. On a cached proposal, only its
small preview is fetched if absent from the bounded memory cache. New online
checks are paced; cached and saved decisions incur no delay.
Reopening first validates and restores all saved contexts locally, including
ready comparisons later in the list, before starting any online search. Only
uncompleted or changed contexts enter the pending queue. Progress counts this
remaining queue and shows saved/reviewed totals separately. A decision made while
the queue runs is checked again before its row can start a request. Local image
SHA validation and exact publication baselines remain mandatory; resuming is not
authority to reuse changed bytes. No matching-policy bump is needed for this
scheduling/presentation change.
`desktop.reviewplayer` adds compact local audition controls beside the decision
buttons, reusing PlayerBar/CoreAudio through a read-only snapshot and clickless
pause/resume/seek APIs. The compact snapshot includes actual native playback,
so a transient PAUSED/refill report cannot leave running audio with an inert
Play button. Resume can cancel a pending pause fade. Selecting a row alone is
silent; moving rows or closing
review cancels owned playback, including pending grid/source changes. Review
audition does not capture/advance playlist autoplay, and shuts down before native
teardown. Keep/Replace/Next retain the next visible position below the selection;
reaching the end does not implicitly wrap to the first remaining suggestion.
Review normalizes SoundCloud source identity across tracking links and mobile/
web aliases before fetching a preview. A bounded coarse colour/layout comparison
also suppresses equivalent resized/recompressed artwork. These checks do not
infer publisher trust. Keep/Replace persist per-track protection, not a learned
global ranking model; deliberate cross-song artwork choices do not establish
song identity.
`ArtCache.apply_review` checks exact displayed metadata/image SHA under the state
owner and atomically preserves one original per changed track in `art/review/`.
Backend recovery survives restart and refuses changed/corrupt state. An accepted replacement
also stamps its new cover/context as reviewed in the same transaction. Browsing/searching never
publish images; manual artist overrides remain authoritative.

Matching uses the same `metadata.editor_credits` parser as artist derivation:
named Edition/Version credits outrank original tags, generic Deluxe/Radio/etc.
descriptors do not. Unicode identities use normalization/accent folding. The
requested version must agree; the original or a different named edit cannot
receive an own-artist rescue. Exact song cores of three or more letters can
match one contributor of a collaboration. Review additionally admits labeled
possibilities supported by an artist word in the uploader identity and strong
song-word evidence (including colloquial in/ing forms). These never enter
unattended selection. Queries include version creators and bounded wording
variants; no account-specific aliases are hard-coded.

Pure `art_sources` rules and core-owned `art/source-policy.json` fence every
discovery path and new publication. `canonical_image_path`/`image_path` quarantine
historical excluded-source art, while `cover_snapshot` can still read its exact
bytes for recovery. The authorized excluded-source policy uses only strictly
matched artist-profile images (confidence >= .90, profile URL), captured through
the ordinary artwork worker ownership. It compares the old snapshot again, so
intervening manual changes win. Original bytes/index and any previous review
recovery are atomically preserved under `art/source-recovery/`. Intentional
profile defaults carry `fallback=excluded-source` and effective artist identity;
they do not remain Suggested issues or get cleared by normal re-scans. Missing
profiles leave excluded images hidden. No source audio/rekordbox write occurs.
New uncached artwork still uses `store_auto_if_unfetched` to avoid overwriting
intervening manual edits/misses/blocks. The runtime provider receives immutable
source preferences and never writes them. Custom exclusions can be reversed.
This is not complete reseller detection or proof of account authenticity.

## Persistence

Library curation lives in one portable `library.json` (overrides,
playlists, imported records, hides/removals, settings) at
`~/Library/Application Support/LowFreqLabs/Soundbox/`, keyed by stable track
IDs so a newer rekordbox library preserves all work. Atomic ordinary JSON saves, rotating
backups, corruption auto-recovery, one-click full `.zip` backup/restore
(library + history + art cache, including managed default photos). Track-specific
art stays in `art/index.json`; the independent `art/defaults/` pool holds
content-addressed baseline JPEG copies plus its assignment seed/map. Successful
track covers/avatars take precedence. Naturally missing and explicitly blocked
tracks receive a deterministic-random, exact-balanced pool assignment without
changing their underlying cache state. Rebalancing preserves valid assignments
and moves only the minimum required, preventing unrelated device-art drift. The
settings payload also stores the last
closed window geometry, table columns, and Rekordbox/Soundbox/Devices splitter
heights plus waveform zoom and beat-jump mode; startup restores
the outer geometry before applying inner pane sizes.
`state_repository` owns schema validation, recovery, rotating history and saves,
retaining unknown extension namespaces. Invalid shapes enter recovery just like
invalid JSON; without a usable backup, normal saves remain disabled. Save failures
return false at the UI adapter and retain a visible window-title warning.
Identical saves compare serialized bytes and persisted filesystem identity under
that same lock before skipping backup/publication/fsync. Missing/replaced state
or restored generations cannot reuse this shortcut. Zoom/jump/display/autoplay
preferences debounce for 400 ms; curation, backup/restore and shutdown save
immediately and cancel any pending preference timer.

`state_io` serializes library/artwork/backup access within the app. The executable
also holds a `QLockFile` for its AppData directory to reject a second writer app.
`artwork` owns local ArtCache/results/defaults without importing online discovery;
`artfetch` retains compatibility aliases. Artwork mutations stage image/index/default
changes together, publish under the area lock, and revert memory on failure.

Backup copies a consistent snapshot under that lock, then compresses outside it
and atomically publishes the zip. Restore validates bounded archive/folder input,
rejects links/unsafe members, stages the legacy merge of library/history/art and
retains originals in a write-ahead journal through publication. Startup rolls back
an interrupted transaction; committed or rolled-back journals are atomically
retired before recursive cleanup. Failed rollback preserves recovery material and
blocks further publication. This is a recoverable multi-path transaction, not a
single filesystem-atomic swap visible to arbitrary external readers.

The backup/restore UI temporarily disables editing, closes background admission,
drains existing direct-window writers while Qt events remain alive, saves the final
state, then starts its worker. Result handling/reload happens after `finished` and
join. Restoring increments the area generation so stale repositories/caches cannot
write. Active device operations prevent entry. No analysis or derived-device cache
is included in restore; no live AppData fault injection is used for validation.

The sibling `stock_artwork/` directory is disposable derived data, not curated
state: it can always be regenerated from the canonical baseline JPEG art cache.
A completion inventory binds source, derivative-manifest and pixel-file stat
identities to the format profile. Warm hits skip preparation and pacing; changed
or absent files receive ordinary preparation. Selected sync still verifies hashes.

## Build / install

    ./check.sh                 # + arm64 native build, then Python gates
    ./build_native.sh          # native dylib only
    ./build_app.sh             # PyInstaller → dist/personal/Soundbox.app (self-signed)

`./build_app.sh public` separately writes `dist/public/Soundbox.app`.
The tracked `Soundbox.spec` delegates profile content to
`scripts/build_composition.py`; the shell only drives compilation, stamping,
signing and artifact inspection. `requirements-core.txt` excludes `yt-dlp`;
`requirements-personal.txt` adds it, and `requirements.txt` selects the full
personal/developer environment. Artifact checks inspect the executable PYZ and
bundle files for provider exclusion and fixture leaks. Signed profile/module and
source-fingerprint fields accompany Git provenance; dirty builds remain labeled
as such. `--isolated-check OUTPUT_DIRECTORY` uses fresh temporary state, disables
live library/device discovery and production logging, renders and exits. This
checks packaged startup without testing real sources/devices.

Python 3.14, venv at `.venv/`. `build_app.sh` uses the stable local
`Soundbox Self-Signed` identity when present so macOS can retain TCC grants
across rebuilds; it falls back to ad-hoc signing. The app is not Apple-notarized,
so first launch may still require right-click → Open. Notarization is a public
distribution concern (see the ROADMAP public distribution gate). Before signing,
the build stamps the project
version, Git revision, commit-count build number, and clean/dirty source state
into `Info.plist`; `scripts/app_provenance.py /Applications/Soundbox.app`
checks the bundle signature and its declared revision/version against a clean
checkout. A dirty checkout or bundle, bad signature, or mismatched metadata
fails verification. This is signed build metadata captured at stamping time,
not independent proof that packaged code was compiled from those sources.
Both stable and ad-hoc signing happen after all plist edits.

Installation is a state-preserving handoff, not a delete/copy shortcut:
gracefully quit SoundBox, back up/hash `library.json`, move the previous app
bundle to a temporary rollback directory, `ditto` the tested bundle into
`/Applications`, verify its signature, relaunch, and inspect the startup log.
Never use `rm -rf` for installation and never touch SoundBox AppData while
replacing the application bundle.

## Safety contract

The synchronized non-negotiable rules are in `AGENTS.md` and `CLAUDE.md`; the
authoritative per-write commentary is at the top of `soundbox/sync.py`.

`device_identity.remember_identity` enriches read-only dashboard snapshots from
`settings.stock_ipod_identity`, keyed by volume UUID/library persistent ID.
Original provenance persists; fresh observations win and serial conflicts reset
the association. This cache cannot authorize writes or identify a new mount.


Explicit composite Repair plans missing manifest-owned Rockbox audio and per-track
artwork as verified new copies when the guarded destination is absent. Normal
Sync trusts retained manifest inventory and does not look for missing files.
Existing occupants, symlinks,
and inaccessible paths are not treated as missing; commit rechecks vacancy and
rollback removes only files created by that transaction. A fully reconciled stock
library with no semantic changes retains its original iTunesDB bytes even when
the disposable stock shadow is absent.


The bounded composite drift poll treats source size/mtime changes as Sync-needed
hints, but not ctime alone: cloud eviction/hydration and filesystem metadata edits
can change ctime immediately after a successful commit. This UI status is not a
content-integrity certificate. Source proofs/receipts still include ctime.
Normal composite Sync also retains unchanged committed contracts across
ctime-only drift, without renewing their
original proof or manifest timestamps. New/relocated/changed sources and actual
payload acquisition retain exact-byte verification. Verify checks device
references/sizes; Deep Verify also hashes managed files; Repair explicitly audits
inventory and restores missing owned Rockbox payloads/art. None of these modes
turns a clean badge into a fresh source-content integrity certificate.

Playback and iPod management are independent. Device discovery and ordinary
status polling must not check player.is_playing as a suppression condition.
Only device operation ownership pauses competing device reads/writes. Reconnect,
Rockbox tag-cache updates, unrelated firmware preferences and ctime-only cloud
metadata changes do not themselves create managed-library Sync drift.

September 27: the actual reconnected generation-60 device had no managed library
or playlist drift, only the absent unsupported `sort playlists` setting. Rockbox
4.0's [settings table](https://raw.githubusercontent.com/Rockbox/rockbox/v4.0-final/apps/settings_list.c)
does not define that key. SoundBox no longer inserts or requires it. This corrects
the prior documented assumption; no device configuration was rewritten for the fix.

Post-sync polling is held through the completion dialog only. Dismissal releases
the hold so subsequent SoundBox edits can update drift without reconnecting.

### Playback startup readiness (October 8)

Native CloudStorage admission preserves POSIX read errors before AVAudioFile can
obscure them. One-byte probes run only on the decode thread; ENEEDAUTH gets at
most two cancellable three-second waits. This is transient readiness, not a
content proof. Failed audio sources can reopen on Play while resident waveform
buffers remain intact. PlayerBar cancels pending transport/cue/gain intentions on
error; the bottom status exposes the cause. Visual prediction is bounded below
by the current Play/seek frame until its CoreAudio presentation time arrives,
preventing the initial backward jump without changing steady-state rendering.


Grid editing uses `desktop.grid_controller` for immediate RAM rows, ordered
250-ms/one-second grouped nudges and discrete FIFO saves. It shares the tag/cue
TaskOwner and recovery lane. A generation-bound native grid setter updates only
geometry/bar counters; late analysis retains the newer grid/cue overlays. Saved
rows update V5 metadata and one browser BPM row, preserving waveform/audio state.
Old preparation publications are fenced before source saving, off the GUI thread.
Known grid recovery runs before source snapshots. Unknown payloads fail closed.
Normal shutdown flushes accepted grid intents before draining the shared owner.


The prep Q button stores `settings.quantize` (default false) in SoundBox state.
PlayerBar finds the nearest current grid row by binary search over its existing
beat list; the GUI captures the resulting cue timestamp before enqueueing.
No native timing, database protocol or source Quantize preference changes.
Existing cue playback remains immediate and unsnapped. State load/restore updates
both the button and player without re-saving or moving transport.
Six unassigned actions in the shared keyboard catalog dispatch the five grid
operations and Q through their existing admission paths. BPM Value has no action.
Paused bar-start capture reads the visible waveform cursor after loaded-source
guards; playing capture uses fresh native time. Neither path quantizes grid edits.
Grid intents may enter the same FIFO before the initial source probe completes.
They retain track/UUID/path and captured milliseconds. A RAM-only base previews
the shared row arithmetic immediately; it cannot reach Library/cache/source.
The head requests its probe before saving, requires that loaded base to match
the source grid exactly, and validates native metadata. Failed checks cancel the
tail and restore its base; shutdown drains admitted probe-bound work. Provisional
doubling omits final half-beat extrapolation until native end bounds arrive.
Manual-BPM terminal-row ambiguity similarly stays provisional in RAM. The source
worker alone may resolve `AmbiguousGridEnd` using `pcm_endpoint`'s bounded PCM
WAVE framing proof: exact integral milliseconds, native-bound agreement, stable
canonical file identity and header digest. No audio payload is read or decoded.
The optional journal proof is revalidated before publication, commit and recovery;
unsupported endpoints fail closed. Exact-EOF inclusion matches the October28
STOP CAPPIN native control. Ordinary edits retain their existing source reads
and do not open audio.

MainWindow places the cue panel beside both header and deck rows; standalone
PlayerBar instances keep their embedded cue panel. The title frame shares the
waveform's right edge, and the first grid button shares its left edge. My Tags,
Settings and Search occupy the prep strip's right end, with Search beneath the
cue panel. The wordmark retains its original 270×55 slot.
The logo/title gap equals the left inset. MainWindow mounts the transport deck
beside both waveform and prep rows; standalone PlayerBar keeps its embedded
transport. The default 1 Player dropdown exposes disabled 2 Player/Simple/Browser
placeholders and stores no setting or mode transition.

Live Tag/Cue/Grid backups use `rekordbox_write.encrypted_backup`: a stable raw
DB/WAL copy, one encrypted checkpointed copy with SQLite integrity/identity
checks, and existing exact-hash/fsync/recovery publication. SQL still opens only
private copies for this capture. `OfflineWorkspace.capture` retains its separate
full semantic inventory and editable working copy for research proofs; live
backup creation does not need those Python allocations. Source SQL/publication
and recovery formats remain unchanged. Fresh closed-app checks query exact
rekordbox/agent process names through the OS, without a Python process walk or
liveness cache. Library-cache serialization stays atomic but encodes/writes
bounded records so the C JSON encoder cannot monopolize the GIL for a full library.

Manual BPM uses the same immediate grid/FIFO path with a separate acceptance flag
on library and track-bound authorities. Its native protocol additionally owns the
primary contentFile hash/size/revision and AnalysisUpdated. Whole-row recovery
binds those changes with DAT/EXT and SQL. Enter captures exact hundredths plus
track identity; browser row confirmation follows the saved signal, never RAM
preview. The player layout field uses a rounded custom painter with an inset
chevron while retaining the native popup; transport row/column gaps both use 5 px.
