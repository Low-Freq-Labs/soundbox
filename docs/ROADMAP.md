# SoundBox roadmap

Living to-do. Work top-down. The MA446 dual-boot iPod workflow is shipped and
physically accepted; remaining device work is follow-up hardening and new hardware.
For the live test count, trust `./check.sh` output—not any number written here.

## Current status (2026-08-25)

- **CORE LIBRARY/EXPORT COMPLETE; LEGACY COVER RE-AUDIT ACTIVE.** Every track
  title is cut/cleaned. A 2026-08-04 review proved that some pre-hardening
  automatic cover rows survived with unrelated same-title/same-artist art, so
  the old claim that every cover was eye-verified is retired. Current automatic
  discovery hard-stops on Unknown Artist, orients both title/artist orders,
  requires 0.80 confidence, and records provenance; existing manual selections
  and sticky removals remain protected while legacy automatic rows are reviewed.
  A managed default-photo pool now fills natural misses and deliberate blocks
  without replacing real covers/avatars: uploads are SoundBox-owned baseline
  JPEGs, assignments are deterministic-random and exactly balanced, and the
  existing stock-iTunes/NightPod derivative paths consume them unchanged.
  Engine is mypy-clean,
  `./check.sh` is the one-command gate, and the installed `Soundbox.app` is
  signed + current.
- **NATIVE PLAYER CAMPAIGN COMPLETE.** CoreAudio sample-clock playback and a
  120 Hz Metal deck now provide RGB overview/detail waveforms, Rekordbox beat
  grid, persistent zoom/jump controls, CUE/play/track navigation, precision
  scrub, clean gain ramps, and cache-first rapid loading. Full reference:
  `docs/PLAYER.md`.
- **PLAYER SIMPLIFICATION SHIPPED AND ACCEPTED.** BLUE/RGB now composite the
  exact continuous interval/color resolve directly; the rejected
  carrier-neutral envelope, background bed, coverage sharpening, and RGB
  transition experiments are gone. Native zoom snaps to the same thirteen UI
  stops, and the old overlapping stop tapers are one explicit 64–256 midrange
  rule. The signed ABI-4 app is installed and accepted, the full V5 audit is
  complete, curated state was verified, and no temporary rollback remains.
  Full evidence: `docs/PLAYER.md` → “Production renderer simplification on
  2026-07-30”.
- **GLOBAL GRAVEYARD CACHE RECLAMATION SHIPPED.** A globally hidden track now
  relinquishes its disposable SoundBox V4/V5 waveform record immediately.
  Bulk preparation excludes global-Graveyard IDs, in-flight bulk and
  interactive cooks cannot republish them, startup reclaims records left by
  older builds, and selection-scoped restore returns a playable source to
  ordinary bounded preparation. Playlist-only removals retain analysis because
  the track remains browsable elsewhere. Installed-app verification reclaimed
  499 records / 1.244 GiB for the live library, retained the two playlist-only
  records, and scheduled zero preparation tasks.
- **SOURCE-ADAPTER BOUNDARY SHIPPED.** `soundbox.library` now owns the
  provider-neutral `Track`, `Playlist`, and `Library` model. The live
  rekordbox DB and XML readers are concrete read-only adapters into it;
  curation, cache, sync, workers, and tests no longer import their domain
  types from an XML parser. Source identity, cues, and optional lazy-analysis
  paths are normalized, XML `KeyType=1` playlists are converted to canonical
  track IDs at the edge, and V4 instant-paint snapshots remain readable.
  There is deliberately no speculative Serato/Traktor registry before a
  second real source establishes the necessary interface.
- **REKORDBOX MY-TAG SMART PLAYLISTS MIRROR DELETIONS.** The live adapter now
  excludes soft-deleted tag definitions, `djmdSongMyTag` history, and content
  from My Tag `contains` and `not contains` predicates while preserving
  compound ALL/ANY criteria. The 2026-08-05 live audit found 40 deleted tag
  associations: the old evaluator emitted 28 stale keys from the 10 active
  smart playlists (15 visible active-song memberships and 13 already-deleted
  song ghosts), while the remaining 12 belonged to a deleted playlist. The
  corrected read retained `Jump Bros!` in Trap and removed its historical DnB
  membership without any rekordbox write.
- **ROCKBOX-SIDE PLAYLIST APPENDS IMPLEMENTED.** A schema-3 connection now
  reports ordinary Sync-needed drift when Rockbox changes an owned M3U8 size.
  Confirmed Sync accepts only append-only, already-managed device paths, saves
  genuinely new membership into SoundBox without writing rekordbox, and
  reconciles the same playlist into stock firmware without recopying audio. An
  accidental appended duplicate is recovery-backed and removed from the M3U8;
  pre-existing duplicates remain deliberate. Unknown device songs are not
  imported and no destination is guessed; deletion/reorder edits remain
  fail-closed pending a separate product decision. Physical testing exposed
  Rockbox's `/<HDD0>/...` serialization and a dashboard coupling bug; the
  reconciler now normalizes that exact volume-root form, while changed M3U8
  contents are withheld as untrusted without hiding the device's valid
  identity/database details.
- **IPOD COMPLETION NOW MEANS EJECT-READY.** Stock and composite Sync/Delete,
  including queued composite jobs, keep a visible `Finalizing iPod...` state
  while the committed Device/Songs/Playlists snapshot and sidebar drift/free-
  space scan run in series. Their completion dialog is now the terminal event,
  not the trigger for another hidden worker: periodic polling remains suppressed
  throughout the dialog's nested event loop and stays paused in an explicit
  eject-ready state after OK. Eject is not coupled to scanner cancellation or
  completion; the scanner has already finished. Any explicit later device
  action releases the hold and refreshes normally. Clean/no-write Sync and
  already-absent Delete outcomes use the same finalization path, allowing an
  immediate trustworthy Eject even when there was nothing to publish.
- **DEVICE TRANSACTIONS NOW HAVE ONE TERMINAL CONTRACT.** Export, Sync,
  managed/file Delete, connection preload, verification, and final refreshes
  consume results only after their QThread has returned and released UI
  ownership. Newly detected generic rows also wait for that boundary; new iPods
  additionally remain hidden through dashboard preload and drift. Composite
  progress is monotonic 0–30% planning, 30–80% guarded work, and 80–100% final
  dashboard/sidebar/drift release. Eject and Sync are disabled during true
  ownership, a recurring read-only poll queues Eject instead of throwing a busy
  modal, multi-target exports finalize every touched volume, and a failed final
  refresh cannot run a success dialog.
- **IPOD CONNECTION NOW PRELOADS THE USEFUL VIEW.** A `/Volumes` notification
  starts detection immediately instead of waiting up to three seconds. A new
  stock/dual mount remains absent from the sidebar while the lower-left status
  reads `Loading iPod...`; it receives one serialized Device/Songs/Playlists
  preload and the ordinary drift scan. Only that fully loaded result publishes
  the clickable row, so selecting it performs no removable-device read. The poll
  remains as missed-event/failure recovery, readers never overlap, and a
  same-name remount invalidates path caches through its changed filesystem
  device token.
- **NEXT ARCHITECTURE MAINTENANCE: decompose `MainWindow` by real ownership.**
  `desktop/app.py` is the remaining general orchestration hotspot. Work in
  small behavior-preserving stages: extract crown-jewel state persistence,
  library load/refresh plus row-cache coordination, playback/autoplay
  orchestration, and custom-playlist/import operations into independently
  tested services/controllers. Keep `MainWindow` as widget/signal composition.
  Do not merely scatter methods into cosmetic mixins, and do not combine this
  with a new DJ-system integration.
- **HISTORICAL 2026-07-29 — FINAL-PULSE CANDIDATE.** BLUE
  and RGB now resolve signed-interval coverage and dependent color jointly in
  premultiplied linear light while preserving the already accepted continuous
  silhouette exactly. BLUE's white/cyan radial field no longer exhibits the
  close-zoom internal brightness tick; RGB was explicitly phase-swept and
  retained its established appearance and motion. Both exact tracks passed all
  thirteen zoom stops, the six-reference corpus, 32-position motion sweeps, and
  the full source/render gate. Live acceptance now confirms playback and
  3BAND's Rekordbox-authored paired-row cadence are dialed. The remaining
  BLUE/RGB static pulse at the five close stops was isolated to extrema-source
  density. The initial two-ladder-entry reduction helped but remained visible.
  Those modes now choose one peak mip from a four-physical-pixel output
  footprint, reducing density from roughly one cell per physical pixel to
  0.20–0.26 while preserving exact peak extrema and angular facets. In
  32-position source-frame sweeps, BLUE/RGB mean registered change improved at
  all five stops; exact ten-refresh-step translations held p99 change to 0–1
  display code with 0.08–0.14% silhouette mismatch. Both 3BAND reference
  renders remain byte-for-byte identical. Both full gates passed 551 tests
  with one expected skip. The signed candidate is installed with its packaged
  native dylib active, 3,263 tracks / 83 playlists loaded, zero preparation
  tasks, and all curation-bearing `library.json` sections verified unchanged.
  Follow-up recordings then isolated a separate peak-mip height discontinuity
  and ordinary-pause rewind: BLUE/RGB height is now divisor-normalized from
  64–1536 px/s, and pause uses Metal's exact held frame instead of a coarse
  millisecond transport estimate. Both full gates passed 551 tests with one
  expected skip; the signed candidate is installed with its packaged native
  dylib active, 3,263 tracks / 83 playlists loaded, zero preparation tasks,
  and `library.json` byte-identical to its post-quit backup. Live visual
  acceptance rejected the straight-multiply height normalization because loud
  256 px/s peaks clipped and then collapsed at the 384 px/s source-family
  crossover. The replacement applies the same divisor compensation only
  through unused vertical headroom and adds narrow bounded compensation at
  64/92/128. On the exact recorded passage, the full 24–1536 median outer-height
  ladder now changes by at most three physical pixels per step and the
  256→384 drop is gone. Both full gates passed 552 tests with one expected
  skip; the signed replacement is installed with its packaged native dylib
  active, 3,263 tracks / 83 playlists loaded, zero preparation tasks, and
  `library.json` byte-identical to its post-quit backup. Live acceptance
  confirms the bounded full-ladder transition is much better; height and pause
  are now the accepted checkpoint.
- **HISTORICAL 2026-07-29 — CARRIER-NEUTRAL CLOSE RENDERER REJECTED.** The paired
  physical/GPU run names the residual BLUE/RGB pulse: the source's 41.209 Hz
  signed bass polarity dominates the exact drawables at 41.33 Hz and folds to
  about 18.9 Hz in the 60 fps phone recording. Presentation, GPU deadlines,
  random redraw, transport jitter, and chroma breathing are rejected. A
  controlled 41.2092 Hz fixture now drives the actual Metal compositor in the
  native test suite. The close BLUE/RGB renderer replaces signed
  polarity with the existing song-anchored unsigned peak envelope; across the
  real five-stop sweep it cuts the causal 41 Hz band from 19–37% of temporal
  power to 1–3%, while both ordinary rendering and 3BAND remain untouched.
  The matched physical phone run now passes: BLUE's folded carrier falls from
  4.86% to 0.38% mean temporal power and RGB from 3.30% to 0.20%; both land
  below the fixed calibration and unchanged 3BAND control. This exposes the
  unavoidable product tradeoff cleanly: the supplied paused Rekordbox
  references visibly contain the same signed zigzag, so removing it changes
  those close paused shapes into conventional filled envelopes. After
  reviewing the matched physical result, Sonny initially chose the
  carrier-neutral moving texture. Live use immediately rejected it because the
  filled envelope removed the detail that defines the close waveform. The
  detailed signed contour is restored as the stopped-and-playing product
  default; 3BAND remains unchanged and a developer control retains the neutral
  experiment for exact A/Bs. The rejected build had passed both full gates
  (556 tests, one expected skip) and its safe install preserved all curated
  state. The restored detailed build also passes both full gates (556 tests,
  one expected skip) and is safely installed with its packaged native dylib
  active, 3,263 tracks / 83 playlists loaded, zero preparation tasks, unchanged
  analysis/art cache counts, and `library.json` byte-identical to its
  authoritative post-quit backup. The aliasing remains open; the next remedy
  must preserve the accepted signed inner/outer detail and paused identity,
  never replace the whole contour with an unsigned envelope.
  Live exact-Metal comparison subsequently isolated a more useful perceptual
  target: the black negative-space boundaries looked soft while scrolling.
  Sonny selected 0.75 continuous gap-edge sharpening for BLUE and RGB, plus
  1.0 sharpening of RGB's fractional PWV5 color transitions. This candidate
  preserves exact signed boundaries, full black, half coverage, full color,
  and endpoint hues; it adds no bed or envelope. Full-ladder capture is
  complete, both full gates pass 559 tests with one expected skip, and the
  signed candidate is installed with its packaged native dylib active, 3,263
  tracks / 83 playlists loaded, zero preparation work, unchanged cache counts,
  and `library.json` byte-identical to its post-quit backup. Live installed-app
  acceptance immediately rejected applying those strengths across the entire
  ladder: counted from fully zoomed out, stops 3–6 (48/64/92/128 px/s) became
  horrible. Both transfers are now hard-gated to the tested 384–1536 close
  family; every stop through 256 retains the prior renderer exactly. The
  corrected 39-segment exact-Metal run covers all thirteen UI stops in all
  three modes (7,020 drawables, zero GPU deadline misses), and 24 isolated
  lower-stop/mode candidate-control renders are pixel-identical. At 384,
  BLUE/RGB change while 3BAND remains pixel-identical. Both full gates pass
  560 tests with one expected skip. The corrected signed bundle is safely
  installed with its packaged native dylib active, 3,263 tracks / 83 playlists
  loaded, zero preparation tasks, unchanged analysis/art cache counts, and
  `library.json` byte-identical to its post-quit backup. This detailed,
  close-family renderer is the accepted hardline and must not be softened or
  traded away to address temporal behavior.
  A subsequent exact-Metal 384 px/s comparison retried close signed
  monotone-Hermite reconstruction at 25/50/75/100% in BLUE and RGB. Sonny
  selected the current linear control over every blend because the alternatives
  softened the accepted micro-detail before buying enough stability. All ten
  240-frame traces completed GPU and presentation accounting; the uninstalled
  developer probe was removed. Signed Hermite is now explicitly rejected.
  Full evidence ledger and acceptance:
  `docs/PLAYER.md` → “BLUE/RGB temporal-aliasing R&D record.”
- **ACTIVE DEVICE WORK: real iPod hardware validation.**
  On 2026-07-31 the real 256 GB flash-modded MA446LL iPod arrived running stock
  1.3 firmware on FAT32. Read-only probing identified `/Volumes/IPOD` as a
  stock-only iPod and preserved the complete pre-Finder Apple control/database
  fixture with byte-for-byte verification before any Apple sync. Rekordbox had
  automatically created a root `PIONEER` export tree on initial connection;
  Sonny deleted it before inspection, leaving its small settings tree in the
  volume Trash; inspection found no Rekordbox artifacts beneath `iPod_Control`.
  On 2026-08-05 Sonny proved a fresh rekordbox playlist export on this same iPod
  loads quickly on a CDJ. SoundBox now formalizes `/PIONEER` as rekordbox/CDJ's
  external tree: it counts toward volume capacity but cannot create SoundBox
  drift or be claimed, verified, imported, cleaned, edited, deleted, or written
  by ordinary SoundBox operations. The apparent post-export Sync badge was
  independently traced to a real Rockbox append in the managed Favorites
  playlist, not to the CDJ export.
  Apple/Finder then produced and physically validated a three-track MP3/WAV
  stock playlist in exact order. All media matched SoundBox-derived staging
  hashes, no accidental media remained, GodLovesUgly played successfully through
  the Wolfson DAC, and its sole play count was captured on reconnect. A pinned
  Clickwheel 0.19.1 engine is now isolated behind SoundBox's MA446/MP3/WAV-only
  two-phase adapter: it renders off-device, preserves Apple smart rules and
  pending play counts, validates the final DB, saves recovery bytes, copies
  media atomically, and commits `iTunesDB` last. Synthetic commit/rollback tests
  and a zero-write real-fixture dry-run pass. The packaged app was installed
  without curated-state or cache loss, and the first SoundBox-authored physical
  commit completed on the MA446: all three existing tracks were content-reused,
  no media bytes changed, the exact requested order and GodLovesUgly play count
  reparsed from the live device, both recovery copies were created, and the iPod
  ejected cleanly. Sonny then browsed, played, and skipped the SoundBox playlist
  in stock firmware. The reconnect capture preserved the SoundBox database hash
  and exact playlist order, recorded distinct play/skip deltas for all three
  tracks, and passed another zero-write production plan before clean eject. The
  first stock-hardware acceptance loop is complete. MA446 stock-art support
  pre-generates content-addressed
  100×100/200×200 RGB565 derivatives from the curated baseline JPEG cache,
  builds and structurally audits ArtworkDB/ithmb off-device, and passes fixture
  commit plus exact-artwork rollback tests. Physical artwork and normalized
  metadata now display correctly on stock firmware. The eight-row HOUSE source
  playlist resolved to six Collection-visible, stock-safe tracks; both incompatible
  WAV files were reported and omitted, and all six accepted tracks played without
  a crash. Checkbox multi-delete then removed two playlists and their now-unowned
  media in one guarded commit, leaving zero indexed tracks and zero regular user
  playlists. The clean reconnect retained all five Apple smart rules, no pending
  Play Counts, eleven recovery points, and iTunesDB SHA-256
  `c235f57378793d1db96d3f5b059bdb01e1262e04b9b54375a2c78cce6770123c`.
  The canonical Apple Videos smart index was subsequently deleted on hardware
  with zero media loss and stayed absent through later syncs. Favorites,
  SONNY.AIF, and Indie now produce a verified 100-track/100-file stock state.
  A playlist-scoped-shadow defect that duplicated 18 existing SONNY files was
  repaired recovery-first and closed with narrow exact-content reconciliation.
  Real Spaceman cover churn then proved both the genuine 100,000-byte artwork
  update and zero-write relinking of matching pixels left in free slots. The
  final database/artwork/shadow audit passed and the iPod was cleanly unmounted
  for one deliberate overnight stock-firmware soak before Rockbox installation.
  The composite MA446/FAT32 migration routes a layout containing both
  `.rockbox` and `iPod_Control` through one single-copy ownership plan—not two
  exporters. Additive export, metadata repair, Sync, order, authoritative
  deletion, reference-counted cleanup, interruption recovery, and full-Collection
  scale are shipped. Schema-2 migration remains fail-closed. Never erase,
  bulk-clean, or guess the target.
- New frontier (2026-07-08): **CDJ USB export** → `docs/cdj-usb-export.md`.
- **V5 BOUNDED WAVEFORM CACHE SHIPPED.** The canonical cache now
  persists only the exact signed min/max mip levels selected by every supported
  zoom at macOS scale 1/Retina scale 2, while retaining the compact
  BLUE/RGB/3BAND streams, spectral master, grid, cues, provenance, and source
  identity. Fresh cooks and V4 migration share one extrema reducer. Migration
  is atomic, resumable, and retires one V4 only after a complete V5 audit.
  Full cooks preserve a 20 GiB OS reserve and resume at 25 GiB.
  The 2026-07-30 controlled installed-app cutover backed up curated state and
  every V4 manifest, freed the 40 GiB V4 cache before starting V5, verified the
  signed ABI-3 native deck plus the 3,263-track/83-playlist library, and left
  the direct V5 recook resumable with its temporary rollback retained until
  the final full audit.
- **CONTINUOUS NEW-TRACK PREPARATION SHIPPED.** Newly detected rekordbox tracks
  and audio imported directly into SoundBox enter the same canonical V5 cooker
  immediately. Successful source loads/imports also arm a serialized 60-second
  source-identity recheck: it reads only stats and small manifests, never full
  waveform payloads, and recooks only a track whose container changed after its
  first pass. The installed 2026-08-03 repair automatically found 13 such stale
  new-track records without loading them; the final 2026-08-04 wrap-up launch
  and recurring pass both reported zero pending work.
- **LATE REKORDBOX ANALYSIS RECOVERY SHIPPED.** The recurring scan now probes
  only Rekordbox-backed display modes whose cache provenance still identifies a
  SoundBox fallback; when a valid late PWV3/PWV5/PWV7 stream appears, that one
  track recooks automatically. A track-row context action can still force one
  atomic V5 recook even when its manifest is structurally ready. Both routes
  import the complete current read-only streams, keep the previous playable
  record on failure, and hot-refresh a loaded track without touching source
  audio or Rekordbox.
- Read-only live-DB memory/hot cues now occupy a dedicated two-page 2×4 panel;
  V5 retains overflow cues beyond the eight visible slots.
- **V4 ANALYSIS FOUNDATION SHIPPED.** SoundBox now owns checksummed per-track
  grid/cue/RGB/24-band/ultra-waveform records, refreshes read-only Rekordbox
  grid and cue metadata by exact fingerprint, imports valid Rekordbox analysis
  as a seed/fallback, natively analyzes missing data, and labels AAC/ALAC as
  player-only.
  Rekordbox is no longer a prepared-playback dependency.
- **SOUNDBOX RGB FILTERBANK SHIPPED.** Display color now comes from the
  dedicated overlapping 100 Hz low / 300–3000 Hz mid / 1500 Hz high analysis
  with frequency-dependent peak holds and local nonlinear RGB transfer.
  Renderers no longer remix the independently normalized 24-band master.
  Existing V4 records upgrade atomically one track at a time, preserving
  SoundBox-owned grid/cue rows and the established ultra silhouette.
- **POLARITY/RANGE GEOMETRY + RGB TIMING UPGRADE SHIPPED.** The high-rate
  contour stores independent positive and negative stereo-midpoint peaks plus
  signed per-cell midpoint minimum/maximum with a 1/16 channel-safe floor.
  BLUE and RGB both render the signed range as a trace instead of filling peaks
  to the center; their PWV3/PWV5 color compositors remain distinct.
  The 150 Hz envelope uses complete non-overlapping cells instead of
  the 46 ms spectral window. A display-only `height^1.4` curve, 0.84 ceiling,
  and true-zero continuous shoulder retain both attack shoulders and a stable
  subpixel tail. Lossless RGB uses zero timeline correction; MP3 retains its
  measured seven-row decoder correction. The waveform model receives one
  rolling atomic recook while preserving SoundBox-owned grids/cues.
- **REKORDBOX BLUE/RGB/3BAND DISPLAY MODES SHIPPED.** Settings persistently
  selects among three independently cached native waveform pyramids. Existing
  Rekordbox tracks import exact packed PWV3/PWV5/PWV7 rows read-only; other
  tracks derive the full set in the same analysis pass, so one model migration
  is sufficient. The July 28 six-reference rebuild gives each mode a distinct
  geometry contract. Raw ultra maxima and pure PWV slabs were both rejected.
  BLUE uses PWV3 luminance for its blue/cyan edge and RGB uses PWV5 packed
  color. Both use the persisted signed min/max trace at the literal
  physical-pixel footprint. 3BAND instead uses its
  three native PWV7 shapes,
  decodes them as
  low/mid/high display layers, applies `0.40/0.43/0.40` native band scales,
  begins at the paired-row peak level matching Rekordbox's visible 75 Hz
  facets, linearly connects those values for clean slopes, and reproduces
  the measured blue/brown/cream/pale-blue/white membership palette. Exact
  display rows retain peak geometry when reduced, remain song anchored, and
  never trigger a cache recook when the persistent selector changes. The
  native real-track corpus renderer is the acceptance gate.
- **PERFORMANCE/POLISH PASS SHIPPED.** Long-track preparation is bounded,
  cooperatively interruptible, and automatically battery-aware; playback and
  foreground interaction preempt it inside the current file. Static Metal
  rendering and native polling now sleep; waveform host, AppKit child, and
  Retina drawable resize in the same UI turn without generic child-resize wake
  storms; focus return re-synchronizes transport from the native snapshot. No
  cache schema or analysis-quality change was required.
- **UNIFIED ZOOM/SCROLL RENDERER SHIPPED.** Thirteen truthful scales now span
  24–1536 px/s. Overview, scrolling geometry, and RGB use one continuous,
  song-anchored LOD path; amplitude-weighted color mips remove far-zoom chroma
  fighting while the established maximum-zoom appearance remains intact.
  Scrub math consumes the native-applied scale, detail promotion no longer
  re-uploads color, render encoding does not hold the analysis mutex, and
  display cadence follows the active screen through resize/screen changes.
  The moving lane now translates height/hue attributes from a fixed global
  song-coordinate lattice, then computes final vertical coverage once at each
  Retina pixel; there is no translated RGBA surface or second coverage/color
  filter. Overview and ultra
  buffers make one atomic first-visible commit, so long sets do not inflate
  during detail promotion. A playing waveform grab captures the last drawable
  already presented, preserves its exact source frame through
  fractional-millisecond drag math, and hard-mutes/pauses synchronously on
  mouse-down; a stationary release resumes from exactly that held frame.
  One footprint-matched mip is selected with ceiling rather than blending two
  differently quantized song lattices or exposing undisplayable detail. Four
  attributes per physical pixel at every zoom retain continuous subpixel
  motion; the former one-attribute close mode made dense spikes strobe in large
  whole-pixel steps. Five quarter-pixel boundary samples integrate height and
  premultiplied hue over exactly one final pixel. Premultiplication keeps edge
  color energy phase-stable instead of multiplying separately interpolated
  straight color and height. The prior cubic B-spline reduced redraw shimmer
  but visibly softened maximum-zoom detail. The final split uses positive cubic
  reconstruction through 384 px/s. From 576 px/s upward, BLUE/RGB signed
  intervals and the native 3BAND layers retain their close detail;
  the prior close
  monotone-cubic path rounded the straight carrier edges in the supplied
  references. Both paths share the narrowly integrated fixed lattice. Final
  edge compositing integrates both boundaries of the BLUE/RGB signed interval
  continuously across the final pixel. The former five independent
  point-in-interval tests quantized a moving edge into 20% coverage steps even
  though its geometry was song-locked. On the exact High & Scared 1536 px/s
  RGB phase sweep, continuous boundary integration reduced the 99th-percentile
  aligned pixel change from 69.0 to 39.6 display codes and the share above 64
  codes from 1.15% to 0.12%, while static silhouette identity changed by less
  than 0.1% of lane pixels. 3BAND already used continuous profile coverage.
  BLUE/RGB color is also integrated conditionally on that signed coverage.
  Clipped-linear coverage moments prevent a hidden PWV5 sample from tinting a
  visible RGB edge, while BLUE evaluates its white/cyan/blue radial transfer
  from the same local signed center, width, and coverage centroid. The accepted
  continuous boundary coverage is reapplied exactly, so this changes color
  energy only—not silhouette, reconstruction, LOD, or timing. On the two
  32-position real-track sweeps, Bad Decisions BLUE p99 fell from 127.8 to 68.0
  display codes and its greater-than-64 share from 9.13% to 1.40%; High &
  Scared BLUE p99 fell from 36.7 to 29.5. RGB stayed effectively flat or
  improved slightly. At that checkpoint 3BAND reference renders were
  byte-identical; the current paired-row follow-up intentionally supersedes
  that historical result.
  Final compositing keeps the vivid continuous
  curve only for the small overview; the scrolling lane uses exact geometric
  coverage at every zoom, removing saturated red/orange amplification without
  changing reconstruction or detail. Timing lines are opaque red on downbeats
  and opaque gray on ordinary beats so waveform content cannot alter their
  color or contrast. Scrolling beat/downbeat
  geometry now also carries analytic physical-pixel line coverage, so the
  one-point timing reference moves at fractional Retina positions rather than
  snapping as a hard two-pixel rectangle. The final all-zoom recording
  isolated residual contour motion at 64 and 184 px/s; only those two fixed
  stops receive a narrow same-LOD half-pixel `[1, 2, 1]` pass. Every
  neighboring stop and the 576–1536 px/s detail-preserving close path remain
  unchanged. A final red-line-only recording exposed the remaining display
  transfer pulse: equal analytic box areas were blended as unequal SDR
  luminance phases in BGRA8Unorm. The line-only compensation confirmed the
  diagnosis but was incomplete: waveform silhouette coverage still used the
  same display-code compositor. The final surface is now BGRA8Unorm_sRGB;
  waveform and grid RGB are decoded before coverage/blending and encoded once
  by Metal on store. Geometry, reconstruction, LOD, detail density, full
  colors, and the established background remain unchanged.
  Ordinary Qt position publication remains idempotent and cannot wake the
  Metal run loop through an inactive preview. **Rendered motion blur was tried
  and removed:** the eye
  tracks a scrolling waveform, so a shutter smears a retinally stationary
  image, rounds edges, and freezes blurred under a grab. The integration
  window is the physical pixel and nothing else. Cadence is
  measured from actual drawable presentations, not display-link submissions,
  and `sb_deck_presentation_error_*` now reports predicted-versus-actual
  presentation timing, measured at single-digit microseconds across all zooms.
  The final registered phone/native comparison isolated the remaining rare
  doubled interval at `CAMetalDisplayLink` callback delivery itself while
  Metal still had roughly 8.2 ms before deadline. Hard 120 Hz, a 90–120 Hz
  floor, and latency two all measured worse and were reverted; the shipped
  latency-one negotiated 60–120 Hz policy is the validated boundary, not an
  unfinished waveform/cache fix.
  A subsequent exact same-event visual-clock comparison tested repayment of
  one missing 120 Hz sample over 2/4/6/8 refreshes. The middle four-refresh
  preview looked best and bounded the live 735-audio-frame kick to 460; a
  natural all-thirteen-zoom run confirmed that bound with zero GPU deadline
  misses and zero audio underruns. Installed-app acceptance nevertheless found
  it worse: one rare discontinuity became several frames of visible
  catch-up/rubber-band velocity. The recovery clock and probe are removed, and
  the definitively better direct-prediction control is restored. CoreAudio
  remains master; waveform, grid, cues, and playhead share its predicted target
  time. The remaining rare hitch is visual-only ProMotion callback omission,
  not unfinished waveform, grid, cache, clock-drift, GPU, or audio work. Both
  final gates pass 560 tests with one expected skip. The signed direct-control
  build is installed with its native deck active, 3,263 tracks / 83 playlists,
  zero preparation tasks, unchanged 3,248-record analysis and 3,125-file art
  caches. Curated `library.json` data is unchanged; its only semantic
  post-relaunch difference from the protected post-quit backup is the ordinary
  `settings.waveform_zoom` save from 576 to 256.
  Per-frame positional stability on a real library track is 0.3% of travel
  (~0.07 Retina pixels at maximum zoom), so motion is exact by measurement.
  A latency-one grab freezes the newest presented/committed source frame,
  eliminating the backwards mouse-down jump.
- **PLAYLIST AUTOPLAY SHIPPED.** Header up/down modes walk the playlist snapshot
  that deliberately started the track; random uses a no-repeat shuffle bag.
  Browsing is independent, a new deliberate source replaces the snapshot, and
  only a true native end-of-media edge advances.
- **LOADED-TRACK QUALITY PREEMPTS BULK WORK.** A loaded cache miss keeps its
  UI responsive and immediately runs the exact canonical RGB/ultra analysis in
  a persistent foreground lane. Battery,
  Low Power Mode, playback, and interaction policy apply only to bulk backlog;
  below 50% a large backlog becomes gentler without changing one stored or
  rendered sample. Grid metadata now decodes DAT/PQTZ rows directly and never
  opens Rekordbox EXT/2EX data.
- **NATIVE-ONLY + V4-ONLY CLEANUP SHIPPED.** The Qt playback setting/path and
  schema-1/schema-3 waveform caches are gone. Qt Multimedia remains only for
  AAC/ALAC analysis. A strict read-only audit validated every reachable library
  track before the three retired cache directories were removed. The audit
  separately reports valid/playable V4 coverage and current ultra/RGB coverage,
  so safe legacy retirement never hides a still-running quality upgrade.
- **DEVICE PROFILES + NARROW STOCK ENGINE SHIPPED AND INSTALLED.** Read-only probes
  distinguish Rockbox, stock iPod, dual-boot, and generic USB layouts. FAT32
  Rockbox/generic exports retain the guarded existing workflow. A stock-only
  MA446 can enter the separate MP3/WAV dry-run/explicit-commit path; unknown
  models and checksum families fail closed. The
  installed build completed its first guarded physical database commit,
  stock-firmware playback/skip session, reconnect capture, recovery proof, and
  second zero-write plan. A dual MA446/FAT32 layout now enters the separate
  repeatable single-copy composite planner; remembered stock identity cannot
  activate either old exporter. Additive/repair generations are supported;
  schema-3 destructive reconciliation is recovery-backed and shipped, while
  schema-2 migration remains fail-closed. See
  `DEVICES.md`.
- **STOCK IPOD DEVICE SURFACE SHIPPED AND INSTALLED.** Selecting a stock or
  dual-boot iPod now opens header-level Device, Songs, and Playlists tabs populated by an
  asynchronous read-only database snapshot. General identity/storage/database
  health, indexed metadata, playback totals, recovery points, and exact ordered
  user-created playlists are visible without exposing raw control folders; the
  exact Videos smart index is also visible as a deletable System playlist while
  the library root and all other built-in smart lists remain internal. MA446 confirmation
  persists by library persistent ID + volume UUID and migrates from successful
  SoundBox recovery evidence; rewrite-volatile DatabaseID is never trusted.
- **DUAL LIBRARY TRUTH + ROCKBOX METADATA REPAIR SHIPPED AND INSTALLED.** The
  dashboard now reports schema-3 managed, stock-visible, and Rockbox-only track
  counts instead of presenting the iTunesDB subset as the whole library. Dual
  file tabs identify firmware visibility, commit progress counts actual audio
  copies/replacements/removals as `#/#`, and composite payloads receive effective
  SoundBox title/artist tags in audio-identical disposable copies. Completion
  refreshes the open device snapshot and then the sidebar scan in series. The
  previous schema-3 profile migrates through the original unified manifest
  rather than creating a second ownership system. Canonical-source size and
  sanitized device-payload size are tracked separately, removing the immediate
  false Sync-needed state seen after the 37-track physical commit. Reconnect,
  firmware-owned Rockbox database refresh, and corrected tag display passed on hardware.
- **DUAL PLAYLIST QUEUE, NAMES, AND ORDER SHIPPED AND INSTALLED.** Confirmed playlist
  drops waiting behind an active operation now form one next batch instead of
  repeatedly parsing and validating the same iPod generation. Additive stock
  plans no longer hash-read manifest-proven media; exact reconciliation remains
  limited to new/unproven sources, closing the no-write-planning `Errno 6` seen
  during a seven-playlist export. Device titles use concise rekordbox leaf names
  with folder context only for collisions. The Device Playlists tab can publish
  one explicit sequence to iTunesDB and Rockbox; Rockbox uses alphabetic
  catalogue sorting over zero-padded, SoundBox-owned playlist filenames. The
  validated, signed build is installed with curated state intact. Stock order
  and zero-padded Rockbox catalogue order both passed physical acceptance.
- **ORDER-THEN-DELETE DEAD END REMOVED.** The intermediate retained-row guard
  first learned to ignore schema-3 presentation fields; the final shipped path
  makes explicit Delete authoritative instead. Applying order or changing an
  unrelated playlist cannot force a separate Sync, while unavailable unrelated
  identities are retained from committed snapshots. Header/context Sync remains
  manually reachable even at zero reported drift. The exact regressions and
  final 721-test gate pass in the installed build.
- **ROCKBOX VISIBLE PLAYLIST ORDER SHIPPED AND INSTALLED.** Physical acceptance proved
  stock iTunesDB honors the manifest sequence while Rockbox presents its
  Playlist Catalogue alphabetically. Rockbox-only M3U8 filenames now receive
  zero-padded order prefixes; stock titles and SoundBox rows remain unnumbered.
  Reordering transactionally renames only playlist files and keeps media single-
  copy. The full 721-test gate passes and the signed build is installed with
  curated state intact. Physical sync/restart acceptance confirms Rockbox now
  displays the numbered sequence in the applied order.
- **AUTHORITATIVE COMPOSITE DELETE SHIPPED AND INSTALLED.** A checked managed-playlist
  deletion no longer stops with “run Sync before deleting” when another
  playlist changed or disappeared. The same confirmed transaction reconciles
  available changes and retains unavailable unrelated identities from their
  committed snapshots. Regression coverage also proves a normal same-profile
  sync migrates an unnumbered Rockbox playlist to its numbered catalogue path;
  successful commits now log every published relative playlist path for the
  next physical verification. The full 721-test gate and signed build pass;
  the app is installed with curated state intact. Numbered-order verification
  passed physically; authoritative deletion retains its automated regression
  proof and is ready for the next routine device deletion.
- **STOCK IPOD HARDWARE IDENTITY SHIPPED AND INSTALLED.** The macOS device probe now
  separates Apple's cached stock firmware/hardware serial from IORegistry's USB
  and SCSI transport facts. Guarded serial-suffix mapping independently confirms
  MA446, whose hardware catalog entry presents the concise Wolfson WM8758BG name
  while retaining model-derived provenance internally. Ambiguous records fail
  closed instead of guessing.
- **ROCKET-LOAD + RELIABILITY PASS SHIPPED.** Prepared V4 loads now stage
  manifest/packed overview/ultra detail off the UI thread, native buffer work is
  generation-guarded, track replacement clears every old renderer channel
  atomically, and the 0:00 RGB delay wedge is repaired without a cache recook.
  Session CUE uses five explicit transport states.
- **SYNC MANIFEST V2 SHIPPED.** A pinned device session revalidates every write;
  normalized path ownership, hashes, playlist membership, artwork, and
  completed generations make corruption repair and prune honest. Deep Verify,
  verified eject, FAT collision handling, and ordered device import into
  SoundBox Media are included. Normal Sync never prunes unknown content. An
  explicitly confirmed whole-device erase removes all children and the
  manifest last, then verifies and normally ejects; stock/dual-boot iTunes
  volumes remain protected from generic erase, prune, and manifest sync.

## Scorecard (~9.4/10, honest, not curved)

The core library/export system is mature; the residue is hardware-gated (iPod),
phase-gated (rekordbox 7 / Windows / notarization), or explicitly listed player
follow-up.

| Area | Score | Note |
|------|-------|------|
| Export (copy engine) | 9.5 | pinned target, atomic writes, hashes, FAT collisions, free-space/cancel honesty; last point is USB physics |
| Sync / re-sync | 9.7 | ownership-aware repair/prune, Deep Verify, completed generations, shared-file safe |
| Cover art | 8.4 | conservative auto matching, Unknown-Artist hard stop, manual picker/removal, provenance, and a balanced managed-default pool; legacy pre-provenance auto rows are under review |
| Device detect & safety | 9.5 | off-thread scan, hardened guards, FAT32 warning |
| Live rekordbox read | 9 | read-only temp copy, sacred order, smart playlists; validate vs rb7 in Windows phase |
| UI/UX | 9.7 | collection/artist browsing, curation tools, persistent layout, native DJ-style player |
| Persistence | 10 | portable library.json by track ID + rotating backups + one-click full backup/restore |
| Tests | 10 | engine + hermetic UI + property-based (hypothesis) + live-DB mocks; `./check.sh` |
| Packaging/signing | 8 | stable self-signed; notarization for public (Phase 5) |
| iPod readiness | 7 | software side done; the rest is genuinely hardware-gated |
| Code cleanliness | 9 | monolith split into engine + desktop mixins; engine mypy-clean |

## Phases

### Phase 1 — Re-sync engine ✅ DONE (validated on USB)
Playlist `pid` on the model; per-device `manifest.json`; drift diff
(renamed/moved/changed/removed); `sync.resync` (idempotent re-export, stale
m3u8 cleanup, track-folder-level prune that keeps shared files, forget deleted);
drift UI ("⚠ N out of date" badge → Sync). Sonny's rename-v#/move workflow
"just works" because pid is stable.

### Phase 2 — Settings / polish ✅ DONE
Scoped FINALIZING (clean only written dirs); diff-driven resync; configurable
cue/placeholder hiding; cover-art workshop (accuracy-first matcher, ART column,
manual picker, artist/title derivation incl. trusted-gated reversed flip,
filename-stem fallback, named bracketed remix credits that consistently outrank
original-artist tags, alias/merge window, uploader-consensus suggester);
app.py split into modules; UI-layer tests; rotating + full .zip backup/restore;
graveyard with global-track waveform reclamation and restore-time preparation;
title-cleanup named rules; baseline-JPEG art enforcement.

### Phase 2.5 — Native player ✅ DONE

- [x] Two-row layout: metadata/search/settings above a full-width player.
- [x] Previous/next track, selectable beat/bar jumps, momentary CUE, play/pause.
- [x] Read-only Rekordbox PQTZ beat grid and PWV5/PWV7 RGB analysis.
- [x] Unzoomed whole-track overview plus thirteen truthful detail zoom levels
      from 24 through 1536 px/s; persistent zoom and jump selection.
- [x] Authoritative Rekordbox first-grid-beat loading, zero-anchored fallback
      when no valid grid exists, beat-aware jumps, and thresholded drag-only
      main-wave seeking with pause/live preview/resume semantics.
- [x] SoundBox-owned background caches for instant loads and high-detail
      all-zoom rendering; preparation pauses during playback.
- [x] Independent positive/negative, midpoint-faithful linear uint16 ultra
      geometry plus format-aware RGB timing with one rolling model upgrade;
      legacy V4 remains playable during migration and the display
      contrast/visibility curve stays outside the persisted cache.
- [x] Persistent BLUE/RGB/3BAND selector backed by one combined cache model,
      exact read-only PWV3/PWV5/PWV7 imports where available, and immediate
      resident-GPU switching without recooking.
- [x] Native arm64 macOS deck: AVAudioFile decode thread, lock-free ring,
      CoreAudio render callback, CAMetalDisplayLink, coverage-filtered Metal
      waveform, continuous visual PLL.
- [x] Demand-driven Metal frames, adaptive native snapshot polling, bounded
      PCM analysis chunks, automatic AC/battery/Low-Power policy, and
      interaction-level preparation suspension.
- [x] Native-only playback; native failure disables the deck with a diagnostic
      while library management remains available.
- [x] Window geometry, table columns, sidebar pane sizes, zoom, and jump mode
      persist across clean closes.
- [x] Read-only `djmdCue` memory/hot cues from the live DB snapshot, with
      PCOB/PCO2 fallback, in a HOT CUE / MEM CUE panel: eight visible slots per
      page, overflow retention, label/loop/color preservation, and clickless
      seeking.

### Player follow-up — cues and additive grid

- [x] **Rekordbox memory/hot-cue import:** read `djmdCue` from the temporary
      read-only DB snapshot, preserve position/type/label/loop/color and every
      hot slot, use PCOB/PCO2 only as fallback, and sync canonical metadata without an
      audio recook. Never write cues or colors back to Rekordbox.
- [x] Exact live-DB hot-slot decoding: preserve Rekordbox's reserved Kind=4
      gap (A/B/C=1/2/3, D–P=5–17), reject unknown values instead of guessing,
      and regression-pin all sixteen identities.
- [x] Live cue precedence at track load: inject the read-only DB rows directly
      into the deck so stale/missing cache or ANLZ sidecars can never delay or
      override the authoritative hot/memory identities.
- [x] Cue density/selection workshop: keep the waveform clean and place eight
      hot plus eight memory slots in a compact two-page panel.
- [x] Cue waveform identity: fixed-palette, black-outlined A–H hot-cue boxes
      with flush stems and red-only memory-cue arrows appear in both the
      whole-track overview and scrolling waveform/beat grid in Metal; imported
      color metadata remains preserved.
- [x] Gated cue transport: session CUE, hot cues, and memory cues play while
      held and return on release; Space/play while held latches playback until
      Space/play or another cue action.
- [x] One-shot session-CUE placement: a paused manual playhead move arms exactly
      the next CUE press to relocate the ephemeral orange marker without audio;
      following presses return to normal gated preview.
- [x] Metadata-only legacy-grid migration: valid read-only Rekordbox rows win
      exactly; otherwise existing fallback spacing is shifted to beat 1 at
      `0:00` without recooking or rewriting ultra waveform payloads. Migration
      reads DAT/PQTZ only, never EXT/2EX waveform/color sections.
- [x] Later Rekordbox grid correction refresh: exact PQTZ-row fingerprints
      detect edits to the initial automatic grid and atomically replace only
      canonical metadata on the next live-library refresh. Clean discovery scans stay
      silent and never claim to be preparing when zero work exists.
- [x] Canonical persistence and precedence boundary: SoundBox-owned grid,
      every imported memory cue, RGB/24-band analysis, ultra contour, atomic
      user-data updates, and no Rekordbox runtime dependency.
- [x] Export-field bridge for PQTZ, PCOB/PCO2, PWV5, and PWV7. It is pure and
      non-writing; actual ANLZ/USB assembly remains hardware-gated R&D.
- [ ] **SoundBox-owned beat grid (later):** additive grid state beginning at
      `0:00`, with a manual adjustment workflow and explicit precedence over the
      imported Rekordbox grid. Store only in SoundBox state/cache; never modify
      ANLZ files, `master.db`, or audio.
- [ ] Decide migration/export semantics for a custom SoundBox grid before
      building the editor. Initial scope is player display/transport, not
      writing it into Rekordbox or CDJ analysis.

### Phase 3 — iPod validation ✅ (MA446 stock + dual-boot workflow accepted)
The validated MA446 path is complete; remaining unchecked items are explicit
future expansion, not blockers for normal use:
- [x] Real MA446LL iPod mounted as a 256 GB FAT32 stock-only device; read-only
      detection classified it correctly and withheld every SoundBox write.
- [x] Complete pre-Finder `iPod_Control`/metadata fixture captured locally and
      verified by relative path, size, and SHA-256 before the first sync.
- [x] Apple/Finder generated a three-track MP3/WAV oracle from SoundBox-derived
      inputs; exact playlist order, byte-identical media, stock playback, and
      GodLovesUgly's sole post-play count were verified on real hardware.
- [x] Pinned pure-Python iTunesDB dependency isolated behind a SoundBox-owned
      MA446 adapter; synthetic plan/commit/stale-state/recovery tests and the
      real three-track capture dry-run preserve order, play counts, existing
      tracks/playlists, and Apple smart rules without fixture writes.
- [x] Complete first SoundBox-authored one-playlist MA446 acceptance: explicit
      approval, live dry-run, database-last commit, recovery proof, post-write
      reparse, unchanged media hashes, stock-firmware display/play/skip, exact
      playlist order, reconnect capture, play-count merge dry-run, and clean
      eject all passed.
- [x] Pre-generate deduplicated MA446 stock artwork from the canonical curated
      JPEG cache; validate MHIT/MHII/MHNI/ithmb linkage and exact recovery in
      hermetic plan/commit/failure transactions without retagging source audio.
- [x] Add a disposable Mac-side per-MA446 shadow and incremental artwork
      transaction. Exact reconnect fingerprints enable true no-op plans and
      proven media reuse. Playlist-scoped shadows narrowly reconcile missing
      selected sources against exact live content instead of duplicating audio.
      One changed cover writes only unreferenced 20 KB + 80 KB slots, relinks
      pixel-identical free slots with zero range writes, reuses other freed holes
      on later changes, and keeps the old database/artwork view valid through
      injected mid-range failures. Both artwork paths passed real hardware.
- [x] Complete real-MA446 artwork write/display/reconnect acceptance: curated
      covers displayed in stock firmware, later reconnect/delete plans parsed
      and compacted the linked artwork successfully, and source tags remained
      untouched.
- [x] Enrich selected content-reused stock records from authoritative SoundBox
      title/artist metadata without recopying media or changing dbid, location,
      technical fields, ratings, bookmarks, or playback history; the dry-run
      reports the exact metadata-update count before commit. The preserved real
      fixture renders the intended four-way artist index (`GRiZ`, `DENNETT`,
      `Subtronics`, `Zeds Dead, Subtronics`) with three reused-record updates.
- [x] Add explicit stock user-playlist deletion with a read-only impact plan,
      regular-playlist reference counting, newly orphaned media deletion,
      playback preservation, artwork compaction, stale-state refusal, and
      database/artwork rollback. `Collection` retains every song it owns; the
      master library and built-in smart playlists never make tracks immortal.
- [x] Make audio compatibility destination-specific: stock MA446 plans omit and
      explicitly report anything outside validated MP3 or stereo PCM16/44.1-kHz
      WAV, while Rockbox/generic exports retain original high-resolution files.
      Added after a 24-bit WAV reproducibly rebooted the physical stock iPod.
- [x] Complete the physical HOUSE format-guard acceptance: all six included
      safe tracks played without a firmware reboot; `Loot` and `DONTTRUSTME`
      remained omitted.
- [x] Complete real-MA446 playlist-delete/reconnect acceptance through a clean
      zero-track/zero-user-playlist state, including two checked playlists
      deleted successfully in one guarded operation.
- [x] Physically delete the Apple Videos system index. The first pass exposed a
      real Apple shape with an ordinary-MHLP smart alias and canonical MHSD-5
      record under different IDs; the corrected snapshot selects the canonical
      ID and the planner removes both representations together. The retry kept
      all 73 tracks/media files and every protected smart list. Later syncs still
      parse no Videos representation. On-The-Go is firmware UI.
- [x] Complete the deliberate overnight stock-firmware soak, then reconnect
      read-only and capture the final 100-track stock state off-device before
      installing Rockbox.
- [x] Music writes to + reads from the real Rockbox iPod. The controlled frozen
      track A/B proved the exact original freezes and both picture-free payloads
      play.
- [x] M3U8 works on-device with drive-root-absolute paths, exact membership,
      and zero-padded filenames that preserve applied order in Rockbox.
- [x] External `cover.jpg` renders in the active theme/WPS; the verified
      picture-free A/B variant displayed its curated 500×500 cover.
- [x] Add the Rockbox-side media/art safety prerequisite: managed covers resolve
      to a separate RGB baseline-JPEG profile, while
      embedded pictures are removed only from a verified disposable device
      payload. Container audio essence and technical properties must match, the
      canonical source remains byte-identical, large staging clears after each
      batch, and an export-profile drift marker repairs older managed copies.
      Local real-format validation passes MP3, WAV, and M4A; physical replay of
      original/picture-free/external-cover `Take a Trip` variants proved the
      17.4 MB APIC was the freeze trigger on the target hardware.
- [x] Right-size only disposable Rockbox cover derivatives from the initial
      500×500 canary to 160×160 playback and 116×116 home-screen RGB BMPs,
      exactly matching AdwaitaPod Dark Simplified's WPS/SBS viewports. Canonical
      JPEGs and MA446 stock ithmb artwork remain unchanged.
- [x] Match the installed theme's 8-bit paletted BMP representation for both
      Rockbox viewports. The exact same dimensions/lookups remain, but each
      managed cover is roughly one third the former 24-bit payload size; the
      profile bump replaces every owned Rockbox cover without touching curated
      JPEGs or stock artwork.
- [x] Make external art theme-independent without giving up exact fast paths.
      SoundBox reads bounded square `%Cl` requests from the active WPS/SBS,
      retains exact sizes already managed for prior themes, and also writes a
      lightweight dimensionless 160×160 fallback. NightPod's official 130×130
      WPS and 30×30 SBS requests are parsed including optional placement flags.
      Theme changes add only missing disposable variants and never rewrite
      canonical or stock artwork.
- [x] Remove the remaining Rockbox display stalls: dimension-qualified per-track
      BMP names hit each theme lookup's first pass and avoid JPEG decode;
      shared WAV payloads carry Rockbox-native RIFF INFO title/artist instead of
      ignored ID3-in-WAV. The display fields are kept in one small early INFO
      list; every competing album/year/comment/vendor/embedded-ID3 field is
      removed from the disposable payload so Rockbox cannot surface a receipt
      or year as its title.
      Effective metadata now participates in schema-3 drift,
      so a title or artist edit marks Sync needed without a membership change.
- [x] Keep Rockbox's rebuildable tag database coordinated without a second
      database writer. Composite sync recovery-safely enables
      `tagcache_autoupdate` and tag-title playlist display while preserving all
      unrelated settings; Rockbox alone reconciles `database_*.tcd` on boot.
      The dashboard distinguishes an index that predates the latest SoundBox
      generation as `Rockbox restart pending`.
- [x] Keep full-library M3U8 catalogues playable by raising a stale Rockbox
      `max files in playlist` value to the documented 10,000 default while
      preserving any larger user value. The change remains part of the guarded
      composite config transaction and takes effect after Rockbox restarts.
- [x] Make repeated full-Collection planning genuinely incremental. Schema-3
      rows now retain canonical source stat/hash provenance and the per-file
      normalization profile; unchanged explicitly-profiled payloads take a
      stat-only fast path with no repeated audio-tag inspection or derivative
      regeneration, while changed source bytes, title/artist, or policy still
      take the exact verified path. The title/artist-only profile performs one
      verified cleanup of older narrower metadata profiles, then becomes
      stat-only. Drift compares the committed per-file profile explicitly, so
      that one-time migration cannot be hidden by unchanged playlist signatures
      or source stats. Rockbox cover planning uses the same persistent source-stat
      index principle; unchanged manifest/cache-identical BMPs require no device
      read, while rewrites/deletions retain exact live-byte verification. This
      removes thousands of no-op reads that reset the physical MA446 transport.
- [x] Keep the last verified Device/Songs/Playlists snapshot visible throughout
      iPod planning, sync, deletion, and post-write refresh. Successful reads
      replace all three tabs atomically; transient refresh failures retain the
      prior verified view instead of blanking it.
- [x] Make every disposable Rockbox payload expose exactly the effective
      SoundBox title and artist. MP3/AIFF/FLAC/MP4 user metadata and WAV
      INFO/embedded-ID3/BWF/XMP competitors—including Beatport receipts, album,
      year, and comments—are removed only from the verified derivative. Source
      audio and canonical artwork remain untouched. MP3 verification uses the
      exact tag-excluded MPEG essence rather than Mutagen's tag-size-dependent
      CBR duration estimate, preventing canonical ID3 replacement from falsely
      reporting that an unchanged audio stream was modified.
- [x] Make final-playlist deletion physically clean: an empty composite library
      can still plan/confirm cleanup of Finder `._*` and abandoned `*.sbpart`
      files below the Rockbox media/playlist roots, then prune only empty child
      directories after a successful transaction. AppleDouble is opportunistic
      cleanup rather than drift because Finder recreates it; only an abandoned
      `*.sbpart` independently requests repair.
- [x] Unify schema-3 Songs/Playlists device views across both firmware indexes.
      Type reports Both, Stock, or Rockbox from the manifest+iTunesDB baseline;
      verified M3U8 payloads preserve playlist order, Apple smart indexes stay
      internal, and stock-only legacy playlists cannot invoke composite cleanup.
- [x] Make iPod status and post-commit refresh honest. Sentence-case animated
      `Syncing audio...` keeps a separate audio-mutation `#/#`; zero-work counts
      are hidden, sync-to-zero counts removals, and Device/Songs/Playlists refresh
      before the serialized sidebar scan without requiring navigation. Status
      width follows only the active operation, and the commit prompt is a concise
      Both/Rockbox plus add/update/remove summary.
- [x] Keep dual playlists visible in Rockbox's non-recursive Playlist Catalogue.
      Managed M3U8 files live directly under `/Playlists`; visible names use the
      concise leaf, folder context appears only for duplicate leaves, and a
      zero-padded Rockbox-only prefix carries the manifest order. Migration
      removes older nested/folder-prefixed files transactionally while stock
      titles and manifest identity stay intact.
- [x] Make schema-3 orphan deletion independent of derivative-profile drift.
      Exact live orphans move into transient same-volume rollback storage and
      are discarded only after manifest-last success; a missing canonical cover
      no longer blocks an explicitly confirmed sync-to-zero.
- [x] The real iPod is already verified FAT32; Rockbox does not support HFS+,
      so no filesystem restore is currently required.
- [x] Rockbox DB refresh is coordinated without writing its binary cache:
      SoundBox enables automatic tag-cache updates, reports restart pending,
      and physical reboot acceptance corrected the stale four-letter display.
- [x] Exact playlist order holds on hardware. Rockbox catalogue nesting is not
      supported; SoundBox intentionally flattens hierarchy into playlist names.
- [x] Full-library no-write scale plan: 2,746 effective tracks across 94
      Collection/rekordbox/SoundBox playlists; 1,747 distinct stock-safe records,
      999 Rockbox-only files, 12,845 validated m3u8 entries, 98 sanitized
      payloads, zero missing/unsupported-by-both sources, and unchanged device
      inventory/database/artwork hashes. That historical dry-run preceded the
      successful full-Collection physical commit and incremental follow-up syncs.
- [x] Replace the initial-only composite boundary with additive schema-3
      generations and select a three-track physical canary (one shared safe,
      two Rockbox-only, three covers, zero omissions). The canary dry-run leaves
      all 2,470 device inventory rows and stock hashes unchanged; the later
      approved canary and full-library generations committed successfully.
- [x] Read-only layout classifier and capability boundary for Rockbox,
      stock-only, dual-boot, and generic USB.
- [x] Stock-iPod readiness preview plus a default-Cancel, two-phase MA446-only
      playlist writer; every other stock profile remains read-only.
- [x] Build the storage-efficient dual-library composite sync: one
      verified copy of each safe file shared by iTunesDB and Rockbox,
      stock-unsafe files in Rockbox only, filename-keyed external art for shared
      files, one schema-3 manifest, and coordinated stock/Rockbox rollback.
      Planning is no-target-write and commit requires explicit confirmation.
- [x] Add repeatable additive composite generations. Later playlist drops
      preserve previous snapshots, reuse exact stock/Rockbox payloads, repair
      only manifest-owned files from verified source derivatives, advance
      schema 3 last, and an
      identical repeat is a true no-op.
- [x] Add interrupted-generation recovery and a growable dual-iPod playlist
      queue. A remount preserves the last committed manifest; re-queued work
      adopts only exact fresh-plan path/size/SHA-256 matches, while a disposable
      Mac-side proof journal resumes verification across repeated USB resets.
      Each drop gets an immediate Yes/No prompt before planning. Confirmed jobs
      are identity-deduplicated FIFO work; jobs accepted while one operation is
      active coalesce into the next generation against newly committed media.
      The queue shares one cumulative entry counter/bar and one completion
      summary. Failure cancels the unseen remainder so a later drop cannot
      revive an old write.
- [x] Unify composite Sync/export/delete operation ordering. Every user action
      now receives its concise Yes/No before expensive work. Explicit Delete is
      authoritative: other available changes reconcile in the same operation
      and unavailable unrelated playlists retain their committed snapshots.
      Planning occupies 0–30%, guarded device work 30–80%, and final reads plus
      ownership release 80–100% of one monotonic progress bar; one genuinely
      terminal completion closes the action or queued batch.
- [x] Extend the original manifest drift/Sync contract across stock-only and
      schema-3 dual layouts. Rekordbox rename/move/order/membership/removal and
      SoundBox vetoes surface one format-neutral Sync-needed state; stock-only
      routes through its shadow-backed planner and dual routes through the same
      composite planner. Superseded Rockbox playlist files are recovery-backed,
      matching iTunesDB playlists reconcile, and exact managed orphans are pruned
      only after whole-device playlist reference counting.
- [x] Add validated schema-3 orphan cleanup. Managed media requires its
      ownership/path/size baseline and an exact reproducible source payload;
      art and playlist cleanup retain exact live-byte checks. Deletion rolls
      back on failure and the ownership manifest still publishes last.
- [ ] Add separately validated schema-2 ownership migration. Ordinary Rockbox
      export continues to refuse schema 3.
- [x] Preserve distinct logical source identities across identical bytes. The
      first full plan exposed 19 byte-identical source pairs, including six with
      different effective metadata; each now receives its own stock DBID and
      physical path while repeat playlist references to one source still share.
- [x] Guarded FAT32 dummy-volume export/verify/eject with pre-existing hidden
      data preserved.

**Manual-sourced facts** (official Rockbox manuals): baseline-JPEG required
(no progressive/multi-scan/RLE); art search order prefers EMBEDDED over
`cover.jpg` (now neutralized only in verified device copies); Max Playlist Size default 10k; on-device
perf ritual = Load to RAM + Directory Cache + DB init; Playlist Catalogue dir =
`/Playlists`.

### Phase 4 — Windows port ⬜ (a real port, not a recompile)
- [ ] `pyrekordbox` vs **rekordbox 7** (Sonny runs 6.8.5 on Mac; validate v7 on PC).
- [ ] Windows rekordbox DB path.
- [ ] Replace `diskutil` + `/Volumes/` detection/guards with drive-letter logic.
- [ ] Eject on Windows.
- [ ] Windows PyInstaller `.exe`.

### Phase 5 — Public fork ⬜ (gated on distribution decisions)
- [ ] Public build distinct from Sonny's personal build.
- [ ] Do NOT bundle yt-dlp — detect a user-installed one; opt-in, no silent scrape.
- [ ] Embedded-art reader (MP3/AIFF/FLAC/M4A) — 100% clean, ships to everyone.
- [ ] `features` flag + `build_app.sh --public`.
- [ ] Distinguish iPod/MP3 device vs plain USB; block plain USBs.
- [ ] Code-sign + notarize.

### Frontier — CDJ USB export
Reverse-engineer rekordbox's `export.pdb` so SoundBox can write CDJ-readable
USB sticks (Sonny performs on 4× CDJ-3000). Full plan + status + prototype:
**`docs/cdj-usb-export.md`**.

### Someday / v2
- [ ] Expand stock writing beyond MA446/MP3/WAV only after per-model hash/layout
      research, fixture round-trips, artwork validation, and separate physical
      acceptance. Do not generalize from the 5.5G result.
- [ ] Pursue complete **Rockbox Track ReplayGain**, including WAV rather than
      shipping a misleading MP3-only subset. Start upstream-first with a
      Rockbox WAV-metadata patch that recognizes a standards-compatible early
      RIFF/ID3 ReplayGain representation and feeds Rockbox's existing gain/peak
      parser; do not invent private tags without maintainer review. SoundBox's
      scanner must use a reference-validated ReplayGain 2.0 / ITU-R BS.1770
      gated track-loudness implementation at the standard -18 LUFS target plus
      true peak, checked against independent scanners, official vectors, and
      real MP3/WAV/AIFF/FLAC/M4A fixtures. Tags may be written only into the
      bounded, verified disposable device payload: canonical sources stay
      byte-identical and audio essence stays exact. Preserve the dual iPod's
      single shared WAV—no lossy conversion or duplicate Rockbox collection—
      and require physical playback/tag recognition on Rockbox plus unchanged
      stock-firmware WAV playback before enabling it. A privately carried
      firmware build remains an explicit later choice if upstream does not
      accept the parser support.

## Product decisions (don't re-litigate)

- **Rockbox uses one managed external cover.** Embedded pictures are removed
  only from the verified device payload after a frozen track exposed a 17.4 MB
  APIC as a metadata/memory hazard. Physical A/B reproduced the freeze only
  with that embedded picture; both picture-free copies played and the external
  cover rendered.
  Canonical source audio remains byte-identical; stock artwork continues through
  its separate ArtworkDB/RGB565 path.
- **Personal-edit tags are NOT stripped** from search — a coverless own-edit is
  Sonny's visual cue for cleanup.
- **Two-plane deletes:** Delete-from-Collection (global hide) vs
  Remove-from-Playlist (per-pid veto). Both drift devices; rekordbox never changes.
- **Unknowns-suggester rows UNCHECKED by default** (streaming list must never arm
  an accidental accept); merge-dialog rows stay pre-checked.
- **Imported audio referenced in place** — never copied or re-tagged.
- **Device filenames = SoundBox effective titles**, always.
- **The connected layout selects the exporter.** A stock-only supported iPod uses
  the guarded stock adapter; Rockbox-only/generic FAT32 uses filesystem export;
  an MA446/FAT32 layout with both markers uses only the composite planner.
- **Dual-library support is single-copy and composite.** Stock-safe files
  live once beneath `iPod_Control/Music`, are indexed in iTunesDB, and may also be
  referenced by Rockbox playlists. Rockbox-only files live once beneath `/Music`
  and never enter iTunesDB. One planner/schema-3 manifest owns both sets. Do not implement
  dual boot as two independent exporters or duplicate the full library. Apply
  the current MA446 MP3/stereo-PCM16/44.1-kHz-WAV validator only to the stock
  subset; capability decisions for the Rockbox set belong to its own profile.
- **Apple Videos is the only deletable stock smart index.** It deletes no media.
  Music, Movies, TV Shows, and Audiobooks remain protected; On-The-Go is generated
  by firmware rather than stored in iTunesDB.
- **Rekordbox analysis is read-only.** Imported grids/waveforms seed V5;
  live-DB cues refresh into V5 until SoundBox claims cue ownership. Rekordbox
  files are never edited in place and SoundBox-owned edits affect only V5.
- **Load starts at the first valid imported beat, else `0:00`.** Double-click
  is the only browser load gesture and loads paused; Space only toggles the
  loaded deck; C is momentary CUE. Session CUE remains ephemeral until a
  separate persistence design is explicitly approved.
