# SoundBox — orientation (start here)

SoundBox is a native macOS (PySide6) app that bridges **rekordbox → Rockbox
iPods / USB players**: browse the live rekordbox library, curate it without ever
touching it, and drag playlists onto a device — exact order, per-track cover
art, honest sync semantics. Built by Sonny (Low Freq Labs) + Claude.

## Sacred safety contract (NON-NEGOTIABLE)

- rekordbox is **never** written to. `master.db` is read via a read-only temp
  snapshot (rekordbox can stay open); the XML export is a fallback.
- Source audio files are **never** modified, renamed, moved, or deleted.
- Every write lands **under an explicit export target** (the device), guarded so
  a target can never overlap the source library.
- Dry-run by default; ask before anything irreversible; never `rm -rf` /
  bulk-delete / orphan-cleanup. Don't assume paths — confirm detected ones.
- Full per-write contract lives at the top of `soundbox/sync.py`.

## Where everything is

| Doc | What's in it |
|-----|--------------|
| `docs/ROADMAP.md` | Status, scorecard, phases (iPod P3 · Windows P4 · public fork P5), product decisions, + the CDJ-export frontier |
| `docs/ARCHITECTURE.md` | Engine/desktop layout, key modules, data model, two-plane model, persistence, build/install |
| `docs/GOTCHAS.md` | Hard-won Qt/QThread/rekordbox/Rockbox traps. **READ before touching workers, playlists, sync, or the iPod path** |
| `docs/cdj-usb-export.md` | CDJ USB export R&D — reverse-engineering rekordbox's `export.pdb`; prototype in `research/cdj_export/` |
| `README.md` | User/dev product tour |

## Dev

    ./check.sh            # pyflakes + mypy (engine) + full test suite  ← the gate
    ./check.sh --render   # + offscreen UI render to /tmp
    ./build_app.sh        # PyInstaller → dist/Soundbox.app (self-signed)

- Run `./check.sh` **bare** — never pipe it through `tail`/`grep` in a `&&`
  chain (a failing gate gets masked; see `docs/GOTCHAS.md`).
- Python 3.14, deps in `requirements.txt`, venv at `.venv/`.
- Logs: `~/Library/Logs/Soundbox/soundbox.log`.
- Git: local `master` only, no remote. Commit at the end of each session.
  Don't commit or push unless asked.
- Install a fresh build:
  `rm -rf /Applications/Soundbox.app && cp -R dist/Soundbox.app /Applications/ && xattr -dr com.apple.quarantine /Applications/Soundbox.app`

## NOT in this repo (private — keep it that way)

- The **liveset / SoundCloud download pipeline** is a personal workflow and must
  **never** be documented in this repo. It lives only in Claude's memory.
- CDJ-export **reference USB snapshots** (large audio binaries) live outside the
  repo at `~/soundbox-cdj-ref/` — the repo only carries the prototype *code*.
