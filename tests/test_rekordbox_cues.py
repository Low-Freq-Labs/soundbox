"""Pure cue planning; synthetic rows only, no live sources or AppData."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import json
from hashlib import sha256
import unittest
from unittest import mock

from soundbox import rekordbox_cues as cues
from soundbox.rekordbox_write import ConflictError, WriteProofError

NOW = datetime(2026, 10, 27, 3, 0, 1, 123456, tzinfo=timezone.utc)
STAMP = '2026-10-27 03:00:01.123 +00:00'
TRACK_UUID = '00000000-0000-4000-8000-000000000001'
IDS = cues.CueIdentities('4294967295', '00000000-0000-4000-8000-000000000002',
                        '00000000-0000-4000-8000-000000000003')


def snapshot(format_id=11):
    return cues.CueSnapshot(
        {'ID': '7', 'UUID': TRACK_UUID, 'FileType': format_id, 'Length': 100,
         'FolderPath': '/synthetic/track', 'CueUpdated': None, 'rb_local_deleted': 0,
         'rb_local_usn': 19, 'updated_at': 'old-track-time', 'Title': 'Preserve this'}, (), None)


def request(state, **kwargs):
    return cues.CueEdit('7', TRACK_UUID, state.revision, 'hot', 0, 'add', 9449, **kwargs)


def plan(state, edit=None, counter=None, identities=IDS):
    counter = counter or {'registry_id': 'localUpdateCount', 'int_1': 41, 'updated_at': 'old-counter-time'}
    return cues.plan_cue_edit(state, counter, edit or request(state), now=NOW, identities=identities)


def advance(state, delta):
    rows = {row['ID']: deepcopy(row) for row in state.cues}
    track, aggregate = deepcopy(state.track), deepcopy(state.aggregate)
    for d in delta:
        if d.table == 'djmdCue':
            if d.after is None:
                del rows[d.key]
            else:
                rows[d.key] = deepcopy(d.after)
        elif d.table == 'djmdContent':
            track = deepcopy(d.after)
        elif d.table == 'contentCue':
            aggregate = deepcopy(d.after)
    return cues.CueSnapshot(track, tuple(rows.values()), aggregate)


def with_existing(rows):
    state = snapshot()
    aggregate = dict.fromkeys(cues._AGGREGATE_COLUMNS)
    aggregate.update(ID=TRACK_UUID, ContentID='7', UUID='existing-aggregate',
                     rb_data_status=0, rb_local_data_status=0, rb_local_deleted=0,
                     rb_local_synced=0, rb_local_usn=40, created_at='old-aggregate-time',
                     updated_at='old-aggregate-time',
                     Cues=json.dumps([cues._public_cue(r) for r in rows]))
    return replace(state, cues=tuple(deepcopy(rows)), aggregate=aggregate)


def existing(kind, position, key='100', **extra):
    row = deepcopy(plan(snapshot())[0].after)
    row.update(ID=key, UUID=f'existing-{key}', Kind=kind, InMsec=position,
               InFrame=position * 150 // 1000, **extra)
    return row


class CuePlanning(unittest.TestCase):
    def test_new_cue_matches_native_wav_mp3_aiff_fields(self):
        for format_id in (1, 11, 12):
            with self.subTest(format_id=format_id):
                delta = plan(snapshot(format_id))
                self.assertEqual([d.table for d in delta],
                                 ['djmdCue', 'djmdContent', 'contentCue', 'agentRegistry'])
                row = delta[0].after
                self.assertEqual((row['InMsec'], row['InFrame'], row['Kind']), (9449, 1417, 1))
                self.assertEqual([row[k] for k in ('InMpegFrame', 'InMpegAbs', 'OutFrame',
                                                  'OutMpegFrame', 'OutMpegAbs')], [0] * 5)
                self.assertEqual((row['OutMsec'], row['Color']), (-1, -1))
                self.assertTrue(all(row[k] is None for k in ('CueMicrosec', 'InPointSeekInfo',
                    'OutPointSeekInfo', 'ActiveLoop', 'BeatLoopSize', 'ColorTableIndex', 'Comment',
                    'rb_local_usn', 'usn')))
                self.assertEqual((row['created_at'], row['updated_at']), (STAMP, STAMP))
                self.assertEqual(delta[1].after['CueUpdated'], '1')
                self.assertEqual(delta[1].after['rb_local_usn'], 42)
                self.assertEqual(delta[2].after['rb_local_usn'], 43)
                self.assertEqual(delta[3].after, {'registry_id': 'localUpdateCount', 'int_1': 43,
                                                 'updated_at': 'old-counter-time'})
                public = json.loads(delta[2].after['Cues'])[0]
                self.assertEqual(public['created_at'], '2026-10-27T03:00:01.123+00:00')
                self.assertFalse(set(public) & cues._INTERNAL)
                self.assertNotIn('Comment', public)

    def test_hot_slot_reserved_gap_and_h_letter(self):
        state = snapshot()
        for slot, kind in enumerate((1, 2, 3, 5, 6, 7, 8, 9)):
            self.assertEqual(plan(state, replace(request(state), slot=slot))[0].after['Kind'], kind)

    def test_memory_is_display_sorted_but_aggregate_keeps_insertion_order(self):
        state = with_existing([existing(0, 19021)])
        delta = plan(state, replace(request(state), page='memory', slot=1, position_ms=9691))
        result = advance(state, delta)
        self.assertEqual([r['InMsec'] for r in json.loads(result.aggregate['Cues'])], [19021, 9691])
        self.assertEqual(cues.cue_at_slot(result, 'memory', 0)['InMsec'], 9691)
        self.assertEqual(cues.cue_at_slot(result, 'memory', 1)['InMsec'], 19021)
        self.assertEqual(result.cues[0], state.cues[0])

    def test_delete_preserves_aggregate_identity_and_creation_time_when_empty(self):
        state = with_existing([existing(1, 9449)])
        edit = replace(request(state), operation='delete', position_ms=None,
                       cue_id='100', cue_uuid='existing-100')
        delta = plan(state, edit, identities=None)
        self.assertEqual(delta[0], cues.CueDelta('djmdCue', '100', state.cues[0], None))
        aggregate = delta[2].after
        self.assertEqual(aggregate['Cues'], '[]')
        self.assertEqual(aggregate['UUID'], 'existing-aggregate')
        self.assertEqual(aggregate['created_at'], 'old-aggregate-time')

    def test_untouched_loops_overflow_colors_labels_and_opaque_json_survive(self):
        state = with_existing([existing(0, 19021, OutMsec=21000, Comment='loop',
                                       Color=6, CueMicrosec=19021444, InMpegAbs=33),
                               existing(17, 30000, key='101')])
        items = json.loads(state.aggregate['Cues'])
        items[0]['future-metadata'] = {'value': [1, 2, 3]}
        state.aggregate['Cues'] = json.dumps(items)
        delta = plan(state)
        result = advance(state, delta)
        self.assertEqual(result.cues[:2], state.cues)
        self.assertEqual(json.loads(result.aggregate['Cues'])[:2], items)
        self.assertEqual(result.track['Title'], 'Preserve this')

    def test_memory_overflow_is_preserved_on_delete(self):
        rows = [existing(0, i * 1000, key=str(i + 1)) for i in range(10)]
        state = with_existing(rows)
        edit = replace(request(state), page='memory', slot=2, operation='delete', position_ms=None,
                       cue_id='3', cue_uuid='existing-3')
        result = advance(state, plan(state, edit))
        self.assertEqual(len(result.cues), 9)
        self.assertEqual(cues.cue_at_slot(result, 'memory', 7)['ID'], '9')
        self.assertIn(rows[-1], result.cues)

    def test_legacy_empty_comment_omission_is_preserved_without_normalization(self):
        state = with_existing([existing(0, 19021, Comment='')])
        items = json.loads(state.aggregate['Cues'])
        del items[0]['Comment']
        state.aggregate['Cues'] = json.dumps(items)
        result = advance(state, plan(state))
        self.assertEqual(result.cues[0]['Comment'], '')
        self.assertNotIn('Comment', json.loads(result.aggregate['Cues'])[0])

    def test_historical_aggregate_sync_flag_is_preserved_like_native(self):
        state = with_existing([existing(2, 19021)])
        state.aggregate['rb_local_synced'] = 1
        delta = plan(state, identities=replace(IDS, aggregate_uuid=''))
        result = advance(state, delta)
        self.assertEqual(result.aggregate['rb_local_synced'], 1)
        self.assertEqual(result.aggregate['UUID'], 'existing-aggregate')
        self.assertEqual(result.aggregate['created_at'], 'old-aggregate-time')
        self.assertEqual(result.cues[0], state.cues[0])

    def test_stale_revision_and_reordered_memory_delete_fail(self):
        state = with_existing([existing(0, 10000), existing(0, 20000, key='101')])
        edit = replace(request(state), page='memory', slot=0, operation='delete', position_ms=None,
                       cue_id='101', cue_uuid='existing-101')
        with self.assertRaises(ConflictError):
            plan(state, edit)
        edit = replace(edit, cue_id='100', cue_uuid='existing-100', revision='stale')
        with self.assertRaises(ConflictError):
            plan(state, edit)

    def test_every_raw_field_and_aggregate_metadata_affects_revision(self):
        state = with_existing([existing(0, 10000)])
        changed = deepcopy(state)
        changed.cues[0]['InPointSeekInfo'] = b'opaque'
        self.assertNotEqual(state.revision, changed.revision)
        changed = deepcopy(state)
        changed.aggregate['UUID'] = 'replaced'
        self.assertNotEqual(state.revision, changed.revision)
        self.assertEqual(state.revision, replace(state, cues=tuple(reversed(state.cues))).revision)

    def test_revision_retains_original_encoded_bytes_for_every_raw_cell(self):
        from soundbox import rekordbox_write
        base = with_existing([existing(0, 10000), existing(1, 20000, key='101')])
        variants = [base, replace(base, cues=()), snapshot()]
        binary = deepcopy(base)
        binary.track['opaque_binary'] = b'\x00\xff'
        binary.cues[0]['InPointSeekInfo'] = b'\x01\x80'
        binary.aggregate['opaque_binary'] = b'\x00'
        binary.track['opaque_object'] = {'nested': [None, 1.5, '日本語', {'a': False}]}
        variants.extend([binary, replace(binary, cues=tuple(reversed(binary.cues)))])
        for state in variants:
            with self.subTest(binary=state is binary, cues=len(state.cues)):
                before = deepcopy(state)
                def cells(row):
                    return ({key: {'blob': value.hex()} if isinstance(value, bytes) else value
                             for key, value in row.items()} if row is not None else None)
                expected = json.dumps({'track': cells(state.track),
                    'cues': [cells(row) for row in sorted(state.cues, key=lambda row: row['ID'])],
                    'aggregate': cells(state.aggregate)}, sort_keys=True, ensure_ascii=False,
                    separators=(',', ':'), allow_nan=False).encode()
                actual_json = rekordbox_write._json
                with mock.patch.object(cues, '_REVISION_CACHE', cues._RevisionCache()), \
                        mock.patch.object(rekordbox_write, '_json', wraps=actual_json) as encoded:
                    self.assertEqual(state.revision, sha256(expected).hexdigest())
                self.assertEqual(actual_json(encoded.call_args.args[0]), expected)
                self.assertEqual(state, before)
                # An unchanged owner is no permission to reuse stale hashes.
                changed = deepcopy(state)
                changed.track['Title'] = 'Later title'
                self.assertNotEqual(changed.revision, sha256(expected).hexdigest())

    def test_revision_reuses_only_complete_fresh_immutable_inputs(self):
        state = with_existing([existing(0, 10000), existing(1, 20000, key='101')])
        with mock.patch.object(cues, '_REVISION_CACHE', cues._RevisionCache()), \
                mock.patch.object(cues, '_revision_digest', wraps=cues._revision_digest) as digest:
            original = state.revision
            copied = deepcopy(state)
            self.assertEqual(copied.revision, original)
            self.assertEqual(replace(copied, cues=tuple(reversed(copied.cues))).revision, original)
            self.assertEqual(digest.call_count, 1)
            for owner in ('track', 'cues', 'aggregate'):
                changed = deepcopy(state)
                row = changed.cues[0] if owner == 'cues' else getattr(changed, owner)
                row['unknown native column'] = 'fresh value'
                self.assertNotEqual(changed.revision, original)
            self.assertEqual(digest.call_count, 4)
            state.track['opaque'] = b'first'
            binary = state.revision
            state.track['opaque'] = b'second'
            self.assertNotEqual(state.revision, binary)

    def test_revision_cache_distinguishes_scalar_json_bytes_and_skips_mutable_values(self):
        cache = cues._RevisionCache()
        with mock.patch.object(cues, '_REVISION_CACHE', cache), \
                mock.patch.object(cues, '_revision_digest', wraps=cues._revision_digest) as digest:
            results = []
            for value in (0, 0.0, -0.0, 1, 1.0, True):
                state = replace(snapshot(), track={'opaque': value})
                expected = sha256(json.dumps({'track': {'opaque': value}, 'cues': [],
                    'aggregate': None}, sort_keys=True, separators=(',', ':'),
                    allow_nan=False).encode()).hexdigest()
                self.assertEqual(state.revision, expected)
                self.assertEqual(state.revision, expected)
                results.append(expected)
            self.assertEqual(len(set(results)), 6)
            self.assertEqual(len(cache._values), 5)
            self.assertEqual(digest.call_count, 7)
            state = replace(snapshot(), track={'opaque': {'nested': [1]}})
            before = state.revision
            state.track['opaque']['nested'].append(2)
            self.assertNotEqual(state.revision, before)
            self.assertEqual(len(cache._values), 5)

    def test_revision_cache_has_entry_and_byte_limits_and_does_not_cache_failures(self):
        cache = cues._RevisionCache(entries=2)
        with mock.patch.object(cues, '_REVISION_CACHE', cache), \
                mock.patch.object(cues, '_revision_digest', wraps=cues._revision_digest) as digest:
            states = [replace(snapshot(), track={'ID': str(index)}) for index in range(3)]
            expected = [state.revision for state in states]
            self.assertEqual(len(cache._values), 2)
            self.assertEqual(states[0].revision, expected[0])
            self.assertEqual(digest.call_count, 4)
            self.assertEqual(replace(snapshot(), track={'huge': b'x' * 200000}).revision,
                             replace(snapshot(), track={'huge': b'x' * 200000}).revision)
            self.assertEqual(len(cache._values), 2)
            self.assertLessEqual(cache._bytes, cache.byte_limit)
        cache = cues._RevisionCache(byte_limit=1)
        with mock.patch.object(cues, '_REVISION_CACHE', cache):
            snapshot().revision
            self.assertFalse(cache._values)
            for _ in range(2):
                with self.assertRaises(WriteProofError):
                    replace(snapshot(), track={'bad': float('nan')}).revision
            self.assertFalse(cache._values)

    def test_revision_cache_byte_budget_evicts_old_entries(self):
        state = replace(snapshot(), track={'ID': 'one'})
        cache = cues._RevisionCache()
        with mock.patch.object(cues, '_REVISION_CACHE', cache):
            state.revision
        budget = cache._bytes
        cache = cues._RevisionCache(byte_limit=budget)
        with mock.patch.object(cues, '_REVISION_CACHE', cache):
            state.revision
            replace(state, track={'ID': 'two'}).revision
            self.assertEqual(len(cache._values), 1)
            self.assertLessEqual(cache._bytes, budget)

    def test_revision_cache_miss_uses_frozen_cells_during_later_input_mutation(self):
        state = snapshot()
        expected = state.revision
        original = cues._revision_digest
        def mutate_after_capture(track, cue_rows, aggregate):
            state.track['Title'] = 'Changed after capture'
            return original(track, cue_rows, aggregate)
        with mock.patch.object(cues, '_REVISION_CACHE', cues._RevisionCache()), \
                mock.patch.object(cues, '_revision_digest', side_effect=mutate_after_capture):
            self.assertEqual(state.revision, expected)
            self.assertNotEqual(state.revision, expected)

    def test_revision_preserves_rejection_of_invalid_json_and_missing_cue_identity(self):
        variants = [replace(snapshot(), track={'unsupported': float('nan')}),
                    replace(snapshot(), track={'unsupported': object()}),
                    replace(snapshot(), cues=({'Kind': 1},))]
        for state in variants:
            with self.subTest(state=state):
                with self.assertRaisesRegex(WriteProofError, 'Invalid cue snapshot'):
                    state.revision

    def test_memory_duplicate_position_and_empty_delete_are_noops(self):
        state = with_existing([existing(0, 10000)])
        edit = replace(request(state), page='memory', slot=1, position_ms=10000)
        self.assertEqual(plan(state, edit), ())
        state = snapshot()
        self.assertEqual(plan(state, replace(request(state), operation='delete', position_ms=None)), ())

    def test_occupied_hot_slot_is_never_overwritten(self):
        state = with_existing([existing(1, 10000)])
        with self.assertRaisesRegex(ConflictError, 'no longer empty'):
            plan(state)

    def test_deleted_rebound_and_unproven_format_tracks_fail(self):
        for changed in ({'ID': 'other'}, {'UUID': 'other'}, {'rb_local_deleted': 1},
                        {'FileType': 5}, {'FileType': True}):
            state = snapshot()
            edit = request(state)
            state.track.update(changed)
            edit = replace(edit, revision=state.revision)
            with self.subTest(changed=changed), self.assertRaises(WriteProofError):
                plan(state, edit)

    def test_positions_and_slots_are_validated_without_quantization(self):
        state = snapshot()
        for position in (-1, True, 1.25, None, 101000, 2**31):
            with self.subTest(position=position), self.assertRaises(WriteProofError):
                plan(state, replace(request(state), position_ms=position))
        self.assertEqual(plan(state, replace(request(state), position_ms=100999))[0].after['InMsec'], 100999)
        for slot in (-1, 8, True):
            with self.subTest(slot=slot), self.assertRaises(WriteProofError):
                plan(state, replace(request(state), slot=slot))

    def test_counter_and_timestamp_validation(self):
        state = snapshot()
        for count in (True, -1, 1.5, None, 2**63 - 2):
            with self.subTest(count=count), self.assertRaises(WriteProofError):
                plan(state, counter={'registry_id': 'localUpdateCount', 'int_1': count})
        for value in ('', 'bad', '-1', '1.0', 4, str(2**63 - 1)):
            state.track['CueUpdated'] = value
            with self.subTest(value=value), self.assertRaises(WriteProofError):
                plan(state)
        state.track['CueUpdated'] = '10'
        self.assertEqual(plan(state)[1].after['CueUpdated'], '11')
        with self.assertRaises(WriteProofError):
            cues.plan_cue_edit(state, {'registry_id': 'localUpdateCount', 'int_1': 1},
                               request(state), now=NOW.replace(tzinfo=None), identities=IDS)

    def test_aggregate_inconsistency_and_duplicate_json_keys_fail(self):
        state = with_existing([existing(0, 10000)])
        for value in ('[]', '[{}]', '{}', '[NaN]', '[{"ID":"100","ID":"101"}]', 'bad'):
            state.aggregate['Cues'] = value
            with self.subTest(value=value), self.assertRaises(WriteProofError):
                plan(state)
        state = with_existing([existing(0, 10000)])
        items = json.loads(state.aggregate['Cues'])
        items[0]['InMsec'] += 1
        state.aggregate['Cues'] = json.dumps(items)
        with self.assertRaises(ConflictError):
            plan(state)
        with self.assertRaises(WriteProofError):
            plan(replace(state, aggregate=None))

    def test_unproven_sync_metadata_blocks_target_deletion(self):
        for field, value in (('rb_local_synced', 1), ('usn', 3), ('rb_data_status', 2)):
            state = with_existing([existing(1, 10000, **{field: value})])
            edit = replace(request(state), operation='delete', position_ms=None,
                           cue_id='100', cue_uuid='existing-100')
            with self.subTest(field=field), self.assertRaises(WriteProofError):
                plan(state, edit)

    def test_ambiguous_hot_and_memory_slots_fail(self):
        state = with_existing([existing(1, 10000), existing(1, 20000, key='101')])
        with self.assertRaisesRegex(WriteProofError, 'Duplicate Hot'):
            plan(state)
        state = with_existing([existing(0, 10000), existing(0, 10000, key='101')])
        with self.assertRaisesRegex(WriteProofError, 'numbering is ambiguous'):
            cues.cue_at_slot(state, 'memory', 0)

    def test_memory_tie_order_matches_display_reader(self):
        state = with_existing([existing(0, 10000, OutMsec=11000, key='101'),
                               existing(0, 10000, Comment='zzz'),
                               existing(0, 10000, Comment='aaa', key='102')])
        self.assertEqual([cues.cue_at_slot(state, 'memory', i)['ID'] for i in range(3)],
                         ['102', '100', '101'])

    def test_new_identity_validation(self):
        state = snapshot()
        for ids in (None, replace(IDS, cue_id='0'), replace(IDS, cue_id='4294967296'),
                    replace(IDS, cue_id='01'), replace(IDS, cue_uuid=TRACK_UUID),
                    replace(IDS, cue_uuid='invalid'), replace(IDS, aggregate_uuid=IDS.cue_uuid)):
            with self.subTest(ids=ids), self.assertRaises(WriteProofError):
                plan(state, identities=ids)

    def test_inputs_and_results_do_not_share_mutable_rows(self):
        state = snapshot()
        before = deepcopy(state)
        delta = plan(state)
        self.assertEqual(state, before)
        delta[1].before['Title'] = 'changed plan'
        self.assertEqual(state, before)
