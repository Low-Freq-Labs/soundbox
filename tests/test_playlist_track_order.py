"""Browser defaults preserve set occurrences without changing source membership."""
from copy import deepcopy
from pathlib import Path
import shutil
import tempfile
from types import MethodType, SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtCore import Qt

from desktop.app import MainWindow
from desktop.model import COLUMNS, TrackModel
from desktop.rekordbox_playlists import NativePlaylistsMixin
from soundbox.library import Library, Playlist, Track
from soundbox import playlist_track_order, rekordbox_playlist_store as store, rekordbox_playlist_plan as plans
from soundbox.rekordbox_write import WriteProofError


class PlaylistTrackOrder(unittest.TestCase):
    def host(self, *, smart=False, native=True):
        tracks = {identity: Track(identity, title, '', f'file:///never-read/{identity}.wav',
                                 added_ts=stamp, source_uuid='track-' + identity)
                  for identity, title, stamp in [('one', 'Zulu', 10), ('two', 'Alpha', 30)]}
        playlist = Playlist('Set', [], ['one', 'two', 'one'], '100')
        catalog = dict(nodes=[dict(ID='100', UUID='playlist', Attribute=4 if smart else 0,
                                   SmartList='')], members=[
            dict(ID=str(index), UUID=f'member-{index}', PlaylistID='100', ContentID=identity,
                 TrackNo=index, rb_local_deleted=0)
            for index, identity in enumerate(playlist.keys, 1)]) if native else {}
        library = Library(tracks, [playlist], Path('/synthetic/library'),
                          source_kind='rekordbox-db' if native else 'rekordbox-xml',
                          source_library_id='library' if native else '', source_playlist_catalog=catalog)
        host = SimpleNamespace(lib=library, settings={}, _landed_cache={},
                               _row_for=lambda track: dict(id=track.track_id, title=track.name),
                               _native_view_playlist=lambda path: playlist,
                               _native_presentation=lambda: library)
        host._landed_time = MethodType(MainWindow._landed_time, host)
        host._rows_for = MethodType(MainWindow._rows_for, host)
        host._native_membership_rows = MethodType(NativePlaylistsMixin._native_membership_rows, host)
        host._native_row_identity = NativePlaylistsMixin._native_row_identity
        return host

    def test_regular_native_set_preserves_authored_order_and_duplicate_memberships(self):
        host = self.host(); before = deepcopy(host.lib)
        rows = MainWindow._playlist_rows(host, 'Set')
        self.assertEqual([row['id'] for row in rows], ['one', 'two', 'one'])
        self.assertEqual([row['_native_membership']['ID'] for row in rows], ['1', '2', '3'])
        self.assertEqual(host.lib, before)

    def test_xml_set_preserves_authored_order_without_resolving_unknown_add_times(self):
        host = self.host(native=False)
        host._landed_time = lambda track: self.fail('Regular set order must not inspect add times')
        self.assertEqual([row['id'] for row in MainWindow._playlist_rows(host, 'Set')],
                         ['one', 'two', 'one'])

    def test_smart_and_collection_keep_newest_first(self):
        host = self.host(smart=True)
        self.assertEqual([row['id'] for row in MainWindow._playlist_rows(host, 'Set')],
                         ['two', 'one', 'one'])
        self.assertEqual([row['id'] for row in host._rows_for(host.lib.tracks_by_id.values())],
                         ['two', 'one'])

    def test_explicit_header_sort_and_clear_restore_authored_occurrences(self):
        host = self.host(); rows = MainWindow._playlist_rows(host, 'Set')
        model = TrackModel(rows)
        model.sort(next(i for i, column in enumerate(COLUMNS) if column[0] == 'title'),
                   Qt.AscendingOrder)
        self.assertEqual([row['id'] for row in model._rows], ['two', 'one', 'one'])
        model.sort(-1)
        self.assertEqual([row['_native_membership']['ID'] for row in model._rows], ['1', '2', '3'])

    def test_local_custom_set_keeps_order_on_reopen(self):
        host = self.host(native=False); rendered = []
        host.custom = {'Set': ['one', 'two', 'one']}; host.hidden = set()
        host._annotate_art = lambda rows: rows
        host._set_refreshed_rows = rendered.extend
        host.stack = SimpleNamespace(setCurrentIndex=lambda index: None)
        MainWindow._show_custom(host, 'Set')
        self.assertEqual([row['id'] for row in rendered], ['one', 'two', 'one'])


class NativeTrackOrderBatch(unittest.TestCase):
    def setUp(self):
        from tests.test_playlist_transaction import fixture
        from soundbox.rekordbox_tags import _schema, NATIVE_VERSION
        from soundbox.rekordbox_write import _connect
        self.area = tempfile.TemporaryDirectory(); self.addCleanup(self.area.cleanup)
        self.root = Path(self.area.name).resolve(); self.source = self.root / 'source'; self.source.mkdir()
        fixture(self.source)
        with mock.patch('soundbox.rekordbox_cue_store._closed', return_value=None), \
                mock.patch('soundbox.rekordbox_cue_store._installed_version', return_value=NATIVE_VERSION):
            conn = _connect(self.source / 'master.db', readonly=True)
            try:schema = _schema(conn)
            finally:conn.close()
        self.authority = store.PlaylistAuthority(str(self.source / 'master.db'), 'synthetic-playlists', schema, True)
        self.writer = store.PlaylistStore(self.authority, self.root / 'recovery')
        self.enterContext(mock.patch('soundbox.rekordbox_cue_store._closed', return_value=None))
        self.enterContext(mock.patch('soundbox.rekordbox_cue_store._installed_version', return_value=NATIVE_VERSION))
        self.tracks = {identity: Track(identity, identity, '', f'file:///never-open/{identity}.wav', source_uuid=uuid)
                       for identity, uuid in [('track', 'uuid'), ('track-two', 'uuid-two')]}
        initial = ['track', 'track-two', 'track', 'track', 'track-two']
        self.writer.apply(store.request_for(self.writer.snapshot(), 'append', identity='1000',
                                           tracks=[self.tracks[key] for key in initial], allow_duplicate=True))

    def library(self):
        catalog = self.writer.snapshot()
        return Library(self.tracks, [], self.source / 'master.db', source_kind='rekordbox-db',
                       source_library_id='synthetic-playlists', source_playlist_catalog=catalog)

    def members(self, catalog=None):
        return sorted((row for row in (catalog or self.writer.snapshot())['members']
                       if row['PlaylistID'] == '1000' and row['rb_local_deleted'] == 0),
                      key=lambda row: row['TrackNo'])

    def state(self, writer=None):
        from soundbox.rekordbox_write import _connect, _state
        conn = _connect((writer or self.writer).database, readonly=True)
        try:return _state(conn)
        finally:conn.close()

    def test_reposition_equals_accepted_sequential_commands_with_duplicates_and_hidden_tail(self):
        from dataclasses import replace
        library = self.library(); rows = self.members()
        library.hidden_ids = {'track'}
        request, pairs = playlist_track_order.request(library, '1000', [rows[-1]], rows[2])
        other = self.root / 'sequential'; shutil.copytree(self.source, other)
        sequential = store.PlaylistStore(replace(self.authority, database=str(other / 'master.db')),
                                        self.root / 'sequential-recovery')
        before = self.state(); self.writer.apply(request)
        for child in request['requests']:
            sequential.apply({**child, 'revision': sequential.snapshot()['revision']})
        self.assertEqual(self.state(), self.state(sequential))
        self.assertEqual(self.writer.xml.read_bytes(), sequential.xml.read_bytes())
        actual = self.members()
        self.assertEqual([row['ContentID'] for row in actual],
                         ['track', 'track-two', 'track-two', 'track', 'track'])
        self.assertEqual([row['ID'] for row in actual[:2]], [row['ID'] for row in rows[:2]])
        self.assertEqual([old['ID'] for old, new in pairs], [rows[2]['ID'], rows[3]['ID']])
        for table in before['tables']:
            if table not in ('djmdSongPlaylist', 'agentRegistry'):
                self.assertEqual(self.state()['tables'][table], before['tables'][table], table)
        saved = self.state(); self.writer.apply(request); self.assertEqual(self.state(), saved)

    def test_flat_copy_plan_has_exact_original_canonical_bytes_and_detached_results(self):
        from soundbox.rekordbox_write import _connect,_json
        library=self.library();rows=self.members()
        request,_=playlist_track_order.request(library,'1000',[rows[-1]],rows[1])
        xml=self.writer.xml.read_bytes();conn=_connect(self.writer.database,readonly=True)
        try:
            with (mock.patch.object(plans,'_copy_row',deepcopy),
                  mock.patch.object(plans,'_copy_rows',deepcopy)):
                original=store.build_plan(conn,request,xml)
            actual=store.build_plan(conn,request,xml)
        finally:conn.close()
        encode=lambda plan:_json({**plan,'xml':plan['xml'].hex()})
        self.assertEqual(encode(actual),encode(original))
        self.assertEqual(store.planner._digest(actual['changes']),store.planner._digest(original['changes']))
        actual['changes'][0]['before']['updated_at']='detached mutation'
        self.assertNotEqual(actual,original)
        self.assertEqual(self.members(),rows)

    def test_flat_row_copies_preserve_aliasing_fallbacks_and_mutation_isolation(self):
        class CustomText(str):pass
        class CustomRow(dict):pass
        row={'text':'hello','integer':1,'float':1.5,'bool':True,'blob':b'bytes','null':None}
        actual=plans._copy_row(row)
        self.assertEqual(actual,deepcopy(row));self.assertIsNot(actual,row)
        actual['text']='changed';self.assertEqual(row['text'],'hello')
        copied=plans._copy_rows([row,row])
        self.assertIs(copied[0],copied[1]);self.assertIsNot(copied[0],row)
        shared=[];left={'nested':shared};right={'nested':shared}
        copied=plans._copy_rows([left,right,left])
        self.assertIs(copied[0],copied[2]);self.assertIs(copied[0]['nested'],copied[1]['nested'])
        copied[0]['nested'].append('changed');self.assertEqual(shared,[])
        cycle={};cycle['self']=cycle
        copied=plans._copy_row(cycle);self.assertIs(copied['self'],copied)
        container=[];container.append(container)
        copied=plans._copy_rows(container);self.assertIs(copied[0],copied)
        for row in (CustomRow(value=[]),{'custom':CustomText('hello')},{'buffer':bytearray(b'bytes')}):
            with self.subTest(row=row):
                actual=plans._copy_row(row);expected=deepcopy(row)
                self.assertEqual(actual,expected);self.assertIs(type(actual),type(expected))
                self.assertEqual([type(value) for value in actual.values()],
                                 [type(value) for value in expected.values()])
        with self.assertRaises(TypeError):plans._copy_row({'uncopyable':memoryview(b'bytes')})

    def test_noop_and_replaced_occurrence_cannot_publish_a_guess(self):
        library = self.library(); rows = self.members()
        self.assertEqual(playlist_track_order.request(library, '1000', [rows[2], rows[3]], rows[4]), (None, []))
        selected = {**rows[3], 'UUID': 'replaced'}
        with self.assertRaises(WriteProofError):
            playlist_track_order.request(library, '1000', [selected], rows[1])

    def test_fast_marked_copy_equals_deepcopy_and_detaches_all_mutable_intent_parts(self):
        library = self.library(); rows = self.members()
        request, _ = playlist_track_order.request(library, '1000', [rows[-1]], rows[0])
        jobs = [dict(request=request, source='source', error='', removal_overlay={'entries': []})]
        copy = playlist_track_order.copy_intents(jobs)
        self.assertEqual(copy, deepcopy(jobs))
        copy[0]['request']['requests'][0]['tracks'][0]['membership_id'] = 'different'
        copy[0]['request']['requests'][1]['tracks'].clear()
        copy[0]['removal_overlay']['entries'].append('different')
        self.assertNotEqual(copy[0]['request'], request)
        self.assertTrue(request['requests'][1]['tracks'])
        self.assertEqual(jobs[0]['removal_overlay'], {'entries': []})

    def test_nonflat_or_unmarked_copy_retains_generic_deepcopy_and_alias_isolation(self):
        for value in ({'request': {'operation': 'append', 'tracks': [{'nested': []}]}},
                      {'request': {'operation_id': 'a', 'operation': 'batch', 'revision': 'b',
                                   'requests': [{'tracks': [{'nested': []}]}, {'tracks': []}],
                                   'plan_encoding': 'membership-replay-v1'}}):
            with self.subTest(value=value):
                copy = playlist_track_order.copy_intents(value)
                self.assertEqual(copy, deepcopy(value))
                nested = copy['request'].get('tracks') or copy['request']['requests'][0]['tracks']
                nested[0]['nested'].append('different')
                self.assertNotEqual(copy, value)

    def test_nonplain_keys_use_deepcopy_at_every_marked_envelope_level(self):
        class MutableKey(str):
            pass
        library = self.library(); rows = self.members()
        request, _ = playlist_track_order.request(library, '1000', [rows[-1]], rows[0])
        for level in ('envelope', 'child', 'track'):
            with self.subTest(level=level):
                value = deepcopy(request)
                mapping = (value if level == 'envelope' else value['requests'][0] if level == 'child'
                           else value['requests'][0]['tracks'][0])
                name = next(iter(mapping)); key = MutableKey(name); key.state = {'nested': []}
                mapping[key] = mapping.pop(name)
                copied = playlist_track_order.copy_intents(value)
                self.assertEqual(copied, deepcopy(value))
                clone = (copied if level == 'envelope' else copied['requests'][0] if level == 'child'
                         else copied['requests'][0]['tracks'][0])
                cloned_key = next(item for item in clone if isinstance(item, MutableKey))
                self.assertIsNot(cloned_key, key)
                cloned_key.state['nested'].append('isolated')
                self.assertEqual(key.state, {'nested': []})

    def test_large_moves_use_compact_replay_and_preserve_the_existing_batch_bound(self):
        from soundbox.rekordbox_playlist_plan import revision
        library = self.library(); catalog = library.source_playlist_catalog
        template = self.members()[0]
        rows = [{**template, 'ID': f'entry-{i}', 'UUID': f'00000000-0000-4000-8000-{i:012d}', 'TrackNo': i + 1}
                for i in range(2917)]
        catalog['members'] = rows; catalog['revision'] = revision(catalog['nodes'], rows)
        request, replacements = playlist_track_order.request(library, '1000', [rows[0]])
        self.assertEqual([len(child['tracks']) for child in request['requests']], [1, 1])
        self.assertEqual([old['ID'] for old, new in replacements], ['entry-0'])
        request, replacements = playlist_track_order.request(library, '1000', [rows[-1]], rows[0])
        self.assertEqual(request['plan_encoding'], 'membership-replay-v1')
        self.assertEqual([len(child['tracks']) for child in request['requests']], [2916, 2916])
        self.assertEqual(len(replacements), 2916)
        catalog['members'] = [{**template, 'ID': f'entry-{i}',
                               'UUID': f'00000000-0000-4000-8000-{i:012d}', 'TrackNo': i + 1}
                              for i in range(8194)]
        catalog['revision'] = revision(catalog['nodes'], catalog['members'])
        with self.assertRaisesRegex(WriteProofError, 'accepted playlist batch size'):
            playlist_track_order.request(library, '1000', [catalog['members'][-1]], catalog['members'][0])

    def test_each_publication_crash_prefix_rolls_back_or_replays_exact_move(self):
        from dataclasses import replace
        for phase in ('journal-ready', 'sql-applied', 'xml-published', 'sql-committed'):
            with self.subTest(phase=phase):
                source = self.root / phase; shutil.copytree(self.source, source)
                writer = store.PlaylistStore(replace(self.authority, database=str(source / 'master.db')),
                                             self.root / (phase + '-recovery'))
                library = self.library(); rows = self.members()
                request, _ = playlist_track_order.request(library, '1000', [rows[-1]], rows[2])
                before = self.state(writer); xml = writer.xml.read_bytes()
                def fault(point):
                    if point == phase:raise RuntimeError('interrupted move')
                with mock.patch.object(store, '_fault', side_effect=fault), self.assertRaisesRegex(RuntimeError, 'interrupted'):
                    writer.apply(request)
                outcome = writer.recover()
                if phase == 'sql-committed':
                    self.assertEqual(outcome, 'committed'); saved = self.state(writer)
                    writer.apply(request); self.assertEqual(self.state(writer), saved)
                else:
                    self.assertEqual(outcome, 'rolled-back'); self.assertEqual(self.state(writer), before)
                    self.assertEqual(writer.xml.read_bytes(), xml)
                    writer.apply(writer.prepare_retry(request))
                self.assertEqual([row['ContentID'] for row in self.members(writer.snapshot())],
                                 ['track', 'track-two', 'track-two', 'track', 'track'])
