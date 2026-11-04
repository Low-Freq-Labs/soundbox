"""Synchronous immutable waveform uploads without another full ctypes copy."""
import ctypes
import gc
import struct
import tracemalloc
from types import SimpleNamespace
import unittest

from desktop.native_deck import NativeDeckBackend


class NativeWaveformBuffers(unittest.TestCase):
    def detail(self):
        return dict(rate=1000.0, base_count=4, mip_counts=[4, 2], mip_divisors=[1, 2],
                    range_min_mips=[struct.pack('<4h', -32768, -128, 0, 255),
                                    struct.pack('<2h', -32768, 0)],
                    range_max_mips=[struct.pack('<4h', -32767, 128, 0, 32767),
                                    struct.pack('<2h', 128, 32767)],
                    encoding='song-anchored-extrema-i16le-v1')

    def backend(self, function):
        return SimpleNamespace(_handle=None, _analysis_generation=7,
            _lib=SimpleNamespace(sb_deck_set_audio_detail_range_mips_i16_for_generation=function))

    def test_typed_c_call_retains_all_signed_bytes_during_collection_and_reentrancy(self):
        calls = []
        prototype = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_uint64,
            ctypes.POINTER(ctypes.c_int16), ctypes.POINTER(ctypes.c_int16),
            ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_size_t, ctypes.c_size_t, ctypes.c_double)

        def upload(handle, generation, minimum, maximum, counts, divisors, levels, base, rate):
            gc.collect()
            pressure = bytes(range(256)) * 4096
            size = sum(counts[index] for index in range(levels)) * 2
            calls.append((handle, generation, ctypes.string_at(minimum, size),
                          ctypes.string_at(maximum, size), list(counts[:levels]),
                          list(divisors[:levels]), base, rate, len(pressure)))
            return 1

        native = prototype(upload)
        detail = self.detail()
        NativeDeckBackend._set_audio_detail(self.backend(native), detail)
        self.assertEqual(calls, [(None, 7, b''.join(detail['range_min_mips']),
            b''.join(detail['range_max_mips']), [4, 2], [1, 2], 4, 1000.0, 1048576)])

    def test_large_upload_requires_only_one_transient_flattened_copy(self):
        counts = [1000000, 500000]
        minimum = [b'\x00\x80' * count for count in counts]
        maximum = [b'\xff\x7f' * count for count in counts]
        detail = dict(rate=1000.0, base_count=counts[0], mip_counts=counts,
                      mip_divisors=[1, 2], range_min_mips=minimum, range_max_mips=maximum,
                      encoding='song-anchored-extrema-i16le-v1')
        seen = []
        def upload(_handle, _generation, low, high, *_rest):
            seen.append((low[0], high[sum(counts) - 1]))
        tracemalloc.start()
        try:
            NativeDeckBackend._set_audio_detail(self.backend(upload), detail)
            _current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(seen, [(-32768, 32767)])
        flattened_bytes = sum(counts) * 2 * 2
        self.assertLess(peak, flattened_bytes * 1.3)

    def test_empty_malformed_and_mutable_ranges_never_enter_native_upload(self):
        cases = [dict(range_min_mips=[], range_max_mips=[], mip_counts=[], mip_divisors=[]),
                 dict(range_min_mips=[b'', b''], mip_counts=[0, 0]),
                 dict(range_min_mips=[b'\0', b'\0']),
                 dict(range_min_mips=[bytearray(8), b'\0' * 4]),
                 dict(range_max_mips=[memoryview(bytes(8)), b'\0' * 4])]
        for change in cases:
            calls = []
            detail = dict(self.detail(), **change)
            NativeDeckBackend._set_audio_detail(self.backend(lambda *args: calls.append(args)), detail)
            self.assertEqual(calls, [], change)


if __name__ == '__main__':
    unittest.main()
