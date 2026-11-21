"""Context creation retains its captured sibling position through source ACK."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import threading
import unittest
from unittest import mock

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog

from desktop.rekordbox_playlists import QUEUE,PlaylistEditController
from desktop.trees import RB_NODE_KIND_ROLE,NATIVE_BINDING_ROLE
from desktop.tag_creation import NewSmartPlaylistDialog
from soundbox import engine,rekordbox_playlist_plan as planner
from soundbox.library import LibraryTag,Playlist,TagCategory
from soundbox.rekordbox_playlist_store import request_for,_smart_sql_clock
from soundbox.state_repository import read_state
from tests import test_smart_playlist_move_ui as fixture


class CreationPlacement(unittest.TestCase):
    @classmethod
    def setUpClass(cls):fixture.SmartMovePublication.setUpClass()

    def setUp(self):
        fixture.SmartMovePublication.setUp(self)
        # Presentation cases hold actual source admission while canonical saves
        # remain live. A rejected mock dispatcher is not a source pause: its
        # ordinary deferred wakeup may run as soon as durability settles.
        self.w.tag_controller.admission_paused=True
        self.addCleanup(setattr,self.w.tag_controller,'admission_paused',False)
        w=self.w;catalog=deepcopy(w.lib.source_playlist_catalog)
        template=next(row for row in catalog['nodes'] if row['ID']=='5')
        additions=[]
        for identity,name,seq,kind in (('8','Root ordinary',4,0),('9','Root smart',5,4)):
            catalog['nodes'].append({**template,'ID':identity,'Name':name,'ParentID':'root','Seq':seq,
                'UUID':f'00000000-0000-4000-8000-{int(identity):012d}','Attribute':kind,
                'SmartList':None if kind==0 else '<NODE Existing="rule"/>'})
            additions.append(Playlist(name,[],['t'],identity))
        catalog['members'].append(dict(ID='member-8',UUID='member-uuid-8',PlaylistID='8',ContentID='42',TrackNo=1,rb_local_deleted=0))
        catalog['revision']=planner.revision(catalog['nodes'],catalog['members'])
        self.tag=LibraryTag('201','Favorites','Genre','00000000-0000-4000-8000-000000000201','1','00000000-0000-4000-8000-000000000001')
        w.lib.tracks_by_id['t'].source_tag_ids=['201']
        w.lib=replace(w.lib,source_playlist_catalog=catalog,playlists=[*w.lib.playlists,*additions],
            source_tags=[self.tag],source_tag_categories=[TagCategory('1','Genre',self.tag.category_uuid)])
        w.tag_controller.configure(w.lib,True)
        w._populate_tree(engine.summary(w.lib));self.assertTrue(w._save_state())
        self.enterContext(mock.patch.object(self.controller,'node_move_reason',return_value=''))

    def create(self,name='Below folder',folder=False):
        anchor=self.w._native_creation_anchor('1')
        with mock.patch('desktop.rekordbox_playlists.QInputDialog.getText',return_value=(name,True)):
            self.w._native_new('root',folder=folder,placement=anchor)
        return self.case.durable()[0]

    def discard(self):
        # Discard legitimately requires an idle source owner. Its mocked
        # confirmation is synchronous; the ensuing canonical checkpoint owns
        # admission before Qt resumes, and the queue is empty when it finishes.
        paused=self.w.tag_controller.admission_paused
        self.w.tag_controller.admission_paused=False
        try:self.case.discard()
        finally:self.w.tag_controller.admission_paused=paused

    def test_context_menus_capture_same_parent_below_regular_smart_and_folder_rows(self):
        for identity,kind in (('1',1),('8',0),('9',4)):
            menu=self.w._native_menu(self.w._find_tree_item('',identity))
            self.addCleanup(menu.deleteLater)
            actions={action.text():action for action in menu.actions()}
            with mock.patch.object(self.w,'_native_new') as ordinary,mock.patch.object(self.w,'_new_smart_playlist') as smart:
                for label in ('New Playlist','New Folder'):
                    self.assertTrue(actions[label].isEnabled());actions[label].trigger()
                    self.assertEqual(ordinary.call_args.args,('root',))
                    anchor=ordinary.call_args.kwargs['placement']
                    self.assertEqual((anchor['anchor_id'],anchor['anchor_attribute'],anchor['parent']),(identity,kind,'root'))
                actions['New Smart Playlist'].trigger()
                self.assertEqual(smart.call_args.args,('root',))
                self.assertEqual(smart.call_args.kwargs['placement']['anchor_id'],identity)

    def test_stale_painted_anchor_uuid_cannot_offer_creation_against_a_replacement_identity(self):
        item=self.w._find_tree_item('','1');binding=dict(item.data(0,NATIVE_BINDING_ROLE))
        item.setData(0,NATIVE_BINDING_ROLE,{**binding,'uuid':'00000000-0000-4000-8000-000000000099'})
        menu=self.w._native_menu(item);self.addCleanup(menu.deleteLater)
        actions={action.text():action for action in menu.actions()}
        for label in ('New Playlist','New Smart Playlist','New Folder'):
            self.assertFalse(actions[label].isEnabled())
        self.assertEqual(self.controller.pending,[])

    def test_ordinary_creation_shows_below_anchor_and_saves_position_with_create(self):
        original=deepcopy(self.w.lib.source_playlist_catalog)
        job=self.create();target=job['request']['playlist_id']
        self.assertEqual(job['creation_placement']['target_uuid'],job['request']['playlist_uuid'])
        self.assertEqual(job['creation_placement']['anchor_id'],'1')
        rows=sorted((row for row in self.w._native_presentation().source_playlist_catalog['nodes']
                     if row['ParentID']=='root'),key=lambda row:row['Seq'])
        self.assertEqual([row['ID'] for row in rows],['1',target,'3','7','8','9'])
        self.assertEqual(self.w.lib.source_playlist_catalog,original);self.case.source.assert_not_called()

    def test_folder_creation_projects_the_new_folder_below_its_clicked_sibling(self):
        job=self.create(folder=True);target=job['request']['playlist_id']
        self.assertTrue(job['request']['folder'])
        self.assertEqual(self.w._find_tree_item('',target).data(0,RB_NODE_KIND_ROLE),1)
        rows=sorted((row for row in self.w._native_presentation().source_playlist_catalog['nodes']
                     if row['ParentID']=='root'),key=lambda row:row['Seq'])
        self.assertEqual([row['ID'] for row in rows],['1',target,'3','7','8','9'])

    def test_modal_anchor_replacement_or_reparent_refuses_before_any_creation_admission(self):
        w=self.w;row=next(row for row in w.lib.source_playlist_catalog['nodes'] if row['ID']=='1')
        for field,value in (('UUID','00000000-0000-4000-8000-000000000099'),('ParentID','3'),('Attribute',0)):
            with self.subTest(field=field):
                before=deepcopy(row);anchor=w._native_creation_anchor('1')
                def renamed(*_args,**_kwargs):row[field]=value;return 'Stale',True
                with mock.patch('desktop.rekordbox_playlists.QInputDialog.getText',side_effect=renamed):
                    w._native_new('root',placement=anchor)
                self.assertEqual(self.controller.pending,[])
                row.clear();row.update(before)

    def test_smart_form_defaults_to_sibling_parent_and_folder_choice_clears_the_anchor(self):
        w=self.w;anchor=w._native_creation_anchor('1')
        def accepted(dialog):
            self.assertEqual(dialog.folder.currentData()[0],'root')
            dialog.name.setText('New smart sibling');dialog.tags.item(0).setCheckState(Qt.Checked)
            return QDialog.Accepted
        with mock.patch.object(NewSmartPlaylistDialog,'exec',new=accepted):
            self.assertTrue(w._new_smart_playlist('root',placement=anchor))
        first=self.case.durable()[0];self.assertEqual(first['creation_placement']['anchor_id'],'1')
        self.assertIsNotNone(w._find_tree_item('',first['request']['playlist_id']))
        # Discard this unstarted owned intention before checking an explicit
        # folder override; no source publisher has been called.
        self.discard()
        def changed(dialog):
            dialog.name.setText('Explicit location');dialog.tags.item(0).setCheckState(Qt.Checked)
            index=next(index for index in range(dialog.folder.count()) if dialog.folder.itemData(index)[0]=='3')
            dialog.folder.setCurrentIndex(index);dialog.folder.setCurrentIndex(0)
            return QDialog.Accepted
        with mock.patch.object(NewSmartPlaylistDialog,'exec',new=changed):
            self.assertTrue(w._new_smart_playlist('root',placement=anchor))
        self.assertNotIn('creation_placement',self.case.durable()[0])

    def test_creation_admission_failure_keeps_one_whole_create_with_placement_and_no_source_effect(self):
        anchor=self.w._native_creation_anchor('1');before=deepcopy(self.w.lib.source_playlist_catalog)
        with (mock.patch.object(self.w._state_repository,'commit_patch',side_effect=OSError('Generated admission full disk')),
              mock.patch('desktop.rekordbox_playlists.QInputDialog.getText',return_value=('Unsaved whole gesture',True))):
            self.w._native_new('root',placement=anchor);self.case.failed_admission()
        job=self.controller.pending[0]
        self.assertEqual(job['request']['name'],'Unsaved whole gesture')
        self.assertEqual(job['creation_placement']['anchor_id'],'1')
        self.assertEqual(read_state(self.w._state_repository.path)['settings'].get(QUEUE,[]),[])
        self.assertEqual(self.w._native_presentation().source_playlist_catalog,before)
        self.discard();self.case.source.assert_not_called()

    def test_pending_smart_membership_reuses_only_unchanged_tag_assignments_and_definition_identities(self):
        request=request_for(self.w.lib.source_playlist_catalog,'create_smart',name='Pending matching list',tags=[self.tag])
        self.assertTrue(self.controller.enqueue(request,placement=self.w._native_creation_anchor('1')))
        self.case.durable()
        def keys():return next(playlist.keys for playlist in self.w._native_presentation().playlists
                              if playlist.pid==request['playlist_id'])
        self.assertEqual(keys(),['t'])
        self.w.lib.tracks_by_id['u'].source_tag_ids=['201']
        self.assertEqual(keys(),['t','u'])
        self.w.lib=replace(self.w.lib,source_tags=[replace(self.tag,source_uuid='00000000-0000-4000-8000-000000000299')])
        self.assertNotIn(request['playlist_id'],{playlist.pid for playlist in self.w._native_presentation().playlists})
        self.assertEqual(self.controller.pending[0]['request']['operation_id'],request['operation_id'])

    def test_unanchored_smart_creation_shows_matching_tracks_before_canonical_ack_and_failure_retains_the_intention(self):
        w=self.w;original=deepcopy(w.lib.source_playlist_catalog);saved=read_state(w._state_repository.path)
        request=request_for(original,'create_smart',name='Immediate smart',tags=[self.tag])
        with self.case.held_canonical() as (entered,release):
            self.assertTrue(self.controller.enqueue(request));self.assertTrue(entered.wait(2))
            projected=w._native_presentation()
            self.assertEqual(next(playlist.keys for playlist in projected.playlists if playlist.pid==request['playlist_id']),['t'])
            self.assertIsNotNone(w._find_tree_item('',request['playlist_id']))
            self.assertEqual(w.lib.source_playlist_catalog,original)
            self.assertEqual(read_state(w._state_repository.path),saved)
            self.case.source.assert_not_called();release.set()
        self.case.durable();self.discard()
        failed=request_for(original,'create_smart',name='Retained smart',tags=[self.tag])
        with mock.patch.object(w._state_repository,'commit_patch',side_effect=OSError('Generated smart admission full disk')):
            self.assertTrue(self.controller.enqueue(failed));self.case.failed_admission()
        self.assertEqual(w._native_presentation().source_playlist_catalog,original)
        self.assertEqual(self.controller.pending[0]['request'],failed)
        self.discard();self.case.source.assert_not_called()

    def test_two_quick_smart_creations_in_an_existing_folder_capture_without_future_native_history(self):
        original=deepcopy(self.w.lib.source_playlist_catalog)
        names=iter(('Quick existing folder one','Quick existing folder two'))
        def accepted(dialog):
            dialog.name.setText(next(names));dialog.tags.item(0).setCheckState(Qt.Checked)
            self.assertTrue(dialog.create.isEnabled(),dialog.message.text())
            return QDialog.Accepted
        with (mock.patch.object(self.controller,'smart_normalized_reason',return_value=''),
              mock.patch.object(NewSmartPlaylistDialog,'exec',new=accepted)):
            self.assertTrue(self.w._new_smart_playlist('3'))
            self.assertTrue(self.controller.admission.pending)
            self.assertTrue(self.w._new_smart_playlist('3'))
            self.assertEqual(self.w.lib.source_playlist_catalog,original)
            self.assertEqual(len(self.controller.pending),2)
            for job in self.controller.pending:
                projected=self.w._native_presentation()
                row=next(row for row in projected.source_playlist_catalog['nodes'] if row['ID']==job['request']['playlist_id'])
                self.assertNotIn('rb_local_usn',row)
                self.assertNotIn('rb_local_synced',row)
            self.case.source.assert_not_called()
            try:queue=self.case.durable()
            except AssertionError:
                self.fail(repr(dict(queue=[(job['request']['operation'],job.get('error')) for job in self.controller.pending],
                    admission_error=self.controller.admission.error,active=self.controller.admission.active,
                    staged=len(self.controller.admission.staged),ack_error=self.controller.ack.error)))
        self.assertEqual([job['request']['operation'] for job in queue],['create_smart_normalized','create_smart_append'])

    def test_constructor_capture_refuses_unknown_future_identity_or_a_not_yet_created_parent(self):
        job=self.create(folder=True)
        projected=deepcopy(self.w._native_presentation().source_playlist_catalog)
        with self.assertRaisesRegex(ValueError,'finish creating'):
            self.controller.creation_catalog(projected,job['request']['playlist_id'])
        future=next(row for row in projected['nodes'] if row['ID']==job['request']['playlist_id'])
        future['UUID']='00000000-0000-4000-8000-000000000299'
        with self.assertRaisesRegex(ValueError,'unverified future'):
            self.controller.creation_catalog(projected,'root')

    def test_constructor_capture_preserves_pending_existing_node_moves_and_root_gaps_for_profile_selection(self):
        self.assertTrue(self.w._native_move('7','3'));self.case.durable()
        projected=self.w._native_presentation().source_playlist_catalog
        captured=self.controller.creation_catalog(projected,'3')
        self.assertEqual(next(row['ParentID'] for row in captured['nodes'] if row['ID']=='7'),'3')
        self.assertEqual([row['Seq'] for row in sorted((row for row in captured['nodes'] if row['ParentID']=='root'),
                                                      key=lambda row:row['Seq'])],[1,2,4,5])
        with mock.patch.object(self.controller,'smart_normalized_reason',return_value=''):
            self.assertEqual(self.controller.smart_creation_operation('3',projected),('create_smart_normalized',''))

    def test_pending_smart_order_uses_only_matching_assignment_recency_and_preserves_unknown_or_equal_order(self):
        w=self.w;t=w.lib.tracks_by_id['t'];u=w.lib.tracks_by_id['u'];u.source_tag_ids=['201']
        t.source_tag_times={'999':1000000};u.source_tag_times={'201':20};t.added_ts=100000000000
        request=request_for(w.lib.source_playlist_catalog,'create_smart',name='Matching recency',tags=[self.tag])
        self.assertTrue(self.controller.enqueue(request));self.case.durable()
        def keys():return next(playlist.keys for playlist in w._native_presentation().playlists
                              if playlist.pid==request['playlist_id'])
        self.assertEqual(keys(),['u','t'])
        t.source_tag_times={'201':20}
        self.assertEqual(keys(),['t','u'])
        t.source_tag_times={};u.source_tag_times={};u.added_ts=t.added_ts+1000000
        self.assertEqual(keys(),['t','u'])

    def root_gaps(self):
        catalog=self.w.lib.source_playlist_catalog
        for row in catalog['nodes']:
            if row['ParentID']=='root':row['Seq']*=2
        catalog['revision']=planner.revision(catalog['nodes'],catalog['members'])
        self.w._populate_tree(engine.summary(self.w.lib))

    def test_gapped_root_folder_creation_uses_its_separate_profile_and_keeps_the_captured_sibling_position(self):
        self.root_gaps();before=deepcopy(self.w.lib.source_playlist_catalog)
        with mock.patch.object(self.controller,'normalized_folder_reason',return_value=''):
            job=self.create(folder=True)
        self.assertEqual(job['request']['operation'],'create_folder_normalized')
        self.assertEqual(job['creation_placement']['anchor_id'],'1')
        rows=sorted((row for row in self.w._native_presentation().source_playlist_catalog['nodes']
                     if row['ParentID']=='root'),key=lambda row:row['Seq'])
        self.assertEqual([row['ID'] for row in rows],['1',job['request']['playlist_id'],'3','7','8','9'])
        self.assertEqual(self.w.lib.source_playlist_catalog,before)

    def test_gapped_root_smart_creation_selects_the_new_constructor_before_applying_context_position(self):
        self.root_gaps()
        def accepted(dialog):
            dialog.name.setText('Normalized smart sibling');dialog.tags.item(0).setCheckState(Qt.Checked)
            return QDialog.Accepted
        with (mock.patch.object(self.controller,'smart_normalized_reason',return_value=''),
              mock.patch.object(NewSmartPlaylistDialog,'exec',new=accepted)):
            self.assertTrue(self.w._new_smart_playlist('root',placement=self.w._native_creation_anchor('1')))
        job=self.case.durable()[0]
        self.assertEqual(job['request']['operation'],'create_smart_normalized')
        rows=sorted((row for row in self.w._native_presentation().source_playlist_catalog['nodes']
                     if row['ParentID']=='root'),key=lambda row:row['Seq'])
        self.assertEqual([row['ID'] for row in rows],['1',job['request']['playlist_id'],'3','7','8','9'])

    def test_unaccepted_normalized_folder_profile_never_stages_a_partial_context_creation(self):
        self.root_gaps()
        with mock.patch('desktop.rekordbox_playlists.QInputDialog.getText',return_value=('Unaccepted folder',True)):
            self.w._native_new('root',folder=True,placement=self.w._native_creation_anchor('1'))
        self.assertEqual(self.controller.pending,[])
        self.assertFalse(read_state(self.w._state_repository.path)['settings'].get(QUEUE,[]))
        self.case.source.assert_not_called()

    def backend(self):
        owner=self;catalog=deepcopy(self.w.lib.source_playlist_catalog);calls=[];edges={}
        def apply(request):
            canonical=read_state(owner.w._state_repository.path)['settings'][QUEUE]
            owner.assertEqual(canonical[0]['request'],request)
            if request['operation_id'] in edges:return deepcopy(catalog)
            owner.assertEqual(request['revision'],catalog['revision']);old=catalog['revision']
            if request['operation'] in ('create','create_normalized','create_smart'):
                template=next(row for row in catalog['nodes'] if row['ID']=='8')
                if request['operation']=='create_normalized':
                    peers=sorted((row for row in catalog['nodes'] if row['ParentID']==request['parent']),key=lambda row:row['Seq'])
                    for position,row in enumerate(peers,1):row['Seq']=position
                catalog['nodes'].append({**template,'ID':request['playlist_id'],'UUID':request['playlist_uuid'],
                    'Name':request['name'],'ParentID':request['parent'],
                    'Attribute':4 if request['operation']=='create_smart' else int(request['folder']),
                    'SmartList':planner.smart_tag_rule(request['playlist_id'],[tag['id'] for tag in request['tags']])
                        if request['operation']=='create_smart' else None,
                    'Seq':1+max(row['Seq'] for row in catalog['nodes'] if row['ParentID']==request['parent']),
                    'rb_local_synced':0,'rb_local_usn':11,'created_at':request['timestamp'],'updated_at':request['timestamp']})
            elif request['operation']=='move':
                target=next(row for row in catalog['nodes'] if row['ID']==request['playlist_id'])
                rows=sorted((row for row in catalog['nodes'] if row['ParentID']==request['parent'] and row is not target),
                            key=lambda row:row['Seq'])
                rows.insert(next(index for index,row in enumerate(rows) if row['ID']==request['anchor_id'])+1,target)
                for position,row in enumerate(rows,1):row['Seq']=position
            elif request['operation']=='rename':
                next(row for row in catalog['nodes'] if row['ID']==request['playlist_id'])['Name']=request['name']
            else:raise AssertionError(request['operation'])
            catalog['revision']=planner.revision(catalog['nodes'],catalog['members'])
            edges[request['operation_id']]=(old,catalog['revision']);calls.append(deepcopy(request))
            return deepcopy(catalog)
        store=SimpleNamespace(apply=apply,committed_transition=lambda request:edges[request['operation_id']])
        self.controller.factory=lambda _library:store
        self.enterContext(mock.patch('desktop.rekordbox_playlists.assert_rekordbox_closed'))
        self.enterContext(mock.patch.object(self.w.tag_controller,'_recover_pending'))
        self.enterContext(mock.patch.object(self.w.tag_controller,'run_playlist_job',side_effect=lambda work,done:
            self.w.tag_controller._run_source_job('playlists',work,done)))
        def readback(_library):
            created=[row for row in catalog['nodes'] if row['ID'] not in {playlist.pid for playlist in owner.w.lib.playlists}
                     and row['Attribute']==4]
            return replace(owner.w.lib,source_playlist_catalog=deepcopy(catalog),playlists=[*owner.w.lib.playlists,
                *(Playlist(row['Name'],[],['t'],row['ID']) for row in created)])
        self.enterContext(mock.patch.object(self.w.tag_controller,'readback_library',side_effect=readback))
        self.w.tag_controller.admission_paused=False
        return catalog,calls

    def test_real_worker_create_ack_atomically_replaces_create_with_actual_move_before_publishing(self):
        self.create();catalog,calls=self.backend();published=[]
        original=self.w._native_apply_catalog
        def publish(current,request):
            canonical=read_state(self.w._state_repository.path)['settings'][QUEUE]
            if request['operation']=='create':
                self.assertEqual(canonical[0]['request']['operation'],'move')
                self.assertEqual(canonical[0]['request']['playlist_id'],request['playlist_id'])
                self.assertNotIn('creation_placement',canonical[0])
            published.append(request['operation']);original(current,request)
        with mock.patch.object(self.w,'_native_apply_catalog',side_effect=publish):
            self.controller.pump();self.case.wait(lambda:not self.controller.pending and not self.controller.admission_pending)
        self.assertEqual([request['operation'] for request in calls],['create','move'])
        self.assertEqual(published,['create','move'])
        rows=sorted((row for row in catalog['nodes'] if row['ParentID']=='root'),key=lambda row:row['Seq'])
        self.assertEqual([row['Name'] for row in rows],['Folder','Below folder','Keep','Empty','Root ordinary','Root smart'])
        self.assertEqual(self.case.durable(),[])

    def test_failed_create_ack_keeps_sidecar_and_retry_publishes_one_saved_move_without_repeating_create(self):
        job=self.create();_catalog,calls=self.backend()
        with mock.patch.object(self.w._state_repository,'commit_patch',side_effect=OSError('Generated ACK full disk')):
            self.controller.pump();self.case.wait(lambda:bool(self.controller.ack.error))
        self.assertEqual(read_state(self.w._state_repository.path)['settings'][QUEUE],[job])
        self.assertEqual([request['operation'] for request in calls],['create'])
        self.assertTrue(self.controller.retry())
        self.case.wait(lambda:not self.controller.pending and not self.controller.admission_pending)
        self.assertEqual([request['operation'] for request in calls],['create','move'])
        self.assertEqual(self.case.durable(),[])

    def test_smart_creation_uses_its_verified_defined_rule_before_materializing_the_actual_move(self):
        request=request_for(self.w.lib.source_playlist_catalog,'create_smart',name='Defined smart sibling',tags=[self.tag])
        self.assertTrue(self.controller.enqueue(request,placement=self.w._native_creation_anchor('1')))
        self.case.durable();_catalog,calls=self.backend();published=[]
        def publish(library):
            queue=read_state(self.w._state_repository.path)['settings'][QUEUE]
            self.assertEqual(queue[0]['request']['operation'],'move')
            self.assertTrue(queue[0]['request']['smart_rule'])
            self.w.lib=library;published.append(library)
        with mock.patch.object(self.w,'_publish_created_library',side_effect=publish):
            self.controller.pump();self.case.wait(lambda:not self.controller.pending and not self.controller.admission_pending)
        self.assertEqual([entry['operation'] for entry in calls],['create_smart','move'])
        self.assertEqual(calls[1]['smart_rule'],planner.smart_tag_rule(request['playlist_id'],['201']))
        self.assertEqual(calls[1]['playlist_uuid'],request['playlist_uuid'])
        self.assertEqual(len(published),1)
        self.assertEqual(next(playlist.keys for playlist in self.w.lib.playlists if playlist.pid==request['playlist_id']),['t'])

    def test_materialized_move_advances_future_create_target_and_parent_xml_without_changing_sql_clock_domain(self):
        request=request_for(self.w.lib.source_playlist_catalog,'create_normalized',name='Future XML sibling',parent='1')
        request['xml_timestamp']+=1000000
        request['normalized_create_clocks']['parent_xml_timestamp']=request['xml_timestamp']+1000000
        with mock.patch.object(self.controller,'normalized_creation_reason',return_value=''):
            self.assertTrue(self.controller.enqueue(request,placement=self.w._native_creation_anchor('2')))
            self.case.durable();_catalog,calls=self.backend();self.controller.pump()
            self.case.wait(lambda:not self.controller.pending and not self.controller.admission_pending)
        self.assertEqual([entry['operation'] for entry in calls],['create_normalized','move'])
        self.assertGreater(calls[1]['xml_timestamp'],calls[0]['xml_timestamp'])
        self.assertGreater(calls[1]['node_move_clocks']['parent_xml_timestamp'],
                           calls[0]['normalized_create_clocks']['parent_xml_timestamp'])
        self.assertLess(_smart_sql_clock(calls[1]['timestamp']).timestamp()*1000,calls[0]['xml_timestamp'])
        self.assertTrue(all(_smart_sql_clock(clock).timestamp()*1000<calls[0]['xml_timestamp']
                            for clock in calls[1]['node_move_clocks']['sibling_timestamps'].values()))

    def test_controller_restart_replays_receipt_and_preserves_the_saved_placement_after_lost_ack(self):
        job=self.create();_catalog,calls=self.backend();old=self.controller
        with mock.patch.object(self.w._state_repository,'commit_patch',side_effect=OSError('Generated lost ACK')):
            old.pump();self.case.wait(lambda:bool(old.ack.error))
        self.assertEqual(read_state(self.w._state_repository.path)['settings'][QUEUE],[job])
        fresh=PlaylistEditController(self.w,store_factory=old.factory)
        self.w.playlist_controller=fresh;old.stop()
        self.assertFalse(old.ack.pending)
        self.controller=fresh;self.case.controller=fresh
        self.enterContext(mock.patch.object(fresh,'node_move_reason',return_value=''))
        fresh.pump();self.case.wait(lambda:not fresh.pending and not fresh.admission_pending)
        self.assertEqual([request['operation'] for request in calls],['create','move'])
        self.assertEqual(self.case.durable(),[])

    def test_placement_move_precedes_and_causally_renews_a_later_saved_user_edit(self):
        self.create();later=request_for(self.w.lib.source_playlist_catalog,'rename',identity='8',name='Later user name')
        self.assertTrue(self.controller.enqueue(later));self.case.durable()
        _catalog,calls=self.backend();self.controller.pump()
        self.case.wait(lambda:not self.controller.pending and not self.controller.admission_pending)
        self.assertEqual([request['operation'] for request in calls],['create','move','rename'])
        self.assertEqual(calls[-1]['operation_id'],later['operation_id'])
        self.assertNotEqual(calls[-1]['revision'],later['revision'])
        self.assertEqual(next(row['Name'] for row in self.w.lib.source_playlist_catalog['nodes'] if row['ID']=='8'),
                         'Later user name')

    def test_create_ack_keeps_an_unadmitted_later_gesture_behind_the_materialized_move(self):
        self.create();_catalog,calls=self.backend();store=self.controller.factory(self.w.lib)
        apply=store.apply;entered=threading.Event();release=threading.Event()
        def held(request):
            result=apply(request)
            if request['operation']=='create':
                entered.set()
                if not release.wait(5):raise RuntimeError('Generated source hold timed out')
            return result
        store.apply=held
        self.addCleanup(release.set)
        self.controller.pump();self.case.wait(entered.is_set)
        later=request_for(self.w.lib.source_playlist_catalog,'rename',identity='8',name='Staged later gesture')
        self.assertTrue(self.controller.enqueue(later))
        self.assertEqual(self.controller.admission.staged[0]['request']['operation_id'],later['operation_id'])
        release.set()
        self.case.wait(lambda:len(calls)>=2)
        self.case.wait(lambda:len(calls)==3)
        try:self.case.wait(lambda:not self.controller.pending and not self.controller.admission_pending)
        except AssertionError:
            self.fail(repr(dict(calls=[row['operation'] for row in calls],
                queue=[(row['request']['operation'],row.get('error')) for row in self.controller.pending],
                admission_error=self.controller.admission.error,admission_active=self.controller.admission.active,
                staged=len(self.controller.admission.staged),ack_error=self.controller.ack.error)))
        self.assertEqual([request['operation'] for request in calls],['create','move','rename'])
        self.assertEqual(calls[-1]['operation_id'],later['operation_id'])
        self.assertEqual(self.case.durable(),[])

    def test_changed_anchor_after_source_creation_retains_create_and_placement_for_explicit_retry(self):
        job=self.create();catalog,calls=self.backend()
        original=self.controller._creation_successor
        def replaced(captured,current):
            next(row for row in current['nodes'] if row['ID']=='1')['UUID']='00000000-0000-4000-8000-000000000099'
            return original(captured,current)
        with mock.patch.object(self.controller,'_creation_successor',side_effect=replaced):
            self.controller.pump();self.case.wait(lambda:bool(self.controller.pending[0]['error']))
        canonical=read_state(self.w._state_repository.path)['settings'][QUEUE]
        self.assertEqual(canonical[0]['request'],job['request'])
        self.assertEqual(canonical[0]['creation_placement'],job['creation_placement'])
        self.assertEqual([request['operation'] for request in calls],['create'])
        self.assertTrue(any(row['ID']==job['request']['playlist_id'] for row in catalog['nodes']))


if __name__=='__main__':unittest.main()
