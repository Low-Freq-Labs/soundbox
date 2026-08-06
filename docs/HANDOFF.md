# SoundBox current handoff

Updated: 2026-08-05 (America/Chicago)

Read this immediately after `AGENTS.md` / `CLAUDE.md`. This is the single
operational resume note; historical product evidence stays in the roadmap and
topic documents.

## Repository and runtime

- Branch `main` is clean and published at the current wrap-up commit titled
  `Harden device workflows and sync feedback`. It contains the case-only
  composite playlist rename fix, device/remount/import hardening, progress/test
  changes, tests, and docs.
- The completed wrap-up build is installed at `/Applications/Soundbox.app`
  and running as PID 8773. Installed and `dist` executable SHA-256 values match
  at `3f79d13cfef42e7f4db6040bec3d51ae0d289d6b2607cf59c9a6cf84eb657f3c`;
  strict deep signature verification passed and the packaged native
  CoreAudio/Metal deck activated.
- Bare `./check.sh` passed at wrap-up: native build, pyflakes, mypy, and 820
  tests in 12.044 seconds with four expected skips. The gate now buffers
  passing-test noise and always reports its 15 slowest cases. Classic-USB
  150 ms pacing is preserved in production through a tested hook but skipped
  in fixtures; the
  prior roughly 28-second suite now takes about 10–11 seconds without removing
  recovery, rollback, integration, or property tests. `./build_app.sh` passed.
- Startup instant-painted and freshly loaded 3,403 tracks / 82 playlists. It
  prepared zero analysis/grid/cue tasks and all 2,775 curated stock-artwork
  inputs with zero failures. Curated state remains intact: 84
  imports, 24 aliases, 463 artist overrides, 1,049 title overrides, 10 custom
  playlists, 513 hidden tracks, one removal layer, and no addition layer.
  Every non-settings section is canonically identical to the pre-install copy;
  relaunch changed only `settings.columns_state`. Current `library.json`
  SHA-256 is `fb2034528b58c3d4382a41591279f8d3bb2c089adf37e26ee3f431eb9a856106`.
  Cache inventories remained exactly 7,585 stock-artwork, one stock shadow,
  3,399 track-analysis, 3,289 curated-art, one Rockbox-media, and 20,127
  Rockbox-artwork files.
- Install rollback is
  `/private/tmp/soundbox-global-audit-install-wEuOkM/`: it contains the prior
  app bundles and verified pre-install library snapshots, including the
  immediate `Soundbox.pre-planning-counter-removal.app` and
  `library.before-planning-counter-removal.json`. AppData was not removed or
  rebuilt.

## Edge-case hardening now in the installed build

- Composite FAT case-only playlist renames (for example `DnB` → `DNB`) retain
  the verified old path, recovery-back/unlink it, publish the requested spelling,
  suppress the stale case-equivalent manifest row, and restore original name,
  bytes, and mtime on rollback. Physical generation 55 successfully published
  `/Playlists/14 - DNB.m3u8`; the later global managed-library reconciliations
  committed through generation 57 and retained that spelling.
- Stock Sync, stock playlist Delete, and composite Sync plans now pin the target
  mount identity at planning start, revalidate it at planning end, carry it
  across the confirmation modal, and compare it before commit. Same-name target
  replacement tests prove the replacement volume receives zero writes.
- Filesystem-browser rename, Assign Artist, New Folder, and Delete refuse to
  race another device operation and use a session pinned before user input or
  confirmation. Delete cannot span volumes, deduplicates selected parent/child
  paths, revalidates recursive removal without following symlinks, and reports
  exact deleted/total/cancelled results. QFileSystemModel's unpinned direct
  rename path is disabled.
- Device M3U8 import now refuses both lexical `..` and resolved symlink escapes
  before creating the SoundBox Media destination. `/PIONEER` remains outside all
  ordinary authority.
- Composite progress now maps real planner work to 0–30%, publication to
  30–80%, and required dashboard/sidebar/drift finalization to 80–100%.
  Planning shows animated `Planning iPod...` with percentage only; its roughly
  5,000-unit complete managed-library reconciliation remains internal because
  those units are not device writes. Only publication exposes a `#/#`, under
  `Syncing audio`. Confirmed multi-job math gives every job a complete
  proportional slice and remains monotonic. `100%` still means every
  required worker has released the target and Eject is actually available.

## Exact next check and boundaries

- The iPod is currently ejected and the installed runtime reports zero
  removable mounts. Physical generation 57 completed and final refresh/eject
  succeeded. Its roughly 5,000-unit complete-library planner counter was
  technically correct but looked like a device-write count; the installed
  follow-up restores animated `Planning iPod...` with no counter and reserves
  `Syncing audio X/Y` for actual audio work. Verify that presentation on the
  next physical Sync.
  A same-name remount regression is automated and should not be induced on the
  physical iPod.
- One launch overlapped Sonny's live rekordbox backup and its fresh refresh
  failed while retaining the complete 3,403-track instant-paint snapshot. After
  the backup ended, a clean relaunch freshly loaded 3,403 tracks / 82 playlists;
  this was external read contention, not a packaged-app regression.
- No in-scope source, testing, packaging, installation, documentation, commit,
  or push work remains. At resume, verify `git status -sb` is clean and `main`
  still matches `origin/main`; the next physical Sync presentation check above
  is the only outstanding acceptance evidence.
