# SoundBox roadmap

Living to-do. Work top-down. Rule of the house: **don't over-polish before the
iPod validates the device side** (~end July 2026). For the live test count,
trust `./check.sh` output — not any number written here.

## Current status (2026-07-08)

- **SOFTWARE COMPLETE + CURATION MONTH COMPLETE.** Every track title cut/cleaned,
  every cover art eye-verified. Engine is mypy-clean, ~317 tests, `./check.sh` is
  the one-command gate, `dist/Soundbox.app` signed + current.
- **WAITING ON HARDWARE** for Phase 3 (iPod, ~end July).
- New frontier (2026-07-08): **CDJ USB export** → `docs/cdj-usb-export.md`.

## Scorecard (~9.4/10, honest, not curved)

Everything software-reachable is maxed; the residue is hardware-gated (iPod) or
phase-gated (rekordbox 7 / Windows / notarization).

| Area | Score | Note |
|------|-------|------|
| Export (copy engine) | 9 | atomic writes, free-space precheck, dedup, FAT-safe, guarded, cancel; last point is USB physics |
| Sync / re-sync | 9.5 | diff-driven, prune-only-when-needed, shared-file safe, USB-validated; cancel leaves usable+resumable state |
| Cover art | 8.5 | accuracy-first auto (0 wrong art), manual picker, derivation; ceiling = SoundCloud search + unreleased tracks with no art anywhere |
| Device detect & safety | 9.5 | off-thread scan, hardened guards, FAT32 warning |
| Live rekordbox read | 9 | read-only temp copy, sacred order, smart playlists; validate vs rb7 in Windows phase |
| UI/UX | 9.5 | sort, Collection + Artists views, art picker, graveyard, title cleanup, drag-scrub, now-playing pill |
| Persistence | 10 | portable library.json by track ID + rotating backups + one-click full backup/restore |
| Tests | 10 | engine + hermetic UI + property-based (hypothesis) + live-DB mocks; `./check.sh` |
| Packaging/signing | 8 | stable self-signed; notarization for public (Phase 5) |
| iPod readiness | 7 | software side done; the rest is genuinely hardware-gated |
| Code cleanliness | 9 | monolith split into engine + desktop mixins; engine mypy-clean |

## Phases

### Phase 1 — Re-sync engine ✅ DONE (validated on USB)
Playlist `pid` on the model; per-device `manifest.json`; drift diff
(renamed/moved/changed/removed); `sync.resync` (idempotent re-export, stale
m3u8 cleanup, track-folder-level prune that keeps shared files, forget deleted);
drift UI ("⚠ N out of date" badge → Sync). Sonny's rename-v#/move workflow
"just works" because pid is stable.

### Phase 2 — Settings / polish ✅ DONE
Scoped FINALIZING (clean only written dirs); diff-driven resync; configurable
cue/placeholder hiding; cover-art workshop (accuracy-first matcher, ART column,
manual picker, artist/title derivation incl. trusted-gated reversed flip,
filename-stem fallback, alias/merge window, uploader-consensus suggester);
app.py split into modules; UI-layer tests; rotating + full .zip backup/restore;
graveyard; title-cleanup named rules; baseline-JPEG art enforcement.

### Phase 3 — iPod validation ⬜ (hardware-gated, ~end July 2026)
Rockbox iPod ≈ a USB stick; getting music written + readable is 80% done. To
validate on-device:
- [ ] Music writes to + reads from the real Rockbox iPod.
- [ ] m3u8 works on-device (drive-root-absolute paths; expect maybe a tweak).
- [ ] `cover.jpg`-per-folder renders (baseline-JPEG already enforced + cache
      migrated — pure on-device check; theme/WPS must support album art).
- [ ] iPod is FAT32 (Rockbox does NOT support HFS+ — restore if needed).
- [ ] Rockbox DB refresh workflow after dumping files.
- [ ] Exact playlist order + folder nesting hold.
- [ ] Scale test (~2900 tracks) — no choke.

**Manual-sourced facts** (official Rockbox manuals): baseline-JPEG required
(no progressive/multi-scan/RLE); art search order prefers EMBEDDED over
`cover.jpg` (accepted — see decisions); Max Playlist Size default 10k; on-device
perf ritual = Load to RAM + Directory Cache + DB init; Playlist Catalogue dir =
`/Playlists`.

### Phase 4 — Windows port ⬜ (a real port, not a recompile)
- [ ] `pyrekordbox` vs **rekordbox 7** (Sonny runs 6.8.5 on Mac; validate v7 on PC).
- [ ] Windows rekordbox DB path.
- [ ] Replace `diskutil` + `/Volumes/` detection/guards with drive-letter logic.
- [ ] Eject on Windows.
- [ ] Windows PyInstaller `.exe`.

### Phase 5 — Public fork ⬜ (gated on iPod validation)
- [ ] Public build distinct from Sonny's personal build.
- [ ] Do NOT bundle yt-dlp — detect a user-installed one; opt-in, no silent scrape.
- [ ] Embedded-art reader (MP3/AIFF/FLAC/M4A) — 100% clean, ships to everyone.
- [ ] `features` flag + `build_app.sh --public`.
- [ ] Distinguish iPod/MP3 device vs plain USB; block plain USBs.
- [ ] Code-sign + notarize.

### Frontier — CDJ USB export
Reverse-engineer rekordbox's `export.pdb` so SoundBox can write CDJ-readable
USB sticks (Sonny performs on 4× CDJ-3000). Full plan + status + prototype:
**`docs/cdj-usb-export.md`**.

### Someday / v2
- [ ] Native Apple/iTunesDB iPod support ("nerd toggle").

## Product decisions (don't re-litigate)

- **Embedded MP3/M4A art MAY win over `cover.jpg`** — accepted by design; don't
  nag. `cover.jpg` is the universal floor.
- **Personal-edit tags are NOT stripped** from search — a coverless own-edit is
  Sonny's visual cue for cleanup.
- **Two-plane deletes:** Delete-from-Collection (global hide) vs
  Remove-from-Playlist (per-pid veto). Both drift devices; rekordbox never changes.
- **Unknowns-suggester rows UNCHECKED by default** (streaming list must never arm
  an accidental accept); merge-dialog rows stay pre-checked.
- **Imported audio referenced in place** — never copied or re-tagged.
- **Device filenames = SoundBox effective titles**, always.
