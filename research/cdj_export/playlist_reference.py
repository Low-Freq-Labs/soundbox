"""Read-only native playlist protocol comparison over explicit frozen captures.

No live-source defaults, discovery or publisher. SQL opens disposable copies only.
Capture directories must contain encrypted master.db and its semantic inventory.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import tempfile

from soundbox.rekordbox_write import _connect, _rows, _state


def compare_rows(before, after):
    changes = {}
    for table in sorted(before.keys() | after.keys()):
        key = 'registry_id' if table == 'agentRegistry' else 'ID'
        left = {str(r[key]):r for r in before.get(table,[])}
        right = {str(r[key]):r for r in after.get(table,[])}
        if len(left) != len(before.get(table,[])) or len(right) != len(after.get(table,[])):
            raise ValueError('Ambiguous row identity')
        rows = []
        for identity in sorted(left.keys() | right.keys()):
            old, new = left.get(identity), right.get(identity)
            if old == new: continue
            rows.append(dict(id=identity, before=old, after=new,
                fields=sorted((old or {}).keys() | (new or {}).keys()) if old is None or new is None else
                    sorted(k for k in old.keys() | new.keys() if old.get(k) != new.get(k))))
        if rows: changes[table] = rows
    return changes


def load_capture(directory):
    root = Path(directory).expanduser().resolve(strict=True)
    if root.is_relative_to('/Volumes') or not root.is_dir():
        raise ValueError('An explicit local frozen capture directory is required')
    for name in ('master.db','state.json'):
        path = root/name
        if path.is_symlink() or not path.is_file():
            raise ValueError('Capture is missing a regular '+name)
    expected = json.loads((root/'state.json').read_text())
    with tempfile.TemporaryDirectory(prefix='soundbox-playlist-audit-') as folder:
        copy = Path(folder).resolve()/'master.db'
        shutil.copyfile(root/'master.db',copy)
        conn = _connect(copy,readonly=True)
        try:
            state = _state(conn)
            if state != expected:
                raise ValueError('Frozen capture does not match its recorded semantic inventory')
            rows = {name:_rows(conn,name) for name in ('djmdPlaylist','djmdSongPlaylist','agentRegistry')}
        finally:
            conn.close()
    return state, rows


def compare_captures(before, after):
    first, left = load_capture(before)
    second, right = load_capture(after)
    changed_tables = [name for name in first['tables'].keys() | second['tables'].keys()
                      if first['tables'].get(name) != second['tables'].get(name)]
    deltas = compare_rows(left,right)
    xml = [Path(p)/'masterPlaylists6.xml' for p in (before,after)]
    return dict(schema_unchanged=first['schema']==second['schema'],
                metadata_unchanged=first['metadata']==second['metadata'],
                changed_tables=sorted(changed_tables), deltas=deltas,
                xml_changed=xml[0].read_bytes()!=xml[1].read_bytes() if all(p.is_file() for p in xml) else None,
                source_write_authority=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before',type=Path); parser.add_argument('after',type=Path)
    args = parser.parse_args()
    print(json.dumps(compare_captures(args.before,args.after),indent=2,default=str))


if __name__ == '__main__':
    main()
