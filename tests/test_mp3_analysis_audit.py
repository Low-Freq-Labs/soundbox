"""The MP3 candidate comparison writes only TEMP packets and a new report."""
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from research.native_library import mp3_analysis_audit as audit
from test_mp3_timing import packet


class MP3AnalysisComparison(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.inputs = self.root / 'protected'
        self.inputs.mkdir()
        self.audio = self.inputs / 'source.mp3'
        self.audio.write_bytes((bytes.fromhex('fffb9064') + bytes(413)) * 100)
        self.audio_bytes = self.audio.read_bytes()
        self.output = self.root / 'report.json'
        self.timing_issues = []
        self.renderer_frames = 115200
        self.bytes = packet()
        text = '?/source.mp3\0'.encode('utf-16-be')
        path = b'PPTH' + struct.pack('>III', 16, 16 + len(text), len(text)) + text
        for ext, raw in self.bytes.items():
            self.bytes[ext] = raw[:8] + struct.pack('>I', len(raw) + len(path)) + path + raw[12:]
        self.paths = {ext: self.inputs / name for ext, name in audit.NAMES.items()}
        for ext, path in self.paths.items():
            path.write_bytes(self.bytes[ext])

    def timing(self, source, packets, report):
        self.assertNotEqual(source, self.audio)
        self.assertEqual(source.read_bytes(), self.audio_bytes)
        self.assertEqual(set(packets), set(audit.NAMES))
        return dict(source=dict(sha256=hashlib.sha256(source.read_bytes()).hexdigest()),
                    disagreements=list(self.timing_issues), source_write_authority=False)

    def renderer(self, command, root, request):
        self.assertEqual(command, 'saved-analysis')
        value = json.loads(request.read_bytes())
        source = Path(value['audio'])
        self.assertTrue(source.is_relative_to(root))
        self.assertEqual(source.name, self.audio.name)
        self.assertEqual(source.read_bytes(), self.audio.read_bytes())
        self.assertEqual(value['original_name'], 'source.mp3')
        self.assertEqual(value['beats'], [dict(number=1, tempo=12000, time_ms=274),
                                         dict(number=2, tempo=12000, time_ms=774)])
        destination = root / 'packet'
        destination.mkdir()
        for ext, name in audit.NAMES.items():
            raw = bytearray(self.bytes[ext])
            if ext == '.DAT':
                # The candidate has an empty table; never silently copy native.
                offset = raw.index(b'PVBR')
                raw[offset + 16:offset + 1620] = bytes(1604)
            (destination / name).write_bytes(raw)
        return dict(frames=self.renderer_frames, rate=44100, source_write_authority=False)

    def compare(self, **kwargs):
        args = dict(audio=self.audio, native_packets=self.paths, output=self.output, renderer=self.renderer)
        args.update(kwargs)
        with (mock.patch.object(audit, 'timing_audit', side_effect=self.timing),
              mock.patch.object(audit, 'scan_mp3', return_value=SimpleNamespace(
                  cbr=False, info_kind='Xing', sample_count=115200, sample_rate=44100)),
              mock.patch.object(audit, 'compare_pvbr', return_value={'rows': []})):
            return audit.compare(**args)

    def test_captured_render_and_exact_grid_keep_seek_mismatch_visible(self):
        original = {path: path.read_bytes() for path in (self.audio, *self.paths.values())}
        report = self.compare()
        self.assertTrue(report['grid_exact'])
        self.assertFalse(report['seek_table_exact'])
        self.assertFalse(report['comparison_ok'])
        self.assertTrue(report['protected_inputs_unchanged'])
        self.assertFalse(report['source_write_authority'])
        self.assertFalse(report['native_acceptance'])
        self.assertEqual(report, json.loads(self.output.read_bytes()))
        self.assertEqual(original, {path: path.read_bytes() for path in original})
        self.assertNotEqual(report['packets']['.DAT']['native']['sha256'],
                            report['packets']['.DAT']['generated']['sha256'])
        self.assertEqual({path.name for path in self.root.iterdir()}, {'protected', 'report.json'})

    def test_matching_grid_and_seek_still_require_waveform_counts_and_renderer_clock(self):
        def matching(command, root, request):
            result = self.renderer(command, root, request)
            (root / 'packet' / audit.NAMES['.DAT']).write_bytes(self.bytes['.DAT'])
            return result

        cases = ((['native_renderer_detail_counts_differ'], 115200, False),
                 (['header_renderer_durations_differ'], 115200, True),
                 ([], 115201, False))
        for index, (issues, frames, expected) in enumerate(cases):
            with self.subTest(issues=issues, frames=frames):
                self.timing_issues, self.renderer_frames = issues, frames
                output = self.output.with_name(f'case-{index}.json')
                report = self.compare(renderer=matching, output=output)
                self.assertTrue(report['grid_exact'])
                self.assertTrue(report['seek_table_exact'])
                self.assertEqual(report['comparison_ok'], expected)
                argv = ['mp3-analysis-audit', str(self.audio), '--report', str(output)]
                for ext, path in self.paths.items():
                    argv.extend(['--native-' + ext[1:].lower(), str(path)])
                with (mock.patch.object(sys, 'argv', argv),
                      mock.patch.object(audit, 'compare', return_value=report),
                      mock.patch('builtins.print')):
                    self.assertEqual(audit.main(), 0 if expected else 1)

    def test_existing_report_aliases_and_protected_destinations_are_refused_before_render(self):
        self.output.write_text('preserve')
        forbidden = mock.Mock(side_effect=AssertionError('render forbidden'))
        with mock.patch.object(audit, 'run_renderer', forbidden):
            with self.assertRaises(ValueError):
                self.compare(renderer=forbidden)
            with self.assertRaises(ValueError):
                self.compare(output=self.inputs / 'new.json', renderer=forbidden)
            alias = self.root / 'alias.mp3'
            alias.symlink_to(self.audio)
            with self.assertRaises(ValueError):
                self.compare(audio=alias, renderer=forbidden)
            alias = self.root / 'alias.DAT'
            alias.symlink_to(self.paths['.DAT'])
            with self.assertRaises(ValueError):
                self.compare(native_packets={**self.paths, '.DAT': alias}, renderer=forbidden)
        forbidden.assert_not_called()
        self.assertEqual(self.output.read_text(), 'preserve')
        self.assertFalse((self.inputs / 'new.json').exists())

    def test_foreign_packet_filename_is_refused_before_rendering(self):
        raw = self.bytes['.DAT'].replace('source'.encode('utf-16-be'), 'foeign'.encode('utf-16-be'))
        self.paths['.DAT'].write_bytes(raw)
        forbidden = mock.Mock(side_effect=AssertionError('render forbidden'))
        with self.assertRaisesRegex(ValueError, 'exact MP3 reference filename'):
            self.compare(renderer=forbidden)
        forbidden.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_changed_native_packet_aborts_without_publishing_a_report(self):
        def changed(command, root, request):
            value = self.renderer(command, root, request)
            self.paths['.DAT'].write_bytes(self.bytes['.DAT'] + b'external-change')
            return value

        with self.assertRaisesRegex(ValueError, 'reference changed'):
            self.compare(renderer=changed)
        self.assertFalse(self.output.exists())

    def test_changed_original_audio_aborts_without_publishing_a_report(self):
        def changed(command, root, request):
            value = self.renderer(command, root, request)
            self.audio.write_bytes(self.audio.read_bytes() + b'external-change')
            return value

        with self.assertRaisesRegex(ValueError, 'MP3 reference changed'):
            self.compare(renderer=changed)
        self.assertFalse(self.output.exists())
