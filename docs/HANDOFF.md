# SoundBox current handoff

Updated September 27, 2026, America/Chicago.

## Start next session: architecture cleanup

Sonny explicitly requested that next session resume cleanup, not repeat the audit
or reopen iPod diagnostics unless OOM returns. Read the
[software audit](software-audit-2026-09.md), [roadmap](ROADMAP.md) and
[public/personal feature matrix](release-boundaries.md).

1. Begin with A2/A3: transactional restore and serialized artwork/state writes.
   Reproduce the known partial restore using disposable fixtures. Stage and
   validate before publication; quiesce competing writers; retain exact rollback;
   inject failures at each publish boundary and concurrent artwork completion.
   Do not experiment on live AppData or rebuild curated caches.
2. Extract the state repository (A8): schema validation, preserved extension
   namespaces, one write authority and explicit persistence failures.
3. Integrate the actual device command queue and measure source/I/O/UI costs;
   separate local artwork from optional providers, then decompose remaining
   window/transaction ownership. Keep player/native contracts and firmware-specific
   publication order. Do not split files arbitrarily or hide buttons over active
   providers. The detailed sequence and acceptance gates are in the roadmap.

Confirmed product scope: the public application is the full music manager for
iPod/MP3/USB, including guarded device editing and confirmed generic Erase.
Future validated rekordbox-compatible/CDJ USB export is shared product scope;
today's research is not production `/PIONEER` write authority. Personal modules
add online artwork/artist scraping and discovery. Dependency redistribution
review is separate from this product choice. Provider extraction, capability
registry and public packaging remain unimplemented; preserve personal behavior.

## Released source and installed runtime

Local branch `main`. Runtime release **`4c131bc`** contains Rockbox preference
ownership, A1 lifecycle corrections, audits and the reorganized roadmap. The
following handoff commit changes documentation only; use `git rev-parse HEAD`
for checkout HEAD rather than assuming it equals the installed source revision.
Sonny explicitly authorized commit/push by requesting wrap-up.

Installed `/Applications/Soundbox.app`: **build 112**, source
`4c131bc338ea3ddfc9ad589db3f153bd5fa2588c`, **dirty=false**. Signature and declared
source provenance verified before and after installation. Final launch:
**September 27, 23:44:00 local**, native CoreAudio/Metal active, 3397 tracks /
75 playlists loaded, zero analysis preparation and zero artwork fetched.
SoundBox is running. No app reinstall is required for the handoff-only follow-up.

- Sync never creates, rewrites, backs up or rolls back Rockbox preferences.
  Auto-update, playlist capacity and tag display remain user-owned and do not
  independently create Sync drift or invalidate a plan. Active theme sizes can
  still require new managed cover derivatives; theme discovery is read-only.
- `TaskOwner` retains artwork/suggestion dialog and interactive waveform workers
  through queued finished delivery and actual join. Shutdown closes admission,
  rejects late results, drains child owners with Qt events alive, then closes
  the native backend. Search socket/retry budgets are explicit; suggestion pacing
  is interruptible. A1's three reported timed-wait gaps are corrected, but other
  task families/device queue storage are not all migrated to this primitive.

## Final verification and curated state

Final bare check with offline capture:
`SOUNDBOX_IPOD_CAPTURE="$PWD/soundbox-ipod-ref/offline-lifecycle/20260926-163945" ./check.sh`
passed native build, pyflakes, engine mypy, **942 tests / four skips**.
Hermetic render `/tmp/soundbox-wrap-final.png` passed and was visually inspected;
the earlier focused database-off dashboard render was also inspected. Harnesses
suppress real discovery and use synthetic/disposable state. Runtime instruction
byte parity, relative links and diff whitespace passed. Build log:
`/tmp/soundbox-final-wrap-build.log`.

Final closed and postlaunch library SHA-256:
`873ddc2f7814ce95d7087e43b12b0ae6f87b7a58fe5f8e42653c86ff8d88958f`.
All JSON, **202 Favorites**, **3298 artwork hashes** and **2924 analysis size/mtime
baselines** match after launch. Final quit changed only Qt columns serialization.
One title override changed earlier between installs and was preserved; earlier
baselines remain intact. Never reset them merely to silence a mismatch.

Use existing `/private/tmp/soundbox-lifecycle-rollback.18e3k4di/`:
`library-final-wrap-before.json`, `library-final-wrap-closed.json`,
`final-wrap-*-state.json`, `final-wrap-installed-plist.json` and
`before-final-architecture-wrap.app` are the final release evidence/rollback.
`/tmp/soundbox-final-wrap-state.py verify` compares the final closed baseline.
Preserve earlier named app/library rollbacks there, including `pre-lifecycle.app`,
`lifecycle-before-wrap.app`, `before-user-settings-fix.app` and
`before-task-owner.app`. Older recovery directory
`/private/tmp/soundbox-sync-performance-rollback.vlodpruy/` remains protected.

## iPod: observe normal playback; diagnostics only if needed

User reports stable playback with database auto-update off and settings persisting
through normal Play/Pause shutdown. Menu+Select forced reset had lost settings.
Usual OOM window was a couple of songs, but duration/allocator state were not
measured. Root cause is unproven; do not call a leak fixed or require another
firmware experiment while normal use is stable.

The iPod is disconnected. Last observed generation **60**, manifest SHA-256
`63844c8b231d77d7794a42252ebe038b1c2a46f624cab0599eea944e4ef7fdd5`.
Last USB volume UUID `F83BEF4A-47F1-31F7-96EA-E2BA53049BE1`, `/Volumes/IPOD`,
`disk4s2`; revalidate after reconnect. No agent sync/config/firmware/media write
or filesystem repair occurred during this investigation. macOS read-only FAT
verification passed and remounted the volume before the user ejected it.

[OOM report](rockbox-playlist-oom-2026-09.md) records the complete evidence/limits.
Private ignored evidence: `soundbox-ipod-ref/playlist-oom/20260927-223541/`.
18 playlist and 14,405 cover hashes matched; 2,881 managed audio files parsed
without embedded pictures; FAT check passed; official 4.0 package matched.
No full audio decode/hash, free-space fragmentation map, clean-state playback
comparison or live allocator trace was performed. `/PIONEER` content was excluded.

Remaining device gates: measured no-op source reads/writes, overlapping drops,
cancel/retry, normal eject after success/failure, repeated reconnect/power-cycle
and real drift after edits. Generation 59 read about 20.89 GB of source bytes;
generation 60's zero-copy dialog does not establish zero reads/DB writes.
`_safe_copy` still needs explicit payload fsync before rename; normal OS eject
remains required. Keep this durability task in the cleanup queue, not as a proven
OOM cause.

Preserve generation-58 recovery, including `20260926-152340-47477cfa4a49`, until
separately reviewed physical acceptance. Historical `copying` status proves
neither commit nor rollback. Pristine offline capture:
`soundbox-ipod-ref/offline-lifecycle/20260926-163945/`; use disposable copies only.
Earlier `20260926-163557/` is incomplete. Never restore captures to hardware
without a reviewed recovery plan. Rekordbox/source audio remain immutable;
normal idle eject remains independent of Sync/Verify.
