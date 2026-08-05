# SoundBox current handoff

Updated: 2026-08-04 (America/Chicago)

Read this immediately after `AGENTS.md` / `CLAUDE.md`. This is the single
operational resume note; historical evidence stays in the roadmap/device docs.

## Repository and installed runtime

- Branch `main` contains the completed Rockbox managed-playlist adoption plus
  the physical-test fixes for Rockbox's `/<HDD0>/...` path spelling and the
  drift-tolerant device dashboard. Completion dialogs follow, rather than
  trigger, post-write finalization so OK means Eject-ready. Named bracketed
  transformation credits now consistently outrank original-artist tags; the
  wrap-up tip is published to `origin/main`.
- The freshly signed app from that working tree is installed at
  `/Applications/Soundbox.app`, last observed as PID 43399. Its executable
  SHA-256 matches `dist/Soundbox.app` at
  `78e757bede10ea13d3757b959cbc73fb028e0b5a0f98b2c7606fa527b51916e0`.
  The packaged CoreAudio/Metal deck activated and the live read-only rekordbox
  snapshot loaded 3,414 tracks and 81 playlists.
- Bare `./check.sh` passed on 2026-08-04: pyflakes, native build, mypy, and 761
  tests with four expected skips. `./build_app.sh` passed; strict signature
  verification passed for both the packaged and installed app.
- rekordbox remained open throughout the initial 2026-08-03 installation and
  verification. SoundBox warned that it was running, used its read-only temp
  snapshot normally, and neither stopped nor wrote to rekordbox. The first
  installed launch found and completed 20 background tasks without loading
  tracks: 13 stale waveform records, one grid refresh, and six cue refreshes.
  The recurring 60-second pass then caught one additional live grid change,
  proving the watcher rearmed.
  The final managed-default relaunch scheduled zero analysis/grid/cue tasks and
  zero art fetches. Its stock-artwork scan found all 2,786 unique inputs ready
  (2,770 real cover/avatar files plus 16 defaults) and failed zero.
- `AGENTS.md` and `CLAUDE.md` remain byte-identical and explicitly define
  **wrap up** as the complete validation, documentation, install-if-needed,
  commit/push, and clean-published-state sequence—not an intermediate progress
  report.
- The remixer-precedence install preserved `library.json` byte-for-byte at
  SHA-256 `cd52184419d4d7b9794499bef026dae5b9ebc622e0afbb1a16ef73f8780ff4ca`;
  a parsed comparison excluding normal `settings.columns_state` serialization
  was also identical.
  Current curated state remains 84 direct imports, 1,048 title overrides, 462
  artist overrides, 10 custom playlists containing 682 references, 513 hidden
  tracks, one playlist-removal overlay, and no pending playlist-addition
  overlay. Physical composite generation 50 committed successfully before this
  source-only metadata installation; installation did not touch the iPod.
  The real-cover index stayed byte-identical across installation at SHA-256
  `5628df19b4c549fcc12e35e419da8ed11d30234921451d31f9295a7f2ed44b8b`:
  3,270 images, 131 misses, and 47 sticky blocks. These newer counts reflect
  Sonny's live curation before installation and are authoritative.
- The immediate rollback is `/private/tmp/soundbox-remix-artist-ivY5Qt/`: it
  contains the prior accepted app (executable SHA-256
  `1b380a892c06ec944058095bb9012e0c49be5d1550a9b428304102c1fad19190`)
  and exact pre-install `library.json`. Four older obsolete rollbacks remain
  recoverably in Trash (`Soundbox.app-wrong-heading-20260804` plus the three
  `soundbox-install-*-superseded` directories); permanently deleting them still
  requires Sonny's explicit confirmation.

## Effective remix artist derivation

- An explicit named bracket credit such as `(WHALES REMIX)`, `[Zubah Remix]`,
  or `(KVN FLIP)` now becomes the effective artist before SoundBox considers
  the original-artist tag. This makes tagged edits behave like the already-good
  untagged `Zeds Dead - Collapse (DENNETT remix)` case. A manual SoundBox artist
  override still wins absolutely.
- Bare/generic labels such as `(Remix)`, `(Official Remix)`, `(Radio Edit)`, and
  `(Original Mix)` cannot replace the tag. The existing last-transformation rule
  and registry casing/canonicalization remain intact.
- A read-only audit of the current 3,414-track cache found 93 unoverridden tagged
  edits affected across 40 credited remixers/editors: 46 are WHALES. The exact
  screenshot rows `DON'T CHA`, `ROCK THAT BODY`, and `WHITE WINE & ADDERALL`
  now render as `WHALES` in the installed app's effective-row path.
- Effective artist participates in the composite schema-3 baseline. Generation
  50 therefore remains the physical baseline, and the device may honestly show
  **Sync needed** until these metadata changes are reconciled. No device sync or
  write was performed during this installation.

## Legacy cover audit and prevention

- `NADII - TRIGGER FINGER V2` TrackID `73146859` proved the issue. The legacy
  automatic row used an unrelated `RAP TRIGGER FINGER V2` SoundCloud upload at
  confidence 0.78; Sonny manually replaced it on 2026-08-04 with NADII's real
  track URL and correct cover. That current confidence-1.0 result is protected.
- New unattended discovery now refuses `Unknown Artist`, uses the effective
  artist to orient both `ARTIST - TITLE` and `TITLE - ARTIST`, requires at least
  0.80 confidence, and records `origin=auto`. Manual picker/file/URL/refetch
  writes record `origin=manual`; `blocked` removals remain sticky. Legacy rows
  are deliberately not rewritten or guessed into a provenance class.
- After Sonny finished the six visible rows, the final 2026-08-04 live
  Unknown-Artist audit found 146 tracks: 98 misses, 12 unfetched, 22 sticky
  blocks, eight confidence-1.0 selections treated conservatively as manual,
  and only six legacy automatic images—all six globally hidden/Graveyard.
  There are zero visible Unknown legacy-auto rows left to review.
- The complete hidden legacy-art set is `Nite 'N' Day` (`124593519`),
  `BABYBOY (sonnyINTROEDIT)` (`170102671`), `throw some ds radio edit`
  (`200970449`), obsolete `Gangster Shit (FINAL)` (`227406458`), `Hit the Road
  Jack (Remastered)` (`232031710`), and `BEAUTIFUL RIDDIM` (`236575337`). The
  current visible `ZYKO - Gangster Shit (FINAL v5)` (`234007176`) is a distinct
  source file and retains Sonny's manual confidence-1.0 ZYKO profile picture.
- A broader read-only audit restricted to legacy confidence-below-1.0 track
  URLs found 205 source/title rows with no significant song-token overlap.
  This is a review pool, not 205 proven errors: visual inspection confirmed
  multiple obvious wrong-track clusters (NADII, UNOWN, WODD, JISATSU, PSYCHE)
  and one-offs, while also proving that many rows are legitimate opaque slugs,
  compilations, or remixes. No cache row or JPEG was changed by the audit.
- Unknown legacy-auto review is complete. Any later broader review must use only
  high-certainty visual mismatches; never review hidden tracks or bulk-clear the
  whole 205-row pool. Back up the full art cache before accepted cleanup and
  preserve every block/manual row exactly.

## Managed default photos

- Settings → Cover Art includes **Default photos → Manage photos…**.
  It accepts multiple local JPG/JPEG/PNG/WebP/BMP files, copies each unique
  image into `art/defaults/`, and normalizes it to a capped baseline JPEG. The
  source file can move afterward. The catalog shows 96 px previews, source
  names, live assignment counts, multi-select deletion, and an explicit
  confirmation that only managed copies are removed.
- Successful real covers and artist-profile avatars always win and are never
  rewritten. Naturally missing/unfetched tracks and deliberately `blocked`
  tracks receive defaults; their underlying index state remains unchanged, so
  real discovery can still improve a miss while auto-fetch continues to skip a
  block. The Art column uses `◆` for this distinct fallback layer.
- The pool manifest persists a random seed and per-track assignments outside
  `art/index.json`. Assignments are exact-balanced (counts differ by at most
  one), stable across relaunch, and minimal-churn: pool/library changes preserve
  valid mappings and move only the number needed to rebalance. Hidden tracks are
  excluded. The full backup/restore now handles nested pool files.
- `ArtCache.image_path()` is the shared consumer boundary, so the same managed
  baseline JPEG enters existing content-addressed MA446 RGB565 preparation and
  the existing per-track NightPod/Rockbox 160×160, 116×116, active-theme, and
  dimensionless BMP paths. No alternate device-art implementation was added.
- Live state has 16 managed 500×500 baseline RGB JPEGs and 136 assignments:
  41 deliberate blocks, 86 natural misses, and nine unfetched tracks. Every
  eligible visible track has exactly one assignment, each photo is used by
  eight or nine tracks, and zero real cover/avatar or Graveyard tracks are in
  the assignment map. The pool manifest stayed byte-identical across the final
  reinstall at SHA-256
  `23e52d6d03497413580672e7ed4c1591eaa310916ffd7b9fc4e2b7791ce2853a`.
  A byte-level audit verified all managed filename/content hashes, both MA446
  RGB565 formats, both 160×160 and 116×116 NightPod BMPs, and every warmed stock
  derivative cache entry.

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
- A managed top-level Rockbox M3U8 whose size changes now surfaces ordinary
  **Sync needed**. Confirmed Sync accepts only an exact committed prefix plus
  appended schema-3-owned media paths. It adopts each genuinely new recognized
  track into the SoundBox playlist overlay once, persists that state before the
  device transaction, and reconciles the Rockbox playlist, stock playlist, and
  unified manifest without copying media again. Appended duplicates of an
  existing member are removed; intentional duplicates already present in the
  committed prefix remain intact. Unknown/unowned paths are not imported and
  fail closed without a device write. Device-side deletion, reorder, or edits
  to the committed prefix also fail closed. Rockbox's observed primary-volume
  `/<HDD0>/...` spelling is normalized before ownership lookup; no other volume
  token is aliased. A changed M3U8 no longer hides separately valid device
  identity/database details: only that playlist is labeled Sync needed, and
  its live entries remain untrusted until confirmed planning.
- Successful stock/composite Sync and Delete—including queued composite jobs—
  now show `Finalizing iPod...` while the committed Device/Songs/Playlists
  snapshot and sidebar free-space/drift scan finish. Only then does the success
  dialog appear; dismissing it starts no hidden device worker and Eject is
  immediately available.
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
- Physical append acceptance passed: the first Rockbox-USB commit attempt lost
  transport during manifest preflight before any write, then the stock-boot
  retry recognized the `/<HDD0>/...` entry and committed composite generation
  50 successfully. A later routine duplicate append should still physically
  confirm that only the new duplicate is removed; fixture coverage already
  proves that rule.
- Rockbox Track ReplayGain remains intentionally unimplemented. Rockbox reads
  gain/peak tags from MP3 and other tagged containers but its current WAV
  metadata parser does not expose ReplayGain; an MP3-only SoundBox feature was
  rejected because most of the live library is WAV. `docs/ROADMAP.md` now keeps
  the complete future path: upstream-first WAV parser support, independently
  validated ReplayGain 2.0 / ITU-R BS.1770 gated loudness and true peak,
  disposable-payload-only tags, no transcoding/duplication, and physical proof
  in both Rockbox and stock firmware before enablement.
- Other iPod models/checksum families and schema-2 ownership migration remain
  separately hardware-gated and fail closed.
- If macOS resets the USB transport, preserve
  `~/Library/Logs/Soundbox/soundbox.log`, wait for a clean remount, and requeue
  through SoundBox. Never inspect, mount, eject, or mutate the physical iPod
  from development shell commands.
