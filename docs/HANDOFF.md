# SoundBox current handoff

Updated October 27, 2026, America/Chicago. Sonny accepted this build and is playing
with it. Wrap-up publishes the completed prep/player/appearance work on `main`.

## Installed app and source provenance

Installed personal **1.0.0 build 131**, signed and running; no reinstall or restart
was needed for this documentation/cleanup wrap-up. The app remains the accepted
binary, with embedded base revision `87687337f9b4ad526e7a9af1e14d9889546721fb`,
dirty=true, and runtime-source fingerprint:
`b276d6a9aa002877767759c8589b522b70f4c33fd3fb700595843cfa904e62d0`.
Repository runtime source release: `fee2b394fd467892c63153dff7b87d79d53960fd` on `main`; this handoff is
a documentation-only follow-up. That release has the same runtime fingerprint.
Do not restamp the older binary with the repository commit; resolve current HEAD
with Git.
Installed **2026-10-27T19:31:57.205780-05:00**, launched
**2026-10-27T19:31:57.206243-05:00**. Stable signature, personal composition,
packaged field instructions, isolated native launch and real window verified.
PID 26286/window 15181 were observed; resolve fresh IDs for later inspection.

## Completed and accepted

- Existing My Tags edit selected native tracks in a flat, button-width two-column
  menu, ordered down the left column then down the right. Native acceptance passed.
- Empty Hot/Memory slots create immediately usable session cues; populated slots
  preview exactly. Command-1–8 deletes. A shared FIFO saves verified native rows
  without reloading audio/library. Normal quit drains accepted intents; an abrupt
  exit can lose not-yet-started RAM intents. Unknown recovery blocks all domains.
- First grid clicks work during the initial source probe. Captured track/UUID/path
  and loaded RAM rows preview immediately; source checks still precede saving.
  Set bar start preserves earlier bars; shifts, double/half and manual constant
  BPM share the source lane. Verified saves update browser BPM immediately and
  reject stale row-cache results. Variable-tempo scaling remains blocked.
- BPM entry uses Enter to submit, Escape/focus loss to cancel. Track changes
  discard unfinished input; status refreshes preserve typing. Empty deck `000.00`.
- Q is independently persistent in SoundBox: red on/grey off, semibold lettering.
  New cues snap to current RAM beats when on; existing triggers stay exact. Never
  read/change rekordbox's global Quantize preference. Grid/Q shortcuts are bindable.
- Wordmark remains 270×55; General provides accent and White/Black/Custom selection
  text. Title/grid/waveform edges align; cue panel spans header/deck; My Tags,
  Settings/Search share the prep strip. Transport gaps match at 5px. The layout
  selector has rounded ends, inset arrow and 11px text inset. Additional layouts
  are disabled placeholders. Sonny confirmed manual BPM and final layout work.
- Cue/scroll responsiveness passed live review. Transport edges no longer scan
  art or discover devices. Verified encrypted backups avoid offline inventories;
  bounded library-cache encoding avoids a full-library GIL hold. The requested
  active-save sampler missed the edits; no live profile is claimed.

## Final verification and preserved state

Wrap-up bare `./check.sh --render` passed **1,433 tests**: 1,377 offscreen plus
56 Cocoa, zero skips/expected failures, complete inventories reconciled. Relevant
Cocoa controls/popups/editor, geometry, packaged smoke and actual runtime renders
were inspected. An earlier padding-release run had one 110.924-FPS cadence miss
against 114 minimum; its full strict rerun and the final full wrap-up run passed
without threshold/code changes. Cause remains unestablished; no cadence fix claimed.
Real startup: 3,740 tracks/75 playlists, zero preparation tasks, zero artwork
fetched, 3,106 stock-art derivatives reused. No new physical-device acceptance.

Installation evidence: `~/Library/Logs/Soundbox/layout-text-2026-10-27/`.
Prequit SHA `e9e042fb8308aaeb7ac7358d4a39419a2acbe04e692dae1be98d517400ca35ec`;
quit changed only settings/columns_state. Postquit/postlaunch/post-startup SHA:
`f6cd3f1dbae016243d091f70a11e8e9b4d68194fe4b3a9ecd254d82fa5ad0a6d`.
That final startup snapshot was 2026-10-27T19:32:35.120413-05:00. Source DB/WAL,
ten sampled DAT/EXT/2EX sets and all authorities stayed exact across that install.

Wrap-up read-only snapshot at **2026-10-27T19:42:58.016353-05:00** has SHA:
`276fed975e79d4a5bc2500e2221e014da5e1aef4d4d4fec6389232bf16f76527`.
Since installation, only Sonny's settings changed: Q false→true and zoom
92→1152. Curation and all 4,040 art/review hashes remain exact; 3,277 analysis files.
Counts: 10 custom playlists, 84 imports, 489 artist overrides, 1,101 title edits,
517 hidden tracks, 1 removal group, 0 additions, 24 aliases, 22 settings. This is
an as-of disk snapshot while the accepted app stays open; later prep may supersede it.

## Cleanup and evidence

Wrap-up retired the exact prior rollback app, duplicate dist app/collection and
build/personal intermediates reversibly to Trash. The empty rollback directory
was removed. Keep only the installed app and active checkout; retain build/native,
build/check and required fixtures. AppData/source/device recovery were untouched.
Exact cleanup destinations, final gate reports/render, state comparison and Git
publication receipts: `~/Library/Logs/Soundbox/wrap-2026-10-27/`.
Earlier retirement receipts remain in layout-selector/layout-text evidence roots.
Trash is not retained recovery. No superseded app rollback is required after
Sonny's acceptance. Manual-BPM native/copy/source proofs and install evidence
remain in `~/Library/Logs/Soundbox/manual-bpm-2026-10-27/` and protected AppData.

## Manual BPM acceptance and source safety

Native No Trust captures90→91→90 supplement prior90→90.03 and45.01→90 references.
All four complete DAT/EXT replays match exactly. Native manual entry rebuilds the
existing row count, then extrapolates from its rounded tail; one unbroken formula
is wrong by milliseconds. Constant grids only. Native timestamps for file/track
may differ because it defers persistence; SoundBox uses one transaction timestamp.

Four encrypted private source-layout replays and full OfflineGridWorkspace
inventories passed. Actual-source writer90→91→90 verified complete SQL inventory:
only the target track, primary contentFile and local counter changed. Whole DAT/
EXT matched native controls; audio and2EX were hash-preserved. Sonny confirmed
91.00 and normal grid loading in rekordbox, then closed it. Reverse90.00 was
verified against the native capture. The original primary DAT was restored under
the existing narrow grant; no native counters/history or file metadata were
rolled back. That historical restore can leave native file hashes describing its
prior native-saved grid, as before. Do not broaden its scope.

Source `~/Library/Pioneer/rekordbox/master.db`; exact authority is rekordbox
6.8.7.0303, DBVersion6000, library629922790, schema
`c3255e959e179e46cd15e6ae2da50543427c03c8399b11b7a6809515f06e8c08`.
No Cloud/Mobile Library Sync. Closed rekordbox/agent, Analysis Lock, UUID/path,
regular owner-controlled files, backups and shared Tag/Cue/Grid recovery remain
mandatory. Manual metadata rejects unknown sync states; variable tempo is blocked.
Missing primary file registration blocks manual entry, preserving original grid
operations. Audio/XML/2EX and unrelated ANLZ sections stay untouched.

Protected AppData roots: rekordbox_recovery/native-grid-pilot-20261027,
native-grid-initial-state-pilot-20261027, native-grid-historical-pilot-20261027,
earlier cue/tag baselines, generation58 recovery20260926-152340-47477cfa4a49.
Preserve offline iPod20260926-163945, waveform/CDJ fixtures and curated artwork.
No Trust original DAT hash:
`f82e11d7a7f0d4a8a1150ae28bfcc4168602f187064ddebd2b7822fb32b9db71`.
Incomplete*.pending captures are not baselines. Never restore older tests over
Sonny's subsequent prep. Native Quantize is global/user-owned: never read/write it.
SoundBox Q is independent, persistent and snaps only newly captured cue positions.

## Remaining product work

Next approved milestone: single-tag source intelligent-playlist creation,
then the default-off full-Collection setting. Variable-tempo scaling remains
deferred. MiniMeters was cancelled. Playlist-only ALL LOCAL remains default.
Physical iPod acceptance awaits its return; CDJ export is later and device/PIONEER
remains excluded. No new physical-device or intelligent-playlist acceptance.
