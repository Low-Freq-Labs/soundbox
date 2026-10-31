"""General bare construction remains captured-input/original-TEMP-only."""
from dataclasses import replace
import hashlib,json
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
from uuid import uuid4

from test_registration_proof import fixture,wave
from research.native_library import media_registration as media
from research.cdj_export.playlist_transaction import _copy_backup
from soundbox.rekordbox_write import _connect,_state,WriteProofError


class MediaRegistrationProof(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.source=self.root/'input.wav';wave(self.source)
        s=self.source.stat()
        self.audio=media.MediaIdentity(str(self.source),(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns),
            hashlib.sha256(self.source.read_bytes()).hexdigest(),1,'wav','pcm_s16le',1,1,1)

    def test_probe_reads_captured_copy_and_keeps_original_inode_digest_and_duration(self):
        before=self.source.read_bytes();original=self.source.stat()
        def probe(args,**kwargs):
            selected=Path(args[-1]);self.assertNotEqual(selected,self.source)
            self.assertEqual(selected.read_bytes(),before);self.assertNotEqual(selected.stat().st_ino,original.st_ino)
            return SimpleNamespace(stdout=json.dumps(dict(streams=[dict(codec_type='audio',codec_name='pcm_s16le')],
                format=dict(format_name='wav',duration='1.000000'))))
        with mock.patch.object(media.subprocess,'run',side_effect=probe):
            facts=media.inspect_media(self.source,self.root/'scratch',ffprobe=Path(sys.executable).resolve(),
                                      expected_sha256=self.audio.sha256)
        self.assertEqual(facts,self.audio);self.assertEqual(self.source.read_bytes(),before)
        self.assertEqual(self.source.stat().st_ino,original.st_ino)

    def test_format_duration_policy_rejects_unknown_codec_and_preserves_extensible_bare_zero(self):
        facts=replace(self.audio,codec='pcm_s24le',wave_format_tag=65534,seconds=0)
        facts.validate();self.assertEqual(facts.file_type,11)
        for change in (dict(codec='pcm_f32le'),dict(seconds=1),dict(family='m4a'),dict(sha256='bad')):
            with self.assertRaises(WriteProofError):replace(facts,**change).validate()
        self.assertEqual(media.MediaIdentity.from_dict(self.audio.__dict__),self.audio)

    def test_marked_copy_transaction_rolls_back_and_commits_only_content_and_counter(self):
        frozen=self.root/'frozen';frozen.mkdir();fixture(frozen)
        area=Path(tempfile.mkdtemp(prefix=media.PREFIX)).resolve();self.addCleanup(shutil.rmtree,area)
        media.mark(area);_copy_backup(frozen/'master.db',area/'working.db')
        before=_connect(frozen/'master.db',readonly=True);conn=_connect(area/'working.db')
        try:
            initial=_state(conn);plan=media.plan(conn,self.audio,identity='1234',uuid=str(uuid4()),
                timestamp='2026-10-31 03:00:00.000 +00:00',day='2026-10-30')
            conn.execute('BEGIN');media.apply_temp(conn,plan);media.verify(before,conn,[plan]);conn.rollback()
            self.assertEqual(_state(conn),initial)
            conn.execute('BEGIN');media.apply_temp(conn,plan);conn.commit();media.verify(before,conn,[plan])
            self.assertEqual(conn.execute('SELECT FileType,Length,rb_file_id FROM djmdContent WHERE ID=?',('1234',)).fetchone(),
                             (11,1,str(self.source.stat().st_ino)))
        finally:before.close();conn.close()

    def test_unmarked_connection_tampered_intent_and_outside_marker_never_mutate(self):
        frozen=self.root/'frozen';frozen.mkdir();fixture(frozen);conn=_connect(frozen/'master.db')
        try:
            initial=_state(conn);plan=media.plan(conn,self.audio,identity='1234',uuid=str(uuid4()),
                timestamp='2026-10-31 03:00:00.000 +00:00',day='2026-10-30')
            with self.assertRaises(WriteProofError):media.apply_temp(conn,plan)
            plan['row']['FileType']=4
            with self.assertRaises(WriteProofError):media.validate_plan(conn,plan)
            self.assertEqual(_state(conn),initial)
        finally:conn.close()
        mode=frozen.stat().st_mode
        with self.assertRaises(WriteProofError):media.mark(frozen)
        self.assertEqual(frozen.stat().st_mode,mode)
