# Generated CDJ export — native-reference gate

October 28, 2026. **Standalone fixture backend, not installed SoundBox export.**
Sonny required a real generated playlist export and an offline comparison before
more deck testing. After examining native-versus-native database differences, he
explicitly selected this gate:

- Byte-identical audio, native analysis companions and settings/profile files.
- Exact decoded database records, schema declarations, columns and indexes.
- Preserve/report all raw database mismatches; never relabel them byte-identical.
- Same required file set, with no unreported extra or missing export files.

## Current result

The backend has generated the five-track **smol test** playlist locally. The
native-equivalence gate **passes** for this fixture. Literal whole-export byte
identity remains **false** and is displayed separately.

| Compared output | Result |
| --- | --- |
| Five WAV payloads | Exact size/SHA-256 match, all 207,656,246 bytes |
| Fifteen DAT/EXT/2EX companions | Byte-identical, including prepared grids/cues/waveform sections |
| Five settings/profile files | Byte-identical |
| Classic `export.pdb` | Exact compared scalar/string/playlist rows and retained browse/property rows; structural and typed page-slot checks pass |
| Extended `exportExt.pdb` | Exact compared tag identities/sequence/labels/memberships/master identity; physical row-slot/header/padding differences remain in raw comparison |
| Plus `exportLibrary.db` | All 22 table row sets, column metadata, complete table declarations, four indexes, views/triggers and user-version/application-ID/encoding agree |
| Required file set | 28 files, no missing or extra files |

**25/28 files are byte-identical.** All three database containers still differ.
The native PDBs reflect different physical layout/write history. SQLCipher uses
random per-page IVs and a per-database salt, so fresh encrypted files do not have
a meaningful deterministic ciphertext identity. See the vendor's
[design](https://www.zetetic.net/sqlcipher/design/). Our two retained native
post-override/pre-tempo and post-tempo DBs already have identical decoded contents
but different ciphertext; their salts stayed the same while page writes changed.
This is recorded, not assumed. No raw PDB mismatch is automatically attributed
to harmlessness merely because records compare equal.

## Reproduce the complete build → export → comparison

From the project root, use the prepared explicit input fixture:

```sh
.venv/bin/python -m research.cdj_export.fixture_export \
  soundbox-cdj-ref \
  soundbox-cdj-ref/parity-request.json \
  soundbox-cdj-ref/20261028-mysettings-tempo16-reset-fat32 \
  soundbox-cdj-ref/parity-audio-fixture \
  soundbox-cdj-ref/NEW-GENERATION-NAME \
  --gate native-equivalence
```

Output/report/build-record names must be new. The command builds the Rust adapter
with its locked dependencies, refuses a failed build before invoking any binary,
generates the complete export and compares it with the frozen native reference.
It returns nonzero on the selected gate's failure. `--gate strict-bytes` retains
the literal-byte gate and correctly fails this result. No implicit normalization
or ignored extra-file allowlist turns a failure green.

The isolated Rust 1.99.0 toolchain is under
`soundbox-cdj-ref/toolchain-1.99/install/`; its downloaded components were checked
against the official manifest hashes. No shell profile or global toolchain was
changed. Cargo dependencies/build output stay under the private reference root,
with two compile jobs. The upstream kit remains byte-identical to its pinned
source manifest; corrections live in our separate adapter.

Current artifacts:

- `soundbox-cdj-ref/native-parity-export/`: complete generated native-layout tree.
- `native-parity-export-comparison.json`: selected gate, every file hash, all
  remaining raw mismatches and source/request/binary/toolchain provenance.
- `native-parity-export-build-records/`: upstream bookkeeping retained outside
  the native-layout fixture; not a published production ownership manifest.
- `parity-audio-fixture/capture.json`: stable, hash-verified input capture of the
  five test-USB WAVs. Acquisition read the explicit test USB, never canonical
  source media; all generator/comparator runs use local inputs only.
- `generated-export-005-container-differences.json`: quantified initial remaining
  PDB/encrypted-container differences and the native-versus-native control.
- `adapter-tests.log`: two hermetic Rust checks, including a generated export's
  metadata/index completion and preservation of original test media.

The earlier numbered generated exports/reports are diagnostic iterations, not
accepted alternatives. Their duplicate media/build products are disposable;
preserve the current fixture, native snapshots and evidence receipts.

## What was actually implemented

[fixture_export.py](fixture_export.py) orchestrates a checked build/run/comparison.
[rbxport_adapter](rbxport_adapter/Cargo.toml) consumes explicit ordered track,
playlist, tag and profile DTOs, then invokes pinned `rbl-export` to copy media,
render both databases and extended tags, and republish the native analysis paths.
It does **not** copy the reference library databases into output. It carries the
five profile files as deliberately selected export inputs.

The first generated result exposed real upstream omissions. The separate adapter
now supplies input-owned content-link/counter fields and the independent My Tag
master identity, legacy counter/date/publication strings, background and sort
preferences, native null/zero/search defaults and all four native indexes.
Legacy tag sequence conversion now preserves the shared canonical order rather
than subtracting one from an already device-level sequence. These changes are
bound to the explicit **rekordbox-6.8.7.0303** fixture metadata profile.

[export_compare.py](export_compare.py) reads full file hashes, checks PDB chains,
counts, string bounds/alignment and typed row-slot identity, and independently
decodes the compared library fields. It opens disposable SQLCipher copies and
compares schema/index/record details. Source and result use different parser
paths, with native controls providing the actual expected data. Its tests prove
that changed database values, unexpected files, reference corruption and path
escapes fail, while random encryption alone remains visible as a raw failure.

The fixture DTO was prepared from the captured native **exported metadata** plus
the captured five audio payloads. It is an input to the renderer, not evidence
that live SoundBox source selection/curation integration already works. The
building agent must replace fixture acquisition with SoundBox's verified source
snapshot/selection adapter and supply fresh authoritative cues and metadata.
Do not infer export authority from browser-cache rows. My Tag master identity and
per-track counters must be explicit verified inputs; do not hard-code this user's
values or assume the My Tag master ID equals the desktop library DBID.

## Limits before app integration or hardware acceptance

- The metadata-completion adapter currently rejects a track table exceeding its
  bounded one-page fixture limit. Underlying upstream pagination exists, but
  complete new-row/page-slot handling for these extra legacy fields is not yet
  integrated. This is not a full-library exporter.
- Media/analysis/profile preparation uses explicit captured local inputs. Live
  library selection, SoundBox overrides, new analysis authoring and edit freshness
  still need the application adapter. A single passing WAV fixture does not
  prove arbitrary codecs, Unicode/long paths, folders, updates or scale.
- The fixture has no artwork, memory cues or saved loops. Existing hot-cue/grid
  bytes match native exactly; missing feature references remain explicit.
- This is a fresh one-shot fixture renderer. Managed ownership, coexistence,
  incremental sync, cancellation and coordinated failure recovery are not a
  production CDJ transaction yet. Upstream manifest/locks/sync records are kept
  in build records for diagnosis rather than claimed as SoundBox's contract.
- No live USB writer is added, no source database/audio/global preferences are
  modified, and the installed app is untouched by this parallel work. Existing
  production exporters keep their `/PIONEER` guard. Both profiles remain excluded
  from any claim of shipped CDJ support.
- The accepted fixture gate is a prerequisite, not CDJ recognition/behavior
  acceptance. No additional native/deck tests are being requested in place of
  remaining implementation. Newer-hardware acceptance remains separate.

The immediate next implementation is live SoundBox DTO acquisition and scalable
legacy metadata rendering, followed by the dedicated guarded CDJ transaction.
Keep [My Settings](MY_SETTINGS_PLAN.md) work sequenced behind getting this export
path integrated; its field prototype does not substitute for playlist export.
