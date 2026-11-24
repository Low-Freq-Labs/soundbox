"""Device-side Rockbox derivatives never modify source audio or curated art."""

import hashlib
import io
import json
import os
import struct
import tempfile
import unittest
import wave
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from mutagen.id3 import APIC, COMM, ID3, TALB, TIT2, TPE1, TXXX
from mutagen.mp3 import MP3
from PIL import Image

from soundbox.rockbox_media import (
    ART_PROFILE,
    InterruptedFileVerifier,
    NORMALIZED_MEDIA_PROFILE,
    RECEIPT_SANITIZED_MEDIA_PROFILE,
    RockboxArtworkCache,
    RockboxMediaError,
    RockboxMediaCache,
    _audio_essence,
    _riff_info_values,
    _technical_signature,
)


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tagged_mp3(path: Path) -> None:
    # Repeated valid MPEG-1 Layer III frames keep this fixture hermetic; no
    # encoder binary or copyrighted audio fixture is required.
    frame = b"\xff\xfb\x90\x64" + b"\0" * 413
    path.write_bytes(frame * 100)
    tags = ID3()
    tags.add(APIC(
        mime="image/jpeg", type=3, desc="", data=b"oversized-art" * 1000))
    tags.save(path)


class InterruptedVerification(unittest.TestCase):
    def test_each_adoption_reads_exact_bytes_without_redundant_proof_writes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            live = root / "IPOD/Music/Artist/Track.wav"
            live.parent.mkdir(parents=True)
            payload = b"device payload" * 200_000
            live.write_bytes(payload)
            digest = _hash(live)
            cache = root / "cache"
            baseline = "a" * 64
            with mock.patch("soundbox.rockbox_media.record_metric") as metric:
                for verifier in (InterruptedFileVerifier(cache, baseline),
                                 InterruptedFileVerifier(cache, baseline)):
                    self.assertTrue(verifier.verify(
                        live, relative_path=Path("Music/Artist/Track.wav"),
                        size=len(payload), sha256=digest))
            read_bytes = sum(call.args[1] for call in metric.call_args_list
                             if call.args[0] == "interrupted_verification_bytes_read")
            self.assertEqual(read_bytes, 2 * len(payload))
            self.assertFalse(cache.exists())
            self.assertEqual(live.read_bytes(), payload)

    def test_equal_fat_epoch_and_edges_never_accept_changed_middle_from_old_proof(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            live = root / "track.wav"
            live.write_bytes(b"a" * (3 * 1024 * 1024))
            before = live.stat()
            digest = _hash(live)
            cache = root / "cache"
            proof = cache / "interrupted-verification" / ("b" * 64 + ".json")
            proof.parent.mkdir(parents=True)
            old_receipt = json.dumps({
                "schema": 1, "baseline_sha256": "b" * 64,
                "files": {"/track.wav": {
                    "size": before.st_size, "sha256": digest,
                    "mtime_ns": before.st_mtime_ns,
                    "edge_sha256": hashlib.sha256(b"a" * (2 * 1024 * 1024)).hexdigest(),
                }},
            }).encode()
            proof.write_bytes(old_receipt)
            with live.open("r+b") as handle:
                handle.seek(1536 * 1024)
                handle.write(b"changed middle")
            os.utime(live, ns=(before.st_atime_ns, before.st_mtime_ns))
            # Reproduce coarse FAT epochs, including ctime, without real media.
            original_lstat = Path.lstat
            def coarse_lstat(path):
                return before if path == live else original_lstat(path)
            with mock.patch.object(Path, "lstat", coarse_lstat), \
                    mock.patch("soundbox.rockbox_media.os.fstat", return_value=before), \
                    mock.patch("soundbox.rockbox_media.record_metric") as metric:
                for _ in range(2):
                    verifier = InterruptedFileVerifier(cache, "b" * 64)
                    self.assertFalse(verifier.verify(
                        live, relative_path=Path("track.wav"),
                        size=before.st_size, sha256=digest))
            read_bytes = sum(call.args[1] for call in metric.call_args_list
                             if call.args[0] == "interrupted_verification_bytes_read")
            self.assertEqual(read_bytes, 2 * before.st_size)
            self.assertEqual(proof.read_bytes(), old_receipt)
            self.assertEqual(list(proof.parent.iterdir()), [proof])

    def test_cancelled_adoption_does_not_read_or_write(self):
        from threading import Event
        from soundbox.operation_control import OperationCancelled, operation_scope
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            live = root / "track.wav"
            live.write_bytes(b"audio")
            stop = Event()
            stop.set()
            with operation_scope(stop), self.assertRaises(OperationCancelled):
                InterruptedFileVerifier(root / "cache", "c" * 64).verify(
                    live, relative_path=Path("track.wav"), size=5, sha256=_hash(live))
            self.assertFalse((root / "cache").exists())

    def test_symlink_is_not_an_adoption_candidate(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "source.wav"
            source.write_bytes(b"audio")
            live = root / "track.wav"
            live.symlink_to(source)
            self.assertFalse(InterruptedFileVerifier(root / "cache", "d" * 64).verify(
                live, relative_path=Path("track.wav"), size=5, sha256=_hash(source)))


class RockboxAudioDerivative(unittest.TestCase):
    def test_malformed_derivative_metadata_repairs_only_the_disposable_audio_copy(self):
        with tempfile.TemporaryDirectory() as area:
            root = Path(area)
            source = root / 'source.mp3'
            _tagged_mp3(source)
            original = source.read_bytes()
            cache = RockboxMediaCache(root / 'cache')
            prepared = cache.prepare(source)
            payload = prepared.payload.read_bytes()
            path = prepared.payload.parent / 'manifest.json'
            baseline = json.loads(path.read_bytes())
            for row in ([], dict(baseline, removed_images=float('inf')),
                        dict(baseline, payload_size=float('inf'))):
                with self.subTest(row=row):
                    path.write_text(json.dumps(row))
                    rebuilt = cache.prepare(source)
                    self.assertEqual(rebuilt.payload.read_bytes(), payload)
                    self.assertEqual(rebuilt.payload_sha256, prepared.payload_sha256)
                    self.assertEqual(source.read_bytes(), original)

    def test_mp3_technical_signature_ignores_tag_sized_duration_estimate(self):
        class Info:
            sample_rate = 44_100
            channels = 2
            bits_per_sample = 0

            def __init__(self, length):
                self.length = length

        class FakeMP3:
            def __init__(self, length):
                self.info = Info(length)

        source = Path("cbr.mp3")
        with mock.patch(
                "soundbox.rockbox_media.mutagen.File",
                side_effect=(FakeMP3(152.1142), FakeMP3(152.1110))):
            before = _technical_signature(source)
            after = _technical_signature(source)

        self.assertEqual(before, after)
        self.assertIsNone(before[1])

    def test_committed_derivative_reuses_exact_incremental_baseline(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "stable.wav"
            with wave.open(str(source), "wb") as output:
                output.setnchannels(2)
                output.setsampwidth(2)
                output.setframerate(44_100)
                output.writeframes(b"\1\0\2\0" * 100)
            cache = RockboxMediaCache(root / "cache")
            first = cache.plan(source, title="Stable", artist="Fixture")
            row = {
                "source": str(source.resolve()),
                "title": "Stable",
                "artist": "Fixture",
                "size": first.payload_size,
                "sha256": first.payload_sha256,
                "source_size": first.source_size,
                "source_mtime_ns": first.source_mtime_ns,
                "source_ctime_ns": first.source_ctime_ns,
                "source_sha256": first.source_sha256,
                "source_sha256_verified": True,
                "media_profile": first.profile,
                "transformed": first.transformed,
            }

            with mock.patch.object(
                    cache, "plan", side_effect=AssertionError("no rebuild")), \
                    mock.patch(
                        "soundbox.rockbox_media.mutagen.File",
                        side_effect=AssertionError("no container tag scan"),
                    ), mock.patch(
                        "soundbox.rockbox_media._riff_info_values",
                        side_effect=AssertionError("no WAVE tag scan"),
                    ), mock.patch(
                        "soundbox.rockbox_media._embedded_picture_count",
                        side_effect=AssertionError("no art scan"),
                    ), mock.patch(
                        "soundbox.rockbox_media._display_tags",
                        side_effect=AssertionError("no display-tag scan"),
                    ):
                reused = cache.plan_from_committed(
                    source,
                    title="Stable",
                    artist="Fixture",
                    committed=row,
                )

            self.assertEqual(reused, first)

    def test_legacy_manifest_migrates_through_canonical_derivative(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "legacy.wav"
            with wave.open(str(source), "wb") as output:
                output.setnchannels(2)
                output.setsampwidth(2)
                output.setframerate(44_100)
                output.writeframes(b"\1\0\2\0" * 100)
            cache = RockboxMediaCache(root / "cache")
            committed = {
                "source": str(source.resolve()),
                "title": "Legacy",
                "artist": "Fixture",
                "size": 1234,
                "sha256": "a" * 64,
            }

            migrated = cache.plan_from_committed(
                source,
                title="Legacy",
                artist="Fixture",
                committed=committed,
            )

            self.assertTrue(migrated.transformed)
            self.assertTrue(migrated.source_sha256_verified)
            self.assertEqual(migrated.profile, NORMALIZED_MEDIA_PROFILE)
            self.assertNotEqual(migrated.payload_sha256, "a" * 64)

    def test_bounded_plan_discards_payload_then_rematerializes_exactly(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "bounded.mp3"
            _tagged_mp3(source)
            cache = RockboxMediaCache(root / "cache")

            planned = cache.plan(
                source, title="Bounded title", artist="Bounded artist")

            self.assertTrue(planned.transformed)
            self.assertFalse(list(cache.root.rglob("*.mp3")))
            self.assertEqual(_hash(source), planned.source_sha256)
            prepared = cache.materialize(planned)
            self.assertEqual(_hash(prepared.payload), planned.payload_sha256)
            self.assertEqual(prepared.payload.stat().st_size, planned.payload_size)
            cache.discard(prepared)
            self.assertFalse(list(cache.root.rglob("*.mp3")))

    def test_bounded_plan_refuses_changed_canonical_source(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "changed.mp3"
            _tagged_mp3(source)
            cache = RockboxMediaCache(root / "cache")
            planned = cache.plan(
                source, title="Before", artist="SoundBox")
            source.write_bytes(source.read_bytes() + b"changed")

            with self.assertRaisesRegex(
                    RockboxMediaError, "captured bytes differ"):
                cache.materialize(planned)
            self.assertFalse(list(cache.root.rglob("*.mp3")))

    def test_embedded_art_is_removed_only_from_verified_copy(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "Take a Trip.mp3"
            _tagged_mp3(source)
            source_hash = _hash(source)
            source_audio = MP3(source)
            self.assertEqual(len(source_audio.tags.getall("APIC")), 1)

            cache = RockboxMediaCache(root / "cache")
            prepared = cache.prepare(source)

            self.assertTrue(prepared.transformed)
            self.assertEqual(prepared.removed_images, 1)
            self.assertNotEqual(prepared.payload, source)
            self.assertEqual(_hash(source), source_hash)
            self.assertEqual(len(MP3(source).tags.getall("APIC")), 1)
            self.assertEqual(len(MP3(prepared.payload).tags.getall("APIC")), 0)
            self.assertEqual(MP3(prepared.payload).info.sample_rate, 44_100)
            self.assertEqual(_hash(prepared.payload), prepared.payload_sha256)

            # Exact cache hit is stable and does not rewrite the source.
            again = cache.prepare(source)
            self.assertEqual(again, prepared)
            self.assertEqual(_hash(source), source_hash)
            cache.clear()
            self.assertFalse((root / "cache").exists())

    def test_audio_without_embedded_art_passes_through(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "plain.mp3"
            frame = b"\xff\xfb\x90\x64" + b"\0" * 413
            source.write_bytes(frame * 100)
            prepared = RockboxMediaCache(root / "cache").prepare(source)
            self.assertFalse(prepared.transformed)
            self.assertNotEqual(prepared.payload, source.resolve())
            self.assertEqual(prepared.payload.read_bytes(), source.read_bytes())
            RockboxMediaCache(root / "cache").discard(prepared)
            self.assertFalse((root / "cache").exists())

    def test_display_tags_are_normalized_only_in_verified_mp3_copy(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "scrambled.mp3"
            frame = b"\xff\xfb\x90\x64" + b"\0" * 413
            source.write_bytes(frame * 100)
            source_hash = _hash(source)

            prepared = RockboxMediaCache(root / "cache").prepare(
                source, title="Correct title", artist="Correct artist")

            self.assertTrue(prepared.transformed)
            self.assertEqual(_hash(source), source_hash)
            self.assertIsNone(MP3(source).tags)
            tags = MP3(prepared.payload).tags
            self.assertEqual(tags.getall("TIT2")[0].text, ["Correct title"])
            self.assertEqual(tags.getall("TPE1")[0].text, ["Correct artist"])
            self.assertEqual(
                _audio_essence(prepared.payload), _audio_essence(source))

    def test_beatport_receipts_are_removed_only_from_rockbox_mp3_copy(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "beatport.mp3"
            frame = b"\xff\xfb\x90\x64" + b"\0" * 413
            source.write_bytes(frame * 100)
            tags = ID3()
            tags.add(TIT2(encoding=3, text=["Wrong title"]))
            tags.add(TPE1(encoding=3, text=["Wrong artist"]))
            tags.add(TALB(encoding=3, text=["Real album"]))
            tags.add(TXXX(
                encoding=3,
                desc="COMMENT",
                text=["Purchased at Beatport.com"],
            ))
            tags.add(COMM(
                encoding=3,
                lang="eng",
                desc="ID3v1 Comment",
                text=["Purchased at Beatport"],
            ))
            tags.add(COMM(
                encoding=3,
                lang="eng",
                desc="SoundBox fixture",
                text=["Keep this real note"],
            ))
            tags.save(source)
            source_hash = _hash(source)

            prepared = RockboxMediaCache(root / "cache").prepare(
                source, title="Correct title", artist="Correct artist")

            self.assertEqual(_hash(source), source_hash)
            payload_tags = MP3(prepared.payload).tags
            self.assertEqual(payload_tags.getall("TIT2")[0].text, ["Correct title"])
            self.assertEqual(payload_tags.getall("TPE1")[0].text, ["Correct artist"])
            self.assertEqual(payload_tags.getall("TALB"), [])
            self.assertEqual(
                prepared.profile, RECEIPT_SANITIZED_MEDIA_PROFILE)
            comments = [
                str(text)
                for item in payload_tags.getall("COMM")
                for text in item.text
            ]
            user_text = [
                str(text)
                for item in payload_tags.getall("TXXX")
                for text in item.text
            ]
            self.assertEqual(comments, [])
            self.assertNotIn("Purchased at Beatport", " ".join(user_text))

    def test_display_tags_are_normalized_in_wav_without_changing_pcm(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "untagged.wav"
            with wave.open(str(source), "wb") as output:
                output.setnchannels(2)
                output.setsampwidth(2)
                output.setframerate(44_100)
                output.writeframes(b"\0\0\0\0" * 100)
            source_hash = _hash(source)

            prepared = RockboxMediaCache(root / "cache").prepare(
                source, title="Visible WAV", artist="SoundBox")

            self.assertTrue(prepared.transformed)
            self.assertEqual(_hash(source), source_hash)
            self.assertEqual(
                _riff_info_values(prepared.payload),
                ("Visible WAV", "SoundBox"),
            )
            # Rockbox decodes RIFF INFO using the configured codepage (UTF-8 by
            # default); prove the payload is not merely carrying Mutagen ID3.
            self.assertIn(b"LIST", prepared.payload.read_bytes())
            self.assertIn(b"INAM", prepared.payload.read_bytes())

    def test_wav_display_tags_replace_all_competing_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "opaque-info.wav"
            with wave.open(str(source), "wb") as output:
                output.setnchannels(2)
                output.setsampwidth(2)
                output.setframerate(44_100)
                output.writeframes(b"\1\0\2\0" * 100)

            original = source.read_bytes()
            fmt_size = struct.unpack("<I", original[16:20])[0]
            fmt_end = 20 + fmt_size + (fmt_size & 1)
            opaque = b"NITR" + struct.pack("<I", 24_000) + b"x" * 24_000
            old_info_body = b"INFO" + opaque
            old_info = (
                b"LIST" + struct.pack("<I", len(old_info_body))
                + old_info_body
            )
            rebuilt = original[:fmt_end] + old_info + original[fmt_end:]
            rebuilt = rebuilt[:4] + struct.pack("<I", len(rebuilt) - 8) + rebuilt[8:]
            source.write_bytes(rebuilt)
            source_hash = _hash(source)

            prepared = RockboxMediaCache(root / "cache").prepare(
                source, title="Visible early", artist="Noize Complaint")

            payload = prepared.payload.read_bytes()
            self.assertEqual(_hash(source), source_hash)
            self.assertEqual(
                _riff_info_values(prepared.payload),
                ("Visible early", "Noize Complaint"),
            )
            self.assertLess(payload.index(b"INAM"), 256)
            self.assertNotIn(b"NITR", payload)
            self.assertNotIn(opaque, payload)
            with wave.open(str(source), "rb") as before, wave.open(
                    str(prepared.payload), "rb") as after:
                self.assertEqual(before.getparams(), after.getparams())
                self.assertEqual(before.readframes(100), after.readframes(100))

    def test_beatport_receipt_is_removed_only_from_rockbox_wav_copy(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "beatport.wav"
            with wave.open(str(source), "wb") as output:
                output.setnchannels(2)
                output.setsampwidth(2)
                output.setframerate(44_100)
                output.writeframes(b"\1\0\2\0" * 100)

            original = source.read_bytes()
            fmt_size = struct.unpack("<I", original[16:20])[0]
            fmt_end = 20 + fmt_size + (fmt_size & 1)

            def info_field(tag: bytes, value: bytes) -> bytes:
                data = value + b"\0"
                return (
                    tag + struct.pack("<I", len(data)) + data
                    + (b"\0" if len(data) & 1 else b"")
                )

            fields = [
                info_field(b"INAM", b"Wrong title"),
                info_field(b"IART", b"Wrong artist"),
                info_field(b"IPRD", b"Real album"),
                info_field(b"ICMT", b"Purchased at Beatport.com"),
            ]
            body = b"INFO" + b"".join(fields)
            old_info = b"LIST" + struct.pack("<I", len(body)) + body
            rebuilt = original[:fmt_end] + old_info + original[fmt_end:]
            rebuilt = rebuilt[:4] + struct.pack("<I", len(rebuilt) - 8) + rebuilt[8:]
            source.write_bytes(rebuilt)
            source_hash = _hash(source)

            prepared = RockboxMediaCache(root / "cache").prepare(
                source, title="Correct title", artist="Correct artist")

            payload = prepared.payload.read_bytes()
            self.assertEqual(_hash(source), source_hash)
            self.assertEqual(
                _riff_info_values(prepared.payload),
                ("Correct title", "Correct artist"),
            )
            self.assertNotIn(b"Real album", payload)
            self.assertNotIn(b"Purchased at Beatport", payload)

    def test_wav_embedded_id3_and_year_cannot_compete_with_soundbox_title(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "competing.wav"
            with wave.open(str(source), "wb") as output:
                output.setnchannels(2)
                output.setsampwidth(2)
                output.setframerate(44_100)
                output.writeframes(b"\1\0\2\0" * 100)

            original = source.read_bytes()
            fmt_size = struct.unpack("<I", original[16:20])[0]
            fmt_end = 20 + fmt_size + (fmt_size & 1)
            competing = ID3()
            competing.add(TIT2(encoding=3, text=["2023"]))
            competing.add(TPE1(encoding=3, text=["Wrong artist"]))
            id3_buffer = io.BytesIO()
            competing.save(id3_buffer)
            id3_payload = id3_buffer.getvalue()
            id3_chunk = (
                b"id3 " + struct.pack("<I", len(id3_payload)) + id3_payload
                + (b"\0" if len(id3_payload) & 1 else b"")
            )
            rebuilt = original[:fmt_end] + id3_chunk + original[fmt_end:]
            rebuilt = rebuilt[:4] + struct.pack("<I", len(rebuilt) - 8) + rebuilt[8:]
            source.write_bytes(rebuilt)
            source_hash = _hash(source)

            prepared = RockboxMediaCache(root / "cache").prepare(
                source, title="Actual title", artist="Actual artist")

            payload = prepared.payload.read_bytes()
            self.assertEqual(_hash(source), source_hash)
            self.assertEqual(
                _riff_info_values(prepared.payload),
                ("Actual title", "Actual artist"),
            )
            self.assertNotIn(b"2023", payload)
            self.assertNotIn(b"Wrong artist", payload)


class RockboxArtworkDerivative(unittest.TestCase):
    @staticmethod
    @contextmanager
    def _replace_after_source_read(source, replace):
        source = source.resolve()
        original_open = Path.open

        @contextmanager
        def open_with_replacement(path, *args, **kwargs):
            with original_open(path, *args, **kwargs) as handle:
                if path == source and (args[0] if args else kwargs.get('mode')) == 'rb':
                    reader = mock.Mock(wraps=handle)

                    def read(*read_args):
                        data = handle.read(*read_args)
                        if data:
                            replace()
                        return data

                    reader.read.side_effect = read
                    yield reader
                else:
                    yield handle

        with mock.patch.object(Path, 'open', open_with_replacement):
            yield

    def test_replaced_cover_hash_and_pixels_stay_paired_on_cold_and_warm_misses(self):
        from soundbox import artencode

        for warm_index in (False, True):
            for side in (160, 116, 32):
                with self.subTest(warm_index=warm_index, side=side):
                    with tempfile.TemporaryDirectory() as area:
                        root = Path(area).resolve()
                        source = root / 'curated.png'
                        images = []
                        for color in ((240, 20, 10), (10, 20, 240)):
                            buffer = io.BytesIO()
                            Image.new('RGB', (80, 80), color).save(buffer, 'PNG')
                            images.append(buffer.getvalue())
                        source.write_bytes(images[0])
                        cache = RockboxArtworkCache(root / 'cache')
                        if warm_index:
                            cache.prepare(source, side=64)
                            cache.flush_source_index()
                            cache = RockboxArtworkCache(root / 'cache')
                        replacements = []

                        def replace_once():
                            if not replacements:
                                newer = root / 'replacement.png'
                                newer.write_bytes(images[1])
                                newer.replace(source)
                                replacements.append(True)

                        with self._replace_after_source_read(source, replace_once):
                            prepared = cache.prepare(source, side=side)

                        self.assertEqual(replacements, [True])
                        manifest = json.loads(
                            (prepared.payload.parent / 'manifest.json').read_bytes())
                        self.assertEqual(manifest['source_sha256'],
                                         hashlib.sha256(images[1]).hexdigest())
                        self.assertEqual(prepared.payload.read_bytes(),
                                         artencode.to_rockbox_cover(images[1], side))
                        self.assertEqual(source.read_bytes(), images[1])
                        source.write_bytes(images[0])
                        restored = cache.prepare(source, side=side)
                        self.assertEqual(restored.payload.read_bytes(),
                                         artencode.to_rockbox_cover(images[0], side))

    def test_continuously_replaced_cover_refuses_without_publishing_cache(self):
        with tempfile.TemporaryDirectory() as area:
            root = Path(area).resolve()
            source = root / 'curated.png'
            buffer = io.BytesIO()
            Image.new('RGB', (80, 80), (240, 20, 10)).save(buffer, 'PNG')
            image = buffer.getvalue()
            source.write_bytes(image)
            cache = RockboxArtworkCache(root / 'cache')
            replacements = []

            def replace():
                newer = root / 'replacement.png'
                newer.write_bytes(image)
                newer.replace(source)
                replacements.append(True)

            with self._replace_after_source_read(source, replace):
                with self.assertRaisesRegex(RockboxMediaError, 'remained unstable'):
                    cache.prepare(source)

            self.assertEqual(len(replacements), 2)
            self.assertFalse(cache.root.exists())
            self.assertEqual(cache._source_index, {})
            self.assertEqual(source.read_bytes(), image)

    def test_cover_capture_cancels_before_encoding_or_cache_publication(self):
        from threading import Event
        from soundbox.operation_control import OperationCancelled, operation_scope

        with tempfile.TemporaryDirectory() as area:
            root = Path(area).resolve()
            source = root / 'curated.png'
            buffer = io.BytesIO()
            Image.new('RGB', (80, 80), (240, 20, 10)).save(buffer, 'PNG')
            image = buffer.getvalue()
            source.write_bytes(image)
            cache = RockboxArtworkCache(root / 'cache')
            stop = Event()

            with operation_scope(stop), \
                    self._replace_after_source_read(source, stop.set), \
                    mock.patch('soundbox.rockbox_media.artencode.to_rockbox_cover',
                               side_effect=AssertionError('cancelled cover encoded')):
                with self.assertRaises(OperationCancelled):
                    cache.prepare(source)

            self.assertFalse(cache.root.exists())
            self.assertEqual(cache._source_index, {})
            self.assertEqual(source.read_bytes(), image)

    def test_legacy_self_consistent_wrong_cover_cache_is_not_reused(self):
        from soundbox import artencode

        with tempfile.TemporaryDirectory() as area:
            root = Path(area).resolve()
            source = root / 'curated.png'
            images = []
            for color in ((240, 20, 10), (10, 20, 240)):
                buffer = io.BytesIO()
                Image.new('RGB', (80, 80), color).save(buffer, 'PNG')
                images.append(buffer.getvalue())
            source.write_bytes(images[0])
            source_hash = hashlib.sha256(images[0]).hexdigest()
            legacy_profile = 'rockbox-paletted8-bmp-universal-v6'
            key = hashlib.sha256(
                f'{legacy_profile}\0{160}\0{source_hash}'.encode('ascii')).hexdigest()
            entry = root / 'cache' / legacy_profile / key
            entry.mkdir(parents=True)
            wrong = artencode.to_rockbox_cover(images[1], 160)
            self.assertIsNotNone(wrong)
            (entry / 'cover.160x160.bmp').write_bytes(wrong)
            legacy_manifest = {
                'profile': legacy_profile, 'side': 160,
                'source_sha256': source_hash, 'size': len(wrong),
                'sha256': hashlib.sha256(wrong).hexdigest(),
            }
            (entry / 'manifest.json').write_text(json.dumps(legacy_manifest))
            stat = source.stat()
            index = {str(source): {
                'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns,
                'ctime_ns': stat.st_ctime_ns, 'source_sha256': source_hash,
            }}
            (entry.parent / 'source-index.json').write_text(json.dumps(index))
            before = {path: path.read_bytes() for path in entry.parent.rglob('*')
                      if path.is_file()}

            prepared = RockboxArtworkCache(root / 'cache').prepare(source)

            self.assertNotEqual(ART_PROFILE, legacy_profile)
            self.assertEqual(prepared.profile, ART_PROFILE)
            self.assertEqual(prepared.payload.read_bytes(),
                             artencode.to_rockbox_cover(images[0], 160))
            self.assertEqual({path: path.read_bytes() for path in before}, before)
            self.assertEqual(source.read_bytes(), images[0])

    def test_malformed_cover_index_and_manifest_repair_without_changing_curated_art(self):
        with tempfile.TemporaryDirectory() as area:
            root = Path(area)
            source = root / 'curated.jpg'
            Image.new('RGB', (320, 240), (24, 90, 180)).save(source, 'JPEG')
            source = source.resolve()
            original = source.read_bytes()
            cache = RockboxArtworkCache(root / 'cache')
            prepared = cache.prepare(source)
            payload = prepared.payload.read_bytes()
            cache.flush_source_index()
            index = cache._source_index_path
            baseline = json.loads(index.read_bytes())
            for row in ('bad', [1], dict(baseline[str(source)], size=float('inf'))):
                with self.subTest(index_row=row):
                    index.write_text(json.dumps({str(source): row}))
                    repaired = RockboxArtworkCache(root / 'cache').prepare(source)
                    self.assertEqual(repaired.payload.read_bytes(), payload)
                    self.assertEqual(source.read_bytes(), original)
            path = prepared.payload.parent / 'manifest.json'
            baseline = json.loads(path.read_bytes())
            for row in ([], dict(baseline, side=float('inf')),
                        dict(baseline, size=float('inf'))):
                with self.subTest(manifest=row):
                    path.write_text(json.dumps(row))
                    repaired = cache.prepare(source)
                    self.assertEqual(repaired.payload.read_bytes(), payload)
                    self.assertEqual(repaired.sha256, prepared.sha256)
                    self.assertEqual(source.read_bytes(), original)

    def test_unmodified_cover_fast_path_uses_persisted_source_index(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "canonical.jpg"
            buffer = io.BytesIO()
            Image.new("RGB", (160, 160), (20, 40, 60)).save(
                buffer, "JPEG", progressive=False)
            source.write_bytes(buffer.getvalue())
            cache = RockboxArtworkCache(root / "cache")
            first = cache.prepare(source)
            cache.flush_source_index()

            with self._replace_after_source_read(
                    source, lambda: self.fail('unchanged cover was reread')), mock.patch(
                    "soundbox.rockbox_media._sha256",
                    side_effect=AssertionError("unchanged cover was rehashed")):
                reused = RockboxArtworkCache(root / "cache").prepare(
                    source, verify_cache=False)

            self.assertEqual(reused, first)

    def test_unverified_cache_still_repairs_missing_or_invalid_payload_digest(self):
        with tempfile.TemporaryDirectory() as area:
            root = Path(area).resolve()
            source = root / 'curated.png'
            buffer = io.BytesIO()
            Image.new('RGB', (80, 80), (24, 90, 180)).save(buffer, 'PNG')
            original = buffer.getvalue()
            source.write_bytes(original)
            cache = RockboxArtworkCache(root / 'cache')
            prepared = cache.prepare(source)
            payload = prepared.payload.read_bytes()
            path = prepared.payload.parent / 'manifest.json'
            baseline = json.loads(path.read_bytes())
            cases = [{key: value for key, value in baseline.items() if key != 'sha256'}]
            cases += [dict(baseline, sha256=value)
                      for value in (None, 0, '', 'x' * 64, 'A' * 64, 'f' * 63, 'f' * 65)]
            for manifest in cases:
                with self.subTest(digest=manifest.get('sha256', 'MISSING')):
                    path.write_text(json.dumps(manifest))
                    with mock.patch('soundbox.rockbox_media._sha256', side_effect=
                                    AssertionError('unverified payload was rehashed')):
                        repaired = cache.prepare(source, verify_cache=False)
                    self.assertEqual(repaired, prepared)
                    self.assertEqual(repaired.payload.read_bytes(), payload)
                    self.assertEqual(json.loads(path.read_bytes()), baseline)
                    self.assertEqual(source.read_bytes(), original)

    def test_cover_is_exact_paletted_bmp_without_rewriting_canonical(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "canonical.jpg"
            image = Image.new("L", (100, 100), 128)
            buffer = io.BytesIO()
            image.save(buffer, "JPEG", progressive=False)
            source.write_bytes(buffer.getvalue())
            source_hash = _hash(source)

            prepared = RockboxArtworkCache(root / "cache").prepare(source)

            self.assertEqual(_hash(source), source_hash)
            with Image.open(prepared.payload) as output:
                self.assertEqual(output.format, "BMP")
                self.assertEqual(output.size, (160, 160))
                self.assertEqual(output.mode, "P")
            self.assertLess(prepared.size, 30_000)
            self.assertEqual(
                RockboxArtworkCache(root / "cache").prepare(source), prepared)
            mini = RockboxArtworkCache(root / "cache").prepare(source, side=116)
            with Image.open(mini.payload) as output:
                self.assertEqual(output.format, "BMP")
                self.assertEqual(output.size, (116, 116))
                self.assertEqual(output.mode, "P")
            self.assertLess(mini.size, 18_000)
            self.assertNotEqual(mini.payload, prepared.payload)
            custom = RockboxArtworkCache(root / "cache").prepare(
                source, side=200)
            with Image.open(custom.payload) as output:
                self.assertEqual(output.format, "BMP")
                self.assertEqual(output.size, (200, 200))
                self.assertEqual(output.mode, "P")
            self.assertLess(custom.size, 42_000)
            self.assertNotEqual(custom.payload, prepared.payload)

    def test_exact_jpeg_still_becomes_separate_rockbox_bmp(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "canonical.jpg"
            buffer = io.BytesIO()
            Image.new("RGB", (160, 160), (20, 40, 60)).save(
                buffer, "JPEG", progressive=False)
            source.write_bytes(buffer.getvalue())
            source_hash = _hash(source)
            prepared = RockboxArtworkCache(root / "cache").prepare(source)
            self.assertNotEqual(prepared.payload, source.resolve())
            self.assertEqual(_hash(source), source_hash)
            with Image.open(prepared.payload) as output:
                self.assertEqual(output.format, "BMP")
                self.assertEqual(output.size, (160, 160))


if __name__ == "__main__":
    unittest.main()
