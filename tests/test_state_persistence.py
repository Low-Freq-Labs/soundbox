"""Disposable failure/race fixtures for canonical state; never live AppData."""
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock
import zipfile

from soundbox import backup
from soundbox.artfetch import ArtCache, ArtResult
from soundbox.state_io import StateArea, StateChangedError, state_area
from soundbox.state_repository import InvalidState, StateRepository, validate_state


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes()
            for p in root.rglob('*') if p.is_file()
            and '.state-transaction' not in p.parts}


def fixture(root, name):
    root.mkdir(parents=True)
    (root / 'library.json').write_text(json.dumps({'custom': {name: ['1']}}))
    (root / 'art').mkdir()
    (root / 'art' / 'index.json').write_text(
        json.dumps({'1': {'status': 'ok', 'ext': 'jpg'}}))
    (root / 'art' / '1.jpg').write_bytes(name.encode())
    (root / 'backups').mkdir()
    (root / 'backups' / 'library-old.json').write_text('{}')
    return root


class RestoreTransactions(unittest.TestCase):
    def test_every_rename_boundary_rolls_back_exact_bytes(self):
        # library, history, artwork: old-away and new-in for each publication.
        for failure_at in range(1, 7):
            with self.subTest(boundary=failure_at), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                live = fixture(root / 'live', 'old')
                src = fixture(root / 'source', 'new')
                before = snapshot(live)
                original = os.replace
                count = 0

                def fail(source, dest):
                    nonlocal count
                    source, dest = Path(source), Path(dest)
                    if ((source.parent.name == '.state-transaction' and source.name.startswith('new-'))
                            or (dest.parent.name == '.state-transaction' and dest.name.startswith('old-'))):
                        count += 1
                        if count == failure_at:
                            raise OSError('injected publication failure')
                    return original(source, dest)

                with mock.patch('soundbox.state_io.os.replace', side_effect=fail):
                    with self.assertRaises(OSError):
                        backup.restore_backup(live, src)
                self.assertEqual(snapshot(live), before)
                self.assertFalse((live / '.state-transaction').exists())

    def test_interrupted_publication_recovers_on_new_owner(self):
        class Crash(BaseException):
            pass
        for failure_at in range(1, 7):
            with self.subTest(boundary=failure_at), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                live = fixture(root / 'live', 'old')
                src = fixture(root / 'source', 'new')
                before = snapshot(live)
                original = os.replace
                count = 0

                def crash(source, dest):
                    nonlocal count
                    result = original(source, dest)
                    source, dest = Path(source), Path(dest)
                    if ((source.parent.name == '.state-transaction' and source.name.startswith('new-'))
                            or (dest.parent.name == '.state-transaction' and dest.name.startswith('old-'))):
                        count += 1
                        if count == failure_at:
                            raise Crash()
                    return result

                with mock.patch('soundbox.state_io.os.replace', side_effect=crash):
                    with self.assertRaises(Crash):
                        backup.restore_backup(live, src)
                StateArea(live).recover()
                self.assertEqual(snapshot(live), before)

    def test_failed_rollback_retains_recovery_and_blocks_more_writes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            live = fixture(root / 'live', 'old')
            src = fixture(root / 'source', 'new')
            before = snapshot(live)
            original = os.replace

            def fail(source, dest):
                if Path(source).parent.name == '.state-transaction' and Path(source).name.startswith(('new-', 'old-')):
                    raise OSError('device full')
                return original(source, dest)

            with mock.patch('soundbox.state_io.os.replace', side_effect=fail):
                with self.assertRaises(OSError):
                    backup.restore_backup(live, src)
            self.assertTrue((live / '.state-transaction').exists())
            with self.assertRaisesRegex(OSError, 'recovery is pending'):
                state_area(live).publish({'library.json': b'{}'})
            StateArea(live).recover()
            self.assertEqual(snapshot(live), before)

    def test_stage_failure_and_bad_input_leave_live_state_untouched(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            live = fixture(root / 'live', 'old')
            src = fixture(root / 'source', 'new')
            before = snapshot(live)
            with mock.patch('soundbox.backup.copy_state', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    backup.restore_backup(live, src)
            self.assertEqual(snapshot(live), before)
            (src / 'art' / '1.jpg').unlink()
            with self.assertRaises(ValueError):
                backup.restore_backup(live, src)
            self.assertEqual(snapshot(live), before)

    def test_archive_bounds_links_traversal_and_duplicates_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            live = fixture(root / 'live', 'old')
            before = snapshot(live)
            for name in ('../escape', '/absolute', 'art/../x', 'art\\x', 'art//x'):
                zpath = root / 'bad.zip'
                with zipfile.ZipFile(zpath, 'w') as z:
                    z.writestr('library.json', '{}')
                    z.writestr(name, 'bad')
                with self.assertRaises(ValueError):
                    backup.restore_backup(live, zpath)
            zpath = root / 'bad.zip'
            with zipfile.ZipFile(zpath, 'w') as z:
                z.writestr('library.json', '{}')
                link = zipfile.ZipInfo('art/link')
                link.create_system = 3
                link.external_attr = 0o120777 << 16
                z.writestr(link, '../../elsewhere')
            with self.assertRaises(ValueError):
                backup.restore_backup(live, zpath)
            with zipfile.ZipFile(zpath, 'w') as z:
                z.writestr('library.json', '{}')
                z.writestr('art/X', 'a')
                z.writestr('art/x', 'b')
            with self.assertRaises(ValueError):
                backup.restore_backup(live, zpath)
            with mock.patch('soundbox.backup._MAX_BYTES', 1):
                with self.assertRaises(ValueError):
                    backup.restore_backup(live, zpath)
            src = fixture(root / 'source', 'new')
            (src / 'art' / 'bad').symlink_to(root / 'absent')
            with self.assertRaises(ValueError):
                backup.restore_backup(live, src)
            self.assertEqual(snapshot(live), before)

    def test_success_fences_stale_artwork_and_library_writers(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            live = fixture(root / 'live', 'old')
            src = fixture(root / 'source', 'new')
            cache = ArtCache(live / 'art')
            repo = StateRepository(live / 'library.json')
            repo.load()
            backup.restore_backup(live, src)
            after = snapshot(live)
            with self.assertRaises(StateChangedError):
                cache.store_none('late')
            with self.assertRaises(StateChangedError):
                repo.save({'custom': {'stale': []}})
            self.assertEqual(snapshot(live), after)
            self.assertEqual(repo.load()['custom'], {'new': ['1']})

    def test_existing_zip_survives_compression_failure(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            live = fixture(root / 'live', 'old')
            dest = root / 'backup.zip'
            dest.write_bytes(b'existing backup')
            with mock.patch.object(zipfile.ZipFile, 'write', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    backup.export_backup(live, dest)
            self.assertEqual(dest.read_bytes(), b'existing backup')


class ArtworkPublication(unittest.TestCase):
    def test_concurrent_instances_serialize_without_lost_updates(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / 'art'
            caches = [ArtCache(root), ArtCache(root)]
            barrier = threading.Barrier(3)
            errors = []

            def write(cache, tid):
                try:
                    barrier.wait()
                    cache.store_none(tid)
                except Exception as exc:
                    errors.append(exc)
            workers = [threading.Thread(target=write, args=(cache, str(i)))
                       for i, cache in enumerate(caches)]
            for worker in workers:
                worker.start()
            barrier.wait()
            for worker in workers:
                worker.join(5)
                self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(set(ArtCache(root).index), {'0', '1'})

    def test_image_index_and_memory_rollback_together(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / 'art'
            cache = ArtCache(root)
            cache.store_ok('1', ArtResult(b'old', 'image/jpeg', 1.0, 'local', 'old'))
            before = snapshot(root)
            index = json.loads(json.dumps(cache.index))
            original = os.replace

            def fail(source, dest):
                if Path(source).name.startswith('new-') and Path(dest).name == 'index.json':
                    raise OSError('index failed')
                return original(source, dest)
            with mock.patch('soundbox.state_io.os.replace', side_effect=fail):
                with self.assertRaises(OSError):
                    cache.store_ok('1', ArtResult(b'new', 'image/jpeg', 1.0, 'local', 'new'))
            self.assertEqual(cache.index, index)
            self.assertEqual(snapshot(root), before)
            with mock.patch('soundbox.state_io.os.replace', side_effect=fail):
                with self.assertRaises(OSError):
                    cache.block('1')
            self.assertEqual(cache.index, index)
            self.assertEqual(snapshot(root), before)

    def test_backup_blocks_art_publication_until_snapshot_complete(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            live = fixture(root / 'live', 'old')
            cache = ArtCache(live / 'art')
            entered, release, written = threading.Event(), threading.Event(), threading.Event()
            original = backup.copy_state
            errors = []

            def pause(source, dest):
                if Path(source).name == 'library.json':
                    entered.set()
                    if not release.wait(5):
                        raise TimeoutError('test coordination')
                return original(source, dest)

            def run_backup():
                try:
                    backup.export_backup(live, root / 'copy.zip')
                except Exception as exc:
                    errors.append(exc)

            def write():
                cache.block('1')
                written.set()

            with mock.patch('soundbox.backup.copy_state', side_effect=pause):
                copier = threading.Thread(target=run_backup)
                copier.start()
                self.assertTrue(entered.wait(5))
                writer = threading.Thread(target=write)
                writer.start()
                try:
                    self.assertFalse(written.wait(.05))
                finally:
                    release.set()
                    copier.join(5)
                    writer.join(5)
            self.assertEqual(errors, [])
            with zipfile.ZipFile(root / 'copy.zip') as z:
                self.assertEqual(z.read('art/1.jpg'), b'old')
                self.assertEqual(json.loads(z.read('art/index.json'))['1']['status'], 'ok')
            self.assertEqual(cache.status('1'), 'blocked')


class LibraryRepository(unittest.TestCase):
    def test_load_detaches_once_and_public_validation_still_detaches(self):
        from soundbox import state_repository
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'library.json'
            path.write_text(json.dumps({'custom': {'Set': ['1']},
                                        'extensions': {'nested': ['original']}}))
            repo = StateRepository(path)
            detach = state_repository.copy.deepcopy
            with mock.patch.object(state_repository.copy, 'deepcopy', wraps=detach) as copies:
                loaded = repo.load()
            self.assertEqual(copies.call_count, 1)
            loaded['custom']['Set'].append('late')
            loaded['extensions']['nested'].append('late')
            repo.save({'settings': {}})
            saved = json.loads(path.read_bytes())
            self.assertEqual(saved['custom']['Set'], ['1'])
            self.assertEqual(saved['extensions']['nested'], ['original'])
            checked = validate_state(saved)
            checked['custom']['Set'].append('detached')
            self.assertEqual(saved['custom']['Set'], ['1'])

    def test_exact_noop_avoids_full_copy_encoding_and_publication(self):
        from soundbox import state_repository
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'library.json'
            repo = StateRepository(path)
            values = {'custom': {'Set': [str(i) for i in range(1000)]},
                      'extensions': {'ordered': {'one': 1.0, 'two': True}}}
            repo.save(values)
            before = path.read_bytes()
            with (
                mock.patch.object(state_repository, 'validate_state',
                                  side_effect=AssertionError('No-op revalidated')),
                mock.patch.object(state_repository.json, 'dumps',
                                  side_effect=AssertionError('No-op reencoded')),
                mock.patch.object(repo.area, 'publish',
                                  side_effect=AssertionError('No-op republished')),
            ):
                repo.save(values)
            self.assertEqual(path.read_bytes(), before)
            values['custom']['Set'].append('after-save')
            repo.save({'settings': {}})
            self.assertNotIn('after-save', json.loads(path.read_bytes())['custom']['Set'])

    def test_noop_hint_preserves_json_types_zero_sign_and_object_order(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'library.json'
            repo = StateRepository(path)
            repo.save({'settings': {'hide_untracked': True},
                       'custom': {'First': [], 'Second': []},
                       'extension': {'number': 1.0, 'zero': 0.0}})
            with self.assertRaises(InvalidState):
                repo.save({'settings': {'hide_untracked': 1}})
            repo.save({'custom': {'Second': [], 'First': []}})
            payload = path.read_text()
            self.assertLess(payload.index('"Second"'), payload.index('"First"'))
            repo.save({'extension': {'number': 1, 'zero': -0.0}})
            payload = path.read_text()
            self.assertLess(payload.index('"Second"'), payload.index('"First"'))
            self.assertIn('"number": 1,', payload)
            self.assertIn('"zero": -0.0', payload)

    def test_noop_hint_does_not_hide_missing_or_replaced_state(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'library.json'
            repo = StateRepository(path)
            values = {'custom': {'Set': ['1']}}
            repo.save(values)
            before = path.read_bytes()
            path.unlink()
            repo.save(values)
            self.assertEqual(path.read_bytes(), before)
            replacement = path.with_name('replacement.json')
            replacement.write_bytes(before.replace(b'"1"', b'"2"'))
            replacement.replace(path)
            repo.save(values)
            self.assertEqual(path.read_bytes(), before)

    def test_backup_reuses_only_exact_previously_validated_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'library.json'
            repo = StateRepository(path)
            repo.save({'custom': {'Set': ['1', '2']},
                       'extensions': {'opaque': {'preserved': [1, 2]}}})
            expected = path.read_bytes()
            reads = []
            original_read = Path.read_bytes

            def read(current):
                if current == path:
                    reads.append(current)
                return original_read(current)

            with mock.patch.object(Path, 'read_bytes', read), mock.patch(
                    'soundbox.state_repository.validate_state',
                    side_effect=AssertionError('Exact saved bytes were revalidated')):
                repo.backup()
            self.assertEqual(len(reads), 1)
            self.assertEqual(path.read_bytes(), expected)
            copies = list((path.parent / 'backups').glob('library-*.json'))
            self.assertEqual(len(copies), 1)
            self.assertEqual(copies[0].read_bytes(), expected)

    def test_recent_backup_never_hides_same_size_corruption(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'library.json'
            repo = StateRepository(path)
            repo.save({'custom': {'Set': ['1', '2']}})
            repo.backup()
            old = path.stat()
            corrupt = path.read_bytes().replace(b'"custom"', b'"custo!"').replace(
                b'"Set": [', b'"Set": {', 1)
            self.assertEqual(len(corrupt), old.st_size)
            path.write_bytes(corrupt)
            os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns))
            with self.assertRaises(ValueError):
                repo.backup()
            self.assertEqual(path.read_bytes(), corrupt)
            self.assertEqual(len(list((path.parent / 'backups').glob('library-*.json'))), 1)

    def test_changed_valid_backup_bytes_still_receive_full_validation(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'library.json'
            repo = StateRepository(path)
            repo.save({'custom': {'Set': ['1']}})
            changed = path.read_bytes().replace(b'"1"', b'"2"')
            path.write_bytes(changed)
            with mock.patch('soundbox.state_repository.validate_state',
                            wraps=validate_state) as validate:
                repo.backup()
            validate.assert_called_once()
            self.assertEqual(path.read_bytes(), changed)
            copies = list((path.parent / 'backups').glob('library-*.json'))
            self.assertEqual(len(copies), 1)
            self.assertEqual(copies[0].read_bytes(), changed)

    def test_schema_and_extension_roundtrip(self):
        for data in ([], None, {'custom': []}, {'custom': {'bad': 2}},
                     {'hidden_tracks': 42}, {'imported': {'1': False}},
                     {'artist_overrides': {'1': []}}):
            with self.subTest(data=data), self.assertRaises(InvalidState):
                validate_state(data)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'library.json'
            original = {'extensions': {'provider.v1': {'enabled': True, 'opaque': [3]}},
                        'unknown_future': {'nested': None}, 'custom': {'Old': ['1']}}
            path.write_text(json.dumps(original))
            repo = StateRepository(path)
            repo.load()
            repo.save({'custom': {'New': ['2']}})
            saved = json.loads(path.read_text())
            self.assertEqual(saved, {**original, 'custom': {'New': ['2']}})

    def test_invalid_shape_recovers_newest_valid_backup(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / 'library.json'
            path.write_text('[]')
            (root / 'backups').mkdir()
            (root / 'backups' / 'good.json').write_text('{"custom":{"Recovered":["1"]}}')
            (root / 'backups' / 'bad.json').write_text('{"custom":42}')
            repo = StateRepository(path)
            self.assertEqual(repo.load()['custom'], {'Recovered': ['1']})
            self.assertEqual(repo.recovery.damaged.read_text(), '[]')
            self.assertEqual(repo.recovery.backup.name, 'good.json')

    def test_no_valid_backup_disables_autosave_preserves_corrupt_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'library.json'
            path.write_text('not json')
            repo = StateRepository(path)
            self.assertEqual(repo.load(), {})
            with self.assertRaises(OSError):
                repo.save({'custom': {}})
            self.assertEqual(path.read_text(), 'not json')
            self.assertEqual(repo.recovery.damaged.read_text(), 'not json')

    def test_durability_failure_does_not_publish_new_state(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'library.json'
            path.write_text('{"custom":{"Old":["1"]}}')
            repo = StateRepository(path)
            repo.load()
            before = path.read_bytes()
            with mock.patch('soundbox.state_io.os.fsync', side_effect=OSError('disk failed')):
                with self.assertRaises(OSError):
                    repo.save({'custom': {}})
            self.assertEqual(path.read_bytes(), before)


class PayloadDurability(unittest.TestCase):
    def test_fsync_precedes_publication_and_failure_preserves_vacancy(self):
        from soundbox.sync import _safe_copy
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src, dest = root / 'source', root / 'target'
            src.write_bytes(b'payload')
            events = []
            replace = os.replace
            fsync = os.fsync
            with mock.patch('soundbox.sync.os.fsync', side_effect=lambda fd: (events.append('flush'), fsync(fd))[-1]), \
                    mock.patch('soundbox.sync.os.replace', side_effect=lambda a, b: (events.append('publish'), replace(a, b))[-1]):
                _safe_copy(src, dest)
            self.assertEqual(events, ['flush', 'publish'])
            self.assertEqual(dest.read_bytes(), src.read_bytes())
            dest.unlink()
            with mock.patch('soundbox.sync.os.fsync', side_effect=OSError('flush failed')):
                with self.assertRaises(OSError):
                    _safe_copy(src, dest)
            self.assertFalse(dest.exists())
            self.assertFalse(dest.with_name('target.sbpart').exists())
            self.assertEqual(src.read_bytes(), b'payload')


class LocalArtworkBoundary(unittest.TestCase):
    def test_local_artwork_works_without_provider_imports_or_network(self):
        import subprocess
        import sys
        script = '''
import importlib.abc
import socket
import sys
import tempfile
from pathlib import Path
class NoProvider(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in ("soundbox.artfetch", "yt_dlp") or fullname.startswith("yt_dlp."):
            raise AssertionError("provider imported: " + fullname)
sys.meta_path.insert(0, NoProvider())
def denied(*args, **kwargs):
    raise AssertionError("network attempted")
socket.create_connection = denied
from soundbox.artwork import ArtCache, ArtResult
with tempfile.TemporaryDirectory() as td:
    cache = ArtCache(Path(td) / "art")
    cache.store_ok("local", ArtResult(b"image", "image/jpeg", 1.0, "file:///fixture", "fixture"))
    assert cache.image_path("local").read_bytes() == b"image"
    cache.block("local")
    assert cache.status("local") == "blocked"
'''
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


class JournalRetirement(unittest.TestCase):
    def test_crash_during_committed_cleanup_never_rolls_back_a_subset(self):
        class Crash(BaseException):
            pass
        from soundbox import state_io
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            area = StateArea(root)
            (root / 'library.json').write_bytes(b'old library')
            (root / 'art').mkdir()
            (root / 'art' / 'index.json').write_bytes(b'old art')
            original = state_io._remove

            def crash(path):
                if path == area.retired:
                    (path / 'committed').unlink()
                    (path / 'old-0').unlink()
                    raise Crash()
                return original(path)
            with mock.patch('soundbox.state_io._remove', side_effect=crash):
                with self.assertRaises(Crash):
                    area.publish({'library.json': b'new library', 'art/index.json': b'new art'})
            StateArea(root).recover()
            self.assertEqual((root / 'library.json').read_bytes(), b'new library')
            self.assertEqual((root / 'art' / 'index.json').read_bytes(), b'new art')
            self.assertFalse(area.retired.exists())
