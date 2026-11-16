"""Direct iPod admission waits for the complete native edit generation."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest import mock

from desktop.export_wait import defer_source_sync,cancel_source_syncs
from desktop.app import MainWindow
from soundbox.library import Library,Playlist,Track


class ExportSourceWait(unittest.TestCase):
    def host(self):
        playlist=Playlist('Before',[],['one'],'42',export_key='cust:alias')
        library=Library({'one':Track('one','Unknown Artist - Title','','file:///fixture/one.wav')},[playlist],
                        '/fixture/master.db',source_library_id='library',
                        source_playlist_catalog={'nodes':[dict(ID='42',UUID='native-uuid',rb_local_deleted=0)]})
        host=SimpleNamespace(lib=library,tag_controller=SimpleNamespace(owned=False,pending=[]),
            metadata_controller=SimpleNamespace(pending=[{'error':''}],queue_error=lambda:'',migration_pending=False),
            _device_mount_tokens={'/fixture/ipod':4},_shutting_down=False,_state_maintenance=False,
            _device_operation_owned=mock.Mock(return_value=False),cancel_btn=mock.Mock(),
            _start_pulse=mock.Mock(),_stop_pulse=mock.Mock(),_flash_status=mock.Mock(),
            _begin_stock_ipod_sync=mock.Mock(),_begin_dual_ipod_sync=mock.Mock())
        return host,playlist

    def setUp(self):
        self.timers=[]
        self.timer=mock.patch('desktop.export_wait.QTimer.singleShot',side_effect=lambda interval,fn:self.timers.append(fn))
        self.timer.start();self.addCleanup(self.timer.stop)
        self.facts=mock.patch('desktop.export_wait.device_facts',return_value=SimpleNamespace(volume_uuid='volume'))
        self.facts.start();self.addCleanup(self.facts.stop)

    def poll(self):self.timers.pop(0)()

    def test_title_and_artist_pair_fully_drains_then_rebinds_native_selection_and_keeps_alias(self):
        host,playlist=self.host()
        self.assertTrue(defer_source_sync(host,'stock','/fixture/ipod',[playlist],reconcile=True))
        host.lib.tracks_by_id['one'].name='Title'  # First native ACK, artist still queued.
        host.lib.playlists=[Playlist('After',[],['one','one'],'42')]
        self.poll();host._begin_stock_ipod_sync.assert_not_called()
        host.lib.tracks_by_id['one'].artist='Artist';host.metadata_controller.pending=[]
        self.poll()
        args,kwargs=host._begin_stock_ipod_sync.call_args
        self.assertEqual(args[1][0].keys,['one','one']);self.assertEqual(args[1][0].name,'After')
        self.assertEqual(args[1][0].export_key,'cust:alias');self.assertTrue(kwargs['reconcile'])
        self.assertTrue(kwargs['_source_ready']);self.assertIsNone(host._source_sync_waiting)
        self.assertNotIn('_pending_device_operation',vars(host));self.assertNotIn('_exporting',vars(host))

    def test_commands_fifo_preserve_all_options_and_never_overwrite_one_another(self):
        host,playlist=self.host();manifest={'generation':1,'rows':[1]}
        options=dict(reconcile=True,respect_selected_order=True,preconfirmed=True,confirmed=True,
                     action='resync',expected_playlist_rows={'row':{'uuid':'value'}},expected_manifest=manifest,audit_existing=True)
        self.assertTrue(defer_source_sync(host,'dual','/fixture/ipod',[playlist],**options))
        self.assertTrue(defer_source_sync(host,'stock','/fixture/ipod',None,reconcile=True))
        manifest['rows'].append(2);host.metadata_controller.pending=[]
        self.poll();host._begin_dual_ipod_sync.assert_called_once()
        self.assertEqual(host._begin_dual_ipod_sync.call_args.kwargs,{**options,'expected_manifest':{'generation':1,'rows':[1]},'_source_ready':True})
        host._device_operation_owned.return_value=True;self.poll();host._begin_stock_ipod_sync.assert_not_called()
        host._device_operation_owned.return_value=False;self.poll()
        self.assertIsNone(host._begin_stock_ipod_sync.call_args.args[1])

    def test_source_playlist_uuid_or_target_change_cancels_instead_of_retargeting(self):
        for change in ('source','uuid','mount','volume','deleted'):
            with self.subTest(change=change):
                self.timers.clear();host,playlist=self.host()
                defer_source_sync(host,'stock','/fixture/ipod',[playlist],reconcile=False)
                if change=='source':host.lib.source_library_id='replacement'
                if change=='uuid':host.lib.source_playlist_catalog['nodes'][0]['UUID']='replacement'
                if change=='deleted':host.lib.source_playlist_catalog['nodes'][0]['rb_local_deleted']=1
                if change=='mount':host._device_mount_tokens['/fixture/ipod']=5
                if change=='volume':self.facts.stop();self.facts=mock.patch('desktop.export_wait.device_facts',return_value=SimpleNamespace(volume_uuid='replacement'));self.facts.start()
                host.metadata_controller.pending=[];self.poll()
                host._begin_stock_ipod_sync.assert_not_called();host._flash_status.assert_called_once()
                self.facts.stop();self.facts=mock.patch('desktop.export_wait.device_facts',return_value=SimpleNamespace(volume_uuid='volume'));self.facts.start()

    def test_cancel_shutdown_restore_and_backup_pause_fence_callbacks(self):
        for action in ('cancel','shutdown','restore','backup'):
            with self.subTest(action=action):
                self.timers.clear();host,playlist=self.host();defer_source_sync(host,'stock','/fixture/ipod',[playlist])
                host.metadata_controller.pending=[]
                if action in ('cancel','restore'):self.assertTrue(cancel_source_syncs(host))
                if action=='shutdown':host._shutting_down=True
                if action=='backup':host._state_maintenance=True
                self.poll();host._begin_stock_ipod_sync.assert_not_called()
                if action=='backup':host._state_maintenance=False;self.poll();host._begin_stock_ipod_sync.assert_called_once()

    def test_entry_points_wait_before_device_model_or_snapshot_capture(self):
        for kind in ('stock','dual'):
            host,playlist=self.host();host._stock_write_model=mock.Mock()
            with mock.patch('soundbox.export_snapshot.capture_export') as capture:
                getattr(MainWindow,f'_begin_{kind}_ipod_sync')(host,'/fixture/ipod',[playlist])
                capture.assert_not_called();host._stock_write_model.assert_not_called()
            self.assertEqual(len(host._source_sync_waiting),1)
            cancel_source_syncs(host)

    def test_model_dialog_reentrancy_rechecks_source_generation_before_capture(self):
        for change in ('pending','source'):
            host,playlist=self.host();host.metadata_controller.pending=[]
            host._device_profiles={};host._defer_device_operation_until_scan_finishes=lambda *args:False
            def model(*args):
                if change=='pending':host.metadata_controller.pending=[{'error':''}]
                else:host.lib.source_library_id='replacement'
                return 'MA446'
            host._stock_write_model=model
            with mock.patch('soundbox.export_snapshot.capture_export') as capture:
                MainWindow._begin_stock_ipod_sync(host,'/fixture/ipod',[playlist])
                capture.assert_not_called()
            if change=='pending':self.assertEqual(len(host._source_sync_waiting),1);cancel_source_syncs(host)
            else:host._flash_status.assert_called_once()

    def test_local_selection_and_mutable_options_detach_before_waiting(self):
        host,_=self.host();playlist=Playlist('Custom',[],['one']);before=deepcopy(playlist)
        defer_source_sync(host,'stock','/fixture/ipod',[playlist]);playlist.keys.clear();playlist.name='Later'
        host.metadata_controller.pending=[];self.poll()
        self.assertEqual(host._begin_stock_ipod_sync.call_args.args[1],[before])

    def test_disabled_metadata_publication_refuses_export_without_waiting_or_discarding_intentions(self):
        host,playlist=self.host();before=deepcopy(host.metadata_controller.pending)
        host.metadata_controller.reason=lambda:'Native metadata editing requires separate acceptance for this library'
        self.assertTrue(defer_source_sync(host,'stock','/fixture/ipod',[playlist]))
        self.assertIsNone(getattr(host,'_source_sync_waiting',None))
        self.assertEqual(self.timers,[]);host._start_pulse.assert_not_called()
        host._begin_stock_ipod_sync.assert_not_called()
        self.assertEqual(host.metadata_controller.pending,before)
        self.assertIn('cannot be saved',host._flash_status.call_args.args[0])

    def test_metadata_error_after_wait_begins_finishes_wait_without_touching_another_device_owner(self):
        for owned in (False,True):
            with self.subTest(owned=owned):
                self.timers.clear();host,playlist=self.host()
                defer_source_sync(host,'stock','/fixture/ipod',[playlist])
                host.metadata_controller.pending[0]['error']='Selected song changed; refresh before retrying'
                before=deepcopy(host.metadata_controller.pending)
                host._device_operation_owned.return_value=owned
                self.poll()
                self.assertIsNone(host._source_sync_waiting);self.assertEqual(self.timers,[])
                host._begin_stock_ipod_sync.assert_not_called()
                self.assertEqual(host.metadata_controller.pending,before)
                self.assertIn('Selected song changed',host._flash_status.call_args.args[0])
                self.assertEqual(host._stop_pulse.called,not owned)

    def test_cdj_metadata_blocker_refuses_initial_export_and_ends_an_existing_wait(self):
        from desktop.cdj import CdjDeviceMixin
        for after_wait in (False,True):
            with self.subTest(after_wait=after_wait):
                self.timers.clear();host,playlist=self.host()
                host._defer_device_operation_until_scan_finishes=lambda *_args,**_kwargs:False
                host._begin_cdj_export=mock.Mock()
                reason=lambda:'Native metadata editing requires separate acceptance for this library'
                if not after_wait:host.metadata_controller.reason=reason
                with mock.patch('desktop.cdj.QTimer.singleShot',side_effect=lambda _ms,fn:self.timers.append(fn)):
                    CdjDeviceMixin._begin_cdj_export(host,'/fixture/ipod',[playlist])
                    if after_wait:
                        host.metadata_controller.reason=reason;self.poll()
                self.assertIsNone(getattr(host,'_cdj_waiting',None));self.assertEqual(self.timers,[])
                host._begin_cdj_export.assert_not_called()
                self.assertIn('cannot be saved',host._flash_status.call_args.args[0])
