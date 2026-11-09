# research/cdj_export — CDJ USB export prototype

Status checked November5,2026 against installed responsiveness build167/runtime3fb0ff60…;
published USB source `cf2ef5b` is an earlier delivery.
The desktop uses the shared renderer in
[`native/cdj`](../../native/cdj/README.md), fresh `soundbox.cdj_source` capture and
reviewed `cdj_transaction` publication. The standalone export tools below remain explicit
offline fixtures, not device writers. The original desktop playlist pilot remains
an exact-plan research tool; ordinary desktop playlist editing now uses its
separately accepted production authority. Research receipts never provision that
authority. Current contract/limits/install evidence:
[CDJ guide](../../docs/cdj-usb-export.md), [HANDOFF](../../docs/HANDOFF.md).


**Standalone R&D. NOT part of the shipped SoundBox app.** The normal check gate
lints this code and runs synthetic ANLZ-bridge tests; engine mypy does not cover
this directory. Those checks do not certify a hardware-valid CDJ export. This is the
reverse-engineering prototype for writing rekordbox's `export.pdb` — the
database a Pioneer CDJ reads off a USB stick.

Full plan, status, and next steps: [CDJ export research](../../docs/cdj-usb-export.md).

**Developer pickup: [RBXPORT_HANDOFF.md](RBXPORT_HANDOFF.md).** Start there for
the working export, exact reproduction command, historical settings proposal, code map,
native evidence and the historical integration sequence. It covers classic Device Library
and OneLibrary, the pinned local source kit, completed native captures and the
passing offline fixture. Both formats are maintained automatically by the runtime;
newer-player hardware acceptance remains pending.
The product experience is one export that maintains the selected hierarchy and
exact memberships/order in both formats with shared media and coordinated
recovery. Existing unrelated differences between the two catalogs remain each
format’s own baseline; global equivalence of the whole USB is not a publication
requirement. Users never choose or separately enable the formats.

## What's here

- [EXPORT_PARITY.md](EXPORT_PARITY.md) — compiled standalone dual-format export
  and the user-approved native-reference gate. The five-track fixture passes:
  25/28 files byte-identical, all three databases' compared records/schema/indexes
  equal, no extra/missing files. Raw database mismatches remain explicit. This
  is the dated standalone-fixture result, not a new hardware acceptance. The app's
  source adapter and device transaction are documented separately in the CDJ guide.
- `fixture_export.py`, `export_compare.py`, `rbxport_adapter/` — reproducible
  locked build → local generation → comparison. Inputs are explicit private
  fixture DTOs; no automatic library/device discovery or live writes.
- `devicesql.py` — DeviceSQL `.pdb` reader + writer (pages, table directory, the
  backward-growing row index) and a small-file assembler with one data page per
  table. This Python research assembler is not the production renderer;
  `native/cdj` and its shared fixture adapter implement pagination. Hardware
  acceptance remains bounded to the observations in the CDJ guide.
- `rows.py` — `device_sql_string` codec (short-ASCII / long-ASCII / UTF-16LE)
  and simple-row codecs (keys, colors, artists, albums, labels, genres,
  playlist_tree, playlist_entries).
- `tracks.py` — the `track_row` codec (136-byte scalar header + 21 string
  pointers): decode to fields, and generate a valid row.
- `anlz_bridge.py` — strict candidate grid/cue field validation, separately typed
  display intermediates and unmodified packed waveform streams. It rejects
  invalid musical values instead of clamping/substituting them; it does not
  encode native tags/containers or authorize a device write. Fresh DB cues are
  required by the runtime exporter because source ANLZ cues can lag accepted DB edits.
- `pdb_audit.py` — independent, read-only structural report for copied local
  PDBs. Uses native little-endian counts and bounded chain/index checks. It
  diagnoses the historical prototype's count-field defect without changing it.
- `mysettings_codec.py` / `mysettings_fields.json` — bytes-only candidate reader
  and patcher for 42 player/mixer fields, retaining unknown bytes and exact
  base/CRC guards. No file/device publication APIs. The
  [My Settings proposal](MY_SETTINGS_PLAN.md) records the original defaults/presets
  and Follow Global / Custom design. Current
  production controls instead use44 fields, direct USB editing and explicit
  Save to Global / Load Global without automatic propagation; see the CDJ guide.
  Broader native mapping/hardware acceptance remains separate. The complete
  native ±10→±16 tempo file matches the staged codec output exactly.

- `playlist_reference.py`, `playlist_membership.py`, `playlist_lifecycle.py` and
  `playlist_replay.py` — explicit frozen-capture comparison, pure unsynced
  membership/root-create/rename and scoped deletion plans, and semantic replay on
  disposable encrypted database copies. SQL rollback and commit/reopen are checked against
  the native inventories. Strict replay rejects unrelated differences; explicit
  notification-timestamp audit reports its residual and never claims a complete
  match. Deletion is limited to the observed last-root single-member case.
  This does not prove XML publication/interruption recovery or grant source-write authority; see
  [the separately accepted current playlist protocol](../../docs/analysis-playlists-next.md).

- `playlist_xml.py` / `playlist_transaction.py` — byte-preserving XML codec and
  original-TEMP-only paired publication with complete evidence validation and
  real process-exit recovery checks. `playlist_replay.py --paired` exercises the
  frozen native DB/XML pairs. No source permission is granted by that proof.
- `playlist_pilot_plan.py` / `playlist_pilot.py` — a separately approved, unintegrated
  desktop-source test for one new named playlist plus two existing unsynced tracks.
  Preparation/show are read-only to the source. Publish/recover require the exact
  separate original approval; the research tool does not inherit production
  authority from the accepted runtime writer.
  CLI: `scripts/prove_rekordbox_playlist_pilot.py`; read the
  [research scope and production boundaries](../../docs/analysis-playlists-next.md) first.

## Checking it

Use the project's existing venv. From the repository root, run the pure bridge
tests without opening a device or the reference captures:

    .venv/bin/python -m unittest discover -s tests -p test_anlz_bridge.py
    .venv/bin/python -m unittest discover -s tests -p test_cdj_pdb_audit.py
    .venv/bin/python -m unittest discover -s tests -p test_cdj_mysettings_codec.py
    .venv/bin/python -m unittest discover -s tests -p test_cdj_export_compare.py

`devicesql.py` exposes reader/writer APIs; running it as a script does not execute
an export or a validation campaign.

The **reference USB snapshots** (real rekordbox exports + staged test sticks)
are large audio binaries and live in the project-local, fully git-ignored
`../../soundbox-cdj-ref/` workspace — do not commit them.

## Status in one line

Historical PDB round-trips preserved bytes, including the October 26 recheck,
but **the old reader and writer share an incorrect count-field byte order**.
The independent October 28 audit reproduces this on the retained native and
failed candidate files. That historical candidate was **rejected by the CDJ-3000**;
audio played through folder-mode fallback. Full library/performance acceptance
has not passed. The new compiled Rust fixture uses the correct upstream count
encoding and passes the native-reference gate. The shared runtime renderer,
scalable metadata completion, fresh source DTO capture and dedicated device
transaction are now integrated. Scaffold-and-inject remains an isolation
experiment, not a production design. Current runtime/release evidence and the
remaining model/firmware/control matrix belong in the CDJ guide and HANDOFF,
not this historical prototype result.
