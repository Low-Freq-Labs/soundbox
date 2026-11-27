"""Selected metadata ORM reads use generated SQLite only, with exact native IDs."""
from types import SimpleNamespace
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from sqlalchemy import create_engine, event, String
from sqlalchemy.orm import Session
from pyrekordbox.db6.tables import ContentFile, DjmdCue, DjmdSongMyTag, DjmdSongPlaylist

from soundbox.cdj_source import _selected_content_files, _selected_cues, capture_metadata
from soundbox.rekordbox_db import _read_tags
from soundbox.library import Library,Playlist


def row(table, **values):
    defaults={column.name:('' if isinstance(column.type,String) else 0)
              for column in table.__table__.columns if not column.nullable and column.default is None}
    return table(**{**defaults,**values})


class SelectedMetadata(unittest.TestCase):
    def setUp(self):
        engine=create_engine('sqlite:///:memory:');self.addCleanup(engine.dispose)
        for table in (ContentFile,DjmdCue,DjmdSongMyTag,DjmdSongPlaylist):table.__table__.create(engine)
        self.session=Session(engine);self.addCleanup(self.session.close)
        self.sql=[]
        event.listen(engine,'before_cursor_execute',lambda _conn,_cursor,statement,parameters,_context,_many:
                     self.sql.append((statement,parameters)))
        self.db=SimpleNamespace(get_content_file=lambda:self.session.query(ContentFile),
            get_my_tag_songs=lambda:self.session.query(DjmdSongMyTag),get_my_tag=Mock(),
            get_cue=lambda:self.session.query(DjmdCue))

    def test_chunked_queries_equal_full_exact_filter_including_lexical_ids_and_duplicate_links(self):
        identities=[str(i) for i in range(1002)]+['0001']
        for index,identity in enumerate(identities):
            self.session.add(row(ContentFile,ID=str(index),ContentID=identity,Path='/ANLZ0000.DAT'))
            self.session.add(row(DjmdSongMyTag,ID=str(index),ContentID=identity,MyTagID='leaf'))
        self.session.add(row(DjmdSongMyTag,ID='duplicate',ContentID='1',MyTagID='leaf'))
        self.session.add(row(DjmdSongMyTag,ID='unknown',ContentID='1',MyTagID='unknown'))
        self.session.add(row(DjmdSongMyTag,ID='deleted',ContentID='1',MyTagID='second',rb_local_deleted=1))
        self.session.commit()
        definitions=[SimpleNamespace(ID='category',Attribute=1,Seq=1,Name='Category',ParentID='root',UUID='',rb_local_deleted=0),
                     SimpleNamespace(ID='leaf',Attribute=0,Seq=1,Name='Leaf',ParentID='category',UUID='',rb_local_deleted=0),
                     SimpleNamespace(ID='leaf',Attribute=0,Seq=1,Name='Duplicate definition',ParentID='category',UUID='',rb_local_deleted=0),
                     SimpleNamespace(ID='second',Attribute=0,Seq=2,Name='Second',ParentID='category',UUID='',rb_local_deleted=0)]
        wanted=set(identities)-{'0','1'}
        expected_files={file.ID for file in self.db.get_content_file() if str(file.ContentID) in wanted}
        all_times={}
        expected_tags,all_members=_read_tags(self.db,definitions=definitions,assignment_times=all_times)
        self.session.expunge_all();self.sql.clear()
        actual={file.ID for file in _selected_content_files(self.db,wanted)}
        selected_times={}
        actual_tags,members=_read_tags(self.db,track_ids=wanted,definitions=definitions,assignment_times=selected_times)
        self.assertEqual(actual,expected_files)
        self.assertEqual(actual_tags,expected_tags)
        self.assertEqual(members,{tid:tags for tid,tags in all_members.items() if tid in wanted})
        self.assertEqual(selected_times,{tid:times for tid,times in all_times.items() if tid in wanted})
        self.assertNotIn('1',members);self.assertEqual(members['0001'],['leaf','leaf'])
        selects=[parameters for sql,parameters in self.sql if sql.lstrip().startswith('SELECT')]
        self.assertEqual(len(selects),6)
        self.assertTrue(all(len(parameters)<=500 for parameters in selects))
        self.db.get_my_tag.assert_not_called()

    def test_cue_queries_materialize_only_selected_exact_ids_in_bounded_chunks(self):
        identities = [str(index) for index in range(1002)] + ['0001']
        for index, identity in enumerate(identities):
            self.session.add(row(DjmdCue, ID=str(index), ContentID=identity, ContentUUID='fixture', Kind=0))
        self.session.commit()
        wanted = set(identities) - {'1'}
        self.sql.clear()
        actual = list(_selected_cues(self.db, wanted))
        self.assertEqual({str(cue.ContentID) for cue in actual}, wanted)
        self.assertNotIn('1', {str(cue.ContentID) for cue in actual})
        selects = [parameters for sql, parameters in self.sql if sql.lstrip().startswith('SELECT')]
        self.assertEqual(len(selects), 3)
        self.assertTrue(all(len(parameters) <= 500 for parameters in selects))
        db = SimpleNamespace(get_cue=Mock(side_effect=AssertionError('Empty cue query')))
        self.assertEqual(list(_selected_cues(db, set())), [])

    def test_selected_playlist_capture_skips_unrelated_smart_evaluation_and_membership_rows(self):
        from soundbox.rekordbox_db import _read_playlists
        nodes = [SimpleNamespace(ID='01', ParentID='root', Name='Selected', Attribute=0, Seq=1, rb_local_deleted=0),
                 SimpleNamespace(ID='2', ParentID='root', Name='Other normal', Attribute=0, Seq=2, rb_local_deleted=0),
                 SimpleNamespace(ID='3', ParentID='root', Name='Other smart', Attribute=4, Seq=3, rb_local_deleted=0)]
        links = [('first', '01', '17', 2, 0), ('second', '01', '18', 1, 0),
                 ('deleted', '01', '19', 3, 1), ('other', '2', '20', 1, 0)]
        for identity, playlist, content, order, deleted in links:
            self.session.add(row(DjmdSongPlaylist, ID=identity, PlaylistID=playlist,
                                 ContentID=content, TrackNo=order, rb_local_deleted=deleted))
        self.session.commit()
        db = SimpleNamespace(get_playlist=lambda: nodes, query=self.session.query)
        self.sql.clear()
        with patch('soundbox.rekordbox_db._smart_playlist_contents', side_effect=AssertionError('Unselected smart playlist')):
            playlists = _read_playlists(db, {'17', '18', '19', '20'}, selected_ids={'01'})
        self.assertEqual([(p.pid, p.name, p.keys) for p in playlists], [('01', 'Selected', ['18', '17'])])
        selects = [parameters for sql, parameters in self.sql if sql.lstrip().startswith('SELECT')]
        self.assertEqual(selects, [('01',)])
        self.sql.clear()
        self.assertEqual(_read_playlists(db, set(), selected_ids=set()), [])
        self.assertEqual(self.sql, [])

    def test_empty_selection_skips_queries_and_list_fallback_keeps_exact_filter(self):
        files=[SimpleNamespace(ContentID='1'),SimpleNamespace(ContentID='01')]
        db=SimpleNamespace(get_content_file=Mock(return_value=files))
        self.assertEqual(list(_selected_content_files(db,set())),[])
        db.get_content_file.assert_not_called()
        self.assertEqual(list(_selected_content_files(db,{'01'})),[files[1]])


class CapturedGlobalRevision(unittest.TestCase):
    def test_destination_projection_never_changes_canonical_native_playlist_proof(self):
        with tempfile.TemporaryDirectory() as area:
            source=Path(area)/'master.db';source.write_bytes(b'generated source identity')
            canonical=Playlist('Bass',['SETS','HAMDI'],[],'20')
            library=Library({},[canonical],source,source_kind='rekordbox-db',source_library_id='42')
            selected=replace(canonical,export_folder_path=['HAMDI'])
            db=Mock();db.get_property.return_value=[SimpleNamespace(DBID='42')]
            db.get_cue.return_value=[];db.get_my_tag.return_value=[]
            with patch('soundbox.rekordbox_db._copy_stable_snapshot'), \
                    patch('pyrekordbox.Rekordbox6Database',return_value=db), \
                    patch('soundbox.rekordbox_db._content_rows',return_value=[]), \
                    patch('soundbox.rekordbox_db._active_track_ids',return_value=set()), \
                    patch('soundbox.rekordbox_db._read_playlists',return_value=[canonical]) as fresh:
                ordinary=capture_metadata(library,[],playlists=[canonical])
                projected=capture_metadata(library,[],playlists=[selected])
                self.assertEqual(ordinary['source_revision'],projected['source_revision'])
                fresh.return_value=[replace(canonical,folder_path=['OTHER','HAMDI'])]
                with self.assertRaisesRegex(ValueError,'Source playlist changed'):
                    capture_metadata(library,[],playlists=[selected])

    def test_full_rendered_tag_tree_and_order_bind_revision_without_unrelated_counters(self):
        with tempfile.TemporaryDirectory() as area:
            source=Path(area)/'master.db';source.write_bytes(b'explicit synthetic source identity')
            library=Library({},[],source,source_kind='rekordbox-db',source_library_id='42')
            definitions=[SimpleNamespace(ID='1',Attribute=1,ParentID='root',Seq=1,Name='Category',rb_local_deleted=0),
                         SimpleNamespace(ID='2',Attribute=0,ParentID='1',Seq=1,Name='Unassigned',rb_local_deleted=0)]
            def capture(values):
                db=Mock();db.get_property.return_value=[SimpleNamespace(DBID='42')]
                db.get_cue.return_value=[];db.get_my_tag.return_value=values
                with patch('soundbox.rekordbox_db._copy_stable_snapshot') as snapshot, \
                        patch('pyrekordbox.Rekordbox6Database',return_value=db), \
                        patch('soundbox.rekordbox_db._content_rows',return_value=[]):
                    result=capture_metadata(library,[])
                snapshot.assert_called_once();db.get_my_tag.assert_called_once();db.close.assert_called_once()
                db.get_my_tag_songs.assert_not_called();db.get_content_file.assert_not_called()
                return result
            original=capture(definitions)
            for field,value in (('Name','Renamed'),('Seq',3),('ParentID','root'),('Attribute',1)):
                changed=deepcopy(definitions);setattr(changed[1],field,value)
                with self.subTest(field=field):
                    current=capture(changed)
                    self.assertNotEqual(current['my_tags'],original['my_tags'])
                    self.assertNotEqual(current['source_revision'],original['source_revision'])
            self.assertNotEqual(capture(list(reversed(definitions)))['source_revision'],original['source_revision'])
            changed=deepcopy(definitions);changed[0].rb_local_usn=1000
            self.assertEqual(capture(changed)['source_revision'],original['source_revision'])
