# SoundBox current handoff

October30, 2026. Branch main. Alignment, choice controls, Graveyard audition and
wide waveform follow-up are the current release. Source playlist pilot remains
NOT approved/published; general native playlist writes and two-way source
registration/analysis remain unfinished. No new production authority is enabled.

## Runtime release

Personal 1.0.0 **build 146** is installed at `/Applications/Soundbox.app`.
Actual build base `4f8f6c119081cf63dcc28e4aee7950650eb77e89`, dirty=true;
signed source fingerprint `98e008d31f85ff2b7571b1c4a3ebebe67be2bdd6ce90539e9cbb55360c939d2a`.
The final Git main revision/publication receipt is in completion.json under the
release evidence root below; later documentation does not restamp this binary.

Final bare `./check.sh --render` passed all 1652 tests: 1592 offscreen in 543.458s
and 60 Cocoa in 63.384s, zero skips/xfails, both inventories reconciled. Native/Rust
builds, Rust 3, 243 pinned vendor files, pyflakes and mypy 71 passed. An earlier
full run failed three stale temporal-capture expectations for the former wide
half-pixel LOD; those expectations now match conservative source support. The
final complete gate passed afterward. Exploratory logs are retained, including a
cadence run occluded by a preview and a QTest.qWait worker-import starvation
check; production thresholds/lifetime contracts were not weakened.

Signature/personal composition (3585 modules, shared choices present), complete
embedded Metal/native text and Rust/schema checks pass. Fresh packaged isolated
startup reports native=true, no library/device/art work. Cocoa Analysis/dialog,
Summary/Info, right checks, layout popup, General/My Settings and Graveyard renders
were inspected. Real installed screenshot confirms Collection 3228, retained
Genres folder and aligned sidebar labels. Startup loads 3740 tracks/75 playlists,
reuses 3237 analyses with 0 preparation tasks, and reuses 3106 stock images with
0 prepared. No source/USB publication was performed for this release.

Normal quit/relaunch of 142→146 and 146→146 completed; no workers/processes were
killed. AppleScript returned -128 during quit but the verified app processes fully
exited and saved state remained sound. Each new launch changed no saved bytes.
The final restart baseline below is authoritative.

Evidence: `~/Library/Logs/Soundbox/choices-graveyard-waveform-2026-10-29/`.
check.log and offscreen/native.json are final; state-timeline.json and
state-verification-post-restart.json bind installation/state/native proof.
phase-comparison.json plus before/after frames retain frozen-wave evidence.

- Sidebar leaf icons occupy the folder-chevron column; labels align at every
  depth in both trees. Folder navigation/drag behavior and playlist identity stay
  intact. Collection/Artists remain pinned and local hierarchy remains in settings.
- Analysis controls have 24-point inner margins and aligned 30-point control rows;
  header/note retain the outer edge. BPM Range, Analysis Process, New Imports use
  title case. Existing analysis choices/defaults/data are preserved.
- desktop.choices paints checks on the right of app menus and QListView combo
  popups. Qt retains activation/navigation/accessibility. Arrows sit 14 points from
  the right. Menu QSS reserves the right gutter in action geometry. The narrow
  player popup constrains its view too, preventing a clipped checkmark.
- Summary retains the 320-point cover with inline caption/value rows on its right;
  technical details remain below. Info retains 208-point artwork/three-column
  form/full-width Comments, fitting default 800×760 without scrolling.
- Graveyard double-click/Load Track loads the existing deck without restoring
  membership or capturing autoplay. Hidden tracks prepare a private temporary
  analysis record, upload the bytes, then remove that directory before worker
  retirement. No canonical hidden cache is recreated. Missing native records or
  files report unavailable. Source audio remains immutable.
- Wide BLUE/RGB use conservative physical-pixel source LOD and the middle stops'
  same-LOD reconstruction. All modes and 13 zooms integrate complete lattice
  corners. RGB 576/864 motion/footprint/occupancy is unchanged. Frozen Nuclear
  RGB 24/32/48 registered redraw falls 36–63%, silhouette change 31–42%; 54 captured
  RGB 256–1500 images are byte-identical. These measurements do not establish
  Sonny's live acceptance on every track. No audio/clock/cache/export rewrite.

Next user checks: sidebar/Analysis/Summary spacing, right-hand checks and inset
arrows including My Settings, Graveyard audition before Restore, and wide RGB
playback alongside the accepted close stops. Keep rollback until acceptance.

## Source pilot — explicit approval still pending

Private plan:
`~/Library/Logs/Soundbox/playlist-source-pilot-2026-10-29/plan/`.
Plan SHA `ab2331c468bfa152726df43e699f4fe72afb75b6f01014a3febe42dde90033c9`;
operation `5ec931cd8d814c8baaf636bf68cdbc07`; new root playlist3604899226,
SoundBox Bridge Pilot, with existing102012532 then109556371. Counter352315→352319.
No Collection/audio/analysis/cue changes are permitted. Approval.json is absent.
Current UI requests did not approve this source test.

After Sonny explicitly approves that exact test:

1. Read the prepared plan; validate its exact digest/source/operation identity and
   current source base. Never silently refresh a stale plan under old approval.
2. Record private approval.json, format soundbox-playlist-pilot-approval-v1,
   approved=true, exact plan_sha256/source/operation_id from the approved plan.
3. Back up/hash current curation; gracefully quit SoundBox to drain source intents;
   confirm SoundBox, rekordbox and agent closed. Publish with
   `.venv/bin/python scripts/prove_rekordbox_playlist_pilot.py publish --plan … --approval …`.
4. Verify the actual source result and preserve encrypted DB/XML backups/journal.
   Sonny opens rekordbox to inspect name/order/two songs and retained BPM/Key/cues,
   then quits normally. Capture/compare native result before provisioning anything.

Recover only with the same plan/approval and both apps closed. Unknown state keeps
all evidence; no whole-DB restore. SQL commit alone confers no native acceptance.

The pilot shares Tag/Cue/Grid root/lock/journal:
`~/Library/Application Support/LowFreqLabs/Soundbox/rekordbox_recovery/tag-writes/b496f6c142799c5315d33799`.
It is a separate unintegrated pilot; app ordinary-playlist writes remain disabled.
Its paired TEMP proofs and source SQL policy passed before this UI release.
Source SQL allows only the two playlist tables/counter and denies triggers/DDL.
Paired recovery admits exact complete DB/XML states; unknown prefixes fail closed.
See [analysis-playlists-next.md](analysis-playlists-next.md) for native captures,
copy-only replay/residuals, protocols and source pilot invocation details.

For downloads now: import into native ALL LOCAL (or another native playlist),
then refresh SoundBox. SoundBox import/local playlist editing does not register
tracks in rekordbox yet. Reverse analysis/Key/contentFile/gain/peak/duration needs
its separate copy proof and narrow native acceptance; manual-grid permission
cannot authorize it. SoundBox-first reference.wav remains unimported. Native
Auto Analysis=Enable and BPM/Grid+Key are confirmed restored. Sonny operates
rekordbox UI; never drive it remotely or read/change global Quantize.

## Protected state and recovery

Final restart baseline at `2026-10-30T00:27:28.889159-05:00`:
library SHA `35cd74a893ad3369cda84fff59aca78f97d3b96d72da9f1f7469a895ddb2ff49`;
curation SHA `de29a5330cb48ecfeed5e8e3aefd4b7fbd68509966d7e8faf1c60dab525d4d68`.
All 4041 art/review files, 3 source authorities and 3280 analysis records are exact
across prequit/postquit/postlaunch/restart-postquit/final restart. Counts remain
custom10/import84/artists489/titles1103/hidden517/removal groups1/additions0/
aliases24/settings28. Title 117829415 remains
`Zeds Dead - Rudeboy (Ruudy Flip)`. Never restore the older 1102-title dictionary.
The two quits serialized only columns_state; launches changed no saved bytes.
Keep all saved keybindings, local folders/expansion, sidebar and waveform settings.

Current rollback is `/var/folders/rf/fhvnltd519d1jf6kj6jxj8yh0000gn/T/soundbox-analysis-rollback-flpz99ko/Soundbox-build142.app`.
Build 138/140/141 remain beside it in that same temporary root, pending acceptance.
The older retained rollback remains
`/var/folders/rf/fhvnltd519d1jf6kj6jxj8yh0000gn/T/soundbox-info-layout-rollback-f6ow8jiw/pre-info-layout-Soundbox.app`.
No protected source/device recovery was removed. See PROJECT_LAYOUT for evidence
and regenerated packaging output, separate from retained fixtures.

Authority remains rekordbox6.8.7.0303, DBVersion6000, library629922790, schema
c3255e959e179e46cd15e6ae2da50543427c03c8399b11b7a6809515f06e8c08.
STOP CAPPIN228450310/a191ffb9-1cf5-4166-b61b-97cb453a311b retains native138.00,
exact128750ms endpoint, DAT SHA
715850b961c1ae5caebc941bd390a9c87699ec51401d3521f168e7832312a872.
Tag/Cue/Grid fresh closed-app guards, exact recovery and Analysis Lock remain.
Preserve native-grid-pilot-20261027, native-grid-initial-state-pilot-20261027,
native-grid-historical-pilot-20261027, cue/tag references, generation58
recovery20260926-152340-47477cfa4a49, offline iPod20260926-163945 and frozen
waveform/CDJ fixtures. No Trust's historical primary-only restore grant never
authorizes another track/secondary cache. Physical iPod, wider CDJ hardware,
OneLibrary, recovery retention and distribution gates remain open.
RB USB TEST UUID5B29E617-ACFB-32C2-B580-077A6BF02C64 remains native yerbski,
tempo±10; never restore historical±16. Prior settings restoration receipts remain.
