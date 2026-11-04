# Shared app and optional personal modules

Allocation requested October26,2026; status checked November4 against source
release `4a3d29a` and installed build160/runtime `7bbe8577…`. Product direction: one shared application,
nearly identical public/personal UI, optional installed modules adding a small
number of capabilities. Sonny clarified that the public product remains the
complete music manager, spanning iPod/MP3 players and USB export. Personal
extensions isolate the current uncleared online scraping/discovery implementations
and dependencies withheld from public distribution. Online features with permitted
access/content use and compatible dependency licenses remain public-eligible.
This document records that allocation and the implemented first-party
interface contract. The provider boundary and explicit public/personal build
profiles are implemented. Core owns local artwork and resolved action availability;
the online implementation is isolated in `soundbox_personal.discovery` and
`soundbox_personal.connection`. The latter adds optional user-supplied API
credentials, application-token access to public-profile likes and a disposable
likes index for personal artwork matching. New connections need no browser
authorization; legacy user-token compatibility remains in the backend.
Canonical curation remains core-owned. This is not new distribution
clearance. See [connection setup](soundcloud-connection.md).
Build/install validation is recorded in the current handoff.

## Allocation

“Public” means intended shared product scope, subject to the release gates in
[ROADMAP.md](ROADMAP.md), not a declaration that today's build is ready to ship.
Device management is shared, including guarded file maintenance and confirmed
generic-volume Erase. Validated rekordbox-compatible/CDJ USB export is
also shared product scope. Neither profile may bypass source/device safety
contracts. September 28 clarification: Sonny wants features in public when their
specific implementation can be properly distributed and operated. Online features,
including Unknown-Artist suggestions, are not inherently personal-only. The table's
online exclusions apply to the current SoundCloud implementations pending clearance
or replacement. Review dependency redistribution separately from service access,
attribution, persistent storage/export and commercial-use permissions. Personal
packaging does not waive service terms or confer redistribution permission.

The current `soundbox_personal.discovery.suggest_artist` searches SoundCloud via `yt_dlp`,
compares titles and infers an artist from uploaders; only user-checked suggestions
are applied. No audio is downloaded by this action. Sonny confirmed keeping this
implementation out of the public build after learning how it works. A cleared
metadata provider could support public suggestions later; none is implemented.
[SoundCloud's platform terms](https://soundcloud.com/terms-of-use) restrict scraping
information about users, and its [API terms](https://developers.soundcloud.com/docs/api/terms-of-use/)
restrict persistent User Content storage and commercial use. Moving to that API
alone would not clear permanent artwork caching or device export.
[yt-dlp's source license](https://github.com/yt-dlp/yt-dlp#licensing) is the Unlicense,
with different licenses in some bundled artifacts; it is not categorically
undistributable, nor does its license authorize a particular service use.
These September 28 findings support withholding the current implementation;
they are not a completed legal or dependency clearance.

| Capability / current surface | Public | Personal | Current boundary / work needed |
|---|---|---|---|
| App layout, theme, search, tables, sidebar, saved columns/window | Keep | Same | Shared shell |
| Read-only live rekordbox and XML refresh | Keep | Same | Core Connections card; validated database-location selection; shared snapshot/XML adapters; validate supported versions |
| My Tag / Hot–Memory cue / beat-grid source edits | Explicit authority gate; no general-library compatibility claim | Accepted exact-library domains | Shared core APIs; native acceptance binds library/schema/version, closed rekordbox/agent and recovery; manual BPM retains its independent capability; smart-list creation remains disabled |
| Ordinary native playlist/folder CRUD, membership and sibling reorder | Same explicit authority boundaries | Accepted exact-library domains | Shared worker/recovery; reorder has an independent default-false capability; no intelligent/cloud mutations |
| Bare native registration and initial full BPM/Grid+Key/waveform publication | Same explicit authority boundaries | Accepted registration plus independent WAV/narrow CBR profiles | No existing-analysis replacement, gain, broader metadata or unaccepted codecs; source selection never provisions authority |
| Collection, artists, custom playlists, ordering, drag/drop | Keep | Same | Shared library/curation controller |
| Local audio import, bookmarks, bounded missing-link repair | Keep | Same | Shared imports preserve originals outside the separately accepted recoverable conversion/locator domains; no general retagging or cleanup |
| Confirmed Collection-and-disk Delete / recoverable compatibility conversion and locator update | Shared core with independent exact-source authorities | Same accepted-domain limits | Explicit targets/recovery, common source lane and fresh native guards; no permanent deletion, Trash emptying or general metadata rewrite |
| Title/artist overrides, cleanup rules, aliases | Keep | Same | Shared curation; personal values are data |
| Local fuzzy artist-merge suggestions | Keep | Same | `Suggest artist merges…` does not require online provider |
| Global hides, per-playlist removals, Graveyard restore | Keep | Same | Shared additive curation |
| CoreAudio/Metal playback and error fallback to library-only UI | Keep | Same | Native core, not an extension |
| Waveforms, zoom/modes, jumps, CUE, imported hot/memory cues | Keep | Same | Preserve exact native/player contracts |
| Autoplay up/random/down, now-playing navigation | Keep | Same | Shared navigation |
| Background analysis, reanalysis, cache reclamation | Keep | Same | Shared task ownership and resource policy |
| Local cover selection and sticky cover removal | Keep | Same | Local picker works with no provider |
| Artist catalog, local avatar upload/rename/delete | Keep | Same | Hide only provider-specific URL action; atomic artist batches preserve newer covers/manual blocks |
| Default-photo pool and balanced assignments | Keep | Same | Personal photos are user data, never bundled |
| Existing curated JPEGs and provenance | Keep readable | Same | Removing provider never removes stored art |
| Embedded artwork import from local audio | Planned | Same when built | Not a shipped feature; read-only extraction, explicit precedence |
| Automatic online cover discovery for new tracks | Omit | Retain via optional provider | Starts only with the personal provider |
| Fetch cover art now / background / stop / re-scan | Omit online actions | Retain via provider | Gate command and scheduler, not just menu |
| Online thumbnail candidate search in cover picker | Omit | Retain via provider | Local picker remains present in both |
| SoundCloud connection and likes-first artwork matching | Omit | Optional SoundCloud card in Connections | App credentials/tokens in Keychain; public-profile likes aid general artwork search without browser authorization or website redirect changes; metadata index outside curation/backup; live service acceptance is separate from offline validation |
| SoundCloud page URL cover/avatar actions | Omit | Retain via provider | Track picker and artist catalog gate URL controls and commands |
| Suggest artists for Unknowns (online consensus) | Omit | Retain via provider | Separate from local merge suggestions |
| `yt_dlp` and provider-specific network/matching code | Exclude package | Optional dependency | Personal bundles it; public physically excludes it |
| Device discovery, loading status, dashboard, normal eject | Keep | Same | Core serialized device controller |
| Rockbox filesystem export / generic FAT32–exFAT native CDJ export | Shared scope, guarded | Same; CDJ remains a testing pilot | Separate firmware/layout contracts and publication boundaries |
| Stock MA446 export | Keep after acceptance | Same allowlist | Validated MP3/supported WAV only |
| Dual MA446 stock/Rockbox single-copy sync | Keep after acceptance | Same allowlist | Schema 3; one transaction authority |
| Managed playlist deletion, reference-counted cleanup | Keep, confirmed | Same | Core sync semantics, not an unlock bypass |
| Rockbox append adoption into custom playlists | Keep | Same | Existing managed paths only; no rekordbox writes |
| Verify / Deep Verify | Keep, explicit | Same | Read-only; never prerequisite for idle eject |
| Read-only device file browsing/import | Keep | Same | Confined source paths and serialized reads |
| Raw device file rename/delete/Assign Artist/New Folder | Keep, guarded | Same | Shared device tools with target/source guards and confirmations |
| Whole generic-volume Erase | Keep, separately confirmed | Same | Exact target confirmation; never enabled for stock/dual |
| Backup/restore | Keep | Same | Staged restore/recovery and serialized core state ownership |
| Diagnostic logging / actionable errors | Keep | Same | No user paths/library data bundled as examples |
| Exact native traces, developer scripts and legacy XML CLI | Not product UI | Developer tools | Separate tools; do not treat as personal feature unlock |
| Packaged native CDJ USB pilot | Backend packaged; public release withheld | Personal testing build | Both frozen profiles include the dedicated renderer; physical and dependency/distribution review remain open |
| CDJ research harnesses and hardware fixtures | Exclude | Developer workspace only | Only the compiled dedicated renderer enters app profiles; research code and private fixtures are not bundled |
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
5. Initial delivery uses explicit build composition: shared core plus a
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


October28 CDJ integration vendors rbxport revision
`b739d369665b492534dc2eae0733322c040a26d5` (GPL-2.0-or-later) and its dependency
closure. Upstream notices/full GPL text are retained and packaged with the
separate renderer. The local personal pilot is not redistribution clearance or
hardware certification; review corresponding-source/dependency obligations before
any public release. The source kit/provenance are under `native/cdj/vendor/`;
private reference media, DB captures and settings never enter the app bundle.
