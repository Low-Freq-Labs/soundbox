# CDJ USB export (R&D)

**Goal:** let SoundBox write a USB stick a Pioneer **CDJ** reads natively —
tracks, playlists, beatgrids, memory + RGB hot cues, color waveform — **without
opening rekordbox**. Sonny performs on 4× CDJ-3000 off USB; today rekordbox is
used only to set cues + export the stick. This closes that loop.

Honors the same safety contract as the rest of SoundBox: writes land only under
an explicit USB target, never `master.db`.

Prototype code: **`research/cdj_export/`** (`devicesql.py`, `rows.py`,
`tracks.py`, `anlz_bridge.py`) — standalone, NOT wired into a USB workflow
until proven.

Related but separate: the shipped player currently imports PQTZ/PWV5/PWV7 only.
Read-only memory/hot-cue display now ships in the player (`PLAYER.md`). The
cue knowledge validated here can inform that reader, but CDJ export code remains
standalone R&D and must not be pulled into the product wholesale.

## The format (Deep-Symmetry `crate-digger` spec)

A CDJ reads, off the stick:
- `PIONEER/rekordbox/export.pdb` — a **DeviceSQL** database (the library:
  tracks, playlists, keys, colors, …). 20 tables, 4096-byte pages.
- `PIONEER/rekordbox/exportExt.pdb` — extended DB (RGB hot cues etc.).
- `PIONEER/USBANLZ/**/ANLZ0000.{DAT,EXT,2EX}` — per-track analysis: beatgrid
  (`PQTZ`), cues (`PCOB` classic, `PCO2` RGB with explicit `color_red/green/blue`),
  waveforms incl. **RGB color** (`PWV4/PWV5`, and `PWV6/PWV7/PWVC` for CDJ-3000).
- `PIONEER/*.DAT` — player/mixer settings (auto-cue, jog, RGB, DJM curves).
- `exportLibrary.db` — **SQLCipher-encrypted** "Device Library Plus" sidecar.
  **Optional** — a CDJ plays fine without it (classic `export.pdb` path). It
  carries the device *name* + background *color* (Sonny's purple).

Byte-level notes learned: file header = 7×u4 then 16-byte table pointers; each
data page has a 40-byte header, a heap growing UP from offset 40, and a **row
index growing DOWN from the end** in groups of 16 (present-flag bitmask at
`base-4`, offsets at `base-6-2i`, `base = len_page - group*0x24`). Rows are
4-byte aligned. rekordbox pre-allocates pages and does NOT zero deleted rows
("ghost" bytes). Each table is a 2-page chain: a non-data index/`first_page`
(flags `0x64`, templated `ff1ff8ff…` filler) + a data `last_page` (flags `0x24`).
`track_row` = 136-byte scalar header (tempo = BPM×100, etc.) + 21
`device_sql_string` pointers. Strings: short-ASCII (odd length byte) / long-ASCII
(`0x40`) / long-UTF-16LE (`0x90`).

## Status

### Analysis-side foundation ✅

- Shipped V4 records contain the future export allowlist, beat grid, every
  memory/hot cue, RGB overview, 24-band master, and ultra amplitude contour.
- `anlz_bridge.py` maps that record without writing files into PQTZ tempo/time
  units, PCOB/PCO2 cue semantics, PWV5 RGB bytes, and PWV7 mid/high/low bytes.
- MP3, WAV, AIFF/AIF, and FLAC can be candidates. AAC/ALAC/M4A/MP4 are
  explicitly player-only and must never enter a CDJ export.
- This does not claim that a newly assembled ANLZ container or compact PDB is
  hardware-valid. The validated scaffold-and-inject campaign below remains the
  gate before any app-visible USB export.

### Proven ✅ (all on Sonny's real files, on real hardware)
- **Read** the entire format via `pyrekordbox` + our reader — beatgrid, memory +
  RGB hot cues (exact RGB), color waveform, settings.
- **Write byte-identical** — round-trip every real `.pdb` through our
  reader→writer, SHA-identical.
- **All row codecs** — strings + keys/colors/artists/albums/labels/genres +
  `track_row` (decode + generate) + `playlist_tree`/`playlist_entries`.
- **Generate a `.pdb` from scratch** — page assembler builds a fresh file; our
  reader reads back all tracks/playlists/keys/colors, content equivalent.
- **Mounted + played on a CDJ-3000** — a stick our pipeline assembled mounts and
  plays audio.

### The decisive isolation test
A stick with **rekordbox's real `export.pdb` and NO sidecar → fully works**
(playlists, cues trigger, tracks color-analyzed). This proved three things:
1. our stick-assembly/copy pipeline is correct;
2. `exportLibrary.db` is **optional** (not the blocker);
3. **our from-scratch `export.pdb` is the sole remaining fix target** — with our
   compact pdb the CDJ says *"no rekordbox database found"* and drops to
   folder-mode (no playlists; cues show as inert ghosts; live-analyzed waveform).

### Known / cosmetic
- Device name + background color live in the encrypted `exportLibrary.db`
  (stripped in the test) → restore the sidecar later for name + purple.
- A track loading "blue" once was transient CDJ paint-while-loading — all 3
  tracks' ANLZ are intact (color `PWV4/5` + `.2EX` present).

## Next campaign — scaffold-and-inject

Our *aggressive compact* pdb was rejected (122 KB vs rekordbox's 164 KB; we
dropped pre-allocated pages and stale-copied the index pages). The strategy:

> Start from a known-good rekordbox `.pdb` **scaffold** — keep every structural
> page byte-exact (the `first_page` index pages, pre-allocation, pre-alloc
> `num_row_offsets`) — and **inject only the library's rows** into the data
> pages. That is almost certainly what the CDJ validates against, and it doubles
> as the product approach: SoundBox ships a canonical scaffold and fills it from
> the live library. Then binary-search what's actually essential.

This is a hardware-gated loop: build a candidate → walk it to the CDJ → read the
result → adjust. Best done with the decks nearby.

## Reference material (outside the repo)

`~/soundbox-cdj-ref/` holds the golden-master snapshots and staged test sticks
(large audio — deliberately NOT committed):
- `ref-usb-empty/` — a blank rekordbox scaffold (just from plugging a stick in).
- `ref-usb-full/` — 3 tracks + playlist "Test" + memory/RGB hot cues.
- `stage-A-clone/` (byte-identical control) + `stage-B-scratch/` (from-scratch).

Run the prototype under the app's own venv (`pyrekordbox` is already a dep):
`~/Projects/soundbox/.venv/bin/python`.

Spec source: Deep-Symmetry `crate-digger` (`rekordbox_pdb.ksy` /
`rekordbox_anlz.ksy`) — note the org slug is hyphenated (`Deep-Symmetry`).
