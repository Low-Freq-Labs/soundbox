"""Pending presentation reuse stays separate from source/edit authority."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest import mock

from desktop.rekordbox_playlists import NativePlaylistsMixin
from soundbox import playlist_presentation,rekordbox_playlist_plan as planner
from soundbox.library import Library,Playlist,Track
from soundbox.rekordbox_playlist_store import request_for,validate_request


class _Host(NativePlaylistsMixin):
    pass


class ProjectionCache(unittest.TestCase):
    def setUp(self):
        node=dict(ID='1000',UUID='00000000-0000-4000-8000-000000001000',Name='Original',
                  ParentID='root',Seq=1,Attribute=0,rb_local_deleted=0)
        member=dict(ID='member',UUID='membership',PlaylistID='1000',ContentID='42',TrackNo=1,rb_local_deleted=0)
        catalog=dict(nodes=[node],members=[member]);catalog['revision']=planner.revision(catalog['nodes'],catalog['members'])
        track=Track('logical','Song','Artist','file:///never-open/song.wav',source_native_id='42',source_uuid='native-track')
        self.host=_Host();self.host.lib=Library({'logical':track},[Playlist('Original',[],['logical'],'1000')],
            '/synthetic/master.db',source_kind='rekordbox-db',source_library_id='fixture',source_playlist_catalog=catalog)
        request=request_for(catalog,'rename',identity='1000',name='Pending')
        jobs=[dict(source=str(self.host.lib.source),library_id='fixture',request=request,error='',retry=False)]
        self.host.playlist_controller=SimpleNamespace(pending=jobs,queue_error=lambda:'')
        self.host.shared_import_controller=SimpleNamespace(pending=[],queue_error=lambda:'')

    def test_repeated_pending_reads_project_once_without_touching_source_rows(self):
        before=deepcopy(self.host.lib.source_playlist_catalog)
        with mock.patch.object(playlist_presentation,'project',wraps=playlist_presentation.project) as project:
            first=self.host._native_presentation();second=self.host._native_presentation()
        self.assertEqual(project.call_count,1)
        self.assertEqual(first.playlists[0].name,'Pending')
        self.assertIs(first.playlists,second.playlists)
        self.assertIs(first.source_playlist_catalog,second.source_playlist_catalog)
        self.assertEqual(self.host.lib.source_playlist_catalog,before)
        self.assertEqual(self.host.lib.playlists[0].name,'Original')

    def test_reused_structure_always_uses_current_filters_registry_and_source_metadata(self):
        original=self.host._native_presentation();library=self.host.lib
        library.hidden_ids={'logical'};library.hide_untracked=True;library.artist_registry=object()
        library.removed_by_playlist={'1000':{'logical'}};library.title_junk=['new rule']
        library.source_binding_issues={'logical':'changed binding'}
        with mock.patch.object(playlist_presentation,'project',side_effect=AssertionError('Structural inputs did not change')):
            fresh=self.host._native_presentation()
        self.assertIs(fresh.playlists,original.playlists)
        for field in ('hidden_ids','artist_registry','removed_by_playlist','title_junk','source_binding_issues'):
            self.assertIs(getattr(fresh,field),getattr(library,field),field)
        self.assertTrue(fresh.hide_untracked)
        self.assertEqual(fresh.resolve(fresh.playlists[0]),[])

    def test_in_place_native_uuid_membership_and_pending_error_changes_invalidate(self):
        self.host._native_presentation()
        catalog=self.host.lib.source_playlist_catalog
        catalog['nodes'][0]['UUID']='00000000-0000-4000-8000-000000009999'
        # Even an in-place mutation with an unchanged revision cannot reuse a
        # previously successful target projection under the wrong UUID.
        self.assertEqual(self.host._native_presentation().playlists[0].name,'Original')
        catalog['nodes'][0]['UUID']='00000000-0000-4000-8000-000000001000'
        self.assertEqual(self.host._native_presentation().playlists[0].name,'Pending')
        catalog['members'].clear()
        self.assertEqual(self.host._native_presentation().playlists[0].keys,[])
        self.host.playlist_controller.pending[0]['error']='Native identity changed'
        self.assertEqual(self.host._native_presentation().playlists[0].name,'Original')

    def test_in_place_request_and_track_alias_changes_invalidate(self):
        self.host._native_presentation()
        self.host.playlist_controller.pending[0]['request']['name']='New intention'
        self.assertEqual(self.host._native_presentation().playlists[0].name,'New intention')
        track=self.host.lib.tracks_by_id['logical'];track.source_native_id='99'
        self.assertEqual(self.host._native_presentation().playlists[0].keys,['42'])
        track.source_native_id='42';track.track_id='renamed-logical'
        self.assertEqual(self.host._native_presentation().playlists[0].keys,['renamed-logical'])

    def test_queue_validation_stays_authoritative_and_drained_cache_is_released(self):
        self.host._native_presentation()
        self.host.playlist_controller.queue_error=lambda:'Malformed durable queue'
        self.assertIs(self.host._native_presentation(),self.host.lib)
        self.assertIsNone(self.host._native_presentation_cache)
        self.host.playlist_controller.queue_error=lambda:''
        self.host.playlist_controller.pending=[]
        self.assertIs(self.host._native_presentation(),self.host.lib)
        self.assertIsNone(self.host._native_presentation_cache)

    def test_shared_import_work_bypasses_reuse_and_empty_catalogs_remain_usable(self):
        self.host._native_presentation()
        self.host.shared_import_controller.pending=[object()]
        self.host.shared_import_controller.queue_error=lambda:'Foreign import intent'
        with mock.patch.object(playlist_presentation,'project',wraps=playlist_presentation.project) as project:
            self.host._native_presentation();self.host._native_presentation()
        self.assertEqual(project.call_count,2)
        self.assertIsNone(self.host._native_presentation_cache)
        self.host.lib=replace(self.host.lib,source_playlist_catalog={})
        self.assertIs(self.host._native_presentation(),self.host.lib)

    def test_native_scalar_row_copy_preserves_unknown_cells_and_never_changes_original_fields(self):
        catalog=self.host.lib.source_playlist_catalog
        catalog['nodes'][0].update(opaque=b'\x00\xff',unrecognized=123)
        before=deepcopy(catalog);request=self.host.playlist_controller.pending[0]['request'];validate_request(request)
        projected=playlist_presentation.project(self.host.lib,self.host.playlist_controller.pending)
        self.assertEqual(catalog,before)
        self.assertIsNot(projected.source_playlist_catalog['nodes'][0],catalog['nodes'][0])
        self.assertIsNot(projected.source_playlist_catalog['members'][0],catalog['members'][0])
        self.assertEqual(projected.source_playlist_catalog['nodes'][0]['opaque'],b'\x00\xff')
        self.assertEqual(projected.source_playlist_catalog['nodes'][0]['unrecognized'],123)
