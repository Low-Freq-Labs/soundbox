"""Read-only comparison of captured MP3 clocks and explicit native packets.

This reports evidence, not a duration policy. Header, playback-origin and renderer
clocks stay separate; an agreement grants no analysis/source-write authority.
"""
from fractions import Fraction
import hashlib
import math
import os
from pathlib import Path
import stat
import struct

from soundbox.media_probe import read_media_header
from soundbox.native_media import epoch
from soundbox.rekordbox_grid import AnalysisImage, read_grid
from soundbox.rekordbox_write import ConflictError, WriteProofError
from soundbox.waveform_detail import mp3_origin

MAX_SOURCE_BYTES = 256 * 1024 * 1024
MAX_PACKET_BYTES = 64 * 1024 * 1024
RENDER_FIELDS = {'frames', 'rate', 'channels', 'duration_ms', 'detail_columns',
                 'peak', 'rms', 'dat_bytes', 'ext_bytes', 'two_ex_bytes',
                 'source_write_authority'}


def _ratio(value):
    return {'numerator': value.numerator, 'denominator': value.denominator}


def _renderer(report):
    if not isinstance(report, dict) or set(report) != RENDER_FIELDS:
        raise WriteProofError('Exact saved-analysis renderer report required')
    for name in ('frames', 'rate', 'channels', 'detail_columns',
                 'dat_bytes', 'ext_bytes', 'two_ex_bytes'):
        if type(report[name]) is not int or report[name] <= 0:
            raise WriteProofError('Invalid renderer dimensions')
    for name in ('duration_ms', 'peak', 'rms'):
        value = report[name]
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise WriteProofError('Invalid renderer measurement')
    if (report['source_write_authority'] is not False
            or not 8000 <= report['rate'] <= 192000 or not 1 <= report['channels'] <= 8
            or report['frames'] > min(1800 * report['rate'], 96000000)
            or abs(report['duration_ms'] - report['frames'] * 1000 / report['rate']) > 0.000001):
        raise WriteProofError('Renderer clock or scope is inconsistent')
    return Fraction(report['frames'], report['rate'])


def audit(captured_mp3, packets, renderer_report=None):
    """Inspect an explicit captured file and DAT/EXT/2EX bytes without decoding.

    The caller retains the evidence. No filesystem outputs, SQL, discovery or
    publisher exist here. The optional renderer report describes these exact
    supplied packets; passing native packets with a different renderer result
    deliberately exposes their differing sizes/counts in ``disagreements``.
    """
    path = Path(captured_mp3)
    if (not path.is_absolute() or path != path.resolve(strict=True)
            or path.is_symlink() or path.is_relative_to('/Volumes')
            or path.suffix.lower() != '.mp3'):
        raise WriteProofError('Explicit canonical captured desktop MP3 required')
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_SOURCE_BYTES:
        raise WriteProofError('Captured MP3 exceeds its file budget')
    if (not isinstance(packets, dict) or set(packets) != {'.DAT', '.EXT', '.2EX'}
            or any(type(raw) is not bytes or not 0 < len(raw) <= MAX_PACKET_BYTES
                   for raw in packets.values())):
        raise WriteProofError('Explicit bounded DAT/EXT/2EX bytes required')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        if epoch(os.fstat(stream.fileno())) != epoch(before):
            raise ConflictError('Captured MP3 changed before inspection')
        remaining = before.st_size
        while remaining:
            chunk = stream.read(min(1024 * 1024, remaining))
            if not chunk:
                raise ConflictError('Captured MP3 truncated during inspection')
            digest.update(chunk)
            remaining -= len(chunk)
        if stream.read(1):
            raise ConflictError('Captured MP3 grew during inspection')
    header = read_media_header(path)
    if header.family != 'mp3' or header.codec != 'mp3':
        raise WriteProofError('Captured source is not a verified MP3 header')
    origin = mp3_origin(path)
    images = {ext: AnalysisImage.parse(raw) for ext, raw in packets.items()}
    beats = read_grid(packets['.DAT'])
    details = {}
    for ext, kind, width in (('.EXT', b'PWV3', 1), ('.EXT', b'PWV5', 2), ('.2EX', b'PWV7', 3)):
        section = images[ext].section(kind)
        if section.header_size != 24:
            raise WriteProofError('Unverified native detail header')
        unit, count, _flags = struct.unpack_from('>III', section.raw, 12)
        if unit != width or count == 0 or len(section.raw) != 24 + width * count:
            raise WriteProofError('Native detail count/width mismatch')
        details[kind.decode()] = count
    vbr = images['.DAT'].section(b'PVBR')
    if vbr.header_size != 16 or len(vbr.raw) != 1620:
        raise WriteProofError('Unverified native seek-table dimensions')
    disagreements = []
    if len(set(details.values())) != 1:
        disagreements.append('native_detail_counts_differ')
    renderer_duration = _renderer(renderer_report) if renderer_report is not None else None
    comparison = None
    if renderer_duration is not None:
        if renderer_report['rate'] != header.sample_rate:
            disagreements.append('header_renderer_sample_rates_differ')
        if renderer_report['channels'] != header.channels:
            disagreements.append('header_renderer_channels_differ')
        if any(count != renderer_report['detail_columns'] for count in details.values()):
            disagreements.append('native_renderer_detail_counts_differ')
        if any(len(packets[ext]) != renderer_report[field] for ext, field in
               (('.DAT', 'dat_bytes'), ('.EXT', 'ext_bytes'), ('.2EX', 'two_ex_bytes'))):
            disagreements.append('native_renderer_packet_sizes_differ')
        if header.duration != renderer_duration:
            disagreements.append('header_renderer_durations_differ')
        if beats and Fraction(beats[-1].time_ms, 1000) > renderer_duration:
            disagreements.append('native_grid_exceeds_renderer_endpoint')
        comparison = {'duration': _ratio(renderer_duration),
                      'header_delta_seconds': _ratio(renderer_duration - header.duration),
                      'detail_delta_columns': {kind: renderer_report['detail_columns'] - count
                                               for kind, count in details.items()}}
    after = path.stat()
    if path != path.resolve(strict=True) or epoch(after) != epoch(before):
        raise ConflictError('Captured MP3 changed during inspection')
    return {
        'format': 'soundbox-mp3-timing-audit-v1',
        'source': {'path': str(path), 'epoch': list(epoch(before)), 'sha256': digest.hexdigest(),
                   'header_duration': _ratio(header.duration), 'sample_rate': header.sample_rate,
                   'channels': header.channels},
        'playback_origin': {'recognized': origin.sample_rate > 0, 'lead_frames': origin.lead_frames,
                            'decoded_frames': origin.decoded_frames, 'sample_rate': origin.sample_rate},
        'native': {'packet_sha256': {ext: hashlib.sha256(raw).hexdigest() for ext, raw in packets.items()},
                   'detail_columns': details, 'grid_count': len(beats),
                   'first_beat_ms': beats[0].time_ms if beats else None,
                   'last_beat_ms': beats[-1].time_ms if beats else None,
                   'pvbr_sha256': hashlib.sha256(vbr.raw).hexdigest(),
                   'pvbr_nonzero_payload_bytes': sum(value != 0 for value in vbr.raw[16:])},
        'renderer': comparison, 'disagreements': disagreements, 'source_write_authority': False,
    }
