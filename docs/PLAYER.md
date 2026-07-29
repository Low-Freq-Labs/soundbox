# SoundBox player

Current as of 2026-07-29. This is the player handoff: shipped behavior,
architecture, cache ownership, non-regression rules, validation references, and
the next grid work. Read it with `ARCHITECTURE.md` and `GOTCHAS.md` before
changing the deck.

## Shipped behavior

The player occupies two compact lanes:

- Header: compact SoundBox wordmark, centered artist/title with three stacked
  autoplay controls, search, Settings.
- Deck: centered transport stack on the left, waveform in the middle, and the
  two-page cue panel on the right.

Transport controls:

- Previous/next track follows the active table/view order.
- Back/forward jump uses a persistent selector: 4 beats, 8 beats, 16 beats,
  4 bars, 8 bars, or 16 bars.
- Grid-aware jumps preserve the current offset inside the beat and follow
  Rekordbox's exact timestamps, including variable-tempo grids. Edge buttons
  disable when the requested jump is unavailable.
- CUE, populated hot cues, and populated memory cues share one gated-preview
  contract: press starts from that cue, holding keeps it playing, and release
  pauses and returns to that exact cue.
- Pressing Space/play while any cue is held latches the preview into normal
  playback. Releasing the cue then leaves it running; Space/play stops it at
  its current position, while pressing another cue begins a new momentary gate.
  Session CUE state is never written to Rekordbox or the audio file.
- The transport state is visually unambiguous: every cue-family press lights
  the white CUE label on an orange fill while PLAY remains an outline. Space
  promotes a held preview into transport playback, filling PLAY purple; release
  then clears only CUE. Paused transport is always a purple outline. The PLAY
  glyph becomes PAUSE only with that purple transport state; decoder playback
  during an unlatched cue preview continues to show the PLAY glyph.
- `C` is the dedicated keyboard equivalent of pressing/releasing CUE while a
  browser surface has focus. SoundBox claims macOS's shortcut-override phase
  and consumes repeat events, so holding C remains a clean momentary gate
  instead of opening the system accent-character panel. Text fields and
  Command-C retain normal behavior.
- Each loaded track has one orange, upward-arrow-only session deck-CUE marker
  in both the whole-track overview and scrolling waveform. It has no vertical
  stem through the audio. It initially follows the first-grid-beat load point.
  After a paused manual playhead move, exactly the next deck-CUE press relocates
  the marker without starting audio; following presses use normal gated preview
  until the playhead is manually moved again. Hot/memory cue presses never move
  it. The marker is separate from canonical cue data and is never persisted or
  exported.
- Session CUE is an explicit five-state machine: `unset`, `set`,
  `relocation_pending`, `previewing`, and `latched_playback`. Pausing ordinary
  transport or moving a paused playhead enters `relocation_pending`; the next
  press sets without preview and its release is inert.
- Play/pause and all transport/source changes use short gain ramps so starts,
  stops, seeks, and track replacement do not click.
- CUE and play/pause are equal-size circular controls.
- Header autoplay is mutually exclusive: blue up follows the frozen source
  playlist upward, green down follows it downward, and red `R` uses a shuffle
  bag so every playable track is visited before a new cycle and the cycle
  boundary cannot immediately repeat the current track. Browsing elsewhere
  does not change that source snapshot. Deliberately starting a track from
  another playlist replaces it. Manual pause/resume preserves the mode; only
  the native terminal playback edge advances.

Cue panel:

- `HOT CUE` and `MEM CUE` are separate pages with eight slots each, arranged
  as two columns by four rows.
- The cue grid starts level with the whole-track overview and fills the player
  height down to its page tabs; the separate elapsed/duration label was removed.
- Existing cues are imported from `djmdCue` in the read-only live database
  snapshot. Hot cues retain every canonical slot (including I–P) and memory
  cues retain chronological order; the calm panel displays A–H / the first
  eight memory cues while V4 and exporters keep the overflow.
- `djmdCue.Kind` has a reserved gap: A/B/C are 1/2/3 and D–P are 5–17.
  SoundBox translates that encoding explicitly; value 4 and unknown values
  are never guessed into a letter.
- Labels, loops, positions, and verified color information are preserved.
  ANLZ PCOB/PCO2 remains a fallback when no live database cue source exists.
- The loaded `Track.rekordbox_cues` rows are injected directly into the deck
  and override V4/ANLZ sidecars. Exact cue identity therefore does not wait for
  background waveform preparation; V4 still receives the same rows atomically
  for long-term SoundBox ownership.
- Populated slots emit separate press/release edges for gated playback. Empty
  slots are inert. No cue is written back.
- Whole-track and scrolling memory arrows are slightly wider than hot-cue
  stems and terminate flush on the waveform edge, fully covering a coincident
  red grid line.

Loading semantics are deliberate:

- Double-click is the only browser gesture that loads a track, and loads it
  paused. Previous/next deck controls can still advance the loaded track.
- Space only toggles play/pause for the already-loaded track. Browser selection
  is ignored, and Space does nothing when the deck is empty.
- A non-empty, successfully parsed Rekordbox beat grid is authoritative and a
  new track begins at its exact first timestamp. When no valid Rekordbox grid
  exists, SoundBox's constant-tempo fallback is anchored with beat 1 at
  exactly `0:00`. That decision is based on grid availability/provenance, never
  file extension. A loaded legacy fallback is zero-anchored for its first paint
  even if the incremental metadata migration has not reached that record yet.
- Rapid-fire source replacement is supported and tested.
- The centered now-playing title has no duplicate hover tooltip. Clicking its
  box clears a hiding search and returns browser focus to the exact loaded
  track by ID, preserving the current view when possible and otherwise falling
  back to the SoundBox Collection.

## Waveform interaction

The fixed cyan playhead sits over three waveform lanes:

1. A small, unzoomed whole-track RGB overview. Clicking or dragging it seeks
   anywhere in the song.
2. A ruler showing only the current `bar.beat` at the playhead, avoiding
   triple-digit label clutter.
3. A zoomed, scrolling RGB detail waveform with Rekordbox beat/downbeat lines.

The `−` and `+` controls change only the detail lane. The overview never zooms.
There are thirteen persistent scales governed by one UI/native zoom contract:

`24, 32, 48, 64, 92, 128, 184, 256, 384, 576, 864, 1152, 1536 px/s`.

The 24 px/s stop shows about 38 seconds in a 900-point lane. The 1536 px/s
maximum retains the established close-up appearance (roughly three beats
across the deck for a typical 140–175 BPM track). The native ABI returns its
applied scale, so persisted zoom, paint, grid translation, and scrub math can
never disagree about a silently clamped value.

Settings exposes three persistent waveform displays:

- `BLUE` uses the exact packed PWV3 stream when Rekordbox supplies one. Its
  three luminance bits select the blue/cyan edge while the renderer supplies
  Rekordbox's white center core. Its scrolling geometry is a high-rate signed
  min/max trace: low-frequency bins remain thin oscillating lines and dense
  bins widen naturally.
- `RGB` uses the exact packed PWV5 stream. Each row is one display color plus
  one compact height—not three band layers. PWV5 owns color and distant-scale
  amplitude. Its scrolling geometry uses the same signed min/max trace as
  BLUE, while song-anchored PWV5 color-cell boundaries remain crisp.
- `3BAND` uses the exact packed PWV7 display rows as three independent
  shapes. Rekordbox's band scales are applied separately and composited as
  blue lows, brown mids, white highs, and the measured cream/pale-blue
  membership colors.

All three streams are imported or derived in one cache preparation pass and
remain resident as separate native GPU pyramids. Switching the selector is
immediate and never recooks the track. BLUE and RGB combine their authored
150 Hz color with the same persisted signed minimum/maximum cells rather than
filling positive and negative envelopes back to zero. That shared geometry
keeps tonal passages as continuous oscillating traces and lets noisy passages
widen naturally; the modes differ only in their PWV3/PWV5 color compositor.
Both use the literal physical-pixel footprint. 3BAND is different: its three
PWV7 heights are the complete native geometry and retain their authored timing
without borrowing the signed trace. The packed stream stays on its 150 Hz cache
clock, while the scrolling lane begins at the paired-row peak level that
matches Rekordbox's visible 75 Hz close facets. Successive paired values are
connected linearly; holding each row as a rectangle creates a different coarse
staircase.

Main-waveform scrubbing is relative and precise:

- Mouse-down atomically captures the last Metal drawable whose presentation
  time has actually occurred, makes that exact source frame the relative-drag
  origin, freezes the visual preview, hard-mutes, and pauses native playback
  synchronously. The ordinary pause envelope is deliberately bypassed because
  it advances to its fade endpoint after the hand already owns the waveform. It
  never substitutes a newly predicted between-refresh position or the older
  coarse Python poll.
- Press/release without horizontal movement performs one authoritative seek to
  the exact held timestamp, then resumes only if playback was active before
  the grab. A paused deck remains paused.
- Relative movement begins continuously with the pointer. There is no
  three-pixel dead zone whose first update can appear as a small jump.
- Dragging gives live visual position feedback without repeatedly seeking the
  decoder.
- Release performs one authoritative seek and resumes only if playback was
  active before the drag.
- The main lane never walks or beat-snaps from a stationary grab. The
  whole-track overview remains an absolute click/drag navigation lane.

## Native playback boundary

`PlayerBar` owns the transport behavior; one native backend owns playback and
the scrolling waveform. There is no engine selector and no compatibility
playback path.

Files:

- `native/SoundboxDeck.mm` — CoreAudio playback and Metal renderer.
- `native/SoundboxDeck.h` — stable C ABI.
- `desktop/native_deck.py` — small Qt-facing adapter over the native C ABI.
- `build_native.sh` / `native/CMakeLists.txt` — arm64 macOS 14+ build.

Playback pipeline:

- `AVAudioFile` decoding runs on a background thread.
- Decoded stereo floats feed a lock-free ring buffer.
- The Audio Unit render callback is the authoritative sample clock. It never
  decodes, allocates, performs file I/O, enters Python, or takes a blocking
  mutex.
- `CAMetalDisplayLink` presents moving/interactive surfaces at the active
  display's negotiated cadence (120 Hz on the built-in ProMotion panel).
  SoundBox does not force 120 Hz onto a fixed-60 Hz or throttled display. A
  paused/static deck draws one dirty frame and then pauses the display link
  until state or size changes.
- The Qt waveform host synchronously commits its new point size to the embedded
  AppKit view and Retina drawable on each host resize. Generic child-widget
  resizes only defer background preparation; they do not wake Metal. Maximize,
  restore, and live resize therefore render the final geometry in the same UI
  turn instead of briefly stretching a smaller drawable.
- Python polls state at 8 ms only while playing, loading, or gain-ramping and
  drops to 125 ms while paused. Commands and app activation request an immediate
  authoritative snapshot; Python never repaints the native waveform. Ordinary
  coarse position publication cannot wake Metal through the preview API once
  preview is already inactive.

The CoreAudio presentation timestamps feed a continuous visual PLL. Device
clock drift changes visual velocity by a microscopic bounded amount; it never
changes position at an audio-callback boundary. Predicted positions retain
fractional source samples into Metal.

Qt Multimedia remains only as an analysis decoder for AAC/ALAC. If the native
deck cannot initialize, the library, curation, and export surfaces stay usable,
but playback controls are disabled and SoundBox reports the native failure.
There is no silent fallback whose timing or visuals differ from the tested
player.

## Analysis ownership and read-only Rekordbox import

V4 analysis is owned by SoundBox. For an existing Rekordbox library track, the
live DB snapshot supplies authoritative cue rows and its `AnalysisDataPath` is
resolved beneath Rekordbox's `share/` directory for analysis fallback:

- `ANLZ0000.DAT` / `PQTZ`: beat-in-bar, exact millisecond position, BPM.
- `master.db` snapshot / `djmdCue`: active memory/hot cues, loops, and labels.
- `ANLZ0000.DAT` / `PCOB`: fallback cue positions and types.
- `ANLZ0000.EXT` / `PWV3`: approximately 150 Hz BLUE waveform.
- `ANLZ0000.EXT` / `PWV5`: approximately 150 Hz RGB waveform.
- `ANLZ0000.EXT` / `PCO2`: fallback cue comments and explicit RGB colors.
- `ANLZ0000.2EX` / `PWV7`: approximately 150 Hz 3BAND shapes.

SoundBox then persists the complete canonical record independently; prepared
playback does not reopen Rekordbox analysis. Exact normalized grid and cue
fingerprints are refreshed into V4 through atomic manifest-only updates until
a future SoundBox edit claims ownership. Correcting Rekordbox's initial
automatic beat grid therefore reaches SoundBox on the next live-library
refresh without reanalyzing audio, and waveform payload bytes never change
during either metadata refresh.
SoundBox's audio analyzer supplies the ultra contour, derived BLUE/RGB/3BAND
fallbacks for tracks without complete Rekordbox detail, and the 24-band
spectral master. No player code opens Rekordbox files for writing. No SoundBox
operation updates `master.db`, ANLZ data, or source audio.

## SoundBox-owned caches

All derived caches are disposable accelerators under
`~/Library/Application Support/LowFreqLabs/Soundbox/`:

| Directory/file | Owner and purpose |
|---|---|
| `libcache.json` | Last read-only library snapshot for instant first paint |
| `track_analysis/*.sba4` | Canonical V4: checksummed Zstandard sections for source identity/provenance/export eligibility, full grid/cues, 150 Hz BLUE/RGB/3BAND display streams, 24-band spectral master, and ultra contour |

Each V4 record is atomically replaced, keyed by track ID plus audio identity.
Interactive loads validate only the requested section SHA-256; cache audits
also verify the complete compressed payload. A
changed/new track prepares only its own record. Storage-only cue/grid mutation
support is a dormant boundary for later work; no manual grid editor exists.

The native analyzer streams PCM in bounded memory, derives approximately
6144 peak windows/second, keeps a normalized 24-band master, and builds the
three 150 Hz display-mode fallbacks from overlapping second-order Butterworth
paths:
LP 100 Hz, HP 300 + LP 3000 Hz, and HP 1500 Hz. One-millisecond band peaks use
approximately 25/3/1 ms low/mid/high holds before a local per-column energy
transfer, overlap correction, green restraint, blue lift, and compact
quantization. When no valid Rekordbox grid exists, the analyzer derives a
constant-tempo grid from FFT autocorrelation and anchors beat 1 at exactly
`0:00`. The fallback path is NumPy-only and has no runtime JIT.
Qt/FFmpeg feeds the same analyzer for AAC/ALAC set rips. Export eligibility is
deliberately separate: only MP3, WAV, AIFF/AIF, and FLAC are future-CDJ
candidates; AAC/ALAC/M4A/MP4 are always player-only.

Waveform geometry follows the stereo midpoint, which preserves the
attack/decay contrast lost when the louder channel was allowed to inflate every
row. A 1/16 peak-channel safety floor retains genuinely wide or anti-phase
signal that cancels at the midpoint. The ultra stream stores independent
positive and negative peaks as little-endian uint16 plus signed per-cell
minimum and maximum as little-endian int16 under
`soundbox-polarity-range-u16-v4`. Mirroring one unsigned height cannot
reproduce RGB polarity, while two peak envelopes filled back to zero cannot
reproduce BLUE's oscillating trace. The compact overview remains a symmetric
maximum for its 28-point-high lane. The 150 Hz overview uses complete,
non-overlapping 1/150-second cells; it never reuses the wider spectral FFT
window, which previously looked ahead about 46 ms, rounded attacks, filled
decay valleys, and omitted the final seven rows.

Native upload applies a display-only `height^1.4` curve and 0.84 full-scale
ceiling plus a continuous low-end shoulder. This retains real shoulders without
the needle-like peaks caused by `height^2` or the swollen center body caused by
a linear map. The 1/64-height shoulder rises through a four-LSB knee and
resolves real -60 to -73 dB tails as a
physical-pixel hairline but evaluates to exactly zero at digital zero; it is
not a hard minimum-height rule. Packed PWV3/PWV5/PWV7 heights remain linear
because they are already authored display values. The cache remains linear.
Existing V3 peak records fall back to a compatibility interval until the
background worker atomically adds the V4 signed-range sections used by both
BLUE and RGB, preserving SoundBox-owned grid/cue data.

RGB rows use the same track timeline, but decoder compensation is
format-specific. Lossless PCM (WAV/AIFF/FLAC) has no correction. MP3 retains
the measured seven-row / 46.7 ms decoder correction. Applying that MP3
correction globally made lossless color arrive seven rows late, so a kick
could inherit the preceding clap/snare hue. `soundbox-rgb-filterbank-v2`
versions this corrected timing contract.

The zero-anchor contract has a one-time metadata-only migration for older V4
records. It reads only the small read-only DAT/PQTZ grid, never EXT/2EX
waveform/color sections. It then atomically replaces only the SoundBox V4
manifest while preserving the compressed ultra waveform payload byte-for-byte.

The background `WaveformFillWorker`:

- scans the library incrementally;
- keeps the currently loaded track in a persistent foreground-critical lane;
  that track bypasses playback, interaction, Low Power Mode, and bulk battery
  deferral, and its completed canonical color/detail hot-loads into the deck;
- yields bulk work inside the current file while playback, scrolling, resizing,
  or app focus transitions are active, then resumes 750 ms after interaction
  settles;
- batches Qt/FFmpeg PCM into bounded chunks so a long AAC/ALAC set cannot hold
  the Python GIL across the UI event stream;
- runs bulk work uncapped at background/utility priority on AC, budgets 45% of
  one worker CPU on battery, drops a backlog of at least 25 items to 20% below
  50% charge, and pauses only bulk preparation in Low Power Mode;
- imports grid/cues as seeds, copies complete PWV3/PWV5/PWV7 display rows
  read-only over the derived mode fallbacks, and refreshes live-DB cue metadata
  until SoundBox user data claims that field;
- uses a direct, bounds-checked PQTZ row reader to fingerprint current
  Rekordbox grids and migrate legacy grid metadata, so it never inflates generic
  Rekordbox objects or opens EXT/2EX waveform/color analysis just to determine
  grid ownership;
- uses a 16 MiB worker stack and a 5/7 GiB free-space pause/resume guard;
- keeps the discovery scan silent and reports `Preparing your library…` only
  after actual analysis or metadata work is found;
- can hot-load newly completed detail for the current track;
- is interrupted and joined during shutdown.

A prepared canonical cache hit is staged off the UI thread: the small manifest
positions transport first, the packed overview is decompressed, and the
BLUE/RGB/3BAND plus ultra sections are prepared together. All display pyramids
and ultra geometry are committed inside one native presentation transaction:
the display link skips incomplete grouped uploads, so a long set's first
visible waveform is already its final silhouette rather than a smaller
overview that visibly inflates. Selecting another resident mode never
re-uploads or recooks the payload. Native decode starts
before those reads. Every stage carries a monotonic track generation;
`begin_track(generation)` atomically clears the previous waveform, grid, cues,
session marker, and detail before the replacement source appears. Late stages
from an older track are rejected in both Python and native code. No phase
expands packed cache rows into nested Python lists.

Legacy V4 records with an audible height and zero RGB are repaired at load by
borrowing the nearest valid hue. New analysis clamps filterbank group-delay
sampling to the first valid color rather than writing black leading rows. Time
before `0:00` is deck background, true silence remains zero-height, and overview,
ultra detail, grid, and cues share one origin.

Exact imported PWV5 also has a separate runtime edge case: a row may contain
zero height and zero color even when SoundBox's independent decoded overview
and ultra envelopes prove that the source audio is nonzero. That row used to
leave the correct Metal contour present but black, which looked like missing
waveform. Before upload, SoundBox now gates on the overview height, borrows only
the nearest valid PWV5 color bits, and assigns the minimum representable PWV5
height as a color carrier. The canonical cache remains byte-for-byte unchanged,
true-silence rows remain zero, and the rule applies to every track rather than
one reference passage.

## Why the waveform remains stable

The following renderer rules are measured invariants, not style preferences:

- BLUE and RGB share persisted signed minimum/maximum detail and a
  literal-pixel interval trace; only their PWV3/PWV5 color compositors differ.
  3BAND uses only its three packed PWV7 heights and must never be clipped to
  the signed audio trace.
- Pyramid levels remain song-anchored. Exact display pyramids retain peak
  geometry and amplitude-weighted color. The higher-rate signed detail uses
  an in-memory integer
  divisor ladder
  (`1,2,3,4,5,6,8,9,13,...`). The dense ladder prevents a 4.2-cell physical
  pixel from being rounded by an 8-cell max bin. Blending adjacent lattices
  made them beat against each other while moving, so every frame still selects
  exactly one song-anchored level. There is no alternate wide/close geometry
  shader. A second
  RMS/translucent envelope was rejected because it reads as a ghost waveform
  rather than additional useful detail.
- Display color remains a compact ~150 Hz mode buffer rather than being
  duplicated into millions of ultra samples. BLUE/RGB use PWV3/PWV5 directly;
  3BAND uses measured low/mid/high PWV7 display layers with `0.40/0.43/0.40`
  vertical transfers. Its compositor uses the exact seven-color membership
  table instead of alpha-compositing three generic paints. Each pyramid is
  song-anchored, so
  adjacent colors converge instead of alternating at far zoom. For derived
  records, per-column normalization retains the real low/mid/high relationship
  instead of making weak independently normalized FFT bands falsely
  competitive. The 24-band master is stored independently and neither Metal
  nor Qt may remix it into display color. PWV5 RGB expands its compact channels
  with the visually measured `14 / 8 / 8.75` divisors; close rows use crisp
  cell ownership rather than cubic color gradients, and only PWV7 enters the
  layered 3BAND compositor.
- `color_model` versions the persisted RGB analysis without changing the V4
  container schema. Old records remain playable while the background worker
  atomically replaces one track at a time; each temporary record lives beside
  its target, and SoundBox-owned grid/cue rows survive the upgrade.
- Derive one footprint-matched LOD from source samples per physical pixel and
  select one peak level. The overview and main lane call the same sampler;
  high-rate detail refines geometry only, never timing or color.
- Print height and amplitude-weighted RGB onto a fixed, global song-coordinate
  attribute lattice, then translate those attributes. The lattice stores no
  alpha or vertical coverage, so it is not a pre-rendered waveform texture and
  the final drawable never refilters an already-antialiased edge. Every zoom
  uses four attributes per physical pixel and linear subpixel translation.
  The earlier one-attribute close mode preserved an identical raster after
  integer motion alignment, but its 25/26-pixel maximum-zoom steps made dense
  spikes visibly strobe and bend.
- **The LOD is footprint-matched and `kLodBias` must stay 0.** A physical
  pixel cannot display more than about one envelope value. Selecting a mip
  two levels sharper (`kLodBias = 2`) was tried on the theory that
  supersampling buys mip bias back as recovered detail; it does not. The
  surplus four-to-eight values per pixel become content above the pixel
  Nyquist, and that is per-frame flicker, not detail. Measured on a real
  track as the share of coverage energy above the pixel Nyquist, bias 2
  scored 0.062 mean / 0.147 worst against bias 0's 0.029 / 0.066 — worse at
  every one of the thirteen zooms, with the peak in the middle where the
  fractional LOD stranded the most unresolvable values under each pixel.
  The taps still earn their keep by feeding `profile_coverage` a dense
  profile to integrate; they simply must not buy undisplayable detail.
- **Ceil the level — at most one envelope value per physical pixel.** Mip
  levels are powers of two but the thirteen zoom steps are not, so flooring
  strands every other zoom at ~1.54 envelope values per pixel while its
  neighbours sit at ~1.03, and rounding still leaves up to 1.41. Anything
  above 1.0 is undisplayable surplus: it is the hairy comb along the contour
  that rewrites itself every frame. Measured on a screen recording of the
  real app, that comb was 4.7 pixels of column-to-column edge jump at the
  zooms Sonny called worst. Measured aliasing across all thirteen zooms —
  floor 0.029 mean / 0.066 worst, round 0.021 / 0.066, **ceil 0.012 / 0.032**,
  better at every zoom. Sonny chose ceil over keeping the extra sharpness
  after seeing those numbers.
- **Use one mip level, never a blend of two.** Levels are song-anchored
  maxima on power-of-two lattices, so level N quantizes every 2^N bins and
  N+1 every 2^(N+1). Blending scrolls both at the same speed with different
  quantization and they beat against each other. Continuous blending only
  ever protected zoom *transitions*, and zoom is thirteen fixed steps, so the
  level is constant for the whole of playback.
- **Use the stable B-spline through 384 px/s, then preserve the native close
  edges.** The B-spline removes mid/wide lattice corners where the display
  cannot resolve them. At 576 px/s and above, BLUE/RGB signed intervals use
  direct linear reconstruction. 3BAND linearly connects the authored
  paired-row PWV7 peak cells instead of exposing twice the source screenshot's
  corner density or holding each row as a rectangle. The prior
  monotone-Hermite close path rounded the straight-sided carrier peaks that
  distinguish the supplied Rekordbox views. The kernel choice depends only on
  the fixed zoom, never on playback phase.
- **At the five close stops, pace signed extrema in output space.** BLUE/RGB
  selects one conservative peak row from a four-physical-pixel footprint,
  producing about 0.20–0.23 cells per pixel. Exact extrema and linear facets
  remain; only adjacent peak knots too dense to stay visually stable are
  removed. This is the stricter close-family exception to the general
  one-value-per-pixel ceiling rule. It does not apply to 3BAND.
- Rebuild the small private attribute buffer only when analysis, zoom, Retina
  scale, or its guarded song-space margin changes. The buffer is deliberately
  wider than the drawable, and every recenter is computed from an absolute
  integer song coordinate, so overlapping regions are identical and no tile
  seam can enter the viewport.
- Rasterize final vertical coverage exactly once at each Retina drawable
  pixel. There is no moving RGBA texture, coverage resampling, accumulated
  frame, or refresh seam. Height and hue move together, while final edge
  antialiasing remains tied to the destination pixel. The scrolling lane uses
  exact geometric coverage at every zoom. Boosting 20% coverage to nearly 49%
  made saturated red/orange edges visibly pulse even at 24 px/s. The small
  overview alone retains the continuous cubic vividness curve for true
  subpixel peaks. This distinction affects only compositing, never height,
  hue, LOD, or detail.
- Integrate the fixed height/hue profile over exactly one physical pixel before
  final vertical coverage. Below 1152 px/s, five quarter-pixel boundary samples
  retain the accepted result. At 1152–1536 px/s, use the pixel endpoints plus
  every integer knot of the four-attributes-per-pixel lattice, weighted by
  their actual spans; this is the complete piecewise-linear profile and avoids
  a fractional-phase knot hiding between fixed probes. Premultiplied color uses
  the same spans.
  Store premultiplied RGB on the lattice so color energy and height are both
  piecewise linear and their trapezoid integrals are exact. Interpolating
  straight color and height separately before multiplying them introduces a
  phase-dependent quadratic edge signal that reads as a faint color pulse.
  This removes subpixel needles and edge pulsing without the multi-pixel
  softening of the retired B-spline.
- BLUE/RGB interval color must be conditioned on the portion of the signed
  interval that actually covers the destination pixel. The accepted continuous
  upper/lower boundary coverage remains unchanged. Within that coverage,
  clipped-linear moments integrate PWV5 color in premultiplied linear light;
  a horizontally hidden color sample can no longer tint a visible RGB edge.
  BLUE evaluates its white/cyan/blue radial transfer from the same local
  signed center, width, and coverage centroid, then accumulates that energy in
  premultiplied linear light. Averaging color independently from interval
  coverage creates an internal brightness/color tick even when the silhouette
  itself is stationary.
- **There is no temporal shutter. The integration window is the physical
  pixel and nothing else.** Rendered motion blur is correct for content the
  eye does not track. A DJ stares at the scrolling waveform and tracks it, so
  under smooth pursuit the waveform is stationary on the retina and any
  rendered blur is smeared onto it permanently — it destroys exactly the
  detail it was meant to protect, rounds every edge, and, because a grab
  freezes the last rendered frame, hands back a blurred still. On top of that
  the display's own sample-and-hold already supplies ~25 Retina pixels of
  persistence blur at 1536 px/s, which no software can remove and which a
  shutter only doubles. Taps stay flat weighted: a box across the pixel is
  exact area coverage and is what keeps a moving contour crisp.
- Filter final silhouette coverage—not raw peak height. Geometry and color are
  reconstructed from the same level and phase, preventing the contour from
  acquiring a separate translucent edge.
- The direct fallback path still integrates exact area under its tap profile,
  never the mean of the taps' individual coverages. The normal audio-detail
  path instead interpolates height/hue first and applies one continuous
  one-physical-pixel vertical coverage band at final compositing. In neither
  path may per-tap alpha be averaged or an already-rasterized edge be sampled
  again.
- Composite coverage through a continuous cubic ease-out. This keeps
  one-physical-pixel quiet passages vivid against the dark deck without
  increasing their amplitude or adding a second envelope, while avoiding the
  former nonzero visibility floor that made entering/leaving edge pixels flash.
- Address audio detail and compact color with integer sample anchors plus local
  float phase. Never multiply a large absolute float time back into a sample
  index.
- Address beat-grid vertices with integer milliseconds plus local phase.
  Absolute float-second subtraction loses precision late in a track.
- The PLL may change slope, never phase-step the visible frame. The removed
  0.15 ms callback correction could become almost half a Retina pixel at
  maximum zoom.
- Keep the Metal child view opaque and let it own waveform pixels. Reblending
  previous translucent frames recreates the original shimmer.
- A display-link target is a submission deadline, not proof that its drawable
  reached the screen. Presentation diagnostics and waveform grabs use
  `MTLDrawable.presentedTime`; zero-time dropped drawables are ignored.
  Latency is one frame, and a grab holds the newest fresh presented or already
  committed source frame so an in-flight drawable cannot land and then rewind.
  Moving and held frames share the same one-physical-pixel spatial footprint.

Cue markers share one native-renderer contract:

- hot cues use a fixed A–H identity palette (A pink-red, B blue, C green,
  D purple, E emerald, F orange, G deep blue, H yellow) and their exact slot
  letter in a thin black-outlined box; imported RGB remains preserved metadata
  for exporters;
- marker boxes sit directly on the overview/detail boundary and the cue-colored
  stem begins below the box, so no one-pixel color seam can show above it;
- memory cues are always red upward arrows, regardless of imported palette
  metadata;
- both identities appear in the whole-track overview and scrolling waveform,
  while the existing cue panel remains the direct seek surface;
- late cue-sidecar refreshes update the panel and both waveform renderers
  together without reanalyzing audio.
- the orange session CUE is an upward arrow with no waveform stem and is
  rendered from a separate ephemeral channel, so it cannot be mistaken for or
  serialized with imported hot/memory cues.

Transport state has one authority boundary. Gain completions are tagged with a
monotonic intent epoch, and the native snapshot owns actual playback/position.
Returning from another application synchronously rejoins the Qt buttons and
both waveform scales to that snapshot before the next focused command. Space
and C remain focus-only and never steal shortcuts from Ableton, Terminal, or
Rekordbox.

## Regression coverage and manual references

Automated coverage:

- `tests/test_track_analysis.py`: exact filter coefficients and RGB transfer,
  synthetic low/mid/high tones, 25/3/1 ms holds, calibrated row timing,
  chunk/cancellation invariance, unchanged ultra geometry, cache versioning, and
  user-data-preserving upgrades.
- `tests/test_native_deck.py`: real native load/play/seek/ramp/rapid replacement,
  cache/cue upload, same-turn host/view/Retina resize geometry, active display
  cadence, static display-link sleep, inactive-preview wake suppression,
  adaptive polling, continuous visual clock, atomic mouse
  holds/complete-surface commits, and source immutability.
- `tests/test_native_shader.py`: shader/source contracts for one continuous
  geometry/color LOD, amplitude-weighted RGB mips, coverage filtering, vivid
  thin-contour color, one-pass final-pixel rasterization, cue coordinate modes,
  integer/local-phase addressing, PLL correction, future presentation
  timestamps, and exact already-presented-frame holds.
- `tests/test_ui.py`: transport-only Space, double-click loading, keyboard CUE,
  cue-panel/overview/detail marker identity, activation resynchronization,
  stale-completion rejection, beat/bar jumps,
  exact native-anchored scrubbing, autoplay transition/shuffle semantics,
  overview, zoom persistence, rapid loading, preparation, and window state.

Dated validation on 2026-07-23:

- Full gate: 477 tests passed, one platform skip; offscreen reference render
  also passed.
- Three read-only real-library comparisons against existing PWV5 reference
  rows measured red correlations of 0.962–0.967, green 0.818–0.864, blue
  0.712–0.813, and 63.4–71.4% exact compact-channel agreement. SoundBox audio
  was analyzed independently; reference data was read only for validation.
- `BOOGIE T.RIO - Dear Weed Man (Ganja White Night Remix)` at 240 seconds,
  1536 px/s: installed renderer held 120.000 FPS, 8.333 ms maximum display gap,
  zero underruns.
- `HUZE - THE FLOORS HAVE TEETH (V2)` at 240 seconds, 1536 px/s: same result.
- Both source files remained byte-identical. Rekordbox data was read-only.

Dated unified-renderer validation on 2026-07-24:

- The bare and offscreen-render gates passed with 481 tests and one expected
  platform skip.
- A populated 900×148 native deck held 118.992 FPS at 24 px/s (16.667 ms
  maximum gap) and 120.000 FPS at 1536 px/s (8.333 ms maximum gap) on the
  built-in ProMotion display.
- The signed installed app activated the native deck, loaded 3,265 tracks /
  83 playlists, and reported zero preparation work. Curated state remained
  semantically identical apart from runtime settings; 3,247 V4 caches and
  3,122 artwork files remained present.

Historical song-tile experiment on 2026-07-24 (superseded):

The bounded tile held callback cadence but was later removed because its
presentation stage could either refilter coverage/hue or quantize translation.
These numbers describe that retired renderer, not the shipped invariant.

- The bare and offscreen-render gates passed with 483 tests and one expected
  platform skip.
- A populated 900×148 foreground native deck, including repeated incremental
  strip refreshes over five seconds, held 119.800 FPS at 24 px/s and 119.599
  FPS at 1536 px/s. The largest presentation-target gap was 16.667 ms at both
  scales on the built-in ProMotion display.
- Live 517→1183→640→901 point resizes kept the Qt host, AppKit child, and
  2× drawable widths synchronized on every sampled size.
- The signed installed app activated the native deck, loaded 3,265 tracks /
  83 playlists, and reported zero preparation tasks. Curated state, 3,247 V4
  records, and 3,124 art files remained intact.

Dated cohesive-surface/autoplay validation on 2026-07-24:

- The bare gate passed with 494 tests and one expected platform skip; the
  offscreen render also passed with the three-state autoplay control active.
- The real prepared `OF THE TREES (LIVE SET) @ DEF: UNDERGROUND` cache made its
  small manifest available in 37 ms and atomically presented its colored
  overview plus 26,570,650-sample ultra envelope in 240 ms warm. No intermediate
  coarse silhouette was presented and no analysis recook was required.
- Native integration tests held display cadence at both zoom extremes after
  grouped buffer commit and pinned the exact continuous-frame mouse hold.
- The signed installed app activated the native deck, loaded 3,265 tracks /
  83 playlists, retained 3,247 V4 records and 3,124 art files, and left the
  pre-install `library.json` semantically identical.

Historical nearest-tile experiment on 2026-07-24 (superseded):

Nearest sampling improved a capture residual but exposed discrete quarter-pixel
velocity steps to the eye. The direct final-pixel renderer replaced it.

- The bare gate passed with 494 tests and one expected platform skip.
- Consecutive real Metal screen captures used the prepared 26,570,650-sample
  `OF THE TREES` ultra envelope. At 92 px/s, removing the second linear tile
  filter reduced the median translation-aligned silhouette residual from
  7.45% to 5.84% while retaining the established coverage waveform.
- In the complete application, suppressing redundant inactive-preview wakes
  reduced the same capture residual from 8.79% to 7.16%. Estimated
  frame-to-frame travel tightened around the correct 184 physical px/s target
  from a 128–249 px/s 5th–95th-percentile range to 141–230 px/s. Screen
  recording is deliberately a visual-compositor test, not the display-link
  cadence test.
- The signed installed app activated the packaged native deck, loaded 3,265
  tracks / 83 playlists, reported zero preparation tasks, retained 3,247 V4
  records and 3,124 art files, and left `library.json` byte-identical. No cache
  recook was required.

Dated direct-renderer validation on 2026-07-24:

- The bare gate and offscreen-render gate passed with 494 tests and one expected
  platform skip.
- A populated 1800×190-point Retina native deck sustained the negotiated
  display cadence at both 24 and 1536 px/s with ultra detail active. The test
  renders each final drawable pixel directly and contains no translated
  intermediate waveform texture.
- Native mouse-hold coverage verifies already-presented-frame selection with a
  freshness fallback, fractional-millisecond drag/release preservation, one
  decoder seek, and no dead-zone jump.
- Same-turn Qt/AppKit/Retina resize coverage still passes, so direct
  rasterization does not reintroduce stretched or one-frame-small waveforms.
- The signed installed app loaded the real 26,570,650-sample `OF THE TREES`
  set, played through the direct renderer, paused on a stationary off-center
  grab, and resumed from that held position. Startup reported 3,265 tracks /
  83 playlists and zero preparation tasks; 3,247 V4 records, 3,124 artwork
  files, and every curated `library.json` key remained intact.

Dated physical-pixel integration validation on 2026-07-24:

- The bare and offscreen-render gates passed with 496 tests and one expected
  platform skip.
- Source comparison identified a concrete regression from the earlier stable
  renderer: the unified continuous-LOD rewrite had retained one center sample
  per output pixel but dropped its four horizontal midpoint taps. The repaired
  path integrates both adjacent LODs and amplitude-weighted RGB across those
  taps without restoring a wide/close branch or translated texture.
- A populated 1800×190-point Retina deck with ultra detail retained negotiated
  display cadence at both 24 and 1536 px/s. Same-turn Qt/AppKit/Retina resize
  coverage remained green.
- The prepared `DJ Snake - Middle (Twopercent Flip)` V4 record was exercised in
  the complete source application at multiple zooms. Waveform mouse-down now
  hard-mutes and pauses synchronously; release performs the only decoder seek
  and restores ordinary playback through its short click-safe fade.
- The signed installed app activated the packaged native deck, loaded 3,265
  tracks / 83 playlists with zero preparation work, retained 3,247 V4 records
  and 3,124 artwork files, and passed the same stationary playing-waveform grab
  on the DJ Snake track. The pre-install `library.json` stayed byte-identical.

Dated actual-presentation/close-zoom validation on 2026-07-24:

- The bare and offscreen-render gates passed with 497 tests and one expected
  platform skip. Cadence assertions now count drawable presentations and their
  real gaps rather than display-link submissions.
- A real-app recording of the prior grab path showed a mouse-down rewind of
  more than 90 physical pixels. On the signed installed build, the prepared
  `DJ Snake - Middle (Twopercent Flip)` waveform at 1536 px/s traveled 51–77
  physical pixels between screen-recorder samples immediately before contact,
  then changed by zero pixels at mouse-down. The first held-frame mean image
  difference was 0.010/255, confirming that the moving temporal footprint was
  preserved rather than redrawn as a thinner stationary waveform.
- The installed app activated its packaged native deck, loaded 3,265 tracks /
  83 playlists, and reported zero preparation work. The 3,247 V4 records and
  3,124 artwork files were unchanged; the protected pre-install
  `library.json` was restored byte-for-byte after interaction testing. No
  waveform cache recook was required.

Dated Rekordbox-grid refresh validation on 2026-07-26:

- The bare gate passed with 500 tests and one expected platform skip. Coverage
  models an old cache holding Rekordbox's initial automatic grid, applies later
  corrected PQTZ rows, and verifies that the compressed waveform payload stays
  byte-identical. A UI regression test verifies that a zero-task discovery scan
  never enters the `Preparing your library…` state.
- A read-only scan of the real 3,265-track library found six cached grids that
  differed from the current Rekordbox rows in 5.1 seconds. The installed signed
  app then loaded 3,265 tracks / 83 playlists and atomically refreshed seven
  grid manifests plus current cue changes with zero audio-analysis tasks.
  Rekordbox remained open and read-only throughout.
- The installed app retained 3,247 V4 records and 3,124 artwork files. Every
  curated `library.json` field remained semantically identical; only ordinary
  runtime window/column settings changed during the launch-and-quit checks.

### Temporal shutter tried and removed on 2026-07-27 (superseded)

A tent-weighted shutter spanning the whole presentation interval was built,
measured green, installed, and then **rejected on sight**. It is recorded here
so nobody rebuilds it.

The reasoning that produced it was wrong at the premise: it treated the
scrolling waveform as content the eye does not track, and reached for motion
blur to kill judder. A DJ tracks the waveform. Under smooth pursuit the
waveform is stationary on the retina, so rendered blur is painted onto a
stationary retinal image and never averages out. Sonny's report was exact —
"detail gets blurred while scrolling by", "rounding edges", "expanding and
blurring when it starts and then gets clean when it stops", and worst, a grab
froze a blurred frame because the hold preserved the moving frame's shutter.
Widening it from the original 180-degree box made all of this worse, not
better. The correct reading of the same physics is the opposite: the display's
sample-and-hold already imposes the persistence blur, and the renderer's only
job is to put maximum real detail at the exact right position each frame.

### Negative-LOD close-zoom experiment on 2026-07-27 (superseded)

Reported symptom: at maximum zoom the moving waveform looked staticy, and the
contour appeared to step across the screen rather than glide. Both were
reproduced as arithmetic before any change was made.

This was an intermediate build, retained as experiment history rather than a
current renderer contract. It concluded that more raw detail was the fix and
set `kLodBias = 2`; the later full zoom-ladder recording showed the added
  above-Nyquist content shimmering at middle zooms. The current rule is the
  opposite: `kLodBias = 0`, ceiling LOD, one mip, and a fixed song-space
  height/hue attribute lattice.

- **The experiment treated the renderer as double-filtering.** It selected the nominal footprint LOD
  *and* integrated the physical pixel on top of it. At 1536 px/s with a
  6300 bins/s record, one physical pixel spans 2.05 bins, so the nominal LOD
  is 1: the surface was drawn from two-bin maxima, one value per physical
  pixel, critically sampled. Every sub-pixel phase change therefore rewrote
  the contour — the crawling static. Eight taps buy about two mip levels of
  bias, so `kLodBias = 2` now yields four real bins under every pixel at
  24/92/384 px/s (a 4.0x detail gain at each) and 2.05 bins at 1536 px/s,
  where it floors at level 0 — the raw analysis, a 2.1x gain and the most
  detail that exists.
- **The position path was audited and cleared.** `predictedFrame` and
  `setUniformPosition` carry double precision end to end and split to integer
  index plus float phase, so nothing quantizes translation.
- **Per-frame positional stability was measured, not assumed**, by sampling
  the frame attached to each presented drawable while playing a real library
  track (`NGHTMRE & Subtronics - Nuclear Bass Face`, 1229760 detail bins at
  6300/s) from 0:30 on an 1800x260 deck. Perfect 120 Hz motion on 44100 Hz
  audio advances 367.5 audio frames per displayed frame; the measured median
  is 367.5-368.0 with a 5th-95th percentile spread of 1.0 frame — **0.3% of
  travel, about 0.07 Retina pixels at 1536 px/s, at every zoom.** There is no
  positional shake. Synthetic periodic waveforms were abandoned for this
  measurement because they are materially easier than real music.
- `sb_deck_presentation_error_*` compares `presentedTime` against the target
  each frame was predicted for: single-digit microseconds at every zoom (max
  zoom mean 5.1 us, stddev 0.7 us) — about 0.02 Retina pixels. Occasional
  whole-frame drops show as an 8.33 ms outlier and a 16.67 ms `max_gap_ms`,
  but there is no systematic lead or lag, so the prediction and PLL were left
  untouched. Caveat: the residual is small enough that `presentedTime` may
  simply echo the scheduled target; either way there is no actionable signal.
- Cadence on the real track after the change: 118-120 fps across
  92/384/1536 px/s. Eight taps at a lower LOD cost less than the retired
  48-tap shutter.
- The suite went from 500 to 506 passing. Four pre-existing shader contracts
  were rewritten because the design they encoded was retired:
  `test_high_velocity_motion_extends_the_same_pixel_footprint` became
  `..._keeps_the_physical_pixel_footprint`, the two continuous-LOD tests now
  assert `biased_lod`, and the physical-pixel integration test asserts the
  generated tap form. `test_no_temporal_shutter_blurs_the_moving_waveform`
  now fails the build if any shutter symbol returns.

### Dropped frames in the real app on 2026-07-27

The renderer work above was measured green in an isolated deck harness while
Sonny still reported shaking in the running app. He was right, and the harness
was the problem: a bare `QWidget` host with hand-uploaded analysis held
118-120 fps while the real `MainWindow` ran 107-113 on the same machine and
build. Nothing in the harness was connected to `positionChanged`, so the
actual cost was invisible to it.

- Instrumenting the real `PlayerBar.player` while playing a real library track
  at 1536 px/s showed **15-37 lost displayed frames per three seconds**. At
  that zoom each loss is a doubled 51-Retina-pixel scroll step. That is the
  shake, and it is not a shader problem.
- Because run-to-run fps varies by 5+ on this machine, the cost was attributed
  with **paired alternating A/B rounds inside one process**. Five rounds,
  consistent every round: disabling `_update_transport` was worth **+8.0 fps**
  (106.9 -> 115.0) while `set_visual_position` and `wave.set_timing` were
  worth nothing (-0.4 and -1.0).
- Cause: the 20 Hz position path called `_update_transport()` unconditionally,
  which called `cue_panel.set_loaded()` unconditionally, which ran `_refresh()`
  over all sixteen cue buttons — allocating a `QPixmap` and `QIcon` per filled
  slot — twenty times a second, for state that had not changed. `set_loaded`
  is now idempotent and `_update_transport` early-outs on an unchanged
  signature, with `_sync_deck_button_states()` deliberately left outside that
  guard because a cue press changes it alone.
- Result: **106.9 -> 118.0 fps** at maximum zoom in the real app, and removing
  the transport work now changes nothing (+0.5), so the cost is gone.
- `WaveformBar._repaint()` was added in the same pass: `paintEvent`
  early-returns while Metal owns the pixels, so every Qt repaint there
  composited the window for nothing. This was a real cleanup but **not** the
  cause; the A/B is what separated them.

### Visual-only contour follow-up on 2026-07-27 (B-spline, superseded)

Sonny confirmed that the remaining report is entirely visual: wiggly waveform
ends, color pulsing, and uneven-looking motion. There have never been audible
drops. The supplied 69.9-second 3600×518 screen recording walks the full zoom
ladder through 1536 px/s. Although its stream declares 120 fps, it contains
3958 frames (56.6 fps average), mostly 16.67 ms holds plus 482 25 ms holds, so
it is evidence of the artifact but not a faithful measurement of the live
120 Hz presentation cadence. Sonny sees the same visual artifact without
recording.

Song-locking frames from that capture separated color data changes from moving
edge coverage: center RGB was stable (median change about 1.3/255, 95th
percentile 3.8/255), while the decoded contour varied much more at maximum
zoom. Replaying the exact read-only V4 cache for that track showed that
straight two-point interpolation left sample-lattice corners moving through
the contour. Positive four-point cubic B-spline reconstruction reduced the
stabilized motion residual at every one of the thirteen zooms, by about
10–40%, including 16.4% at 1536 px/s. Its eight midpoint taps use the same 32
sample reads as the former sixteen linear taps. The change is spatial only:
audio, transport, the presentation clock, and the no-temporal-shutter contract
are untouched.

The bare and render gates passed with 512 tests and one expected platform skip.
The signed app was installed and relaunched with the packaged native deck
active, 3,265 tracks / 83 playlists loaded, and zero preparation tasks. All
3,247 V4 records and 3,124 artwork files remained present. Curated
`library.json` fields matched the protected pre-install copy exactly; only
ordinary runtime window/column geometry changed during quit and relaunch.

### Fixed song-space attribute validation on 2026-07-27 (nearest close mode,
superseded)

The paired follow-up recordings made the remaining distinction concrete.
SoundBox's 70.08-second 3600×582 capture contained 3,975 frames (56.7 fps
average), while Rekordbox's 75.02-second 3600×760 capture contained 4,248
frames (56.6 fps average). The recorder therefore still could not judge live
cadence, but translation-aligning maximum-zoom silhouettes could judge whether
each renderer redrew its contour. The installed B-spline SoundBox build changed
by about three physical pixels at the edge after motion was removed; Rekordbox
translated an effectively fixed raster. The B-spline also visibly removed too
much close-zoom detail.

The replacement keeps geometry and RGB as attributes on a fixed global
song-coordinate lattice. At 384–1536 px/s one attribute represents one
physical pixel and the raster translates in exact whole-pixel steps; slower
zooms use four attributes per pixel and subpixel interpolation. The buffer is
recomputed only for analysis/zoom/Retina-scale changes or after the playhead
crosses half its guarded margin. It stores no coverage: final one-pixel
vertical antialiasing is evaluated once in the drawable fragment.

A floor-mip prototype recovered still more texture and was **rejected on
sight** when the real track immediately showed a nasty hairy/wiggly contour in
motion. The shipped balance retains the footprint-safe ceiling mip and uses
linear reconstruction instead of the soft B-spline. On the exact read-only
`XIII - BAD LUCK CHARM (FINAL v4)` V4 record, a 12-second maximum-zoom pass
presented 1,424 drawables at 118.91 fps. Twelve 120 Hz-equivalent frame grabs
moved by the expected alternating 25/26 physical pixels; after that translation
was removed, their binary silhouettes differed by only 0.027% on average
(0.039% worst), roughly two orders of magnitude below the supplied SoundBox
recording's 2.5–2.8% residual.

The dated full gate passed with 515 tests and one expected platform skip. The
signed installed app activated the packaged native deck, loaded 3,265 tracks /
83 playlists, and scheduled zero preparation work. All 3,247 V4 records and
3,124 artwork files remained present. Every curated `library.json` field
matched the protected pre-install copy; only ordinary runtime geometry
settings changed. No source audio, Rekordbox data, or waveform cache was
rewritten.

### Full-ladder pixel-integration follow-up on 2026-07-27

Sonny's next 60.93-second 3600×582 recording exercised all thirteen zooms on
`skantia - Yoyo Bop (Original Mix)`. Its declared 120 fps stream contained
3,447 frames (56.6 fps average), again making it a visual artifact reference
rather than a live cadence measurement. Motion-aligned edge error remained
comparatively controlled through the first five levels, rose through the
middle, and peaked near 7.4 physical pixels in the dense 0:45–0:50 passage.

An exact read-only replay of the track's 1,416,960-bin V4 envelope crossed two
forced attribute-buffer recenter boundaries with essentially zero aligned
contour error. Recenter seams were therefore ruled out. The problem was the
one-attribute close mode itself: preserving a fixed raster did not make its
large whole-pixel motion perceptually stable when the contour contained
one-to-two-pixel spikes.

The renderer now uses four fixed song-space attributes per physical pixel at
all thirteen zooms. The fragment samples five quarter-pixel boundaries across
exactly one output pixel, integrates the piecewise-linear height profile for
coverage, and amplitude-weights RGB over the identical span. Coverage is never
stored or resampled. The result retains the ceiling-mip detail while replacing
whole-pixel close motion and center-sample needles with continuous subpixel
travel and the narrowest valid spatial antialias footprint.

The exact Yoyo Bop dense passage sustained 118.8 fps at 1536 px/s after the
change. A full visible pass through 24/32/48/64/92/128/184/256/384/576/864/
1152/1536 px/s remained between 117.1 and 120.0 fps. The dated full source gate
passed with 515 tests and one expected platform skip.

The signed candidate was installed for Sonny's hands-on visual test. It
activated the packaged native deck, loaded 3,265 tracks / 83 playlists, and
scheduled zero preparation work. All 3,247 V4 records and 3,124 artwork files
remained present. Every curated `library.json` field matched the protected
pre-install copy; only ordinary runtime settings changed. The replaced
nearest-close build remains in one temporary rollback directory.

### Targeted 24–384 px/s follow-up on 2026-07-27

The next 43.79-second 3600×582 Yoyo Bop recording intentionally started beyond
the affected range and walked inward only through 384 px/s. Its 2,475 frames
again averaged 56.5 fps. Measured travel identifies the shown levels as
24/32/48/64/92/128/184/256/384 px/s. Residual contour motion is concentrated
at 256 and remains visible at 384; the already-improved 576–1536 close range
was not part of the complaint.

The fixed attribute lattice and one-pixel profile integration remain unchanged.
Only its reconstruction kernel now follows the demonstrated boundary:
positive cubic at 24–384 px/s and sharp linear at 576–1536 px/s. The zoom is
fixed during playback, so the static kernel choice cannot pulse with phase.
On exact 10 Hz-equivalent grabs of the same read-only track passage, correctly
advanced frame pairs measured about 0.55 physical pixels of aligned edge error
at 256 and 0.38 at 384, versus roughly 5.25 pixels in the supplied recording.
The unchanged 576 path measured 0.72 pixels.

Live passes sustained 117.97 fps at 256, 118.25 at 384, and 119.25 at 576,
with presentation timing error remaining in the single-digit microseconds.
The dated full source gate passed with 516 tests and one expected platform
skip.

The signed targeted build was installed and relaunched with the packaged native
deck active, 3,265 tracks / 83 playlists loaded, and zero preparation work.
All 3,247 V4 records and 3,124 artwork files remained present. Every curated
`library.json` field matched the protected pre-install copy; only ordinary
runtime column state changed. The prior pixel-integrated build remains in one
temporary rollback directory.

### Targeted edge-color follow-up on 2026-07-27

The next 23.14-second 3600×582 Yoyo Bop recording showed only the remaining
issue range and advanced through 24/32/48/64/92/128 px/s. Motion-aligning the
captured silhouettes left them nearly rigid, with median clipped edge error
between roughly 0.4 and 1.1 physical pixels. The remaining visible motion was
concentrated in the brightness and hue of partially covered contour pixels,
not lost close-zoom geometry.

The fixed lattice now stores premultiplied RGB plus height. The final five
boundary samples integrate that premultiplied color directly and divide by the
identically integrated height. Both signals are piecewise linear, making the
one-pixel trapezoid exact and removing the former phase-dependent product of
separately interpolated straight color and height. Reconstruction kernels,
ceiling LOD, silhouette height, the one-pixel footprint, and close-zoom detail
are unchanged.

The dated full source gate passed with 516 tests and one expected platform
skip. The signed build was installed and relaunched with the packaged native
deck active, 3,265 tracks / 83 playlists loaded, and zero preparation work.
All 3,247 V4 records and 3,122 artwork files remained present. Every curated
`library.json` field matched the protected pre-install copy; only ordinary
runtime settings changed. The preceding cubic-kernel build remains available
in one temporary rollback directory.

### Full-ladder motion follow-up on 2026-07-27

The next 68.27-second 3600×582 Yoyo Bop recording covered all thirteen zoom
levels from 24 through 1536 px/s. Its declared 120 fps stream contained 3,850
frames, averaging 56.4 captured fps: 3,282 intervals were about 16.7 ms, 501
were about 25 ms, 18 were about 33.3 ms, and one reached 41.7 ms. Recording
holds therefore contribute visible horizontal steps, but they do not explain
the whole contour complaint.

After compensating each captured pair for its horizontal travel, median clipped
edge error stayed roughly 0.4–1.7 physical pixels through 24–384 except for a
2.4-pixel dense passage at 256. It rose to 3.5 at 576, 4.5 at 864, 2.7 at
1152, and 3.2 at 1536. That boundary exactly matched the return to linear
close-range reconstruction. Linear preserved the detail Sonny preferred but
left a slope discontinuity at every song-lattice sample, so those corners
moved through the raster as residual wiggle.

The 576–1536 path now uses monotone cubic Hermite reconstruction for height and
premultiplied RGB. It interpolates every real mip sample exactly, clamps each
span between its two endpoints, and sets extrema slopes to zero; unlike the
retired B-spline it does not average peaks away, and unlike linear
interpolation it has no moving slope corners. The 24–384 B-spline, ceiling LOD,
four-attribute density, exact one-pixel integration, and color
premultiplication remain unchanged.

Motion analysis also found isolated slow-frame/fast-frame pairs that cancel
over two captured frames. Native presentation tests continued to target actual
drawables within microseconds but occasionally observed one 16.7 ms
presentation gap on the variable-refresh display. Increasing the offscreen
attribute margin did not reliably improve that cadence and was reverted; no
speculative buffer or clock change shipped from that experiment.

The dated full source gate passed with 517 tests and one expected platform
skip. Close-range native passes kept actual drawable targets within
microseconds and reached 120 fps at 1536 px/s. The signed build was installed
and relaunched with the packaged native deck active, 3,265 tracks / 83
playlists loaded, and zero preparation work. All 3,247 V4 records and 3,122
artwork files remained present. Every curated `library.json` field matched the
protected pre-install copy; only ordinary runtime settings changed. The prior
linear-close build remains in one temporary rollback directory.

These are useful real-library stress tracks because they expose dense
high-frequency contours and late-track precision. Automated tests must remain
hermetic and must not depend on Sonny's personal files; the real-track
measurements above were run from a scratch harness that only reads the
existing V4 cache and audio.

### Remaining-artifact follow-up on 2026-07-27

The next 69.98-second 3600×582 recording again covered all thirteen zoom
levels. It contained 3,960 frames, averaging 56.6 captured fps: 3,391
intervals were about 16.7 ms, 495 were about 25 ms, 19 were about 33.3 ms,
and the longest was 33.3 ms. Those held capture frames explain some recorded
horizontal steps, but a five-second native pass also occasionally observed one
16.667 ms drawable gap at close zoom. The submitted target remained within
0.018 ms of the actual presentation. A measured latency-two experiment made
cadence worse at all three close test zooms and retained the doubled interval,
so latency one and the existing presentation clock remain unchanged.

The recording also exposed two independent color artifacts. Downbeat lines
were composited at 82% opacity after the waveform, allowing saturated green
content to alter the red timing reference; downbeats are now fully opaque
and ordinary beat lines now use the same opaque-overlay contract in gray. At
256 px/s and above, cubic
ease-out coverage was exaggerating small geometric changes at saturated
red/orange edges. The first candidate transitioned the scrolling lane from
that wide-zoom visibility curve to exact physical-pixel coverage from 184–256
px/s. The overview kept its vivid curve, and reconstruction, peak height, hue,
LOD, and the four-attribute close detail path remained unchanged.

The dated full source gate passed with 519 tests and one expected platform
skip. The signed build was installed and relaunched with the packaged native
deck active, 3,265 tracks / 83 playlists loaded, and zero preparation work.
All 3,247 V4 records and 3,124 artwork files remained present. Every curated
`library.json` field matched the protected pre-install copy; only ordinary
runtime settings changed. The preceding build remains in one temporary
rollback directory.

The next 53.36-second highlighted recording revisited the remaining passages
back and forth entirely at 24 px/s. It contained 3,024 frames, averaging 56.7
captured fps: 2,636 intervals were about 16.7 ms, 347 were about 25 ms, nine
were about 33.3 ms, and two early stalls exceeded 38 ms (one reached 158 ms).
Those holds add recorded steps, but the same contour pulse recurred during
ordinary 16.7 ms stretches. Because the first coverage correction began at
184 px/s, it could not affect the highlighted level. The scrolling lane now
uses exact physical-pixel coverage at all thirteen zooms; only the overview
keeps cubic vividness. Geometry, reconstruction, height, hue, and detail
density remain unchanged.

The exact-coverage build passed the full source and offscreen-render gate with
519 tests and one expected platform skip. The signed app was installed and
relaunched with the packaged native deck active, 3,265 tracks / 83 playlists
loaded, and zero preparation work. All 3,247 V4 records and 3,124 artwork
files remained present, and every curated `library.json` field matched the
protected pre-install copy. The immediate predecessor remains the single
active temporary rollback.

### Grid-edge and isolated-stop follow-up on 2026-07-27

The next 27.73-second 3600×582 recording deliberately traversed all thirteen
zoom levels and replayed the remaining problem passages. It contained 1,571
frames, averaging 56.7 captured fps: 1,377 intervals were about 16.7 ms, 186
were about 25 ms, and only one reached 41.7 ms. Grid spacing mapped the two
replayed contour passages to 64 and 184 px/s. The highlighted red downbeat did
not drift in song time; it remained exactly two physical pixels wide, but its
center moved only on integer Retina pixel boundaries while the waveform used
continuous subpixel reconstruction. That phase mismatch made the red reference
appear to step sideways even when both layers represented the same time.

Scrolling beat/downbeat rectangles now carry signed local line distance into
the fragment and use analytic physical-pixel box coverage. The geometry
includes a half-point fringe, so a one-point timing line moves continuously
instead of snapping as a hard two-pixel rectangle. Downbeats remain opaque red
and ordinary beats are opaque gray; analytic edge coverage is applied after
that color policy so both retain continuous subpixel motion.

The two isolated waveform stops use a deliberately smaller correction. At
exactly 64 and 184 px/s, height and premultiplied color receive a same-LOD
`[1, 2, 1] / 4` pass at half-physical-pixel offsets before the established
one-pixel profile integration. Narrow zoom-space tapers make its weight zero
at all eleven neighboring fixed stops. It does not choose a coarser mip,
change peak storage, or touch the 576–1536 px/s interpolating close-detail
path. A static nine-phase pass over the real skantia stress track at both
affected stops retained transient shape and showed continuous grid fringes
without reopening the close-zoom detail loss.

The candidate passed the full source and offscreen-render gate with 521 tests
and one expected platform skip. The signed app was installed and relaunched
with the packaged native deck active, 3,265 tracks / 83 playlists loaded, and
zero preparation work. All 3,247 V4 records and 3,124 artwork files remained
present; the curated `library.json` object matched the protected pre-install
copy exactly, including the 384 px/s runtime zoom. The immediately preceding
exact-coverage build remains the single active temporary rollback.

### Perceptual grid-coverage follow-up on 2026-07-27

The 24.74-second 3600×582 red-line-only follow-up contained 1,407 frames and
again traversed the zoom ladder. Lossless source renders showed that analytic
grid geometry had already solved position and energy: peak red stayed fixed
and total geometric coverage varied only 0.45%. The remaining pulse came from
the BGRA8Unorm display path. A two-physical-pixel box alternates between
`[0.5, 1.0, 0.5]` and `[1.0, 1.0]`; fixed-function blending treats those as
display-code values, so the equal-area phases differed by about 31% when
measured as SDR linear-light energy.

Only the analytic grid-line branch now encodes coverage with the scoped
`coverage^(1/2.2)` transfer before alpha blending. It changes neither the
signed line distance nor its two-pixel geometric width, and it cannot affect
waveform reconstruction, final waveform coverage, color, zoom, or transport.
A sixteen-phase lossless render over the same real skantia cache held the red
peak constant and reduced displayed-energy variation to 0.86%.

The correction passed the full source and offscreen-render gate with 521 tests
and one expected platform skip. The signed app was installed and relaunched
with the packaged native deck active, 3,265 tracks / 83 playlists loaded, and
zero preparation work. All 3,247 V4 records and 3,124 artwork files remained
present; curated `library.json` state and the 384 px/s zoom matched the
protected pre-install copy. The immediately preceding analytic-grid build
remains the single active temporary rollback.

### Whole-compositor follow-up on 2026-07-28

The next 49.34-second 3600×582 recording contained 2,789 frames at 56.5
captured fps. Of the measured intervals, 2,392 were about 16.7 ms, 353 were
about 25 ms, 31 were about 8.3 ms, and 11 were about 33.3 ms; none exceeded
33.334 ms. Grid spacing mapped the deliberate holds to 184 px/s at roughly
8–12 and 36–40 seconds, 64 px/s at 17–24 seconds, and 24 px/s at 27–30
seconds. The rest of the pass stepped through the surrounding ladder up to
the final 1,152 px/s setting. The repeatable contour/color pulse occurred
through ordinary capture intervals, so it was neither a stall nor a missed
drawable.

The line-only display transfer from the preceding build was a useful
diagnostic but not the complete fix. The scrolling waveform still mixed its
exact geometric coverage between display-sRGB code values in a
BGRA8Unorm target. Equal-area subpixel phases therefore retained different
linear-light energy—the same underlying mistake already measured on the red
line.

The Metal layer and both render pipelines now use BGRA8Unorm_sRGB.
Display-authored waveform and grid RGB is decoded in the final fragments,
waveform coverage and fixed-function grid blending occur in linear light, and
Metal performs the sole sRGB encode when storing the drawable. The clear color
uses the exact linear decode of `#0d0e12`. Full-coverage waveform colors match
the previous build with median zero and 95th-percentile one-code difference;
the background matches exactly. No height, hue reconstruction, mip, LOD,
attribute density, zoom, timing, or close-detail path changed.

Sixteen-phase lossless renders over the real skantia cache reduced
motion-aligned mean edge variation by about 18% at each deliberately held
level. Worst-edge variation fell about 27% at 24 px/s, 30% at 64 px/s, and
28% at 184 px/s. The analytic red-line peak remained fixed and its measured
displayed-energy span tightened to 0.59%.

The whole-compositor correction passed the full source and offscreen-render
gate with 522 tests and one expected platform skip. The signed app was
installed and relaunched with the packaged native deck active, 3,265 tracks /
83 playlists loaded, and zero preparation work. All 3,247 V4 records and
3,124 artwork files remained present, and curated `library.json` state matched
the protected pre-install copy exactly; only ordinary runtime settings changed
during relaunch. The preceding line-compensation build remains the single
active temporary rollback.

### Quiet-signal geometry upgrade on 2026-07-28

The Rekordbox comparison exposed two independent losses in the persisted
SoundBox envelope rather than a renderer defect. Geometry was formed from the
arithmetic stereo mean, so out-of-phase channel content could cancel, and the
remaining linear amplitude was quantized directly to uint8. On the real
skantia Yoyo Bop intro, channel-safe peaks remained roughly -40 to -54 dB in
rows the legacy cache encoded as zero.

That first upgrade used the `soundbox-channel-peak-u16-v1` geometry
model and little-endian `linear-u16le` height encoding at both overview and
ultra rates. Each analysis row takes the largest absolute sample from either
channel; spectral color and beat analysis retain their existing mono input.
True digital zero remains zero. A renderer-only exponent of 0.80 lifts ordinary
quiet geometry without baking a visual preference into the cache, so later
display tuning does not require another recook. A same-day comparison exposed
one still-darker, genuinely nonzero bridge at roughly -60 to -73 dB: its u16
rows were intact, but their projected height was below one tenth of a pixel.
A continuous low-end shoulder over the first four u16 values now resolves that
signal as a stable physical-pixel hairline. It evaluates to zero at zero and is
applied before song-space reconstruction, avoiding both a fake silence line
and a discontinuous moving edge. Legacy uint8 records remain playable and are
upgraded atomically by the normal prioritized background worker.

A scratch recook of the exact comparison track retained all 1,416,960 detail
rows and eliminated encoded-zero rows in each pictured quiet interval, where
the legacy cache lost between 0.53% and 2.94% of rows. The target display
produced roughly 1.85–3.03 physical pixels of median height in those passages.
Source audio, Rekordbox data, beats, cues, and import provenance were unchanged.

The upgrade passed the native build, full source gate, and hermetic offscreen
render gate with 526 tests and one expected platform skip. The signed app was
installed and relaunched with the packaged native deck active and 3,265 tracks
/ 83 playlists loaded. The exact Yoyo Bop record was recooked once and verified
complete before launch; the worker then found 3,245 remaining legacy analysis
records and began the one-time rolling migration under battery eco policy.
All 3,247 waveform records and 3,124 artwork files remained present, all
inspected containers were structurally valid, and the protected curated
`library.json` semantic hash remained exactly unchanged.

The same-day subpixel-tail follow-up required no cache change or recook. The
exact comparison cache remained byte-for-byte unchanged while the native and
Qt display uploads gained the continuous visibility shoulder described above.
That follow-up passed the full source and offscreen-render gate with 527 tests
and one expected platform skip. The signed app was installed and relaunched
with the native deck active, 3,265 tracks / 83 playlists loaded, all 3,247
waveform records and 3,124 artwork files retained, and the protected curated
state hash unchanged. The rolling migration had progressed to 3,195 remaining
legacy analysis records at that launch.

This channel-peak/display-0.80 presentation was superseded later the same day
by the transient-fidelity correction below. Its uint16/true-zero finding
remains valid; making the louder channel the primary silhouette and lifting it
with exponent 0.80 did not.

### Transient fidelity and lossless RGB timing on 2026-07-28

The exact `Sonny - High & Scared v13.wav` comparison made two remaining
analysis errors measurable. The louder-channel envelope inflated ordinary
stereo content, while the 150 Hz overview reused a 2048-sample spectral window:
at 44.1 kHz that looked ahead roughly 46 ms, rounded attacks, filled the black
space between decays, and shortened the record by seven rows. Separately, the
seven-row decoder correction proven necessary for MP3 had been applied to
lossless PCM as well, painting every WAV transient with color from 46.7 ms
late.

`soundbox-mid-peak-u16-v2` now uses peak absolute stereo-midpoint geometry with
a 1/16 peak-channel safety floor. This restores Rekordbox-like attack/decay
contrast while linear uint16 and the existing continuous shoulder keep
cancelled or very quiet real signal visible. The 150 Hz envelope now consists
of complete, non-overlapping time cells and includes the final partial cell.
The persisted amplitude remains linear; a renderer-only `height^2` curve
separates narrow attacks from their valleys without inventing silence or
discarding close-zoom samples.

`soundbox-rgb-filterbank-v2` makes the pipeline correction format-specific:
MP3 keeps its measured seven rows, while WAV/AIFF/FLAC and the Qt-decoded
player-only formats use zero correction unless their own evidence establishes
otherwise. On the exact reference WAV, the new cache has 24,580 overview rows,
matching PWV5 exactly; displayed-envelope correlation against the read-only
PWV5 heights is 0.957 with 0.038 mean absolute error, and RGB correlation is
best at zero-row offset instead of seven. Native 384 and 1536 px/s pixel
captures retain the repeated transient gaps and close oscillation detail. Both
model identifiers changed together, so every stale record receives one full
atomic recook rather than serial color and geometry migrations.

The source gate and hermetic render gate passed with 532 tests and one expected
platform skip. The signed app was built, safely installed, and relaunched with
the packaged native deck active. The 3,265-track instant snapshot and all 83
playlists loaded; 3,247 waveform records and 3,124 artwork files remained
present, and the protected `library.json` SHA-256 stayed
`d0eaba6a65a12e243921f0c09441dc58d173ba5338f33aa96ceee6a42aa3c0fd`.
The exact High & Scared cache was prepared and fully verified as both v2 models
before launch. The worker then found 3,244 remaining stale analysis records and
began the single combined rolling migration; twelve records were already v2
at the first post-launch audit. The source WAV remained byte-for-byte unchanged.

### Three-mode reference correction on 2026-07-28

The complete reference set is the six untouched PNGs under
`~/Desktop/rekordbox wave`: BLUE, RGB, and 3BAND for track ID `54231927`
(`Sonny - High & Scared (final master).wav`) and track ID `33638604`
(`Bad Decisions w SUMA (FINAL FR).wav`).

Pixel registration against the user-provided SoundBox screenshots established
that the compared SoundBox view was `1536` points/s, not `184`, and uniquely
identified the High & Scared source as track `54231927`; the other local
masters have materially different color/timing streams. Registration against
the untouched Rekordbox PNGs then established two reference scales: Bad
Decisions is `768` points/s and High & Scared is `1536` points/s. The fixed
harness centers them at the grid-derived `25597 ms` (bar 17.3) and `27000 ms`
(bar 17.2), respectively.

The first installed candidate from this investigation was rejected by the
exact side-by-side: drawing independent polarity maxima produced a hairy,
discontinuous RGB contour, while a 1 ms crest still filled both polarity
envelopes inward to zero and left a false center seam. Substituting PWV5 height
alone produced overfilled slabs. Outer-silhouette correlation remained above
`0.87` for both rejected filled models, proving that outline-only scores cannot
detect the obvious internal representation error.

BLUE required a different data model. Filling positive and negative peaks back
to zero cannot form Rekordbox's thin low-frequency trace. V4 therefore stores
the signed minimum and maximum of every high-rate stereo-midpoint cell as
little-endian int16. Metal renders each cell as a literal-pixel signed interval:
oscillating material stays a line and dense/noisy material becomes a band. This
requires one rolling cache migration. RGB now uses the same signed geometry as
BLUE and differs only in its PWV5 color; exact geometry in either mode appears
after that track receives its signed-range sections.

3BAND bypasses both audio-derived contours and uses its three native PWV7 layer
heights directly. The stored blue-low/amber-mid/white-high display layers use
`0.40/0.43/0.40` transfers, linear per-band edge reconstruction, and a measured
membership palette including blue
(`0/85/225`), amber (`255/166/0`), brown (`180/105/10`), cream
(`245/235/215`), pale blue (`210/220/250`), and white. The source cache remains
V4; imported rows are reused without recooking, while legacy SoundBox-derived
rows are permuted in memory.

`scripts/render_waveform_reference.py` is the visual acceptance engine. Its
`--corpus` mode loads the exact two source WAVs and canonical caches through the
real native Metal deck and emits all six aligned PNGs without opening
rekordbox, writing AppData, or starting playback. The source test verifies the
corpus identity and refuses to substitute a correlation score for rendering.

### Rejected shared-ultra investigation on 2026-07-28

Everything in this subsection through the next major heading is retained as
failure history. Claims of final visual acceptance, red/pink/blue 3BAND
identity, or shared-ultra geometry are superseded by the correction above.

The next exact `Sonny - High & Scared v13.wav` comparison established that
Rekordbox does not derive BLUE, RGB, and 3BAND from one recolored surface. Its
read-only ANLZ set contains three independent 24,580-row streams at 150 Hz:
packed PWV3 in EXT, packed PWV5 in EXT, and packed PWV7 in 2EX. SoundBox now
copies all three exact byte streams into one owned V4 record and derives the
same set during one analyzer pass for tracks without complete Rekordbox data.
`rekordbox-blue-rgb-three-band-v1` gates the complete set, so the migration
happens once and the persistent Settings selector swaps resident native
pyramids without cache or file I/O.

Literal 150 Hz height geometry was not sufficient: several saturated clap rows
became a broad rectangle and discarded the useful close detail already present
in the 6300-window/s SoundBox envelope. The final renderer uses that ultra
stream as the common outer contour in every mode. 3BAND independently clips its
low/mid/high layers to the contour. A first close-detail attempt additionally
gated that contour with separated 150 Hz columns; the real final-master
comparison rejected it because it produced sparse picket-fence hits. The
shipped correction keeps the ultra contour continuous at every zoom and uses a
30% contrast-curved display-envelope floor to retain the low body Rekordbox
shows between cycles.

Pixel measurement also found two color errors. Nonzero leakage bands had been
given equal votes at the center line, muddying kicks, and the implicit layer
color space converted authored red `255/0/0` to `234/51/35` on the built-in P3
display. The shipped 3BAND mix weights hue by squared PWV7 energy while geometry
alone owns opacity, and the Metal layer explicitly declares Display P3. Final
captures land red at `255/0/0` and high blue at `127/163/255`, against the
Rekordbox reference values `255/0/0` and `127/162/255`.

The initial 39-combination audit and install used the older
`2025/12:29/Sonny - High & Scared v13.wav` as its comparison track. That build
proved the cache/selector/color-space plumbing but was rejected on sight: the
close column gate lost definition. A second installed candidate removed that
gate and used the newer track ID `54231927`,
`2026/4:20/Sonny - High & Scared (final master).wav`, but it was also rejected:
its one unsigned ultra envelope was mirrored around the center line. Tests and
aggregate image scores did not catch the obvious diamond-like cycles, rounded
clap blocks, and missing independent upper/lower detail. Those records are
historical validation failures, not accepted quality.

### Independent-polarity final-master correction on 2026-07-28

The untouched 2,916×310 Rekordbox final-master capture is now the visual gate,
not an image score. Inspecting its upper and lower outlines separately exposed
the representation error: they are strongly asymmetric, while the rejected
SoundBox surface was almost perfectly mirrored. No filter or palette adjustment
can recover information a one-height cache never stores.

`soundbox-polarity-peak-u16-v3` adds a second `detail_lower` section. The
analyzer extracts positive and negative stereo-midpoint peaks with one shared
normalization ceiling, the Python/native ABI uploads both uint16 arrays, every
native maximum mip retains both sides, and the fixed song-space Metal lattice
selects the appropriate side around the center line. Older records temporarily
duplicate their one envelope during the rolling migration.

Only the exact final-master track was recooked into a temporary cache for
proof; the live cache was untouched. The fresh native capture and the original
Rekordbox image were cropped to the same main-lane height, aligned at cue D,
and viewed together. That direct inspection—not the earlier correlation
number—drove two additional display-only corrections:

- the rejected `height^2` curve made attacks tall and needle-thin, while a
  linear trial overfilled the center; `height^1.4` with a 0.84 ceiling retained
  the reference's shoulders and valleys;
- the imported PWV7 mid-heavy decay was painted pink because SoundBox's
  hard-coded mid anchor was pink; the reference is amber/orange, so the current
  3BAND anchors are red/amber/blue.

The resulting reference strip visibly retains the asymmetric repeating cycles,
the noisy decay, and the internal cutouts of both large blue clap blocks. A
second native audit opened BLUE, RGB, and 3BAND at all thirteen actual UI stops
(`24` through `1536` px/s); the formerly ugly 128/184/256/384 stops were also
opened at full 2,916-pixel width.

The full gate then passed with 540 tests and one expected platform skip, as did
the hermetic offscreen-render gate. The stable-signed app was safely installed
and relaunched with its packaged native deck active; it loaded 3,264 tracks /
83 playlists, then began the intended one-time rolling V3 migration. The live
final-master record is now V3 with 1,178,969 genuinely different upper/lower
detail rows. All 3,247 waveform records and 3,124 artwork files remained
present.

The first installed comparison was still not sufficient. It was cropped and
rescaled to align one interval, which understated a full-width mismatch. Sonny's
subsequent 1152 px/s screenshot rejected that build: its red/pink waveform
remained connected through a thick center body and the blue transient cells
were visibly overfilled. The cache polarity was correct; the renderer was
undoing it by applying 30% of the unsigned 150 Hz display envelope as a minimum
to *both* signed sides at every zoom.

The correction retains that quiet-signal floor through distant/middle scales,
then tapers it from full at 384 px/s to zero at 576 px/s. Every close stop
therefore uses the independent decoded upper/lower geometry without an unsigned
minimum reconnecting the lobes. Fresh native captures at the exact cue-D window
show black separation through the center, the decaying signed cycles, the noisy
orange transition, and detailed blue clap cutouts. BLUE, RGB, and 3BAND were
each reopened at all thirteen real UI stops using their genuinely independent
PWV3/PWV5/PWV7 streams; an initial audit harness that accidentally supplied the
same fallback stream to all three modes was rejected and rerun correctly.

A loaded-window screenshot or an equivalent direct full-width visual comparison
is required for future waveform-detail changes. A cropped interval, passing
test count, or aggregate image metric is not visual acceptance.

The subsequent installed RGB comparison exposed three display-only regressions.
PWV5 had first been treated like three independent band heights even though it
stores one RGB color and one height per row; only PWV7/3BAND is layered. Cubic
color interpolation then smeared each close 150 Hz RGB cell into a broad pastel
gradient, while power-of-two-only audio mips could choose an 8-cell maximum for
a 4.2-cell physical pixel and round away nearly half the contour detail. RGB
now retains crisp, song-anchored PWV5 cells through the close ladder, uses the
measured `14 / 8 / 8.75` channel expansion, and draws the outer shape from the
single footprint-matched integer audio peak level. The same comparison at bar
21 showed that the apparent missing waveform was not
missing geometry or a stale cache: PWV5 contained all-zero color rows while
the SoundBox ultra envelope and source PCM remained nonzero. The generic
upload-time color-carrier repair above restores that entire quiet interval
without recooking or modifying any track cache. Full-width native captures were
visually aligned against both desktop references: the 185-second
`Sonny - High & Scared` master at cue D and `Bad Decisions w SUMA (FINAL FR)`
at bar 17 beat 3. Both exact tracks were then viewed at all thirteen UI stops.

The final visual references are the untouched files in
`~/Desktop/rekordbox wave`: `Screenshot 2026-07-28 at 4.55.12 AM.png`
(2,916×310) for track ID `54231927`,
`Sonny - High & Scared (final master).wav`, and
`Screenshot 2026-07-28 at 1.28.48 PM.png` (2,926×292) for track ID `33638604`,
`Bad Decisions w SUMA (FINAL FR).wav`. Do not silently substitute either of
the other High & Scared masters: their audio and waveform are different. The
earlier `211048395` identification was disproved by direct pixel comparison;
the RGB stream in the supplied SoundBox screenshot matches `54231927`.

The final source and hermetic-render gates passed with 545 tests and one
expected platform skip. The stable-signed app was installed and relaunched
with the packaged native deck active; it loaded 3,265 tracks / 83 playlists,
warmed all 3,265 rows, and reported zero preparation tasks. All 3,248 waveform
records remained present. The pre-install `library.json` backup has SHA-256
`9d265455e1ca4921c5caebed4eb897f38282e87d8915ab0f7dd47d8bdb6fd411`;
the post-proof live hash is
`658415d8bcb255942bdc7d0c11a6c9050cb228416b86ac7c31f0861f0b9c85e0`.
A recursive semantic comparison found changes only in
`settings.columns_state` and `settings.waveform_zoom`. Playlists, aliases,
overrides, hidden/import state, and every other curated collection remained
unchanged.

### Opaque timing-grid overlay on 2026-07-28

The beat grid is navigation ink, not waveform decoration. Metal already drew
the grid after the waveform, but ordinary gray beats still used 32% alpha;
the Qt fallback also left both red and gray timing lines translucent.
Waveform color could therefore alter their contrast and, in the fallback,
their identity.

Both renderers now draw downbeats as opaque red and ordinary beats as opaque
gray after the waveform. Cues and the playhead remain above that grid.
Red/gray supplies the hierarchy instead of transparency. Metal retains its
analytic physical-pixel edge coverage, so the one-point lines remain smoothly
positioned at subpixel phases without changing their timing or width.

The display-only change passed the native build, full source gate, and
offscreen-render gate with 528 tests and one expected platform skip. The
signed app was installed and relaunched with the native deck active and 3,265
tracks / 83 playlists loaded. All 3,247 waveform records and 3,124 artwork
files remained present, the Yoyo Bop comparison cache stayed byte-for-byte
unchanged, and the protected curated-state hash remained exact. The unrelated
one-time waveform migration had progressed to 3,160 remaining legacy analysis
records at that launch.

### Phone/native cadence boundary on 2026-07-28

The final 64.995-second phone recording contained 3,897 HEVC frames at a
stable 59.94 fps while traversing the complete zoom ladder. After the phone
was moved close at roughly 23 seconds, registration against static library UI
showed essentially zero camera translation. Waveform travel then formed the
expected zoom-proportional clusters from roughly 2.11 camera pixels per phone
frame at 128 px/s through 25.79 at 1536 px/s.

Almost all travel was even. The remaining outliers were sparse complementary
short/long pairs: at 1536 px/s, for example, roughly 13-pixel travel was
followed by roughly 39-pixel travel around the expected 25.8. The paired
distance equaled two normal phone-frame steps, so there was no accumulating
transport drift. The same proportional pattern appeared at several
384–1152 px/s stops. A 60 fps camera cannot independently resolve every 120 Hz
panel refresh, but the static registration and conserved travel made it a
useful presentation trace when combined with native counters.

A clean full-width native pass then exercised all thirteen zooms with the real
audio path silent. Most 2.5-second windows measured 118–120 actual drawable
presentations per second; waveform complexity did not correlate with the rare
16.667 ms maximum gaps. At 1536 px/s, a longer 15-second pass presented 1,786
drawables at 119.066 fps. Presentation landed within 0.011 ms mean and
0.025 ms worst-case of the submitted `targetPresentationTimestamp`.

A temporary callback probe isolated the boundary. Each stable hitch reported
a 16.667 ms jump in `CAMetalDisplayLinkUpdate.targetPresentationTimestamp`,
the callback itself arrived roughly 16.5–16.7 ms after the prior callback, and
Metal still had roughly 8.1–8.2 ms before `targetTimestamp`, its render
deadline. The renderer did not run late and the compositor did not discard a
finished drawable: Core Animation omitted one 120 Hz update while negotiating
the ProMotion cadence. That one absent temporal sample is most visible at high
zoom because the correct constant-velocity distance per interval is larger.
It is not an envelope, cache, LOD, color, grid, clock, or audio-drop defect.

Three targeted policies were measured and rejected:

- hard-locking the range to 120–120 Hz increased 16.667 ms gaps across nearly
  every zoom;
- raising the floor to 90–120 Hz produced 118.866 fps over the long
  close-zoom pass, retained doubled intervals, and introduced one full-frame
  presentation-target error;
- increasing `preferredFrameLatency` from one to two reduced cadence and
  reached a 25 ms gap at 1152 px/s while retaining the grab/run-on tradeoff.

All probes and experimental policies were removed. The shipped renderer keeps
latency one, the negotiated 60–120 Hz range, future-presentation prediction,
and exact CoreAudio alignment. A fixed 60 Hz comparison remains a possible
deliberate user-facing A/B experiment, but replacing the mostly 120 Hz path
would trade away overall fluidity and was not shipped speculatively.

### Continuous signed-interval coverage on 2026-07-28

The corrected 135.958-second 4K phone recording contains 8,152 HEVC frames at
59.94 fps. Presentation timestamps are stable: 8,089 of 8,151 intervals are
16.666/16.667 ms, and the remaining 62 are small periodic 15/18.333 ms camera
clock corrections with no missing camera frame. Registered high-zoom travel
again contains sparse complementary half/one-and-a-half-step pairs around the
normal step. Their two-frame distance is conserved, confirming the previously
isolated ProMotion callback omission rather than a transport jump.

The recording also exposed an independent shader defect. BLUE/RGB geometry was
song-locked, but `interval_profile_coverage` still averaged five separate
point-in-interval tests. Those tests made final edge opacity jump in 20%
increments as a signed upper or lower boundary crossed the physical pixel.
3BAND did not share the defect because each PWV7 layer already used continuous
piecewise-linear `profile_coverage`.

BLUE and RGB now clamp their minimum interval width at each of the same five
profile locations, integrate the upper boundary continuously, integrate the
inverted lower boundary continuously, and use the intersection. The two
one-pixel antialias bands cannot overlap because the interval has an enforced
3.6-point minimum width. Geometry, signed-range data, reconstruction, LOD,
color, transport, and cadence are untouched.

The read-only acceptance harness swept 32 consecutive 1 ms positions on the
exact canonical tracks using both the previous installed dylib and the new
dylib. Static silhouette XOR was only 0.08–0.13% of lane pixels, confined to
fractional edge coverage. On High & Scared at 1536 px/s, RGB
99th-percentile motion-aligned maximum-channel change fell from 68.98 to 39.60
display codes; pixels exceeding 64 codes fell from 1.152% to 0.124%. BLUE's
greater-than-64-code tail fell from 1.413% to 0.462%. Bad Decisions at
768 px/s retained the same accepted waveform geometry. The isolated test
engine used the real WAVs and V4 caches and did not launch or mutate the
installed app.

### Waveform reference refinement checkpoint on 2026-07-28 (superseded)

This is a **source-only checkpoint**, not an installed or visually accepted
release. The acceptance corpus remains the six untouched Rekordbox screenshots
under `~/Desktop/rekordbox wave`, paired only with the real tracks
`33638604 Bad Decisions w SUMA (FINAL FR).wav` at 25,597 ms and
`54231927 Sonny - High & Scared (final master).wav` at 27,000 ms. Synthetic
audio is not an acceptance fixture.

The last visually proven source state has these properties:

- BLUE and RGB share continuous signed interval geometry. A narrow,
  song-space `[1 2 1]` detail filter applies only around the
  64/92/128/184/256/384/864/1152 px/s stops; the 768 and 1536 px/s reference
  stops remain untouched.
- RGB is visually stable. 3BAND is solid and its subtle close-detail facet
  improved the High & Scared reference silhouette error by approximately 5%
  while leaving Bad Decisions at 768 px/s unchanged.
- BLUE's silhouette is stable, and integration across the smooth
  white/cyan/blue radial field materially improved its middle zooms. At the
  more zoomed-in stops, however, an internal brightness/color tick remains
  even though the outline itself does not move.

The remaining BLUE issue is not a PWV3 hue-data problem: a temporary flat-fill
diagnostic removed the tick, while holding encoded hue constant but retaining
the radial field restored it. Flattening BLUE is therefore not an acceptable
fix because it discards useful frequency/depth color information. Rendering
research points instead to jointly integrating dependent coverage and
procedural color in premultiplied linear light over the final pixel footprint;
MSAA alone cannot prefilter that nonlinear interior signal. A joint resolve is
the first next-session experiment, but its unverified implementation was
reverted before this checkpoint.

The macOS GUI/capture session became unreliable during that experiment.
SystemUIServer and ControlCenter were restarted to clear a stuck screen-sharing
indicator, after which standalone harness windows registered zero-sized
CoreGraphics bounds and native presentation tests temporarily submitted zero
frames. The final bare `./check.sh` recovered and passed all non-visual gates,
including the native presentation tests, but the interactive capture path was
not trusted again in this session. Temporary probes remained under `/tmp`; no
SoundBox app was built or installed and no curated state was touched. Reboot
macOS before resuming visual acceptance.

### Joint BLUE/RGB color-coverage resolve on 2026-07-29

The reboot restored trustworthy CoreGraphics/Metal capture. The untouched
six-image Rekordbox corpus, both exact source WAVs, and both canonical V4 caches
were revalidated before any source change. The first code inspection then found
two related defects:

- the BLUE radial path accumulated its signed center and half-width twice while
  dividing by one profile weight; and
- BLUE and RGB both averaged horizontal color independently of the portion of
  the signed interval that actually covered the destination pixel. BLUE made
  this shared mismatch much more visible because its procedural white/cyan/blue
  field depends on the same moving center and width.

The final joint resolve analytically integrates the clipped linear coverage and
its first moment within each quarter-pixel song-space segment. RGB integrates
PWV5 color against those moments in linear light. BLUE evaluates its smooth
radial transfer at the coverage centroid of each half-segment and accumulates
the result in premultiplied linear light. The already accepted continuous
signed-boundary coverage is then reapplied exactly, so this is a color resolve,
not a silhouette, LOD, reconstruction, timing, or cache change. 3BAND bypasses
the helper and remained byte-for-byte identical in the six-case corpus.

The harness now supports `--position-count` and `--position-step-ms`, allowing
motion sweeps to stay inside one native session. Two 32-position, 1 ms sweeps
compared the committed checkpoint with the joint resolve:

- Bad Decisions BLUE at 768 px/s reduced visible motion-aligned
  maximum-channel p99 from 127.8 to 68.0 display codes and the share above 64
  codes from 9.13% to 1.40%. On solid waveform pixels, p99 fell from 131.2 to
  58.6 and the greater-than-64 share from 8.37% to 0.58%.
- High & Scared BLUE at 1536 px/s reduced visible p99 from 36.7 to 29.5 and
  the greater-than-32 share from 1.55% to 0.75%.
- RGB was explicitly checked rather than assumed clean. Bad Decisions' visible
  mean/p99 changed from 13.035/63.60 to 13.009/63.36, with its
  greater-than-64 share improving from 0.967% to 0.947%. High & Scared remained
  effectively identical at 3.824/27.392 versus 3.832/27.432; its
  greater-than-64 share stayed 0.052%.

Both tracks were rendered again in BLUE and RGB at all thirteen UI zoom stops.
The BLUE white/cyan core now follows the supplied Rekordbox references instead
of collapsing toward dark blue. RGB retained its established appearance: at
864–1536 px/s, 99% of compared pixels changed by no more than 0–2 display
codes. The Metal runtime compiled the shader, the exact six-case corpus
rendered, 83 focused native/reference tests passed, and `./check.sh --render`
passed 549 tests with one expected platform skip. This validation was
read-only with respect to source audio, Rekordbox, and SoundBox AppData.

The signed app was then built, safely installed through one temporary rollback
directory, signature-verified, and relaunched with the packaged native deck
active. It loaded all 3,263 live tracks and 83 playlists. The protected
pre-install `library.json` backup has SHA-256
`a3de5286f637922a6d4708b4b60417825f468bef0433bb7fa7f540e198fe0cc4`;
recursive comparison after launch found changes only in the actively selected
waveform mode and zoom. All playlists, aliases, overrides, hidden/import state,
playlist removals, and other curated data remained identical. The existing
3,248 V4 records remained present. Startup found 43 genuine per-track
`is_ready()` misses rather than zero tasks: this renderer-only change did not
alter any cache model/version, and a read-only audit confirmed the bounded
queue was the existing set of available changed/new files (predominantly
player-only M4A sets), not a model-wide recook. Battery policy is processing
that queue incrementally.

### Close-detail and cadence follow-up on 2026-07-29

Sonny's new 95.903-second, 3600×582 screen recording traverses the BLUE and RGB
zoom ladders. Its HEVC stream is nominally 120 fps but contains 5,454 frames at
56.86 fps average: 3,236 intervals are 16.667 ms, 1,617 are 16.666 ms, 599 are
25 ms, and one is 33.333 ms. At the stable maximum-zoom BLUE and RGB passages,
ordinary registered travel is 15 physical pixels for a 16.67 ms interval and
23 pixels for 25 ms. Sparse conserved short/long pairs remain—for example
8 then 31 pixels or 8 then 23—rather than cumulative transport drift. One exact
duplicate frame exists. The recording therefore confirms a rare presentation
pacing irregularity, while its variable capture cadence is itself too uneven
to label every long source interval as a renderer miss.

Apple's `CAMetalDisplayLink` guidance and current Chromium macOS implementation
were audited against the deck. Prediction already uses the target presentation
timestamp; the explicit 60–120 Hz preferred range, latency one,
`NSRunLoopCommonModes`, actual `MTLDrawable.presentedTime`, and skipped-target
handling match that platform/community direction. Using the system-default
range tied the current implementation in repeated eight-second probes. Fixed
60 Hz introduced 25 ms gaps and approximately 8.33 ms target error. Replacing
the bounded run-mode pump with a continuously running display thread also tied
it in paired alternating trials. The existing cadence architecture is
therefore retained. BBC audiowaveform and Audacity were also checked for
waveform reconstruction precedent; their display-resolution peak aggregation
is consistent with this renderer, but the supplied Rekordbox screenshots remain
the authoritative visual contract.

The exact Desktop screenshots exposed two independent close-detail refinements.
For BLUE/RGB, a physical pixel spans four attribute samples and can contain one
fractional-phase integer lattice knot that the previous fixed quarter-pixel
probes missed. At 1152–1536 px/s the fragment now gathers both pixel endpoints
and every interior knot, then weights coverage and premultiplied color by the
true segment spans. On the 32-position High & Scared sweep this reduced BLUE
visible p99 from 42.443 to 39.903 codes and RGB from 44.321 to 41.571; the
greater-than-64 tails fell from 0.093% to 0.049% and 0.119% to 0.079%.
Applying that topology at 768 px/s was fractionally worse, so the accepted
fixed five-point profile remains below 1152.

For 3BAND, a hard PWV7 cell hold was immediately rejected as blocky. Counting
the exact screenshot facets instead showed that Rekordbox displays one close
facet per pair of stored 150 Hz PWV7 rows. The scrolling lane now begins at the
existing per-band peak mip's paired-row level and linearly connects those 75 Hz
values. This halves SoundBox's false corner density while retaining the larger
authored Rekordbox facets and exact measured seven-color membership palette.
Registered silhouette error improved on both reference tracks; visual
acceptance remains the untouched six-image Desktop corpus, not that imperfect
registration metric.

The exact seven 3BAND membership colors and the BLUE/RGB source hues were
audited directly from the Desktop PNGs. No screenshot-derived compensating RGB
curve was added: the renderer retains the measured authored display values and
the explicit Display-P3 layer contract, while coverage is blended in linear
light. Shape experiments were not allowed to retune color.

Both `./check.sh --render` and the required bare `./check.sh` passed 549 tests
with one expected platform skip. The signed candidate was built and staged
with `ditto`; a direct copy into `/Applications` on this macOS build silently
omitted PyInstaller symlinks and the signature directory, so that incomplete
copy was never launched. Moving the verified complete staged bundle into place
preserved all 63 resource symlinks and the signature. The installed app then
launched with the packaged native CoreAudio/Metal deck, loaded 3,263 tracks and
83 playlists, and retained all 3,248 V4 records. `library.json` remained
byte-identical at SHA-256
`d4e7bf428e87bda019e476703fadba34d55e0208a3ec52a39002e9e3b07d501e`.
Startup scheduled 11 genuine analysis misses under battery policy; this
renderer-only change did not alter cache schema or trigger a broad recook. The
candidate remains subject to Sonny's live visual acceptance.

### Initial close BLUE/RGB extrema-density follow-up on 2026-07-29

Live acceptance established two important boundaries: playback motion across
all three display modes and 3BAND's paired-row shape are accepted; the only
remaining visible defect was a static/pulsating texture in both BLUE and RGB
at the five closest UI stops (384/576/864/1152/1536 px/s). That common mode
boundary ruled out BLUE's radial transfer and the presentation clock.

The signed audio contour was still selecting 0.91–1.03 peak-extrema cells per
physical pixel at those stops. A min/max cell may alternate fully against its
neighbor, so that density places real source energy directly on the pixel
Nyquist boundary even though the contour is song-locked. The close BLUE/RGB
path now advances exactly two entries through the existing measured integer
peak ladder. Its resulting density is 0.51–0.63 cells per physical pixel.
Linear reconstruction, the four-sample song-space attribute lattice, exact
one-pixel coverage/color integration, palette, timing, and caches remain
unchanged. 3BAND bypasses the signed audio contour and is untouched.

Thirty-two-position real-track sweeps measured lower motion-aligned visible
maximum-channel change at every close stop. Mean change fell from
10.527/10.638/12.220/11.923/7.156 display codes to
8.268/7.918/9.731/8.516/4.072 for BLUE and from
9.504/8.892/10.481/9.935/5.917 to
8.035/7.009/8.801/7.851/3.434 for RGB. The exact High & Scared RGB reference
improved silhouette correlation from 0.9111 to 0.9248 and cutout agreement
from 0.9280 to 0.9383; Bad Decisions RGB also improved on both measures.
Both 3BAND corpus PNGs remained byte-for-byte identical.

The focused native/reference set passed 83 tests. Both `./check.sh --render`
and the required bare `./check.sh` passed 549 tests with one expected platform
skip. The signed app was built, staged with all 320 bundle symlinks intact,
signature-verified, and installed through the existing temporary rollback
directory. Relaunch loaded the packaged CoreAudio/Metal deck, 3,263 tracks,
83 playlists, and zero preparation tasks. All 3,248 V4 records and 3,125 art
files remained present. `library.json` stayed byte-identical to the post-quit
backup at SHA-256
`26fa68974f9b174b5fa00e2059c1ce00c224e9ecf05d2b37a320360faac628af`.
The installed candidate is ready for the final live pulsation verdict.

### Close BLUE/RGB output-footprint follow-up on 2026-07-29

The final live verdict on the two-entry peak-ladder candidate was that its
improvement was real but the same static/wiggly color pulse remained visible
in BLUE and RGB at all five closest stops. The new SDR screen recording was
H.264 Main, BT.709 `yuv420p`; lossless samples of the exact Retina waveform
crop measured codec noise below one code value. Registered playback travel was
also exact. Over ten 120 Hz refresh intervals the five stops moved by the
expected integer `64/96/144/192/256` physical pixels, yet the installed
renderer still changed roughly 4–8 display codes with 1–2% silhouette churn.
The defect was therefore renderer source/raster phase, not HDR conversion,
capture cadence, presentation hitching, or the transport clock.

Controlled probes separated the remaining variables:

- Doubling the fixed attribute lattice from four to eight values per physical
  pixel produced essentially identical redraw, ruling out lattice sampling.
- Triangle/RMS minification and fixed 150–600 Hz song-space low-pass filters
  reduced redraw but visibly shortened peaks and rounded the straight facets
  in the supplied desktop references; they were rejected.
- Retaining the conservative peak extrema while selecting one knot per roughly
  four physical pixels preserved the angular carrier and attack height while
  removing the undisplayable alternating extrema cadence.

The shipped candidate derives the close mip with
`audio_detail_lod(detailFootprint * 4)`. At
384/576/864/1152/1536 px/s this selects integer peak divisors
`35/25/18/13/9`, or 0.20–0.23 extrema cells per physical pixel. This is a
song-anchored source decision, not a final-pixel blur; the four-sample
attribute lattice, exact interval/color coverage, Display-P3 palette, timing,
and cache format are unchanged. 3BAND bypasses this signed audio contour.

On the 32-position Bad Decisions source-frame sweep, BLUE registered visible
mean change fell from `8.252/11.562/7.621/7.344/9.697` display codes to
`4.829/5.212/4.307/4.778/5.192`; RGB fell from
`7.994/10.116/6.988/6.754/8.239` to
`5.177/5.460/4.369/4.718/4.882`. On exact ten-refresh-step translations,
more than 99% of registered waveform pixels were unchanged: visible p99 was
0–1 code and silhouette mismatch was 0.08–0.14% across both modes and all five
stops. Both accepted 3BAND corpus renders remained byte-for-byte identical.

The focused native/reference set passed 85 tests. Both the required bare
`./check.sh` and `./check.sh --render` passed 551 tests with one expected
platform skip. The signed app was built and installed with all 320 bundle
symlinks intact; the packaged and installed native dylibs were byte-identical.
Relaunch activated the packaged CoreAudio/Metal deck, loaded 3,263 tracks and
83 playlists, and scheduled zero preparation tasks. All 3,248 V4 records and
3,125 art files remained present. The pre-install `library.json` is protected
at SHA-256
`f11a2c2d0439ea5a3d3fd3773fcae299873f819f2c894b1a381b0d0b465c8141`;
after relaunch every curation-bearing section remained identical and only the
actively exercised waveform mode/zoom settings changed.

### Zoom-height and ordinary-pause follow-up on 2026-07-29

The next two SDR recordings separated a perceived playback-size change from a
transport discontinuity. The dramatic vertical growth occurred while paused
on a 256→384 zoom change. It was not gain or a playing-only render path: 384
px/s is the boundary where BLUE/RGB begins using the four-physical-pixel peak
source, and max pooling over its larger divisor raises typical contour height.
On the same 0.8-second Bad Decisions song window, median half-height previously
walked `44.5/42.5/39.5/37.5/33.5` pixels from 64 through 256, jumped to `42.5`
at 384, then fell `39.5/37.5/34.5/30.5` through 1536. This exactly matched the
recorded grow/shrink pattern and the reported thin 92/128 region.

BLUE/RGB now applies a shallow signed height normalization from 92 px/s upward
based only on the selected integer peak divisor:
`pow(50 / divisor, 0.22)`, bounded to `0.8...1.5`. The same measured window now
holds a 45.5–47.5-pixel median half-height at every stop from 92 through 1536;
64 remains 44.5. Horizontal detail, phase, mode color, and caches are
unchanged. The exact High & Scared desktop references remain the acceptance
source, and both 3BAND corpus renders remain byte-for-byte identical.

The start/stop recording also exposed an independent pause endpoint bug. After
the button changed state, queued Metal frames continued forward and the
millisecond Python estimate then sought 7–13 physical pixels backward. Clean
pause now captures Metal's newest presented/scheduled source frame at fade
completion—the same proven atomic hold used by waveform grabbing—then pauses
and seeks to that fractional-millisecond frame. Resume starts from the exact
hold rather than a stale poll.

The first recording averaged 56.9 captured fps while the app presented at
120 Hz, so its irregular 1–3-refresh holds necessarily alias the fast
carrier/“traffic-line” motion. No temporal shutter or extra waveform smoothing
was added from that evidence because either would change the desktop-reference
edges and make paused/playing sharpness disagree. The planned high-frame-rate
iPhone recording remains the correct live-display check for residual temporal
motion and hitching.

Both the required bare `./check.sh` and `./check.sh --render` passed 551 tests
with one expected platform skip. The signed app was built, safely installed
through one temporary rollback directory, and relaunched with all 320 bundle
symlinks intact. The packaged and installed native dylibs were byte-identical;
startup activated that native deck, loaded 3,263 tracks / 83 playlists, and
scheduled zero preparation tasks. All 3,248 V4 records and 3,125 art files
remained present. The post-quit backup and relaunched `library.json` were
byte-identical at SHA-256
`6053360eb9dd6ed5266a387ef42fdc9c6c265a9d4e5b26cea609ae005e2ff60f`.
Live acceptance of the normalized height and exact pause endpoint is pending.

### Height-crossover correction on 2026-07-29

The first installed normalization was rejected on sight. The 23.53-second
SDR follow-up traversed the full RGB zoom ladder during playback and exposed
what the earlier fixed-window median hid: the divisor curve was applied as a
straight signed multiply. At the exact 24.2 passage surrounding the
256→384 click, the 256 row clipped loud extrema toward the lane edge and its
median outer half-height then fell from 78 to 69 physical pixels when the
four-pixel source family began. The 92→128 handoff also changed apparent
tooth height more abruptly than its aggregate median suggested.

The replacement keeps the same divisor selection and shallow gain but applies
it only through the magnitude's unused headroom:
`m + (gain - 1) * m * (1 - m)`. Digital zero and full scale are fixed points,
so a loud row cannot be enlarged into clipping and then collapse at the close
family boundary. Narrow bounded additions of `0.15` and `0.18` at 92 and
128 px/s initially lifted the two specifically reported thin stops without
moving their full-scale peaks. The live follow-up then identified the third
click from the 24 px/s end—64 px/s—as the remaining midrange waist. The final
bounded additions are `0.10/0.23/0.26` at 64/92/128 px/s, spreading that lift
across the whole transition rather than creating another breakpoint.

At the real app geometry and exact recorded 24.2 position, RGB and BLUE now
share the same median outer-half-height ladder:
`67/67/66/63/61/60/60/63/65/65/66/66/67` physical pixels at
24/32/48/64/92/128/184/256/384/576/864/1152/1536 px/s. The largest
adjacent change is three pixels and the 256→384 crossover is two pixels
instead of a nine-pixel collapse. This is vertical-only; source density, song
phase, color, caches, and the accepted 3BAND path are unchanged. Against the
exact High & Scared desktop source, the replacement improved RGB silhouette
correlation/MAE to `0.9308/0.1533` and BLUE to `0.6955/0.2141`. Both 3BAND
corpus PNGs remained byte-for-byte identical to the accepted renders.

Both the required bare and offscreen-render gates passed 552 tests with one
expected platform skip. The signed app was built, safely installed through one
temporary rollback directory, and relaunched with all 320 bundle symlinks
intact. Its packaged and installed native dylibs were byte-identical. Startup
activated that deck, loaded 3,263 tracks / 83 playlists, and scheduled zero
preparation tasks; all 3,248 V4 records and 3,125 art files remained present.
The post-quit backup and relaunched `library.json` were byte-identical at
SHA-256
`4bb621ab0e957d9bb32e1c4a306082ca6c40c73965436fc3eb91e3d8ee3d5856`.
Sonny's immediate live verdict was that the full zoom transition was “much
better”; the bounded height ladder and exact ordinary-pause endpoint are the
accepted checkpoint.

### BLUE/RGB temporal-aliasing R&D handoff

The next player session starts here. Do not reopen the accepted 3BAND shape,
BLUE/RGB height ladder, pause endpoint, palette, or paused desktop-reference
edges unless new evidence directly implicates one of them. The sole unresolved
visual defect is the static/wiggly/color-pulse impression while BLUE or RGB
moves at the five closest stops (384/576/864/1152/1536 px/s). A separate
reported hitch is not yet isolated and must not be conflated with the carrier
texture.

Authoritative local evidence:

- Desktop PNGs under `~/Desktop/rekordbox wave/` remain the paused spatial
  source of truth for color, edge shape, height, and detail.
- `Screen Recording 2026-07-29 at 4.14.25 PM.mov` traverses RGB and BLUE and
  is the best existing close-motion recording.
- `Screen Recording 2026-07-29 at 4.24.58 PM.mov` isolates start/stop; it led
  to the now-shipped exact Metal pause hold.
- `Screen Recording 2026-07-29 at 4.49.20 PM.mov` traverses the RGB height
  ladder; it led to the accepted bounded crossover correction.
- The first recording is H.264 Main, SDR BT.709 `yuv420p`, 3,600×582, and
  averaged only 56.9 captured fps while SoundBox presented at 120 Hz. It
  necessarily contains irregular one-to-three-refresh frame holds and cannot
  by itself prove or disprove a physical-display temporal artifact.

What the completed investigation already established:

- The display clock advances the waveform by the exact expected physical
  distance. Registered ten-refresh translations at every close stop held
  visible p99 change to 0–1 display code and silhouette mismatch to
  0.08–0.14%; a gross transport-position error is not the pulse.
- BLUE and RGB share the defect while accepted 3BAND does not. BLUE's radial
  transfer, RGB's distinct color path, and the authored palette are therefore
  not sufficient explanations.
- Doubling the fixed attribute lattice from four to eight values per physical
  pixel made no material difference.
- Triangle/RMS minification and 150–600 Hz song-space low-pass probes reduced
  redraw but rounded attacks and shortened peaks relative to the desktop
  sources; they were rejected.
- Selecting one peak knot per roughly four physical pixels improved the
  artifact materially while retaining angular extrema. It remains the shipped
  spatial source decision, not proof that the residual is solved.
- Linear-light joint interval/color integration removed an earlier brightness
  tick. Final-pixel blur, temporal shutter, or playing-only smoothing remain
  unacceptable without evidence because they would make stopped and moving
  spatial truth disagree.
- The height crossover and pause rewind were separate measured defects and are
  now fixed. Do not use their before/after recordings as evidence that the
  residual carrier pulse changed.

The next session must be an experiment-led temporal investigation, not another
documentation survey or blind filter sweep:

1. Record the physical built-in panel at 120 or 240 fps with the iPhone locked
   for focus, exposure, white balance, and framing. Capture the same passage
   stopped and playing at all five close stops, plus accepted 3BAND as a
   control. Include enough screen border to recover camera/display phase.
2. Add a diagnostic-only native capture that records the exact Metal drawable
   sequence and display-link target/present timestamps for the same run.
   Compare those GPU frames with the filmed panel. This separates shader
   redraw from compositor/ProMotion cadence, sample-and-hold motion, panel
   response, and camera aliasing.
3. Build space-time strips through fixed song coordinates after exact motion
   registration. Measure carrier temporal frequency, phase reversals, color
   variance, repeated/late presents, and whether the residual exists in the
   drawable sequence before it reaches the panel.
4. Drive synthetic signed carriers through the same Metal interval compositor:
   constant-color triangles, alternating extrema, isolated impulses, and
   controlled knot densities bracketing the shipped 0.20–0.23 cells per
   physical pixel. The smallest pattern that reproduces the filmed defect
   identifies whether geometry, chroma, coverage, or presentation is causal.
5. Only after that split should a remedy be designed. Candidate domains include
   source reconstruction, analytic prefiltering, presentation scheduling, or
   display-aware temporal treatment; acceptance still requires paused
   reference fidelity, stopped/playing agreement, 3BAND invariance, and no
   cache/schema change.

Success is not “looks somewhat calmer in QuickTime.” It is one named causal
mechanism, a reproducible diagnostic, a remedy that addresses that mechanism,
and side-by-side physical-panel evidence at every affected zoom.

## Committed next player work

See `ROADMAP.md` for sequencing.

1. Execute the BLUE/RGB temporal-aliasing experiment above. Preserve the
   accepted spatial checkpoint and separate renderer, presentation, panel, and
   camera effects before changing the shipped contour again.
2. Design the not-yet-shipped grid/cue editing UX on top of the V4 storage
   mutation boundary. It must never mutate Rekordbox analysis. Today a valid
   Rekordbox grid remains authoritative and only the no-grid fallback begins at
   `0:00`; later manual adjustment and precedence require a UX workshop.

Cue/grid storage and export-field mapping exist. No manual grid editor exists
yet; its interaction still needs a UX workshop before code.
