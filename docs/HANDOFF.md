# SoundBox current handoff

Updated September 27, 2026, America/Chicago.

## Start next session here: architecture cleanup

Sonny explicitly requested that next session resume the architecture cleanup,
not repeat the completed audit or reopen iPod diagnostics unless OOM returns.
Read [software audit](software-audit-2026-09.md),
[ROADMAP.md](ROADMAP.md) and [release boundaries](release-boundaries.md).

First implementation slice is A2/A3: transactional restore and one artwork/state
write authority. Reproduce the known partial restore in disposable fixtures;
stage and validate before publication, quiesce competing writers, retain exact
rollback and test failures at each publish boundary plus concurrent completion.
Then extract the state repository (A8) with schema validation, preserved extension
namespaces and explicit save failures. Do not run experiments on live AppData.
Follow with actual device queue integration/performance measurements, local art
versus optional providers, then incremental decomposition of window/transaction
monoliths. The full public/personal matrix is settled; packaging is not built.

## Checkout and runtime

Local `main`. Sonny's wrap-up instruction authorizes final validation, release
commit and push. Runtime changes and the audit are in the source release
containing this handoff; determine its exact revision with `git log`.
The last validated local installation before release packaging is build **111**,
source `97fc0236229a8f1e7542cce9418cc7680f9f957b`, dirty=true, launched September 27
at **23:37:08**. Executable SHA-256:
`74d0d80ba92fb5ad0d69a5301b6c77f08eee9d0029bad9a938103e438330546b`.
The previous settings-only build is `before-task-owner.app` in the existing
rollback directory; the earlier build 110 is also retained. Installation facts
are a dated observation, not a claim that the old bundle declares the new commit.

Current changes: Sync no longer owns Rockbox preferences or settings-only drift.
TaskOwner now retains dialog/interactive waveform workers through finished
callback delivery and actual join, closes admission on stop, and drains with Qt
events alive before native teardown. Late results and post-dialog work during
window shutdown are rejected. Search socket/retry budgets are explicit; they
are not permission to destroy a slow running task.

Installation: signature verified; native CoreAudio/Metal active; 3397 tracks /
75 playlists loaded; zero analysis preparation and zero artwork fetched.
Postlaunch library SHA-256:
`5dc8148434eeaaa9509fa83c56b09d3e39c1f9d3bd05f96578de88801d0ee939`.
All JSON, 202 Favorites, 3298 artwork hashes and 2924 analysis size/mtime baselines
match the final closed pre-install state. Compared with the previous installation,
one title override had changed before this quit and was preserved. Quit itself
changed only Qt column serialization. Earlier evidence remains intact.
`library-task-owner-before.json` and `library-task-owner-closed.json`, their
`task-owner-*-state.json` inventories, installed plist and executable hashes are
in the existing rollback directory. Do not substitute an earlier library hash.

Validation: final bare `./check.sh` with the private offline capture enabled
passed (942 tests / four skips, native build,
pyflakes, engine mypy); new lifecycle regressions cover delayed retirement,
responsive drain, late results, child owners and waveform admission closure.
Final hermetic render `/tmp/soundbox-wrap-final.png` passed and was inspected.
Runtime-file parity, relative links and diff whitespace passed. Build log:
`/tmp/soundbox-task-owner-build.log`. No real-device sync performed.

The iPod is no longer mounted at the latest settings-persistence follow-up.
Last USB identity: `/Volumes/IPOD`, `disk4s2`, volume UUID
`F83BEF4A-47F1-31F7-96EA-E2BA53049BE1`. Revalidate identity after any reconnect.

## Active investigation: Rockbox playback OOM

Read [the full evidence and controlled test plan](rockbox-playlist-oom-2026-09.md).
User reports a mid-song “Playlist OOM” stop; repeated Play eventually resumes
at the same position without reboot. No standalone reproduction or memory trace
has been captured. USB mass storage cannot expose live Rockbox playback RAM.
User reports stable listening with database auto-update off and no OOM so far.
Usual failure window was a couple of songs; duration was not measured. Continue
normal use and revisit diagnostics only if it returns; root cause is unproven.

Private ignored evidence:
`soundbox-ipod-ref/playlist-oom/20260927-223541/`.
Contains inventory, complete copied `.rockbox`, playlists, manifest, hash/metadata
reports, official stable/daily packages and upstream source. No canonical audio
was read, and `/PIONEER` content was excluded from application inspection.

- Generation 60: all managed paths/sizes present, all 18 playlist hashes and
  14,405 cover hashes match. Covers decode; all 2,881 managed audio files pass
  bounded metadata inspection without embedded pictures. Audio frames were not
  fully decoded/hashed. Two paths repeat once in each of two playlists.
- FAT check `diskutil verifyVolume disk4s2` / `fsck_msdos -n` passed with exit 0,
  including orphan clusters; volume was remounted. No repairs. Free-space
  fragmentation unmeasured because raw-device access was denied.
- All 381 official Rockbox 4.0 package files match installed bytes. Clean 4.0
  reference and September 27 daily are staged on Mac only, not installed/tested.
- Strongest testable lead: captured config had database auto-update on. The old
  SoundBox build forced that plus a 10,000 playlist minimum; saved limit was 8,000.
  Source shows database allocation can compete with playback. Cause unproven.
- `_safe_copy` verifies bytes but lacks explicit payload fsync before rename;
  normal eject remains the OS flush boundary. No observed partial export.

Settings initially reverted after Menu+Select forced reset. User confirmed they
persist after normal Play/Pause shutdown; the database-off listening trial is
underway. User explicitly requested that Sync never modify firmware preferences;
the new implementation removes that policy entirely. Keep firmware/themed playback
comparisons controlled; no live iPod writes were needed for the app correction.
Then isolate theme, clean firmware state and pinned daily separately if needed.
No config/firmware/media write, cleanup, sync or repair was performed in this
investigation. Clean-state/runtime tests remain outstanding, not passed.

Library backed up before quit in the audit's `host/` directory. Pre-quit SHA-256:
`7071024731f232c1a20d62b3776f2faadcc3756a45719c646339c56308c2bb78`.
Post-quit SHA-256:
`4cbbf67c293a8ac29073f74e10cb24eaaf746fe461b5442cc3ef4c234c396816`.
JSON comparison found only `settings.columns_state` serialization changed;
curation remained identical. Retain both baselines. No AppData reset occurred.

## Architecture/public-personal audit retained

Read [software audit](software-audit-2026-09.md) for A1–A13 and
[release boundaries](release-boundaries.md) for the full feature matrix.
Roadmap contains active work instead of completed diaries. User confirmed public
scope: complete music manager for iPod/MP3/USB, including guarded file editing
and confirmed generic Erase; future validated rekordbox/CDJ USB export is shared.
Personal module isolates online artwork/artist scraping/discovery and its
provider dependencies. Redistribution eligibility needs its own review; do not
label an entire library prohibited without evidence. The feature allocation is
settled; capability registry, provider extraction and public packaging are still
unimplemented. Existing personal functionality stays available.

A1 is now implemented: a shared TaskOwner retains dialog/interactive waveform
workers through queued finished delivery and join, closes admission before
shutdown dispatch, rejects late results and drains before native teardown.
Search socket/retry budgets are explicit and suggestion pacing is interruptible.
The full check passed (942 tests / 13 skips) plus an inspected hermetic render.
The tested change is installed as recorded above; not all task families have
migrated to this owner.

Next structural work: transactional restore; serialized artwork persistence;
state repository; actual coordinator queue integration; measured performance;
optional online-provider separation.
Do not split transaction files arbitrarily or hide buttons over live providers.

Initial architecture audit validation: bare `./check.sh` passed native build,
pyflakes, engine mypy, 941 tests / 13 skips. Disposable restore fault injection exposed
publication before complete artwork restoration. This evidence predates the USB
inspection and the installed runtime fixes. Neither the preference-ownership
fix nor A1 establishes the OOM root cause.

## Previous release and physical acceptance

Build 110 launch was verified September 27 at 20:27:14: native CoreAudio/Metal,
3397 tracks / 75 playlists, zero preparation and artwork fetch. Previously
verified cache counts: 202 Favorites, 3298 artwork, 2924 analysis baselines.
Previous full capture-enabled check/render passed 941 tests / four skips, with
hermetic discovery suppression and inspected rendering. These are dated release
checks, not proof of current standalone Rockbox behavior.

Outstanding lifecycle gates: repeated reconnect/power-cycle during app playback,
loading/readiness presentation, clean badge without edits and real drift after
curation; measured no-op source reads/writes; overlapping drops; cancel/retry;
normal eject after success/failure. Generation 59 read about 20.89 GB of source
bytes; generation 60's zero-copy dialog does not establish zero reads/DB writes.
Coordinator ownership lasts through QThread.finished, but GUI queue integration
and non-device worker lifecycle work remain incomplete.

Current manifest SHA-256:
`63844c8b231d77d7794a42252ebe038b1c2a46f624cab0599eea944e4ef7fdd5`.
Preserve generation-58 recovery, including `20260926-152340-47477cfa4a49`, until
separately reviewed physical acceptance. Historical `copying` status proves
neither commit nor rollback. Never restore a capture without reviewed authority.

## Protected recovery and installation

Earlier private capture:
`soundbox-ipod-ref/offline-lifecycle/20260926-163945/` (4854 hash-verified files,
644906530 bytes). Pristine originals remain read-only; disposable fixture copies
only. The earlier `20260926-163557/` capture is incomplete.

Use existing `/private/tmp/soundbox-lifecycle-rollback.18e3k4di/` for any next
installation; preserve `pre-lifecycle.app`, `lifecycle-before-wrap.app` and other
named rollbacks. Older `/private/tmp/soundbox-sync-performance-rollback.vlodpruy/`
remains protected. `/tmp/soundbox-lifecycle-install-state.py` references earlier
baselines: account for the documented Qt-only serialization change before using
its comparison. Do not overwrite earlier evidence to silence a mismatch.

For runtime work: bare `./check.sh`, build, back up/hash library, graceful quit
and verified exit, rename old bundle + ditto install, signature/launch/native
log and curated-state verification. Never wipe AppData or use rm -rf to install.
Normal idle eject must remain independent of sync/integrity. Rekordbox and source
audio remain immutable. See both runtime instruction files for the full contract.
