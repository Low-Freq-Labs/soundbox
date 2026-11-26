"""Real native SQL, worker ownership and canonical acknowledgements in groups."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from desktop.metadata_controller import ACKS, KEY, MetadataController
from desktop.state_commit import StateCommitter
from desktop.tag_controller import TagEditController
from soundbox import metadata_cleanup_policy, rekordbox_cue_store, rekordbox_db, rekordbox_metadata_store as native, rekordbox_tags, source_truth
from soundbox.native_reader_process import NativeReaderProcess
from soundbox.rekordbox_write import _connect
from soundbox.state_repository import StateRepository, read_state
from tests.test_metadata_controller import Window
from tests.test_native_reader_process import fixture


def generated_source(root, count=3):
    from pyrekordbox.db6 import tables
    columns=[column for table in tables.Base.metadata.sorted_tables for column in table.columns if not column.primary_key]
    nullable=[column.nullable for column in columns]
    try:
        for column in columns:column.nullable=True
        root.mkdir();fixture(root/'master.db')
    finally:
        for column,value in zip(columns,nullable,strict=True):column.nullable=value
    (root/'masterPlaylists6.xml').write_bytes(b'<opaque preserved="yes"/>')
    conn=_connect(root/'master.db')
    try:
        names=[row[1] for row in conn.execute('PRAGMA table_info(djmdContent)')]
        row=dict(zip(names,conn.execute('SELECT * FROM djmdContent').fetchone(),strict=True))
        conn.execute('DELETE FROM djmdContent')
        for index in range(1,count+1):
            value={**row,'ID':str(index),'UUID':f'00000000-0000-4000-8000-{index:012d}',
                'Title':f'Artist - Song {index} (Original Mix)','ArtistID':'','AlbumID':'',
                'FolderPath':f'/synthetic/song-{index}.wav','TrackInfoUpdated':'2','rb_local_usn':40,
                'rb_data_status':0,'rb_local_data_status':0,'rb_local_deleted':0,'rb_local_synced':0,'usn':None}
            conn.execute('INSERT INTO djmdContent('+','.join('"'+name+'"' for name in names)+') VALUES ('+
                ','.join('?' for _ in names)+')',tuple(value[name] for name in names))
        registry={row[1]:(0 if 'INT' in row[2].upper() else '') for row in conn.execute('PRAGMA table_info(agentRegistry)')}
        registry.update(registry_id='localUpdateCount',int_1=40)
        conn.execute('INSERT INTO agentRegistry('+','.join('"'+name+'"' for name in registry)+') VALUES ('+
            ','.join('?' for _ in registry)+')',tuple(registry.values()))
        return rekordbox_tags._schema(conn)
    finally:conn.close()


class MetadataBatch(unittest.TestCase):
    track_count=3
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.schema=generated_source(self.root/'source',self.track_count)
        self.enterContext(mock.patch.object(rekordbox_cue_store,'_closed'))
        self.enterContext(mock.patch.object(rekordbox_cue_store,'_installed_version',return_value=rekordbox_tags.NATIVE_VERSION))
        self.enterContext(mock.patch('desktop.metadata_controller.assert_rekordbox_closed'))
        self.w=Window(self.root/'data');self.addCleanup(self.w.deleteLater)
        self.w.lib=rekordbox_db.load_live(self.root/'source/master.db',resolve_media_paths=False)
        self.tags=TagEditController(self.root/'data',self.w)
        self.tags.lib=self.w.lib;self.tags.verified=True
        self.tags._recover_pending=mock.Mock()
        self.w.tag_controller=self.tags
        self.writer=native.SourceMetadataStore(native.MetadataAuthority(str(self.w.lib.source),self.w.lib.source_library_id,self.schema,
            True,native.ALBUM_PROFILE),self.root/'data')
        self.reader=NativeReaderProcess(self.root/'reader');self.reader.start();self.addCleanup(self.reader.close)
        self.reads=0;self.applies=0;self.published=[]
        def read(_source):
            self.reads+=1;return self.reader.read_metadata(self.w.lib)
        self.controller=MetadataController(self.w,store_factory=lambda _:self.writer,loader=read,batch_size=8)
        self.w.metadata_controller=self.controller
        self.w._state_repository=StateRepository(self.root/'state/library.json');self.w._state_repository.load()
        def save():
            self.w._state_repository.save(dict(settings=self.w.settings,title_overrides=self.w.titles,
                artist_overrides=self.w.overrides,artist_aliases=self.w.aliases))
            return True
        self.w._save_state=save
        self.w._state_publication=False
        self.coordinator=StateCommitter(self.w)
        self.w._commit_metadata_state=self.coordinator.submit
        self.w._publish_metadata_library=self.publish
        self.addCleanup(self.cleanup)
        descriptor,targets=source_truth.build(source_truth.capture(self.w.lib,titles={},artists={},aliases={},settings={}))
        self.w.settings.update({source_truth.MIGRATION:descriptor,source_truth.TARGETS:targets})
        self.w._state_repository.save(dict(settings=self.w.settings,title_overrides={},artist_overrides={},artist_aliases={}))
        original=self.writer.apply
        def apply(request):
            saved=read_state(self.w._state_repository.path)['settings'][KEY]
            self.assertTrue(any(job['request']==request for job in saved))
            self.applies+=1;return original(request)
        self.enterContext(mock.patch.object(self.writer,'apply',side_effect=apply))

    def publish(self,library,changed_ids):
        saved=read_state(self.w._state_repository.path)
        self.assertEqual(saved['settings'][KEY],self.w.settings[KEY])
        self.assertEqual(saved['settings'][ACKS],self.w.settings[ACKS])
        self.published.append(tuple(changed_ids));self.w.lib=library

    def cleanup(self):
        self.w._shutting_down=True;self.controller.timer.stop()
        self.tags.owner.drain();self.coordinator.owner.drain();self.controller.stop()

    def wait(self,predicate,seconds=20):
        loop=QEventLoop();poll=QTimer();poll.setInterval(10)
        poll.timeout.connect(lambda:loop.quit() if predicate() else None)
        timeout=QTimer();timeout.setSingleShot(True);timeout.timeout.connect(loop.quit)
        poll.start();timeout.start(int(seconds*1000))
        if not predicate():loop.exec()
        poll.stop();timeout.stop()
        self.assertTrue(predicate(),str(self.w.settings.get(KEY))+' / '+self.tags.metadata_update_status)

    def done(self):
        return (not self.controller.pending and not self.w.settings.get(ACKS) and not self.tags.owned
            and not self.coordinator.pending and not self.controller.tasks.pending
            and self.w.settings[source_truth.MIGRATION]['cursor']==self.track_count)

    def manual_idle(self):
        return (not self.controller.pending and not self.w.settings.get(ACKS) and not self.tags.owned
            and not self.coordinator.pending and not self.controller.tasks.pending)

    def choose_manual_mode(self,mode):
        self.w.settings={key:value for key,value in self.w.settings.items()
            if key not in (source_truth.MIGRATION,source_truth.TARGETS)}
        self.w.settings=metadata_cleanup_policy.with_choice(self.w.settings,self.w.lib,mode)
        self.w.lib.metadata_cleanup_enabled=mode=='clean';self.w.lib.artist_registry=None
        self.w._save_state()

    def test_first_keep_title_edit_ack_and_clear_restore_exact_raw_title_with_cold_registry(self):
        self.choose_manual_mode('original');raw=self.w.lib.tracks_by_id['1'].name
        self.assertTrue(self.controller.enqueue('1',{'title':'Manual title'}));self.wait(self.manual_idle)
        self.assertEqual(self.w.lib.tracks_by_id['1'].name,'Manual title')
        self.assertTrue(self.controller.enqueue('1',{'title':'','artist':''}))
        self.assertEqual({row['field']:row['value'] for row in self.controller.pending},{'title':raw,'artist':''})
        self.wait(self.manual_idle)
        self.assertEqual((self.w.lib.tracks_by_id['1'].name,self.w.lib.tracks_by_id['1'].artist),(raw,''))

    def test_first_keep_artist_edit_ack_and_clear_do_not_clean_or_infer_either_raw_field(self):
        self.choose_manual_mode('original');raw=self.w.lib.tracks_by_id['1'].name
        self.assertTrue(self.controller.enqueue('1',{'artist':'Manual artist'}));self.wait(self.manual_idle)
        self.assertEqual((self.w.lib.tracks_by_id['1'].name,self.w.lib.tracks_by_id['1'].artist),(raw,'Manual artist'))
        self.assertTrue(self.controller.enqueue('1',{'artist':''}))
        self.assertEqual([(row['field'],row['value']) for row in self.controller.pending],[('artist','')])
        self.wait(self.manual_idle)
        self.assertEqual((self.w.lib.tracks_by_id['1'].name,self.w.lib.tracks_by_id['1'].artist),(raw,''))

    def test_keep_literal_unknown_native_artist_survives_title_edit_and_artist_reset(self):
        from soundbox.metadata import UNKNOWN_ARTIST
        self.choose_manual_mode('original')
        conn=_connect(self.writer.database)
        try:
            row={item[1]:(0 if 'INT' in item[2].upper() else '') for item in conn.execute('PRAGMA table_info(djmdArtist)')}
            row.update(ID='900',Name=UNKNOWN_ARTIST,UUID='00000000-0000-4000-8000-000000000900',usn=None,rb_local_usn=40)
            conn.execute('INSERT INTO djmdArtist('+','.join('"'+key+'"' for key in row)+') VALUES ('+
                ','.join('?' for _ in row)+')',tuple(row.values()))
            conn.execute("UPDATE djmdContent SET ArtistID='900' WHERE ID='1'")
        finally:conn.close()
        self.w.lib=self.reader.read_metadata(self.w.lib);self.tags.lib=self.w.lib
        self.assertTrue(self.controller.enqueue('1',{'title':'Manual title'}))
        self.assertEqual([row['field'] for row in self.controller.pending],['title'])
        self.wait(self.manual_idle)
        self.assertEqual(self.w.lib.tracks_by_id['1'].artist,UNKNOWN_ARTIST)
        self.assertTrue(self.controller.enqueue('1',{'artist':'Manual artist'}));self.wait(self.manual_idle)
        self.assertTrue(self.controller.enqueue('1',{'artist':''}))
        self.assertEqual(self.controller.pending[0]['value'],UNKNOWN_ARTIST)
        self.wait(self.manual_idle)
        self.assertEqual(self.w.lib.tracks_by_id['1'].artist,UNKNOWN_ARTIST)

    def test_inline_artist_intention_votes_manual_values_once_per_track_before_native_capture(self):
        self.choose_manual_mode('clean')
        conn=_connect(self.writer.database)
        try:
            row={item[1]:(0 if 'INT' in item[2].upper() else '') for item in conn.execute('PRAGMA table_info(djmdArtist)')}
            row.update(ID='900',Name='NOVA',UUID='00000000-0000-4000-8000-000000000900',usn=None,rb_local_usn=40)
            conn.execute('INSERT INTO djmdArtist('+','.join('"'+key+'"' for key in row)+') VALUES ('+
                ','.join('?' for _ in row)+')',tuple(row.values()))
            conn.execute("UPDATE djmdContent SET ArtistID='900'")
        finally:conn.close()
        self.w.lib=self.reader.read_metadata(self.w.lib);self.tags.lib=self.w.lib
        self.w.overrides={'2':'nova','3':'nova'};self.w._save_state()
        self.assertTrue(self.controller.enqueue('1',{'artist':'nova'}))
        self.assertEqual(next(row['value'] for row in self.controller.pending if row['field']=='artist'),'nova')
        self.wait(self.manual_idle)
        self.assertEqual(self.w.lib.tracks_by_id['1'].artist,'nova')

    def test_six_existing_single_field_writes_share_one_complete_readback_and_durable_acks(self):
        self.controller.schedule();self.wait(self.done)
        self.assertEqual((self.applies,self.reads,len(self.published)),(6,1,1))
        self.assertEqual(set(self.published[0]),{'1','2','3'})
        for index,track in self.w.lib.tracks_by_id.items():
            self.assertEqual((track.name,track.artist),(f'Song {index}','Artist'))
        self.assertEqual(read_state(self.w._state_repository.path)['settings'][source_truth.MIGRATION]['cursor'],3)
        self.assertFalse(self.tags.source_ack_pending)

    def test_group_ack_uses_worker_selected_rows_without_gui_library_inventory_iteration(self):
        import threading
        from soundbox.library import Track
        gui=threading.get_ident();original=self.controller.loader
        class Unrelated(Track):
            @property
            def path(self):raise AssertionError('Grouped readback decoded an unrelated path')
        class WorkerInventory(dict):
            def check(self):
                if threading.get_ident()==gui:raise AssertionError('Grouped ACK walked the Library on the GUI thread')
            def items(self):self.check();return super().items()
            def values(self):self.check();return super().values()
            def keys(self):self.check();return super().keys()
            def __iter__(self):self.check();return super().__iter__()
        def read(source):
            result=original(source)
            result.tracks_by_id['unrelated']=Unrelated('unrelated','','','unavailable-uri',
                source_native_id='99',source_uuid='unrelated-uuid')
            result.tracks_by_id=WorkerInventory(result.tracks_by_id)
            return result
        self.controller.loader=read
        self.controller.schedule();self.wait(self.done)
        self.assertEqual((self.applies,self.reads,len(self.published)),(6,1,1))
        self.assertEqual(self.w.lib.tracks_by_id.get('1').name,'Song 1')

    def test_partial_native_failure_keeps_all_intentions_and_restart_replays_existing_receipts(self):
        original=self.writer.apply;failed=False
        def fail(request):
            nonlocal failed
            if self.applies==2 and not failed:
                failed=True;raise OSError('generated third-write failure')
            return original(request)
        with mock.patch.object(self.writer,'apply',side_effect=fail):
            self.controller.schedule();self.wait(lambda:any(job['error'] for job in self.controller.pending))
        self.assertEqual(self.applies,2);self.assertEqual(self.reads,0)
        self.assertFalse(self.tags.source_ack_pending)

        saved=read_state(self.w._state_repository.path)['settings'][KEY]
        self.assertEqual(len(saved),6);self.assertTrue(all(job['request'] for job in saved[:3]))
        self.assertEqual(len(list((self.writer.root/'metadata-receipts').glob('*.json'))),2)
        self.wait(lambda:not self.tags.owner.pending and not self.coordinator.pending)
        # A new controller/store reloads durable intentions without any RAM group,
        # planner proof or previous-store recovery witness.
        self.controller.stop()
        self.w.settings=read_state(self.w._state_repository.path)['settings']
        self.w.lib=self.reader.read_metadata(self.w.lib);self.tags.lib=self.w.lib
        self.writer=native.SourceMetadataStore(self.writer.authority,self.root/'data')
        def read(_source):
            self.reads+=1;return self.reader.read_metadata(self.w.lib)
        self.controller=MetadataController(self.w,store_factory=lambda _:self.writer,loader=read,batch_size=1)
        self.w.metadata_controller=self.controller
        self.assertTrue(self.controller.retry());self.wait(self.done)
        self.assertEqual(len(list((self.writer.root/'metadata-receipts').glob('*.json'))),6)
        self.assertFalse(self.tags.source_ack_pending)

    def test_final_readback_failure_acknowledges_nothing_and_retains_every_committed_request(self):
        original=self.controller.loader
        self.controller.loader=mock.Mock(side_effect=OSError('generated readback failure'))
        self.controller.schedule();self.wait(lambda:any(row['error'] for row in self.controller.pending))
        self.assertEqual((self.applies,self.reads,len(self.published)),(6,0,0))
        self.assertEqual(len(read_state(self.w._state_repository.path)['settings'][KEY]),6)
        self.assertEqual(len(list((self.writer.root/'metadata-receipts').glob('*.json'))),6)
        self.assertFalse(self.tags.source_ack_pending)
        self.controller.loader=original;self.controller.batch_size=1
        self.wait(lambda:not self.tags.owner.pending and not self.coordinator.pending)
        self.assertTrue(self.controller.retry());self.wait(self.done)

    def test_request_storage_failure_cannot_run_its_native_mutation_or_lose_prior_receipt(self):
        original=self.w._state_repository.commit_patch
        def commit(patch):
            replacements,_=patch.edits();queue=replacements.get(('settings',KEY),[])
            if queue and len(queue)>1 and queue[1]['request'] is not None:
                raise OSError('generated request storage failure')
            return original(patch)
        with mock.patch.object(self.w._state_repository,'commit_patch',side_effect=commit):
            self.controller.schedule();self.wait(lambda:bool(self.controller._state_commit_error))
        self.assertEqual((self.applies,self.reads,len(self.published)),(1,0,0))
        saved=read_state(self.w._state_repository.path)['settings'][KEY]
        self.assertIsNotNone(saved[0]['request']);self.assertIsNone(saved[1]['request'])
        self.assertEqual(len(list((self.writer.root/'metadata-receipts').glob('*.json'))),1)
        self.assertFalse(self.tags.source_ack_pending)

    def test_group_ack_owner_cannot_authorize_other_domain_or_borrowed_metadata_token(self):
        token=object();self.tags.reserve_source_ack(token)
        try:
            self.assertFalse(self.tags._run_source_job('grid',lambda:None,lambda _:None,source_ack_token=token))
            self.assertFalse(self.tags._run_source_job('metadata',lambda:None,lambda _:None,source_ack_token=object()))
            self.assertFalse(self.tags._run_source_job('metadata',lambda:None,lambda _:None))
            self.assertFalse(self.tags.busy)
        finally:self.tags.release_source_ack(token)

    def test_group_ack_storage_failure_preserves_all_native_receipts_before_any_publication(self):
        original=self.w._state_repository.commit_patch
        def commit(patch):
            replacements,_=patch.edits();queue=replacements.get(('settings',KEY))
            if queue is not None and len(queue)==0:
                raise OSError('generated acknowledgement storage failure')
            return original(patch)
        with mock.patch.object(self.w._state_repository,'commit_patch',side_effect=commit):
            self.controller.schedule();self.wait(lambda:bool(self.controller._state_commit_error))
        self.assertEqual((self.applies,self.reads,len(self.published)),(6,1,0))
        self.assertEqual(len(read_state(self.w._state_repository.path)['settings'][KEY]),6)
        self.assertEqual(len(list((self.writer.root/'metadata-receipts').glob('*.json'))),6)
        self.assertFalse(self.tags.source_ack_pending)
        self.wait(lambda:not self.tags.owner.pending and not self.coordinator.pending)
        self.assertTrue(self.controller.retry());self.wait(self.done)

    def test_group_receipt_failure_retires_only_proven_prefix_and_retries_remaining_markers(self):
        original=native.retire_acknowledged_receipt;attempts=0
        def retire(root,entry):
            nonlocal attempts
            attempts+=1
            if attempts==3:raise OSError('generated receipt failure')
            return original(root,entry)
        with mock.patch.object(native,'retire_acknowledged_receipt',side_effect=retire):
            self.controller.schedule()
            self.wait(lambda:bool(self.w.settings.get(ACKS)) and bool(self.w.settings[ACKS][0]['error']))
        self.assertEqual((self.applies,self.reads,len(self.published)),(6,1,1))
        saved=read_state(self.w._state_repository.path)['settings']
        self.assertEqual(len(saved[ACKS]),4);self.assertEqual(saved[KEY],[])
        formats=[native._read_json(path)['format'] for path in (self.writer.root/'metadata-receipts').glob('*.json')]
        self.assertEqual(formats.count(native.ACK_FORMAT),2)
        self.wait(lambda:not self.coordinator.pending and not self.controller.tasks.pending)
        self.assertTrue(self.controller.retry());self.wait(self.done)
        self.assertEqual(self.applies,6)

    def test_unexpected_second_ack_transform_failure_restores_all_original_gui_roots(self):
        original=self.controller._stage_ack_source_curation;calls=0
        def stage(*args,**kwargs):
            nonlocal calls
            calls+=1
            if calls==2:raise RuntimeError('generated pure staging failure')
            return original(*args,**kwargs)
        with mock.patch.object(self.controller,'_stage_ack_source_curation',side_effect=stage):
            self.controller.schedule();self.wait(lambda:any(row['error'] for row in self.controller.pending))
        self.assertEqual((self.applies,self.reads,len(self.published)),(6,1,0))
        self.assertFalse(self.tags.source_ack_pending)
        self.assertNotIn(source_truth.CURATION,self.w.settings)
        saved=read_state(self.w._state_repository.path)['settings']
        self.assertEqual(len(saved[KEY]),6);self.assertNotIn(ACKS,saved)
        self.assertEqual(len(saved[source_truth.MIGRATION]['active']['operations']),2)

    def test_source_switch_during_shared_readback_retires_owner_without_acknowledging_other_library(self):
        from dataclasses import replace
        from PySide6.QtCore import QObject, Signal
        from threading import Event
        class Bridge(QObject):ready=Signal()
        bridge=Bridge();changed=Event()
        def switch():
            self.w.lib=replace(self.w.lib,source=self.root/'different/master.db',source_library_id='different')
            changed.set()
        bridge.ready.connect(switch)
        original=self.controller.loader
        def read(source):
            result=original(source)
            bridge.ready.emit()
            if not changed.wait(5):raise RuntimeError('Generated source switch did not finish')
            return result
        self.controller.loader=read
        self.controller.schedule();self.wait(lambda:any(row['error'] for row in self.controller.pending))
        self.assertEqual((self.applies,self.reads,len(self.published)),(6,1,0))
        self.assertFalse(self.tags.source_ack_pending)
        self.assertEqual(self.w.lib.source_library_id,'different')
        self.assertEqual(len(read_state(self.w._state_repository.path)['settings'][KEY]),6)

    def test_feature_title_and_primary_artist_both_persist_without_losing_frozen_credit(self):
        import json
        from soundbox import alias_intents, metadata_provenance
        conn=_connect(self.writer.database)
        try:conn.execute("UPDATE djmdContent SET Title='Headliner feat. Guest - Song 1' WHERE ID='1'")
        finally:conn.close()
        self.w.lib=self.reader.read_metadata(self.w.lib);self.tags.lib=self.w.lib
        raw=source_truth.capture(self.w.lib,titles={},artists={},aliases={},settings={})
        descriptor,targets=source_truth.build(raw)
        origins=alias_intents.origins(json.loads(raw),{},{} )
        for key,row in targets.items():
            row['curation']={field:metadata_provenance.snapshot(origins[key],field,{}) for field in ('title','artist')}
        self.w.settings.update({source_truth.MIGRATION:descriptor,source_truth.TARGETS:targets,
            alias_intents.ORIGINS:origins});self.w._save_state()
        self.controller.schedule();self.wait(self.done)
        track=self.w.lib.tracks_by_id['1']
        self.assertEqual((track.name,track.artist),('Song 1 (feat. Guest)','Headliner'))
        saved=read_state(self.w._state_repository.path)
        origin=next(row for row in saved['settings'][alias_intents.ORIGINS].values() if row['native_id']=='1')
        self.assertEqual(origin['provenance']['raw'],{'title':'Headliner feat. Guest - Song 1','artist':''})
        self.assertEqual((origin['native_title'],origin['native_artist']),(track.name,track.artist))

    def test_late_same_field_intention_is_preserved_after_shared_readback_and_saved_next(self):
        # The complete readback is held until the GUI accepts the later edit.
        from PySide6.QtCore import QObject, Signal
        from threading import Event
        class Bridge(QObject):ready=Signal()
        bridge=Bridge();released=Event();original=self.controller.loader
        def later():
            self.assertTrue(self.controller.enqueue('1',{'title':'Later explicit title'}))
            released.set()
        bridge.ready.connect(later)
        once=False
        def load(source):
            nonlocal once
            if not once:
                once=True;bridge.ready.emit()
                if not released.wait(5):raise RuntimeError('Generated GUI edit did not finish')
            return original(source)
        self.controller.loader=load
        self.controller.schedule();self.wait(self.done)
        self.assertEqual(self.w.lib.tracks_by_id['1'].name,'Later explicit title')
        self.assertEqual(self.writer.capture_request('1',self.w.lib.tracks_by_id['1'].source_uuid,
            '/synthetic/song-1.wav','title','Later explicit title')['expected'],'Later explicit title')
