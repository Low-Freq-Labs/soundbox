# SoundBox roadmap

Living to-do. Work top-down. The Rockbox iPod remains the hardware priority;
player work may continue in parallel when it is isolated from export safety.
For the live test count, trust `./check.sh` output—not any number written here.

## Current status (2026-07-31)

- **CORE LIBRARY/EXPORT COMPLETE + CURATION MONTH COMPLETE.** Every track title
  cut/cleaned, every cover art eye-verified. Engine is mypy-clean,
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
  2026-07-30” and `docs/HANDOFF.md`.
- **SOURCE-ADAPTER BOUNDARY SHIPPED.** `soundbox.library` now owns the
  provider-neutral `Track`, `Playlist`, and `Library` model. The live
  rekordbox DB and XML readers are concrete read-only adapters into it;
  curation, cache, sync, workers, and tests no longer import their domain
  types from an XML parser. Source identity, cues, and optional lazy-analysis
  paths are normalized, XML `KeyType=1` playlists are converted to canonical
  track IDs at the edge, and V4 instant-paint snapshots remain readable.
  There is deliberately no speculative Serato/Traktor registry before a
  second real source establishes the necessary interface.
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
  The exact Apple Videos smart index is now exposed as a narrowly deletable
  System playlist in the installed build; that single physical delete/reconnect
  check remains. On-The-Go is firmware-generated and absent from iTunesDB.
  Current dual-boot routing stays fail-closed: a `.rockbox` installation selects
  Rockbox export and leaves the stock database read-only. Never erase, bulk-clean,
  or guess the target.
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
  models, checksum families, and dual-boot stock databases fail closed. The
  installed build completed its first guarded physical database commit,
  stock-firmware playback/skip session, reconnect capture, recovery proof, and
  second zero-write plan. `/.rockbox/` wins operational routing even beside a
  valid MA446 database, so remembered stock identity cannot activate the wrong
  exporter. The approved future dual-library mode is a new single-copy composite
  planner, not current behavior. See `DEVICES.md`.
- **STOCK IPOD DEVICE SURFACE SHIPPED AND INSTALLED.** Selecting a stock-only iPod
  now opens header-level Device, Music, and Playlists tabs populated by an
  asynchronous read-only database snapshot. General identity/storage/database
  health, indexed metadata, playback totals, recovery points, and exact ordered
  user-created playlists are visible without exposing raw control folders; the
  exact Videos smart index is also visible as a deletable System playlist while
  the library root and all other built-in smart lists remain internal. MA446 confirmation
  persists by library persistent ID + volume UUID and migrates from successful
  SoundBox recovery evidence; rewrite-volatile DatabaseID is never trusted.
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
| Cover art | 8.5 | accuracy-first auto (0 wrong art), manual picker, derivation; ceiling = SoundCloud search + unreleased tracks with no art anywhere |
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
filename-stem fallback, alias/merge window, uploader-consensus suggester);
app.py split into modules; UI-layer tests; rotating + full .zip backup/restore;
graveyard; title-cleanup named rules; baseline-JPEG art enforcement.

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

### Phase 3 — iPod validation 🟨 (real stock hardware active; Rockbox still pending)
Rockbox iPod ≈ a USB stick; getting music written + readable is 80% done. To
validate on-device:
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
- [ ] Reconnect once and physically delete the Apple Videos system playlist.
      The installed UI exposes it; the hermetic plan proves zero track/media
      deletion and preserves every retained smart rule. On-The-Go is firmware UI.
- [ ] Music writes to + reads from the real Rockbox iPod.
- [ ] m3u8 works on-device (drive-root-absolute paths; expect maybe a tweak).
- [ ] `cover.jpg`-per-folder renders (baseline-JPEG already enforced + cache
      migrated — pure on-device check; theme/WPS must support album art).
- [x] The real iPod is already verified FAT32; Rockbox does not support HFS+,
      so no filesystem restore is currently required.
- [ ] Rockbox DB refresh workflow after dumping files.
- [ ] Exact playlist order + folder nesting hold.
- [ ] Scale test (~2900 tracks) — no choke.
- [x] Read-only layout classifier and capability boundary for Rockbox,
      stock-only, dual-boot, and generic USB.
- [x] Stock-iPod readiness preview plus a default-Cancel, two-phase MA446-only
      playlist writer; every other stock profile remains read-only.
- [ ] Build the approved storage-efficient dual-library composite planner only
      after Rockbox hardware acceptance: one copy of each safe file shared by
      iTunesDB and Rockbox, stock-unsafe files in Rockbox only, one unified
      ownership manifest/rollback transaction, and no duplicate collection.
- [x] Guarded FAT32 dummy-volume export/verify/eject with pre-existing hidden
      data preserved.

**Manual-sourced facts** (official Rockbox manuals): baseline-JPEG required
(no progressive/multi-scan/RLE); art search order prefers EMBEDDED over
`cover.jpg` (accepted — see decisions); Max Playlist Size default 10k; on-device
perf ritual = Load to RAM + Directory Cache + DB init; Playlist Catalogue dir =
`/Playlists`.

### Phase 4 — Windows port ⬜ (a real port, not a recompile)
- [ ] `pyrekordbox` vs **rekordbox 7** (Sonny runs 6.8.5 on Mac; validate v7 on PC).
- [ ] Windows rekordbox DB path.
- [ ] Replace `diskutil` + `/Volumes/` detection/guards with drive-letter logic.
- [ ] Eject on Windows.
- [ ] Windows PyInstaller `.exe`.

### Phase 5 — Public fork ⬜ (gated on iPod validation)
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

## Product decisions (don't re-litigate)

- **Embedded MP3/M4A art MAY win over `cover.jpg`** — accepted by design; don't
  nag. `cover.jpg` is the universal floor.
- **Personal-edit tags are NOT stripped** from search — a coverless own-edit is
  Sonny's visual cue for cleanup.
- **Two-plane deletes:** Delete-from-Collection (global hide) vs
  Remove-from-Playlist (per-pid veto). Both drift devices; rekordbox never changes.
- **Unknowns-suggester rows UNCHECKED by default** (streaming list must never arm
  an accidental accept); merge-dialog rows stay pre-checked.
- **Imported audio referenced in place** — never copied or re-tagged.
- **Device filenames = SoundBox effective titles**, always.
- **The connected layout selects the exporter.** A stock-only supported iPod uses
  the guarded stock adapter; any mounted `.rockbox` installation currently uses
  Rockbox filesystem export and leaves iTunesDB read-only.
- **Future dual-library support is single-copy and composite.** Stock-safe files
  live once beneath `iPod_Control/Music`, are indexed in iTunesDB, and may also be
  referenced by Rockbox playlists. Rockbox-only files live once beneath `/Music`
  and never enter iTunesDB. One planner/manifest owns both sets. Do not implement
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
