# SoundBox current handoff

Updated: 2026-07-31 (America/Chicago)

This is the single current session-resume note. Read it immediately after
`AGENTS.md` / `CLAUDE.md`. Replace stale facts here at every wrap-up; do not
append a session diary.

## Repository and installed app

- Branch: `main`, tracking `origin/main`.
- Current implementation commit: `WRAP_COMMIT_PENDING` (`Suppress macOS accent
  picker for held CUE`). The prior remote commit is `708ea5e` (`Record Rekordbox
  6.8.7 compatibility`).
- `/Applications/Soundbox.app` is the signed build from this exact source tree.
  Sonny accepted the physical first-track hold test: holding `C` remains a
  momentary CUE gate and the macOS accent-character panel no longer appears.
- The installed native deck is active. The real library loaded 3,263 tracks / 83
  playlists and scheduled zero preparation work.

## Current state

The held-C bug was a native AppKit boundary, not a missing Qt repeat-event
filter. The existing shortcut override and repeat swallowing worked after the
first track transition, but Cocoa could still claim the first held `C` for its
press-and-hold text service.

The packaged entrypoint now writes the bundle-local
`ApplePressAndHoldEnabled=false` preference before importing Qt, and the built
app declares the same key in `Info.plist`. This changes only Soundbox; it does
not alter the global macOS preference or other apps. The Qt-side defense remains:
every non-text `NoTypeAhead` browser opts out of the input-method client and
reasserts that state after editable model selections, while delegate-created
line editors retain IME support. Search fields and Command-C keep normal
behavior; held-letter accent selection is intentionally unavailable inside
Soundbox because `C` is a deck control.

The source-neutral library architecture and accepted ABI-4 player/cache state
remain unchanged. Rekordbox 6.8.7 remains compatible with the read-only live-DB
adapter. No source audio, Rekordbox data, analysis data, or device target was
written.

## Exact next-session work

1. Start the staged `MainWindow` decomposition with the crown-jewel persistence
   boundary. Characterize `library.json` load, corruption recovery, rotating
   backup, and atomic-save behavior, then extract a UI-independent state store
   without changing schema or recovery semantics.
2. Continue only in separately validated stages: library load/refresh plus row
   cache coordination; playback/autoplay orchestration; then custom
   playlists/imported-track operations. Keep `MainWindow` as widget/signal
   composition rather than cosmetic mixins.
3. The next committed product/device task remains read-only-first real iPod
   hardware validation per `docs/DEVICES.md` and the top-of-file contract in
   `soundbox/sync.py`.

## Validation and rollback

Validation on 2026-07-31:

- `./check.sh` passed 569 tests with one expected platform skip; native build,
  pyflakes, and mypy passed.
- Seven focused macOS-default/CUE tests passed, covering packaged-only default
  mutation, failure-safe launch, Qt shortcut ownership, repeat suppression,
  browser input-method state, inline-editor IME support, and Command-C.
- The signed installed bundle verifies and contains
  `ApplePressAndHoldEnabled=false`; the live per-bundle default reads `0`.
- Relaunch activated the packaged native dylib, loaded 3,263 tracks / 83
  playlists, and scheduled zero preparation tasks.
- The authoritative post-quit `library.json` remained byte-identical after
  install/relaunch at SHA-256
  `183e8413a705ebdeea34a4c3d728e727b2181d283c805590616120b6a41aec3c`.
  All 3,244 analysis files and 3,125 art files remain present.
- Sonny physically accepted held CUE on the first loaded track. This is the
  acceptance signal that the synthetic Qt event tests could not supply.

The temporary install rollback is retired after the accepted wrap-up. Recovery
does not require a data migration: reverting the source/app bundle and deleting
the Soundbox-only `ApplePressAndHoldEnabled` default restores prior behavior.
