# SoundBox current handoff

Updated October 28, 2026, America/Chicago.

## Release and runtime

Sonny accepted this build for continued use and requested final wrap-up.
Efficiency/player/browser work and the manual-BPM EOF
fix are complete, tested, installed and relaunched. Repository branch `main`;
application-source release commit: `9035f70a8572fa9b3783051a06bf1e7c1b7a9ef5`. The current documentation-only
follow-up records its provenance and does not restamp the installed binary.
The release and documentation follow-ups are published on `origin/main`;
no in-scope release work remains. Final acceptance recheck confirmed the running
installed build's signature and source fingerprint; no rebuild or restart was
needed for this documentation-only closeout.

Installed `/Applications/Soundbox.app`, personal **1.0.0 build 133**.
True signed base revision `a25de73b341eedacbdc72c7bedf4d66ea66e0f39`, dirty=true,
application source fingerprint:
`cbd50fccf4fa9c99669b09db49037be62196f57ea3e3fdfe13ca51a1d246f2be`.
Installed **2026-10-28T04:53:57.510539-05:00**; launch requested **2026-10-28T04:53:57.510679-05:00**.
Signature, personal composition and source fingerprint verified against the tested
checkout. Native source SHA remains
`e475b3c7525ad967f74e22f2b967063824fa330e807b46fc91403a6cc5bfc345`;
native binary outside renewed signing bytes is identical to the preceding build.
Actual Cocoa window and offscreen release render inspected. Startup log confirms
CoreAudio/Metal, snapshot and fresh source load: 3,740 tracks / 75 playlists;
Collection 3,228 rows. Preparation reused 3,236 tracks and performed only one grid-
metadata update for the native edit, with zero analysis or compact upgrades.
Stock artwork reused all 3,106; zero new covers fetched. Ready footer and actual
138.00 browser row observed. Resolve process/window IDs fresh.

The canceled thin black text-outline experiment never changed production source,
launched a live viewer or reached an installed build. Scratch moved reversibly to
Trash; receipt: `~/Library/Logs/Soundbox/text-outline-discarded-2026-10-28/`.
The prior app and duplicate dist/build packaging moved reversibly to
`~/.Trash/Soundbox-wrap-20261028-045509/`; exact moves are in `cleanup.json`.
The 2fed… prior app is `previous-Soundbox.app` there; its former temporary rollback
path is empty. Only the current installed app and active checkout remain active. Preserve all source/native
recovery and offline fixtures; cleanup never targets AppData or media.

## Current behavior

- **Original October 28 RGB B; keep BOTH visibility lifts unchanged.** Main-lane
  RGB integrates encoded channels jointly with signed coverage, blends the encoded
  background and decodes the final opaque pixel for Metal. BLUE/3BAND/overview/grid
  keep their compositors. Source detail/extrema, height transfer, LOD, mips,
  footprint and clocks are unchanged. October 27 coverage C at 384 is retained
  underneath B; it is distinct from rejected October 28 C/D/E lift-removal/direct-
  cell trials. Do not revive those trials or change lift/detail without instruction.
- Elapsed/total clock uses `M:SS/M:SS`, with `H:MM:SS/H:MM:SS` on BOTH sides for
  hour-plus tracks. Existing transport updates publish only changed seconds;
  drag-owned preview survives late backend polls. No added polling or source reads.
- Centered larger cue pads/color rims, intent-preserving next/previous and playing
  hot cues, precise paused hold-preview and zoom-scaled grid nudges remain.
- Specific prep-edit progress uses the existing bottom-left status label/style:
  beatgrid/BPM/hot cue/memory cue/My Tags `Updating…`, then verified `Done` for 2 seconds,
  then `Ready`. Focus-only saved-grid checks stay silent. Errors name the operation;
  older timeouts cannot clear newer edits. Shared worker/recovery ownership stays.
- A 30-point footer-colored right rail has a borderless inactive circle-i placeholder.
  The actual scrollbar begins below the 26-point header. Rail/cap/footer junctions
  have no extra separators; Settings retains a full-width footer divider.
- Every column drags independently, including Title. Window width goes to Title
  first when growing/shrinking, retaining other widths. Shrink precedes child layout
  to prevent temporary horizontal-scrollbar flashes; growth follows layout. Artists
  mirrors only changed sections; TrackModel declines unused paint roles early.
- Startup batches art-policy reads, scopes Track construction to playlist members,
  reuses unchanged display rows/registry, defers heavy imports and only retries
  incomplete preparation at the bounded epoch. `browsercache.json` is disposable
  display state; version its policy and never treat it as edit/export authority.
- CDJ research now separates display fields from byte-preserving native streams and
  rejects invalid musical values. It remains standalone, not an installed writer.

## Manual BPM fix and native reference

`138`, `138.0` and `138.00` submit the same 13800 hundredths and display 138.00.
The apparent 138.01 lock was a terminal-row proof failure, not a decimal parser bug.
STOP CAPPIN (track 228450310, UUID a191ffb9-1cf5-4166-b61b-97cb453a311b) has
5,677,875 PCM frames at 44,100 Hz: exactly 128,750 ms. PWV3 bounds EOF to
128746–128754 ms. Sonny set 138.00 in rekordbox and closed it. Native retains its
297th beat exactly at 128750 ms. Complete native DAT SHA:
`715850b961c1ae5caebc941bd390a9c87699ec51401d3521f168e7832312a872`.
An isolated reconstruction reproduced the earlier 138.01 receipt hash, then the
fixed transform matched the complete native 138.00 DAT/EXT bytes exactly.

`AmbiguousGridEnd` admits only a provisional RAM preview; the accepted source
worker resolves disputed existing/proposed terminal rows using a bounded read-only
PCM WAVE framing proof. It skips payloads/decoding, requires exact integral
milliseconds agreeing with native bounds, and binds canonical path, stable file
epoch, header digest, frames and rate. Journaled proof is revalidated before
publication/commit and during recovery. Unsupported/fractional/redirected/changed
sources fail closed. Ordinary edits never open audio. Existing backup, +4 counter,
manual authority, metadata, shared lock/journal and closed-app guards remain.

**Live source remains at Sonny's native 138.00 edit.** No old DAT, native counters,
metadata or source audio were restored. This fix was exercised on isolated source
transactions plus the native read-only reference; no new actual-source writer
acceptance is implied. Fresh installed snapshot confirms 138.00.

## Validation and state

Final bare `./check.sh --render`: 1,477 tests, 1,420 offscreen + 57 Cocoa; zero skips/expected failures, inventories
reconciled. Lint, native build, types and required frozen fixtures passed. The 59
focused checks cover shorthand/retry, endpoint bounds, framing-only reads,
malformed/redirected/changed sources, source WAL exits before/after commit, ordinary
edits avoiding audio, subsequent BPM edits and verified preview reconciliation.
Release render and installed window inspected; native player/GPU gates pass.
AGENTS/CLAUDE parity, relative Markdown links and `git diff --check` pass.

Release evidence:
`~/Library/Logs/Soundbox/manual-bpm-recovery-2026-10-28/` contains native 138.00
captures, verified-native-parity.json, source.json, targeted-final.log, full release
log/inventories, app-build.log, installed receipt/provenance/native preservation,
startup log/window, state verification and scoped cleanup receipt.
Earlier resize/footer/RGB evidence remains in rail-seams-2026-10-28,
browser-columns-2026-10-28, browser-footer-2026-10-28 and waveform-rgb-edge-2026-10-28
under the same private Logs root; small private harnesses remain under build/.

Fresh state backups:
`~/Library/Logs/Soundbox/efficiency-implementation-2026-10-27/install/manual-bpm-`
prequit/postquit/postlaunch/settled library/state records. Sonny had already closed
the app for the native reference before both quit baselines. Prequit time
**2026-10-28T04:46:50.597653-05:00**, SHA `1bddad40428bc18220ca9b0a71d4f39e376c15b646f48644333e17a7c9fcfb1c`.
Postquit **2026-10-28T04:52:32.307464-05:00**, SHA `1bddad40428bc18220ca9b0a71d4f39e376c15b646f48644333e17a7c9fcfb1c`.
Settled **2026-10-28T04:54:24.559899-05:00**, SHA `1bddad40428bc18220ca9b0a71d4f39e376c15b646f48644333e17a7c9fcfb1c`.
Curation excluding settings remained
`a32587405b5432d55c16c7a3b5c8dc4113c2217f7785533af90deaf051cc5cc6`.
All 4,041 art/review hashes, 3
authorities and symlink inventory preserved. Library/settings bytes are identical
across all four stages; installed-state-verification.json records empty differences. Counts: 10 custom, 84 imports,
489 artist overrides, 1101 title edits, 517 hidden, 1 removal group, 0 additions,
24 aliases, 22 settings; analysis count 3276.
These are dated baselines; preserve subsequent user edits.

## Remaining work and safety

Center glistening/native appearance equivalence and occasional scrolling hitch
remain open. Original B's 39-frame/13-zoom preview parity is implementation parity,
not native appearance acceptance. Earlier populated traces retained occasional
16.667-ms presented gaps, roughly 117 fps and zero audio underruns. Keep zero-time
rows in cadence evidence. No fill, temporal blur, source smoothing or lift removal.
MOONLIGHT 576 reference work remains in build/efficiency-implementation-2026-10-27/
moonlight-576; Sonny's recordings/max-zoom stills are retained. No preview is running.

Synthetic 3000-row/240-step resize wall times improved median 46.931→36.265 ms,
p95 60.522→45.186 ms; horizontal-bar Show/Hide 119/118→0/0. This is not live-drag or
compositor 120 fps proof. Earlier 764d… build measured 20 warm packaged sanitized
snapshot launches at median 1402 ms / p95 1424 ms; not repeated on this BPM build.
This is a Qt-paint proxy, not OS-cold or fresh-source readiness.

Next approved milestones: single-tag intelligent playlists, then default-off
full-Collection setting. Variable-tempo scaling deferred. MiniMeters canceled.
Physical iPod acceptance awaits its return; CDJ USB is later and `/PIONEER` stays
excluded. No new intelligent-playlist, physical-device or CDJ acceptance.

Source authority remains rekordbox 6.8.7.0303, DBVersion 6000, library 629922790,
schema `c3255e959e179e46cd15e6ae2da50543427c03c8399b11b7a6809515f06e8c08`.
Sonny alone operates rekordbox reference UI. Source audio is immutable. Closed
rekordbox/agent, Analysis Lock, exact track/UUID/path, backups and Tag/Cue/Grid
recovery remain mandatory. Native Quantize is global/user-owned; never read/write
it. SoundBox Q is separate and affects only newly captured cue placement.
Preserve AppData rekordbox_recovery/native-grid-pilot-20261027,
native-grid-initial-state-pilot-20261027, native-grid-historical-pilot-20261027,
cue/tag baselines and generation 58 recovery 20260926-152340-47477cfa4a49.
Historical No Trust primary-only restore permission does not authorize another
track or secondary cache; original DAT SHA
`f82e11d7a7f0d4a8a1150ae28bfcc4168602f187064ddebd2b7822fb32b9db71`.
Preserve offline iPod 20260926-163945 and waveform/CDJ fixtures. Never restore old
reference state over Sonny's later prep.
