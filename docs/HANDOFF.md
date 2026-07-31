# SoundBox current handoff

Updated: 2026-07-30 (America/Chicago)

This is the single current session-resume note. Read it immediately after
`AGENTS.md` / `CLAUDE.md`. Replace stale facts here at every wrap-up; do not
append a session diary. Dated implementation and validation history belongs in
the relevant reference document.

## Repository and installed app

- Branch: `main`, tracking `origin/main`.
- Last completed implementation: `63c9acd` (`Bound canonical waveform cache
  with V5 mips`), pushed to GitHub.
- The signed V5 app is installed at `/Applications/Soundbox.app` and is
  running with native deck ABI 3.
- Installed launch verification loaded 3,263 tracks / 83 playlists and logged
  the native CoreAudio/Metal deck active.
- Final source validation for the V5 implementation: bare `./check.sh` passed
  563 tests with one expected environment skip. `./check.sh --render`, the
  signed build, install, relaunch, and real two-track waveform-reference gate
  also passed.

## Active unfinished work

The direct full-library V5 waveform recook is still running and is resumable
across clean app restarts. At this handoff snapshot:

- 707 `.sba5` records were present for 3,244 reachable tracks (the worker
  validates each record before accepting it; this was a count/size snapshot,
  not the final full audit);
- the V5 cache was 1.8 GiB with zero stranded `.tmp` files;
- the data volume had about 57 GiB free;
- `library.json` remained SHA-256
  `198b46c6bcd71510ac965a82f97f51c28bef5f348d45038cdb1f03106dbb1c11`.

Do not start another full cache operation or delete the rollback while this is
unfinished. SoundBox itself enforces a 20 GiB cook floor and resumes at 25 GiB.

## Exact next-session checks

1. Confirm SoundBox is running and inspect the current V5 count, cache size,
   temp count, and free space. Do not babysit the worker; it can finish on its
   own.
2. Once preparation is complete, run the full production audit:

       PYTHONPATH=. .venv/bin/python scripts/audit_analysis.py --full

   Expected reachable result: 3,244 verified V5 records, zero playable V4,
   zero missing/invalid reachable analysis, 19 already-missing sources, and no
   orphan V4/V5 records.
3. Recheck the `library.json` hash above and inspect the log for
   `Library preparation ended`.
4. Only after those checks pass, retire the temporary rollback directory
   described below. Report exactly what was removed.
5. The next committed product task remains read-only-first real iPod hardware
   validation per `docs/DEVICES.md` and the top-of-file contract in
   `soundbox/sync.py`.

## Rollback that must remain for now

`/private/tmp/soundbox-v5-cutover.p6pzRg` is the single temporary rollback
(about 236 MiB). It contains:

- the previous installed `Soundbox.app`;
- pre-quit and post-quit `library.json` copies;
- `libcache.json`;
- `analysis-metadata.json.zst`, covering all 3,248 retired V4 manifests,
  1,983,934 beat rows, and 11,344 cue rows.

The old 40 GiB V4 waveform cache was deliberately deleted only after these
backups were verified, raising free space from 19 GiB to 59 GiB before the V5
recook began.

## Documentation status

Current-facing architecture, player, cache, cue, worker, storage, and roadmap
language describes V5. Remaining V4 references are intentional: either the
read-only compatibility contract or dated historical validation/R&D records.
