# SoundBox — orientation (start here)

SoundBox is a native macOS music-library app that bridges **rekordbox →
Rockbox iPods / USB players** and includes a high-refresh DJ-style player.
Browse the live rekordbox library, curate it without ever touching it, and drag
playlists onto a device with exact order, per-track cover art, and honest sync
semantics. Built by Sonny (Low Freq Labs) with Claude and Codex.

## Runtime instruction parity

`AGENTS.md` and `CLAUDE.md` are runtime equivalents and intentionally contain
the same shared project guidance. Whenever shared instructions, safety rules,
documentation routes, build steps, or handoff facts change, update **both files
in the same change**. Runtime-specific guidance must be clearly labeled and
must not silently contradict the other file.

At the start of an unfamiliar session, read this file and then the relevant
documents below. Do not rely on a prior agent's memory when the repository can
answer the question.

## Sacred safety contract (NON-NEGOTIABLE)

- rekordbox is **never** written to. `master.db` is read via a read-only temp
  snapshot (rekordbox can stay open); the XML export is a fallback.
- Rekordbox analysis (`ANLZ*.DAT/EXT/2EX`) is read-only. SoundBox-derived
  waveform/grid caches live only under SoundBox AppData.
- Source audio files are **never** modified, renamed, moved, or deleted.
- Every export write lands **under an explicit device target**, guarded so the
  target can never overlap the source library.
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
- Git is local `master` with no remote. Inspect and preserve the dirty worktree.
  Do not commit or push unless Sonny explicitly asks.

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
- CDJ reference USB snapshots and large audio fixtures live outside the repo at
  `~/soundbox-cdj-ref/`; only prototype code belongs here.
