# SoundBox current handoff

Updated September 28, 2026, America/Chicago.

## Release checkpoint: source validated; installation follows

Sonny explicitly authorized installation and wrap-up, including commit and push.
This September 28 session verified temporary writes and cleanup in `/Applications`
and `.git`, and found the normal `Soundbox Self-Signed` identity. The earlier
session access blocker is resolved. GitHub SSH authentication fails, but the
existing `gh` login can access the repository over HTTPS without changing origin.

The complete inherited diff was reviewed. Runtime source hashes match the earlier
reviewed candidate. The fresh protected offline check and hermetic render passed
at **02:09 local**: 966 tests / four skips, native build, pyflakes and mypy.
Both standard and loading-row renders were visually checked. The source release
commit will precede the normal signed build so the installed bundle can name an
unambiguous clean revision. Installation and final publication are still pending
at this checkpoint; the final handoff will replace this section with observed
installed provenance and curated-state evidence.

Next: build, back up/hash current library and cache baselines in the existing
rollback directory, quit gracefully and capture post-quit state, move the old app,
install with `ditto`, verify signature and relaunch, verify native startup and
curation, exercise ordinary Random advancement, then publish the release and
final documentation. No further approval is needed for those authorized steps.

## Implemented work

Already present in the earlier installed persistence build:

- A2/A3/A8: `state_io` owns serialized publication, exact rollback and interrupted
  transaction recovery; `state_repository` owns schema/recovery/rotating history
  and extension-preserving saves. No valid backup means corrupt-library autosaves
  and artwork mutations remain disabled. Save failure persists in the title bar.
- Backup captures a consistent snapshot; restore validates bounded input, stages
  legacy merge semantics, drains old UI writers/results before final save, and
  reloads after worker finished/join. Stale area generations cannot write.
  Preserve `.state-transaction` after failed rollback; `.state-retired` is only
  completed cleanup. Multi-path restore is recoverable, not filesystem-atomic
  to arbitrary external readers. App entrypoint holds an AppData instance lock.
- A4 local-storage slice: `soundbox.artwork` owns local art/results/defaults;
  `artfetch` retains compatibility aliases. Online UI/startup/providers and
  packaging are still coupled. `_safe_copy` fsyncs payloads before publication.

New source/candidate changes, **not installed**:

- Device rows have no loading suffix. Disabled/readiness gates remain; bottom
  status retains Loading iPod. Discovery and playback remain independent.
- A newer native terminal flag overrides a stale PLAYING snapshot before deferred
  source replacement. Play/Space-latched cue playback keeps autoplay eligibility;
  releasing the cue does not cancel deferred advancement. Explicit pause does.
  Regression tests cover terminal ordering and Random with synthetic discovery.
  These are code defects reproduced in tests, not proof of the live failure cause.
- UI tests/render use fresh temporary AppData and suppress real device discovery.
  Synthetic AAC/ALAC fixtures prefer ffmpeg when present because restricted
  macOS afconvert did not expose ALAC. No live source/device test fixtures.

## Validation and candidate

Final wrap-up gate passed September 28 at **02:03 local**: native build,
pyflakes, mypy (39 engine modules), **966 tests / four skips**, and hermetic render.
`/tmp/soundbox-check.png` and `/tmp/soundbox-wrapup-loading-row.png` were visually
inspected. Instruction byte parity, relative Markdown links and diff whitespace
passed. Command:
`SOUNDBOX_IPOD_CAPTURE="$PWD/soundbox-ipod-ref/offline-lifecycle/20260926-163945" ./check.sh --render`.
Full intended code/docs review found no further required implementation change.

Prepared `dist/Soundbox.app` matches reviewed runtime source hashes and passes
signature verification. Its build script used **ad-hoc signing** because the
normal identity was unavailable. Sign normally before installation. Build log:
`/tmp/soundbox-connected-review-build.log`. Source/binary fingerprints:
`/tmp/soundbox-connected-review-artifact.json`.

Both installed app and candidate declare build **113**, base **c5f9619**, dirty;
the counter is commit-based, so these labels do not distinguish the binaries.
Executable SHA-256:

- Candidate: `8d103ecca221d7d318d185757f5ba4423bb7ff76c5e0856ee9f3820fbd75b2d2`
- Installed: `fc48e281c7706b205833b47fcee4e3046cde1780f2df97037bd674eacec7416a`

## Installed runtime and rollback

`/Applications/Soundbox.app` remains the earlier persistence build 113. Last
verified launch: September 28 **00:23:11 local**, native CoreAudio/Metal,
**3397 tracks / 75 playlists**, zero analysis/grid/cue preparation and artwork
fetched. Its art scheduler saw six uncached records but no eligible fetch; stock
art warming checked 2768 existing images. No newer installed launch is claimed.
Installed source evidence: `persistence-source-files.json` in the rollback dir,
SHA-256 `094abb0e638465774bf57d079166e282fd7322e50cda78cc8eb79f7a733500b8`.

Library SHA-256, unchanged at wrap-up preflight:
`873ddc2f7814ce95d7087e43b12b0ae6f87b7a58fe5f8e42653c86ff8d88958f`.
**202 Favorites** remain. Earlier post-quit/postlaunch validation also preserved
3298 artwork hashes and 2924 analysis size/mtime baselines. No active/retired
state journal exists. No new candidate postlaunch baseline is claimed.

Preserve `/private/tmp/soundbox-lifecycle-rollback.18e3k4di/`: old app
`before-persistence.app`, all `library-persistence-*`/`persistence-*` evidence,
and latest verified running-app backup `library-connected-install-before.json`.
Recheck final post-quit state when installing. Comparison helper:
`/tmp/soundbox-persistence-state.py`. Also retain the older rollback directory
`/private/tmp/soundbox-sync-performance-rollback.vlodpruy/`.

## iPod: disconnected, zero settings changes

Sonny reports no recent Playlist OOM and explicitly requires **zero iPod settings
changes**. No sync, config/firmware/media write, restore or new firmware experiment
is authorized by this wrap-up. Continue normal playback; root cause is unproven.
The earlier user-observed stability with auto-update off and preferences saved by
normal shutdown does not justify another settings change.

Last read-only review confirmed unchanged generation **60**, manifest SHA-256
`63844c8b231d77d7794a42252ebe038b1c2a46f624cab0599eea944e4ef7fdd5`.
Historical identity: UUID `F83BEF4A-47F1-31F7-96EA-E2BA53049BE1`, `/Volumes/IPOD`,
`disk4s2`; revalidate after reconnect. Current Sync-needed evidence is real:

- VOLATIVE track `93738342`: device `e`, current `E [FINAL]`.
- FINNUH track `213412723`: device `Knock2 -Dashstar (Finnuh Remix)`, current
  `Knock2 - Dashstar (Finnuh Remix)`. Titles affect DUBSTEP, TRAP and Collection.
- OG M3U8 grew 5279 → 5319 bytes. Original prefix matches its manifest hash;
  Rockbox appended `/Music/STAYNS/FUNQ DANSE/FUNQ DANSE.wav`, an owned track.
  A separately confirmed sync can adopt the custom-playlist addition. Do not
  suppress the badge or discard the appended membership.
- Other cached-library/saved-curation membership/order and source size/mtime
  matched. Theme art sizes 130/30 are already covered by 160/130/116/30. This
  bounded review did not audit every payload, recovery entry or clutter file.

Evidence: `/tmp/soundbox-connected-manifest.json`, `/tmp/soundbox-drift-review.py`.
Earlier OOM checks and their limits: [OOM report](rockbox-playlist-oom-2026-09.md),
ignored evidence `soundbox-ipod-ref/playlist-oom/20260927-223541/`.
Preserve generation-58 recovery, including `20260926-152340-47477cfa4a49`, until
separately reviewed physical acceptance. Complete pristine offline capture:
`soundbox-ipod-ref/offline-lifecycle/20260926-163945/`; disposable copies only.
Earlier `20260926-163557/` is incomplete. Never restore captures to hardware
without a reviewed plan. `/PIONEER`, rekordbox and source audio remain untouched.

After release completion, follow [ROADMAP.md](ROADMAP.md): A5 desktop queue
integration; A7/A9/A10 worker probes and measured performance; optional provider
wiring/packaging; remaining controller extraction. Physical no-op I/O,
cancel/retry, overlapping drops, eject and reconnect gates remain open.
