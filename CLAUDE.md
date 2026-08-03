# SoundBox — orientation (start here)

SoundBox is a native macOS music-library app that bridges **rekordbox → stock
or Rockbox iPods / USB players** and includes a high-refresh DJ-style player.
Browse the live rekordbox library, curate it without ever touching it, and drag
playlists onto a device with exact order, per-track cover art, and honest sync
semantics. Built by Sonny (Low Freq Labs) with Claude and Codex.

## Runtime instruction parity

`AGENTS.md` and `CLAUDE.md` are runtime equivalents and intentionally contain
the same shared project guidance. Whenever shared instructions, safety rules,
documentation routes, build steps, or handoff facts change, update **both files
in the same change**. Runtime-specific guidance must be clearly labeled and
must not silently contradict the other file.

At the start of every session, read this file and `docs/HANDOFF.md` before the
relevant documents below. Do not rely on a prior agent's memory when the
repository can answer the question.

## Sacred safety contract (NON-NEGOTIABLE)

- rekordbox is **never** written to. `master.db` is read via a read-only temp
  snapshot (rekordbox can stay open); the XML export is a fallback.
- Rekordbox analysis (`ANLZ*.DAT/EXT/2EX`) is read-only. SoundBox-derived
  waveform/grid caches live only under SoundBox AppData.
- Source audio files are **never** modified, renamed, moved, or deleted.
- Every export write lands **under an explicit device target**, guarded so the
  target can never overlap the source library.
- `soundbox.stock_ipod` is the only stock-firmware path allowed below
  `iPod_Control`. It is MA446/MP3/WAV-only until separately expanded, uses the
  pinned Clickwheel database engine only as an offline renderer, validates the
  rendered database and every artwork link, saves recovery material, copies
  media/artwork safely, and commits `iTunesDB` last. A disposable Mac-side
  `stock_ipod_state/` shadow may accelerate only an exact matching library
  identity/database/artwork baseline; mismatch always falls back to full
  reconciliation. A valid shadow can contain only playlist-scoped source
  mappings; missing selected sources must be narrowly content-reconciled against
  live media, never assumed new. Curated baseline JPEGs remain canonical;
  `stock_artwork/` under AppData is a disposable, content-addressed MA446 RGB565
  derivative cache. Incremental art writes may touch only currently
  unreferenced fixed-size ithmb slots; pixel-identical free slots are relinked
  without a write before publishing ArtworkDB and iTunesDB. Never embed art into
  or otherwise retag source audio. Rockbox export may remove embedded pictures
  only from a verified disposable Mac-side payload. Composite payloads also
  receive the effective SoundBox title/artist so Rockbox never falls back to a
  scrambled stock filename. WAV copies use Rockbox's native RIFF
  `LIST/INFO` `INAM`/`IART` fields rather than ID3-in-WAV. Those display fields
  live in a small canonical INFO list immediately after `fmt`; unrelated source
  INFO fields are preserved in separate lists so large vendor metadata cannot
  hide the artist/title from Rockbox's bounded scan. Effective
  title/artist is part of the composite playlist baseline, so either edit must
  surface Sync needed. The container audio essence and technical
  properties must remain identical, and the canonical source remains
  byte-identical. Full-library planning must never retain the complete set of
  transformed audio: it records an exact source/output hash contract and
  discards each derivative immediately. Commit rematerializes, verifies, copies,
  and discards one track at a time so Mac-side staging is bounded by the largest
  individual payload. Managed
  Rockbox covers are per-track uncompressed 8-bit paletted BMP derivatives:
  exact-size 160×160 and 116×116 fast paths, bounded exact square sizes read
  from the active WPS/SBS and retained across later theme changes, plus a
  lightweight dimensionless 160×160 fallback. The curated JPEG cache remains
  canonical and stock artwork is untouched. On a composite device, SoundBox may manage only
  `.rockbox/config.cfg` values `tagcache_autoupdate: on`,
  `playlist viewer track display: title from tags`, `sort playlists: alpha`,
  and a minimum `max files in playlist: 10000`, preserving a larger user limit,
  every unrelated line, and the file's existing newline style. That write is
  baseline-verified, recovery-backed, and atomic. Rockbox
  remains the only writer of `database_*.tcd`; SoundBox never manufactures or
  directly edits the binary tag cache. SoundBox may report that the index
  predates its latest manifest/config generation so a required Rockbox restart
  is not mistaken for a clean cache. The connected layout selects the exporter:
  stock-only uses the guarded stock planner; Rockbox-only/generic FAT32 uses the
  Rockbox filesystem planner; a FAT32 MA446 containing both `.rockbox/` and
  `iPod_Control/` uses only `soundbox.dual_ipod`, never two exporters. The
  first composite generation requires no existing SoundBox device manifest,
  builds one no-target-write plan, asks before commit, and is single-copy:
  stock-safe verified payloads live under `iPod_Control/Music`, enter iTunesDB,
  and are referenced by Rockbox playlists; stock-unsafe Rockbox-supported files
  live only under `/Music`; unsupported-by-both media is omitted and reported.
  Distinct canonical source paths retain distinct iTunesDB records, metadata,
  artwork, and device paths even when their audio bytes match; repeated playlist
  references to the same source path share its one record.
  For MA446, stock-safe means validated MP3 or stereo PCM16/44.1-kHz WAV; that
  restriction applies only to the iTunesDB-visible subset. Rockbox external art
  for every audio file is its sibling `filename.160x160.bmp` and
  `filename.116x116.bmp`, plus theme-independent `filename.bmp`, preventing
  folder-level art collisions. Rockbox files land
  before the recovery-first stock transaction, playlists follow the validated
  stock commit, and the unified schema-3 ownership manifest publishes last; any
  later-phase failure coordinates stock rollback with restoration of managed
  Rockbox rewrites and removal of only new files. For schema-3 managed audio,
  planning validates ownership/path/size without streaming every old device
  payload; the exact desired derivative is verified on the Mac, the old live
  file is renamed within the device only for transaction rollback, and the
  replacement is atomically copied and verified one track at a time. Successful
  commits discard those transient old managed-audio bytes. Unowned paths remain
  untouched except for interrupted-generation recovery: a recovery directory
  bound to the still-current manifest may authorize adoption only when the
  freshly planned path, size, and SHA-256 match exactly. Completed proofs are
  journaled in disposable Mac-side state so verification itself resumes after
  another remount. Later playlist drops accumulate through new schema-3 generations,
  reuse exact managed payloads, source-authoritative repairs, and become true
  no-ops when unchanged. Generic Sync
  must route schema 3 back through that same composite planner: stable playlist
  IDs drive rename/move/order/membership/removal drift, old Rockbox playlist
  files are deleted only with exact recovery, and matching iTunesDB playlists
  reconcile in the same transaction. Sync reference-counts the complete managed
  playlist set: a track removed from SoundBox is pruned only when no remaining
  playlist uses it. Schema-3 media cleanup candidates require manifest
  ownership/path/size; art and playlists being rewritten or deleted retain
  exact live-byte checks, while unchanged retained artwork may reuse its
  matching manifest/cache identity without rereading device bytes. Each
  orphan is renamed into transient same-volume transaction recovery before the
  manifest publishes last, restored byte-for-byte if any later phase fails,
  and discarded only after success. Cleanup never depends on regenerating an
  older media or artwork profile from its source. Shared stock
  records additionally survive any remaining ordinary stock playlist reference.
  Finder-created `._*` AppleDouble files and abandoned `*.sbpart` temporaries
  below `/Music` or `/Playlists` are exact-baseline cleanup rows in that same
  confirmed transaction; successful cleanup also removes only now-empty child
  directories. Finder sidecars are opportunistic cleanup and never independently
  signal drift because macOS may recreate them immediately; an abandoned
  `*.sbpart` is an interrupted SoundBox write and does require reconciliation.
  An empty schema-3 library remains plannable so cleanup can never become
  unreachable after the last playlist is deleted.
  Schema-2 migration is not enabled. The old Rockbox exporter must still fail
  closed on schema 3. Never approximate dual boot with independent exporters or
  a duplicate collection.
  Rockbox's Playlist Catalogue sees only direct playlist files under
  `/Playlists` and sorts them alphabetically. Composite sync must therefore keep
  every managed M3U8 at that top level and prefix only its Rockbox filename with
  a zero-padded manifest sequence (`01 - NAME.m3u8`). SoundBox rows and stock
  iTunesDB titles use the concise playlist leaf, adding folder context only to
  disambiguate duplicate leaves; the manifest alone retains the full hierarchy.
- Stock playlist deletion may remove regular user playlists and the exact Apple
  **Videos** smart index only. Apple databases may mirror that index in the
  ordinary MHLP and canonical MHSD-5 datasets under different IDs; the device
  UI must select the canonical ID and deletion must remove both matching
  representations as one zero-media operation. Music, Movies, TV Shows, and
  Audiobooks remain immutable. On-The-Go is firmware-generated, not an iTunesDB
  playlist. Before a dual device has schema-3 ownership, the same guarded UI may
  delete those stock playlists with the same reference-counted cleanup: each
  track record unused by every remaining user playlist is removed from iTunesDB,
  and its linked device media is deleted only after the database commit. Source
  audio remains immutable; unowned Rockbox paths remain outside that transaction.
- `soundbox.stock_ipod_info` may parse stock identity, tracks, playlists, and
  playback state for the device UI, but is strictly read-only. Remembered model
  confirmation is keyed by library persistent ID + volume UUID; the ordinary
  iTunesDB `DatabaseID` is rewrite-volatile and must never identify hardware.
  On macOS, hardware serial/firmware may come from Apple's read-only classic-iPod
  device record and USB/SCSI facts from IORegistry. Serial-derived model and
  model-derived DAC must retain provenance in the immutable snapshot, though the
  dashboard may show the concise component name; a SCSI transport revision is
  never firmware. Ambiguous device records and model mappings fail closed.
- Dry-run by default where the workflow supports it; ask before anything
  irreversible; never bulk-delete or orphan-clean without an explicit target
  and confirmation. Do not assume paths—resolve and verify them.
- `library.json` is crown-jewel user state. Never wipe AppData or trigger a
  full cache rebuild casually. Back it up before installation or risky state
  work and verify curated data afterward.
- Full per-write export contract lives at the top of `soundbox/sync.py`.

## Documentation map

| Doc | What's in it |
|-----|--------------|
| `docs/HANDOFF.md` | Single current session-resume note: live state, unfinished work, exact next checks, validation, and rollback facts |
| `docs/ROADMAP.md` | Current status, committed next work, phases, product decisions, and CDJ frontier |
| `docs/PLAYER.md` | Player behavior, native architecture, cache formats, cue/transport/render invariants, QA references, and pending grid work |
| `docs/DEVICES.md` | Rockbox/stock/dual-boot/generic layouts, capability boundaries, and validation |
| `docs/ARCHITECTURE.md` | Engine/desktop/native layout, data model, persistence, build/install |
| `docs/GOTCHAS.md` | Hard-won Qt/QThread/native-player/rekordbox/Rockbox traps |
| `new-ideas-7-22.md` | Exploratory quality-of-life concepts that require UX workshop before commitment |
| `docs/cdj-usb-export.md` | CDJ USB R&D and `research/cdj_export/` status |
| `docs/storage-audit-2026-07.md` | Resolved storage audit; full backups remain deliberate |
| `README.md` | User/dev product tour |

Read `docs/GOTCHAS.md` before touching workers, playlists, sync, removable
devices, caching, or the player. Read `docs/PLAYER.md` before changing waveform
rendering, clocks, transport behavior, analysis loading, or native code.

## Documentation discipline

- Documentation is part of the change. Update relevant docs before ending a
  session when behavior, architecture, invariants, setup, or status changed.
- Put committed/sequenced work in `docs/ROADMAP.md`. Put uncommitted concepts
  and UX explorations in `new-ideas-7-22.md`.
- Keep shipped behavior, future work, and historical validation clearly
  separated. Never leave a completed feature presented as a TODO.
- On every **wrap up**, replace/update `docs/HANDOFF.md` with the single current
  handoff. Keep it concise and operational rather than appending a session
  diary: current branch/commit, installed/runtime state, unfinished work,
  exact next checks, validation performed, and any rollback that must remain.
  Remove facts that are no longer current.
- **“Wrap up” is a completion command, not a progress report.** Finish every
  in-scope pending task that can be completed in the current session, update all
  required documentation, run the required final validation, and—when the work
  is user-facing—build, safely install, relaunch, and verify the real curated
  runtime state. Then update `docs/HANDOFF.md`, commit and push the intended
  completed changes, and verify the worktree is clean and the current branch is
  published before saying the wrap-up is complete. Do not defer any of these
  steps for Sonny to request again. If a genuine blocker prevents completion,
  report the exact blocker and leave an operational handoff instead of claiming
  the session is wrapped.
- Do not hard-code a "current" test count as evergreen truth; trust
  `./check.sh`. Dated validation records may include the observed count.

## Development

    ./check.sh            # native build + pyflakes + mypy(engine) + full suite
    ./check.sh --render   # + hermetic offscreen UI render
    ./build_native.sh     # arm64 CoreAudio/Metal dylib only
    ./build_app.sh        # PyInstaller → dist/Soundbox.app, then stable signing

- Run `./check.sh` **bare**—never pipe it through `tail`/`grep` in an `&&`
  chain because that can mask a failing gate.
- Python 3.14, pinned dependencies in `requirements.txt`, venv at `.venv/`.
- Native deck requires arm64 macOS 14+. Qt Multimedia is analysis-only
  (`QAudioDecoder` for AAC/ALAC); native-deck failure disables playback with a
  diagnostic while library management remains available.
- Logs: `~/Library/Logs/Soundbox/soundbox.log`.
- Git uses local `main`, tracking `origin/main` on the Low Freq Labs GitHub
  repository. Inspect and preserve the dirty worktree. Do not commit or push
  unless Sonny explicitly asks. Sonny saying **“wrap up”** is standing explicit
  permission to run the final validation, commit the intended completed
  worktree changes, and push the current branch; never push a failing build.

## Build/install handoff

For user-facing app changes, a passing source tree is not the handoff.
“Completed” means the tested app has been built, safely installed, relaunched,
and verified against the real curated state unless Sonny explicitly asks for
source-only work. Keep rollback proportional: use one temporary rollback
directory for the install, not a new trail of persistent backups.

1. Run `./check.sh` bare, then `./build_app.sh`.
2. Back up and hash SoundBox `library.json`; record cache counts when relevant.
3. Quit SoundBox gracefully and verify its process exited.
4. Move the old `/Applications/Soundbox.app` to a temporary rollback directory;
   install with `ditto`. **Never use `rm -rf` for app installation.**
5. Verify the signature, relaunch, and confirm the native-deck startup log,
   library load, zero unnecessary preparation, and curated-state integrity.

Moving/replacing the `.app` bundle must never touch
`~/Library/Application Support/LowFreqLabs/Soundbox/`.

## Private / out of scope

- The personal liveset/SoundCloud download pipeline is private and must never be
  documented or inferred in this repository.
- CDJ reference USB snapshots and large audio fixtures live in the local,
  git-ignored `soundbox-cdj-ref/` workspace at the project root; only prototype
  code under `research/cdj_export/` belongs in version control.
- Stock/Rockbox iPod captures and oracle-sync media live in the local,
  git-ignored `soundbox-ipod-ref/` workspace. Treat pristine captures as
  read-only research/recovery fixtures; never restore one to hardware without
  an explicit, separately reviewed recovery plan.
