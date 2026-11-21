"""Gapped native parents keep source truth and independent creation admission."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest import mock

from desktop.playlist_names import playlist_name_taken
from desktop.rekordbox_playlists import _intent_xml_clocks
from soundbox import engine,rekordbox_playlist_plan as planner,rekordbox_playlist_create as creation
from soundbox.rekordbox_playlist_store import request_for
from soundbox.library import Playlist
from soundbox.state_repository import read_state
from tests import test_playlist_presentation as fixture


class NormalizedCreationPresentation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):fixture.PlaylistPresentation.setUpClass()

    def setUp(self):
        case=fixture.PlaylistPresentation();case.setUp();self.addCleanup(case.doCleanups)
        self.case=case;self.w=case.win;self.controller=case.controller
        catalog=deepcopy(self.w.lib.source_playlist_catalog)
        for row in catalog['nodes']:
            row.update(rb_data_status=0,rb_local_data_status=0,rb_local_synced=1,
                rb_local_usn=10,usn=None,SmartList=None,
                created_at='2026-11-19 00:00:00.000 +00:00',updated_at='2026-11-19 00:00:00.000 +00:00')
        catalog['nodes'].append({**catalog['nodes'][1],'ID':'5',
            'UUID':'00000000-0000-4000-8000-000000000005','Name':'After gap','Seq':3})
        catalog['revision']=planner.revision(catalog['nodes'],catalog['members'])
        self.w.lib=replace(self.w.lib,source_playlist_catalog=catalog,
            playlists=[*self.w.lib.playlists,Playlist('After gap',['Folder'],[],'5')])
        self.w.tag_controller.configure(self.w.lib,True)
        self.w._populate_tree(engine.summary(self.w.lib))
        self.assertTrue(self.w._save_state())

    def create(self,name='New playlist',**kwargs):
        return self.w._native_request('create',name=name,parent='1',folder=False,**kwargs)

    def test_old_crud_authority_cannot_admit_new_placement(self):
        before=deepcopy(self.w.lib);saved=read_state(self.w._state_repository.path)
        self.assertTrue(self.controller.normalized_creation_reason())
        self.assertFalse(self.create())
        self.assertFalse(self.controller.pending)
        self.assertEqual(self.w.lib,before)
        self.assertEqual(read_state(self.w._state_repository.path),saved)
        self.case.source.assert_not_called()

    def test_held_admission_projects_normalized_order_without_changing_source_or_export_library(self):
        before=deepcopy(self.w.lib);saved=read_state(self.w._state_repository.path)
        with mock.patch.object(self.controller,'normalized_creation_reason',return_value=''):
            with self.case.held_canonical() as (entered,release):
                self.assertTrue(self.create());self.assertTrue(entered.wait(2))
                request=self.controller.pending[0]['request']
                self.assertEqual(request['operation'],'create_normalized')
                rows={row['ID']:row for row in self.w._native_presentation().source_playlist_catalog['nodes']}
                self.assertEqual((rows['2']['Seq'],rows['5']['Seq'],rows[request['playlist_id']]['Seq']),(1,2,3))
                self.assertIsNotNone(self.w._find_tree_item('Folder / New playlist',request['playlist_id']))
                self.assertEqual(self.w.lib,before)
                self.assertEqual(read_state(self.w._state_repository.path),saved)
                self.case.source.assert_not_called();release.set()
            self.assertEqual(self.case.durable()[0]['request'],request)

    def test_pending_new_name_is_reserved_and_existing_names_remain_protected(self):
        with mock.patch.object(self.controller,'normalized_creation_reason',return_value=''):
            self.assertFalse(self.create('Other'))
            self.assertTrue(self.create('Available'))
            self.assertTrue(playlist_name_taken(self.w,' available '))
            self.assertFalse(self.create('AVAILABLE'))
            self.case.durable()
        self.assertEqual(len(self.controller.pending),1)

    def test_stale_parent_identity_cannot_project_a_new_row(self):
        from soundbox.playlist_presentation import project
        with mock.patch.object(self.controller,'normalized_creation_reason',return_value=''):
            self.assertTrue(self.create());self.case.durable()
        job=deepcopy(self.controller.pending[0]);job['request']['parent_uuid']='replaced-parent'
        result=project(self.w.lib,[job])
        self.assertEqual(result.source_playlist_catalog,self.w.lib.source_playlist_catalog)
        self.assertFalse(any(playlist.pid==job['request']['playlist_id'] for playlist in result.playlists))

    def test_unproved_folder_placement_and_ambiguous_positions_admit_nothing(self):
        self.assertFalse(self.w._native_request('create',name='Folder',parent='1',folder=True))
        catalog=deepcopy(self.w.lib.source_playlist_catalog)
        next(row for row in catalog['nodes'] if row['ID']=='5')['Seq']=1
        catalog['revision']=planner.revision(catalog['nodes'],catalog['members'])
        self.w.lib=replace(self.w.lib,source_playlist_catalog=catalog)
        self.assertFalse(self.create())
        self.assertFalse(self.controller.pending)
        self.case.source.assert_not_called()

    def test_held_worker_ack_rebases_following_normalized_creation_before_native_publication(self):
        w=self.w;catalog=deepcopy(w.lib.source_playlist_catalog)
        catalog['nodes'].append({**catalog['nodes'][-1],'ID':'6',
            'UUID':'00000000-0000-4000-8000-000000000006','Name':'Other gap','ParentID':'3','Seq':4})
        catalog['revision']=planner.revision(catalog['nodes'],catalog['members'])
        w.lib=replace(w.lib,source_playlist_catalog=deepcopy(catalog))
        w.tag_controller.configure(w.lib,True);w._populate_tree(engine.summary(w.lib))
        original=deepcopy(w.lib);members=deepcopy(catalog['members'])
        calls=[];edges=[];published=[];counter=dict(registry_id='localUpdateCount',int_1=10)
        def apply(request):
            nonlocal counter
            self.assertEqual(request['revision'],catalog['revision'])
            self.assertEqual(read_state(w._state_repository.path)['settings']['rekordbox_playlist_edits'][0]['request'],request)
            old=catalog['revision'];clocks=request['normalized_create_clocks']
            planned=creation.candidate(catalog['nodes'],counter,identity=request['playlist_id'],
                uuid=request['playlist_uuid'],name=request['name'],parent=request['parent'],
                timestamp=request['timestamp'],position_timestamp=clocks['position_timestamp'],
                sibling_timestamps=clocks['sibling_timestamps'],name_timestamp=request['survivor_timestamp'])
            for change in planned['changes']:
                if change['table']!='djmdPlaylist':continue
                row=next((row for row in catalog['nodes'] if row['ID']==change['id']),None)
                self.assertEqual(row,change['before'])
                if row is None:catalog['nodes'].append(deepcopy(change['after']))
                else:row.clear();row.update(deepcopy(change['after']))
            counter=planned['counter'];catalog['revision']=planner.revision(catalog['nodes'],catalog['members'])
            calls.append(deepcopy(request));edges.append((old,catalog['revision']))
            return deepcopy(catalog)
        self.controller.factory=lambda _library:SimpleNamespace(apply=apply,committed_transition=lambda _request:edges[-1])
        publish=w._native_apply_catalog
        def acknowledged(current,request):
            saved=read_state(w._state_repository.path)['settings']['rekordbox_playlist_edits']
            self.assertFalse(any(job['request']['operation_id']==request['operation_id'] for job in saved))
            if saved:self.assertEqual(saved[0]['request']['revision'],current['revision'])
            published.append(request['operation_id']);publish(current,request)
        with mock.patch.object(self.controller,'normalized_creation_reason',return_value=''):
            self.assertTrue(self.create('First normalized'))
            self.assertTrue(w._native_request('create',name='Second normalized',parent='3',folder=False))
            queued=self.case.durable()
            self.assertEqual([job['request']['operation'] for job in queued],['create_normalized']*2)
            with (mock.patch('desktop.rekordbox_playlists.assert_rekordbox_closed'),
                  mock.patch.object(w.tag_controller,'_recover_pending'),
                  mock.patch.object(w.tag_controller,'run_playlist_job',side_effect=lambda work,done:
                      w.tag_controller._run_source_job('playlists',work,done)),
                  mock.patch.object(w,'_native_apply_catalog',side_effect=acknowledged)):
                with self.case.held_canonical() as (entered,release):
                    self.controller.pump();self.case.wait(entered.is_set)
                    self.assertEqual(len(calls),1);self.assertFalse(published)
                    self.assertEqual(w.lib,original)
                    self.assertEqual(read_state(w._state_repository.path)['settings']['rekordbox_playlist_edits'],queued)
                    release.set()
                self.case.wait(lambda:not self.controller.pending and not self.controller.admission_pending)
        self.assertEqual(len(calls),2);self.assertEqual(len(published),2)
        self.assertNotEqual(calls[1]['revision'],queued[1]['request']['revision'])
        self.assertEqual(calls[1]['operation_id'],queued[1]['request']['operation_id'])
        self.assertEqual(catalog['members'],members)
        self.assertEqual(w.lib.source_playlist_catalog,catalog)
        self.assertEqual(self.case.durable(),[])
        for request in calls:
            row=next(row for row in catalog['nodes'] if row['ID']==request['playlist_id'])
            self.assertEqual((row['Name'],row['UUID'],row['ParentID']),
                             (request['name'],request['playlist_uuid'],request['parent']))

    def test_normalized_fifo_xml_chain_advances_shared_parent_without_changing_sql_clocks(self):
        request=request_for(self.w.lib.source_playlist_catalog,'create_normalized',name='Clock test',parent='1')
        before=deepcopy(request);parent_clock=request['normalized_create_clocks']['parent_xml_timestamp']+100
        target_clock=request['xml_timestamp']+200
        clocks={'1':parent_clock,request['playlist_id']:target_clock,'3':parent_clock+1000}
        _intent_xml_clocks(request,clocks,advance=True)
        self.assertEqual(request['xml_timestamp'],target_clock+1)
        self.assertEqual(request['normalized_create_clocks']['parent_xml_timestamp'],
                         max(parent_clock+1,target_clock+1))
        self.assertEqual(clocks['3'],parent_clock+1000)
        self.assertEqual({key:value for key,value in request['normalized_create_clocks'].items()
                          if key!='parent_xml_timestamp'},
                         {key:value for key,value in before['normalized_create_clocks'].items()
                          if key!='parent_xml_timestamp'})
        for key in ('timestamp','survivor_timestamp','parent_uuid','operation_id','playlist_uuid'):
            self.assertEqual(request[key],before[key])
