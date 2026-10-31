# SoundBox current handoff

October31, 2026. Branch **main**. This wrap-up publishes the checkpoint based on
`1620900f9af4f826bb3502d321816d99704e6963`; use `git log -1` for its published commit.
Sonny explicitly requested wrap-up: validate and publish this checkpoint, then
start the direct-import work next session. No subagents are authorized.
Sonny asked to complete the overhaul and explicitly authorized careful computer
control for generated native references. Real-music UI edits and new source SQL
pilots remain outside that computer-control grant. Verify exact fixture selection.
The latest explicit approval, “Run the ten-file registration test”, is completed.

## Next session starts here — direct imports first

Sonny explicitly chose this as the next session’s first task. Both reported paths
are **still broken in the installed app**:

1. A brand-new audio file cannot be dropped directly onto a rekordbox playlist
   before being added to Collection. SoundBox must capture that playlist target,
   register/reuse the native song, place it in both Collections and append it to
   the requested playlist in order. No Collection-first workaround.
2. A song added to SoundBox Collection does not enter rekordbox Collection. The
   normal SoundBox import must automatically save one native content identity,
   with durable retry/restart and both apps seeing that same song.

Also keep direct SoundBox playlist drops in both Collections. Existing songs must
benefit without reimport or reanalysis. Screenshot examples: WHOLE CLUB ROCKIN /
SIX FIGURES (`sb-5adee0814145`,174BPM/9A) and Innerbloom
(`sb-19a5565cfb6d`,140BPM/6A). Preserve their saved musical data and stable IDs.
Automatic analysis remains silent/per-song; off means manual Analyze Track.
Bare registration and playlist membership must work with BPM/key blank and
automatic analysis off. Do not defer these import paths until general native
analysis/gain/metadata publication is complete. Acquiring a native ID must not
disable existing local edits or discard/reanalyze completed saved results.

Begin with the existing registration store/identity foundations, canonical-alias
proof for the old Seafile match, and remaining native-ID consumers. Wire a durable
import/destination queue through the one source worker; register/reuse before
native playlist membership and reconcile both Collection views. Use the accepted
native references already retained. The ten-file registration pilot is a proof,
not an implemented ordinary import path. Do not report this task complete until
both real user workflows pass native reopen/restart with no duplicate entries.
Defer RGB, additional UI polish and unrelated experiments until these paths work.

Starting code: `desktop/app.py` (`_on_files_dropped`, `_import_files`),
`desktop/rekordbox_playlists.py` (`_native_append`, durable membership FIFO),
`soundbox/rekordbox_registration_store.py` and `native_registration_plan.py`
(registration/reuse), `native_identity.py` (stable logical IDs), and
`soundbox/rekordbox_db.py` (`load_live`: currently playlist references + UUID pins).
Extend `desktop/tag_controller.py`’s existing source lane/recovery dispatch for the
registration domain; translate native IDs in cue/grid/tag/CDJ consumers before
activating bindings. Ordinary drops must not expose a separate publish/save step.


## Installed and running

Signed personal **build152** at `/Applications/Soundbox.app`, PID35574 after the cleanup restart. Original installation launch03:18:51. Build number alone does not distinguish the recent dirty releases. The installed
artifact retains its original dirty1620900… provenance and the fingerprint below;
this publication does not relabel or rebuild unchanged runtime code.

- Source fingerprint: `64e6ac25cf936d5a47b9eb4668707bdc190ed3fd5631b33729ded4c7a0aa3502`.
- Executable SHA: `941317a97690c68250271d7985d7eb7549ff60a518a0958c2cbb288a1a4e2ce6`.
- Sonny explicitly requested cleanup of extra apps; obsolete rollback bundles
  and duplicate packaging have been removed. The verified installed app remains.
- The original installation verification loaded3744 tracks/75 playlists with its native CoreAudio/Metal deck;
  preparation scheduled0 tasks, art fill fetched0 and all3107 stock derivatives
  were reused. Collection3231/Artists1563 inspected, Ready footer, deck empty.
  Existing Innerbloom remains140BPM/6A. No live audio/manual edit was injected.

Installed behavior:

- Folder expansion is off-thread. All dropped files enter/save Collection before
  header/bookmark probing, appear at the top and remain Collection members when
  dropped into SoundBox playlists. Native-playlist file drops are still gated.
- Manual/automatic detection runs silently per song, without popup/progress bar.
  Each saved result updates its row while later songs continue. Durable intents
  persist before worker admission and retire atomically with their saved result.
  Startup resumes unfinished jobs only after a matching verified source load,
  retaining original preferences. Failed jobs wait for explicit retry.
- Browser Analyze Track uses that queue; Rebuild Waveform is the separate display
  recovery action. Captured imports admit durable manual BPM/grid and Hot/Memory
  Cue drafts immediately. Late detection preserves newer drafts and deleted cues.
  These are migration input, not a second final native musical authority.
- Existing Innerbloom’s saved721-beat140BPM/6A result survives cloud timestamp
  changes through an exact captured-byte proof. It no longer falls back to the
  older724-row140.62 grid merely because ctime changed. Local edits use the saved
  base; translated EOF rows clip instead of vetoing every nudge. Earlier isolated
  native edit/restart proof retained0 audio reloads/0 waveform restarts.
- Native playlist create/rename/append/remove/delete feedback now derives from
  the durable FIFO immediately. Pending folders/playlists retain their generated
  identities for later queued edits. Verified source catalog/playlists remain
  unchanged until commit. Failure/discard restores current source presentation;
  newer revisions stop projection until receipt reconciliation. Deleting a new
  pending active playlist returns to Collection. Background creation completion
  no longer steals later navigation. The existing source writer/barriers remain.
- Whole-v3 near-integer tempo detection and common vector sidebar geometry remain
  installed. RGB92/128/184 motion is still open; no shader tuning occurred here.

**The shared native-library overhaul is unfinished.** Read the
[implementation plan](shared-library-workflow-plan.md); do not call the installed
local drafts or the ten-file pilot general two-way source publication.

## Verification and preservation

Latest wrap-up gate: `~/Library/Logs/Soundbox/wrapup-2026-10-31/check.log`.
The full bare `./check.sh --render` passed all1836 tests with zero skips/expected
failures:1776 offscreen in902.300s,60 Cocoa in62.281s. Lint, Rust/native build,
mypy83, required fixtures and inspected render passed. Runtime fingerprint stayed
64e6ac25… throughout; the installed app already contains that runtime, so this
wrap-up publishes the source/docs checkpoint without rebuilding or relabeling it.
Regenerated compiler/test caches are retired again after the gate. Installed
signature/binary, library, curation, source DB/XML and retained art/waveform state
are verified by `wrapup-2026-10-31/preservation.json`.

Earlier installation evidence: `~/Library/Logs/Soundbox/shared-imports-2026-10-31/`.

- `playlist-release-final-check.log`: lint, Rust/native build, mypy83, required
  offline fixtures, render and1776 offscreen tests passed (863.124s). The first
  Cocoa run had one cadence miss112.941fps against114fps; retained as failure
  evidence. Screen was unlocked and temporary caffeinate active; no threshold or
  source change was made. The entire60-test Cocoa phase subsequently passed in
  62.821s (`playlist-native-recheck.log`). The inventory verifier then passed all
  **1836 tests**, zero skips/expected failures. Both frozen phase reports and
  `playlist-test-inventory-verification.log` retain that complete result. Do not
  describe the initial shell invocation itself as a clean pass.
- The earlier playlist gate was deliberately interrupted for a queued-create/
  delete corner-case fix; its log remains. Runtime fingerprint stayed exact from
  the replacement gate through build/install. Build log: `playlist-build.log`.
- `playlist-package-verification.json` verifies18 changed packaged Python
  modules, all file-backed native/CDJ Mach-O sections, full Metal/schema,
  signature and personal composition. Research adapters remain excluded. The
  unused media probe/general registration store/identity projection are not app
  import routes; bundled Mutagen codec parsers are present.
- Packaged isolated smoke: native=true/devices0/library_loaded=false/art_worker=false.
  Hermetic render and actual-catalog UI replay inspected. Seven isolated-window
  playlist tests and eleven worker-gesture tests pass. The actual frozen75-list
  catalog replay preserved raw source rows with the writer lane blocked; warm
  feedback measured106–255ms, while cold first Collection rendering took about1s.
  Those are offscreen UI measurements, not native drawable/presentation claims.
- `playlist-installation.json`, `playlist-installed-window.png` and
  `playlist-installed-preservation.json` verify installation and real-state launch.
  No AppData reset, source DB/XML change, artwork fetch or waveform recook occurred.

Current crown-jewel `library.json` SHA after Sonny’s newer imports/curation and
normal cleanup quit:
`5d6ef43665a26eed4160f4e86c3ce99f2b373e583ec004199368afde8508eb44`.
Curation SHA `121982338dd5d9bd2c17662dd0acfb424b1bf6675f2ee9113f2069047a9e5a0e`;
analysis SHA `babddc112e795f8f3081d1adec9f3a08ce7b06f0951af639bbc6ea38d34fca1f`.
87 imports/1105 titles/490 artist overrides/10 custom playlists/520 hidden/
1 removal group/0 additions/24 aliases. Automatic BPM/Grid+Key=true,
first_cue=false; preserve current RGB preferences.4043 canonical art files and
3285 waveform files are unchanged by cleanup, as are all four source authorities.
Never restore earlier86/1105 or85/1104 snapshots over this newer state.

The earlier installed ef326e… snapshot remains historical release evidence.
The cleanup’s normal quit saved only columns_state; curation was unchanged.
`cleanup-2026-10-31/library-closed.json` exactly matches the restarted live state. Only the disposable preparation-index
metadata refreshed after restart; waveform payloads stayed exact.
Source DB/XML still match the earlier installed baseline. Temporary caffeinate
was already stopped; no power preferences changed.

## Cleanup — October31

Sonny explicitly requested removal of accumulated scrap files, old apps,
packaging, diagnostics and duplicate backup storage. Receipt root:
`~/Library/Logs/Soundbox/cleanup-2026-10-31/`.

- Removed22,117 generated files across317 exact targets: all five obsolete app
  bundles, duplicate dist, packaging, Rust dependency/debug caches, PyInstaller/
  mypy/Hypothesis/Python caches, abandoned isolated test folders and loose scratch
  scripts/screenshots/logs. No rollback app remains; the former temporary rollback
  root is retired. Build retains about18MiB of checked binaries/reports/proofs.
- Losslessly verified203 encrypted captures and consolidated seven additional
  copies with independent APFS clones; previously shared copies were retained.
  Runtime baseline.db and pending journals were excluded; source apps were closed
  and the shared source lock was held. Current recovery/acceptance archives remain
  complete and encrypted.25 diagnostic files were transparently compressed with
  identical readable bytes/paths.
- Retired41 redundant master.db checkpoints from the **completed**
  `playlist-management-2026-10-30` and `playlist-analysis-ui-2026-10-29` diagnostic
  runs, following the explicit cleanup request. Comparison reports/metadata and
  exact retirement hashes remain. Their old raw checkpoint replays are no longer
  available. Prepared-plan payloads, native sidecars, paired recovery archives,
  current shared-library references and required fixtures remain protected.
- Net disk space reclaimed is about6.8GiB; Logs fell from about6.7GiB to3.6GiB.
  APFS shared-block directory totals are not additive physical-use measurements.
  The remaining active native reference data is required for unfinished work.
- Verified live library/curation, source DB/XML, art/waveforms and authorities
  unchanged, installed signature/binary exact, checked native binaries and all
  required offline fixtures intact. Reopened the same installed app. This was
  cleanup/documentation only; no runtime source change or rebuild occurred.

For further work, keep diagnostics lean: reuse frozen snapshots for status/UI
checks, capture full DB pairs only where acceptance/recovery actually requires
them, and remove generated packaging/scratch output after verified installation.
Do not recreate a full-library snapshot for every inspection step. Retain small
comparison/approval/installation receipts and the required active reference set.

## Approved ten-file registration — completed

Sonny explicitly approved the prepared new ten-file scope; the old one-WAV grant
was not reused. Evidence at the current root above:
`registration-source-approval.json`, `registration-source-candidate/`, operation
`e0911862-d908-470f-ab31-8c79af8c2144`, plan SHA
`49ee62f052de82bb3da88d89d7be845cf3d44a6d5a7c224c7f1fab8d7c623d20`.

Publication at01:45:59 added ten generated `SoundBox registration - …` rows with
BPM/key blank, counter352672→352682. Only content/counter changed; entire planned
AFTER inventory, XML, generated audio and AppData verified. The shared journal
retired. Full encrypted evidence remains in shared-root archive
`media-registration-pilot-e0911862-d908-470f-ab31-8c79af8c2144`.

Authorized computer control opened rekordbox and inspected all ten distinct
entries at0.00BPM/blank key, Collection total3724, without Analyze/load/edit;
restored empty search/Info pane and quit normally. Separate
`native-media-registration-acceptance.json` accepted at01:59:41. Every non-registry
table/target/counter, XML and generated audio stayed exact. Native startup registry
changes are retained as redacted hashes/field names, complete_semantic_match=false.
Original transaction receipts remain native_acceptance=false. No general authority
was provisioned. Rekordbox/agent remain closed; all ten are still unanalyzed.
`sequential-formats-before-open` is a read-only checkpoint, not a new analysis test.

## Registration foundations and exact next work

Source code is present but **not activated for ordinary imports**:

- `soundbox.media_probe` uses bounded PCM/AIFF/FLAC and bundled MPEG/MP4 header
  parsers on captured files, without external ffprobe/decode/tag writes. Pure
  captured facts/row construction moved into `native_media` and
  `native_registration_plan`; research wrappers reuse them without widening the
  original narrow WAV scope.
- Distinct `media_workspace`/`media_pilot` markers, adapters and approvals retain
  encrypted recovery. Six tests each include nine actual exits. TEMP WAL
  normalization applies only to verified private working copies, never source.
- `rekordbox_registration_store` has its own explicit authority format, shared
  lease/lock/journal, content INSERT/counter-only SQL, exact-path reuse, encrypted
  paired recovery, completed-operation replay and bounded ordinary retention.
  Eleven synthetic tests include nine real exits, shared-domain exclusion,
  preservation of later native edits, and protection of acceptance/latest backups.
  `registration-store-native-reference-final-replay.json` reproduces the complete
  actual ten-file native AFTER/XML on TEMP only. No real general-store SQL test,
  production authority file or desktop caller was created.
- `native_identity` projects verified native rows under stable logical IDs;
  `Track.native_id`/cache10 retain native SQL identity separately. Imported-record
  injection cannot overwrite such native data. Five bridge/nine original identity
  tests pass. Remaining source consumers must translate IDs before activation.
- The earlier86-import metadata-only migration preview found **85 missing imports/
  one existing native match**, preserving86 logical IDs and all curation/device identities. A legacy
  Seafile symlink makes literal canonical-string lookup falsely report86 missing.
  Keep that naive report as history; `canonical-86-identity-preview.json` is the
  corrected preview. Refresh it for the87th import before migration. The planner/store and projection still need durable
  canonical-alias proofs preserving the existing raw native path/ID/UUID before
  activation; otherwise that legacy song could be duplicated.
- Native Collection has3724 active audio rows: WAV3087, MP3 604, AIFF23, AAC4,
  FLAC4, ALAC2.2243 additional FileType19 placeholders also have paths; exclude
  by verified type, not path emptiness. Two genuine MP3s retain foreign producer
  MasterDBIDs. Physical library authority comes from djmdProperty, not producer ID.

Next: canonical-alias-safe bindings and remaining native-ID consumers; durable
import intents/destination/preferences on the existing one-source worker; all-
Collection discovery; accepted initial-analysis/per-component publication and
migration of existing musical drafts/metadata/custom memberships. Preserve pending
jobs across native-ID acquisition without discarding/reanalyzing completed audio.
Only then activate ordinary shared imports/playlist drops. RGB92/128/184 temporal
work and full end-to-end native/hardware acceptance remain open.

## Earlier native references — preserve, do not broaden

Older evidence: `~/Library/Logs/Soundbox/shared-native-library-2026-10-30/` and
`~/Library/Logs/Soundbox/import-flow-fix-2026-10-30/`.
Research code: [native-library README](../research/native_library/README.md).

- Original generated WAV bare registration passed explicit approval and native
  acceptance. SHA f3c0536e1637361847a5982aaf54dd4a0430d183a7be9b562ae999cf3bcbd711,
  native190717052/UUID2e47171e-a854-4b9c-be9b-128e28202cce. Its separate initial-
  analysis operation55eef064-4f54-4275-8de9-aca45d42c4ec was separately approved;
  published120BPM/8B,64 beats249–31749ms, three packets, zero cues, counter
  352412→352432. Sonny confirmed waveform/grid and quit normally. Exact target/
  mixer/file rows and packets survived; separate acceptance retains startup
  bookkeeping deltas. Full seven-payload recovery archive remains protected.
  This grants no general gain/codec/endpoint/partial-analysis policy or authority.
- Original11-format native import/analysis matrix retains full before/after
  captures and33 verified packets. Bare counter352432→352443, batch analysis
  →352659. All120BPM/8B/Analysed105,64 beats; MP3 origin274ms/AAC270ms/others249ms.
  Detector origins agree within1ms on ten unchanged inputs. Interleaved batch
  delta216 is not proof of a universal per-file counter formula. Native gain is
  linear (quiet gain2×/peak½), but its general DSP/endpoint policy is unproven.
- Native ASCII title/artist reference actions on generated04 reconstructed five
  complete inventories/XML exactly, counter352659→352672. Artist creation/
  reuse/clear/retirement/restoration retained IDs/history correctly. Native UI
  retagged only that generated WAV; PCM/fmt stayed exact. Its original hash
  manifest is historical; exclude04 from the ten unchanged registration refs.
  Unicode, nonempty albums, broader artist lifecycle and an immutable-audio
  metadata source adapter remain separate work. No metadata authority exists.
- Four provisioned Tag/Cue/Grid/Playlist authorities remain unchanged, bound to
  library629922790/schema c3255e959e179e46cd15e6ae2da50543427c03c8399b11b7a6809515f06e8c08 /
  rekordbox6.8.7.0303. Current source counter352682; no pending shared journal.
  Do not borrow accepted manual-grid permission for initial analysis/key writes.
