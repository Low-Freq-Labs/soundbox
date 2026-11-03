"""Exact CBR pilot on synthetic encrypted TEMP sources; no live app/state."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from uuid import uuid4
import struct

from research.native_library import mp3_analysis_pilot as pilot, mp3_seek
from soundbox import rekordbox_analysis_store as transport, rekordbox_cue_store as cues
from soundbox.native_media import MediaIdentity, epoch
from soundbox.rekordbox_grid import AnalysisImage, Beat, replace_grid
from soundbox.rekordbox_write import _connect, _json, _state, _LINK_COLUMNS, ConflictError, RecoveryRequired, WriteProofError
from soundbox.state_io import atomic_bytes
import test_analysis_workspace as database_fixture
import test_mp3_analysis_plan as packet_fixture


class ExactCBRSourcePilot(unittest.TestCase):
    def setUp(self):
        self.area = tempfile.TemporaryDirectory(); self.addCleanup(self.area.cleanup)
        self.root = Path(self.area.name).resolve()
        self.raw = (bytes.fromhex('fffb9000') + bytes(413)) * 1226
        for obj, name, value in (
                (pilot, 'REFERENCE_SHA256', hashlib.sha256(self.raw).hexdigest()),
                (pilot, '_installed_version', lambda: pilot.NATIVE_VERSION),
                (pilot, '_apps_closed', lambda: None),
                (pilot, 'assert_rekordbox_closed', lambda: None),
                (cues, '_installed_version', lambda: pilot.NATIVE_VERSION),
                (cues, '_closed', lambda: None)):
            patch = mock.patch.object(obj, name, value); patch.start(); self.addCleanup(patch.stop)
        self.source, self.folder, self.approval = self.prepare(self.root / 'case')

    def prepare(self, root):
        source = root / 'source'; fixture = database_fixture.fixture(source)
        (source / 'share').mkdir(mode=0o700)
        audio = root / pilot.REFERENCE_NAME; audio.write_bytes(self.raw)
        scan = mp3_seek.scan_mp3(audio)
        conn = _connect(source / 'master.db')
        try:
            conn.execute('CREATE TABLE djmdSongMyTag (' + ','.join('"' + column + '" TEXT' for column in _LINK_COLUMNS) + ')')
            conn.execute('CREATE TABLE djmdMyTag(ID TEXT PRIMARY KEY,UUID TEXT,Attribute INTEGER,rb_local_deleted INTEGER)')
            conn.execute('UPDATE djmdContent SET FolderPath=?,FileSize=?,rb_file_id=?,FileType=1,Length=32 WHERE ID=?',
                         (str(audio), len(self.raw), str(audio.stat().st_ino), '123'))
        finally:
            conn.close()
        result = dict(engine='rbxport-adaptive-v4', path=str(audio), sha256=scan.sha256, epoch=list(epoch(audio.stat())),
            size=len(self.raw), duration=scan.sample_count / scan.sample_rate, bpm=120.0, key='8B',
            beats=[[i % 4 + 1, 273 + i * 500, 120.0] for i in range(64)], first_cue=None)
        from soundbox.analysis import ENGINE
        result['engine'] = ENGINE
        data = packet_fixture.CBRComponentProposals(); data.setUp()
        try:
            images = deepcopy(data.images)
        finally:
            data.doCleanups()
        text = ('?/' + audio.name + '\0').encode('utf-16-be')
        ppth = b'PPTH' + struct.pack('>III', 16, 16 + len(text), len(text)) + text
        images = {ext: AnalysisImage.parse(raw).replacing(b'PPTH', ppth) for ext, raw in images.items()}
        images['.DAT'] = replace_grid(images['.DAT'], tuple(Beat(n, round(bpm * 100), ms) for n, ms, bpm in result['beats']))

        def renderer(command, scratch, request):
            self.assertEqual(command, 'saved-analysis')
            captured = Path(json.loads(request.read_bytes())['audio'])
            self.assertTrue(captured.is_relative_to(scratch)); self.assertEqual(captured.read_bytes(), self.raw)
            packet = scratch / 'packet'; packet.mkdir()
            for name in pilot.NAMES:
                (packet / name).write_bytes(images[Path(name).suffix])
            return dict(frames=scan.sample_count, rate=scan.sample_rate, channels=2,
                duration_ms=scan.sample_count * 1000 / scan.sample_rate,
                detail_columns=(scan.sample_count + 293) // 294, peak=0.0, rms=0.0,
                dat_bytes=len(images['.DAT']), ext_bytes=len(images['.EXT']), two_ex_bytes=len(images['.2EX']),
                source_write_authority=False)

        operation = str(uuid4()); folder = root / 'plans' / operation
        pilot.prepare(source / 'master.db', '123', fixture.track['UUID'], result, folder,
                      data_root=root / 'appdata', operation_id=operation, renderer=renderer)
        plan = pilot.load_plan(folder); approval = root / 'approval.json'
        atomic_bytes(approval, _json(pilot.approval_fields(plan, transport._sha(folder / 'plan.json'))))
        return source, folder, approval

    def instance(self):
        return pilot.MP3AnalysisPilot(self.folder, self.approval)

    def state(self, source=None):
        conn = _connect((source or self.source) / 'master.db', readonly=True)
        try:
            return _state(conn)
        finally:
            conn.close()

    def test_copy_preparation_then_exact_approved_commit_and_idempotent_replay(self):
        obj = self.instance(); audio = Path(obj.plan['audio']['path'])
        before, xml = audio.read_bytes(), obj.xml.read_bytes()
        self.assertEqual(self.state(), obj.plan['before'])
        self.assertFalse(obj.journal.exists()); self.assertFalse(obj.archive.exists())
        with self.assertRaises(WriteProofError):
            transport.load_plan(self.folder)
        self.assertEqual(obj.apply(), '123'); self.assertEqual(self.state(), obj.plan['after'])
        self.assertEqual(audio.read_bytes(), before); self.assertEqual(obj.xml.read_bytes(), xml)
        self.assertEqual(obj.apply(), 'already-completed')
        self.assertEqual(set(path.name for path in obj._locations()[1].iterdir()), set(pilot.NAMES))
        self.assertTrue(all((obj.archive / name).is_file() for name in pilot.PAYLOADS))
        receipt = json.loads((obj.archive / 'result.json').read_bytes())
        self.assertEqual(receipt['format'], pilot.FORMAT); self.assertFalse(receipt['native_acceptance'])
        self.assertNotIn('djmdMixerParam', [change['table'] for change in obj.plan['candidate']['changes']])

    def test_missing_foreign_or_changed_exact_approval_never_admits_sql(self):
        with self.assertRaises(OSError):
            pilot.MP3AnalysisPilot(self.folder, self.root / 'missing.json')
        original = self.approval.read_bytes()
        for field, value in (('format', transport.FORMAT), ('approved', False), ('approved', 1), ('approved', 1.0), ('track_id', '124'),
                             ('plan_sha256', '0' * 64), ('schema_sha256', '0' * 64), ('source_sha256', '0' * 64)):
            data = json.loads(original); data[field] = value; atomic_bytes(self.approval, _json(data))
            with self.subTest(field=field), self.assertRaises(WriteProofError):
                self.instance()
        atomic_bytes(self.approval, original); obj = self.instance()
        atomic_bytes(self.approval, original + b' ')
        with mock.patch.object(obj.lease, '_connect') as sql, self.assertRaises(WriteProofError):
            obj.apply()
        sql.assert_not_called(); self.assertEqual(self.state(), obj.plan['before'])

    def test_scope_packet_and_native_app_guards_fail_before_source_sql(self):
        obj = self.instance(); original = (self.folder / 'plan.json').read_bytes()
        from soundbox import analysis, local_grid
        drafted = local_grid.edit(obj.plan['result'], 'shift', 1)
        analysis.validate_result(drafted)
        self.assertEqual(drafted['beats'], obj.plan['result']['beats'])
        self.assertNotEqual(local_grid.effective_result(drafted)['beats'], obj.plan['result']['beats'])
        audio = MediaIdentity.from_dict(obj.plan['audio'])
        for record in (drafted, {**obj.plan['result'], 'grid_draft': None}):
            with self.assertRaisesRegex(WriteProofError, 'saved detector grid'):
                pilot._scope(audio, record)
        for kind in ('sha', 'grid', 'draft'):
            plan = json.loads(original)
            if kind == 'sha':
                plan['audio']['sha256'] = '0' * 64
            elif kind == 'grid':
                plan['result']['beats'][0][1] = 274
            else:
                plan['result'] = drafted
            atomic_bytes(self.folder / 'plan.json', _json(plan))
            with self.subTest(kind=kind), self.assertRaises(WriteProofError):
                pilot.load_plan(self.folder)
        atomic_bytes(self.folder / 'plan.json', original)
        packet = self.folder / 'ANLZ0000.DAT'; saved = packet.read_bytes(); packet.write_bytes(saved[:-1] + b'X')
        with self.assertRaises(RecoveryRequired):
            pilot.load_plan(self.folder)
        packet.write_bytes(saved)
        for target in ('_apps_closed', 'assert_rekordbox_closed'):
            with mock.patch.object(pilot, target, side_effect=WriteProofError('owner open')), \
                    mock.patch.object(obj.lease, '_connect') as sql, self.assertRaises(WriteProofError):
                obj.apply()
            sql.assert_not_called()
        self.assertEqual(self.state(), obj.plan['before'])

    def test_json_budgets_and_plan_types_reject_before_recovery_or_sql(self):
        obj = self.instance(); original = (self.folder / 'plan.json').read_bytes()
        plan = json.loads(original); plan['source_file_id'] = True
        atomic_bytes(self.folder / 'plan.json', _json(plan))
        with mock.patch.object(pilot, '_connect') as sql, self.assertRaisesRegex(WriteProofError, 'fields/types'):
            pilot.load_plan(self.folder)
        sql.assert_not_called(); atomic_bytes(self.folder / 'plan.json', original)
        atomic_bytes(obj.journal, b'{}' + bytes(500))
        with mock.patch.object(pilot, 'MAX_JSON_BYTES', 100), self.assertRaisesRegex(WriteProofError, 'byte budget'):
            obj.recover()
        self.assertTrue(obj.journal.exists()); self.assertEqual(self.state(), obj.plan['before'])

    def test_changed_audio_unknown_shared_recovery_or_unrelated_native_edits_are_preserved(self):
        obj = self.instance(); audio = Path(obj.plan['audio']['path']); before = audio.read_bytes()
        audio.write_bytes(before + b'changed')
        with self.assertRaises(ConflictError):
            obj.apply()
        self.assertEqual(audio.read_bytes(), before + b'changed')
        atomic_bytes(obj.journal, _json(dict(format='other-domain')))
        with self.assertRaises(RecoveryRequired):
            obj.recover()
        self.assertEqual(json.loads(obj.journal.read_bytes()), dict(format='other-domain'))
        self.assertEqual(self.state(), obj.plan['before'])

    def test_unknown_recovery_packet_bytes_are_never_cleaned(self):
        obj = self.instance()
        def interrupted(phase):
            if phase == 'files-published':
                raise RuntimeError('interrupted')
        with mock.patch.object(transport, '_fault', side_effect=interrupted), self.assertRaises(RuntimeError):
            obj.apply()
        target = obj._locations()[1]; path = target / 'ANLZ0000.DAT'
        changed = path.read_bytes() + b'unknown'; path.write_bytes(changed)
        with self.assertRaises(RecoveryRequired):
            obj.recover()
        self.assertTrue(obj.journal.exists()); self.assertEqual(path.read_bytes(), changed)
        self.assertEqual(self.state(), obj.plan['before'])

    def test_actual_process_exit_reconciles_directory_and_sql_prefixes(self):
        script = '''
import os,sys,json
from pathlib import Path
from research.native_library import mp3_analysis_pilot as p
from soundbox import rekordbox_analysis_store as t,rekordbox_cue_store as c
folder=Path(sys.argv[1]);raw=json.loads((folder/'plan.json').read_bytes())
p.REFERENCE_SHA256=raw['audio']['sha256'];p._apps_closed=lambda:None
p.assert_rekordbox_closed=lambda:None;p._installed_version=lambda:p.NATIVE_VERSION
c._installed_version=lambda:p.NATIVE_VERSION;c._closed=lambda:None
obj=p.MP3AnalysisPilot(folder,Path(sys.argv[2]))
def crash(phase):
 if phase==sys.argv[3]:os._exit(73)
t._fault=crash;obj.apply()
'''
        for index, phase in enumerate(('files-published', 'sql-applied', 'sql-committed', 'receipt-ready')):
            with self.subTest(phase=phase):
                source, folder, approval = self.prepare(self.root / str(index))
                obj = pilot.MP3AnalysisPilot(folder, approval)
                process = subprocess.run([sys.executable, '-c', script, str(folder), str(approval), phase],
                                         capture_output=True, text=True, timeout=40)
                self.assertEqual(process.returncode, 73, process.stderr)
                expected = 'committed' if phase in ('sql-committed', 'receipt-ready') else 'rolled-back'
                self.assertEqual(obj.recover(), expected)
                self.assertEqual(self.state(source), obj.plan['after' if expected == 'committed' else 'before'])
                self.assertFalse(obj.journal.exists())
