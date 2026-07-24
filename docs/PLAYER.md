# SoundBox player

Current as of 2026-07-23. This is the player handoff: shipped behavior,
architecture, cache ownership, non-regression rules, validation references, and
the next grid work. Read it with `ARCHITECTURE.md` and `GOTCHAS.md` before
changing the deck.

## Shipped behavior

The player occupies two compact lanes:

- Header: compact SoundBox wordmark, centered artist/title, search, Settings.
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
There are twelve persistent scales:

`64, 92, 128, 184, 256, 384, 576, 864, 1152, 1536, 2048, 3072 px/s`.

At maximum zoom, a typical 140–175 BPM track shows roughly three beats across
the deck.

Main-waveform scrubbing is relative and precise:

- Press/release without horizontal movement is inert, even during playback.
- Crossing the three-pixel drag threshold claims the playhead, freezes the
  visual preview, and fades playing audio to pause.
- Dragging gives live visual position feedback without repeatedly seeking the
  decoder.
- Release performs one authoritative seek and resumes only if playback was
  active before the drag.
- The main lane never walks or snaps on a click; only a deliberate drag moves
  it. The whole-track overview remains an absolute click/drag navigation lane.

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
- `CAMetalDisplayLink` presents moving/interactive surfaces at the display
  cadence (120 Hz on the built-in ProMotion panel). A paused/static deck draws
  one dirty frame and then pauses the display link until state or size changes.
- The Qt waveform host synchronously commits its new point size to the embedded
  AppKit view and Retina drawable on each host resize. Generic child-widget
  resizes only defer background preparation; they do not wake Metal. Maximize,
  restore, and live resize therefore render the final geometry in the same UI
  turn instead of briefly stretching a smaller drawable.
- Python polls state at 8 ms only while playing, loading, or gain-ramping and
  drops to 125 ms while paused. Commands and app activation request an immediate
  authoritative snapshot; Python never repaints the native waveform.

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
next, and ultra detail plus its native peak pyramid is promoted last. Native
decode starts before those reads. Every stage carries a monotonic track
generation; `begin_track(generation)` atomically clears the previous waveform,
grid, cues, session marker, and detail before the replacement source appears.
Late stages from an older track are rejected in both Python and native code.
No phase expands packed cache rows into nested Python lists.

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
  solid silhouette. A second RMS/translucent envelope was rejected because it
  reads as a ghost waveform rather than additional useful detail.
- The scrolling lane's color comes only from SoundBox's dedicated overlapping
  RGB filterbank; color remains a compact ~150 Hz buffer rather than being
  duplicated into millions of ultra samples. Per-column normalization retains
  the real low/mid/high relationship instead of making weak independently
  normalized FFT bands falsely competitive. The 24-band master is stored
  independently and neither Metal nor Qt may remix it into display color.
- `color_model` versions the persisted RGB analysis without changing the V4
  container schema. Old records remain playable while the background worker
  atomically replaces one track at a time; each temporary record lives beside
  its target, and SoundBox-owned grid/cue rows survive the upgrade.
- Select one level no more than roughly 1.1 source samples per physical output
  pixel. A two-level/per-fragment footprint loop dropped a populated render to
  roughly 13 FPS and was rejected.
- Filter final silhouette coverage and premultiplied RGB—not raw peak height.
  Interpolating peak height makes the top/bottom contour grow and shrink as it
  crosses fractional pixels, perceived as static.
- Once a fragment belongs to that silhouette, apply a small chromatic
  visibility floor before compositing. This keeps one-physical-pixel quiet
  passages vivid against the dark deck without increasing their amplitude or
  adding a second envelope.
- Four midpoint coverage taps across one physical pixel are the current
  quality/performance sweet spot.
- Address audio detail with an integer sample anchor plus local float phase.
  Never multiply a large absolute float time back into a sample index.
- Address beat-grid vertices with integer milliseconds plus local phase.
  Absolute float-second subtraction loses precision late in a track.
- The PLL may change slope, never phase-step the visible frame. The removed
  0.15 ms callback correction could become almost half a Retina pixel at
  maximum zoom.
- Keep the Metal child view opaque and let it own waveform pixels. Reblending
  previous translucent frames recreates the original shimmer.

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
  cadence, static display-link sleep, adaptive polling, continuous visual
  clock, and source immutability.
- `tests/test_native_shader.py`: shader/source contracts for coverage filtering,
  vivid thin-contour color, cue coordinate modes, one output-safe mip, four
  physical-pixel taps, integer/local-phase addressing, PLL correction, and
  future presentation timestamps.
- `tests/test_ui.py`: transport-only Space, double-click loading, keyboard CUE,
  cue-panel/overview/detail marker identity, activation resynchronization,
  stale-completion rejection, beat/bar jumps,
  scrubbing, overview, zoom persistence, rapid loading, preparation, and window
  state.

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
