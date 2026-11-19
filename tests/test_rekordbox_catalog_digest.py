"""Read-only catalog hashing keeps every byte of the existing native revision."""
from copy import deepcopy
import json
import math
import random
from types import SimpleNamespace
import unittest
from unittest import mock

from sqlalchemy import create_engine

from soundbox import rekordbox_db as reader
from soundbox.rekordbox_playlist_plan import revision


class NativeCatalogDigest(unittest.TestCase):
    def test_generated_unicode_numeric_null_opaque_and_historical_rows_match_existing_protocol(self):
        randomizer=random.Random(617)
        nodes=[dict(ID=str(index%23),UUID='identity-'+str(index),Name='不明 🎶 \\ "\n'+str(index),
            ParentID='root' if index%4 else '17',Seq=index%7,Attribute=(0,1,4,-128)[index%4],
            SmartList=None if index%3 else '<CONDITION ValueLeft="-2147483648"/>',
            rb_local_deleted=index%2,rb_local_synced=index%2,usn=None,rb_local_usn=2**62+index,
            created_at='2026-11-17 01:02:03.123 +00:00',updated_at='opaque lexical clock',
            extra=dict(sequence=[None,True,False,-0.0,1e-100,2**64,'\b\f\r\t'],nested={'é':'🖤'}))
            for index in range(97)]
        members=[dict(ID=str(index%101),PlaylistID=str(index%23),ContentID=str(index%13),
            TrackNo=None if index%17==0 else index%9,UUID='member-'+str(index),
            rb_local_deleted=index%2,rb_data_status=index%3,rb_local_usn=index,
            historical={'clock':None,'title':'old / new'}) for index in range(1205)]
        for candidate_nodes,candidate_members in (([],[]),(nodes,[]),([],members),(nodes,members)):
            randomizer.shuffle(candidate_nodes);randomizer.shuffle(candidate_members)
            before=deepcopy((candidate_nodes,candidate_members))
            self.assertEqual(reader._playlist_catalog_revision(candidate_nodes,candidate_members),
                             revision(candidate_nodes,candidate_members))
            self.assertEqual((candidate_nodes,candidate_members),before)

    def test_raw_catalog_preserves_tombstones_duplicate_track_numbers_and_lexical_clock_cells(self):
        engine=create_engine('sqlite:///:memory:');self.addCleanup(engine.dispose)
        with engine.begin() as connection:
            connection.exec_driver_sql('CREATE TABLE djmdPlaylist(ID TEXT,Name TEXT,Seq INTEGER,rb_local_deleted INTEGER,updated_at TEXT)')
            connection.exec_driver_sql('CREATE TABLE djmdSongPlaylist(ID TEXT,PlaylistID TEXT,ContentID TEXT,TrackNo INTEGER,rb_local_deleted INTEGER,updated_at TEXT)')
            connection.exec_driver_sql('INSERT INTO djmdPlaylist VALUES (?,?,?,?,?)',
                [('z','不明',2,0,'2026-11-17 01:02:03.123456 +00:00'),('a','History',None,1,'unknown clock')])
            connection.exec_driver_sql('INSERT INTO djmdSongPlaylist VALUES (?,?,?,?,?,?)',
                [('m2','z','t',1,0,'same'),('m1','z','t',1,1,None)])
        with engine.connect() as connection:
            db=SimpleNamespace(session=SimpleNamespace(connection=lambda:connection))
            catalog=reader._playlist_catalog(db)
        self.assertEqual([row['ID'] for row in catalog['nodes']],['z','a'])
        self.assertEqual([row['ID'] for row in catalog['members']],['m2','m1'])
        self.assertEqual(catalog['nodes'][0]['updated_at'],'2026-11-17 01:02:03.123456 +00:00')
        self.assertEqual(catalog['revision'],revision(catalog['nodes'],catalog['members']))
        changed=deepcopy(catalog['members']);changed[1]['rb_local_deleted']=0
        self.assertNotEqual(catalog['revision'],reader._playlist_catalog_revision(catalog['nodes'],changed))

    def test_encoder_never_receives_the_full_membership_inventory(self):
        rows=[dict(ID=str(index),UUID='member-'+str(index),TrackNo=index,Name='opaque') for index in range(1500)]
        expected=revision([],rows);encoded=[];encode=json.dumps
        def bounded(value,*args,**kwargs):
            self.assertIsInstance(value,dict)
            encoded.append(value['ID']);return encode(value,*args,**kwargs)
        with mock.patch.object(reader.json,'dumps',side_effect=bounded):
            self.assertEqual(reader._playlist_catalog_revision([],rows),expected)
        self.assertEqual(len(encoded),len(rows));self.assertEqual(set(encoded),{row['ID'] for row in rows})

    def test_invalid_nonfinite_and_unsupported_values_are_not_given_a_native_revision(self):
        for value in (math.nan,math.inf,-math.inf,object()):
            with self.subTest(value=value):
                row={'ID':'1','opaque':value}
                with self.assertRaises((TypeError,ValueError)):revision([row],[])
                with self.assertRaises((TypeError,ValueError)):reader._playlist_catalog_revision([row],[])


if __name__=='__main__':unittest.main()
