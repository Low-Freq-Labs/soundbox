"""Publication guards cover every captured child, including late native drift."""
from copy import deepcopy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from test_playlist_transaction import fixture
from soundbox import rekordbox_playlist_store as playlists,rekordbox_cue_store as cues
from soundbox.rekordbox_tags import _schema,NATIVE_VERSION
from soundbox.rekordbox_write import _connect,_rows,_state,ConflictError


class BatchPublicationGuard(unittest.TestCase):
    def test_last_child_collection_identity_is_revalidated_before_any_batch_sql(self):
        with tempfile.TemporaryDirectory(prefix='soundbox-batch-review-') as area:
            root=Path(area).resolve();source=root/'source';source.mkdir();fixture(source)
            database=source/'master.db';conn=_connect(database,readonly=True)
            try:
                before=_state(conn);schema=_schema(conn)
            finally:conn.close()
            writer=playlists.PlaylistStore(playlists.PlaylistAuthority(
                str(database),'synthetic-playlists',schema,True),root/'recovery')
            catalog=writer.snapshot();planning=deepcopy(catalog);commands=[]
            for name,track,uuid in (('First','track','uuid'),('Last','track-two','uuid-two')):
                create=playlists.request_for(planning,'create',name=name);commands.append(create)
                planning['nodes'].append(dict(ID=create['playlist_id'],UUID=create['playlist_uuid']))
                commands.append(playlists.request_for(planning,'append',identity=create['playlist_id'],
                    tracks=[SimpleNamespace(track_id=track,source_uuid=uuid)]))
            request=playlists.batch_for(catalog,commands);original_xml=writer.xml.read_bytes()
            source_connect=writer.lease._connect
            def changed_after_preparation():
                # This is a synthetic TEMP source. Model an independent native
                # change after both verified backup payloads have been prepared.
                connection=source_connect()
                connection.execute("UPDATE djmdContent SET UUID='changed-last-content' WHERE ID='track-two'")
                return connection
            with mock.patch.object(cues,'_closed',return_value=None), \
                    mock.patch.object(cues,'_installed_version',return_value=NATIVE_VERSION), \
                    mock.patch.object(writer.lease,'_connect',side_effect=changed_after_preparation):
                with self.assertRaisesRegex(ConflictError,'Collection track changed before save'):
                    writer.apply(request)
            conn=_connect(database,readonly=True)
            try:
                after=_state(conn)
                rows=_rows(conn,'djmdContent')
            finally:conn.close()
            self.assertEqual([name for name in before['tables']
                              if before['tables'][name]!=after['tables'][name]],['djmdContent'])
            self.assertEqual(next(row['UUID'] for row in rows if row['ID']=='track-two'),'changed-last-content')
            self.assertEqual(writer.xml.read_bytes(),original_xml)
            self.assertFalse(writer.journal.exists())
            self.assertFalse(writer.authority.reorder_accepted)
