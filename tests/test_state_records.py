"""Per-song canonical persistence, recovery and portable-backup contracts."""
import copy
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock
import zipfile

from soundbox import analysis, backup, state_records
from soundbox.state_io import StateArea, StateChangedError
from soundbox.state_repository import StateRepository, portable_state, read_state, validate_state


def result(index=0, rows=8):
    path = f'/unavailable-fixture/{index}.wav'
    return dict(engine=analysis.ENGINE, path=path, sha256='a' * 64,
                size=100, epoch=[1, index + 1, 100, 4, 5], duration=600,
                beats=[[i % 4 + 1, i * 500, 120.0] for i in range(rows)],
                bpm=120.0, key='8A', first_cue=None)


def values(count=2):
    records = [result(i) for i in range(count)]
    return {'settings': {'analysis_results': {analysis.result_key(r['path']): r for r in records},
                         'waveform_zoom': 92.0}, 'custom': {'Set': ['1', '2']},
            'extensions': {'preserved': {'items': [1.0, True, -0.0]}}}


def files(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


class SongStateRecords(unittest.TestCase):
    def test_private_scalar_patch_reuses_owned_namespace_specs_rows_and_exact_costs(self):
        with tempfile.TemporaryDirectory() as td:
            repo=StateRepository(Path(td)/'library.json');repo.save(values());repo.backup()
            prior=repo._record_specs['analysis_results'];owned=repo._document['settings']['analysis_results']
            epochs=dict(repo._record_epochs)
            with (mock.patch.object(state_records,'entry_cost',side_effect=AssertionError('Private entry rebuilt')),
                  mock.patch.object(state_records,'capture',side_effect=AssertionError('Untouched epoch recaptured'))):
                repo.commit_patch(repo.prepare_patch({('settings','waveform_zoom'):184.0}))
            self.assertIs(repo._record_specs['analysis_results'],prior)
            self.assertIs(repo._document['settings']['analysis_results'],owned)
            self.assertEqual(repo._record_epochs,epochs)
            self.assertEqual(read_state(repo.path)['settings']['waveform_zoom'],184.0)
            # Loading computes the same exact cost before the private fast path.
            reloaded=StateRepository(repo.path);reloaded.load()
            with mock.patch.object(state_records,'entry_cost',side_effect=AssertionError('Loaded entry rebuilt')):
                reloaded.commit_patch(reloaded.prepare_patch({('settings','waveform_zoom'):92.0}))

    def test_generic_namespace_keeps_exact_entry_rows_but_detaches_changed_caller_records(self):
        with tempfile.TemporaryDirectory() as td:
            repo=StateRepository(Path(td)/'library.json');repo.save(values());repo.backup()
            incoming=repo.load();prior=repo._record_specs['analysis_results']
            items=list(incoming['settings']['analysis_results'].items())
            incoming['settings']['analysis_results']=dict(reversed(items))
            items[0][1]['key']='9A';repo.save(incoming)
            entries=repo._record_specs['analysis_results']['entries']
            self.assertIs(entries[0],prior['entries'][1]);self.assertIsNot(entries[1],prior['entries'][0])
            self.assertEqual([row[0] for row in entries],[items[1][0],items[0][0]])
            items[0][1]['key']='1A'
            repo.commit_patch(repo.prepare_patch({('settings','waveform_zoom'):184.0}))
            saved=read_state(repo.path)['settings']['analysis_results']
            self.assertEqual(saved[items[0][0]]['key'],'9A')
            self.assertEqual(list(saved),[items[1][0],items[0][0]])

    def test_postpublication_unrelated_tampering_never_receives_a_fresh_epoch(self):
        with tempfile.TemporaryDirectory() as td:
            repo=StateRepository(Path(td)/'library.json');repo.save(values(1));repo.backup()
            digest=next(iter(repo._record_epochs));record=state_records.record_path(repo.path,digest)
            prior=repo._record_epochs[digest];publish=repo.area.publish
            def tamper(changes):
                publish(changes)
                old=record.stat();record.write_bytes(record.read_bytes().replace(b'120.0',b'121.0'))
                os.utime(record,ns=(old.st_atime_ns,old.st_mtime_ns))
            with mock.patch.object(repo.area,'publish',side_effect=tamper):
                repo.commit_patch(repo.prepare_patch({('settings','waveform_zoom'):184.0}))
            self.assertEqual(repo._record_epochs[digest],prior)
            manifest=repo.path.read_bytes()
            with self.assertRaises(ValueError):repo.save({'custom':{'Refuse':[]}})
            self.assertEqual(repo.path.read_bytes(),manifest)

    def test_newly_published_record_requires_stable_bytes_and_matching_digest(self):
        with tempfile.TemporaryDirectory() as td:
            repo=StateRepository(Path(td)/'library.json');repo.save(values(1));repo.backup()
            publish=repo.area.publish;incoming=repo.load()
            next(iter(incoming['settings']['analysis_results'].values()))['key']='9A'
            def tamper(changes):
                publish(changes)
                for name,value in changes.items():
                    if name.startswith(state_records.DIRECTORY+'/') and isinstance(value,bytes):
                        (repo.path.parent/name).write_bytes(value.replace(b'9A',b'1A'))
            with mock.patch.object(repo.area,'publish',side_effect=tamper):
                with self.assertRaisesRegex(ValueError,'does not match'):repo.save(incoming)
            with self.assertRaises(StateChangedError):repo.save({'custom':{'Refuse':[]}})

    def test_private_namespace_cached_cost_still_enforces_size_and_record_count(self):
        from soundbox import state_repository
        with tempfile.TemporaryDirectory() as td:
            repo=StateRepository(Path(td)/'library.json');repo.save(values());repo.backup()
            before=files(repo.path.parent)
            for owner,name,budget in ((state_records,'MAX_RECORDS',1),(state_repository,'_MAX_STATE_BYTES',100)):
                with self.subTest(limit=name),mock.patch.object(owner,name,budget):
                    with self.assertRaises(ValueError):
                        repo.commit_patch(repo.prepare_patch({('settings','waveform_zoom'):184.0}))
                self.assertEqual(files(repo.path.parent),before)

    def test_alias_origins_and_next_pairs_survive_progress_backup_and_restore_without_rewriting(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);path=root/'library.json';repo=StateRepository(path)
            document={'artist_aliases':{'Variant':'Canonical'},'settings':{
                'source_truth_alias_targets':{'next':{'title':'Clean Title','artist':'Canonical'}},
                'source_truth_artist_origins':{'song':{'uuid':'retained-identity',
                    'unaliased_artist':'Variant','acknowledged_artist':'Canonical'}},
                'source_truth_alias_next':{'cursor':0}}}
            repo.save(document)
            repo.backup()
            self.assertEqual(read_state(next((root/'backups').glob('library-*.json'))),document)
            physical=json.loads(path.read_bytes())
            paths=[state_records.record_path(path,digest)
                   for namespace in ('source_truth_alias_targets','source_truth_artist_origins')
                   for _key,digest in physical['records'][namespace]['entries']]
            epochs=[state_records.identity(p) for p in paths]
            document['settings']['source_truth_alias_next']['cursor']=1
            repo.save(document)
            self.assertEqual([state_records.identity(p) for p in paths],epochs)
            portable=json.loads(portable_state(path))
            self.assertEqual(portable,document)
            restored=StateRepository(root/'restored/library.json');restored.save(portable)
            self.assertEqual(read_state(restored.path),document)

    def test_source_truth_targets_reuse_immutable_records_and_survive_portable_snapshot_restore(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);path=root/'library.json';repo=StateRepository(path)
            targets={'one':dict(local_id='1',title='Frozen Title',artist='Frozen Artist'),
                     'two':dict(local_id='2',title='Other Title',artist='Other Artist')}
            document={'settings':{'source_truth_targets':targets,'source_truth_migration':{'cursor':0}}}
            repo.save(document)
            physical=json.loads(path.read_bytes());references=physical['records']['source_truth_targets']['entries']
            record_paths=[state_records.record_path(path,digest) for _key,digest in references]
            identities=[state_records.identity(p) for p in record_paths]
            document['settings']['source_truth_migration']['cursor']=1;repo.save(document)
            self.assertEqual([state_records.identity(p) for p in record_paths],identities)
            portable=json.loads(portable_state(path));self.assertEqual(portable,document)
            restored=StateRepository(root/'restored/library.json');restored.save(portable)
            self.assertEqual(read_state(restored.path),document)

    def test_legacy_migration_keeps_logical_state_and_only_rewrites_changed_song(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / 'library.json'
            original = values()
            path.write_text(json.dumps(original))
            repo = StateRepository(path)
            incoming = repo.load()
            repo.save(incoming)
            self.assertEqual(read_state(path), original)
            physical = json.loads(path.read_bytes())
            with self.assertRaises(ValueError):
                validate_state(physical)
            references = physical['records']['analysis_results']['entries']
            first = root / state_records.DIRECTORY / f'{references[0][1]}.json'
            unchanged_inode = first.stat().st_ino
            key = references[1][0]
            incoming['settings']['analysis_results'][key]['key'] = '9A'
            repo.save(incoming)
            self.assertEqual(first.stat().st_ino, unchanged_inode)
            self.assertEqual(read_state(path), incoming)
            self.assertEqual(json.loads(portable_state(path)), incoming)
            self.assertEqual(len(list((root / state_records.DIRECTORY).glob('*.json'))), 2)
            old = next((root / 'backups').glob('library-*.json'))
            self.assertEqual(read_state(old), original)

    def test_scalar_save_does_not_revalidate_copy_or_encode_unchanged_song_records(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'library.json'
            repo = StateRepository(path)
            incoming = values()
            repo.save(incoming)
            repo.backup()
            encoded = state_records.encode
            detached = copy.deepcopy
            def encode(value):
                if isinstance(value, dict) and 'engine' in value:
                    raise AssertionError('Unchanged song reencoded')
                return encoded(value)
            def detach(value, *args):
                if isinstance(value, dict) and 'engine' in value:
                    raise AssertionError('Unchanged song detached again')
                return detached(value, *args)
            incoming['settings']['waveform_zoom'] = 184.0
            with (mock.patch.object(state_records, 'encode', side_effect=encode),
                  mock.patch.object(analysis, 'validate_result', side_effect=AssertionError('Unchanged song revalidated')),
                  mock.patch('soundbox.state_repository.copy.deepcopy', side_effect=detach)):
                repo.save(incoming)
            self.assertEqual(read_state(path), incoming)

    def test_caller_mutations_are_detached_and_newest_song_edits_persist(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'library.json'
            repo = StateRepository(path)
            incoming = values(1)
            repo.save(incoming)
            key, record = next(iter(incoming['settings']['analysis_results'].items()))
            record['beats'][0][2] = 128.0
            repo.save({'custom': {'Another': []}})
            self.assertEqual(read_state(path)['settings']['analysis_results'][key]['beats'][0][2], 120.0)
            repo.save({'settings': incoming['settings']})
            self.assertEqual(StateRepository(path).load()['settings']['analysis_results'][key]['beats'][0][2], 128.0)
            record['beats'][0][2] = 0
            before = files(path.parent)
            with self.assertRaises(ValueError):
                repo.save({'settings': incoming['settings']})
            self.assertEqual(files(path.parent), before)

    def test_another_repository_cannot_overwrite_acknowledged_changes(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'library.json'
            first = StateRepository(path)
            first.save(values())
            second = StateRepository(path)
            stale = second.load()
            first.save({'custom': {'Newest': ['3']}})
            with self.assertRaises(StateChangedError):
                second.save(stale)
            self.assertEqual(read_state(path)['custom'], {'Newest': ['3']})

    def test_read_state_rejects_a_manifest_changed_during_record_expansion(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'library.json'
            repo = StateRepository(path)
            repo.save(values())
            repo.backup()
            expand = state_records.expand
            def racing(source, physical):
                captured = expand(source, physical)
                repo.save({'custom': {'Newest': ['3']}})
                return captured
            with mock.patch.object(state_records, 'expand', side_effect=racing):
                with self.assertRaises(StateChangedError):
                    read_state(path)
            self.assertEqual(read_state(path)['custom'], {'Newest': ['3']})

    def test_shard_corruption_never_receives_freshness_from_restored_mtime(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'library.json'
            repo = StateRepository(path)
            repo.save(values(1))
            repo.backup()
            target = next((path.parent / state_records.DIRECTORY).glob('*.json'))
            old = target.stat()
            bad = target.read_bytes().replace(b'120.0', b'121.0')
            target.write_bytes(bad)
            os.utime(target, ns=(old.st_atime_ns, old.st_mtime_ns))
            before = files(path.parent)
            with self.assertRaises(ValueError):
                repo.save({'custom': {'Unsafe': []}})
            with self.assertRaises(ValueError):
                repo.backup()
            self.assertEqual(files(path.parent), before)
            restored = StateRepository(path)
            self.assertEqual(restored.load(), values(1))
            self.assertIsNotNone(restored.recovery)
            self.assertEqual(target.read_bytes(), bad)

    def test_epoch_inventory_rejects_record_directory_replacement_or_symlink_midpass(self):
        for redirect in (False,True):
            with self.subTest(redirect=redirect), tempfile.TemporaryDirectory() as td:
                root=Path(td);path=root/'library.json';repo=StateRepository(path)
                repo.save(values())
                folder=root/state_records.DIRECTORY
                prior=dict(repo._record_epochs);observed=state_records.identity;swapped=False
                def identity(record):
                    nonlocal swapped
                    result_value=observed(record)
                    if not swapped:
                        swapped=True;folder.rename(root/'old-records')
                        if redirect:folder.symlink_to(root/'old-records',target_is_directory=True)
                        else:shutil.copytree(root/'old-records',folder)
                    return result_value
                with mock.patch.object(state_records,'identity',side_effect=identity):
                    with self.assertRaisesRegex(ValueError,'directory changed'):
                        state_records.validate_epochs(path,prior)

    def test_internal_backup_reuse_cannot_hide_record_drift_before_publication(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);path=root/'library.json';repo=StateRepository(path)
            repo.save(values(1));repo.backup()
            manifest=path.read_bytes();record=next((root/state_records.DIRECTORY).glob('*.json'))
            original=repo._backup
            def corrupt(**kwargs):
                original(**kwargs)
                record.write_bytes(record.read_bytes().replace(b'120.0',b'121.0'))
            with mock.patch.object(repo,'_backup',side_effect=corrupt):
                with self.assertRaises(ValueError):repo.save({'custom':{'Unsafe':[]}})
            self.assertEqual(path.read_bytes(),manifest)
            self.assertFalse((root/'.state-transaction').exists())

    def test_publication_failure_rolls_back_changed_retired_records_and_manifest(self):
        for fail_at in range(1, 3):
            with self.subTest(boundary=fail_at), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                path = root / 'library.json'
                repo = StateRepository(path)
                incoming = values(1)
                repo.save(incoming)
                repo.backup()
                before = files(root)
                next(iter(incoming['settings']['analysis_results'].values()))['key'] = '9A'
                original_replace = os.replace
                seen = 0
                def replace(source, target):
                    nonlocal seen
                    source, target = Path(source), Path(target)
                    if source.parent.name == '.state-transaction' and source.name.startswith('new-'):
                        seen += 1
                        if seen == fail_at:
                            raise OSError('interrupted record publication')
                    return original_replace(source, target)
                with mock.patch('soundbox.state_io.os.replace', side_effect=replace):
                    with self.assertRaises(OSError):
                        repo.save(incoming)
                    self.assertEqual(files(root), before)

    def test_process_loss_recovers_every_record_retirement_manifest_and_commit_boundary(self):
        from soundbox import state_io
        class ProcessLoss(BaseException):
            pass
        for boundary in ('record', 'retirement', 'manifest', 'commit'):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                path = root / 'library.json'
                repo = StateRepository(path)
                incoming = values(1)
                repo.save(incoming)
                repo.backup()
                before = files(root)
                next(iter(incoming['settings']['analysis_results'].values()))['key'] = '9A'
                replace, remove, atomic = os.replace, state_io._remove, state_io.atomic_bytes
                observed = []
                def crash(label):
                    observed.append(label)
                    if boundary == label:
                        raise ProcessLoss(label)
                def displaced(source, target):
                    result_value = replace(source, target)
                    source, target = Path(source), Path(target)
                    if source.parent.name == '.state-transaction' and source.name.startswith('new-'):
                        crash('manifest' if target.name == 'library.json' else 'record')
                    return result_value
                def retired(target):
                    result_value = remove(target)
                    if Path(target).parent.name == state_records.DIRECTORY:
                        crash('retirement')
                    return result_value
                def durable(target, payload):
                    result_value = atomic(target, payload)
                    if Path(target).name == 'committed':
                        crash('commit')
                    return result_value
                with (mock.patch.object(state_io.os, 'replace', side_effect=displaced),
                      mock.patch.object(state_io, '_remove', side_effect=retired),
                      mock.patch.object(state_io, 'atomic_bytes', side_effect=durable)):
                    with self.assertRaises(ProcessLoss):
                        repo.save(incoming)
                self.assertEqual(observed[-1], boundary)
                StateArea(root)  # Fresh process owner resolves the retained journal.
                if boundary == 'commit':
                    self.assertEqual(read_state(path), incoming)
                    self.assertEqual(len(list((root / state_records.DIRECTORY).glob('*.json'))), 1)
                else:
                    self.assertEqual(files(root), before)
                    self.assertEqual(read_state(path), values(1))

    def test_completing_head_and_reordering_queue_reuses_untouched_song_records(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'library.json'
            repo = StateRepository(path)
            queue = [dict(id=str(i), grid=result(i, rows=500)['beats']) for i in range(3)]
            repo.save({'settings': {'pending_prep_edits': queue}})
            repo.backup()
            original_encode = state_records.encode
            def encode(value):
                if isinstance(value, dict) and 'grid' in value:
                    raise AssertionError('Untouched queued song reencoded after head removal/reorder')
                return original_encode(value)
            with mock.patch.object(state_records, 'encode', side_effect=encode):
                repo.save({'settings': {'pending_prep_edits': queue[1:]}})
                repo.save({'settings': {'pending_prep_edits': list(reversed(queue[1:]))}})
            self.assertEqual(read_state(path)['settings']['pending_prep_edits'], list(reversed(queue[1:])))

    def test_explicit_restore_repairs_corrupt_json_and_missing_or_corrupt_records(self):
        for damage in ('legacy', 'missing', 'corrupt'):
            with self.subTest(damage=damage), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                path = root / 'live/library.json'
                repo = StateRepository(path)
                if damage == 'legacy':
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b'broken crown-jewel JSON')
                else:
                    repo.save(values())
                    record = next((path.parent / state_records.DIRECTORY).glob('*.json'))
                    if damage == 'missing':
                        record.unlink()
                    else:
                        record.write_bytes(b'broken song data')
                old_physical = files(path.parent)
                source = root / 'selected'
                source.mkdir()
                (source / 'library.json').write_text('{"custom":{"Restored":[]}}')
                backup.restore_backup(path.parent, source)
                self.assertEqual(read_state(path), {'custom': {'Restored': []}})
                evidence = next((path.parent / 'backups').glob('pre-restore-*-damaged'))
                for name, payload in old_physical.items():
                    self.assertEqual((evidence / name).read_bytes(), payload)
                    if name.startswith(state_records.DIRECTORY):
                        self.assertEqual((path.parent / name).read_bytes(), payload)

    def test_backup_is_standalone_and_restore_fences_old_store_preserves_unknown_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / 'live/library.json'
            repo = StateRepository(path)
            incoming = values()
            repo.save(incoming)
            unowned = path.parent / state_records.DIRECTORY / 'preserved-evidence.txt'
            unowned.write_bytes(b'unrelated')
            archive = root / 'portable.zip'
            backup.export_backup(path.parent, archive)
            with zipfile.ZipFile(archive) as zipped:
                self.assertEqual(json.loads(zipped.read('library.json')), incoming)
                self.assertFalse(any(state_records.DIRECTORY in name for name in zipped.namelist()))
            source = root / 'old'
            source.mkdir()
            (source / 'library.json').write_text('{"custom":{"Restored":[]}}')
            backup.restore_backup(path.parent, source)
            self.assertEqual(read_state(path), {'custom': {'Restored': []}})
            self.assertEqual(unowned.read_bytes(), b'unrelated')
            self.assertEqual(list(unowned.parent.glob('*.json')), [])
            self.assertEqual(read_state(next((path.parent / 'backups').glob('pre-restore-*.json'))), incoming)
            with self.assertRaises(StateChangedError):
                repo.save(incoming)
            fresh = root / 'fresh'
            backup.restore_backup(fresh, archive)
            self.assertEqual(read_state(fresh / 'library.json'), incoming)

    def test_record_directory_redirect_and_manifest_namespace_tampering_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / 'library.json'
            repo = StateRepository(path)
            repo.save(values())
            physical = json.loads(path.read_bytes())
            physical['records']['unknown'] = physical['records'].pop('analysis_results')
            path.write_text(json.dumps(physical))
            with self.assertRaises(ValueError):
                read_state(path)
            store = root / state_records.DIRECTORY
            store.rename(root / 'moved-records')
            store.symlink_to(root / 'moved-records', target_is_directory=True)
            with self.assertRaises(ValueError):
                state_records.capture(path, next(iter(repo._record_epochs)))

    def test_unhashable_manifest_digests_recover_instead_of_crashing_startup(self):
        for malformed in ([], {}, None, 1, True):
            with self.subTest(digest=malformed), tempfile.TemporaryDirectory() as td:
                path = Path(td) / 'library.json'
                repo = StateRepository(path)
                repo.save(values())
                repo.backup()
                physical = json.loads(path.read_bytes())
                physical['records']['analysis_results']['entries'][0][1] = malformed
                path.write_text(json.dumps(physical))
                damaged = path.read_bytes()
                reopened = StateRepository(path)
                self.assertEqual(reopened.load(), values())
                self.assertEqual(reopened.recovery.damaged.read_bytes(), damaged)

    def test_recovery_materializes_a_record_store_backup_before_relocating_it(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            history = root / 'backups/library.json'
            history_repo = StateRepository(history)
            history_repo.save(values())
            history.rename(history.with_name('stored-history.json'))
            path = root / 'library.json'
            path.write_bytes(b'broken current state')
            reopened = StateRepository(path)
            self.assertEqual(reopened.load(), values())
            self.assertEqual(json.loads(path.read_bytes()), values())
            self.assertFalse((root / state_records.DIRECTORY).exists())
            self.assertEqual(reopened.recovery.backup.name, 'stored-history.json')

    def test_migration_size_budget_counts_inline_metadata_once(self):
        from soundbox import state_repository
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'library.json'
            incoming = values(1)
            incoming['extensions']['opaque'] = 'x' * 2400
            portable = state_records.encode(incoming)
            budget = len(portable) + 512
            path.write_bytes(portable)
            with (mock.patch.object(state_records, 'MAX_BYTES', budget),
                  mock.patch.object(state_repository, '_MAX_STATE_BYTES', budget)):
                repo = StateRepository(path)
                self.assertEqual(repo.load(), incoming)
                repo.save(incoming)
                self.assertLessEqual(path.stat().st_size, budget)
                self.assertEqual(portable_state(path), portable)
                self.assertEqual(StateRepository(path).load(), incoming)

    def test_portable_legacy_capture_and_backup_use_bounded_reads_and_preserve_numeric_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / 'state/library.json'
            path.parent.mkdir()
            original = b'{ "extension": {"flag": true, "integer": 1, "real": 1.0, "zero": -0.0} }'
            path.write_bytes(original)
            archive = root / 'portable.zip'
            with mock.patch.object(Path, 'read_bytes', side_effect=AssertionError('Unbounded state capture')):
                self.assertEqual(portable_state(path), original)
                backup.export_backup(path.parent, archive)
            with zipfile.ZipFile(archive) as zipped:
                self.assertEqual(zipped.read('library.json'), original)
