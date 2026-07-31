"""Read-only V4/V5 lifecycle coverage audit."""

from __future__ import annotations

import json
import tempfile
import unittest
import wave
from pathlib import Path

from scripts.audit_analysis import audit
from soundbox import imports, libcache
from soundbox.library import Library, Track
from soundbox import track_analysis
from soundbox.track_analysis import store


class AnalysisAudit(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="sb-analysis-audit-")
        self.data = Path(self.temp.name)
        self.audio = self.data / "track.wav"
        with wave.open(str(self.audio), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(8000)
            out.writeframes(b"\0\0" * 800)
        track = Track(
            track_id="1", name="One", artist="Artist",
            location_uri=self.audio.as_uri(),
        )
        libcache.dump(
            Library(
                tracks_by_id={"1": track}, playlists=[],
                source=Path("snapshot"),
            ),
            self.data / "libcache.json",
        )
        (self.data / "library.json").write_text(
            json.dumps({"imported": {}, "hidden_tracks": {"1": "today"}}),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_hidden_track_is_audited_and_blocks_retirement_until_ready(self):
        result = audit(self.data, full=True)
        self.assertEqual(result["total_tracks"], 1)
        self.assertEqual(result["missing_analysis"], ["1"])
        self.assertFalse(result["safe_to_retire_v4"])

        stored = store(
            self.data / "track_analysis", "1", self.audio,
            {
                "sample_rate": 150.0,
                "overview_heights": b"\xff\xff",
                "overview_rgb": b"\x02\x03\x04",
                "blue_detail": b"\x01",
                "rgb_detail": b"\x00\x04",
                "three_band": b"\x01\x02\x03",
                "spectral_bands": bytes(24),
                "beats": [],
                "cues": [],
                "_audio_detail": {
                    "rate": 6144.0,
                    "heights": b"\xff\xff",
                    "encoding": track_analysis.HEIGHT_ENCODING,
                },
                "color_model": track_analysis.COLOR_MODEL,
                "waveform_model": track_analysis.WAVEFORM_MODEL,
                "three_band_model": track_analysis.THREE_BAND_MODEL,
                "display_waveform_model":
                    track_analysis.DISPLAY_WAVEFORM_MODEL,
                "height_encoding": track_analysis.HEIGHT_ENCODING,
            },
        )
        self.assertIsNotNone(stored)
        result = audit(self.data, full=True)
        self.assertEqual(result["ready"], 1)
        self.assertEqual(result["current_v5"], 1)
        self.assertEqual(result["playable_legacy_v4"], 0)
        self.assertEqual(result["needs_current_analysis"], [])
        self.assertTrue(result["safe_to_retire_v4"])
        self.assertEqual(result["orphan_v5_records"], [])
        self.assertEqual(result["orphan_v4_records"], [])

    def test_imported_track_is_included(self):
        imported_audio = self.data / "imported.wav"
        imported_audio.write_bytes(self.audio.read_bytes())
        state = {
            "imported": {
                "sb-imported": imports.probe(imported_audio),
            },
        }
        (self.data / "library.json").write_text(
            json.dumps(state), encoding="utf-8",
        )
        result = audit(self.data, full=True)
        self.assertEqual(result["total_tracks"], 2)
        self.assertEqual(
            set(result["missing_analysis"]), {"1", "sb-imported"},
        )


if __name__ == "__main__":
    unittest.main()
