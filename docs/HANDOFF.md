# SoundBox current handoff

Updated: 2026-08-03 (America/Chicago)

Read this immediately after `AGENTS.md` / `CLAUDE.md`. This is the single
current resume note, not a session diary.

## Repository and runtime

- Branch `main`, tracking `origin/main`; `bc79dd0` was the pre-session baseline.
  This handoff accompanies the wrapped dual-iPod implementation/UI commit, with
  no intentional residual worktree changes.
- The installed source baseline passed bare `./check.sh`: 721 tests with four expected
  skips, plus pyflakes, native build, and mypy. `./build_app.sh` passed and the
  signature verified.
- The fixed app is installed at `/Applications/Soundbox.app`, PID 53699. The
  native CoreAudio/Metal deck is active; startup loaded 3,263 tracks / 83
  playlists, scheduled zero library-preparation work, and verified 2,625 stock
  artwork derivatives with zero failures.
- The latest pre-quit `library.json` SHA-256 was
  `6b55483ae55a9d98b2d42956281a077ed6513820d0238fd10186394c3a5c40be`;
  the post-quit/relaunch hash is
  `2171dfee742db24877d06fb5282052dcf164f58ee8332aae81a94f5a46c29fcf`.
  Sorted JSON comparison after excluding Qt's normal `columns_state` save found
  every curated field identical.
- The one active rollback is
  `/private/tmp/soundbox-install-20260803-72RwQm/`; it contains the immediately
  preceding installed app and `library.json.before`. The superseded rollback
  was deleted after signature/startup validation, reclaiming about 235 MB while
  preserving exactly one 235-MB rollback.

## Newly installed order/delete follow-up

- A physical Apply order → Delete attempt was falsely refused because deletion
  compared schema-3's device-only `device_name` field as if it were canonical
  playlist state. Retained validation now requires every canonical source-row
  field while ignoring extra presentation fields.
- A Sync button is always visible in the device header, and the context-menu
  manual recheck remains enabled even when the latest drift count is zero.
- The exact reorder-then-delete regression passes. Bare `./check.sh` passes 721
  tests with four expected skips. The signed build is installed and its startup,
  native deck, library state, zero preparation work, and 2,625 artwork
  derivatives were verified. The iPod was not touched from Terminal.

## Newly installed Rockbox order follow-up

- Physical validation proved the stock iTunesDB follows the applied manifest
  order but Rockbox still presents the Playlist Catalogue alphabetically.
- Source now uses alphabetic Rockbox catalogue sorting plus zero-padded,
  Rockbox-only M3U8 filenames (`01 - NAME.m3u8`). Stock playlist titles and
  SoundBox device rows remain clean; reorder renames only owned playlist files.
- Focused dual/config tests and bare `./check.sh` (721 tests, four expected
  skips) pass. The signed build is installed with native deck, curated state,
  zero preparation work, and 2,625 artwork derivatives verified. Physical
  acceptance now confirms Rockbox displays the zero-padded sequence.

## Newly installed authoritative deletion follow-up

- An explicit checked playlist deletion no longer refuses because another
  managed playlist changed or disappeared. It reconciles available playlist
  updates in the same transaction and reconstructs unavailable unrelated
  playlists from their committed snapshots so they are retained.
- A same-profile regression proves normal Sync migrates an unnumbered Rockbox
  playlist to its numbered catalogue filename. Successful dual commits now log
  the exact published Rockbox playlist paths for physical verification.
- Bare `./check.sh` passes 721 tests with four expected skips. The signed build
  is installed; startup loaded 3,263 tracks / 83 playlists, scheduled zero
  preparation work, and completed 2,625 stock-art derivatives with zero failures.

## Latest Error 6 incident

- During a seven-playlist export, macOS returned `Errno 6` while the background
  no-write planner hashed an existing stock media file in
  `soundbox.stock_ipod._sha256`; no commit had begun and no device file was
  intentionally changed.
- The Mac-side stock shadow's last successful committed generation is 29 with
  1,745 stock sources and these seven managed playlists: Collection (2,744),
  SONNY.AIF (38), DUBSTEP (572), TEAROUT (40), HOUSE (8), BROSTEP (20), and
  DEEP DUB (557). The failed batch did not publish a later manifest generation.
- No terminal command mounted, ejected, read, or wrote the physical iPod.

## Newly installed behavior

- Additive stock planning excludes sources already proven by the composite
  source→DBID/path/payload manifest from live content reconciliation. New or
  otherwise unproven sources retain the narrow size/suffix/hash adoption path.
  The regression test fails if an additive playlist re-hashes proven stock
  media.
- Playlists confirmed while another dual job is busy are coalesced into one
  next guarded plan/commit batch. Drops arriving after that batch starts form
  the following batch; per-drop confirmation and failure cancellation remain.
- Device-facing playlist names use the rekordbox leaf (`DUBSTEP`) on both stock
  and Rockbox. Folder context is added only when duplicate leaves require it;
  the full hierarchy remains in the ownership manifest.
- The Device Playlists page provides Move up, Move down, and Apply order. Apply
  publishes clean stock iTunesDB titles and numbered Rockbox-only M3U8 files in
  the same manifest sequence without duplicating media.

## Remaining physical acceptance

- No check blocks normal use. Stock firmware follows the applied manifest order,
  and Rockbox now visibly follows the numbered M3U8 sequence after sync/restart.
- On a future routine deletion, confirm the newly installed authoritative path
  proceeds directly without the old Sync-first refusal. If another macOS USB
  reset occurs, preserve the app log and retry only after a clean remount.
