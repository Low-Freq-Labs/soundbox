"""Independent frame/seek research stays read-only and rejects unknown framing."""
from pathlib import Path
from dataclasses import replace
import hashlib
import os
import struct
import tempfile
import unittest
from unittest import mock

from research.native_library import mp3_seek as compatibility
from soundbox import mp3_scan as seek
from soundbox.rekordbox_write import ConflictError, WriteProofError


def frame(bitrate=128, rate=44100, channels=2, version=3):
    index = seek.BITRATES.index(bitrate)
    rate_index = seek.RATES.index(rate)
    header = (0x7ff << 21) | (version << 19) | (1 << 17) | (1 << 16) | (index << 12) | (rate_index << 10)
    if channels == 1:
        header |= 3 << 6
    length = 144000 * bitrate // rate
    return struct.pack('>I', header) + bytes(length - 4)


def info_frame(audio, *, kind=b'Xing', bad_count=0, rate=44100):
    value = bytearray(frame(rate=rate))
    value[36:52] = kind + struct.pack('>III', 3, len(audio) + bad_count, len(value) + sum(map(len, audio)))
    return bytes(value)


class MP3SeekResearch(unittest.TestCase):
    def setUp(self):
        self.area = tempfile.TemporaryDirectory()
        self.addCleanup(self.area.cleanup)
        self.root = Path(self.area.name).resolve()
        self.path = self.root / 'capture.mp3'

    def write(self, raw):
        self.path.write_bytes(raw)
        return self.path

    def test_research_compatibility_exports_preserve_every_implementation_object(self):
        for name, value in vars(seek).items():
            if not name.startswith('__'):
                with self.subTest(name=name):
                    self.assertIs(getattr(compatibility, name), value)
        self.write(frame() * 5)
        scan = seek.scan_mp3(self.path)
        self.assertEqual(compatibility.scan_mp3(self.path), scan)
        self.assertEqual(compatibility.cbr_pvbr(scan), seek.cbr_pvbr(scan))

    def test_cbr_reconstruction_counts_untrimmed_samples_and_preserves_capture(self):
        raw = frame() * 5
        self.write(raw)
        before = self.path.stat()
        scan = seek.scan_mp3(self.path, expected_sha256=hashlib.sha256(raw).hexdigest())
        section = seek.cbr_pvbr(scan)
        self.assertEqual(section, struct.pack('>4sIII', b'PVBR', 16, 1620, 0) + bytes(1600) + struct.pack('>I', 5760))
        report = seek.compare_pvbr(scan, section)
        self.assertTrue(report['cbr_reconstruction_exact'])
        self.assertEqual(report['frame_boundary_matches'], 400)
        self.assertFalse(report['source_write_authority'])
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(self.path.stat().st_ino, before.st_ino)
        self.assertEqual(self.path.stat().st_mtime_ns, before.st_mtime_ns)

    def test_vbr_boundaries_are_relative_to_first_audio_frame_and_do_not_invent_generation(self):
        audio = [frame(n) for n in (224, 64, 96, 128)]
        leading = b'ID3\x04\0\0\0\0\0\x03abc'
        self.write(leading + info_frame(audio) + b''.join(audio) + b'TAG' + bytes(125))
        scan = seek.scan_mp3(self.path)
        self.assertEqual(scan.info_kind, 'Xing')
        self.assertEqual(scan.audio_frames, 4)
        self.assertEqual(scan.audio_start, len(leading) + len(info_frame(audio)))
        offsets = [0, len(audio[0]), len(audio[0]) + len(audio[1])]
        words = [offsets[min(i // 134, 2)] for i in range(400)] + [4 * 1152]
        section = struct.pack('>4sIII401I', b'PVBR', 16, 1620, 0, *words)
        report = seek.compare_pvbr(scan, section)
        self.assertTrue(report['all_frame_boundaries'])
        self.assertEqual(report['rows'][134]['audio_frame'], 1)
        self.assertIsNone(report['cbr_reconstruction_exact'])
        self.assertFalse(report['vbr_generation_supported'])
        with self.assertRaises(WriteProofError):
            seek.cbr_pvbr(scan)
        words[200] += 1
        broken = seek.compare_pvbr(scan, struct.pack('>4sIII401I', b'PVBR', 16, 1620, 0, *words))
        self.assertEqual(broken['frame_boundary_matches'], 399)
        self.assertFalse(broken['all_frame_boundaries'])

    def test_rejects_truncation_junk_unknown_tags_and_inconsistent_frames(self):
        cases = [b'', b'junk' + frame(), frame() + b'junk', frame()[:-1],
                 frame() + frame(rate=48000), frame() + frame(channels=1), frame(version=2),
                 frame(rate=32000), b'ID3\x04\0\0\x80\0\0\0' + frame(),
                 b'ID3\x04\0\x10\0\0\0\0' + frame(), b'ID3\x02\0\0\0\0\0\0' + frame(),
                 b'ID3\x04\0\0\0\0\x10\0' + frame(),
                 info_frame([frame()], bad_count=1) + frame(), info_frame([frame()])]
        for raw in cases:
            with self.subTest(size=len(raw), start=raw[:12]):
                self.write(raw)
                with self.assertRaises(WriteProofError):
                    seek.scan_mp3(self.path)

    def test_budgets_expected_digest_and_aliases_fail_closed(self):
        self.write(frame() * 3)
        for kwargs in (dict(max_bytes=1), dict(max_frames=2), dict(max_bytes=seek.MAX_BYTES + 1), dict(max_frames=0)):
            with self.subTest(kwargs=kwargs), self.assertRaises(WriteProofError):
                seek.scan_mp3(self.path, **kwargs)
        with self.assertRaises(ConflictError):
            seek.scan_mp3(self.path, expected_sha256='0' * 64)
        alias = self.root / 'alias.mp3'
        alias.symlink_to(self.path)
        with self.assertRaises(WriteProofError):
            seek.scan_mp3(alias)
        with self.assertRaises(WriteProofError):
            seek.scan_mp3(Path(os.path.relpath(self.path)))
        directory_alias = self.root / 'alias-dir'
        directory_alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(WriteProofError):
            seek.scan_mp3(directory_alias / self.path.name)

    def test_rejects_input_drift_and_native_shape_or_count_mismatch(self):
        self.write(frame() * 2)
        scan = seek.scan_mp3(self.path)
        section = seek.cbr_pvbr(scan)
        for raw in (section[:-1], b'xxxx' + section[4:], section[:-4] + struct.pack('>I', 1)):
            with self.assertRaises(WriteProofError):
                seek.compare_pvbr(scan, raw)
        original = seek.epoch
        count = 0
        def changing_epoch(value):
            nonlocal count
            count += 1
            result = original(value)
            return (*result[:-1], result[-1] + 1) if count == 3 else result
        with mock.patch.object(seek, 'epoch', side_effect=changing_epoch), self.assertRaises(ConflictError):
            seek.scan_mp3(self.path)

    def test_plain_id3v23_and_info_are_inspectable_without_extending_cbr_profile(self):
        audio = [frame()] * 2
        leading = b'ID3\x03\0\0\0\0\0\0'
        self.write(leading + info_frame(audio, kind=b'Info') + b''.join(audio))
        scan = seek.scan_mp3(self.path)
        self.assertEqual(scan.sample_count, 2304)
        self.assertTrue(scan.cbr)
        self.assertEqual(scan.info_kind, 'Info')
        with self.assertRaises(WriteProofError):
            seek.cbr_pvbr(scan)

    def test_malformed_inspection_facts_cannot_produce_a_matching_report(self):
        self.write(frame() * 2)
        scan = seek.scan_mp3(self.path)
        section = seek.cbr_pvbr(scan)
        cases = [None, replace(scan, sample_rate=True), replace(scan, channels=True),
                 replace(scan, audio_frames=3), replace(scan, bitrates=(128,)),
                 replace(scan, frame_offsets=(0, 1)), replace(scan, frame_lengths=(1, 1)),
                 replace(scan, sha256='0'), replace(scan, info_kind='unknown'),
                 replace(scan, mpeg_end=1), replace(scan, audio_start=1),
                 replace(scan, frame_offsets=list(scan.frame_offsets))]
        for value in cases:
            with self.subTest(value=value):
                with self.assertRaises(WriteProofError):
                    seek.cbr_pvbr(value)
                with self.assertRaises(WriteProofError):
                    seek.compare_pvbr(value, section)
                with self.assertRaises(WriteProofError):
                    seek.vbr_pvbr(value)

    def test_vbr_all_bucket_frame_indices_match_two_independent_native_sequences(self):
        # SHA-256 of all 400 big-endian native audio-frame indices, obtained
        # independently from the 32s and 19s generated native reference packets.
        # Synthetic varying frame sizes make byte offsets distinguishable from
        # sample counts, file offsets and the leading Xing information frame.
        native = {
            1226: '9ac9a4cf393a2b22b955bec38fe76b775a72199b0b7e021075939544ccfad375',
            734: '7bc7786b7dc472c43bf4f0c0501fceabbd915a46ac92da577147d8ac4bc9c85b',
        }
        for count, expected in native.items():
            with self.subTest(audio_frames=count):
                audio = [frame((224, 64, 96, 128)[i % 4]) for i in range(count)]
                self.write(b'ID3\x04\0\0\0\0\0\x03abc' + info_frame(audio) + b''.join(audio))
                scan = seek.scan_mp3(self.path)
                section = seek.vbr_pvbr(scan)
                words = struct.unpack('>401I', section[16:])
                self.assertEqual(words[-1], count * 1152)
                lookup = {offset - scan.audio_start: i for i, offset in enumerate(scan.frame_offsets[1:])}
                indices = [lookup[offset] for offset in words[:-1]]
                self.assertEqual(hashlib.sha256(struct.pack('>400I', *indices)).hexdigest(), expected)
                self.assertEqual(indices[0], 0)
                self.assertEqual(indices[-1], count - 8)
                self.assertEqual(len(section), 1620)
                report = seek.compare_pvbr(scan, section)
                self.assertTrue(report['research_vbr_reconstruction_exact'])
                self.assertTrue(report['all_frame_boundaries'])
                self.assertFalse(report['source_write_authority'])
                self.assertFalse(report['vbr_generation_supported'])
                # An altered but still valid frame boundary must fail exact
                # reconstruction, even though all offsets remain well framed.
                changed = list(words)
                changed[200] = words[201]
                altered = struct.pack('>4sIII401I', b'PVBR', 16, 1620, 0, *changed)
                self.assertTrue(seek.compare_pvbr(scan, altered)['all_frame_boundaries'])
                self.assertFalse(seek.compare_pvbr(scan, altered)['research_vbr_reconstruction_exact'])

    def test_vbr_generation_rejects_unobserved_info_rate_and_tagless_profiles(self):
        for rate, kind, bitrates, information in [
            (44100, b'Info', [64, 128], True), (48000, b'Xing', [64, 128], True),
            (44100, b'Xing', [128, 128], True), (44100, b'Xing', [64, 128], False),
        ]:
            with self.subTest(rate=rate, kind=kind, bitrates=bitrates, information=information):
                audio = [frame(bitrate, rate=rate) for bitrate in bitrates]
                self.write((info_frame(audio, kind=kind, rate=rate) if information else b'') + b''.join(audio))
                scan = seek.scan_mp3(self.path)
                with self.assertRaises(WriteProofError):
                    seek.vbr_pvbr(scan)
