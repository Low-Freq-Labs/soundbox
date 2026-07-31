# SoundBox current handoff

Updated: 2026-07-30 (America/Chicago)

This is the single current session-resume note. Read it immediately after
`AGENTS.md` / `CLAUDE.md`. Replace stale facts here at every wrap-up; do not
append a session diary. Dated implementation and validation history belongs in
the relevant reference document.

## Repository and installed app

- Branch: `main`, tracking `origin/main`.
- Last completed implementation: `a7793f2` (`Simplify waveform renderer`);
  this handoff update follows that implementation commit.
- The signed player-renderer simplification is installed at
  `/Applications/Soundbox.app` and running with native deck ABI 4.
- Installed launch verification loaded 3,263 tracks / 83 playlists, activated
  the native CoreAudio/Metal deck from the installed bundle, and scheduled zero
  library-preparation work.

## Current state

The direct full-library V5 waveform recook is complete. The full production
audit reported 3,244 verified V5 records, zero playable V4, zero
missing/invalid reachable analysis, 19 already-missing sources, and zero
orphan V4/V5 records. The cache was about 12 GiB with zero stranded `.tmp`
files and about 47 GiB free on the data volume.

Before install, `library.json` was SHA-256
`198b46c6bcd71510ac965a82f97f51c28bef5f348d45038cdb1f03106dbb1c11`.
After the old app quit and the new app launched it was
`e6efdb75024a24badbc21e87597ad6bf6bb23b4c95d36305061a42eedc3b1ec2`.
A recursive semantic comparison found exactly one difference:
`settings.columns_state`, the Qt column-layout blob saved during the normal
quit/start cycle. Artist/title overrides, aliases, hidden tracks, playlist
removals, imports, and custom state are identical. The temporary pre-install
backup has now been retired.

The installed candidate removes the rejected carrier-neutral/background-bed
and post-resolve coverage/RGB sharpening controls. Native zoom now snaps to
the same thirteen UI stops, and the former overlapping stop tapers are one
explicit 64–256 midrange rule. CoreAudio timing, close mip selection, signed
detail, height normalization, exact coverage, and 3BAND are unchanged.
Sonny accepted the installed simplification. The remaining softness growing
with close zoom matches sample-and-hold display persistence: at Retina scale
two and 120 Hz the lane advances from 6.4 physical pixels per refresh at
384 px/s to 25.6 at 1536 px/s, while those stops already use the sharp direct
linear path. Do not add coverage sharpening, motion blur, whole-pixel snapping,
or a playing-only filter to compensate; each was either measured or directly
implicated in the retired static/pulse/judder tradeoff.

## Exact next-session checks

1. The next committed product task is read-only-first real iPod hardware
   validation per `docs/DEVICES.md` and the top-of-file contract in
   `soundbox/sync.py`.
2. If player motion is revisited, begin from new physical evidence and the
   accepted exact renderer; do not revive the removed experiment controls.

## Rollback

No temporary rollback remains. The obsolete 236 MiB pre-V5 rollback was
removed after the complete V5 audit passed. The 230 MiB player-simplification
rollback, containing the previous ABI 3 app and pre-install `library.json`,
was removed only after the installed ABI 4 app passed validation and Sonny
accepted its live behavior.

## Documentation status

Current-facing architecture, player, cache, cue, worker, storage, and roadmap
language describes V5. Remaining V4 references are intentional: either the
read-only compatibility contract or dated historical validation/R&D records.

Player simplification validation on 2026-07-30:

- `./check.sh` passed 560 tests with one expected platform skip.
- `./check.sh --render` passed the same suite and hermetic render.
- The six-image real-track BLUE/RGB/3BAND corpus rendered successfully.
- The focused native/UI suite passed 257 tests with one expected skip.
- The full V5 production audit passed exactly as recorded above.
- `./build_app.sh`, pre-install and installed signature verification, safe
  replacement, relaunch, ABI 4 verification, native-deck startup, real library
  load, and zero-task preparation verification all passed.
- The wrap-up reran both complete gates successfully before commit.
