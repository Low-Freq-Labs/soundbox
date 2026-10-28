# SoundBox — orientation (start here)

SoundBox is a native macOS music-library app that bridges **rekordbox → stock
or Rockbox iPods / USB players** and includes a high-refresh DJ-style player.
Browse the live rekordbox library, curate it, edit existing My Tags and cues, and drag
playlists onto a device with exact order, per-track cover art, and honest sync
semantics. Built by Sonny (Low Freq Labs) with Claude and Codex.

Product sequence confirmed October 26: physical iPod acceptance when Sonny's
device returns, music-library management refinement, then native CDJ USB export
with full library/performance acceptance against a rekordbox control. See
`docs/ROADMAP.md` and `docs/cdj-usb-export.md`. Existing `research/cdj_export/`
is standalone and its generated database has not passed CDJ acceptance. The
installed app's `/PIONEER` exclusion remains in force.

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

- Desktop rekordbox reads use a read-only DB/WAL snapshot; XML is a fallback.
  The October 26 prep plan authorizes narrowly scoped My Tag and Hot/Memory Cue
  writes through `soundbox.rekordbox_tags` and `soundbox.rekordbox_cue_store`,
  bound to explicit library/schema/version authorities,
  verified backup, exact expected track/tag identities and closed rekordbox/agent.
  Native tag acceptance passed October 26 on Sonny’s actual library; its exact
  authority enables selected-row assignments only on rekordbox 6.8.7.0303. A
  different library/schema/version requires separate acceptance. The separate
  `soundbox.rekordbox_write` proof remains temporary-copy-only, without a
  publish-to-source API. Native DB-only cue creation/deletion passed on No Trust;
  WAV/MP3/AIFF and older-aggregate references inform the accepted protocol.
  `CueLibraryAuthority` is provisioned only after native acceptance and binds each
  store to one explicit track. Both domains share the exact recovery root/lock/
  journal and one desktop worker owner. Pending operations reconcile before a
  different edit; unknown recovery or sync metadata fails closed. Cue identity
  revisions and native IDs are separate from waveform/display cue dictionaries.
  SoundBox’s Q setting optionally snaps newly captured cue points against the
  current RAM grid; existing cue triggers stay exact. Never read or change
  rekordbox’s global Quantize preference.
  Cue session overlays are immediately usable while a FIFO saves in background;
  only verified target metadata reaches Library/cache, without reloading audio or
  the whole library. Normal shutdown drains accepted cue intents before stopping
  TaskOwners; failure cancels the unconfirmed tail and reconciles source state.
  Abrupt exit can lose not-yet-started RAM intents. Transport state edges must not
  scan library artwork or trigger device discovery. Grid native references and
  actual-source downbeat/BPM acceptance passed October 27. Intelligent-playlist creation remains disabled pending its own proof.
  Sonny operates the rekordbox UI for reference/acceptance actions;
  ask for specific actions rather than driving it remotely. See `docs/rekordbox-prep.md`.
- Rekordbox analysis is read-only outside the accepted October 27 grid API.
  `soundbox.rekordbox_grid_store.PilotGridStore` requires an explicitly accepted
  exact library/schema/version authority, bound per operation to the track/UUID,
  with rekordbox/agent closed. Only DAT/PQTZ and native EXT/PQT2 invalidation may
  change; preserve all other sections, cues/audio/XML/2EX. BPM double/half alone
  may change the four native djmdContent fields and local counter. Manual BPM
  requires separate manual_bpm_accepted authority; it additionally updates
  AnalysisUpdated and only the primary DAT contentFile hash/size/history, with
  the native +4 counter sequence. Its recovery binds that exact metadata row.
  Ambiguous manual-BPM terminal rows may use a bounded read-only PCM WAVE
  header proof in that worker: exact integral milliseconds within native PWV3
  bounds, stable canonical identity/framing, journaled and revalidated through
  publication/commit/recovery. Exact-EOF inclusion passed native reference
  October28; unsupported endpoints fail closed. No audio payload read or decode. Respect
  Analysis Lock, preserve unknown metadata, reject unverified tempo/sync states,
  and retain encrypted DB plus exact analysis before/after backups. Shared
  Tag/Cue/Grid lock/journal recovery validates the whole set before publication;
  unknown state blocks all domains. Desktop previews and grouped saves share one
  worker lane; loaded-deck clicks may queue during the initial source probe,
  and preview from loaded RAM rows immediately. Saving requires a matching
  verified source base; RAM previews grant no authority. Normal quit drains those
  captured intents. Live backups retain verified encrypted DB/WAL/integrity/hash
  capture; full semantic inventories remain offline proof work. Closed-app checks
  stay fresh at every guard boundary. No audio reload, waveform recook or transport wait. Derived V5
  grid updates stay in SoundBox AppData. Source intelligent playlists remain
  a separate gate. See `docs/rekordbox-prep.md` for exact protocol and limits.
  Historical No Trust primary-PQTZ restores were explicitly granted only for its
  verified native-test baseline with closed rekordbox and native-saved 90 BPM;
  preserve those receipts and native counters/history. That grant does not
  authorize restoring another track or any secondary analysis cache.
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
  or otherwise retag source audio. Rockbox export may remove embedded pictures
  only from a verified disposable Mac-side payload. Composite payloads also
  receive the effective SoundBox title/artist so Rockbox never falls back to a
  scrambled stock filename. WAV copies use Rockbox's native RIFF
  `LIST/INFO` `INAM`/`IART` fields rather than ID3-in-WAV. Those display fields
  live in a small canonical INFO list immediately after `fmt`; competing source
  INFO/ID3/BWF/XMP metadata containers are removed only from the disposable
  derivative so vendor metadata cannot override or hide the intended display.
  Audio essence and technical properties remain verified unchanged. Effective
  title/artist is part of the composite playlist baseline, so either edit must
  surface Sync needed. The container audio essence and technical
  properties must remain identical, and the canonical source remains
  byte-identical. Full-library planning must never retain the complete set of
  transformed audio: it records an exact source/output hash contract and
  discards each derivative immediately. Commit rematerializes, verifies, copies,
  and discards one track at a time so Mac-side staging is bounded by the largest
  individual payload. Managed
  Rockbox covers are per-track uncompressed 8-bit paletted BMP derivatives:
  exact-size 160×160 and 116×116 fast paths, bounded exact square sizes read
  from the active WPS/SBS and retained across later theme changes, plus a
  lightweight dimensionless 160×160 fallback. The curated JPEG cache remains
  canonical and stock artwork is untouched. SoundBox never writes Rockbox firmware
  preferences during sync, including
  `.rockbox/config.cfg` and its `.new`/`.old` companions. Database auto-update,
  playlist capacity, tag display, theme and all other settings remain user-owned;
  they never independently signal managed-library drift or invalidate a plan.
  Active theme dimensions may be read to size exported artwork. Rockbox
  remains the only writer of `database_*.tcd`; SoundBox never manufactures or
  directly edits the binary tag cache. SoundBox may report that the index
  predates its latest manifest/config generation so an enabled updater awaiting
  Rockbox restart is not mistaken for a clean cache. The connected layout selects
  the exporter:
  stock-only uses the guarded stock planner; Rockbox-only/generic FAT32 uses the
  Rockbox filesystem planner; a FAT32 MA446 containing both `.rockbox/` and
  `iPod_Control/` uses only `soundbox.dual_ipod`, never two exporters. The
  first composite generation requires no existing SoundBox device manifest,
  builds one no-target-write plan, asks before commit, and is single-copy:
  stock-safe verified payloads live under `iPod_Control/Music`, enter iTunesDB,
  and are referenced by Rockbox playlists; stock-unsafe Rockbox-supported files
  live only under `/Music`; unsupported-by-both media is omitted and reported.
  Distinct canonical source paths retain distinct iTunesDB records, metadata,
  artwork, and device paths even when their audio bytes match; repeated playlist
  references to the same source path share its one record.
  For MA446, stock-safe means validated MP3 or stereo PCM16/44.1-kHz WAV; that
  restriction applies only to the iTunesDB-visible subset. Rockbox external art
  for every audio file is its sibling `filename.160x160.bmp` and
  `filename.116x116.bmp`, plus theme-independent `filename.bmp`, preventing
  folder-level art collisions. Rockbox files land
  before the recovery-first stock transaction, playlists follow the validated
  stock commit, and the unified schema-3 ownership manifest publishes last; any
  later-phase failure coordinates stock rollback with restoration of managed
  Rockbox rewrites and removal of only new files. For schema-3 managed audio,
  normal planning trusts unchanged manifest inventory; affected audio paths
  require live ownership/path/size checks without streaming their old payload.
  The exact changed derivative is verified on the Mac, the old live
  file is renamed within the device only for transaction rollback, and the
  replacement is atomically copied and verified one track at a time. Successful
  commits discard those transient old managed-audio bytes. Unowned paths remain
  untouched except for interrupted-generation recovery: a recovery directory
  bound to the still-current manifest may authorize adoption only when the
  freshly planned path, size, and SHA-256 match exactly. Completed proofs are
  journaled in disposable Mac-side state so verification itself resumes after
  another remount. Later playlist drops accumulate through new schema-3 generations,
  reuse exact managed payloads, source-authoritative repairs, and become true
  no-ops when unchanged. Generic Sync also recognizes append-only edits that
  Rockbox made to an owned M3U8. The frequent device poll uses only the bounded
  playlist-size baseline to surface the standard Sync-needed state; confirmed
  planning reads the changed small M3U8 and accepts only device paths that map
  unambiguously to existing schema-3 managed media. A genuinely new membership
  is persisted only into a SoundBox-owned custom playlist, then reconciled
  into both firmware views. Rekordbox playlists do not adopt iPod additions;
  device reconciliation never writes rekordbox. Exact hash-verified historical M3U8 prefixes
  bind device paths to stable track IDs even after local source removal or
  relocation. The planning view may rebind those proven IDs to their current
  canonical Mac sources while retaining original device ownership/recovery
  baselines. Reusing a moved source's derivative requires matching source
  SHA-256; a metadata change during cloud hydration permits one full re-hash
  against a new stable stat baseline, never acceptance of the old digest with
  unverified new timestamps. Repeated change fails closed. Completed source
  proofs may persist only in disposable SoundBox state and are reusable only
  for the same canonical path, filesystem/inode identity, size, mtime, and
  ctime. Normal composite Sync treats the committed manifest as its inventory:
  unchanged canonical path, size/mtime, effective display and media policy retain
  the original source/payload contract across ctime-only drift, for local and
  cloud files alike. This is not a fresh integrity proof and never refreshes
  stored proof timestamps. New, relocated or changed sources retain exact-byte
  verification; preflight may refresh their changed timestamps only after exact
  source SHA-256 equality against a stable baseline. Unchanged verified stock
  payloads reuse compatibility and DBID/path identity; live-size/ownership checks
  run for affected paths and explicit Repair, not every retained file. Changed bytes use
  normal verified replacement. Multiple old copies
  may converge only when their recorded verified source hashes are identical
  and their current canonical source is one file; surplus copies remain under
  ordinary reference-counted cleanup. Custom playlist reconciliation retains the first occurrence of
  each track, canonical source, or byte-identical source file, including older
  duplicates; distinct recordings are never merged by title/artist. Membership
  is baseline-checked and saved before device writes, and M3U8 changes are
  recovery-backed. Unknown/unowned paths are never imported
  automatically, and deletion/reorder edits fail closed. The proven app-side
  membership is saved before device writes so a failed device phase remains an
  honest retry. Generic Sync
  must route schema 3 back through that same composite planner: stable playlist
  IDs drive rename/move/order/membership/removal drift, old Rockbox playlist
  files are deleted only with exact recovery, and matching iTunesDB playlists
  reconcile in the same transaction. Sync reference-counts the complete managed
  playlist set: a track removed from SoundBox is pruned only when no remaining
  playlist uses it. Schema-3 media cleanup candidates require manifest
  ownership/path/size; art and playlists being rewritten or deleted retain
  exact live-byte checks, while unchanged retained artwork may reuse its
  matching manifest/cache identity without rereading device bytes. Each
  orphan is renamed into transient same-volume transaction recovery before the
  manifest publishes last, restored byte-for-byte if any later phase fails,
  and discarded only after success. Cleanup never depends on regenerating an
  older media or artwork profile from its source. Shared stock
  records additionally survive any remaining ordinary stock playlist reference.
  The top-level `/PIONEER` tree is reserved for rekordbox/CDJ. SoundBox may
  include its bytes only in whole-volume capacity, but must never use it for
  drift, claim it in ownership, verify it, clean it, repair it, import from it,
  edit it, delete it, or write below it. Case-insensitive spellings and symlink
  redirects fail closed. The separately confirmed generic-volume whole-device
  Erase is the sole exception; stock and dual iPods never expose that command.
  Finder-created `._*` AppleDouble files and abandoned `*.sbpart` temporaries
  below `/Music` or `/Playlists` are exact-baseline cleanup rows during explicit
  Repair or journal-backed interrupted recovery; successful cleanup removes only
  now-empty child directories. Normal Sync/polling never walks these trees just
  to find clutter. Finder sidecars never independently signal drift. Recovery
  journals signal interrupted work; unjournaled `*.sbpart` files are discovered
  by explicit Verify/Repair.
  An empty schema-3 library remains plannable so cleanup can never become
  unreachable after the last playlist is deleted.
  Schema-2 migration is not enabled. The old Rockbox exporter must still fail
  closed on schema 3. Never approximate dual boot with independent exporters or
  a duplicate collection.
  Rockbox's Playlist Catalogue sees only direct playlist files under
  `/Playlists` and sorts them alphabetically. Composite sync must therefore keep
  every managed M3U8 at that top level and prefix only its Rockbox filename with
  a zero-padded manifest sequence (`01 - NAME.m3u8`). SoundBox rows and stock
  iTunesDB titles use the concise playlist leaf, adding folder context only to
  disambiguate duplicate leaves; the manifest alone retains the full hierarchy.
- Stock playlist deletion may remove regular user playlists and the exact Apple
  **Videos** smart index only. Apple databases may mirror that index in the
  ordinary MHLP and canonical MHSD-5 datasets under different IDs; the device
  UI must select the canonical ID and deletion must remove both matching
  representations as one zero-media operation. Music, Movies, TV Shows, and
  Audiobooks remain immutable. On-The-Go is firmware-generated, not an iTunesDB
  playlist. Before a dual device has schema-3 ownership, the same guarded UI may
  delete those stock playlists with the same reference-counted cleanup: each
  track record unused by every remaining user playlist is removed from iTunesDB,
  and its linked device media is deleted only after the database commit. Source
  audio remains immutable; unowned Rockbox paths remain outside that transaction.
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
- Device activity and library integrity are separate. An idle device may
  always request normal macOS eject without Sync, Verify, or Deep Verify.
  Never force-unmount automatically. Device worker ownership lasts through
  QThread.finished; cancellation clears pending work and stops active work at
  a safe boundary. Shutdown must keep Qt events alive until workers stop;
  an expired timed wait never permits destroying a worker.
- Canonical audio acquisition captures one local snapshot while hashing. An
  expected digest binds captured bytes, so matching bytes survive cloud
  eviction/timestamp drift. Without a digest, require a stable capture with at
  most one hydration retry. macOS CloudStorage reads returning ENEEDAUTH may
  restart from byte zero after at most two cancellable three-second waits;
  partial reads never publish proofs or snapshots. Persistent errors still
  fail closed with source context. This readiness budget is separate from
  the single unstable-capture retry. Inspection/transformation/copy use local captured
  bytes; retain at most one source and working derivative. Disposable receipts
  and source proofs never confer device ownership. Preserve generation-58
  recovery until separately reviewed hardware acceptance succeeds.
- `library.json` is crown-jewel user state. Never wipe AppData or trigger a
  full cache rebuild casually. Back it up before installation or risky state
  work and verify curated data afterward.
- Full per-write export contract lives at the top of `soundbox/sync.py`.


Explicit composite Repair plans missing manifest-owned Rockbox audio and per-track
artwork as verified new copies when the guarded destination is absent. Normal
Sync trusts retained manifest inventory and does not look for missing files.
Existing occupants, symlinks,
and inaccessible paths are not treated as missing; commit rechecks vacancy and
rollback removes only files created by that transaction. A fully reconciled stock
library with no semantic changes retains its original iTunesDB bytes even when
the disposable stock shadow is absent.


The bounded composite drift poll treats source size/mtime changes as Sync-needed
hints, but not ctime alone: cloud eviction/hydration and filesystem metadata edits
can change ctime immediately after a successful commit. This UI status is not a
content-integrity certificate. Source proofs/receipts still include ctime.
Normal composite Sync also retains unchanged committed contracts across
ctime-only drift, without renewing their
original proof or manifest timestamps. New/relocated/changed sources and actual
payload acquisition retain exact-byte verification. Verify checks device
references/sizes; Deep Verify also hashes managed files; Repair explicitly audits
inventory and restores missing owned Rockbox payloads/art. None of these modes
turns a clean badge into a fresh source-content integrity certificate.

Connection presentation separates visibility from readiness. Debounced `/Volumes`
notifications and a 250-ms cached kernel mount-table check request discovery even
during playback or an eject-ready hold. The latter uses Darwin
`getfsstat64(MNT_NOWAIT)` without opening mounted paths, so mounting over an
existing directory cannot strand detection behind probe backoff. These are hints,
never external-device/write authority. A busy lane retains the request and retries
on worker retirement. The three-second local `/Volumes` timestamp fallback remains;
unchanged mount/timestamp state starts no idle discovery worker. Failed probes
retain 3–30-second backoff, bypassed by mount changes. Connected drift stays on its
three-second poll. Ordinary unmounted folders retain the 30-second attachment grace.
Verified external mounts appear disabled as `NAME · Checking…`. The first scan
computes sync drift before publishing the selectable row at QThread.finished;
unknown/failed checks say `Sync status pending`, never imply a clean result.
Stock/dual dashboard preload follows in the same serialized device lane, with
loading only in the bottom status after the row becomes ready. Successful preload
clears that status directly, without another immediate drift scan. A cached
dashboard is presentation-only; selecting while it loads uses the same serialized
preload. Writes stay guarded by operation ownership and firmware capabilities.
Remounts invalidate stale views.


Playback and iPod management are independent. Device discovery and ordinary
status polling must not check player.is_playing as a suppression condition.
Only device operation ownership pauses competing device reads/writes. Reconnect,
Rockbox tag-cache updates, unrelated firmware preferences and ctime-only cloud
metadata changes do not themselves create managed-library Sync drift.

Rockbox 4.0 does not support `sort playlists`; never add or require that key.
Its absence after firmware saves preferences is not Sync drift. Preserve existing
unknown config lines; retain the established numbered playlist filenames.

## Documentation map

| Doc | What's in it |
|-----|--------------|
| `docs/ipod-lifecycle-audit-2026-09.md` | Lifecycle inventory, failure matrix, implementation and acceptance boundaries |
| `docs/rockbox-playlist-oom-2026-09.md` | Read-only USB findings, firmware comparison and pending runtime OOM isolation |
| `docs/HANDOFF.md` | Single current session-resume note: live state, unfinished work, exact next checks, validation, and rollback facts |
| `docs/PROJECT_LAYOUT.md` | Checkout contents, external evidence archive, disposable outputs and retained fixtures |
| `docs/ROADMAP.md` | Active sequence, acceptance gates, public release and deferred work |
| `docs/sync-performance-audit-2026-09.md` | Composite inventory reuse, source-read counters and physical acceptance |
| `docs/archive/repeated-work-audit-2026-09.md` | Completed non-USB optimizations and before/after evidence |
| `docs/software-audit-2026-09.md` | Architecture, lifecycle, persistence and performance findings with evidence |
| `docs/release-boundaries.md` | Public/personal scope, provider contract and distribution boundaries |
| `docs/KEYBOARD.md` | Keymap editor, action catalog, default numeric cues and held-key routing |
| `docs/soundcloud-connection.md` | Personal OAuth/Keychain setup, likes index and acceptance boundary |
| `docs/PLAYER.md` | Player behavior, native architecture, cache formats, cue/transport/render invariants, QA references, and deferred grid work |
| `docs/archive/waveform-reuse-audit-2026-09.md` | Rekordbox-first waveform implementation, storage audit, diagnostic previews and remaining acceptance |
| `docs/DEVICES.md` | Rockbox/stock/dual-boot/generic layouts, capability boundaries, and validation |
| `docs/ARCHITECTURE.md` | Engine/desktop/native layout, data model, persistence, build/install |
| `docs/GOTCHAS.md` | Hard-won Qt/QThread/native-player/rekordbox/Rockbox traps |
| `docs/IDEAS.md` | Exploratory quality-of-life concepts that require UX workshop before commitment |
| `docs/cdj-usb-export.md` | CDJ USB R&D and `research/cdj_export/` status |
| `docs/archive/storage-audit-2026-07.md` | Resolved storage audit; full backups remain deliberate |
| `README.md` | User/dev product tour |

Read `docs/GOTCHAS.md` before touching workers, playlists, sync, removable
devices, caching, or the player. Read `docs/PLAYER.md` before changing waveform
rendering, clocks, transport behavior, analysis loading, or native code.

## Documentation discipline

- Documentation is part of the change. Update relevant docs before ending a
  session when behavior, architecture, invariants, setup, or status changed.
- Put committed/sequenced work in `docs/ROADMAP.md`. Put uncommitted concepts
  and UX explorations in `docs/IDEAS.md`.
- Keep shipped behavior, future work, and historical validation clearly
  separated. Never leave a completed feature presented as a TODO.
- On every **wrap up**, replace/update `docs/HANDOFF.md` with the single current
  handoff. Keep it concise and operational rather than appending a session
  diary: current branch/commit, installed/runtime state, unfinished work,
  exact next checks, validation performed, and any rollback that must remain.
  Remove facts that are no longer current.
- **“Wrap up” is a completion command, not a progress report.** Finish every
  in-scope pending task that can be completed in the current session, update all
  required documentation, run the required final validation, and—when the work
  is user-facing—build, safely install, relaunch, and verify the real curated
  runtime state. Then update `docs/HANDOFF.md`, commit and push the intended
  completed changes, and verify the worktree is clean and the current branch is
  published before saying the wrap-up is complete. Do not defer any of these
  steps for Sonny to request again. If a genuine blocker prevents completion,
  report the exact blocker and leave an operational handoff instead of claiming
  the session is wrapped.
- **Wrap-up consistency gate (required before commit/push and final response):**
  - Review the complete intended diff, including work inherited from an earlier
    session. For each changed behavior, search the README, both runtime files,
    handoff, roadmap, architecture, device/player guidance as applicable, and
    gotchas for the old behavior as well as the new wording. Updating one current
    section does not fix contradictory instructions elsewhere. Correct current
    guidance; label historical evidence explicitly rather than rewriting history.
  - Check documentation claims against implementation and tests. Audit recovery,
    eject, worker ownership, source safety and acceptance claims especially;
    passing tests do not prove unobserved hardware behavior or complete a partial
    architecture migration. Keep outstanding work explicit in the handoff.
  - Check `AGENTS.md`/`CLAUDE.md` byte parity, relative Markdown links, and
    `git diff --check`. Verify developer harnesses do not read live device/source
    state merely because their AppData is isolated. Run the relevant render gate
    for UI/harness changes and inspect its output, in addition to `./check.sh`.
  - Record the actual final installed build/revision, launch verification and
    post-quit/postlaunch curated-state baseline. Never copy an intermediate
    timestamp, hash or build number into the final handoff. If state changed,
    inspect the exact differences before establishing a new baseline; preserve
    the earlier evidence and distinguish UI-setting serialization from curation.
  - Distinguish repository HEAD from the installed app revision. Documentation
    and developer-harness-only changes do not require reinstalling an unchanged
    application. State that distinction explicitly; do not stamp an older binary
    as a newer revision to satisfy a provenance check. User-facing/runtime changes
    still require the full safe build/install/relaunch procedure below.
  - Finish documentation before the release commit where possible. Installation
    evidence added afterward may be a documentation-only follow-up; retain the
    true installed source revision instead of creating a rebuild/documentation
    loop. Remove stale pending/uncommitted statements before publishing.
  - After push, verify the remote branch resolves to local HEAD and the intended
    worktree is clean. The final response must separate completed release work
    from remaining physical acceptance or implementation limitations. Never claim
    that everything is correct merely because the automated suite passed.
- Do not hard-code a "current" test count as evergreen truth; trust
  `./check.sh`. Dated validation records may include the observed count.

Waveform preparation is import-first for complete PWV3/PWV5/PWV7 streams plus grid.
Compact V5 stores one 1-ms signed-detail supplement and builds zoom mips in
memory; incomplete imports retain the full fallback analyzer. Old V5 remains
ready while eligible local records upgrade one at a time in background preparation
or on selected-track load. Dataless sources defer until local; never invalidate
the whole library at startup. Preserve manual grid/cue
ownership and authoritative DB cues. MP3 source-origin changes must couple
analysis, audible playback, duration and seeks; never shift only the waveform.
Playback-origin header probes belong to the native decode thread; the bounded
manual-grid PCM endpoint proof uses the shared source worker. See `docs/PLAYER.md`.

## Development

Prepared browser rows live only in disposable `browsercache.json`, bound to
complete display inputs and a versioned checksum. They never supply native edit
or device-export authority. Bump `soundbox.browser_cache.SCHEMA` whenever display
derivation semantics change. Invalidating rows must also retire their checkpoint
key; artwork policy/visibility and source authority remain independently fresh.

Desktop accent roles come from `desktop.theme`, persisted as `settings.theme_accent`.
Use those roles for UI chrome rather than fixed purple values. The supplied SVG
wordmark stays in its 270×55 header slot; only BOX follows the accent. Waveform
and cue data colors retain their musical/source meaning.

Checkout hygiene and historical evidence locations: `docs/PROJECT_LAYOUT.md`.
Current guidance stays in `docs/`; superseded experiments belong in
`docs/archive/` and never override the current handoff or roadmap.

    ./check.sh            # lint/build/types + all offscreen/Cocoa tests; zero skips
    ./check.sh --render   # + hermetic offscreen UI render
    ./check.sh --native   # compatibility alias; Cocoa is always required
    ./build_native.sh     # arm64 CoreAudio/Metal dylib only
    ./build_app.sh        # personal → dist/personal/Soundbox.app, then stable signing

- The release gate routes every discovered test exactly once into offscreen or
  Cocoa processes and reconciles their inventories. Skips, expected failures,
  missing fixtures and unrun tests fail the gate. Headless Metal oracles and
  native drawable/playback tests always run on the supported arm64 Mac.
  Required offline iPod and frozen waveform snapshots are private, read-only
  inputs; see `docs/test-release-gate.md` for provisioning. Never read live
  devices/AppData or silently replace reference fixtures to make a gate pass.
- Run `./check.sh` **bare**—never pipe it through `tail`/`grep` in an `&&`
  chain because that can mask a failing gate.
- Python 3.14, pinned dependencies in `requirements.txt`, venv at `.venv/`.
- Native deck requires arm64 macOS 14+. Qt Multimedia is analysis-only
  (`QAudioDecoder` for AAC/ALAC); native-deck failure disables playback with a
  diagnostic while library management remains available.
- The offscreen render harness must suppress device discovery before creating
  MainWindow and throughout rendering; test-mode AppData alone does not isolate
  real removable mounts.
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

After the installed app passes verification and Sonny accepts it, retain only the
current `/Applications/Soundbox.app` and active source checkout. Remove the prior
rollback app, duplicate dist app and obsolete packaging intermediates; do not
accumulate zombie `.app` directories. Keep small install/provenance records and
curated library backups separately. Required offline fixtures and device recovery
material are not application rollbacks and remain protected.

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

Artwork worker ownership also lasts through delivery of its `finished` callback.
Completion retires the captured instance, never whatever currently occupies a
worker attribute; shutdown callbacks cannot start replacement artwork passes.

Dashboard serial and stock-firmware facts may persist under the exact volume UUID
plus library persistent ID, with provenance. Fresh observations supersede stored
facts; conflicting serials discard the old association. Remembered facts are
presentation-only and never grant mount/write authority. A sole historical Apple
record without an exact device match is not sufficient identification. Recovery
counts and database digests stay in backend snapshots, not dashboard rows.

Post-sync polling is held through the completion dialog only. Dismissal releases
the hold so subsequent SoundBox edits can update drift without reconnecting.

Artwork/suggestion dialogs and interactive waveform workers use
`desktop.task_owner.TaskOwner`: adopt before start, close admission on stop,
retain through queued QThread.finished delivery and final join, and dispatch
Qt events while draining. Window shutdown stops child owners before callbacks
can start more work and drains them before native-player teardown. Stopped
consumers reject late results. Socket/retry budgets are not a total cancellation
deadline and never authorize destruction of a running worker.

Canonical library/artwork persistence shares `soundbox.state_io` ownership.
Use `state_repository` for library schema/recovery/saves and `soundbox.artwork`
for local art/results/defaults (`artfetch` compatibility aliases remain). Backup
snapshots and restore publication share the same lock. Restore closes UI admission,
drains existing writers/results before final save, publishes staged validated
state, and reloads only after worker finished/join. Old area generations cannot
write after restore. Preserve `.state-transaction` if rollback fails; startup
recovers it. `.state-retired` is disposable only after commit/rollback retirement.
The app entrypoint holds an AppData instance lock. Never fault-inject live state.

Public/personal composition uses the tracked `Soundbox.spec` and
`scripts/build_composition.py`. `./build_app.sh public` writes
`dist/public/Soundbox.app`; no argument builds personal. Only install the personal
artifact on Sonny's Mac. Frozen profiles come from the signed bundle manifest;
user settings/environment cannot enable an excluded provider. Source runs may use
`SOUNDBOX_PROFILE=public`. Existing SoundCloud discovery is in
`soundbox_personal.discovery`; core `soundbox.providers` owns typed interfaces and
explicit module/action admission. Core retains canonical library/artwork state and device authority.
Online functionality is public-eligible only after service/content and dependency
clearance; personal packaging does not waive those terms.
`--isolated-check OUTPUT_DIRECTORY` launches an empty disposable-state packaged
smoke check, suppresses library/device discovery, writes a screenshot/report and
exits. It is not live-library or physical-device acceptance.


Keyboard mappings use the shared `desktop.keyboard` action catalog and physical
press/release router. Preserve text-focus boundaries, press-time cue page/slot,
modifier-independent release, alias hold ownership and release on deactivation,
shutdown or successful remap. Save before replacing the live map; restore reloads
both router and editor. See `docs/KEYBOARD.md` before changing keyboard transport.

Personal SoundCloud credentials and rotating OAuth tokens live only in Keychain.
Never log or persist them in library state, backups, source or build output.
The optional connection's disposable metadata/index is separate from curation;
connecting never clears manual art, misses or blocks. Account jobs use TaskOwner,
and test/isolated harnesses must never read live Keychain or auto-refresh accounts.
