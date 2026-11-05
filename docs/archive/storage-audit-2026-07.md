# Soundbox cache and storage audit — RESOLVED

Historical evidence; dated status and instructions below apply only to their
original checkpoints. Current behavior and priorities are in [PLAYER](../PLAYER.md),
[ROADMAP](../ROADMAP.md) and [HANDOFF](../HANDOFF.md).
Original `build/` paths are historical evidence locators. Some private archives
have since been retired; [project layout](../PROJECT_LAYOUT.md) distinguishes
retained receipts from deleted material. Old paths do not guarantee retention
or authorize replay; inspect retained scripts before reuse.

Historical app-control, source-write, installer, restore, replay and cleanup
instructions below grant no present authorization. Current permissions and
protected recovery bases are governed by [the current handoff](../HANDOFF.md).

Audit date: 2026-07-13 · Resolved: 2026-07-19 (was `CACHEISSUE.md` at repo root)

All footprints, cleanup approvals and rollback counts below are dated historical
evidence. They do not authorize deleting current recovery material. Consult
[HANDOFF.md](../HANDOFF.md) for rollback/state that must remain today.

## Resolution (what was actually done, and what was deliberately declined)

### Follow-up: temporary install rollbacks (2026-08-03)

A read-only audit after repeated iPod development installs found about 9.26 GiB
under `/tmp/soundbox-*`, mostly superseded application rollback bundles. This is
the avoidable growth the user observed. It is separate from the 28 physical-iPod
stock recovery points (about 51 MB total) and the bounded V5 analysis cache
(about 11.6 GB for 3,244 records). No files were removed during the audit.

Subsequent guarded installs retired the superseded bundles after signature,
startup, and curated-state verification. At the 2026-08-03 checkpoint, exactly
one immediate rollback remained at
`/private/tmp/soundbox-install-20260803-72RwQm/` (about 235 MB), containing the
preceding app and `library.json.before`. That was a dated temporary recovery
point, not a requirement to retain it indefinitely.

### Follow-up: all temporary rollbacks retired (2026-08-25)

Later guarded installs superseded that August 3 recovery point. After the
current signed app, native-deck startup, curated state, and Graveyard cache
reclamation were verified, Sonny explicitly approved permanent rollback
cleanup. The final install rollback was deleted; a follow-up inventory found
no SoundBox install/rollback directories in `/private/tmp` and no loose
rollback-named artifacts under `~/Projects`. The pre-author-rewrite Git bundle
was separately proven redundant and deleted with the same explicit approval.

### Follow-up: bounded V5 waveform cache (2026-07-30)

The July 28 V4 quality recook had grown `track_analysis/` to 40 GiB because it
persisted four redundant approximately-6144 Hz streams: upper/lower uint16
peaks and signed int16 minimum/maximum rows. A production audit found 3,248 V4
records (3,244 reachable/current and three small orphans), 1,983,934 beat rows,
11,344 cue rows, and no SoundBox-owned manual grid/cue data.

V5 retains the compact BLUE/RGB/3BAND streams, 24-band spectral master, full
grid/cues, provenance, and source identity, but stores only exact
song-anchored signed min/max extrema at the divisors selected by all supported
zoom stops at macOS scale 1 and Retina scale 2. Real-record sampling projected
about 11.7 GiB for this library. Direct cooks and compatibility migrations use
the same reducer; a V4 is removable only after the corresponding V5 passes a
complete section/payload audit. Full cooking pauses below 20 GiB free and
resumes at 25 GiB.

For the controlled installed-app cutover, SoundBox was stopped before cache
removal. The temporary rollback directory contains the old app, pre/post-quit
`library.json`, `libcache.json`, and a compressed metadata snapshot of every
V4 grid/cue/provenance manifest. Only the 3,248 resolved `.sba4` files under
`track_analysis/` were deleted, raising free space from 19 GiB to 59 GiB. The
signed ABI-3 app then relaunched, activated the native deck, loaded 3,263 tracks
and 83 playlists, and began the direct resumable V5 recook. Early production
records passed complete hashes and preserved their prior beat/cue rows exactly.
That temporary rollback remained until the full recook/audit completed and was
then superseded by the single current install rollback recorded above.

### Follow-up: canonical V4 retirement (2026-07-24)

SoundBox later replaced all player-analysis families with one checksummed V4
container under `track_analysis/`. Before removing anything, the read-only
`scripts/audit_analysis_v4.py` production-loader audit combined the last
library snapshot with all SoundBox imports, including hidden tracks. It found
3,247 valid V4 records for 3,247 reachable sources, 18 already-missing sources,
zero invalid reachable records, and zero orphan V4 records. Of those valid,
immediately playable records, 2,750 already carried the current ultra/RGB
analysis model and 497 remained in the resumable quality-upgrade queue. The
audit reports these two facts separately: safe retirement means no reachable
audio depends on an older cache family; it does not pretend every optional
quality upgrade has finished.

After a verified `library.json` backup, SoundBox was stopped and only these
exact retired directories were deleted:

- `analysis/` — 201 MB;
- `detail_waveforms/` — 2.2 GB;
- `waveforms/` — 656 KB.

The 7.0 GB canonical `track_analysis/`, artwork, source audio, Rekordbox data,
and every other AppData path were preserved. The post-shutdown state was backed
up again because the normal final layout save changed `columns_state`.

- **Backup format: DECLINED by choice.** Sonny reviewed the state-only-backup
  proposal and chose to keep the current always-full backups (library.json +
  history + art, ~200 MB). The app's backup/restore behavior is unchanged —
  don't "fix" this later without asking. (Restore already tolerates a zip with
  no `art/`, so a state-only format needs only export-side work if ever wanted.)
- **Backup retention: done manually.** Deleted the four older ZIPs (Jul 2/5/6/8,
  ~780 MB); kept the newest full snapshot (`soundbox-backup-2026-07-10.zip`,
  209 MB) in `Desktop/{SUBYATA.music}/SoundBox Backup/`. No retention code was
  added — it stays a manual habit: keep roughly one recent full ZIP.
- **Build outputs: deleted** `dist/` (412 MB) and `build/` (64 MB). Both are
  rebuildable with `./build_app.sh`; `/Applications/Soundbox.app` is the
  installed copy and was untouched.
- **`.venv` (1.3 GB): kept** — the project remains in active development.
  Revisit only if the project goes dormant.
- **Live app data (art 229 MB / waveforms 16 MB / history 1 MB): kept** — the
  audit itself concluded these are useful caches, not the problem.
- No storage-report/cleanup command was built; footprint after cleanup is small
  enough that it wasn't worth the surface area.

Net: ~1.25 GB freed with zero app-behavior changes.

The original audit follows, unedited, for reference.

---

## Summary

Soundbox's live application cache is not the main storage problem. The useful
runtime data is about 232 MB. Most of the project's disk usage comes from a
rebuildable Python environment, duplicate PyInstaller outputs, and full backup
ZIPs that repeatedly embed the entire cover-art cache.

Soundbox can behave just as well with a smaller footprint by keeping one live
art cache, treating development/build outputs as disposable, and separating
the tiny irreplaceable library state from the regenerable art included in
backups.

## Measured footprint

Measurements below are allocated sizes from this Mac and are rounded:

| Location | Size | What it is |
| --- | ---: | --- |
| project `.venv` | 1.3 GB | Rebuildable Python development environment |
| project `build` | 63 MB | PyInstaller work files |
| project `dist` | 412 MB | A 206 MB onedir build plus a 207 MB `.app` build |
| `/Applications/Soundbox.app` | 207 MB | Installed application |
| live app-data directory | 232 MB | User state and runtime accelerators |
| live `art` cache | 215 MB | Baseline JPEG cover art |
| live `waveforms` cache | 15 MB | Regenerable waveform peaks |
| live rotating JSON backups | 1 MB | Small state history |
| `Desktop/{SUBYATA.music}/SoundBox Backup` | 987 MB | Five full backup ZIPs |

The project directory is approximately 1.8 GB. Including the installed app,
live data, and exported backups, the observed Soundbox footprint is roughly
3.2 GB.

## What is wrong

### 1. Full backup ZIPs duplicate regenerable art

`soundbox/backup.py` defines every exported backup as:

```text
library.json + backups/ + art/
```

The five observed backups are 191-209 MB each. Their contents show that almost
all of each archive is cover art:

| Backup date | Art payload | Library JSON |
| --- | ---: | ---: |
| 2026-07-02 | 200.6 MB | 28 KB |
| 2026-07-05 | 201.0 MB | 45 KB |
| 2026-07-06 | 200.9 MB | 72 KB |
| 2026-07-08 | 212.6 MB | 105 KB |
| 2026-07-10 | 218.8 MB | 107 KB |

JPEG is already compressed, so ZIP cannot shrink this content meaningfully.
Each full backup is effectively another complete copy of the art cache. Five
backups consume nearly 1 GB even though the irreplaceable `library.json` state
is only tens of kilobytes per snapshot.

### 2. `dist` keeps two equivalent packaged applications

The current `dist` directory contains both:

```text
dist/Soundbox       approximately 206 MB
dist/Soundbox.app   approximately 207 MB
```

The installed `/Applications/Soundbox.app` is another 207 MB copy. Once a
signed app has been verified and installed, keeping both distribution forms is
usually unnecessary. The build script uses PyInstaller `--clean`, but that
cleans PyInstaller's work/cache state; it does not implement output retention.

### 3. The development environment looks like cache to macOS

`.venv` is 1.3 GB and lives in a hidden project directory. It is not runtime
user data and is reproducible from `requirements.txt`, but macOS may categorize
it as System Data/developer data. It is useful during active development, yet
it does not need to exist on a machine that only runs the installed app.

### 4. Build and backup retention are manual

`build/`, `dist/`, and `.venv/` are correctly ignored by Git, but there is no
project cleanup command or size budget. Exported full backups also have no
retention policy, so every manual backup permanently adds another copy of art.

## What should be preserved

- `library.json` is the crown-jewel user state: overrides, playlists, hides,
  removals, and settings. It must always be backed up.
- The small rotating JSON history should remain.
- The live 215 MB art cache is useful: it preserves manually chosen artwork,
  avoids repeated network matching, and guarantees iPod-safe baseline JPEGs.
- The 15 MB waveform cache makes redraws instant, although it is safe to
  regenerate.
- One installed `Soundbox.app` is required to run the application.

The correct fix is not to remove all caching. It is to stop copying the same
regenerable cache into every historical backup and to prune build outputs.

## Proper target design

### Split state backups from full media-cache backups

Make the default backup small and durable:

```text
library.json + backups/ + README/manifest
```

Offer art as an explicit optional "full/offline backup" mode. Retain at most
one recent full backup locally, or put full backups on external storage. Older
state-only backups would be measured in kilobytes or low megabytes instead of
roughly 200 MB each.

Restore should accept both formats. A state-only restore can reuse the current
art cache, fetch missing art again, or show missing art until the background
fetcher repopulates it. User organization and edits remain intact because
they live in `library.json`, not in JPEG files.

If manually selected art must be considered irreplaceable, record its origin
and/or mark it in the art index, then include only those manual files in the
small backup. Automatically fetched art can always be regenerated.

### Keep one build product

After `dist/Soundbox.app` passes signing and verification:

1. install or update `/Applications/Soundbox.app`;
2. remove `build/`;
3. remove the redundant `dist/Soundbox` onedir output;
4. optionally remove all of `dist/` after the installed app is verified.

This does not affect the installed app. The distribution output can be
recreated with `./build_app.sh`.

### Treat `.venv` as a rebuildable development dependency

Keep `.venv` while actively developing because it makes tests and builds
immediate. Provide a documented clean target that can delete it when space is
tight, with a corresponding bootstrap target that recreates it from the
pinned requirements. The installed app does not use the project `.venv`.

For dependable recreation, dependency versions should be locked rather than
relying only on unconstrained package names. Native dependencies and Python
3.14 compatibility should be verified by the normal test/build gate after a
rebuild.

### Add a storage report and retention command

A Soundbox maintenance command should report separate totals for live state,
art, waveforms, exported backups, `.venv`, `build`, and `dist`. Cleanup should
support:

- removing waveform peaks only;
- pruning old full backups while preserving all state-only backups;
- removing PyInstaller work/output;
- removing and recreating the development environment as an explicit action.

It must never touch source audio, rekordbox data, `library.json`, or manually
selected art without an explicit backup/confirmation path.

## Expected result

Keeping one full backup instead of five saves about 780 MB immediately while
retaining a complete recent offline snapshot. Cleaning PyInstaller work and
redundant distribution output saves roughly 475 MB. Removing `.venv` when the
project is inactive saves another 1.3 GB.

That is roughly 2.5 GB of reclaimable or rebuildable storage without changing
Soundbox's user-facing behavior. The live art and waveform caches can remain,
because together they are small relative to the duplicated backups and they
provide real performance and offline-value benefits.
