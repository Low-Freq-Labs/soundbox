"""Hermetic grid planning/opaque-byte tests; never discover live libraries."""
import struct
import hashlib
import unittest
from dataclasses import replace
from urllib.parse import quote

from soundbox.rekordbox_grid import (
    AmbiguousGridEnd, AnalysisImage, Beat, GridEdit, GridSnapshot, clear_secondary_grid, double_grid,
    duration_bounds, halve_grid, plan_grid_edit, read_grid, replace_grid, set_bar_start, shift_grid,
    set_grid_bpm, transform_grid,
)
from soundbox.rekordbox_write import WriteProofError


def section(kind, payload, header=12):
    return kind + struct.pack('>II', header, len(payload) + 12) + payload


def container(*sections):
    header = b'PMAI' + struct.pack('>II', 16, 16 + sum(map(len, sections))) + b'keep'
    return header + b''.join(sections)


def primary(beats):
    body = b''.join(struct.pack('>HHI', b.number, b.tempo, b.time_ms) for b in beats)
    return container(section(b'UNKN', b'opaque before'),
                     section(b'PQTZ', b'\0\0\0\0\0\x08\0\0' +
                             struct.pack('>I', len(beats)) + body, 24),
                     section(b'PCO2', b'opaque cue bytes'),
                     section(b'PWV3', bytes(range(256))))


def content_file(track, dat, local_path='/synthetic/ANLZ0000.DAT'):
    path = track['AnalysisDataPath']
    return dict(ID=track['UUID'] + '_' + quote(path, safe=''), ContentID=track['ID'],
                Path=path, UUID='synthetic-file-uuid', Hash=hashlib.md5(dat).hexdigest(),
                Size=len(dat), rb_local_path=local_path, rb_data_status=0, rb_local_data_status=0,
                rb_local_deleted=0, rb_local_synced=0, rb_file_hash_dirty=0, rb_file_size_dirty=0,
                rb_local_file_status=0, rb_in_progress=0, rb_process_type=0, usn=None,
                rb_insync_hash=None, rb_insync_local_usn=None, rb_temp_path=None,
                rb_local_usn=30, updated_at='old-file-time')


class GridPlanningTests(unittest.TestCase):
    def test_manual_bpm_includes_exact_eof_only_with_stronger_evidence(self):
        rows = tuple(Beat(i % 4 + 1, 13801, 54 + (i * 6000000 + 6900) // 13801)
                     for i in range(297))
        dat = primary(rows)
        ext = container(section(b'PQT2', bytes.fromhex('000000000100000200000000') + bytes(32), 56),
                        section(b'PWV3', struct.pack('>III', 1, 19313, 0x960000)
                                + bytes(19313), 24))
        self.assertEqual(duration_bounds(ext), (128746, 128754))
        with self.assertRaises(AmbiguousGridEnd):
            transform_grid(dat, ext, 'bpm', 13800)
        after, _ = transform_grid(dat, ext, 'bpm', 13800, exact_end_ms=128750)
        self.assertEqual(len(read_grid(after)), 297)
        self.assertEqual(read_grid(after)[-1], Beat(1, 13800, 128750))
        with self.assertRaises(AmbiguousGridEnd):
            transform_grid(after, ext, 'bpm', 13801)
        restored, _ = transform_grid(after, ext, 'bpm', 13801, exact_end_ms=128750)
        self.assertEqual(restored, dat)
        shorter, _ = transform_grid(dat, ext, 'bpm', 13800, exact_end_ms=128749)
        self.assertEqual(len(read_grid(shorter)), 296)
        for end in (True, 128750.0, 128745, 128755):
            with self.subTest(end=end), self.assertRaises(WriteProofError):
                transform_grid(dat, ext, 'bpm', 13800, exact_end_ms=end)
        with self.assertRaises(WriteProofError):
            transform_grid(dat, ext, 'double', exact_end_ms=128750)

    def test_manual_bpm_rebuilds_then_extends_from_rounded_tail(self):
        rows = tuple(Beat((i + 3) % 4 + 1, 9000, 39 + i * 2000 // 3) for i in range(252))
        result = set_grid_bpm(rows, 9100, end_bounds_ms=(167613, 167620))
        self.assertEqual(len(result), 255)
        self.assertEqual(result[0], Beat(4, 9100, 39))
        self.assertEqual(result[253], Beat(1, 9100, 166853))
        self.assertEqual(result[-1], Beat(2, 9100, 167512))
        fractional = tuple(Beat(i % 4 + 1, 4501, 705 + i * 1333) for i in range(126))
        restored = set_grid_bpm(fractional, 9000, end_bounds_ms=(167613, 167620))
        self.assertEqual(restored[127].time_ms, 85371)
        self.assertEqual(restored[-1].time_ms, 167371)
        self.assertEqual(set_grid_bpm(rows, 9000), rows)
        for tempo in (0, 65536, True, 91.0):
            with self.subTest(tempo=tempo), self.assertRaises(WriteProofError):
                set_grid_bpm(rows, tempo)
        with self.assertRaises(WriteProofError):
            set_grid_bpm((Beat(1, 9000, 0), Beat(2, 9100, 666)), 9200)
        with self.assertRaisesRegex(WriteProofError, 'audio-end'):
            set_grid_bpm((Beat(1, 9000, 0), Beat(2, 9000, 666)), 12000,
                         end_bounds_ms=(1498, 1505))

    def test_manual_bpm_metadata_has_separate_analysis_revision_and_counter(self):
        snap = self.metadata_snapshot((Beat(4, 9000, 39), Beat(1, 9000, 705)))
        track = {**snap.track, 'AnalysisDataPath': '/PIONEER/USBANLZ/synthetic/ANLZ0000.DAT'}
        snap = replace(snap, track=track, content_file=content_file(track, snap.dat))
        edit = GridEdit('track', 'uuid', snap.revision, 'bpm', 9100)
        counter = {'registry_id': 'localUpdateCount', 'int_1': 41, 'updated_at': 'keep'}
        candidate = plan_grid_edit(snap, counter, edit, timestamp='2026-10-27 08:00:00.123 +00:00')
        before, after = snap.track, candidate.changes[0].after
        self.assertEqual({k for k in before if before[k] != after[k]},
                         {'BPM', 'AnalysisUpdated', 'TrackInfoUpdated', 'rb_local_usn', 'updated_at'})
        self.assertEqual((after['BPM'], after['AnalysisUpdated'], after['TrackInfoUpdated'],
                          after['rb_local_usn']), (9100, '2', '7', 45))
        self.assertEqual(candidate.changes[1].after['int_1'], 45)
        self.assertEqual(candidate.changes[1].after['updated_at'], 'keep')
        file = candidate.changes[2]
        self.assertEqual(file.table, 'contentFile')
        self.assertEqual(file.after['Hash'], hashlib.md5(candidate.dat).hexdigest())
        self.assertEqual(file.after['Size'], len(candidate.dat))
        self.assertEqual(file.after['rb_local_usn'], 43)
        for changed in ({'ContentID': 'other'}, {'ID': 'foreign'}, {'Path': '/other.DAT'},
                        {'rb_file_hash_dirty': 1}, {'usn': 99}, {'rb_local_synced': 3}):
            altered = replace(snap, content_file={**snap.content_file, **changed})
            request = replace(edit, revision=altered.revision)
            with self.subTest(changed=changed), self.assertRaises(WriteProofError):
                plan_grid_edit(altered, counter, request, timestamp='2026-10-27 08:00:00.123 +00:00')

    @staticmethod
    def metadata_snapshot(beats):
        track = {'ID': 'track', 'UUID': 'uuid', 'rb_local_deleted': 0,
                 'Analysed': 105, 'FileType': 11, 'BPM': 9000,
                 'TrackInfoUpdated': '6', 'AnalysisUpdated': '1',
                 'rb_local_usn': 41, 'updated_at': 'old', 'opaque': b'\x00\xff'}
        ext = container(section(b'PQT2', bytes.fromhex('000000000100000200000000')
                                + bytes(32), 56),
                        section(b'PWV3', struct.pack('>III', 1, 25143, 0x960000)
                                + bytes(25143), 24))
        return GridSnapshot(track, primary(beats), ext)

    def test_variable_grid_boundary_shift_never_changes_headline_bpm(self):
        snapshot = self.metadata_snapshot((Beat(1, 9000, 0), Beat(2, 9100, 666),
                                           Beat(3, 9200, 1325)))
        edit = GridEdit('track', 'uuid', snapshot.revision, 'shift', -1)
        candidate = plan_grid_edit(snapshot, {}, edit, timestamp='unused for a nudge')
        self.assertEqual(candidate.changes, ())
        self.assertEqual(read_grid(candidate.dat),
                         (Beat(2, 9100, 665), Beat(3, 9200, 1324)))
        self.assertEqual(snapshot.track['BPM'], 9000)

    def test_metadata_plan_preserves_analysis_and_opaque_fields_and_rejects_stale_lock(self):
        snapshot = self.metadata_snapshot((Beat(4, 9000, 39), Beat(1, 9000, 705)))
        edit = GridEdit('track', 'uuid', snapshot.revision, 'halve')
        counter = {'registry_id': 'localUpdateCount', 'int_1': 41, 'updated_at': 'keep'}
        candidate = plan_grid_edit(snapshot, counter, edit,
                                   timestamp='2026-10-27 08:00:00.123 +00:00')
        updated = candidate.changes[0].after
        self.assertEqual(updated['AnalysisUpdated'], '1')
        self.assertEqual(updated['opaque'], b'\x00\xff')
        self.assertEqual((updated['BPM'], updated['TrackInfoUpdated'], updated['rb_local_usn']),
                         (4500, '7', 43))
        self.assertEqual(candidate.changes[1].after,
                         {'registry_id': 'localUpdateCount', 'int_1': 43, 'updated_at': 'keep'})
        changed = replace(snapshot, track={**snapshot.track, 'Analysed': 233})
        for target, request in ((snapshot, replace(edit, revision='stale')),
                                (changed, replace(edit, revision=changed.revision))):
            with self.subTest(target=target.track), self.assertRaises(WriteProofError):
                plan_grid_edit(target, counter, request,
                               timestamp='2026-10-27 08:00:00.123 +00:00')

    def test_replace_changes_only_grid_and_container_length(self):
        beats = (Beat(4, 9000, 39), Beat(1, 9000, 705), Beat(2, 9000, 1372))
        before = primary(beats)
        self.assertEqual(read_grid(before), beats)
        self.assertEqual(replace_grid(before, beats), before)
        after = replace_grid(before, double_grid(beats))
        a, b = AnalysisImage.parse(before), AnalysisImage.parse(after)
        self.assertEqual(a.header[:8] + a.header[12:], b.header[:8] + b.header[12:])
        self.assertEqual([s.raw for s in a.sections if s.kind != b'PQTZ'],
                         [s.raw for s in b.sections if s.kind != b'PQTZ'])
        self.assertEqual(read_grid(after), (Beat(3, 18000, 39), Beat(4, 18000, 372),
                                          Beat(1, 18000, 705), Beat(2, 18000, 1038),
                                          Beat(3, 18000, 1372)))

    def test_negative_boundary_drops_only_negative_rows_and_preserves_phase(self):
        beats = (Beat(1, 9000, 0), Beat(2, 9100, 666), Beat(3, 8900, 1333))
        self.assertEqual(shift_grid(beats, -1),
                         (Beat(2, 9100, 665), Beat(3, 8900, 1332)))
        self.assertEqual(shift_grid(shift_grid(beats, 3), -3), beats)
        with self.assertRaises(WriteProofError):
            shift_grid(beats, -2000)
        with self.assertRaises(WriteProofError):
            shift_grid(beats, 0xffffffff)

    def test_halving_preserves_retained_times_and_first_full_bar_anchor(self):
        original = (Beat(4, 9000, 39), Beat(1, 9000, 705),
                    Beat(2, 9000, 1372), Beat(3, 9000, 2039))
        self.assertEqual(halve_grid(double_grid(original)), original)
        self.assertEqual(halve_grid(original),
                         (Beat(1, 4500, 705), Beat(2, 4500, 2039)))
        partial = (Beat(3, 12000, 0), Beat(4, 12000, 500), Beat(1, 12000, 1000))
        self.assertEqual(halve_grid(partial),
                         (Beat(4, 6000, 0), Beat(1, 6000, 1000)))
        for unproved in ((Beat(1, 1, 0),), (Beat(4, 9000, 0),),
                         (Beat(1, 9000, 0), Beat(2, 9100, 666))):
            with self.subTest(beats=unproved), self.assertRaises(WriteProofError):
                halve_grid(unproved)

    def test_fractional_halving_uses_native_hundredth_floor_and_keeps_times(self):
        beats = (Beat(4, 9003, 39), Beat(1, 9003, 705),
                 Beat(2, 9003, 1372), Beat(3, 9003, 2038))
        self.assertEqual(halve_grid(beats),
                         (Beat(1, 4501, 705), Beat(2, 4501, 2038)))

    def test_double_restores_available_leading_half_beat_with_native_rounding(self):
        half = (Beat(1, 4500, 705), Beat(2, 4500, 2039), Beat(3, 4500, 3372))
        self.assertEqual(double_grid(half), (
            Beat(4, 9000, 38), Beat(1, 9000, 705), Beat(2, 9000, 1372),
            Beat(3, 9000, 2039), Beat(4, 9000, 2705), Beat(1, 9000, 3372)))
        with self.assertRaises(WriteProofError):
            double_grid((Beat(1, 9000, 5000), Beat(2, 9000, 5666)))

    def test_double_extends_only_into_proven_audio_tail(self):
        beats = (Beat(1, 14000, 71), Beat(2, 14000, 499), Beat(3, 14000, 928))
        ext = container(section(b'PWV3', struct.pack('>III', 1, 200, 0x960000)
                                + bytes(200), 24))
        self.assertEqual(duration_bounds(ext), (1326, 1334))
        doubled = double_grid(beats, end_bounds_ms=duration_bounds(ext))
        self.assertEqual(doubled[-1], Beat(2, 28000, 1142))
        self.assertEqual(halve_grid(doubled), beats)
        for bounds in ((1142, 1148), (920, 930), (-1, 0)):
            with self.subTest(bounds=bounds), self.assertRaises(WriteProofError):
                double_grid(beats, end_bounds_ms=bounds)

    def test_double_extrapolates_tail_from_saved_interval_without_nominal_tempo_rounding(self):
        beats = (Beat(4, 14500, 274396), Beat(1, 14500, 274810))
        # Use a zero-based excerpt so the lead-in guard remains meaningful.
        excerpt = tuple(Beat(b.number, b.tempo, b.time_ms - 274396) for b in beats)
        result = double_grid(excerpt, end_bounds_ms=(800, 807))
        self.assertEqual(result[-1], Beat(2, 29000, 621))
        self.assertEqual(result[-1].time_ms + 274396, 275017)

    def test_downbeat_keeps_earlier_bars_and_exact_audio_position(self):
        beats = tuple(Beat((i + 3) % 4 + 1, 9000, 39 + (i * 2000) // 3)
                      for i in range(20))
        result = set_bar_start(beats, 10009)
        self.assertEqual(len(result), len(beats))
        self.assertEqual([b.time_ms for b in result if b.number == 1],
                         [2009, 4675, 7342, 10009, 12675])
        self.assertEqual(result[0], Beat(2, 9000, 9))
        at_zero = set_bar_start(beats, 0)
        self.assertEqual(at_zero[0], Beat(1, 9000, 0))
        with self.assertRaises(WriteProofError):
            set_bar_start((Beat(1, 12000, 0), Beat(2, 12000, 500)), 250)

    def test_secondary_invalidation_preserves_waveform_cues_and_unknown_bytes(self):
        body = bytes.fromhex('000000000100000200000000')
        body += struct.pack('>HHIHHI', 4, 9000, 39, 3, 9000, 167372)
        body += struct.pack('>IIII', 3, 12345, 0, 0) + b'\x01\x00' * 3
        before = container(section(b'PQT2', body, 56),
                           section(b'PCOB', b'cue'), section(b'PWV5', b'wave'))
        after = clear_secondary_grid(before)
        self.assertEqual(AnalysisImage.parse(after).section(b'PQT2').raw,
                         bytes.fromhex('505154320000003800000038000000000100000200000000')
                         + bytes(32))
        for kind in (b'PCOB', b'PWV5'):
            self.assertEqual(AnalysisImage.parse(before).section(kind),
                             AnalysisImage.parse(after).section(kind))
        self.assertEqual(clear_secondary_grid(after), after)

    def test_rejects_malformed_ambiguous_and_unproved_shapes(self):
        beats = (Beat(1, 12000, 0), Beat(2, 12000, 500))
        raw = primary(beats)
        malformed = [b'', raw[:-1], raw + b'x',
                     raw[:8] + struct.pack('>I', len(raw) + 1) + raw[12:]]
        for data in malformed:
            with self.subTest(data=data[:12]), self.assertRaises(WriteProofError):
                read_grid(data)
        duplicate = container(*(s.raw for s in AnalysisImage.parse(raw).sections),
                              AnalysisImage.parse(raw).section(b'PQTZ').raw)
        with self.assertRaises(WriteProofError):
            read_grid(duplicate)
        for invalid in ((Beat(1, 0, 0),), (Beat(1, 12000, -1),),
                        (Beat(1, 12000, 0), Beat(3, 12000, 500)),
                        (Beat(1, 12000, 0), Beat(2, 12000, 0))):
            with self.subTest(beats=invalid), self.assertRaises(WriteProofError):
                replace_grid(raw, invalid)
        with self.assertRaises(WriteProofError):
            double_grid((Beat(1, 12000, 0), Beat(2, 12100, 500)))
        with self.assertRaises(WriteProofError):
            double_grid((Beat(1, 32768, 0),))


if __name__ == '__main__':
    unittest.main()
