"""Compiled renderer + independent decoders, using required offline copies only."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock
import uuid
import io
from types import SimpleNamespace

from scripts.check_fixtures import cdj_reference
from soundbox import cdj_export, cdj_transaction
from soundbox.cdj_usb import DATABASE_FILES, PROFILE_FILES, read_library, run_renderer
from soundbox.library import Library, Playlist, Track
from soundbox.rekordbox_analysis import read_cues
from research.cdj_export.export_compare import sql_rows

ROOT = Path(__file__).resolve().parents[1]
KEY_SOURCE = ROOT/'native/cdj/vendor/rbxport/crates/rbl-onelibrary/src/key.rs'


class CdjIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = cdj_reference()

    def setUp(self):
        self.area = tempfile.TemporaryDirectory()
        self.addCleanup(self.area.cleanup)
        self.root = Path(self.area.name).resolve()
        self.target = self.root/'USB'
        self.target.mkdir()
        self.volume = str(uuid.uuid4())
        self.token = self.target.stat().st_dev
        self.request = json.loads((self.fixture/'request.json').read_text())
        row = self.request['tracks'][0]
        row['source_path'] = str(self.fixture/row['source_path'])
        row['analysis_source'] = str(self.fixture/row['analysis_source'])
        track = Track('17','Fixture Track','Fixture Artist',(self.fixture/'source.wav').as_uri(),
                      source_uuid='explicit-fixture-uuid',source_analysis_path=row['analysis_source'])
        self.playlist = Playlist('Ordered Fixture',['Folder'],keys=['17','17'],pid='fixture')
        self.lib = Library({'17':track},[self.playlist],'synthetic-master.db',
                           source_kind='rekordbox-db',source_library_id='42')
        self.recovery = self.root/'recovery'
        self.plans = []
        self.addCleanup(lambda:[p.close() for p in self.plans])

    def plan(self):
        # This explicit DTO replaces the source-read boundary only. The real
        # acquisition, Rust renderer, independent readers and transaction run.
        with mock.patch('soundbox.cdj_source.capture_metadata',side_effect=lambda *_a,**_k:deepcopy(self.request)):
            plan = cdj_export.plan_export(self.lib,[self.playlist],self.target,token=self.token,
                     volume_uuid=self.volume,recovery_root=self.recovery)
        self.plans.append(plan)
        return plan

    def standalone(self):
        with mock.patch('soundbox.cdj_source.capture_metadata', side_effect=lambda *_a,**_k:deepcopy(self.request)):
            plan = cdj_export.plan_export(self.lib, [], self.target, token=self.token,
                volume_uuid=self.volume, recovery_root=self.recovery, additive=True,
                standalone=(self.lib.tracks_by_id['17'],))
        self.plans.append(plan)
        return plan

    def selected(self, playlists, *, standalone=(), art_cache=None):
        def capture(_library, tracks, **_kwargs):
            value=deepcopy(self.request);ids={int(t.track_id) for t in tracks}
            value['tracks']=[t for t in value['tracks'] if int(t['id']) in ids]
            value['track_extras']=[t for t in value['track_extras'] if int(t['source_id']) in ids]
            return value
        with mock.patch('soundbox.cdj_source.capture_metadata',side_effect=capture):
            plan=cdj_export.plan_export(self.lib,playlists,self.target,token=self.token,
                volume_uuid=self.volume,recovery_root=self.recovery,additive=True,
                standalone=standalone,art_cache=art_cache)
        self.plans.append(plan)
        return plan

    def extra_track(self, tid):
        source=self.root/'sources'/f'{tid}.wav';source.parent.mkdir(exist_ok=True)
        shutil.copyfile(self.fixture/'source.wav',source)
        dto=deepcopy(self.request['tracks'][0]);dto.update(id=int(tid),source_path=str(source),title=f'Track {tid}')
        extra=deepcopy(self.request['track_extras'][0]);extra['source_id']=int(tid)
        self.request['tracks'].append(dto);self.request['track_extras'].append(extra)
        track=Track(tid,dto['title'],'Fixture Artist',source.as_uri(),source_uuid=f'fixture-{tid}',source_analysis_path=dto['analysis_source'])
        self.lib.tracks_by_id[tid]=track
        return track

    def plus_connection(self):
        import base64,re,zlib
        from sqlcipher3 import dbapi2 as sqlcipher
        source=KEY_SOURCE.read_text()
        blob=re.search(r'const BLOB: &str = "([^"]+)";',source).group(1)
        xor=re.search(r'const XOR_KEY: &\[u8\] = b"([^"]+)";',source).group(1).encode()
        decoded=base64.b85decode(blob)
        key=zlib.decompress(bytes(byte^xor[i%len(xor)] for i,byte in enumerate(decoded))).decode()
        db=sqlcipher.connect(self.target/DATABASE_FILES[2])
        db.execute("PRAGMA key='"+key.replace("'","''")+"'")
        return db

    def test_exporting_one_playlist_does_not_capture_or_refresh_other_exported_playlists(self):
        retained_track = self.extra_track('18')
        self.playlist.keys = ['18']
        cdj_transaction.commit(self.selected([self.playlist]))
        second = Playlist('Selected only', [], keys=['17'], pid='selected-only')
        self.lib.playlists.append(second)
        cdj_transaction.commit(self.selected([second]))
        before = read_library(self.target, self.token)
        retained_playlist = next(p for p in before.playlists if p.title == self.playlist.name)
        retained_song = next(s for s in before.songs if s.source_id == '18')
        # The earlier source playlist has changed and its source is unavailable.
        # Exporting a different selected playlist must still be independent.
        self.playlist.name = 'Changed on desktop'
        self.playlist.keys = []
        retained_track.path.unlink()
        forbidden = {retained_track.path, self.target / retained_song.path.lstrip('/')}
        original = Path.open
        def guarded(path, *args, **kwargs):
            if path in forbidden:
                raise AssertionError('Unselected source or retained USB audio opened')
            return original(path, *args, **kwargs)
        with mock.patch.object(Path, 'open', guarded):
            plan = self.selected([second])
            self.assertFalse(plan.requires_commit)
            cdj_transaction.commit(plan)
        after = read_library(self.target, self.token)
        self.assertEqual(next(p for p in after.playlists if p.id == retained_playlist.id), retained_playlist)
        self.assertEqual(next(s for s in after.songs if s.source_id == '18'), retained_song)

    def test_freshly_formatted_initialization_publishes_only_native_assets(self):
        from soundbox import my_settings
        profile = {'MYSETTING.DAT': {'tempo_range': 'ten'}}
        with mock.patch('soundbox.cdj_source.capture_metadata', side_effect=AssertionError('Initialization read source')):
            plan = cdj_export.plan_initialize(self.target, token=self.token, volume_uuid=self.volume,
                name='TEST USB', profile=profile, recovery_root=self.recovery)
        self.plans.append(plan)
        self.assertEqual(set(plan.manifest['files']), set(DATABASE_FILES) | {'PIONEER/DEVSETTING.DAT'}
                         | {'PIONEER/' + name for name in my_settings.new_files(profile)})
        self.assertFalse((plan.candidate / '.rbxport-write.lock').exists())
        cdj_transaction.commit(plan)
        result = read_library(self.target, self.token)
        self.assertEqual(result.name, 'TEST USB')
        self.assertEqual(result.songs, ())
        self.assertEqual(result.playlists, ())
        self.assertFalse((self.target / '.rbxport-write.lock').exists())
        for name, raw in my_settings.new_files(profile).items():
            self.assertEqual((self.target / 'PIONEER' / name).read_bytes(), raw)

    def test_cleanup_preserves_different_collection_only_record_with_same_numeric_id(self):
        cdj_transaction.commit(self.selected([self.playlist]))
        before=read_library(self.target,self.token)
        original=before.songs[0]
        foreign=self.target/'Contents/Foreign/other.wav';foreign.parent.mkdir()
        shutil.copyfile(self.fixture/'source.wav',foreign)
        db=self.plus_connection()
        try:
            db.execute("UPDATE content SET masterDbId=77,masterContentId=81,title='Unrelated collection song',path='/Contents/Foreign/other.wav',analysisDataFilePath='' WHERE content_id=?",[int(original.id)])
            db.execute('DELETE FROM playlist_content WHERE content_id=?',[int(original.id)])
            db.commit()
        finally:db.close()
        self.playlist.keys=[]
        plan=self.selected([self.playlist])
        view=plan.manifest['view']
        self.assertEqual(view['classic_track_rows'],[])
        self.assertEqual([r['title'] for r in view['track_rows']],['Unrelated collection song'])
        cdj_transaction.commit(plan)
        self.assertFalse((self.target/original.path.lstrip('/')).exists())
        self.assertTrue(foreign.is_file())

    def test_selected_export_removes_only_last_playlist_references_and_reclaims_payloads(self):
        from PIL import Image
        image=io.BytesIO();Image.new('RGB',(20,20),'red').save(image,format='JPEG')
        art=SimpleNamespace(export_image_snapshot=lambda _tid:image.getvalue())
        cdj_transaction.commit(self.selected([self.playlist],art_cache=art))
        self.extra_track('18');orphan=self.extra_track('19')
        shared=Playlist('Shared',[],keys=['17','18'],pid='shared');self.lib.playlists.append(shared)
        cdj_transaction.commit(self.selected([shared],standalone=(orphan,),art_cache=art))
        before=read_library(self.target,self.token)
        removed=next(s for s in before.songs if s.source_id=='17')
        self.playlist.keys=[]
        kept=self.selected([self.playlist],art_cache=art)
        self.assertFalse(any(c.new_sha256 is None for c in kept.changes))
        cdj_transaction.commit(kept)
        self.assertTrue((self.target/removed.path.lstrip('/')).is_file())
        shared.keys=['18']
        prune=self.selected([shared],art_cache=art)
        deletions=[c for c in prune.changes if c.retire]
        self.assertIn(removed.path.lstrip('/'),[c.path for c in deletions])
        self.assertFalse(any(c.path.startswith('PIONEER/Artwork/') for c in deletions))
        cdj_transaction.commit(prune)
        self.assertFalse((self.target/removed.path.lstrip('/')).exists())
        self.assertEqual({s.source_id for s in read_library(self.target,self.token).songs},{'18','19'})
        backup=self.target/'.soundbox-cdj/transactions'/prune.manifest['generation']
        self.assertTrue(all(not (backup/c.path).exists() for c in deletions))
        self.assertTrue((backup/DATABASE_FILES[0]).is_file())
        self.assertFalse((self.target/cdj_transaction.JOURNAL).exists())

    def test_removed_member_rollback_and_committed_retirement_resume(self):
        cdj_transaction.commit(self.selected([self.playlist]))
        before={p:p.read_bytes() for p in self.target.rglob('*') if p.is_file()}
        self.playlist.keys=[]
        prune=self.selected([self.playlist])
        class Crash(BaseException):pass
        def crash_on_removal(_done,_total,path):
            if path.startswith('Contents/'):raise Crash()
        with self.assertRaises(Crash):cdj_transaction.commit(prune,on_progress=crash_on_removal)
        session=cdj_transaction.CdjSession(self.target,[],token=self.token,volume_uuid=self.volume)
        cdj_transaction.recover(session,self.recovery)
        for path,raw in before.items():self.assertEqual(path.read_bytes(),raw)
        again=self.selected([self.playlist])
        with mock.patch('soundbox.cdj_transaction._retire_removed',side_effect=Crash),self.assertRaises(Crash):
            cdj_transaction.commit(again)
        deletions=[c for c in again.changes if c.retire]
        self.assertTrue(deletions)
        backup=self.target/'.soundbox-cdj/transactions'/again.manifest['generation']
        # Simulate interruption after retiring one exact original, then resume.
        (backup/deletions[0].path).unlink()
        cdj_transaction.recover(session,self.recovery)
        self.assertTrue(all(not (backup/c.path).exists() and not (self.target/c.path).exists() for c in deletions))
        self.assertEqual(read_library(self.target,self.token).songs,())

    def test_unrelated_plus_only_song_and_playlist_difference_do_not_block_selected_export(self):
        import base64,re,zlib
        from sqlcipher3 import dbapi2 as sqlcipher
        cdj_transaction.commit(self.selected([self.playlist]))
        source=KEY_SOURCE.read_text()
        blob=re.search(r'const BLOB: &str = "([^"]+)";',source).group(1)
        xor=re.search(r'const XOR_KEY: &\[u8\] = b"([^"]+)";',source).group(1).encode()
        decoded=base64.b85decode(blob)
        key=zlib.decompress(bytes(byte^xor[i%len(xor)] for i,byte in enumerate(decoded))).decode()
        db=sqlcipher.connect(self.target/DATABASE_FILES[2])
        try:
            db.execute("PRAGMA key='"+key.replace("'","''")+"'")
            columns=[r[1] for r in db.execute('PRAGMA table_info(content)')]
            replacements={'content_id':'5000','masterContentId':'9999','masterDbId':'71', 'title':"'Unrelated native song'"}
            db.execute('INSERT INTO content SELECT '+','.join(replacements.get(c,'"'+c+'"') for c in columns)+' FROM content LIMIT 1')
            db.execute("UPDATE playlist SET name='Different Plus title' WHERE attribute=0")
            db.commit()
        finally:db.close()
        second=Playlist('Second',[],keys=['17'],pid='second');self.lib.playlists.append(second)
        original=Path.open
        def guarded(path,*args,**kwargs):
            if path.is_relative_to(self.target/'Contents') or path.is_relative_to(self.target/'PIONEER/USBANLZ'):
                raise AssertionError('Unrelated/retained media was opened')
            return original(path,*args,**kwargs)
        with mock.patch.object(Path,'open',guarded):plan=self.selected([second])
        view=plan.manifest['view']
        self.assertIn(5000,[t['id'] for t in view['track_rows']])
        self.assertNotIn(5000,[t['id'] for t in view['classic_track_rows']])
        self.assertIn('Different Plus title',[p['title'] for p in view['playlists']])
        self.assertIn('Ordered Fixture',[p['title'] for p in view['classic_playlists']])
        with mock.patch.object(Path,'open',guarded):cdj_transaction.commit(plan)

    def test_standalone_initial_export_has_no_synthetic_playlist_and_is_a_true_noop(self):
        unsafe = Track('outside-library','Unsafe','Fixture',(self.target/'source.wav').as_uri(),source_uuid='fixture')
        with self.assertRaisesRegex(RuntimeError,'source'), mock.patch('soundbox.cdj_source.capture_metadata') as capture:
            cdj_export.plan_export(self.lib,[],self.target,token=self.token,volume_uuid=self.volume,
                                  recovery_root=self.recovery,standalone=(unsafe,))
        capture.assert_not_called()
        first = self.standalone(); cdj_transaction.commit(first)
        view = read_library(self.target,self.token)
        self.assertEqual(len(view.songs),1)
        self.assertEqual(view.playlists,())
        original = Path.open
        def no_device_media(path,*args,**kwargs):
            if path.is_relative_to(self.target/'Contents') or path.is_relative_to(self.target/'PIONEER/USBANLZ'):
                raise AssertionError('Retained media was read')
            return original(path,*args,**kwargs)
        with mock.patch.object(Path,'open',no_device_media), mock.patch('soundbox.cdj_incremental.acquire_source',side_effect=AssertionError('Source acquired again')):
            again = self.standalone()
        self.assertFalse(again.requires_commit)
        self.assertEqual(again.changes,())

    def test_interrupted_standalone_intent_resumes_from_local_receipt_without_a_playlist(self):
        first = self.standalone()
        class Crash(BaseException): pass
        with self.assertRaises(Crash):
            cdj_transaction.commit(first,on_progress=lambda *_:(_ for _ in ()).throw(Crash()))
        with mock.patch('soundbox.cdj_source.capture_metadata',side_effect=lambda *_a,**_k:deepcopy(self.request)):
            retry = cdj_export.plan_export(self.lib,[],self.target,token=self.token,
                volume_uuid=self.volume,recovery_root=self.recovery)
        self.plans.append(retry);cdj_transaction.commit(retry)
        view = read_library(self.target,self.token)
        self.assertEqual((len(view.songs),len(view.playlists)),(1,0))
        self.assertEqual(retry.manifest['standalone'][0]['track_id'],'17')

    def test_standalone_incremental_preserves_native_and_managed_playlists(self):
        first = self.plan(); cdj_transaction.commit(first)
        before = read_library(self.target,self.token)
        again = self.standalone(); cdj_transaction.commit(again)
        after = read_library(self.target,self.token)
        self.assertEqual(after.playlists,before.playlists)
        self.assertEqual(after.songs,before.songs)
        self.assertFalse(again.requires_commit)

    def test_read_only_native_snapshot_uses_captured_settings_and_playlist_order(self):
        snapshot = read_library(self.fixture/'native',(self.fixture/'native').stat().st_dev)
        self.assertEqual(len(snapshot.playlists),1)
        self.assertEqual(len(snapshot.playlists[0].entries),5)
        self.assertEqual(dict(snapshot.display)['waveform_color'],'RGB')
        self.assertEqual(dict(snapshot.display)['waveform_position'],'LEFT')
        self.assertFalse(snapshot.managed)

    def test_classic_only_snapshot_needs_no_manual_conversion(self):
        shutil.copytree(self.fixture/'native/PIONEER',self.target/'PIONEER')
        (self.target/DATABASE_FILES[2]).unlink()
        snapshot = read_library(self.target,self.token)
        self.assertEqual(len(snapshot.playlists[0].entries),5)
        self.assertIsNone(snapshot.background_plus)
        self.assertIsNotNone(snapshot.background_classic)
        plan = self.plan()
        self.assertTrue((plan.candidate/DATABASE_FILES[2]).is_file())
        self.assertTrue(plan.replaces_native_library)

    def test_display_reads_a_stable_native_wal_without_touching_the_usb(self):
        import base64,re,zlib,hashlib
        from sqlcipher3 import dbapi2 as sqlcipher
        shutil.copytree(self.fixture/'native/PIONEER',self.target/'PIONEER')
        source=KEY_SOURCE.read_text()
        blob=re.search(r'const BLOB: &str = "([^"]+)";',source).group(1)
        xor=re.search(r'const XOR_KEY: &\[u8\] = b"([^"]+)";',source).group(1).encode()
        decoded=base64.b85decode(blob)
        key=zlib.decompress(bytes(byte^xor[i%len(xor)] for i,byte in enumerate(decoded))).decode()
        db=sqlcipher.connect(self.target/DATABASE_FILES[2])
        try:
            db.execute("PRAGMA key='"+key.replace("'","''")+"'")
            db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA wal_autocheckpoint=0')
            db.execute("UPDATE property SET deviceName='Native WAL name'");db.commit()
            paths=[p for p in self.target.rglob('*') if p.is_file()]
            before={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
            view=read_library(self.target,self.token,snapshot_only=True)
            self.assertEqual(view.name,'Native WAL name')
            self.assertEqual(before,{p:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})
            with self.assertRaisesRegex(ValueError,'SQLite'):read_library(self.target,self.token)
        finally:db.close()

    def test_plus_only_display_does_not_require_classic_or_ext_catalogs(self):
        path=self.target/DATABASE_FILES[2];path.parent.mkdir(parents=True)
        shutil.copyfile(self.fixture/'native'/DATABASE_FILES[2],path)
        native=read_library(self.target,self.token)
        self.assertGreater(len(native.songs),0)
        self.assertEqual(native.catalog['classic_track_rows'],[])
        self.assertEqual(native.catalog['classic_tags'],[])

    def test_unmanaged_rekordbox_catalog_is_preserved_without_reading_its_media(self):
        shutil.copytree(self.fixture/'native/PIONEER',self.target/'PIONEER')
        before=read_library(self.target,self.token)
        original=Path.open
        def guarded(path,*args,**kwargs):
            if path.is_relative_to(self.target/'Contents') or path.is_relative_to(self.target/'PIONEER/USBANLZ'):
                raise AssertionError('An unrelated native payload was read')
            return original(path,*args,**kwargs)
        with mock.patch.object(Path,'open',guarded):
            plan=self.plan()
            cdj_transaction.commit(plan)
        after=read_library(self.target,self.token)
        for playlist in before.playlists:
            retained=next(p for p in after.playlists if p.id==playlist.id)
            self.assertEqual(retained,playlist)
        for song in before.songs:
            self.assertEqual(next(s for s in after.songs if s.id==song.id),song)
        self.assertTrue(all(not c.path.startswith('Contents/') or c.old_sha256 is None for c in plan.changes))
        self.assertTrue(plan.manifest['foreign_playlists'])

    def test_rekordbox_can_add_a_playlist_between_soundbox_exports(self):
        first=self.plan();cdj_transaction.commit(first)
        old_manifest=(self.target/cdj_transaction.MANIFEST).read_bytes()
        second_source=self.root/'native-source'/'source.wav';second_source.parent.mkdir();shutil.copyfile(self.fixture/'source.wav',second_source)
        extra_track=Track('18','Native addition','Fixture Artist',second_source.as_uri(),source_uuid='external-uuid')
        self.lib.tracks_by_id['18']=extra_track
        native_playlist=Playlist('Native playlist',[],keys=['18'],pid='native')
        self.lib.playlists.append(native_playlist)
        request=deepcopy(self.request)
        row=deepcopy(request['tracks'][0]);row.update(id=18,title='Native addition',source_path=str(second_source))
        extra=deepcopy(request['track_extras'][0]);extra['source_id']=18
        request['tracks']=[row];request['track_extras']=[extra]
        with mock.patch('soundbox.cdj_source.capture_metadata',side_effect=lambda *_a,**_k:deepcopy(request)):
            external=cdj_export.plan_export(self.lib,[native_playlist],self.target,token=self.token,
                volume_uuid=self.volume,recovery_root=self.recovery,additive=True)
        self.plans.append(external);cdj_transaction.commit(external)
        # A native exporter updates the catalogs, without updating our manifest.
        (self.target/cdj_transaction.MANIFEST).write_bytes(old_manifest)
        before=read_library(self.target,self.token)
        native=next(p for p in before.playlists if p.title=='Native playlist')
        native_song=next(s for s in before.songs if s.title=='Native addition')
        original=Path.open
        def guard(path,*args,**kwargs):
            if path.is_relative_to(self.target/'Contents') or path.is_relative_to(self.target/'PIONEER/USBANLZ'):
                raise AssertionError('Retained native media was read')
            return original(path,*args,**kwargs)
        self.playlist.keys=['17']
        with mock.patch.object(Path,'open',guard),mock.patch('soundbox.cdj_incremental.acquire_source',side_effect=AssertionError('Unchanged source acquired')):
            update=self.plan();cdj_transaction.commit(update)
        after=read_library(self.target,self.token)
        self.assertEqual(next(p for p in after.playlists if p.id==native.id),native)
        self.assertEqual(next(s for s in after.songs if s.id==native_song.id),native_song)
        self.assertTrue(all(c.path in DATABASE_FILES for c in update.changes))
        self.assertFalse(any(p.lstrip('/') in update.manifest['files'] for p in (native_song.path,native_song.analysis)))

    def test_native_audio_is_reused_on_first_soundbox_drop_without_a_usb_hash(self):
        first=self.plan();cdj_transaction.commit(first);first.close()
        (self.target/cdj_transaction.MANIFEST).unlink()
        original=Path.open
        def guard(path,*args,**kwargs):
            if path.is_relative_to(self.target/'Contents'):raise AssertionError('Native USB audio was read')
            return original(path,*args,**kwargs)
        # A metadata edit still retains the native song by its source identity.
        self.request['tracks'][0]['title']='SoundBox display title'
        with mock.patch.object(Path,'open',guard):
            adopted=self.plan();cdj_transaction.commit(adopted)
        self.assertEqual(adopted.tracks,1)
        self.assertEqual(adopted.playlists,1)
        self.assertFalse(any(c.path.startswith('Contents/') for c in adopted.changes))
        self.assertEqual(read_library(self.target,self.token).songs[0].title,'SoundBox display title')

    def test_different_desktop_library_source_ids_and_tags_do_not_replace_each_other(self):
        self.request['my_tags']=[dict(id=1,seq=0,name='Original category',attribute=1,parent=0),dict(id=2,seq=0,name='Original tag',attribute=0,parent=1)]
        self.request['tracks'][0]['my_tags']=[2]
        first=self.plan();cdj_transaction.commit(first)
        original=read_library(self.target,self.token).songs[0]
        self.lib.source_library_id='43';self.request['db_id']=43
        self.request['tracks'][0]['title']='Other library recording'
        self.playlist.name='Other library playlist';self.playlist.pid='other-library'
        self.request['my_tags'][0]['name']='Different category';self.request['my_tags'][1]['name']='Different tag'
        second=self.plan();cdj_transaction.commit(second)
        view=read_library(self.target,self.token)
        self.assertEqual(len(view.songs),2)
        self.assertEqual(next(s for s in view.songs if s.id==original.id),original)
        newer=next(s for s in view.songs if s.source_db_id=='43')
        self.assertNotEqual(newer.path,original.path)
        self.assertEqual((original.info['myTags'],newer.info['myTags']),('Original tag','Different tag'))
        self.assertEqual({t['name'] for t in view.catalog['native_tags']},{'Original category','Original tag','Different category','Different tag'})

    def test_newer_native_export_supersedes_interrupted_soundbox_work_without_media_reads(self):
        first=self.plan();cdj_transaction.commit(first)
        self.playlist.keys=['17'];interrupted=self.plan()
        class Crash(BaseException):pass
        with self.assertRaises(Crash):
            cdj_transaction.commit(interrupted,on_progress=lambda *_:(_ for _ in ()).throw(Crash()))
        # The foreign native export has its own coherent catalogs, ignoring
        # SoundBox's journal, as rekordbox normally does.
        newer=self.root/'newer-native';shutil.copytree(self.fixture/'native/PIONEER',newer/'PIONEER')
        for path in DATABASE_FILES:shutil.copyfile(newer/path,self.target/path)
        expected={p:(self.target/p).read_bytes() for p in DATABASE_FILES}
        original=Path.open
        def guard(path,*args,**kwargs):
            if path.is_relative_to(self.target/'Contents') or path.is_relative_to(self.target/'PIONEER/USBANLZ'):
                raise AssertionError('Recovery read another export’s media')
            return original(path,*args,**kwargs)
        session=cdj_transaction.CdjSession(self.target,[],token=self.token,volume_uuid=self.volume)
        with mock.patch.object(Path,'open',guard):cdj_transaction.recover(session,self.recovery,retain_new=True)
        self.assertFalse((self.target/cdj_transaction.JOURNAL).exists())
        self.assertEqual({p:(self.target/p).read_bytes() for p in DATABASE_FILES},expected)
        self.assertTrue(cdj_transaction.reusable_proof(session,self.recovery)['selection'])

    def test_matching_native_record_needs_no_source_acquisition_or_media_preparation(self):
        first=self.plan();cdj_transaction.commit(first);first.close()
        (self.target/cdj_transaction.MANIFEST).unlink()
        with mock.patch('soundbox.cdj_incremental.acquire_source',side_effect=AssertionError('Native song was acquired')):
            adopted=self.plan()
        self.assertFalse(any(c.path.startswith(('Contents/','PIONEER/USBANLZ/')) for c in adopted.changes))
        self.assertTrue(adopted.requires_commit)
        cdj_transaction.commit(adopted)
        self.assertEqual(adopted.manifest['track_inputs']['17']['baseline'],'native catalog inventory')
        with mock.patch('soundbox.cdj_incremental.acquire_source',side_effect=AssertionError('Native song was acquired')):
            repeat=self.plan()
        self.assertEqual(repeat.changes,())
        self.assertFalse(repeat.requires_commit)

    def test_browse_and_my_settings_save_only_small_preferences_in_both_formats(self):
        shutil.copytree(self.fixture/'native/PIONEER',self.target/'PIONEER')
        before=read_library(self.target,self.token)
        from soundbox import my_settings
        current=my_settings.values(before.files)
        current['MYSETTING.DAT']['tempo_range']='wide'
        changes=dict(categories=[2,3,4,11,17,19,20,27,24],sorts=[25,26,5,11,22,2],sub_column=11,
                     colors={'8':'Energy'},my_settings=current)
        original=Path.open
        def guard(path,*args,**kwargs):
            if path.is_relative_to(self.target/'Contents') or path.is_relative_to(self.target/'PIONEER/USBANLZ'):
                raise AssertionError('Settings operation inspected media')
            return original(path,*args,**kwargs)
        with mock.patch.object(Path,'open',guard):
            plan=cdj_export.plan_settings(self.target,changes,token=self.token,volume_uuid=self.volume,recovery_root=self.recovery)
            self.plans.append(plan);cdj_transaction.commit(plan)
        after=read_library(self.target,self.token)
        self.assertEqual(after.songs,before.songs);self.assertEqual(after.playlists,before.playlists)
        self.assertEqual(my_settings.values(after.files)['MYSETTING.DAT']['tempo_range'],'wide')
        self.assertEqual(dict(after.preferences['colors'])[8],'Energy')
        self.assertEqual(after.preferences['sub_column'],11)
        self.assertTrue(all(c.path in DATABASE_FILES or c.path=='PIONEER/MYSETTING.DAT' for c in plan.changes))
        from soundbox.cdj_usb import digest
        raw=dict(after.files)['PIONEER/rekordbox/export.pdb']
        from soundbox.cdj_pdb import audit
        self.assertFalse(audit(raw)['issues'])
        for kind,key in ((17,'categories'),(18,'sorts')):
            rows=after.preferences['classic_browse'][kind-16]
            actual=[]
            for row in rows:
                menu=int.from_bytes(bytes(row[:2]),'little')
                seq=int.from_bytes(bytes(row[6:8]),'little') if kind==17 else row[5]
                hidden=bool(row[5]&1) if kind==17 else bool(row[4]&1)
                if not hidden:actual.append((seq,menu))
            self.assertEqual([menu for _,menu in sorted(actual)],changes[key])
        noop=cdj_export.plan_settings(self.target,changes,token=self.token,volume_uuid=self.volume,recovery_root=self.recovery)
        self.plans.append(noop)
        self.assertFalse(any(c.path.startswith('PIONEER/MYSETTING') for c in noop.changes))
        self.assertEqual(digest(dict(before.files)['PIONEER/djprofile.nxs']),digest(dict(after.files)['PIONEER/djprofile.nxs']))

    def test_new_usb_exports_both_formats_exact_audio_and_fresh_cues_then_noops(self):
        plan = self.plan()
        self.assertEqual((plan.tracks,plan.playlists),(1,1))
        self.assertEqual(len(plan.manifest['view']['playlists'][-1]['entries']),2)
        output = plan.candidate
        rows = sql_rows(output/DATABASE_FILES[2],KEY_SOURCE)['tables']
        self.assertEqual(len(rows['content']),1)
        self.assertEqual(len(rows['playlist_content']),2)
        self.assertEqual({c['inUsec'] for c in rows['cue']},{12_345_000,23_456_000})
        analysis = output/rows['content'][0]['analysisDataFilePath'].lstrip('/')
        self.assertEqual({(c['kind'],c['position_ms']) for c in read_cues(analysis)},
                         {('memory',12345),('hot',23456)})
        payload = output/rows['content'][0]['path'].lstrip('/')
        self.assertEqual(cdj_transaction._hash_file(payload),cdj_transaction._hash_file(self.fixture/'source.wav'))
        cdj_transaction.commit(plan)
        plan.close()
        before = (self.target/cdj_transaction.MANIFEST).read_bytes()
        second = self.plan()
        self.assertEqual(second.changes,())
        cdj_transaction.commit(second)
        self.assertEqual((self.target/cdj_transaction.MANIFEST).read_bytes(),before)
        self.assertFalse(cdj_export.status_hint(self.lib,self.target,token=self.token,volume_uuid=self.volume))
        verified = cdj_export.verify(self.target,token=self.token,volume_uuid=self.volume,deep=True)
        self.assertFalse(verified['managed_issues'])

    def test_existing_profiles_are_byte_preserved_and_settings_edit_updates_both(self):
        shutil.copytree(self.fixture/'native/PIONEER',self.target/'PIONEER')
        profiles = {n:(self.target/'PIONEER'/n).read_bytes() for n in PROFILE_FILES}
        plan = self.plan()
        for name,raw in profiles.items():
            self.assertEqual((plan.candidate/'PIONEER'/name).read_bytes(),raw)
        cdj_transaction.commit(plan)
        settings = cdj_export.plan_settings(self.target,{'device_name':'Fixture USB','background_plus':7,
                    'background_classic':4,'waveform_position':'CENTER'},token=self.token,
                    volume_uuid=self.volume,recovery_root=self.recovery)
        self.plans.append(settings)
        self.assertTrue(all(c.path in DATABASE_FILES or c.path=='PIONEER/DEVSETTING.DAT' for c in settings.changes))
        cdj_transaction.commit(settings)
        after = read_library(self.target,self.token)
        self.assertEqual(after.name,'Fixture USB')
        self.assertEqual(after.background_classic,4)
        self.assertEqual(after.background_plus,7)
        self.assertEqual(dict(after.display)['waveform_position'],'CENTER')
        for name in PROFILE_FILES[1:]:
            self.assertEqual((self.target/'PIONEER'/name).read_bytes(),profiles[name])

    def test_late_source_metadata_change_cancels_preparation_without_usb_writes(self):
        changed = deepcopy(self.request)
        changed['source_revision']='newer-cue-or-grid'
        with mock.patch('soundbox.cdj_source.capture_metadata',side_effect=[deepcopy(self.request),changed]):
            with self.assertRaisesRegex(ValueError,'changed during preparation'):
                cdj_export.plan_export(self.lib,[self.playlist],self.target,token=self.token,
                    volume_uuid=self.volume,recovery_root=self.recovery)
        self.assertEqual(list(self.target.iterdir()),[])

    def test_local_renderer_refuses_paths_outside_its_workspace(self):
        with self.assertRaisesRegex(RuntimeError,'local workspace'):
            run_renderer('render',self.root,self.fixture/'request.json',self.root/'candidate')


    def test_source_capture_rejects_cached_track_uuid_against_fresh_database(self):
        from soundbox.cdj_source import capture_metadata
        source = self.root/'master.db'
        source.write_bytes(b'explicit disposable source identity')
        self.lib.source = source
        db = mock.Mock()
        db.get_property.return_value = [SimpleNamespace(DBID='42')]
        db.get_cue.return_value = []
        db.get_my_tag.return_value = []
        db.get_content_file.return_value = []
        changed = SimpleNamespace(ID='17',UUID='different-native-uuid',rb_local_deleted=0)
        with mock.patch('soundbox.rekordbox_db._copy_stable_snapshot'), \
             mock.patch('pyrekordbox.Rekordbox6Database',return_value=db), \
             mock.patch('soundbox.rekordbox_db._content_rows',return_value=[changed]), \
             mock.patch('soundbox.rekordbox_db._read_tags',return_value=([],{})):
            with self.assertRaisesRegex(ValueError,'identity changed'):
                capture_metadata(self.lib,list(self.lib.tracks_by_id.values()))
        db.close.assert_called_once()

    def test_source_capture_requires_native_authority_for_imports_and_xml(self):
        from soundbox.cdj_source import capture_metadata
        self.lib.source_kind='rekordbox-xml'
        with self.assertRaisesRegex(ValueError,'native rekordbox library'):
            capture_metadata(self.lib,list(self.lib.tracks_by_id.values()))
        self.lib.source_kind='rekordbox-db'
        self.lib.tracks_by_id['17'].imported=True
        with self.assertRaisesRegex(ValueError,'native analysis and identity'):
            capture_metadata(self.lib,list(self.lib.tracks_by_id.values()))

    def test_source_capture_translates_logical_ids_but_keeps_logical_display_overrides(self):
        from dataclasses import replace
        from soundbox.cdj_source import capture_metadata
        original=self.lib.tracks_by_id['17'];logical=replace(original,track_id='sb-stable',source_native_id='17')
        self.lib.tracks_by_id={'sb-stable':logical};self.playlist.keys=['sb-stable','sb-stable']
        source=self.root/'master.db';source.write_bytes(b'explicit disposable source');self.lib.source=source
        row=SimpleNamespace(**dict.fromkeys(('ContentLink CueUpdated AnalysisUpdated TrackInfoUpdated BPM Length '
            'Rating ColorID ReleaseYear BitRate SampleRate FileSize TrackNo DiscNo BitDepth DJPlayCount Analysed').split(),0),
            ID='17',UUID=logical.source_uuid,FolderPath=str(logical.path),rb_local_deleted=0,
            Title='Native',Artist=None,Album=None,Genre=None,Label=None,Key=None,Commnt='',
            DateCreated='',ReleaseDate='',HotCueAutoLoad='on',DeliveryControl='on',ISRC='',AnalysisDataPath='native')
        db=mock.Mock();db.get_property.return_value=[SimpleNamespace(DBID='42')]
        db.get_cue.return_value=[];db.get_my_tag.return_value=[];db.get_content_file.return_value=[]
        current=Playlist(self.playlist.name,self.playlist.folder_path,['17','17'],self.playlist.pid)
        with mock.patch('soundbox.rekordbox_db._copy_stable_snapshot'), \
                mock.patch('pyrekordbox.Rekordbox6Database',return_value=db), \
                mock.patch('soundbox.rekordbox_db._content_rows',return_value=[row]) as content, \
                mock.patch('soundbox.rekordbox_db._read_tags',return_value=([],{})), \
                mock.patch('soundbox.rekordbox_db._active_track_ids',return_value={'17'}), \
                mock.patch('soundbox.rekordbox_db._read_playlists',return_value=[current]), \
                mock.patch('soundbox.cdj_source.resolve_dat_path',return_value=Path(original.source_analysis_path)):
            captured=capture_metadata(self.lib,[logical],titles={'sb-stable':'My title'},playlists=[self.playlist])
        self.assertEqual(content.call_args.args[1],{'17'})
        self.assertEqual(captured['tracks'][0]['id'],17)
        self.assertEqual(captured['tracks'][0]['title'],'My title')
        self.assertEqual(captured['track_extras'][0]['source_id'],17)

    def test_second_playlist_merges_only_new_inputs_and_retains_unrelated_bytes(self):
        from PIL import Image
        image = io.BytesIO();Image.new('RGB',(20,20),'red').save(image,format='JPEG')
        art = SimpleNamespace(export_image_snapshot=lambda _tid:image.getvalue())
        def plan(selected, request, **kwargs):
            def capture(_lib, tracks, **_kwargs):
                value = deepcopy(request)
                ids = [int(t.track_id) for t in tracks]
                value['tracks'] = [next(t for t in value['tracks'] if t['id']==tid) for tid in ids]
                value['track_extras'] = [next(t for t in value['track_extras'] if t['source_id']==tid) for tid in ids]
                return value
            with mock.patch('soundbox.cdj_source.capture_metadata',side_effect=capture):
                result = cdj_export.plan_export(self.lib,selected,self.target,token=self.token,
                    volume_uuid=self.volume,recovery_root=self.recovery,art_cache=art,**kwargs)
            self.plans.append(result)
            return result
        first = plan([self.playlist],self.request)
        cdj_transaction.commit(first)
        before = deepcopy(first.manifest)
        first.close()
        # A different source with the same filename exercises collision handling.
        second_source = self.root/'another/source.wav';second_source.parent.mkdir()
        shutil.copyfile(self.fixture/'source.wav',second_source)
        second_analysis = self.root/'second-analysis/ANLZ0000.DAT'
        second_analysis.parent.mkdir()
        for ext in ('DAT','EXT','2EX'):
            shutil.copyfile(Path(self.request['tracks'][0]['analysis_source']).with_suffix('.'+ext),
                            second_analysis.with_suffix('.'+ext))
        row = deepcopy(self.request['tracks'][0]);row.update(id=18,title='Second recording',
            source_path=str(second_source),genre='New genre',key='7A',analysis_source=str(second_analysis))
        extra = deepcopy(self.request['track_extras'][0]);extra['source_id']=18
        request = deepcopy(self.request);request['tracks'].append(row);request['track_extras'].append(extra)
        self.lib.tracks_by_id['18'] = Track('18','Second recording','Fixture Artist',second_source.as_uri(),
            source_uuid='second-native-uuid',source_analysis_path=row['analysis_source'])
        second = Playlist('Second',[],keys=['18','17','18'],pid='second')
        self.lib.playlists.append(second)
        old_assets = {self.target/path for path in before['files'] if path not in DATABASE_FILES
                      and path not in {'PIONEER/'+name for name in PROFILE_FILES}}
        forbidden = old_assets|{self.fixture/'source.wav',Path(self.request['tracks'][0]['analysis_source'])}
        original = Path.open
        def guard(path,*args,**kwargs):
            if path in forbidden:
                raise AssertionError(f'Retained payload was opened: {path}')
            return original(path,*args,**kwargs)
        with mock.patch.object(Path,'open',guard):
            addition = plan([second],request,additive=True)
        self.assertEqual(addition.playlists,2)
        self.assertEqual(addition.tracks,2)
        self.assertTrue(all(self.target/c.path not in old_assets for c in addition.changes))
        self.assertEqual(len([c for c in addition.changes if c.path.startswith('Contents/')]),1)
        self.assertFalse(any(c.path.startswith('PIONEER/Artwork/') for c in addition.changes))
        self.assertEqual(set(addition.manifest['track_inputs']),{'17','18'})
        # Old playlist ordering, fresh repeated membership and both format rows.
        view = addition.manifest['view']
        old = next(p for p in view['playlists'] if p['title']=='Ordered Fixture')
        new = next(p for p in view['playlists'] if p['title']=='Second')
        self.assertEqual(old['entries'],before['view']['playlists'][-1]['entries'])
        self.assertEqual([title for title,_ in new['entries']],['Second recording','Fixture Track','Second recording'])
        merged = sql_rows(addition.candidate/DATABASE_FILES[2],KEY_SOURCE)['tables']
        self.assertEqual(len(merged['content']),2)
        self.assertEqual(len(merged['cue']),4)
        self.assertEqual(len(merged['image']),1)
        with mock.patch.object(Path,'open',guard):
            cdj_transaction.commit(addition)
        self.assertFalse(cdj_export.verify(self.target,token=self.token,volume_uuid=self.volume,deep=True)['managed_issues'])
        # Repeating the drop never acquires audio or renders assets again.
        with mock.patch('soundbox.cdj_incremental.acquire_source',side_effect=AssertionError('Repeated acquisition')):
            unchanged = plan([second],request,additive=True)
        self.assertEqual(unchanged.changes,())

    def test_membership_only_update_reuses_assets_and_removes_a_playlist_without_clearing_collection(self):
        first = self.plan();cdj_transaction.commit(first);first.close()
        self.playlist.keys = ['17']
        with mock.patch('soundbox.cdj_incremental.acquire_source',side_effect=AssertionError('Membership read audio')):
            reordered = self.plan()
        self.assertTrue(all(c.path in DATABASE_FILES for c in reordered.changes))
        self.assertEqual(len(reordered.manifest['view']['playlists'][-1]['entries']),1)
        cdj_transaction.commit(reordered);reordered.close()
        self.lib.playlists = []
        empty = deepcopy(self.request);empty['tracks']=[];empty['track_extras']=[]
        with mock.patch('soundbox.cdj_source.capture_metadata',return_value=empty):
            removal = cdj_export.plan_export(self.lib,[],self.target,token=self.token,
                volume_uuid=self.volume,recovery_root=self.recovery)
        self.plans.append(removal)
        self.assertEqual((removal.tracks,removal.playlists),(1,0))
        self.assertFalse(any(c.path.startswith('Contents/') for c in removal.changes))
        cdj_transaction.commit(removal)
        self.assertEqual(len(read_library(self.target,self.token).songs),1)

    def test_selected_cue_update_changes_analysis_and_catalogs_without_copying_audio(self):
        first = self.plan();cdj_transaction.commit(first);first.close()
        self.request['tracks'][0]['cues'][0]['time_ms'] += 1
        self.request['track_extras'][0]['cue_update_count'] += 1
        changed = self.plan()
        self.assertFalse(any(c.path.startswith('Contents/') for c in changed.changes))
        row = changed.manifest['view']['track_rows'][0]
        self.assertIn(12346,{c['position_ms'] for c in read_cues(changed.candidate/row['analysis'].lstrip('/'))})
        cdj_transaction.commit(changed)
        self.assertFalse(cdj_export.verify(self.target,token=self.token,volume_uuid=self.volume,deep=True)['managed_issues'])

    def test_corrupted_pre_staged_audio_is_rejected_before_usb_changes(self):
        first = self.plan();cdj_transaction.commit(first);first.close()
        baseline = (self.target/cdj_transaction.MANIFEST).read_bytes()
        self.request['tracks'][0]['cues'][0]['time_ms'] += 1
        original = run_renderer
        def corrupt(command,root,*args,**kwargs):
            result = original(command,root,*args,**kwargs)
            if command=='render':
                request = json.loads(Path(args[0]).read_text())
                self.assertTrue(request['pre_staged'])
                with Path(request['tracks'][0]['source_path']).open('r+b') as stream:
                    stream.write(b'corrupt!')
            return result
        with mock.patch('soundbox.cdj_incremental.run_renderer',side_effect=corrupt):
            with self.assertRaisesRegex(ValueError,'differs from captured source'):
                self.plan()
        self.assertEqual((self.target/cdj_transaction.MANIFEST).read_bytes(),baseline)
        self.assertFalse((self.target/cdj_transaction.JOURNAL).exists())

    def test_pre_staging_cannot_smuggle_metadata_into_the_renderer(self):
        first = self.plan();cdj_transaction.commit(first);first.close()
        baseline = (self.target/cdj_transaction.MANIFEST).read_bytes()
        self.request['tracks'][0]['cues'][0]['time_ms'] += 1
        original = run_renderer
        def inject(command,root,*args,**kwargs):
            if command=='render':
                forbidden = Path(args[1])/'PIONEER/rekordbox'
                forbidden.mkdir(parents=True)
                (forbidden/'export.pdb').write_bytes(b'unexpected metadata')
            return original(command,root,*args,**kwargs)
        with mock.patch('soundbox.cdj_incremental.run_renderer',side_effect=inject):
            with self.assertRaisesRegex(RuntimeError,'pre-staging contains metadata'):
                self.plan()
        self.assertEqual((self.target/cdj_transaction.MANIFEST).read_bytes(),baseline)
