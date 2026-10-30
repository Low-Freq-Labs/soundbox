"""Replay native playlist plans on disposable encrypted DB copies only.

Inputs are explicit frozen captures. This verifies complete database semantics
and SQL rollback, not source authority or atomic DB/XML publication/recovery.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import tempfile

from soundbox.rekordbox_write import _connect, _quote, _rows, _state
from . import playlist_lifecycle as lifecycle, playlist_membership as membership
from .playlist_reference import compare_rows, load_capture


def _one(rows, key, value):
    matches = [r for r in rows if r.get(key) == value]
    if len(matches) != 1:
        raise ValueError('Expected exactly one reference row')
    return matches[0]


def _apply(conn, changes):
    for change in changes:
        table = change['table']
        if table not in ('djmdPlaylist', 'djmdSongPlaylist', 'agentRegistry'):
            raise ValueError('Unproven replay table')
        if table == 'agentRegistry' and change['id'] != 'localUpdateCount':
            raise ValueError('Replay cannot write unrelated registry metadata')
        key = 'registry_id' if table == 'agentRegistry' else 'ID'
        cursor = conn.execute(f'SELECT * FROM {_quote(table)} WHERE {_quote(key)}=?', (change['id'],))
        names = [c[0] for c in cursor.description]
        actual = [dict(zip(names,row,strict=True)) for row in cursor]
        before, after = change['before'], change['after']
        if actual != ([] if before is None else [before]):
            raise ValueError('Replay baseline conflict')
        if after is None:
            conn.execute(f'DELETE FROM {_quote(table)} WHERE {_quote(key)}=?', (change['id'],))
        else:
            columns = names
            if set(columns) != set(after) or after[key] != change['id']:
                raise ValueError('Replay row/schema mismatch')
            values = [after[c] for c in columns]
            if before is None:
                conn.execute(f'INSERT INTO {_quote(table)} '
                    f'({",".join(_quote(c) for c in columns)}) VALUES ({",".join("?" for c in columns)})', values)
            else:
                columns = [c for c in columns if after[c] != before[c]]
                if not columns:
                    raise ValueError('Unchanged replay row')
                values = [after[c] for c in columns]
                conn.execute(f'UPDATE {_quote(table)} SET '
                    f'{",".join(_quote(c)+"=?" for c in columns)} WHERE {_quote(key)}=?', values+[change['id']])


def _reference_differences(conn, expected, reference_rows, *, audit_notification_timestamp=False):
    """Strict by default; explicitly audit one reference-only notification clock.

    The candidate must preserve its baseline notification metadata. This is an
    audited residual difference, never a complete native match or permission to
    normalize/mutate either frozen input.
    """
    actual = _state(conn)
    if actual == expected:
        return []
    differing = [t for t in actual['tables'].keys() | expected['tables'].keys()
                 if actual['tables'].get(t) != expected['tables'].get(t)]
    if (not audit_notification_timestamp or actual['schema'] != expected['schema']
            or actual['metadata'] != expected['metadata'] or differing != ['agentRegistry']):
        raise ValueError('Planned replay differs from the complete native reference')
    differences = compare_rows({'agentRegistry':_rows(conn, 'agentRegistry')},
                               {'agentRegistry':reference_rows['agentRegistry']})['agentRegistry']
    if (len(differences) != 1 or differences[0]['id'] != 'notificationNextFetch'
            or differences[0]['fields'] != ['updated_at']):
        raise ValueError('Unproven reference registry difference')
    return differences


def replay(before, after, operation, playlist_id, *, audit_notification_timestamp=False, paired=False):
    first, left = load_capture(before)
    expected, right = load_capture(after)
    if first['schema'] != expected['schema'] or first['metadata'] != expected['metadata']:
        raise ValueError('Reference schema or library identity changed')
    counter = _one(left['agentRegistry'], 'registry_id', 'localUpdateCount')
    plans = []
    with tempfile.TemporaryDirectory(prefix='soundbox-playlist-replay-') as folder:
        database = Path(folder).resolve()/'master.db'
        shutil.copyfile(Path(before)/'master.db', database)
        conn = _connect(database)
        try:
            if _state(conn) != first:
                raise ValueError('Captured input changed before replay')
            if operation == 'create':
                target = _one(right['djmdPlaylist'], 'ID', playlist_id)
                plans.append(lifecycle.create(left['djmdPlaylist'], counter, identity=target['ID'],
                    uuid=target['UUID'], name=target['Name'], timestamp=target['created_at']))
            else:
                playlist = _one(left['djmdPlaylist'], 'ID', playlist_id)
                members = [r for r in left['djmdSongPlaylist'] if r['PlaylistID'] == playlist_id]
                final = [r for r in right['djmdSongPlaylist'] if r['PlaylistID'] == playlist_id]
                if operation == 'rename':
                    target = _one(right['djmdPlaylist'], 'ID', playlist_id)
                    plans.append(lifecycle.rename(playlist, counter,
                        name=target['Name'], timestamp=target['updated_at']))
                elif operation == 'append':
                    old_ids = {r['ID'] for r in members}
                    new = sorted((r for r in final if r['ID'] not in old_ids), key=lambda r:r['TrackNo'])
                    if not new:
                        raise ValueError('No new memberships to replay')
                    contents = _rows(conn, 'djmdContent')
                    for row in new:
                        content = _one(contents, 'ID', row['ContentID'])
                        plan = membership.append(playlist, members, counter, content,
                            identity=row['ID'], uuid=row['UUID'], timestamp=row['created_at'])
                        plans.append(plan);members, counter = plan['members'], plan['counter']
                elif operation == 'remove':
                    final_ids = {r['ID'] for r in final}
                    removed = [r for r in members if r['ID'] not in final_ids]
                    if len(removed) != 1:
                        raise ValueError('Removal replay requires exactly one removed membership')
                    modified = [r for r in final if r != _one(members, 'ID', r['ID'])]
                    timestamps = {r['updated_at'] for r in modified}
                    if len(timestamps) > 1:
                        raise ValueError('Unproven multi-timestamp survivor update')
                    plans.append(membership.remove(playlist, members, counter, removed[0]['ID'],
                        timestamp=next(iter(timestamps), 'unused')))
                elif operation == 'delete-last':
                    plans.append(lifecycle.delete_last(left['djmdPlaylist'], members, counter,
                        identity=playlist_id))
                else:
                    raise ValueError('Unsupported replay operation')
            changes = [change for plan in plans for change in plan['changes']]
            conn.execute('BEGIN IMMEDIATE')
            try:
                _apply(conn, changes)
                differences = _reference_differences(conn, expected, right,
                    audit_notification_timestamp=audit_notification_timestamp)
            finally:
                conn.rollback()
            if _state(conn) != first:
                raise ValueError('Rollback did not restore the complete initial reference')
            conn.execute('BEGIN IMMEDIATE')
            try:
                _apply(conn, changes)
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
        finally:
            conn.close()
        conn = _connect(database, readonly=True)
        try:
            if _reference_differences(conn, expected, right,
                    audit_notification_timestamp=audit_notification_timestamp) != differences:
                raise ValueError('Reopened committed copy differs from native reference')
        finally:
            conn.close()
    publication = None
    if paired:
        from .playlist_transaction import OfflinePlaylistWorkspace, _sha
        workspace = OfflinePlaylistWorkspace.capture(before)
        try:
            xml_path = Path(after).expanduser().resolve()/'masterPlaylists6.xml'
            _sha(xml_path)  # reject redirects/hardlinks before reading
            xml_after = xml_path.read_bytes()
            workspace.apply(changes, xml_after, expected_before=first,
                expected_xml_sha256=_sha(workspace.xml))
            conn = _connect(workspace.database, readonly=True)
            try:
                if _reference_differences(conn, expected, right,
                        audit_notification_timestamp=audit_notification_timestamp) != differences:
                    raise ValueError('Paired publication differs from the native DB reference')
            finally:conn.close()
            if workspace.xml.read_bytes() != xml_after:
                raise ValueError('Paired publication differs from native XML bytes')
            publication = dict(verified_commit=True, xml_byte_match=True,
                pending_recovery=workspace.transaction.exists(), source_write_authority=False)
        except BaseException as exc:
            exc.add_note('Preserved offline playlist workspace: '+str(workspace.root))
            raise
        else:
            shutil.rmtree(workspace.root)
    return dict(operation=operation, playlist_id=playlist_id, planned_changes=len(changes),
        complete_semantic_match=not differences, reference_only_differences=differences,
        audited_semantic_match=True, rollback_match=True, reopened_commit_match=True,
        paired_publication=publication,
        xml_publication_proven=False, source_write_authority=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before', type=Path);parser.add_argument('after', type=Path)
    parser.add_argument('operation', choices=('create', 'rename', 'append', 'remove', 'delete-last'))
    parser.add_argument('playlist_id')
    parser.add_argument('--audit-notification-timestamp', action='store_true')
    parser.add_argument('--paired', action='store_true')
    args = parser.parse_args()
    print(json.dumps(replay(args.before, args.after, args.operation, args.playlist_id,
        audit_notification_timestamp=args.audit_notification_timestamp, paired=args.paired), indent=2))


if __name__ == '__main__':
    main()
