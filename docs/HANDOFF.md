# SoundBox current handoff

Updated: 2026-07-30 (America/Chicago)

This is the single current session-resume note. Read it immediately after
`AGENTS.md` / `CLAUDE.md`. Replace stale facts here at every wrap-up; do not
append a session diary.

## Repository and installed app

- Branch: `main`, tracking `origin/main`.
- Last completed implementation: `61eb15d` (`Decouple library model from
  rekordbox`). This wrap-up records the subsequent Rekordbox 6.8.7
  compatibility audit; no Soundbox source change was required.
- The architecture change is source-only and has no user-visible behavior, so
  the installed app was deliberately not replaced.
- `/Applications/Soundbox.app` remains the accepted signed ABI-4 player build.
  After Rekordbox was updated to 6.8.7, the installed app refreshed the live
  database successfully, loaded 3,263 tracks / 83 playlists, and scheduled
  zero unnecessary library-preparation work.

## Current state

`soundbox.library` now owns the source-neutral `Track`, `Playlist`, and
`Library` domain model. The read-only live rekordbox DB and XML modules are
concrete adapters into it. Engine, sync, imports, instant-paint cache, desktop
workers, UI orchestration, and tests import domain types from the neutral
module rather than from an XML parser.

The model exposes generic source identity, normalized cue rows, and an optional
read-only source-analysis path. Rekordbox XML `KeyType=1` location playlists
are normalized to canonical track IDs at the adapter edge. Compatibility
properties/re-exports remain for XML-era callers, and schema-4 instant-paint
snapshots are read and normalized without sacrificing first paint. No
Serato/Traktor registry or speculative provider interface was added; a second
real integration should establish only the interface its actual discovery,
refresh, configuration, and analysis semantics require.

The architecture audit found one remaining general hotspot:
`desktop/app.py` is 3,334 lines and `MainWindow` owns 170 methods. This is
manageable, but it should be decomposed by real state/behavior ownership rather
than split into cosmetic mixins. `desktop/player.py`,
`soundbox/track_analysis.py`, and the native deck are also large, but their
cache/waveform/realtime invariants are substantially more cohesive and should
move only across tested protocol or data-format boundaries.

The installed player/cache state remains unchanged from the accepted prior
handoff: the complete V5 audit passed, no V4 rollback remains, and no temporary
install rollback remains. Do not revive the rejected player-renderer
experiments without new physical evidence.

Rekordbox 6.8.7 is compatible with the live-DB adapter. Its database has the
same 42-table schema and table row counts as the automatic 6.8.6 backup. At
Soundbox's normalized boundary, both versions produced exactly 3,180
playlist-referenced tracks, 83 playlists, 11,617 playlist memberships, and
11,370 cue rows, with zero changed track records or playlist contents. Every
referenced analysis file still resolved. The installed build's 3,263-track
count includes collection orphans; the current source intentionally filters
those and this pre-existing difference is unrelated to the Rekordbox update.

## Exact next-session work

1. Start the staged `MainWindow` decomposition with the crown-jewel persistence
   boundary. Characterize the current `library.json` load, corruption
   recovery, rotating backup, and atomic-save behavior in focused tests, then
   extract a UI-independent state store without changing the file schema or
   recovery semantics.
2. Continue only in separately validated stages: library load/refresh plus
   row-cache coordination; playback/autoplay orchestration; then custom
   playlists/imported-track operations. Keep `MainWindow` as widget/signal
   composition. Do not combine this pass with another DJ-system integration or
   merely redistribute methods among new mixins.
3. The next committed product/device task remains read-only-first real iPod
   hardware validation per `docs/DEVICES.md` and the top-of-file contract in
   `soundbox/sync.py`.

## Validation and rollback

Architecture wrap-up validation on 2026-07-30:

- `./check.sh` passed 565 tests with one expected platform skip.
- The focused Rekordbox adapter/analysis suite passed 23 tests against the
  6.8.7 environment, and a real read-only live load completed successfully.
- A read-only comparison of the live 6.8.7 database with Rekordbox's automatic
  6.8.6 backup found no schema, row-count, normalized-track, cue, playlist, or
  membership changes.
- `./check.sh --render` passed the same suite and hermetic offscreen render.
- Native build, pyflakes, and mypy for the engine passed in both gates.
- The source-neutral model, adapter normalization, schema-4 cache read, live-DB
  mapping, sync, imports, and desktop integration have focused regression
  coverage.
- `git diff --check` passed before the implementation commit.

No source audio, rekordbox data, device target, installed bundle, or
`~/Library/Application Support/LowFreqLabs/Soundbox/` state was modified.
This refactor needs no data rollback; schema-4 instant-paint caches remain
readable and schema-5 is written only by the source/runtime that adopts this
commit.
