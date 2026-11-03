"""Accepted CBR transactions use synthetic encrypted TEMP sources only."""
from copy import deepcopy
from dataclasses import asdict
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from uuid import uuid4

from soundbox import rekordbox_cbr_analysis_store as store, rekordbox_analysis_store as wav
from soundbox import rekordbox_cue_store as cues
from soundbox.analysis import ENGINE
from soundbox.mp3_scan import scan_mp3
from soundbox.native_media import epoch
from soundbox.rekordbox_grid import AnalysisImage, Beat, replace_grid
from soundbox.rekordbox_tags import _schema
from soundbox.rekordbox_write import (_connect, _json, _state, _LINK_COLUMNS,
    ConflictError, RecoveryRequired, WriteProofError)
from soundbox.state_io import atomic_bytes
import test_analysis_workspace as db_fixture
import test_mp3_analysis_plan as packet_fixture


class CBRAnalysisPublication(unittest.TestCase):
    def setUp(self):
        self.area = tempfile.TemporaryDirectory(); self.addCleanup(self.area.cleanup)
        self.root = Path(self.area.name).resolve(); self.raw = (bytes.fromhex('fffb9000') + bytes(413)) * 1226
        for obj, name, value in ((store, '_installed_version', lambda: store.NATIVE_VERSION),
                (store, 'assert_rekordbox_closed', lambda: None),
                (cues, '_installed_version', lambda: store.NATIVE_VERSION), (cues, '_closed', lambda: None)):
            patch = mock.patch.object(obj, name, value); patch.start(); self.addCleanup(patch.stop)
        self.source, self.authority, self.folder = self.prepare(self.root / 'ordinary')

    def prepare(self, root, *, ctime_drift=False, extra=False, musical=False):
        source = root / 'source'; fixture = db_fixture.fixture(source); (source / 'share').mkdir(mode=0o700)
        path = root / 'Ordinary song.mp3'; path.write_bytes(self.raw); scan = scan_mp3(path)
        conn = _connect(source / 'master.db')
        try:
            conn.execute('CREATE TABLE djmdSongMyTag (' + ','.join('"' + column + '" TEXT' for column in _LINK_COLUMNS) + ')')
            conn.execute('CREATE TABLE djmdMyTag(ID TEXT PRIMARY KEY,UUID TEXT,Attribute INTEGER,rb_local_deleted INTEGER)')
            conn.execute('UPDATE djmdContent SET FolderPath=?,FileSize=?,rb_file_id=?,FileType=1,Length=32 WHERE ID=?',
                         (str(path), len(self.raw), str(path.stat().st_ino), '123'))
            if musical:
                conn.execute('UPDATE djmdContent SET BPM=12000 WHERE ID=\'123\'')
            if extra:
                from soundbox.rekordbox_write import _rows, _quote
                row = next(row for row in _rows(conn, 'djmdContent') if row['ID'] == '123')
                for tid in ('124', '125'):
                    other = root / (tid + '.mp3'); other.write_bytes(self.raw)
                    added = {**row, 'ID': tid, 'UUID': str(uuid4()), 'FolderPath': str(other),
                             'rb_file_id': str(other.stat().st_ino)}
                    conn.execute('INSERT INTO djmdContent (' + ','.join(_quote(key) for key in added) + ') VALUES (' +
                                 ','.join('?' for _ in added) + ')', list(added.values()))
            schema = _schema(conn)
        finally:
            conn.close()
        authority = store.CBRAnalysisAuthority(str(source / 'master.db'), '456', schema, 'a' * 64, True)
        result = dict(engine=ENGINE, path=str(path), sha256=scan.sha256, epoch=list(epoch(path.stat())), size=len(self.raw),
            duration=scan.sample_count / scan.sample_rate, bpm=120.0, key='8B',
            beats=[[i % 4 + 1, 299 + i * 500, 120.0] for i in range(64)], first_cue=None)
        if ctime_drift:
            os.chmod(path, 0o600)
            self.assertEqual(result['epoch'][:4], list(epoch(path.stat()))[:4])
            self.assertNotEqual(result['epoch'][4], epoch(path.stat())[4])
        operation = str(uuid4()); folder = root / 'appdata' / 'native-analysis-plans' / operation
        self.render_images = self.images(path, result)
        store.prepare(authority, '123', fixture.track['UUID'], result, folder, data_root=root / 'appdata',
                      operation_id=operation, renderer=self.renderer)
        return source, authority, folder

    def images(self, path, result):
        fixture = packet_fixture.CBRComponentProposals(); fixture.setUp()
        try:
            images = deepcopy(fixture.images)
        finally:
            fixture.doCleanups()
        text = ('?/' + path.name + '\0').encode('utf-16-be')
        ppth = b'PPTH' + struct.pack('>III', 16, 16 + len(text), len(text)) + text
        images = {ext: AnalysisImage.parse(raw).replacing(b'PPTH', ppth) for ext, raw in images.items()}
        images['.DAT'] = replace_grid(images['.DAT'], tuple(Beat(n, round(bpm * 100), ms) for n, ms, bpm in result['beats']))
        # The renderer does not author the MP3 seek table; the store must.
        empty_seek = b'PVBR' + struct.pack('>II', 16, 1620) + bytes(1608)
        images['.DAT'] = AnalysisImage.parse(images['.DAT']).replacing(b'PVBR', empty_seek)
        return images

    def renderer(self, command, root, request):
        self.assertEqual(command, 'saved-analysis'); captured = Path(json.loads(request.read_bytes())['audio'])
        self.assertTrue(captured.is_relative_to(root)); self.assertEqual(captured.read_bytes(), self.raw)
        scan = scan_mp3(captured); packet = root / 'packet'; packet.mkdir()
        for name in store.NAMES:
            (packet / name).write_bytes(self.render_images[Path(name).suffix])
        return dict(frames=scan.sample_count, rate=scan.sample_rate, channels=2,
            duration_ms=scan.sample_count * 1000 / scan.sample_rate, detail_columns=(scan.sample_count + 293) // 294,
            peak=0.0, rms=0.0, dat_bytes=len(self.render_images['.DAT']), ext_bytes=len(self.render_images['.EXT']),
            two_ex_bytes=len(self.render_images['.2EX']), source_write_authority=False)

    def instance(self):
        return store.CBRAnalysisStore(self.authority, self.folder)

    def state(self, source=None):
        conn = _connect((source or self.source) / 'master.db', readonly=True)
        try:
            return _state(conn)
        finally:
            conn.close()

    def test_ordinary_cbr_saved_components_commit_idempotently_without_replacing_music(self):
        obj = self.instance(); audio = Path(obj.plan['audio']['path']); before = audio.read_bytes(); xml = obj.xml.read_bytes()
        self.assertEqual(self.state(), obj.plan['before']); self.assertFalse(obj.journal.exists())
        self.assertEqual(obj.apply(), '123'); self.assertEqual(self.state(), obj.plan['after'])
        self.assertEqual(obj.apply(), 'already-completed'); self.assertEqual(audio.read_bytes(), before)
        self.assertEqual(obj.xml.read_bytes(), xml)
        self.assertEqual(json.loads((obj.folder / 'intent.json').read_bytes())['result'], obj.plan['result'])
        self.assertTrue(all((obj.archive / name).exists() for name in store.PAYLOADS))
        self.assertEqual(json.loads((obj.archive / 'result.json').read_bytes())['format'], store.FORMAT)
        self.assertNotIn('djmdMixerParam', [change['table'] for change in obj.plan['candidate']['changes']])
        self.assertEqual(obj.plan['candidate']['changes'][0]['after']['Length'], 32)

    def test_cbr_reuse_preserves_fresh_payload_and_profile_checks(self):
        expected = store.load_plan(self.folder)
        with mock.patch.object(store, '_verify_plan_reconstruction', side_effect=AssertionError('Duplicate pure proof')), \
                mock.patch.object(wav, '_sha', wraps=wav._sha) as hashes, \
                mock.patch.object(store, '_require_profile', wraps=store._require_profile) as profile:
            self.assertEqual(store.load_plan(self.folder), expected)
            self.assertEqual(self.instance().plan, expected)
        for name in store.PAYLOADS:
            self.assertGreaterEqual(sum(call.args[0] == self.folder / name for call in hashes.call_args_list), 2)
        self.assertGreaterEqual(profile.call_count, 2)
        packet = self.folder / 'ANLZ0000.DAT'; raw = packet.read_bytes(); info = packet.stat()
        packet.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1])); os.utime(packet, ns=(info.st_atime_ns, info.st_mtime_ns))
        with mock.patch.object(store, '_connect', side_effect=AssertionError('Hash check must precede SQL')), \
                self.assertRaises(RecoveryRequired):
            store.load_plan(self.folder)
        packet.write_bytes(raw)

    def test_changed_cbr_record_reconstructs_without_duplicate_before_inventory(self):
        raw = (self.folder / 'plan.json').read_bytes(); atomic_bytes(self.folder / 'plan.json', raw + b'\n')
        with mock.patch.object(wav, '_state', wraps=wav._state) as states:
            expected = store.load_plan(self.folder)
        self.assertEqual(states.call_count, 2)
        changed = json.loads(raw); changed['candidate']['scope'] = 'tampered copied proposal'
        atomic_bytes(self.folder / 'plan.json', _json(changed))
        with self.assertRaises(WriteProofError):
            store.load_plan(self.folder)
        atomic_bytes(self.folder / 'plan.json', raw)
        self.assertEqual(store.load_plan(self.folder), expected)

    def test_cbr_preparation_keeps_one_fresh_inventory_per_private_side(self):
        with mock.patch.object(wav, '_state', wraps=wav._state) as inventories:
            source, authority, folder = self.prepare(self.root / 'single-before-scan')
        self.assertEqual(inventories.call_count, 4)
        obj = store.CBRAnalysisStore(authority, folder)
        self.assertEqual(obj._snapshot(), obj.plan['before'])
        self.assertEqual(obj.apply(), '123')
        self.assertEqual(self.state(source), obj.plan['after'])

    def test_missing_wrong_or_legacy_authorities_cannot_enable_cbr(self):
        path = self.root / 'authority.json'
        self.assertIsNone(store.load_authority(path, self.authority.database, '456'))
        for accepted in (False, 1, 1.0):
            with self.assertRaises(WriteProofError):
                store.CBRAnalysisAuthority(self.authority.database, '456', self.authority.schema_sha256, 'a' * 64, accepted)
        atomic_bytes(path, _json(dict(format=store.AUTHORITY_FORMAT, **asdict(self.authority))))
        self.assertEqual(store.load_authority(path, self.authority.database, '456'), self.authority)
        self.assertIsNone(wav.load_authority(path, self.authority.database, '456'))
        self.assertIsNone(store.load_authority(path, self.authority.database, 'different'))
        self.assertEqual(self.authority.profile, store.PROFILE)
        for field, value in (('library_id', '789'), ('accepted_reference_sha256', 'b' * 64)):
            foreign = store.CBRAnalysisAuthority(**{**asdict(self.authority), field: value})
            with self.subTest(field=field), self.assertRaises(WriteProofError):
                store.CBRAnalysisStore(foreign, self.folder)
        atomic_bytes(path, _json(dict(format=store.AUTHORITY_FORMAT, profile='mp3-vbr', **asdict(self.authority))))
        self.assertIsNone(store.load_authority(path, self.authority.database, '456'))
        legacy = wav.AnalysisAuthority(self.authority.database, '456', self.authority.schema_sha256, 'a' * 64, True)
        with self.assertRaises(WriteProofError):
            store.CBRAnalysisStore(legacy, self.folder)
        with self.assertRaises(WriteProofError):
            wav.load_plan(self.folder)
        with self.assertRaises(WriteProofError):
            self.prepare(self.root / 'existing-music', musical=True)

    def test_ctime_only_renewal_retains_original_durable_result_after_exact_byte_capture(self):
        source, authority, folder = self.prepare(self.root / 'ctime', ctime_drift=True)
        obj = store.CBRAnalysisStore(authority, folder); original = deepcopy(obj.plan['result'])
        self.assertNotEqual(original['epoch'], obj.plan['audio']['epoch'])
        self.assertEqual(store.load_plan(folder)['result'], original)
        self.assertEqual(json.loads((folder / 'intent.json').read_bytes())['result'], original)
        self.assertEqual(obj.apply(), '123'); self.assertEqual(self.state(source), obj.plan['after'])
        self.assertEqual(obj.plan['result'], original)

    def test_audio_drift_native_app_guards_and_unknown_shared_journal_fail_closed(self):
        obj = self.instance()
        packet = self.folder / 'ANLZ0000.DAT'; original = packet.read_bytes()
        packet.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
        with self.assertRaises(RecoveryRequired):
            store.load_plan(self.folder)
        packet.write_bytes(original)
        plan_path = self.folder / 'plan.json'; original_plan = plan_path.read_bytes()
        plan = json.loads(original_plan); plan['technical']['bits'] = 0
        atomic_bytes(plan_path, _json(plan))
        with mock.patch.object(store, '_connect') as sql, self.assertRaises(WriteProofError):
            store.load_plan(self.folder)
        sql.assert_not_called(); atomic_bytes(plan_path, original_plan)
        with mock.patch.object(store, 'assert_rekordbox_closed', side_effect=WriteProofError('owner open')), \
                mock.patch.object(obj.lease, '_connect') as sql, self.assertRaises(WriteProofError):
            obj.apply()
        sql.assert_not_called()
        audio = Path(obj.plan['audio']['path']); changed = audio.read_bytes() + b'external-change'; audio.write_bytes(changed)
        with self.assertRaises(ConflictError):
            obj.apply()
        self.assertEqual(audio.read_bytes(), changed); self.assertEqual(self.state(), obj.plan['before'])
        atomic_bytes(obj.journal, _json(dict(format=wav.FORMAT)))
        with self.assertRaises(RecoveryRequired):
            obj.recover()
        self.assertEqual(json.loads(obj.journal.read_bytes()), dict(format=wav.FORMAT))

    def test_interrupted_cbr_journal_blocks_other_domains_and_recovers_its_owned_files(self):
        obj = self.instance()
        def stop(phase):
            if phase == 'files-published':
                raise RuntimeError('interrupted')
        with mock.patch.object(wav, '_fault', side_effect=stop), self.assertRaises(RuntimeError):
            obj.apply()
        other = cues.PilotCueStore(cues.CueWriteAuthority(self.authority.database, '456', self.authority.schema_sha256, '123'), obj.root)
        with self.assertRaises(RecoveryRequired):
            other.snapshot()
        self.assertTrue(obj.journal.exists()); self.assertEqual(obj.recover(), 'rolled-back')
        self.assertEqual(self.state(), obj.plan['before']); self.assertFalse(obj._locations()[1].exists())
        self.assertEqual(obj.apply(), '123')

    def test_bounded_cbr_retention_preserves_its_first_latest_and_all_pilot_or_wav_archives(self):
        source, authority, folder = self.prepare(self.root / 'retention', extra=True)
        first = store.CBRAnalysisStore(authority, folder); first.apply(); store.retire_confirmed(first, [])
        untouched = first.root / 'cbr-analysis-pilot-reference'; untouched.mkdir(); (untouched / 'keep').write_bytes(b'pilot')
        wav_archive = first.root / 'analysis-unrelated'; wav_archive.mkdir(); (wav_archive / 'keep').write_bytes(b'wav')
        confirmed, objects = [first.plan['operation_id']], [first]
        from soundbox.rekordbox_write import _rows
        for tid in ('124', '125'):
            conn = _connect(source / 'master.db', readonly=True)
            try:
                row = next(row for row in _rows(conn, 'djmdContent') if row['ID'] == tid)
            finally:
                conn.close()
            path = Path(row['FolderPath']); scan = scan_mp3(path)
            result = {**first.plan['result'], 'path': str(path), 'epoch': list(scan.source_epoch)}
            self.render_images = self.images(path, result)
            operation = str(uuid4()); current = first.folder.parent / operation
            store.prepare(authority, tid, row['UUID'], result, current, data_root=Path(first.plan['data_root']),
                          operation_id=operation, renderer=self.renderer)
            obj = store.CBRAnalysisStore(authority, current); obj.apply(); store.retire_confirmed(obj, confirmed)
            confirmed.append(operation); objects.append(obj)
        self.assertTrue((objects[0].archive / 'before.db').exists())
        self.assertFalse((objects[1].archive / 'before.db').exists())
        self.assertTrue((objects[2].archive / 'before.db').exists())
        self.assertTrue(all((obj.archive / 'result.json').exists() and (obj.archive / 'ANLZ0000.DAT').exists() for obj in objects))
        self.assertEqual((untouched / 'keep').read_bytes(), b'pilot'); self.assertEqual((wav_archive / 'keep').read_bytes(), b'wav')

    def test_actual_process_exit_recovers_directory_and_sql_prefixes(self):
        script = '''
import os,sys
from pathlib import Path
from soundbox import rekordbox_cbr_analysis_store as s,rekordbox_analysis_store as w,rekordbox_cue_store as c
s._installed_version=lambda:s.NATIVE_VERSION;s.assert_rekordbox_closed=lambda:None
c._installed_version=lambda:s.NATIVE_VERSION;c._closed=lambda:None
folder=Path(sys.argv[1]);plan=s.load_plan(folder);authority=s.CBRAnalysisAuthority(**plan['authority']);obj=s.CBRAnalysisStore(authority,folder)
def crash(phase):
 if phase==sys.argv[2]:os._exit(73)
w._fault=crash;obj.apply()
'''
        for index, phase in enumerate(('files-published', 'sql-applied', 'sql-committed', 'receipt-ready')):
            with self.subTest(phase=phase):
                source, authority, folder = self.prepare(self.root / str(index)); obj = store.CBRAnalysisStore(authority, folder)
                process = subprocess.run([sys.executable, '-c', script, str(folder), phase], capture_output=True, text=True, timeout=40)
                self.assertEqual(process.returncode, 73, process.stderr)
                expected = 'committed' if phase in ('sql-committed', 'receipt-ready') else 'rolled-back'
                self.assertEqual(obj.recover(), expected)
                self.assertEqual(self.state(source), obj.plan['after' if expected == 'committed' else 'before'])
                self.assertFalse(obj.journal.exists())
