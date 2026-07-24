# SoundBox player

Current as of 2026-07-24. This is the player handoff: shipped behavior,
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
- `ANLZ0000.EXT` / `PWV5`: approximately 150 Hz RGB waveform.
- `ANLZ0000.EXT` / `PCO2`: fallback cue comments and explicit RGB colors.
- `ANLZ0000.2EX` / `PWV7`: three-band detail used to sharpen fallback height.

SoundBox then persists the complete canonical record independently; prepared
playback does not reopen Rekordbox analysis. Cue fingerprints are refreshed
into V4 through an atomic manifest-only update until a future SoundBox edit
claims ownership; waveform payload bytes never change during that refresh.
SoundBox's audio analyzer supplies the ultra contour, dedicated RGB stream,
and 24-band spectral master. No player code opens Rekordbox files for writing.
No SoundBox operation updates `master.db`, ANLZ data, or source audio.

## SoundBox-owned caches

All derived caches are disposable accelerators under
`~/Library/Application Support/LowFreqLabs/Soundbox/`:

| Directory/file | Owner and purpose |
|---|---|
| `libcache.json` | Last read-only library snapshot for instant first paint |
| `track_analysis/*.sba4` | Canonical V4: checksummed Zstandard sections for source identity/provenance/export eligibility, full grid/cues, 150 Hz RGB, 24-band spectral master, and ultra contour |

Each V4 record is atomically replaced, keyed by track ID plus audio identity.
Interactive loads validate only the requested section SHA-256; cache audits
also verify the complete compressed payload. A
changed/new track prepares only its own record. Storage-only cue/grid mutation
support is a dormant boundary for later work; no manual grid editor exists.

The native analyzer streams PCM in bounded memory, derives approximately
6144 peak windows/second, keeps a normalized 24-band master, and builds the
separate 150 Hz RGB stream from overlapping second-order Butterworth paths:
LP 100 Hz, HP 300 + LP 3000 Hz, and HP 1500 Hz. One-millisecond band peaks use
approximately 25/3/1 ms low/mid/high holds before a local per-column energy
transfer, overlap correction, green restraint, blue lift, and compact
quantization. When no valid Rekordbox grid exists, the analyzer derives a
constant-tempo grid from FFT autocorrelation and anchors beat 1 at exactly
`0:00`. The fallback path is NumPy-only and has no runtime JIT.
Qt/FFmpeg feeds the same analyzer for AAC/ALAC set rips. Export eligibility is
deliberately separate: only MP3, WAV, AIFF/AIF, and FLAC are future-CDJ
candidates; AAC/ALAC/M4A/MP4 are always player-only.

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
- imports grid/cues as seeds, uses PWV5 RGB only when native audio analysis is
  unavailable, and refreshes live-DB cue metadata until SoundBox user data
  claims that field;
- uses a direct, bounds-checked PQTZ row reader for legacy grid metadata
  migration so it never inflates generic Rekordbox objects or opens EXT/2EX
  waveform/color analysis just to determine grid ownership;
- uses a 16 MiB worker stack and a 5/7 GiB free-space pause/resume guard;
- reports `Preparing your library…` only when work exists;
- can hot-load newly completed detail for the current track;
- is interrupted and joined during shutdown.

A prepared canonical cache hit is staged off the UI thread: the small manifest
positions transport first, the packed RGB overview is decompressed and uploaded
once as the permanent color/overview pyramid, and ultra geometry is prepared
last. Overview and ultra buffers are committed inside one native presentation
transaction: the display link skips incomplete grouped uploads, so a long set's
first visible waveform is already its final silhouette rather than a smaller
overview that visibly inflates. The detail stage never re-uploads the color
payload. Native decode starts
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

## Why the waveform remains stable

The following renderer rules are measured invariants, not style preferences:

- The main waveform always uses the SoundBox audio-detail envelope pyramid when
  available; there is no zoom-level crossover back to PWV7.
- Pyramid levels are song-anchored maxima that retain transient detail as one
  solid silhouette. Both overview and scrolling geometry continuously blend
  the two adjacent levels around the physical-pixel footprint; there is no
  zoom threshold or alternate wide/close shader. A second RMS/translucent
  envelope was rejected because it reads as a ghost waveform rather than
  additional useful detail.
- The scrolling lane's color comes only from SoundBox's dedicated overlapping
  RGB filterbank; color remains a compact ~150 Hz buffer rather than being
  duplicated into millions of ultra samples. Its mip pyramid averages RGB
  using waveform amplitude as energy weight, then uses the same continuous LOD
  and song anchor as geometry. Adjacent colors therefore converge instead of
  alternating at far zoom. Per-column normalization retains the real
  low/mid/high relationship instead of making weak independently normalized
  FFT bands falsely competitive. The 24-band master is stored independently
  and neither Metal nor Qt may remix it into display color.
- `color_model` versions the persisted RGB analysis without changing the V4
  container schema. Old records remain playable while the background worker
  atomically replaces one track at a time; each temporary record lives beside
  its target, and SoundBox-owned grid/cue rows survive the upgrade.
- Derive one continuous LOD from source samples per physical pixel and blend
  the adjacent peak levels. The overview and main lane call the same sampler;
  ultra detail replaces geometry quality only, never timing or color.
- Integrate four song-anchored midpoint taps across that physical-pixel
  footprint at both adjacent LOD levels before blending them. A single
  center-of-pixel sample aliases dense transient edges and neighboring RGB rows
  as playback phase advances. Color uses amplitude-weighted integration over
  the identical taps, so hue and silhouette never choose different phases.
- Rasterize each final Retina pixel directly from its continuous song
  coordinate in one Metal pass. There is no moving intermediate texture,
  discrete texel selection, coverage resampling, or refresh seam. This retains
  the same physical-pixel LOD and maximum-zoom detail while allowing every
  display frame to advance by its true fractional distance.
- At close zoom, one 120 Hz refresh can move a sharp contour by more than
  twenty Retina pixels even when the clock and presentation cadence are
  perfect. While playback is moving, the four-tap sampler therefore extends
  its integration footprint across half of the measured presentation interval.
  The LOD footprint remains the physical output pixel, so this temporal
  reconstruction reduces strobing without selecting a coarser waveform or
  changing the paused maximum-zoom surface.
- Filter final silhouette coverage—not raw peak height. Coverage from each
  adjacent level is blended after sampling, preventing the contour from
  acquiring a separate translucent edge.
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
  The moving frame's temporal footprint is retained through the first held
  redraw, preventing a no-motion grab from changing thickness or color.

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

These are useful real-library stress tracks because they expose dense
high-frequency contours and late-track precision. Automated tests must remain
hermetic and must not depend on Sonny's personal files.

## Committed next player work

See `ROADMAP.md` for sequencing.

1. Design the not-yet-shipped grid/cue editing UX on top of the V4 storage
   mutation boundary. It must never mutate Rekordbox analysis. Today a valid
   Rekordbox grid remains authoritative and only the no-grid fallback begins at
   `0:00`; later manual adjustment and precedence require a UX workshop.

Cue/grid storage and export-field mapping exist. No manual grid editor exists
yet; its interaction still needs a UX workshop before code.
