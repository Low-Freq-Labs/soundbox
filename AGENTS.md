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
  or otherwise retag source audio. The connected layout selects the exporter:
  stock-only uses the guarded stock planner; `/.rockbox/` uses the Rockbox
  filesystem planner and leaves any coexisting stock database read-only.
  A storage-efficient dual-library mode is approved only as future work: safe
  tracks would be shared between iTunesDB and Rockbox while stock-unsafe tracks
  remain Rockbox-only. For the current MA446 boundary, stock-safe means a
  validated MP3 or stereo PCM16/44.1-kHz WAV; that restriction applies only to
  the iTunesDB-visible subset and must never filter the broader Rockbox library.
  Unsupported-by-both media is omitted and reported. Do not approximate that
  design with two independent exporters or duplicate the collection.
- Stock playlist deletion may remove regular user playlists and the exact Apple
  **Videos** smart index only. Apple databases may mirror that index in the
  ordinary MHLP and canonical MHSD-5 datasets under different IDs; the device
  UI must select the canonical ID and deletion must remove both matching
  representations as one zero-media operation. Music, Movies, TV Shows, and
  Audiobooks remain immutable. On-The-Go is firmware-generated, not an iTunesDB
  playlist.
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
