"""Native readback narrows snapshot rows without relaxing exact identities."""
from contextlib import nullcontext
import sqlite3
import unittest
from unittest import mock

from desktop import native_analysis as native
from soundbox.rekordbox_write import WriteProofError


def database(count=1000):
    conn=sqlite3.connect(':memory:')
    conn.executescript('''
        CREATE TABLE djmdContent(ID TEXT, UUID TEXT, KeyID TEXT, AnalysisDataPath TEXT);
        INSERT INTO djmdContent VALUES('abc','exact','key','');
        CREATE TABLE djmdKey(ID TEXT,ScaleName TEXT);
        INSERT INTO djmdKey VALUES('key','8A');
        CREATE TABLE djmdCue(ID TEXT,ContentID TEXT COLLATE NOCASE,Payload TEXT);
        CREATE INDEX cue_content ON djmdCue(ContentID);
        CREATE TABLE contentCue(ID TEXT,ContentID TEXT COLLATE NOCASE,Payload TEXT);
        CREATE INDEX aggregate_content ON contentCue(ContentID);
    ''')
    for table in ('djmdCue','contentCue'):
        conn.executemany(f'INSERT INTO {table} VALUES(?,?,?)',
                         ((str(i),str(i),'unrelated') for i in range(count)))
        conn.executemany(f'INSERT INTO {table} VALUES(?,?,?)',
                         [('exact','abc','selected'),('different-case','ABC','preserved separately')])
    return conn


class NativeReadback(unittest.TestCase):
    def read(self,conn,*,real_fields=False):
        fields=(nullcontext() if real_fields else mock.patch.object(native,'cue_snapshot_fields',
                side_effect=lambda snap:dict(cues=snap.cues,aggregate=snap.aggregate)))
        with mock.patch.object(native,'encrypted_backup',return_value=nullcontext('/synthetic/snapshot.db')), \
             mock.patch.object(native,'_connect',return_value=conn), \
             fields:
            return native.readback('/synthetic/master.db','abc','exact')

    def test_selected_snapshot_queries_keep_exact_lexical_rows_and_bound_materialization(self):
        conn=database();queries=[];conn.set_trace_callback(queries.append)
        result=self.read(conn)
        self.assertEqual(result['cue_fields'],dict(cues=({'ID':'exact','ContentID':'abc','Payload':'selected'},),
                        aggregate={'ID':'exact','ContentID':'abc','Payload':'selected'}))
        self.assertEqual(result['key'],'8A');self.assertEqual(result['beats'],[])
        for table in ('djmdCue','contentCue'):
            self.assertTrue(any(f'FROM "{table}" NOT INDEXED WHERE ContentID=' in sql for sql in queries))

    def test_selected_aggregate_ambiguity_still_fails_closed(self):
        conn=database();conn.execute("INSERT INTO contentCue VALUES('duplicate','abc','second selected')")
        with self.assertRaisesRegex(WriteProofError,'ambiguous'):self.read(conn)

    def test_changed_track_uuid_fails_before_any_cue_query(self):
        conn=database();conn.execute("UPDATE djmdContent SET UUID='changed'")
        queries=[];conn.set_trace_callback(queries.append)
        with self.assertRaisesRegex(WriteProofError,'identity changed'):self.read(conn)
        self.assertFalse(any('FROM "djmdCue"' in sql for sql in queries))

    def test_competing_indexes_and_reverse_scan_keep_full_hot_refs_and_memory_tie_colors(self):
        from soundbox.rekordbox_cues import CueSnapshot
        for reverse in (False,True):
            with self.subTest(reverse=reverse):
                conn=database(0);conn.execute('DELETE FROM djmdCue')
                fields=dict(Kind='INTEGER',InMsec='INTEGER',OutMsec='INTEGER',Comment='TEXT',
                            UUID='TEXT',rb_local_deleted='INTEGER',ColorRed='INTEGER',ColorGreen='INTEGER',ColorBlue='INTEGER')
                for name,kind in fields.items():conn.execute(f'ALTER TABLE djmdCue ADD COLUMN {name} {kind}')
                entries=(('hot-a',1,(1,2,3)),('hot-z',2,(4,5,6)),
                         ('memory-a',0,(255,0,0)),('memory-z',0,(0,0,255)))
                for identity,kind,color in (reversed(entries) if reverse else entries):
                    values=(identity,'abc','opaque',kind,1000,-1,'tie','uuid-'+identity,0,*color)
                    conn.execute('INSERT INTO djmdCue VALUES('+','.join('?' for _ in values)+')',values)
                conn.execute('CREATE INDEX competing_cue ON djmdCue(ContentID,ID DESC)')
                if reverse:conn.execute('PRAGMA reverse_unordered_selects=ON')
                def rows(table):
                    cursor=conn.execute(f'SELECT * FROM {table}');names=[v[0] for v in cursor.description]
                    return [dict(zip(names,row,strict=True)) for row in cursor]
                track=rows('djmdContent')[0]
                selected=tuple(row for row in rows('djmdCue') if row['ContentID']=='abc')
                aggregate=next(row for row in rows('contentCue') if row['ContentID']=='abc')
                expected=native.cue_snapshot_fields(CueSnapshot(track,selected,aggregate))
                self.assertEqual(self.read(conn,real_fields=True)['cue_fields'],expected)


if __name__=='__main__':unittest.main()
