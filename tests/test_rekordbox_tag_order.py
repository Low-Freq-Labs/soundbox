"""Read-only tag projection preserves native order without per-song pool scans."""
from types import SimpleNamespace as Row
import unittest
from unittest import mock

from soundbox import rekordbox_db as reader


class TagOrder(unittest.TestCase):
    def test_selected_tracks_reuse_supplied_snapshot_definitions_and_empty_skips_links(self):
        definitions=[Row(ID='a',Attribute=0,Name='A',ParentID='',Seq=0),
                     Row(ID='deleted',Attribute=0,Name='Gone',ParentID='',Seq=1,rb_local_deleted=1)]
        db=Row(get_my_tag=mock.Mock(side_effect=AssertionError('independent definition read')),
               get_my_tag_songs=mock.Mock(return_value=[Row(ContentID='selected',MyTagID='a'),
                                                       Row(ContentID='other',MyTagID='a')]))
        tags,empty=reader._read_tags(db,track_ids=set(),definitions=definitions)
        self.assertEqual([tag.tag_id for tag in tags],['a']);self.assertEqual(empty,{})
        db.get_my_tag_songs.assert_not_called()
        _tags,members=reader._read_tags(db,track_ids={'selected'},definitions=definitions)
        self.assertEqual(members,{'selected':['a']});db.get_my_tag.assert_not_called()

    def test_definitions_duplicates_deletions_and_track_insertion_order_are_exact(self):
        definitions=[Row(ID='category',Attribute=1,Name='Category',ParentID='',Seq=1),
                     Row(ID='b',Attribute=0,Name='B',ParentID='category',Seq=2),
                     Row(ID='a',Attribute=0,Name='A',ParentID='category',Seq=1),
                     Row(ID='a',Attribute=0,Name='Duplicate A',ParentID='category',Seq=3),
                     Row(ID='deleted',Attribute=0,Name='Deleted',ParentID='',Seq=0,rb_local_deleted=1)]
        links=[Row(ContentID=tid,MyTagID=tag,rb_local_deleted=deleted) for tid,tag,deleted in
               [('second','b',0),('first','a',0),('second','a',0),('first','a',0),
                ('first','b',1),('unknown','absent',0),('gone','deleted',0)]]
        tags,members=reader._read_tags(Row(get_my_tag=lambda:definitions,get_my_tag_songs=lambda:links))
        self.assertEqual([t.tag_id for t in tags],['a','b','a'])
        self.assertEqual(list(members),['second','first'])
        self.assertEqual(members,{'second':['a','b','a'],'first':['a','a']})

    def test_large_sparse_memberships_read_each_definition_identity_linearly(self):
        class CountedTag:
            reads=0
            def __init__(self,identity,*_args):self.identity=identity
            @property
            def tag_id(self):type(self).reads+=1;return self.identity
        definitions=[Row(ID=str(i),Attribute=0,Name=str(i),ParentID='',Seq=i) for i in range(256)]
        links=[Row(ContentID=str(i),MyTagID=str(i%256)) for i in range(3200)]
        with mock.patch.object(reader,'LibraryTag',CountedTag):
            _tags,members=reader._read_tags(Row(get_my_tag=lambda:definitions,get_my_tag_songs=lambda:links))
        self.assertEqual(members,{str(i):[str(i%256)] for i in range(3200)})
        self.assertLess(CountedTag.reads,5000)


if __name__=='__main__':unittest.main()
