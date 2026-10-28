"""Raw cue edit identities from synthetic snapshot rows; no source access."""
from types import SimpleNamespace
import unittest

from soundbox.library import Track
from soundbox.rekordbox_cues import CueSnapshot
from soundbox.rekordbox_db import _populate_cue_edit_state
from tests.test_rekordbox_cues import existing, with_existing


class CueEditMetadata(unittest.TestCase):
    def test_batch_reads_match_writer_revision_and_preserve_display_cues(self):
        state = with_existing([existing(0, 19021), existing(0, 9691, key='101'),
                               existing(5, 30000, key='102'), existing(17, 40000, key='103')])
        state.track['FileType'] = 11
        normalized = [dict(kind='memory', slot=0, position_ms=9691)]
        track = Track('7', 'Track', '', 'file:///synthetic/track.wav', source_cues=normalized)
        tables = {'djmdContent': [state.track, {**state.track, 'ID': 'outside-scope'}],
                  'djmdCue': list(state.cues), 'contentCue': [state.aggregate]}
        calls = []
        class Driver:
            def exec_driver_sql(self, sql):
                calls.append(sql)
                return SimpleNamespace(mappings=lambda: tables[sql.split('"')[1]])
        db = SimpleNamespace(session=SimpleNamespace(connection=lambda: Driver()))
        _populate_cue_edit_state(db, {'7': track})
        self.assertEqual(len(calls), 3)
        self.assertEqual(track.source_cue_revision, state.revision)
        self.assertIs(track.source_cues, normalized)
        refs = {(r['kind'], r['slot']): r for r in track.source_cue_refs}
        self.assertEqual(refs['hot', 3]['id'], '102')
        self.assertEqual(refs['hot', 15]['id'], '103')
        self.assertEqual(refs['memory', 0]['id'], '101')
        self.assertEqual(refs['memory', 1]['id'], '100')
        # Exact SQL timestamp spelling and opaque metadata participate in stale
        # detection without changing display/analysis cue dictionaries.
        changed = {**state.track, 'updated_at': 'different-time'}
        self.assertNotEqual(track.source_cue_revision,
                            CueSnapshot(changed, state.cues, state.aggregate).revision)
