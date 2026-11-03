"""Copy-only CBR proposals bind captured frames and preserve unrelated native data."""
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
import hashlib
from pathlib import Path
import struct
import tempfile
import unittest
from uuid import uuid4

from research.native_library import mp3_analysis_plan as compatibility
from soundbox import native_mp3_analysis_plan as planner, mp3_scan as mp3_seek
from soundbox.analysis import ENGINE
from soundbox.native_media import MediaIdentity
from soundbox.rekordbox_grid import AnalysisImage
from soundbox.rekordbox_write import ConflictError, WriteProofError
import test_fixture_analysis as fixtures


class CBRComponentProposals(unittest.TestCase):
    def setUp(self):
        self.area = tempfile.TemporaryDirectory(); self.addCleanup(self.area.cleanup)
        self.path = Path(self.area.name).resolve() / 'Synthetic CBR.mp3'
        self.raw = (bytes.fromhex('fffb9000') + bytes(413)) * 1226
        self.path.write_bytes(self.raw)
        self.scan = mp3_seek.scan_mp3(self.path)
        duration = Fraction(self.scan.sample_count, self.scan.sample_rate)
        self.audio = MediaIdentity(str(self.path), self.scan.source_epoch, self.scan.sha256,
            int(duration), 'mp3', 'mp3', duration.numerator, duration.denominator)
        fixture = fixtures.InitialFixtureAnalysis(); fixture.setUp()
        self.base = deepcopy(fixture.base)
        self.base['track'].update(FolderPath=str(self.path), FileSize=len(self.raw),
            rb_file_id=str(self.path.stat().st_ino), FileType=1, Length=int(duration))
        self.seeds = {key: fixture.seeds[key] for key in ('track_stamp', 'file_uuids', 'file_stamps')}
        self.result = dict(engine=ENGINE, path=str(self.path), epoch=list(self.audio.epoch),
            size=len(self.raw), sha256=self.audio.sha256, duration=float(duration), bpm=120.0, key='8B',
            beats=[[i % 4 + 1, 274 + i * 500, 120.0] for i in range(64)], first_cue=None)
        self.technical = dict(sample_rate=44100, channels=2, bits=16, bitrate=128)
        self.images = {}
        for ext, raw in fixtures.packet(self.path.name).items():
            image = AnalysisImage.parse(raw); sections = []
            for section in image.sections:
                body = section.raw
                if section.kind == b'PVBR':
                    body = mp3_seek.cbr_pvbr(self.scan)
                elif section.kind == b'PQTZ':
                    body = body[:24] + b''.join(struct.pack('>HHI', n, round(bpm * 100), ms)
                                               for n, ms, bpm in self.result['beats'])
                elif section.kind in (b'PWV3', b'PWV5', b'PWV7'):
                    width = {b'PWV3': 1, b'PWV5': 2, b'PWV7': 3}[section.kind]
                    columns = (self.scan.sample_count + 293) // 294
                    body = body[:16] + struct.pack('>I', columns) + body[20:24] + bytes(width * columns)
                body = body[:8] + struct.pack('>I', len(body)) + body[12:]
                sections.append(body)
            payload = b''.join(sections)
            self.images[ext] = image.header[:8] + struct.pack('>I', 28 + len(payload)) + image.header[12:] + payload

    def plan(self, **kwargs):
        args = dict(base=self.base, audio=self.audio, images=self.images, scan=self.scan,
            share_root='/explicit/native/share', seeds=self.seeds, result=self.result, technical=self.technical)
        args.update(kwargs)
        return planner.plan(**args)

    def test_exact_component_metadata_counter_files_and_opaque_preservation(self):
        original = deepcopy((self.base, self.result, self.images, self.seeds))
        proposal = self.plan(); changes = proposal['changes']; row = changes[0]['after']
        self.assertEqual([c['table'] for c in changes],
                         ['djmdContent', 'contentFile', 'contentFile', 'contentFile', 'agentRegistry'])
        self.assertEqual((row['BitRate'], row['BitDepth'], row['SampleRate'], row['Length']), (128, 16, 44100, 32))
        self.assertEqual((row['BPM'], row['KeyID'], row['rb_local_usn']), (12000, '789', 58))
        allowed = {'BPM', 'KeyID', 'AnalysisDataPath', 'BitRate', 'BitDepth', 'SampleRate', 'ContentLink',
                   'Analysed', 'AnalysisUpdated', 'TrackInfoUpdated', 'rb_local_usn', 'updated_at'}
        self.assertEqual({key for key in row if row[key] != self.base['track'][key]}, allowed)
        for change, ext, count in zip(changes[1:4], planner.FILE_ORDER, (59, 60, 61), strict=True):
            native = change['after']
            self.assertEqual(native['rb_local_usn'], count)
            self.assertEqual(native['Hash'], hashlib.md5(self.images[ext]).hexdigest())
            self.assertEqual(native['Size'], len(self.images[ext]))
            self.assertTrue(native['rb_local_path'].endswith('ANLZ0000' + ext))
        self.assertEqual(changes[-1]['after'], {**self.base['counter'], 'int_1': 61})
        self.assertFalse(proposal['source_write_authority']); self.assertFalse(proposal['native_acceptance'])
        self.assertFalse(proposal['component_policy_accepted'])
        self.assertEqual((self.base, self.result, self.images, self.seeds), original)
        self.assertEqual(self.path.read_bytes(), self.raw)

    def test_retained_research_plans_reconstruct_with_identical_wire_and_api(self):
        for name, value in vars(planner).items():
            if not name.startswith('__'):
                with self.subTest(name=name):
                    self.assertIs(getattr(compatibility, name), value)
        proposal = self.plan()
        replay = compatibility.plan(base=self.base, audio=self.audio, images=self.images, scan=self.scan,
            share_root='/explicit/native/share', seeds=self.seeds, result=self.result, technical=self.technical)
        self.assertEqual(replay, proposal)
        self.assertEqual(proposal['profile'], 'research-initial-mpeg1-cbr-no-info-stereo-44100-v1')
        for field in ('source_write_authority', 'native_acceptance', 'component_policy_accepted'):
            self.assertIs(proposal[field], False)

    def test_rounded_headers_different_captured_bytes_epoch_or_codec_never_become_scan_clock(self):
        for field, value in (('sha256', '0' * 64), ('duration', 32.0), ('path', '/different/source.mp3'),
                             ('epoch', [*self.audio.epoch[:-1], self.audio.epoch[-1] + 1]), ('first_cue', 274)):
            result = deepcopy(self.result); result[field] = value
            with self.subTest(field=field), self.assertRaises((ValueError, WriteProofError)):
                self.plan(result=result)
        for scan in (replace(self.scan, sha256='0' * 64), replace(self.scan, info_kind='Xing'),
                     replace(self.scan, sample_rate=48000), replace(self.scan, channels=1),
                     replace(self.scan, bitrates=tuple([128] * 1225 + [160]))):
            with self.subTest(scan=scan.info_kind), self.assertRaises(WriteProofError):
                self.plan(scan=scan)
        with self.assertRaises(WriteProofError):
            self.plan(audio=replace(self.audio, duration_numerator=32, duration_denominator=1))
        for technical in ({**self.technical, 'bits': 0}, {**self.technical, 'bitrate': 320},
                          {**self.technical, 'channels': True}, {**self.technical, 'extra': 1}):
            with self.assertRaises(WriteProofError): self.plan(technical=technical)

    def test_existing_native_analysis_lock_sync_cues_gain_and_stale_identity_are_rejected(self):
        for field, value in (('BPM', 12000), ('KeyID', '789'), ('FileType', 11), ('Length', 31),
                             ('FileSize', 1), ('rb_file_id', 'different'), ('AnalysisUpdated', '1'),
                             ('DisableQuantize', 1), ('rb_local_synced', 1), ('usn', 1), ('BitRate', 128)):
            base = deepcopy(self.base); base['track'][field] = value
            with self.subTest(field=field), self.assertRaises(ConflictError): self.plan(base=base)
        for field in ('files', 'mixers', 'cues', 'aggregates'):
            base = deepcopy(self.base); base[field] = [dict(ContentID='123')]
            with self.subTest(field=field), self.assertRaises(ConflictError): self.plan(base=base)

    def test_wrong_packet_path_seek_detail_embedded_cues_secondary_grid_or_opaque_header_fail(self):
        for ext, kind, offset in (('.DAT', b'PVBR', 16), ('.DAT', b'PPTH', 16), ('.DAT', b'PQTZ', 12),
                                  ('.EXT', b'PWV3', 16), ('.EXT', b'PCOB', 16), ('.EXT', b'PCOB', 4),
                                  ('.EXT', b'PQT2', 24), ('.2EX', b'PWVC', 14)):
            images = dict(self.images); body = bytearray(images[ext]); body[body.index(kind) + offset] ^= 1
            images[ext] = bytes(body)
            with self.subTest(kind=kind), self.assertRaises(WriteProofError): self.plan(images=images)
        images = dict(self.images); images['.DAT'] = images['.DAT'][:-1]
        with self.assertRaises(WriteProofError): self.plan(images=images)
        result = deepcopy(self.result); result['beats'][-1][1] += 1
        with self.assertRaises(WriteProofError): self.plan(result=result)
        for share in ('relative/share', '/explicit/../share', '/Volumes/device/share'):
            with self.assertRaises(WriteProofError): self.plan(share_root=share)

    def test_counter_uuid_key_history_and_backwards_clocks_remain_explicit(self):
        for clock in (True, -1, 2**63 - 20):
            base = deepcopy(self.base); base['counter']['int_1'] = clock
            with self.assertRaises(WriteProofError): self.plan(base=base)
        seeds = deepcopy(self.seeds); seeds['file_uuids']['.DAT'] = seeds['file_uuids']['.EXT']
        with self.assertRaises(ConflictError): self.plan(seeds=seeds)
        seeds = deepcopy(self.seeds); seeds['track_stamp'] = '2026-10-29 00:00:00.000 +00:00'
        with self.assertRaises(WriteProofError): self.plan(seeds=seeds)
        base = deepcopy(self.base); base['keys'] *= 2
        with self.assertRaises(WriteProofError): self.plan(base=base)
        base = deepcopy(self.base)
        base['files'] = [dict(ID='other', ContentID='other', UUID=self.seeds['file_uuids']['.DAT'])]
        with self.assertRaises(ConflictError): self.plan(base=base)
        base['files'][0]['UUID'] = str(uuid4())
        self.assertEqual(len(self.plan(base=base)['changes']), 5)
