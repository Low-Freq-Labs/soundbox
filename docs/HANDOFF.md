# SoundBox current handoff

October29, 2026. Branch main. This checkpoint includes the preceding uncommitted
USB/export/settings/player work plus the completed UI/Info refinements. Sonny
requested wrapping this session and continuing the larger additions in a fresh
session. The release commit containing this handoff is the repository checkpoint;
the installed binary's actual build base is recorded separately below.

## Installed release and validation

Personal1.0.0 build138, built from base HEAD
`2c761726b6c10e703a9466c444fbb3a3a96c8d2e`, dirty=true. Installed source fingerprint:
`349e6cc41055966070c8e2391593eb4a553fe5d28551981e9c2aba4c3613b0e7`.
Installed `2026-10-29T04:25:17.766440-05:00`;
launch requested `2026-10-29T04:25:17.767002-05:00`.
Runtime source matches this fingerprint. Subsequent documentation/checkpoint
commits do not relabel the binary as a newer Git build.

Bare `./check.sh --render` passed:1586 Python tests (1528 offscreen+58 Cocoa),
zero skips/xfails, Rust3, pinned243-file vendor verification, pyflakes, mypy68
files and hermetic render. Final targeted Info tests also passed after centering
its summary group. Signed personal composition and isolated packaged launch
passed. Installed native __text/full embedded Metal and release CDJ __text/schema
match source/build artifacts; both code signatures verify.
Native __text `7c5f2eff8b491ab1fa96490b704a7069a1ad8cf64a9adda8b0d3916c576e4f0b`;
CDJ __text `b8b89c99b00e655ca7cd258a1fd96cd279c866e515dabf404a556b90bfca1855`.
The new checkpoint does not alter the accepted native waveform binary.

Evidence: `~/Library/Logs/Soundbox/playlist-analysis-ui-2026-10-29/release/`.
It contains gate/build/install logs, test inventories, checked source, isolated
runtime report, six inspected UI renders, actual installed desktop screenshot,
installation/launch records and exact prequit/postquit/postlaunch/postvisual state
backups. The actual installed browser is populated with the saved seven columns.
Startup:3740 tracks/77 playlists, Collection3228;3237 analysis reused,0 preparation,
3106 stock artwork reused,0 fetched. The extra source playlist is the native test
playlist below, not an automatic SoundBox playlist.

## Curated state and rollback

Prequit `2026-10-29T04:25:15.479169-05:00` SHA `b250596aabdd231d355f97dca87f7cc52389f8a09d42e90a12b1092dda301bbd`;
postquit `2026-10-29T04:25:16.575790-05:00` SHA `d23114b0ce96eec114b12bc625e0dd28c983e42a8ad33f96d135d222300769e2`;
postlaunch `2026-10-29T04:25:59.094013-05:00` SHA `d23114b0ce96eec114b12bc625e0dd28c983e42a8ad33f96d135d222300769e2`.
Curation SHA `efb9c44c927b20bf044d70c40e7a8f817b4273861b73efc7de62c8a4340efe64`.
All4041 art/review files,3 authority files and0 symlinks preserved. Analysis records
remain3280. Counts: custom10/import84/artists489/titles1102/hidden517/removal groups1/
additions0/aliases24/settings25. Quit changed only columns_keys, columns_state and
sidebar_split; the installation launch changed no canonical state.
columns_keys is the new explicit saved column schema, not curation loss.

A later live interaction added exactly title override117829415 =
Zeds Dead - Rudeboy (Ruudy Flip), plus columns_state. It is preserved, not rolled
back. Final observed baseline `2026-10-29T04:27:33.264924-05:00` has title overrides1103,
library SHA `92b2034be6fa7f903f6c7d5704d1ca4b3b4c48036f95439c5ced02fbf7c4c744` and curation SHA
`de29a5330cb48ecfeed5e8e3aefd4b7fbd68509966d7e8faf1c60dab525d4d68`. All other library data and all artwork/authorities/
analysis counts match the pristine postlaunch baseline. Exact comparison is
state-comparison-postvisual.json. Keep both baselines; never describe this later
curation edit as installation drift or restore an older title dictionary.

Immediate prior installed build0c7b519 remains at:
`/var/folders/rf/fhvnltd519d1jf6kj6jxj8yh0000gn/T/soundbox-info-layout-rollback-f6ow8jiw/pre-info-layout-Soundbox.app`.
The older c7d454 rollback was signature/provenance checked and retired; see
retired-obsolete-bundles.json. Dist/personal retains the matching artifact pending
Sonny's visual acceptance. Retire that duplicate and immediate rollback after
acceptance. Never remove source references, recovery receipts or AppData as cleanup.

## Completed runtime behavior

USB tabs: Overview, Playlists, Category, Sort, Column, Color, My Settings.44
player/mixer fields, explicit global Save to Global / Load Global, unknown-byte
preservation. Category/Sort have contrasting panels/visible borders; Color uses
dots directly beside inputs. Global My Settings and Keyboard scrollbars reach
the right window edge.

Info opens before artwork lookup/decode; TaskOwner resolves/decodes a bounded
QImage in a worker after show. Info fields build lazily on selection. No extra
work is added to track loading. Lyricist is omitted from Info; Comments spans the
row with a visible border. Artwork/button and Summary art/title group are centered;
selectable technical details are anchored in a bordered panel; the local-edit
note sits bottom left opposite Close. Supported metadata edits remain local and
feed export. No new source write domain has been enabled.

Old Qt header states explicitly hide fields absent from their saved schema;
optional metadata stays unchecked until selected. Deliberately selecting more
fields than fit permits horizontal scrolling without a blank browser. FooterBar
stays28 points with progress/count/Cancel visible. Backtick cue-page mapping and
all other saved keybindings remain intact.

The dedicated generic FAT32/exFAT CDJ exporter prepares both library formats from
fresh source snapshots. Drops publish directly through one serialized device lane.
rekordbox and SoundBox are equal USB exporters: use current small native catalogs,
preserve other playlists and collection songs; no normal orphan sweep or retained
media audit. New audio checksums stream during copy. Coherent later native exports
supersede older interrupted catalog work without rolling back over rekordbox.
Display reads stable local DB/WAL snapshots, never live USB SQL/SHM. Publication
still rejects active/unreconciled SQLite companions. Reformat alone requires
concrete destructive confirmation; no agent hardware-format test occurred.

## Next session — explicitly unfinished

Read [the full accepted scope](analysis-playlists-next.md). Remaining: sandwich
menu immediately right of My Tags (waveform color, three beat counts, standalone
song USB export, Analyze Track); Analysis settings tab and functional BPM/Grid+
Key analysis; ordinary native rekordbox playlist create/add/remove/delete. Auto
analysis off; Phrase disabled placeholder; no cloud/mode-selector clutter.
Use the pinned [rbxport](https://github.com/chrisle/rbxport) engine/reference.

Private root: `~/Library/Logs/Soundbox/playlist-analysis-ui-2026-10-29/`.
ui-references/ retains all22 supplied screenshots. unfinished-prototypes/ retains
an apply-checked resume.patch and exact source copies for the Rust analysis helper,
standalone planner input and native/Python beat-count prototype. These were removed
from the release surface at Sonny's session-checkpoint request; they are untested,
unwired and not shipped. Review/apply selectively, never overwrite newer files
wholesale. Known tuple-order/decoded-budget/countdown/test work is documented there.
No source analysis or playlist authority was provisioned.

Native playlist reference snapshots (encrypted DB, full semantic hash inventory,
selected rows and XML): native-before, native-created, native-added, native-removed.
Native changes were only djmdPlaylist/agentRegistry for create and
 djmdSongPlaylist/agentRegistry for add/remove; schema/metadata stayed identical.
The native surviving membership renumbered2→1; the removed membership disappeared
physically. rbxport's tombstone removal is not yet proven equivalent.

Temporary source playlist still exists: SoundBox Playlist Test,
ID4086406462, UUID4739cf09-58a3-4081-8280-a1695ded60c3. Sonny created it, added
STOP CAPPIN228450310 then Gangster Tune18213989, then removed STOP CAPPIN. Only
Gangster Tune remains. Last pending request: Sonny deletes ONLY this temporary
playlist in rekordbox, keeps both songs in Collection, quits normally and replies
done. That deletion sample has not been captured. If a delayed done arrives,
use the preserved capture_soundbox_playlist_ref.py as native-deleted before doing
anything else; first check fresh source state. Never drive rekordbox UI remotely.
Then compare native create/add/remove/delete protocols/XML/counters against copied
rbxport output before designing an exact accepted source publisher in the shared
Tag/Cue/Grid lane. Intelligent playlists remain a separate gate.

## Retained authority, device and performance boundaries

Source authority: rekordbox6.8.7.0303, DBVersion6000, library629922790,
schema c3255e959e179e46cd15e6ae2da50543427c03c8399b11b7a6809515f06e8c08.
STOP CAPPIN228450310/a191ffb9-1cf5-4166-b61b-97cb453a311b remains Sonny's native
138.00 edit and exact128750ms endpoint; DAT SHA
715850b961c1ae5caebc941bd390a9c87699ec51401d3521f168e7832312a872.
No agent source/audio/analysis/USB write or restore occurred in this follow-up.
Closed-app, exact identity/authority, Analysis Lock and shared recovery guards stay
mandatory. Never read/change rekordbox global Quantize. Existing source Key/detected
analysis writes are not authorized by the accepted manual-grid API.

Preserve native-grid-pilot-20261027, native-grid-initial-state-pilot-20261027,
native-grid-historical-pilot-20261027 and cue/tag baselines in AppData recovery;
generation58 recovery20260926-152340-47477cfa4a49; offline iPod20260926-163945;
frozen waveform/CDJ fixtures. Historical No Trust primary-only restore grant does
not authorize another track or secondary cache (original DAT f82e11d7a7f0d4a8a1150ae28bfcc4168602f187064ddebd2b7822fb32b9db71).

Accepted Gunslinger RGB576/864 logical-point motion/footprint/occupancy and both
faint visibility lifts remain. Mini RGB uses its faint-only encoded-space blend;
BLUE/3BAND controls and source/cache data are unchanged. Do not claim perfect
cadence or wider-zoom color equivalence. Physical iPod acceptance, exact CDJ
firmware/full matrix, newer OneLibrary hardware, wider media/loop/credit coverage,
large-library efficiency/recovery retention and redistribution remain open.

RB USB TEST UUID5B29E617-ACFB-32C2-B580-077A6BF02C64, native name yerbski,
current tempo±10. Do not restore historical±16. Earlier44-field restored-profile
and whole-file parity receipts remain under usb-settings-2026-10-29/ and
usb-preferences-2026-10-29/release-final/. All five native sample files were
restored byte for byte by Sonny; no more My Settings samples are currently needed.
