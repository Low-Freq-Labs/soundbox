# SoundBox current handoff

October29, 2026. Branch main; this document's commit contains the local folder
and layout follow-up to bf6f412. The menu/local detection stage is installed and
validated. **Two-way source analysis publication and ordinary native playlist
CRUD remain unfinished.** This release does not complete that round trip.

## Installed app and validation

Personal1.0.0 **build141** is running from `/Applications/Soundbox.app`.
Actual build basebf6f4123a750b526e33f2f910e24cb395c51ed3b, dirty=true;
source fingerprint
`d460893babc7fd14cdf9040d61c11a0d14cf00f99133b14c50365ded3b1eabc4`.
Documentation changes after packaging do not change that binary provenance.

Full bare `./check.sh --render` passed: native/Rust builds,243 pinned vendor
files, Rust3, pyflakes, mypy71 files and **1614 tests** (1555 offscreen plus59
Cocoa), zero skips/xfails; both inventories reconciled. Offscreen ran485.195s,
Cocoa62.235s. The earlier locked-session failure is historical.

Signature/personal composition and fresh packaged isolated launch passed:
native=true,0 devices/no library/no art worker. Settings/menu/dialog renders were
inspected on offscreen and Cocoa, including both open Analysis popups and the
folder context menu. Info's default-size render asserts no vertical scrolling.
A real installed-window capture verifies the curated library and sidebar.
Native ABI6, complete embedded Metal and renderer
schema match source; both Mach-O text hashes match the checked local build.
Both the prior app and build141 quit normally;141 was relaunched and reverified.
Final startup:3740 tracks/76 playlists, Collection3228;3237 analysis reused,
0 preparation tasks;3106 stock art reused/0 prepared,0 new art-fill tracks.
Native test playlist deletion explains the playlist-count change. New reference
tracks have no source playlist membership and do not enter this playlist-reached
library view. No source/USB write was made by the agent.

Evidence:
`~/Library/Logs/Soundbox/layout-folders-2026-10-29/`.
Gate log: check.log. Release records include
build/install/smoke logs, test reports, actual provenance, screenshots, library
backups and exact state comparisons. Harnesses use frozen offline fixtures;
render and isolated launch suppress real source/device discovery.

## Final curated-state baseline

At `2026-10-29T18:46:06.260848-05:00`, after141's normal quit/relaunch:
library SHA `cb99ec178b8fa36acf183ed805e3a9b46e93decceab29b1644c34a38d9aa6152`;
curation SHA `de29a5330cb48ecfeed5e8e3aefd4b7fbd68509966d7e8faf1c60dab525d4d68`.
All4041 art/review files,3 authorities and3280 analysis records are preserved.
Counts: custom10/import84/artists489/titles1103/hidden517/removal groups1/
additions0/aliases24/settings26. Title117829415 remains
`Zeds Dead - Rudeboy (Ruudy Flip)`; never restore the older1102-title dictionary.
Old app quit serialized columns_state; first141 launch changed no saved bytes;
141 quit serialized columns_state; final relaunch
changed no saved bytes. Curation is identical throughout. Exact stages are in
state-timeline.json and state-verification-postvisual.json.

Retain current rollback pending Sonny's acceptance:
`/var/folders/rf/fhvnltd519d1jf6kj6jxj8yh0000gn/T/soundbox-analysis-rollback-flpz99ko/Soundbox-build140.app`.
Build138 remains beside it in that same rollback directory. The preexisting
retained rollback also remains:
`/var/folders/rf/fhvnltd519d1jf6kj6jxj8yh0000gn/T/soundbox-info-layout-rollback-f6ow8jiw/pre-info-layout-Soundbox.app`.
These are not source recovery backups; retain pending Sonny's acceptance of141.

## Implemented stage and unfinished work

- SoundBox folders nest, expand/collapse persistently and accept playlist/folder
  drags, including back to root. Context order is New Playlist, New Folder,
  Rename, Delete; Import Audio Files is removed. Membership stays in custom;
  hierarchy stays in validated settings. Folder moves retain cust:name device
  identity and exact song order; folder drags export descendants. Confirmed folder
  deletion removes its playlists while preserving Collection/audio. Legacy playlist
  names remain globally unique. No live user playlists were reorganized for testing.
- Analysis controls now align in one compact form and Cocoa popups fit all labels.
  Info uses smaller centered artwork, a three-column form and full-width Comments;
  all fields fit without scrolling at the default800×760 size.
- Menu right of Q: waveform color, three RAM-only beat-count modes, standalone
  loaded native song → USB collection without a synthetic playlist, Analyze Track.
  Mount/UUID binds deferred intent; compiled disposable-target tests cover
  initial/incremental/no-op and interrupted standalone recovery.
- Analysis tab after General: bounded cancellable rbxport BPM/Grid+Key jobs,
  ten BPM ranges and child scheduling priorities. Automatic SoundBox imports and
  first-beat cue are opt-in/initially off; Phrase disabled. Results persist in
  backed-up library.json with path/full audio hash/exact filesystem identity.
  Native/manual grids and source cues win. Shared64MiB publication guard preserves
  old bytes on refusal. Detected results do not yet publish into rekordbox/native
  CDJ data. This temporary stage is not an author-based ownership policy.
- Sonny requires shared analysis regardless of app/import order. Next work is
  the **copy-only** native registration/publication/recovery proof, then narrowly
  accepted source authority and actual SoundBox-first/rekordbox-first round trips.
  Prove duration/endpoint, gain/peak, sidecar/contentFile/contentCue, counters/flags
  and existing-data preservation. Manual-grid authority cannot authorize these writes.
- Ordinary source playlist append/remove has a pure unsynced planner and exact
  encrypted-copy replay, without a live publisher. Native deletion is captured;
  creation/name, deletion counters, XML and interrupted recovery still need proof.
  Synced/cloud and intelligent playlists remain separate gates.

## Native references saved — normal preferences deferred

Sonny completed deletion, unanalysed import, BPM/Grid+Key, Key-only recheck,
first-beat cue and fresh Key-only import+analysis. Captures and sidecars:
`~/Library/Logs/Soundbox/playlist-analysis-ui-2026-10-29/`.
Full findings and exact capture names:
[analysis-playlists-next.md](analysis-playlists-next.md).

Track102012532/a8aad14e-d39a-486d-aa6f-05dc9a4905a6 first registered unanalysed;
full analysis matches SoundBox:120 BPM,8B, first beat249ms,64 beats to31749ms.
Key-only recheck preserves all analysis files exactly. The first-beat option adds
one DB-only249ms Memory Cue with unchanged sidecars. Fresh track109556371/
2bd69678-b386-454f-a4d0-a7c4c208830c was imported and analyzed in one capture
interval. Sonny confirms Key-only selection; it still creates BPM/grid/waveforms
and gain/peak. All sidecar sections match the full fixture except the filename
path. Combined session counter deltas are not isolated write formulas. Native
Length31 for exact32-second PCM remains a proof item. Audio hashes are unchanged.
SoundBox-first reference.wav remains unimported for reverse-import acceptance.
Sonny operates all rekordbox UI; never drive it remotely or read global Quantize.
Sonny is busy and will restore native Auto Analysis=Enable and BPM/Grid+Key later
after the temporary reference settings. No further reference action is requested.
For new songs now, native import into ALL LOCAL then SoundBox refresh is supported;
SoundBox import/playlist editing still does not register tracks in rekordbox.

## Protected source/device boundaries

Authority remains rekordbox6.8.7.0303, DBVersion6000, library629922790, schema
c3255e959e179e46cd15e6ae2da50543427c03c8399b11b7a6809515f06e8c08.
STOP CAPPIN228450310/a191ffb9-1cf5-4166-b61b-97cb453a311b retains native138.00,
exact128750ms endpoint, DAT SHA
715850b961c1ae5caebc941bd390a9c87699ec51401d3521f168e7832312a872.
No detected-analysis/Key/playlist authority was added. Tag/Cue/Grid closed checks,
exact shared recovery and Analysis Lock remain mandatory.

Preserve native-grid-pilot-20261027, native-grid-initial-state-pilot-20261027,
native-grid-historical-pilot-20261027, cue/tag references and generation58
recovery20260926-152340-47477cfa4a49; offline iPod20260926-163945 and frozen
waveform/CDJ fixtures. No Trust's historical primary-only restore grant never
authorizes another track/secondary cache. Physical iPod, wider CDJ hardware,
OneLibrary, recovery retention and distribution gates remain open.
RB USB TEST UUID5B29E617-ACFB-32C2-B580-077A6BF02C64 remains native yerbski,
tempo±10; never restore historical±16. Earlier settings restoration receipts stay.
