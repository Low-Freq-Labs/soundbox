"""Strict research field contracts; no writer, source authority or device access.

Display values are never native waveform payloads. Future export must obtain
fresh authoritative DB cues (ANLZ cues can be stale), verified grids and original
analysis sections against one track/audio identity before encoding anything.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import math


def _integer(value, low: int, high: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{field} must be an integer')
    if not math.isfinite(value) or int(value) != value or not low <= value <= high:
        raise ValueError(f'{field} is outside its native range')
    return int(value)


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


@dataclass(frozen=True)
class DisplayWaveformFields:
    """Display intermediates, not serialized PWV5/PWV7 or export authority."""
    rgb: tuple[tuple[int, int, int], ...]
    heights: tuple[float, ...]
    sample_rate: float


@dataclass(frozen=True)
class NativeWaveformStreams:
    """Unmodified packed payloads; framing/provenance/hardware proof are separate."""
    pwv3: bytes
    pwv5: bytes
    pwv7: bytes
    sample_rate: int = 150

    def __post_init__(self):
        if self.sample_rate != 150:
            raise ValueError('Unverified native sample rate')
        if any(type(v) is not bytes for v in (self.pwv3, self.pwv5, self.pwv7)):
            raise ValueError('Native streams must be packed bytes')
        count = len(self.pwv3)
        if not count or len(self.pwv5) != count * 2 or len(self.pwv7) != count * 3:
            raise ValueError('Native waveform counts disagree')


def beat_grid(analysis: dict) -> list[BeatRow]:
    rows = []
    previous = -1
    for row in analysis.get('beats') or []:
        if not isinstance(row, (tuple, list)) or len(row) != 3:
            raise ValueError('Malformed beat row')
        beat = _integer(row[0], 1, 4, 'beat number')
        time_ms = _integer(row[1], 0, 0xffffffff, 'beat position')
        try:
            tempo = Decimal(str(row[2])) * 100
            if not tempo.is_finite() or tempo != tempo.to_integral_value() or not 1 <= tempo <= 65535:
                raise ValueError('Tempo requires an explicit native hundredths value')
        except InvalidOperation as exc:
            raise ValueError('Invalid tempo') from exc
        if time_ms <= previous:
            raise ValueError('Beat timestamps must increase')
        previous = time_ms
        rows.append(BeatRow(beat, int(tempo), time_ms))
    return rows


def cue_rows(analysis: dict) -> list[CueRow]:
    """Canonical candidate fields; actual export still needs native DB provenance."""
    rows = []
    hot_slots = set()
    for cue in analysis.get('cues') or []:
        if not isinstance(cue, dict) or cue.get('kind') not in ('hot', 'memory'):
            raise ValueError('Unknown cue identity')
        kind = cue['kind']
        slot = _integer(cue.get('slot'), 0, 15 if kind == 'hot' else 0xffffffff, 'cue slot')
        if kind == 'hot':
            if slot in hot_slots:
                raise ValueError('Duplicate hot cue slot')
            hot_slots.add(slot)
        start = _integer(cue.get('position_ms'), 0, 0xfffffffe, 'cue position')
        cue_type = cue.get('type')
        if cue_type == 'loop':
            end = _integer(cue.get('end_ms'), start + 1, 0xfffffffe, 'loop endpoint')
        elif cue_type == 'single' and cue.get('end_ms', -1) == -1:
            end = 0xffffffff
        else:
            raise ValueError('Invalid cue type/endpoint')
        color = cue.get('color', [0, 0, 0])
        if not isinstance(color, (list, tuple)) or len(color) != 3:
            raise ValueError('Malformed cue RGB')
        rgb = tuple(_integer(v, 0, 255, 'cue RGB') for v in color)
        label = cue.get('label', '')
        if not isinstance(label, str):
            raise ValueError('Invalid cue comment')
        rows.append(CueRow(kind, slot + 1 if kind == 'hot' else 0, start, end,
                           2 if cue_type == 'loop' else 1, label, rgb))
    return rows


def waveform_rows(analysis: dict) -> DisplayWaveformFields:
    colors, heights = analysis.get('colors') or [], analysis.get('heights') or []
    if len(colors) != len(heights):
        raise ValueError('Display waveform counts disagree')
    rgb = []
    for color in colors:
        if not isinstance(color, (list, tuple)) or len(color) != 3:
            raise ValueError('Malformed display RGB')
        rgb.append(tuple(_integer(v, 0, 255, 'display RGB') for v in color))
    for value in heights:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError('Invalid display height')
    rate = float(analysis.get('sample_rate', 150))
    if not math.isfinite(rate) or rate <= 0:
        raise ValueError('Invalid display sample rate')
    return DisplayWaveformFields(tuple(rgb), tuple(heights), rate)


def export_contract(analysis: dict) -> dict:
    """Non-writing research candidate; eligibility is not device acceptance."""
    eligibility = dict(analysis.get('eligibility') or {})
    return {'eligible': eligibility.get('eligible') is True, 'eligibility': eligibility,
            'pqtz': beat_grid(analysis), 'pcob_pco2': cue_rows(analysis),
            'display_waveform_fields': waveform_rows(analysis)}
