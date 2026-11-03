# SoundBox

A native macOS (PySide6) music-library bridge from **rekordbox** to **stock or
Rockbox iPods / USB players** — browse the live rekordbox library, curate it,
edit existing My Tags, and drag playlists onto a device: exact order, per-track
cover art, honest sync semantics.

## Start here

SoundBox is a desktop app for keeping a curated music library and exporting it
to portable players. It reads rekordbox through snapshots, saves accepted My Tag,
cue and grid edits to the source, preserves source audio during ordinary playback/
export, and keeps title/artist edits and artwork in SoundBox's own state. Explicit
recoverable compatibility conversion and confirmed Graveyard Delete are separate
audio-changing workflows. It
provides a CoreAudio/Metal player for previewing tracks before export.

### Requirements

Rekordbox prep editing is being developed in reviewed stages. [My Tag, cue and grid editing](docs/rekordbox-prep.md)
requires an explicitly accepted library on rekordbox 6.8.7.0303. Close rekordbox
and its background agent before saving. Cue editing supports WAV/MP3/AIFF with
verified metadata. Grid controls support setting a bar start, whole-grid nudges,
and constant-tempo double/half under the same accepted source boundary. Manual
BPM entry on native tracks requires its separate acceptance flag; `138`, `138.0` and
`138.00` all submit the same two-decimal value.
Direct imports register/reuse native records and durably route playlist membership.
Captured imports and native songs admit durable cue/grid intentions during source
work. Initial shared analysis supports the separately accepted bare stereo
PCM16/24 WAV44.1/48kHz full BPM/Grid+Key profile; broader profiles remain gated.
Smart-list creation remains a subsequent milestone.
New cues appear and become usable immediately in the playing session while
guarded database saves run in the background. The existing bottom-left footer
names the edit being updated, then shows “Done” after verified persistence;
window close and Cmd+Q keep SoundBox open while captured imports, analysis or
source saves remain; editing and save workers stay available. Quit again after
those saves finish. Durable prep intentions survive failure/restart
and reconcile exact native receipts before retry; unknown source states stay guarded.
Grid edits also update the loaded session immediately, including during the
initial source check; verified saving remains in the background.

- **Apple Silicon Mac with macOS 14 or later** for the native player. The native
  build targets `arm64`; this is not an Intel or cross-platform player build.
- **Python 3.14** and the pinned packages in [`requirements.txt`](requirements.txt)
  for development, plus **CMake 3.24+** and Apple's command-line developer tools
  for the native CoreAudio/Metal library.
- A local rekordbox library or XML export, or audio files imported directly into
  SoundBox. The desktop app reads the live database through a read-only snapshot;
  rekordbox can remain open.
- An explicitly selected, supported destination for device export. Source audio
  and the destination must not overlap.

### Run from source

From the repository root on a Mac with those prerequisites available:

```sh
python3.14 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
./build_native.sh
./.venv/bin/python soundbox_app.py
```

The `.venv` location matters: the build and check scripts use it directly.
The native build produces `build/native/libsoundbox_deck.dylib`. If the deck
cannot load, SoundBox reports the problem and disables playback while leaving
library management available.

General Settings includes a **Theme color** picker and Reset button. The accent
updates selections, controls and the SVG logo’s BOX layer immediately and persists
with your settings. **Text color** independently selects White, Black or a custom
color for lettering on theme-colored selections and buttons (White by default).
Click a color swatch for the picker, or its hex text to type a six-digit RGB value.
Both pickers preview live as you move through colors; OK saves and Cancel
restores your previous appearance. Text-color switching updates immediately.
Changing the accent never overrides that choice. The logo retains the previous
header footprint.

### First workflow

1. Open the desktop app and browse the rekordbox library, or import audio from
   Finder into Collection, a SoundBox playlist or directly onto a native rekordbox
   playlist. Compatible imports enter both Collection views without a separate save.
2. Curate titles, artists, playlists, and cover art in SoundBox. Edits remain
   SoundBox-owned; they do not retag your source audio.
3. Double-click a track to load it. Playing decks keep playing; paused decks stay
   paused. **Space** toggles the loaded deck;
   **C** is momentary CUE. Use the waveform and overview to inspect the track. The
   prep-strip clock shows elapsed/total time, including hours for long sets.
4. Connect a supported player and inspect its identity under **Devices**. Drag a
   playlist to the device and follow the displayed planning/confirmation flow.
5. After later edits, use **Sync** when the device shows **Sync needed**. Review
   the result, then use **Eject** before disconnecting.

| Destination | Supported path and limits |
| --- | --- |
| Rockbox | Filesystem export with ordered M3U8 playlists and per-track artwork |
| Generic FAT32/exFAT USB | Native CDJ pilot: classic Device Library + Device Library Plus, prepared together; playlist drops start directly |
| Stock MA446 iPod | Guarded stock database export; validated MP3 and supported WAV only |
| FAT32 MA446 with stock firmware and Rockbox | One composite transaction and a single shared copy where both firmwares support the audio |
| Other stock iPod models | Read-only; stock writes are not enabled |
| rekordbox/CDJ `/PIONEER` tree | Dedicated CDJ boundary only; excluded from ordinary stock/dual/Rockbox/filesystem writes |

October28: Sonny advanced the native CDJ pilot to the active milestone. Both
library formats share one selection, playlist order, audio/artwork/analysis set and
recoverable transaction. USB settings are editable directly, with explicit global
My Settings import/apply. Sonny reports playback, artwork and waveforms working.
The full CDJ-3000 control matrix, newer OneLibrary hardware and redistribution
review remain open. Physical iPod acceptance is still pending. See the
[CDJ contract](docs/cdj-usb-export.md) and [roadmap](docs/ROADMAP.md).

Background upkeep reuses unchanged analysis and prepared artwork, title edits
refresh only their display row, and idle device watching waits for mount changes.
Ordinary player preferences save in a short batch; library edits still save
immediately. Generic USB export optimization remains a separate upcoming task.

See [`docs/DEVICES.md`](docs/DEVICES.md) for exact format, ownership, recovery,
and model constraints. The desktop app is the product: `python -m soundbox`
exposes an older XML-based maintenance CLI, not the desktop curation and device
workflow.

### Build, verify, and install

```sh
./check.sh
./build_app.sh
```

The check script runs lint, the native build, engine type checks, and the test
suite. `./check.sh --render` also creates an offscreen UI render. Run the script
directly so a failed check remains visible; historical test counts in handoff
notes are dated evidence, not a promise about a new checkout. Every discovered
test must run and pass; skips and expected failures fail the release gate.
Required offline fixtures and provisioning are documented in the
[release test guide](docs/test-release-gate.md).

`./build_app.sh` builds the full personal app; `./build_app.sh public` builds
`dist/public/Soundbox.app` separately. `Soundbox.spec` is the shared packaging
authority. The personal build writes `dist/personal/Soundbox.app`, stamps its source
revision, profile, modules and source fingerprint, inspects package contents, and verifies
its signature. It uses the local `Soundbox Self-Signed` identity when available,
otherwise ad-hoc signing; it is not Apple-notarized. Build does not install the
app. Follow the [state-preserving install procedure](docs/ARCHITECTURE.md#build--install)
to back up curated state, retain the previous bundle for rollback, replace the
app, and verify its signature, launch, and library before accepting an upgrade.

### State and troubleshooting

- **Curated state:** `~/Library/Application Support/LowFreqLabs/Soundbox/` holds
  `library.json`, history, and curated artwork. Use Settings backup/restore;
  never wipe this directory to fix a player or cache problem.
- **Logs:** `~/Library/Logs/Soundbox/soundbox.log` records startup and runtime
  diagnostics. A missing native deck requires checking the macOS/architecture
  prerequisites and native build, not rebuilding the music library.
- **Missing imports:** use **Fix Missing Links…** on unresolved tracks and select
  a bounded search location. Ambiguous matches are not guessed automatically.
- **Device unavailable or export refused:** check the selected volume, filesystem,
  model, and source/target separation. Ordinary writes exclude `/PIONEER` and
  unsupported stock models; do not bypass those guards.
- **Runtime acceptance:** source tests and offscreen renders do not substitute
  for playback, physical-device, or real curated-state verification. The latest
  recorded checks and remaining acceptance gates are in [`docs/HANDOFF.md`](docs/HANDOFF.md).

## Docs & roadmap

See [project layout](docs/PROJECT_LAYOUT.md) for checkout contents, retained
fixtures and external diagnostic evidence. Superseded investigations are indexed
in [the documentation archive](docs/archive/README.md).

- **[`AGENTS.md`](AGENTS.md) / [`CLAUDE.md`](CLAUDE.md)** — synchronized
  runtime orientation + sacred safety contract (start here).
- **[`docs/HANDOFF.md`](docs/HANDOFF.md)** — exact current device/app state,
  rollback facts, validation, and next-session sequence.
- **[`docs/ROADMAP.md`](docs/ROADMAP.md)** — active sequence, release gates and deferred work.
- **[`docs/PLAYER.md`](docs/PLAYER.md)** — native deck behavior, architecture,
  caches, cue panel/editing, non-regression rules, QA, and staged grid work.
- **[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)** — engine/desktop layout, data model, build.
- **[`docs/DEVICES.md`](docs/DEVICES.md)** — Rockbox, stock iPod, dual-boot,
  and generic-USB layouts, capabilities, and validation rules.
- **[`docs/GOTCHAS.md`](docs/GOTCHAS.md)** — Qt/native-player/rekordbox/Rockbox
  traps (read before touching workers, player, or sync).
- **[`docs/IDEAS.md`](docs/IDEAS.md)** — exploratory QOL backlog; UX
  workshop before roadmap commitment.
- **[`docs/cdj-usb-export.md`](docs/cdj-usb-export.md)** — CDJ pilot write contract, reference evidence and remaining hardware/distribution gates.

The [October efficiency audit](docs/efficiency-audit-2026-10.md) measures startup,
browser and recurring work and separates candidate improvements from shipped behavior.
The [November performance audit](docs/performance-audit.md),
[cloud identity audit](docs/cloud-library-audit.md),
[test-runtime audit](docs/testing-runtime-audit.md), and
[native playlist ordering guide](docs/native-playlist-order.md) record the newer
measurements and organization/loading contracts.
The [software audit](docs/software-audit-2026-09.md) records architecture and
lifecycle gaps. The [release boundary](docs/release-boundaries.md)
defines the shared app and optional personal providers. The public build excludes
the current SoundCloud provider and `yt_dlp`; the personal build retains them.
Both use the same library format, local artwork controls, player and device guards.

## What it does

**Rekordbox playlists.** Right-click to create a playlist/folder, rename or
delete it. Drag existing registered tracks onto a native playlist and use Remove
from Playlist to save membership changes to rekordbox. Songs remain in Collection.
Edits wait while rekordbox or its agent is open and report success after verified
publication; pending errors offer Retry or Discard. This accepted writer is bound
to the current library/version. Compatible direct imports register in Collection
and route into their chosen native playlist through a durable queue. Intelligent/
cloud edits remain separate. New float32 WAV imports automatically become24-bit
integer WAVs at the same filename/path, with exact originals retained for recovery.
Conversion preserves gain (over-range peaks clip, as selected after audition),
frame timing, metadata and saved BPM/key/grid/cues. The broader Collection
controller checks the common original-CDJ-2000/CDJ-3000 profile, converts eligible
files in place and updates existing native paths automatically when an extension
changes. Four real references retain their original backups and native acceptance;
no manual Relocate step. The installed batch completed124 WAV conversions with
exact originals retained. Timing-unproven, cloud-deferred and unavailable files
remain explicit; see [compatibility limits](docs/cdj-media-compatibility.md).
Device originals stay untouched.
Native registration accepts the separately proven MP3, AAC/ALAC M4A, FLAC and
PCM16/24 WAV/AIFF profiles. Initial native analysis has the narrower accepted
stereo PCM16/24 WAV44.1/48kHz profile. Unsupported or changed sources retain
their intention and original import destination.

**Library.** Reads the live rekordbox database (`master.db`, via a read-only
temp snapshot — rekordbox can stay open) or an XML export as fallback; ↻
Refresh any time. After relocating a file in rekordbox, refresh SoundBox (or
reopen it) to read the new path under the same track ID; SoundBox edits and
playlist membership remain attached to that ID. SoundBox does not watch the
rekordbox database continuously. Smart playlists follow active rekordbox My Tags; removed-tag
history is ignored. Select browser rows and open **My Tags** below the waveform
to assign existing tags in one flat checklist. My Tags sits left of Settings and
Search in the prep strip; Search aligns beneath the Hot Cue / Mem Cue tabs.
The October29 installed detection stage
adds a three-line menu immediately right of Q that controls waveform color and beat-count
display, exports the loaded native song to a USB collection without a playlist,
and starts Analyze Track in the background using Settings → Analysis preferences.
There is no analysis popup or progress bar. Automatic detection is opt-in (off
for new settings); when enabled, folder/file imports appear at the top of
Collection before analysis, and BPM/Key populate one completed song at a time.
Finished songs can be edited while later songs continue analyzing. Automatic
BPM/key detection skips songs longer than15minutes, preserving existing musical
data; explicit analysis remains available.
Unfinished detection resumes after restart with the original job settings;
failed songs wait for an explicit retry. The browser context menu also offers
Analyze Track, which also rebuilds the selected waveform after detection succeeds.
With BPM and Key unchecked, Analyze Track rebuilds only the waveform.
Its default RBXport pipeline follows tempo
changes with attack placement and downbeat selection, using the selected BPM
range. Tracks within the thirty-minute/96M mono-sample bounds are analyzed
completely, with longer tails streamed and the grid continued
at the last measured tempo. Inconclusive results preserve prior musical data for
the same audio. Detected results are saved in SoundBox and
can follow the same audio into a later native track ID. Existing saved grids/cues
are preserved. Automatic initial BPM/grid/key/waveform publication is accepted for bare stereo
16/24-bit integer WAV44.1/48kHz. Other codecs/existing-analysis replacement remain
separate profiles; see [the exact stage](docs/analysis-playlists-next.md) and the
[upstream comparison](docs/rbxport-reference-audit-2026-10.md). Five grid buttons occupy the left, enabled for a ready accepted
track with Analysis Lock off; the BPM field after the first-beat button shows
`000.00` while empty. On an accepted constant grid, type a BPM and press Enter:
the loaded grid changes immediately, saves in the background, and the browser BPM
updates as soon as that save verifies. Captured BPM/Grid analysis on a direct
import can also be corrected through these controls: its manual draft persists
before the actual grid and browser update, preserving the detector's raw result
and cues. Captured imports also retain durable Hot/Memory Cue drafts. Ctime-only cloud
metadata drift captures the gesture durably against its original bytes, then
verifies the original SHA-256 on the existing worker before renewing identity.
Changed bytes remain guarded; unchanged audio needs no reimport or reanalysis.
Captured-import cue/grid intentions remain editable while native work is busy,
with persistence before presentation. Exact prepared requests and source receipts
protect retry/restart. Cues publish through the accepted cue API after native
analysis exists; native writes retain closed-rekordbox/agent and profile guards.
Native playlist create, rename, delete and song membership edits appear as soon
as their intent is safely saved; the shared background writer completes the native
save, while failures offer retry or discard against the current source view. A
failed native-analysis save retains its own song intention while other imported
songs continue saving. Full supported WAV publication preserves every adaptive
beat tempo and endpoint; the headline BPM need not be constant across the grid.
Steady detections now test nearby whole BPM against onsets and attack timing;
genuine fractional/variable grids remain valid. Escape or leaving the field cancels typing. The checklist menu matches the button width and uses two columns, with separated
rows and wrapped availability text. Tags read down the left column, then down the
right, preserving rekordbox order. Mixed assignments become checked
for every selected track; unchecking removes the tag from every selected track.
Saves are automatic, with a verified encrypted backup and conflict checks.
SoundBox refreshes smart memberships after each successful save. Reopen rekordbox
to see the changes; simultaneous writing is blocked. Other libraries remain
read-only until explicitly accepted. On top of the rekordbox mirror, SoundBox keeps its own
plane: **import audio files directly** (drag from Finder onto a SoundBox
playlist or Collection, or use the Import audio files keyboard action). Imported files
are referenced in place — never copied or re-tagged — and become first-class
tracks: container-derived duration (including ALAC/AAC, displayed as H:MM:SS
for hour-plus sets), edits, art, waveforms, export. Direct imports are always
exempt from the rekordbox cue/placeholder filter. New rekordbox tracks and
direct SoundBox imports enter the same background waveform cooker immediately;
lightweight source-identity rechecks recook only a file that changes later.
Reorganized your files on disk? On startup, SoundBox checks only the recorded
import paths, then uses persistent macOS file bookmarks to follow moved files
or whole-folder moves without crawling the filesystem. Legacy records and
cross-volume copies get one batched Spotlight metadata lookup requiring an
exact filename + recorded byte-size match; duplicates and ambiguity fail
closed. Only unresolved files expose right-click → **Fix Missing Links…**,
whose folder picker bounds the fallback search to the location you select.
Cloud-provider timestamp normalization is content-verified against the existing
cache instead of recooking unchanged audio, and on-demand placeholders are not
hydrated by startup checks.

**Sidebar.** One **Playlists** tree contains Collection, native folders/playlists,
and any legacy playlists waiting for their native merge. **Collection** includes
native songs, divider/test entries and direct imports, newest added first. Every
playlist opens newest first; header sorting changes the view only, preserving
native/export order. Up/Down after selecting a playlist browse its tracks.

Playlists and folders use rekordbox directly. **Artists** is a SoundBox-only
view grouping the current Collection. It creates no native folders, per-artist
playlists or background database jobs. Collection and Artists stay fixed at the
top; ordinary native playlists and folders can be reordered. Right-click directly
on a playlist icon for **Star**, **Music note**, or **Vinyl crate**. Only playlist
icons are configurable: Artists stays a person, Collection stays home and folders
keep their expansion icons. Choices are local SoundBox preferences and preserve
the active playlist/track. The old split-library arrangement has a one-time
internal upgrade,
with durable restart recovery. There is no merge command or recurring migration
step. New Playlist/New Folder/Rename/Delete use the accepted native writer;
deleting a playlist preserves Collection and files. Legacy local-only sources
retain their local organization. Folders expand
without replacing the active playlist. **Devices** remains a separate section;
playlist/folder drops use the guarded exporter appropriate to the device.

**USB dashboard.** Tabs are Overview, Playlists, Category, Sort, Column, Color and
My Settings. Edit native browse/sort order, default right column, color titles and
player/mixer settings directly. Global My Settings lives in SoundBox Settings,
with explicit Save to Global / Load Global controls. Existing settings survive
ordinary playlist exports. Overview uses a compact two-column General layout.

rekordbox and SoundBox can export over each other at any time. Existing native
collections and other playlists are preserved. Drops export directly; Update
Playlists updates SoundBox's managed selections without auditing retained songs
or deleting collection media. The USB menu is Update Playlists, Import “My
Settings”, Reformat USB…, Eject. Reformat offers the actual volume name and
FAT32/exFAT, with a destructive confirmation. HFS+ is not currently offered.
Source metadata/cues are captured fresh; unsupported imported/XML analysis fails
explicitly. Song columns match your regular browser; double-click loads actual
USB audio/cues. Device views remember your tab, hierarchy, selection and scroll.
The information button opens Summary and Info, with editable artwork inside Info
and optional metadata columns, hidden until selected. The dialog opens before
artwork resolution; Info uses a compact three-column form that fits at its default
size, with larger centered artwork and a full-width Comments row. Summary uses
a larger cover alongside inline labels and track values. The edit note sits bottom
left. USB Category/Sort panels have visible borders, Color uses dots beside
inputs, and Global My Settings/Keyboard scroll at the right window edge.
Older saved layouts keep
new fields unchecked; many selected fields can scroll without stranding data.
The footer keeps its height when Cancel appears. Supported metadata edits stay in SoundBox and
feed export without changing source audio. See [CDJ limits](docs/cdj-usb-export.md).

**Curation.** Inline Title/Artist edits (double-click or right-click) become
per-track overrides that rename files on the device — sources untouched.
Every override teaches the artist deriver (trusted-name gating for reversed
"Title - Artist" flips, collab-aware splitting, Unknown rescue from titles).
An explicit named transformation credit such as `(WHALES REMIX)` becomes the
effective artist even when the source tag still names the original performer;
generic labels such as `(Radio Edit)` leave that tag alone.
Batch tools: **Suggest artist merges…** (fuzzy spelling dedup with in-dialog
Unmerge), **Suggest artists for Unknowns…**, named **Title Cleanup** rules
(five defaults: Mixdown Label, Copy / Version tags, BPM / Key tags,
Render / File tags, and Drop "(Original Mix)"; plus exact custom words/bracket
tags or validated regex), neat-title edge-artist
stripping. A track's context menu can reveal the selected source directly in
Finder. Analyze Track also rebuilds its waveform; the separate filename-copy,
waveform-rebuild and cover-art URL actions are removed from this menu.
Removal has two explicit scopes: playlist menus offer
both **Remove from Playlist** (only that playlist) and **Remove from Collection**
(global hide, restorable from the dated **Settings → Graveyard** tab); Collection
offers only the latter. Graveyard restore is selection-scoped: select one,
Shift/Cmd-select several, or use Cmd-A, then right-click **Restore**. There is
no unconditional Restore All shortcut. Cmd+A in Graveyard always selects tracks,
even when another action has that binding. Double-click a Graveyard track (or choose
Load Track) to audition it in the deck before restoring; it stays removed and
does not join autoplay. Space, held C and existing cue keys work there. Cue
creation/deletion waits until restoration. A global Graveyard entry immediately
drops its disposable SoundBox waveform record and background preparation skips
it; restoring the track prepares it again when its source remains available.
Playlist-only removals retain analysis because the track can still be browsed
and played elsewhere. Native playlist removal saves to rekordbox; ordinary
restorable Collection hiding leaves its native record and audio untouched.

Graveyard also shows total and selected file sizes using metadata, without
decoding or downloading audio. Its i button follows the highlighted row, including
exact historical identities recovered for native-deleted tracks. **Delete from
Collection and disk…** prepares a selection-specific confirmation, removes the
chosen native Collection entries and memberships, and moves their files to macOS
Trash. Shared files still used by unselected Collection entries are explicitly
kept in that review. Unknown paths remain guarded; entries independently proven
absent from native Collection can use explicit local-only stale-entry dismissal.
Known missing files can still have their Collection entries removed. Saved SoundBox musical/curation data
remains. Disk space is reclaimed when Trash is emptied. This uses a separately
accepted native deletion profile and the shared source worker/recovery journal;
see [the deletion contract](docs/graveyard-delete-plan.md) and [installed status](docs/HANDOFF.md).

**Cover art.** Local images, cache indexes and default-photo changes share one
serialized state owner with backup/restore. Failed publication retains the previous
state and reports the error. The personal provider adds background auto-fetch
with an accuracy-first matcher (an
artist-accountable title match of at least 0.80 → track cover, else artist
avatar; wrong art is treated as worse than no art), resumable and scoped to new
tracks only. `Unknown Artist` is a hard automatic-discovery stop: those tracks
receive no guessed SoundCloud art until the artist is resolved or a human
picks art. Artist-aware
title orientation handles both `ARTIST - TITLE` and `TITLE - ARTIST`, and new
cache entries retain `auto`/`manual` provenance. Manual per-track
picker (image files in both builds; personal also offers search results and URL — an artist profile URL yields
the avatar), an **Artist Pictures catalog** for avatar-wearing artists, and
per-track blocking ("Remove cover art" is sticky). The track menu has a
personal-provider **Set cover art to artist profile** action immediately above
Remove cover art. It uses the effective artist’s profile photo, without falling
back to a track cover; a late request cannot overwrite a newer choice or artist edit.
Settings also provides a
managed **Default photos** pool: imported originals are copied into SoundBox,
normalized, and deterministically randomized with exact-even distribution
across visible tracks whose art is naturally missing or deliberately blocked.
A real cover or artist avatar always wins; the fallback never rewrites the
underlying miss/block. Pool changes rebalance only fallback assignments.
**Settings → General → Review covers…** opens **Suggested**, automatically
checking every existing cover in the active library, including manual and
pre-provenance artwork. A loading wheel appears before the first comparisons,
then progress stays visible while the rest are checked; **All** remains optional browsing. The main actions are **Keep**,
**Replace**, and **Next**. Keep remembers the exact cover/artist/title decision;
Next skips for this session; Replace is individual approval, with originals kept
for recovery. The track title is selectable for copying. **More… → Use artist
profile…** finds an artist picture and previews it before approval. **Exclude
Uploader…** opens one saved block-list manager with add/remove controls; known
automatic seller/dealer blocks are listed separately. Manual URL/file choices
remain under More. Undo is not offered in that menu.
The compact player beside these actions plays the selected local library track,
with pause, click/drag scrubbing and elapsed/duration time. Changing review tracks
or closing the window stops its audition. Keep/Replace/Next continue downward
from the selected position without jumping back to the first suggestion.
Completed checks resume across closing/reopening without repeating searches.
Saved checks, review decisions and prepared comparisons restore before any new
search; progress counts only the remaining work, with saved/reviewed counts shown
separately. Reopening never queues an unchanged completed cover for online search.
Small comparison previews also reuse a separate disposable cache capped at 8 MiB /
128 images; evicting a thumbnail never removes a saved suggestion or decision.
Exact Keep/Replace decisions stay honored until the cover or artist/title changes;
More → Recheck whole library starts a fresh search pass. An empty completed queue
shows a clear result instead of blank comparison panels. Searches never change
artwork. Tracking-link duplicates and equivalent resized/recompressed copies
of the current design are omitted from review suggestions. Strong exact matches and clearly labeled possible
artist-anchored matches are both selectable; automatic fetching remains stricter.
Edition/remix credits identify the version creator, Unicode accents normalize
consistently, and exact short titles from a collaborator's account are supported.
Named editions such as `(So Sus Edition)` also derive **So Sus** as the artist
for imports/existing rows, while manual artist overrides retain priority.
Dubplate seller/dealer/shop/trader patterns and custom excluded uploaders are
never displayed/exported as artwork sources. Existing excluded-source images
are quarantined and replaced with strictly matched **artist-profile art**, not a
guessed public track. Originals remain recoverable under SoundBox's state;
if the profile is unavailable the excluded image stays hidden. Other manual and
unrecorded artwork stays protected. Additional source controls are under More.
The bulk re-scan retries misses and explicitly automatic avatars; it preserves
manual and unrecorded images. In-flight background results cannot overwrite an edit.
Settings orders its tabs **General**, **Analysis**, **My Settings**, **Keyboard**, **Graveyard**, **Connections**,
with tabs centered across one header and Back on the left; Graveyard retains its
full-width page. Menu and dropdown labels start on the left, checks sit on the
right, and dropdown arrows share the player selector’s inset spacing.
[Keyboard settings](docs/KEYBOARD.md) provide searchable,
remappable actions with multiple bindings and explicit conflict moves. Edit a
keycap and choose **Unbind** to remove that shortcut. By default,
1–8 preview a filled slot on the active Hot/Memory page or set an empty slot at
the exact playhead. The grid strip’s **Q** toggle snaps new cue points to the nearest
beat when red (on); grey is off. Its state is saved in SoundBox, independently of
rekordbox’s preference. Existing cue playback stays exact. Command-1–8 deletes; 9 selects Hot Cue and 0 selects Memory
Cue. A remappable **Toggle cue page** action under Cue pages switches between
them; it starts unassigned. Memory numbers reorder chronologically; Hot letters stay fixed. Empty slots
can also be clicked to set. Saves update markers without moving playback or
reloading the library. `=` / `-` zoom in/out. General balances Player/Library/Backup on the
left with Title Cleanup/Cover Art on the right. **Connections** shows rekordbox
on the left: loaded source/status, database location, Refresh, Choose database,
Default location, and Open XML fallback. Database choices persist only after a
successful read-only snapshot and save; failure retains the previous library.
Reopening or refreshing reads saved rekordbox hot/memory cue changes, including
deletions, and checks changed beatgrids. Fresh cues reach an already-loaded player
without reloading audio; grid metadata refreshes in the background without
recooking waveform data.
The personal build adds SoundCloud on the right. **Connect** opens Client ID and
Client Secret and public profile URL fields with **Save**. No browser sign-in or
redirect change is needed; **Edit…** changes saved credentials or profile later.
Credentials and application tokens stay in macOS Keychain. A disposable public
liked-track index is refreshed at launch and with **Refresh likes**; confident
liked matches help prioritize art while general search supplies other candidates. Existing
artwork is not refreshed merely by connecting; exclusions follow the explicit
artist-profile fallback policy above. See
[SoundCloud connection setup](docs/soundcloud-connection.md).

Everything in the cache is normalized to **baseline JPEG** as canonical app state.
The Rockbox exporter
derives 8-bit paletted BMPs per track: baseline 160×160/116×116 fast paths,
exact square sizes discovered from the active WPS/SBS, and a lightweight
dimensionless 160×160 fallback. Exact sizes are retained as themes change. All
are siblings of the audio file. The exporter also
removes embedded pictures only from its verified device-side audio copy, so a
huge source APIC cannot outrank the curated cover or exhaust a classic iPod.
Source audio and canonical curated JPEGs remain byte-identical. In parallel, a low-priority
worker pre-generates content-addressed MA446 RGB565 derivatives for stock iPods.
Stock sync assembles those prepared pixels into `F1028_1.ithmb` (100×100),
`F1029_1.ithmb` (200×200), and a validated `ArtworkDB`; identical curated
images share one pixel payload while every track retains its own database link.
Source audio is never retagged or used as the artwork store.

**Player.** The default native deck uses CoreAudio sample-clock playback while
Metal scrolls the RGB waveform/grid at display refresh (120 Hz on ProMotion).
Three compact header toggles provide playlist-relative autoplay: up, a
no-repeat shuffle bag, or down. The source playlist is frozen when a track is
deliberately started, so browsing elsewhere does not redirect playback.
It includes a whole-song overview, thirteen detail zoom levels from 24–1500
px/s, bar/beat
counting, previous/next, selectable beat/bar jumps, momentary CUE, clean
play/pause/seek ramps, and precise pause-preview-resume scrubbing. Double-click
is the only browser load gesture and preserves playback: a new track plays when
the deck was playing, or loads paused when it was paused. Space only toggles the
loaded deck, and C is momentary CUE. Grabbing the playing waveform pauses it
immediately; releasing without moving resumes from the exact held timestamp.
A native atomic hold uses the frame actually visible under the fixed playhead,
not a lagging UI poll. Complete overview, mode streams and signed detail become
visible together, so a long AAC/ALAC set first appears at its finished size and
color. Manifest/grid/cue readiness remains immediate. Partial caches wait for
repair, and an upgrade performed by that load is saved for the next load without
replacing the waveform already on screen.
A newly loaded track starts on its first analyzed beat. SoundBox V5 owns the
prepared grid/cues, waveform streams, export eligibility, and signed detail.
Complete Rekordbox analysis is reused first, with only a compact 1-ms detail
supplement generated from audio; fallback analysis handles incomplete imports, while cues refresh by fingerprint from
a read-only live-DB snapshot, reconciled with durable pending edit intentions.
If a track was first cached before Rekordbox finished its display analysis,
the recurring scan notices when a valid PWV3/PWV5/PWV7 stream appears for a
mode still using SoundBox's fallback and atomically recooks only that track.
Right-click **Analyze Track** runs the selected BPM/Grid+Key detection and rebuilds
the waveform in the background. It is also the waveform recovery action when both
detection components are unchecked. Both waveform routes
read existing ANLZ through snapshots and preserve source audio. Completed
initial detection can publish native BPM/key/grid/waveforms through the separately
accepted shared-analysis writer; broader replacement profiles remain gated.
Stored hot-cue colors appear as black-outlined A–H boxes on both the
whole-track overview and scrolling waveform; memory cues use one consistent
red upward arrow in both scales.
Waveform colors and three-band shapes come directly from imported rekordbox
analysis when complete. Compact caches store one signed detail base and build
zoom levels in memory, omitting duplicate spectral analysis. Existing caches
remain playable while eligible local tracks upgrade individually in the background;
there is no need to load each song. Interactive reads omit the unused spectral
master while still validating displayed streams. A bounded native transfer lookup
keeps the original geometry/palette exact and reduces upload cost. Offloaded audio
waits until it is local.
Incomplete imports retain SoundBox's generated fallback colors.
Playback/cache preparation preserves source audio and native data; accepted
cue/grid/initial-analysis saves use the separate guarded source worker. Native-deck failure disables
playback with a diagnostic while library management remains available.

**Devices & sync.** New connections appear as **Checking…**, then become usable
with their sync status already shown. Full iPod dashboard loading follows
separately. Drop starts export: threaded copy with one aggregating
progress bar, free-space precheck,
FAT32-safe names (length-budgeted artist/title), catalogue-visible flat
`/Playlists` names for dual iPods, and UTF-8 no-BOM `.m3u8` in exact order with
`# MISSING:` comments. Dual-iPod Rockbox filenames receive zero-padded order
prefixes while SoundBox and stock-firmware titles stay clean. Generic
Rockbox/USB exports may additionally mirror the playlist tree for file-browser use.
A per-device ownership manifest tracks normalized paths, source identity,
hashes, playlist membership, artwork, and completed generations. Playlist
edits/reorders/deletes show as **⚠ Sync needed**; **Sync** resolves
the diff and prunes only explicitly owned paths. On a managed dual iPod,
normal Sync trusts SoundBox's committed inventory and reads changes, without
rescanning unchanged device audio or rehashing unchanged local/cloud sources.
On a schema-3 dual iPod, that same badge also catches an append made in
Rockbox itself. Sync adopts a new song only when its path already maps to
SoundBox-managed media, removes a newly appended duplicate instead of listing
the song twice, and updates both firmware playlist views without copying the
audio again. Unknown device songs are not imported automatically, and
device-side deletion/reorder edits fail closed. **Cancel is honest** — generic
filesystem exports retain playable partial M3U8s flagged for retry; stock/composite
transactions stop at safe boundaries and report rollback or committed state.
Generic export finalization scrubs AppleDouble files in touched directories;
composite normal Sync does not perform a broad clutter sweep. Right-click
a device: Sync, **Verify export…** (references and sizes),
**Deep Verify managed files…** (also checks file hashes), and Eject. Dual iPods
also offer **Repair managed library…** to inspect inventory, restore missing
owned Rockbox files and clean interrupted writes. Existing damaged or unexpected
occupants fail closed; Repair does not silently replace arbitrary corruption.
Generic volumes additionally offer **Erase all files from device…**. Whole-device erase names the exact
volume in a destructive confirmation, removes the SoundBox manifest last, and
normally ejects only after a complete result. Stock-only MA446 iPods have a
separate MP3/WAV two-phase path: read-only database plan, explicit confirmation,
recovery archive, atomic media/artwork copies, and `iTunesDB` committed last.
Its confirmation reports linked tracks, unique images, and artwork bytes. Other
stock models remain read-only. The connected layout chooses the destination
rules automatically: stock-only devices use the conservative database path;
Rockbox-only/generic FAT32 devices use the broader filesystem path; and a FAT32
MA446 containing both layouts enters the composite single-copy workflow below.
Selecting a stock or dual-boot iPod opens Device, Songs, and Playlists tabs backed by a
read-only database snapshot rather than exposing its internal FAT folders. A
confirmed model is remembered per persistent iPod-library/volume identity. On
macOS the Device dashboard also separates Apple-reported firmware and hardware
serial, serial-derived model, model-derived DAC, and SCSI transport revision so
inferred hardware is never presented as a direct probe. The DAC row stays concise,
while its catalog provenance remains in the snapshot. The Playlists tab shows
user-created playlists plus the narrowly deletable Apple **Videos** system
index; the library root and all other built-in smart lists stay protected.
On-The-Go is generated by stock firmware and cannot be deleted through iTunesDB.
Idle eject requests normal macOS eject without Sync, Verify, or Deep Verify.
If a background read is active, eject waits for it to finish. SoundBox reports
macOS refusal and never falls back to force-unmount.
The device browser can copy selected audio/M3U8 entries in order into
`~/Music/SoundBox Media/Unknown Artist/` without changing the device. Click one
for rename, new folder, delete, and assign-artist. Playlist entries that escape
the selected volume through `..` or a symlink are refused. Browser mutations
are pinned to the exact confirmed mount and cannot span devices. A root `/PIONEER` rekordbox/CDJ
export counts toward used space and stays excluded from ordinary stock/dual/
Rockbox/filesystem Sync, ownership, verification, import and cleanup. Generic
USBs use the separate native CDJ contract described above.
Non-FAT32 filesystems are flagged for Rockbox; native generic USB export also supports exFAT.

Dual-boot sync is deliberately single-copy. A complete no-write
plan classifies each selected source: validated MA446-safe MP3 or stereo
PCM16/44.1-kHz WAV lives once under `iPod_Control/Music`, is indexed by iTunesDB,
and is referenced by Rockbox; stock-unsafe but Rockbox-supported media lives
once under `/Music`; unsupported-by-both and missing sources are reported.
Every shared or Rockbox-only payload gets its own sibling
`filename.160x160.bmp`, `filename.116x116.bmp`, and universal `filename.bmp`, so
tracks sharing a folder cannot steal one another's artwork. Those Rockbox-only derivatives use the
theme-native uncompressed 8-bit paletted BMP representation, reducing device
reads by roughly two thirds versus 24-bit BMP while leaving canonical JPEGs and
stock-iPod artwork unchanged.
SoundBox asks before expensive planning begins, archives exact stock recovery,
publishes the
Apple database before Rockbox playlists, and writes one schema-3 ownership
manifest last. A later-phase failure rolls back the committed stock phase,
restores managed Rockbox rewrites, and removes only files created by that plan.
Existing schema-3 audio is not streamed during planning: SoundBox validates its
ownership/path/size baseline, verifies the exact desired Mac-side derivative,
then replaces one file at a time with a transient same-volume rollback rename.
Old managed-audio bytes are discarded after success; unowned paths are untouched.
If USB remounts during planning, confirmation, or a generation, the pinned
identity check refuses the stale plan and the last committed library remains authoritative.
Re-queueing the playlist verifies and adopts only exact completed leftovers;
Mac-side proof journaling makes that verification resumable too. Additional
playlist drops made while a dual-iPod sync is running are queued and deduplicated,
then planned against the newly committed media instead of recopied.
Sync, playlist export, and managed-playlist deletion all follow the same visible
contract: immediate Yes/No, background Planning, guarded device writes, then one
completion. Composite planning occupies 0–30% of the same progress bar, guarded device work
occupies 30–80%, and required dashboard/sidebar/drift finalization owns 80–100%.
Planning shows animated `Planning iPod...` and percentage only; its internal
full-library safety units are deliberately hidden because they are not files
being changed. `Syncing audio` alone shows the honest `#/#` mutation counter.
The bar never disappears or jumps backward between phases or queued playlist
jobs, and 100% means every SoundBox worker has released the target and Eject is
actually available.
The first generation requires a device without an existing SoundBox manifest;
later playlist drops accumulate safely, and an identical repeat is zero-write.
Thereafter the normal Sync action uses the same stable playlist identities and
composite planner to reconcile rekordbox renames, moves, order, membership, and
removal across both firmware views. Effective title/artist is also part of that
baseline, so a metadata edit marks the device Sync needed and repairs both the
stock database and Rockbox-visible payload metadata. Removed playlist files are recovery-backed;
media and artwork are removed only when no remaining managed playlist references
them. Managed audio requires an ownership/path/size baseline, while art and
playlists retain live hash checks. Orphans are renamed into transient same-volume
recovery and restored exactly on failure; the temporary bytes are discarded
only after the schema-3 manifest publishes successfully. Cleanup therefore does
not depend on regenerating an older cover with the current multiview profile.
Schema-2 migration remains disabled and fails closed.

Sync never changes Rockbox settings. Database auto-update, playlist capacity,
playlist-viewer display and all other preferences remain yours, and changing them
alone never creates Sync-needed drift. SoundBox reads the active theme to size
exported artwork and keeps catalogue order through numbered playlist filenames.
Rockbox remains the sole writer of its optional `database_*.tcd` cache; database
users manage updates in Rockbox. Exported playlists do not require that database.

On a schema-3 dual device, **Songs** and **Playlists** are unified views rather
than stock-only tabs. Their Type column reports `Both`, `Stock`, or `Rockbox`
from the validated ownership manifest and parsed iTunesDB. Playlist expansion
keeps the verified M3U8 order and labels every child track the same way. Apple
smart indexes remain protected internal state and are not presented as managed
dual-library metrics or playlists. `Syncing audio...` remains animated while a
separate `#/#` counts
audio files actually copied, replaced, or removed; commits with no audio work
hide that counter. Successful sync/deletion refreshes the open Device, Songs,
and Playlists snapshot before the sidebar free-space/drift scan, so navigating
away and back is never required. The status label reserves only the active
animation width, keeping the count beside it; static messages resize to fit.
Per-file acquisition/copy/verification phases and byte totals appear on hover,
so small files cannot flicker through phases or replace the overall track count.
Analysis/artwork use the same steady-label/separate-counter presentation and
yield the status to device work. Low-space analysis pauses explicitly name disk
space; hover shows the automatic resume threshold (25 GiB for full analysis).
Approval is intentionally concise
and precedes the expensive exact plan; completion reports the result without
exposing the transaction's internal checklist.
Before that first composite generation, deleting old stock playlists removes
their now-unreferenced iTunesDB records and linked device media after the guarded
database commit. Source-library audio and unrelated Rockbox paths stay untouched.

**Persistence.** Everything user-made lives in one portable
`library.json` (overrides, playlists, imported records, hides, settings) at
`~/Library/Application Support/LowFreqLabs/Soundbox/` — keyed by stable
track IDs so a newer rekordbox library preserves all work. Atomic saves,
rotating backups, schema-validated corruption recovery, and full backup/restore
with staged publication, exact rollback and interrupted-operation recovery
`.zip` (library + history + art cache) in Settings. Window size/position,
columns, and sidebar pane proportions reopen exactly from the last clean close.

## The safety contract (non-negotiable)

- Library reads use a verified read-only `master.db`/WAL snapshot; XML is a
  fallback. Narrow live-write APIs change existing My Tag assignments and
  Hot/Memory cues for an
  explicitly accepted library/schema/version, with rekordbox/agent closed, a
  verified encrypted backup, guarded transactions and independent read-back.
  The accepted grid API changes only DAT/PQTZ, native EXT/PQT2 invalidation and
  the narrow BPM metadata/counter delta. Other analysis sections and metadata
  remain protected. Grid previews update immediately while the same shared
  worker saves and verifies in background. Ordinary registration, playlist CRUD,
  initial supported WAV analysis, confirmed removal and conversion locator updates
  each require their own independently accepted source authority and recovery.
  See [the prep plan](docs/rekordbox-prep.md) and [current handoff](docs/HANDOFF.md).
- Ordinary playback/export leaves source audio unchanged. Only the explicitly
  authorized recoverable compatibility converters and user-confirmed Graveyard
  Delete may replace/move desktop originals, with exact recovery and separate
  source authorities. No general source cleanup or permanent deletion is granted.
- Every export write lands **under an explicit export target** (the device), guarded
  so a target can never overlap the source library.
- Writes below `iPod_Control` go only through the guarded MA446 stock adapter.
  On a dual layout, only the composite planner may coordinate that adapter with
  Rockbox media/playlists and schema-3 ownership; independent exporters are
  forbidden.
- Full contract with per-write commentary: top of `soundbox/sync.py`.

## Layout

    soundbox/   pure-Python engine (no Qt): rekordbox readers, data model,
                sync/resync + manifest drift, playlists (m3u8), metadata
                derivation, imported-file records + relink search, cover-art
                fetch/encode, backup — mypy-clean.
    desktop/    PySide6 app: MainWindow + mixins (settings, device browser),
                workers (all QThreads), trees, player, art picker, suggesters,
                artist catalog.
    native/     arm64 macOS CoreAudio decode/render deck + Metal waveform/grid.
    tests/      engine, property-based, live-DB mocks, hermetic UI, native deck,
                and Metal shader contracts; trust ./check.sh for the live count.

## Development

    ./check.sh            # lint/build/types + all offscreen/Cocoa tests; zero skips
    ./check.sh --render   # + offscreen UI render to /tmp
    ./check.sh --native   # compatibility alias; native tests always run
    ./build_native.sh     # CoreAudio/Metal dylib only
    ./build_app.sh        # PyInstaller → dist/personal/Soundbox.app (self-signed)

Python 3.14, deps in `requirements.txt` (venv at `.venv/`). Logs at
`~/Library/Logs/Soundbox/soundbox.log`. Player accelerators live under AppData:
`track_analysis/` contains the canonical player-analysis format: atomic,
checksummed V5 per-track records; V4 is read-only migration input. Derived
analysis and the content-addressed `stock_artwork/` RGB565 cache are rebuildable,
but `library.json` and the curated `art/` cache are
not; cache maintenance must never touch it, Rekordbox, or source audio.
