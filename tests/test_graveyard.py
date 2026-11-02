"""Graveyard metadata reads never inspect audio payloads or guess old identities."""
from copy import deepcopy
from pathlib import Path
import os
import tempfile
import unittest
from unittest import mock

from test_removal_store import fixture,UUID
from soundbox import graveyard
from soundbox.rekordbox_write import _connect


class GraveyardCatalog(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name).resolve()
        self.source,self.audio,_,_=fixture(self.root);self.db=self.source/'master.db'
        conn=_connect(self.db)
        try:
            for table,field in (('djmdArtist','Name'),('djmdAlbum','Name'),('djmdGenre','Name'),('djmdKey','ScaleName')):
                conn.execute('CREATE TABLE '+table+'(ID TEXT PRIMARY KEY,'+field+' TEXT)')
            conn.execute("INSERT INTO djmdArtist VALUES ('7','Artist')")
            conn.execute("UPDATE djmdContent SET ArtistID='7',BPM=14000,Length=61,Title='Known title' WHERE ID='1'")
        finally:conn.close()

    def catalog(self,ids=('1',),**kw):return graveyard.catalog(self.db,'123',ids,**kw)

    def test_current_and_tombstoned_native_rows_supply_exact_info_without_opening_audio(self):
        original=Path.open
        def guard(path,*args,**kw):
            if path==self.audio:raise AssertionError('No audio reads for storage or info')
            return original(path,*args,**kw)
        with mock.patch.object(Path,'open',guard):rows=self.catalog()
        row=rows['1'];self.assertEqual(row['display']['artist'],'Artist');self.assertEqual(row['display']['time'],'1:01')
        self.assertEqual(row['display']['bpm'],'140.00');self.assertEqual(row['size'],self.audio.stat().st_size)
        self.assertEqual(row['proof']['kind'],'native-read-snapshot');self.assertEqual(row['references'][0]['id'],'1')
        conn=_connect(self.db)
        try:conn.execute("UPDATE djmdContent SET rb_local_deleted=1 WHERE ID='1'")
        finally:conn.close()
        row=self.catalog()['1'];self.assertFalse(row['native']['active']);self.assertEqual(row['references'],[])

    def test_historical_reference_recovers_numeric_row_but_wrong_library_or_uuid_cannot_replace_identity(self):
        entry=self.catalog()['1'];history=dict(id='1',uuid=UUID,path=str(self.audio),source=str(self.db),library_id='123',
            display=entry['display'],proof=entry['proof'])
        wrong=deepcopy(history);wrong['uuid']='changed'
        self.assertIn('identity changed',self.catalog(remembered={'1':wrong})['1']['error'])
        conn=_connect(self.db)
        try:conn.execute("DELETE FROM djmdContent WHERE ID='1'")
        finally:conn.close()
        row=self.catalog(remembered={'1':history})['1'];self.assertEqual(row['display']['title'],'Known title')
        self.assertEqual(row['proof'],entry['proof']);self.assertTrue(row['exists']);self.assertFalse(row['native']['active'])
        wrong=deepcopy(history);wrong['library_id']='456'
        row=self.catalog(remembered={'1':wrong})['1'];self.assertFalse(row['path']);self.assertNotIn('display',row)

    def test_totals_deduplicate_aliases_and_hardlinks_and_distinguish_missing_and_unknown(self):
        alias=self.root/'alias';alias.symlink_to(self.root,target_is_directory=True)
        hard=self.root/'hard.wav';os.link(self.audio,hard)
        imported={'alias':dict(path=str(alias/self.audio.name)),'hard':dict(path=str(hard)),
            'missing':dict(path=str(self.root/'missing.wav'))}
        rows=self.catalog(('1','alias','hard','missing','unknown'),imported=imported)
        total=graveyard.totals(rows);self.assertEqual(total['files'],1);self.assertEqual(total['size'],self.audio.stat().st_size)
        self.assertEqual((total['unavailable'],total['unresolved']),(1,1));self.assertEqual(rows['hard']['links'],2)
        self.assertEqual(rows['alias']['references'][0]['id'],'1');self.assertEqual(graveyard.totals(rows,['missing'])['files'],0)

    def test_binding_maps_logical_track_and_source_mismatch_cannot_rebind_it(self):
        binding=dict(source=str(self.db),library_id='123',local_id='sb-import',native_id='1',native_uuid=UUID)
        row=self.catalog(('sb-import',),bindings=[binding])['sb-import'];self.assertEqual(row['native_id'],'1')
        self.assertTrue(row['exists'])
        binding['native_uuid']='00000000-0000-4000-8000-000000000099'
        self.assertFalse(self.catalog(('sb-import',),bindings=[binding])['sb-import']['path'])
        binding['native_uuid']=UUID
        self.assertFalse(self.catalog(('sb-import',),bindings=[binding,binding])['sb-import']['path'])
        binding['source']='different'
        self.assertFalse(self.catalog(('sb-import',),bindings=[binding])['sb-import']['path'])

    def test_cancellation_and_foreign_library_stop_read_only_scan(self):
        with self.assertRaisesRegex(RuntimeError,'cancelled'):self.catalog(should_cancel=lambda:True)
        with self.assertRaisesRegex(ValueError,'library changed'):graveyard.catalog(self.db,'456',('1',))

    def test_unknown_native_id_has_absence_proof_but_no_guessed_path(self):
        entry=self.catalog(('999',))['999']
        self.assertTrue(entry['native_absent']);self.assertEqual(entry['path'],'')
        self.assertEqual(entry['proof']['kind'],'native-absent-snapshot')
        self.assertEqual(len(entry['proof']['database_sha256']),64)

    def test_imported_only_library_retains_information_without_native_source(self):
        row=graveyard.catalog(None,None,['local'],imported={'local':dict(path=str(self.audio),time=60)})['local']
        self.assertEqual(row['display']['time'],'1:00');self.assertTrue(row['exists']);self.assertFalse(row['native']['active'])
