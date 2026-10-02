"""Resolve required offline release inputs; never discover mounted devices or AppData."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def ipod_capture() -> Path:
    path = Path(os.environ.get('SOUNDBOX_IPOD_CAPTURE',
        ROOT / 'soundbox-ipod-ref/offline-lifecycle/20260926-163945')).resolve()
    if path == Path('/Volumes') or Path('/Volumes') in path.parents:
        raise RuntimeError('Release fixtures must be offline, never a mounted device')
    for name in ('inventory.json', 'config.after.cfg', 'device/iPod_Control/iTunes/iTunesDB'):
        if not (path / name).is_file():
            raise RuntimeError(f'Required offline iPod fixture missing: {path / name}. '
                               'Set SOUNDBOX_IPOD_CAPTURE to the preserved capture; never a live device.')
    return path


def waveform_reference() -> tuple[Path, dict]:
    path = Path(os.environ.get('SOUNDBOX_WAVEFORM_REFERENCE',
        ROOT / 'soundbox-waveform-ref/20261001')).resolve()
    if path == Path('/Volumes') or Path('/Volumes') in path.parents:
        raise RuntimeError('Release fixtures must be offline, never a mounted device')
    manifest = json.loads((ROOT / 'tests/fixtures/waveform-reference.json').read_text())
    for name, row in manifest['files'].items():
        target = path / name
        if not target.resolve().is_relative_to(path):
            raise RuntimeError(f'Fixture redirects outside its snapshot: {target}')
        if not target.is_file():
            raise RuntimeError(f'Required frozen waveform fixture missing: {target}. '
                               'Set SOUNDBOX_WAVEFORM_REFERENCE to the preserved snapshot.')
        if target.stat().st_size != row['size'] or hashlib.sha256(target.read_bytes()).hexdigest() != row['sha256']:
            raise RuntimeError(f'Waveform reference fixture changed: {target}')
    return path, manifest


if __name__ == '__main__':
    print('Offline iPod fixture:', ipod_capture())
    print('Frozen waveform fixture:', waveform_reference()[0])
