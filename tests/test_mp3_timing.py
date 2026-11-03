"""MP3 evidence keeps distinct clocks visible and never changes its inputs."""
from copy import deepcopy
from fractions import Fraction
import hashlib
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

from research.native_library import mp3_timing
from soundbox.rekordbox_write import ConflictError, WriteProofError


def packet(counts=(392, 392, 392)):
    def section(kind, header=b'', payload=b''):
        size = 12 + len(header)
        return kind + struct.pack('>II', size, size + len(payload)) + header + payload

    def image(sections):
        body = b''.join(sections)
        return b'PMAI' + struct.pack('>II', 12, 12 + len(body)) + body

    grid = section(b'PQTZ', struct.pack('>III', 0, 0x80000, 2),
                   struct.pack('>HHIHHI', 1, 12000, 274, 2, 12000, 774))
    # A nonzero seek-table word is evidence; this inspector never replaces it.
    seek = section(b'PVBR', bytes(4), struct.pack('>I', 417) + bytes(1600))
    details = [section(kind, struct.pack('>III', width, count, 0x960000), bytes(width * count))
               for kind, width, count in zip((b'PWV3', b'PWV5', b'PWV7'), (1, 2, 3), counts, strict=True)]
    return {'.DAT': image([seek, grid]), '.EXT': image(details[:2]), '.2EX': image(details[2:])}


class MP3TimingAudit(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name).resolve() / 'captured.mp3'
        frame = bytes.fromhex('fffb9064') + bytes(413)
        self.path.write_bytes(frame * 100)
        self.packets = packet()
        self.report = dict(frames=115200, rate=44100, channels=2,
                           duration_ms=115200 * 1000 / 44100, detail_columns=392,
                           peak=0.0, rms=0.0, dat_bytes=len(self.packets['.DAT']),
                           ext_bytes=len(self.packets['.EXT']), two_ex_bytes=len(self.packets['.2EX']),
                           source_write_authority=False)

    def audit(self, **kwargs):
        args = dict(captured_mp3=self.path, packets=self.packets, renderer_report=self.report)
        args.update(kwargs)
        return mp3_timing.audit(**args)

    def test_reports_exact_observed_clocks_hashes_and_nonzero_seek_table(self):
        source = self.path.read_bytes()
        packets = deepcopy(self.packets)
        report = deepcopy(self.report)
        result = self.audit()
        self.assertEqual(result['source']['sha256'], hashlib.sha256(source).hexdigest())
        self.assertFalse(result['source_write_authority'])
        self.assertFalse(result['playback_origin']['recognized'])
        self.assertEqual(result['native']['pvbr_nonzero_payload_bytes'], 2)
        self.assertEqual(result['native']['first_beat_ms'], 274)
        self.assertEqual(result['native']['last_beat_ms'], 774)
        self.assertEqual(result['renderer']['duration'], {'numerator': 128, 'denominator': 49})
        self.assertEqual(result['renderer']['detail_delta_columns'], {'PWV3': 0, 'PWV5': 0, 'PWV7': 0})
        self.assertEqual((self.path.read_bytes(), self.packets, self.report), (source, packets, report))

    def test_distinct_native_header_and_renderer_clocks_remain_explicit(self):
        report = dict(self.report, frames=116352, duration_ms=116352 * 1000 / 44100, detail_columns=396)
        result = self.audit(packets=packet((392, 393, 392)), renderer_report=report)
        self.assertIn('header_renderer_durations_differ', result['disagreements'])
        self.assertIn('native_detail_counts_differ', result['disagreements'])
        self.assertIn('native_renderer_detail_counts_differ', result['disagreements'])
        header = Fraction(**result['source']['header_duration'])
        delta = Fraction(**result['renderer']['header_delta_seconds'])
        self.assertEqual(header + delta, Fraction(116352, 44100))
        self.assertEqual(result['native']['detail_columns']['PWV5'], 393)

    def test_playback_origin_is_reported_without_substituting_renderer_endpoint(self):
        frame = bytearray(417)
        frame[:4] = bytes.fromhex('fffb9000')
        frame[36:40] = b'Info'
        frame[40:44] = (1).to_bytes(4, 'big')
        frame[44:48] = (100).to_bytes(4, 'big')
        frame[48:57] = b'LAME3.100'
        frame[69:72] = ((576 << 12) | 1152).to_bytes(3, 'big')
        self.path.write_bytes(frame + (bytes.fromhex('fffb9000') + bytes(413)) * 100)
        result = self.audit(renderer_report=None)
        self.assertEqual(result['playback_origin'], dict(recognized=True, lead_frames=2257,
                                                       decoded_frames=113472, sample_rate=44100))
        self.assertIsNone(result['renderer'])

    def test_malformed_packets_or_renderer_reports_are_rejected(self):
        for field, value in (('frames', True), ('duration_ms', float('nan')),
                             ('source_write_authority', True), ('duration_ms', 3), ('extra', 1)):
            with self.subTest(field=field), self.assertRaises(WriteProofError):
                self.audit(renderer_report=dict(self.report, **{field: value}))
        for extension in self.packets:
            with self.subTest(extension=extension), self.assertRaises(WriteProofError):
                self.audit(packets={**self.packets, extension: self.packets[extension][:-1]})
        with self.assertRaises(WriteProofError):
            self.audit(packets={**self.packets, '.other': b'unknown'})
        raw = bytearray(self.packets['.EXT'])
        struct.pack_into('>I', raw, 28, 394)
        with self.assertRaises(WriteProofError):
            self.audit(packets={**self.packets, '.EXT': bytes(raw)})

    def test_aliases_budgets_and_source_drift_fail_without_outputs(self):
        alias = self.path.with_name('alias.mp3')
        alias.symlink_to(self.path)
        with self.assertRaises(WriteProofError):
            self.audit(captured_mp3=alias)
        with mock.patch.object(mp3_timing, 'MAX_SOURCE_BYTES', 10), self.assertRaises(WriteProofError):
            self.audit()
        original = mp3_timing.mp3_origin

        def changed(path):
            value = original(path)
            path.write_bytes(path.read_bytes() + b'changed')
            return value

        with mock.patch.object(mp3_timing, 'mp3_origin', side_effect=changed), self.assertRaises(ConflictError):
            self.audit()
        self.assertEqual({p.name for p in self.path.parent.iterdir()}, {'captured.mp3', 'alias.mp3'})
