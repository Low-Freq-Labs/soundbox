"""Real recoverable file conversion on TEMP encrypted libraries; no live source."""
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from uuid import uuid4

from test_registration_pilot import source_fixture
from test_wave_conversion import wave
from soundbox import media_conversion_store as store,rekordbox_cue_store as cues,rekordbox_tags as tags
from soundbox.rekordbox_write import _connect,ConflictError,RecoveryRequired

UUID='00000000-0000-4000-8000-000000000001'


def fixture(root):
    source=root/'source';source_fixture(source);audio=root/'music/song.wav';audio.parent.mkdir();wave(audio)
    conn=_connect(source/'master.db')
    try:
        columns={r[1] for r in conn.execute('PRAGMA table_info(djmdContent)')}
        if 'FileType' not in columns:conn.execute('ALTER TABLE djmdContent ADD COLUMN FileType INTEGER')
        conn.execute("UPDATE djmdContent SET UUID=?,FolderPath=?,FileType=11 WHERE ID='1'",(UUID,str(audio)))
        schema=tags._schema(conn)
    finally:conn.close()
    return source,audio,schema


class ExistingMediaConversion(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name).resolve()
        self.source,self.audio,schema=fixture(self.root)
        for obj,key in ((cues,'_closed'),(tags,'_assert_closed')):self.enterContext(mock.patch.object(obj,key,return_value=None))
        for obj in (cues,tags):self.enterContext(mock.patch.object(obj,'_installed_version',return_value=tags.NATIVE_VERSION))
        self.authority=store.ConversionAuthority(str(self.source/'master.db'),'123',schema,'0'*64,True)
        self.writer=store.ConversionStore(self.authority,self.root/'data');self.op=str(uuid4())
        self.native=[dict(id='1',uuid=UUID)]

    def convert(self):return self.writer.convert(self.audio,self.op,native=self.native)

    def test_existing_float_replacement_preserves_entire_native_db_and_exact_original_and_replays(self):
        original=self.audio.read_bytes();database=(self.source/'master.db').read_bytes()
        result=self.convert();self.assertEqual(result['facts']['bits'],24)
        self.assertEqual((self.source/'master.db').read_bytes(),database)
        self.assertEqual(Path(result['original']).read_bytes(),original)
        self.assertNotEqual(self.audio.read_bytes(),original);self.assertFalse(self.writer.journal.exists())
        self.assertEqual(self.convert(),result)
        self.assertFalse(list(self.audio.parent.glob('.soundbox-media-*')))

    def test_changed_native_uuid_or_shared_unknown_journal_never_reaches_audio_replacement(self):
        raw=self.audio.read_bytes()
        conn=_connect(self.source/'master.db')
        try:conn.execute("UPDATE djmdContent SET UUID='different' WHERE ID='1'")
        finally:conn.close()
        with self.assertRaises(ConflictError):self.convert()
        self.assertEqual(self.audio.read_bytes(),raw)
        self.writer.journal.write_text('{"format":"another-domain"}')
        with self.assertRaises(RecoveryRequired):self.convert()
        self.assertEqual(self.audio.read_bytes(),raw)

    def test_foreign_audio_after_interrupted_conversion_is_preserved_and_blocks_recovery(self):
        def fail(phase):
            if phase=='journal':raise RuntimeError('interrupted')
        with mock.patch.object(store,'_fault',side_effect=fail),self.assertRaises(RuntimeError):self.convert()
        self.audio.write_bytes(b'user replacement')
        with self.assertRaises(RecoveryRequired):self.writer.recover()
        self.assertEqual(self.audio.read_bytes(),b'user replacement');self.assertTrue(self.writer.journal.exists())

    def test_process_exit_before_and_after_replacement_recovers_without_native_sql_or_duplicate_conversion(self):
        for phase in ('prepared','journal','replaced','receipt'):
            with self.subTest(phase=phase):
                wave(self.audio);self.op=str(uuid4());raw=self.audio.read_bytes();database=(self.source/'master.db').read_bytes()
                body=dict(authority=asdict(self.authority),data=str(self.root/'data'),audio=str(self.audio),op=self.op,native=self.native,phase=phase)
                path=self.root/'child.json';path.write_text(json.dumps(body))
                code='''import json,os,sys
from pathlib import Path
from soundbox import media_conversion_store as s,rekordbox_cue_store as c,rekordbox_tags as t
b=json.loads(Path(sys.argv[1]).read_bytes());c._closed=lambda:None;t._assert_closed=lambda:None
c._installed_version=t._installed_version=lambda:t.NATIVE_VERSION
s._fault=lambda p:os._exit(73) if p==b['phase'] else None
w=s.ConversionStore(s.ConversionAuthority(**b['authority']),b['data'])
w.convert(b['audio'],b['op'],native=b['native'])
'''
                child=subprocess.run([sys.executable,'-c',code,str(path)],capture_output=True,text=True)
                self.assertEqual(child.returncode,73,child.stderr)
                result=self.convert();self.assertEqual(Path(result['original']).read_bytes(),raw)
                self.assertEqual((self.source/'master.db').read_bytes(),database)
                self.assertFalse(self.writer.journal.exists());self.assertEqual(self.convert(),result)

    def test_original_backup_tampering_and_device_or_redirect_paths_fail_closed(self):
        def fail(phase):
            if phase=='prepared':raise RuntimeError('paused')
        with mock.patch.object(store,'_fault',side_effect=fail),self.assertRaises(RuntimeError):self.convert()
        original=self.root/'data/media-conversion-originals'/self.op/'original.wav';original.write_bytes(b'changed backup')
        with self.assertRaises(RecoveryRequired):self.convert()
        alias=self.root/'alias.wav';alias.symlink_to(self.audio)
        with self.assertRaises(Exception):self.writer.convert(alias,str(uuid4()))
        with self.assertRaises(Exception):self.writer.convert(Path('/Volumes/Fake/song.wav'),str(uuid4()))

    def test_unregistered_flac_becomes_same_folder_wave_with_exact_original_and_no_native_sql(self):
        import numpy as np
        import soundfile as sf
        path=self.audio.with_name('new.flac');sf.write(path,np.zeros((44100*5,2)),44100,subtype='PCM_24')
        original=path.read_bytes();database=(self.source/'master.db').read_bytes();operation=str(uuid4())
        result=self.writer.convert(path,operation)
        self.assertFalse(path.exists());self.assertEqual(result['target'],str(path.with_suffix('.wav')))
        self.assertEqual(Path(result['original']).read_bytes(),original)
        self.assertEqual((self.source/'master.db').read_bytes(),database)
        self.assertEqual(self.writer.convert(path,operation),result)

    def test_codec_process_loss_recovers_each_dual_path_boundary_without_overwriting_an_occupant(self):
        import numpy as np
        import soundfile as sf
        for phase in ('prepared','journal','replaced','displaced','receipt'):
            with self.subTest(phase=phase):
                path=self.audio.with_name(phase+'.flac');sf.write(path,np.zeros((44100,2)),44100,subtype='PCM_24')
                raw=path.read_bytes();op=str(uuid4())
                body=dict(authority=asdict(self.authority),data=str(self.root/'data'),audio=str(path),op=op,phase=phase)
                config=self.root/'codec-child.json';config.write_text(json.dumps(body))
                code='''import json,os,sys
from pathlib import Path
from soundbox import media_conversion_store as s,rekordbox_cue_store as c,rekordbox_tags as t
b=json.loads(Path(sys.argv[1]).read_bytes());c._closed=lambda:None;t._assert_closed=lambda:None
c._installed_version=t._installed_version=lambda:t.NATIVE_VERSION
s._fault=lambda p:os._exit(73) if p==b['phase'] else None
s.ConversionStore(s.ConversionAuthority(**b['authority']),b['data']).convert(b['audio'],b['op'])
'''
                child=subprocess.run([sys.executable,'-c',code,str(config)],capture_output=True,text=True)
                self.assertEqual(child.returncode,73,child.stderr)
                result=self.writer.convert(path,op);self.assertFalse(path.exists())
                self.assertEqual(Path(result['original']).read_bytes(),raw);self.assertTrue(path.with_suffix('.wav').exists())
                self.assertFalse(self.writer.journal.exists())

    def test_registered_codec_and_same_name_destination_conflicts_leave_originals_untouched(self):
        import numpy as np
        import soundfile as sf
        path=self.audio.with_name('new.flac');sf.write(path,np.zeros((44100,2)),44100,subtype='PCM_24');raw=path.read_bytes()
        path.with_suffix('.wav').write_bytes(b'unrelated occupant')
        with self.assertRaises(ConflictError):self.writer.convert(path,str(uuid4()))
        self.assertEqual(path.read_bytes(),raw)
        path.with_suffix('.wav').unlink();conn=_connect(self.source/'master.db')
        try:conn.execute("UPDATE djmdContent SET FolderPath=?,FileType=5 WHERE ID='1'",(str(path),))
        finally:conn.close()
        with self.assertRaises(Exception):self.writer.convert(path,str(uuid4()),native=self.native)
        self.assertEqual(path.read_bytes(),raw);self.assertFalse(path.with_suffix('.wav').exists())

    def test_registered_codec_receipt_rebinds_existing_native_record_and_replays_after_restart(self):
        import numpy as np
        import soundfile as sf
        from soundbox import rekordbox_locator_store as locator
        path=self.audio.with_name('registered.flac');sf.write(path,np.zeros((44100,2)),44100,subtype='PCM_24')
        conn=_connect(self.source/'master.db')
        try:
            columns={r[1] for r in conn.execute('PRAGMA table_info(djmdContent)')}
            for name in ('FileNameL','rb_file_id','usn','rb_local_synced','rb_local_usn','updated_at'):
                if name not in columns:conn.execute('ALTER TABLE djmdContent ADD COLUMN '+name+' TEXT')
            conn.execute("UPDATE djmdContent SET FolderPath=?,FileType=5,FileNameL=?,rb_local_synced=0 WHERE ID='1'",(str(path),path.name))
            schema=tags._schema(conn)
        finally:conn.close()
        self.authority=store.ConversionAuthority(str(self.source/'master.db'),'123',schema,'0'*64,True)
        self.writer=store.ConversionStore(self.authority,self.root/'data')
        authority=locator.LocatorAuthority(str(self.source/'master.db'),'123',schema,'1'*64,('1',))
        (self.root/'data/rekordbox-media-locator-authority.json').write_text(json.dumps(dict(format=locator.AUTHORITY_FORMAT,**asdict(authority))))
        raw=path.read_bytes();conn=_connect(self.source/'master.db')
        try:conn.execute("UPDATE djmdContent SET usn='cloud-state' WHERE ID='1'")
        finally:conn.close()
        with self.assertRaises(Exception):self.writer.convert(path,self.op,native=self.native)
        self.assertEqual(path.read_bytes(),raw);self.assertFalse(path.with_suffix('.wav').exists())
        conn=_connect(self.source/'master.db')
        try:
            conn.execute("UPDATE djmdContent SET usn=NULL WHERE ID='1'")
            conn.execute("INSERT INTO djmdContent(ID,UUID,FolderPath,rb_local_deleted) VALUES ('2','other',?,0)",(str(path.with_suffix('.wav')),))
        finally:conn.close()
        with self.assertRaises(ConflictError):self.writer.convert(path,self.op,native=self.native)
        self.assertEqual(path.read_bytes(),raw);self.assertFalse(path.with_suffix('.wav').exists())
        conn=_connect(self.source/'master.db')
        try:conn.execute("DELETE FROM djmdContent WHERE ID='2'")
        finally:conn.close()
        result=self.writer.convert(path,self.op,native=self.native)
        self.assertFalse(path.exists());self.assertTrue(Path(result['original']).exists())
        writer=locator.LocatorStore(authority,self.root/'data');writer.relocate_conversion(result)
        self.assertEqual(writer.relocate_conversion(self.writer.convert(path,self.op,native=self.native)),result)
        conn=_connect(self.source/'master.db',readonly=True)
        try:
            row=locator.encoded_row(conn,'djmdContent','1');self.assertEqual(row['FolderPath'],str(path.with_suffix('.wav')))
            self.assertEqual(row['UUID'],UUID);self.assertEqual(row['FileType'],11)
        finally:conn.close()


class TechnicalHeaderProjection(unittest.TestCase):
    def test_usb_metadata_uses_actual_epoch_bound_header_without_changing_musical_fields(self):
        from soundbox.cdj_source import _actual_technical
        from soundbox.analysis import identity
        with tempfile.TemporaryDirectory() as area:
            path=Path(area).resolve()/'song.wav';path.write_bytes(b'new PCM24 bytes')
            dto=dict(sample_rate=96000,file_size=999,bitrate=6144,metadata=dict(bit_depth=32),bpm_x100=14500,key='1A',cues=[dict(time_ms=1234)])
            actual=dict(path=str(path),epoch=identity(path.stat()),facts=dict(status='compatible',family='wav',bits=24,channels=2,sample_rate=48000))
            _actual_technical(dto,actual,path)
            self.assertEqual((dto['sample_rate'],dto['file_size'],dto['metadata']['bit_depth']),(48000,path.stat().st_size,24))
            self.assertEqual((dto['bpm_x100'],dto['key'],dto['cues']),(14500,'1A',[dict(time_ms=1234)]))
            path.write_bytes(b'new user bytes');before=json.loads(json.dumps(dto));_actual_technical(dto,actual,path)
            self.assertEqual(dto,before)
