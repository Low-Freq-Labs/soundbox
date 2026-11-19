"""Folder deletion composes accepted leaves on encrypted TEMP catalogs only."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from test_playlist_transaction import fixture
from soundbox import rekordbox_playlist_store as store, rekordbox_playlist_xml as xml
from soundbox import rekordbox_playlist_plan as planner, rekordbox_cue_store as cues
from soundbox.rekordbox_tags import _schema, NATIVE_VERSION
from soundbox.rekordbox_write import _connect, _rows, _state, ConflictError, WriteProofError


def native_uuid(number):return f'00000000-0000-4000-8000-{number:012d}'


class PlaylistTreeDeletion(unittest.TestCase):
    def setUp(self):
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        source=self.root/'source';source.mkdir();fixture(source)
        self.database=source/'master.db';self.xml=source/'masterPlaylists6.xml'
        conn=_connect(self.database)
        try:
            template=_rows(conn,'djmdPlaylist')[0];columns=list(template)
            for identity,parent,seq,attribute,synced in (
                    (2000,'root',2,1,0),(2100,'2000',1,1,1),(2200,'2100',1,0,0),
                    (2300,'2100',2,4,0),(2400,'2000',2,4,1),(3000,'root',3,0,1)):
                row={**template,'ID':str(identity),'UUID':native_uuid(identity),'ParentID':parent,'Seq':seq,
                     'Attribute':attribute,'Name':str(identity),'rb_local_synced':synced,
                     'SmartList':planner.smart_tag_rule(str(identity),['1']) if attribute==4 else None}
                conn.execute(f'INSERT INTO djmdPlaylist ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})',
                             [row[key] for key in columns])
            conn.commit();schema=_schema(conn)
        finally:conn.close()
        payload=self.xml.read_bytes()
        for identity,parent,attribute in ((2000,'root',1),(2100,'2000',1),(2200,'2100',0),
                                           (2300,'2100',4),(2400,'2000',4),(3000,'root',0)):
            payload=(xml.append_smart_node(payload,str(identity),parent) if attribute==4
                     else xml.append_node(payload,str(identity),parent,attribute))
        self.xml.write_bytes(payload)
        for name,value in (('_closed',None),('_installed_version',NATIVE_VERSION)):
            self.enterContext(mock.patch.object(cues,name,return_value=value))
        self.authority=store.PlaylistAuthority(str(self.database),'synthetic-playlists',schema,True,
            smart_delete_accepted=True,smart_delete_profile=store.SMART_LOCAL_DELETE_PROFILE,retain_full_backups=True)
        self.writer=store.PlaylistStore(self.authority,self.root/'recovery')
        tracks=[SimpleNamespace(track_id='track',source_uuid='uuid'),SimpleNamespace(track_id='track-two',source_uuid='uuid-two')]
        self.writer.apply(store.request_for(self.writer.snapshot(),'append',identity='2200',tracks=tracks))

    def request(self):return store.request_for(self.writer.snapshot(),'delete_tree',identity='2000')

    def state(self):
        conn=_connect(self.database,readonly=True)
        try:return _state(conn)
        finally:conn.close()

    def test_complete_envelope_matches_independent_sequential_native_primitives(self):
        request=self.request();before=self.state();before_xml=self.xml.read_bytes()
        self.assertEqual([child['playlist_id'] for child in request['requests']],['2200','2300','2100','2400','2000'])
        work=self.root/'sequential.db';shutil.copyfile(self.database,work)
        conn=_connect(work)
        try:
            payload=before_xml;sequential=[]
            for child in request['requests']:
                current={**deepcopy(child),'revision':store.catalog(conn)['revision']}
                plan=store.build_plan(conn,current,payload);sequential.extend(plan['changes']);payload=plan['xml']
                conn.execute('BEGIN IMMEDIATE');conn.set_authorizer(store.authorizer)
                store.apply_changes(conn,plan['changes']);conn.set_authorizer(None);conn.commit()
            expected=_state(conn)
        finally:conn.close()
        conn=_connect(self.database,readonly=True)
        try:atomic=store.build_plan(conn,request,before_xml)
        finally:conn.close()
        self.assertEqual(atomic['changes'],sequential)
        self.assertEqual(atomic['xml'],payload)
        pins=self.writer.prepare_collection_pins(request)
        self.assertEqual(pins,{'track':'uuid','track-two':'uuid-two'})
        self.writer.apply(request,collection_pins=pins)
        self.assertEqual(self.state(),expected);self.assertEqual(self.xml.read_bytes(),payload)
        for table,rows in before['tables'].items():
            if table not in ('djmdPlaylist','djmdSongPlaylist','agentRegistry'):
                self.assertEqual(self.state()['tables'][table],rows,table)
        exact=self.database.read_bytes(),self.xml.read_bytes()
        self.writer.apply(request,collection_pins=pins)
        self.assertEqual((self.database.read_bytes(),self.xml.read_bytes()),exact)
        conn=_connect(self.database,readonly=True)
        try:
            self.assertIsNone(store.row(conn,'djmdPlaylist','2000'))
            self.assertEqual(store.row(conn,'djmdPlaylist','2400')['rb_local_deleted'],1)
            self.assertEqual(store.row(conn,'djmdPlaylist','2100')['rb_local_deleted'],1)
            self.assertEqual(store.row(conn,'djmdPlaylist','3000')['Seq'],2)
        finally:conn.close()
        self.assertTrue((self.root/'recovery'/('playlist-'+request['operation_id'])/'before.db').exists())
        self.assertFalse(any((self.root/'recovery'/('playlist-'+child['operation_id'])).exists() for child in request['requests']))

    def test_every_smart_child_requires_its_own_profile_before_source_admission(self):
        request=self.request();before=self.database.read_bytes(),self.xml.read_bytes()
        for authority in (replace(self.authority,smart_delete_accepted=False),
                          replace(self.authority,smart_delete_profile=store.SMART_DELETE_PROFILE)):
            writer=store.PlaylistStore(authority,self.root/'other-recovery')
            with mock.patch.object(writer.lease,'_lock',side_effect=AssertionError('Entered source lane')):
                with self.assertRaises(WriteProofError):writer.apply(request)
        self.assertEqual((self.database.read_bytes(),self.xml.read_bytes()),before)

    def test_new_descendant_or_reused_uuid_cannot_enter_retry_or_rebased_scope(self):
        request=self.request();current=self.writer.snapshot()
        added={**current['nodes'][0],'ID':'999','UUID':native_uuid(999),'ParentID':'2000','Seq':3}
        for changed in (dict(current,nodes=[*current['nodes'],added]),
                        dict(current,nodes=[{**row,'UUID':native_uuid(888)} if row['ID']=='2300' else row for row in current['nodes']])):
            changed['revision']=planner.revision(changed['nodes'],changed['members'])
            with self.assertRaises(ConflictError):store.renew_tree_delete_request(request,changed)
        renewed=store.renew_tree_delete_request(request,current)
        self.assertEqual(renewed['operation_id'],request['operation_id'])
        self.assertEqual([row['operation_id'] for row in renewed['requests']],
                         [row['operation_id'] for row in request['requests']])

    def test_interrupted_tree_recovers_exact_pair_and_retry_persists_new_child_clocks(self):
        request=self.request();pins=self.writer.prepare_collection_pins(request)
        before=self.state(),self.xml.read_bytes()
        def interrupt(phase):
            if phase=='sql-applied':raise RuntimeError('tree interrupted')
        with mock.patch.object(store,'_fault',side_effect=interrupt):
            with self.assertRaises(RuntimeError):self.writer.apply(request,collection_pins=pins)
        self.assertEqual(self.writer.recover(),'rolled-back')
        self.assertEqual((self.state(),self.xml.read_bytes()),before)
        renewed=self.writer.prepare_retry(request)
        self.assertNotEqual(renewed['operation_id'],request['operation_id'])
        self.assertEqual(renewed['descendants'],request['descendants'])
        pins=self.writer.prepare_collection_pins(renewed)
        def committed(phase):
            if phase=='sql-committed':raise RuntimeError('tree committed')
        with mock.patch.object(store,'_fault',side_effect=committed):
            with self.assertRaises(RuntimeError):self.writer.apply(renewed,collection_pins=pins)
        self.assertEqual(self.writer.recover(),'committed')
        self.assertEqual(self.writer.prepare_retry(renewed),renewed)
        self.assertFalse(any(row['ID'] in {'2000','2100','2200','2300','2400'} and row['rb_local_deleted']==0
                             for row in self.writer.snapshot()['nodes']))

    def test_new_playlist_parent_uuid_survives_retry_revision_refresh(self):
        request=store.request_for(self.writer.snapshot(),'create',name='New child',parent='2000')
        self.assertEqual(request['parent_uuid'],native_uuid(2000))
        conn=_connect(self.database)
        try:
            conn.execute('UPDATE djmdPlaylist SET UUID=? WHERE ID=?',(native_uuid(999),'2000'));conn.commit()
        finally:conn.close()
        renewed=self.writer.prepare_retry(request)
        self.assertEqual(renewed['parent_uuid'],native_uuid(2000))
        before=self.database.read_bytes(),self.xml.read_bytes()
        with self.assertRaisesRegex(ConflictError,'folder'):self.writer.apply(renewed)
        self.assertEqual((self.database.read_bytes(),self.xml.read_bytes()),before)
        legacy={key:value for key,value in request.items() if key!='parent_uuid'}
        store.validate_request(legacy)

    def test_definitely_unstarted_archive_can_fork_after_staging_failure(self):
        import json
        request=store.request_for(self.writer.snapshot(),'rename',identity='1000',name='After staging')
        before=self.database.read_bytes(),self.xml.read_bytes()
        def interrupted(phase):
            if phase=='archive-prepared':raise RuntimeError('staging stopped before source admission')
        with mock.patch.object(store,'_fault',side_effect=interrupted):
            with self.assertRaises(RuntimeError):self.writer.apply(request)
        archive=self.writer.root/('playlist-'+request['operation_id'])
        self.assertEqual(json.loads((archive/'result.json').read_text())['outcome'],'not-started')
        self.assertFalse(self.writer.journal.exists())
        self.assertEqual((self.database.read_bytes(),self.xml.read_bytes()),before)
        renewed=self.writer.prepare_retry(request)
        self.assertNotEqual(renewed['operation_id'],request['operation_id'])
        self.writer.apply(renewed)
        self.assertEqual(next(row['Name'] for row in self.writer.snapshot()['nodes'] if row['ID']=='1000'),'After staging')

    def test_prepublication_cas_failure_records_no_source_effect_and_keeps_retryable_intent(self):
        request=store.request_for(self.writer.snapshot(),'rename',identity='1000',name='After conflict')
        before=self.database.read_bytes(),self.xml.read_bytes()
        with mock.patch.object(store,'_matches',return_value=False):
            with self.assertRaises(ConflictError):self.writer.apply(request)
        self.assertFalse(self.writer.journal.exists())
        self.assertEqual((self.database.read_bytes(),self.xml.read_bytes()),before)
        retry=self.writer.prepare_retry(request);self.writer.apply(retry)
        self.assertEqual(next(row['Name'] for row in self.writer.snapshot()['nodes'] if row['ID']=='1000'),'After conflict')

    def test_malformed_tree_cannot_drop_bindings_or_inject_nondelete_commands(self):
        request=self.request()
        for mutate in (lambda value:value['descendants'].pop(),
                       lambda value:value['requests'].reverse(),
                       lambda value:value['requests'][0].update(operation='rename'),
                       lambda value:value['requests'][0].update(operation_id=value['operation_id'])):
            changed=deepcopy(request);mutate(changed)
            with self.subTest(changed=changed),self.assertRaises(WriteProofError):store.validate_request(changed)


class TreeDeletionGestures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        from test_ui_source_playlists import Window
        from desktop.rekordbox_playlists import PlaylistEditController
        from soundbox.library import Playlist
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.window=Window(self.root)
        self.controller=PlaylistEditController(self.window,store_factory=lambda _lib:None)
        self.window.playlist_controller=self.controller
        self.addCleanup(self.window.deleteLater);self.addCleanup(self.window.close)
        self.addCleanup(self.window.tag_controller.shutdown);self.addCleanup(self.controller.stop)
        node=self.window.lib.source_playlist_catalog['nodes'][0]
        node.update(Attribute=1,SmartList=None,rb_data_status=0,rb_local_data_status=0,rb_local_synced=0,usn=None,rb_local_usn=1)
        children=[{**node,'ID':str(identity),'UUID':native_uuid(identity),'ParentID':'1000','Seq':seq,
                   'Attribute':attribute,'SmartList':planner.smart_tag_rule(str(identity),['1']) if attribute==4 else None}
                  for identity,seq,attribute in ((2000,1,0),(3000,2,4))]
        catalog=self.window.lib.source_playlist_catalog;catalog['nodes']=[node,*children]
        catalog['revision']=planner.revision(catalog['nodes'],[])
        self.window.lib.playlists=[Playlist('2000',['List'],[],'2000'),Playlist('3000',['List'],[],'3000')]

    def test_advertised_folder_delete_captures_whole_tree_before_any_worker(self):
        from desktop.rekordbox_playlists import QMessageBox
        before=deepcopy(self.window.lib.source_playlist_catalog)
        with mock.patch.object(self.controller,'smart_delete_reason',return_value=''), \
                mock.patch.object(QMessageBox,'question',return_value=QMessageBox.Yes):
            self.window._native_delete('1000')
        request=self.controller.pending[0]['request']
        self.assertEqual(request['operation'],'delete_tree')
        self.assertEqual([row['id'] for row in request['descendants']],['2000','3000','1000'])
        self.window._save_state.assert_called_once()
        self.assertEqual(self.window.lib.source_playlist_catalog,before)
        self.assertEqual(self.window._native_presentation().playlists,[])
        self.assertEqual(set(self.window.lib.tracks_by_id),{'one'})

    def test_confirmation_cannot_absorb_new_descendants_and_projection_preserves_them(self):
        from desktop.rekordbox_playlists import QMessageBox
        catalog=self.window.lib.source_playlist_catalog
        captured=store.tree_delete_for(catalog,'1000')
        def added(*_args):
            catalog['nodes'].append({**catalog['nodes'][1],'ID':'4000','UUID':native_uuid(4000),'Seq':3})
            catalog['revision']=planner.revision(catalog['nodes'],catalog['members'])
            return QMessageBox.Yes
        with mock.patch.object(self.controller,'smart_delete_reason',return_value=''), \
                mock.patch.object(QMessageBox,'question',side_effect=added):
            self.window._native_delete('1000')
        self.assertEqual(self.controller.pending,[]);self.window._save_state.assert_not_called()
        # Even a bad producer retaining the old revision cannot hide an extra
        # descendant through the presentation envelope.
        catalog['revision']=captured['revision']
        self.window.settings['rekordbox_playlist_edits']=[dict(source=str(self.window.lib.source),
            library_id=self.window.lib.source_library_id,request=captured,error='',retry=False)]
        self.assertIn('4000',{row['ID'] for row in self.window._native_presentation().source_playlist_catalog['nodes']})

    def test_metadata_and_assignment_retries_preserve_errors_and_guard_physical_worker(self):
        metadata=SimpleNamespace(pending=[dict(error='exact metadata conflict')],retry=mock.Mock(),
                                 background_paused=False)
        tags=SimpleNamespace(pending=[dict(error='exact assignment conflict')],retry=mock.Mock(),
                             busy=False,checking=False,owned=True,owner=SimpleNamespace(pending=0))
        self.window.metadata_controller=metadata
        with mock.patch.object(self.window,'tag_controller',tags):
            menu=self.window._native_menu(None)
            try:
                actions={action.text():action for action in menu.actions()}
                for label,error in (('Retry metadata saves','exact metadata conflict'),
                                    ('Retry My Tag assignments','exact assignment conflict')):
                    self.assertTrue(actions[label].isEnabled());self.assertEqual(actions[label].toolTip(),error)
                    actions[label].trigger()
            finally:menu.deleteLater()
            self.assertEqual(metadata.pending[0]['error'],'exact metadata conflict')
            self.assertEqual(tags.pending[0]['error'],'exact assignment conflict')
            metadata.retry.assert_called_once();tags.retry.assert_called_once()
            tags.owner.pending=1;menu=self.window._native_menu(None)
            try:
                actions={action.text():action for action in menu.actions()}
                self.assertFalse(actions['Retry metadata saves'].isEnabled())
                self.assertFalse(actions['Retry My Tag assignments'].isEnabled())
            finally:menu.deleteLater()
