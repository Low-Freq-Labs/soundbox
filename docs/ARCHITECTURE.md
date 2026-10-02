# SoundBox architecture

## Repo layout

Current debt and staged extraction plan: [September software audit](software-audit-2026-09.md).
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
PQTZ/PCOB/PCO2/PWV3/PWV5/PWV7 parser) · `device_profile` (read-only device
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
SoundBox's schema-V5 container owns the imported streams, grid/cues, source
identity and optional derived detail under `track_analysis/`. Sections are
independently Zstandard-compressed and SHA-256 verified, then atomically published.
The import-first path reads complete PWV3/PWV5/PWV7 plus grid data before selecting an analyzer.
It produces only exact 1-ms signed stereo-midpoint extrema, with no duplicate
filter bank, FFT or beat analysis, and builds its overview from PWV5.

Compact records use `soundbox-rekordbox-supplement-i16-v1` and
`rekordbox-pwv5-rgb-v1`, store divisor 1 only, and omit the 24-band master. Loading
builds `1/2/3/4/6/8/12/16/24/32/48/64` mips in memory. Incomplete imports and codecs
not supported by the supplemental decoder retain full fallback analysis. Old
V5 remains ready; startup does not force conversion. Interactive playback loads
old analysis immediately and upgrades just that selected local track atomically.
The old record survives interruption or failure; manual metadata ownership and
DB cue authority survive replacement. Source changes, forced Reanalyze Track and
late imports use the same preparation path. Dataless background reads remain
deferred and per-track preparation receipts still avoid repeated work.

Loading remains generation-guarded: native decode, manifest, overview and detail.
All cache decoding and waveform preparation stay off the UI thread. A complete
native upload presents overview and all three display modes together. Source
replacement rejects late generations. Native ABI 5 additionally accepts a tested
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
lattice. The three widest stops and the 384–1500 close family use a half-pixel source
footprint; the middle family keeps one pixel. Thin signed carriers use a
one-physical-pixel minimum before coverage, replacing the former fixed
3.6-point stroke. Color is
premultiplied before the final one-pixel integral, with no adjacent-LOD blend or
translated RGBA texture. Legacy/fallback spectral masters never recolor the
imported display modes.
Metal receives integer sample anchors plus
small local phases for both geometry and color, retaining long-track subpixel
precision. The scrolling lane translates fixed height/hue attributes into each
final Retina drawable pixel. The fragment computes vertical silhouette
coverage and composites the final color exactly once. The accepted
quarter-pixel profile remains in use at 64–184 px/s for RGB/BLUE and below
1152 px/s for 3BAND. RGB/BLUE at 24–48 and 256–1500, and 3BAND at
1152–1500, use the pixel endpoints plus every integer knot of the four-attributes-per-pixel
lattice to form the complete piecewise-linear profile; integrating those true
segment spans avoids a hidden corner moving through a fixed probe interval.
There is no translated RGBA texture or second coverage/color filter; every
zoom retains fractional travel through the oversampled attribute lattice. The
antialias window is exactly one physical pixel. The small overview applies
continuous cubic vividness to true subpixel coverage; the scrolling lane uses
geometric coverage rather than the overview's vividness transfer.
For signed intervals, taking the smaller of separately integrated upper/lower
half-plane coverages is an approximation: it can overestimate narrow diagonal
traces. The September 30 audit establishes this limitation; changing it still
requires comparison against the accepted detail.
For BLUE/RGB signed intervals, the fragment additionally integrates clipped
linear coverage moments and conditions color on the locally visible portion of
each quarter-pixel segment. PWV5 RGB and BLUE's center/width-dependent radial
field are accumulated in premultiplied linear light; a hidden horizontal sample
cannot tint a visible interval. The established continuous upper/lower boundary
coverage is reapplied exactly after the color resolve, preserving silhouette
geometry independently from color.
The canonical 64–256 px/s midrange family applies one same-LOD
half-physical-pixel `[1, 2, 1]` reconstruction pass before that final
integration. The 24–48 distant stops and 384–1500 close family bypass it; it
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
uses continuous coverage/color integration directly, subject to the signed
coverage approximation above. The installed September 30 correction additionally
retains the original color-premultiplication weight through attribute interpolation;
reconstructing that weight from interpolated signed bounds spuriously brightens
polarity/dominant-boundary crossings. Geometry and coverage are unchanged.
The October 1 complete-corner correction at RGB/BLUE 256/384/576/864
resolved the remaining reported pulses in Sonny's live B comparison. The separate
BLUE palette/white-core correction was also accepted and installed. Broader track
and exact-reference acceptance remain open; see the current handoff.
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
each row Both, Stock, or Rockbox; the filesystem browser remains for Rockbox-only
and generic USB targets. Connection presentation separates visibility from readiness. Debounced `/Volumes`
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

## Data model

- **Reads the LIVE rekordbox DB** (`~/Library/Pioneer/rekordbox/master.db`) via
  a read-only temp copy through `pyrekordbox`; XML export is a fallback.
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
- **Two planes.** rekordbox is king (the mirror); SoundBox keeps its own plane
  on top: imported audio (referenced in place, never copied), per-track
  title/artist overrides, custom playlists, and deletes/removals that act as
  *vetoes* over the mirror. Both planes drift synced devices; rekordbox is never
  changed.
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
  GOTCHAS caching note — the field must ride through `libcache` + `_landed_cache`.
- **Drag-to-add works on the sidebar AND the main browser.** Dropping tracks or
  Finder audio onto the open view adds to the playlist it shows; Collection
  imports files only; Artists (tag-derived) and read-only rekordbox views reject
  every drop (`TrackTable.resolve_drop` gates it — rekordbox is never written).

## Persistence

Everything user-made lives in one portable `library.json` (overrides,
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
