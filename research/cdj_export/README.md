# research/cdj_export — CDJ USB export prototype

**Standalone R&D. NOT part of the shipped SoundBox app.** The normal check gate
lints this code and runs synthetic ANLZ-bridge tests; engine mypy does not cover
this directory. Those checks do not certify a hardware-valid CDJ export. This is the
reverse-engineering prototype for writing rekordbox's `export.pdb` — the
database a Pioneer CDJ reads off a USB stick.

Full plan, status, and next steps: [CDJ export research](../../docs/cdj-usb-export.md).

## What's here

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

## Checking it

Use the project's existing venv. From the repository root, run the pure bridge
tests without opening a device or the reference captures:

    .venv/bin/python -m unittest discover -s tests -p test_anlz_bridge.py

`devicesql.py` exposes reader/writer APIs; running it as a script does not execute
an export or a validation campaign.

The **reference USB snapshots** (real rekordbox exports + staged test sticks)
are large audio binaries and live in the project-local, fully git-ignored
`../../soundbox-cdj-ref/` workspace — do not commit them.

## Status in one line

Reference PDBs round-trip byte-identically, including the October 26 read-only
recheck. The historical compact generated PDB is readable by our reader but was
**rejected by the CDJ-3000**; audio played through folder-mode fallback. Full
library/performance acceptance has not passed. Next experiment: **scaffold and
inject**, with its structural explanation still a hypothesis. See the plan for
the model/firmware matrix, normal export/update behavior and recovery gates.
This research follows iPod acceptance and library refinement in the roadmap.
