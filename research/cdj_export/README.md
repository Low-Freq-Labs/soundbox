# research/cdj_export — CDJ USB export prototype

**Standalone R&D. NOT part of the shipped SoundBox app** (kept out of the
packaged bundle and out of `./check.sh` / mypy / the test suite). This is the
reverse-engineering prototype for writing rekordbox's `export.pdb` — the
database a Pioneer CDJ reads off a USB stick.

Full plan, status, and next steps: **`../../docs/cdj-usb-export.md`**.

## What's here

- `devicesql.py` — DeviceSQL `.pdb` reader + writer (pages, table directory, the
  backward-growing row index) and a from-scratch file/page assembler.
- `rows.py` — `device_sql_string` codec (short-ASCII / long-ASCII / UTF-16LE)
  and simple-row codecs (keys, colors, artists, albums, labels, genres,
  playlist_tree, playlist_entries).
- `tracks.py` — the `track_row` codec (136-byte scalar header + 21 string
  pointers): decode to fields, and generate a valid row.

## Running it

Needs `pyrekordbox` (reads the ANLZ/settings files). The R&D venv used during
development is external:

    ~/soundbox-cdj-ref/rbxenv/bin/python

The **reference USB snapshots** (real rekordbox exports + staged test sticks)
are large audio binaries and deliberately live OUTSIDE this repo at
`~/soundbox-cdj-ref/` — do not commit them.

## Status in one line

Reads + writes rekordbox `.pdb` byte-identical; generates a full `.pdb` from
scratch; a code-built stick **mounts and plays on a CDJ-3000** but the CDJ does
not yet accept our from-scratch database (folder-mode fallback). Next campaign:
**scaffold-and-inject** (see the doc).
