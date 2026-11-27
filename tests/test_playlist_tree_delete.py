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

    def ordinary_request(self):
        conn=_connect(self.database)
        try:
            conn.execute('UPDATE djmdPlaylist SET Attribute=0,SmartList=NULL WHERE Attribute=4');conn.commit()
        finally:conn.close()
        self.xml.write_bytes(self.xml.read_bytes().replace(b'Attribute="4"',b'Attribute="0"'))
        return store.request_for(self.writer.snapshot(),'delete',identity='2000')

    def test_ordinary_confirmation_keeps_exact_legacy_native_multinode_protocol(self):
        request=self.ordinary_request()
        legacy={key:value for key,value in request.items() if key not in store._FOLDER_DELETE_FIELDS}
        store.validate_request(legacy)
        conn=_connect(self.database,readonly=True)
        try:
            with mock.patch.object(planner,'delete',wraps=planner.delete) as native:
                guarded=store.build_plan(conn,request,self.xml.read_bytes())
            native.assert_called_once()
            original=store.build_plan(conn,legacy,self.xml.read_bytes())
        finally:conn.close()
        self.assertEqual(guarded,original)
        # One ordinary native primitive, rather than smart-tree child clocks.
        self.assertNotIn('requests',request)
        self.writer.apply(request,collection_pins=self.writer.prepare_collection_pins(request))
        self.assertFalse(any(row['ID'] in {'2000','2100','2200','2300','2400'}
            and row['rb_local_deleted']==0 for row in self.writer.snapshot()['nodes']))

    def test_large_ordinary_folder_keeps_one_native_plan_without_composed_command_capacity_limit(self):
        self.ordinary_request()
        conn=_connect(self.database)
        payload=self.xml.read_bytes()
        try:
            template=store.row(conn,'djmdPlaylist','2200');columns=list(template)
            for offset in range(300):
                identity=str(4000+offset)
                node={**template,'ID':identity,'UUID':native_uuid(4000+offset),'ParentID':'2000','Seq':3+offset}
                conn.execute(f'INSERT INTO djmdPlaylist ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})',
                             [node[key] for key in columns])
                payload=xml.append_node(payload,identity,'2000',0)
            conn.commit()
        finally:conn.close()
        self.xml.write_bytes(payload);current=self.writer.snapshot()
        request=store.request_for(current,'delete',identity='2000')
        self.assertEqual(len(request['delete_descendants']),305)
        legacy={key:value for key,value in request.items() if key not in store._FOLDER_DELETE_FIELDS}
        conn=_connect(self.database,readonly=True)
        try:
            with mock.patch.object(planner,'delete',wraps=planner.delete) as native:
                guarded=store.build_plan(conn,request,payload)
            native.assert_called_once()
            self.assertEqual(guarded,store.build_plan(conn,legacy,payload))
        finally:conn.close()
        # The independently measured smart leaf composition remains bounded.
        with self.assertRaises(WriteProofError):store.tree_delete_for(current,'2000')
        self.writer.apply(request)
        selected={binding['id'] for binding in request['delete_descendants']}
        self.assertFalse(any(row['ID'] in selected and row['rb_local_deleted']==0
                             for row in self.writer.snapshot()['nodes']))

    def test_ordinary_scope_rejects_late_descendant_membership_identity_and_rename(self):
        request=self.ordinary_request();current=self.writer.snapshot()
        mutations=[]
        added=deepcopy(current)
        added['nodes'].append({**current['nodes'][0],'ID':'999','UUID':native_uuid(999),'ParentID':'2000','Seq':3})
        mutations.append(added)
        for field,value in (('UUID',native_uuid(999)),('Name','Changed after confirmation'),('ParentID','root')):
            changed=deepcopy(current)
            next(row for row in changed['nodes'] if row['ID']=='2200')[field]=value
            mutations.append(changed)
        changed=deepcopy(current)
        changed['members'].append({**changed['members'][0],'ID':'late','UUID':native_uuid(999)})
        mutations.append(changed)
        for changed in mutations:
            changed['revision']=planner.revision(changed['nodes'],changed['members'])
            with self.subTest(changed=changed),self.assertRaises((ConflictError,WriteProofError,ValueError)):
                store.renew_ordinary_delete_request(request,changed)
        # A causal unrelated ACK may change native metadata and the global
        # revision, but never the confirmed semantic scope or operation ID.
        unrelated=deepcopy(current)
        next(row for row in unrelated['nodes'] if row['ID']=='3000')['Name']='Unrelated'
        next(row for row in unrelated['nodes'] if row['ID']=='2000')['updated_at']='later native clock'
        unrelated['revision']=planner.revision(unrelated['nodes'],unrelated['members'])
        renewed=store.renew_ordinary_delete_request(request,unrelated)
        for key in (*store._FOLDER_DELETE_FIELDS,'operation_id'):
            self.assertEqual(renewed[key],request[key])
        self.assertEqual(renewed['revision'],unrelated['revision'])

    def test_ordinary_native_plan_cannot_hide_new_scope_behind_old_revision(self):
        request=self.ordinary_request();current=self.writer.snapshot()
        current['nodes'].append({**current['nodes'][0],'ID':'999','UUID':native_uuid(999),'ParentID':'2000','Seq':3})
        conn=_connect(self.database,readonly=True)
        try:
            with mock.patch.object(planner,'delete',side_effect=AssertionError('Unconfirmed native deletion')):
                with self.assertRaises(ConflictError):store._single_plan(conn,request,self.xml.read_bytes(),current=current)
        finally:conn.close()

    def test_ordinary_interruption_restart_and_legacy_journal_receipt_reconstruction(self):
        request=self.ordinary_request();before=self.state(),self.xml.read_bytes()
        def interrupt(phase):
            if phase=='sql-applied':raise RuntimeError('owned folder interruption')
        with mock.patch.object(store,'_fault',side_effect=interrupt):
            with self.assertRaises(RuntimeError):self.writer.apply(request)
        restarted=store.PlaylistStore(self.authority,self.root/'recovery')
        self.assertEqual(restarted.recover(),'rolled-back')
        self.assertEqual((self.state(),self.xml.read_bytes()),before)
        renewed=restarted.prepare_retry(request)
        self.assertNotEqual(renewed['operation_id'],request['operation_id'])
        for key in store._FOLDER_DELETE_FIELDS:self.assertEqual(renewed[key],request[key])
        # Old saved requests and their interrupted journals reconstruct exactly
        # as before; recovering them must never silently add the new marker.
        legacy={key:value for key,value in renewed.items() if key not in store._FOLDER_DELETE_FIELDS}
        with mock.patch.object(store,'_fault',side_effect=interrupt):
            with self.assertRaises(RuntimeError):restarted.apply(legacy)
        self.assertEqual(restarted.recover(),'rolled-back')
        legacy=restarted.prepare_retry(legacy)
        self.assertFalse(store._FOLDER_DELETE_FIELDS.intersection(legacy))
        restarted.apply(legacy)
        exact=self.state(),self.xml.read_bytes()
        self.assertEqual(restarted.prepare_retry(legacy),legacy)
        restarted.apply(legacy)
        self.assertEqual((self.state(),self.xml.read_bytes()),exact)

    def test_ordinary_committed_receipt_replay_rejects_new_descendant_references(self):
        request=self.ordinary_request()
        def interrupted_commit(phase):
            if phase=='sql-committed':raise RuntimeError('owned committed folder interruption')
        with mock.patch.object(store,'_fault',side_effect=interrupted_commit):
            with self.assertRaises(RuntimeError):self.writer.apply(request)
        self.writer=store.PlaylistStore(self.authority,self.root/'recovery')
        self.assertEqual(self.writer.recover(),'committed')
        self.assertEqual(self.writer.prepare_retry(request),request)
        exact=self.state(),self.xml.read_bytes()
        self.writer.apply(request)
        self.assertEqual((self.state(),self.xml.read_bytes()),exact)
        conn=_connect(self.database)
        try:
            template=store.row(conn,'djmdPlaylist','1000');columns=list(template)
            child={**template,'ID':'999','UUID':native_uuid(999),'ParentID':'2000','Seq':1}
            conn.execute(f'INSERT INTO djmdPlaylist ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})',
                         [child[key] for key in columns]);conn.commit()
        finally:conn.close()
        before=self.state(),self.xml.read_bytes()
        with self.assertRaises(ConflictError):self.writer.prepare_retry(request)
        with self.assertRaises(ConflictError):self.writer.apply(request)
        self.assertEqual((self.state(),self.xml.read_bytes()),before)

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

    def ordinary_catalog(self):
        catalog=self.window.lib.source_playlist_catalog
        for node in catalog['nodes']:
            if node['Attribute']==4:node.update(Attribute=0,SmartList=None)
        catalog['revision']=planner.revision(catalog['nodes'],catalog['members'])
        return catalog

    def test_ordinary_confirmation_captures_native_multinode_request(self):
        from desktop.rekordbox_playlists import QMessageBox
        before=deepcopy(self.ordinary_catalog())
        with mock.patch.object(QMessageBox,'question',return_value=QMessageBox.Yes):
            self.window._native_delete('1000')
        request=self.controller.pending[0]['request']
        self.assertEqual(request['operation'],'delete')
        self.assertEqual(request['delete_scope_profile'],store.ORDINARY_FOLDER_DELETE_PROFILE)
        self.assertEqual([row['id'] for row in request['delete_descendants']],['2000','3000','1000'])
        self.assertEqual(self.window.lib.source_playlist_catalog,before)
        self.assertEqual(self.window._native_presentation().source_playlist_catalog['nodes'],[])
        self.window._save_state.assert_called_once()

    def test_large_ordinary_folder_confirmation_preserves_native_multinode_capacity(self):
        from desktop.rekordbox_playlists import QMessageBox
        catalog=self.ordinary_catalog();template=catalog['nodes'][1]
        for offset in range(300):
            catalog['nodes'].append({**template,'ID':str(4000+offset),'UUID':native_uuid(4000+offset),'Seq':3+offset})
        catalog['revision']=planner.revision(catalog['nodes'],catalog['members'])
        with mock.patch.object(QMessageBox,'question',return_value=QMessageBox.Yes) as confirmation:
            self.window._native_delete('1000')
        confirmation.assert_called_once()
        self.assertEqual(len(self.controller.pending),1)
        request=self.controller.pending[0]['request']
        self.assertEqual(request['operation'],'delete')
        self.assertEqual(len(request['delete_descendants']),303)
        self.assertNotIn('requests',request)
        self.assertEqual(self.window._native_presentation().source_playlist_catalog['nodes'],[])
        self.window._save_state.assert_called_once()

    def test_ordinary_late_child_is_refused_at_confirmation_admission_and_restarted_projection(self):
        from desktop.rekordbox_playlists import QMessageBox,PlaylistEditController
        catalog=self.ordinary_catalog();captured=store.request_for(catalog,'delete',identity='1000')
        def added(*_args):
            catalog['nodes'].append({**catalog['nodes'][1],'ID':'4000','UUID':native_uuid(4000),'Seq':3})
            catalog['revision']=planner.revision(catalog['nodes'],catalog['members'])
            return QMessageBox.Yes
        with mock.patch.object(QMessageBox,'question',side_effect=added):self.window._native_delete('1000')
        self.assertEqual(self.controller.pending,[]);self.window._save_state.assert_not_called()
        self.assertFalse(self.controller.enqueue(captured));self.window._save_state.assert_not_called()
        # Reopen a saved request even if a bad catalog producer kept its old
        # revision. Neither restart validation nor projection expands scope.
        catalog['revision']=captured['revision']
        self.window.settings['rekordbox_playlist_edits']=[dict(source=str(self.window.lib.source),
            library_id=self.window.lib.source_library_id,request=captured,error='',retry=False)]
        self.controller.stop()
        restarted=PlaylistEditController(self.window,store_factory=lambda _lib:None)
        self.addCleanup(restarted.stop);self.window.playlist_controller=restarted
        self.assertEqual(restarted.queue_error(),'')
        self.assertEqual({row['ID'] for row in self.window._native_presentation().source_playlist_catalog['nodes']},
                         {'1000','2000','3000','4000'})

    def test_ordinary_cancel_and_save_failure_preserve_previous_queue_and_view(self):
        from desktop.rekordbox_playlists import QMessageBox
        before=deepcopy(self.ordinary_catalog())
        with mock.patch.object(QMessageBox,'question',return_value=QMessageBox.No):self.window._native_delete('1000')
        self.assertEqual(self.controller.pending,[]);self.window._save_state.assert_not_called()
        self.window._save_state.return_value=False
        with mock.patch.object(QMessageBox,'question',return_value=QMessageBox.Yes):self.window._native_delete('1000')
        self.assertEqual(self.controller.pending,[])
        self.assertNotIn('rekordbox_playlist_edits',self.window.settings)
        self.assertEqual(self.window._native_presentation().source_playlist_catalog,before)
        self.window._save_state.assert_called_once()

    def test_ordinary_confirmation_preserves_newer_queued_descendant_edit(self):
        from desktop.rekordbox_playlists import QMessageBox
        catalog=self.ordinary_catalog()
        def edited(*_args):
            self.assertTrue(self.controller.enqueue(store.request_for(catalog,'rename',identity='2000',name='Newer child')))
            return QMessageBox.Yes
        with mock.patch.object(QMessageBox,'question',side_effect=edited):self.window._native_delete('1000')
        self.assertEqual([job['request']['operation'] for job in self.controller.pending],['rename'])
        self.assertEqual(next(row for row in self.window._native_presentation().source_playlist_catalog['nodes']
            if row['ID']=='2000')['Name'],'Newer child')
        self.window._save_state.assert_called_once()

    def test_ordinary_confirmation_rejects_reloaded_source_and_restored_state_epoch(self):
        from desktop.rekordbox_playlists import QMessageBox
        self.ordinary_catalog()
        owner=self.window.lib
        repository=SimpleNamespace(generation=1,area=SimpleNamespace(generation=1))
        self.window._state_repository=repository
        for change in ('source','identity','restored'):
            self.window.lib=owner;repository.area.generation=1
            def changed(*_args):
                if change=='source':self.window.lib=replace(owner)
                elif change=='identity':
                    self.window.lib=replace(owner,source_playlist_catalog=deepcopy(owner.source_playlist_catalog))
                    self.window.lib.source_playlist_catalog['nodes'][0]['UUID']=native_uuid(999)
                else:repository.area.generation+=1
                return QMessageBox.Yes
            with self.subTest(change=change),mock.patch.object(QMessageBox,'question',side_effect=changed):
                self.window._native_delete('1000')
            self.assertEqual(self.controller.pending,[]);self.window._save_state.assert_not_called()

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
