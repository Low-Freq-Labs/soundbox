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
  `playlist viewer track display: title from tags`,
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
  no-ops when unchanged. Generic Sync also recognizes append-only edits that
  Rockbox made to an owned M3U8. The frequent device poll uses only the bounded
  playlist-size baseline to surface the standard Sync-needed state; confirmed
  planning reads the changed small M3U8 and accepts only device paths that map
  unambiguously to existing schema-3 managed media. A genuinely new membership
  is persisted only into a SoundBox-owned custom playlist, then reconciled
  into both firmware views. Rekordbox playlists do not adopt iPod additions;
  rekordbox remains read-only. Exact hash-verified historical M3U8 prefixes
  bind device paths to stable track IDs even after local source removal or
  relocation. The planning view may rebind those proven IDs to their current
  canonical Mac sources while retaining original device ownership/recovery
  baselines. Reusing a moved source's derivative requires matching source
  SHA-256; a metadata change during cloud hydration permits one full re-hash
  against a new stable stat baseline, never acceptance of the old digest with
  unverified new timestamps. Repeated change fails closed. Completed source
  proofs may persist only in disposable SoundBox state and are reusable only
  for the same canonical path, filesystem/inode identity, size, mtime, and
  ctime. Timestamp-only drift at commit preflight requires exact source SHA-256
  equality against a stable baseline before refreshing the plan; different
  bytes or continuing changes stop before device writes. Unchanged verified
  stock payloads may reuse their manifest-proven compatibility, while DBID,
  device path, and live-size ownership checks still run. Changed bytes use
  normal verified replacement. Multiple old copies
  may converge only when their recorded verified source hashes are identical
  and their current canonical source is one file; surplus copies remain under
  ordinary reference-counted cleanup. Custom playlist reconciliation retains the first occurrence of
  each track, canonical source, or byte-identical source file, including older
  duplicates; distinct recordings are never merged by title/artist. Membership
  is baseline-checked and saved before device writes, and M3U8 changes are
  recovery-backed. Unknown/unowned paths are never imported
  automatically, and deletion/reorder edits fail closed. The proven app-side
  membership is saved before device writes so a failed device phase remains an
  honest retry. Generic Sync
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
  The top-level `/PIONEER` tree is reserved for rekordbox/CDJ. SoundBox may
  include its bytes only in whole-volume capacity, but must never use it for
  drift, claim it in ownership, verify it, clean it, repair it, import from it,
  edit it, delete it, or write below it. Case-insensitive spellings and symlink
  redirects fail closed. The separately confirmed generic-volume whole-device
  Erase is the sole exception; stock and dual iPods never expose that command.
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
- Device activity and library integrity are separate. An idle device may
  always request normal macOS eject without Sync, Verify, or Deep Verify.
  Never force-unmount automatically. Device worker ownership lasts through
  QThread.finished; cancellation clears pending work and stops active work at
  a safe boundary. Shutdown must keep Qt events alive until workers stop;
  an expired timed wait never permits destroying a worker.
- Canonical audio acquisition captures one local snapshot while hashing. An
  expected digest binds captured bytes, so matching bytes survive cloud
  eviction/timestamp drift. Without a digest, require a stable capture with at
  most one hydration retry. Inspection/transformation/copy use local captured
  bytes; retain at most one source and working derivative. Disposable receipts
  and source proofs never confer device ownership. Preserve generation-58
  recovery until separately reviewed hardware acceptance succeeds.
- `library.json` is crown-jewel user state. Never wipe AppData or trigger a
  full cache rebuild casually. Back it up before installation or risky state
  work and verify curated data afterward.
- Full per-write export contract lives at the top of `soundbox/sync.py`.


Missing manifest-owned Rockbox audio and per-track artwork are planned as verified
new copies when the guarded destination is absent. Existing occupants, symlinks,
and inaccessible paths are not treated as missing; commit rechecks vacancy and
rollback removes only files created by that transaction. A fully reconciled stock
library with no semantic changes retains its original iTunesDB bytes even when
the disposable stock shadow is absent.


The bounded composite drift poll treats source size/mtime changes as Sync-needed
hints, but not ctime alone: cloud eviction/hydration and filesystem metadata edits
can change ctime immediately after a successful commit. This UI status is not a
content-integrity certificate. Source proofs/receipts still include ctime, and
explicit planning/acquisition retain exact-byte verification; no stored proof or
manifest baseline is refreshed merely to clear the badge.

Connection presentation separates visibility from readiness. A debounced
`/Volumes` notification requests discovery even during playback or an eject-ready
hold. If a device worker owns the lane, the request stays pending for retry.
A three-second timer checks the local `/Volumes` directory timestamp to recover
missed events; it does not open mounted children merely to check that timestamp.
Verified external mounts appear as disabled loading rows during discovery.
At QThread.finished they become selectable, while stock/dual dashboard loading
continues with a visible loading label. A cached dashboard is presentation-only;
selecting a loading row uses the same serialized preload. Writes stay guarded by
operation ownership and firmware capabilities. Remounts invalidate stale views.


Playback and iPod management are independent. Device discovery and ordinary
status polling must not check player.is_playing as a suppression condition.
Only device operation ownership pauses competing device reads/writes. Reconnect,
Rockbox tag-cache updates, unrelated firmware preferences and ctime-only cloud
metadata changes do not themselves create managed-library Sync drift.

Rockbox 4.0 does not support `sort playlists`; never add or require that key.
Its absence after firmware saves preferences is not Sync drift. Preserve existing
unknown config lines; retain the established numbered playlist filenames.

## Documentation map

| Doc | What's in it |
|-----|--------------|
| `docs/ipod-lifecycle-audit-2026-09.md` | Lifecycle inventory, failure matrix, implementation and acceptance boundaries |
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

Artwork worker ownership also lasts through delivery of its `finished` callback.
Completion retires the captured instance, never whatever currently occupies a
worker attribute; shutdown callbacks cannot start replacement artwork passes.

Dashboard serial and stock-firmware facts may persist under the exact volume UUID
plus library persistent ID, with provenance. Fresh observations supersede stored
facts; conflicting serials discard the old association. Remembered facts are
presentation-only and never grant mount/write authority. A sole historical Apple
record without an exact device match is not sufficient identification. Recovery
counts and database digests stay in backend snapshots, not dashboard rows.

Post-sync polling is held through the completion dialog only. Dismissal releases
the hold so subsequent SoundBox edits can update drift without reconnecting.
