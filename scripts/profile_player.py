"""Read-only frozen-fixture memory/temporal probes; no live library or devices.

Reconstruct a disposable cache from the required, hash-verified offline fixture.
Never resolve the historical source path embedded in its manifest. Captures use
muted audio and the production renderer. Results are diagnostic, not a frame gate.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import time

from scripts.check_fixtures import waveform_reference
from soundbox import track_analysis as ta


def flat_red_pwv5(packed: bytes) -> bytes:
    """Diagnostic color override; preserve native big-endian height/reserved bits."""
    import numpy as np
    if len(packed) % 2:
        raise ValueError('Incomplete PWV5 word')
    words = np.frombuffer(packed, dtype='>u2')
    return ((words & 0x7f) | 0xe000).astype('>u2').tobytes()


def offline_cache(destination: Path) -> tuple[str, Path]:
    root, reference = waveform_reference()
    source = root / reference['source_file']
    raw = (root / reference['cache_file']).read_bytes()
    _, size = struct.unpack('<4sI', raw[:8])
    manifest = json.loads(raw[8:8 + size])
    sections = ta._decode_sections(raw[8 + size:], manifest)
    if sections is None:
        raise ValueError('Frozen sections failed validation')
    prepared = {**manifest, **sections,
                'detail_minimum_values': sections['detail_minimum_d1'],
                'detail_maximum_values': sections['detail_maximum_d1']}
    tid = reference['track_id']
    if ta.store(destination, tid, source, prepared) is None:
        raise ValueError('Cannot build disposable fixture cache')
    return tid, source


def main() -> None:
    from PySide6.QtCore import QUrl
    from PySide6.QtWidgets import QApplication
    from desktop.native_deck import NativeDeckBackend
    from desktop.player import WaveformBar
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--switches', type=int, default=100)
    parser.add_argument('--capture', action='store_true')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    host = WaveformBar(); host.resize(1000, 166); host.show()
    app.processEvents()
    deck = NativeDeckBackend(host)
    rows = []
    def settle(seconds=.05):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents(); time.sleep(.002)
    def record(phase):
        rss = int(subprocess.check_output(['ps', '-p', str(os.getpid()), '-o', 'rss='], text=True))
        rows.append({'phase': phase, 'rss_kib': rss,
                     'native_audio_detail_bytes': deck.diagnostic_audio_detail_buffer_bytes()})
        (args.out / 'memory.json').write_text(json.dumps(rows, indent=2) + '\n')
    try:
        deck.set_gain(0)
        host.set_native_rendering(True)
        record('empty')
        with tempfile.TemporaryDirectory(prefix='soundbox-offline-player-') as area:
            cache = Path(area)
            tid, source = offline_cache(cache)
            manifest = ta.load_manifest(cache, tid, source)
            overview = {**manifest, **ta.load_overview(cache, tid, source)}
            detail = ta.load_detail(cache, tid, source)
            def load(generation):
                deck.begin_track(generation)
                deck.setSource(QUrl.fromLocalFile(str(source)))
                if not all(deck.set_complete_analysis(overview, detail, generation)):
                    raise ValueError('Native upload failed')
                settle()
            load(1); record('loaded')
            deck.play(); settle(.5); record('playing')
            deck.pause(); settle(.1); record('paused')
            for index in range(args.switches):
                load(index + 2)
                if (index + 1) % 10 == 0:
                    record(f'switch-{index + 1}')
            if args.capture:
                import numpy as np
                original = detail
                for control in ('normal', 'flat-red', 'fixed-contour'):
                    changed = dict(original)
                    if control == 'flat-red':
                        changed['_rgb_detail'] = flat_red_pwv5(original['_rgb_detail'])
                    elif control == 'fixed-contour':
                        audio = dict(original['_audio_detail'])
                        audio['range_min_mips'] = [np.full(count, -16384, dtype='<i2').tobytes()
                                                   for count in audio['mip_counts']]
                        audio['range_max_mips'] = [np.full(count, 16384, dtype='<i2').tobytes()
                                                   for count in audio['mip_counts']]
                        changed['_audio_detail'] = audio
                    generation = args.switches + 10
                    deck.begin_track(generation)
                    deck.setSource(QUrl.fromLocalFile(str(source)))
                    deck.set_complete_analysis(overview, changed, generation)
                    deck.set_waveform_mode('RGB')
                    for zoom in (128, 184, 256, 384):
                        deck.pause(); deck.setPosition(27000); deck.set_zoom(zoom); settle(.1)
                        deck.diagnostic_begin_frame_trace(24, 128)
                        deck.play()
                        deadline = time.monotonic() + 5
                        while deck.diagnostic_frame_trace_status()['presentation_completed_frames'] < 24:
                            if time.monotonic() > deadline:
                                raise RuntimeError('Capture timed out')
                            settle(.01)
                        deck.pause()
                        target = args.out / f'{control}-{zoom}'
                        target.mkdir()
                        deck.diagnostic_finish_frame_trace(target)
            with (args.out / 'vmmap.txt').open('w') as output:
                subprocess.run(['vmmap', '-summary', str(os.getpid())], check=True, stdout=output)
        (args.out / 'memory.json').write_text(json.dumps(rows, indent=2) + '\n')
        print(json.dumps(rows, indent=2))
    finally:
        deck.close(); host.close(); app.processEvents()


if __name__ == '__main__':
    main()
