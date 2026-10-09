# Project layout and retention

Updated October 9, 2026. This describes the checkout, not the installed app's
library or waveform caches.

## Active checkout

| Location | Purpose |
| --- | --- |
| `soundbox/`, `desktop/`, `native/`, `soundbox_personal/` | Application source |
| `tests/`, `scripts/`, `research/` | Regression tests, maintained developer tools and scoped CDJ research |
| `assets/`, `profiles/` and root build/config files | Product assets and reproducible build inputs |
| `docs/` | Current behavior, handoff, roadmap, exploratory ideas and open audits |
| `docs/archive/` | Clearly dated completed audits and superseded experiments; not current instructions |
| `.venv/` | Active pinned development environment; installed SoundBox does not depend on it |
| `build/native/`, `build/check/` | Current development native library and latest release reports |
| `soundbox-ipod-ref/`, `soundbox-waveform-ref/` | Private read-only regression fixtures and recovery/research captures |
| `soundbox-cdj-ref/` | Private read-only CDJ research fixtures |

The three reference directories are intentionally retained, not disposable build
output. The release gate requires the offline iPod and frozen waveform fixtures.
Do not remove or replace them to make tests pass. Native source runs also need
`build/native/`; `./build_native.sh` can regenerate it.

`./build_app.sh` recreates packaging work under `build/` and output under `dist/`.
Python, mypy and Hypothesis also recreate their ignored caches during development.
Ignored does not automatically mean safe to delete: diagnostic evidence may live
under `build/` until deliberately archived.

## October 9 cleanup

Obsolete root/public/personal packaged apps, PyInstaller intermediates and
Python/tool caches were moved reversibly to:

`~/.Trash/Soundbox-project-cleanup-2026-10-09/`

The first Trash batch was already absent by final verification; this cleanup did
not empty Trash. Regenerated validation caches were subsequently moved into its
`after-validation/` subdirectory. Do not depend on Trash for recovery evidence;
all retained diagnostic evidence uses the separate archive below.

Historical diagnostic scripts, captures, comparison libraries and audit reports
were moved with their original `build/` subpaths into:

`~/Library/Logs/Soundbox/development-archive-2026-10-09/`

That archive contains `moves.json` with exact source/destination paths, file counts
and logical sizes. Old `build/<investigation>/...` references in dated docs resolve
under that archive. The scripts are retained as evidence and may contain old
absolute paths; inspect/adapt them before rerunning. Latest gate reports remain
at `build/check/`. Moving files out of this directory does not itself reclaim
volume space; Trash can be emptied separately when desired.

Current player guidance is in [PLAYER](PLAYER.md); experiments formerly embedded
there are in [player history](archive/player-history-2026-07-to-09.md). Completed
reports are indexed in [the archive](archive/README.md). Exploratory concepts are
in [IDEAS](IDEAS.md), separate from the committed [ROADMAP](ROADMAP.md).

The installed `/Applications/Soundbox.app`, all live SoundBox AppData, source
music, rekordbox data, required fixtures and generation-58 recovery were untouched.
Current installed provenance and recovery location remain in [HANDOFF](HANDOFF.md).
