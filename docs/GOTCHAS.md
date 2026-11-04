# SoundBox gotchas & niche facts

Hard-won + researched. **Read before touching workers, playlists, sync, art, or
the iPod path.** Related: `ARCHITECTURE.md`, `ROADMAP.md`.

## Preparation priority and harmless metadata drift — November3

- Compatibility preparation is a prerequisite; local waveform/BPM/key work then
  precedes new native DB writes. Persist wave completion and each detector phase.
  Never run key during a supposedly BPM-only helper call. Pins bind every stage
  to the same full audio SHA; do not silently substitute changed bytes.
- Deferred cue/grid intentions count as pending work, but must not count as an
  active source worker when admitting required conversion. Otherwise local prep
  and the deferred writers deadlock. Later import jobs may preflight compatibility
  without reordering source writes. Ack the selected job by exact ID/index.
- A cue priority timer must not overlap another source task. Closing admission
  still permits accepted queued cues to drain; treating `_closing` as a ban on
  that drain strands normal quit.
- `native_media.metadata_identity_proof` is worker-scoped and selected by validated
  path/SHA/size/first-four epoch fields. Ctime-only changes get a stable exact-byte
  check, with at most8fresh full-epoch proofs reused within the worker. Default
  MediaIdentity remains strict. Continuous metadata motion waits quietly with
  backoff. Plans, saved results and recorded epochs are not rewritten, and
  changed bytes/path/inode/size/mtime remain protected. Reconcile exact committed
  receipts before treating an unfinished newer detector job as a publication gate.
- Cache publication must bind the captured source epoch, rather than write old
  waveform data with a newly changed file header. Reject before atomic replace
  when the source changes; metadata-only retry reuses decoded waveform data.
  Background discovery snapshots mutable track dictionaries: importing mid-scan
  must not raise `dictionary changed size during iteration`.
- Selecting an existing Memory Cue also moves the regular temporary CUE anchor
  to that exact target. Do not quantize recalled saved positions or emit cue-create
  signals; hot-cue preview behavior and all saved cue rows remain unchanged.

## Browser BPM and inconclusive detection — November3

A saved key does not imply an accepted BPM: detector confidence below0.5 clears
its BPM/grid. STINK MODE and SUPERSONIC exposed a second display path: loading
their waveform supplied a zero-anchor estimate through Prep.refresh, but background
completion did not update Collection. Background cache hits/completions now emit
source-bound, header-only presentation snapshots. The GUI checks the exact worker,
Library and track binding; native/saved detector/manual BPM wins. Invalid caches
clear the estimate. The tooltip labels it; it never enters analysis_results or a
native save request. Do not lower confidence or treat a cache as source authority.
Manual drafts over an empty detector grid must resolve before playback's empty-grid
test, after unchanged source-identity and native/manual provenance guards.

## Native playlist writer — accepted October30

- A local membership veto is not a rekordbox save. The writer replaces the
  removal gesture with a durable, identity-bound source intent and leaves the
  verified native membership intact until publication. A durable matching-identity
  deletion hides the sidebar row immediately through a presentation projection;
  create/rename and membership edits now project the same durable queue into the
  sidebar/browser, including nested pending creations. The verified revision stays
  separate from these display rows; newer snapshots must reconcile receipts first.
  Background creation completion must not steal the user’s later navigation. This view
  never changes that source catalog or grants source authority. Failure/discard
  rebuilds against the latest verified catalog and requests a fresh serialized
  snapshot; a read completion must retain the pending/error status. An error says the edit
  needs attention; interrupted publication may already have committed. Explicit
  retry replays a committed receipt without duplicate creation, or persists a new
  operation/revision/clock before attempting a previously rolled-back/stale edit.
- Use the same Tag/Cue/Grid owner, lock and journal. Source authority is an
  explicitly provisioned exact library/schema/version file, never a cached tree.
  Pending work waits while rekordbox/agent are open. Unknown recovery blocks all
  domains; source audio, Collection rows, cues and analysis are outside SQL scope.
- Native deletion differs by row history. Unsynced rows delete physically; the
  approved older two-entry reference keeps playlist/member tombstones and uses
  three counter groups. Single-entry removal updates every active survivor in
  one group, even unchanged TrackNo values. Missing legacy XML is not universally
  created: ordinary membership edits can add a zero-clock node, but deletion
  retains its absence. The native empty/folder and mixed-member references also
  pass; bulk UI gestures compose the proven single-entry edits.
- A persisted playlist FIFO is not TagEditController.owned: its pump waits for
  that owner. Including its own pending jobs in owned deadlocks admission. CDJ
  export checks both independently, waits without reserving a device operation,
  and rebinds native playlist IDs after saving. Deleted selections/source changes
  cancel before capture; mount/UUID and standalone intents survive deferral.
- Preserve removed songs in Collection using exact native UUID pins bound to the
  source library. These pins are display retention, never registration or source
  permission. Cache schema11 retains raw playlist rows/clocks, empty folders and
  distinct logical/native IDs and raw native alias paths;
  encode its catalog one row at a time, as with tracks. Intelligent memberships
  retain their computed keys when their containing folder is renamed.
- Acceptance captures keep full encrypted pairs. Normal retention keeps
  the latest full pair and permanent row/XML/operation receipts. Older completed
  DB payload retirement must verify receipts/hashes, journal its own progress and
  resume safely; it must never touch unresolved or native-acceptance evidence.


## Float-WAVE import conversion — November1

- This import adapter is the explicit same-path exception before new-track
  registration. Existing registered Collection conversion uses the separate
  accepted media-conversion authority/controller and locator API; never route it
  through INSERT-only registration. Device originals remain immutable.
  Keep the common source worker/lock and fresh native catalog guards.
- Same gain is intentional: Sonny preferred the clipped PCM24 audition to the
  quieter alternative. No automatic normalization. Non-finite/malformed samples,
  source races, redirected recovery and unrelated occupants fail without overwrite.
- Persist conversion acknowledgement before registration or the next file.
  Recovery verifies exact original/stage/output hashes. A failed local save must
  replay the receipt, not convert again or discard manual musical edits. Saved
  detections retain original SHA/epoch provenance plus the verified conversion.
- Fence existing waveform publications before replacement. New interactive cooks
  are transient while that import phase owns the lane. Refresh detail afterward
  without replacing the audio transport or redetecting BPM/key.

## Graveyard information and confirmed disposal — November1

- Select from the visible Graveyard before browser/deck fallback. Button focus
  does not change that target. Historical native rows are read-only info evidence,
  never active Collection entries or general edit authority.
- Count storage with stat/inode identity, without audio decode/hydration. Exact
  old native rows can recover paths; `track <ID>` and title matches cannot.
- Different native IDs can reference the same file. VOLATIVE’s removed93738342
  and retained259251743 are a real same-path example, not two audio files. Keep
  the file for the retained entry and show this explicitly in the Delete review.
- Disposal is a separate accepted writer, never a playlist/cue authority bypass.
  Persist concrete confirmation before mutation; keep the shared TaskOwner lane
  through finished delivery/join and shutdown. Unknown journal prefixes block all
  domains. Recover SQLite’s owned hot journal before making read-only snapshots.
- Confirmed macOS disposal uses deterministic UUID-tagged exclusive same-volume
  moves, source-parent fsync and exact-target F_FULLFSYNC. Opening/enumerating
  protected Trash can fail even when the move is allowed; do not require that
  access. Recovery checks exact tagged paths before legacy OS-name discovery;
  never scan/hash unrelated Trash audio. The old QFile route is historical.
- Metadata-only totals are not reclaimed capacity: Trash still occupies storage.
  Retain SoundBox musical/curation data, latest encrypted pair and permanent small
  receipts. Retire only verified older completed generated DB payloads.

## Repo hygiene
- Current guidance and historical evidence are separated; see
  [project layout](PROJECT_LAYOUT.md) before cleaning ignored files. Required
  fixture/recovery directories are not disposable build output.
- **NEVER pipe `./check.sh` through `tail`/`grep` in a `&&` chain** — the
  pipeline exit code is `tail`'s `0`, so a FAILING gate gets masked and the
  build/commit proceeds. (Real incident: a syntax-broken module shipped + got
  committed.) Run `./check.sh` bare (or `> file 2>&1; echo $?`), then build.
- Project uses local `main`, tracking `origin/main` on the Low Freq Labs GitHub
  repository. Preserve any existing dirty tree and inspect `git status` at
  handoff. Do not commit or push unless Sonny explicitly asks; saying
  **“wrap up”** is standing approval to validate, commit the intended completed
  changes, and push the current branch.
  `dist/`, `build/`, `.venv/` are ignored.
- **`AGENTS.md` and `CLAUDE.md` are synchronized runtime equivalents.** A shared
  rule changed in one must change in the other in the same edit. Shipped
  behavior/status belongs in the relevant docs; committed work goes in
  `ROADMAP.md`; exploratory UX belongs in `docs/IDEAS.md`.
- **Installation must preserve rollback and AppData.** Back up/hash
  `library.json`, gracefully quit, move the old app bundle into a temporary
  rollback directory, install with `ditto`, verify/relaunch/log-check, and
  compare curated state. Never use `rm -rf /Applications/Soundbox.app`; never
  replace or clear SoundBox AppData as part of an app install.

## Historical offline native playlist references

- Root playlist creation and renaming are separate native operations. The isolated
  6.8.7 reference increments the local counter once for each, retaining the new
  playlist identity and creation time across rename. Do not infer a one-operation
  counter formula from the earlier combined create/name sample.
- The observed last-root single-member deletion removes DB playlist/membership
  rows while preserving the entire content table and byte-identical playlist XML,
  even after normal quit. Its retained XML node is not an orphan to clean up.
  At that checkpoint other deletion shapes were unaccepted. Paired copy recovery
  and the narrow source pilot subsequently passed, followed by the broader
  ordinary API acceptance described above. Registration/analysis remain separate.
- Explicit frozen-copy replay is strict by default. A reference-only
  notificationNextFetch.updated_at difference can be audited explicitly, but
  must remain visible as complete_semantic_match=false. Never rewrite the
  notification row to make a comparison pass or transfer that research exception
  into a source writer. See [the current proof](analysis-playlists-next.md).

- A native snapshot can retain a WAL header even after folding committed pages.
  Normalize only newly created private recovery copies to DELETE journal mode;
  never normalize the source to simplify a proof. Stable hash identities exclude
  atime (the first read can update it), while inode/size/mtime/ctime stay bound.
- Check XML again after recovery inventory reads and immediately before rollback.
  The standalone pilot holds SQLite's writer lock while validating the pair and
  restoring XML. Its transport lease reuses native guards, not Cue permission.
  The historical standalone pilot's exact-plan approval is never consumed by an
  app loader. Ordinary playlists later passed a separate October30 acceptance and
  use their own explicit authority; analysis publication remains separate.

## Stable native waveform loading

- Interactive first paint waits for complete mode streams and signed detail;
  manifest/grid/cues still become ready first. A compact-only preview followed
  by full detail changes the main silhouette and palette. Do not reintroduce a
  duration-specific early commit. Partial caches wait for repair. A selected-load
  worker saves its own valid-cache upgrade for the next load; separate fill
  completions and explicit reanalysis retain targeted loaded-deck refresh.
- Grouped uploads must check their depth inside the render mutex before taking
  a buffer snapshot. Checking at draw entry alone races with an upload starting
  before that snapshot. Restore the dirty flag on a skipped draw, and advertise
  scheduled presentation only after a valid snapshot. Capture its waveform
  revision under that same lock; reading a newer revision after unlocking can
  retain old attributes for a new complete buffer.
- Public compact mode0 uses positive upper/lower magnitudes; BLUE/RGB modes1/2
  use signed intervals. This sign contract remains separate from publication.
- Interactive detail reads can omit spectral_24, which has no renderer consumer;
  full default APIs and audits still validate it. All displayed sections retain
  checksums. The bounded signed-transfer lookup must reproduce the original
  scalar's65536 outputs exactly; it changes cost, never geometry or palette.

## Qt / PySide6 (these bite HARD)
- **Tree stripes and embedded controls need explicit geometry.** QTreeView paints
  alternate backgrounds only for real items. Device BrowserDetailTree extends
  the same 24-point grid through empty space without fake rows. Legacy embedded device
  controls need fixed24-point height to avoid enlarging uniform rows. Native USB
  General now uses a separate28-point two-column form. Shared device headers
  match the 26-point sidebar line.
- **Run Cocoa cadence checks without competing preview windows.** A capture
  harness with an always-on-top window can fully cover the native test host;
  macOS then stops its drawable cadence. Finish visual captures before the full
  native gate and preserve failed exploratory evidence; do not lower thresholds.
- **Python worker tests need an event loop that yields the GIL.** Repeated
  QTest.qWait calls can starve first NumPy/SciPy imports in a QThread even
  while native audio plays. Use QEventLoop with a bounded timer and the owner's
  drained signal, or join the worker then dispatch its queued delivery. Do not
  treat that test-only starvation as a playback timeout or suppress the check.
- **Choice checks belong in the right gutter.** Use desktop.choices Menu and
  ComboBox across app menus, settings and USB controls. Qt retains navigation,
  activation and accessibility; QListView popups avoid Cocoa's left check gutter.
  Keep text/icon-aware minimum sizes and inspect open Cocoa popups with
  `--settings-analysis --analysis-menus` and `--theme-text-menu`. Menu QSS must
  reserve enough right padding in the action geometry itself; enlarging only
  QMenu.sizeHint leaves action rectangles narrow and clips custom text.
  A proxy style must own a fresh base, never QApplication.style().
- **Folder moves must preserve custom playlist identities.** Keep membership in
  `custom` and organization in the bounded `custom_playlist_tree` settings tree.
  Export the actual folder_path while retaining `cust:<name>` identity; do not
  encode the folder path into a new playlist name/key. Folder deletion needs its
  own explicit descendant-playlist review and never deletes Collection/source audio.
- **Sidebar layout uses common vector bounds.** One delegate paints folder
  chevrons and note/star/home paths with the same icon center, label start and
  24-point row geometry. Never mix font-glyph bearings with a native branch
  painter again. Fill the complete selected panel; preserve Qt's model, branch
  hit testing, drag and folder-only navigation.
- **Graveyard is an audition input surface.** Admit playback/cue/grid/wave/autoplay
  actions and selection-bound Load, while retaining native selection and blocking
  unrelated browser edits. Disable native input-method/typeahead ownership so
  held C reaches the keyboard router. Standard Cmd+A selects all before
  configurable grid bindings dispatch. Auditions capture no queue; existing cues
  remain playable while new/deleted cues wait for restoration. General settings
  editors and dialogs retain their ordinary input boundary.
- **Folder navigation is not playlist activation.** Check PATH_ROLE before
  clearing the other sidebar's selection. Local folder rows likewise leave the
  active view alone. A folder can hold navigation focus while refresh/drop/track
  actions still address the last chosen playlist. Retain a persistent pressed
  index for folder drags; selectedItems alone can describe the active leaf instead.
- **Native checkbox layout margins can overlap custom menu rows.** My Tags
  gives its checkbox cells explicit rectangles inside the QWidgetAction, instead
  of allowing macOS layout-item margins to compress the grid. Its two-column
  menu matches the deck button width and fills down the left column, then down
  the right in source order; long labels use one column and tooltips.
  QMenu caches its first action size, so update the frame from content geometry
  plus native menu metrics. Verify catalog/layout changes on Cocoa, not only
  offscreen.
- **SVG CSS custom properties need a Qt adapter.** Keep the supplied SVG intact
  in assets; Wordmark resolves SOUND/BOX fills in memory and crops its viewBox
  to the outline. Preserve the former 270×55 header footprint. Include the SVG
  in both PyInstaller data and the source fingerprint; source-only tests that
  copy desktop code must also provision that asset.
- **Accent changes must not repolish the whole app.** Install static QSS once;
  `theme_controller` updates visible accent declarations and hidden widgets on
  Show. Custom menu/combo painters read theme roles directly. Refresh the wordmark
  only when its accent changes. QColorDialog previews coalesce at16ms without
  saving settings; OK commits once and Cancel restores the prior visual roles.
  Reapplying QApplication.setStyleSheet cost1.6–1.9seconds on the measured page;
  native component updates measured13–14ms and text changes7–9ms. The scripted
  Cocoa capture uses asynchronous native open because programmatic reject can
  strand Cocoa's helper exec; production retains the ordinary user-driven dialog.
  Preserve external inline styles such as musical cue rims. Persist the accent in `settings.theme_accent`
  and lettering independently in `theme_text_mode` / `theme_text_custom`; never
  replace the user's text choice with a luminance-based black/white decision.
  Saved-state reload/restore applies the same path. Fixed cue/analysis colors are
  data, not theme chrome. The render harness takes `--accent=#RRGGBB` and
  `--theme-text=white|black|#RRGGBB` and always
  resets its default to avoid prior fake-state colors leaking into snapshots.
- **App icon SVG colors need explicit roles.** Preserve the user-supplied SVG
  bytes; resolve its CSS class roles and currentColor in memory for Qt. Render
  native ICNS sizes from the vector and retain PNG representations; legacy
  iconutil encoding/readback changed transparent-edge RGB. The running Dock icon
  changes only on saved color/launch, independently of picker preview.
- **Widget-local QSS outranks application selectors.** Never apply generic accent
  hover rules to CueBtn/DeckPlayBtn: they can override fixed orange CUE even when
  the app rule has a named active selector. These controls receive only named
  accent rules; hover must paint exactly like rest/playing. PLAY feedback is
  fixed green#00c301 and CUE fixed orange, independently of theme. Production pixel
  checks cover three accents, mouse/keyboard states and release.
- **A tooltip must add information.** Never assign a cell's displayed string as
  its tooltip just to repeat fully visible text on hover. Keep tooltips for icon
  meaning, warnings, hidden diagnostics, or genuinely richer content.
- **Classic-iPod playlist IDs are unsigned 64-bit values.** Never carry them in
  a Qt `Signal(int, ...)` (signed 32-bit) or store the Python integer directly
  in a `QVariant`/item role (signed 64-bit). Real Apple IDs exceed both ranges;
  the former prevents delete slots from firing and the latter can abort a tree
  render halfway through. Preserve the exact decimal string across every Qt
  boundary and convert to Python `int` only at the stock-iPod engine call.
- **QThread "destroyed while running" → SIGABRT.** Never let the last Python ref
  to a worker QThread drop *mid-run*. Slots on a worker's CUSTOM signals
  (`finished_all`/`done`/`failed`) run WHILE `run()` is still finishing — nulling
  `self.x_worker` there GCs the thread on its own thread → abort. FIX (in place):
  drop the ref only on the built-in `finished` signal via
  `MainWindow._finish_worker(attr, w)` (main thread, after `run()` returns). Used
  for export/refresh/resync workers + `PlayerBar._wf`. Don't regress it.
- **A custom worker result signal is not a device-completion boundary.** `done`,
  `finished_all`, and similar signals are emitted inside `run()`; the QThread and
  its UI ownership still exist. Device workers store their result, consume it
  only from the built-in `finished` signal, clear the owning attribute first,
  and then enter UI result/finalization handling. Newly connected rows, Eject,
  Sync, success dialogs, and 100% progress must all follow that same boundary.
- Accessing a `deleteLater()`'d worker raises `RuntimeError: Internal C++ object
  already deleted`. Guard with the `_running(w)` helper.
- `shutdown()` (from `closeEvent` + `app.aboutToQuit`) must stop every
  worker BEFORE Qt teardown. Keep Qt events alive until actual completion;
  timed waits are not permission to destroy a still-running worker.
- **There is no Qt playback fallback.** Qt Multimedia is analysis-only through
  `QAudioDecoder` for AAC/ALAC. Native-deck initialization failure must leave
  library management usable, disable player controls, and surface the exact
  diagnostic. Do not reintroduce an engine setting or silent fallback.
- **A consumed Qt key event does not disable Cocoa press-and-hold text input.**
  `QTableView` advertises a macOS text-input client even with edit triggers off,
  and the first track-load transition can reach the native accent service before
  repeat `QKeyEvent`s arrive. Keep `Qt.WA_InputMethodEnabled` false on every
  `NoTypeAhead` browser view, but do not mistake that for the native boundary:
  the packaged entrypoint must also set the bundle-local
  `ApplePressAndHoldEnabled=false` default before importing Qt, and the bundle
  plist declares the same policy. Delegate-created line editors remain separate
  widgets with IME support. Never change the global macOS preference.
- **Native player thread boundary:** no Python callback, Objective-C decode,
  allocation, file I/O, or blocking lock may enter `renderCallback`. Commands
  are atomics/generation-tagged; AVAudioFile runs only on the decode thread.
  Metal runs from CAMetalDisplayLink, not `PlayerBar._scroll_timer`.
- **A future CoreAudio timestamp must not rewind a new playback run.** Keep
  extrapolation for steady playback, but clamp its lower bound to the actual
  Play/seek source frame until presentation catches up. At a nonzero start,
  clamping only to zero caused a reproducible roughly 13-ms visual backstep.
- **AVAudioFile can hide cloud ENEEDAUTH behind an opaque `wht?` error.** Probe
  one source byte only on the decode thread for CloudStorage paths, then allow
  at most two cancellable three-second ENEEDAUTH waits. Other failures surface
  immediately. Cancellation/source replacement interrupts the waits; it cannot
  forcibly interrupt an OS read. Reset transport/gain completions on failure,
  expose the cause in status/tooltip, and let Play reopen the failed audio source.
- **Autoplay intent is not the polled PLAY button state.** Native seek/refill
  can briefly report PAUSED, and activation can observe that interval. The
  terminal PAUSED snapshot and ended flag can also arrive on separate polls.
  Keep ordinary-playback eligibility until explicit cancellation or terminal
  consumption; never gate advancement on `_transport_play_active`. Delay the
  source swap one event turn, guarded by transport epoch and source generation.
  If the separate ended read is newer than a PLAYING snapshot, publish PAUSED
  before deferred advancement so source replacement cannot wait for a fade on
  an already stopped engine. A cue promoted with Play/Space is ordinary playback
  and retains autoplay eligibility after cue release.
- **Browser replacement preserves intent before fading the old source.** Use
  `play_track(autoplay=None)` for browser loads; False explicitly loads paused
  and True explicitly plays. Preserve pending grid/media and replacement-fade
  intent across rapid selections. Do not infer it from a polled native state or
  let an unlatched CUE preview become ordinary playback.
- **A static Metal deck must not own a permanent display loop.** Playback,
  loading, preview, and live resize wake the link; every other mutation marks
  one dirty frame and the link pauses again. Python snapshot polling is 8 ms
  only while active and 125 ms while paused. Any new render-state setter must
  request a dirty frame, and any focus-return transport path must pull one
  authoritative native snapshot before repainting Qt state.
- **Qt child resize events are not Metal resize events.** An application-wide
  `Resize` filter sees every control affected by a window layout and must only
  defer background work. `NativeDeckBackend` filters the waveform host itself;
  its one synchronous C call commits the AppKit child frame and Retina
  `drawableSize` before requesting a frame. Returning to generic resize wakes
  causes both a request storm and a one-frame small/stretched waveform.
- **The track scrollbar begins below the headers.** BrowserScrollBar overlays
  the row edge while headers use the full frame width. Qt’s hidden native bar
  retains scrolling/range ownership; synchronize the visible control both ways,
  including pageStep and viewport geometry. Do not reintroduce a reserved gutter
  or automatic last-column stretching. Artists and USB use the same overlay. There is no utility rail. The circle-i opens Summary/Info from a
  bordered prep control before My Tags; Info and Settings share the 44×28 size,
  three-point corners and BG fill of the grid controls. Draw both icons centered
  on QRectF geometry; integer QRect centers and font bearings shift the ink.
  The layout-selector popup is constrained to the field width after showPopup.
  FooterBar draws one full-width separator without changing footer height/margins.
- **Browser widths must fit the viewport without coupling manual drags.** Keep
  Interactive headers. A manual drag changes only that section and is capped by
  existing free width; do not take width from Title or another column. A full row
  must first be narrowed to free space. Window resizing/column visibility can
  refit Title and reduce other widths when necessary. Guard the section-resize feedback loop, including bulk default
  and saved-state restoration, then reconcile Artists. Ordinary layouts keep horizontal
  scrolling disabled; when selected fields exceed the minimum-width budget,
  enable scrolling in the regular/Artists/USB views rather than strand columns.
  Fit against the full browser viewport on Resize/Show, including after resizing in Settings. Do not fit against a hidden Artists viewport's stale geometry.
  TrackModel's fixed role constants belong outside its hot data() callback;
  unsupported paint roles need no row/key work or Qt enum lookup.
- **Worker ownership does not mean an edit is saving.** Focus return rechecks
  the saved grid through the shared lane. Show footer progress only for accepted
  edit intents, using the existing status label/style; never show `Saving grid…`
  for that check. Only verified save callbacks may show `Done`. Idle/changed
  notifications also occur on failure, so they cannot establish success. Retain
  the existing status-generation guard so an old timeout cannot clear a new edit.
- **The elapsed/total clock reuses transport updates.** Publish only changed
  whole-second text, preserve the mouse-owned drag preview against late backend
  polls, and format BOTH sides with hours for tracks at least one hour long.
  Keep a monospaced width reservation and flexible toolbar gaps so changing
  digits cannot move the grid controls. Never add a polling timer or library/
  audio read just to update the label. Hermetic renders pin a synthetic clock
  after event settling; their unavailable offscreen backend is not a clock proof.
- **Imported analysis comes first.** Complete, equal-length 150-Hz PWV3/5/7 plus grid
  bypass FFT/filterbank analysis. Decode only the 1-ms signed midpoint supplement;
  persist its base once and build mips in memory. Compact V5 omits spectral_24.
  Old V5 remains ready/playable while eligible local records convert one at a
  time in the background or on selection. Include the compact model in receipt
  policy, recheck completed upgrades after waits, and never fall back to a full
  analyzer for a failed compact replacement. Do not invalidate caches at launch. Failure or
  interruption preserves the prior record and manual/DB metadata authority.
- **A worker upload can still stall Qt if delivery rebuilds its detail.** The
  native host must skip the unused Qt `array` envelope, whose per-cell generator
  ran on the UI thread. Keep packed color repair vectorized and check cancelled
  workers after detail reads. Display-stream publication must reject stale
  generations under the native render lock, just like signed detail. Cue-button
  style polish is needed only when the filled property changes.
- **libsndfile does not decode AAC/ALAC here.** Complete imports still need their
  compact supplement. Use QAudioDecoder at native rate/channels and the same
  1-ms reducer; never silently retry an unsupported primary decoder forever or
  run full spectral analysis just to obtain that supplement.
- **Zero compact height does not mean black RGB.** PWV5 can carry valid color
  while its five-bit height rounds to zero and signed audio detail remains
  visible. Cancel positive color weights exactly and use the authored RGB
  color when weight is zero; never blend a fabricated black into quiet colors.
  This is color recovery only, not permission to alter geometry or coverage.
- **BLUE/RGB require signed intervals.** Filling back to zero destroys the thin
  carrier. Close cells are held on the song clock and resolved with the
  spatial profile integral. Current upload is linear with the existing 0.84
  ceiling/quiet shoulder; the older `height^1.4` rule is superseded. True zero
  stays zero. Compact cells use absolute integer sample boundaries, including
  alternating 44/45-sample cells at 44.1 kHz, independent of decoder chunking.
- **MP3 clock correction must include audible playback.** Recognized MPEG-1
  LAME Info/Xing headers determine encoder delay, packet length and decoded
  frame count. Both analysis and native playback restore that lead-in; seek and
  duration share the same clock. Native probes run on the decode thread and
  validate against AVAudioFile. Unknown headers retain the old decoded origin;
  recognized decoder/header mismatches report an error. Never shift only the
  visual trace or use a per-track measured constant globally. The legacy RGB
  fallback's seven-row filterbank delay is separate from this source origin.
- **3BAND geometry is independent.** Use original PWV7 150-Hz rows, linear layer
  interpolation, a one-row sampling offset and the measured seven membership
  colors. The older forced 75-Hz pairing and eight-percent facet are superseded
  by the September 29 reference. Mode switches select resident buffers only.
- **Full-range RGB is 14/7/7.** Interpolate PWV5 channels on their song clock.
  The older 14/8/8.75 divisors and nearest-row hold were too dark and discontinuous.
  The legacy upload-only zero-row visibility repair may use its independent
  overview; compact imported overviews preserve authored PWV5 zero rows.
- **Filter final coverage once.** Keep integer song anchors and local phases,
  one footprint-selected peak level, and one-pixel coverage. Signed intervals
  intersect before integration, as documented below. Compact
  mips are `1/2/3/4/6/8/12/16/24/32/48/64`; old V5 retains its persisted ladder.
  All stops use one physical pixel of source support except the accepted
  logical-pixel RGB 576/864 policy. Wide BLUE/RGB also use the middle stops'
  same-LOD reconstruction. This supersedes the four-pixel rule and artificial
  divisor-dependent height compensation. Never blend adjacent mips or blur
  final pixels to hide sampling errors.
- **Compact RGB codes are already PWV5 channel values.** SoundBox's
  `_pack_rgb3` produces even red `0..14` and green/blue `0..7`; do not send
  those bytes through a second 0..255 quantizer. Older fallback records did so
  and shifted their remaining red into the green field. At native upload,
  repair only `soundbox-analysis` PWV5 from its canonical same-clock overview,
  preserving the low seven height/control bits. Never rewrite the cache,
  re-open source audio, or apply this compatibility path to exact Rekordbox
  PWV5.
- **Reference scale must be measured.** The fresh Nuclear Bass Face reference
  is 1500 points/s (3000 physical pixels/s at Retina scale two), now the maximum
  UI stop. The older Bad Decisions/High & Scared corpus is unavailable; its
  historical zoom assumptions are not new acceptance. Capture actual native
  surfaces in all three modes, then inspect moving frames as well as stills.
- **Waveform primaries are authored for the built-in Display-P3 surface.**
  Keep `CAMetalLayer.colorspace` explicitly set to Display P3 while retaining
  the sRGB-transfer Metal attachment. Main-lane RGB decodes its final encoded-
  space blend, as does the RGB overview; BLUE/3BAND, their overviews and the grid
  retain linear-light compositing.
  Leaving the layer color space implicit converts Rekordbox red `255/0/0` into
  `234/51/35` and high blue `127/162/255` into `134/162/248` on capture. In
  3BAND, preserve the measured flat membership palette rather than
  remixing band energy into red/pink/blue hues.
- **The scrolling surface must be fixed in song space, not reconstructed at a
  new playback phase each frame.** Print height and amplitude-weighted RGB onto
  a private song-coordinate attribute lattice with four attributes per
  physical pixel at every zoom. RGB 576/864 use logical-point motion after the
  native Gunslinger reference; the installed8e4 build keeps other RGB stops fractional.
  The October31 installed285163dc build snaps only128/184 to physical pixels
  after Sonny accepted the bottom BREATHE moving comparison. All1900 tests,
  signed package/smoke/install/startup and exact curation/analysis preservation
  pass (see HANDOFF). The subsequent approved third-stop/height pass adds
  physical-pixel motion at64 and peak-retaining signed reconstruction at64–256.
  Other RGB stops remain fractional. The November2 color update supersedes
  October27 coverage C's384-only exact-area exception: every RGB stop now uses
  the fixed92pt/s reference color field and bounded faint-detail shoulder below
  18% coverage, including384. 3BAND also
  stays continuous. BLUE rounds its display offset to physical pixels
  through 384 points/s and logical points at 576–1500. Historically, logical points at
  256/384 made visible steps, and fractional motion paired with conservative
  RGB coverage brought fuzz back. Historical C removed that partial-pixel brightness
  boost at RGB384 instead of changing detail. Never confuse those combinations. Historical
  whole-pixel probes used different detail/coverage rules and were rejected.
  Do not infer current acceptance from those old measurements. The buffer needs a
  generous guarded margin scaled by the attribute density and absolute
  integer-song-coordinate recentering so regeneration cannot create a viewport
  seam.
- **Historical occupancy correction, superseded November2.** The accepted RGB
  576/864 follow-up selects one logical-pixel source footprint and one logical-
  point motion unit (two physical pixels on Retina). A plain 2-ms peak envelope
  improved glisten but increased painted area/brightness. Keep the original
  physical-footprint level as the area reference and weight encoded contrast by
  its overlapping signed widths divided by the coarse width. Apply this on the
  fixed attribute lattice before existing premultiplication; retain the final
  one-physical-pixel integral and both visibility policies. Do not substitute a
  blanket palette gain. When both levels agree (including 1x), do not compensate.
  The real source/caches remain unchanged; BLUE/3BAND and other RGB stops are controls.
- **Recenter tests must compare different overlapping origins.** Returning to
  the identical origin misses floating-point source-cell boundary drift. Generate
  attributes from canonical 128-attribute clock blocks, preserving the same
  integer anchors and local phases across tiles. Never hide outline changes by
  averaging source cells. See the [October 2 review](archive/waveform-zoom-review-2026-10.md).
- **Color's premultiplication weight is not reconstructed signed amplitude.**
  Keep `lowerAndColorWeight.y` with the amplitude-weighted RGB through every
  lattice interpolation. The magnitude of interpolated signed bounds can be
  much smaller than that original weight at a polarity or dominant-boundary
  crossing. Dividing RGB by it creates bright/white pulses. The September 30
  correction uses an existing padding lane and adds no buffer or cache space.
  That correction alone left playback-only dark waves. The October 1 complete-
  corner sampling and stable-moment corrections subsequently passed Sonny's B
  comparison. The CPU regression runs the actual extracted shader expression
  and fails against the old formula; native motion checks cover the later fixes.
- **The former four-pixel close footprint is historical.** That rule addressed
  the old dense detail ladder. The September 29 reference established held
  1-ms cells, selected against one physical pixel in the October 2 follow-up; do not restore the older
  broadening or compensate it with per-divisor height gain.
- **Static drawable callbacks are not pixel acceptance.** macOS can expose the
  correct static Metal surface without a nonzero presentedTime callback.
  Resize tests inspect the playhead pixels and subsequent idle retirement;
  moving capture tests still require real presentation timestamps.
- **Do not recover close detail by flooring the mip.** The fixed lattice stops
  phase-dependent redraw, but it cannot make above-Nyquist values displayable.
  A floor-mip prototype looked richly detailed in a still and immediately
  produced a nasty hairy/wiggly edge in motion on the real stress track. Keep
  the conservative ceiling mip. The targeted zoom recording later justified a
  deliberate kernel split: positive B-spline reconstruction through 384 px/s
  for stable mid/wide contours, then direct linear reconstruction at 576 px/s
  and above for BLUE/RGB signed intervals. PWV7 retains its
  native cell timing. The
  supplied `1536`
  references proved that monotone Hermite rounded the native straight-sided
  carrier peaks. Do not extend the averaging B-spline into the close range.
- **Keep coverage policy explicit and test the final compositor.** A
  hard minimum vividness for every nonzero fragment turns a contour crossing a
  pixel into an on/off color flash, even when the song coordinate itself is
  perfectly smooth. The RGB overview now uses the same bounded faint-only
  shoulder and encoded-space resolve as main RGB. Keep cubic ease-out only for
  BLUE/3BAND overviews, where true subpixel peaks need visibility. Do not apply that curve to the scrolling
  lane; at 24 px/s the ease-out exaggerated
  20% physical coverage to nearly 49%, making saturated red/orange edges pulse.
  Coverage policy may change compositing only—never height, hue, LOD, detail
  density, or timing. This rule applies to both sides of a signed interval:
  averaging five independent inside/outside tests still quantizes BLUE/RGB
  coverage into fifths. Integrate the pointwise upper/lower intersection with
  the same clipped moments used for visible color. The older minimum of two
  separate edge integrals overfilled pixels and was removed October 2. Keep
  the final fragment covered by its declared numerical oracle: BLUE retains
  exact joint area. Every RGB stop, including384, boosts only faint coverage
  below0.18, using c′=c×(1+1.5×(1−c/0.18)²); normal coverage is untouched.
  This monotonic shoulder adds at most 0.04, joins exact area smoothly and never
  fills zero-area pixels or changes hue. The broad October 6 occupied-column
  policy was removed. Independent area/color and mutation oracles protect
  joint-area integration and faint-only behavior across red, blue, green and orange.
  The interval's minimum full width is one physical pixel before coverage;
  it must follow drawable scale, not the old fixed 3.6-point width. This keeps
  the two half-pixel AA supports from overlapping. The historical High & Scared close-RGB phase sweep cut the aligned
  greater-than-64-code change tail by almost 90% without changing its accepted
  silhouette.
- **BLUE must not force a white center into every interval.** The October 1
  recording contains saturated cyan interiors. Use normalized source interval
  width for cyan-to-white strength, then the continuous radial edge transfer.
  Do not use a zoom-specific gain, enlarge the source footprint, or smooth the
  signed detail to reduce white washout. The production-Metal regression checks
  the thin center itself and proves restoring fixed white breaks the test.
- **BLUE/RGB color must be resolved conditionally on signed coverage.** The
  continuous upper/lower boundary resolve can be perfectly stable while an
  independently averaged color still ticks inside it. RGB may inherit hue from
  a horizontal tap that does not cover the queried destination pixel; BLUE
  exposes the same defect more strongly because its white/cyan/blue radial
  color depends on the moving signed center and width. Integrate clipped-linear
  coverage moments per quarter-pixel segment, accumulate only visible color in
  premultiplied display channels for RGB, linear light for BLUE. Split BLUE at
  coverage/radial branch changes and
  integrate its nonlinear color field with four-point Gauss-Legendre quadrature;
  evaluating only the coverage centroid is insufficient. Resolve coverage and color together so neither can use an
  independently averaged silhouette. A temporary flat BLUE fill
  can diagnose this class of defect but is not a fix because it discards PWV3
  depth information.
- **Keep timing guides continuous, with density-controlled contrast.** The main
  grid is drawn after the waveform. Ordinary/downbeat widths stay 0.9/1.1
  points at every zoom. Interior alpha grows from 0.28/0.58 to 0.45/0.75 as
  local spacing grows from 32 to 80 points. Edge ticks stay opaque. Sonny's
  live review rejected edge-only wide views: never remove the through-wave
  guides just because the grid is crowded, or restore the old opaque fence.
  Use adjacent beat timestamps, not nominal BPM, to derive density. Cues and
  the overview are not tagged as grid guides and must bypass this policy.
- **A one-point Metal grid rectangle is not subpixel-stable on Retina by
  itself.** Non-MSAA triangles rasterize it as a hard two-physical-pixel box,
  so its center advances on integer pixel boundaries while the waveform below
  moves continuously. Carry signed local line distance and half-width through
  the grid vertex, expand the fringe geometry by half a point, and evaluate
  analytic box coverage in physical pixels in the fragment. This applies to
  both downbeats and ordinary scrolling beats; color/opacity policy is
  separate from edge coverage.
- **RGB edge blending is deliberately mode-specific.** Sonny selected original
  October28 B and explicitly retained BOTH existing visibility lifts. Main-lane
  RGB integrates encoded channels jointly with coverage, blends the encoded
  background, then decodes that opaque result for BGRA8Unorm_sRGB. Do not decode
  RGB channel endpoints first or let the sRGB attachment encode an already-encoded
  result. The RGB overview now shares this encoded-space resolve with a faint-only
  shoulder. BLUE/3BAND, their overviews and grid retain linear light. Keep
  Display-P3 primaries and the linear clear for `#0d0e12`. The earlier universal
  linear-light guidance described the preceding renderer and no longer applies
  to main-lane RGB. Source samples, height transfer, footprint, LOD and timing
  are unchanged; later C/D/E lift-removal/direct-cell trials were set aside.
- **Do not insert a translated RGBA waveform texture between song data and the
  drawable.** It filters already-antialiased coverage and hue a second time,
  making contours and adjacent colors breathe. A `float4` attribute buffer is
  different: it stores premultiplied RGB plus height, never coverage.
  Premultiplication is required: interpolating straight RGB and height
  separately before multiplying them creates a phase-dependent quadratic
  signal at the moving edge. Translate the premultiplied attributes and
  compute the one-pixel vertical coverage exactly once in the final fragment.
  Never cache or interpolate final alpha.
- **A fixed attribute lattice still needs one-physical-pixel horizontal
  integration.** One center height sample leaves subpixel needles that crawl
  through dense passages. At every mode/zoom a fractional song phase can
  place an integer attribute-lattice knot between fixed probes; integrate the
  two pixel endpoints plus every interior knot
  with their actual spans. Feed the same positioned profile to silhouette,
  signed-interval, color, and 3BAND coverage. Do not widen the footprint: one
  pixel removes the alias while retaining the close detail that B-spline
  reconstruction erased.
- **Do not cure source aliasing by widening the final image filter.** The
  earlier four-pixel close-source rule is historical. Current compact detail
  uses a physical-pixel close source footprint except the accepted logical-pixel
  footprint at RGB576/864; final coverage remains one physical pixel. Keep any needed
  reconstruction in song space; no adjacent-LOD blends or temporal blur.
- **Inactive preview is an idempotent native transition.** PlayerBar publishes
  coarse transport text positions during ordinary playback. Those
  `preview_active=false` calls must do nothing once native preview is already
  off; waking `CAMetalDisplayLink` or posting a run-loop block for each poll
  introduces full-app pacing noise that an isolated deck harness will not
  reproduce. Active drag preview still republishes every position and wakes
  one frame.
- **Do not pin `CAMetalDisplayLink` to 120 Hz.** Let Core Animation negotiate
  the active screen cadence so fixed-60 Hz, ProMotion, power throttling, and
  moving the window between displays remain evenly paced. Snapshot render
  buffers under the renderer mutex, then release it before command encoding;
  the display thread also reuses its overlay vertex storage. The final
  2026-07-28 phone/native comparison proved that the remaining rare doubled
  interval begins at `CAMetalDisplayLink` itself: its next
  `targetPresentationTimestamp` occasionally advances by 16.667 ms, the
  callback arrives on that same interval, and Metal still has about 8.2 ms
  before `targetTimestamp`. Geometry, GPU load, the CoreAudio PLL, and drawable
  presentation are therefore downstream of the omission. Hard 120 Hz and a
  90–120 Hz floor both increased gaps and were reverted. A registered 60 fps
  phone capture cannot resolve every 120 Hz panel refresh by itself, but paired
  with native target/presentation counters it can expose the conserved
  half-step/one-and-a-half-step sampling pairs without screen-recorder
  compositor interference.
  A 2026-07-29 retry against the new 120 Hz screen recording also rejected
  three apparent fixes: the system-default frame-rate range tied the existing
  60–120 preference, fixed 60 Hz introduced 25 ms gaps, and a continuously
  running display-thread run loop tied the existing bounded run-mode pump in
  paired trials. Keep the explicit 60–120 preferred range, latency one, common
  run-loop modes, and timestamp-driven prediction.
  A subsequent grid-on/grid-off isolation covered 5,400 exact drawables per
  state at 1536 px/s. Display-link gaps were 23 with the ordinary static
  2,688-vertex beat buffer and 24 without it; both had zero GPU deadline
  misses and zero CoreAudio underruns. Mean GPU time changed by only 0.021 ms.
  Do not rebuild, hide, decimate, or otherwise blame the beat grid for this
  cadence omission.
- **Exact-drawable diagnostics must remain bounded and off the display path.**
  The temporal trace preallocates its one shared Metal buffer before playback
  (maximum 600 frames / 512 MiB), then adds only a final-texture blit and
  timestamp bookkeeping per captured drawable. Never add file I/O, PNG/video
  encoding, Python callbacks, allocation, or blocking waits to `draw`, its
  command completion, `addPresentedHandler`, or the CoreAudio callback.
  Finalization is allowed only after playback stops and only beneath the
  caller's explicit output directory. Preserve dropped presentations as
  `presentedTime == 0` ledger rows; omitting them would falsify the cadence
  experiment. The dual-edge Gray/parity marker is diagnostic-only and must be
  gated by the exact reserved trace sequence. Never expose it during ordinary
  playback. Keep the fixed-waveform calibration: without it, a 60 fps camera
  cannot distinguish panel phase/exposure blending from waveform motion.
- **A display-link submission is not an onscreen frame.** Target timestamps
  can run ahead of the drawable the hand actually sees. Count cadence from
  `MTLDrawable.presentedTime`, ignore its documented zero value for a dropped
  drawable, and keep presentation latency at one. On mouse-down, retain the
  newest fresh presented or irreversibly scheduled source frame. Freezing an
  older submitted frame produces a visible rewind. A measured latency-two
  retry on 2026-07-27 reduced five-second close-zoom cadence and did not remove
  the occasional doubled presentation interval; do not revive it as a hitch
  fix. The 2026-07-28 retry was worse again and reached a 25 ms gap at
  1152 px/s.
- **NEVER motion-blur the scrolling waveform.** This was built twice — a
  180-degree box, then a full-interval tent — and rejected on sight both
  times. The premise is wrong: rendered motion blur is for content the eye
  does *not* track. A DJ tracks the waveform, so under smooth pursuit it is
  stationary on the retina and the blur is painted onto a stationary image
  and never averages out. It rounds every edge, destroys the detail it was
  meant to protect, visibly "expands" when transport starts, and — because a
  grab freezes the last rendered frame — hands back a blurred still. The
  display's own sample-and-hold already supplies ~25 Retina pixels of
  persistence blur at 1536 px/s; a shutter only doubles it. Keep the
  integration window equal to the physical pixel and keep tap weights flat.
  `test_no_temporal_shutter_blurs_the_moving_waveform` fails the build if any
  shutter symbol returns.
- **Diagnose close-zoom fizz in song space before changing the clock.** Check
  the selected LOD, attribute-lattice anchoring/recentering, sample density,
  and final coverage; a stable presentation clock cannot hide content above
  the pixel Nyquist or a contour that is being reprinted at changing phases.
- **A signed audio contour can be spatially correct and still strobe.** The
  paired 2026-07-29 physical/GPU run isolated a 41.209 Hz bass fundamental in
  the source, the same 41.33 Hz line in exact BLUE/RGB drawables, and its
  approximately 18.9 Hz fold in the 60 fps phone capture. 3BAND's unsigned
  Rekordbox band envelopes omit that polarity carrier and stayed at the fixed
  calibration floor. Five-refresh motion registration held BLUE/RGB to p99
  0–1 display code, so do not misdiagnose this as random redraw, chroma
  breathing, position jitter, or GPU lateness. Remedy experiments must isolate
  signed interval center/polarity motion from accepted outer extrema and
  attacks; never reach first for final-pixel blur or a playing-only shutter.
- **Removing the signed carrier cannot preserve its paused pixels.** The
  supplied close Rekordbox screenshots visibly contain the same oscillating
  polarity trace that strobes when translated. The carrier-neutral compositor
  experiment proves the tradeoff: using the existing unsigned peak
  magnitude cuts the real 41 Hz drawable band from 19–37% to 1–3%, but turns
  the signed zigzag into a symmetric filled envelope and materially changes
  the paused reference silhouette. The matched physical A/B proves the remedy:
  BLUE/RGB's phone-folded carrier falls below calibration and 3BAND control.
  The first physical review accepted its motion, but live use immediately
  rejected the lost detail. The signed contour therefore remains the close
  BLUE/RGB product default for stopped and playing. The neutral-envelope ABI
  and shader branch were removed on 2026-07-30 after they served their causal
  experiment. Keep 3BAND outside any replacement and never reintroduce a
  playing-only filter.
- **Do not fill a soft black gap to make it look sharp.** A low-opacity neutral
  bed behind the signed contour reduced binary negative-space carrier power,
  but live comparison preferred the untouched far-left BLUE control because
  every bed made the result look filled. Later 0.75 signed-coverage and 1.0 RGB
  fractional-coordinate sharpening added contrast after the exact resolve and
  made the remaining physical carrier easier to read as pulsing/static. The
  2026-07-30 simplification removed the bed and both post-resolve transfers
  from the ABI and shader. Keep continuous joint interval/color integration;
  keep 3BAND exact, and regression-check all thirteen real UI zooms.
- **Never blend two mip levels' coverage. Pick one.** Levels are song-anchored
  maxima on power-of-two lattices, so level N quantizes every 2^N bins and
  N+1 every 2^(N+1). Blending scrolls both at the same speed with different
  quantization and they beat against each other. The artifact tracks the
  fractional LOD exactly: edges were bad at 32/64/128/256/576 px/s (fraction
  ~0.62) and clean at 24/48/92/184/384 px/s (fraction ~0.05). Continuous
  blending only ever protected zoom *transitions*, and zoom is thirteen fixed
  steps, so the level is constant for the whole of playback. `biased_lod`
  ceils the footprint and `kIntegrationTaps` integrates coverage within that
  one selected level.
- **Averaging per-tap coverages quantizes the edge.** One tap's analytic band
  is about `antialias / heightScale` -- roughly 1% of full height -- so each
  tap is effectively binary and the mean of N of them takes only N+1 values.
  Sliding taps then cross one at a time and the contour visibly wiggles at
  every zoom. `profile_coverage` integrates the exact area under the
  piecewise-linear tap profile instead.
- **Native fps counters do not measure what reaches the screen.** A 120 fps
  screen recording of the real app showed only **52-54 unique frames per
  second**, with irregular hold lengths of 0, 1, 2 and 3 captured frames,
  while `sb_deck_display_stats` simultaneously reported ~117 presented fps.
  The deck renders roughly twice as fast as the window is composited and the
  surviving frames are unevenly spaced. Every native counter is blind to
  this; only a frame-accurate capture of the screen shows it. Note macOS may
  drop ProMotion to 60 Hz *while recording*, so confirm the symptom without
  a recorder before treating the rate itself as the bug.
- **A Python polling loop cannot measure display freezes.** Sampling
  `sb_deck_presented_frame` from Python and differencing shows "freezes" that
  are the *observer* stalling, not the deck: a 490 ms Python hiccup is
  indistinguishable from a 490 ms display stall. Trust `max_gap_ms`, which the
  presented handler computes natively from real `presentedTime`.
- **Main-thread widget work steals displayed frames, and an isolated deck
  harness will never show it.** The 20 Hz transport position path called
  `_update_transport()`, which called `cue_panel.set_loaded()`, which
  rebuilt all sixteen cue buttons — allocating a `QPixmap` and `QIcon` per
  filled slot — even though `loaded` had not changed. That cost **eight
  displayed frames per second at 1536 px/s** in the real app: 106.9 -> 118.0
  fps once guarded. Every dropped frame at maximum zoom is a doubled 51-pixel
  scroll step, which reads as shaking, and no shader change can fix it.
  Anything on the position path must early-out when its state is unchanged.
  `WaveformBar._repaint()` exists for the same reason: `paintEvent`
  early-returns under Metal, so a Qt repaint composites for nothing.
- **Measure renderer changes in the real app, not only in a deck harness.**
  Cue PLAYING/PAUSED edges must not scan the library's artwork or launch device
  discovery. An October 27 full-library synthetic profile measured about 38 ms
  in `refresh_art` per cue release; the live sample also showed repeated scan
  launches during tapping. Mount/device timers already run independently of
  playback. Removing this work preserves discovery while avoiding input stalls.
  A short cue tap can release before Python receives PLAYING: explicitly cancel
  the pending native start, not just its current sampled state.
  A bare `QWidget` host with hand-uploaded analysis ran 118-120 fps while the
  real app ran 107-113 on the same machine and build, and the harness could
  not see the cue-panel cost at all because nothing was connected to
  `positionChanged`. Run-to-run fps also varies by 5+, so attribute costs
  with **paired alternating A/B rounds inside one process**, never by
  comparing two separate runs.
- **Verify shader edits by compiling the Metal source, not by reading tests.**
  The source is a runtime string, so a syntax error is silent: pipelines fail,
  nothing presents, and the failure surfaces as unrelated cadence tests
  reporting zero presentations. `half` is a reserved MSL type name and is an
  easy accidental variable. Extract the `R"METAL(...)METAL"` body and hand it
  to `newLibraryWithSource:` to get the real compiler diagnostic.
- **The visual clock is a continuous PLL, not a corrected counter.** Never
  `fetch_add` a phase correction onto the visual frame at an Audio Unit callback
  boundary: the former 0.15 ms cap became almost half a Retina pixel at 1536
  px/s and looked like close-zoom shaking. CoreAudio timestamp observations may
  adjust `visualEffectiveRate_`, while rebasing must preserve the already
  predicted fractional position. Keep the sequence lock around the
  frame/host/rate tuple. Likewise, scrolling grid vertices stay as integer
  milliseconds + local phase; subtracting absolute float seconds loses visible
  precision late in a track.
- **Do not spread an omitted ProMotion callback across later frames.**
  An exact synthetic A/B made four-refresh repayment look best and reduced the
  largest source-frame advance from 735 to 460, but the installed app felt
  worse and the direct-prediction control was clearly better. Repayment adds
  temporary visual lag, then exposes that lag as several frames of altered
  velocity—a lingering rubber-band effect instead of one rare correct-time
  double advance. Keep CoreAudio authoritative and predict its position
  directly for each display target. The absent callback cannot be rendered.
  Do not revive the recovery clock or its diagnostic injection probe.
- **Player cache schemas are data contracts, not renderer version numbers.**
  A shader/filter/clock change does not require re-preparing the library.
  Bump/rebuild only when the persisted payload itself changes. The hot path is
  canonical V5 sections. Cache hits start one short-lived staged reader: never
  read/hash the whole record or build Metal buffers on the UI thread.
  `WaveformFillWorker` remains incremental, prioritizes the
  selected track, and cooperatively yields inside long tracks during playback
  and foreground interaction. Startup metadata discovery reads only V5
  manifests. Compare the exact cached beat rows with the current read-only
  Rekordbox PQTZ fingerprint so later manual grid corrections refresh metadata
  without an audio recook; do not treat `grid=rekordbox` as permanently fresh.
- **`djmdCue.Kind` has a reserved hot-slot gap.** A/B/C are 1/2/3, value 4 is
  reserved, and D–P are 5–17. `slot = Kind - 1` silently shifts every cue from
  D onward and makes D impossible. Keep the explicit mapping regression test,
  reject reserved/unknown values, and preserve I–P even though the current
  panel shows A–H.
- Player interaction and transport semantics are specified in `PLAYER.md`.
  Keep double-click = the only browser load gesture (preserving playback), Space =
  play/pause for the already-loaded deck only, C = momentary CUE, one decoder
  seek on drag release, and first-Rekordbox-beat loading when a valid imported
  grid exists. A missing/invalid imported grid uses the SoundBox fallback with
  beat 1 at `0:00`; neither path writes to Rekordbox.
- The scrolling detail waveform claims transport on mouse-down by design:
  use the native atomic hold to freeze the newest drawable already presented
  or committed at latency one,
  re-anchor Qt drag math to its fractional-millisecond timestamp, cancel any
  pending gain ramp, hard-mute, and pause synchronously. The ordinary pause
  envelope advances to its fade endpoint and must not own a waveform grab.
  Predicting a fresh frame or rounding to whole milliseconds visibly jerks at
  high zoom. Relative movement follows the pointer continuously; do not add a
  dead zone that releases its accumulated delta as one jump. Release sends one
  exact authoritative seek—even without movement—and resumes only when the
  deck was playing before the grab. Never seek the decoder on move events. The
  whole-track overview deliberately retains absolute click/drag navigation.
- Ordinary click/Space pause must capture that same exact newest Metal frame
  at fade completion before stopping and seeking the decoder. A coarse Python
  millisecond estimate trails the drawable by about one refresh: recordings
  showed forward settling followed by a 7–13 physical-pixel snap backward.
  Resume must start from the held fractional-millisecond frame. This does not
  replace the gain envelope; it makes its endpoint authoritative.
- Overview and ultra waveform buffers are one visible surface. Prepared loads
  must group both native uploads while presentation is suspended and commit
  them together. Painting the overview while a long ultra section is still
  decompressing/building makes the waveform visibly change size on promotion.
- Prep grid buttons use the accepted source writer; manual SoundBox ownership
  remains protected. Native/manual/saved grids retain their source priority.
  Only eligible short tracks may use the zero-anchored automatic fallback;
  sets over900seconds keep their waveform without an invented BPM/key/grid.
  Never infer this boundary from AAC/ALAC/WAV or a filename. Legacy zero-anchor
  metadata is reconciled through the same bounded read-only rules.
- Custom QWidget with layout `stretch=1` may not expand — also set
  `setSizePolicy(Expanding, …)`.
- QSS: `QLabel { background: transparent }` or you get dark boxes behind text.
- **NEVER do synchronous removable-USB I/O on the main thread** (`statvfs`,
  reading the manifest, `walk`/`rglob`, `rmtree`, drift diff). macOS fskit
  BLOCKS these for SECONDS when the stick is busy → whole-app beachball + frozen
  animations. Thread it (`DeviceScanWorker`/`DeleteWorker`), show the lower-left
  progress bar, cache drift in `_drift_cache`. Any new device op must be threaded.
- **USB tabs are cached presentation, not a volume browser or write authority.**
  For Rockbox/file playlists, read M3U/M3U8 text only below `/Playlists` using pinned no-follow descriptors in
  DeviceScanWorker. That reader never resolves/opens referenced media or `/PIONEER`.
  Generic USB native metadata/settings use the separate `cdj_*` contract; ordinary
  exporters keep their exclusion. Reuse the
  snapshot on normal polls; explicit tab refreshes stay queued behind the same
  device owner. Publish only at QThread.finished, invalidate remount tokens and
  show failed/oversized/redirected reads as unavailable. A missing folder alone
  is a valid empty list. Use cached-only mount facts on this path; isolated
  AppData alone never authorizes device probes in a harness. Lazy row expansion
  must stay in RAM. Explicit song activation is a separate read-only lane task:
  validate the selected USB audio/ANLZ paths, then load a device-specific ID with
  no desktop cue overlay or source edit authority. Include the USB song viewport
  in shared column Resize/Show fitting. Its keyboard scope admits deck/load
  actions, not edits of a stale Mac browser selection. Stock/dual iPod routing
  and guards remain separate.
- **Confirmation belongs before exact iPod planning.** Sync, composite export,
  and composite playlist deletion must read as one action: immediate Yes/No,
  visible background planning, guarded commit, one completion. Never compute a
  full plan (or retained-playlist metadata signatures) merely to populate the
  approval dialog. Planning and commit use different work units, so map them to
  monotonic segments of one bar rather than resetting the range at the phase
  boundary.
- **Do not sustain full-file hash reads across a classic iPod during a UI plan.**
  The real MA446 dropped off USB twice while the composite planner streamed its
  managed audio baseline, even after device polling was serialized. Schema-3
  planning uses manifest ownership/path/size plus exact Mac-side desired
  derivatives. Commit replaces one managed audio file at a time using a
  same-volume rollback rename, verifies the newly landed bytes, and briefly
  yields between tracks. Keep polling paused through plan, confirmation, and
  commit; never run a second CLI/device reader beside the app.
- **Post-commit iPod refreshes are one serialized device operation.** Refresh
  the already-open Device/Songs/Playlists snapshot first, then start the sidebar
  free-space/drift scan from that worker's `finished` signal. Starting both
  readers together adds avoidable USB contention and leaves the visible
  snapshot stale until the user navigates away and back.
- **Connection visibility must not wait for dashboard integrity.**
  Connection presentation separates visibility from readiness. Debounced `/Volumes`
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
- **A stock shadow source row describes two files.** `size`/`sha256` belong to
  the verified device payload; `source_size`/`mtime_ns` describe the canonical
  Mac source. Picture removal changes payload size without changing the source.
  Comparing source stat against payload size creates a permanent false
  Sync-needed state immediately after a good commit.
- **The offscreen UI harness must suppress MainWindow's immediate device scan.**
  Stopping `_dev_timer` after construction is too late: `__init__` already calls
  `_refresh_devices()`. Patch that method during construction so running UI
  tests beside the real app cannot create a second removable-device reader.

## API traps
- **Qt slots SWALLOW exceptions** — a crash inside a signal handler prints to
  stderr and keeps going, so a UI test toggling a widget can PASS while the slot
  is broken. Test slot logic by DIRECT call too. (This hid a real `is_untracked`
  crash that only surfaced in the packaged app.)
- **Rapid track loads pile up waveform/analysis workers** → hang + latent GC-mid-run
  crash. Any "load X, user can rapid-switch" worker needs interrupt-previous +
  a strong-ref set (`_wf_pending`), never a single `self.x` reassignment;
  every result is track-generation guarded. `TaskOwner` now retains interactive
  workers through queued finished delivery and join. Shutdown closes admission,
  rejects late results and dispatches events until retirement before closing
  the native backend; never reintroduce timeout-based destruction.
- **A new track can change after its first background cook.** Rekordbox/tag
  tools may finish rewriting a newly landed container minutes later; direct
  SoundBox imports can also be replaced in place. One refresh-time waveform
  pass therefore is not enough. Successful source loads/imports arm the
  single-shot 60-second identity recheck, which rearms only after the previous
  worker finishes. `is_ready` uses size + mtime identity and recooks only stale
  records. Disposable `track_analysis/preparation-index.json` receipts bind
  source/V4/V5/DAT/EXT/2EX stat identities, cue inputs and analysis policies.
  Unchanged receipts skip record/ANLZ opens; changed records share one scoped
  manifest read. A 30-minute fallback retries silent sidecar parse failures.
  Never inflate waveform payloads or overlap workers, and never use scheduling
  receipts as source-integrity proofs or bypass interactive checks.
- **Global Graveyard membership vetoes persistent analysis.** Remove from
  Collection must discard the exact SoundBox V4/V5 record, and startup/bulk
  preparation must skip every globally hidden ID. Recheck membership before
  and after atomic publication because either the bulk worker or an interactive
  deck worker can already be decoding when the UI removal lands. Graveyard
  double-click and Load Track audition through that same deck without restoring
  membership or capturing autoplay. Its worker uses a private temporary record,
  uploads the loaded bytes and removes that directory before retirement; it
  never recreates the canonical hidden cache. Restore uses
  the normal bounded preparation path. Do not apply this rule to a
  playlist-only removal: that track remains browsable elsewhere and still owns
  its cache. These preparation/removal paths never write source audio or ANLZ.
  The separate accepted grid source writer and historical restore grant are documented in
  [the prep plan](rekordbox-prep.md); it grants no preparation-writer authority.
- **A ready cache can still preserve an early Rekordbox fallback.** Rekordbox
  may finish PWV3/PWV5/PWV7 after SoundBox has already stored a structurally
  complete V5 record. The recurring scan must use stream provenance to keep
  current compact records on the manifest-only path. Legacy detail models and
  modes still marked as SoundBox fallbacks may probe the read-only ANLZ payload;
  receipts suppress repeated unchanged ineligible probes.
  Eligibility uses packed-only stream/grid reads; never expand every track into
  per-sample Python colors or generic ANLZ objects during discovery.
  A newly valid exact stream queues that one atomic V5 recook. The explicit
  track-row Analyze Track waveform-rebuild path bypasses `is_ready`
  for only the requested track. Both routes retain the old playable cache until
  a complete replacement is atomically stored and hot-refresh the deck after
  success. Never delete the record first, and never write source audio or
  Rekordbox analysis.
- **Track replacement is an atomic renderer boundary.** Call native
  `begin_track(generation)` before setting the source. It clears overview,
  detail/mips, grid, imported cues, and session CUE together. Packed overview
  and detail uploads happen on the staged worker and native code rechecks the
  generation after allocation, before buffer promotion.
- **Do not reintroduce black RGB delay padding at 0:00.** New RGB resampling
  clamps to the first valid hue; selective overview load repairs audible
  zero-RGB legacy rows. Negative-time fragments and true silence stay pure deck
  background.
- **Computed-row cache:** effective title/artist is cached per track_id, warmed
  in background (debounced, generation-guarded), invalidated on
  override/alias/registry/title-junk change. `_row_for` returns a COPY. Title-only
  edits call `_invalidate_rows((track_id,))`; warms fill only missing rows.
  Registry/alias/title-policy changes still invalidate globally. Keep generation
  checks and retry after the old worker's `finished` boundary.
- **A new per-track `Track` field that affects a view MUST survive two caches or
  it silently reverts on the instant paint.** (1) Add it to `libcache._TRACK_FIELDS`
  and bump `SCHEMA` — else the on-disk snapshot drops it and every track loads with
  the default. (2) Clear any derived fallback when the live library replaces the
  snapshot. `_landed_cache` now stores only unknown-date path → birthtime fallback
  and is cleared in `_apply_loaded_library`; known `added_ts` returns directly,
  without resolving a media path. Historically caching known dates by path let an
  instant-paint value defeat fresh add times and conflated separate track records
  sharing one source path. Sidebar counts use unsorted visible membership, never
  the Collection's newest-added sorting path.
- **Restoring a tree selection with signals BLOCKED re-renders NOTHING.** The
  two row-rebuild paths in the unified sidebar are asymmetric: `_populate_tree` re-selects a native playlist with
  signals LIVE (fires `_on_rb_select` → fresh rows), but `_refresh_custom`
  re-seats Collection/Artists/custom inside `blockSignals(True)` — the table
  keeps its old rows. So any path that swaps `self.lib` under a live SoundBox
  view (refresh-done, instant paint) must re-render by hand;
  `_apply_loaded_library` does. (Real bug: new rekordbox adds stayed invisible
  in Collection until you clicked away and back.)
- **`_restore_view` re-seats ONLY on a view SWITCH** (same `_view_key` → early
  return), and `model.set_rows` is a full model reset (selection + scroll
  gone). In-place reload with fresh rows = `_stash_view()` → reload → null
  `_view_key` → `_restore_view(key)`, or the user gets dumped at the top.
- **Worker convention:** `OneShotTask` and `_OneShotArt` emit result-OR-Exception
  — done-handlers MUST isinstance-check. Never map an exception to a "nothing
  there" answer (a working art URL once flashed "No art found" with no log trace).
- `Track.is_untracked(*, nokey, short, short_secs)` is **KEYWORD-ONLY**.
- `engine.export_playlists(lib, [Playlist_objects], target)` takes Playlist
  **OBJECTS**, not display-path strings. `SyncReport` has no `files_copied`
  (use `.playlists` / `.bytes_to_copy`).
- `Playlist.display_path` joins with `" / "` (SPACES around slash), not `"/"`.
- Settings page lives on `body_stack` (index 1); its tabs are General, Analysis, My Settings, Keyboard,
  Graveyard, Connections; Graveyard uses the full width. Connections keeps core rekordbox on the
  left and adds SoundCloud on the right only in personal builds. Database changes
  retain the current library until a successful worker retirement and settings
  save; database snapshots and ANLZ resolution use the selected source path.
  XML parse failures likewise retain the previous library. A refreshed matching
  track/source must publish the newest DB cues to PlayerBar's live cue authority
  immediately, including an empty list; refreshing only the cache leaves its old
  live-cue override masking the update. Never seek/reload audio for a cue refresh.
  Track table / device browser use
  `stack` (0/1).
- Keyboard defaults, dispatch and the editor share `desktop.keyboard.COMMANDS`.
  Never add a competing hard-coded QShortcut for a catalog action. Momentary
  controls retain their actual press-time release callback by physical key;
  auto-repeat releases cannot end a hold, and modifier/page/focus changes cannot
  strand it. Active-page numeric cues capture the original Hot/Memory identity;
  aliases share the same control until the final physical key releases. Release
  held controls on app deactivation, successful remapping and shutdown. Persist
  the map before replacing the live snapshot, and reload it after restore.
  Platform text editing/navigation is not an app mapping. See [KEYBOARD](KEYBOARD.md).
- Splitting files with a script bypasses the Edit tool's read-tracking — Read
  again before Edit after any scripted rewrite.

## Cancel semantics (sync.py — don't regress)
- `sync.run` on cancel: writes HONEST partial m3u8s (only on-device entries +
  "# CANCELLED: N …" footer) and records incomplete playlists with **sig="partial"**
  → drift flags them → next Sync completes. A cancelled export must NEVER look
  fully synced.
- `sync.resync` on cancel: ownership prune + forget are SKIPPED (re-polls
  `should_cancel` directly); next Sync finishes.
  Tests: `tests/test_cancel.py`.
- Manifest v2 owns individual normalized paths. Only `owned: true` rows may be
  repaired or pruned; matching pre-existing media can be referenced with
  `owned: false` but is never claimed or deleted. Every target write goes
  through a pinned `DeviceSession`, same-filesystem temp, and atomic replace.
  FAT identity is Unicode-NFD + casefold, not Python path equality.

## Type-checking + test-honesty
- Waveform arithmetic must run production helpers against an independent oracle,
  not a Python copy of the same formula or source strings alone. Headless Metal
  tests run in the offscreen phase on arm64 macOS. `./check.sh` also runs the
  required Cocoa phase and verifies complete, nonduplicated test inventories.
  Any skip or expected failure fails the gate; see [the release guide](test-release-gate.md).
- Integrate tiny clipped pieces using local midpoint moments. Subtraction of
  global squared/cubed endpoints can corrupt color centroids by cancellation,
  producing different BLUE color when an identical segment is reversed.
- `./check.sh` gates: pyflakes + **mypy on `soundbox/` and `soundbox_personal/`
  (must stay 0 errors)** +
  the full suite. `desktop/` is NOT mypy-gated yet (PySide6 stub gaps).
- Never `assertX(...) and assertY(...)` (short-circuits the second). Never test
  dict/py semantics against themselves — drive the real app helper. A
  failure-tolerance test must create a REAL failure (e.g. squat a file where a
  dir must go), not assert its own monkeypatch.

## Property-test findings (keep hypothesis in the loop)
- `str.split()` does NOT cover everything `str.splitlines()` splits on
  (`\x1c-\x1e`, `\x85`, ` / `) — text in line-oriented formats (m3u8!)
  must be flattened via `ch.isprintable()`.
- `sanitize_component` turns ALL non-printables into `-` (they once split m3u8
  path lines on-device). Any new filename-ish surface must use it.
- Device path sanitization is not metadata sanitization. Stock iTunesDB text and
  the SoundBox UI preserve printable Unicode. FAT/Rockbox path components use
  readable Latin transliteration (`CØSTA` → `COSTA`, not `C_STA`), retain
  non-Latin scripts, remove decorative `*`/`?`, normalize compatibility
  punctuation, and protect DOS names such as `CON`/`LPT1`.
- Hypothesis tests are `derandomize=True` — deterministic; a failure is REAL.

## Offscreen UI tests can dirty the REAL config
- Constructing `MainWindow()` offscreen and firing a widget handler runs the real
  slot → `_save_state()` → WRITES the real `library.json`. For render/smoke
  tests, set state via `win.settings[...] = …` + `win._sync_settings_controls()`
  (blockSignals, NO save), or point AppDataLocation at a temp dir.
- `setup_logging()` must honor `QStandardPaths` test mode too. Otherwise fixture
  failures such as the intentional `boom` cases are appended to the production
  `~/Library/Logs/Soundbox/soundbox.log` and corrupt later hardware log audits.
- `desktop/_shot.py` is the SELF-CONTAINED reference render harness (synthetic
  lib + qttest sandbox): `PYTHONPATH=. .venv/bin/python -m desktop._shot`.
- **A failed `open_library` pops a MODAL QMessageBox** → headless/offscreen
  scripts hang FOREVER. The old Desktop test XML is deleted; automated smokes
  must use a synthetic Library and suppress live DB/device discovery.
- Keep test definitions before a script's `unittest.main()` guard when direct
  execution is supported. Classes after it are still collected by unittest
  discovery, but direct script execution reaches the runner before defining
  them. Confirm new cases appear in the intended runner.
- unittest runs classes alphabetically; UI tests that mutate lib/view state must
  fully restore (incl. `_reload_current_view()`) or later classes see stale rows.
- **`setCurrentItem` on the ALREADY-current item fires NO signal** → the slot
  never runs and the test silently operates on whatever rows an earlier class
  left in the table. This masked RowCache's fake warm rows (`{"id","title":"x"}`
  left in `_row_cache`) for the WHOLE suite — SearchSelection "selected" SET ONE
  as a no-op and filtered old real rows — until a new class cleared the tree
  selection, the re-select genuinely fired, and 'brav' matched nothing. Rules:
  (1) fake `_row_cache` rows are view state — clear them in tearDown; (2) adding
  a class that moves selection can change which later `setCurrentItem`s actually
  fire, surfacing latent poison in classes you didn't touch — bisect predecessor
  classes, not the failing test.

## Metadata / veto plumbing
- Direct-import duration probing must keep both paths: libsndfile handles the
  ordinary WAV/AIFF/FLAC headers cheaply, while Mutagen supplies ALAC/AAC MP4
  container duration. `imports.inject` backfills legacy zero-time records and
  the app persists a repair once. `Track.imported` is the non-negotiable
  exemption from rekordbox cue/placeholder hiding; unknown duration must never
  make a real direct import disappear.
- **Moved direct imports must never trigger a startup tree walk.** Healthy
  records get only the saved-path file check. `soundbox.file_locations` stores
  an opaque Core Foundation bookmark on import/relink and resolves it without
  UI or volume mounting only when that path disappears. Bookmark-less legacy
  records and unresolved cross-volume moves use one background, batched
  Spotlight metadata query with exact filename + recorded size; a duplicate,
  unknown size, stale result, or any ambiguity stays unresolved. Only the
  user's explicit `Fix Missing Links…` folder choice authorizes a bounded
  recursive scan. Apply worker results only when the record still has the
  expected old path, refresh the bookmark after every relocation, and preserve
  the stable `sb-` ID so playlists/overrides/art/waveforms do not detach.
- **File Provider mtime normalization is not permission to recook.** SeaDrive
  can preserve paths, sizes, and audio while truncating nanosecond mtimes to
  whole seconds. On a V5 stat-identity miss, verify the existing boundary
  fingerprint against current first/last 128 KiB using the manifest's recorded
  size/mtime. Only an exact match may publish the cache-bound source-identity
  xattr proof; a size or byte mismatch still recooks. Never rewrite the multi-MB
  V5 payload merely to update a timestamp. `SF_DATALESS` placeholders are a
  separate boundary: matching logical size may provisionally reuse the cache,
  while a new or size-mismatched placeholder stays stale without a background
  decode. Startup must not open/hydrate the cloud source. Deliberate playback,
  export, or a forced recook hydrates it and a later ordinary check verifies the
  local bytes.
  Apply the guard to late-waveform recooks too, not only cache misses, and
  recheck after waits before decoding a queued track. A failed stat must defer
  background work; it is not evidence that a source is local.
- Title pipeline order: effective ARTIST is computed from the RAW/overridden name
  FIRST, then `effective_title = override | tidy(neat(name, artist), junk)`. Junk
  rules: `metadata.BUILTIN_TITLE_RULES` + user words; `Library.title_junk=None`
  means defaults. The five defaults are mixdown, copy/version, BPM/key,
  render/file, and exact `(Original Mix)` removal. Legacy custom copies of that
  exact tag remain in saved state but are owned by the default toggle and hidden
  from custom rows; disabling the default must not re-enable its legacy copy.
  `normalize_title_rules_off` conservatively migrates the former eight IDs. A
  custom plain bracket tag such as `(Club Version)` is one exact literal tag,
  including its bracket type; it does not match bare text, longer content, or
  unrelated empty brackets. Other custom plain words/phrases likewise match
  only as a complete trailing unit or complete paired-bracket contents. Raw
  regex remains the explicitly advanced, run-as-written escape hatch.
- `manifest._excluded(pl, hidden, removals)` is the single source of veto truth
  for sig/entry/diff/prune — any new "don't export this track" feature must flow
  through it or devices won't drift.
- Artist aliases: `build_registry(..., aliases)` applies them TWICE so chains
  resolve (yeschef→YESCHEF!→YES CHEF). `suggest_merges` retains quadratic worst-case
  pair work, with length buckets, matcher-index reuse and quick-ratio upper-bound
  pruning; keep it off the main thread. Original asymmetric rank/tie order matters.
- Row alignment: QTreeView items QSS-locked to `height:24px`; table + rb tree use
  `ScrollPerItem` — don't reintroduce pixel scrolling or padding-based heights.

## Python 3.14 / build
- **`aifc`, `audioop`, `sunau` REMOVED in Python 3.13+** → can't decode AIFF with
  stdlib. Use `soundfile` (libsndfile) — reads WAV/AIFF/FLAC/MP3 (NOT ALAC/.m4a).
- **ALAC/AAC V5 analysis uses `qt_detail_peaks()` (Qt/FFmpeg analysis only;
  playback uses native AVAudioFile/CoreAudio).** THREE traps, don't regress:
  (1) an unreadable file reports
  its error SYNCHRONOUSLY inside `dec.start()` — check `done`/`dec.error()` BEFORE
  `loop.exec()` or the worker strands until process exit → SIGABRT; (2)
  `dec.stop()` is async — bounded `isDecoding()` wait before the object dies; (3)
  `drain()` checks interruption/cooperative policy at each bounded PCM chunk.
  It must consume at most one analysis chunk per event-loop turn so decoder
  callbacks cannot monopolize the GIL; QAudioDecoder backpressure is the pause
  mechanism. Must run off the UI thread.
- **Battery efficiency is cooperative, never a lower-quality cache or a
  colorless loaded deck.** AC uses uncapped optimized analysis. Battery mode
  budgets only bulk worker CPU; below 50%, a large backlog is slowed further.
  Low Power Mode, playback, scrolling, resizing, and focus transitions suspend
  only bulk work at chunk boundaries. The currently loaded track remains
  foreground-critical and bypasses those deferrals. Do not reduce V5
  resolution, skip spectral/color sections, or substitute a colorless
  placeholder to save power.
- **PQTZ-only means direct DAT rows, not a smaller generic ANLZ parse.** The
  legacy grid migration bounds-checks and decodes only the eight-byte PQTZ rows
  from `ANLZ0000.DAT`. It must never call the generic parser or open EXT/2EX;
  thousands of tiny metadata migrations otherwise become a needless energy
  spike. Every path in this migration remains read-only.
- **A V4 cache hit is playable but still V5-upgrade-pending.** Only
  `track_analysis.is_ready()` (or its unchanged preparation receipt) decides
  whether background preparation is done;
  it requires the complete V5 mip/color contract. The player may load any valid
  V4 record immediately. Do not collapse those two meanings. The read-only
  audit reports playable V4 and verified V5 coverage separately.
- **Cancellation must not downgrade canonical analysis.** The native analyzer returns no result
  when its cooperative checkpoint is interrupted. In that case preparation
  must retain the existing record; writing only an imported Rekordbox shell
  discards SoundBox detail data and creates a permanent retry loop.
- **Never put librosa/numba/LLVM in background beat analysis.** LLVM's first
  compilation overflowed a PySide QThread's 512 KiB stack during
  `MCJIT::finalizeObject` and SIGBUSed the entire frozen app; disabling JIT by
  environment variable was not honored reliably by the PyInstaller bundle.
  `track_analysis._beat_grid()` is deliberately NumPy-only (FFT
  autocorrelation + onset-phase comb), so the live worker has no LLVM path.
  `WaveformFillWorker` also reserves a 16 MiB stack as defense in depth against
  future scientific-library lazy imports; do not remove it because current
  code happens not to JIT.
- **A nearly full data volume must pause preparation, never trigger cache
  cleanup.** Full V5 audio cooking pauses below 20 GiB and resumes at
  25 GiB. A verified one-record V4→V5 reduction uses a 5/7 GiB guard because
  it retires its larger V4 only after complete V5 verification. One-at-a-time
  compact V5 replacements also use the migration reserve; they keep the prior
  record until atomic publication and never escalate to full fallback cooking.
  Schedule these ahead of full cooks that may be waiting for 25 GiB. `library.json`,
  its small rotating backups, art, and existing analysis are not expendable.
  Installer app rollbacks are temporary and only one may exist during
  verification.
- **Status presentation has one owner at a time.** Per-file acquisition/copy/
  verification notifications update hover details only; they never replace the
  operation label, track count, pulse or overall percentage. Reserve count width
  against the total, and resize static messages through `_set_status_text`.
  Static messages retire the old animation; transient expiry callbacks are bound
  to a presentation generation so an older timer cannot clear a newer message.
  Background preparation/artwork yields to all device operations (including
  Verify), not just export/delete flags. Returning to Ready restores an active
  analysis pause. The hermetic render harness supports `--analysis-paused` and
  `--sync-progress`; the latter checks that a phase burst never moves the row.
- Build: `./build_app.sh` first builds the arm64 CoreAudio/Metal dylib, then
  PyInstaller collects it alongside pyrekordbox, sqlcipher3,
  sqlalchemy, soundfile, zstandard, and NumPy's standard runtime hook; the
  personal profile additionally collects yt_dlp. Shared composition also collects
  PySide6.QtMultimedia submodules and PIL.
- The app is locally self-signed when the `Soundbox Self-Signed` identity is
  available (otherwise ad-hoc), but it is NOT Apple-notarized → Gatekeeper may
  require right-click → Open. Logs at
  `~/Library/Logs/Soundbox/soundbox.log` (Python errors here; C++ Qt fatals also
  leave `crash-stacks.log` / macOS crash reports).

## rekordbox DB — snapshots and narrowly accepted prep writes
- **Cue aggregates are ordered records, not display slots.** Native JSON keeps
  insertion order while Memory numbers sort chronologically. `CueUpdated` is a
  string counter, and each cue edit advances the global counter twice. Native
  preserves older aggregate sync flag 1, UUID and creation time. Legacy empty
  Comments may be absent from JSON; preserve that representation and all other
  existing fields. `rekordbox_cues` plans rows only; the separate accepted writer
  owns transactions. DB-only native cue add/delete acceptance passed; none of this
  grants ANLZ permission. The separate accepted grid API may publish its exact
  DAT/PQTZ and EXT/PQT2 delta; initial WAV/CBR MP3 analysis uses its independently
  accepted packet-publication domains, never cue authority.
- **Recover before copying a source after a crash.** A spilled rollback journal
  can leave uncommitted pages in master.db; copying DB/WAL alone cannot repair
  them. Refresh preflight uses only a prior explicit authority to recover its
  pending operation. Journals bind the source inode before SQLite opens it;
  replaced files fail closed. Unknown/nonzero rollback journals stop the snapshot.
  Verified inactive zero-header journals remain untouched. Never manufacture a
  data mutation or manually delete a journal to clear this state.
- **Cue edit identity must not recook audio.** Raw source revisions and native
  IDs live outside normalized display dictionaries. Current cache schema11 retains
  those fields (introduced in schema7); older cache paints remain read-only until
  a fresh source read. Cue saves
  publish a new target Track/Library view on the GUI thread and update markers
  directly; tags still require full smart-membership refresh. Never read a native
  cue timestamp from the 20-Hz visual clock or let a save finishing under a held
  creation key start preview on release.
- **The copy-only proof must stay isolated from the live API.**
  `soundbox.rekordbox_write` admits only its marked private temporary copies;
  even its SQL connection helper rejects non-temporary paths. Complete logical
  deltas are verified on an encrypted stage before the working copy is changed.
  Recovery proves exact before/after state and never overwrites an unexpected
  database. Hot rollback journals require a writable connection to the guarded
  copy; read-only SQLite cannot recover spilled dirty pages. See
  [the milestone gates](rekordbox-prep.md). Normal reads still use snapshots.
- **Live My Tag flags follow native evidence, not the offline prototype.**
  On accepted rekordbox 6.8.7.0303, removing an unsynced link with NULL `usn`
  hard-deletes it; removing an older synced link tombstones it while preserving
  `rb_local_synced=1`. The local counter advances without changing its timestamp.
  Adds use new ID/UUID, NULL TrackNo/usn and millisecond UTC timestamps. Unknown
  sync metadata fails closed. `rekordbox_tags` alone owns this API, restricted by
  source identity, exact schema/version authority, expected rows and closed native
  processes. Never generalize to cloud sync or a new version without evidence.
  Sonny operates the real app UI. His later October31 no-controls instruction
  supersedes the earlier generated-reference input grant: do not automate mouse,
  keyboard or Accessibility inputs into his real SoundBox/rekordbox. Private
  fixtures and read-only evidence remain permitted; an old pilot grants no new
  source publisher or real-app input authority.
- **Tag saves and readiness checks own a TaskOwner lane.** Retain through finished
  delivery and join; source selection/refresh/backup/restore must wait. A committed
  write followed by refresh failure is an error requiring reconciliation, never
  permission to retry stale expected membership or blindly restore a whole DB.
- **`db.get_playlist_contents()` returns tracks sorted by ContentID, NOT playlist
  order.** For track order use `DjmdSongPlaylist.TrackNo`. (Sonny's order is
  sacred — this was a real bug.)
- **SMART playlists (Attribute 4) have ZERO `DjmdSongPlaylist` rows** (computed) →
  for them use `get_playlist_contents()`. Attribute: 0=normal, 1=folder, 4=smart,
  -128=cloud(skip).
- **Older removed My Tags can remain as soft-deleted `djmdSongMyTag` history.**
  `pyrekordbox 0.4.4` does not include `rb_local_deleted` in its My Tag
  smart-list predicate, so calling `get_playlist_contents()` directly
  resurrects old genre memberships and also breaks `not contains`. SoundBox
  rebuilds any smart-list predicate containing `myTag`, preserves
  pyrekordbox's other condition clauses and ALL/ANY operator, and requires the
  tag definition, association, and content to be active. `Jump Bros!` was the
  proving row: Trap remained active while its historical DnB link was deleted.
- **A snapshot is `master.db` plus its matching WAL, never `-shm`.** Copying a
  main file and a WAL across a rekordbox checkpoint can mix two points in time.
  Compare device/inode/size/mtime/ctime for both before and after the copy and
  retry the whole pair when either changes or the WAL disappears. SHM contains
  process-local locks/index state and must be reconstructed for the copy.
- `rb_local_deleted` truthy = soft-deleted. Apply it at every visible endpoint:
  content, playlist nodes, ordinary membership rows, My Tag definitions, and
  My Tag associations. Filtering only the parent row is insufficient.
- The native reader now discovers all active audio content independently of
  playlist membership. Admit verified FileType1/4/5/6/11/12; FileType19 placeholders
  can contain paths and must stay excluded. Existing UUID pins remain readable
  for old state but no longer determine Collection discovery. HANDOFF distinguishes
  the current installed build from earlier playlist-scoped readers.
- Direct imports persist source/library, ordered logical IDs/canonical paths,
  destination UUID and analysis preference before source-worker admission.
  Registration and membership requests persist before their SQL calls; verified
  bindings persist before projection. A failed state save replays the same native
  receipt. Retry renews proven uncommitted operations, preserving committed rows.
  Cancellation must reconcile a committed registration before retiring its local
  job; otherwise a failed state save can lose the stable binding. Keep the pump
  paused through the confirmation and recheck ownership afterward. Own membership
  rebase must also order XML clocks strictly when admissions share a millisecond.
  All source domains share the existing worker/root/lock/journal; a pending import
  is a separate export barrier, never part of its own worker's `owned` predicate.
  Existing source aliases require durable inode/epoch proofs and exact raw-row
  guards. Refresh/edit workers recheck canonical alias identity. Curation under
  both IDs requires review, rather than an automatic curation merge. A native ID
  without native analysis must retain captured local grid/cue drafts and pending
  detection. No registration authority follows from these tests or a source read.
- A stale imported/native binding must not invalidate an otherwise verified full
  native snapshot. Normal native deletion is a per-song read concern. Browse with
  only matching ID/UUID/raw/canonical claims projected; retain unmatched native rows
  under their actual IDs. Omit unavailable bound imports from Collection injection
  while preserving their durable records and drafts. Never reinsert a removed song
  from a local import record. Default transaction projection remains strict;
  malformed/duplicate/source-conflicting bindings still fail closed. An import job
  for an unavailable binding stops before SQL, without disabling unrelated edits.
  `source_binding_issues` is a transient read result, recomputed per snapshot.
- Finder hover state must survive source readback rebuilding the sidebar during
  the drag. Store a `QPersistentModelIndex`, which Qt invalidates when the row is
  deleted; retaining a Python `QTreeWidgetItem` wrapper can dereference a deleted
  C++ item on the next move/drop and prevent Collection admission. The regression
  covers row replacement followed by a real file-drop event and durable intent.
- Validate `Attribute`, parent folders, sibling `Seq`, membership `TrackNo`,
  reachability, stable IDs, and unique display paths before accepting a tree.
  Keep exact `TrackNo` order. An ambiguity or an unverified smart field must
  fail refresh visibly, never degrade to an empty/all-tracks playlist.
- **`DjmdPlaylist.ID` is STABLE across rename + move** → the device-sync identity.
- **"Date added" = `content.created_at` (the DB row's real timestamp, time-of-day
  precision), NOT `DateCreated`.** `DateCreated` is a stale tag/metadata date that
  can predate the actual add by weeks (e.g. a track bounced 6/29, added 7/10 reads
  `DateCreated=6/29`, `created_at=7/10 16:43`). Collection's newest-added sort keys
  on `created_at` → `Track.added_ts`. `DateCreated[:10]` stays as display `date_added`.
- rekordbox reorders → manifest sig is order-sensitive → reorder = drift = resync.
- rekordbox auto-writes a `PIONEER/` folder onto any USB it touches. It is a
  hard external-product boundary: count its space, but never use it for drift,
  manifest ownership, verification, cleanup, import, browsing actions, or an
  ordinary write. The separately accepted `cdj_*` boundary is the scoped
  generic-FAT32/exFAT native-catalog/export exception, with its own mount/source/
  metadata/recovery guards. Explicit generic whole-volume Erase remains a
  separately confirmed destructive action.

## Cover art (optional SoundCloud provider + shared Rockbox/stock rendering)

The SoundCloud details below apply only to `soundbox_personal.discovery`.
Public has local image controls and retains existing cached art/provenance, but
imports no personal provider or `yt_dlp`. Gate online direct commands and startup
scheduling as well as menus. Never clear misses/blocks on provider detach. The
tracked `Soundbox.spec` is the only packaging composition; use `build_app.sh`
with `personal` (default) or `public`. Packaged profile selection is immutable
for that build; source-only `SOUNDBOX_PROFILE` cannot enable a frozen module.

- **Paste an ARTIST PROFILE URL into the art picker → you get the AVATAR**
  (og:image of a profile page IS the avatar; lands baseline-JPEG via `store_ok`).
- SoundCloud deep extract 403s → use FLAT search `thumbnails` (t500x500) for
  covers; artist avatar via the page `<meta og:image>`, NOT yt-dlp's user API.
- Rule: real title match → track cover, ELSE artist avatar (never a different
  track's cover).
- **`Unknown Artist` must never drive unattended art discovery.** A title alone
  is not accountable enough, and searching the placeholder can select an
  unrelated same-title upload or even a random profile using that name. Leave
  it coverless for a manual artist correction/picker/URL. Manual art remains
  available and is recorded with `origin=manual`; the background worker records
  `origin=auto` for new cache rows.
- **Missing provenance is protected artwork.** `retry_weak` may clear misses and
  explicitly automatic avatars only; URL/confidence cannot identify manual work.
  Background completion must use `store_auto_if_unfetched` under the shared lock,
  never an earlier todo-list decision to overwrite a now-curated entry.
- **Artwork review is read-only until individual approval.** Compare exact
  displayed metadata/bytes before publication. Preserve the original and receipt
  in the same transaction as the approved replacement, retain one original per
  track, and refuse stale/corrupt recovery. The review More menu deliberately
  omits Undo. Artist-profile lookup is a worker-owned strict profile fetch,
  previewed for approval before using the same exact-baseline replacement path.
  Missing/excluded profiles, changed selections and closing cannot publish it.
  The single Exclude Uploader manager retains explicit preferences and lists
  known seller-rule matches; automatic seller blocks cannot be removed there.
  Preference changes filter only blocked proposals in the open queue, without
  clearing unrelated previews, checks or session decisions. Cancellation closes admission and drains
  TaskOwner; a finished-but-undelivered fetch must not apply after dialog close.
- **Suggested contains prepared alternatives, not a library-sized task list.**
  Check every All entry, including manual and unrecorded art, while keeping
  scans read-only. Show the initial loading wheel before revealing ready
  comparisons, keep whole-library progress visible, and explain empty results. Keep stamps
  exact cover metadata/SHA plus artist/title; Next is only a session dismissal.
  Replace is already explicit approval and stamps the accepted context too.
  Completed metadata checks resume from disposable `art_review_scan/` SQLite
  state, not from duplicated images. Restore every exact saved context before
  the first online request, so an uncached first row cannot block cached
  comparisons later in the list. Show pending-work progress separately from
  saved checks/review decisions; recheck decisions before a queued search starts.
  Never cache a failed request as a miss;
  pause after repeated failures. Bump its matching-policy version when matching
  semantics change. More can explicitly start a fresh pass. Preserve stale-result generation checks,
  exact baseline checks, memory bounds and TaskOwner lifetime through shutdown.
- **A compact overview zero is not independent silence proof.** Imported
  overview heights can be the very same quantized PWV5 heights. A repair gated
  only on those heights misses quiet audible passages. At zero compact weight,
  BLUE/RGB overview rendering uses resident high-precision audio geometry with
  authored hue; do not fabricate color or sound from a source-specific exception.
  True zero audio stays background; PWV7 remains independent. Check both whole-track
  overview and main lane against the real reported cache, not only positive-weight
  synthetic compositor inputs. Do not rewrite caches or sources to fix rendering.
- **Review audio shares the native deck.** It plays only the selected local
  track on explicit Play, uses the ordinary grid/origin load and gain-ramped
  pause/seek, and suppresses playlist autoplay. A source-change fade and a pending
  grid load must remain cancellable when selecting another row or closing review.
  The review snapshot must include actual native playback as well as button
  intent: a transient PAUSED/refill report can clear the latter while audio runs.
  Explicit pause-in-progress wins, and resume can cancel that fade.
  Stop review transport timers/intent before native teardown. Keep/Replace/Next
  advance below the selected row; later scan callbacks must not reset an end
  cursor to the first suggestion. Explicit browsing can choose earlier rows.
- **Preview eviction never evicts queue metadata.** Comparison thumbnails may
  persist separately in `art_review_scan/` SQLite, capped at 8 MiB / 128 entries,
  at most 512 KiB per image. They are disposable, outside full backups and never
  canonical artwork/publication authority. Exact suggestion contexts and review
  decisions remain separate; adding this cache does not bump matching policy.
- **A tracking URL or smaller JPEG is not a new artwork suggestion.** Review
  compares SoundCloud upload identity without tracking query/fragment or mobile/
  web host aliases, before downloading previews. Other providers retain their
  query semantics. Equivalent resized/recompressed designs are omitted using
  bounded colour/layout comparison. Different artwork remains selectable;
  source identity is not publisher trust or authority to overwrite anything.
  Keep/Replace currently save exact per-track decisions, not global ranking
  feedback. A deliberately chosen image from another song must never train
  a false song identity or blanket trust in its uploader.
- **Artist derivation and artwork share version credits.** `(So Sus Edition)`
  derives So Sus ahead of an original-artist tag, while manual overrides win.
  Normalize Unicode identities: precomposed ADÉLA and decomposed Adéla cannot
  disagree. A requested named version must not rescue the original or another
  edit. Exact short titles may match one collaborator; review-only alternate
  accounts/wording need independent artist and song evidence, not hard-coded aliases.
- **Excluded-source fallback is separately authorized policy.** Never display or
  export the historical excluded image; retain it for exact recovery. Fetch only
  a strictly matched artist profile, not a guessed public song for an unreleased
  purchase. Apply under the state owner with a fresh baseline; a manual edit made
  during the request wins. Preserve originals/index and earlier review recovery
  under `art/source-recovery/`. These intentional profile defaults stay out of
  Suggested and bulk avatar re-scans. Unavailable profiles remain hidden. Other
  manual and unrecorded art is still protected from automatic replacement.
- **Source exclusion fences every boundary.** Seller/dealer patterns include
  Plate Selling and related plate/dubplate role variants; a bare plate/dubplate
  word or a title mentioning sales never excludes an artist. The rules apply to
  likes, search, avatars, suggestions, previews, URLs and final publication.
  Titles mentioning dubplates are not uploader identities. Preferences live in
  backed-up `art/source-policy.json`; provider receives only the runtime set.
  Review may show artist-anchored possibilities below the automatic threshold,
  clearly labeled. Uploader similarity is never official-account proof.
- **Orient hyphenated working titles with the effective artist.** Libraries use
  both `ARTIST - TITLE` and `TITLE - ARTIST`. Unconditionally dropping the left
  side made `CRASH TEST - WODD` use `WODD` as its song core, so any upload from
  WODD could receive the own-account rescue. `_core_title_for_artist` selects
  the opposite edge, and the unattended full-title floor is 0.80. Legacy cache
  rows predate provenance and require a read-only confidence/source/image audit;
  never infer that a `blocked` row or a confidence-1.0 picker/URL result is safe
  to replace merely because an old automatic row looks suspicious.
- **Remix credits are not hyphenated artist edges.** Remove bracket groups before
  orienting the song core. `PHAROAHE MONCH - SIMON SAYS (MYTHM FLIP)` must yield
  `SIMON SAYS`, and `Huey- Pop, Lock & Drop It - (SAINT MILLER REMIX)` must yield
  `Pop, Lock & Drop It`. Reordered working names and small spelling differences
  receive a separate own-account rescue only with matching named edit credits
  and strong song evidence. Distinct remixers, edit types and VIPs stay distinct.
  Structured SoundCloud artist credits can account for a label upload with an
  exact song core; descriptions/tracklists cannot.
- **SoundCloud connection credentials never enter library JSON or logs.** The
  personal connection uses Keychain and client-credentials public-profile reads;
  new setup has no browser/loopback authorization (legacy OAuth remains backend
  compatibility). Choose Always Allow on the app-specific macOS Keychain prompt
  to remember approval; Allow grants one access. Do not loosen the item to trust
  every application. Paginated likes are read only from the exact API origin; rotating
  refresh tokens persist immediately. Cancelled/failed scans retain the previous
  same-account complete index; account changes clear it before scanning.
  The disposable index lives separately under `connections/soundcloud/` and is
  excluded from library backup/restore. Only confident, unambiguous artist-name-
  matched liked uploads precede general search; other accountable likes remain
  fallback evidence, including online-search failure. Connecting never clears known misses, manual art or
  blocks. Connection workers use TaskOwner; isolated/test harnesses never read
  live Keychain or start account refresh. Setup: [connection guide](soundcloud-connection.md).
- **Managed defaults are a fallback layer, never discovered or per-track art.**
  An `ok` track cover or artist avatar always wins. Both natural misses and
  deliberate `blocked` rows receive a pool picture when the pool is non-empty;
  the underlying miss/block remains intact, so removing the pool reveals the
  same state and auto-fetch still skips blocks. Visible/exportable tracks are
  deterministically shuffled over the pool in exact round-robin balance.
  The separate pool manifest persists assignments; rebalancing must preserve
  valid mappings and move only the minimum needed for exact balance, or one new
  real cover creates gratuitous full-library device-art drift. Imported pool
  images are copied to `art/defaults/`, capped/normalized as
  baseline JPEG, backed up recursively, and then enter the ordinary MA446
  RGB565 and per-track Rockbox BMP derivative pipelines. Never make a default
  row `status=ok`, or it will suppress real discovery and masquerade as curation.
- yt-dlp = gray area → PUBLIC fork must NOT bundle it.
- **⚠ Rockbox can't render progressive/multi-scan JPEG, RLE BMP, or PNG.**
  HANDLED: `soundbox/artencode.py` normalizes ALL cached art to baseline JPEG;
  `store_ok` enforces at the cache boundary. Any NEW art path (manual picker!)
  must go through `ArtCache.store_ok` or `artencode.ensure_device_safe`.
- Rockbox first searches dimension-specific art requested by the theme, then
  repeats its track/album/cover search without the dimensions. AdwaitaPod Dark
  Simplified requests 160×160 in its WPS and 116×116 in its SBS mini-player.
  Read bounded square `%Cl` dimensions from the active WPS/SBS and write them as
  per-track `filename.<side>x<side>.bmp` siblings, retaining prior exact sizes.
  Also write a lightweight dimensionless 160×160 `filename.bmp` fallback. A
  160-only export makes one home screen blank; exact-only export makes a theme
  switch blank. NightPod is the acceptance case: WPS 130×130 and SBS 30×30,
  with optional `%Cl` placement flags after the dimensions. Folder `cover.*`
  files can collide when several tracks share a folder.
- **Embedded art outranks that external cover and is a low-memory risk.**
  The real `Take a Trip (DIRTYSNATCHA REMIX)` MP3 decoded cleanly but carried a
  3000×3000, 17,432,315-byte APIC. The track froze on Rockbox even though the
  installed theme displayed no covers; controlled original/picture-free/
  external-cover A/B playback isolated that APIC as the trigger. Rockbox
  exports therefore strip picture
  records only from a plan-scoped device payload, verify MP3 frames/WAV data/
  AIFF SSND/MP4 mdat/FLAC frames plus technical properties unchanged, and clear
  large staging after the batch. Composite payloads also retain only the
  effective SoundBox title/artist metadata: otherwise Rockbox can display a
  scrambled `iPod_Control/Music/Fxx/ABCD.*` filename, album, year, or comment
  even while iTunesDB is correct. Both
  transformations happen only in verified disposable copies. Never retag the
  canonical source. Do not keep a full playlist's normalized copies through the
  confirmation dialog: a large Collection can exceed available Mac space even
  when it easily fits the iPod. Planning must verify one derivative, retain its
  exact canonical-source and output size/hash contract, and discard it; commit
  rematerializes and verifies one track at a time. Managed Rockbox covers are uncompressed 8-bit paletted BMPs:
  retained exact theme sizes plus a dimensionless 160×160 fallback. Do not pass
  the canonical 500px image through to Rockbox or touch stock ithmb art.
- **Do not rebuild every committed derivative to plan an incremental sync.**
  Schema-3 media rows carry canonical source size/mtime/ctime, effective
  title/artist, payload hash, and the per-file media profile. Reuse that exact
  committed contract when path/size/mtime/display/policy still match. Normal
  Sync tolerates ctime-only drift without refreshing old proof timestamps;
  explicit Repair retains the conservative source checks. Stat-check planned
  sources again before the first device write using their respective policy. Once a row records an explicit
  media profile, do not reopen it for embedded-art or
  title/artist inspection during every plan—the committed profile is the policy
  result; source size/mtime, display or policy changes invalidate it. A changed source,
  display field, or media profile must still take the full derivative
  verification route. A narrower legacy profile cannot prove that competing
  metadata is absent and therefore requires a one-time verified migration.
- **Do not hash unchanged device artwork during a no-op plan.** A Collection
  owns thousands of tiny BMPs; reading every one merely to retain it has reset
  the real MA446 USB transport with `ENXIO`. When the verified Mac variant's
  size/hash matches the owned manifest row, retain that device path without a
  live read. A planned artwork rewrite or deletion still requires the exact
  live-byte baseline before any mutation.
- **An in-flight iPod refresh is not a blank device.** Keep the immutable last
  verified Device/Songs/Playlists snapshot visible through planning, commit,
  deletion, and the post-write read. Replace the three surfaces atomically only
  when that read succeeds; on a transient read failure, retain the snapshot and
  label it as the last verified state. A successful commit is not user-complete
  until that snapshot refresh and the following sidebar free-space/drift scan
  both finish. Show `Finalizing iPod...` during those reads and delay the success
  dialog until Eject is actually available; never make dismissing an alleged
  completion dialog start another blocking device worker. `QMessageBox` runs a
  nested event loop, so install the explicit eject-ready latch before releasing
  `_exporting` and opening the modal; that latch pauses recurring polling through
  the dialog and releases on dismissal, while leaving Eject genuinely enabled.
  Do not make Eject cancel, adopt, or finish dashboard work: no such work
  may remain at the success boundary. Queue Eject behind a finishing background
  read; writers, planners and imports must reach their safe boundary first. Suppress new polling through the normal
  unmount outcome, without starting verification. A no-write/up-to-date plan is
  still a completed Sync or Delete and must pass through the same final scan/latch before its OK.
- **Device progress is a transaction meter, not a copy meter.** Planning owns
  0–30% for composite sync, guarded device work owns 30–80%, and final
  dashboard/sidebar/drift reads
  plus worker-ownership release own 80–100%. Keep it monotonic across queued
  batches and multiple targets. Internal planner units can be roughly twice the
  unique managed source union, so use them only for the percentage bar: show
  animated `Planning iPod...` with no `#/#`. Show the counter only under
  `Syncing audio`, where it represents actual audio work. Byte-level acquisition/
  copy phases show transferred/total bytes in the status tooltip, retaining
  spaced units and a spaced slash. They never replace the visible track count.
  Reserve counter width against its total for the whole operation. Queued-job formulas
  must give each job its complete proportional slice. Never publish 100%, `Complete`, a success modal,
  or enabled Eject/Sync while any required worker attribute remains owned. A
  failed required final read is terminal but not successful: retain Eject,
  report `Final refresh failed`, and suppress the success callback.
- **An explicit managed-playlist Delete is authoritative.** Do not force a
  separate Sync merely because a different schema-3 playlist changed or is no
  longer available from the current source. Reconcile other available managed
  playlists in the same transaction; reconstruct an unavailable unrelated
  playlist from its exact committed manifest snapshot so it is retained rather
  than silently deleted. Validate malformed identities/snapshots and keep all
  ordinary manifest-last rollback guards. Schema-3 presentation fields such as
  `device_name` are not a reason to refuse the user's checked deletion.
- **Rockbox playlist appends are incoming membership, not media imports.** The
  three-second poll may use the owned M3U8 size because a catalogue append must
  add a path line; do not hash every unchanged playlist on every tick. Confirmed
  planning reads/hashes only bounded managed M3U8 payloads and may authorize an
  external baseline only when the committed track sequence is an exact prefix
  and every appended path maps through schema-3 ownership to one current source
  track. Authenticate the historical M3U8 prefix against its recorded hash to
  recover stable IDs: old Mac paths may have disappeared while the original
  device playlist is still valid. Use that proof in downstream stock/Rockbox
  source mappings too: fixing only the append reader leaves moved sources
  failing later as unavailable or unowned. Keep original manifest recovery
  bytes and hash moved sources before reusing derivatives. Only local custom
  SoundBox playlists accept incoming membership; native-adopted customs retain
  their device key but do not adopt incoming device edits. Never create new
  rekordbox playlist overlays. Baseline-check and
  persist the exact custom membership before device writes. Deduplicate custom
  membership by track ID, canonical source, or exact source bytes. Normal Sync
  may reuse matching committed hashes; otherwise hash only size-matched
  candidates. Retain the first occurrence, including older duplicates. Never merge by title/artist. Unknown paths
  are not imports; deletion/reorder edits and SoundBox-removal conflicts fail
  closed. Rockbox may serialize primary-volume entries as `/<HDD0>/...`; strip
  only that exact leading HDD0 token before schema-3 ownership lookup and
  canonical M3U8 regeneration. Expected playlist drift must not erase the
  separately valid read-only device identity/database dashboard: show the
  affected playlist as Sync needed, but never present its changed entries as
  trusted before confirmed planning.
- **Rockbox payload metadata has one authority.** Some purchased MP3s store
  `Purchased at Beatport.com` in `TXXX:COMMENT`/`COMM`; WAVs may have several
  INFO/ID3/BWF containers containing receipts, years, albums, or stale titles.
  Themes and Rockbox parsers do not choose consistently among them. Strip all
  competing user-facing metadata from the disposable payload and write exactly
  the effective SoundBox title/artist pair. Never modify the canonical source.
- **MP3 tag size is not an audio property.** Mutagen estimates the duration of
  some CBR MP3s from total file size, so replacing a padded ID3 block can move
  its reported length by milliseconds while every MPEG frame remains exact.
  The Rockbox derivative contract compares MP3 sample rate/channels plus the
  exact tag-excluded audio-essence hash; never reject an otherwise byte-exact
  payload because that tag-size-dependent duration estimate changed.
- **Rockbox preferences and tag cache belong to the user/firmware.** Sync must
  never rewrite `config.cfg`, `.new`, `.old` or other firmware preference files,
  including auto-update, playlist limits and tag display. They are not ownership
  baselines or independent Sync drift. Active theme sizes may be read for artwork.
  Never synthesize or patch `database_*.tcd`. Database maintenance is optional;
  exported playlists do not require it. Dashboard freshness is observational,
  not authority to change settings or caches. Higher playlist limits consume
  memory; let the user choose rather than silently raising them.
- **A forced Rockbox reset can interrupt settings saves.** In 4.0, saving queues
  a storage-idle callback to write `config.cfg.new`, promoted at the next load;
  `fixed.cfg` is loaded afterward. In the September 27 playback test, settings
  survived normal Play/Pause shutdown after reverting with Menu+Select reset.
  Confirm persistence before drawing conclusions from a settings A/B test.
- **Do not require unsupported firmware settings.** Rockbox 4.0's
  [settings table](https://raw.githubusercontent.com/Rockbox/rockbox/v4.0-final/apps/settings_list.c)
  has no `sort playlists` key. The September 27 hardware config omitted the key
  after firmware saved its preferences, causing a false Sync-needed loop. Do not
  add or require it; preserve existing unknown lines without treating them as
  authority. Catalogue order on the validated device uses filenames with a zero-
  padded manifest position (`01 - HOUSE.m3u8`). Keep stock titles and manifest
  `device_name` clean, retain full rekordbox hierarchy in the manifest, and add
  folder context only for duplicate leaves. Reorder is a transactional rename
  of tiny owned playlist files, never a reason to duplicate or recopy media.
  FAT resolves case-only names to the same directory entry, so `DnB` to `DNB`
  cannot use the ordinary write-new/delete-old path. Preserve the verified old
  path in the playlist plan, recovery-back and unlink that owned entry before
  publishing the requested spelling, suppress its duplicate stale manifest
  row, and restore both the original spelling and bytes on rollback. A distinct
  file at a non-case-equivalent destination remains a hard collision.
- **Deleting the last managed track can expose directory AppleDouble clutter.**
  Scrubbing only `._filename` for transaction payload paths misses Finder's
  `._Artist` directory sidecars after the real tree becomes empty. Composite
  Repair (or journal-backed interrupted recovery) treats regular `._*`/`*.sbpart`
  below `/Music` or `/Playlists` as verified transient orphans, permits a cleanup-only empty-library plan,
  and removes empty child directories only after the manifest-last commit.
  Do not use `._*` presence as drift: Finder can recreate those files as soon
  as a clean export finishes, creating a permanent false Sync-needed loop.
  Normal Sync/polling does not walk the media trees for these files. Recovery
  journals signal interrupted work; explicit Verify/Repair finds unjournaled
  `*.sbpart` files.
- **Rockbox WAV titles are RIFF INFO, not ID3-in-WAV.** Its WAV metadata parser
  reads `LIST/INFO` `INAM` and `IART`. Mutagen's normal WAVE tag writer creates
  an ID3 chunk that Rockbox ignores, causing a shared `ABCD.wav` to display its
  scrambled filename. A real source also carried a 23.5 KiB opaque `NITR` field
  before `INAM`/`IART`; desktop parsers found the artist but classic Rockbox did
  not. Put one small canonical title/artist INFO list immediately after `fmt`
  and remove every competing INFO/ID3/BWF/XMP display container from the
  disposable copy. Normalize every WAV payload even when its text already
  matches, preserve all PCM bytes, and verify the resulting RIFF values.
- **Composite drift includes effective metadata.** Membership-only playlist
  signatures cannot notice a SoundBox title/artist edit. Keep the schema-3
  `metadata_sig` generated by the same effective metadata function as planning,
  and pass both override maps into every sidebar/dashboard drift worker.
- **Never regenerate an orphan to prove it can be deleted.** Export profiles
  intentionally evolve (the physical 500px → 160px cover migration exposed
  this). Manifest ownership plus the exact live baseline authorizes cleanup;
  rename the live file into same-volume transaction recovery, restore that
  exact file on failure, and discard it only after manifest-last success.
- **A dual device view is not an iTunesDB view.** Build Songs/Playlists from the
  schema-3 manifest and parsed iTunesDB together, label rows Both/Stock/Rockbox,
  and read only verified small M3U8 payloads for order. Reporting the iTunesDB
  count or Apple smart indexes as the whole device library is misleading.
- **The curated baseline JPEG is canonical for both firmware paths.** Never
  embed it into source audio to satisfy stock firmware. `stock_artwork/` under
  AppData is disposable content-addressed RGB565: MA446 format 1028 is
  100×100×2 bytes and 1029 is 200×200×2 bytes. Background preparation
  pauses during playback; sync still verifies every payload hash.
- **Stock artwork is four coupled layers:** MHIT (`mhiiLink`/count/size), one
  per-track ArtworkDB MHII with a matching song dbid, MHNI format offsets, and
  the two ithmb pixel files. Never update only one layer. Full replacement and
  compaction preserve ArtworkDB/ithmb bytes first; incremental sync follows the
  unreferenced-slot rule below. Validate all bounds, then publish the linking
  iTunesDB last. Missing/incomplete old pixels fail closed.
- **Incremental ithmb writes may touch only currently unreferenced slots.** Keep
  image IDs stable, reuse free fixed-size holes before appending, and publish
  ArtworkDB before iTunesDB. Hash free-slot pixels during the Mac-side plan and
  relink an exact match without rewriting it; only a nonmatching free slot needs
  a range write. Never overwrite the current track's live slot.
  Failed range writes can leave harmless orphan pixels; a later plan rediscovers
  them as free. Full playlist deletion remains the compaction path.
- **The Mac stock shadow is acceleration, never authority.** It lives under
  AppData `stock_ipod_state/`, keyed by library persistent ID + volume UUID, and
  requires matching identity, model, iTunesDB, ArtworkDB, and ithmb facts.
  Missing/mismatched state performs full reconciliation. Save it after device
  commit; shadow-save failure must not roll back a healthy iPod.
- **A valid stock shadow may still contain only a subset of source mappings.**
  Playlist-scoped bootstrap must not classify a later-selected track as new
  merely because its source key is absent from the shadow. Reconcile only the
  missing candidates against live device media by size/suffix and exact content
  hash before planning a copy. Likewise, compare prepared RGB565 payloads with
  their current device slices when the local artwork cache key is absent. A
  partial acceleration index must never turn an artwork-only edit into duplicate
  audio or unchanged artwork writes.
- **Do not content-scan manifest-proven managed stock media during an additive
  plan.** An exact source→DBID/path/payload baseline already proves those rows.
  Building original/payload reconciliation keys for them can make every queued
  playlist stream the existing iPod library and has produced macOS `ENXIO`
  (`Errno 6`) before any write. Narrow content reconciliation to newly selected,
  unproven sources; their partial-shadow adoption rule remains unchanged.

## Rockbox / iPod (Phase 3 — hardware)

- Do not stream large interrupted device payloads merely to construct the
  confirmation dialog. A dual recovery plan may identify an exact-path,
  exact-size candidate, but commit preflight must verify its full expected
  SHA-256 before the first write. Report that verification through normal sync
  progress and persist completed proofs in the Mac-side journal.
- **The mounted on-disk layout selects the exporter.** SoundBox does not know
  which firmware the user last booted. `/.rockbox/` alone uses ordinary Rockbox
  export; `/.rockbox/` plus `iPod_Control/` uses only the MA446/FAT32 composite
  path. Root `/Music` files are not automatically visible to stock firmware.
- **Dual-library means shared files, not two exports.** Stock-safe media lives
  once under `iPod_Control/Music` and is referenced by both iTunesDB and Rockbox
  playlists; stock-unsafe media lives once under `/Music` and stays absent from
  iTunesDB. Schema 3 extends the original stable-ID/signature/drift manifest and
  coordinates the ownership graph and rollback. Ordinary schema-2 export must
  refuse schema 3; generic Sync must route it through the composite planner,
  never either independent exporter. Playlist rename/move/content/removal is
  coordinated across both firmware views. Managed audio uses its schema-3
  ownership/path/size baseline and a reproducible exact source-side payload;
  art and playlists retain live hash checks. Rename removed/replaced owned audio
  into same-volume recovery before final manifest publication; restore those original bytes on rollback.
- **Content equality is not library identity.** Deduplicate repeated playlist
  references to the same canonical source path, but never collapse two distinct
  source paths onto one iTunesDB record merely because their bytes match. The
  real full-library plan contained 19 byte-identical pairs and six had different
  effective title/artist labels. Each distinct source needs its own DBID,
  metadata/artwork, and scrambled device path.
- **Scrambled stock names require filename artwork, not folder artwork.** For
  `/iPod_Control/Music/Fxx/ABCD.wav`, write `ABCD.160x160.bmp`,
  `ABCD.116x116.bmp`, and fallback `ABCD.bmp` beside it, plus required theme sizes.
  Never guess the audio suffix when recording cover membership: retain an explicit cover→audio
  mapping because shared files may be WAV or MP3.
- **iPod MUST be FAT32, NOT HFS+.** Rockbox does not support HFS+.
- m3u8: UTF-8 **no BOM**; absolute-from-drive-root paths (`/Music/Artist/...`).
  The Playlist Catalogue default dir is `/Playlists` and it does not recurse:
  dual sync writes direct, zero-padded children such as `01 - HOUSE.m3u8`,
  never `/Playlists/GENRES/HOUSE.m3u8`. Keep full hierarchy in the manifest;
  stock/SoundBox titles use the concise leaf unless a collision needs context.
- Rockbox DB is OPTIONAL (playlists play without it). SoundBox never enables
  automatic database update or writes firmware preferences. If the user has
  enabled updating, SoundBox may report that the firmware-owned cache predates
  the latest sync. Cache age and preference changes alone are not Sync drift.
  Stable playback is not a reason to change settings or initialize the database.
- Device sync manifest lives at `<device>/.soundbox/manifest.json`.
- **A USB remount can defeat rollback without corrupting the committed
  generation.** `DeviceSession` must reject the new mount identity; a read-only
  stock/composite plan must also carry its pinned identity across the
  confirmation modal and compare it before commit performs any write. Never cross
  the remount to delete paths remembered by the old session. A dual recovery
  directory named for the still-current manifest can authorize a later retry to
  adopt only freshly planned path/size/SHA-256 matches. Persist each completed
  full-hash proof in Mac-side disposable state and recheck its size, timestamp,
  and edge fingerprint so repeated USB resets resume verification rather than
  rereading all prior survivors. The UI uses recorded transaction outcomes; an
  incomplete rollback must retain recovery and must never claim that all new
  files were removed. Unknown outcomes must not claim no device writes.
- **A filesystem-browser confirmation grants authority to one mounted volume,
  not a reusable path string.** Pin before showing rename/new-folder/artist/delete
  input, carry that session into a delete worker, require all selected items to
  share the mount, and revalidate recursively without following symlinks. M3U8
  device import is read-only but still must confine every resolved entry to that
  volume; otherwise `..` or an on-device symlink can copy an unrelated Mac file.
- **Dual playlist drops are confirmed jobs, not hidden planner mutations.** Ask
  Yes/No for each drop before expensive work. Deduplicate by stable playlist
  identity and retain FIFO intent. Once the active worker finishes, coalesce all
  already-confirmed waiting playlists into one next generation so iTunesDB and
  the same managed-media baseline are not reparsed for every drop. Drops that
  arrive after that plan starts form the following batch. Carry one cumulative
  entry counter/summary across the queue and cancel unseen queued work after a
  failure; never revive hidden stale intent later.
- Device detection is read-only and capability-based. See `DEVICES.md`.
- `soundbox.stock_ipod` is the sole exception to the `iPod_Control` no-write
  rule. Its first boundary is stock-only MA446 + checksum-zero `iTunesDB` +
  MP3/WAV: Clickwheel renders only off-device, SoundBox reparses and audits the
  database plus artwork linkage, archives recovery bytes, copies media/artwork
  atomically, and commits the database last. Never pass the live mount to
  Clickwheel's high-level copy/sync
  helpers; they do not meet SoundBox's rollback, stale-state, play-count, or
  smart-playlist contract. On a dual-boot FAT32 MA446, only
  `soundbox.dual_ipod` may invoke this transaction. It supplies verified
  picture-free payloads, pre-copies Rockbox-only files, publishes Rockbox
  playlists after the validated stock commit, writes schema 3 last, and uses
  exact stock rollback if a later publication fails.
- **Apple Videos is the only deletable smart index.** It must match the exact
  `Videos` title plus MHSD-5 type 0. A real Apple database can contain both an
  ordinary-MHLP smart alias and the canonical MHSD-5 record with different
  playlist IDs. Prefer the canonical record in the device snapshot and remove
  both representations when that canonical ID is selected; otherwise the UI
  can report success while the true index remains. Neither representation is a
  media owner or candidate. Preserve all other smart rules and never generalize
  this to Music, Movies, TV Shows, or Audiobooks. On-The-Go does not exist in
  iTunesDB and cannot be deleted there.
- **The stock Artists → artist → All level is firmware UI.** SoundBox supplies
  clean title/artist metadata and the intended artist indexes, but classic stock
  firmware inserts its own `All` level before the songs. Do not corrupt album or
  artist metadata trying to remove that accepted firmware navigation layer.
- **MHBD `DatabaseID` is not a device identity.** Apple and SoundBox database
  rewrites legitimately replace it. The library persistent ID remained stable
  across every captured generation; pair it with the macOS volume UUID when
  remembering a confirmed model. `stock_ipod_info` owns read-only UI snapshots
  and may merge `Play Counts` only in memory.
- **The validated MA446 Rockbox path has real hardware acceptance.** The
  original/picture-free/external-cover `Take a Trip` A/B proved the giant APIC
  caused the freeze; picture-free media, theme-native art, full-Collection
  commit, firmware database refresh, and zero-padded playlist order all passed
  on-device. Do not generalize that result to another iPod model, checksum
  family, or schema-2 migration without a separate fixture and physical gate.

## Sync performance and cloud source proofs

- Export modules and disposable artwork indexes are lazy. Do not restore eager
  stock/dual/CDJ/filesystem imports in desktop presentation or annotation imports.
  Keep mount detection active. Empty DeviceScanWorker scans must not construct
  export playlists; detected targets request one GUI-thread input capture through
  queued delivery. Cancellation/interruption must release its wait without GUI
  delivery; capture errors mean unknown drift. Canceled scans cannot preload
  dashboards. Stock warming requires an attached iPod, stops cooperatively on
  disconnect and retains its slot through finished delivery. Reconnect schedules
  a current-library pass after any retiring worker.
- iPod drift availability/display reuse lasts one poll only, keyed by path and
  track identity respectively. It is presentation, never a source proof. USB
  playlist indexes must preserve duplicate-name ambiguity, folder type, foreign
  library membership and evolving bindings. Filesystem destination indexes retain
  FAT Unicode/case collision rules and exact deterministic suffixes.

- A waking SeaDrive File Provider can return ENEEDAUTH (errno 81) before its
  account registration completes, even when the account/server works. macOS
  CloudStorage reads get two cancellable three-second waits, reopening and
  hashing/capturing from zero each time. Partial snapshots are discarded;
  EAUTH, permission errors, non-cloud sources and exhausted retries still fail.
  The budget does not extend the one retry for unstable completed captures,
  nor impose a deadline on a blocked OS read. Never keep Finder open as an
  application dependency or refresh ctime proofs without exact-byte evidence.
- Composite source failures during preflight carry their path and a no-device-
  writes outcome. Only actual transaction evidence may describe rollback or
  committed cleanup; cancellation retains its typed result.
- A successful cloud read is not a durable source timestamp. A long composite
  plan can outlive hydration/eviction metadata changes. Normal composite Sync
  retains an unchanged committed contract across ctime-only drift without
  claiming a new proof. When new/relocated/changed sources need fresh evidence,
  reprove exact bytes before accepting new timestamps; never replace the proof
  baseline after an unverified change.
- Completed relocation/dedup hashes persist individually under Rockbox staging
  `source-verification-v1/`, so a later error does not erase all progress. Every
  reuse checks canonical path, filesystem/inode, size, mtime, and ctime. This
  is disposable source evidence, never device ownership or permission to skip
  destination verification. Staging cleanup deliberately preserves it.
- Stock format probes can hydrate cloud placeholders. A verified planned
  derivative matching a managed stock payload's size/hash already has a known
  compatibility result; retain independent DBID/path identity checks and live
  size checks for affected paths or explicit Repair. Unchanged normal-Sync
  inventory does not need thousands of per-file USB metadata reads.
  Unproven/changed sources are inspected once per canonical source per plan.
- Batch the initial multi-playlist drop as well as jobs queued behind it.
  Generic exports coalesce contiguous jobs for one target and scale unique-file
  progress to playlist-entry progress; keep different targets in FIFO order.

- Idle eject never calls library verification. Repair warnings are informational
  for eject; normal diskutil refusal is the authority, without a force fallback.
- Once acquired, all audio inspection and transformation must use the local
  snapshot. A cloud stat check followed by reopening the source is not a stable
  snapshot and caused the CRANKDAT build-108 failure.

- **Artwork worker completion must retire its own instance.** The September 26
  offscreen crash showed QThread destruction concurrent with ArtFillWorker
  startup. A finished-but-undelivered thread still owns its slot: never replace
  it merely because `isRunning()` became false. Capture the worker in its
  finished connection and clear only that exact instance. Shutdown callbacks
  must not schedule new artwork passes while the event loop drains.

## Offline iPod metadata tests

`tests/test_ipod_capture.py` requires the preserved offline fixture;
`SOUNDBOX_IPOD_CAPTURE` overrides its documented repository-local location. It copies
private captured metadata into temporary writable fixtures and supplies generated
audio. Omitted historical audio has sparse size placeholders whose reads fail:
passing these tests proves database/transaction behavior, not historical audio
integrity, USB reliability, or playback. Never run mutations on pristine captures.
A no-cache full reconciliation must retain unchanged database bytes; renderer
header changes alone must not manufacture a Sync requirement.


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

Post-sync polling is held through the completion dialog only. Dismissal releases
the hold so subsequent SoundBox edits can update drift without reconnecting.

## September 27 software audit — outstanding boundaries

The A1 timed-wait gaps are corrected: ArtPickerDialog, SuggestDialog and
interactive waveforms use `TaskOwner`. Main-window shutdown stops and drains
child owners as well as its directly collected workers. Adopt before start;
retain until queued finished delivery and final join. Search socket/retry budgets
are explicit, but they are not a total cancellation deadline. An uncooperative
operation must remain owned until it actually returns.

September 28 follow-up: state publication now uses `state_io` and
`state_repository`. Never bypass the shared area lock for canonical art/library
writes. `artwork.ArtCache` stages and publishes image/index/default changes with
rollback; `artfetch` only re-exports those local types for compatibility.
Backup snapshots hold the same lock. Restore stages/validates before publication,
retains exact originals in `.state-transaction`, and fences old consumers by area
generation. Startup recovers interrupted publication; `.state-retired` is only
cleanup after a completed commit/rollback. Never recursively delete an active
journal or its commit marker to "fix" a failed operation.

The restore UI must drain old writers/results before publishing and reload only
after QThread.finished plus join. Editing and background admission are paused;
active device work prevents entry. Corrupt library JSON/shapes enter recovery;
without a valid backup, saving stays disabled until explicit restore. Unknown
extension namespaces survive saves. The application entrypoint holds one AppData
lock, so two app processes cannot independently write that state. Failure/race
injections use disposable fixtures only. The original September 27 partial-restore
reproduction remains historical evidence in the audit.

The coordinator serializes the active device lane; its tested queue API is not
yet the queue used by desktop adapters. See the prioritized
[software audit](software-audit-2026-09.md) and [roadmap](ROADMAP.md) before
expanding lifecycle or persistence claims.

Identical library saves may skip publication only with matching persisted bytes
and filesystem identity under the existing state lock/generation guard. Deletion,
replacement and restore invalidate reuse. Zoom/jump/display/autoplay preferences
coalesce for 400 ms; curation, backup/restore and shutdown still flush immediately.
Stock-art preparation receipts are metadata-only scheduling hints; selected sync
continues checking payload hashes. Deleted/changed derivatives invalidate receipts.
Never pace unchanged art with per-image sleeps.


- **Grid-only edits cannot upload waves or reset clocks.** Use the native
  generation-bound grid setter and keep the override through late overview/full
  uploads; clear it only on beginTrack. Reapply live cue rows after late overview
  metadata too. Partial opening bars count as 0, not 1.
- **Native grid boundaries use saved intervals.** Midpoint insertion and leading/
  trailing extrapolation use adjacent saved timestamps, not nominal BPM math.
  Half truncates integer hundredths (90.03→45.01). Grouped nudges retain ordered
  steps: a clipped row cannot return merely because net offset is zero.
- **Fence stale preparation before saving a source grid.** Wait out publication
  under its worker fence from the source thread, then suppress that worker's old
  target publications. Never use Graveyard's discard operation for grid edits:
  its delayed deletion could erase a freshly verified V5 record. A status-clear
  timer has the window as QObject context so destroyed windows cannot receive it.


- **Paused bar-start editing follows the visible cursor.** Do not reuse cue
  authoring's forced native poll for a paused grid edit: a decoder/visual position
  mismatch can turn the click into an apparent no-op at the old beat. Read the
  displayed millisecond position after normal loaded-track guards. Playing
  placement still uses fresh native time. Quantize affects cue creation, never
  the grid-edit target. Keep a one-click, zero-before-first-row regression and
  distinguish installed user acceptance from a simulated stale-clock case.
- **Do not drop clicks during the saved-grid probe.** A loaded deck can be
  seekable before the separate source identity/grid check finishes. Keeping a
  visually unchanged button disabled through that gap swallowed the first
  click. Capture its immutable intent and preview from loaded RAM rows immediately.
  Those rows grant no source authority: require an exact checked-base match before saving,
  cancel on failed checks, and drain accepted intents on normal shutdown. A
  disabled-button timing trace is distinct from a cursor-capture mismatch.

- **A worker thread still competes for the Python GIL.** Live encrypted backup
  capture must not call OfflineWorkspace's complete `_state` research inventory
  or create its unused working database. Use the dedicated encrypted DB/WAL
  capture, retaining SQLite integrity/identity and exact publication hashes. The
  full semantic inventory remains mandatory for offline proofs. Closed-app
  guards use a fresh exact-name OS query at every original check boundary;
  never replace them with cached process absence. Snapshot-cache JSON encodes
  individual records and writes bounded chunks; one whole-library C encoding
  can block table model callbacks even from a background thread.

- **Manual BPM is not the double/half protocol.** Native entry also changes
  AnalysisUpdated and the primary contentFile hash/size/history, with +4 local
  counters. It rebuilds the existing row count before extrapolating from the
  rounded tail; a single unbroken formula differs by milliseconds. Gate manual
  writing separately, bind the file row into recovery, and keep browser BPM on
  the verified-save signal. A focused BPM editor must retain typed text through
  grid/status refreshes and abandon it when the loaded track changes.

- **Hot-cue playback intent is distinct from a paused audition.** A hot cue
  pressed during ordinary playback must retain that intent across its 3-ms seek
  fade and release, including repeated taps before the native PLAYING poll.
  Paused hot cues and session/memory cues retain hold-preview. Previous/next
  uses the same intent-preserving load path as browser activation. A new Pause
  during a pending seek must replace its gain completion even with no `after`:
  keeping the old epoch/completion can strand `_pause_pending` or restart audio.
- **Smooth source translation is not proof of perfect presentation cadence.**
  The October 27 populated-window probe removed RGB 1500 pixel-step error, but
  both builds still had zero-time dropped drawables and occasional 16.667-ms
  presented gaps. Keep those rows and the actual presentation/target timestamps;
  do not claim the residual hitch is fixed from arithmetic tests or mean FPS.

## Manual BPM terminal-row evidence — October28

`138`, `138.0` and `138.00` already parse to identical hundredths. STOP CAPPIN's
apparent 138.01 lock was an EOF proof failure: a proposed beat at 128,750 ms sat
inside the 150-Hz PWV3 uncertainty interval. Native 138.00 keeps that exact-EOF
beat. `AmbiguousGridEnd` now permits a bounded PCM header proof only in the source
worker. Existing final rows inside the interval need the same proof for subsequent
edits. Never round an unsupported endpoint or weaken the pure planner's guard.
No payload read/decode or source audio changes; journal and revalidate the proof
through publication and recovery. Full protocol: [prep plan](rekordbox-prep.md).


## Native CDJ pilot — October28

- Keep ordinary stock/dual/Rockbox PIONEER guards intact. Only the dedicated
  generic-FAT32/exFAT CDJ session admits its authorized native paths; reject iPod,
  Rockbox, hidden-library, source-overlap, linked and FAT-alias targets.
- Capture current raw DB cues and source playlist order from the read-only
  snapshot, after accepted source-edit intents drain. ANLZ can have stale cues.
  Never export player overlays/display mips as native authority or waveform bytes.
- A grown DeviceSQL row can move to another page. Renumber typed track rows by
  their new page-local slot before table replacement; a one-page fixture is not
  a scalable proof. Independent page/index checks complement the Rust reader.
- Preserve raw classic browse/color rows and Plus menu/category/sort/color rows.
  General updates patch only requested property fields; a full StickSettings
  rewrite can normalize unknown flags. DEVSETTING unknowns are read-only and
  existing player/mixer profile files survive unrelated sync byte for byte.
- A USB playlist drop is the authorization. Preserve mount/UUID through deferrals
  and hand off plan→commit at worker.finished without a second modal question.
  Export and Save to USB also publish directly. Reformat alone has a
  destructive target confirmation. Drop scopes selected
  playlists; it must not reconcile unrelated source edits or audit retained media.
  Existing unrelated classic/Plus differences are separate baselines, not a
  global equivalence veto. Prune only members removed by this selection, after
  counting every remaining playlist in both catalogs; preserve shared assets and
  unrelated collection-only records. Exact removed assets stay recoverable until
  durable commit, then retire their backups to free device space. Resume partial
  committed retirement; never require already retired originals for rollback.
- Package build/cdj/release/soundbox-cdj. A debug renderer adds substantial CPU
  cost to multi-GB input. Captured new audio may pre-exist only as the exact
  Contents set in a fresh local partial output; no metadata/extra files/links.
  Retained catalogs merge off-device; raw media is never a synthetic placeholder.
- Both databases and changed assets share one recoverable journal. Original bytes
  remain live or in same-volume recovery until each replacement. The manifest
  publishes last. Recovery requires the exact Mac-side intent receipt, validates
  the affected set. A newer coherent native export supersedes the old pending
  catalogs without rolling back over rekordbox; only small catalog copies are
  checked. Never recover from an untrusted
  USB journal alone. Worker ownership and shutdown rules apply through finished.
- The native executable accepts only explicit local workspaces; it never
  discovers devices or source libraries. The installed parent captures files and
  owns publication. Pin source/lock files and package the executable plus notices.
- Offline fixtures cannot establish CDJ hardware support. Record model/firmware
  and retain the native control comparison; newer OneLibrary hardware is separate.

- Native USB catalogs are shared with rekordbox, never exclusive manifest authority.
  Use source-library ID plus track ID and map source-local tag IDs. Drop obsolete
  asset ownership when another exporter changes/removes a record, without deleting
  its files. Retained inventory is not a fresh integrity certificate.
- My Settings Talkover −∞ uses separated bytes123/131, not one enum byte. Finite
  levels clear131; preserve124–130. Mic Echo uses a two-byte rational. Legacy
  unset Mic Echo/Headphones B display1/None without rewriting them on no-op.
- Nested Qt layouts inherit spacing. Set USB settings column/row spacing explicitly;
  the32-point column gap otherwise becomes32 points between every settings row.

- Qt QHeaderView.restoreState succeeds for older section counts but unhides new
  sections. Store columns_keys alongside the header state and explicitly hide
  fields absent from that saved schema; legacy states know only the original10.
  Test the actual ten-to28 transition, not only same-count round trips.
- FooterBar has a fixed28-point height. Cancel is20, progress18 and the count20
  with zero label margin; showing them cannot resize the footer or viewport.
- Display-only native USB reads may copy a stable exportLibrary.db/WAL pair into
  a local workspace and parse it there. Never copy SHM locks or open live USB SQL.
  Include WAL/journal in freshness stamps. This is presentation, not publication
  authority; a write still rejects active/unreconciled SQLite companions.

- Track information prepares its selected cover before modal show, so “No
  artwork” cannot flash ahead of an existing cover. Canonical resolution and
  display-sized decode stay in its TaskOwner worker; preparation dispatches Qt
  timers/results with user input excluded and a wait cursor. A contended art
  lock can delay opening. Build Info fields on demand; do not prefetch art during
  track load. Publication refreshes matching open dialogs after the save, with
  coalesced replacement reads and the prior cover retained until ready. Closing
  retains/drains through finished/join and prevents a pending initial show.
  See [the polish audit](ui-polish-audit.md) for coverage and remaining GUI costs.

- Pinned rbxport source retains upstream whitespace exactly. The narrow vendor
  `.gitattributes` exemption keeps `git diff --check` useful for SoundBox files
  without normalizing hash-verified dependency bytes. Vendor verification remains
  mandatory; do not extend that exemption to application code.

## Analysis and track menu — October29

- The hamburger belongs immediately right of Q. Use the same44×28 geometry and
  flexible gaps as the grid strip, preserving clock width and Search alignment.
  Menu and General waveform choices share one setting; no second color cache.
- Beat countdowns must interpolate both current and off-grid cue positions on
  the actual RAM intervals. Counting grid rows up to a rounded cue overcounts
  partial beats. At a cue, target the next strictly later memory cue; outside
  supported rows/no next cue means blank, not an invented zero. Cache cue times
  when they change; never scan source data or add a transport timer per frame.
- Captured rbxport detection is a separate durable result, not source authority.
  Check decoded budgets before allocation. Cancellation must reap the process
  before staging is removed. TaskOwner callbacks run before retirement finishes:
  do not synchronously close/drain the same dialog from that callback. Automatic
  completion defers close one event turn; manual completion shows detected values.
- `settings.analysis_results` lives in backed-up library.json, outside disposable
  waveform caches. Bind path/hash/exact filesystem identity, preserve native and
  manual grids/cues, and never route arbitrary detection through the accepted
  manual-grid writer. The two-way source publication gate remains explicit.
- Manual edits of captured imports live in a revisioned `grid_draft` beside the
  raw detector result. Publish through StateRepository before changing RAM/browser
  rows. Compare the loaded rows at admission, merge against the newest draft on
  late analysis delivery, and pin BPM as well as rows through late manifests.
  Key-only/inconclusive analysis also retains the grid component's tempo-policy
  provenance, including the raw estimate used before a whole-BPM decision.
  Native/manual source priority and exact capture binding remain independent;
  neither a draft nor a waveform fallback grants registration/analysis authority.
- Standalone CDJ export supplies tracks directly, never a hidden fake playlist.
  Include those identities in locally bound interrupted-work receipts. Preserve
  native/managed playlists and prove the unchanged repeat performs no writes or
  retained media reads. Harnesses still deny live source/device discovery.

## Upstream analysis/waveform tuning — October30

- `AnalysisPreset::Rekordbox` and `Rbxport` are not equivalent in the pinned code:
  the former disables tempo following. Our default now uses the adaptive preset
  and attack placement. Keep scheduling priority distinct from DSP choices.
- A full head is not proof of a valid tail. Stream past the bounded30-minute/96M
  head and compare declared frames even after it fills. Unknown codec endpoints
  still confer no source-publication authority. Preserve legacy analysis and each
  component's engine when a partial or inconclusive result retains previous data.
- PWV5's even red0–14 extraction/division is equivalent to0–7; do not halve red
  twice or copy upstream per-row peak normalization over accepted RGB display
  policy. All seven 3BAND palette values already matched. The demonstrated issue
  was overview layer loss, fixed by independent profiles rather than new colors.
- Upstream's own waveform notes distinguish empirical calibration, detail/overview
  data and conditional close-zoom heuristics. Reference code alone does not prove
  universal pixel parity. Preserve frozen inputs and compare actual native draws;
  the scoped39-frame main-lane before/after comparison is exact. See the
  [audit](rbxport-reference-audit-2026-10.md) for constants and limitations.


- A completed detector result is not proof of native publication. Persist the
  ordinary analysis intent, run it on the shared source lane, and require native
  row/packet readback before retiring its receipt. Registration alone still
  leaves BPM/key/grid/waveforms absent. Preserve logical/native IDs and current
  playlist membership throughout. The latest BREATHE request explicitly requires
  this automatic sharing; older generated pilots provide no permission.
- An opening boundary beat can be omitted even when the later grid is correct.
  Whole-v4 restores only an implicit beat1 before beat2 near one period, with
  measured audio in the first1ms and a rising attack in4ms; it never shifts later
  rows or invents an onset across silence. Preserve Sonny's newer manual BREATHE
  draft exactly instead of installing the earlier proposed prepend-only draft.
- Initial native analysis may use optional absent mixer/gain records, as the
  accepted real reference and existing native rows demonstrate. Never copy the
  generated32s fixture's gain/peak onto real music. Native integral PCM Length
  reflects the last sample index; do not copy Length31 onto arbitrary durations.
- Source save callbacks may arrive while the user is making new edits. Verify
  old curation as preserved data plus recorded new actions, and inspect native
  bookkeeping differences explicitly. Never restore older whole state because
  an obsolete verification baseline no longer equals the live user library.


- **Keep Dock icon commits out of live color preview.** The October31 request
  applies the saved accent after OK and startup. Only `_apply_theme_color` invokes
  `app_icon.apply_saved_icon`; `_render_theme` never does. Cancel must preserve the
  prior application icon. Cache only the last normalized accent, preserve neutral
  template pixels/alpha/shading. Persist through NSWorkspace custom-icon metadata
  at the app root, never a rewrite of Contents or runtime re-signing. Ordinary
  deep signature verification remains valid; strict packaging verification rejects
  the user-owned Finder metadata, so verify strict signing before customization
  and prove the signed Contents bytes separately afterward. Isolated/development
  runs must never customize the installed app. Native icon captures need Display-ICC normalization before
  comparing colors; Apple's native TIFF mode is not a generic Pillow fixture.

- **Convert registered audio with its existing identity.** A cross-format native
  Relocate may follow the old inode into a backup or reject the new extension.
  Do not send users through a chooser for an automatic conversion. The separate
  locator authority updates path/name/type/inode on the existing record, retaining
  UUID, cues, grid, key/BPM, playlists/history/tags and XML. File conversion and
  locator publication have distinct durable receipts on one shared source owner.
  A crash between them resumes the existing conversion job. Never borrow bare
  registration's INSERT permission, create duplicate content rows, or restore an
  old entire library to repair a location.

- **Trash directory access is separate from moving an exact owned file.** macOS
  can allow rename into ~/.Trash while denying directory open/fsync/enumeration.
  Never sync_dir(Trash) after rename. Sync the source parent and exact target inode
  with F_FULLFSYNC. Deterministic UUID-tagged exclusive moves let recovery check
  exact paths after process loss before progress publication. Validate under the
  signed app identity; a Terminal/Codex-only test can have broader privacy access.

- **Native terminal beats can sit past audio EOF after a nudge.** Do not truncate
  durable native intentions to decoder duration: the accepted writer retains that
  row, and a later opposite nudge can return it. Native prep v2 matches that exact
  arithmetic; cue capture positions remain bounded by the actual audio.
- **Grid and cue receipt shapes differ.** Only cues provide after_revision. Grid
  confirmation reconstructs snapshot revision from encoded track/content-file
  AFTER rows and actual DAT/EXT hashes. A test fixture that invents after_revision
  for grids conceals restart/failed-ack bugs. Exact receipt/base proofs migrate
  legacy intentions without reapplying an already committed nudge.

## November1 unified-playlist follow-up

- `tree` and `sb_tree` now alias one widget. Source rows carry `NATIVE_TREE_ROLE`
  even when XML lacks a native ID; rebuild only the intended row domain. Folder
  navigation keeps the previous active playlist. Sidebar Up/Down route into the
  track table without changing the sidebar selection.
- Decoder-ready and grid-ready are independent events. Refresh grid admission on
  the manifest signal too; otherwise a fast decoder can leave bar-start disabled.
- Use indexed conversion facts for browser rows. A scan of the entire conversion
  cache for each song made Collection clicks quadratic. Decoded Track.path caches
  only its URI conversion; filesystem/native authority still validates separately.
- Native merge mappings persist with admitted source requests. Never infer a
  binding from an existing name or discard original custom data to cut over UI.
  A native adopted custom keeps its original device export key, but it cannot
  adopt iPod additions. All source domains retain their own authorities.

## November2 native command performance follow-up

- Reuse only successful immutable reconstruction keyed by exact raw plan/record,
  authority/profile and verified payload digests. Fresh source/apps/paths, ALL
  payload hashes and SQL/readback guards still run on every use. The bounded
  analysis memo is4entries/30seconds and retains no plan objects or connections.
- An analysis store constructor already loads/verifies its plan. Check that
  `store.plan` matches the durable job before apply and retry; a separate loader
  repeats large encrypted inventories. Keep legacy WAV and separate CBR dispatch.
- Collection registration can finish from its one fresh full library observation
  only after strict native binding projection and atomic durable acknowledgement.
  Failed local saves replay committed source receipts. Tags retain full refresh
  for intelligent memberships; native playlist saves use their targeted catalog.
- Cue handle reuse requires fresh authority, source inode and exact native ID/
  UUID/path bindings. Underlying backup counter/epoch verification, closed-app
  and recovery guards remain live. Never turn store reuse into a media proof cache.
- Native command timing logs measure dispatch, worker execution and GUI
  acknowledgement separately; unknown durable-FIFO wait is explicit. Private-copy
  performance is not observed native UI or cloud latency. The November2 delivery
  used a closed-app window; current installed/running state is in HANDOFF and
  does not grant permission to close Sonny's app.

## November2 second native performance pass

- Process absence is never cached. The native kernel-query helper caches only
  its loaded function interface; malformed/denied/incomplete snapshots fall back
  to strict pgrep. Test with uniquely named private processes, never fake real
  rekordbox names while Sonny's actual app is open. Verify all three packaged
  native binaries against tested code/data sections, including the process dylib.
- A plan memo seeded in prepare must bind the ARCHIVED bytes to the exact bytes
  actually proved. Computing a first archive hash after an unchecked copy can
  bless corruption. Locator tests fault all four transfers before first archive
  hashing; proof-bracketed known digests must match before seeding.
- Promoting every delayed prep timer without a native-owner deadline spins source
  workers: the common worker emits both changed and idle after completion. Keep
  a separate monotonic native-open backoff; tests send repeated wake signals and
  require zero new probes before deadline, then one fresh attempt.
- Integer-WAV import no-op detection is header-only only when canonical, known
  PCM16/24 and no conversion receipt exists. Receipt/dangling link/symlink/unknown
  headers remain on the old guarded path. Real registration still captures bytes.
  Broader compatibility conversion retains provider-safe captured-header reads.
- Cue saves do not rewrite an already identical derived grid cache. Compare its
  small header with fresh native grid readback; grid edits or external grid
  changes keep normal fencing/refresh, and malformed disposable cache cannot
  stall the independently verified cue save.

## November3 broader efficiency follow-up

- ScrollEnd can occur between rapid trackpad gestures; immediate accent removal
  creates gray flashes. Keep120ms idle feedback, including phase-less wheels;
  an inactive axis receiving only End must not acquire a highlight/timer.
- Cheap USB scroll capture mutates the stored state dictionary. Suppress recording
  while deferred restoration applies both scrollbars, or the first valueChanged
  can overwrite the second saved target. Flush coalesced preferences before
  backup/restore and explicit shutdown/snapshot captures.
- Exact-byte validation reuse is valid only after a fresh locked read equals the
  previous validated payload. Corrupt/changed bytes require full validation.
- Playlist row maps require ordered replacement/delete/re-add behavior and the
  original duplicate-ID fallback. Queue indexes retain per-job path/UUID checks.
- Borrowed immutable waveform buffers require synchronous native copying. Do not
  retain their pointers in C++ or replace them with mutable borrowed storage.
- Pure bounded provider text caches must not expand to credentials, candidates,
  account/network results or ranking. Review depth and component timings do not
  constitute physical export or exhaustive dependency acceptance.

## November3 correctness and remaining-cost pass

- A matching SHA cannot justify pairing captured bytes with a replacement file's
  after-stat. Require stable descriptor/path identity; metadata drift before the
  read can still pass. Keep bounded retry and all downstream freshness guards.
- Shared untouched records require replacement-only ownership. Conversion/prep
  callbacks clone touched nested state and restore exact prior owners on failure;
  general queue helpers still return fully detached preferences/epochs.
- A committed receipt alone does not authorize rebasing arbitrary later intents.
  Reconstruct the exact accepted transition from fresh verified retained bytes
  and require current catalog/XML equality. Historical Collection-pin proof is
  separate, allowing unrelated later edits while binding original/current UUIDs.
- Capture folder descendants from guarded native memberships, not visible sidebar
  tracks. Persist exact retention before deleting; restart/failed acknowledgement
  cannot rediscover missing memberships from the post-delete browser. Legacy
  retired receipts without original UUID proof must not guess a replacement.
- SQL WHERE can choose a different cue index. Final selected readback retains
  table traversal (NOT INDEXED) plus exact Python identity filtering; hot refs and
  equal-time Memory colors/order must match, not only sorted display rows.
- USB source_revision binds all exported tag definitions, including unassigned
  categories. Keep original manifest model/raw bytes paired through make_plan
  and late no-op construction. Never reread a newer manifest as authority for an
  older model. Explicit settings patches still verify their captured base bytes.
- Profile-only USB inventory refresh during explicit export writes only the
  manifest; unchanged DB/profile/media bytes and poll-only behavior remain exact.
  An unrelated renderer source change is not a policy version bump. Deliberate
  catalog semantic changes must bump the explicit reusable-render policy.
- Provider stream wrappers must preserve decoding, short reads, typed writable
  buffers, invalid-argument no-consumption behavior, primary exceptions and pool
  ownership. Guard actual receives on supported adapters; never bypass a custom
  response.read transform through fp.read1. No worker force-kill is permitted.
- Dialog selection signals must not clear busy state. Late callbacks check closed
  Python state before touching deleted widgets. Drain tasks before deleteLater;
  parent ownership otherwise retains closed image/widget trees for the session.
- Bulk artwork default rebalance is path-dependent. Preserve intermediate pure
  assignments even when serialization is deferred, and recheck current avatars
  under the art lock so a newer real cover/manual block wins.
