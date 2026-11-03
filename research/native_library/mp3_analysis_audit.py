"""Compare explicit MP3 references with fresh TEMP-rendered analysis packets.

This research command reads no database and publishes no source or authority.
Native seek bytes are evidence to compare, never a template silently copied into
the generated packet. A successful audit is not MP3 source-write acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import stat
import tempfile

from soundbox.cdj_usb import run_renderer
from soundbox.native_media import epoch
from soundbox.rekordbox_grid import AnalysisImage, read_grid
from soundbox.source_acquisition import SourceContract, acquire_source
from .mp3_seek import scan_mp3, compare_pvbr, cbr_pvbr
from .mp3_timing import audit as timing_audit

NAMES = {'.DAT': 'ANLZ0000.DAT', '.EXT': 'ANLZ0000.EXT', '.2EX': 'ANLZ0000.2EX'}
LIMIT = 64 * 1024 * 1024


def _read(path):
    path = Path(path).absolute()
    if (path != path.resolve(strict=True) or path.is_relative_to('/Volumes')
            or not stat.S_ISREG(path.lstat().st_mode)):
        raise ValueError('Explicit original regular reference files required')
    before = epoch(path.stat())
    if not 0 < before[2] <= LIMIT:
        raise ValueError('Reference packet exceeds its byte budget')
    with path.open('rb') as stream:
        raw = stream.read(LIMIT + 1)
    if len(raw) != before[2] or epoch(path.stat()) != before:
        raise ValueError('Reference packet changed during capture')
    return path, before, raw


def _packet_summary(raw):
    image = AnalysisImage.parse(raw)
    return dict(sha256=hashlib.sha256(raw).hexdigest(), size=len(raw), sections=[
        dict(kind=s.kind.decode('ascii'), header_size=s.header_size, size=len(s.raw),
             sha256=hashlib.sha256(s.raw).hexdigest()) for s in image.sections])


def compare(audio, native_packets, output, *, renderer=None):
    """Render only a captured local copy; exclusively create one small report."""
    audio, output = Path(audio).absolute(), Path(output).absolute()
    if set(native_packets) != set(NAMES):
        raise ValueError('Explicit native DAT/EXT/2EX reference paths required')
    if (audio != audio.resolve(strict=True) or audio.is_relative_to('/Volumes') or audio.suffix.lower() != '.mp3'
            or not stat.S_ISREG(audio.lstat().st_mode)):
        raise ValueError('Explicit original MP3 reference required')
    captured = {ext: _read(path) for ext, path in native_packets.items()}
    protected = [audio, *(entry[0] for entry in captured.values())]
    # Report placement cannot add files to a protected reference directory.
    if (output.exists() or output.is_symlink() or output.parent != output.parent.resolve(strict=True)
            or any(output.is_relative_to(path.parent) for path in protected)):
        raise ValueError('New report must be outside the protected reference directories')
    packets = {ext: entry[2] for ext, entry in captured.items()}
    expected_path = ('?/' + audio.name + '\0').encode('utf-16-be')
    for raw in packets.values():
        path = AnalysisImage.parse(raw).section(b'PPTH')
        if path.header_size != 16 or path.raw[16:] != expected_path:
            raise ValueError('Native packets must name the exact MP3 reference filename')
    grid = read_grid(packets['.DAT'])
    if not 2 <= len(grid) <= 100_000:
        raise ValueError('Native reference grid exceeds its row budget')
    before = epoch(audio.stat())
    if not 0 < before[2] <= 256 * 1024 * 1024:
        raise ValueError('MP3 reference exceeds its byte budget')
    with tempfile.TemporaryDirectory(prefix='soundbox-mp3-audit-') as area:
        root = Path(area).resolve()
        with acquire_source(SourceContract(audio, before[2]), root / 'audio') as source:
            request = root / 'request.json'
            request.write_text(json.dumps(dict(audio=str(source.path), original_name=audio.name,
                beats=[dict(number=b.number, tempo=b.tempo, time_ms=b.time_ms) for b in grid])))
            result = (renderer or run_renderer)('saved-analysis', root, request)
            generated = {ext: _read(root / 'packet' / name)[2] for ext, name in NAMES.items()}
            native_timing = timing_audit(source.path, packets, result)
            generated_timing = timing_audit(source.path, generated, result)
            if any(timing['source']['sha256'] != source.sha256 for timing in (native_timing, generated_timing)):
                raise ValueError('Captured MP3 bytes changed during rendering')
            native_seek = AnalysisImage.parse(packets['.DAT']).section(b'PVBR').raw
            generated_seek = AnalysisImage.parse(generated['.DAT']).section(b'PVBR').raw
            scan = scan_mp3(source.path, expected_sha256=source.sha256)
            seek = compare_pvbr(scan, native_seek)
            seek.pop('rows')  # Keep reports small; the scanner exposes detailed rows separately.
            if scan.cbr and scan.info_kind is None:
                seek['constructed_sha256'] = hashlib.sha256(cbr_pvbr(scan)).hexdigest()
            report = dict(format='soundbox-mp3-analysis-audit-v1', source_sha256=source.sha256,
                source_size=before[2], renderer=result, native_timing=native_timing,
                generated_timing=generated_timing, native_seek=seek,
                packets={ext: dict(native=_packet_summary(packets[ext]),
                                   generated=_packet_summary(generated[ext])) for ext in NAMES},
                grid_exact=read_grid(generated['.DAT']) == grid,
                seek_table_exact=native_seek == generated_seek,
                source_write_authority=False, native_acceptance=False,
                scope='Copy-only MP3 packet/timing comparison; no database, source publisher or authority')
            # Header decimal rounding is evidence, not an incompatible packet.
            # Structural/count/clock disagreements must still fail the CLI even
            # if its primary grid and seek bytes happen to match.
            report['comparison_ok'] = bool(report['grid_exact'] and report['seek_table_exact']
                and result['frames'] == scan.sample_count and result['rate'] == scan.sample_rate
                and not any(issue != 'header_renderer_durations_differ'
                    for timing in (native_timing, generated_timing) for issue in timing['disagreements']))
        if epoch(audio.stat()) != before:
            raise ValueError('Protected MP3 reference changed during rendering')
    for path, baseline, raw in captured.values():
        if epoch(path.stat()) != baseline or _read(path)[2] != raw:
            raise ValueError('Protected native reference changed during rendering')
    report['protected_inputs_unchanged'] = True
    encoded = json.dumps(report, indent=2, allow_nan=False) + '\n'
    with output.open('x') as stream:
        stream.write(encoded)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('audio', type=Path)
    for ext in NAMES:
        parser.add_argument('--native-' + ext[1:].lower(), type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    report = compare(args.audio, {'.DAT': args.native_dat, '.EXT': args.native_ext,
                                 '.2EX': args.native_2ex}, args.report)
    print(json.dumps({key: report[key] for key in ('comparison_ok', 'grid_exact', 'seek_table_exact',
        'protected_inputs_unchanged', 'source_write_authority', 'native_acceptance')}, indent=2))
    # A packet mismatch remains visible to scripts; no green acceptance signal.
    return 0 if report['comparison_ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
