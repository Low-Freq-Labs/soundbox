# SoundBox full personal beta RC checklist

Requested November26,2026. Execution began after personal180 delivery; see the
[dated results](rc-beta-results-2026-11-26.md). Unchecked work remains unproved.
Keep the complete personal build:
artwork discovery, account connections, artist tools, playback, prep and every
device function Sonny uses. The first recipient is one private beta tester.
General-user readiness means the installer and runtime work without Sonny's
username, paths, Keychain, saved curation, credentials or accepted authorities.

Read [HANDOFF](HANDOFF.md) first for the actual source/build/install checkpoint.
The [next-session queue](NEXT_SESSION.md) breaks autonomous source-only work into
small sections; signing and installation wait until Sonny returns for Keychain.
Use this checklist alongside the [native feature inventory](shared-library-audit-2026-11.md),
[critical findings](critical-audit-2026-11.md), [runtime contracts](runtime-contracts.md)
and [test workflow](test-release-gate.md). Existing unresolved findings are work,
even if a complete automated gate passes. Do not rerun old private repair helpers.

## Outcome, scope and evidence

Every checkbox is open unless checked with a dated result and exact build/input
identity. A supported feature is complete only when its real persistence target
is correct after normal quit/reopen. A local overlay, exporter-only correction,
rejected unsupported write or copied-DB test alone does not establish native
rekordbox acceptance. Test refusals too; they do not substitute for implementing
an existing offered capability.

Use these result states: PASS, FAIL, NOT RUN, BLOCKED with reason. Record whether
evidence is pure/generated, copied database, packaged app, manual native reopen,
live network or physical hardware. No status carries automatically between versions,
libraries, source identities or build candidates. New fixes get focused meaningful
regressions and the relevant integration/recovery checks; the final frozen RC gets
the complete bare gate once. Do not broaden fixture permissions to make tests pass.

- [ ] RC-001 Inventory every visible action, context menu, shortcut, Settings control and background service in the personal build; map each to one owner and checklist section.
- [x] RC-002 Record source commit/fingerprint, app executable/signature/profile/build, installer hash, OS/chip and exact rekordbox version for every acceptance run. November26 lane A baseline: [identity and limits](rc-beta-results-2026-11-26.md#lane-a-baseline); repeat for later runs.
- [ ] RC-003 Record initial track, playlist, folder, tag, cue, grid and artwork counts plus source-bound musical identities; compare identities/content, not counts alone.
- [x] RC-004 Preserve current effective curation before refreshing native data; never restore an older whole-library snapshot over newer edits. November26 personal180 delivery: [fresh capture, paired plan and preservation](rc-beta-results-2026-11-26.md#completed-local-delivery-with-rc-boundaries); later runs retain this obligation.
- [ ] RC-005 Build a defect ledger with reproduction, severity, affected versions, test gap, fix commit and fresh retest; close findings with evidence rather than moving them out of scope.
- [ ] RC-006 Separate existing implemented functions, existing functions with persistence gaps, and entirely new product ideas; finish the first two before the private beta.
- [ ] RC-007 Agree performance budgets from repeatable baseline measurements before optimization; keep correctness and complete first-frame readiness as independent gates.
- [ ] RC-008 Keep actual app input, rekordbox upgrades/reopens and destructive/device steps user-operated and explicitly scoped; planning is not permission to erase or replay devices.

## Test order and version matrix

Sonny's installed app reports `6.8.7.0303` from its Info.plist. On November26,
AlphaTheta lists6.8.8, released November19, as a PRO DJ LINK security update.
The6.8.7 notes describe LINK EXPORT security;6.8.6 fixed a Device Library Plus
folder-move issue. Do not assume the offered6 update adds CDJ-3000X support.
[Official6 release notes](https://rekordbox.com/en/support/releasenote/page/4/).

The official current7 download is7.2.20, released November19. Recheck when
execution starts. CDJ-3000X support first appears in7.2.2; Device Library Plus was
renamed OneLibrary in7.2.5. AlphaTheta's current OneLibrary export guide targets
7.2.11 or later and requires matching the two USB libraries.
[Current download](https://rekordbox.com/en/download/),
[7 release history](https://rekordbox.com/en/support/releasenote/page/2/),
[OneLibrary guide](https://cdn.rekordbox.com/files/20260317132052/OneLibrary-Compatible-USB-Device-Export-.pdf).

| Lane | Required purpose | Acceptance boundary |
| --- | --- | --- |
| A: current6.8.7.0303 | Close current functionality and establish reproducible baseline | Existing Sonny acceptance is exact-library/domain-specific |
| B: latest6 patch, currently6.8.8 | Repeat writer/native reopen and USB comparisons after patch | New version must be assessed; no old authority restamping |
| C: latest7, currently7.2.20 | Existing6-library migration plus every write and export | New app/schema/version and recovery acceptance |
| D: brand-new6 and7 libraries | Prove setup and every feature without Sonny's history | Independently created identities, empty and populated libraries |
| E: clean Mac account/recipient | Install, first launch, credentials, updates and full personal workflows | No developer tooling or Sonny state required |
| F: physical targets | Playback/readback on actual iPod/CDJ/USB/Rockbox used by tester | Generated catalogs never certify firmware behavior |

- [ ] VER-001 Finish and preserve lane A results before upgrading; record the actual installed version, selected database location and relevant companion formats.
- [ ] VER-002 Establish a user-owned rollback/export backup before native version migration; keep this distinct from SoundBox's bounded transaction recovery and no whole-iPod-backup policy.
- [ ] VER-003 Use dedicated acceptance libraries/media with distinguishable names and IDs; avoid destructive experiments on the curated production collection.
- [ ] VER-004 Install6.8.8 normally, observe its first native open, and record schema/row/companion/version differences before any SoundBox writes.
- [ ] VER-005 Repeat every writer domain on6.8.8, including crash/retry and native reopen, rather than testing only database reads.
- [ ] VER-006 Export the same controlled library with6.8.7,6.8.8 and SoundBox; compare both catalogs, analysis, artwork, order and settings on disposable targets.
- [ ] VER-007 Install7 only after the6 baseline is closed; observe the actual migration and whether6/7 share or copy the selected library. Do not assume isolation or downgrade safety.
- [ ] VER-008 Test7 on both a migrated6 library and a natively created7 library; compare identities, clocks, row states, XML and companion data.
- [ ] VER-009 Handle6 and7 installed together: current installed-version discovery refuses mixed versions; bind compatibility to the selected native source without guessing or requiring one app to be uninstalled.
- [ ] VER-010 Test old pending jobs/recovery after a patch/major upgrade: reconcile or refuse actionably; never execute an old request against a changed unaccepted profile.
- [ ] VER-011 Test returning to an already accepted source after selecting another version/library; preserve its own queue and curation without borrowing authority.
- [ ] VER-012 Pin supported chip/OS/version claims to observed lanes; the first Apple Silicon tester is not proof of an Intel or arbitrary future-version package.

## Current native-write completion map

"Existing accepted scope" below means the implementation has a separately
accepted domain for Sonny's current library. It is not blanket6/7 or another
user's library support. Read the linked inventory for exact envelope restrictions.

| Function family | Starting point | Required RC closure |
| --- | --- | --- |
| Title/Artist, cleanup, aliases, reset | Exact-library v5; new grouped readback/ACK candidate | Whole fresh-library workload, all manual provenance,6 patch/7/recipient acceptance |
| Broader Track Info | Native protocols missing for several offered fields | Complete album/genre/year/date/comments/count/rating/label/color/autoload persistence |
| Curated track cover | Local cache/export works; native ImagePath/image publisher missing | Native counterpart and image lifecycle, preserving originals and manual priority |
| My Tags | Assignment/create/rename/order and narrow local deletion accepted | Historical/synced envelopes, all queue/admission/recovery and native reopen |
| Smart playlists | Tag ANY creation and separately admitted name/order/move/delete | Existing-rule edits, older/synced deletions and exclusion representation |
| Ordinary playlists/folders | CRUD, membership, node order/moves, exact occurrence order | Every destructive confirmation, duplicate case and late callback |
| Hot/Memory cues | DB-only and initial-analysis ordering scopes | Synced deletion, cue-first/later-analysis preservation and codec/version matrix |
| Grid/manual BPM | Independently admitted profiles | Lock/codec/companion coverage and native/device persistence |
| Analysis/key/waveform | Initial narrow WAV/CBR MP3 profiles | Existing-analysis replacement, wider supported codecs and gain where offered |
| Import/registration/conversion/relink | Separate stores and authority domains | Identity rebinding, failure/restart and source-switch gaps |
| Collection removal/Graveyard | Accepted native-and-disk scope; other paths incomplete | Lossless native Restore, source-bound history and save-failure truth |

## Every-write contract and efficiency audit

Create one write ledger covering production call sites, not just SQL stores.
For each row record trigger → intended durable target → queue/controller → lock
and transaction owner → exact mutation → readback → canonical ACK → recovery
retirement. Include failures before and after each boundary. Reuse cohesive
infrastructure, while keeping authority-specific SQL and device recovery separate.

Existing optimization evidence is a baseline, not an RC-wide performance pass:
the same39-field original-copy workload fell59.568→28.551 seconds, while verified
native baseline work still took20.523 seconds. A separate generated2,400-field
end-to-end workload completed in214.254 seconds. Different DB sizes make those
per-field rates non-interchangeable; neither is a full original-library timing.
Prioritize proving which repeated baseline work can safely be shared under one
exact owned lease, rather than promising instantaneous durable commits or merely
hiding the wait. See [measured limits](performance-audit.md).

- [ ] WRITE-001 Inventory `rekordbox_*_store.py`, tag/smart writers, conversion/removal/locator stores, native transactions and every actual controller caller.
- [ ] WRITE-002 Inventory canonical state/record publication, preferences, aliases, imports, artwork originals/index/review/defaults, caches, account metadata, Keychain and updater downloads.
- [ ] WRITE-003 Inventory CDJ, stock iPod, Rockbox/dual, settings, copy/conversion, delete/Trash, backup/restore and every rollback/cleanup primitive.
- [ ] WRITE-004 Prove a repeated accepted no-op does not write the DB, native clock, artwork, unchanged media or complete canonical state unnecessarily.
- [ ] WRITE-005 Measure admission, queue wait, fresh guards, baseline/capture, native SQL, readback, ACK and retirement separately; report p50/p95/max with byte/syscall/transaction counts.
- [ ] WRITE-006 Audit repeated full-library scans, cipher derivation, snapshot copies, digesting, JSON encoding, repeated fsync and complete cache rebuilds; retain only work justified by a concrete correctness boundary.
- [ ] WRITE-007 Keep required durable intentions, freshness, native transaction/journal and exact readback; optimize redundant work instead of removing atomicity wholesale.
- [ ] WRITE-008 Reuse proof only within its exact exclusive ownership/epoch scope; an old successful check or unchanged filename is not freshness.
- [ ] WRITE-009 Verify small edits scale with affected records wherever possible, without silently changing complete musical/recovery invariants.
- [ ] WRITE-010 Keep disk/network/native operations and expensive derivation off the GUI thread; measure continuous scrolling, inline typing and cue response during every bulk write family.
- [ ] WRITE-011 Test rapid same-object edits, multiple-object edits and mixed domains; older ACKs must preserve later accepted choices and selection/drafts.
- [ ] WRITE-012 Prove fair scheduling: a large cleanup/artwork/analysis backlog cannot starve a user tag, playlist, cue or explicit export.
- [ ] WRITE-013 Test storage full, permission loss, read-only source, process crash, canceled worker, changed native DB, replaced parent and unknown journal at every meaningful phase.
- [ ] WRITE-014 After DB commit but canonical ACK failure, restart replays the receipt without duplicating the native mutation or losing the intention.
- [ ] WRITE-015 Retry/discard must leave an actionable state and resolve dependent work; Quit/export must not wait forever on an impossible queue.
- [ ] WRITE-016 Test latest-value coalescing only before native request capture; captured requests/receipts retain immutable identity and causal order.
- [ ] WRITE-017 Test main app/rekordbox/rekordboxAgent/process-lock coexistence; never silently write while a foreign writer owns the source.
- [ ] WRITE-018 Bind every operation to selected source/library/native ID/UUID/path and relevant companion epochs; reused numeric IDs and source A→B→A must not leak work.
- [ ] WRITE-019 Test cancel, source switch, Restore and normal Quit with each accepted worker; join ownership before reporting finished or reusing its resources.
- [ ] WRITE-020 Cleanup removes only exact owned completed recovery. Unknown/foreign occupants and interrupted operations survive for reconciliation; no whole-device backups.
- [ ] WRITE-021 Publish before/after performance evidence on identical inputs and hardware, including failures and residual cost; never call pure planning a thousands-of-writes benchmark.
- [ ] WRITE-022 Retain compact receipts after completion; retire copied databases, full state snapshots, captures and duplicate packages after their acceptance purpose ends.
- [ ] WRITE-023 Compare complete schema/table/opaque-column state and DB/XML/analysis consistency against an independent expected mutation for each writer, including clock/counter/tombstone sequence and every unselected row.

## Rekordbox6: titles, artists and Track Info

- [ ] META-001 Run complete original-backup planning and an independent comparison against the current manually curated library; distinguish intentional manual choices from derivable cleanup rules.
- [ ] META-002 Run the complete thousands-of-fields durable native workload on an owned acceptance copy, then manually reopen its designated native library; the prior39-field and2,400-field generated runs do not replace this.
- [ ] META-003 Verify Title and Artist together before/after/restart; cleaning one must not destroy evidence needed to derive the other.
- [ ] META-004 Preserve Whiplash-style featured credits in Title; direct collaboration stays in Artist. Cover feat/ft/featuring, brackets, punctuation, multiple guests and genuine names containing those strings.
- [ ] META-005 Cover original/remix/flip/edit/bootleg/VIP/version/live-set identities; do not replace the actual editor with the original artist or erase meaningful title punctuation.
- [ ] META-006 Verify majority constituent spelling across solo and collaborations, one vote per song, manual values first, explicit aliases strongest; preserve order/separators and ambiguous group identities.
- [ ] META-007 Test tie behavior, changing majority, alias merge/unmerge, collaboration-only artists, non-Latin names, diacritics, NFC/NFD and case-sensitive brand names.
- [ ] META-008 Cover reverse `Title - Artist`, missing artist, literal Unknown Artist versus empty native artist, filenames and descriptor suffixes without per-song hardcoding.
- [ ] META-009 Verify Keep Original prevents automatic changes in browser, Artists, player labels, queries, migrations and all exports while explicit edits still persist.
- [ ] META-010 Verify Clean/Keep choice is exact-library-bound, persisted before startup work, maintained through restart and separate from artwork opt-in.
- [ ] META-011 Verify cleanup rules/drop words/aliases create durable paired successor plans without reverting newer manual edits or repeatedly requeueing fulfilled work.
- [ ] META-012 Test blank-field reset, edit→ACK→reset, failed admission, retry, dirty native refresh and interrupted migration with both modes.
- [ ] META-013 Validate inline click-away, Enter/Escape/Tab/Backtab, sorted/filtered rows, multi-selection and rapid edits in Collection, playlists and Artists under a real Cocoa session.
- [ ] META-014 Complete native protocols for each offered Track Info field: album, genre, year, release date, comments, play count, rating, label, color and hot-cue autoload; inventory any other actual controls.
- [ ] META-015 For related artist/album/label rows, prove reuse/create/deduplication, native counters/tombstones and exact unselected-row preservation; avoid dangling relations.
- [ ] META-016 Reopen Track Info after save and restart both apps; compare native values and related rows, not only SoundBox's display overlay.
- [ ] META-017 Test stale dialogs after selection/source/Restore change, concurrent inline edits and save failure; show success only after durable acceptance.
- [ ] META-018 Validate album/cloud/streaming row-state restrictions separately; existing field support must not be inferred from generic SQL access.

## Rekordbox6: tags, playlists and folders

- [ ] ORG-001 Assign/unassign single and many tags across single/multiple tracks; reopen rekordbox and verify smart membership.
- [ ] ORG-002 Create, rename and reorder categories/tags, including literal ampersands, Unicode, duplicate-looking names, empty categories and long labels.
- [ ] ORG-003 Move the same tag repeatedly and several tags quickly without waiting for save; queue projection and pointer/selection stay stable through older ACKs.
- [ ] ORG-004 Test tag delete for newly created/local, older and synced records; preserve surviving siblings/assignments and implement missing offered envelopes.
- [ ] ORG-005 Create tag-derived ANY/OR smart playlists; verify exact native rules, destination normalization, membership and restart.
- [ ] ORG-006 Inventory and complete offered existing smart-rule edits, older/synced smart deletion and intelligent-list exclusion behavior.
- [ ] ORG-007 Create/rename/delete ordinary playlists and nested folders, including same-name siblings and mixed ordinary/smart children.
- [ ] ORG-008 Capture the complete confirmed folder-delete subtree before the dialog; children added while confirmation is open cannot be silently deleted.
- [ ] ORG-009 Reorder siblings and move ordinary/smart nodes across parents; test before/after/inside collapsed folders, root, cycles and mixed history states.
- [ ] ORG-010 Append/remove/reorder exact playlist occurrences, including duplicate songs and repeated object identities; compare native order and tombstone/clock behavior.
- [ ] ORG-011 Rapid track moves, tag operations and playlist edits retain newer projected order while background saves run.
- [ ] ORG-012 Test drag/drop from Collection, Artists, other playlists, folders and imported files; revalidate the actual destination after asynchronous expansion.
- [ ] ORG-013 Close legacy-adoption races: a delayed folder expansion or old open menu cannot create a hidden local-only organization after native adoption.
- [ ] ORG-014 Resolve retained smart-membership exclusion migration inputs explicitly; never silently substitute a broader tag removal.
- [ ] ORG-015 Verify selected/exported playlist changes become pending exactly once and accepted completion preserves later edits.

## Rekordbox6: cues, grid, analysis, import and deletion

- [ ] PREP-001 Add/edit/move/delete Hot and Memory cues, names/colors/loops where offered; verify native DB and DAT/EXT/2EX companion effects after native reopen.
- [ ] PREP-002 Cover local versus synced cue deletion and cue-first→later-analysis preservation; complete the remaining offered profiles.
- [ ] PREP-003 Test manual BPM, grid origin, phase shift, double/half tempo, reset and locked grids without changing unrelated cues/analysis.
- [ ] PREP-004 Verify immediate usable cue overlays and complete waveform/audio readiness while selected preparation and native saves finish.
- [ ] PREP-005 Analyze and reanalyze every UI-supported local codec/profile; track initial versus replacement separately, with key/BPM/grid/waveform and gain where offered.
- [ ] PREP-006 Expand accepted persistence beyond initial narrow WAV/CBR MP3 wherever the existing app offers broader analysis; VBR, AIFF, AAC/ALAC/FLAC need their own results.
- [ ] PREP-007 Cover missing/corrupt/stale companions, very short audio, long sets, unusual sample rates/channels/bit depths, silence and analysis cancellation.
- [ ] PREP-008 Scope analysis completion memo to library/native identity/result; source B cannot inherit source A's local-only completion.
- [ ] PREP-009 Eliminate whole beat-array GUI admission/ACK serialization for large pending grids with an exact sparse protocol and full lifecycle proof.
- [ ] PREP-010 Import single/many files and folders with mixed supported/missing/unreadable entries; stage validation so an error cannot leave unsaved partial imports.
- [ ] PREP-011 Register exact imported identities once, preserve existing originals, append memberships and publish initial analysis in correct dependency order.
- [ ] PREP-012 Edit metadata/cues/tags before registration completes; test Retry and Discard without orphaned dependent queues or unfinishable Quit.
- [ ] PREP-013 Conversion preserves original audio and updates exact native locator, logical binding, pending intentions and provenance atomically; captured requests remain immutable.
- [ ] PREP-014 Verify conversion/locator-only authorities are independently discovered at startup and unfinished operations recover without another domain being present.
- [ ] PREP-015 Source A pending conversion cannot starve current-source B; define queue partition/switch policy and retain unresolved foreign work.
- [ ] PREP-016 Fix Links must require actual matching audio identity; known-size mismatch is not acceptable even with only one filename candidate.
- [ ] PREP-017 Incomplete/capped/permission-failed relink inventories cannot assert uniqueness; report incomplete search and keep uncertain items unresolved.
- [ ] PREP-018 Late relink results cannot replace a newer accepted path, source or Restore generation; persist exact rebind before success.
- [ ] PREP-019 Verify Collection hide, playlist removal, native removal and confirmed Collection-and-disk deletion have distinct scope and honest durable outcomes.
- [ ] PREP-020 Deletion retains other playlist/native/media references and original conversions; shared files, aliases and hard links cannot be removed by logical-song deduplication.
- [ ] PREP-021 Complete lossless native Restore where offered: identity, playlists/tags/cues/analysis/art/history and audio return without adopting a foreign row or file.
- [ ] PREP-022 Partition Graveyard history by exact source identity; full backup/restore includes necessary historical identity records in a different empty destination.
- [ ] PREP-023 Inject save/Trash/rename failure: no success toast or dependent prep after failure; rollback never overwrites newly arrived unrelated audio.

## Artwork accuracy, scraping performance and persistence

Artwork is a required part of this private beta. Optimize the full pipeline:
eligibility → query construction → connection/likes index → search/extraction →
candidate scoring → image download/decode → owned selection → original/index
publication → thumbnail/display → native/device derivative. A fast wrong cover is
a failure. Local artwork, artist pictures and fallback images are separate kinds
of ownership, not interchangeable successful track matches.

- [ ] ART-001 Trace every automatic and explicit entry point, including title/artist-edit retries, source-exclusion configuration, startup, resume, context Fetch, URL, picker and Rescan.
- [ ] ART-002 Verify fresh default OFF, opt-in ON and legacy preserved setting; automatic OFF prevents all automatic network admissions while explicit user Fetch/Rescan still works.
- [ ] ART-003 Test without an account, with the tester's own new account/credentials, expired token, revoked Keychain item and canceled credential prompt; never ship Sonny's connection data.
- [ ] ART-004 Build a reviewed diverse query corpus: exact originals, same-title different artists, features versus collabs, edits/remixes/VIPs, live sets, obscure uploads and unknown artists.
- [ ] ART-005 Record expected cover/provenance or expected abstention independently of the scoring implementation; review both false positives and missed covers.
- [ ] ART-006 Verify cleaned title and majority artist queries preserve editor/featured context and do not send filename garbage or trusted personal aliases from another user.
- [ ] ART-007 Test likes-first, original uploader, editor uploader, reposts, compilation/profile/default photos, excluded uploaders and ambiguous low-confidence matches.
- [ ] ART-008 Inspect query expansion and candidate rescoring cost; reuse normalized title/artist/signatures and avoid repeatedly extracting/downloading the same accepted candidate.
- [ ] ART-009 Measure cold and warm1/100/1,000+ track batches: useful covers/minute, requests per song, network wait versus CPU, cache hit rate, disk bytes, p95 latency and UI heartbeat.
- [ ] ART-010 Count search/extractor/image/likes refresh requests separately; a warm no-op must not repeat network work for already fulfilled unchanged selections.
- [ ] ART-011 Add bounded in-flight deduplication/caching only after identity-aware keys/invalidation are specified; a source change or manual removal must not be hidden by a stale cached result.
- [ ] ART-012 Reuse immutable account/likes/search observations within their valid scope; test refresh age, account change, title/artist change and exclusion change.
- [ ] ART-013 Benchmark bounded concurrency and connection reuse against serial behavior; obey provider limits/backoff and preserve manual work priority without starting unbounded workers.
- [ ] ART-014 Verify explicit deadlines/cancel checkpoints across DNS/connect/read/extractor/subrequests; socket timeout alone is not proof of a hard operation deadline.
- [ ] ART-015 Cover offline, captive portal,401/403/404/429/5xx, throttled/never-ending responses, redirects, TLS errors and partial bodies without retry storms or GUI blocking.
- [ ] ART-016 Bound response bytes, decoded pixels and image dimensions; test malicious/broken/unsupported images, misleading content types, EXIF orientation and corrupt downloads.
- [ ] ART-017 Negative/retry caches distinguish true no match from temporary outage/rate limit; restart/reconnect must retry appropriately without permanent false absence.
- [ ] ART-018 Manual image, explicit URL and sticky Remove win over every late single/bulk/default/profile result, even after source A→B→A or Restore.
- [ ] ART-019 Exact source/song/repository generation and latest per-target token are rechecked after worker join before any art/index/canonical mutation.
- [ ] ART-020 Test simultaneous manual edits and background batches; preserve newer choices, names, exclusions, sticky blocks and unrelated artists/tracks.
- [ ] ART-021 Distinguish real track cover, artist avatar, fallback/default photo and generic SB placeholder in cache and exports; Turn Up-style manual cover must never turn into fallback on Sync.
- [ ] ART-022 Original selected image remains canonical and reusable; decode/resize thumbnails once per meaningful key/size/DPR and cache derivatives without damaging originals.
- [ ] ART-023 Complete selected artwork before Track Info's first frame; verify no blank→pop-in or source-crossed image while scrolling and editing.
- [ ] ART-024 Review cover picker candidates, URL paste, preview, selection, retry, cancel and keyboard behavior with source/provider failures.
- [ ] ART-025 Artist Pictures upload/rename/delete/default pool/assignment and merge/unmerge retain independent source ownership and truthful native admission/save outcomes.
- [ ] ART-026 Bulk art saves use sparse bounded durable transactions rather than a full manifest rewrite per result; measure fsync/index/canonical work and preserve failed prefixes.
- [ ] ART-027 Test full disk, index save failure, original publication failure and thumbnail failure; keep the previous accepted image and exact retry ownership.
- [ ] ART-028 Stop/cancel/normal Quit/restart/source switch/Restore join all artwork owners and reject every late result without discarding accepted progress.
- [ ] ART-029 Compare artwork/cache/preference counts across app update; unchanged art does not trigger a full rescan or mass fetch.
- [ ] ART-030 Inventory and complete curated native rekordbox cover publishing, including ImagePath/related metadata, bounded file lifecycle and normal native reopen; exporter-only art is insufficient for a native-cover claim.
- [ ] ART-031 Verify track/artist art and provenance in canonical backup/restore; never embed service credentials in backup, logs or exported metadata.
- [ ] ART-032 Measure stock fixed-size model thumbnails, CDJ derivatives and Rockbox art independently; verify exact pixels/encoding/offsets/links and physical quick loading.
- [ ] ART-033 Add deterministic network-server/provider fixtures and meaningful end-to-end GUI/controller tests for uncovered boundaries, then run a small consented live-service corpus with reviewed matches.
- [ ] ART-034 Determine whether an unrelated theme/column preference save incorrectly invalidates an accepted manual artwork request through the ordinary repository generation; preserve intended restore/source fences without discarding compatible work.
- [ ] ART-035 Define and test turning automatic artwork OFF during an active automatic pass: stop new automatic admissions and handle in-flight work predictably while preserving independently requested manual work.
- [ ] ART-036 Profile existing per-song pacing (`ArtFillWorker.DELAY = 1.6`) and bounded provider operations separately from useful download time; optimize justified redundancy/concurrency without blindly removing rate control.

## Generic USB/CDJ and iPod-as-USB

- [ ] USB-001 Treat iPod USB exactly like a generic USB: actual volume name/type, explicit Export/Delete, independent choices and never automatic Sync/idle catalog comparison.
- [ ] USB-002 New unconfigured USB offers Configure; Not now is quiet for that attachment, reconnect offers again, and existing catalogs open unchanged.
- [ ] USB-003 Export-dialog behavior: exactly one target auto-selected;2+ require user selection; support explicit multiple selections and changing/removing targets while open.
- [ ] USB-004 Device/Songs/Playlists tabs show the selected tab's actual content after eject/replug/type changes; playlist frame/order matches familiar stock layout.
- [ ] USB-005 Selected-device eject navigates to a surviving neighbor; shared stock/USB roles retain physical busy/mount ownership and cannot eject a replacement at the same path.
- [ ] USB-006 Export fresh small and thousands-track playlists to FAT32 and exFAT; compare actual both-catalog members/order/metadata and analysis paths, not just media files.
- [ ] USB-007 Complete the explicitly deferred fresh90-song SONNY.AIF export to SONNY USB; use its retained partial-media proof and only a fresh specifically scoped reset if needed.
- [ ] USB-008 Existing-playlist export asks named Overwrite; cancel writes nothing, acceptance changes only selected targets and preserves unselected catalogs/media.
- [ ] USB-009 Verify repeated songs/shared playlists/source aliases reuse appropriate media and never export duplicate audio accidentally.
- [ ] USB-010 Test NFC/NFD, non-ASCII, case/trailing-dot/space, FAT-equivalent names, collisions and long names; resolve equivalent spellings without admitting links or aliases.
- [ ] USB-011 Compare first export, unchanged overwrite, metadata/art-only overwrite, one-addition, reorder and delete; unchanged audio should not be re-read/copied without a necessary proof.
- [ ] USB-012 Throughput profile fast USB-C versus SONNY USB; separate device transfer limit from metadata syscalls, hashing, analysis/rendering and blocking UI stages.
- [ ] USB-013 Byte/filename progress advances during large files and states the actual phase; completion is joined success, not an arbitrary early100%.
- [ ] USB-014 Cancel at planning, source wait, large copy, catalog publication and cleanup; no false success, lost unselected data or silent recovery leftovers.
- [ ] USB-015 Full device, unplug, read-only remount, changed volume identity, foreign file arrival and late catalog changes preserve correct recovery and originals.
- [ ] USB-016 Replace vacancy-check→replacing-rename races with exact ownership/no-replace publication; rollback cannot overwrite a concurrently arriving unrelated file.
- [ ] USB-017 Delete selected playlists quickly, prune only unreferenced owned media/art/analysis and preserve external playlists, references and user files.
- [ ] USB-018 Validate Import My Settings and exported settings across supported models without silently substituting another device's defaults.
- [ ] USB-019 Reopen exported USBs in native6.8.7/6.8.8/7 and compare both Device Library and OneLibrary/Device Library Plus after export, overwrite and deletion.
- [ ] USB-020 Perform physical player checks on actual target firmware: playlist order, artwork, cue/loop/grid/key, waveforms, search/sort, settings and playback near beginning/end.
- [ ] USB-021 Validate CDJ-3000X/OneLibrary separately from legacy player support; latest desktop rekordbox compatibility does not certify the generated USB library.
- [ ] USB-022 Verify generic-USB initialization/export/delete never touches stock iTunesDB, iPod_Control or the other logical role's selection/state.
- [ ] USB-023 Confirm whether a dedicated Reformat USB entry is actually exposed; the initial static inventory finds only an unexposed maintenance backend. If offered, test on a dedicated expendable generic target with exact identity/partition/filesystem confirmation, cancellation/failure and subsequent Configure/export; never run it against stock/dual by label alone. This row does not request a new UI entry.

## Stock iPod, Rockbox and dual

- [ ] IPOD-001 Test actual supported Video/Classic firmware/database/checksum/mount profiles; remembered model names must not grant writes.
- [ ] IPOD-002 First selected export, no-op Sync, one-addition/removal/reorder, metadata-only and artwork-only Sync preserve correct song/native identities.
- [ ] IPOD-003 Successful Sync returns both primary buttons to Export when no accepted pending work remains; newer edits stay Sync and omitted unsupported sources do not stay falsely dirty.
- [ ] IPOD-004 Already-linked playlist selected for Export uses incremental Sync; new-playlist Export batches exact linked pending work in one captured operation.
- [ ] IPOD-005 Unchanged playlists and known unchanged rejected sources are not repeatedly hydrated/read; inspect long-set and WAV header fallback paths with byte/open telemetry.
- [ ] IPOD-006 Validate each advertised source codec per destination; stock omissions and Rockbox-supported WAVs remain separate truthful profiles.
- [ ] IPOD-007 Never duplicate identical song exports after repeat Sync, shared membership, interrupted retry or different logical aliases.
- [ ] IPOD-008 Manual Turn Up-style cover survives export/Sync/reconnect; model-specific artwork has correct dimensions/encoding/offsets and fast physical display.
- [ ] IPOD-009 Delete one/many playlists quickly without retained-pixel compaction or unrelated media backup; shared music/artwork stays referenced correctly.
- [ ] IPOD-010 Preserve foreign files/catalog references during failure compensation; an acknowledged copied file is not always safe to unlink after an external catalog adopts it.
- [ ] IPOD-011 Test Play Counts/playback sidecars, external iTunes changes, disconnect/remount, late source edits and no-op shadow acceptance without false pending status.
- [ ] IPOD-012 Validate cancellation and recovery before/after inner stock commit and outer dual failure; restore/reconcile disposable shadow truth without destructive guesswork.
- [ ] IPOD-013 Rockbox selected export/append adoption/order/artwork/config/playlist paths/reconnect/delete behave as offered on actual firmware.
- [ ] IPOD-014 Dual stock/Rockbox single-copy operations preserve both destinations, independent codec/art policy and exact one physical ownership lane.
- [ ] IPOD-015 No full iPod backups or idle whole-media scans; bounded current-operation recovery retires only after verified success/rollback.
- [ ] IPOD-016 Verify physical iPod playback and artwork after eject/reconnect/reboot, including very large capacity libraries and free-space/art-slot limits.

## Playback, waveform, browsing and daily workflow

- [ ] UI-001 Fresh and Reset appearance uses white accent/readable lettering/reference columns; existing saved user colors, columns and shortcuts remain intact.
- [ ] UI-002 Format stays beside the overlay scrollbar; BPM decimals fit at native font/DPR, and narrow/wide windows preserve readable fixed columns.
- [ ] UI-003 Collection/Artists/regular/smart/custom/device views preserve multi-selection, exact duplicate occurrence, sort/filter, scroll anchor and drafts during background publication.
- [ ] UI-004 Selection/load prepares the correct artwork/waveforms/audio together; no early incomplete first frame or older track pop-in.
- [ ] UI-005 Load/Info/context actions operate on the visible active view and current source, including filtered Artists and USB rows.
- [ ] UI-006 Late USB load cannot replace a newer Collection load; late decoder/manifest/errors cannot alter a newer track or Play/Pause/Stop/seek/cue intent.
- [ ] UI-007 Test every enabled player layout (current UI:1 Player), enabled audio devices, device loss, Bluetooth/sample-rate changes and app activation/deactivation. Verify disabled layout choices remain unavailable; unoffered multi-deck layouts are a separate product decision.
- [ ] UI-008 Play/Pause/Stop, seeking/scrub, first-beat positioning, loops, previous/next and EOF/autoplay work with duplicate occurrences and source changes.
- [ ] UI-009 Verify very short remaining tails, exact EOF and sub-start-threshold clips end once rather than waiting silently; include immediate seek/retry controls.
- [ ] UI-010 Hot/Memory/Session CUE momentary holds release on key-up, modal entry, source change, deactivation and shortcut recorder; no stuck playback/cue.
- [ ] UI-011 Test every editable shortcut/modifier conflict, press/release ordering and macOS accent-entry behavior; right/middle waveform clicks cannot act as left seek/zoom gestures.
- [ ] UI-012 Verify BLUE/RGB/3BAND, main/mini, zoom/LOD/beat grid, smooth motion and selected previews against approved references; keep existing RGB behavior.
- [ ] UI-013 Continue browsing, playing and editing during thousands of metadata/artwork/analysis writes and device transfers; record worst input/paint/audio gaps.
- [ ] UI-014 Review all dialogs for meaningful error/retry/cancel and stale selection safety; routine completion remains quiet and errors never imply an unsaved success.
- [ ] UI-015 Normal Quit with every active worker drains or retains actionable durable recovery, terminates helpers and does not lose later preferences.

## State, backup/restore and account isolation

- [ ] STATE-001 Canonical manifest plus referenced records materialize into an exact portable snapshot; missing/tampered/foreign records fail before any restoration.
- [ ] STATE-002 Small preference/curation changes update sparse records without whole-library rewrites; failed save retains exact previous state and honest UI status.
- [ ] STATE-003 Full backup/restore round-trips art originals/provenance/defaults, historical Graveyard identities, organization, pending jobs and provider namespaces into a different empty root.
- [ ] STATE-004 Restore drains directory import, artwork, analysis, native/device and preference owners; no old-generation callback publishes afterward.
- [ ] STATE-005 Interrupted `.state-transaction` recovery and retired-record cleanup preserve both generations until verified resolution; malformed recovery never triggers a guessed sweep.
- [ ] STATE-006 Source switching partitions all curation, history and pending domains using exact library/native identity; same IDs and same paths alone are insufficient.
- [ ] STATE-007 Second app instance/process helpers respect the shared lock; stale/foreign locks produce an actionable outcome without silent takeover.
- [ ] STATE-008 Test application update, OS restart, network-volume loss, permissions/full disk and shutdown while saves finish; no false success or auto-reset of existing curation.
- [ ] STATE-009 Account credentials stay in the recipient's Keychain; logs/backup/package never contain tokens, private likes metadata or Sonny's personal data.
- [ ] STATE-010 Exercise encrypted backup password/cancel/wrong-password/corrupt-archive paths, versioned restore and manual backup location selection without partial overwrite or credentials in diagnostic output.

## Clean-user portability, installer and updates

- [ ] INSTALL-001 Scan shipped production code/assets/config/defaults for machine/user paths, library IDs, app versions, device names, API credentials and personal allowlists; classify developer-only references separately.
- [ ] INSTALL-002 Actual new Mac account has no Python, .venv, Rust/Xcode, repository checkout, shell configuration, SoundBox AppData, trusted local certificate or saved Keychain items.
- [ ] INSTALL-003 Download the exact app-only Apple Silicon installer through the link the tester will use, with real browser quarantine; verify bytes match the reviewed receipt.
- [ ] INSTALL-004 Install and first-open via the ordinary manual Privacy & Security flow; record precise short instructions. No security-disabling commands, quarantine stripping or developer-path requirements.
- [ ] INSTALL-005 Verify the fresh three pages: discover/choose library, clear Clean/Keep consequences/examples, independent artwork option OFF; no developer jargon or long confusing choices.
- [ ] INSTALL-006 Empty/missing/moved/read-only/external/nonstandard library paths and access denials are recoverable; cancel makes no accidental source/artwork change.
- [ ] INSTALL-007 General-user native admission is a real supported path, not a copied Sonny authority or permanent disabled cleanup checkbox; validate each offered domain against that user's exact library/version. Define supported version/schema enrollment so ordinary users do not need developer-written permission JSON.
- [ ] INSTALL-008 Clean and Keep, artwork ON/OFF and account connected/unconnected all reach usable correct state with complete existing features.
- [ ] INSTALL-009 Test thousands-track first launch and progressive durable processing; progress/pause/resume/errors are understandable and normal use stays responsive.
- [ ] INSTALL-010 Package includes all personal modules/dependencies/native helpers/licenses/resources needed for offline launch, artwork and selected exports; no feature is removed for this pilot.
- [ ] INSTALL-011 Inspect the full shipped closure, architecture/minimum OS, framework links, runtime code provenance, private fixture absence and absence of embedded recipient/Sonny state.
- [ ] INSTALL-012 Verify app-only replacement retains curation, settings, artwork, credentials, original media/conversions and unfinished recovery; no obsolete bundled code survives an upgrade.
- [ ] INSTALL-013 Installer refuses when app/native helper is running or the target identity is uncertain; never kills/starts real apps or installs onto an unrelated volume.
- [ ] INSTALL-014 Check for Updates uses the exact official personal channel, correct numeric build and stable asset URL; test current/newer/older/reordered/invalid/missing/rate-limited/offline responses.
- [ ] INSTALL-015 Download/install a newer RC and relaunch in the clean account; first-launch setup does not repeat, no full re-clean/re-art run occurs, and all curation stays exact.
- [ ] INSTALL-016 Verify the existing shared GitHub link serves the intended version across updates; private-beta artifact delivery must not accidentally become an unreviewed broader release.
- [ ] INSTALL-017 Confirm uninstall/reinstall leaves user data recoverable, and a new account cannot read/use another account's library authority or credentials.
- [ ] INSTALL-018 Give the friend build ID, download/open/update steps, expected first-run work, a concise feature test script and a privacy-conscious way to return failures/logs.
- [ ] INSTALL-019 Test a different username/home, spaces/non-ASCII paths, standard account, missing network share, renamed external drive and denied removable-volume access; actionable choices replace developer-path assumptions.

## Confirmed defects to reconcile before RC

The critical ledger contains reproduced failures, not merely speculative leads.
Re-read current code before fixing; some rows are explicitly Delivered. The
remaining rows below must get an updated disposition, regression and retest.
No existing full-gate pass by itself closes them.

- [ ] GAP-001 Foreign file overwrite during CDJ publication/recovery; stock rollback deleting a foreign replacement or newly referenced acknowledged copy.
- [ ] GAP-002 Removal rollback overwriting unrelated arriving audio; exact no-replace/claim-and-verify ownership for all sibling primitives.
- [ ] GAP-003 Folder Delete absorbing unconfirmed new descendants; ordinary and smart subtree envelopes.
- [ ] GAP-004 Fix Links filename-only/wrong-size/partial-index uniqueness, late stale relink completion and save-failure reporting.
- [ ] GAP-005 Missing independent conversion/locator startup recovery; stale binding/metadata provenance after conversion; source queue starvation.
- [ ] GAP-006 Unsaved partial import and folder/menu adoption races; dependent metadata orphan after registration Discard.
- [ ] GAP-007 Local hide/Restore/art-picture rename success after failed save/refused admission; exact rollback and honest outcome propagation.
- [ ] GAP-008 Restore missing accepted import-task drain; source-crossed Graveyard history and backup omission of its identity archive.
- [ ] GAP-009 Late USB playback choice, held cue release inside shortcut recorder, short-tail playback and analysis completion memo source identity.
- [ ] GAP-010 Right/middle waveform action; each other undelivered row in the current ledger gets explicit disposition rather than disappearing from the plan.
- [ ] GAP-011 Any previously private prototype/candidate is reviewed against current production and validated afresh; old scripts/results are not current fix authority.

## Testing changes and release exit criteria

Use named suites `rb-db`, `metadata`, `tags`, `playlists`, `prep`, `analysis`,
`artwork`, `providers`, `library`, `state`, `usb`, `ipod`, `player`, `waveform`,
`ui` and `tooling`. Inspect `--plan` for exact cumulative coverage. Device suites
are separate; reader/UI smoke is not writer recovery proof. Existing focused
tests are reused only when they exercise the actual production assembly.

- [ ] TEST-001 Map every write-ledger row to success, no-op, failure-before/after-commit, retry, restart and source/Restore/Quit tests; add missing tests before declaring coverage.
- [ ] TEST-002 Use generated encrypted databases for causal failure injection and owned original-copy corpora for scale; never point tests at live AppData or a mounted real device.
- [ ] TEST-003 Native reopens and physical firmware checks have a clear manual script and independent expected values; do not turn synthetic test passes into native acceptance.
- [ ] TEST-004 Exercise real controller/MainWindow/default loader/scheduler wiring; injected happy-path mocks cannot stand in for production missing authorities or callbacks.
- [ ] TEST-005 Preserve independent raw/current-curation and artwork matching oracles; idempotence alone cannot prove cleanup correctness.
- [ ] TEST-006 Add native Cocoa gesture/pixel oracles where offscreen geometry or method calls miss actual input/paint behavior.
- [ ] TEST-007 Performance tests use fixed inputs, operation counters and robust budgets; record cache state/hardware and separate throughput, latency and UI stalls.
- [ ] TEST-008 Correct obsolete test setup without weakening assertions; no skipped tests, substituted fixtures, broad expected failures or arbitrary longer sleeps to hide races.
- [ ] TEST-009 Run affected integration/recovery checks after each focused fix; broaden only for changed dependencies or unresolved concerns.
- [ ] TEST-010 Freeze the final tree and pass bare `./check.sh --render`: exact complete inventory, zero skips/expected failures, lint/types/native/Rust/fixtures and inspected render.
- [ ] TEST-011 Build the exact clean commit, verify full signed composition and isolated packaged crypto/SQL/native startup; keep artifact provenance distinct from source checks.
- [ ] TEST-012 Run closed-state-preserving local installation and normal-startup verification under the current runtime contract; keep only one temporary previous app and retire it after acceptance.
- [ ] TEST-013 Complete clean-user install/update and native6/7 acceptance; all confirmed data-loss/wrong-target failures and required persistence gaps are closed before friend handoff.
- [ ] TEST-014 Record any remaining physical/network/OS coverage precisely; unsupported claims are not broadened by one successful friend test.
- [ ] TEST-015 Final handoff names exact artifacts/links/builds and complete versus pending evidence; remove stale pending claims and completed diagnostics without touching live curation or unfinished recovery.

## Next-context start and execution order

1. Read HANDOFF and this checklist; inspect dirty worktree and current agents.
   Finish only any explicitly recorded prior-delivery blocker before new work.
2. Establish the lane A baseline and the complete write/action coverage ledger.
   Reproduce current P1/confirmed failures on owned fixtures; close data-loss and
   wrong-target defects first, then durable-success/recovery and queue deadlocks.
3. Finish existing-feature native persistence gaps and exact-library fresh-user
   admission. Benchmark/optimize every write family with the same correctness
   oracles. Run artwork accuracy and performance as a full parallel workstream.
4. Complete current6 native reopen/device acceptance. Have Sonny perform6.8.8
   update, then revalidate. Only then migrate/test7 and its fresh-library lane.
5. Run the full personal clean-account first-install/update/credentials workflows,
   actual devices and realistic concurrent daily-workflow soak.
6. Freeze, complete final gate, build/package, guarded install/relaunch and exact
   artifact handoff to the friend. Publish only the intended reviewed artifact.

For each acceptance session ask Sonny for one concrete native/device step with
its expected outcome. His availability enables thorough testing; it does not
justify vague repeated permission requests or automated control of his real apps.
