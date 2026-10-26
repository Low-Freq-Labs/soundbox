# SoundBox player

Current as of 2026-10-26. This is the player reference: shipped behavior,
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
- By default, `C` is the keyboard equivalent of pressing/releasing CUE while a
  browser surface has focus. The packaged entrypoint disables Cocoa
  press-and-hold for the Soundbox bundle before importing Qt; non-text browser
  views also opt out of the macOS text-input client, and SoundBox consumes
  repeat events. Holding C therefore remains a clean momentary gate instead of
  opening the system accent-character panel, including on the first track load.
  The setting is bundle-local, so other apps keep normal accent entry. Delegate
  editors remain separate text widgets; text fields and Command-C retain normal
  behavior apart from held-letter accent selection inside Soundbox.
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
  the native terminal playback edge advances. Auto-advance eligibility follows
  the ordinary playback request independently of the button's polled state:
  temporary PAUSED snapshots during seek/refill or activation cannot disarm it.
  Explicit pause/stop, source replacement, and playback errors cancel that
  request; resumed playback arms it again. Each terminal event is consumed once,
  and a newer transport command or source invalidates deferred advancement.
  A newer terminal flag overrides a stale PLAYING snapshot before that deferred
  source swap. Cue playback promoted with Play/Space also advances after release;
  an unlatched cue preview does not. Device discovery does not gate advancement.

Keyboard settings expose the transport and cue actions with remappable modifier
combinations and multiple bindings per action. Space, C and Command-R retain
their defaults. Numbers 1–8 preview slots on the selected Hot/Memory page;
9 selects Hot Cue and 0 selects Memory Cue. `=` zooms in and `-` zooms out by
default. Cue keys use the same gated-preview
contract as mouse presses. A held slot retains its original page/control through
page switches, modifier release and focus changes. Keyboard cue bindings use the
active page; the separate direct Hot/Memory actions are retired. Empty slots remain inert.
See [keyboard settings](KEYBOARD.md) for editing and context boundaries.

Cue panel:

- `HOT CUE` and `MEM CUE` are separate pages with eight slots each, arranged
  as two columns by four rows.
- The cue grid starts level with the whole-track overview and fills the player
  height down to its page tabs; the separate elapsed/duration label was removed.
- Existing cues are imported from `djmdCue` in the read-only live database
  snapshot. Hot cues retain every canonical slot (including I–P) and memory
  cues retain chronological order; the calm panel displays A–H / the first
  eight memory cues while the canonical cache and exporters keep the overflow.
- `djmdCue.Kind` has a reserved gap: A/B/C are 1/2/3 and D–P are 5–17.
  SoundBox translates that encoding explicitly; value 4 and unknown values
  are never guessed into a letter.
- Labels, loops, positions, and verified color information are preserved.
  ANLZ PCOB/PCO2 remains a fallback when no live database cue source exists.
- The loaded, source-normalized `Track.source_cues` rows are injected directly
  into the deck and override cache/ANLZ sidecars. A successful library refresh
  immediately updates an already-loaded track's cue panel and waveform markers,
  including deletions, without seeking or reloading audio. A queued older cache
  manifest cannot restore stale cues; different track/source identities are ignored.
  Exact cue identity therefore
  does not wait
  for background waveform preparation; V5 still receives the same rows atomically
  for long-term SoundBox ownership.
- Populated slots emit separate press/release edges for gated playback. Empty
  slots are inert. No cue is written back.
- Whole-track and scrolling memory arrows are slightly wider than hot-cue
  stems and terminate flush on the waveform edge, fully covering a coincident
  red grid line.

Loading semantics are deliberate:

- Double-click is the only browser gesture that loads a track. It preserves
  ordinary playback intent: a playing deck starts the new track at its first
  grid beat; a paused/empty deck loads it paused. Rapid replacements retain the
  newest request through fade-out and pending grid/media loading. A temporary
  backend PAUSED snapshot does not cancel that intent. Momentary CUE previews
  remain separate; only a preview latched with Play carries playback forward.
  Previous/next deck controls explicitly start the selected track.
- Space only toggles play/pause for the already-loaded track. Browser selection
  is ignored, and Space does nothing when the deck is empty.
- A non-empty, successfully parsed Rekordbox beat grid is authoritative and a
  new track begins at its exact first timestamp. When no valid Rekordbox grid
  exists, SoundBox's constant-tempo fallback is anchored with beat 1 at
  exactly `0:00`. That decision is based on grid availability/provenance, never
  file extension. A loaded legacy fallback is zero-anchored for its first paint
  even if the incremental metadata migration has not reached that record yet.
- CloudStorage audio admission reads one byte on the native decode thread before
  opening AVAudioFile. ENEEDAUTH alone permits two cancellable three-second waits;
  persistent failures name the cloud file and clear pending Play/cue intentions.
  Play retries a failed audio source without rebuilding its resident waveform.
  This is readiness handling, not a source-integrity proof or a cloud sign-in fix.
  A cold cloud-only source can still delay first playback while its provider
  supplies bytes; a ready waveform cache does not make source audio local.
  Keeping intended tracks downloaded avoids that dependency.
- Starting or resuming from a nonzero position holds that exact visual position
  until CoreAudio's future presentation timestamp reaches it. It never extrapolates
  backward before the start; steady playback retains the existing continuous PLL.
- Rapid-fire source replacement is supported and tested.
- The centered now-playing title has no duplicate hover tooltip. Clicking its
  box clears a hiding search and returns browser focus to the exact loaded
  track by ID, preserving the current view when possible and otherwise falling
  back to the SoundBox Collection.

## Waveform interaction

October 1 test audit: production Metal helpers now run against independent
GPU numerical oracles, with a separate required Cocoa drawable gate. Tiny
clipped color moments use stable local midpoint arithmetic; the accepted
geometry and sampling footprint are unchanged. October 2 corrects the signed
coverage intersection and retires its expected failure. The retained five-probe
profile approximation remains explicit. The later October 2 close-zoom follow-up
replaces BLUE radial-centroid evaluation with branch-split color quadrature. See the
[test audit](archive/waveform-test-audit-2026-10.md).

September 30 installed correction: BLUE/RGB retain the original
premultiplication weight in the existing eight-float song attribute. Color
recovery divides by that interpolated weight, never by the magnitude of the
interpolated signed bounds. The latter shrinks at polarity/dominant-boundary
crossings and can spuriously whiten colors. No geometry, clock, cache or LOD
change accompanies this fix. CPU arithmetic, native frames and Cocoa gates pass,
but remaining motion artifacts required the October 1 sampling fix: RGB/BLUE
at 256/384/576/864 now include every lattice corner within one physical pixel.
Sonny describes B in the live A/B preview as “rock solid.” Detail, footprint,
clocks and other zooms remain unchanged. October 2 promotes corrected joint
coverage after numerical and native comparison; it does not smooth source detail. See the
[pulse audit](archive/waveform-reuse-audit-2026-09.md#september-30-color-pulse-correction).

The fixed cyan playhead sits over three waveform lanes:

1. A small, unzoomed whole-track RGB overview. Clicking or dragging it seeks
   anywhere in the song.
2. A ruler showing only the current `bar.beat` at the playhead, avoiding
   triple-digit label clutter.
3. A zoomed, scrolling RGB detail waveform with Rekordbox beat/downbeat lines.

The `−` and `+` controls change only the detail lane. The overview never zooms.
There are thirteen persistent scales governed by one UI/native zoom contract:

`24, 32, 48, 64, 92, 128, 184, 256, 384, 576, 864, 1152, 1500 points/s`.

The 24 px/s stop shows about 38 seconds in a 900-point lane. The 1500 points/s
maximum matches the September 29 reference scale (3000 physical pixels/s at
Retina scale two). Visible duration depends on the lane width. The native ABI returns its
applied scale, so persisted zoom, paint, grid translation, and scrub math can
never disagree about a silently clamped value.

The main grid uses continuous full-height guides with paired edge ticks and
red downbeat triangles. Width stays 0.9 points for ordinary beats and 1.1 for
downbeats at every zoom. Interior alpha grows from 0.28/0.58 to 0.45/0.75
(ordinary/downbeat) as local spacing grows from 32 to 80 points; guides never
disappear in wide views. Edge ticks retain opaque timing colors. Spacing comes from adjacent authoritative beat
timestamps, including variable tempo, not a guessed BPM. Analytic horizontal
coverage preserves continuous subpixel travel. The overview, cues, grid times,
playhead, and transport clocks are unchanged.

Wide and middle framing uses two points of vertical padding through 256 points/s,
smoothly increasing to four points at 384 and above (previously seven everywhere). This reclaims
unused lane height without changing the amplitude curve, cached data, horizontal
detail. Close views gain a little height too, with more breathing room than
wide views. It applies only to the large scrolling waveform.

At 24/32/48 points/s, BLUE/RGB select a half-pixel extrema footprint;
64–1500 select the physical-pixel footprint. The October 2 follow-up selects
2-ms compact detail at Retina 384 and 1-ms at 576–1500. One song-anchored mip and the final
one-pixel profile integral preserve detail without mixing adjacent mip levels.
The former four-pixel close footprint and divisor-dependent height gain were
removed after comparison with the new reference.

Settings exposes three persistent waveform displays:

- `BLUE` uses the exact packed PWV3 stream when Rekordbox supplies one. Its
  three luminance bits select the calibrated blue-to-cyan edge range. The
  interior transitions from cyan to white with normalized signed-interval width;
  thin intervals no longer receive an unconditional white center. The October 1
  correction reduces wide-view white washout without changing geometry or
  smoothing detail. Sonny accepted the narrowed BLUE candidate in a live muted comparison. Exact
  recording parity and broader track acceptance remain open.
  Its scrolling geometry is a high-rate signed
  min/max trace: low-frequency bins remain thin oscillating lines and dense
  bins widen naturally.
- `RGB` uses the exact packed PWV5 stream. Each row is one display color plus
  one compact height—not three band layers. PWV5 owns color and distant-scale
  amplitude. Its scrolling geometry uses the same signed min/max trace as
  BLUE, with linearly interpolated full-range PWV5 colors on their song clock.
  Since October 6, occupied RGB pixel columns use conservative coverage to keep
  narrow detail solid; conditional visible color still comes from the joint
  interval integral. This is not an exact-area presentation or source smoothing.
  October 8 color-only correction: a zero PWV5 compact height no longer turns
  its valid RGB code black before blending at wider zooms. Positive color
  weights cancel exactly; a zero weight uses the authored row color. Quiet
  greens, blues and every other encoded hue retain their source colors.
  Signed geometry, coverage, LOD and scrolling are unchanged; no recook.
- `3BAND` uses the exact packed PWV7 display rows as three independent
  shapes. Empirically calibrated band scales are applied separately and composited as
  blue lows, brown mids, white highs, and the measured cream/pale-blue
  membership colors. The full-rate rows are preserved; exact layer-height and
  interpolation parity remain unverified. See the October 1
  [recording verification](archive/waveform-reference-check-2026-10.md).

All three streams are imported or derived in one cache preparation pass and
remain resident as separate native GPU pyramids. Switching the selector is
immediate and never recooks the track. BLUE and RGB combine their authored
150 Hz color with the same persisted signed minimum/maximum cells rather than
filling positive and negative envelopes back to zero. That shared geometry
keeps tonal passages as continuous oscillating traces and lets noisy passages
widen naturally; the modes differ only in their PWV3/PWV5 color compositor.
Both use output-footprint-selected extrema. Compact supplements hold signed
1-ms cells at close zoom; spatial antialiasing resolves their coverage.
RGB/BLUE round display translation to physical pixels through 384 points/s,
then logical points at 576–1500. Audio, seek and grid clocks remain continuous;
3BAND retains subpixel translation.
The minimum signed stroke is one physical pixel before antialiasing, replacing
the fixed 3.6-point width that made thin carriers visibly thick on Retina.
3BAND uses the original 150-Hz PWV7 rows and linear per-band interpolation,
with a one-row sampling offset. It does not borrow the signed contour, force
paired 75-Hz rows, or add an artificial decay facet.

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

Artwork review exposes a compact Play/Pause, click/drag scrubber and time display
beside Keep/Replace/Next. It auditions the selected local library track through
the existing PlayerBar/CoreAudio deck and its normal load/grid/origin rules.
Selecting a review row alone never loads audio. Changing rows, closing review,
or application shutdown cancels the audition and pending play/source-change
intent; no second audio engine or SoundCloud audio is opened. Review playback
does not capture the playlist autoplay context, and terminal playback cannot
advance it. Ordinary playback is not stopped merely by opening review.
The public compact transport snapshot, pause/resume and bounded seek entrypoints
retain clickless ramps and pending grid-load ownership. The review
button also follows actual native playback if a transient PAUSED/refill snapshot
clears its button intent, so running audio always offers Pause. A quick resume
can cancel an in-flight pause fade.

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

The September 29 implementation reuses complete imported waveform streams and
adds only a 1-ms signed detail supplement. Sonny accepted the isolated visual
candidate; production validation/install status is in [HANDOFF.md](HANDOFF.md).
The [reuse audit](archive/waveform-reuse-audit-2026-09.md) separates measured evidence from
remaining visual/format acceptance. Existing caches remain usable during gradual
conversion; launch does not invalidate the whole library.

V5 analysis is owned by SoundBox. For an existing Rekordbox library track, the
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
fingerprints are refreshed into V5 through atomic manifest-only updates until
a future SoundBox edit claims ownership. Correcting Rekordbox's initial
automatic beat grid therefore reaches SoundBox on the next live-library
refresh without reanalyzing audio, and waveform payload bytes never change
during either metadata refresh.
Complete, consistent PWV3/5/7 imports with a grid use `waveform_detail.DetailSupplement`:
no FFT, filter bank or beat detector runs. The source is decoded once in bounded
chunks for exact song-anchored 1-ms stereo-midpoint signed extrema. The overview
comes from PWV5. Incomplete imports or unsupported supplemental decoders retain
the existing full analyzer and its generated display modes/spectral master. No player code opens Rekordbox files for writing. No SoundBox
operation updates `master.db`, ANLZ data, or source audio.

## SoundBox-owned caches

All derived caches are disposable accelerators under
`~/Library/Application Support/LowFreqLabs/Soundbox/`:

| Directory/file | Owner and purpose |
|---|---|
| `libcache.json` | Last read-only library snapshot for instant first paint |
| `track_analysis/*.sba5` | Canonical V5: checksummed Zstandard sections for source identity/provenance/export eligibility, full grid/cues, 150 Hz BLUE/RGB/3BAND streams and overview; compact imported records store one signed 1-ms base, while fallback/legacy records retain spectral analysis and signed-range mips |
| `track_analysis/*.sba4` | Read-only compatibility input during bounded one-record-at-a-time V4 migration |

Each V5 record is atomically replaced, keyed by track ID plus audio identity.
Interactive loads validate only the requested section SHA-256; cache audits
also verify the complete compressed payload. A
changed/new track prepares only its own record. Storage-only cue/grid mutation
support is a dormant boundary for later work; no manual grid editor exists.
File-provider storage migrations are not audio changes. If size/mtime identity
drifts, SoundBox recomputes the record's existing inexpensive fingerprint using
its recorded size/mtime and the current first/last 128 KiB. An exact match
stores a tiny cache-bound extended-attribute proof and reuses every waveform
section without rewriting or decoding the source; any content/size mismatch
remains a normal recook. A dataless File Provider placeholder is never hydrated
by startup verification: matching logical size provisionally retains its
existing cache, while a new or size-mismatched placeholder remains stale
without being background-decoded. Playback, export, or an explicit recook may
deliberately access the source, after which the ordinary verifier can prove the
new local identity. Healthy exact or
previously proven identities remain metadata-only checks on later scans.
Tracks globally removed from Collection are outside this active cache universe:
the removal drops their exact V4/V5 records, recurring preparation skips them,
and an in-flight bulk or interactive cook cannot republish after the Graveyard
change. Startup also reclaims records left by older builds. Selection-scoped
restore returns a globally removed track to ordinary bounded preparation.
The read-only analysis audit uses this same reachable-track boundary, reports
offline dataless misses as deferred rather than broken, and lists any records
outside that boundary as orphans without deleting them.
Deferred/missing sources and invalid or outdated analysis block global V4
retirement. A full audit must verify current V5 for every active track and find
no orphan V4 records before reporting safe. V5 checksum failures are invalid
analysis, never playable V4. Cache verification does not validate every source
audio byte or prove that offline placeholders can be decoded.
Background late-waveform recooks also defer dataless sources; queued recooks
recheck after playback/storage waits. Metadata-only cue/grid changes can still
use an existing valid cache, and explicit forced reanalysis may hydrate audio.
Playlist-only removals retain analysis because the track remains active in
Collection or other playlists. No cleanup touches source audio or read-only
Rekordbox analysis.
The recurring scan also detects the narrow late-Rekordbox case when sidecar
identities change (with a bounded fallback retry), without polling all ANLZ bytes.
A current compact record stays on the small-manifest path. A legacy detail model
or mode whose provenance still says `soundbox-analysis` may probe its current
read-only PWV3/PWV5/PWV7 payload; unchanged ineligible records reuse receipts. A newly valid stream
queues only that track for a normal atomic recook. The browser's per-track
**Reanalyze Track** action remains the explicit recovery path and deliberately
bypasses the ready-manifest shortcut for only the selected record. Both routes
decode the unchanged source again and publish a verified V5 replacement. The
old playable cache stays in place if decoding or storage fails, a loaded deck
hot-refreshes after success, and neither source audio nor Rekordbox data is
written.

The fallback analyzer streams PCM in bounded memory, derives approximately
6144 peak windows/second, keeps a normalized 24-band master, and builds the
three 150 Hz display-mode fallbacks from overlapping second-order Butterworth
paths:
LP 100 Hz, HP 300 + LP 3000 Hz, and HP 1500 Hz. One-millisecond band peaks use
approximately 25/3/1 ms low/mid/high holds before a local per-column energy
transfer, overlap correction, green restraint, blue lift, and compact
quantization. When no valid Rekordbox grid exists, the analyzer derives a
constant-tempo grid from FFT autocorrelation and anchors beat 1 at exactly
`0:00`. The fallback path is NumPy-only and has no runtime JIT.
For incomplete imports, Qt/FFmpeg feeds the same analyzer for AAC/ALAC set rips.
Complete imports use the compact native-rate supplement below. Export eligibility is
deliberately separate: only MP3, WAV, AIFF/AIF, and FLAC are future-CDJ
candidates; AAC/ALAC/M4A/MP4 are always player-only.

Fallback/legacy geometry is analyzed at approximately 6144 cells/second from the
stereo midpoint, which preserves the
attack/decay contrast lost when the louder channel was allowed to inflate every
row. A 1/16 peak-channel safety floor retains genuinely wide or anti-phase
signal that cancels at the midpoint. V5 persists the literal signed per-cell
minimum/maximum after exact song-anchored extrema reduction at divisors
`8/13/18/25/35/50/66/99/132/192/256` under
`soundbox-polarity-range-mips-i16-v5`. Those levels are the union selected by
all supported zoom stops at macOS scale 1 and Retina scale 2. The renderer
uploads them directly; it does not reconstruct and retain four redundant
6144 Hz base streams. Mirroring one unsigned height cannot reproduce the
oscillating trace. The compact overview remains a symmetric maximum for its
28-point-high lane. The 150 Hz overview uses complete,
non-overlapping 1/150-second cells; it never reuses the wider spectral FFT
window, which previously looked ahead about 46 ms, rounded attacks, filled
decay valleys, and omitted the final seven rows.

Current native upload uses linear amplitude, a 0.84 ceiling and the continuous
low-end shoulder; digital zero remains zero. The former `height^1.4` curve was
superseded by the September 29 comparison. RGB channels use divisors 14/7/7.
PWV7 retains its separate band scales and membership palette.

Background eligibility discovery reads packed PWV3/PWV5/PWV7 rows and PQTZ
directly; it never expands whole-library per-sample color lists or generic ANLZ
objects just to schedule upgrades. Full cue/display parsing remains per-track.

Compact V5 is distinguished by `soundbox-rekordbox-supplement-i16-v1` and
`rekordbox-pwv5-rgb-v1`, not by a new file extension. It stores only divisor 1;
load builds `1/2/3/4/6/8/12/16/24/32/48/64` mips in memory. It omits the unused
24-band master. Legacy V5 remains ready/playable while the recurring background
worker upgrades eligible local records one at a time, without requiring selection.
A complete import is required; incomplete imports retain their valid fallback.
An interactive load can finish a selected upgrade ahead of the queue. The queue
rechecks completion and dataless status after waiting, never runs a full analyzer
when a compact upgrade fails, and retains the prior record on cancellation/failure.
Manual grid/cue ownership and authoritative DB cues survive conversion. Preparation
receipts include the compact model, so older ready receipts cannot suppress the
new work. Reanalyze Track uses the same import-first path. Dataless old-cache
upgrades defer until the source becomes local; no background download is requested.

The compact import path also supports local AAC/ALAC through QAudioDecoder when
libsndfile cannot open the source. It retains the decoder's native sample rate
and channels and feeds the same absolute 1-ms signed-cell reducer, without a
full analyzer or another spectral/color pass. Source identity is checked around
the whole decode; cancellation/failure cannot publish partial detail.

October 9 rapid-load correction: the native waveform host skips materializing
an unused Qt envelope on the UI thread. Packed PWV5 visibility repair uses
vector operations with the same nearest authored-color/tie behavior. Cancelled
workers recheck after detail decompression, and native display-stream uploads
check their track generation before preparation and again under the publication
lock. Cue slots avoid repeated refreshes for equal data and repolish only when
the filled state changes. Shader geometry, palettes, zoom levels and scrolling
are unchanged.
Regressions cover production native delivery of four-minute detail, stale stream
rejection, scalar/vector color equivalence, and native-rate AAC/ALAC supplements.

Tagged MPEG-1 Layer III LAME files have an explicit source origin derived from
Info/Xing frame length, encoder delay and 529 synthesis samples. Both supplemental
analysis and native playback restore that lead-in; seeks subtract it when mapping
to decoded PCM and duration includes it. Native header reads occur only on the
decode thread. The native decoder must match the header's valid-frame count and
sample rate or reports an error. Unknown headers retain the decoded clock; never
apply Nuclear Bass Face's 2257 samples as a universal constant. Legacy signed
contours are sampled with the same origin shift until replaced. Imported grid,
cue timestamps and colors remain unchanged.

V4 stays playable during compatibility migration. A V4 reduction and a fresh
audio cook use the same extrema routine and must produce byte-identical V5
waveform sections. The old record is removed only after the new record passes
schema/source identity, every-section SHA-256, and whole-payload verification.

The legacy generated RGB fallback retains its earlier filterbank compensation;
this is separate from the tagged-MP3 source-clock mapping above. Compensation is
format-specific. Lossless PCM (WAV/AIFF/FLAC) has no correction. MP3 retains
the measured seven-row / 46.7 ms decoder correction. Applying that MP3
correction globally made lossless color arrive seven rows late, so a kick
could inherit the preceding clap/snare hue. `soundbox-rgb-filterbank-v2`
versions this corrected timing contract.

The zero-anchor contract has a one-time metadata-only migration for legacy grid
provenance. It reads only the small read-only DAT/PQTZ grid, never EXT/2EX
waveform/color sections. It atomically replaces only the canonical manifest
while preserving the compressed waveform payload byte-for-byte.

The background `WaveformFillWorker`:

- scans the library incrementally;
- starts for both newly detected rekordbox tracks and direct SoundBox imports,
  then rechecks source identities every 60 seconds after the preceding pass
  completes. Disposable `preparation-index.json` receipts bind source/cache and
  DAT/EXT/2EX identities, cue inputs, BPM and analysis policies. Unchanged entries
  need stats only; changed entries share one manifest-header read. A 30-minute
  fallback retries silent sidecar parse failures. Late sidecars invalidate their
  track's receipt, allowing a targeted read-only probe for fallback modes and
  recooking only that track. Forced reanalysis bypasses receipts; player section
  validation and source-identity proof requirements remain independent;
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
- uses a 16 MiB worker stack and a 20/25 GiB full-cook pause/resume guard;
  one-record V4 reductions and compact replacements use the 5/7 GiB migration
  reserve and run before full cooks; each keeps only one replacement in progress;
- names low disk space in its pause status and exposes the applicable resume
  threshold on hover; status text fits its full message and the pause is restored
  after higher-priority device work finishes;
- keeps the discovery scan silent and reports `Preparing your library…` only
  after actual analysis or metadata work is found; progress uses a separate,
  width-reserved count rather than changing the operation label on every track;
- can hot-load newly completed detail for the current track;
- is interrupted and joined during shutdown.

Interactive PlayerBar waveform workers now share `TaskOwner`: close admission,
request interruption, dispatch Qt events through exact worker retirement, then
close the native backend. Late analysis results cannot update the deck during
shutdown. The unchecked five-second waits identified in the September 27
[software audit](software-audit-2026-09.md) have been removed.

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

The whole-track overview normalizes every positive amplitude color weight
exactly, including quiet tails. Imported PWV5 can round height down to zero
while preserving valid color; the imported compact overview may contain that
same zero, so it cannot independently prove silence. When compact color weight
is zero, BLUE/RGB overview coverage uses the existing high-precision audio
envelope and retains authored hue. True zero audio stays background; PWV7
3BAND keeps its independent geometry. Existing nonzero compact shapes, the main
waveform, clocks, canonical cache bytes and source audio remain unchanged.
The correction applies to all tracks through the shared runtime compositor,
without recooking or per-track exceptions.

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

SoundBox-derived RGB has a provenance-gated compatibility repair of its own.
Early V5 cooks correctly stored the compact overview RGB but mistakenly passed
those already-quantized channel codes through a second display-range quantizer
when packing PWV5 detail. The remaining red code landed in PWV5's green field,
so SoundBox-only files could appear green at close RGB zoom. On upload, only a
stream whose manifest says `soundbox-analysis` reconstructs its PWV5 color bits
from the canonical, same-clock compact overview and preserves every cached
height/control bit. This takes a bounded vectorized copy, leaves canonical cache
and source bytes unchanged, and never applies to Rekordbox-authored PWV5. New
cooks pack the compact codes directly and therefore need no compatibility
repair; existing long Sets need neither hydration nor audio recooking.

## Why the waveform remains stable

The following combines current rendering rules with explicitly labeled historical
measurements. The compact-cache and source-clock contracts above take precedence.

- BLUE and RGB share persisted signed minimum/maximum detail and a
  literal-pixel interval trace; only their PWV3/PWV5 color compositors differ.
  3BAND uses only its three packed PWV7 heights and must never be clipped to
  the signed audio trace.
- Pyramid levels remain song-anchored. Exact display pyramids retain peak
  geometry and amplitude-weighted color. The higher-rate signed detail uses
  compact runtime ladder `1/2/3/4/6/8/12/16/24/32/48/64`, or the legacy
  persisted ladder for older/fallback records. Blending adjacent lattices
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
  competitive. Fallback records retain the 24-band master independently and neither Metal
  nor Qt may remix it into display color. PWV5 RGB expands its compact channels
  with `14 / 7 / 7` divisors and linear interpolation, and only PWV7 enters the
  layered 3BAND compositor.
- `color_model` versions the persisted RGB analysis independently of the V5
  container schema. Old records remain playable during background or selected-track upgrades; each temporary record lives beside
  its target, and SoundBox-owned grid/cue rows survive the upgrade.
- Derive one footprint-matched LOD from source samples per physical pixel and
  select one peak level. The overview and main lane call the same sampler;
  high-rate detail refines geometry only, never timing or color.
- Print height and amplitude-weighted RGB onto a fixed, global song-coordinate
  attribute lattice, then translate those attributes. The lattice stores no
  alpha or vertical coverage, so it is not a pre-rendered waveform texture and
  the final drawable never refilters an already-antialiased edge. Every zoom
  uses four attributes per physical pixel. RGB/BLUE round only the display
  offset: physical-pixel increments through 384 points/s, whole logical points
  at 576–1500. Position error is at most half the chosen increment; 3BAND
  retains linear subpixel translation. The October 6 accepted RGB column
  presentation is paired with this motion policy. Earlier one-attribute
  integer-motion experiments had different source/coverage rules and were
  rejected; their evidence is historical, not acceptance of the current path.
- **The display-stream LOD is footprint-matched and `kLodBias` must stay 0.** A physical
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
- **Select one conservative level for the physical source footprint.** Compact
  signed mips use integer millisecond divisors; legacy V5 uses its persisted
  ladder. Only the three widest stops use the documented half-pixel footprint.
  Earlier power-of-two PWV alias measurements are historical, not a description
  of the compact signed ladder.
- **Use one mip level, never a blend of two.** Levels remain song-anchored.
  Blending independently quantized levels can create phase beating; the thirteen
  fixed zoom stops do not require such a blend.
- **Preserve source cells at close zoom.** BLUE/RGB use held signed extrema;
  3BAND connects the original 150-Hz rows linearly. The former paired-row
  rule, four-pixel close footprint and height compensation are historical.
- **Close detail uses a physical-pixel source footprint.** One selected signed mip
  feeds the fixed four-attributes-per-pixel song lattice; it does not broaden
  the final spatial integral or apply divisor-dependent height gain.
- Rebuild the small private attribute buffer only when analysis, zoom, Retina
  scale, or its guarded song-space margin changes. The buffer is deliberately
  wider than the drawable, and every recenter is computed from an absolute
  integer song coordinate, so overlapping regions are identical and no tile
  seam can enter the viewport.
  Each canonical 128-attribute clock block has CPU-double-derived sample anchors
  and local phases. Different overlapping tile origins must reproduce the same
  cells; recomputing large local phases per tile could round held cells onto
  opposite sides of a boundary. The October 2 regression checks both origins.
- Rasterize final vertical coverage exactly once at each Retina drawable
  pixel. There is no moving RGBA texture, coverage resampling, accumulated
  frame, or refresh seam. Height and hue move together, while final edge
  antialiasing remains tied to the destination pixel. The scrolling lane uses
  joint signed-interval integration without the overview vividness curve.
  BLUE composites its area directly; RGB applies the deliberate conservative
  column coverage described below. Historically, boosting 20% coverage to nearly 49%
  made saturated red/orange edges visibly pulse even at 24 px/s. The small
  overview alone retains the continuous cubic vividness curve for true
  subpixel peaks. This distinction affects only compositing, never height,
  hue, LOD, or detail.
- Integrate the fixed height/hue profile over exactly one physical pixel before
  final vertical coverage. BLUE/RGB retain five quarter-pixel boundary samples
  at 64–184 px/s. At 24–48 and 256–864 (BLUE/RGB only), and 1152–1500 px/s
  (all modes),
  use the pixel endpoints plus
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
  interval that actually covers the destination pixel. Upper/lower boundaries
  intersect pointwise before integration, rather than after it. Within that coverage,
  clipped-linear moments integrate PWV5 color in premultiplied linear light;
  a horizontally hidden color sample can no longer tint a visible RGB edge.
  RGB then increases coverage within occupied physical columns using their
  extrema, retaining that conditional color. Existing minimum-width stroke
  coverage cannot decrease, and zero joint coverage stays empty. This accepted
  October 6 presentation can thicken fine detail; it is intentionally not an
  exact pixel-area integral. Source cells, extrema, LOD and colors are retained.
  BLUE evaluates its white/cyan/blue radial transfer from the same local
  signed center and width. It splits at coverage/radial branch changes and
  integrates each smooth branch with four-point Gauss-Legendre quadrature in
  premultiplied linear light; a single coverage-centroid color evaluation is
  insufficient for the nonlinear field. Averaging color independently from interval
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
  shutter only doubles. Taps stay flat weighted across one physical pixel to keep a moving contour
  crisp. Coverage integrates the resulting profile intersection; profile sampling is
  separate from the coverage integral.
- Filter final silhouette coverage—not raw peak height. Geometry and color are
  reconstructed from the same level and phase, preventing the contour from
  acquiring a separate translucent edge.
- The direct fallback path still integrates exact area under its tap profile,
  never the mean of the taps' individual coverages. The normal audio-detail
  path instead interpolates height/hue first and applies one continuous
  one-physical-pixel vertical coverage band at final compositing. In neither
  path may per-tap alpha be averaged or an already-rasterized edge be sampled
  again.
- The small overview alone composites coverage through a continuous cubic
  ease-out so one-physical-pixel quiet passages remain visible. The scrolling
  lane uses joint geometric coverage with the explicit RGB column policy above;
  applying overview vividness
  there made entering/leaving saturated edge pixels pulse.
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
and C defaults remain focus-only and never steal shortcuts from Ableton, Terminal, or
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

Historical experiments and installation measurements are preserved in the
[player history](archive/player-history-2026-07-to-09.md). They are evidence,
not current implementation instructions or pending work.

## Deferred player work

Manual grid/cue editing needs a UX workshop before implementation. Storage and
export-field mapping exist; a manual editor does not ship. Rekordbox remains
read-only. See [ROADMAP](ROADMAP.md) for scope and sequencing.
