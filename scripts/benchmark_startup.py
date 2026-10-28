"""Launch a packaged app against synthetic metadata; includes process/bootstrap time.

No default live state, source library, media, Keychain or device access. The
optional metadata input is copied and sanitized before the app can see it.
Results describe snapshot browsing, not fresh-source or physical-device readiness.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from soundbox import libcache
from soundbox.library import Library, Playlist, Track


def prepare(state: Path, metadata: Path | None, count: int) -> None:
    state = state.resolve()
    if metadata:
        lib = libcache.load(metadata / 'libcache.json')
        if lib is None:
            raise ValueError('Invalid metadata snapshot')
        data = json.loads((metadata / 'library.json').read_bytes())
    else:
        tracks = {str(i): Track(str(i), f'Track {i}', f'Artist {i % 300}',
                               f'file:///missing/{i}.wav', added_ts=float(i + 1))
                  for i in range(count)}
        lib = Library(tracks, [Playlist('Collection', [], list(tracks), 'synthetic')], 'synthetic')
        data = {}
    lib.source = state / 'source-disabled.db'
    lib.source_kind = 'cache'
    for track in lib.tracks_by_id.values():
        filename = track.path.name
        track.location_uri = (state / 'missing-media' / filename).as_uri()
        track.source_analysis_path = ''
        track.added_ts = track.added_ts or 1.0
    data['imported'] = {}  # already in the snapshot; never resolve bookmarks
    data['last_xml'] = ''
    data.setdefault('settings', {}).pop('rekordbox_db_path', None)
    (state / 'library.json').write_text(json.dumps(data))
    art = state / 'art'
    art.mkdir()
    (art / '.normalized-v1').touch()
    if metadata:
        for name in ('index.json', 'source-policy.json'):
            source = metadata / 'art' / name
            if source.is_file():
                shutil.copyfile(source, art / name)
    libcache.dump(lib, state / 'libcache.json')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--metadata-from', type=Path)
    parser.add_argument('--tracks', type=int, default=3740)
    parser.add_argument('--runs', type=int, default=20)
    args = parser.parse_args()
    if args.runs < 1 or args.tracks < 1:
        parser.error('runs and tracks must be positive')
    args.out.mkdir(parents=True, exist_ok=True)
    results = []
    with tempfile.TemporaryDirectory(prefix='soundbox-startup-benchmark-') as area:
        state = Path(area)
        prepare(state, args.metadata_from, args.tracks)
        # Record the first prepared-row cache miss separately, then measure
        # twenty genuinely warm launches without dropping a slow warm result.
        for index in range(args.runs + 1):
            label = f'launch-{index:02d}' if index else 'cold-cache'
            report = args.out / f'{label}.json'
            env = dict(os.environ, QT_QPA_PLATFORM='cocoa',
                       SOUNDBOX_BENCHMARK_LAUNCH_NS=str(time.monotonic_ns()))
            run = subprocess.run([str(args.app.resolve() / 'Contents/MacOS/Soundbox'),
                                  '--benchmark-startup', str(state), str(report.resolve())],
                                 env=env, capture_output=True, text=True, timeout=60)
            (args.out / f'{label}.log').write_text(run.stdout + run.stderr)
            run.check_returncode()
            result = json.loads(report.read_text())
            if not all(result[key] for key in ('painted', 'search_matched', 'rows')):
                raise RuntimeError('Browser readiness failed')
            if index:
                results.append(result)
            else:
                cold_cache = result['elapsed_ms']
    times = sorted(row['elapsed_ms'] for row in results)
    summary = {'scope': 'disposable-snapshot-browser', 'runs': len(times),
               'cold_row_cache_ms': cold_cache,
               'median_ms': (times[(len(times)-1)//2] + times[len(times)//2]) / 2,
               'p95_ms': times[math.ceil(len(times) * .95) - 1],
               'minimum_ms': times[0], 'maximum_ms': times[-1],
               'under_1500_ms': times[math.ceil(len(times) * .95) - 1] < 1500}
    (args.out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
