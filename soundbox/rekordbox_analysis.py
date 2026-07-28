"""Read-only access to rekordbox's analyzed beat grid, cues, and RGB waveform.

The live database stores an ``AnalysisDataPath`` for each track. SoundBox
resolves that path beneath rekordbox's ``share/`` directory, then reads:

* ``ANLZ0000.DAT`` → PQTZ beat number, BPM, and exact beat position.
* ``ANLZ0000.DAT/EXT`` → PCOB/PCO2 memory and hot cues.
* ``ANLZ0000.EXT`` → PWV3 BLUE and PWV5 RGB detail (~150 samples/second).
* ``ANLZ0000.2EX`` → PWV7 higher-precision three-band detail at the same rate.

This module NEVER builds or saves an ANLZ file. It only opens existing files in
binary read mode through pyrekordbox and returns plain JSON-friendly data.
"""

from __future__ import annotations

from pathlib import Path
import struct


def _enum_name(value) -> str:
    """Normalize construct Enum values without depending on their exact class."""
    text = str(value).lower()
    return text if text.isalpha() else ""


def _cue_rows(*files) -> list[dict]:
    """Merge classic and extended cue tags without discarding memory cues.

    PCO2 carries comments and explicit RGB, so it is applied after PCOB and
    wins when both describe the same cue. Memory cues have no fixed hot-cue
    letter; they are placed in chronological order. The deck presents the
    first eight in its A–H page, while V4 retains every imported row for future
    editing/export fidelity.
    """
    merged: dict[tuple, dict] = {}
    default_hot_colors = (
        [255, 55, 111], [69, 172, 219], [125, 193, 61], [170, 114, 255],
        [48, 210, 110], [224, 100, 27], [48, 90, 255], [195, 175, 4],
    )

    def tags(file, tag_type):
        if file is None or tag_type not in getattr(file, "tag_types", []):
            return []
        try:
            return file.getall_tags(tag_type)
        except (AttributeError, KeyError):
            try:
                return [file.get_tag(tag_type)]
            except (AttributeError, KeyError):
                return []

    def collect(tag, extended: bool):
        content = tag.get() if hasattr(tag, "get") else tag.content
        tag_kind = _enum_name(
            getattr(content, "cue_type", getattr(content, "type", "")))
        for entry in getattr(content, "entries", []):
            status = _enum_name(getattr(entry, "status", "enabled"))
            if status == "disabled" or getattr(entry, "status", 4) == 0:
                continue
            hot_number = int(getattr(entry, "hot_cue", 0) or 0)
            kind = "hot" if tag_kind == "hotcue" or hot_number else "memory"
            position = max(0, int(getattr(entry, "time", 0) or 0))
            if kind == "hot":
                if not 1 <= hot_number <= 8:
                    continue
                slot = hot_number - 1
                key = (kind, slot)
            else:
                slot = -1
                key = (kind, position)
            cue_type = _enum_name(getattr(entry, "type", "")) or (
                "loop" if int(getattr(entry, "type", 1) or 1) == 2 else "single")
            end = int(getattr(entry, "loop_time", -1) or -1)
            label = str(getattr(entry, "comment", "") or "").strip()
            color = [
                max(0, min(255, int(getattr(entry, name, 0) or 0)))
                for name in ("color_red", "color_green", "color_blue")
            ]
            if kind == "hot" and not any(color):
                color = list(default_hot_colors[slot])
            row = {
                "kind": kind,
                "slot": slot,
                "position_ms": position,
                "end_ms": end if position < end < 0xffffffff else -1,
                "type": cue_type,
                "label": label,
                "color": color,
            }
            old = merged.get(key)
            if old is None or extended:
                if old is not None:
                    row["label"] = label or old["label"]
                    if not any(color):
                        row["color"] = old["color"]
                merged[key] = row

    # Classic entries are useful fallbacks; extended entries add label/RGB.
    for file in files:
        for tag in tags(file, "PCOB"):
            collect(tag, False)
    for file in files:
        for tag in tags(file, "PCO2"):
            collect(tag, True)

    hot = sorted(
        (row for row in merged.values() if row["kind"] == "hot"),
        key=lambda row: row["slot"])[:8]
    memory = sorted(
        (row for row in merged.values() if row["kind"] == "memory"),
        key=lambda row: row["position_ms"])
    for slot, row in enumerate(memory):
        row["slot"] = slot
    return hot + memory


def read_cues(dat_path: str | Path) -> list[dict]:
    """Read existing PCOB/PCO2 cue data without modifying Rekordbox files."""
    dat = Path(dat_path)
    if dat.suffix.upper() != ".DAT" or not dat.is_file():
        return []
    try:
        from pyrekordbox.anlz import AnlzFile

        dat_file = AnlzFile.parse_file(dat)
        ext = dat.with_suffix(".EXT")
        ext_file = AnlzFile.parse_file(ext) if ext.is_file() else None
        return _cue_rows(dat_file, ext_file)
    except Exception:  # noqa: BLE001 - malformed/missing cue tags are non-fatal
        return []


def _read_waveform_tag(
        path: str | Path, tag_type: bytes, entry_size: int) -> bytes:
    """Return one validated waveform tag payload from an existing ANLZ file.

    BLUE, RGB, and 3BAND must remain distinct renderer inputs. Reading their
    original packed rows also avoids losing precision through a decode/repack
    round trip. The file is opened only through ``read_bytes`` and is never
    modified.
    """
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return b""
    cursor = 0
    while True:
        offset = raw.find(tag_type, cursor)
        if offset < 0 or offset + 24 > len(raw):
            return b""
        try:
            header_size, tag_size = struct.unpack_from(">II", raw, offset + 4)
            stored_entry_size, count = struct.unpack_from(">II", raw, offset + 12)
        except struct.error:
            return b""
        data_start = offset + header_size
        data_size = count * stored_entry_size
        if (header_size >= 24 and stored_entry_size == entry_size
                and tag_size >= header_size + data_size
                and data_start + data_size <= len(raw)):
            return raw[data_start:data_start + data_size]
        cursor = offset + 4


def _read_pwv3(dat_path: str | Path) -> bytes:
    """Return exact packed BLUE detail rows from the sibling EXT file."""
    return _read_waveform_tag(
        Path(dat_path).with_suffix(".EXT"), b"PWV3", 1)


def _read_pwv5(dat_path: str | Path) -> bytes:
    """Return exact packed RGB detail rows from the sibling EXT file."""
    return _read_waveform_tag(
        Path(dat_path).with_suffix(".EXT"), b"PWV5", 2)


def _read_pwv7(dat_path: str | Path) -> bytes:
    """Return exact packed 3BAND rows from the sibling 2EX file."""
    return _read_waveform_tag(
        Path(dat_path).with_suffix(".2EX"), b"PWV7", 3)


def _beat_rows(dat_file) -> list[list]:
    grid = dat_file.get_tag("PQTZ")
    return [
        [int(beat), int(round(float(time) * 1000)), round(float(bpm), 2)]
        for beat, bpm, time in zip(grid.beats, grid.bpms, grid.times)
    ]


def read_beats(dat_path: str | Path) -> list[list]:
    """Read only the DAT/PQTZ grid, never EXT/2EX waveform sections.

    The metadata migration can touch thousands of tracks. Parsing the entire
    DAT object graph for eight-byte PQTZ rows wastes substantial CPU and
    battery, so this intentionally small reader validates the tag bounds and
    decodes only those rows from a read-only byte buffer.
    """
    dat = Path(dat_path)
    if dat.suffix.upper() != ".DAT" or not dat.is_file():
        return []
    try:
        raw = dat.read_bytes()
    except OSError:
        return []
    cursor = 0
    while True:
        offset = raw.find(b"PQTZ", cursor)
        if offset < 0 or offset + 24 > len(raw):
            return []
        try:
            header_size, tag_size = struct.unpack_from(">II", raw, offset + 4)
            count = struct.unpack_from(">I", raw, offset + 20)[0]
        except struct.error:
            return []
        data_start = offset + header_size
        data_size = count * 8
        if (header_size >= 24
                and tag_size >= header_size + data_size
                and offset + tag_size <= len(raw)
                and data_start + data_size <= len(raw)):
            rows = []
            try:
                for index in range(count):
                    beat, tempo, position = struct.unpack_from(
                        ">HHI", raw, data_start + index * 8)
                    rows.append([
                        int(beat), int(position), round(tempo / 100.0, 2)])
            except struct.error:
                return []
            return rows
        cursor = offset + 4


def resolve_dat_path(db_path: str | Path, analysis_data_path: str) -> Path | None:
    """Resolve a DB ``AnalysisDataPath`` safely beneath ``<db dir>/share``.

    Returns None for blank or traversal-style paths. Pure path resolution; the
    target need not exist yet, which keeps the live DB load free of thousands
    of extra file stats.
    """
    raw = (analysis_data_path or "").strip().strip("\\/")
    if not raw:
        return None
    share = (Path(db_path).expanduser().parent / "share").resolve()
    candidate = (share / raw).resolve()
    if candidate != share and share not in candidate.parents:
        return None
    return candidate


def read(dat_path: str | Path) -> dict:
    """Read one track's existing grid/cues and all detailed waveform modes.

    ``beats`` rows are ``[beat_in_bar, position_ms, bpm]``. ``heights`` are
    normalized 0..1 and ``colors`` are rekordbox's per-sample RGB intensities.
    ``_blue_detail``, ``_rgb_detail``, and ``_detail_bands`` retain exact
    PWV3/PWV5/PWV7 packed rows. All three streams use the format's 150 Hz song
    clock and are copied only into SoundBox-owned cache storage.
    """
    dat = Path(dat_path)
    if dat.suffix.upper() != ".DAT" or not dat.is_file():
        return {}
    try:
        from pyrekordbox.anlz import AnlzFile

        dat_file = AnlzFile.parse_file(dat)
        beats = _beat_rows(dat_file)

        heights: list[float] = []
        colors: list[list[int]] = []
        ext_file = None
        ext = dat.with_suffix(".EXT")
        if ext.is_file():
            ext_file = AnlzFile.parse_file(ext)
            if "PWV5" in ext_file.tag_types:
                hs, cs = ext_file.get_tag("PWV5").get()
                heights = [round(float(x), 4) for x in hs]
                colors = [[int(v) for v in row] for row in cs]
        return {
            "beats": beats,
            "heights": heights,
            "colors": colors,
            "cues": _cue_rows(dat_file, ext_file),
            "_blue_detail": _read_pwv3(dat),
            "_rgb_detail": _read_pwv5(dat),
            "_detail_bands": _read_pwv7(dat),
            "sample_rate": 150.0,
        }
    except Exception:  # noqa: BLE001 - malformed/missing analysis => plain fallback
        return {}
