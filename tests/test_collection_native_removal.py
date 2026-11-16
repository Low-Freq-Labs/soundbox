"""Accepted Collection removal leaves every generated original/sidecar untouched."""
from copy import deepcopy
from contextlib import contextmanager,ExitStack
import builtins
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from soundbox import rekordbox_removal_store as store,rekordbox_removal_plan as planner,rekordbox_db
from soundbox.library import Library,Track
from soundbox.rekordbox_write import _connect,ConflictError,RecoveryRequired
from tests import test_removal_store as fixtures


class NativeOnlyRemoval(unittest.TestCase):
    def setUp(self):
        self.case=fixtures.RemovalStore();self.case.setUp();self.addCleanup(self.case.doCleanups)
        self.writer=self.case.writer;self.entry=self.case.entry

    def prepare(self):return self.writer.prepare([self.entry],native_only=True)
    def apply(self,review):return self.case.apply(review)

    def untouched(self):
        return {str(path):(path.read_bytes(),path.stat().st_ino,path.stat().st_mtime_ns,path.stat().st_ctime_ns)
                for path in (self.case.audio,self.case.resource)}

    def deny_file_disposal(self):
        for name in ('_audio_regular','acquire_source'):
            self.enterContext(mock.patch.object(store,name,side_effect=AssertionError('Native-only removal must not open audio')))
        for name in ('get','set','remove'):
            self.enterContext(mock.patch.object(store.xattrs,name,side_effect=AssertionError('Native-only removal must not touch xattrs')))
        self.enterContext(mock.patch.object(self.writer,'_guard_trash',side_effect=AssertionError('Native-only removal must not access Trash')))
        self.writer.trash=None

    @contextmanager
    def forbid_asset_io(self):
        """Reject actual filesystem access, including a harmless stat/scan."""
        audio=str(self.case.audio);share=str(self.case.source/'share');trash=str(self.case.trash)
        def guard(original,name):
            def checked(path,*args,**kwargs):
                if isinstance(path,(str,bytes,os.PathLike)):
                    value=os.fsdecode(path)
                    protected=(value==audio or any(value==root or value.startswith(root+'/') for root in (share,trash)))
                    if name in ('scandir','listdir') and value==str(self.case.root):protected=True
                    if protected:raise AssertionError('Native-only asset I/O: '+name+' '+value)
                return original(path,*args,**kwargs)
            return checked
        with ExitStack() as stack:
            for owner,names in ((os,('open','stat','lstat','scandir','listdir','unlink','remove','rename','replace')),
                                (builtins,('open',)),(io,('open',))):
                for name in names:stack.enter_context(mock.patch.object(owner,name,guard(getattr(owner,name),name)))
            yield

    def test_native_only_commit_preserves_audio_resources_and_never_uses_file_disposal(self):
        self.deny_file_disposal();before=self.untouched();state=self.case.state()
        with self.forbid_asset_io():review=self.prepare()
        plan=review['plan']
        self.assertTrue(plan['native_only'])
        for name in ('audio','resources','missing','missing_proofs','retained'):self.assertFalse(plan[name])
        self.assertTrue(plan['selections'][0]['keep_file'])
        self.assertEqual(self.case.state(),state);self.assertEqual(self.untouched(),before)
        with self.forbid_asset_io():
            result=self.apply(review)
            self.assertEqual(self.writer.verify_removed(review['folder'],review['sha256'],store.confirmation(plan)),result)
            self.assertEqual(self.apply(review),result)
        self.assertTrue(result['native_only']);self.assertEqual(result['trash'],{})
        self.assertEqual(self.untouched(),before);self.assertEqual(list(self.case.trash.iterdir()),[])
        after=self.case.state()
        self.assertEqual(after['tables']['unknown_table'],state['tables']['unknown_table'])
        conn=_connect(self.writer.database,readonly=True)
        try:
            self.assertEqual(conn.execute("SELECT rb_local_deleted FROM djmdContent WHERE ID='1'").fetchone(),(1,))
            self.assertEqual(conn.execute("SELECT TrackNo FROM djmdSongPlaylist WHERE ContentID='9'").fetchone(),(7,))
            self.assertEqual(conn.execute("SELECT TrackNo FROM djmdSongHistory WHERE ContentID='9'").fetchone(),(7,))
        finally:conn.close()

    def test_native_only_uses_the_identical_accepted_native_plan_as_disposal(self):
        build=store.planner.build
        def fixed(*args,**kwargs):
            kwargs.setdefault('timestamp','2026-11-13 12:00:00.000 +00:00')
            kwargs.setdefault('xml_clocks',{'2':[1000]})
            return build(*args,**kwargs)
        with mock.patch.object(store.planner,'build',side_effect=fixed):
            native=self.prepare()['plan'];disposal=self.case.prepare()['plan']
        self.assertEqual(native['changes'],disposal['changes'])
        self.assertEqual(native['payloads']['before.xml'],disposal['payloads']['before.xml'])
        self.assertEqual(native['payloads']['after.xml'],disposal['payloads']['after.xml'])
        self.assertNotEqual(store.confirmation(native),store.confirmation(disposal))

    def test_unavailable_audio_path_is_not_opened_or_required_for_native_only_removal(self):
        raw='/synthetic/unavailable/selected-song.wav'
        conn=_connect(self.writer.database)
        try:conn.execute("UPDATE djmdContent SET FolderPath=? WHERE ID='1'",(raw,))
        finally:conn.close()
        self.entry={**self.entry,'path':raw,'canonical':raw}
        self.deny_file_disposal();before=self.untouched()
        result=self.apply(self.prepare())
        self.assertTrue(result['native_only']);self.assertEqual(self.untouched(),before)

    def test_source_or_selection_identity_drift_never_changes_any_file(self):
        before=self.untouched()
        for field,value in (('path','/synthetic/other.wav'),('native',dict(uuid='00000000-0000-4000-8000-000000000099'))):
            with self.subTest(field=field):
                with self.assertRaises(ConflictError):self.writer.prepare([{**self.entry,field:value}],native_only=True)
        review=self.prepare()
        conn=_connect(self.writer.database)
        try:conn.execute("UPDATE djmdContent SET UUID=? WHERE ID='1'",('00000000-0000-4000-8000-000000000099',))
        finally:conn.close()
        with self.assertRaises(ConflictError):self.apply(review)
        self.assertEqual(self.untouched(),before)

    def test_native_only_recovery_never_moves_audio_or_restores_native_sidecars(self):
        self.deny_file_disposal();before=self.untouched();state=self.case.state()
        for boundary in ('journal','xml','sql','commit'):
            with self.subTest(boundary=boundary):
                review=self.prepare()
                def fail(phase):
                    if phase==boundary:raise RuntimeError('Simulated source transaction interruption')
                with mock.patch.object(store,'_fault',side_effect=fail):
                    with self.assertRaisesRegex(RuntimeError,'Simulated'):self.apply(review)
                if self.writer.journal.exists():self.writer.recover()
                self.assertEqual(self.untouched(),before)
                result=json.loads((Path(review['folder'])/'result.json').read_bytes())
                self.assertTrue(result['native_only'])
                self.assertEqual(result['outcome'],'committed' if boundary=='commit' else 'rolled-back')
                if boundary!='commit':self.assertEqual(self.case.state(),state)
                else:self.assertEqual(self.apply(review),result)
                self.assertFalse(self.writer.journal.exists())

    def test_native_only_plan_rejects_any_injected_file_phase(self):
        review=self.prepare();path=Path(review['folder'])/'plan.json';plan=deepcopy(review['plan'])
        plan['audio']=[dict(path=str(self.case.audio),size=self.case.audio.stat().st_size,sha256='0'*64)]
        path.write_text(json.dumps(plan));review['sha256']=hashlib.sha256(path.read_bytes()).hexdigest();review['plan']=plan
        before=self.untouched()
        with self.assertRaisesRegex(RecoveryRequired,'Native-only removal contains file disposal'):self.apply(review)
        self.assertEqual(self.untouched(),before)

    def test_committed_readback_refuses_replaced_native_identity_without_asset_io(self):
        review=self.prepare();self.apply(review)
        conn=_connect(self.writer.database)
        try:conn.execute("UPDATE djmdContent SET UUID=? WHERE ID='1'",('00000000-0000-4000-8000-000000000099',))
        finally:conn.close()
        with self.forbid_asset_io(),self.assertRaisesRegex(ConflictError,'restored or replaced'):
            self.writer.verify_removed(review['folder'],review['sha256'],store.confirmation(review['plan']))


class CollectionSelection(unittest.TestCase):
    def test_capture_reads_selected_scalars_without_traversing_the_library_or_filesystem(self):
        class SelectedOnly(dict):
            def values(self):raise AssertionError('No full Collection scan')
            def items(self):raise AssertionError('No full Collection scan')
            def __iter__(self):raise AssertionError('No full Collection scan')
        track=Track('local','Title','Artist','file:///unavailable/song.wav',source_native_id='1',source_uuid=fixtures.UUID,
                    source_native_path='/exact/native/song.wav')
        library=Library(SelectedOnly(local=track),[],Path('/synthetic/master.db'),
                        source_kind='rekordbox-db',source_library_id='123')
        with (mock.patch.object(Path,'stat',side_effect=AssertionError('No filesystem inspection')),
                mock.patch.object(Path,'resolve',side_effect=AssertionError('No filesystem inspection'))):
            captured=planner.collection_selection(library,['local'])
        self.assertEqual(captured['entries'],[dict(id='local',native_id='1',native=dict(uuid=fixtures.UUID),
            path='/exact/native/song.wav',local_path='/unavailable/song.wav',display=dict(title='Title',artist='Artist'))])

    def test_real_encrypted_reader_db_only_mode_never_inspects_any_media_path(self):
        from pyrekordbox.db6 import tables
        from sqlalchemy import Integer,Float
        from sqlalchemy.dialects import sqlite
        from sqlalchemy.schema import CreateTable
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve();source=root/'master.db';audio=root/'unavailable/song.wav'
            conn=_connect(source,create=True)
            try:
                for table in tables.Base.metadata.sorted_tables:
                    conn.execute(str(CreateTable(table).compile(dialect=sqlite.dialect())))
                def insert(table,**values):
                    row={column.name:(0 if isinstance(column.type,(Integer,Float)) else '')
                         for column in table.__table__.columns if not column.nullable}
                    row.update(created_at='2026-11-13 00:00:00.000 +00:00',updated_at='2026-11-13 00:00:00.000 +00:00',**values)
                    conn.execute('INSERT INTO "'+table.__tablename__+'" ('+','.join('"'+key+'"' for key in row)
                                 +') VALUES ('+','.join('?' for _ in row)+')',tuple(row.values()))
                insert(tables.DjmdProperty,DBID='synthetic-reader',DBVersion='6000')
                insert(tables.DjmdContent,ID='1',UUID=fixtures.UUID,Title='Kept',FileType=1,FolderPath=str(audio),
                       AnalysisDataPath='/PIONEER/USBANLZ/test/ANLZ0000.DAT')
                insert(tables.DjmdContent,ID='2',UUID='00000000-0000-4000-8000-000000000002',Title='Deleted',
                       FileType=1,FolderPath=str(audio),rb_local_deleted=1)
            finally:conn.close()
            guard=SimpleNamespace(case=SimpleNamespace(audio=audio,source=root,trash=root/'trash',root=root))
            before=source.read_bytes()
            with NativeOnlyRemoval.forbid_asset_io(guard):
                library=rekordbox_db.load_live(source,resolve_media_paths=False)
            self.assertEqual(source.read_bytes(),before)
            self.assertEqual(list(library.tracks_by_id),['1']);self.assertEqual(library.source_library_id,'synthetic-reader')
            track=library.tracks_by_id['1']
            self.assertEqual((str(track.path),track.source_native_path),(str(audio),''))
            self.assertEqual(track.source_analysis_path,str(root/'share/PIONEER/USBANLZ/test/ANLZ0000.DAT'))


if __name__=='__main__':unittest.main()
