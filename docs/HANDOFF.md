# SoundBox current handoff

Updated: 2026-08-04 (America/Chicago)

Read this immediately after `AGENTS.md` / `CLAUDE.md`. This is the single
operational resume note; historical evidence stays in the roadmap/device docs.

## Repository and installed runtime

- Branch `main` is published at the wrap-up commit containing this handoff; use
  `git rev-parse HEAD` for its exact immutable ID. The intended worktree is
  clean. The published change contains track-removal menus, consolidated title
  cleanup, direct-import duration repair, recurring waveform preparation,
  tests, and user/developer documentation.
- The signed app built from that worktree is installed at
  `/Applications/Soundbox.app`, last observed as PID 11083. It activated the
  packaged CoreAudio/Metal deck and loaded the current live rekordbox snapshot:
  3,414 tracks and 81 playlists.
- `./check.sh` passed on 2026-08-04: pyflakes, native build, mypy, and 738 tests
  with four expected skips.
  `./build_app.sh` passed; strict signature verification passed for both the
  packaged and installed app.
- rekordbox remained open throughout the initial 2026-08-03 installation and
  verification. SoundBox warned that it was running, used its read-only temp
  snapshot normally, and neither stopped nor wrote to rekordbox. The first
  installed launch found and completed 20 background tasks without loading
  tracks: 13 stale waveform records, one grid refresh, and six cue refreshes.
  The recurring 60-second pass then caught one additional live grid change,
  proving the watcher rearmed.
  The final 2026-08-04 wrap-up relaunch scheduled zero analysis/grid/cue tasks
  and zero art fetches.
- `AGENTS.md` and `CLAUDE.md` remain byte-identical and explicitly define
  **wrap up** as the complete validation, documentation, install-if-needed,
  commit/push, and clean-published-state sequence—not an intermediate progress
  report.
- Final pre-install and post-verification `library.json` SHA-256 both equal
  `31b4aeff4e80c7ccfe46767341691166ba2515c4845e4d030731a262e7bc0175`.
  Curated state stayed at 84 direct imports, 1,045 title overrides, 460 artist
  overrides, 10 custom playlists, 512 hidden tracks, and one playlist removal.
- Preserve the one active rollback at
  `/private/tmp/soundbox-install-20260804-m8XQVp/` (preceding app plus
  `library.json.before`, about 235 MB). Superseded install rollbacks were
  removed after validation.

## Track-removal behavior

- Track context menus in both rekordbox and SoundBox playlists expose two clear
  scopes: **Remove from Playlist** affects only that playlist, while **Remove
  from Collection** hides the track from every SoundBox view and export.
- Collection and derived all-track views expose only **Remove from Collection**.
  The confirmation states that rekordbox and source files remain untouched;
  Collection removal is still dated and reversible through the Graveyard.

## Title cleanup and imported duration

- The settings UI now has four intent-based default groups: mixdown markers,
  copy/version tags, BPM/key tags, and render/file tags. The implementation
  covers front, trailing, and bracketed forms within those groups instead of
  exposing overlapping positional rules.
- Plain custom entries are exact units. In particular, the saved
  `(Original Mix)` entry removes only that exact parenthesized trailing tag; it
  does not match bare text, another bracket type, longer contents, or leave an
  empty `()`. Manual title overrides still win. An audit against 3,330 current
  rekordbox playlist-referenced tracks changed 389 unoverridden titles and
  found no empty/mismatched bracket shells or dangling separators; protected
  year, artist-name, key-lookalike, and ordinary-word probes stayed intact.
- Direct imports now probe duration through libsndfile with a read-only Mutagen
  container fallback. The installed app repaired all 81 legacy zero-duration
  records; all 84 imports now have positive duration and size. The 6,839-second
  Dom Dolla set renders as `1:53:59`; hour-plus values use `H:MM:SS`, shorter
  values remain `M:SS`, and canonical persistence remains integer seconds.
- Direct imports are categorically exempt from the rekordbox cue/placeholder
  filter. That preference hides rows only; it never deletes audio or curation.

## Continuous waveform preparation

- Newly detected rekordbox tracks and audio imported directly into SoundBox
  both start the same canonical V5 waveform worker immediately. Fix Missing
  Links also re-enters that path after an imported track moves.
- A single-shot 60-second identity scan rearms only after the preceding worker
  finishes. The scan reads source stats and small manifests, never full
  waveform payloads; size/mtime changes recook only the stale track. Loading a
  track can prioritize it but is no longer required to discover stale analysis.
- The reported failure was reproduced: 23 new WAVs cooked on first detection,
  several source containers changed later (including 14-byte size changes),
  and the old app never rescanned. The installed app automatically found the
  13 remaining stale records. A complete section/hash audit of the 30 newest
  tracks after the pass found 30 ready, zero stale, and zero missing sources.

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
