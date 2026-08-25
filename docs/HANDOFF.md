# SoundBox current handoff

Updated: 2026-08-24 (America/Chicago)

Read this immediately after `AGENTS.md` / `CLAUDE.md`. This is the single
operational resume note; historical product evidence stays in the roadmap and
topic documents.

## Repository and runtime

- Branch `main` is clean and published at the current wrap-up commit titled
  `Repair late analysis and harden track actions`. It contains automatic and
  manual late-Rekordbox waveform recovery, selection-scoped Graveyard restore,
  the track-row Open in Finder action, tests, and documentation.
- The completed build is installed at `/Applications/Soundbox.app` and running
  as PID 29062. Installed and `dist` executable SHA-256 values match at
  `43b7406e1954929d4b41bc9024472526e2d8b7db6cacbde28e694e7c12fd94df`;
  strict deep signature verification passed and the packaged native
  CoreAudio/Metal deck activated.
- Bare `./check.sh` passed at wrap-up: native build, pyflakes, mypy, and 832
  tests in 11.239 seconds with four expected skips. `./build_app.sh` passed.
- Startup instant-painted and freshly loaded 3,401 tracks / 75 playlists. It
  prepared zero analysis/grid/cue tasks and all 2,767 curated stock-artwork
  inputs with zero failures. Curated state remains intact: 84 imports, 10
  custom playlists, 466 artist overrides, 1,075 title overrides, 513 hidden
  tracks, one playlist-removal layer covering two tracks, no playlist-addition
  layer, and 24 aliases. Every non-settings section is canonically identical
  to the immediate pre-install copy; relaunch changed only
  `settings.columns_state`. Current `library.json` SHA-256 is
  `220847353794a64b6cc5124119baa0057f8b1e79bd91eb7cc92da5b878fc62b1`.
  Cache inventories remained exactly 3,420 track-analysis, 3,297 curated-art,
  7,609 stock-artwork, one stock shadow, one Rockbox-media, and 20,127
  Rockbox-artwork files.
- Sonny explicitly requested permanent removal of the completed install
  rollback on 2026-08-24. `/private/tmp/soundbox-final-install-jDWLuK/` and its
  saved app bundles/library snapshots were deleted, freeing 704 MB. No
  temporary rollback remains; the installed app and AppData were untouched.

## Installed behavior

- A ready V5 cache whose display-mode provenance still says
  `soundbox-analysis` receives a targeted read-only ANLZ probe during the
  recurring preparation scan. If Rekordbox has since published a valid
  PWV3/PWV5/PWV7 stream, only that track is atomically recooked. Complete
  Rekordbox-backed modes remain on the cheap manifest-only path.
- Track rows expose `Reanalyze Track` (or a selected-track count). A forced cook
  bypasses the ready shortcut, keeps the previous playable record until a
  verified replacement publishes, and hot-refreshes the loaded deck. Source
  audio and Rekordbox data remain read-only.
- Five live Aug 23 tracks were repaired and verified with exact
  `rekordbox-anlz-pwv3`, `rekordbox-anlz-pwv5`, and
  `rekordbox-anlz-pwv7` provenance plus Rekordbox grids: CHIDORI, MADHOUSE,
  SHIFTY, INCORRECT, and THE BABADOOK. The installed automatic scan then
  reported zero pending work.
- Graveyard has no Restore All action and no double-click restore. Select one,
  Shift/Cmd-select several, or Cmd-A, then right-click the selection and choose
  Restore. The action restores exactly the selected rows.
- Main-table and Artists-browser track rows expose `Open in Finder` directly
  below `Copy File Name(s)`. It launches `/usr/bin/open -R` for the exact
  available selected source paths, warns on a wholly unavailable selection,
  reports partial missing selections, and never opens this bulk action from an
  Artists group header.

## Crash finding and next check

- The append-only crash stack last changed at 2026-08-23 19:43:11. Its latest
  fatal report is a shutdown abort involving `StockArtworkPrepWorker` during
  Python finalization, not an analyzer exception and not evidence of corrupt
  waveform bytes. No crash occurred during the installed reanalysis, automatic
  scans, Finder-action build, or final relaunch.
- No in-scope source, testing, packaging, installation, documentation, commit,
  or push work remains. At resume, verify `git status -sb` is clean and `main`
  still matches `origin/main`; there is no deferred acceptance step from this
  session.
