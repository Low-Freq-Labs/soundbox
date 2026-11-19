"""Generated classic writer-format admission; never physical-device acceptance."""
import struct
import unittest

from soundbox.stock_ipod_format import inspect_stock_write_format, DATABASE_PROFILE, ARTWORK_PROFILE


def database(*, version=0x19, checksum=0, header=0x68, total=0x68):
    data = bytearray(max(header, 0x50)); data[:4] = b'mhbd'
    for offset, value in ((4, header), (8, total), (0x10, version)):
        struct.pack_into('<I', data, offset, value)
    struct.pack_into('<H', data, 0x30, checksum)
    return bytes(data)


def artwork(formats=((1028, 20000), (1029, 80000))):
    data = bytearray(132 + 16 + 12 + len(formats) * 24)
    data[:4] = b'mhfd'; struct.pack_into('<II', data, 4, 132, len(data))
    struct.pack_into('<I', data, 20, 1)
    data[132:136] = b'mhsd'; struct.pack_into('<III', data, 136, 16, len(data) - 132, 3)
    data[148:152] = b'mhlf'; struct.pack_into('<II', data, 152, 12, len(formats))
    for index, (identifier, size) in enumerate(formats):
        offset = 160 + index * 24; data[offset:offset + 4] = b'mhif'
        struct.pack_into('<II', data, offset + 4, 24, 24)
        struct.pack_into('<II', data, offset + 16, identifier, size)
    return bytes(data)


class StockWriteFormatTests(unittest.TestCase):
    def test_model_independent_database_profile_and_unknown_art_are_distinct(self):
        result = inspect_stock_write_format(database())
        self.assertTrue(result.writable)
        self.assertEqual(result.database_profile, DATABASE_PROFILE)
        self.assertFalse(result.supports_artwork)
        self.assertIn('unknown', result.artwork_reason)

    def test_only_observed_supported_art_formats_or_video_family_allow_output(self):
        for options in ({'artwork_database': artwork()}, {'usb_family': 'iPod Video'}):
            with self.subTest(options=tuple(options)):
                result = inspect_stock_write_format(database(), **options)
                self.assertEqual(result.artwork_profile, ARTWORK_PROFILE)
        for data in (artwork(((1028, 123), (1029, 80000))), artwork(((1017, 123),)), b'corrupt'):
            result = inspect_stock_write_format(database(), data, usb_family='iPod Video')
            self.assertTrue(result.writable)
            self.assertFalse(result.supports_artwork)
            self.assertTrue(result.artwork_reason)

    def test_checksum_version_and_framing_refuse_before_write(self):
        for data, reason in ((database(checksum=1), 'checksum family 1'),
                (database(version=0x30), 'version 0x30'),
                (database(header=32), 'header framing'),
                (database(total=0x69), 'truncated'),
                (database() + b'x', 'truncated'), (b'mhbd', 'uncompressed')):
            with self.subTest(reason=reason):
                result = inspect_stock_write_format(data)
                self.assertFalse(result.writable)
                self.assertIn(reason, result.reason)

    def test_header_only_classification_is_bounded_and_full_capture_rechecks_length(self):
        header = database(total=100000)[:0x50]
        self.assertTrue(inspect_stock_write_format(header, header_only=True).writable)
        self.assertFalse(inspect_stock_write_format(header).writable)

    def test_art_descriptor_truncation_duplicates_and_wrong_sizes_do_not_guess(self):
        for data in (artwork()[:-1], artwork(((1028, 20000), (1028, 20000))),
                     artwork(((1028, 20000), (1029, 80000), (9999, 4)))):
            with self.subTest(size=len(data)):
                result = inspect_stock_write_format(database(), data)
                self.assertTrue(result.writable)
                self.assertFalse(result.supports_artwork)


if __name__ == '__main__':
    unittest.main()
