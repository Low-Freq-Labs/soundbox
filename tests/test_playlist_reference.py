"""Synthetic native membership protocol, without a live writer or discovery."""
from copy import deepcopy
import unittest

from research.cdj_export import playlist_membership as proof
from research.cdj_export.playlist_reference import compare_rows


class NativeMembershipProof(unittest.TestCase):
    def setUp(self):
        self.playlist = dict(ID='playlist',Attribute=0,SmartList=None,rb_data_status=0,
            rb_local_data_status=0,rb_local_deleted=0,rb_local_synced=0,usn=None)
        self.counter = dict(registry_id='localUpdateCount',int_1=40,updated_at='unchanged',opaque='keep')
        self.rows = []
        for i in range(2):
            plan = proof.append(self.playlist,self.rows,self.counter,dict(ID=str(i),UUID='content'+str(i)),
                identity=f'00000000-0000-4000-8000-{i:012}',uuid=f'10000000-0000-4000-8000-{i:012}',timestamp='created')
            self.rows,self.counter = plan['members'],plan['counter']

    def test_remove_physically_deletes_and_renumbers_without_changing_survivor_identity(self):
        baseline = deepcopy(self.rows)
        result = proof.remove(self.playlist,self.rows,self.counter,self.rows[0]['ID'],timestamp='updated')
        self.assertEqual(self.rows,baseline)
        self.assertEqual(len(result['members']),1)
        survivor = result['members'][0]
        self.assertEqual((survivor['ID'],survivor['UUID'],survivor['created_at']),
                         (baseline[1]['ID'],baseline[1]['UUID'],'created'))
        self.assertEqual((survivor['TrackNo'],survivor['rb_local_usn']),(1,44))
        self.assertEqual(result['counter'],{**self.counter,'int_1':44})
        deleted = next(c for c in result['changes'] if c['id']==baseline[0]['ID'])
        self.assertIsNone(deleted['after']);self.assertFalse(result['source_write_authority'])

    def test_unknown_sync_and_nonordinary_playlists_fail_closed(self):
        for row in ({**self.playlist,'Attribute':4},{**self.playlist,'rb_local_synced':1}):
            with self.assertRaises(ValueError):proof.remove(row,self.rows,self.counter,self.rows[0]['ID'],timestamp='x')
        rows = deepcopy(self.rows);rows[1]['usn']=7
        with self.assertRaises(ValueError):proof.remove(self.playlist,rows,self.counter,rows[0]['ID'],timestamp='x')

    def test_reference_diff_reports_deletion_and_survivor_fields_separately(self):
        result = proof.remove(self.playlist,self.rows,self.counter,self.rows[0]['ID'],timestamp='updated')
        diff = compare_rows({'djmdSongPlaylist':self.rows},{'djmdSongPlaylist':result['members']})
        self.assertIsNone(diff['djmdSongPlaylist'][0]['after'])
        self.assertEqual(diff['djmdSongPlaylist'][1]['fields'],['TrackNo','rb_local_usn','updated_at'])
