"""Bulk tag capture follows the actual browser selection through publication."""
from dataclasses import replace
import json
import re
import unittest
from unittest import mock

from soundbox import rekordbox_playlist_plan as planner
from soundbox.library import LibraryTag, Playlist, Track
from tests import test_prepared_native_publication as fixture


class BulkTagSelection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.PreparedPublication.setUpClass();cls.app=fixture.PreparedPublication.app

    def setUp(self):
        fixture.PreparedPublication.setUp(self)
        w=self.window
        # Seven scattered rows can change their presentation on a definition
        # readback. Native playlist occurrence/track identities remain exact.
        self.changed={1,12,19,20,23,29,31}
        tracks={str(i):Track(str(i),('Excluded' if i in self.changed else 'Included')+f' song {i}',
            'Artist',f'file:///never-open/{i}.wav',bpm='140',added_ts=float(i+1),
            source_uuid=f'00000000-0000-4000-8000-{i+100:012d}',source_tag_ids=[])
            for i in range(37)}
        nodes=[dict(ID='99',UUID='00000000-0000-4000-8000-000000000099',Name='INDIE',
                    ParentID='root',Seq=1,Attribute=0,rb_local_deleted=0)]
        members=[dict(ID=f'member-{i}',UUID=f'member-uuid-{i}',PlaylistID='99',ContentID=str(i),
                      TrackNo=i+1,rb_local_deleted=0) for i in range(37)]
        catalog=dict(nodes=nodes,members=members,revision=planner.revision(nodes,members))
        w.lib=replace(w.lib,tracks_by_id=tracks,playlists=[Playlist('INDIE',[],list(tracks),'99')],
                      source_tags=[],source_playlist_catalog=catalog)
        w._apply_loaded_library()
        w.tree.setCurrentItem(w._find_tree_item('INDIE','99'));w._reload_current_view()
        self.assertEqual(len(w.model._rows),37)
        self.tag=LibraryTag('201','Indie','Genre','00000000-0000-4000-8000-000000000201','1','1')
        self.enterContext(mock.patch.object(w.tag_controller,'reason',return_value=''))
        self.enterContext(mock.patch.object(w.tag_definition_controller,'assignment_reason',return_value=''))
        self.queue=self.enterContext(mock.patch.object(w.tag_controller,'_queue_tag_request',return_value=True))

    def publish_tag(self, *, removed=()):
        w=self.window
        tracks={key:replace(track,name=track.name+' refreshed' if int(key) in self.changed else track.name)
                for key,track in w.lib.tracks_by_id.items()}
        members=[row for row in w.lib.source_playlist_catalog['members'] if row['ContentID'] not in removed]
        catalog={**w.lib.source_playlist_catalog,'members':members}
        catalog['revision']=planner.revision(catalog['nodes'],members)
        fresh=replace(w.lib,tracks_by_id=tracks,source_tags=[self.tag],source_playlist_catalog=catalog,
                      playlists=[Playlist('INDIE',[],[key for key in tracks if key not in removed],'99')])
        w._publish_created_library(fresh)
        return fresh

    def test_select_all_then_definition_publication_captures_all_37_native_targets(self):
        w=self.window
        with self.assertLogs('soundbox',level='INFO') as logs:
            w._select_all_browser_tracks()
            selected=set(w._selected_ids());self.assertEqual(len(selected),37)
            self.publish_tag()
            self.assertEqual(set(w._selected_ids()),selected)
            w._assign_tag('201',True)
        request=self.queue.call_args.args[0]
        self.assertEqual({target.track_id for target in request.tracks},selected)
        self.assertEqual(len(request.tracks),37)
        self.assertTrue(all(not target.expected_present for target in request.tracks))
        text='\n'.join(logs.output)
        self.assertIn('action=select-all view="rb:INDIE" full=37 visible=37 selected=37',text)
        self.assertIn('action=tag-definition-refresh view="rb:INDIE" full=37 visible=37 selected=37',text)
        self.assertIn('before_selected=37 lost=0',text)
        self.assertIn('action=assign-tag view="rb:INDIE" full=37 visible=37 selected=37',text)

    def test_filtered_select_all_assigns_only_30_visible_songs_and_reports_filter(self):
        w=self.window;w._on_search('included')
        with self.assertLogs('soundbox',level='INFO') as logs:
            w._select_all_browser_tracks();self.publish_tag();w._assign_tag('201',True)
        selected={str(i) for i in range(37) if i not in self.changed}
        request=self.queue.call_args.args[0]
        self.assertEqual({target.track_id for target in request.tracks},selected)
        self.assertEqual(len(request.tracks),30)
        self.assertIn('full=37 visible=30 selected=30 native_targets=30 filtered=True','\n'.join(logs.output))

    def test_source_membership_loss_reports_count_hash_and_at_most_eight_ids(self):
        w=self.window;w._select_all_browser_tracks()
        removed={str(i) for i in range(11)}
        with self.assertLogs('soundbox',level='INFO') as logs:self.publish_tag(removed=removed)
        line=next(line for line in logs.output if 'action=tag-definition-refresh' in line)
        self.assertIn('before_selected=37 lost=11',line)
        self.assertRegex(line,r'lost_sha256=[0-9a-f]{64}')
        ids=json.loads(re.search(r'lost_ids=(\[.*\])$',line).group(1))
        self.assertEqual(len(ids),8);self.assertTrue(set(ids)<=removed)
        self.assertEqual(set(w._selected_ids()),{str(i) for i in range(37)}-removed)


if __name__=='__main__':unittest.main()
