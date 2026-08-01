# SoundBox current handoff

Updated: 2026-08-01 (America/Chicago)

Read this immediately after `AGENTS.md` / `CLAUDE.md`. This is the single
current resume note, not a session diary.

## Repository and installed app

- Branch: `main`. The Videos identity fix plus the Mac-side stock
  shadow/incremental artwork work described below are committed together in the
  stock-iPod wrap commit above `4552461`; start by checking status and recent log.
- `/Applications/Soundbox.app` is the corrected self-signed build from this
  exact tree. Its deep signature verifies, and it is running with the native
  CoreAudio/Metal deck active with the real 3,263-track / 83-playlist curated
  library loaded.
- Durable git-ignored recovery evidence is at
  `soundbox-ipod-ref/2026-08-01-stock-shadow-artwork-wrap/`: post-incident
  database/artwork metadata, shadow generations 1/2/3/5, pre/final
  `library.json`, a README, and a verified SHA-256 inventory. Temporary app
  rollback bundles remain under `/tmp/soundbox-free-art-ui-install.Ii6ox1/` and
  `/tmp/soundbox-partial-shadow-repair.kA913F/`, but the unique forensic evidence
  no longer depends on `/tmp` surviving. The final logging-isolation reinstall
  has its app/state rollback at `/tmp/soundbox-log-isolation-install.zDk1dw/`.
  Relaunch changed only the encoded `settings.columns_state` value; curated
  overrides/playlists and every other persisted field match its pre-install
  snapshot. Current `library.json` SHA-256 is
  `74e5d1bb12d601505eb5b163ea4d96b4782a2508d162cd7c39846d220637a80e`.

## Physical iPod state at wrap

- Target: black Enhanced 5.5-generation iPod Video, MA446LL, stock firmware
  1.3, Wolfson WM8758BG, FAT32, flash-modified to approximately 256 GB.
- The iPod was cleanly unmounted after the final 05:38 verification and is now
  beginning the user's overnight stock-firmware soak. Its last mounted path was
  `/Volumes/IPOD`, external FAT32 `disk4s2`, with no `.rockbox` tree.
- Last parsed stock state is repaired and verified: 100 indexed tracks and
  exactly 100 referenced physical media files totaling 2,681,783,526 bytes;
  `Favorites` has 73 entries, `SONNY.AIF` 19, and `Indie` 10. There is no
  `Videos` record in either playlist dataset and no `Play Counts`.
- Last verified `iTunesDB` SHA-256:
  `201655d4b65b5c95dd626640c2bcceca2b7bb7e481cf8fc86072771539c4ed9e`.
- Last verified ArtworkDB SHA-256:
  `35150373971f15831fc9fbedfdbb1af8028d26efbffab3079aba1dc34a38b935`.
- Last verified F1028/F1029 SHA-256 values are respectively
  `58db6fbdd9d202a6a67ed47cbfdd5d6e81f1f16f00167e7d4ba25569ec66fa86`
  and `6a278978c4150152688dc13411f56f2a9d7f24fcba454fcfbf4518acfb44dc78`.
- Stock firmware still shows its built-in On-The-Go entry. On-The-Go is generated
  by firmware and is not an iTunesDB playlist.
- The corrected physical Videos deletion committed at 02:25:44. It merged 26
  pending play-count entries and deleted zero track records, zero media files,
  and zero bytes. The recovery point is
  `.soundbox/stock-ipod-recovery/20260801-022544-delete-d04dfb85ad4b/`.

## Stock support completed today

The MA446 path is a real recovery-first stock database implementation, not a
folder-copy hack. `soundbox.stock_ipod` renders with pinned Clickwheel only in a
temporary target, reparses and audits the result, writes recovery material,
copies media/artwork atomically, and publishes `iTunesDB` last. It merges a
matching `Play Counts`, preserves technical/rating/bookmark identity, and never
retags or writes source audio.

The stock path now has an optional per-device Mac shadow under app data. The
shadow is an acceleration index, never authority: SoundBox requires the exact
remembered identity/model/serial plus live iTunesDB, ArtworkDB, and ithmb
fingerprints before using it. A missing, corrupt, stale, or mismatched shadow
falls back to the existing full device reconciliation. An exact reconnect can
reuse already-proven media hashes and produce a true no-op plan without
rewriting Apple's opaque database fields.

Cover changes are incremental after that shadow has been bootstrapped. Curated
JPEGs are converted on the Mac; a changed cover writes only one unreferenced
20 KB F1028 slot and one unreferenced 80 KB F1029 slot, then publishes the small
ArtworkDB and iTunesDB last. A slot still referenced by the old ArtworkDB is
never overwritten. Injected range-write failures leave the old database view
valid; unreferenced pixels are harmless and reusable on a later sync. Artwork
preparation after a manual cover edit is targeted to the affected image, and a
whole-library warm pass publishes its source-stat index only once per batch.

The installed app completed the one-time source-index warm for all 2,626 curated
images: 2,626 ready, zero failed. The resulting 623 KB index is at
`stock_artwork/ma446-video-rgb565-v1/source-index.json` under app data.

The real iPod bootstrap is now complete. At 04:34 the installed app reconciled
the user-created `Indie` playlist: 20 selected entries resolved to 10 stock-safe
tracks, one existing file was reused, nine media files (269,656,237 bytes) were
copied, ten unsafe files were omitted, 26 pending playback records were merged,
and 9.5 MB of artwork pixels were published recovery-first. Recovery point:
`.soundbox/stock-ipod-recovery/20260801-043403-a4287c3a1109/`. Shadow generation
1 exactly fingerprints the resulting database/artwork/media state.

A second exact `Indie` plan using the app's saved artist registry completed in
0.173 seconds as a true no-op: zero database, media, artwork, metadata, or
playback changes. An older concurrently prepared SONNY.AIF plan saw the live
database change and was withheld before any write, physically exercising the
stale-state guard. Custom SoundBox playlists are now included in reconnect
status, preventing a false out-of-date dashboard after this exact sync.

The subsequent SONNY.AIF artwork test exposed a partial-shadow defect. Shadow
generation 1 contained source mappings only for the Indie bootstrap scope, so
the old planner treated 18 already-present, stock-compatible SONNY tracks as
new and copied 439,170,875 duplicate audio bytes. This was not caused by the 18
effective unsupported selections; those were correctly omitted. Exact hashing
proves all 18 new files duplicate their old device counterparts byte-for-byte.

The planner now narrowly content-reconciles selected sources missing from a
valid partial shadow against live size/suffix candidates before copying. The
incremental artwork renderer also compares prepared RGB565 bytes with current
device slices when its local cache key is absent. A regression test covers a
playlist-scoped shadow followed by an older playlist with one changed cover.
Against an exact staged copy of the pre-incident recovery state plus the mounted
media, the corrected SONNY plan produces 100 final tracks, reuses all 19
compatible selections, copies zero audio bytes, and appends only 100,000 changed
artwork pixel bytes.

The physical repair completed recovery-first at 05:18. The verified pre-incident
database/artwork state and generation-1 shadow were restored, then the corrected
SONNY plan passed hard assertions and committed zero audio files, two artwork
ranges totaling 100,000 pixel bytes, and the database last. Recovery point:
`.soundbox/stock-ipod-recovery/20260801-051818-86edf983e61f/`. A reference-set
and SHA-256 audit proved exactly 18 physical files were unreferenced duplicates
of retained files before those 18 paths were removed. Artwork validates as 98
track records sharing 95 unique layouts. An immediate second SONNY plan and the
installed app's reconnect refresh were both true no-ops.

At 05:27 the user changed/re-uploaded Spaceman artwork again. The installed app
correctly copied zero audio and reused all 19 compatible tracks, but its plan
reported 100,000 artwork pixel bytes even though the final ithmb hashes were
byte-identical: the desired pixels already occupied newly free offsets. The
incremental allocator now hashes free slots and relinks matching pixels without
a range write. Shadow generation 3 fingerprints the resulting 100-track state
with 28 source mappings. Device-tab identity provenance remains available on the
snapshot for diagnostics but is no longer appended to visible model, firmware,
or serial values.

The final physical regression at 05:37/05:38 exercised both sides of the fixed
artwork allocator. Changing Spaceman to genuinely different pixels copied zero
audio and wrote the expected 20 KB + 80 KB ranges. Changing it back reused the
original pixels already present in free slots: zero audio, zero pixel bytes,
zero range writes, and only the 47,984-byte ArtworkDB plus iTunesDB linkage were
published. Recovery points are
`.soundbox/stock-ipod-recovery/20260801-053752-7b965a5c723b/` and
`.soundbox/stock-ipod-recovery/20260801-053807-e43f98822f19/`. The final audit
proved 100 database references equal exactly 100 physical media files, valid
artwork offsets, no `Play Counts`, and shadow generation 5 matching every
database/artwork fingerprint before the clean unmount.

The full 3,263-track effective-metadata audit found no control characters,
broken Unicode, surrogates, or non-BMP glyphs. Printable Unicode remains intact
in the UI/stock database. FAT/Rockbox path output now uses clean Latin
transliteration (`CØSTA` → `COSTA`, never `_`), preserves non-Latin names,
normalizes compatibility punctuation, removes decorative `*`/`?`, and protects
DOS-reserved components. Source files and tags remain untouched.

Destination compatibility is proven and destination-specific. Stock MA446
accepts validated MP3 plus stereo PCM16/44.1-kHz WAV only. The real iPod
reproducibly rebooted on `Hostage Situation - Loot`, a PCM24/44.1-kHz WAV, so
stock plans now omit every unsafe encoding with title/reason previews. SoundBox
curation vetoes are resolved before format filtering.

Physical acceptance completed:

- Apple/Finder three-track oracle preserved exact order and byte-identical
  media; GodLovesUgly, Scream Saver VIP, and Check It WIP played correctly.
- SoundBox authored and committed stock databases recovery-first, content-reused
  existing media, survived reconnects, and merged real play/skip deltas.
- Curated artwork displayed through real ArtworkDB plus RGB565 100×100/200×200
  ithmb payloads. The pre-generated cache contains 2,252 unique manifests / about
  229 MB derived from 2,626 usable curated images.
- Reused tracks received only authoritative SoundBox title/artist enrichment.
  Stock Artists displayed the intended `GRiZ`, `DENNETT`, `Subtronics`, and
  `Zeds Dead, Subtronics` indexes. The extra Artists → artist → All navigation
  level is generated by classic stock firmware and was accepted; do not distort
  metadata trying to remove it.
- Rekordbox `GENERES / HOUSE` resolved to 8 effective entries; stock included 6
  and omitted only `Loot` and `DONTTRUSTME`. Every included track played without
  another firmware reboot.
- Regular playlist deletion reference-counted only remaining regular user
  playlists. `Collection` retains owned tracks; orphan media/artwork is removed;
  loose pre-existing tracks are not swept. Physical deletion reached a clean
  zero-track/zero-user-playlist state.
- Checkbox multi-delete was physically accepted: two playlists were deleted in
  one guarded operation.
- Qt unsigned-64-bit Apple playlist IDs are carried as decimal strings, avoiding
  both `Signal(int)` truncation and QVariant signed-64-bit overflow. Device page
  refresh preserves the active Playlists tab.

## Videos and other Apple system lists

The real Apple database contained two smart `Videos` representations with the
same rules but different playlist IDs: an ordinary-MHLP alias and the canonical
MHSD-5 record. The first 22:31 deletion targeted the alias and therefore only
appeared to work; the canonical record never left and the later Favorites sync
correctly preserved it. The fixed read-only snapshot suppresses the alias and
exposes canonical ID `6873052597665276463`. Selecting that ID removes both
representations as one system index, never counts either as media ownership,
and validates every retained smart rule. A real-shape regression covers the
duplicate-ID layout.

The physical retry succeeded, and the later reconnect/bootstrap still contains
no Videos representation. Audiobooks, Movies, Music, and TV Shows remain present
and immutable. A stock-firmware post-eject boot check is still needed.

## Stock, Rockbox, and dual boot contract

Stock and Rockbox are permanent first-class SoundBox targets. The connected
layout chooses the exporter automatically:

- stock `iPod_Control` without `/.rockbox/` → guarded MA446 stock rules;
- `/.rockbox/` present → broad Rockbox filesystem rules, even when
  `iPod_Control` coexists;
- remembered MA446 identity cannot re-enable stock writes on a Rockbox layout.

Current dual-boot behavior intentionally uses only the Rockbox filesystem
exporter and keeps iTunesDB read-only. Rockbox `/Music` files are not
automatically visible to stock firmware, so stock fallback cannot encounter
unsupported Rockbox media. This route is selected from the mounted on-disk
layout, not from a guessed boot intention or a manual user mode switch.

The approved future dual-library design is **single-copy**, not duplicate:

- stock-safe files live once under `iPod_Control/Music/Fxx`, are indexed in
  iTunesDB, and are referenced by Rockbox playlists. For the current MA446
  contract, safe means validated MP3 or stereo PCM16/44.1-kHz WAV;
- stock-unsafe but Rockbox-supported files live once under `/Music` and never
  enter iTunesDB;
- unsupported-by-both files are omitted and reported;
- compatibility is evaluated per destination firmware: the conservative stock
  validator controls only the iTunesDB subset and must never remove a file that
  the connected Rockbox profile supports;
- one composite planner/manifest must coordinate ownership, artwork, deletion,
  recovery, stock database publication, Rockbox playlists, and rollback.

That composite planner is not implemented. Do not run the current stock and
Rockbox exporters independently to imitate it, and do not duplicate the whole
collection.

## Exact next-session sequence

1. Tonight, change nothing through SoundBox. Use stock firmware normally and
   verify Favorites, SONNY.AIF, Indie, the final Spaceman cover, playback, the
   absence of Videos, and the protected Apple menus over an ordinary listening
   session. Let firmware-created play/skip state accumulate naturally.
2. Next session, reconnect once and inspect the device/logs before any write.
   Confirm the dashboard/shadow state, parse any `Play Counts`, and preserve a
   final off-device stock snapshot with exact path/size/SHA-256 inventory.
3. Perform a read-only space audit before Rockbox installation. Identify
   recovery, Trash, and system usage before deleting anything; do not prune the
   on-device recovery tree until the final stock snapshot is verified.
4. Begin Rockbox installation/physical export acceptance: preserve recovery
   paths first, verify exact model/FAT32 target, install firmware, then test a
   tiny playlist for playback, absolute m3u8 paths, order, and curated artwork
   before attempting Collection scale.
5. Keep the approved composite dual-library planner as a separate later feature.
   Initial Rockbox hardware work continues to leave stock iTunesDB read-only.

## Validation

- `./check.sh` passes **618 tests** with one expected platform skip; pyflakes,
  native build, and mypy pass.
- Qt test mode now suppresses production file/crash logging. A focused 205-test
  UI run and the full gate left the pre-install production log byte-identical,
  so synthetic `boom` warnings can no longer contaminate hardware log audits.
- Shadow coverage proves atomic per-identity persistence, corrupt/incompatible
  fallback, exact reconnect no-op behavior, local-cover drift reporting, and a
  failed Mac shadow publication that never rolls back an already-valid iPod.
- Incremental-artwork coverage proves identical-cover deduplication, unchanged
  shared links, exact 100,000-byte changed-cover pixel writes, zero-write reuse
  of pixel-identical free slots, later nonmatching free-slot reuse, and old
  ArtworkDB/iTunesDB validity after an injected mid-range failure.
- Videos deletion has a hermetic duplicate-ID Apple-shape test proving the UI
  selects the canonical MHSD-5 ID, both matching representations disappear,
  the regular playlist/track/media state is unchanged, and Music is refused.
- The corrected live plan and commit targeted ID `6873052597665276463`, kept all
  73 tracks/media files, removed both Videos representations, retained the four
  protected Apple smart lists, and wrote the historical hash preserved in the
  physical recovery notes. Later syncs continued to parse no Videos record.
- Device-profile coverage proves `/.rockbox/` wins even beside a valid writable
  MA446 checksum-zero database. An end-to-end dual-layout regression exports a
  real PCM24/44.1-kHz WAV through `/Music` and a Rockbox playlist while keeping
  the coexisting iTunesDB byte-identical.
- UI coverage renders Videos as `System playlist`, sends exact decimal playlist
  IDs, supports checkbox multi-delete, omits the old Artist column, preserves
  the active page, preserves the 2×2 details layout and elastic title columns,
  keeps long stock-operation status text readable, and suppresses redundant
  visible-text tooltips.
- The final signed app verifies, relaunches, activates the packaged native deck,
  warms all 2,626 artwork entries without failure, and preserves curated state;
  the final `library.json` remains semantically identical to its pre-install
  snapshot. The last-mounted iPod's database/artwork hashes stayed byte-identical
  through reinstall; only the two explicitly approved cover tests changed them
  afterward.
  `git diff --check` is clean.

## Preserved fixtures and recovery

- Git-ignored physical captures live under `soundbox-ipod-ref/`, including the
  pristine stock baseline, Apple/Finder oracle, and SoundBox post-play reconnect.
- The durable 2026-08-01 wrap evidence is
  `soundbox-ipod-ref/2026-08-01-stock-shadow-artwork-wrap/`. Its inventory
  verifies the post-incident metadata, shadow generations 1/2/3/5, and curated
  state snapshots. It is not the still-pending full post-soak device capture.
- On-device recovery points live below
  `.soundbox/stock-ipod-recovery/` when mounted; do not prune them until a final
  clean stock snapshot exists off-device.
- Latest app/state rollback root:
  `/tmp/soundbox-log-isolation-install.zDk1dw/`.
- Physical duplicate-repair rollback root:
  `/tmp/soundbox-partial-shadow-repair.kA913F/`.
