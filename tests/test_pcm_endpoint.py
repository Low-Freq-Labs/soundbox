"""Bounded endpoint evidence on synthetic files only; no audio payload reads."""
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

from soundbox.pcm_endpoint import capture_pcm_endpoint
from soundbox.rekordbox_write import ConflictError, WriteProofError


def wave_bytes(frames=5677875, rate=44100, *, encoding=1, channels=2, bits=24):
    align = channels * (bits // 8)
    fmt = struct.pack('<HHIIHH', encoding, channels, rate, rate * align, align, bits)
    data = bytes(frames * align)
    body = b'WAVEfmt ' + struct.pack('<I', len(fmt)) + fmt
    body += b'data' + struct.pack('<I', len(data)) + data + bytes(len(data) & 1)
    return b'RIFF' + struct.pack('<I', len(body)) + body


class PcmEndpointTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name).resolve() / 'synthetic.wav'

    def test_exact_pcm24_endpoint_reads_only_framing_and_format(self):
        raw = wave_bytes()
        self.path.write_bytes(raw)
        with mock.patch('soundbox.pcm_endpoint.os.pread', wraps=os.pread) as read:
            proof = capture_pcm_endpoint(str(self.path))
        self.assertEqual((proof['frames'], proof['sample_rate'], proof['end_ms']),
                         (5677875, 44100, 128750))
        self.assertEqual([(c.args[2], c.args[1]) for c in read.call_args_list],
                         [(0, 12), (12, 8), (20, 16), (36, 8)])
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(capture_pcm_endpoint(str(self.path)), proof)

    def test_malformed_fractional_compressed_and_ambiguous_headers_fail_closed(self):
        valid = wave_bytes(frames=480, rate=48000)
        variants = [valid[:-1], valid + b'x', wave_bytes(frames=481, rate=48000),
                    wave_bytes(frames=480, rate=48000, encoding=3),
                    valid[:32] + b'\x01\x00' + valid[34:]]
        for extra in (valid[12:36], b'data\x00\x00\x00\x00'):
            body = valid[8:] + extra
            variants.append(b'RIFF' + struct.pack('<I', len(body)) + body)
        for raw in variants:
            with self.subTest(length=len(raw)):
                self.path.write_bytes(raw)
                with self.assertRaises(WriteProofError):
                    capture_pcm_endpoint(str(self.path))

    def test_extensible_pcm_and_bounded_unknown_chunks(self):
        fmt = struct.pack('<HHIIHHHHI', 0xfffe, 2, 48000, 288000, 6, 24, 22, 24, 3)
        fmt += bytes.fromhex('0100000000001000800000aa00389b71')
        chunks = b'fmt ' + struct.pack('<I', len(fmt)) + fmt
        chunks += b'JUNK\x03\x00\x00\x00abc\x00data' + struct.pack('<I', 2880) + bytes(2880)
        body = b'WAVE' + chunks
        self.path.write_bytes(b'RIFF' + struct.pack('<I', len(body)) + body)
        self.assertEqual(capture_pcm_endpoint(str(self.path))['end_ms'], 10)
        body = b'WAVE' + b'JUNK\x00\x00\x00\x00' * 1025 + chunks
        self.path.write_bytes(b'RIFF' + struct.pack('<I', len(body)) + body)
        with self.assertRaisesRegex(WriteProofError, 'bounded'):
            capture_pcm_endpoint(str(self.path))

    def test_redirect_and_mid_capture_change_are_rejected(self):
        self.path.write_bytes(wave_bytes(frames=480, rate=48000))
        link = self.path.with_name('redirect.wav')
        link.symlink_to(self.path)
        with self.assertRaises(WriteProofError):
            capture_pcm_endpoint(str(link))
        original = os.pread
        def changed(fd, length, offset):
            result = original(fd, length, offset)
            if offset == 36:
                self.path.write_bytes(self.path.read_bytes() + b'x')
            return result
        with mock.patch('soundbox.pcm_endpoint.os.pread', side_effect=changed):
            with self.assertRaises(ConflictError):
                capture_pcm_endpoint(str(self.path))
