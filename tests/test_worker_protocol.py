"""Bounded wire frames preserve large primitive records without executable data."""
import marshal
import os
from pathlib import Path
import struct
import tempfile
import threading
import unittest

from soundbox import libcache, library_codec, worker_protocol as protocol
from soundbox.library import Library, Track


class PrimitiveWorkerFraming(unittest.TestCase):
    def channel(self):
        read, write = os.pipe()
        channel = protocol.PipeChannel(read, write)
        self.addCleanup(channel.close)
        return channel

    def writer(self, callback):
        errors = []
        def write():
            try:callback()
            except Exception as error:errors.append(error)
        thread = threading.Thread(target=write)
        thread.start()
        self.addCleanup(lambda: thread.join(3))
        return thread, errors

    def test_ordinary_wire_bytes_are_unchanged(self):
        channel = self.channel();value = ('ordinary', 7, None, ['日本語'])
        raw = marshal.dumps(value, allow_code=False)
        self.assertEqual(channel.send(value), len(raw))
        self.assertEqual(os.read(channel.read_fd, 1000), struct.pack('!Q', len(raw)) + raw)

    def test_large_record_roundtrip_then_ordinary_message_preserves_exact_sequences(self):
        channel = self.channel()
        value = ('part', 1, 'nonce', {'info': 'x' * (2 * protocol.FRAME_BYTES),
                                    'cues': [{'label': '日本語', 'clock': 99}]})
        sizes = []
        original = channel._write
        def measured(raw, **kwargs):
            sizes.append(len(raw) + 8)
            original(raw, **kwargs)
        channel._write = measured
        thread, errors = self.writer(lambda: (channel.send(value), channel.send(('done',))))
        actual, size = channel.receive(timeout=3)
        self.assertEqual(actual, value)
        self.assertEqual(size, len(marshal.dumps(value, allow_code=False)))
        self.assertEqual(channel.receive(timeout=3)[0], ('done',))
        thread.join(3);self.assertFalse(thread.is_alive());self.assertEqual(errors, [])
        self.assertTrue(all(size <= protocol.FRAME_BYTES for size in sizes))

    def test_encoding_failure_writes_nothing_and_does_not_advance_identity(self):
        channel = self.channel()
        with self.assertRaises(protocol.EncodingError):channel.send(compile('1', '<synthetic>', 'eval'))
        channel.send(('next',))
        self.assertEqual(channel.receive()[0], ('next',))
        channel._write(marshal.dumps(compile('1', '<synthetic>', 'eval')))
        with self.assertRaisesRegex(protocol.ProtocolError, 'primitive'):channel.receive()

    def test_fragment_missing_order_identity_or_total_change_never_yields_partial_value(self):
        chunk = b'x' * protocol._CHUNK_BYTES
        total = protocol.FRAME_BYTES + 100
        for variant in ('missing', 'index', 'sequence', 'total', 'ordinary'):
            with self.subTest(variant=variant):
                channel = self.channel()
                first = (1, 0, 2, total, chunk)
                def write():
                    channel._write(marshal.dumps(first), fragmented=True)
                    if variant == 'missing':
                        os.close(channel.write_fd);channel.write_fd = -1
                        return
                    if variant == 'ordinary':
                        channel._write(marshal.dumps(('unexpected',)))
                        return
                    second = [1, 1, 2, total, b'x' * (total - len(chunk))]
                    second[{'index': 1, 'sequence': 0, 'total': 3}[variant]] += 1
                    channel._write(marshal.dumps(tuple(second)), fragmented=True)
                thread, errors = self.writer(write)
                with self.assertRaises(protocol.ProtocolError):channel.receive(timeout=3)
                thread.join(3);self.assertFalse(thread.is_alive());self.assertEqual(errors, [])

    def test_fixed_record_groups_and_large_single_record_preserve_wire_facts_and_cache_bytes(self):
        grouped = {f'logical-{i}': Track(str(i), f'Song {i}', 'Artist', 'file:///generated.wav',
            info={'comments': f'{i}:' + 'x' * 9000}) for i in range(257)}
        single = {'logical-one': Track('native-one', '日本語', 'Artist', 'file:///generated.wav',
            info={'comments': '日本語' * (protocol.FRAME_BYTES // 9 + 100)},
            source_cues=[{'label': 'Preserve cue', 'clock': 99}], source_tag_ids=['tag'])}
        for name, tracks, expected_counts in (('grouped', grouped, [128, 128, 1]),
                                               ('single', single, [1])):
            with self.subTest(name=name):
                original = Library(tracks, [], Path('/generated/master.db'))
                batches = list(library_codec.batches(original))
                track_batches = [rows for kind, _, rows in batches if kind == 'tracks']
                self.assertEqual([len(rows) for rows in track_batches], expected_counts)
                self.assertTrue(any(len(marshal.dumps(rows, allow_code=False)) > protocol.FRAME_BYTES
                                    for rows in track_batches))
                channel = self.channel()
                wire_sizes = []
                write_frame = channel._write
                def measured(raw, **kwargs):
                    wire_sizes.append(len(raw) + 8)
                    write_frame(raw, **kwargs)
                channel._write = measured
                messages = [('library', library_codec.describe(original)),
                            *(('part', kind, field, rows) for kind, field, rows in batches), ('done',)]
                def send():
                    for message in messages:channel.send(message)
                thread, errors = self.writer(send)
                descriptor, _size = channel.receive(timeout=3)
                self.assertEqual(descriptor[0], 'library')
                receiver = library_codec.LibraryReceiver(descriptor[1], max_records=None)
                for _ in batches:
                    message, _size = channel.receive(timeout=3)
                    self.assertEqual(message[0], 'part')
                    receiver.add(*message[1:])
                self.assertEqual(channel.receive(timeout=3)[0], ('done',))
                thread.join(3)
                self.assertFalse(thread.is_alive())
                self.assertEqual(errors, [])
                self.assertTrue(all(size <= protocol.FRAME_BYTES for size in wire_sizes))
                actual = receiver.finish()
                self.assertEqual(actual, original)
                self.assertEqual(list(actual.tracks_by_id), list(original.tracks_by_id))
                with tempfile.TemporaryDirectory() as directory:
                    before, after = Path(directory) / 'before.json', Path(directory) / 'after.json'
                    libcache.dump(original, before)
                    libcache.dump(actual, after)
                    self.assertEqual(before.read_bytes(), after.read_bytes())
