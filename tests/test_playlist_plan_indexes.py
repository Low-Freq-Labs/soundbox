"""Batch catalog indexes retain exact native row chains/order on TEMP copies."""
from copy import deepcopy
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
from uuid import UUID

from test_playlist_transaction import fixture
from soundbox import rekordbox_playlist_store as store
from soundbox.rekordbox_write import _connect, _rows, ConflictError


class PlaylistPlanIndexes(unittest.TestCase):
    def setUp(self):
        area=tempfile.TemporaryDirectory()
        self.addCleanup(area.cleanup)
        self.root=Path(area.name).resolve()
        fixture(self.root)
        self.database=self.root/'master.db'
        self.xml=(self.root/'masterPlaylists6.xml').read_bytes()

    def seed(self, playlists=8, members=40):
        conn=_connect(self.database)
        try:
            node=_rows(conn,'djmdPlaylist')[0]
            columns=list(node)
            for index in range(1,playlists):
                value={**node,'ID':str(1000+index),'Seq':index+1,'Name':f'Playlist {index}',
                       'UUID':str(UUID(int=index+100,version=4))}
                conn.execute('INSERT INTO djmdPlaylist ('+','.join(columns)+') VALUES ('+
                             ','.join('?' for _ in columns)+')',list(value.values()))
            rows=[]
            for playlist in range(playlists):
                for at in range(members):
                    number=playlist*members+at+10000
                    rows.append((str(UUID(int=number,version=4)),str(1000+playlist),'track',at+1,
                        str(UUID(int=number+100000,version=4)),0,0,0,playlist%2,None,20,'created','created'))
            conn.executemany('INSERT INTO djmdSongPlaylist VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',rows)
            conn.commit()
            catalog=store.catalog(conn)
        finally:
            conn.close()
        extra=b''.join(f'<NODE Id="{1000+i:X}" ParentId="0" Attribute="0" Timestamp="123000" '
                       'Lib_Type="0" CheckType="0"/>\n'.encode() for i in range(1,playlists))
        self.xml=self.xml.replace(b'</PLAYLISTS>',extra+b'</PLAYLISTS>')
        return catalog

    @staticmethod
    def track():
        return SimpleNamespace(track_id='track',source_uuid='uuid')

    def test_large_batch_changes_xml_and_catalog_equal_exact_sequential_plans(self):
        catalog=self.seed()
        commands=[store.request_for(catalog,'remove',identity=str(1000+i),tracks=[self.track()])
                  for i in range(8)]
        request=store.batch_for(catalog,commands)
        before=self.database.read_bytes()
        conn=_connect(self.database,readonly=True)
        try:
            actual=store.build_plan(conn,request,self.xml)
        finally:
            conn.close()
        self.assertEqual(self.database.read_bytes(),before)

        second=self.root/'sequential.db'
        shutil.copyfile(self.database,second)
        conn=_connect(second)
        try:
            payload=self.xml
            changes=[]
            for child in request['requests']:
                fresh=store.catalog(conn)
                plan=store.build_plan(conn,{**child,'revision':fresh['revision']},payload)
                changes.extend(plan['changes'])
                store.apply_changes(conn,plan['changes'])
                payload=plan['xml']
            self.assertEqual(actual['catalog'],store.catalog(conn))
            self.assertEqual(actual['changes'],changes)
            self.assertEqual(actual['xml'],payload)
        finally:
            conn.close()

    def test_delete_then_readd_keeps_original_list_append_order(self):
        catalog=self.seed(playlists=2,members=3)
        removed=catalog['members'][0]
        remove=store.request_for(catalog,'remove',identity='1000',tracks=[self.track()])
        append=store.request_for(catalog,'append',identity='1000',tracks=[self.track()],allow_duplicate=True)
        append['tracks'][0].update(id=removed['ID'],membership_uuid=removed['UUID'])
        request=store.batch_for(catalog,[remove,append])
        conn=_connect(self.database,readonly=True)
        try:
            result=store.build_plan(conn,request,self.xml)
        finally:
            conn.close()
        expected=[row['ID'] for row in catalog['members'] if row['ID']!=removed['ID']]
        self.assertEqual([row['ID'] for row in result['catalog']['members']],expected+[removed['ID']])
        self.assertEqual(result['catalog']['members'][-1]['TrackNo'],3)

    def test_duplicate_unselected_ids_preserve_first_row_fallback_without_collapse(self):
        catalog=self.seed(playlists=1,members=3)
        retained=deepcopy(catalog['members'][0])
        retained.update(ID=str(UUID(int=900000,version=4)),PlaylistID='9999')
        duplicate={**retained,'TrackNo':2,'UUID':str(UUID(int=900001,version=4))}
        catalog['members'].extend([retained,duplicate])
        catalog['revision']=store.planner.revision(catalog['nodes'],catalog['members'])
        request=store.batch_for(catalog,[store.request_for(catalog,'remove',identity='1000',tracks=[self.track()])])
        conn=_connect(self.database,readonly=True)
        try:
            with mock.patch.object(store,'catalog',return_value=deepcopy(catalog)):
                result=store.build_plan(conn,request,self.xml)
        finally:
            conn.close()
        self.assertEqual(result['catalog']['members'][-2:],[retained,duplicate])

    def test_index_still_checks_every_exact_before_row(self):
        catalog=self.seed(playlists=1,members=3)
        request=store.batch_for(catalog,[store.request_for(catalog,'remove',identity='1000',tracks=[self.track()])])
        row=catalog['members'][0]
        forged=dict(changes=[dict(table='djmdSongPlaylist',id=row['ID'],
                                 before={**row,'TrackNo':999},after=None)],xml=self.xml)
        conn=_connect(self.database,readonly=True)
        try:
            with mock.patch.object(store,'_single_plan',return_value=forged):
                with self.assertRaisesRegex(ConflictError,'row chain changed'):
                    store.build_plan(conn,request,self.xml)
        finally:
            conn.close()


if __name__=='__main__':
    unittest.main()
