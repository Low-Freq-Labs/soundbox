"""Read-only tag projection preserves native order without per-song pool scans."""
from types import SimpleNamespace as Row
from datetime import datetime, timezone
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
        times={'stale':{'a':1.0}}
        tags,empty=reader._read_tags(db,track_ids=set(),definitions=definitions,assignment_times=times)
        self.assertEqual([tag.tag_id for tag in tags],['a']);self.assertEqual(empty,{})
        self.assertEqual(times,{})
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

    def test_active_native_assignment_dates_are_captured_in_the_existing_link_traversal(self):
        stamp=datetime(2026,11,9,19,10,37,420000,tzinfo=timezone.utc)
        older=datetime(2026,11,9,19,10,30,610000,tzinfo=timezone.utc)
        definitions=[Row(ID='leaf',Attribute=0,Name='Dubstep',ParentID='',Seq=1),
                     Row(ID='deleted',Attribute=0,Name='Gone',ParentID='',Seq=2,rb_local_deleted=1)]
        links=[Row(ContentID='newest',MyTagID='leaf',created_at=stamp),
               Row(ContentID='older',MyTagID='leaf',created_at=older),
               Row(ContentID='newest',MyTagID='leaf',created_at=older),
               Row(ContentID='inactive',MyTagID='leaf',created_at=stamp,rb_local_deleted=1),
               Row(ContentID='gone-tag',MyTagID='deleted',created_at=stamp),
               Row(ContentID='unknown-tag',MyTagID='absent',created_at=stamp)]
        db=Row(get_my_tag=mock.Mock(return_value=definitions),get_my_tag_songs=mock.Mock(return_value=links))
        times={}
        tags,members=reader._read_tags(db,assignment_times=times)
        self.assertEqual([tag.tag_id for tag in tags],['leaf'])
        self.assertEqual(members,{'newest':['leaf'],'older':['leaf']})
        self.assertEqual(times,{'newest':{'leaf':stamp.timestamp()},'older':{'leaf':older.timestamp()}})
        self.assertGreater(times['newest']['leaf'],times['older']['leaf'])
        db.get_my_tag.assert_called_once();db.get_my_tag_songs.assert_called_once()

    def test_missing_invalid_and_unsupported_dates_keep_membership_without_inventing_chronology(self):
        class BrokenDate(datetime):
            def timestamp(self):raise OverflowError('invalid date')
        bad=[None,False,0,'2026-11-09 19:10:37.420',
             datetime(1960,1,1,tzinfo=timezone.utc),BrokenDate(2026,1,1,tzinfo=timezone.utc),
             datetime(2026,1,1)]
        definitions=[Row(ID='leaf',Attribute=0,Name='Leaf',ParentID='',Seq=1)]
        links=[Row(ContentID=str(i),MyTagID='leaf',created_at=value) for i,value in enumerate(bad)]
        links.append(Row(ContentID='epoch',MyTagID='leaf',created_at=datetime(1970,1,1,tzinfo=timezone.utc)))
        times={}
        _tags,members=reader._read_tags(Row(get_my_tag=lambda:definitions,get_my_tag_songs=lambda:links),assignment_times=times)
        self.assertEqual(members,{**{str(i):['leaf'] for i in range(len(bad))},'epoch':['leaf']})
        self.assertEqual(times,{'epoch':{'leaf':0.0}})

    def test_native_offset_text_keeps_dst_fold_order_and_rejects_unknown_date_text(self):
        early=reader._tag_assignment_time('2026-11-01 06:30:00.000 +00:00')
        later=reader._tag_assignment_time('2026-11-01 07:15:00.000 +00:00')
        self.assertEqual(early,datetime(2026,11,1,6,30,tzinfo=timezone.utc).timestamp())
        self.assertEqual(later,datetime(2026,11,1,7,15,tzinfo=timezone.utc).timestamp())
        self.assertGreater(later,early)
        self.assertEqual(reader._tag_assignment_time('2026-11-01 01:15:00.000 -06:00 (Central Standard Time)'),later)
        self.assertEqual(reader._tag_assignment_time('2026-11-01T07:15:00Z'),later)
        for invalid in ('2026-11-01 07:15:00.000','not a timestamp','2026-11-01 07:15:00 +99:00'):
            with self.subTest(invalid=invalid):self.assertIsNone(reader._tag_assignment_time(invalid))

    def test_assignment_dates_use_raw_offset_text_without_orm_conversion_or_an_extra_query(self):
        from sqlalchemy import String, create_engine, event
        from sqlalchemy.orm import Session
        from pyrekordbox.db6.tables import DjmdSongMyTag
        engine=create_engine('sqlite:///:memory:');self.addCleanup(engine.dispose)
        DjmdSongMyTag.__table__.create(engine)
        session=Session(engine);self.addCleanup(session.close)
        defaults={column.name:('' if isinstance(column.type,String) else 0)
                  for column in DjmdSongMyTag.__table__.columns
                  if not column.nullable and column.default is None}
        raw={'early':'2026-11-01 06:30:00.000 +00:00',
             'later':'2026-11-01 07:15:00.000 +00:00',
             'malformed':'unparseable date','deleted':'2026-11-01 08:15:00.000 +00:00'}
        for identity in raw:
            session.add(DjmdSongMyTag(**{**defaults,'ID':identity,'ContentID':identity,
                'MyTagID':'leaf','rb_local_deleted':int(identity=='deleted')}))
        session.commit()
        for identity,stamp in raw.items():
            session.connection().exec_driver_sql('UPDATE djmdSongMyTag SET created_at=? WHERE ID=?',(stamp,identity))
        session.commit();session.expunge_all()
        statements=[]
        event.listen(engine,'before_cursor_execute',lambda _conn,_cursor,statement,_params,_context,_many:
                     statements.append(statement))
        definitions=[Row(ID='leaf',Attribute=0,Seq=1,Name='Leaf',ParentID='',rb_local_deleted=0)]
        db=Row(get_my_tag_songs=lambda:session.query(DjmdSongMyTag))
        times={}
        _tags,members=reader._read_tags(db,definitions=definitions,assignment_times=times)
        self.assertEqual(members,{identity:['leaf'] for identity in ('early','later','malformed')})
        self.assertEqual(times,{
            'early':{'leaf':datetime(2026,11,1,6,30,tzinfo=timezone.utc).timestamp()},
            'later':{'leaf':datetime(2026,11,1,7,15,tzinfo=timezone.utc).timestamp()}})
        self.assertEqual(times['later']['leaf']-times['early']['leaf'],45*60)
        selects=[statement for statement in statements if statement.lstrip().startswith('SELECT')]
        self.assertEqual(len(selects),1)


if __name__=='__main__':unittest.main()
