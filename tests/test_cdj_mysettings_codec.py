import binascii
import hashlib
import struct
import unittest

from research.cdj_export.mysettings_codec import catalogue, inspect, patch


def framed_file(filename="MYSETTING.DAT"):
    """Independent framing with nondefault reserved bytes to catch rebuilding."""
    mixer = filename == "DJMMYSETTING.DAT"
    body_size = 52 if mixer else 40
    raw = bytearray(108 + body_size)
    struct.pack_into("<I", raw, 0, 96)
    for offset, value in ((4, "PioneerDJ" if mixer else "PIONEER"),
                          (36, "rekordbox"), (68, "1.000" if mixer else "0.001")):
        raw[offset:offset + 32] = value.encode().ljust(32, b"\0")
    struct.pack_into("<I", raw, 100, body_size)
    raw[104:104 + body_size] = bytes([0xA6]) * body_size
    if filename == "MYSETTING.DAT":
        raw[140] = 0x81  # TEMPO RANGE ±10
        raw[125] = 0x80  # EJECT/LOAD LOCK unlocked
    elif filename == "MYSETTING2.DAT":
        raw[116] = 0x85  # BEAT JUMP 16
    else:
        raw[116] = 0x81  # channel curve linear
    raw[-2:] = b"\x23\x45"
    return rechecksum(raw, mixer)


def rechecksum(raw, mixer=False):
    raw = bytearray(raw)
    struct.pack_into("<H", raw, len(raw) - 4,
                     binascii.crc_hqx(raw[0 if mixer else 104:-4], 0))
    return bytes(raw)


def edited(raw, changes, filename="MYSETTING.DAT"):
    return patch(filename, raw, changes, expected_sha256=hashlib.sha256(raw).hexdigest())


class MySettingsCodecTests(unittest.TestCase):
    def test_native_tempo_offset_and_byte_preservation(self):
        raw = framed_file()
        result = edited(raw, {"tempo_range": "sixteen"})
        self.assertEqual(result[140], 0x82)
        for offset in range(len(raw)):
            if offset not in (140, 144, 145):
                self.assertEqual(result[offset], raw[offset], offset)
        self.assertEqual(inspect("MYSETTING.DAT", result)["fields"]["tempo_range"]["value"],
                         "sixteen")
        self.assertEqual(edited(result, {"tempo_range": "ten"}), raw)

    def test_noop_keeps_exact_original_bytes(self):
        raw = framed_file()
        self.assertIs(edited(raw, {}), raw)
        self.assertIs(edited(raw, {"tempo_range": "ten"}), raw)

    def test_unknown_unrelated_fields_remain_visible_and_untouched(self):
        raw = framed_file()
        record = inspect("MYSETTING.DAT", raw)["fields"]["jog_mode"]
        self.assertEqual(record["raw"], 0xA6)
        self.assertFalse(record["recognized"])
        self.assertIsNone(record["value"])
        self.assertEqual(edited(raw, {"tempo_range": "sixteen"})[137], 0xA6)
        with self.assertRaisesRegex(ValueError, "Unrecognized existing"):
            edited(raw, {"jog_mode": "vinyl"})

    def test_stale_base_crc_and_bad_framing_fail_closed(self):
        raw = framed_file()
        with self.assertRaisesRegex(ValueError, "base changed"):
            patch("MYSETTING.DAT", raw, {"tempo_range": "sixteen"}, expected_sha256="0" * 64)
        broken = bytearray(raw)
        broken[110] ^= 1
        with self.assertRaisesRegex(ValueError, "checksum"):
            inspect("MYSETTING.DAT", bytes(broken))
        for offset in (0, 4, 36, 68, 100):
            broken = bytearray(raw)
            broken[offset] ^= 1
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                inspect("MYSETTING.DAT", rechecksum(broken))
        for data in (raw[:-1], raw + b"x"):
            with self.assertRaises(ValueError):
                inspect("MYSETTING.DAT", data)

    def test_unknown_fields_and_invalid_values_do_not_partially_apply(self):
        raw = framed_file()
        for changes in ({"tempo_range": "sixteen", "unmapped": "on"},
                        {"tempo_range": "typo"}, {"tempo_range": 130},
                        {"tempo_range": True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                edited(raw, changes)
        self.assertEqual(raw[140], 0x81)

    def test_multiple_fields_patch_without_rebuilding_reserved_regions(self):
        raw = framed_file()
        result = edited(raw, {"tempo_range": "sixteen", "eject_lock": "lock"})
        self.assertEqual((result[125], result[140]), (0x81, 0x82))
        self.assertEqual(result[104:125], raw[104:125])
        self.assertEqual(result[-2:], b"\x23\x45")

    def test_second_player_file_has_distinct_fields_and_same_crc_region(self):
        raw = framed_file("MYSETTING2.DAT")
        result = edited(raw, {"beat_jump_beat_value": "thirtytwo"}, "MYSETTING2.DAT")
        self.assertEqual(result[116], 0x86)
        self.assertEqual(edited(result, {"beat_jump_beat_value": "sixteen"}, "MYSETTING2.DAT"), raw)
        with self.assertRaises(ValueError):
            edited(raw, {"tempo_range": "sixteen"}, "MYSETTING2.DAT")

    def test_mixer_checksum_covers_header_as_well_as_body(self):
        raw = framed_file("DJMMYSETTING.DAT")
        result = edited(raw, {"channel_fader_curve": "steep_bottom"}, "DJMMYSETTING.DAT")
        self.assertEqual(result[116], 0x82)
        expected = binascii.crc_hqx(result[:-4], 0)
        self.assertEqual(struct.unpack_from("<H", result, 156)[0], expected)
        self.assertNotEqual(expected, binascii.crc_hqx(result[104:-4], 0))
        self.assertEqual(result[:104], raw[:104])
        self.assertEqual(edited(result, {"channel_fader_curve": "linear"}, "DJMMYSETTING.DAT"), raw)

    def test_catalogue_is_detached_and_exposes_candidate_status(self):
        first = catalogue()
        first["formats"].clear()
        self.assertEqual(len(catalogue()["formats"]), 3)
        self.assertIn("candidate", catalogue()["status"])
        self.assertFalse(inspect("MYSETTING.DAT", framed_file())["native_edit_accepted"])

    def test_only_named_immutable_profile_bytes_are_accepted(self):
        raw = framed_file()
        for name in ("../MYSETTING.DAT", "djprofile.nxs", "/Volumes/x/MYSETTING.DAT"):
            with self.assertRaises(ValueError):
                inspect(name, raw)
        with self.assertRaises(ValueError):
            inspect("MYSETTING.DAT", bytearray(raw))


if __name__ == "__main__":
    unittest.main()
