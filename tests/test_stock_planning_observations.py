"""Generated WAV rejection facts never replace fresh source admission."""
import os
from pathlib import Path
import struct
import tempfile
from threading import Event
from types import SimpleNamespace
import unittest
from unittest import mock

from soundbox import stock_ipod
from soundbox.operation_control import OperationCancelled, operation_scope
from soundbox.stock_plan_observations import NegativeSourceOmissions
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
