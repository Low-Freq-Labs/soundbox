# SoundBox current handoff

Updated: 2026-08-25 (America/Chicago)

Read this immediately after `AGENTS.md` / `CLAUDE.md`. This is the single
operational resume note; historical product evidence stays in the roadmap and
topic documents.

## Repository and runtime

- Branch `main` is clean and published at the current wrap-up commit titled
  `Refresh current documentation`. The documentation audit moved retired
  player acceptance work into dated validation, advanced the current roadmap
  and verified device reference to 2026-08-25, and stopped the storage audit
  from presenting the retired August 3 rollback as live.
- Final bare `./check.sh` passed: native build, pyflakes, mypy, and 837 tests in
  11.737 seconds with four expected skips. The installed app was not rebuilt or
  replaced because this wrap-up changed documentation only; the prior
  `./build_app.sh` result remains the installed runtime baseline.
- The resulting build is installed at `/Applications/Soundbox.app` and running
  as PID 41294. Installed and `dist` executable SHA-256 values match at
  `17734adba2c35e08ea465287e2f3b2ec4e7d529521e6b2d8cc9da74b9e13f7f5`;
  strict deep signature verification passed and the packaged native
  CoreAudio/Metal deck activated.
- After runtime verification, Sonny explicitly requested permanent rollback
  cleanup. The exact 246,329,344-byte install rollback at
  `/private/tmp/soundbox-graveyard-install-eZrARK/` was deleted. A follow-up
  inventory found no rollback-named artifacts under `~/Projects` and no other
  SoundBox install/rollback directories in `/private/tmp`.
- The 714,219-byte
  `~/Projects/soundbox-BACKUP-preauthorrewrite-20260725.bundle` was verified as
  a complete 55-commit Git bundle. Every bundled commit had an exact rewritten
  twin matching tree, timestamp, and subject, so it contained no unique project
  state; Sonny approved its permanent deletion and a follow-up search found no
  other top-level SoundBox backup bundles.

## Installed behavior and real-state proof

- A track globally removed from Collection owns no persistent SoundBox V4/V5
  waveform record. Removal discards its exact cache immediately, bulk scans
  skip global-Graveyard IDs, and in-flight bulk/interactive cooks cannot
  republish one after removal. Startup reclaims records left by older builds.
- Selection-scoped global restore returns the track to ordinary bounded
  preparation. Playlist-only removals retain analysis because those tracks
  remain browsable/playable elsewhere.
- The live startup reclaimed exactly 499 records / 1,336,003,488 bytes
  (1.244 GiB) for the 513 global hidden IDs. Analysis inventory fell from 3,420
  files / 12,904,367,378 bytes to 2,921 files / 11,568,363,890 bytes, with zero
  hidden-ID V4/V5 records remaining. The two playlist-only Graveyard tracks
  retained their active V5 records.
- The two pre-install tasks (`big dubz` and `JUST HANGIN AROUND...`) were both
  global-Graveyard tracks. The installed startup now reports zero analysis,
  grid, or cue tasks, proving that the folder rename did not invalidate active
  caches and hidden tracks no longer prompt preparation.
- Startup instant-painted and freshly loaded 3,401 tracks / 75 playlists.
  Curated state remains intact: 84 imports, 10 custom playlists, 466 artist
  overrides, 1,075 title overrides, 513 hidden tracks, one playlist-removal
  layer covering two tracks, no playlist additions, and 24 aliases. Every
  non-settings `library.json` section is canonically identical to the
  pre-install snapshot; only `settings.columns_state` changed on relaunch.
  Current `library.json` SHA-256 is
  `68474e13b1fdb4423f4bd2051ed5daba388386e13e87c8c3c375f87a21d3f67a`.
- Other cache inventories remained 3,297 curated-art, 7,609 stock-artwork, one
  stock shadow, one Rockbox-media, and 20,127 Rockbox-artwork files.

## Next action

- No implementation, validation, packaging, installation, runtime check, or
  documentation cleanup is pending. At resume, verify `git status -sb` is clean
  and `main` still matches `origin/main`; there is no deferred acceptance step.
