"""Confirmed native Delete refuses ambiguous playlist IDs before planning effects."""
import io
import unittest
from unittest import mock

from clickwheel.ipod.itunesdb_writer import PlaylistInfo,write_itunesdb,ChecksumType
from clickwheel.ipod.ipod_models import capabilities_for_family_gen

from soundbox import stock_ipod
from tests import test_stock_ipod as fixtures


class StockDeleteIdentity(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.StockIpodSync();self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def rewrite(self,playlists,smart_playlists=()):
        root=self.fixture.ipod
        raw=stock_ipod.parse_itunesdb(io.BytesIO((root/'iPod_Control/iTunes/iTunesDB').read_bytes()))
        tracks=[stock_ipod._existing_track_info(row) for row in stock_ipod.get_ipod_tracks(raw)]
        self.assertTrue(write_itunesdb(root,tracks,backup=False,force_checksum=ChecksumType.NONE,
            capabilities=capabilities_for_family_gen('iPod Video','5.5th Gen'),
            playlists=playlists,smart_playlists=smart_playlists))

    def plan(self):
        return stock_ipod.plan_stock_ipod_playlist_delete(self.fixture.ipod,[202],
            model_number='MA446',filesystem='fat32',usb_family='iPod Video')

    def assert_ambiguous_without_effect_plan(self):
        before=self.fixture._snapshot(self.fixture.ipod)
        with (mock.patch.object(stock_ipod,'_render_database',side_effect=AssertionError('Ambiguous catalog reached render')),
              mock.patch.object(stock_ipod.stock_delete,'capture',side_effect=AssertionError('Ambiguous catalog captured a deletion entity')),
              self.assertRaisesRegex(stock_ipod.StockIpodError,'playlist identity is ambiguous')):
            self.plan()
        self.assertEqual(self.fixture._snapshot(self.fixture.ipod),before)

    def test_duplicate_regular_playlist_id_cannot_delete_both_selected_and_unselected_rows(self):
        self.rewrite([PlaylistInfo('First',[101],playlist_id=202),
                      PlaylistInfo('Second',[101],playlist_id=202)])
        self.assert_ambiguous_without_effect_plan()

    def test_regular_id_cannot_collide_with_an_immutable_smart_index(self):
        self.rewrite([PlaylistInfo('User',[101],playlist_id=202)],
            [PlaylistInfo('Music',[],playlist_id=202,mhsd5_type=4,
                smart_prefs=fixtures.SmartPlaylistPrefs(),
                smart_rules=fixtures.SmartPlaylistRules(rules=[]))])
        self.assert_ambiguous_without_effect_plan()

    def test_same_name_different_ids_retains_other_playlist_and_its_shared_audio(self):
        self.rewrite([PlaylistInfo('Same name',[101],playlist_id=202),
                      PlaylistInfo('Same name',[101],playlist_id=203)])
        before=self.fixture._snapshot(self.fixture.ipod)
        plan=self.plan()
        self.assertEqual(plan.deleted_track_count,0)
        self.assertEqual(plan.final_track_count,1)
        self.assertFalse(plan.media_deletes)
        rendered=stock_ipod.parse_itunesdb(io.BytesIO(plan.rendered_database))
        self.assertEqual([row['playlistID'] for row in rendered['mhlp'] if not row.get('isMaster')],[203])
        self.assertEqual(self.fixture._snapshot(self.fixture.ipod),before)
