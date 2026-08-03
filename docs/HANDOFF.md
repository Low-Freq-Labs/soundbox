# SoundBox current handoff

Updated: 2026-08-03 (America/Chicago)

Read this immediately after `AGENTS.md` / `CLAUDE.md`. This is the single
operational resume note; historical evidence stays in the roadmap/device docs.

## Repository and installed runtime

- Branch `main` tracks `origin/main`. Dual-iPod implementation baseline
  `acee4b6` (`Ship guarded dual-boot iPod sync`) and documentation audit
  `c0372c4` (`Align docs with shipped dual iPod workflow`) are pushed; the
  documentation audit contains no application behavior change.
- The installed signed app is `/Applications/Soundbox.app`, last observed as
  PID 53699. It activated the packaged CoreAudio/Metal deck, loaded 3,263 tracks
  and 83 playlists, scheduled zero library-preparation work, and completed
  2,625 stock-art derivatives with zero failures.
- `./check.sh --render` passed on 2026-08-03: pyflakes, native build, mypy, 721
  tests with four expected skips, and the hermetic offscreen UI render.
  `./build_app.sh` and strict signature verification passed before installation;
  strict verification of both the installed app and packaged build passed again
  during the final park-state audit.
- `AGENTS.md` and `CLAUDE.md` remain byte-identical and explicitly define
  **wrap up** as the complete validation, documentation, install-if-needed,
  commit/push, and clean-published-state sequence—not an intermediate progress
  report.
- Pre-install `library.json` SHA-256 was
  `6b55483ae55a9d98b2d42956281a077ed6513820d0238fd10186394c3a5c40be`;
  post-relaunch SHA-256 was
  `2171dfee742db24877d06fb5282052dcf164f58ee8332aae81a94f5a46c29fcf`.
  Sorted JSON excluding Qt's ordinary `settings.columns_state` save was
  identical, so curated state is intact.
- Preserve the one active rollback at
  `/private/tmp/soundbox-install-20260803-72RwQm/` (preceding app plus
  `library.json.before`, about 235 MB). Superseded install rollbacks were
  removed after validation.

## Accepted MA446 behavior

- The FAT32 MA446 dual layout uses only the schema-3 single-copy composite
  planner. Stock-safe MP3/PCM16 WAV files are shared through `iPod_Control`;
  broader Rockbox-supported media lives only under `/Music`.
- Full Collection committed physically. Incremental export/Sync, metadata
  repair, interruption recovery, reference-counted cleanup, theme-native
  per-track art, and firmware-owned Rockbox database refresh are operational.
- Stock firmware follows the applied playlist sequence. Rockbox's alphabetic
  Playlist Catalogue now visibly follows the same order through numbered
  Rockbox-only filenames (`01 - …`, `02 - …`); SoundBox rows and stock titles
  remain unnumbered.
- Explicit managed-playlist Delete is authoritative: it removes checked
  identities, reconciles other available changes in the same transaction, and
  retains unavailable unrelated playlists from their committed snapshots.
- SoundBox manages only four Rockbox config policies: automatic tag-cache
  update, title-from-tags playlist display, alphabetic playlist sorting, and a
  minimum 10,000-entry playlist buffer. Rockbox remains sole writer of
  `database_*.tcd`.
- The physical device uses NightPod with a playlist-centric custom root menu and
  a static bold artist line in its WPS. Those theme/menu files are user-owned:
  SoundBox reads bounded WPS/SBS art dimensions but must preserve the files and
  every unrelated `config.cfg` setting byte-for-byte.

## Remaining boundaries

- No outstanding check blocks normal use of this MA446. On the next routine
  device deletion, physically confirm the new authoritative path no longer
  presents the obsolete Sync-first refusal.
- Other iPod models/checksum families and schema-2 ownership migration remain
  separately hardware-gated and fail closed.
- If macOS resets the USB transport, preserve
  `~/Library/Logs/Soundbox/soundbox.log`, wait for a clean remount, and requeue
  through SoundBox. Never inspect, mount, eject, or mutate the physical iPod
  from development shell commands.
