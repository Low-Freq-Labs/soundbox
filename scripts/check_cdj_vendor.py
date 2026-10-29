"""Check the pinned CDJ implementation and its minimal workspace adaptation."""
import hashlib
import json
from pathlib import Path


def verify(root=None):
    root = root or Path(__file__).resolve().parents[1]/'native/cdj/vendor/rbxport'
    manifest = json.loads((root/'SOURCE-MANIFEST.json').read_text())
    checked = 0
    for relative, expected in manifest['files_sha256_upstream'].items():
        if not relative.startswith('crates/'):
            continue
        path = root/relative
        if not path.is_file():
            raise RuntimeError(f'Missing pinned CDJ source: {relative}')
        raw = path.read_bytes()
        if relative.endswith('/Cargo.toml'):
            raw = raw.replace(b'[package]\nworkspace = "../.."',b'[package]',1)
        if hashlib.sha256(raw).hexdigest() != expected:
            raise RuntimeError(f'Pinned CDJ source changed: {relative}')
        checked += 1
    original = (root/'Cargo.toml.upstream').read_bytes()
    if hashlib.sha256(original).hexdigest() != manifest['files_sha256_upstream']['Cargo.toml']:
        raise RuntimeError('Pinned upstream workspace changed')
    if (root/'Cargo.toml').read_bytes() != original.replace(b'"src-tauri", ',b'',1):
        raise RuntimeError('Unexpected CDJ workspace changes')
    return checked


if __name__ == '__main__':
    print(f'Pinned CDJ source verified: {verify()} files')
