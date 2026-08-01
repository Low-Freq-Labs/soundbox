# SoundBox — New ideas (2026-07-22)

## Status audit (2026-07-23)

This file is the **exploratory UX/QOL backlog**, not an implementation plan.
Committed and sequenced work belongs in `docs/ROADMAP.md`; player architecture
and the cue/grid follow-up live in `docs/PLAYER.md`.

Several ideas below already have an engine or first UI:

| Idea | Current reality | Remaining concept |
|---|---|---|
| Sync Report | `SyncReport` already records export facts; completion UI is still compact | Purpose-built summary dialog and richer transfer metrics |
| Dry Run | Safe planning/preview exists in the engine/CLI | Designed in-app preview |
| Incremental Sync | Manifest drift and diff-driven resync already avoid unnecessary work | Optional scan/unchanged/new breakdown |
| Artwork Dashboard | ART column, artist catalog, sticky blocks, rescan, and missing-art flow exist | One consolidated coverage dashboard |
| Library Health | Missing-link repair, art state, playlist verification, and untracked filters exist separately | One intentionally designed health view/score |
| Export Verification | Right-click **Verify export…** already re-reads playlists and files | Optional automatic pre-eject verification/summary |
| Device Health | The stock-iPod Device tab now reports identity, model-derived DAC, firmware, transport, storage, database state, playback state, and recovery points | Battery/filesystem diagnostics and equivalent Rockbox health/version reporting |

Do not rebuild the completed foundations. Workshop only the remaining UX with
Sonny, and move an accepted slice into the roadmap before implementation.

## Philosophy

Keep SoundBox focused on one pristine combined library view that can target
multiple destinations (Rockbox iPod, iTunes, CDJ USBs, generic USBs, etc.).
Rekordbox remains the read-only mirror; SoundBox's additive curation plane
provides the overrides/imports/playlists without mutating that mirror.

------------------------------------------------------------------------

## 1. Sync Report

Replace a simple toast with a proper sync summary dialog.

Example metrics:

-   Tracks verified
-   New tracks added
-   Updated tracks
-   Removed tracks
-   Playlists created / updated / deleted
-   Artwork embedded / downloaded / missing
-   Device storage used / available
-   Total transfer size
-   Transfer duration
-   Average transfer speed
-   Any warnings or errors

This becomes both useful feedback and a debugging tool.

------------------------------------------------------------------------

## 2. Dry Run Mode

The engine/CLI already has safe planning and preview. The remaining idea is an
in-app preview showing exactly what will happen before syncing.

Show:

-   New tracks
-   Removed tracks
-   Metadata updates
-   Estimated transfer size
-   Estimated transfer time

Very similar to `rsync --dry-run`.

------------------------------------------------------------------------

## 3. Device Health

The first destination-specific dashboard has shipped for supported stock iPods:
Device, Music, and Playlists tabs expose Apple/macOS identity, guarded
model/firmware/DAC provenance, storage, database health, playback state, recovery
points, indexed music, and managed playlists. It also supports guarded regular
playlist deletion plus the narrowly allowlisted Videos system index.

The remaining broader device-health idea is to display, where the connected
firmware can report it reliably:

-   Battery percentage
-   Filesystem health
-   Rockbox version
-   Rockbox database status
-   Destination artwork/cache status

A quick "everything looks good" dashboard before syncing.

------------------------------------------------------------------------

## 4. Incremental Sync

The manifest/diff-driven resync foundation is shipped and already transfers only
what the device needs. The remaining polish is to expose its scan breakdown:

Show scanning progress like:

-   Total tracks scanned
-   Unchanged
-   Modified
-   New

Do not replace or fork the existing manifest engine to build this UI.

------------------------------------------------------------------------

## 5. Sync History

Maintain a history of sync operations.

Store:

-   Date / time
-   Device
-   Tracks added
-   Tracks removed
-   Playlists updated
-   Transfer size
-   Duration

Useful for troubleshooting and tracking changes over time.

------------------------------------------------------------------------

## 6. Duplicate Inspector

Provide tools to identify duplicate music.

Examples:

-   Same audio, different filename
-   Same title, different master
-   WAV + MP3 versions

Allow selecting a preferred version.

------------------------------------------------------------------------

## 7. Artwork Dashboard

The ART column, artist catalog, missing-art workflow, and sticky blocks already
cover the individual operations. This idea would consolidate their coverage
into one intentionally designed view.

Display counts for:

-   Embedded artwork
-   Artist avatar fallback
-   Missing artwork

Provide a "Fix Missing Artwork" workflow.

------------------------------------------------------------------------

## 8. Smart Collections

Automatically generated collections instead of manually maintained
playlists.

Current ideas:

-   Recently Added
-   Never Played
-   Needs Cue Points

Keep this intentionally small and genuinely useful.

------------------------------------------------------------------------

## 9. Library Health

Overall health score for the music library.

Possible checks:

-   Broken file references
-   Missing artwork
-   Metadata consistency
-   Playlist integrity
-   Duplicate warnings

The goal is confidence that the library is clean.

------------------------------------------------------------------------

## 10. Export Verification

Manual right-click **Verify export…** is shipped. A future enhancement could
offer the same check automatically before ejecting and summarize:

-   Files copied successfully
-   Playlist references valid
-   Artwork present
-   Database generated correctly
-   Filesystem appears healthy

Display a simple PASS / WARNING / FAILED result.

------------------------------------------------------------------------

## Future concept: DJ Ready

Potential future feature.

Analyze an export and determine if it is "DJ Ready."

Possible checks:

-   Cue points
-   BPM
-   Key
-   Artwork
-   Waveforms
-   Missing files

Present a single readiness indicator before a performance.

------------------------------------------------------------------------

## UI / UX (high priority)

**Do not start implementing these features without first designing with sonny on how
they fit into the existing interface.**

The current UI is one of Soundbox's biggest strengths.

Goals:

-   Preserve the clean, minimal layout.
-   Avoid cluttering the interface with extra buttons or panels.
-   Surface advanced information only when it is useful.
-   Make sync feedback feel polished rather than overwhelming.
-   Every new feature should feel like it has always belonged.

It is worth spending extra time designing the user experience before
writing code.

A polished UI is part of what separates Soundbox from feeling like an
internal utility.

Workshop these numbered ideas into small QOL/polish slices **with Sonny**.
Taken wholesale, they could easily overwhelm the clean, open UI.
