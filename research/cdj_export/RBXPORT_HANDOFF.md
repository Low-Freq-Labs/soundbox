# CDJ export backend handoff — October 28, 2026

**Status checked November4 against installed whole-app build165/runtime7ffde716…:** this document
is the retained October28 research handoff. The application integrates its renderer
through `native/cdj` and the dedicated
`soundbox.cdj_*`/`desktop.cdj` boundary. The original pending-selection/pagination/
transaction notes below describe the pickup baseline. Current implementation,
limits and install evidence are in [the CDJ guide](../../docs/cdj-usb-export.md)
and [HANDOFF](../../docs/HANDOFF.md). The wider physical acceptance matrix remains open; do not
interpret the fixture parity record as hardware certification.

Current implementation closes the original source-DTO, pagination and coordinated
transaction tasks. Runtime/fixture Cargo dependencies now use the checked-in
vendor closure under `native/cdj`, not the archived private kit. Direct USB My
Settings has44 fields and explicit Save to Global / Load Global; named presets
and automatic Follow Global/Custom propagation remain deferred. The earlier
build160/2564-test release had dirty-source base c2415d5…, separate from Git
release `4a3d29a`; these are dated proofs. The later build163/2595-test USB delivery
and its physical fixture evidence are historical; current build165/2779-test
delivery and exact runtime/source identities are in HANDOFF. Selected
exports now preserve unrelated per-format differences and reclaim only removed
members unused by other USB playlists; the historical whole-fixture equivalence
checks below do not impose a global equality requirement on ordinary export.

Everything below records the October28 pickup checkpoint. Its pending tasks,
uncommitted-source statements, test counts, device UUIDs and preference values
are historical, never a current target, restore instruction or renewed write
approval. Use the linked current contract before any new operation.


For the agent building SoundBox at the October28 checkpoint. Sonny requested this
parallel research handoff; it left the concurrent waveform/build work and the
then-current `docs/HANDOFF.md` untouched. That backend preparation left export UI
to Sonny and changed no installed behavior, live source library, source audio or USB.

## Developer pickup — start here

**At this checkpoint the next task was application integration.** A
compiled standalone backend already renders a complete five-track export and
passes the native-reference gate below. Continue with SoundBox's verified source
DTO, scalable metadata rendering and a dedicated device transaction. Keep the
settings UX contract ready for that integration; additional manual settings
experiments must not replace finishing playlist export.

This research was uncommitted work in the shared checkout, prepared against
repository HEAD `2c76172` on October 28. Another agent owns the concurrent
waveform/player changes and main session handoff. Review the worktree before
staging; do not include or revert their changes. No app build, installation,
commit or push is part of this handoff. The installed application revision is
separate from the research source revision.

| Read / use | Purpose |
| --- | --- |
| This note | Product decisions, source map, evidence, safety and next tasks |
| [EXPORT_PARITY.md](EXPORT_PARITY.md) | Actual generated result, comparator, corrections and limits |
| [MY_SETTINGS_PLAN.md](MY_SETTINGS_PLAN.md) | Global/Custom behavior, persistence, propagation and profile codec contract |
| [fixture_export.py](fixture_export.py) | Checked locked build → local export → native comparison |
| [rbxport_adapter/src/main.rs](rbxport_adapter/src/main.rs) | Compiled Rust DTO adapter and native metadata completion |
| [export_compare.py](export_compare.py), [pdb_audit.py](pdb_audit.py) | Offline comparison and independent structural checks |
| [mysettings_codec.py](mysettings_codec.py), [field catalogue](mysettings_fields.json) | Bytes-only player/mixer profile prototype |
| [Canonical CDJ plan](../../docs/cdj-usb-export.md) | Product roadmap and production acceptance boundary |

### Reproduce the working export

Run from the repository root, choosing a **new** output name:

```sh
.venv/bin/python -m research.cdj_export.fixture_export \
  soundbox-cdj-ref \
  soundbox-cdj-ref/parity-request.json \
  soundbox-cdj-ref/20261028-mysettings-tempo16-reset-fat32 \
  soundbox-cdj-ref/parity-audio-fixture \
  soundbox-cdj-ref/NEW-GENERATION-NAME \
  --gate native-equivalence
```

The checked build cannot fall through to a stale binary. All generator inputs
and outputs are explicit local paths; this command does not discover a device,
read the installed library or write a USB. The prepared isolated Rust toolchain,
source kit and captures live under gitignored `soundbox-cdj-ref/`. They are local
dependencies, **not included by cloning this repository**. Preserve that workspace
as historical evidence. The current adapter's relative crate dependencies resolve
to the checked-in `native/cdj` vendor tree; the isolated toolchain and explicit
private fixtures are still needed to reproduce this command. Porting the fixture
requires its receipts and an equivalent toolchain, without publishing Sonny's
media or library data.

The October28 accepted local output is `soundbox-cdj-ref/native-parity-export/`.
Its sibling `native-parity-export-comparison.json` records:

```json
{
  "gate": "native-equivalence",
  "gate_passed": true,
  "strict_byte_match": false,
  "expected_files": 28,
  "byte_identical_files": 25,
  "all_database_rows_match": true,
  "extra_files": []
}
```

That means five WAVs, fifteen analysis companions and five settings files match
byte for byte; the three databases match their compared decoded records, schema
and indexes. All raw differences remain in the report. Sonny explicitly approved
this gate after native-to-native comparisons showed database-container changes.
`--gate strict-bytes` remains available and fails this result. No deck test has
accepted this new candidate. Earlier `generated-export-00*` outputs are diagnostic
iterations, not alternate accepted results.

The DTO is reconstructed from native **exported metadata**, with captured local
media/analysis/profiles. It is a real rendering pass, not a copy of native library
databases; it does not yet prove SoundBox selection or source-state integration.
The final report includes request, adapter, comparison source, binary and
toolchain provenance so changes cannot silently inherit this result.

### Original implementation order and completion gates (historical)

1. Feed an immutable export DTO from SoundBox's accepted library/source APIs:
   stable identities, effective metadata/artwork, hierarchy and exact order,
   native analysis and fresh authoritative cues. Preserve source bytes. Supply
   counters/content-link/dates and the My Tag master ID as verified inputs;
   do not hard-code fixture values or equate that ID with the library DBID.
2. Remove the adapter's **one-page legacy metadata-completion limit** with
   correct pagination and slot renumbering. The upstream writer has pagination;
   the adapter's additional completed track rows currently reject a combined
   `row length + 4` budget over 3,700 bytes. Complete arbitrary category/sort/
   column profile handling and expand coverage for artwork, memory cues, loops,
   folders, Unicode/long paths and other supported media.
3. Implement one explicit CDJ target planner and recoverable publication job
   for both libraries: verified mount/source separation, capacity, ownership,
   stable IDs, incremental changes, cancellation, coexistence and interrupted
   recovery. Ordinary iPod/generic exporters keep their `/PIONEER` exclusion.
   The fixture renderer is fresh-output-only and grants no live write authority.
4. Integrate direct USB My Settings and Global/Custom persistence/propagation
   using the linked contract. Preserve external/deck changes and unknown bytes.
5. Re-run offline gates on the actual app-produced selection before requesting
   the corresponding CDJ-3000 acceptance. Record exact firmware. New-format
   physical acceptance remains separate because Sonny has no CDJ-3000X.
   Runtime integration also needs the normal app checks/render gate and safe
   build/install/curated-state verification; those have not been run for this
   source-only research.

## Requested result

**Latest priority and completed work:** Sonny required a real generated playlist
and offline comparison before more hardware tests. The standalone Rust backend
is now compiled and generates the five-track fixture. He explicitly approved
byte-identical audio/analysis/settings plus exact database records/schema/indexes,
with all raw database differences reported. That gate passes: **25/28 files
byte-identical, all three decoded databases match, no extra/missing files**.
Literal whole-export byte identity remains false. Reproduction, corrected writer
fields, evidence and remaining implementation limits: [EXPORT_PARITY.md](EXPORT_PARITY.md).
Live SoundBox DTO integration, scalable metadata completion and a production
device transaction were unimplemented at this checkpoint and are now integrated.
The standalone fixture receipt itself remains separate from shipped-runtime proof.

**One export, one library experience.** Sonny explicitly requires both formats
to be produced and maintained automatically on every CDJ USB export. Format
names are backend details: no classic/Plus chooser, separate enable step,
conversion prompt, independent playlist selection or second sync operation.
His export UI operates on one playlist tree and one intended device state.

The backend must derive both database representations from the same immutable
generation: identical playlist/folder hierarchy, sibling order, ordered track
membership and shared metadata/grid/cue/artwork inputs. Renames, moves, additions,
removals and reorders reconcile in both on the same export. Each encoder uses
its native IDs/order fields while preserving those shared semantics. Store audio,
artwork and analysis once, referenced consistently by both databases. Never let
the databases become independently curated collections or silently diverge in
membership. Any unsupported content/feature must be reported honestly during
planning rather than silently dropping it from just one view.

Treat both databases and their companions as one recoverable generation and one
device job, with one progress/result. Stage and validate both before publication;
failure of either must not be reported as a successful export. Interrupted
publication needs coordinated recovery for the complete generation. This is a
required transaction design, not a claim that multi-file publication is atomic
or that the current research implements it. Backend/hardware acceptance records
may distinguish formats; users do not manage that distinction.

**Support both classic Device Library and OneLibrary**, using one audio/artwork/
analysis collection and consistent references in both databases. Sonny has
CDJ-3000s and rekordbox 6. He cannot test a CDJ-3000X. Implementing and checking
the new format is in scope now; physical acceptance of that format stays pending.
Exact CDJ firmware still needs recording. The installed export application is
rekordbox **6.8.7.0303**, recorded during the fresh empty-USB capture. Do not infer
firmware from the approximate last-update date.

AlphaTheta's [March 18 compatibility notice](https://alphatheta.com/en/information/important-notice-for-customers-using-usb-devices-with-our-dj-equipment/)
lists CDJ-3000 as classic Device Library and CDJ-3000X/XDJ-AZ/OPUS-QUAD/OMNIS-DUO
as OneLibrary. The [January firmware notice](https://www.pioneerdj.com/en/news/2026/cdj-3000-firmware-ver330-important-notice/)
explains the withdrawn 3.30 transition. A successful 3000 test cannot certify the
new-format reader. Both files may coexist; test/report each format separately.

## Extracted source kit

Upstream: [chrisle/rbxport](https://github.com/chrisle/rbxport), pinned revision
`b739d369665b492534dc2eae0733322c040a26d5` (the checkout's version is 1.9.0).
No moving `main` dependency is needed to reproduce this investigation.

Local, git-ignored kit:

    soundbox-cdj-ref/rbxport-kit-b739d369/

It contains 286 upstream files, including the six runtime crates below, four
additional crates needed by their development dependencies, upstream docs,
Cargo.lock, toolchain pin, license notices and full GPL text. Crate source is
unmodified. Only root `Cargo.toml` drops `src-tauri` from workspace members;
`Cargo.toml.upstream` retains its original bytes. `SOURCE-MANIFEST.json` records
the revision and SHA-256 of every extracted upstream file, with a subsequent
validation receipt for compilation through our adapter. The runtime crates have
now compiled and produced the accepted local fixture; there is no installed
plugin or hardware acceptance. The kit excludes the React/Tauri UI and deck engine.

| Component | Concrete entry points | What to take from it |
| --- | --- | --- |
| [`rbl-export`](https://github.com/chrisle/rbxport/blob/b739d369665b492534dc2eae0733322c040a26d5/crates/rbl-export/src/lib.rs) | `SourceTrack`, `SourcePlaylist`, `ExportOptions`, `export_cancellable`; `write_one_library`, `add_tracks`, `write_artwork`, `analysis_directory` | Coordinates both formats, IDs, final paths, ANLZ rewriting, art and publication |
| [`rbl-onelibrary`](https://github.com/chrisle/rbxport/tree/b739d369665b492534dc2eae0733322c040a26d5/crates/rbl-onelibrary) | `build::Builder::{create_with, add_track, add_playlist_node, add_to_playlist, replace_cues, finish}`; `ExportLibrary::open_read_only` | SQLCipher `exportLibrary.db` construction/inspection, complete schema and initial browsing rows |
| [`rbl-pdb`](https://github.com/chrisle/rbxport/tree/b739d369665b492534dc2eae0733322c040a26d5/crates/rbl-pdb) | `build::{FileBuilder, PageBuilder}`, `rows::{TrackInput, track_row, set_index_shift}`, `reference` | Classic DeviceSQL pages, pagination, row codecs and fixed browsing/property data |
| [`rbl-anlz`](https://github.com/chrisle/rbxport/tree/b739d369665b492534dc2eae0733322c040a26d5/crates/rbl-anlz) | `parse`, `Anlz::to_bytes`, `write::render`, `AnlzBuilder`, `cues::sections`, `encode` | DAT/EXT/2EX framing; grids, cues, packed waveforms and path replacement |
| `rbl-core` | `durable::Publication`, `VolumeIdentity`, shared metadata/types | Needed by the upstream runtime; recovery is reference material, not SoundBox write authority |
| `rbl-audio` | `compatibility` and decoding/conversion helpers | Upstream runtime dependency; SoundBox's source immutability and supported-media policy still govern |

`rbl-db`, `rbl-index`, `rbl-analysis`, `rbl-devices` are included to complete the
dev-dependency closure. They are **not** required for `rbl-export`'s normal runtime.
Do not adopt their installed-library discovery/write APIs to feed SoundBox exports.

The kit retains upstream's GPL-2.0-or-later notices. Review that dependency choice
before incorporating/distributing its implementation; the existing SoundBox
[distribution boundary](../../docs/release-boundaries.md) still applies. Source
inspection/extraction here is not distribution clearance. No passphrases are
copied into this handoff or diagnostic reports.

## OneLibrary implementation details worth preserving

The implementation is real source, not just an encrypted template: `build.rs`
creates 22 tables and seeds browse/category/sort/color rows. `lib.rs::unlock`
sets `cipher=sqlcipher`, then `legacy=4`, then the derived key, before schema
access. Those are the pragmas of upstream's bundled rusqlite/SQLCipher build;
do not assume another Python/SQLite binding treats `legacy` identically. Use
`key.rs` through its API; avoid exposing derived values in logs or shell arguments.

Content IDs, source-library IDs, playlist hierarchy/order, image IDs, audio paths
and analysis paths are coordinated with classic PDB. Ratings are star counts
0–5. Cue rows include both microsecond and 150-frame/s representations. The
builder's finalization commits and closes the staged database before publication;
validate the completed copy by reopening it with the same cipher configuration.
Never publish a live main-file copy that is missing committed WAL state.

**Native version distinction is now isolated:** the empty and initially exported
references report `property.dbVersion="10000"`. The explicit native **Plus
override** on the same FAT32 USB rebuilds it as `"1000"`, matching the pinned
upstream builder. All 22 tables' column metadata matched in the initial schema
comparison, but the override also changes values/defaults and update counters.
Do not treat the pre-override populated file as equivalent to the post-override
Plus export. Use the latter as the current native Plus reference. No change to
upstream's version constant is indicated by this experiment.

A matching version is not complete parity: upstream fills title/artist search
fields from their names, whereas the native override here writes empty strings.
The fixture also has native defaults/counters worth checking against the builder.
Keep implementation differences explicit until their effects are established.

Upstream's [format reference](https://github.com/chrisle/rbxport/blob/b739d369665b492534dc2eae0733322c040a26d5/docs/reference/usb-export-db.md)
and [pipeline reference](https://github.com/chrisle/rbxport/blob/b739d369665b492534dc2eae0733322c040a26d5/docs/reference/usb-export-pipeline.md)
describe emulator results and remaining gates. They are useful implementation
evidence, not blanket hardware certification. A historical pipeline section says
physical playback had not been shown at that stage; newer format notes also
contain device-specific feedback. Neither establishes SoundBox acceptance.

## Independently reproduced defect in our historical prototype

`devicesql.py` reads/writes the three-byte count field at page offset `0x18`
as a big-endian value with the slot count shifted by 11. The native field is
little-endian: **slots = value & 0x1fff; present = value >> 13**.

| Retained input | Track page bytes at `0x18` | Native interpretation |
| --- | --- | --- |
| `ref-usb-full/.../export.pdb`, page 2 | `03 60 00` | 3 row slots, 3 present |
| `stage-B-scratch/.../export.pdb`, page 2 | `00 18 03` | 6,144 row slots, 24 present; impossible row-index size |

Our old reader decodes the native page as 108 slots and the malformed page as
3. Thus its byte-preserving serialization is not a correct interpretation of
native structure. This is a proven defect in the retained candidate, **not proof
that correcting it alone makes a CDJ accept the file**. Preserve the failed
candidate and native controls. Do not edit the old files or fixtures in place.

New independent read-only tool: [pdb_audit.py](pdb_audit.py). It walks bounded table
chains, reads native counts, checks live-row masks/offsets and heap/index bounds,
and reports headers without using `PdbFile`. It deliberately does not certify
row semantics, ANLZ, encryption or player behavior. It accepts explicit copied
local PDBs up to 64 MiB, rejects symlink inputs and `/Volumes`/`/dev`, and never
discovers devices or reads audio.

    .venv/bin/python research/cdj_export/pdb_audit.py \
      soundbox-cdj-ref/ref-usb-full/PIONEER/rekordbox/export.pdb

Multiple paths produce one JSON array. Exit 0 = no diagnosed structural issues;
1 = issues reported; 2 = invalid input. The private kit's `pdb-audit.json` contains
reports for empty/full native controls and the historical failed candidate.
Controls report no structural issues; the candidate reports nine. Full control
has three tracks but only two playlist entries: do not assume collection count
equals the playlist's membership count.

Other audit targets visible from source comparison: the old builder has no scalable
pagination or heap overflow guard; its row-index transaction word/free-size/header
bookkeeping differs from upstream. `tracks.encode_track` does not align long
strings despite its docstring, and permits null string pointers. Review row slot
numbers, subtype/trailer words, format codes, fixed tables and property metadata
against native data. The scaffold experiment in the older plan is still useful
as an isolation technique, but is not the only next step or a production design.

## Original integration shape for the building agent (historical)

1. Preserve the completed standalone fixture exporter and its passing reference
   report as the baseline. It emits **both** libraries against shared track IDs
   and final paths using the pinned upstream pipeline plus our adapter. Extend
   that implementation rather than restarting from the old Python writer.
2. Feed it explicit SoundBox selections through a narrow DTO/JSON adapter, or
   equivalent Rust structures. Use SoundBox's verified source capture, effective
   title/artist/artwork, ordered playlist membership and fresh authoritative DB
   cues. Browser display-cache rows confer no export authority. Supply already
   captured local audio; no new installed-rekordbox discovery/write path.
3. Reuse native packed waveform streams where available. Never reinterpret the
   displayed RGB/3BAND intermediates as native PWV payloads. Rewrite `PPTH` in
   every companion to the final audio path; keep both databases' analysis paths
   consistent. Compare export-specific preview/phrase transformations section by
   section. DB cue edits can be newer than source ANLZ; explicitly replace cue
   sections and OneLibrary cue rows from the same authoritative cue snapshot.
4. Freeze a generation's shared IDs/paths first, render both databases and all
   companions off-device, reopen independently and check links/membership/order.
   Do not treat `rbl_export::verify` as purely read-only: it runs recovery first.
   Use it only on disposable candidate trees in this phase.
5. Only after offline proof, design the dedicated CDJ target/ownership/publication
   contract and obtain the explicit test-USB target. Ordinary SoundBox stock,
   dual and generic exporters keep their `/PIONEER` exclusion. A future CDJ
   backend must handle both databases as one recoverable generation and retain
   unrelated files/settings. Do not transplant upstream's source/device conflict
   policies or conversion defaults without reviewing them against SoundBox's.
6. Test identical fixtures against rekordbox on the 3000. Continue generating
   OneLibrary and checking its schema/values/references; report newer hardware
   support as awaiting physical acceptance. This is not permission to call the
   new format verified on a CDJ-3000X.

## Original USB settings and player/mixer profile proposal (historical)

**October28 UX clarification, subsequently superseded:** edit My Settings directly on the selected USB.
Global My Settings supplies defaults and named presets. Sonny explicitly chose
**Follow Global / Custom**: Global edits automatically reach followers while
Custom USBs stay unchanged. A differing USB exposes **Save to Global My Settings**
for deliberate promotion; a USB edit does not silently alter Global. Full
state/propagation/native-acceptance contract and staged prototype:
[MY_SETTINGS_PLAN.md](MY_SETTINGS_PLAN.md).

The new bytes-only `mysettings_codec.py` and `mysettings_fields.json` cover
42 candidate player/mixer fields from the installed Python dependency. They
preserve every byte except requested fields/CRC and require an exact SHA-256
base. Ten hermetic checks pass; the captured native files also passed 155
field/choice decode-and-reverse checks through the installed dependency parser. This
does not provide production UI/state/queue/write authority. The first ±10→±16
native tempo reference is now complete: the prepared candidate matches the
entire 148-byte native file exactly. The new snapshot
`20261028-mysettings-tempo16-reset-fat32/tempo16-native-parity.json` records it.
Only that one field's output parity is established; device publication/recovery,
other native mappings and hardware acceptance remain pending. Sonny's deliberate
native ±16 setting remains in place; no agent global/device writes occurred.

Sonny supplied six native USB-settings screenshots and asked to include this
workflow: people rely on extensively customized sticks. Preserve those settings
on ordinary export/sync. Support user-selected export profiles as a planned
capability; the visual design remains Sonny's. One common setting/profile edit
must update every applicable representation without a classic/Plus chooser.
The [rekordbox manual](https://cdn.rekordbox.com/files/20200923164354/rekordbox6.1.1_manual_EN.pdf)
documents General, Category, Sort, Column, Color and My Settings as USB preferences.
It is a historical UI reference, not current filesystem/hardware guidance.

| Native controls | Backend locations / relevant source | Required behavior |
| --- | --- | --- |
| Device name and background color | Classic PDB property; Plus `property`; `rbl-devices/src/settings.rs`, `rbl-onelibrary/src/settings.rs` | One profile choice reaches both representations; preserve existing values on unrelated sync |
| Waveform color/position, overview type, key display | `DEVSETTING.DAT`; `rbl_devices::settings::DevSetting` | Patch only proven fields, retain unknown bytes and framing, validate CRC; waveform display choice must not discard the other prepared waveform streams |
| Category visibility/order, available sort options/order, right-hand column | Classic browse/settings structures and Plus `menuItem`/`category`/`sort`; `StickSettings` | Encode equivalent chosen browsing behavior for both readers; these menus do not replace or rewrite saved playlist order |
| Eight color comments | Classic colors table and Plus `color` | Preserve custom label text, stable color identity and native text encoding; edits propagate consistently |
| Player/mixer My Settings, including performance controls | `MYSETTING.DAT`, `MYSETTING2.DAT`, `DJMMYSETTING.DAT`, `djprofile.nxs` | Preserve native profiles on sync, including deck-saved changes; initialize/apply a chosen profile deliberately, never overwrite existing files with app defaults during playlist export |

The profile's exported **CDJ** Quantize fields are separate from rekordbox's
desktop global Quantize preference and SoundBox Q. Never read/change the global
desktop preference or bind SoundBox Q to this profile. New USB defaults must be
explicit export-profile data, not an incidental copy of desktop preferences.
Loading/saving the profile on a deck is a separate native action; exporting the
files does not promise the deck automatically applies every setting on insertion.

**Pinned upstream is incomplete here:**

- General/browsing/color backends exist in the extracted `rbl-devices` and
  `rbl-onelibrary` crates. The former is included as a development dependency of
  the kit; using its settings code in the runtime is a separate integration choice.
- [`MySettingsTab.tsx`](https://github.com/chrisle/rbxport/blob/b739d369665b492534dc2eae0733322c040a26d5/src/views/devices/MySettingsTab.tsx)
  displays only "My Settings are not available". `rbl-export::copy_my_settings`
  preserves/copies existing profile files; that is not a complete player/mixer
  settings editor. Do not report full screenshot parity from extracting this repo.
- `rbl-export::build_pdb` currently emits fixed reference rows for classic
  tables 16–18. `rbl-devices::settings::updated_pdb` changes colors/name/background,
  but does not mirror arbitrary category/sort/subcolumn edits there. The shared
  browsing-profile requirement needs native reference work and implementation,
  rather than assuming the Plus settings writer configures both readers.
- Upstream's unknown display bytes and assumed enum fallbacks need review before
  adoption. Preserve unsupported settings/versions exactly during unrelated sync;
  do not silently map an unrecognized value to a guessed default when saving.

Private UI evidence: `soundbox-cdj-ref/20261028-usb-settings-ui/` holds all six
screenshots with exact hashes and `ui-observations.json`. The captured
post-override `DEVSETTING.DAT` has a valid CRC and matches the screenshot's RGB,
LEFT, half overview and alphanumeric keys. The three captured player/mixer DATs
also pass length/CRC checks: 40/40/52-byte bodies (148/148/160-byte files).
No new USB or global-preference read was needed for these checks.

Category mapping is not fully settled: native Plus `category` marks DATE ADDED
visible at sequence 10, while Sonny's screenshot lists it inactive. The other
nine visible categories match screenshot order. This discrepancy is also noted
upstream; do not equate a raw flag with proven UI/deck behavior. The captured
active sorts match the screenshot and no subcolumn flag is set (Not Specified).
Exact reports: `captured-display-settings.json` and
`captured-browse-and-settings-checks.json` in the UI evidence folder.

Acceptance must include preservation across no-op/playlist updates, deliberate
profile application to both formats, settings-only cancellation/recovery, unknown
bytes/values, renamed color labels, category/sort order and right column, native
player/mixer values and save-on-deck/reconnect behavior. Use one native field
change at a time to resolve unknown mappings. These settings tests are additional
to track/playlist/analysis acceptance, not closed by the screenshots or CRC checks.

## Native captures — five preserved states; Plus override and tempo edit isolated

Sonny operates rekordbox. He first initialized/exported on exFAT, then reformatted
as FAT32, declined Plus and re-exported **smol test**. Rekordbox later said Plus
was already configured; Sonny accepted its **override**. Captures below are
complete, with pristine source files retained and analysis performed on copies.

| Private snapshot under `soundbox-cdj-ref/` | Observed state | Captured metadata |
| --- | --- | --- |
| `20261028-classic-empty` | exFAT, empty classic and initialized SQLCipher DB, version 10000 | 10 files / 596,556 bytes |
| `20261028-classic-playlist` | exFAT, both DBs populated despite reported decline of Plus | 23 files / 1,268,602 bytes |
| `20261028-classic-playlist-reset-fat32` | Fresh FAT32, same five-track playlist in both DBs, version 10000 | 25 files / 1,672,874 bytes, including WAL/SHM |
| `20261028-plus-same-playlist-reset-fat32` | Same FAT32 generation after explicit Plus override, version 1000 | 23 files / 1,268,602 bytes; WAL/SHM checkpointed away by native app |
| `20261028-mysettings-tempo16-reset-fat32` | Native TEMPO RANGE ±16 after the prepared ±10→±16 experiment | 23 files / 1,268,602 bytes; complete target profile matches candidate |

October28 captured target (not current write authority): `/Volumes/RB USB TEST`, FAT32, volume UUID
`5B29E617-ACFB-32C2-B580-077A6BF02C64`. Earlier exFAT identity was
`EB3EFAF0-D2B7-365F-B498-D79346336E26`. Resolve/revalidate every later mount;
`disk4s1` is only the observed node, not enduring authority. Keep the filesystem
reset comparison separate from the controlled before/after override comparison.

Every capture checks absent rekordbox/agent processes, exact external USB identity,
stable metadata stats/inventory and copied hashes against reread source hashes.
For these metadata captures no USB file is written, no source/audio payload is
read or copied, and no database is checkpointed by our tools. `Contents` inventory
records paths/sizes only. A later, separate parity-audio capture read the five
explicit test-USB WAVs into `parity-audio-fixture/`, totaling 207,656,246 bytes,
with stable size/SHA-256 receipts in its `capture.json`; it never read canonical
source media. Generator/comparator runs then use those local copies only.
Unrelated files remain outside capture. SQLCipher opens disposable DB/WAL/SHM
copies read-only/query-only; integrity is `ok`. All five pristine metadata hash
sets were rechecked after the latest comparison. Both classic PDBs pass the new
structural audit in every state.

The private `capture_metadata.py` binds `*-reset-fat32` stages to the new UUID
and original stages to the old one; it refuses existing snapshot destinations.
`inspect_metadata.py` reads only captured copies. These are manual acquisition
helpers, not application device APIs or automatic release-test dependencies.
Keep native WAL/SHM companions exactly when present: the initial empty DB's main
file was only 4,096 bytes and the committed schema lived in its WAL.

### What the Plus override changed

- **Already present before override:** five content rows, one playlist, five
  entries, with shared IDs, metadata/audio/analysis paths and exact order across
  formats. File presence alone had understated how much was already populated.
- **Plus changed meaningfully:** five tables have row-value changes: `album`,
  `artist`, `content`, `playlist`, `property`. Missing lookup/image references
  become 0 instead of NULL; search fields become empty strings instead of NULL;
  content update counters become populated; the Kuvo-delivery flag becomes 0.
  `property.dbVersion` changes **10000 → 1000**, and background-color type
  **0 → 8**. Names, musical values, membership/order and identity/path mappings
  stay the same in the compared fields. This was not a no-op or just file cleanup.
- **Classic PDB:** only one byte changed, its header sequence at `0x14`, from
  30 to 36. Every other byte is identical. `exportExt.pdb` has 363 differing
  bytes across pages 0/7/8; do not silently discard those differences.
- **Prepared data:** all 15 DAT/EXT/2EX companions and five settings/profile
  files remain byte-identical. Audio inventory paths/sizes also agree; audio
  bytes were not hashed, so no audio-content equality claim is made.
- **Native lifecycle:** the encrypted DB was rebuilt/checkpointed, with WAL/SHM
  absent afterward. Compare logical WAL-inclusive contents, not just main-file
  hashes or timestamps.

Evidence in the post-override snapshot: `capture.json`, `operator-observation.json`,
`pdb-audit.json`, `semantic-inspection.json`,
`comparison-before-after-plus-override.json`, `plus-override-field-summary.json`.
Earlier snapshots retain their acquisition, semantic/schema and comparison reports.
Full track/source-ID mappings and field-by-field changes are in these private
reports; no reconstruction from row ordering is needed.

### October28 fixture coverage and original next steps

The playlist has five WAVs: STOP CAPPIN, MOONLIGHT, Bass Go Silly, Yoyo Bop,
1,2 Step, in that order. Device IDs are 1–5, source library DBID 629922790.
All five enable hot-cue auto-load and have analysis flags 41. All 15 ANLZ `PPTH`
paths agree with their database/audio inventory paths. STOP CAPPIN's 297th beat
is exactly 128750 ms in captured native analysis.

The existing cue reader decodes **19 hot cues** (8 STOP CAPPIN, 6 Yoyo Bop,
5 Ciara), while native Plus's SQL `cue` table is empty before **and after**
override. Do not infer missing ANLZ cues from that table alone. No memory cues,
saved loops or artwork/image rows are covered. These require separate later
native fixtures; the current exports do not validate them.

**Original next step, now implemented:** integrate SoundBox's verified source/selection DTO and scalable legacy
metadata completion into the standalone backend, then design the dedicated
guarded CDJ transaction. The generated five-track fixture already passes the
user-approved native-equivalence gate; its DTO currently comes from captured
native exported metadata rather than the running app. Preserve version-10000
and failed historical prototypes as distinct evidence. The old Python writer's
count defect remains historical; the new adapter uses the correct upstream
engine and independent native-reference comparison. Further memory/loop/art
references and physical acceptance are separate later gates, not substitutes
for implementation. Do not request them in place of finishing the export path.

## October28 validation and proposed follow-up checks

- Dated focused validation: 26 Python CDJ regression checks plus four ANLZ bridge
  checks pass, as do the two isolated Rust adapter tests and focused pyflakes.
  These are offline fixtures; they do not read live source/device state. Run:

  ```sh
  .venv/bin/python -m unittest discover -s tests -p 'test_cdj_*.py'
  .venv/bin/python -m unittest discover -s tests -p test_anlz_bridge.py
  CARGO_HOME="$PWD/soundbox-cdj-ref/toolchain-1.99/cargo-home" \
  CARGO_TARGET_DIR="$PWD/soundbox-cdj-ref/cargo-target" \
  RUSTC="$PWD/soundbox-cdj-ref/toolchain-1.99/install/bin/rustc" \
  CARGO_BUILD_JOBS=2 \
  "$PWD/soundbox-cdj-ref/toolchain-1.99/install/bin/cargo" \
    test --locked --manifest-path research/cdj_export/rbxport_adapter/Cargo.toml
  ```

- Python audit regression checks pass: native byte pattern, malformed candidate,
  deleted slots, truncated/directory bounds, bounded chain handling and symlink
  refusal. Existing ANLZ bridge tests remain relevant to canonical input mapping.
- Extraction manifest hashes and all local Cargo path dependencies were checked.
- Rust 1.99.0 was provisioned only under the private CDJ research root, with
  official component hashes verified and no global configuration changes. The
  locked adapter build and two isolated Rust tests pass. The kit's original
  source is unchanged; separate adapter code completes missing native fields,
  indexes and settings. Full details and the repeatable command are in EXPORT_PARITY.
- Five native metadata captures, Plus-override/tempo-reference comparisons and copy-only OneLibrary inspection
  completed as recorded above. No new physical player acceptance, production
  writer, source edit, USB write, app rebuild/install, commit or push occurred here.

The earlier suggested upstream checks below remain **unrun**; the new adapter
build/tests and native-reference gate above are the recorded passes. If these
additional checks become necessary, use an isolated working copy so resolving
the original workspace does not rewrite the retained source kit's lockfile:

    cargo check -p rbl-export --lib
    RB_LITE_TEST=1 cargo test -p rbl-pdb --test row_count
    RB_LITE_TEST=1 cargo test -p rbl-onelibrary --test roundtrip
    env -u RBX_USB_STICK -u RBX_REAL_USB RB_LITE_TEST=1 \
      cargo test -p rbl-export --test export

These are proposed commands, not recorded passes. Run against disposable local
fixtures. Do not run `--workspace` tests or the `real` example blindly: test mode
blocks certain installed-library writes but does not prohibit live reads, and
upstream tests/examples include opt-in real-device/library paths. The full
SoundBox release gate/build/install remains the building agent's responsibility
when integrating runtime changes; this parallel handoff requires no app restart.
