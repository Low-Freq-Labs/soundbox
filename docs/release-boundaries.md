# Shared app and optional personal modules

September 27, 2026. Product direction requested by Sonny: one shared application,
nearly identical public/personal UI, optional installed modules adding a small
number of capabilities. Sonny clarified that the public product remains the
complete music manager, spanning iPod/MP3 players and USB export. Personal
extensions isolate online scraping/discovery and dependencies withheld from
public distribution. This document records that allocation and the proposed
interface contract. No capability registry, public build profile or detachable provider is
implemented yet. Preserve all existing personal behavior during migration.

## Allocation

“Public” means intended shared product scope, subject to the release gates in
[ROADMAP.md](ROADMAP.md), not a declaration that today's build is ready to ship.
Device management is shared, including guarded file maintenance and confirmed
generic-volume Erase. Future validated rekordbox-compatible/CDJ USB export is
also shared product scope. Neither profile may bypass source/device safety
contracts. Online scraping is personal-only by product choice; dependency
redistribution clearance remains a separate review, not an assumed prohibition
on every scraping library. A personal module does not confer redistribution
permission on a dependency that lacks it.

| Capability / current surface | Public | Personal | Current boundary / work needed |
|---|---|---|---|
| App layout, theme, search, tables, sidebar, saved columns/window | Keep | Same | Shared shell |
| Read-only live rekordbox and XML refresh | Keep | Same | Shared adapters; validate supported versions |
| Collection, artists, custom playlists, ordering, drag/drop | Keep | Same | Shared library/curation controller |
| Local audio import, bookmarks, bounded missing-link repair | Keep | Same | Shared imports; no source moves/retags |
| Title/artist overrides, cleanup rules, aliases | Keep | Same | Shared curation; personal values are data |
| Local fuzzy artist-merge suggestions | Keep | Same | `Suggest artist merges…` does not require online provider |
| Global hides, per-playlist removals, Graveyard restore | Keep | Same | Shared additive curation |
| CoreAudio/Metal playback and error fallback to library-only UI | Keep | Same | Native core, not an extension |
| Waveforms, zoom/modes, jumps, CUE, imported hot/memory cues | Keep | Same | Preserve exact native/player contracts |
| Autoplay up/random/down, now-playing navigation | Keep | Same | Shared navigation |
| Background analysis, reanalysis, cache reclamation | Keep | Same | Shared task ownership and resource policy |
| Local cover selection and sticky cover removal | Keep | Same | Split local picker from auto-started online search |
| Artist catalog, local avatar upload/rename/delete | Keep | Same | Hide only provider-specific URL action |
| Default-photo pool and balanced assignments | Keep | Same | Personal photos are user data, never bundled |
| Existing curated JPEGs and provenance | Keep readable | Same | Removing provider never removes stored art |
| Embedded artwork import from local audio | Planned | Same when built | Not a shipped feature; read-only extraction, explicit precedence |
| Automatic online cover discovery for new tracks | Omit | Retain via optional provider | Currently autostarts in core |
| Fetch cover art now / background / stop / re-scan | Omit online actions | Retain via provider | Gate command and scheduler, not just menu |
| Online thumbnail candidate search in cover picker | Omit | Retain via provider | Local picker remains present in both |
| SoundCloud page URL cover/avatar actions | Omit | Retain via provider | Track picker and artist catalog both need gating |
| Suggest artists for Unknowns (online consensus) | Omit | Retain via provider | Separate from local merge suggestions |
| `yt_dlp` and provider-specific network/matching code | Exclude package | Optional dependency | Current build bundles it unconditionally |
| Device discovery, loading rows, dashboard, normal eject | Keep | Same | Core serialized device controller |
| Generic FAT32 / Rockbox filesystem playlist export | Keep, guarded | Same | Do not revive blanket generic-USB prohibition |
| Stock MA446 export | Keep after acceptance | Same allowlist | Validated MP3/supported WAV only |
| Dual MA446 stock/Rockbox single-copy sync | Keep after acceptance | Same allowlist | Schema 3; one transaction authority |
| Managed playlist deletion, reference-counted cleanup | Keep, confirmed | Same | Core sync semantics, not an unlock bypass |
| Rockbox append adoption into custom playlists | Keep | Same | Existing managed paths only; no rekordbox writes |
| Verify / Deep Verify | Keep, explicit | Same | Read-only; never prerequisite for idle eject |
| Read-only device file browsing/import | Keep | Same | Confined source paths and serialized reads |
| Raw device file rename/delete/Assign Artist/New Folder | Keep, guarded | Same | Shared device tools with target/source guards and confirmations |
| Whole generic-volume Erase | Keep, separately confirmed | Same | Exact target confirmation; never enabled for stock/dual |
| Backup/restore | Keep after transactional hardening | Same | Core state ownership, not personal capability |
| Diagnostic logging / actionable errors | Keep | Same | No user paths/library data bundled as examples |
| Exact native traces, developer scripts and legacy XML CLI | Not product UI | Developer tools | Separate tools; do not treat as personal feature unlock |
| Future validated rekordbox-compatible/CDJ USB export | Planned shared capability | Same | Requires separate implementation, validation and distribution review; not currently shipped |
| CDJ writer research and hardware recovery fixtures | Exclude | Developer workspace only | Not an installed module or released capability |
| Other stock models, schema-2→3 migration, Windows | Not yet supported | Same | Personal profile does not expand firmware authority |

No personal library, photos, absolute source paths, device captures, credentials
or private workflows belong in a distributable artifact. Private workflows
outside this application remain outside repository documentation and module
discovery; this design does not import or describe them.

## What the public UI loses

The same main window, deck, library and device tabs remain. The online fetch,
rescan, URL and Unknown-Artist discovery actions disappear. “Choose cover art…”
still opens local selection. The artist catalog retains local image controls.
Device file editing, confirmed generic Erase and managed Sync/Delete/Verify/Eject
remain normal shared controls with their existing device-specific safeguards.
No locked buttons, empty provider panels, upgrade banners or alternate layout.

## Attachment contract

1. Core defines small typed provider/result and action interfaces. Begin with
   artwork discovery and artist suggestions, the actual existing use cases.
   Do not create a general plugin marketplace or a second application.
2. Core owns artwork persistence, curation, tasks, diagnostics and device safety.
   A provider returns candidate/image/metadata results; core validates and saves
   them. It receives narrow services, never the whole MainWindow or arbitrary
   device-write authority. Device maintenance remains shared core functionality
   with unchanged confirmation requirements.
3. An explicit, versioned module manifest identifies the module, compatible core
   API, contributed actions and optional dependencies. Missing, incompatible or
   failed modules leave core usable and emit a concise diagnostic. No discovery
   by executing arbitrary files from music folders or unrestricted `sys.path`.
4. Resolve capability availability once at startup. Use it at UI construction,
   action execution and background scheduling. Feature settings alone cannot
   activate a module absent from the public artifact. A null provider schedules
   no network work. Optional provider jobs use core cancellation/ownership and
   bounded resource budgets.
5. Initial delivery should use explicit build composition: shared core plus a
   known provider package in the personal artifact, shared core alone in public.
   This gives attach/detach at build and restart without a code fork. Loading
   separately installed modules later requires a reviewed allowlist, compatible
   signing/loading policy and package location; do not mutate a signed bundle
   after installation. Hot unload is out of initial scope.
6. A module failure or detach preserves generated curation and canonical art.
   Provider-specific configuration uses a versioned namespace preserved by core,
   or a separately owned file. It must not be silently dropped by `_save_state`.
   Both builds share the core state schema; concurrent app instances must not
   write that state independently. Detach is not a cache reset or migration.
7. Public packaging explicitly excludes personal code/dependencies, checks the
   built artifact and records profile/module provenance. Core tests run without
   private packages installed; personal tests run with them. “Public” is not
   merely a runtime boolean hiding an already-bundled provider.

These in-process interfaces organize trusted code; they are not a security
sandbox or a claim that an untrusted plugin cannot access the filesystem.

## Completion gates

- Public starts, browses, plays, edits local art and plans supported exports with
  private packages absent and network denied; no provider is imported or started.
- Personal retains existing fetch/search/suggestion workflows through the module;
  both variants retain guarded device maintenance.
- Paired hermetic renders show only the intended action differences.
- Both variants round-trip the same curated-state fixture, including provider
  namespaces, art provenance, defaults and sticky blocks. Removing/reinstalling
  a provider leaves curation intact and causes no full analysis rebuild.
- Incompatible/broken providers fail closed without disabling core features;
  running provider work cannot outlive its owner or app shutdown.
- The public artifact contains no private provider, optional network dependency,
  developer capture, personal configuration or fixture. Review licenses/notices
  and signing/notarization separately before distribution.
