"""Generated iPod admission, frozen worker inputs and honest export summaries."""
from pathlib import Path
from dataclasses import replace
from hashlib import sha256
from threading import Event
from types import MethodType, SimpleNamespace
import tempfile
import unittest
from unittest import mock

from PySide6.QtWidgets import QApplication

from desktop.app import MainWindow
from desktop.devices import DeviceFacts
from desktop.workers import ExportWorker
from desktop.workers import OneShotTask,ResyncWorker
from desktop.export_dialog import persist_ipod_export_witness
from soundbox.export_snapshot import capture_export
from soundbox.library import Library, Playlist, Track
from soundbox.operation_control import OperationCancelled
from soundbox.report import PlaylistReport,SyncReport
from soundbox.stock_ipod import StockIpodCommitResult,StockIpodSyncPlan
from soundbox.dual_ipod import DualIpodCommitResult,DualIpodSyncPlan,DualPlaylistUpdate
from soundbox.device_operations import DeviceCommand,DeviceOperationCoordinator,DeviceSessionIdentity,Phase
from soundbox.stock_ipod_catalog_state import StockCatalogWitness
from tests.test_export_dialog import Host
from tests.test_ipod_desktop_io import host as plan_host


class IpodExportEntries(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def generated(self):
        owner = plan_host()
        track = Track('one', 'Original title', 'Original artist', 'file:///generated/one.wav')
        playlist = Playlist('Selected', [], keys=['one'], pid='selected')
        owner.lib = Library({'one': track}, [playlist], '/generated/master.db',
            source_kind='rekordbox-db', source_library_id='generated-library',
            source_playlist_catalog={'nodes':[{'ID':'selected','UUID':'selected-uuid','rb_local_deleted':0}]})
        owner._request_ipod_export_capture = mock.Mock()
        owner._exporting = False
        owner._begin_stock_ipod_sync = MethodType(MainWindow._begin_stock_ipod_sync, owner)
        owner._begin_dual_ipod_sync = MethodType(MainWindow._begin_dual_ipod_sync, owner)
        rows = {'one':dict(title='Captured title',artist='Captured artist',album='Captured album',
                          genre='Captured genre',bpm=137.0,key='8A',year=2026)}
        snapshot = capture_export(owner.lib,[playlist],complete=True,display_rows=rows)
        capture = SimpleNamespace(snapshot=snapshot,track_total=1,require_current=mock.Mock(),
                                  expected_manifest=None,expected_manifest_payload=None,expected_manifest_absent=False,
                                  verify_worker=mock.Mock(),artwork=None)
        return owner,playlist,capture

    def generated_plan(self,target,*,changed=False):
        before=b'generated database';rendered=b'changed database' if changed else before
        row=target.stat()
        stock=StockIpodSyncPlan(target,(row.st_dev,row.st_ino),'MA446',
            Path('iPod_Control/iTunes/iTunesDB'),sha256(before).hexdigest(),None,rendered,
            identity_key='identity',playlist_names=('Selected',),captured_export=True)
        dual=DualIpodSyncPlan(target,(row.st_dev,row.st_ino),stock,(),(),(),(),(),(),(),(),
                             manifest_payload=b'generated manifest',manifest_payload_before=b'generated manifest',
                             manifest_sha256_before=sha256(b'generated manifest').hexdigest())
        return stock,dual

    def test_new_stock_and_dual_exports_request_checkpoint_with_backlog_paused_or_running(self):
        for kind in ('stock','dual'):
            for paused in (False,True):
                with self.subTest(kind=kind,paused=paused):
                    owner,playlist,_ = self.generated()
                    owner.metadata_controller = SimpleNamespace(migration_pending=True,background_paused=paused)
                    with mock.patch('desktop.export_wait.defer_source_sync',side_effect=AssertionError('Global save barrier')), \
                         mock.patch('desktop.cdj._source_edits_pending',side_effect=AssertionError('Whole backlog check')):
                        getattr(owner,f'_begin_{kind}_ipod_sync')('/generated/IPOD',[playlist])
                    self.assertEqual(owner._request_ipod_export_capture.call_args.args,
                                     (kind,'/generated/IPOD',[playlist]))
                    owner._start_device_worker.assert_not_called()

    def test_stock_and_dual_plan_use_captured_generation_without_recapturing_live_gui(self):
        for kind in ('stock','dual'):
            with self.subTest(kind=kind):
                owner,playlist,capture = self.generated()
                owner.lib.tracks_by_id['one'].name = 'Later title'
                owner.titles['one'] = 'Even later displayed title'
                module = 'soundbox.stock_ipod' if kind=='stock' else 'soundbox.dual_ipod'
                with mock.patch('desktop.app.device_facts',return_value=DeviceFacts()) as facts, \
                     mock.patch('soundbox.export_snapshot.capture_export',side_effect=AssertionError('Live GUI recapture')), \
                     mock.patch('desktop.cdj._source_edits_pending',side_effect=AssertionError('Whole backlog check')), \
                     mock.patch(module+f'.plan_{kind}_ipod_sync',return_value='generated-plan') as plan:
                    getattr(owner,f'_begin_{kind}_ipod_sync')('/generated/IPOD',[playlist],_export_capture=capture)
                    plan.assert_not_called()
                    facts.assert_called_once_with('/generated/IPOD',cached_only=True)
                    worker=getattr(owner,f'_{kind}_plan_worker')
                    self.addCleanup(worker.deleteLater)
                    worker.run()
                self.assertEqual(worker.result,'generated-plan')
                self.assertEqual(plan.call_args.args[0].tracks_by_id['one'].name,'Original title')
                self.assertEqual(plan.call_args.kwargs['display_rows']['one']['title'],'Captured title')
                self.assertEqual(plan.call_args.kwargs['display_rows']['one']['artist'],'Captured artist')
                self.assertEqual(plan.call_args.kwargs['display_rows']['one']['genre'],'Captured genre')
                self.assertEqual(capture.verify_worker.call_count,2)

    def test_dual_device_delete_and_order_use_checkpoint_instead_of_cdj_global_wait(self):
        for action in ('delete','order'):
            with self.subTest(action=action):
                owner,playlist,_capture=self.generated();expected={'playlists':{'selected':{'tracks':['one']}}}
                with mock.patch('desktop.export_wait.defer_source_sync',side_effect=AssertionError('CDJ global wait')), \
                     mock.patch('desktop.cdj._source_edits_pending',side_effect=AssertionError('CDJ global queue')):
                    owner._begin_dual_ipod_sync('/generated/IPOD',[playlist],action=action,reconcile=True,
                        expected_manifest=expected,confirmed=True)
                request=owner._request_ipod_export_capture.call_args
                self.assertEqual(request.args,('dual','/generated/IPOD',[playlist]))
                self.assertEqual(request.kwargs['action'],action)
                self.assertEqual(request.kwargs['expected_manifest'],expected)
                owner._start_device_worker.assert_not_called()

    def test_scan_deferral_carries_the_same_capture_and_rejects_expired_capture(self):
        for kind in ('stock','dual'):
            with self.subTest(kind=kind):
                owner,playlist,capture=self.generated();callbacks=[]
                owner._defer_device_operation_until_scan_finishes=lambda callback:callbacks.append(callback) or True
                getattr(owner,f'_begin_{kind}_ipod_sync')('/generated/IPOD',[playlist],_export_capture=capture)
                self.assertEqual(len(callbacks),1)
                owner._request_ipod_export_capture.assert_not_called()
                capture.require_current.side_effect=ValueError('The connected iPod changed')
                with self.assertRaisesRegex(ValueError,'connected iPod changed'):
                    callbacks.pop()()
                owner._start_device_worker.assert_not_called()

    def test_rockbox_worker_consumes_injected_snapshot_and_exact_display_generation(self):
        owner,playlist,capture=self.generated()
        owner._device_profiles['/generated/IPOD']=SimpleNamespace(kind='rockbox',rockbox_export=True)
        owner.export_worker=None;owner._on_progress=mock.Mock()
        owner._on_export_done=mock.Mock();owner._finish_device_result_worker=mock.Mock()
        owner.rockbox_media_cache=SimpleNamespace(clear=mock.Mock())
        with mock.patch('desktop.app.device_facts',return_value=DeviceFacts(writable=True)), \
             mock.patch('desktop.workers.capture_export',side_effect=AssertionError('Live GUI recapture')):
            MainWindow._begin_rockbox_ipod_sync(owner,'/generated/IPOD',[playlist],_export_capture=capture)
        worker=owner.export_worker;self.addCleanup(worker.deleteLater)
        self.assertIs(worker._jobs[0][1],capture.snapshot)
        owner.lib.tracks_by_id['one'].name='Later title'
        with mock.patch('desktop.workers.engine.export_playlists',return_value=SyncReport(dry_run=False)) as export:
            worker.run()
        self.assertEqual(export.call_args.kwargs['display_rows']['one']['title'],'Captured title')
        self.assertEqual(export.call_args.args[0].tracks_by_id['one'].name,'Original title')

    def test_worker_rejects_unproven_snapshot_or_missing_entry_estimate(self):
        owner,playlist,capture=self.generated()
        worker=ExportWorker(owner.lib);self.addCleanup(worker.deleteLater)
        for options in (dict(snapshot=object(),estimate=1),dict(snapshot=capture.snapshot),
                        dict(snapshot=capture.snapshot,estimate=True)):
            with self.subTest(options=tuple(options)),self.assertRaises(ValueError):
                worker.add('/generated/IPOD',[playlist],**options)
        self.assertEqual(len(worker._jobs),0)

    def test_artwork_change_during_plan_refuses_before_commit(self):
        for kind in ('stock','dual'):
            with self.subTest(kind=kind):
                owner,playlist,capture=self.generated();capture.artwork=('captured-cover',)
                owner.art_cache=SimpleNamespace(selection_current=mock.Mock(side_effect=(True,False)))
                module='soundbox.stock_ipod' if kind=='stock' else 'soundbox.dual_ipod'
                with mock.patch('desktop.app.device_facts',return_value=DeviceFacts()), \
                     mock.patch(module+f'.plan_{kind}_ipod_sync',return_value='plan') as plan:
                    getattr(owner,f'_begin_{kind}_ipod_sync')('/generated/IPOD',[playlist],_export_capture=capture)
                    worker=getattr(owner,f'_{kind}_plan_worker');self.addCleanup(worker.deleteLater)
                    worker.run()
                plan.assert_called_once()
                self.assertIsInstance(worker.result,ValueError)
                self.assertIn('Selected artwork changed',str(worker.result))

    def test_resync_worker_uses_frozen_display_generation_and_validation_hook(self):
        owner,playlist,capture=self.generated();validate=mock.Mock()
        with mock.patch('desktop.workers.capture_export',side_effect=AssertionError('Live GUI recapture')):
            worker=ResyncWorker(owner.lib,'/generated/IPOD',snapshot=capture.snapshot,validate_inputs=validate)
        self.addCleanup(worker.deleteLater)
        owner.lib.tracks_by_id['one'].name='Later native title'
        with mock.patch('desktop.workers.engine.resync_device',return_value=SyncReport(dry_run=False)) as sync:
            worker.run()
        self.assertEqual(sync.call_args.args[0].tracks_by_id['one'].name,'Original title')
        self.assertEqual(sync.call_args.kwargs['display_rows']['one']['title'],'Captured title')
        self.assertIs(sync.call_args.kwargs['validate_inputs'],validate)
        validate.assert_called_once()

    def test_only_engine_verified_matching_witness_is_saved_and_cache_failure_is_warning(self):
        witness=StockCatalogWitness(('source','rekordbox-db','library'),'device','a'*64,())
        result=StockIpodCommitResult(Path('/generated/recovery'),1,0,0,0,('Selected',),'a'*64,
                                    catalog_witness=witness)
        store=SimpleNamespace(save=mock.Mock())
        self.assertIs(persist_ipod_export_witness(result,store,'device'),result)
        store.save.assert_called_once_with(witness)
        store.save.reset_mock()
        mismatched=persist_ipod_export_witness(result,store,'replacement')
        store.save.assert_not_called()
        self.assertIn('another iPod',mismatched.catalog_warning)
        self.assertEqual(mismatched.database_sha256,result.database_sha256)
        store.save.side_effect=OSError('Generated cache unavailable')
        failed=persist_ipod_export_witness(result,store,'device')
        self.assertIn('Device changes saved',failed.catalog_warning)
        self.assertIn('optional Sync comparison cache could not be saved',failed.catalog_warning)
        self.assertEqual(failed.copied_tracks,1)

    def test_captured_noop_verifies_and_persists_only_in_worker_then_publishes_returned_proof(self):
        for kind in ('stock','dual'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as directory:
                target=Path(directory).resolve();dev=str(target)
                owner,playlist,capture=self.generated();stock,dual=self.generated_plan(target)
                candidate=StockCatalogWitness(('source','rekordbox-db','library'),'identity',stock.database_sha256_before,())
                verified=StockCatalogWitness(candidate.source,candidate.device_key,candidate.database_sha256,())
                from dataclasses import replace
                stock=replace(stock,catalog_witness=candidate);dual=replace(dual,stock=stock)
                stock_result=StockIpodCommitResult(target/'.soundbox',0,0,1,0,('Selected',),
                    stock.database_sha256_before,catalog_witness=verified)
                dual_result=DualIpodCommitResult(stock_result,0,0,0,0,0,'a'*64)
                owner._stop_pulse=mock.Mock();owner._export_target_result=mock.Mock()
                owner._flash_status=mock.Mock();owner._device_completion_message=mock.Mock()
                owner._finalize_ipod_operation=lambda _dev,complete:complete()
                owner._publish_ipod_export_witness=MethodType(MainWindow._publish_ipod_export_witness,owner)
                owner._dual_active_preconfirmed=False;owner._dual_active_confirmed=False
                owner._dual_sync_queue={};owner.progress.value.return_value=0
                module='soundbox.stock_ipod' if kind=='stock' else 'soundbox.dual_ipod'
                with mock.patch('desktop.app.device_facts',return_value=DeviceFacts()), \
                     mock.patch(module+f'.plan_{kind}_ipod_sync',return_value=stock if kind=='stock' else dual), \
                     mock.patch('soundbox.stock_ipod.committed_stock_export_witness',return_value=(verified,'')) as readback, \
                     mock.patch('soundbox.dual_ipod.commit_dual_ipod_sync',return_value=dual_result) as dual_noop:
                    getattr(owner,f'_begin_{kind}_ipod_sync')(dev,[playlist],_export_capture=capture)
                    worker=getattr(owner,f'_{kind}_plan_worker');self.addCleanup(worker.deleteLater)
                    readback.assert_not_called();dual_noop.assert_not_called()
                    owner.stock_catalog_witness_store.save.assert_not_called()
                    worker.run()
                    if kind=='stock':readback.assert_called_once()
                    else:dual_noop.assert_called_once()
                owner.stock_catalog_witness_store.save.assert_called_once_with(verified)
                result=worker.result
                self.assertIs((result[1] if kind=='stock' else result[1].stock).catalog_witness,verified)
                getattr(MainWindow,f'_on_{kind}_ipod_plan')(owner,dev,result,_export_capture=capture)
                self.assertIs(owner._stock_catalog_witness_cache[dev],verified)
                self.assertIsNot(owner._stock_catalog_witness_cache[dev],candidate)

    def test_confirmed_and_preconfirmed_dual_branches_keep_capture_and_reject_changed_selection(self):
        for preconfirmed in (False,True):
            for expired in (False,True):
                with self.subTest(preconfirmed=preconfirmed,expired=expired),tempfile.TemporaryDirectory() as directory:
                    owner,_playlist,capture=self.generated();_stock,plan=self.generated_plan(Path(directory).resolve(),changed=True)
                    owner._dual_active_preconfirmed=preconfirmed;owner._dual_active_confirmed=not preconfirmed
                    owner._dual_active_playlist_keys={'selected'};owner._dual_sync_queue={}
                    owner._dual_active_action='sync';owner._dual_active_device='/generated/IPOD'
                    owner._stop_pulse=mock.Mock();owner._export_target_result=mock.Mock()
                    owner._set_status_text=mock.Mock();owner._refresh_devices=mock.Mock()
                    owner._reset_dual_batch=mock.Mock();owner._commit_dual_ipod_plan=mock.Mock()
                    owner.progress.value.return_value=0
                    if expired:capture.require_current.side_effect=ValueError('Selected conversion or artwork changed')
                    with mock.patch('desktop.app.QMessageBox.warning'):
                        MainWindow._on_dual_ipod_plan(owner,'/generated/IPOD',plan,_export_capture=capture)
                    if expired:
                        owner._commit_dual_ipod_plan.assert_not_called()
                        self.assertIn('Selected conversion or artwork changed',str(owner._export_target_result.call_args.args[1]))
                    else:owner._commit_dual_ipod_plan.assert_called_once_with('/generated/IPOD',plan,_export_capture=capture)

    def test_dual_membership_save_failure_never_reports_export_complete(self):
        for another_target in (False, True):
            with self.subTest(another_target=another_target), tempfile.TemporaryDirectory() as directory:
                owner = Host(Path(directory));self.addCleanup(owner.deleteLater)
                batch = owner._export_batch
                target = batch['pending'].pop(0)
                second = batch['pending'][0]
                if not another_target:batch['pending'].clear()
                target['kind'] = 'dual_boot';dev = target['path']
                batch.update(active=target, started=True)
                _, plan = self.generated_plan(Path(dev), changed=True)
                plan = replace(plan, playlist_updates=(
                    DualPlaylistUpdate('cust:Favorites', ('one',), ('one', 'two')),))
                owner.custom = {'Favorites':['one']}
                owner._save_state = mock.Mock(return_value=False)
                owner._apply_settings_to_lib = mock.Mock()
                owner._refresh_custom = mock.Mock()
                owner._persist_rockbox_playlist_additions = MethodType(
                    MainWindow._persist_rockbox_playlist_additions, owner)
                owner._dual_active_device = dev
                owner._dual_active_playlist_keys = {'cust:Favorites'}
                owner._dual_active_preconfirmed = False
                owner._dual_active_confirmed = True
                owner._dual_active_action = 'sync';owner._dual_active_track_total = 2
                owner._dual_commit_worker = None
                owner._set_status_count = mock.Mock()
                owner._start_device_worker = mock.Mock()
                worker = SimpleNamespace(result=plan, device_command=None,
                    export_batch_context=(batch, target))
                owner._dual_plan_worker = worker
                owner._finish_worker = mock.Mock(side_effect=lambda attr, _worker:setattr(owner, attr, None))
                with mock.patch('desktop.export_dialog.device_facts', side_effect=lambda dev, **_:
                         SimpleNamespace(volume_uuid=Path(dev).name, writable=True)), \
                     mock.patch('desktop.app.QMessageBox.critical') as failure, \
                     mock.patch('desktop.export_dialog.QMessageBox.warning') as summary, \
                     mock.patch('soundbox.dual_ipod.commit_dual_ipod_sync') as commit:
                    # Drive the actual joined-result callback boundary. Local
                    # membership persistence fails before any commit admission.
                    MainWindow._finish_device_result_worker(owner, '_dual_plan_worker', worker,
                        lambda result:MainWindow._commit_dual_ipod_plan(owner, dev, result))
                    for _ in range(4):self.app.processEvents()
                    if another_target:
                        self.assertIs(batch['active'], second)
                        self.assertEqual(len(owner.calls), 1)
                        self.assertEqual(owner.calls[0][0], second['path'])
                        owner._export_target_result(second['path'], SyncReport(dry_run=False))
                        owner._exporting = False
                        owner._maybe_finish_export_target()
                        for _ in range(4):self.app.processEvents()
                owner._save_state.assert_called_once_with()
                self.assertEqual(owner.custom, {'Favorites':['one']})
                owner._apply_settings_to_lib.assert_not_called()
                owner._refresh_custom.assert_not_called()
                owner._start_device_worker.assert_not_called();commit.assert_not_called()
                self.assertIsNone(owner._dual_commit_worker)
                self.assertIsNone(owner._dual_plan_worker)
                owner._finish_worker.assert_called_once_with('_dual_plan_worker', worker)
                self.assertFalse(owner._exporting)
                self.assertIsNone(owner._export_batch)
                self.assertEqual(batch['outcomes'], ['failed', 'complete'] if another_target else ['failed'])
                self.assertIn('could not save', batch['results'][0][1])
                self.assertEqual(summary.call_args.args[1],'Export needs attention')
                self.assertIn('could not save', summary.call_args.args[2])
                failure.assert_not_called();summary.assert_called_once()
                self.assertIn('No device files were changed', summary.call_args.args[2])
                self.assertIn('No device files were changed',owner._last_device_failure['outcome'])


    def test_real_rockbox_resync_cover_refusal_keeps_attention_distinct_from_cancel(self):
        from io import BytesIO
        import wave
        from PIL import Image
        from desktop.export_dialog import ExportSelectionMixin
        from soundbox import engine, manifest, sync
        from soundbox.artwork import ArtCache, ArtResult
        from soundbox.operation_control import checkpoint, operation_scope
        from soundbox.rockbox_media import RockboxArtworkCache, RockboxMediaCache
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve(); sources = root / 'sources'; sources.mkdir()
                source = sources / 'song.wav'
                with wave.open(str(source), 'wb') as output:
                    output.setnchannels(2); output.setsampwidth(2); output.setframerate(44100)
                    output.writeframes(b'\x01\x00\x02\x00' * 1323)
                original_audio = source.read_bytes()
                track = Track('selected', 'Selected song', 'Artist', source.as_uri())
                playlist = Playlist('Favorites', [], ['selected'], pid='favorites')
                library = Library({'selected': track}, [playlist], sources / 'library.xml')
                target = root / 'Rockbox'; target.mkdir(); (target / '.rockbox').mkdir()
                art = ArtCache(root / 'art')
                def cover(color):
                    output = BytesIO(); Image.new('RGB', (24, 24), color).save(output, 'JPEG')
                    return ArtResult(output.getvalue(), 'image/jpeg', 1.0,
                                     'https://soundcloud.com/artist/song', 'Selected song')
                art.store_ok('selected', cover('red'))
                media = RockboxMediaCache(root / 'media-cache')
                images = RockboxArtworkCache(root / 'image-cache')
                options = dict(art_cache=art, rockbox_media_cache=media, rockbox_art_cache=images)
                first = engine.export_playlists(library, [playlist], target, **options)
                self.assertFalse(first.cancelled)
                self.assertFalse([error for row in first.playlists for error in row.errors])
                baseline = manifest.load(target)
                before = {path: path.read_bytes() for path in (target / 'Music').rglob('*') if path.is_file()}
                art.store_ok('selected', cover('blue'))
                selected = art.selection_token(['selected']); stop = Event(); prepared = []
                prepare = images.prepare
                def change_after_prepare(*args, **kwargs):
                    result = prepare(*args, **kwargs); prepared.append(result)
                    if cancelled:stop.set()
                    else:art.store_ok('selected', cover('green'))
                    return result
                def validate(_track_id):
                    checkpoint()
                    if not art.selection_current(selected):
                        raise ValueError('Selected cover changed during resync')
                with mock.patch.object(images, 'prepare', side_effect=change_after_prepare), \
                     mock.patch.object(sync, '_prune_manifest_owned', side_effect=AssertionError('Partial resync pruned files')), \
                     mock.patch.object(sync, 'clean_applefiles', side_effect=AssertionError('Partial resync cleaned files')), \
                     mock.patch.object(manifest, 'save', wraps=manifest.save) as saved, \
                     operation_scope(stop):
                    report = engine.resync_device(library, target, validate_artwork=validate,
                                                  should_cancel=stop.is_set, **options)
                self.assertEqual(len(prepared), 1)
                self.assertEqual(source.read_bytes(), original_audio)
                self.assertEqual({path: path.read_bytes() for path in before}, before)
                saved.assert_called_once()  # run's honest partial receipt; no resync cleanup save.
                current = manifest.load(target)
                self.assertEqual(current['playlists']['favorites']['sig'],
                    baseline['playlists']['favorites']['sig'] if cancelled else 'partial')
                self.assertNotEqual(current['generation'], current['completed_generation'])
                target_row = {'path': str(target), 'name': 'Rockbox'}
                batch = dict(active=target_row, started=True, error='', outcome='', note='')
                host = SimpleNamespace(_export_batch=batch, _exporting=True, _cancelling=False,
                    _stop_pulse=mock.Mock(), cancel_btn=mock.Mock(), progress=mock.Mock(),
                    _set_device_progress=mock.Mock(), _set_status_text=mock.Mock(),
                    _flash_status=mock.Mock(), _update_device_action_state=mock.Mock(),
                    _finalize_ipod_operation=mock.Mock(), _eject_ready_device='')
                host._export_target_result = MethodType(ExportSelectionMixin._export_target_result, host)
                host._export_target_result(str(target), report)
                with mock.patch('desktop.app.QMessageBox.warning') as warning:
                    MainWindow._on_resync_done(host, str(target), report)
                host._finalize_ipod_operation.assert_not_called()
                self.assertIs(report.cancelled, cancelled)
                self.assertEqual(batch['outcome'], 'cancelled' if cancelled else 'failed')
                if cancelled:
                    warning.assert_not_called()
                    host._flash_status.assert_called_once_with('Sync cancelled — completed files kept')
                else:
                    self.assertTrue(any('Selected cover changed during resync' in error
                                        for row in report.playlists for error in row.errors))
                    host._set_status_text.assert_called_with('Sync needs attention')
                    host._flash_status.assert_not_called()
                    self.assertIn('Selected cover changed during resync', warning.call_args.args[2])

    def test_cancel_queued_checkpoint_also_cancels_active_real_worker_without_false_no_write_status(self):
        entered=Event();worker=OneShotTask(lambda:entered.set() or worker.cancellation.wait(5))
        coordinator=DeviceOperationCoordinator();identity=DeviceSessionIdentity('/generated/IPOD',1)
        coordinator.observe(identity);state=coordinator.begin(DeviceCommand(identity,'export'),worker,Phase.EXECUTING)
        worker.cancellation=state.cancellation
        cancel_checkpoint=mock.Mock()
        owner=SimpleNamespace(_export_batch=None,_source_sync_waiting=[dict(batch=None,batch_target=None,
            dev='/generated/NEXT',cancel_checkpoint=cancel_checkpoint)],_cdj_waiting=None,
            _device_coordinator=coordinator,_pending_device_operation=None,_dual_sync_queue={},
            _EJECT_BLOCKING_WORKERS=('export_worker',),export_worker=worker,
            _device_operation_owned=lambda:coordinator.active is not None,_start_pulse=mock.Mock(),
            _stop_pulse=mock.Mock(),cancel_btn=mock.Mock(),progress=mock.Mock(),_flash_status=mock.Mock(),
            _exporting=True,_cancelling=False)
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            MainWindow._cancel_export(owner)
            self.assertIsNone(owner._source_sync_waiting);cancel_checkpoint.assert_called_once()
            self.assertTrue(worker.cancellation.is_set());self.assertTrue(owner._cancelling)
            owner._start_pulse.assert_called_once_with('Cancelling')
            owner._flash_status.assert_not_called()
        finally:
            worker.cancel();self.assertTrue(worker.wait(2000));worker.deleteLater()

    def test_partial_resync_and_export_reports_show_attention_not_success(self):
        for sync in (False,True):
            with self.subTest(sync=sync):
                report=SyncReport(dry_run=False,playlists=[PlaylistReport('Selected',copied=2,
                    errors=['Selected cover choice changed'])])
                owner=SimpleNamespace(_exporting=True,_cancelling=False,_stop_pulse=mock.Mock(),
                    cancel_btn=mock.Mock(),progress=mock.Mock(),_set_device_progress=mock.Mock(),
                    _set_status_text=mock.Mock(),_flash_status=mock.Mock(),_export_target_result=mock.Mock(),
                    _update_device_action_state=mock.Mock(),_eject_ready_device='',
                    stack=SimpleNamespace(currentIndex=lambda:1),fs_view=mock.Mock(),fs_model=mock.Mock(),
                    _finalize_ipod_operation=mock.Mock(),
                    _finalize_device_operations=lambda _devs,complete:complete())
                with mock.patch('desktop.app.QMessageBox.warning') as warning, \
                     mock.patch('desktop.app.QMessageBox.information') as information:
                    if sync:MainWindow._on_resync_done(owner,'/generated/IPOD',report)
                    else:MainWindow._on_export_done(owner,[('/generated/IPOD',report)])
                owner._set_status_text.assert_called_with('Sync needs attention' if sync else 'Export needs attention')
                owner._flash_status.assert_not_called();owner.fs_view.setRootIndex.assert_not_called()
                dialog=warning if sync else information
                self.assertIn('Selected cover choice changed',dialog.call_args.args[2])
                self.assertEqual(report.playlists[0].copied,2)


class ExportBatchOutcomes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def generated(self):
        area=tempfile.TemporaryDirectory();self.addCleanup(area.cleanup)
        owner=Host(Path(area.name));self.addCleanup(owner.deleteLater)
        patch=mock.patch('desktop.export_dialog.device_facts',
            side_effect=lambda dev,**_:DeviceFacts(volume_uuid=Path(dev).name,writable=True))
        patch.start();self.addCleanup(patch.stop)
        return owner

    def drain(self):
        for _ in range(5):self.app.processEvents()

    def test_paused_generic_target_does_not_block_stock_dual_or_rockbox_target(self):
        for kind in ('stock_ipod','dual_boot','rockbox'):
            with self.subTest(kind=kind):
                owner=self.generated();target=owner._export_batch['pending'][1]
                target['kind']=kind;owner._device_profiles[target['path']].kind=kind
                owner.metadata_controller=SimpleNamespace(pending=[],queue_error=lambda:'',
                    migration_pending=True,background_paused=True)
                def rockbox(dev,_items,**_options):
                    owner.calls.append((dev,[],{}));owner._exporting=True
                owner._on_device_drop=rockbox
                with mock.patch('desktop.export_dialog.QMessageBox.warning') as message:
                    owner._start_next_export_target()
                    self.assertEqual([call[0] for call in owner.calls],[target['path']])
                    self.assertIn('resume',owner._export_batch['results'][0][1])
                    self.assertIs(owner._export_batch['active'],target)
                    owner._exporting=False;owner._maybe_finish_export_target();self.drain()
                    self.assertIn('needs attention',message.call_args.args[1])
                    self.assertNotIn('USB B: Export complete',message.call_args.args[2])
                    self.assertIn(('USB B','Export complete'),owner._last_export_batch_results)

    def test_all_blocked_targets_never_claim_export_finished_or_complete(self):
        owner=self.generated()
        owner.metadata_controller=SimpleNamespace(pending=[],queue_error=lambda:'',
            migration_pending=True,background_paused=True)
        with mock.patch('desktop.export_dialog.QMessageBox.warning') as message, \
             mock.patch('desktop.export_dialog.QTimer.singleShot') as timer:
            owner._start_next_export_target()
        self.assertEqual(owner.calls,[]);self.assertIsNone(owner._export_batch)
        timer.assert_not_called()
        self.assertEqual(message.call_args.args[1],'Export needs attention')
        self.assertNotIn('Export complete',message.call_args.args[2])
        for name in ('USB A:','USB B:'):self.assertIn(name,message.call_args.args[2])

    def test_closed_source_reports_every_selected_target_without_starting_any(self):
        owner=self.generated();owner.lib=None
        with mock.patch('desktop.export_dialog.QMessageBox.warning') as message:
            owner._start_next_export_target();self.drain()
        self.assertEqual(owner.calls,[]);self.assertIsNone(owner._export_batch)
        self.assertEqual(message.call_args.args[1],'Export needs attention')
        for name in ('USB A:','USB B:'):self.assertIn(name,message.call_args.args[2])
        self.assertIn('Source library changed',message.call_args.args[2])

    def test_selected_failure_is_actionable_and_other_target_completion_stays_truthful(self):
        owner=self.generated()
        with mock.patch('desktop.export_dialog.QMessageBox.warning') as message:
            owner._start_next_export_target()
            owner._export_target_result(owner.calls[0][0],ValueError('Selected song conversion needs attention: original file moved'))
            owner._exporting=False;owner._maybe_finish_export_target();self.drain()
            owner._exporting=False;owner._maybe_finish_export_target();self.drain()
        self.assertIn('needs attention',message.call_args.args[1])
        self.assertIn('USB A: Selected song conversion needs attention: original file moved',message.call_args.args[2])
        self.assertNotIn('USB B: Export complete',message.call_args.args[2])
        self.assertIn(('USB B','Export complete'),owner._last_export_batch_results)

    def test_cancelled_only_target_does_not_claim_completion(self):
        owner=self.generated();owner._export_batch['pending']=owner._export_batch['pending'][:1]
        batch=owner._export_batch
        with mock.patch('desktop.export_dialog.QMessageBox.warning') as message, \
             mock.patch.object(owner,'_flash_status') as status:
            owner._start_next_export_target()
            owner._export_target_result(owner.calls[0][0],OperationCancelled('cancelled'))
            owner._exporting=False;owner._maybe_finish_export_target();self.drain()
        message.assert_not_called()
        self.assertEqual(status.call_args.args[0],'Export cancelled')
        self.assertEqual(batch['outcomes'],['cancelled'])
        self.assertEqual(batch['results'][0][0],'USB A')
        self.assertIn('cancelled',batch['results'][0][1].lower())
        self.assertNotIn('Export complete',batch['results'][0][1])

    def test_cancelled_or_failed_worker_report_is_not_implicit_success(self):
        for report in (SyncReport(dry_run=False,cancelled=True),
                       SyncReport(dry_run=False,playlists=[PlaylistReport('Selected',errors=['Source file moved'])])):
            with self.subTest(cancelled=report.cancelled):
                owner=self.generated();owner._export_batch['pending']=owner._export_batch['pending'][:1]
                batch=owner._export_batch
                with mock.patch('desktop.export_dialog.QMessageBox.warning') as message, \
                     mock.patch.object(owner,'_flash_status') as status:
                    owner._start_next_export_target();dev=owner.calls[0][0]
                    owner._export_target_result(dev,[(dev,report)])
                    owner._exporting=False;owner._maybe_finish_export_target();self.drain()
                self.assertNotIn('Export complete',batch['results'][0][1])
                if report.cancelled:
                    message.assert_not_called()
                    self.assertEqual(status.call_args.args[0],'Export cancelled')
                    self.assertEqual(batch['outcomes'],['cancelled'])
                else:
                    self.assertEqual(message.call_args.args[1],'Export needs attention')
                    self.assertIn('Source file moved',message.call_args.args[2])
