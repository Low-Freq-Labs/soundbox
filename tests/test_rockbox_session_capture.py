"""Generated-only root-identity and complete source-boundary regressions."""
import json
import io
import os
import argparse
from contextlib import redirect_stdout
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from soundbox import manifest, sync
from soundbox.library import Library, Playlist, Track


class RockboxOperationSession(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.library_root = self.root / 'library'
        self.library_root.mkdir()
        self.source = self.library_root / 'generated.mp3'
        self.source.write_bytes(b'generated source copied without decoding')
        self.source_before = self.source.read_bytes()
        self.catalog = self.library_root / 'source.xml'
        self.catalog.write_bytes(b'generated catalog identity')
        self.playlist = Playlist('Favorites', [], ['one'], pid='favorites')
        self.library = Library({'one': Track('one', 'One', 'Fixture', self.source.as_uri())},
                               [self.playlist], self.catalog)
        self.target = self.root / 'iPod'
        self.target.mkdir()
        self.replacement = self.root / 'replacement'
        self.before = None

    def _export(self):
        return sync.run(self.library, [self.playlist], self.target,
                        layout='tree', dry_run=False, derive_artists=False)

    @staticmethod
    def _tree(root):
        return {str(path.relative_to(root)): path.read_bytes() if path.is_file() else None
                for path in root.rglob('*')}

    def _prepare_replacement(self, schema=2, copy_original=False):
        if copy_original:
            shutil.copytree(self.target, self.replacement)
        else:
            self.replacement.mkdir()
            state = manifest.manifest_path(self.replacement)
            state.parent.mkdir()
            state.write_text(json.dumps({
                'schema': schema, 'generation': 37,
                'profile': manifest.COMPOSITE_EXPORT_PROFILE if schema == 3 else manifest.ROCKBOX_EXPORT_PROFILE,
                'playlists': {'foreign': {'name': 'Foreign', 'tracks': ['foreign']}},
                'files': {},
            }))
        (self.replacement / 'foreign-marker').write_bytes(b'foreign original bytes')
        clutter = self.replacement / 'Playlists/.foreign.sbpart'
        clutter.parent.mkdir(exist_ok=True)
        clutter.write_bytes(b'foreign operation incomplete bytes')
        self.before = self._tree(self.replacement)

    def _pivot(self):
        self.target.rename(self.root / 'retained-original')
        self.replacement.rename(self.target)

    def _assert_replacement_unchanged(self):
        self.assertEqual(self._tree(self.target), self.before)
        self.assertEqual(self.source.read_bytes(), self.source_before)

    def _publication_pivot(self, schema):
        self._prepare_replacement(schema)
        original = manifest.record_export

        def publish(*args, **kwargs):
            self._pivot()
            return original(*args, **kwargs)

        with mock.patch.object(manifest, 'record_export', side_effect=publish):
            with self.assertRaisesRegex(RuntimeError, 'remounted'):
                self._export()
        self._assert_replacement_unchanged()

    def test_run_publication_never_adopts_replacement_schema2(self):
        self._publication_pivot(2)

    def test_run_publication_never_overwrites_replacement_schema3(self):
        self._publication_pivot(3)

    def test_initial_manifest_read_keeps_root_through_source_plan(self):
        self._prepare_replacement()
        original = manifest.load
        called = []

        def read(*args, **kwargs):
            result = original(*args, **kwargs)
            if not called:
                called.append(True)
                self._pivot()
            return result

        with mock.patch.object(manifest, 'load', side_effect=read):
            with self.assertRaisesRegex(RuntimeError, 'remounted'):
                self._export()
        self.assertEqual(called, [True])
        self._assert_replacement_unchanged()

    def test_resync_passes_original_root_into_nested_export(self):
        self._export()
        self.playlist.name = 'Renamed'
        self._prepare_replacement(copy_original=True)
        original = sync.run

        def export(*args, **kwargs):
            self._pivot()
            return original(*args, **kwargs)

        with mock.patch.object(sync, 'run', side_effect=export):
            with self.assertRaisesRegex(RuntimeError, 'remounted'):
                sync.resync(self.library, self.target)
        self._assert_replacement_unchanged()

    def test_final_prune_keeps_root_from_before_manifest_capture(self):
        self._export()
        self.library.playlists = []
        self._prepare_replacement(copy_original=True)
        original = manifest.load
        calls = []

        def read(*args, **kwargs):
            result = original(*args, **kwargs)
            calls.append(True)
            if len(calls) == 2:
                self._pivot()
            return result

        with mock.patch.object(manifest, 'load', side_effect=read):
            with self.assertRaisesRegex(RuntimeError, 'remounted'):
                sync.resync(self.library, self.target)
        self.assertEqual(len(calls), 2)
        self._assert_replacement_unchanged()

    def test_final_clutter_cleanup_never_adopts_replacement(self):
        self._export()
        self.playlist.name = 'Renamed'
        self._prepare_replacement(copy_original=True)
        original = sync.clean_applefiles

        def clean(*args, **kwargs):
            self._pivot()
            return original(*args, **kwargs)

        with mock.patch.object(sync, 'clean_applefiles', side_effect=clean):
            with self.assertRaisesRegex(RuntimeError, 'remounted'):
                sync.resync(self.library, self.target)
        self._assert_replacement_unchanged()

    def test_prune_kernel_unlink_uses_original_parent_after_root_replacement(self):
        self._export()
        self.library.playlists = []
        self._prepare_replacement(copy_original=True)
        original = os.unlink
        pivots = []

        def unlink(path, *args, **kwargs):
            if Path(path).name == 'One.mp3' and not pivots:
                self._pivot()
                pivots.append(True)
            return original(path, *args, **kwargs)

        with mock.patch.object(os, 'unlink', side_effect=unlink):
            with self.assertRaisesRegex(RuntimeError, 'remounted'):
                sync.resync(self.library, self.target)
        self.assertEqual(pivots, [True])
        self._assert_replacement_unchanged()

    def test_direct_record_export_keeps_its_original_read_session(self):
        self._prepare_replacement()
        original = manifest.load

        def read(*args, **kwargs):
            result = original(*args, **kwargs)
            self._pivot()
            return result

        with mock.patch.object(manifest, 'load', side_effect=read):
            with self.assertRaisesRegex(RuntimeError, 'remounted'):
                manifest.record_export(self.target, [self.playlist])
        self._assert_replacement_unchanged()

    def test_supplied_manifest_session_cannot_target_another_root(self):
        self._prepare_replacement()
        session = sync.DeviceSession(self.target, [self.source], create=False)
        for action in (manifest.load, manifest.has_composite_ownership):
            with self.subTest(action=action.__name__):
                with self.assertRaisesRegex(ValueError, 'differs'):
                    action(self.replacement, session=session)
        with self.assertRaisesRegex(RuntimeError, 'differs'):
            manifest.save(self.replacement, {}, session=session)
        self.assertEqual(self._tree(self.replacement), self.before)

    def test_raw_library_rejects_all_source_boundaries_before_device_reads(self):
        hidden = Track('hidden', 'Hidden', 'Fixture', (self.library_root / 'hidden.mp3').as_uri())
        self.library.tracks_by_id['hidden'] = hidden
        self.library.hidden_ids.add('hidden')
        for boundary in ('catalog', 'hidden_media', 'hidden_analysis', 'hidden_native'):
            self.library.source = self.catalog
            hidden.location_uri = (self.library_root / 'hidden.mp3').as_uri()
            hidden.source_analysis_path = ''
            hidden.source_native_path = ''
            if boundary == 'catalog':
                self.library.source = self.target / 'source.xml'
            elif boundary == 'hidden_media':
                hidden.location_uri = (self.target / 'hidden.mp3').as_uri()
            elif boundary == 'hidden_analysis':
                hidden.source_analysis_path = str(self.target / 'ANLZ0000.DAT')
            else:
                hidden.source_native_path = str(self.target / 'native.mp3')
            for action in ('run', 'resync'):
                with self.subTest(boundary=boundary, action=action):
                    with mock.patch.object(manifest, 'has_composite_ownership',
                                           side_effect=AssertionError('unsafe target read')):
                        with self.assertRaisesRegex(RuntimeError, 'overlaps source'):
                            self._export() if action == 'run' else sync.resync(self.library, self.target)
                    self.assertEqual(self._tree(self.target), {})

    def test_missing_export_root_cannot_appear_during_source_plan(self):
        self.target.rmdir()
        self._prepare_replacement()
        original = sync.plan

        def plan(*args, **kwargs):
            result = original(*args, **kwargs)
            self.replacement.rename(self.target)
            return result

        with mock.patch.object(sync, 'plan', side_effect=plan):
            with self.assertRaisesRegex(RuntimeError, 'appeared during'):
                self._export()
        self._assert_replacement_unchanged()

    def test_missing_resync_root_is_never_recreated_as_an_empty_success(self):
        self.target.rmdir()
        with self.assertRaisesRegex(RuntimeError, 'no longer mounted'):
            sync.resync(self.library, self.target)
        self.assertFalse(self.target.exists())

    def test_engine_export_cleanup_uses_reported_original_mount_identity(self):
        from soundbox import engine
        original = sync.clean_applefiles

        def clean(*args, **kwargs):
            self._prepare_replacement(copy_original=True)
            self._pivot()
            return original(*args, **kwargs)

        with mock.patch.object(sync, 'clean_applefiles', side_effect=clean):
            with self.assertRaisesRegex(RuntimeError, 'cleanup refused'):
                engine.export_playlists(self.library, [self.playlist], self.target,
                                        derive_artists=False)
        self._assert_replacement_unchanged()

    def test_cli_post_export_cleanup_refuses_replacement_without_a_traceback(self):
        from soundbox import cli
        from tests.support.library import write_xml
        write_xml(self.catalog, [('one', 'One', 'Fixture', self.source)], ['one'])
        args = argparse.Namespace(dry_run=False, xml=self.catalog, playlists=None,
                                  target=str(self.target), layout='tree',
                                  derive_artists=False, clean=True)
        original = sync.clean_applefiles

        def clean(*args, **kwargs):
            self._prepare_replacement(copy_original=True)
            self._pivot()
            return original(*args, **kwargs)

        output = io.StringIO()
        with mock.patch.object(sync, 'clean_applefiles', side_effect=clean), redirect_stdout(output):
            self.assertEqual(cli._cmd_sync(args), 2)
        self.assertIn('cleanup refused', output.getvalue())
        self._assert_replacement_unchanged()

    def test_growable_export_batch_cleanup_retains_completed_job_identity(self):
        from desktop.workers import ExportWorker
        from soundbox import engine
        worker = ExportWorker(self.library)
        worker.add(str(self.target), [self.playlist])
        original = engine.export_playlists

        def export(*args, **kwargs):
            report = original(*args, **kwargs)
            self._prepare_replacement(copy_original=True)
            self._pivot()
            return report

        with mock.patch.object(engine, 'export_playlists', side_effect=export):
            worker.run()
        self.assertEqual(len(worker.result), 1)
        self.assertNotIsInstance(worker.result[0][1], str)
        self._assert_replacement_unchanged()

    def test_cleanup_detects_root_loss_even_when_optional_scan_catches_it(self):
        self._prepare_replacement()
        identity = (self.target.stat().st_dev, self.target.stat().st_ino)
        original = sync._clean_dir
        calls = []

        def clean(*args, **kwargs):
            if not calls:
                calls.append(True)
                self._pivot()
            return original(*args, **kwargs)

        with mock.patch.object(sync, '_clean_dir', side_effect=clean):
            with self.assertRaisesRegex(RuntimeError, 'remounted'):
                sync.clean_applefiles(self.target, expected_mount_identity=identity)
        self._assert_replacement_unchanged()

    def test_growable_batch_with_different_report_epochs_skips_shared_cleanup(self):
        from desktop.workers import ExportWorker
        from soundbox import engine
        from soundbox.report import SyncReport
        worker = ExportWorker(self.library)
        for _ in range(2):
            worker.add(str(self.target), [self.playlist])
        reports = [SyncReport(False, written_dirs={self.target / 'Playlists'},
                              mount_identity=identity)
                   for identity in ((1, 2), (1, 3))]
        with mock.patch.object(engine, 'export_playlists', side_effect=reports), \
                mock.patch.object(engine, 'clean_device') as clean:
            worker.run()
        self.assertEqual(len(worker.result), 2)
        clean.assert_not_called()

    def test_successful_batch_with_no_written_directories_never_traverses_device(self):
        from desktop.workers import ExportWorker
        from soundbox import engine
        from soundbox.report import SyncReport
        worker = ExportWorker(self.library)
        worker.add(str(self.target), [self.playlist])
        identity = (self.target.stat().st_dev, self.target.stat().st_ino)
        report = SyncReport(False, mount_identity=identity)
        with mock.patch.object(engine, 'export_playlists', return_value=report), \
                mock.patch.object(sync, '_clean_dir',
                                  side_effect=AssertionError('no-op traversed device')) as scan:
            worker.run()
        scan.assert_not_called()
        self.assertEqual(len(worker.result), 1)
        self.assertIs(worker.result[0][1], report)


if __name__ == '__main__':
    unittest.main()
