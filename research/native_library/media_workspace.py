"""Durable multi-format bare import on original encrypted TEMP copies only.

Separate workspace/SQL markers retain the original known-WAV boundary. Source
publishers and production registration authority are deliberately absent.
"""
from dataclasses import asdict
import json
from pathlib import Path
import tempfile

from soundbox.rekordbox_write import ConflictError, RecoveryRequired, WriteProofError, _digest, _json, _quote, _regular
from soundbox.state_io import atomic_bytes
from . import media_registration as media
from . import registration
from .registration_workspace import OfflineRegistrationWorkspace

PREFIX='soundbox-media-workspace-'
FORMAT='soundbox-offline-media-registration-v1'


def _apply(conn,candidate):
    databases=conn.execute('PRAGMA database_list').fetchall()
    if len(databases)!=1 or databases[0][1]!='main':
        raise WriteProofError('No attached media-workspace databases')
    path=Path(databases[0][2])
    root=path.parent.parent if path.parent.name=='transaction' else path.parent
    if (path!=path.resolve() or path.name not in ('working.db','after.db')
            or root!=root.resolve() or not root.name.startswith(PREFIX)
            or not root.is_relative_to(Path(tempfile.gettempdir()).resolve())):
        raise WriteProofError('Media workspace SQL requires its original TEMP root')
    for p in (path,root/'workspace.json',root/'planned-intent.json'):_regular(p)
    marker=json.loads((root/'workspace.json').read_bytes());info=root.stat()
    if (marker.get('format')!=FORMAT or marker.get('root')!=str(root)
            or marker.get('root_file_id')!=[info.st_dev,info.st_ino]):
        raise WriteProofError('Media workspace SQL identity changed')
    admitted=json.loads((root/'planned-intent.json').read_bytes())
    if (admitted.get('workspace_id')!=marker.get('workspace_id')
            or _digest(admitted.get('candidate'))!=_digest(candidate)):
        raise ConflictError('Media SQL differs from its durable admitted intent')
    media.validate_plan(conn,candidate)
    row=candidate['row'];columns=list(row);conn.set_authorizer(registration._authorizer)
    try:
        conn.execute('INSERT INTO djmdContent ('+','.join(_quote(c) for c in columns)+') VALUES ('+
                     ','.join('?' for _ in columns)+')',list(row.values()))
        cursor=conn.execute("UPDATE agentRegistry SET int_1=? WHERE registry_id='localUpdateCount' AND int_1=?",
                            (candidate['counter_after']['int_1'],candidate['counter_before']['int_1']))
        if cursor.rowcount!=1:raise ConflictError('Media native counter changed')
    finally:conn.set_authorizer(None)


def _verify(before,after,candidate):
    media.validate_plan(before,candidate)
    return media.verify(before,after,[candidate])


class _Adapter:
    plan=staticmethod(media.plan)
    validate_plan=staticmethod(media.validate_plan)
    _apply=staticmethod(_apply)
    verify=staticmethod(_verify)


class OfflineMediaRegistrationWorkspace(OfflineRegistrationWorkspace):
    PREFIX=PREFIX
    FORMAT=FORMAT
    ADAPTER=_Adapter

    def inspect_wave(self,path):
        raise WriteProofError('Use the separate captured multi-format media inspector')

    def inspect_media(self,path,*,ffprobe=None):
        with self._lock():
            if self.transaction.exists() or self.retired.exists():
                raise RecoveryRequired('Recover media registration before another capture')
            if ffprobe is None:
                audio=media.inspect_packaged_media(path,self.root/'audio-capture')
            else:
                audio=media.inspect_media(path,self.root/'audio-capture',ffprobe=ffprobe)
            audio.revalidate()
            atomic_bytes(self.root/'audio-identity.json',_json(asdict(audio)))
            return audio
