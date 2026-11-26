"""Generated routing checks reuse selected inventory without source/media opens."""
from contextlib import contextmanager
from dataclasses import replace
from copy import deepcopy
import builtins
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import wave

from soundbox.library import Library,Playlist,Track
from soundbox.operation_control import OperationCancelled,operation_scope
from soundbox.stock_ipod_catalog_state import StockCatalogWitness,CatalogWitnessRow
from soundbox.stock_ipod_info import StockIpodSnapshot,StockIpodTrackSnapshot,StockIpodPlaylistSnapshot,StockIpodPlaylistEntry
from soundbox.stock_ipod_playlist_binding import PlaylistBinding,source_playlist_identity
from soundbox.stock_ipod_shadow import StockIpodShadow
from soundbox.stock_plan_observations import NegativeSourceOmissions,STOCK_SOURCE_PROFILE,source_epoch
from soundbox import stock_sync_reuse as reuse


def wav(path,bits,frames=32):
    with wave.open(str(path),'wb') as stream:
        stream.setnchannels(2);stream.setsampwidth(bits//8);stream.setframerate(44100)
        stream.writeframes(bytes(frames*2*(bits//8)))


class StockSyncReuse(unittest.TestCase):
    def setUp(self):
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.positive=self.root/'positive.wav';wav(self.positive,16)
        self.negative=self.root/'negative.wav';wav(self.negative,24)
        tracks={key:Track(key,key,'Artist',path.as_uri(),size=path.stat().st_size,
            source_native_id='native-'+key,source_uuid='uuid-'+key,source_native_path=str(path))
            for key,path in [('one',self.positive),('two',self.negative)]}
        self.playlist=Playlist('Selected',[],['one','two','one'],pid='42')
        self.library=Library(tracks,[self.playlist],self.root/'master.db',source_kind='rekordbox-db',
            source_library_id='library',source_playlist_catalog={'nodes':[dict(ID='42',UUID='playlist-uuid',rb_local_deleted=0)]})
        source=source_playlist_identity(self.library,self.playlist)
        self.binding=PlaylistBinding(source.key,source.native_id,source.uuid,202,source.name,
            (('one',101),('one',101)),source_members=source.identities)
        self.witness=StockCatalogWitness((str(self.library.source),'rekordbox-db','library'),
            'generated-device','b'*64,(CatalogWitnessRow('one','native-one','uuid-one',str(self.positive),101,1,
                ':iPod_Control:Music:F00:POS.wav',(('title','one'),('artist','Artist'),('album',''),
                    ('genre',''),('comments',''),('year',0),('bpm',0))),),bindings=(self.binding,))
        native=StockIpodTrackSnapshot(1,101,'one','Artist','','','wav',self.positive.stat().st_size,
            1,1411,44100,0,0,0,0,0,':iPod_Control:Music:F00:POS.wav',0,0)
        self.snapshot=StockIpodSnapshot(self.root/'never-opened-device','Generated','generated-uuid','fat32','USB',
            'generated-disk',True,1000000,900000,'MA446','confirmed','iPod Video','5.5','generated-device',
            1,2,0x75,0,'b'*64,native.size,0,'',0,(native,),
            (StockIpodPlaylistSnapshot(202,'Selected','Playlist',False,
                (StockIpodPlaylistEntry(101,'one','Artist'),StockIpodPlaylistEntry(101,'one','Artist'))),))
        st=self.positive.stat()
        digest=hashlib.sha256(self.positive.read_bytes()).hexdigest()
        self.row=dict(dbid=101,device_path='iPod_Control/Music/F00/POS.wav',size=st.st_size,
            source_size=st.st_size,mtime_ns=st.st_mtime_ns,source_epoch=[st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns],
            sha256=digest,source_sha256=digest,stock_media_profile=STOCK_SOURCE_PROFILE,stock_technical=[1,1411,44100])
        self.shadow=StockIpodShadow('generated-device','MA446','',1,'generated',self.snapshot.database_sha256,
            self.snapshot.artwork_database_sha256,{},
            {str(self.positive):self.row},{},{})
        self.omissions=NegativeSourceOmissions(profile=STOCK_SOURCE_PROFILE)
        self.reason='PCM_24 / 44100 Hz / 2 ch WAV; validated stock boundary is PCM 16-bit / 44.1 kHz / stereo'
        self.omissions.remember(self.negative,source_epoch(self.negative.stat()),self.reason)

    def check(self,**changes):
        values=dict(library=self.library,selected=[self.playlist],snapshot=self.snapshot,
                    witness=self.witness,shadow=self.shadow,omissions=self.omissions)
        values.update(changes)
        return reuse.stock_selection_reusable(**values)

    @contextmanager
    def no_audio_opens(self):
        raw_open,raw_path,raw_os=builtins.open,Path.open,os.open
        def guard(path):
            if isinstance(path,(str,bytes,os.PathLike)):
                if Path(os.fsdecode(path)).suffix in {'.wav','.m4a','.db'}:
                    raise AssertionError('Routing opened source/media/DB bytes: '+os.fsdecode(path))
        def opened(path,*a,**kw):guard(path);return raw_open(path,*a,**kw)
        def path_open(path,*a,**kw):guard(path);return raw_path(path,*a,**kw)
        def os_open(path,*a,**kw):guard(path);return raw_os(path,*a,**kw)
        with mock.patch.object(builtins,'open',opened),mock.patch.object(Path,'open',path_open),mock.patch.object(os,'open',os_open):
            yield

    def test_warm_inventory_reuses_only_selected_sources_without_opening_audio_or_device(self):
        self.library.tracks_by_id['other']=Track('other','Other','Artist',(self.root/'missing-unselected.wav').as_uri())
        self.library.playlists.append(Playlist('Other',[],['other'],pid='unselected'))
        resolve=self.library.resolve
        def selected_only(playlist):
            self.assertEqual(playlist.pid,'42');return resolve(playlist)
        before=deepcopy(self.shadow)
        with self.no_audio_opens(),mock.patch.object(self.library,'resolve',side_effect=selected_only):
            self.assertTrue(self.check())
        self.assertEqual(self.shadow,before)
        self.assertFalse(self.snapshot.target.exists())

    def test_order_name_metadata_and_art_catalog_changes_do_not_require_audio_capture(self):
        selected=replace(self.playlist,name='Renamed',folder_path=['Moved'],keys=['two','one','one'])
        library=replace(self.library,tracks_by_id={**self.library.tracks_by_id,
            'one':replace(self.library.tracks_by_id['one'],name='New title',artist='New artist')},playlists=[selected])
        with self.no_audio_opens():
            self.assertTrue(self.check(library=library,selected=[selected]))

    def test_changed_native_database_or_artwork_digest_requires_full_planning(self):
        for field in ('database_sha256','artwork_database_sha256'):
            with self.subTest(field=field),self.no_audio_opens():
                changed=replace(self.snapshot,**{field:'e'*64})
                self.assertEqual(changed.tracks,self.snapshot.tracks)
                self.assertEqual(changed.playlists,self.snapshot.playlists)
                self.assertFalse(self.check(snapshot=changed))

    def test_stale_witness_digest_requires_full_planning_even_with_matching_shadow(self):
        for field in ('database_sha256','artwork_database_sha256'):
            with self.subTest(field=field),self.no_audio_opens():
                self.assertFalse(self.check(witness=replace(self.witness,**{field:'e'*64})))

    def test_added_removed_or_changed_occurrence_identity_requires_full_planning(self):
        for keys in (['one','two'],['one','two','one','one'],['one','two','missing']):
            with self.subTest(keys=keys):self.assertFalse(self.check(selected=[replace(self.playlist,keys=keys)]))
        for field in ('source_uuid','source_native_id','source_native_path','location_uri'):
            changed=replace(self.library.tracks_by_id['one'],**{field:'file:///changed.wav' if field=='location_uri' else 'changed'})
            with self.subTest(field=field):
                self.assertFalse(self.check(library=replace(self.library,tracks_by_id={**self.library.tracks_by_id,'one':changed})))

    def test_negative_rewrite_to_supported_wav_with_same_size_and_restored_mtime_falls_through(self):
        before=self.negative.stat();wav(self.negative,16,frames=48)
        os.utime(self.negative,ns=(before.st_atime_ns,before.st_mtime_ns))
        self.assertEqual(self.negative.stat().st_size,before.st_size)
        self.assertEqual(self.negative.stat().st_mtime_ns,before.st_mtime_ns)
        with self.no_audio_opens():self.assertFalse(self.check())

    def test_changed_positive_bytes_or_inode_never_reuse_an_old_source_epoch(self):
        before=self.positive.stat()
        with self.positive.open('r+b') as stream:stream.seek(-1,2);stream.write(b'!')
        os.utime(self.positive,ns=(before.st_atime_ns,before.st_mtime_ns))
        with self.no_audio_opens():self.assertFalse(self.check())
        replacement=self.root/'replacement.wav';wav(replacement,16)
        os.utime(replacement,ns=(before.st_atime_ns,before.st_mtime_ns));replacement.replace(self.positive)
        self.assertEqual(self.positive.stat().st_size,before.st_size)
        self.assertEqual(self.positive.stat().st_mtime_ns,before.st_mtime_ns)
        self.assertNotEqual(self.positive.stat().st_ino,before.st_ino)
        with self.no_audio_opens():self.assertFalse(self.check())

    def test_known_unsupported_suffix_needs_no_negative_wav_cache(self):
        other=self.root/'unsupported.m4a';other.write_bytes(b'Known unsupported stock suffix')
        changed=replace(self.library.tracks_by_id['two'],location_uri=other.as_uri(),source_native_path=str(other))
        library=replace(self.library,tracks_by_id={**self.library.tracks_by_id,'two':changed})
        identity=source_playlist_identity(library,self.playlist)
        witness=replace(self.witness,bindings=(replace(self.binding,source_members=identity.identities),))
        with self.no_audio_opens():self.assertTrue(self.check(library=library,witness=witness,omissions=None))

    def test_missing_negative_or_positive_proof_and_wrong_policy_are_conservative(self):
        for changes in (dict(shadow=None),dict(omissions=None),dict(omissions=NegativeSourceOmissions(profile='old-profile')),
                        dict(shadow=replace(self.shadow,sources={}))):
            with self.subTest(changes=changes):self.assertFalse(self.check(**changes))

    def test_device_or_source_swap_and_ambiguous_selected_identity_fall_through(self):
        for changes in (dict(witness=replace(self.witness,device_key='other')),
                        dict(shadow=replace(self.shadow,identity_key='other')),
                        dict(witness=replace(self.witness,source=('/other/master.db','rekordbox-db','other'))),
                        dict(snapshot=replace(self.snapshot,identity_conflict=True)),
                        dict(selected=[self.playlist,self.playlist]),
                        dict(witness=replace(self.witness,bindings=(self.binding,self.binding))),
                        dict(snapshot=replace(self.snapshot,tracks=self.snapshot.tracks*2))):
            with self.subTest(changes=changes):self.assertFalse(self.check(**changes))

    def test_malformed_shadow_native_path_size_hash_or_source_profile_falls_through(self):
        for key,value in [('device_path','../unowned.wav'),('device_path','/iPod_Control/Music/POS.wav'),
                          ('size',1),('sha256','z'*64),('source_sha256','missing'),('source_epoch',[1]),
                          ('source_size',True),('stock_media_profile','old'),('stock_technical',[1,True,44100]),('dbid',102)]:
            with self.subTest(field=key):
                row={**self.row,key:value}
                self.assertFalse(self.check(shadow=replace(self.shadow,sources={str(self.positive):row})))

    def test_symlink_source_and_missing_source_are_not_retained_inventory(self):
        saved=self.positive.with_suffix('.saved');self.positive.rename(saved);self.positive.symlink_to(saved)
        with self.no_audio_opens():self.assertFalse(self.check())
        self.positive.unlink()
        with self.no_audio_opens():self.assertFalse(self.check())

    def test_cloud_ctime_only_inventory_rule_does_not_refresh_old_positive_proof(self):
        before=deepcopy(self.row);self.positive.chmod(self.positive.stat().st_mode^0o100)
        with mock.patch.object(reuse,'is_cloud_storage_source',return_value=True),self.no_audio_opens():
            self.assertTrue(self.check())
        self.assertEqual(self.row,before)
        with self.no_audio_opens():self.assertFalse(self.check())

    def test_cancellation_propagates_instead_of_falling_through_to_more_work(self):
        from threading import Event
        stop=Event();stop.set()
        with operation_scope(stop),self.assertRaises(OperationCancelled):self.check()

    def test_one_local_id_cannot_collapse_different_bound_dbids(self):
        bad=replace(self.binding,members=(('one',101),('one',102)))
        # Both candidates and the dictionary's last target are otherwise valid;
        # only the contradictory correspondence must force full planning.
        native=replace(self.snapshot.tracks[0],track_id=2,dbid=102,
                       location=':iPod_Control:Music:F00:POS2.wav')
        snapshot=replace(self.snapshot,tracks=(*self.snapshot.tracks,native))
        row={**self.row,'dbid':102,'device_path':'iPod_Control/Music/F00/POS2.wav'}
        shadow=replace(self.shadow,sources={str(self.positive):row})
        self.assertFalse(self.check(witness=replace(self.witness,bindings=(bad,)),snapshot=snapshot,shadow=shadow))

    def test_same_path_under_different_local_ids_cannot_bypass_alias_checks(self):
        alias=replace(self.library.tracks_by_id['one'],track_id='alias',name='Different title')
        playlist=Playlist('Alias',[],['alias'],pid='43')
        library=replace(self.library,tracks_by_id={**self.library.tracks_by_id,'alias':alias},
            playlists=[self.playlist,playlist],source_playlist_catalog={'nodes':[
                *self.library.source_playlist_catalog['nodes'],dict(ID='43',UUID='alias-playlist',rb_local_deleted=0)]})
        source=source_playlist_identity(library,playlist)
        binding=PlaylistBinding(source.key,source.native_id,source.uuid,203,source.name,(('alias',101),),source_members=source.identities)
        snapshot=replace(self.snapshot,playlists=(*self.snapshot.playlists,StockIpodPlaylistSnapshot(203,'Alias','Playlist',False,())))
        self.assertFalse(self.check(library=library,selected=[self.playlist,playlist],snapshot=snapshot,
            witness=replace(self.witness,bindings=(self.binding,binding))))

    def test_same_local_occurrences_across_playlists_reuse_one_selected_file_check(self):
        playlist=Playlist('Another',[],['one'],pid='43')
        library=replace(self.library,playlists=[self.playlist,playlist],source_playlist_catalog={'nodes':[
            *self.library.source_playlist_catalog['nodes'],dict(ID='43',UUID='another-playlist',rb_local_deleted=0)]})
        source=source_playlist_identity(library,playlist)
        binding=PlaylistBinding(source.key,source.native_id,source.uuid,203,source.name,(('one',101),),source_members=source.identities)
        snapshot=replace(self.snapshot,playlists=(*self.snapshot.playlists,StockIpodPlaylistSnapshot(203,'Another','Playlist',False,())))
        with self.no_audio_opens(),mock.patch.object(reuse,'_retained_source',wraps=reuse._retained_source) as positive:
            self.assertTrue(self.check(library=library,selected=[self.playlist,playlist],snapshot=snapshot,
                witness=replace(self.witness,bindings=(self.binding,binding))))
        positive.assert_called_once()
