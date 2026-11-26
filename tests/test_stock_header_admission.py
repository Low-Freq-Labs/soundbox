"""Unsupported iTunes WAVs need bounded rejection, never payload admission."""

import os
from pathlib import Path
import struct
import tempfile
from threading import Event
import unittest
from unittest import mock
import wave

from soundbox import stock_ipod
from soundbox.operation_control import OperationCancelled, operation_scope
from soundbox.stock_ipod_info import read_stock_ipod_snapshot
from soundbox.stock_ipod_shadow import StockIpodShadowStore
from tests import test_export_playlist_baseline as baseline
from tests import test_stock_ipod as fixture


def write_wave(path, *, width=3, rate=44_100, channels=2):
    with wave.open(str(path), 'wb') as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(width)
        handle.setframerate(rate)
        handle.writeframes(bytes(width * channels * 441))


def write_waveex(path, *, width=3, format_tag=1, size=None):
    channels, rate = 2, 44_100
    align = width * channels
    fields = (struct.pack('<HHIIHHHHII', 0xFFFE, channels, rate, rate * align,
                          align, width * 8, 22, width * 8, 3, format_tag)
              + bytes.fromhex('00001000800000aa00389b71'))
    data_size = align * 441 if size is None else size - 68
    with path.open('wb') as handle:
        handle.write(b'RIFF' + struct.pack('<I', data_size + 60) + b'WAVEfmt '
                     + struct.pack('<I', len(fields)) + fields
                     + b'data' + struct.pack('<I', data_size))
        handle.truncate(68 + data_size)


class StockHeaderRejection(unittest.TestCase):
    def setUp(self):
        area = tempfile.TemporaryDirectory()
        self.addCleanup(area.cleanup)
        self.path = Path(area.name).resolve() / 'source.wav'

    def test_definite_pcm_and_float_rejections_match_existing_policy(self):
        for width, rate, channels in ((1, 44_100, 2), (3, 44_100, 2), (4, 48_000, 2),
                                      (2, 48_000, 2), (2, 44_100, 1)):
            with self.subTest(width=width, rate=rate, channels=channels):
                write_wave(self.path, width=width, rate=rate, channels=channels)
                self.assertEqual(stock_ipod._stock_wav_header_omission(self.path),
                                 stock_ipod._stock_media_omission(self.path))
        import soundfile as sf
        import numpy as np
        for subtype in ('FLOAT', 'DOUBLE'):
            with self.subTest(subtype=subtype):
                sf.write(self.path, np.zeros((441, 2)), 44_100, subtype=subtype)
                self.assertEqual(stock_ipod._stock_wav_header_omission(self.path),
                                 stock_ipod._stock_media_omission(self.path))

    def test_large_sparse_source_never_reads_the_audio_payload(self):
        write_wave(self.path)
        size = 512 * 1024 * 1024
        with self.path.open('r+b') as handle:
            handle.seek(4); handle.write(struct.pack('<I', size - 8))
            handle.seek(40); handle.write(struct.pack('<I', size - 44))
            handle.truncate(size)
        reads = []
        real_fdopen = os.fdopen

        class ObservedReader:
            def __init__(self, handle):
                self.handle = handle

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return self.handle.__exit__(*args)

            def __getattr__(self, key):
                return getattr(self.handle, key)

            def read(self, count):
                reads.append((self.handle.tell(), count))
                return self.handle.read(count)

        with mock.patch.object(stock_ipod.os, 'fdopen',
                               side_effect=lambda *a, **kw: ObservedReader(real_fdopen(*a, **kw))):
            reason = stock_ipod._stock_wav_header_omission(self.path)
        self.assertIn('PCM_24', reason)
        self.assertLessEqual(sum(count for _, count in reads), 128)
        self.assertTrue(all(offset + count <= 44 for offset, count in reads))

    def test_compatible_unknown_and_malformed_headers_never_grant_admission(self):
        write_wave(self.path, width=2)
        compatible = self.path.read_bytes()
        self.assertIsNone(stock_ipod._stock_wav_header_omission(self.path))
        for label, payload in (
                ('RF64', b'RF64' + compatible[4:]),
                ('unknown codec', compatible[:20] + struct.pack('<H', 65534) + compatible[22:]),
                ('truncated', compatible[:30]),
                ('invalid rate', compatible[:28] + bytes(4) + compatible[32:]),
                ('missing', b'')):
            with self.subTest(label=label):
                self.path.write_bytes(payload)
                self.assertIsNone(stock_ipod._stock_wav_header_omission(self.path))

    def test_extensible_pcm_and_float_only_prove_existing_rejection(self):
        for width, tag in ((1, 1), (2, 1), (3, 1), (4, 1), (4, 3), (8, 3)):
            with self.subTest(width=width, tag=tag):
                write_waveex(self.path, width=width, format_tag=tag)
                expected = stock_ipod._stock_media_omission(self.path)
                self.assertTrue(expected)
                self.assertEqual(stock_ipod._stock_wav_header_omission(self.path), expected)

    def test_extensible_unknown_and_invalid_framing_fall_back(self):
        write_waveex(self.path)
        original = self.path.read_bytes()
        for offset, value in (
                (16, struct.pack('<I', 38)),
                (36, struct.pack('<H', 20)),
                (38, struct.pack('<H', 0)),
                (38, struct.pack('<H', 25)),
                (40, struct.pack('<I', 1)),
                (44, struct.pack('<I', 99)),
                (48, b'\x01'),
                (28, struct.pack('<I', 0)),
                (32, struct.pack('<H', 1))):
            with self.subTest(offset=offset, value=value):
                changed = bytearray(original)
                changed[offset:offset + len(value)] = value
                self.path.write_bytes(changed)
                self.assertIsNone(stock_ipod._stock_wav_header_omission(self.path))
        write_waveex(self.path, width=4, format_tag=3)
        changed = bytearray(self.path.read_bytes())
        struct.pack_into('<H', changed, 38, 24)
        self.path.write_bytes(changed)
        self.assertIsNone(stock_ipod._stock_wav_header_omission(self.path))

    def test_large_extensible_header_never_reads_audio(self):
        write_waveex(self.path, size=512 * 1024 * 1024)
        reads = []
        fdopen = os.fdopen
        class ObservedReader:
            def __init__(self, handle):self.handle = handle
            def __enter__(self):return self
            def __exit__(self, *args):return self.handle.__exit__(*args)
            def __getattr__(self, name):return getattr(self.handle, name)
            def read(self, count):
                reads.append((self.handle.tell(), count))
                return self.handle.read(count)
        with mock.patch.object(stock_ipod.os, 'fdopen',
                side_effect=lambda *a, **kw: ObservedReader(fdopen(*a, **kw))):
            self.assertIn('PCM_24', stock_ipod._stock_wav_header_omission(self.path))
        self.assertLessEqual(sum(count for _, count in reads), 60)
        self.assertTrue(all(position + count <= 60 for position, count in reads))

    def test_extra_chunks_are_seeked_without_payload_reads_and_have_a_fixed_budget(self):
        write_wave(self.path)
        original = self.path.read_bytes()
        for count, rejects in ((3, True), (128, False)):
            chunks = (b'JUNK' + struct.pack('<I', 4096) + bytes(4096)) * count
            payload = original[:12] + chunks + original[12:]
            self.path.write_bytes(payload[:4] + struct.pack('<I', len(payload) - 8) + payload[8:])
            self.assertEqual(stock_ipod._stock_wav_header_omission(self.path) is not None, rejects)

    def test_changed_identity_and_symlink_fall_back_without_blessing_the_header(self):
        write_wave(self.path)
        real_lstat = Path.lstat
        calls = 0

        def lstat(path):
            nonlocal calls
            if path == self.path:
                calls += 1
                if calls == 2:
                    write_wave(self.path, width=2)
            return real_lstat(path)

        with mock.patch.object(Path, 'lstat', lstat):
            self.assertIsNone(stock_ipod._stock_wav_header_omission(self.path))
        alias = self.path.with_name('alias.wav'); alias.symlink_to(self.path)
        self.assertIsNone(stock_ipod._stock_wav_header_omission(alias))

    def test_named_file_replacement_during_read_cannot_reject_the_new_supported_source(self):
        write_wave(self.path)
        original = self.path.read_bytes(); old = self.path.stat()
        supported = self.path.with_name('replacement.wav'); write_wave(supported, width=2)
        # Match size and timestamps as well as possible. The held-descriptor
        # versus final named-entity check must still notice the different file.
        with supported.open('ab') as handle:
            handle.truncate(old.st_size)
        os.utime(supported, ns=(old.st_atime_ns, old.st_mtime_ns))
        replacement = supported.read_bytes()
        displaced = self.path.with_name('displaced.wav')
        selected_path = self.path
        fdopen = os.fdopen
        class ReplacedReader:
            def __init__(self, handle):
                self.handle = handle
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return self.handle.__exit__(*args)
            def __getattr__(self, key):
                return getattr(self.handle, key)
            def read(self, count):
                result = self.handle.read(count)
                if self.handle.tell() == 36:
                    os.replace(selected_path, displaced)
                    os.replace(supported, selected_path)
                return result
        # Keep the actual open file and OS replacement; only attach the exact
        # generated race to the read callback.
        with mock.patch.object(stock_ipod.os, 'fdopen',
                               side_effect=lambda *a, **kw: ReplacedReader(fdopen(*a, **kw))):
            self.assertIsNone(stock_ipod._stock_wav_header_omission(self.path))
        self.assertEqual(displaced.read_bytes(), original)
        self.assertEqual(self.path.read_bytes(), replacement)
        self.assertIsNone(stock_ipod._stock_wav_header_omission(self.path))

    def test_cancel_is_preserved_before_open_and_during_chunk_walk(self):
        write_wave(self.path)
        event = Event(); event.set()
        with operation_scope(event), mock.patch.object(stock_ipod.os, 'open') as opening:
            with self.assertRaises(OperationCancelled):
                stock_ipod._stock_wav_header_omission(self.path)
        opening.assert_not_called()
        real_checkpoint = stock_ipod.checkpoint
        calls = 0

        def check():
            nonlocal calls
            calls += 1
            if calls == 2:
                event.set()
            real_checkpoint()

        event.clear()
        with operation_scope(event), mock.patch.object(stock_ipod, 'checkpoint', check):
            with self.assertRaises(OperationCancelled):
                stock_ipod._stock_wav_header_omission(self.path)

    def test_complete_rf64_definite_rejections_match_full_policy_but_invalid_ds64_falls_back(self):
        import soundfile as sf
        for subtype in ('PCM_16','PCM_24','FLOAT','DOUBLE'):
            with self.subTest(subtype=subtype):
                sf.write(self.path,[[0.,0.]]*32,44100,format='RF64',subtype=subtype)
                raw=self.path.read_bytes()
                self.assertEqual(raw[:4],b'RF64')
                self.assertEqual(stock_ipod._stock_wav_header_omission(self.path),
                                 stock_ipod._stock_media_omission(self.path))
                for offset,value in ((4,bytes(4)),(12,b'JUNK'),(16,struct.pack('<I',27)),
                                     (20,struct.pack('<Q',len(raw)*2)),(28,struct.pack('<Q',len(raw)*2)),
                                     (36,struct.pack('<Q',99)),(44,struct.pack('<I',1))):
                    with self.subTest(offset=offset):
                        changed=bytearray(raw);changed[offset:offset+len(value)]=value
                        self.path.write_bytes(changed)
                        self.assertIsNone(stock_ipod._stock_wav_header_omission(self.path))

    def test_sparse_large_rf64_rejection_reads_only_bounded_metadata(self):
        import soundfile as sf
        sf.write(self.path,[[0.,0.]]*32,44100,format='RF64',subtype='FLOAT')
        raw=self.path.read_bytes();start=raw.index(b'data')+8
        data_size=(512*1024*1024)//8*8;size=start+data_size
        with self.path.open('r+b') as handle:
            handle.seek(20);handle.write(struct.pack('<QQQ',size-8,data_size,data_size//8));handle.truncate(size)
        reads=[];fdopen=os.fdopen
        class Observed:
            def __init__(self,handle):self.handle=handle
            def __enter__(self):return self
            def __exit__(self,*args):return self.handle.__exit__(*args)
            def __getattr__(self,name):return getattr(self.handle,name)
            def read(self,count):
                reads.append((self.handle.tell(),count));return self.handle.read(count)
        with mock.patch.object(stock_ipod.os,'fdopen',side_effect=lambda *a,**kw:Observed(fdopen(*a,**kw))):
            reason=stock_ipod._stock_wav_header_omission(self.path)
        self.assertIn('FLOAT',reason)
        self.assertLessEqual(sum(count for _,count in reads),128)
        self.assertTrue(all(position+count<=start for position,count in reads))

    def test_provider_readiness_retries_only_header_and_allows_blocking_hydration(self):
        write_wave(self.path);opening=os.open;flags=[]
        def open_source(path,mode,*args,**kwargs):
            if path==self.path:
                flags.append(mode)
                if mode & os.O_NONBLOCK:raise OSError(81,'Generated provider not ready')
            return opening(path,mode,*args,**kwargs)
        with (mock.patch.object(stock_ipod.os,'open',side_effect=open_source),
              mock.patch('soundbox.source_read_retry.is_cloud_storage_source',return_value=True),
              mock.patch('soundbox.source_read_retry._wait') as waiting):
            self.assertIn('PCM_24',stock_ipod._stock_wav_header_omission(self.path))
        self.assertEqual(waiting.call_count,1);self.assertEqual(len(flags),2)
        self.assertTrue(flags[0]&os.O_NONBLOCK);self.assertFalse(flags[1]&os.O_NONBLOCK)

    def test_provider_wait_cancellation_propagates_without_remembering_rejection(self):
        write_wave(self.path)
        with (mock.patch.object(stock_ipod.os,'open',side_effect=OSError(81,'Generated provider not ready')),
              mock.patch('soundbox.source_read_retry.is_cloud_storage_source',return_value=True),
              mock.patch('soundbox.source_read_retry._wait',side_effect=OperationCancelled('Generated cancel'))):
            with self.assertRaises(OperationCancelled):stock_ipod._stock_wav_header_omission(self.path)
        self.assertNotIn(self.path,stock_ipod._WAV_OMISSIONS.rows)

    def test_single_ctime_only_hydration_retries_header_and_continued_churn_is_unknown(self):
        for repeated in (False,True):
            with self.subTest(repeated=repeated):
                write_wave(self.path);mode=self.path.stat().st_mode;fdopen=os.fdopen;changed=[]
                selected=self.path
                class Hydrates:
                    def __init__(self,handle):self.handle=handle
                    def __enter__(self):return self
                    def __exit__(self,*args):return self.handle.__exit__(*args)
                    def __getattr__(self,name):return getattr(self.handle,name)
                    def read(self,count):
                        result=self.handle.read(count)
                        if self.handle.tell()==36 and (repeated or not changed):
                            changed.append(True);selected.chmod(mode^0o100);selected.chmod(mode)
                        return result
                with mock.patch.object(stock_ipod.os,'fdopen',
                        side_effect=lambda *a,**kw:Hydrates(fdopen(*a,**kw))) as opened:
                    result=stock_ipod._stock_wav_header_omission(self.path)
                self.assertEqual(opened.call_count,2)
                self.assertEqual(result is None,repeated)


class StockPlannerHeaderRejection(unittest.TestCase):
    def setUp(self):
        self.f = fixture.StockIpodSync(methodName='runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.key = read_stock_ipod_snapshot(self.f.ipod, volume_uuid='header-test').identity_key
        self.shadow = StockIpodShadowStore(self.f.base / 'shadow')

    def plan(self, **options):
        f = self.f
        return stock_ipod.plan_stock_ipod_sync(f.library, [f.playlist], f.ipod,
            model_number='MA446', filesystem='msdos', usb_family='iPod Video',
            identity_key=self.key, shadow_store=self.shadow, **options)

    def test_restart_reuses_unchanged_rejections_through_real_catalog_noop(self):
        from soundbox.stock_plan_observations import NegativeSourceOmissions,STOCK_SOURCE_PROFILE
        from soundbox.stock_sync_reuse import stock_selection_reusable
        f=self.f;write_wave(f.new_source);cache=(f.base/'negative-observations').resolve()
        f.library.source=f.sources/'synthetic.xml';f.library.source.write_text('<DJ_PLAYLISTS/>')
        for track in f.library.tracks_by_id.values():track.location_uri=track.path.resolve().as_uri()
        display={key:dict(id=key,title=track.name,artist=track.artist)
                 for key,track in f.library.tracks_by_id.items()}
        first=self.plan(omission_cache_root=cache,display_rows=display)
        result=stock_ipod.commit_stock_ipod_sync(first)
        snapshot=read_stock_ipod_snapshot(f.ipod,volume_uuid='header-test')
        witness=result.catalog_witness
        self.assertIsNotNone(witness,result.catalog_notice)
        self.assertTrue(witness.bindings[0].has_omissions)
        before=f._snapshot(f.ipod)
        cache_after_restart=NegativeSourceOmissions(root=cache,profile=STOCK_SOURCE_PROFILE)
        with (mock.patch.object(stock_ipod,'acquire_source',side_effect=AssertionError('Unchanged audio acquired')),
              mock.patch.object(stock_ipod,'_stock_wav_header_omission',side_effect=AssertionError('Unchanged format reprobed'))):
            self.assertTrue(stock_selection_reusable(f.library,[f.playlist],snapshot,witness,
                self.shadow.load(self.key),cache_after_restart))
            plan=stock_ipod.plan_stock_ipod_catalog_update(f.library,[f.playlist],f.ipod,
                snapshot=snapshot,model_number='MA446',filesystem='msdos',usb_family='iPod Video',
                incremental=True,update_metadata=True,catalog_witness=witness,omission_cache_root=cache,
                display_rows=display)
        self.assertFalse(plan.requires_commit)
        self.assertEqual(plan.copies,());self.assertEqual(plan.rewrites,())
        stock_ipod.verify_stock_ipod_catalog_noop(plan)
        self.assertEqual(f._snapshot(f.ipod),before)

    def test_actual_export_and_repeat_sync_do_not_acquire_omitted_audio(self):
        f = self.f; write_wave(f.new_source)
        original = f.new_source.read_bytes()
        f.playlist.keys[:] = ['old']
        stock_ipod.commit_stock_ipod_sync(self.plan())
        f.playlist.keys.append('new')
        with mock.patch.object(stock_ipod, 'acquire_source',
                               side_effect=AssertionError('Omitted or retained audio was acquired')):
            first = self.plan()
        self.assertEqual([row.track_id for row in first.omissions], ['new'])
        self.assertEqual(first.copies, ())
        stock_ipod.commit_stock_ipod_sync(first)
        with mock.patch.object(stock_ipod, 'acquire_source',
                               side_effect=AssertionError('Repeat Sync reacquired audio')):
            second = self.plan()
        self.assertFalse(second.requires_commit)
        self.assertEqual(second.copies, ())
        self.assertEqual(f.new_source.read_bytes(), original)

    def test_same_path_pcm24_to_supported_pcm16_gets_full_fresh_acquisition(self):
        f = self.f; f.playlist.keys[:] = ['new']; write_wave(f.new_source)
        with mock.patch.object(stock_ipod, 'acquire_source',
                               side_effect=AssertionError('Unsupported PCM24 was acquired')):
            omitted = self.plan()
        self.assertEqual(len(omitted.omissions), 1)
        write_wave(f.new_source, width=2)
        original = f.new_source.read_bytes()
        with mock.patch.object(stock_ipod, 'acquire_source', wraps=stock_ipod.acquire_source) as capture:
            admitted = self.plan()
        self.assertGreater(capture.call_count, 0)
        self.assertEqual(admitted.omissions, ())
        self.assertEqual(len(admitted.copies), 1)
        stock_ipod.commit_stock_ipod_sync(admitted)
        self.assertEqual(f.new_source.read_bytes(), original)

    def test_unknown_header_uses_existing_full_acquisition_path(self):
        f = self.f; f.playlist.keys[:] = ['new']
        with mock.patch.object(stock_ipod, '_stock_wav_header_omission', return_value=None), \
                mock.patch.object(stock_ipod, 'acquire_source', wraps=stock_ipod.acquire_source) as capture:
            plan = self.plan()
        self.assertGreater(capture.call_count, 0)
        self.assertEqual(len(plan.copies), 1)
        self.assertEqual(plan.omissions, ())

    def test_exhausted_header_provider_retries_keep_full_acquisition_for_supported_source(self):
        f=self.f;f.playlist.keys[:]=['new'];source=f.new_source.resolve();opening=os.open
        def header_only_failure(path,*args,**kwargs):
            if path==source:raise OSError(81,'Generated nonblocking provider failure')
            return opening(path,*args,**kwargs)
        with (mock.patch.object(stock_ipod.os,'open',side_effect=header_only_failure),
              mock.patch('soundbox.source_read_retry.is_cloud_storage_source',return_value=True),
              mock.patch('soundbox.source_read_retry._wait') as waiting,
              mock.patch.object(stock_ipod,'acquire_source',wraps=stock_ipod.acquire_source) as acquired):
            plan=self.plan()
        self.assertEqual(waiting.call_count,2);self.assertEqual(acquired.call_count,1)
        self.assertEqual(plan.omissions,());self.assertEqual(len(plan.copies),1)

    def test_actual_extensible_pcm16_keeps_existing_policy_without_acquisition(self):
        import numpy as np
        import soundfile as sf
        f = self.f; f.playlist.keys[:] = ['new']
        sf.write(f.new_source, np.zeros((441, 2)), 44_100, format='WAVEX', subtype='PCM_16')
        original = f.new_source.read_bytes()
        self.assertEqual(struct.unpack_from('<H', original, 20)[0], 65534)
        reason = stock_ipod._stock_media_omission(f.new_source)
        self.assertTrue(reason)
        self.assertEqual(stock_ipod._stock_wav_header_omission(f.new_source.resolve()), reason)
        with mock.patch.object(stock_ipod, 'acquire_source',
                               side_effect=AssertionError('Rejected WAVEX was acquired')):
            plan = self.plan()
        self.assertEqual([row.reason for row in plan.omissions], [reason])
        self.assertEqual(plan.copies, ())
        self.assertEqual(f.new_source.read_bytes(), original)

    def test_actual_unknown_extensible_guid_acquires_before_existing_omission(self):
        f = self.f; f.playlist.keys[:] = ['new']
        write_waveex(f.new_source, format_tag=99)
        original = f.new_source.read_bytes()
        self.assertIsNone(stock_ipod._stock_wav_header_omission(f.new_source.resolve()))
        with mock.patch.object(stock_ipod, 'acquire_source', wraps=stock_ipod.acquire_source) as capture:
            plan = self.plan()
        self.assertEqual(capture.call_count, 1)
        self.assertEqual([row.track_id for row in plan.omissions], ['new'])
        self.assertEqual(plan.copies, ())
        self.assertEqual(f.new_source.read_bytes(), original)

    def test_large_extensible_omission_rechecks_changed_supported_container(self):
        f = self.f
        f.playlist.keys[:] = ['new']
        write_waveex(f.new_source, size=512 * 1024 * 1024)
        before = f._snapshot(f.ipod)
        with mock.patch.object(stock_ipod, 'acquire_source',
                               side_effect=AssertionError('Large rejected WAVEX was acquired')):
            for _ in range(2):
                omitted = self.plan()
                self.assertEqual(len(omitted.omissions), 1)
                self.assertEqual(omitted.copies, ())
        self.assertEqual(f._snapshot(f.ipod), before)
        write_wave(f.new_source, width=2)
        with mock.patch.object(stock_ipod, 'acquire_source', wraps=stock_ipod.acquire_source) as capture:
            admitted = self.plan()
        self.assertEqual(capture.call_count, 1)
        self.assertEqual(admitted.omissions, ())
        self.assertEqual(len(admitted.copies), 1)
        stock_ipod.commit_stock_ipod_sync(admitted)

    def test_incremental_added_unsupported_wav_is_omitted_without_acquisition(self):
        case = baseline.ActualExportPlaylistBaseline()
        case.setUp(); self.addCleanup(case.doCleanups)
        f = case.f; f.playlist.keys[:] = ['old']
        first = stock_ipod.commit_stock_ipod_sync(case.plan())
        self.assertIsNotNone(first.catalog_witness)
        write_wave(f.new_source); original = f.new_source.read_bytes()
        f.playlist.keys.append('new')
        snapshot = read_stock_ipod_snapshot(f.ipod, volume_uuid='generated-volume')
        with mock.patch.object(stock_ipod, 'acquire_source',
                               side_effect=AssertionError('Incremental omission acquired audio')):
            plan = stock_ipod.plan_stock_ipod_catalog_update(f.library, [f.playlist], f.ipod,
                snapshot=snapshot, model_number='MA446', filesystem='msdos', usb_family='iPod Video',
                display_rows=case.rows(), catalog_witness=first.catalog_witness, incremental=True)
        self.assertEqual([row.track_id for row in plan.omissions], ['new'])
        self.assertEqual(plan.copies, ())
        self.assertEqual(f.new_source.read_bytes(), original)
