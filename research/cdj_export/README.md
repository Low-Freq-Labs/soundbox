# research/cdj_export — CDJ USB export prototype

October28 integration update: the desktop pilot now uses the shared renderer in
[`native/cdj`](../../native/cdj/README.md), fresh `soundbox.cdj_source` capture and
reviewed `cdj_transaction` publication. The standalone tools below remain explicit
offline fixtures, not device writers. Current contract/limits/install evidence:
[CDJ guide](../../docs/cdj-usb-export.md), [HANDOFF](../../docs/HANDOFF.md).


**Standalone R&D. NOT part of the shipped SoundBox app.** The normal check gate
lints this code and runs synthetic ANLZ-bridge tests; engine mypy does not cover
this directory. Those checks do not certify a hardware-valid CDJ export. This is the
reverse-engineering prototype for writing rekordbox's `export.pdb` — the
database a Pioneer CDJ reads off a USB stick.

Full plan, status, and next steps: [CDJ export research](../../docs/cdj-usb-export.md).

**Developer pickup: [RBXPORT_HANDOFF.md](RBXPORT_HANDOFF.md).** Start there for
the working export, exact reproduction command, approved settings UX, code map,
native evidence and sequenced integration tasks. It covers classic Device Library
and OneLibrary, the pinned local source kit, completed native captures and the
passing offline fixture. Both formats are
requested; newer-player hardware acceptance remains pending.
The required product experience is one export that always maintains both
databases with identical playlist hierarchy/membership/order, shared media and
coordinated recovery. Users never choose or separately enable the formats.

## What's here

- [EXPORT_PARITY.md](EXPORT_PARITY.md) — compiled standalone dual-format export
  and the user-approved native-reference gate. The five-track fixture passes:
  25/28 files byte-identical, all three databases' compared records/schema/indexes
  equal, no extra/missing files. Raw database mismatches remain explicit. This
  is not installed app/source integration or a production device transaction.
- `fixture_export.py`, `export_compare.py`, `rbxport_adapter/` — reproducible
  locked build → local generation → comparison. Inputs are explicit private
  fixture DTOs; no automatic library/device discovery or live writes.
- `devicesql.py` — DeviceSQL `.pdb` reader + writer (pages, table directory, the
  backward-growing row index) and a small-file assembler with one data page per
  table. Scalable pagination and hardware-valid generation remain open.
- `rows.py` — `device_sql_string` codec (short-ASCII / long-ASCII / UTF-16LE)
  and simple-row codecs (keys, colors, artists, albums, labels, genres,
  playlist_tree, playlist_entries).
- `tracks.py` — the `track_row` codec (136-byte scalar header + 21 string
  pointers): decode to fields, and generate a valid row.
- `anlz_bridge.py` — strict candidate grid/cue field validation, separately typed
  display intermediates and unmodified packed waveform streams. It rejects
  invalid musical values instead of clamping/substituting them; it does not
  encode native tags/containers or authorize a device write. Fresh DB cues are
  required for future export because source ANLZ cues can lag accepted DB edits.
- `pdb_audit.py` — independent, read-only structural report for copied local
  PDBs. Uses native little-endian counts and bounded chain/index checks. It
  diagnoses the historical prototype's count-field defect without changing it.
- `mysettings_codec.py` / `mysettings_fields.json` — bytes-only candidate reader
  and patcher for 42 player/mixer fields, retaining unknown bytes and exact
  base/CRC guards. No file/device publication APIs. The
  [My Settings implementation contract](MY_SETTINGS_PLAN.md) specifies direct
  USB editing, Global defaults/presets, Follow Global / Custom propagation and
  the optional Save to Global My Settings action. Production UI, device
  publication and broader native mapping acceptance remain pending; the complete
  native ±10→±16 tempo file already matches the staged codec output exactly.

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
encoding and passes the native-reference gate; app integration, scale and the
dedicated device transaction remain to implement. Scaffold-and-inject remains an isolation experiment,
not a proven production design. Parallel research is now requested; app
integration/release still follows the roadmap and dedicated write-safety gate.
