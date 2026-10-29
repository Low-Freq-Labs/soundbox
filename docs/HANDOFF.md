# SoundBox current handoff

October29, 2026. Branch main; this document’s commit is the local implementation
checkpoint, based on published46271f4. The branch is intentionally unpushed. This overnight
source checkpoint is intentionally **not a release or completed wrap-up**.
The Mac locked while Sonny slept. `CGSessionCopyCurrentDictionary` reports
`CGSSessionScreenIsLocked=1`; loginwindow is frontmost. Onscreen Cocoa timing/cue
checks failed and the stalled native run was interrupted (exit130). Do not
install or push until the complete gate passes with an unlocked GUI. No password
or authentication bypass was attempted. The temporary display assertion was released.

## Source work ready for unlocked validation

- Three-line menu immediately right of Q, matching44×28 controls and equal toolbar
  gaps. Waveform color shares General's existing setting; all three beat-count
  modes use RAM grid/cues, fractional off-grid cue counting and blank/no-next
  behavior. Native ABI6 adds the display-mode setter; waveform shader is unchanged.
- Loaded native song → USB collection, without a synthetic playlist. Existing
  native/managed playlists survive; mount/UUID stays bound through deferrals.
  Initial/incremental/no-op and interrupted standalone resumption have compiled
  renderer/transaction tests. Explicit standalone sources also enter source/target
  separation before preparation. Imported/XML tracks retain the native-analysis gate.
- Analysis tab immediately after General; BPM/Grid+Key use pinned rbxport in a
  cancellable child process. Auto analysis of new SoundBox imports and first-beat
  cue default off; Phrase disabled. Durable results bind canonical path, full
  audio hash, filesystem identity and engine; later native IDs at the same path
  can find them. Native/manual grids/source cues win. Save enforces the reader's
  64MiB library ceiling before publication, preserving prior bytes on refusal.
- This is the detection stage, **not completed two-way analysis**. Sonny explicitly
  requires either app's data to work regardless of import order/author. Detected
  grid/Key/initial-analysis source publication and native CDJ use of those results
  remain pending their own native reference/authority, separate from manual-grid
  acceptance. Never imply that the temporary local stage is the final ownership policy.
- Pure provisional native membership append/remove plans match frozen native
  unsynced rows, physical deletion, survivor renumbering and counters. An encrypted
  temporary-copy replay changed only djmdSongPlaylist and localUpdateCount.
  There is no live ordinary-playlist publisher; create/delete/synced/XML/recovery
  acceptance remains open. See [the accepted scope](analysis-playlists-next.md).

## Validation and blocking gate

Evidence root: `~/Library/Logs/Soundbox/overnight-analysis-2026-10-29/`.
The full check compiled native/Rust, verified243 pinned vendor files and Rust3,
passed pyflakes/mypy70 files and1547 offscreen tests with zero skips/xfails.
The required59-test Cocoa phase could not complete in the locked session;
check.log retains failures/interruption. The stale preceding-release native.json
was archived as native-stale-previous-release.json; no current native report/pass
exists in build/check. Keep both phases
mandatory and rerun the full bare gate after unlocking; investigate any failure
that persists. Do not relabel a locked-session attempt as hardware acceptance.

A fresh offscreen run, focused20 analysis/ownership/proof tests and22 state/backup
tests verify the subsequent source guards. The 64MiB publication guard was added
after the full offscreen run began and was validated separately. Offscreen and
Cocoa chrome renders were inspected; those are not onscreen cadence acceptance.
Signed personal1.0.0 **candidate build139** is at `dist/personal/Soundbox.app`,
not installed. Build base46271f451d771588fb02099d453d2ff35e8ecbf6, dirty=true;
source fingerprint
`a4be514a001f3070a711de1b4390dddfb46c4f86a816cdb76171a9d3a845c5a4`.
Its signature and personal composition verify. Packaged isolated launch passed:
native backend active,0 devices/no library/no art worker. The packaged renderer
also reproduced120 BPM/8B/249ms from the generated reference. Native ABI6 and
both Mach-O text sections match local build artifacts; the complete embedded Metal
matches source. These checks do not replace the unlocked onscreen gate.
Candidate facts are candidate.json/candidate-verification.json; build and isolated
logs are beside them. Do not label build139 as installed or as a clean Git build.

## Installed app and real curated state — retained

Installed personal1.0.0 build138 is unchanged, still running from
`/Applications/Soundbox.app`. Its build base is
`2c761726b6c10e703a9466c444fbb3a3a96c8d2e`, dirty=true, source fingerprint
`349e6cc41055966070c8e2391593eb4a553fe5d28551981e9c2aba4c3613b0e7`.
No installation/relaunch or agent source/USB write occurred during this checkpoint.

Observed state at `2026-10-29T05:36:09.712599-05:00`:
library SHA `78178b37a0649b7fe290e867301ba37d9a4eb90d8de43cc0bc7823336fc82b77`;
curation SHA `de29a5330cb48ecfeed5e8e3aefd4b7fbd68509966d7e8faf1c60dab525d4d68`.
All4041 art/review files and3 authority files match the preceding postvisual
baseline;3280 analysis records remain. Counts: custom10/import84/artists489/
titles1103/hidden517/removal groups1/additions0/aliases24/settings25.
Only settings.device_views differs from the preceding saved baseline. Title
117829415 = Zeds Dead - Rudeboy (Ruudy Flip) is preserved. Never restore the older
1102-title dictionary. Exact backups/comparison are under unchanged-installed/.

The preceding installed release passed its complete1586-test gate and real launch:
3740 tracks/77 playlists, Collection3228;3237 analysis reused/0 preparation,
3106 stock art reused/0 fetched. These are preceding-release observations,
not a new candidate launch. Previous handoff and release provenance remain in
previous-handoff.md and `playlist-analysis-ui-2026-10-29/release/` under Logs.
The existing rollback remains:
`/var/folders/rf/fhvnltd519d1jf6kj6jxj8yh0000gn/T/soundbox-info-layout-rollback-f6ow8jiw/pre-info-layout-Soundbox.app`.
Do not retire it as though a new installation has been accepted.

## Exact next steps

1. Unlock the Mac and keep its GUI available. Run `./check.sh --render` bare.
   Reconcile both report inventories; current discovery is1606 tests. A focused
   native pass or archived old native.json cannot substitute for the full release gate.
2. After it passes, build personal, safely install with a fresh library backup,
   graceful quit, one temporary rollback and ditto, then verify signature/native
   startup/zero preparation and curated state after quit/launch. Verify actual
   hamburger placement/menu and Analysis page against the real library. Record
   the final binary's true build base/fingerprint; never copy candidate/old facts.
3. Update current docs/this handoff, review the complete intended diff, runtime
   byte parity/links/diff check, then commit/push. This checkpoint remains local
   because the release gate is incomplete; do not claim the branch is published.
4. First native-reference action: Sonny deletes **only** SoundBox Playlist Test,
   keeps both songs in Collection, quits normally and replies done. Playlist
   ID4086406462 / UUID4739cf09-58a3-4081-8280-a1695ded60c3 last contained only
   Gangster Tune18213989. Capture native-deleted with the preserved
   `playlist-analysis-ui-2026-10-29/capture_soundbox_playlist_ref.py` before asking
   for another native edit. Never drive rekordbox remotely.
5. Then collect isolated native analysis references for unanalysed import,
   BPM/Grid+Key, Key-only and first-beat cue. Prepared32-second stereo WAVs are
   in overnight evidence/analysis-reference/: SoundBox-first reference.wav and
   rekordbox-first reference.wav, plus exact hashes/results. Both detect120 BPM,
   C major/8B, first beat249ms. They were not imported into either live app.
   Compare full native tables/sidecar registrations/counters/XML on copies, then
   prove/recover the reverse publisher before enabling an exact accepted authority.
   Native create/name has +2 counters/distinct timestamps; upstream create has
   +1. XML uses hex IDs and changes on membership edits too. Do not guess parity.

## Protected source/device boundaries

Authority remains rekordbox6.8.7.0303, DBVersion6000, library629922790, schema
c3255e959e179e46cd15e6ae2da50543427c03c8399b11b7a6809515f06e8c08.
STOP CAPPIN228450310/a191ffb9-1cf5-4166-b61b-97cb453a311b retains native138.00,
exact128750ms endpoint, DAT SHA
715850b961c1ae5caebc941bd390a9c87699ec51401d3521f168e7832312a872.
No detected-analysis/Key/playlist authority was added. Existing Tag/Cue/Grid closed
checks, shared recovery and Analysis Lock remain mandatory; global Quantize is untouched.

Preserve native-grid-pilot-20261027, native-grid-initial-state-pilot-20261027,
native-grid-historical-pilot-20261027, cue/tag references and generation58
recovery20260926-152340-47477cfa4a49; offline iPod20260926-163945 and frozen
waveform/CDJ fixtures. The historical No Trust primary-only restore grant never
authorizes another track/secondary cache. Accepted waveform appearance remains
qualified; physical iPod, wider CDJ firmware/control/media, OneLibrary, performance/
recovery retention and distribution gates remain open.
RB USB TEST UUID5B29E617-ACFB-32C2-B580-077A6BF02C64 remains native yerbski, tempo±10;
never restore historical±16. Earlier five-file settings restoration receipts remain.
