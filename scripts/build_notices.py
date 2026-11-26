"""Collect installed runtime distribution notices without copying user data."""
from importlib import metadata
from pathlib import Path, PurePosixPath
import hashlib
import json
import os
import re
import stat
import sysconfig

ROOT = Path(__file__).resolve().parents[1]
LICENSE_NAME = re.compile(r'^(?:licen[cs]e|copying|copyright|notice)(?:[._-].*)?$', re.I)
MAX_NOTICE = 4 * 1024 * 1024
MAX_INVENTORY = 2 * 1024 * 1024
MAX_FILES = 4096
MAX_TOTAL = 64 * 1024 * 1024
FORMAT = 'soundbox-runtime-notices-v1'


def _relative(value):
    if type(value) is not str or not value or len(value) > 4096 or '\\' in value or '\0' in value:
        raise ValueError('Invalid notice path')
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ('.', '..') for part in path.parts) or str(path) != value:
        raise ValueError('Escaping notice path')
    return path


def _epoch(value):
    return (value.st_dev, value.st_ino, value.st_mode, value.st_nlink,
            value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate runtime notice inventory key')
        value[key] = item
    return value


def _read_regular(root, relative, *, limit=MAX_NOTICE):
    """Read one bounded regular file through held, no-follow ancestors."""
    supplied = Path(root)
    canonical = supplied.resolve(strict=True)
    parts = (*canonical.parts[1:], *_relative(str(relative)).parts)
    descriptors = [os.open(canonical.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)]
    parents = []
    try:
        for part in parts[:-1]:
            parent = descriptors[-1]
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            descriptors.append(child)
            parents.append((parent, part, child))
        parent = descriptors[-1]
        before = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 0 <= before.st_size <= limit:
            raise ValueError('Notice is not a bounded single-link regular file')
        leaf = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        descriptors.append(leaf)
        if _epoch(os.fstat(leaf)) != _epoch(before):
            raise ValueError('Notice changed before reading')
        chunks = []; size = 0
        while True:
            chunk = os.read(leaf, min(65536, limit + 1 - size))
            if not chunk:
                break
            chunks.append(chunk); size += len(chunk)
            if size > limit:
                raise ValueError('Notice exceeds its byte limit')
        if (size != before.st_size or _epoch(os.fstat(leaf)) != _epoch(before)
                or _epoch(os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)) != _epoch(before)
                or supplied.resolve(strict=True) != canonical):
            raise ValueError('Notice changed while reading')
        for parent, part, child in parents:
            named = os.stat(part, dir_fd=parent, follow_symlinks=False)
            opened = os.fstat(child)
            if (named.st_dev, named.st_ino, named.st_mode) != (opened.st_dev, opened.st_ino, opened.st_mode):
                raise ValueError('Notice ancestor changed while reading')
        return b''.join(chunks), _epoch(before)
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _write_new(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(raw)


def collect(module_names, output):
    """Analysis supplies actual runtime imports; no provider or user module runs."""
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    top_level = {name.split('.')[0] for name in module_names}
    mapping = metadata.packages_distributions()
    names = {name for module in top_level for name in mapping.get(module, ())}
    names.add('pyinstaller')  # Its bootloader is part of the app executable.
    inventory = []
    total = 0
    for name in sorted(names, key=str.casefold):
        dist = metadata.distribution(name)
        if type(dist.version) is not str or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+-]{0,127}', dist.version) is None:
            raise ValueError('Invalid distribution version filename')
        label = re.sub(r'[^A-Za-z0-9._-]', '_', dist.metadata['Name'])
        target = output / f'{label}-{dist.version}'
        target.mkdir()
        files = []
        for file in dist.files or ():
            relative = PurePosixPath(str(file))
            if not LICENSE_NAME.fullmatch(relative.name):
                continue
            relative = _relative(str(file))
            raw, _ = _read_regular(dist.locate_file(''), relative)
            total += len(raw)
            if total > MAX_TOTAL or sum(len(row['notice_files']) for row in inventory) + len(files) >= MAX_FILES:
                raise ValueError('Runtime notices exceed their inventory limit')
            destination = target / str(relative)
            _write_new(destination, raw)
            files.append(str(relative))
        expression = dist.metadata.get('License-Expression') or dist.metadata.get('License') or 'See upstream notices'
        # Metadata can contain the complete license where wheel files do not.
        notice = f'{dist.metadata["Name"]} {dist.version}\n\n{expression}\n'
        for url in dist.metadata.get_all('Project-URL') or ():
            notice += url + '\n'
        encoded = notice.encode()
        if len(encoded) > MAX_NOTICE:
            raise ValueError('Distribution metadata exceeds its notice limit')
        _write_new(target / 'DISTRIBUTION.txt', encoded)
        inventory.append({'name': dist.metadata['Name'], 'version': dist.version,
                          'notice_files': files, 'license_metadata': expression})
    raw, _ = _read_regular(sysconfig.get_path('stdlib'), 'LICENSE.txt')
    _write_new(output / 'Python-LICENSE.txt', raw)
    for source in (ROOT / 'assets/licenses').glob('*.txt'):
        raw, _ = _read_regular(ROOT / 'assets/licenses', source.name)
        _write_new(output / source.name, raw)
    records = {str(path.relative_to(output)): hashlib.sha256(path.read_bytes()).hexdigest()
               for path in output.rglob('*') if path.is_file()}
    _write_new(output / 'inventory.json', (json.dumps({'format': FORMAT, 'distributions': inventory,
        'sha256': records}, ensure_ascii=False, indent=2) + '\n').encode())
    return output


def verify(app):
    """Prove the collected notice set; this is not transitive legal clearance."""
    app = Path(app).resolve(strict=True)
    supplied = app / 'Contents/Resources/licenses/runtime'
    root = supplied.resolve(strict=True)
    if not root.is_relative_to(app):
        raise ValueError('Runtime notices escape the app')
    raw, inventory_epoch = _read_regular(root, 'inventory.json', limit=MAX_INVENTORY)
    value = json.loads(raw, object_pairs_hook=_unique_object)
    if (not isinstance(value, dict) or set(value) != {'format', 'distributions', 'sha256'}
            or value['format'] != FORMAT or not isinstance(value['distributions'], list)
            or not isinstance(value['sha256'], dict) or not 0 < len(value['sha256']) <= MAX_FILES):
        raise ValueError('Invalid runtime notice inventory')
    records = value['sha256']
    if 'inventory.json' in records:
        raise ValueError('Runtime notice inventory cannot list itself')
    if not {'Python-LICENSE.txt', 'GPL-3.0-only.txt', 'LGPL-3.0-only.txt'} <= records.keys():
        raise ValueError('Required runtime notice text is missing')
    witnesses = []; total = 0
    for relative, expected in records.items():
        path = _relative(relative)
        if type(expected) is not str or re.fullmatch(r'[0-9a-f]{64}', expected) is None:
            raise ValueError('Invalid runtime notice digest')
        target = (root / str(path)).resolve(strict=True)
        if not target.is_relative_to(app):
            raise ValueError('Runtime notice file escapes the app')
        data, epoch = _read_regular(app, target.relative_to(app))
        total += len(data)
        if total > MAX_TOTAL or hashlib.sha256(data).hexdigest() != expected:
            raise ValueError('Runtime notice digest does not match')
        witnesses.append((root / str(path), target, epoch))
    actual = set()
    for path in root.rglob('*'):
        if len(actual) > MAX_FILES:
            raise ValueError('Runtime notice inventory is too large')
        if path.is_symlink() and path.is_dir():
            raise ValueError('Linked nested notice directories are unsupported')
        if path.is_file() or path.is_symlink():
            actual.add(str(path.relative_to(root)))
    if actual != {*records, 'inventory.json'}:
        raise ValueError('Runtime notice files differ from inventory')
    if (supplied.resolve(strict=True) != root or _epoch((root / 'inventory.json').stat()) != inventory_epoch
            or any(lexical.resolve(strict=True) != target or _epoch(target.stat()) != epoch
                   for lexical, target, epoch in witnesses)):
        raise ValueError('Runtime notices changed during inspection')
    return {'files': len(records), 'bytes': total, 'inventory_sha256': hashlib.sha256(raw).hexdigest()}
