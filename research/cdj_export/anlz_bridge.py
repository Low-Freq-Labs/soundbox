"""Pure canonical analysis → rekordbox ANLZ field bridge (no writes).

This module is the seam between SoundBox-owned analysis and the future,
hardware-gated USB writer.  It expresses the exact units/ranges required by
PQTZ, PCOB/PCO2, PWV5 and PWV7 without pretending that assembling a valid ANLZ
container is already product-safe.  The eventual scaffold injector consumes
this contract; this module never opens an ANLZ path.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BeatRow:
    beat: int
    tempo_x100: int
    time_ms: int


@dataclass(frozen=True)
class CueRow:
    kind: str
    hot_cue: int
    time_ms: int
    loop_time_ms: int
    cue_type: int
    comment: str
    rgb: tuple[int, int, int]


def _byte(value) -> int:
    return max(0, min(255, int(value)))


def beat_grid(analysis: dict) -> list[BeatRow]:
    """Map canonical beats to PQTZ's beat/u16-tempo/u32-millisecond rows."""
    result = []
    for index, row in enumerate(analysis.get("beats") or []):
        if not isinstance(row, (list, tuple)) or len(row) < 3:
            continue
        beat = int(row[0])
        if beat not in {1, 2, 3, 4}:
            beat = index % 4 + 1
        result.append(BeatRow(
            beat=beat,
            tempo_x100=max(0, min(0xffff, round(float(row[2]) * 100))),
            time_ms=max(0, min(0xffffffff, int(row[1]))),
        ))
    return result


def cue_rows(analysis: dict) -> list[CueRow]:
    """Map all canonical hot/memory cues to PCOB/PCO2 field semantics."""
    rows = []
    for cue in analysis.get("cues") or []:
        if not isinstance(cue, dict):
            continue
        kind = "hot" if cue.get("kind") == "hot" else "memory"
        slot = int(cue.get("slot", -1))
        # Preserve canonical hot-cue identity, including I–P. A concrete
        # destination with a smaller capacity must reject/truncate explicitly;
        # silently writing 0 would misclassify the cue as a memory cue.
        hot_cue = slot + 1 if kind == "hot" and 0 <= slot < 16 else 0
        start = max(0, min(0xffffffff, int(cue.get("position_ms", 0))))
        end = int(cue.get("end_ms", -1))
        loop = end if start < end <= 0xffffffff else 0xffffffff
        color = list(cue.get("color") or []) + [0, 0, 0]
        rows.append(CueRow(
            kind=kind,
            hot_cue=hot_cue,
            time_ms=start,
            loop_time_ms=loop,
            cue_type=2 if cue.get("type") == "loop" else 1,
            comment=str(cue.get("label") or ""),
            rgb=tuple(_byte(value) for value in color[:3]),
        ))
    return rows


def waveform_rows(analysis: dict) -> dict:
    """Return exact PWV5 RGB and PWV7 mid/high/low byte streams.

    Canonical analysis keeps 24 normalized spectral bands per 150 Hz frame.
    PWV7 groups those
    into Rekordbox's three-byte order: mid, high, low. PWV5 uses the canonical
    RGB overview directly.  The future writer can insert these bytes into a
    validated scaffold without re-analyzing or color-guessing at export time.
    """
    colors = analysis.get("colors") or []
    heights = analysis.get("heights") or []
    count = min(len(colors), len(heights))
    pwv5 = bytes(
        _byte(channel)
        for row in colors[:count] for channel in (list(row) + [0, 0, 0])[:3])
    master = analysis.get("_spectral_24")
    pwv7 = bytearray()
    if isinstance(master, bytes):
        frames = min(count, len(master) // 24)
        for frame in range(frames):
            bands = master[frame * 24:(frame + 1) * 24]
            low = round(sum(bands[:8]) / 8)
            mid = round(sum(bands[8:16]) / 8)
            high = round(sum(bands[16:]) / 8)
            pwv7.extend((_byte(mid), _byte(high), _byte(low)))
    return {
        "sample_rate": float(analysis.get("sample_rate") or 150.0),
        "pwv5_rgb": pwv5,
        "pwv7_mid_high_low": bytes(pwv7),
        "count": count,
    }


def export_contract(analysis: dict) -> dict:
    """Build the complete non-writing ANLZ payload contract."""
    eligibility = dict(analysis.get("eligibility") or {})
    return {
        "eligible": bool(eligibility.get("eligible")),
        "eligibility": eligibility,
        "pqtz": beat_grid(analysis),
        "pcob_pco2": cue_rows(analysis),
        **waveform_rows(analysis),
    }
