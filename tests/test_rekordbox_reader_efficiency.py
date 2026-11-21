"""Equivalent full native reads on generated SQLite; no actual source access."""
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

from sqlalchemy import String, create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateTable
from pyrekordbox.db6.tables import (
    ContentCue, DjmdAlbum, DjmdArtist, DjmdContent, DjmdCue, DjmdGenre, DjmdKey, DjmdLabel,
    DjmdMyTag, DjmdSongMyTag, DjmdSongPlaylist,
)

from soundbox import rekordbox_db as reader


def row(table, **values):
    defaults = {column.name: ('' if isinstance(column.type, String) else 0)
                for column in table.__table__.columns
                if not column.nullable and column.default is None}
    return table(**{**defaults, **values})


class NativeReaderEfficiency(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        self.addCleanup(self.engine.dispose)
        for table in (DjmdArtist, DjmdKey, DjmdGenre, DjmdAlbum, DjmdLabel, DjmdCue, ContentCue):
            table.__table__.create(self.engine)
        # Real native SQLite can contain legacy NULL flags/types despite ORM
        # declarations. Preserve that edge in the generated fixture.
        with self.engine.begin() as connection:
            connection.exec_driver_sql(str(CreateTable(DjmdContent.__table__).compile(self.engine)).replace('NOT NULL', ''))
        self.session = Session(self.engine)
        self.addCleanup(self.session.close)
        self.sql = []
        event.listen(self.engine, 'before_cursor_execute',
                     lambda _conn, _cursor, statement, parameters, _context, _many:
                     self.sql.append((statement, parameters)))
        self.db = SimpleNamespace(get_content=lambda: self.session.query(DjmdContent), session=self.session)
        self.area = tempfile.TemporaryDirectory()
        self.addCleanup(self.area.cleanup)
        self.source = Path(self.area.name) / 'master.db'
        self.session.add(row(DjmdArtist, ID='artist', Name='Shared artist'))
        self.session.add(row(DjmdGenre, ID='genre', Name='Shared genre'))
        self.session.add(row(DjmdKey, ID='key', ScaleName='Cm'))
        self.session.add(row(DjmdAlbum, ID='album', Name='Shared album'))
        self.session.add(row(DjmdLabel, ID='label', Name='Shared label'))
        stamp = datetime(2026, 11, 4, 1, 2, 3)
        for index in range(1205):
            identity = '0001' if index == 1204 else str(index)
            self.session.add(row(DjmdContent, ID=identity, UUID='uuid-' + identity,
                Title='Track ' + identity, FolderPath='/generated/' + identity + '.wav',
                ArtistID='artist', GenreID='genre', KeyID='key', AlbumID='album', LabelID='label',
                BPM=14000, Length=180, FileSize=1000, FileType=(1, 4, 5, 6, 11, 12, 19)[index % 7],
                created_at=stamp, updated_at=stamp, rb_local_deleted=int(index % 17 == 0)))
        self.session.add(row(DjmdCue, ID='cue', ContentID='1', ContentUUID='uuid-1',
                             Kind=1, InMsec=1000, OutMsec=-1, Comment='Exact cue'))
        self.session.commit()
        with self.engine.begin() as connection:
            connection.exec_driver_sql('UPDATE djmdContent SET rb_local_deleted=NULL WHERE ID=?', ('1',))
            connection.exec_driver_sql('UPDATE djmdContent SET FileType=NULL WHERE ID=?', ('2',))
        self.session.expunge_all()

    def test_combined_classification_equals_two_queries_including_lexical_null_and_placeholder_ids(self):
        expected_active = reader._active_track_ids(self.db)
        expected_audio = reader._audio_track_ids(self.db)
        self.sql.clear()
        active, audio = reader._track_id_sets(self.db)
        self.assertEqual((active, audio), (expected_active, expected_audio))
        self.assertIn('1', audio)
        self.assertIn('2', active)
        self.assertNotIn('2', audio)
        self.assertIn('0001', active)
        self.assertNotIn('0', active)
        self.assertEqual(len(self.sql), 1)

    def test_full_audio_predicate_preserves_every_normalized_and_raw_cue_field_without_n_plus_one(self):
        audio = reader._audio_track_ids(self.db)
        tags = {identity: ['tag', 'tag'] for identity in audio}
        expected = reader._read_tracks(self.db, self.source, tags, track_ids=audio)
        reader._populate_cue_edit_state(self.db, expected)
        expected = {identity: asdict(track) for identity, track in expected.items()}
        self.session.expunge_all()
        self.sql.clear()
        actual = reader._read_tracks(self.db, self.source, tags, track_ids=audio, full_audio=True)
        reader._populate_cue_edit_state(self.db, actual)
        actual = {identity: asdict(track) for identity, track in actual.items()}
        self.assertEqual(list(actual), list(expected))
        self.assertEqual(actual, expected)
        self.assertEqual(actual['1']['source_cues'][0]['label'], 'Exact cue')
        self.assertEqual(actual['1']['info']['album'], 'Shared album')
        self.assertEqual(actual['1']['source_tag_ids'], ['tag', 'tag'])
        selects = [statement for statement, _parameters in self.sql if statement.lstrip().startswith('SELECT')]
        # One joined content query, one display cue query, three raw identity
        # tables. Scalar eager joins require no per-track metadata queries.
        self.assertEqual(len(selects), 5)
        content = [statement for statement in selects if 'LEFT OUTER JOIN' in statement]
        self.assertEqual(len(content), 1)

    def test_selected_id_reader_stays_bounded_and_exact(self):
        selected = {'1', '0001'}
        self.sql.clear()
        actual = reader._read_tracks(self.db, self.source, track_ids=selected)
        self.assertEqual(set(actual), selected)
        content = [parameters for statement, parameters in self.sql if 'LEFT OUTER JOIN' in statement]
        self.assertEqual(len(content), 1)
        self.assertEqual(set(content[0]), selected)

    def test_smart_membership_fetches_only_ids_with_exact_fresh_predicates_and_order(self):
        from soundbox.rekordbox_playlist_plan import smart_tag_rule
        for table in (DjmdMyTag, DjmdSongMyTag):
            table.__table__.create(self.engine)
        self.session.add(row(DjmdMyTag, ID='201', Name='Genre tag', rb_local_deleted=0))
        self.session.add(row(DjmdMyTag, ID='202', Name='Deleted tag', rb_local_deleted=1))
        for index, (content, tag, deleted) in enumerate((
                ('1', '201', 0), ('0001', '201', 0), ('3', '201', 1),
                ('4', '202', 0), ('0', '201', 0))):
            self.session.add(row(DjmdSongMyTag, ID=str(index), ContentID=content,
                                 MyTagID=tag, rb_local_deleted=deleted))
        self.session.commit()
        node = SimpleNamespace(ID='301', Name='Fresh smart', Attribute=4,
            ParentID='root', Seq=1, rb_local_deleted=0,
            SmartList=smart_tag_rule('301', ['201', '202']))
        self.db.get_playlist = lambda: [node]
        self.db.query = self.session.query
        active = reader._active_track_ids(self.db)
        expected = [str(content.ID) for content in reader._smart_playlist_contents(self.db, node)
                    if str(content.ID) in active]
        self.assertEqual(set(expected), {'1', '0001'})
        self.session.expunge_all()
        self.sql.clear()
        result = reader._read_playlists(self.db, active, selected_ids={'301'})
        self.assertEqual(result[0].keys, expected)
        self.assertEqual(len(self.sql), 1)
        self.assertEqual(self.sql[0][0].split('\nFROM ', 1)[0],
                         'SELECT "djmdContent"."ID" AS "djmdContent_ID" ')
        self.assertFalse(self.session.identity_map)


class NativeMembershipEfficiency(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine('sqlite:///:memory:');self.addCleanup(self.engine.dispose)
        with self.engine.begin() as connection:
            connection.exec_driver_sql(str(CreateTable(DjmdSongPlaylist.__table__).compile(self.engine)).replace('NOT NULL',''))
            rows=[dict(ID=str(index),PlaylistID='p',ContentID='track-'+str(index),TrackNo=1205-index,
                       rb_local_deleted=1 if index%17==0 else None if index%19==0 else 0) for index in range(1205)]
            rows.extend([dict(ID='history',PlaylistID='deleted',ContentID='track-history',TrackNo=None,rb_local_deleted=0),
                         dict(ID='other',PlaylistID='q',ContentID='track-other',TrackNo=1,rb_local_deleted=0)])
            connection.execute(DjmdSongPlaylist.__table__.insert(),rows)
        self.session=Session(self.engine);self.addCleanup(self.session.close)
        self.nodes=[SimpleNamespace(ID=identity,Name=name,Attribute=0,ParentID='root',Seq=index+1,
            rb_local_deleted=int(identity=='deleted')) for index,(identity,name) in enumerate(
                (('p','Manual order'),('q','Other'),('deleted','Historical')))]
        self.db=SimpleNamespace(get_playlist=lambda:self.nodes,query=self.session.query)
        self.statements=[]
        event.listen(self.engine,'before_cursor_execute',lambda _c,_cur,statement,parameters,_ctx,_many:
            self.statements.append((statement,parameters)))

    def legacy(self,active,selected=None):
        class LegacyRows:
            def all(inner):return self.session.query(DjmdSongPlaylist).all()
        db=SimpleNamespace(get_playlist=lambda:self.nodes,query=lambda _entity:LegacyRows())
        result=reader._read_playlists(db,active,selected_ids=selected)
        self.session.expunge_all();return result

    def test_bounded_scalar_memberships_match_full_mapped_reader_without_orm_identity_debt(self):
        active={'track-'+str(index) for index in range(1205) if index%13} | {'track-other'}
        expected=self.legacy(active);self.statements.clear()
        actual=reader._read_playlists(self.db,active)
        self.assertEqual(actual,expected)
        self.assertEqual(actual[0].keys,[f'track-{index}' for index in reversed(range(1205)) if index%13 and index%17])
        self.assertEqual(actual[1].keys,['track-other'])
        self.assertFalse(self.session.identity_map)
        self.assertEqual(len(self.statements),1)
        self.assertNotIn('created_at',self.statements[0][0])

    def test_selected_scope_and_duplicate_track_number_guards_match_full_mapped_reader(self):
        active={'track-'+str(index) for index in range(1205)} | {'track-other'}
        expected=self.legacy(active,{'q'})
        self.assertEqual(reader._read_playlists(self.db,active,selected_ids={'q'}),expected)
        with self.engine.begin() as connection:
            connection.exec_driver_sql('UPDATE djmdSongPlaylist SET TrackNo=7 WHERE ID IN (?,?)',('1','2'))
        self.assertEqual(reader._read_playlists(self.db,active,selected_ids={'q'}),expected)
        with self.assertRaisesRegex(ValueError,'duplicate TrackNo'):
            reader._read_playlists(self.db,active)
        with self.assertRaisesRegex(ValueError,'duplicate TrackNo'):self.legacy(active)

    def test_selected_smart_only_view_does_not_query_unselected_manual_memberships(self):
        smart=SimpleNamespace(ID='smart',Name='Smart',Attribute=4,ParentID='root',Seq=1,rb_local_deleted=0)
        db=SimpleNamespace(get_playlist=lambda:[smart],query=mock.Mock(side_effect=AssertionError('No manual membership read')),
            get_playlist_contents=lambda _node:[SimpleNamespace(ID='track-other')])
        result=reader._read_playlists(db,{'track-other'},selected_ids={'smart'})
        self.assertEqual([(playlist.pid,playlist.keys) for playlist in result],[('smart',['track-other'])])
        db.query.assert_not_called()
