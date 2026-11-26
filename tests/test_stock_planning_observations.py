"""Generated WAV rejection facts never replace fresh source admission."""
import os
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import subprocess
import sys
from threading import Event
from types import SimpleNamespace
import unittest
from unittest import mock

from soundbox import stock_ipod
from soundbox.operation_control import OperationCancelled, operation_scope
from soundbox.stock_plan_observations import NegativeSourceOmissions, source_epoch
from tests import test_stock_ipod as fixture


def wave_bytes(bits=24, rate=44100, tag=1):
    align=2*(bits//8)
    fmt=struct.pack('<HHIIHH',tag,2,rate,rate*align,align,bits)
    payload=b'\0'*192
    body=b'WAVEfmt '+struct.pack('<I',len(fmt))+fmt+b'data'+struct.pack('<I',len(payload))+payload
    return b'RIFF'+struct.pack('<I',len(body))+body


class StockOmissionObservations(unittest.TestCase):
    def setUp(self):
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.path=self.root/'source.wav';self.path.write_bytes(wave_bytes())
        self.enterContext(mock.patch.object(stock_ipod,'_WAV_OMISSIONS',NegativeSourceOmissions(2)))

    def test_unchanged_rejection_reuses_no_open_but_preserves_source_bytes(self):
        original=self.path.read_bytes()
        self.assertIn('PCM_24',stock_ipod._stock_wav_header_omission(self.path))
        with mock.patch.object(stock_ipod.os,'open',side_effect=AssertionError('Header reopened')):
            self.assertIn('PCM_24',stock_ipod._stock_wav_header_omission(self.path))
        self.assertEqual(self.path.read_bytes(),original)

    def test_supported_unknown_and_malformed_results_are_never_memoized(self):
        for raw in (wave_bytes(bits=16),wave_bytes(tag=4),b'malformed'):
            with self.subTest(header=raw[:12]):
                self.path.write_bytes(raw)
                with mock.patch.object(stock_ipod.os,'open',wraps=os.open) as opened:
                    self.assertIsNone(stock_ipod._stock_wav_header_omission(self.path))
                    self.assertIsNone(stock_ipod._stock_wav_header_omission(self.path))
                self.assertEqual(opened.call_count,2)

    def test_same_size_mtime_rewrite_inode_mode_and_link_changes_require_new_headers(self):
        original=self.path.stat()
        self.assertTrue(stock_ipod._stock_wav_header_omission(self.path))
        self.path.write_bytes(wave_bytes(bits=16))  # Same bytesize and mtime, newer ctime.
        os.utime(self.path,ns=(original.st_atime_ns,original.st_mtime_ns))
        self.assertEqual(self.path.stat().st_size,original.st_size)
        self.assertEqual(self.path.stat().st_mtime_ns,original.st_mtime_ns)
        self.assertIsNone(stock_ipod._stock_wav_header_omission(self.path))
        self.path.write_bytes(wave_bytes())
        self.assertTrue(stock_ipod._stock_wav_header_omission(self.path))
        for change in ('mode','link','inode'):
            with self.subTest(change=change):
                if change=='mode':self.path.chmod(self.path.stat().st_mode ^ 0o100)
                elif change=='link':os.link(self.path,self.root/'hardlink.wav')
                else:
                    replacement=self.root/'replacement.wav';replacement.write_bytes(wave_bytes())
                    os.replace(replacement,self.path)
                with mock.patch.object(stock_ipod.os,'open',wraps=os.open) as opened:
                    self.assertIn('PCM_24',stock_ipod._stock_wav_header_omission(self.path))
                self.assertEqual(opened.call_count,1)

    def test_changing_or_failed_post_hit_observation_discards_old_rejection(self):
        for failure in ('changed','error'):
            with self.subTest(failure=failure):
                self.assertTrue(stock_ipod._stock_wav_header_omission(self.path))
                before=self.path.lstat()
                after=SimpleNamespace(**{name:getattr(before,name) for name in
                    ('st_dev','st_ino','st_mode','st_nlink','st_size','st_mtime_ns','st_ctime_ns')})
                after.st_ctime_ns+=1
                observations=[before,after if failure=='changed' else OSError('Generated stat failure')]
                with mock.patch.object(Path,'lstat',side_effect=observations):
                    self.assertIsNone(stock_ipod._stock_wav_header_omission(self.path))
                with mock.patch.object(stock_ipod.os,'open',wraps=os.open) as opened:
                    self.assertTrue(stock_ipod._stock_wav_header_omission(self.path))
                self.assertEqual(opened.call_count,1)

    def test_symlink_and_missing_source_do_not_reuse_rejection(self):
        self.assertTrue(stock_ipod._stock_wav_header_omission(self.path))
        target=self.root/'target.wav';target.write_bytes(wave_bytes(bits=16))
        self.path.unlink();self.path.symlink_to(target)
        self.assertIsNone(stock_ipod._stock_wav_header_omission(self.path))
        self.path.unlink()
        self.assertIsNone(stock_ipod._stock_wav_header_omission(self.path))
        self.path.write_bytes(wave_bytes(bits=16))
        self.assertIsNone(stock_ipod._stock_wav_header_omission(self.path))

    def test_negative_observations_are_bounded_and_cancellation_still_precedes_hit(self):
        for name in ('first','second','third'):
            path=self.root/(name+'.wav');path.write_bytes(wave_bytes())
            self.assertTrue(stock_ipod._stock_wav_header_omission(path))
        with mock.patch.object(stock_ipod.os,'open',wraps=os.open) as opened:
            self.assertTrue(stock_ipod._stock_wav_header_omission(self.root/'first.wav'))
        self.assertEqual(opened.call_count,1)
        event=Event();event.set()
        with operation_scope(event),self.assertRaises(OperationCancelled):
            stock_ipod._stock_wav_header_omission(self.root/'first.wav')

    def test_repeat_plan_reuses_only_rejection_and_keeps_supported_source_acquisition(self):
        case=fixture.StockIpodSync();case.setUp();self.addCleanup(case.doCleanups)
        from soundbox.library import Track
        case.library.tracks_by_id['unsupported']=Track('unsupported','Unsupported','',self.path.as_uri())
        case.playlist.keys.append('unsupported')
        before=case._snapshot(case.ipod)
        original_open=os.open;opened=[]
        original_acquire=stock_ipod.acquire_source;acquired=[]
        def observe_open(path,*args,**kwargs):
            if os.fspath(path)==str(self.path):opened.append(path)
            return original_open(path,*args,**kwargs)
        def acquire(contract,*args,**kwargs):
            self.assertNotEqual(contract.canonical,self.path)
            acquired.append(contract.canonical)
            return original_acquire(contract,*args,**kwargs)
        with (mock.patch.object(stock_ipod.os,'open',side_effect=observe_open),
              mock.patch.object(stock_ipod,'acquire_source',side_effect=acquire)):
            plans=[stock_ipod.plan_stock_ipod_sync(case.library,[case.playlist],case.ipod,
                model_number='MA446',filesystem='msdos',usb_family='iPod Video') for _ in range(2)]
        self.assertEqual(len(opened),1)
        self.assertEqual(acquired.count(case.new_source.resolve()),2)
        self.assertEqual([plan.omissions[0].track_id for plan in plans],['unsupported','unsupported'])
        self.assertEqual([len(plan.copies) for plan in plans],[1,1])
        self.assertEqual(case._snapshot(case.ipod),before)


class PersistentStockOmissions(unittest.TestCase):
    def setUp(self):
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.path=self.root/'source.wav';self.path.write_bytes(wave_bytes())
        self.cache=self.root/'cache'
        self.profile=stock_ipod._STOCK_SOURCE_PROFILE

    def store(self, **kwargs):
        return NegativeSourceOmissions(root=self.cache,profile=kwargs.get('profile',self.profile),
                                       limit=kwargs.get('limit',4096))

    def seed(self):
        store=self.store()
        reason=stock_ipod._stock_wav_header_omission(self.path,omissions=store)
        self.assertIn('PCM_24',reason)
        store.flush()
        return reason

    def test_fresh_process_reuses_rejection_without_opening_any_source_audio(self):
        expected=self.seed()
        script='''import sys
from pathlib import Path
from unittest import mock
from soundbox import stock_ipod
from soundbox.stock_plan_observations import NegativeSourceOmissions
source,root=map(Path,sys.argv[1:])
memo=NegativeSourceOmissions(root=root,profile=stock_ipod._STOCK_SOURCE_PROFILE)
with mock.patch.object(stock_ipod.os,'open',side_effect=AssertionError('Source reopened')):
    print(stock_ipod._stock_wav_header_omission(source,omissions=memo))
'''
        run=subprocess.run([sys.executable,'-c',script,str(self.path),str(self.cache)],
                           capture_output=True,text=True,check=True)
        self.assertEqual(run.stdout.strip(),expected)
        self.assertEqual(self.store().current(self.path),expected)

    def test_corrupt_unknown_profile_oversized_and_redirected_cache_are_misses(self):
        self.seed();path=self.cache/'negative-stock-sources-v1.json';original=path.read_bytes()
        self.assertIsNone(self.store(profile='different-stock-policy').current(self.path))
        for raw in (b'{',original.replace(b'PCM_24',b'PCM_16'),b'[]',b' '*((8*1024*1024)+1)):
            with self.subTest(size=len(raw)):
                path.write_bytes(raw)
                self.assertIsNone(self.store().current(self.path))
        path.write_bytes(original)
        target=self.root/'redirected.json';path.rename(target);path.symlink_to(target)
        self.assertIsNone(self.store().current(self.path))
        path.unlink();path.write_bytes(original)
        moved=self.root/'moved-cache';self.cache.rename(moved);self.cache.symlink_to(moved,target_is_directory=True)
        self.assertIsNone(self.store().current(self.path))

    def test_checksum_valid_malformed_rows_and_positive_or_error_results_are_misses(self):
        self.seed();path=self.cache/'negative-stock-sources-v1.json';original=json.loads(path.read_bytes())
        for mutation in ('positive','unreadable','relative','duplicate','bad_epoch','unknown_field'):
            with self.subTest(mutation=mutation):
                doc=json.loads(json.dumps(original))
                if mutation=='positive':doc['rows'][0][2]=''
                elif mutation=='unreadable':doc['rows'][0][2]='unreadable WAV properties (generated)'
                elif mutation=='relative':doc['rows'][0][0]='source.wav'
                elif mutation=='duplicate':doc['rows'].append(doc['rows'][0])
                elif mutation=='bad_epoch':doc['rows'][0][1][0]=True
                else:doc['unknown']='field'
                doc['sha256']=hashlib.sha256(json.dumps(doc['rows'],sort_keys=True,
                    separators=(',',':'),ensure_ascii=True).encode()).hexdigest()
                path.write_text(json.dumps(doc))
                self.assertIsNone(self.store().current(self.path))

    def test_changed_source_epoch_replacement_and_symlink_never_keep_old_rejection(self):
        for mutation in ('rewrite','inode','symlink','mode','link'):
            with self.subTest(mutation=mutation):
                if self.path.is_symlink():self.path.unlink()
                self.path.write_bytes(wave_bytes());before=self.path.stat();self.seed()
                replacement=self.root/'replacement.wav';replacement.write_bytes(wave_bytes(bits=16))
                if mutation=='rewrite':
                    self.path.write_bytes(wave_bytes(bits=16))
                    os.utime(self.path,ns=(before.st_atime_ns,before.st_mtime_ns))
                elif mutation=='inode':os.replace(replacement,self.path)
                elif mutation=='symlink':self.path.unlink();self.path.symlink_to(replacement)
                elif mutation=='mode':self.path.chmod(self.path.stat().st_mode^0o100)
                else:os.link(self.path,self.root/'hardlink.wav')
                self.assertIsNone(self.store().current(self.path))

    def test_unknown_io_and_failed_publication_are_optional_and_never_negative_facts(self):
        memo=self.store()
        for reason in ('','unreadable WAV properties (generated)','unknown'):
            memo.remember(self.path,source_epoch(self.path.stat()),reason)
        self.assertIsNone(memo.current(self.path))
        reason=stock_ipod._stock_wav_header_omission(self.path,omissions=memo)
        with mock.patch('soundbox.stock_plan_observations.atomic_cache_bytes',side_effect=OSError('Generated full disk')):
            memo.flush()
        self.assertEqual(memo.current(self.path),reason)
        self.assertIsNone(self.store().current(self.path))
        memo.flush();self.assertEqual(self.store().current(self.path),reason)

    def test_bounded_persistence_and_changed_snapshot_cannot_renew_negative_epoch(self):
        memo=self.store(limit=2)
        for i in range(3):
            path=self.root/f'{i}.wav';path.write_bytes(wave_bytes())
            self.assertTrue(stock_ipod._stock_wav_header_omission(path,omissions=memo))
        memo.flush();fresh=self.store(limit=2)
        self.assertIsNone(fresh.current(self.root/'0.wav'))
        self.assertEqual(len(fresh.rows),2)
        before=self.path.stat();reason=stock_ipod._stock_media_omission(self.path)
        self.path.write_bytes(wave_bytes(bits=16))
        stock_ipod._remember_stock_snapshot_omission(self.path,before,reason,fresh)
        self.assertIsNone(fresh.current(self.path))

    def test_both_readonly_planners_refuse_cache_roots_overlapping_target(self):
        from soundbox.stock_ipod_info import read_stock_ipod_snapshot
        case=fixture.StockIpodSync();case.setUp();self.addCleanup(case.doCleanups)
        snapshot=read_stock_ipod_snapshot(case.ipod,volume_uuid='generated')
        before=case._snapshot(case.ipod)
        for root in (case.ipod/'cache',case.ipod,case.ipod.parent):
            for catalog in (False,True):
                with self.subTest(root=root,catalog=catalog):
                    planner=(stock_ipod.plan_stock_ipod_catalog_update if catalog else stock_ipod.plan_stock_ipod_sync)
                    kwargs={'snapshot':snapshot} if catalog else {}
                    with self.assertRaisesRegex(stock_ipod.StockIpodError,'outside the iPod'):
                        planner(case.library,[case.playlist],case.ipod,model_number='MA446',
                            filesystem='msdos',usb_family='iPod Video',omission_cache_root=root,**kwargs)
        self.assertEqual(case._snapshot(case.ipod),before)
        self.assertFalse((case.ipod/'cache').exists())

    def test_full_acquisition_definite_negative_survives_cold_plan_then_changed_file_is_admitted(self):
        case=fixture.StockIpodSync();case.setUp();self.addCleanup(case.doCleanups)
        case.playlist.keys[:]=['new'];source=case.new_source.resolve()
        # Valid ordinary WAV with more opaque chunks than our bounded probe
        # walks. libsndfile still identifies the definitive unsupported format.
        def write(bits):
            raw=wave_bytes(bits=bits);chunks=(b'JUNK'+struct.pack('<I',0))*129
            body=raw[8:12]+chunks+raw[12:]
            source.write_bytes(b'RIFF'+struct.pack('<I',len(body))+body)
        def plan():
            return stock_ipod.plan_stock_ipod_sync(case.library,[case.playlist],case.ipod,
                model_number='MA446',filesystem='msdos',usb_family='iPod Video',omission_cache_root=self.cache)
        write(24);original=source.read_bytes();before=case._snapshot(case.ipod)
        with mock.patch.object(stock_ipod,'acquire_source',wraps=stock_ipod.acquire_source) as acquired:
            first=plan()
        self.assertEqual(acquired.call_count,1);self.assertEqual(len(first.omissions),1)
        with mock.patch.object(stock_ipod,'acquire_source',side_effect=AssertionError('Rejected source reacquired')):
            second=plan()
        self.assertEqual(second.omissions,first.omissions);self.assertEqual(second.copies,())
        self.assertEqual(source.read_bytes(),original);self.assertEqual(case._snapshot(case.ipod),before)
        old=source.stat();write(16);os.utime(source,ns=(old.st_atime_ns,old.st_mtime_ns))
        with mock.patch.object(stock_ipod,'acquire_source',wraps=stock_ipod.acquire_source) as acquired:
            admitted=plan()
        self.assertEqual(acquired.call_count,1);self.assertEqual(admitted.omissions,())
        self.assertEqual(len(admitted.copies),1)

    def test_definite_layer_ii_snapshot_rejection_survives_process_restart_but_changed_mp3_is_admitted(self):
        from mutagen.mp3 import MP3
        case=fixture.StockIpodSync();case.setUp();self.addCleanup(case.doCleanups)
        case.playlist.keys[:]=['new'];source=case.new_source.with_suffix('.mp3').resolve()
        # Repeated syntactically valid MPEG-1 Layer II /128kbps /44.1kHz
        # frames, using Mutagen's actual parser rather than a fabricated info.
        source.write_bytes((b'\xff\xfd\x80\x64'+bytes(413))*100)
        case.library.tracks_by_id['new'].location_uri=source.as_uri()
        self.assertEqual(MP3(str(source)).info.layer,2)
        expected='MPEG Layer 2; validated stock support is Layer III'
        def plan():
            return stock_ipod.plan_stock_ipod_sync(case.library,[case.playlist],case.ipod,
                model_number='MA446',filesystem='msdos',usb_family='iPod Video',omission_cache_root=self.cache)
        before=case._snapshot(case.ipod);original=source.read_bytes()
        with mock.patch.object(stock_ipod,'acquire_source',wraps=stock_ipod.acquire_source) as acquired:
            first=plan()
        self.assertEqual(acquired.call_count,1);self.assertEqual(first.omissions[0].reason,expected)
        script='''import sys
from pathlib import Path
from unittest import mock
from soundbox.stock_plan_observations import NegativeSourceOmissions,STOCK_SOURCE_PROFILE
source,root=map(Path,sys.argv[1:])
cache=NegativeSourceOmissions(root=root,profile=STOCK_SOURCE_PROFILE)
with mock.patch('os.open',side_effect=AssertionError('MP3 reopened')):
    print(cache.current(source))
'''
        run=subprocess.run([sys.executable,'-c',script,str(source),str(self.cache)],
                           capture_output=True,text=True,check=True)
        self.assertEqual(run.stdout.strip(),expected)
        with mock.patch.object(stock_ipod,'acquire_source',side_effect=AssertionError('Rejected MP3 acquired')):
            second=plan()
        self.assertEqual(second.omissions,first.omissions);self.assertEqual(second.copies,())
        self.assertEqual(source.read_bytes(),original);self.assertEqual(case._snapshot(case.ipod),before)
        previous=source.stat();source.write_bytes((b'\xff\xfb\x90\x64'+bytes(413))*100)
        os.utime(source,ns=(previous.st_atime_ns,previous.st_mtime_ns))
        self.assertEqual(source.stat().st_size,previous.st_size)
        self.assertEqual(MP3(str(source)).info.layer,3)
        with mock.patch.object(stock_ipod,'acquire_source',wraps=stock_ipod.acquire_source) as acquired:
            admitted=plan()
        self.assertEqual(acquired.call_count,1);self.assertEqual(admitted.omissions,())
        self.assertEqual(len(admitted.copies),1)

    def test_only_known_positive_unsupported_mp3_properties_are_negative_facts(self):
        source=self.root/'generated.mp3';source.write_bytes(b'generated observation')
        valid=('MPEG Layer 1; validated stock support is Layer III',
               'MPEG Layer 2; validated stock support is Layer III',
               '22050 Hz MP3; validated stock rates are 32/44.1/48 kHz',
               '3-channel MP3; stock audio must be mono/stereo',
               '7 kbps MP3; stock limit is 8–320 kbps',
               '384 kbps MP3; stock limit is 8–320 kbps')
        invalid=('unreadable MP3 properties (Generated transient error)',
                 'MPEG Layer 0; validated stock support is Layer III',
                 'MPEG Layer 3; validated stock support is Layer III',
                 '0 Hz MP3; validated stock rates are 32/44.1/48 kHz',
                 '44100 Hz MP3; validated stock rates are 32/44.1/48 kHz',
                 '0-channel MP3; stock audio must be mono/stereo',
                 '2-channel MP3; stock audio must be mono/stereo',
                 '0 kbps MP3; stock limit is 8–320 kbps',
                 '320 kbps MP3; stock limit is 8–320 kbps',
                 stock_ipod._stock_media_omission(self.path))
        for reason in (*valid,*invalid):
            with self.subTest(reason=reason):
                memo=self.store();memo.discard(source)
                memo.remember(source,source_epoch(source.stat()),reason);memo.flush()
                fresh=self.store()
                self.assertEqual(fresh.current(source),reason if reason in valid else None)

    def test_unreadable_mp3_remains_unmemoized_and_retries_full_capture(self):
        case=fixture.StockIpodSync();case.setUp();self.addCleanup(case.doCleanups)
        case.playlist.keys[:]=['new'];source=case.new_source.with_suffix('.mp3').resolve()
        source.write_bytes(b'generated unreadable source')
        case.library.tracks_by_id['new'].location_uri=source.as_uri()
        with mock.patch.object(stock_ipod,'acquire_source',wraps=stock_ipod.acquire_source) as acquired:
            for _ in range(2):
                plan=stock_ipod.plan_stock_ipod_sync(case.library,[case.playlist],case.ipod,
                    model_number='MA446',filesystem='msdos',usb_family='iPod Video',omission_cache_root=self.cache)
                self.assertIn('unreadable MP3 properties',plan.omissions[0].reason)
        self.assertEqual(acquired.call_count,2)
        self.assertIsNone(self.store().current(source))

    def test_incremental_catalog_uses_cold_negative_mp3_before_acquisition(self):
        from soundbox.stock_ipod_info import read_stock_ipod_snapshot
        from tests.test_export_playlist_baseline import ActualExportPlaylistBaseline
        case=ActualExportPlaylistBaseline();case.setUp();self.addCleanup(case.doCleanups)
        f=case.f;f.playlist.keys[:]=['old']
        first=stock_ipod.commit_stock_ipod_sync(case.plan())
        source=f.new_source.with_suffix('.mp3').resolve()
        source.write_bytes((b'\xff\xfd\x80\x64'+bytes(413))*100)
        f.library.tracks_by_id['new'].location_uri=source.as_uri();f.playlist.keys.append('new')
        snapshot=read_stock_ipod_snapshot(f.ipod,volume_uuid='generated-volume')
        before=f._snapshot(f.ipod)
        def plan():
            return stock_ipod.plan_stock_ipod_catalog_update(f.library,[f.playlist],f.ipod,
                snapshot=snapshot,model_number='MA446',filesystem='msdos',usb_family='iPod Video',
                display_rows=case.rows(),catalog_witness=first.catalog_witness,incremental=True,
                omission_cache_root=self.cache)
        with mock.patch.object(stock_ipod,'acquire_source',wraps=stock_ipod.acquire_source) as acquired:
            rejected=plan()
        self.assertEqual(acquired.call_count,1)
        self.assertIn('MPEG Layer 2',rejected.omissions[0].reason)
        with mock.patch.object(stock_ipod,'acquire_source',side_effect=AssertionError('Rejected MP3 reacquired')):
            repeated=plan()
        self.assertEqual(repeated.omissions,rejected.omissions);self.assertEqual(repeated.copies,())
        self.assertEqual(f._snapshot(f.ipod),before)
