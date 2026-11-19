"""Worker-derived smart recency retains exact identities and latest state clocks."""
from copy import deepcopy
from dataclasses import replace
import unittest

from soundbox import smart_playlist_order as order
from tests.test_smart_playlist_order import library, verified


class PreparedSmartOrder(unittest.TestCase):
    def test_aliases_duplicate_members_and_title_collisions_promote_only_native_uuid(self):
        before=library();before.tracks_by_id['two'].name=before.tracks_by_id['one'].name
        before.tracks_by_id['alias']=replace(before.tracks_by_id['one'],track_id='alias',source_native_id='one')
        capture=order.capture(before,['alias','one','alias'],'11',True)
        after=verified(before);after.tracks_by_id['alias'].source_tag_ids=['11']
        after.playlists[0].keys=['three','alias','one','alias','two']
        prepared=order.prepare(capture,after)
        state=order.record_prepared(None,prepared)
        self.assertEqual(state['sequence'],1)
        self.assertEqual(list(state['playlists'].values()),[{'uuid-one':1}])
        self.assertEqual(after.playlists[0].keys,['three','alias','one','alias','two'])
        self.assertEqual(before.tracks_by_id['one'].source_tag_ids,[])

    def test_changed_source_uuid_tag_or_smart_rule_cannot_receive_captured_promotion(self):
        before=library();capture=order.capture(before,['one'],'11',True)
        for fault in ('source','library','native','uuid','tag-uuid','playlist-uuid','rule','nonmember'):
            with self.subTest(fault=fault):
                after=verified(before)
                if fault=='source':after.source='/different/master.db'
                elif fault=='library':after.source_library_id='different'
                elif fault=='native':after.tracks_by_id['one'].source_native_id='replacement'
                elif fault=='uuid':after.tracks_by_id['one'].source_uuid='replacement'
                elif fault=='tag-uuid':after.source_tags[0]=replace(after.source_tags[0],source_uuid='replacement')
                elif fault=='playlist-uuid':after.source_playlist_catalog['nodes'][0]['UUID']='replacement'
                elif fault=='rule':after.source_playlist_catalog['nodes'][0]['SmartList']+=' '
                else:after.playlists[0].keys.remove('one')
                self.assertIsNone(order.prepare(capture,after))

    def test_prepared_facts_apply_to_latest_clock_without_reading_library_again(self):
        before=library();prepared=order.prepare(order.capture(before,['one'],'11',True),verified(before))
        previous={'version':1,'sequence':12,'playlists':{'unrelated':{'other':12}}}
        state=order.record_prepared(previous,prepared)
        self.assertEqual(state['sequence'],13);self.assertEqual(state['playlists']['unrelated'],{'other':12})
        self.assertEqual(previous,{'version':1,'sequence':12,'playlists':{'unrelated':{'other':12}}})
        self.assertEqual([rank for scope,ranks in state['playlists'].items() if scope!='unrelated'
                          for rank in ranks.values()],[13])
        self.assertIs(state['playlists']['unrelated'],previous['playlists']['unrelated'])

    def test_corrupt_and_exhausted_latest_order_remain_errors_and_never_erase_old_state(self):
        before=library();prepared=order.prepare(order.capture(before,['one'],'11',True),verified(before))
        for state in ({'version':1,'sequence':True,'playlists':{}},
                      {'version':1,'sequence':2**63-1,'playlists':{}},
                      {'version':1,'sequence':1,'playlists':{'old':{'uuid':2}}}):
            previous=deepcopy(state)
            with self.assertRaises(ValueError):order.record_prepared(state,prepared)
            self.assertEqual(state,previous)
        self.assertIsNone(order.prepare(order.capture(before,['one'],'11',False),verified(before)))
