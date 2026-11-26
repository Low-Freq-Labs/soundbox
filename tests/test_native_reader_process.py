"""Fresh encrypted TEMP source observations through the production helper."""
from copy import deepcopy
from dataclasses import asdict, replace
from hashlib import sha256
import marshal
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from sqlalchemy import String, DateTime, Float
from sqlalchemy.dialects.sqlite import dialect
from sqlalchemy.schema import CreateTable
from pyrekordbox.db6 import tables

from soundbox import rekordbox_db, library_codec
from soundbox.library import Library, Playlist, Track
from soundbox.native_reader_process import NativeReaderProcess
from soundbox.rekordbox_write import _connect
from soundbox.worker_protocol import ProtocolError
from soundbox.worker_protocol import FRAME_BYTES


def fixture(path):
    connection = _connect(path, create=True)
    try:
        for table in tables.Base.metadata.sorted_tables:
            connection.execute(str(CreateTable(table).compile(dialect=dialect())))
        def insert(table, **values):
            row = {}
            for column in table.__table__.columns:
                if isinstance(column.type, (DateTime, tables.DateTime)):value = '2026-11-17 01:02:03.000 +00:00'
                elif isinstance(column.type, String):value = ''
                elif isinstance(column.type, Float):value = 0.
                else:value = 0
                row[column.name] = value
            row.update(values)
            columns = ','.join('"' + key + '"' for key in row)
            connection.execute(f'INSERT INTO "{table.__tablename__}" ({columns}) VALUES ({",".join("?" for _ in row)})', list(row.values()))
        insert(tables.DjmdProperty, DBID='123', DBVersion='6000')
        insert(tables.DjmdContent, ID='1', UUID='00000000-0000-4000-8000-000000000001',
            Title='日本語 - Song', ArtistID='', FolderPath='/generated/raw.wav', FileNameL='raw.wav',
            BPM=15500, Length=60, FileType=11, FileSize=100, Analysed=105)
        insert(tables.DjmdPlaylist, ID='2', UUID='00000000-0000-4000-8000-000000000002',
            Name='Manual', ParentID='root', Attribute=0, Seq=1)
        for index in (1, 2):
            insert(tables.DjmdSongPlaylist, ID='member-' + str(index), UUID='member-uuid-' + str(index),
                PlaylistID='2', ContentID='1', TrackNo=index)
        insert(tables.DjmdMyTag, ID='10', UUID='category-uuid', Name='Genre', Attribute=1, ParentID='root')
        insert(tables.DjmdMyTag, ID='11', UUID='tag-uuid', Name='Tag', Attribute=0, ParentID='10')
        insert(tables.DjmdSongMyTag, ID='assignment', UUID='assignment-uuid', MyTagID='11', ContentID='1')
        insert(tables.DjmdCue, ID='cue', UUID='cue-uuid', ContentID='1', ContentUUID='00000000-0000-4000-8000-000000000001',
            Kind=1, InMsec=24000, OutMsec=-1, Comment='Retain exact cue')
    finally:connection.close()


class NativeReaderProcessTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.source = self.root / 'master.db'
        fixture(self.source)
        self.reader = NativeReaderProcess(self.root)
        self.addCleanup(self.reader.close)

    def test_first_launch_preview_reads_real_encrypted_source_without_media_resolution(self):
        before = sha256(self.source.read_bytes()).hexdigest()
        expected = rekordbox_db.load_live(self.source, resolve_media_paths=False)
        with mock.patch.object(rekordbox_db, 'load_live', side_effect=AssertionError('No parent fallback')):
            actual = self.reader.read_preview(self.source)
        self.assertEqual(asdict(actual), asdict(expected))
        self.assertEqual(sha256(self.source.read_bytes()).hexdigest(), before)
        self.assertEqual(actual.source_library_id, '123')
        self.assertEqual(actual.tracks_by_id['1'].name, '日本語 - Song')
        self.assertFalse(list(self.root.glob('*authority*')))

    def previous(self):
        track = Track('logical', 'Old', '', 'file:///generated/canonical.wav',
            source_native_id='1', source_uuid='00000000-0000-4000-8000-000000000001',
            source_native_path='/generated/raw.wav')
        return Library({'logical': track}, [], self.source, source_kind='rekordbox-db', source_library_id='123')

    def test_default_helper_keeps_all_fresh_native_facts_and_exact_previous_canonical_scalars(self):
        before = sha256(self.source.read_bytes()).hexdigest()
        previous = self.previous()
        expected = rekordbox_db.load_metadata_readback(previous)
        self.reader.start()
        self.assertTrue(self.reader.available)
        with mock.patch.object(rekordbox_db, 'load_metadata_readback', side_effect=AssertionError('No parent reader fallback')):
            actual = self.reader.read_metadata(previous)
        self.assertEqual(asdict(actual), asdict(expected))
        self.assertEqual(actual.tracks_by_id['1'].location_uri, 'file:///generated/canonical.wav')
        self.assertEqual(actual.playlists[0].keys, ['1', '1'])
        self.assertTrue(actual.tracks_by_id['1'].source_cue_revision)
        self.assertTrue(actual.tracks_by_id['1'].source_cue_refs)
        self.assertEqual(actual.tracks_by_id['1'].source_tag_ids, ['11'])
        self.assertEqual(actual.source_tag_categories[0].category_id, '10')
        self.assertEqual(sha256(self.source.read_bytes()).hexdigest(), before)

    def test_each_read_observes_fresh_native_state_and_never_reuses_old_musical_arrays(self):
        previous = self.previous()
        first = self.reader.read_metadata(previous)
        connection = _connect(self.source)
        try:connection.execute("UPDATE djmdContent SET Title='New native title' WHERE ID='1'")
        finally:connection.close()
        second = self.reader.read_metadata(previous)
        self.assertEqual(second.tracks_by_id['1'].name, 'New native title')
        self.assertNotEqual(first.tracks_by_id['1'].name, second.tracks_by_id['1'].name)
        self.assertNotEqual(first.tracks_by_id['1'].source_cue_revision,
                            second.tracks_by_id['1'].source_cue_revision)
        self.assertEqual(second.tracks_by_id['1'].location_uri, first.tracks_by_id['1'].location_uri)
        self.assertIsNot(first.tracks_by_id['1'].source_cue_refs, second.tracks_by_id['1'].source_cue_refs)

    def test_ambiguous_or_unavailable_previous_identity_cannot_supply_a_canonical_alias(self):
        for unavailable in (False, True):
            with self.subTest(unavailable=unavailable):
                previous = self.previous()
                if unavailable:previous.source_binding_issues['logical'] = 'Historical binding unavailable'
                else:previous.tracks_by_id['different-dictionary-key'] = replace(previous.tracks_by_id['logical'], track_id='alias')
                actual = self.reader.read_metadata(previous)
                expected = rekordbox_db.load_metadata_readback(previous)
                self.assertEqual(asdict(actual), asdict(expected))
                self.assertEqual(actual.tracks_by_id['1'].location_uri, 'file://localhost/generated/raw.wav')

    def test_live_read_uses_the_same_reader_semantics_without_metadata_path_reuse(self):
        expected = rekordbox_db.load_live(self.source, retain_tracks={'123': {'1': 'uuid'}})
        actual = self.reader.read_live(self.source, retain_tracks={'123': {'1': 'uuid'}})
        self.assertEqual(asdict(actual), asdict(expected))

    def test_real_helper_preserves_large_native_records_headers_and_retained_inputs(self):
        # These are valid native strings, not cache records: the old fresh
        # reader accepted them without a cache-sized inventory ceiling.
        large = '日本語' * (FRAME_BYTES // 9 + 100)
        connection = _connect(self.source)
        try:
            connection.execute('UPDATE djmdProperty SET DBID=?', (large,))
            connection.execute('UPDATE djmdContent SET Title=? WHERE ID=?', (large, '1'))
            connection.execute('UPDATE djmdCue SET Comment=? WHERE ID=?', (large, 'cue'))
        finally:connection.close()
        previous = self.previous()
        previous.source_library_id = large
        previous.source_binding_issues['unrelated'] = large
        expected = rekordbox_db.load_metadata_readback(previous)
        self.assertGreater(len(marshal.dumps(library_codec.describe(expected))), FRAME_BYTES)
        self.assertTrue(any(len(marshal.dumps(rows)) > FRAME_BYTES
                            for _kind, _field, rows in library_codec.batches(expected)))
        before = sha256(self.source.read_bytes()).hexdigest()
        with mock.patch.object(rekordbox_db, 'load_metadata_readback',
                side_effect=AssertionError('No parent reader fallback')):
            actual = self.reader.read_metadata(previous)
        self.assertEqual(asdict(actual), asdict(expected))
        retained = {'native-library': {'retained-track': large}}
        self.assertGreater(len(marshal.dumps(retained)), FRAME_BYTES)
        expected_live = rekordbox_db.load_live(self.source, retain_tracks=retained)
        actual_live = self.reader.read_live(self.source, retain_tracks=retained)
        self.assertEqual(asdict(actual_live), asdict(expected_live))
        self.assertEqual(sha256(self.source.read_bytes()).hexdigest(), before)

    def test_failure_preserves_previous_library_and_never_runs_a_parent_fallback(self):
        previous = self.previous();before = deepcopy(asdict(previous))
        self.source.unlink()
        with mock.patch.object(rekordbox_db, 'load_metadata_readback', side_effect=AssertionError('No synchronous fallback')):
            with self.assertRaisesRegex(ProtocolError, 'pending changes are preserved'):
                self.reader.read_metadata(previous)
        self.assertEqual(asdict(previous), before)
        self.assertFalse(self.reader.available)

    def test_failed_read_retry_joins_old_process_then_freshly_recreates_reader_without_gui_fallback(self):
        previous = self.previous()
        first = self.reader.read_metadata(previous)
        old = self.reader.worker.process
        original = self.source.read_bytes()
        self.source.unlink()
        with self.assertRaises(ProtocolError):self.reader.read_metadata(previous)
        self.assertIs(self.reader.worker.process, old)
        self.source.write_bytes(original)
        with mock.patch.object(rekordbox_db, 'load_metadata_readback', side_effect=AssertionError('No parent fallback')):
            fresh = self.reader.read_metadata(previous)
        self.assertIsNot(self.reader.worker.process, old)
        self.assertIsNotNone(old.returncode)
        self.assertEqual(asdict(fresh), asdict(first))
        current = self.reader.worker.process
        self.reader.close()
        with self.assertRaises(ProtocolError):self.reader.read_metadata(previous)
        self.assertIs(self.reader.worker.process, current)

    def test_changed_native_library_id_is_attention_before_any_new_library_is_returned(self):
        previous = self.previous();before = deepcopy(asdict(previous))
        connection = _connect(self.source)
        try:connection.execute("UPDATE djmdProperty SET DBID='456'")
        finally:connection.close()
        with self.assertRaisesRegex(ProtocolError, 'source identity changed'):
            self.reader.read_metadata(previous)
        self.assertEqual(asdict(previous), before)


class FullLibraryWireFacts(unittest.TestCase):
    def test_nondefault_playlist_binding_visibility_and_nullable_cue_facts_survive(self):
        track = Track('record-id', 'Unicode 日本語', '', 'file:///never-open.wav',
            source_cues=None, source_tag_ids=[], source_cue_refs=[{'Kind': 17, 'opaque': None}],
            source_tag_times={'tag': 1.5}, source_cue_revision='revision')
        original = Library({'different-map-key': track}, [Playlist('P', ['Folder'], ['record-id'], '2', ['added'], 'legacy')],
            Path('/synthetic/master.db'), source_kind='rekordbox-db', source_library_id='123',
            hidden_ids={'hidden'}, removed_by_playlist={'2': {'removed'}}, title_junk=['rule'],
            source_binding_issues={'different-map-key': 'Keep unavailable'}, hide_untracked=True, hide_nokey=False)
        receiver = library_codec.LibraryReceiver(library_codec.describe(original), max_records=100)
        for kind, field, rows in library_codec.batches(original):receiver.add(kind, field, rows)
        self.assertEqual(asdict(receiver.finish()), asdict(original))


class NativeReaderRequestFences(unittest.TestCase):
    def test_invalid_rpc_counts_sequences_nonce_and_executable_operations_never_open_a_source(self):
        from soundbox import native_reader_worker
        nonce = 'a' * 32
        begin = ('begin', 1, nonce, 'metadata', ('/synthetic/master.db', 'rekordbox-db', '123'),
                 (('paths', 1), ('bindings', 0)))
        row = ('map-key', 'track-id', '1', 'uuid', 'file:///canonical.wav', '/raw.wav', False)
        cases = [
            [('begin', 1, nonce, 'apply', ('/synthetic/master.db',), ())],
            [('begin', True, nonce, 'metadata', begin[4], begin[5])],
            [('begin', 1, nonce, 'metadata', begin[4], (('paths', -1), ('bindings', 0)))],
            [('begin', 1, nonce, 'metadata', begin[4], (('paths', True), ('bindings', 0)))],
            [begin, ('read', 1, nonce)],
            [begin, ('input', 1, nonce, 2, 'paths', [row])],
            [begin, ('input', 1, nonce, True, 'paths', [row])],
            [begin, ('input', 1, 'b' * 32, 1, 'paths', [row])],
            [begin, ('input', 1, nonce, 1, 'paths', [row, row])],
        ]
        for messages in cases:
            with self.subTest(messages=messages):
                class Channel:
                    def __init__(self):self.messages = iter([*messages, ('stop',)]);self.sent = []
                    def receive(self):return next(self.messages), 100
                    def send(self, value):self.sent.append(value);return 100
                channel = Channel()
                with mock.patch.object(native_reader_worker.rekordbox_db, 'load_metadata_readback',
                        side_effect=AssertionError('No source read before complete safe request')) as read:
                    native_reader_worker.serve(channel, Path('/synthetic/state'))
                read.assert_not_called()
                self.assertTrue(any(message[0] == 'error' for message in channel.sent))
