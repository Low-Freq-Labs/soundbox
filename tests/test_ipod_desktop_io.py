"""iPod presentation never probes a volume before its owned worker runs."""
from dataclasses import dataclass, replace
from types import MethodType, SimpleNamespace
from pathlib import Path
import unittest
from unittest import mock

from PySide6.QtWidgets import QApplication

from desktop.app import MainWindow
from desktop import devices
from desktop.track_info import TrackInfoMixin
from soundbox.library import Library, Playlist, Track
from soundbox.export_snapshot import capture_export
from soundbox.stock_ipod_info import StockIpodPlaylistSnapshot


@dataclass(frozen=True)
class Snapshot:
    identity_key: str = "identity"
    model: str = "MA446"
    serial_number: str = "serial"
    sync_state: str = ""
    sync_detail: str = ""
    sync_local_changes: int = 0
    sync_committed_at: str = ""


def host():
    value = SimpleNamespace(
        lib=Library({}, [], Path('/fixture/master.db')), overrides={}, titles={}, custom={},
        _state_maintenance=False, _shutting_down=False,
        _stock_view_callbacks={}, _stock_view_pending={},
        _stock_view_worker=None, _dev_scan_worker=None,
        _stock_view_warming=set(), _stock_view_cache={}, _stock_catalog_witness_cache={},
        _device_coordinator=SimpleNamespace(eject_pending=False, busy=False),
        _device_profiles={}, _device_mount_tokens={}, _dual_batch_total_tracks=0,
        tag_controller=SimpleNamespace(owned=False,pending=[]),
        _stored_stock_model=lambda *args: "MA446",
        _stock_write_access=lambda *args: object(),
        _stock_device_key=lambda *args: "identity",
        _sync_playlists=lambda: [Playlist('custom', [], [], 'custom')],
        _device_operation_owned=lambda: False,
        _defer_device_operation_until_scan_finishes=lambda *args: False,
        _release_eject_ready=mock.Mock(), _validate_export_target=mock.Mock(),
        _set_device_progress=mock.Mock(), _start_pulse=mock.Mock(),
        _start_device_worker=mock.Mock(), _set_status_count=mock.Mock(),
        _on_dual_ipod_plan_progress=mock.Mock(),
        _schedule_stock_artwork=mock.Mock(), _stock_art_worker=None,
        cancel_btn=mock.Mock(), progress=mock.Mock(),
        stock_art_cache=object(), art_cache=object(),
        stock_ipod_shadow_store=mock.Mock(),
        stock_catalog_witness_store=mock.Mock(),
        rockbox_media_cache=object(), rockbox_art_cache=object(),
        unification_controller=SimpleNamespace(state=lambda: None),
    )
    value._deliver_device_scan_callbacks = MethodType(MainWindow._deliver_device_scan_callbacks, value)
    value._retire_device_scan_worker = MethodType(MainWindow._retire_device_scan_worker, value)
    return value


def admitted_capture(owner,playlists):
    """Worker-input fixture after the separately tested checkpoint admission."""
    return SimpleNamespace(snapshot=capture_export(owner.lib,playlists,
        extra=owner._sync_playlists(),titles=owner.titles,artists=owner.overrides,complete=True),
        require_current=mock.Mock(),expected_manifest=None,expected_manifest_absent=False,
        verify_worker=mock.Mock(),artwork=None,
        track_total=sum(len(owner.lib.resolve(playlist)) for playlist in playlists))


class IpodDesktopIOTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_dashboard_and_status_share_one_worker_capture(self):
        owner = host()
        facts = devices.DeviceFacts(volume_uuid="uuid", filesystem_name="msdos")
        capture = object()
        status = SimpleNamespace(state="up_to_date", detail="ok", local_changes=0, committed_at="now")
        with (
            mock.patch("desktop.app.device_facts", return_value=facts) as probe,
            mock.patch("soundbox.stock_ipod_capture.capture_stock_ipod_native", return_value=capture) as read,
            mock.patch("soundbox.stock_ipod_info.read_stock_ipod_snapshot", return_value=Snapshot()) as snapshot,
            mock.patch("soundbox.stock_ipod.inspect_stock_ipod_sync_status", return_value=status) as inspect,
        ):
            MainWindow._read_stock_device_view(owner, "/fixture/ipod")
            probe.assert_not_called()
            read.assert_not_called()
            worker = owner._stock_view_worker
            worker.run()
            self.assertEqual(worker.result[0].sync_state, "up_to_date")
            probe.assert_called_once_with("/fixture/ipod")
            read.assert_called_once_with("/fixture/ipod", include_artwork=True)
            self.assertIs(snapshot.call_args.kwargs["native_capture"], capture)
            self.assertIs(inspect.call_args.kwargs["native_capture"], capture)
            owner.stock_catalog_witness_store.load.assert_called_once_with("identity")
            self.assertIs(worker.result[2], owner.stock_catalog_witness_store.load.return_value)
            worker.deleteLater()

    def test_stock_sync_reads_shadow_only_in_worker_without_reresolving_admitted_selection(self):
        owner = host()
        selected=Playlist('one',[],keys=[])
        capture=admitted_capture(owner,[selected])
        shadow = SimpleNamespace(playlists={"cust:one": {"name": "one"}}, sources={})
        owner.stock_ipod_shadow_store.load.return_value = shadow
        with (
            mock.patch("desktop.app.device_facts", return_value=devices.DeviceFacts()) as probe,
            mock.patch("soundbox.manifest.managed_current_playlists", return_value=["selected"]) as select,
            mock.patch("soundbox.stock_ipod.plan_stock_ipod_sync", return_value="plan") as plan,
        ):
            MainWindow._begin_stock_ipod_sync(owner, "/fixture/ipod", [selected], reconcile=True,
                                             _export_capture=capture)
            owner.stock_ipod_shadow_store.load.assert_not_called()
            select.assert_not_called()
            probe.assert_called_once_with("/fixture/ipod", cached_only=True)
            worker = owner._stock_plan_worker
            worker.run()
            self.assertEqual(worker.result, "plan")
            self.assertEqual(plan.call_args.args[1], [selected])
            self.assertEqual(plan.call_args.kwargs["managed_playlist_keys_before"], ("cust:one",))
            select.assert_not_called()
            self.assertIsNot(plan.call_args.args[0],owner.lib)
            self.assertEqual(plan.call_args.args[0].source,str(owner.lib.source))
            worker.deleteLater()

    def test_dual_sync_uses_admitted_selection_without_ui_manifest_reads(self):
        owner = host()
        selected=Playlist('one',[],keys=[])
        capture=admitted_capture(owner,[selected])
        with (
            mock.patch("desktop.app.device_facts", return_value=devices.DeviceFacts()) as probe,
            mock.patch("soundbox.manifest.load_composite", return_value={"playlists": {}}) as read,
            mock.patch("soundbox.dual_ipod.plan_dual_ipod_sync", return_value="plan") as plan,
        ):
            MainWindow._begin_dual_ipod_sync(owner, "/fixture/ipod", [selected], reconcile=True, confirmed=True,
                                            _export_capture=capture)
            read.assert_not_called()
            probe.assert_called_once_with("/fixture/ipod", cached_only=True)
            worker = owner._dual_plan_worker
            worker.run()
            self.assertEqual(worker.result, "plan")
            self.assertEqual(plan.call_args.args[1],[selected])
            self.assertEqual(plan.call_args.kwargs["extra_playlists"], tuple(owner._sync_playlists()))
            read.assert_not_called()  # The backend owns the single native capture.
            worker.deleteLater()

    def test_finalization_waits_for_finished_delivery_after_run_returns(self):
        owner = host()
        worker = SimpleNamespace(isRunning=lambda: False, finished=mock.Mock())
        owner._dev_scan_worker = worker
        owner._refresh_devices = mock.Mock()
        complete = mock.Mock()
        MainWindow._finish_device_surface_refresh(owner, complete)
        complete.assert_not_called()
        self.assertTrue(owner._exporting)
        worker.finished.connect.assert_not_called()
        worker._surface_refresh_callbacks[0]()
        complete.assert_called_once_with()

    def test_visible_manifest_becomes_a_backend_precondition(self):
        owner = host()
        with (
            mock.patch("desktop.app.device_facts", return_value=devices.DeviceFacts()),
            mock.patch("soundbox.manifest.load_composite", side_effect=AssertionError("duplicate capture")),
            mock.patch("soundbox.dual_ipod.plan_dual_ipod_sync", return_value="plan") as plan,
        ):
            MainWindow._begin_dual_ipod_sync(
                owner, "/fixture/ipod", [], reconcile=True,
                expected_manifest={"generation": 1})
            worker = owner._dual_plan_worker
            worker.run()
            self.assertEqual(worker.result, "plan")
            self.assertEqual(plan.call_args.kwargs["expected_manifest"], {"generation": 1})
            worker.deleteLater()

    def test_stock_and_dual_planning_use_captured_inventory_after_library_replacement(self):
        for mode in ('stock', 'dual'):
            with self.subTest(mode=mode):
                owner = host()
                selected = Playlist('Selected', [], ['one', 'one'], 'selected')
                retained = Track('retained', 'Retained', 'Artist', 'file:///source/retained.wav')
                track = Track('one', 'Before', 'Artist', 'file:///source/one.wav')
                original = owner.lib = Library({'one': track, 'retained': retained},
                    [selected], '/source/master.db', source_library_id='before')
                owner.titles = {'one': 'Saved'}
                capture=admitted_capture(owner,[selected])
                target = f'/fixture/{mode}'
                with mock.patch('desktop.app.device_facts', return_value=devices.DeviceFacts()), \
                        mock.patch(f'soundbox.{"stock_ipod" if mode == "stock" else "dual_ipod"}.plan_{mode}_ipod_sync',
                                   return_value='plan') as plan:
                    if mode == 'stock':
                        MainWindow._begin_stock_ipod_sync(owner, target, [selected],_export_capture=capture)
                        worker = owner._stock_plan_worker
                    else:
                        MainWindow._begin_dual_ipod_sync(owner, target, [selected],_export_capture=capture)
                        worker = owner._dual_plan_worker
                    selected.keys.clear(); track.name = 'Later'; owner.titles['one'] = 'Later'
                    original.tracks_by_id.clear()
                    owner.lib = Library({}, [], '/other/master.db', source_library_id='after')
                    worker.run()
                    frozen, selection = plan.call_args.args[:2]
                    self.assertEqual(set(frozen.tracks_by_id), {'one', 'retained'})
                    self.assertEqual(frozen.tracks_by_id['one'].name, 'Before')
                    self.assertEqual(frozen.source_library_id, 'before')
                    self.assertEqual(selection[0].keys, ['one', 'one'])
                    self.assertEqual(plan.call_args.kwargs['title_overrides'], {'one': 'Saved'})
                    worker.deleteLater()

    def test_scan_completion_publishes_rows_and_retires_before_finalization(self):
        owner = host()
        events = []
        worker = SimpleNamespace(results=[], retry_discovery=False,
                                 _surface_refresh_callbacks=[])
        owner._dev_scan_worker = worker
        owner.settings = {"cdj_my_settings": True}
        owner._usb_view_cache = {}
        owner._finish_worker = lambda attr, value: (events.append("retired"), setattr(owner, attr, None))
        owner._on_devices_scanned = lambda *args, **kwargs: events.append("published")
        worker._surface_refresh_callbacks.append(lambda: events.append("finalized"))
        MainWindow._finish_device_scan_worker(owner, worker)
        self.assertEqual(events, ["retired", "published", "finalized"])
        self.assertIsNone(owner._dev_scan_worker)
        self.assertEqual(worker._surface_refresh_callbacks, [])

    def test_final_scan_queues_new_ipod_without_starting_owner_after_success(self):
        owner = host()
        profile = SimpleNamespace(has_stock_ipod=True)
        owner._device_profiles = {"/fixture/second": profile}
        owner._stock_view_cache = {}
        owner._stock_view_warming = {"/fixture/second"}
        owner._stock_view_pending = {"/fixture/second": profile}
        owner._EJECT_BLOCKING_WORKERS = ()
        owner._device_coordinator.active = None
        owner._device_coordinator.stopping = False
        owner._pending_device_operation = None
        owner._deleting = False
        owner._exporting = False
        owner._read_stock_device_view = mock.Mock()
        def complete():
            self.assertFalse(MainWindow._device_operation_owned(owner))
        worker = SimpleNamespace(results=[], retry_discovery=False,
                                 new_paths=("/fixture/second",),
                                 _surface_refresh_callbacks=[complete])
        owner._dev_scan_worker = worker
        owner.settings = {"cdj_my_settings": True}
        owner._usb_view_cache = {}
        owner._finish_worker = lambda attr, value: setattr(owner, attr, None)
        owner._on_devices_scanned = mock.Mock()
        MainWindow._finish_device_scan_worker(owner, worker)
        owner._read_stock_device_view.assert_not_called()
        self.assertIs(owner._stock_view_pending["/fixture/second"], profile)

    def test_cancelled_scan_failure_callback_retires_unstarted_warming(self):
        from threading import Event
        owner = host()
        owner._stock_view_warming = {"/fixture/second"}
        owner._device_profiles = {"/fixture/second": object()}
        cancellation = Event()
        cancellation.set()
        observed = []
        worker = SimpleNamespace(results=None, cancellation=cancellation,
                                 retry_discovery=False,
                                 _surface_refresh_callbacks=[lambda: observed.append(bool(owner._stock_view_warming))])
        owner._dev_scan_worker = worker
        owner._finish_worker = lambda attr, value: setattr(owner, attr, None)
        MainWindow._finish_device_scan_worker(owner, worker)
        self.assertEqual(observed, [False])
        self.assertIsInstance(owner._device_finalization_error, RuntimeError)
        self.assertEqual(worker._surface_refresh_callbacks, [])
        self.assertEqual(owner._stock_view_warming, {"/fixture/second"})

    def test_cached_hardware_facts_never_start_a_probe(self):
        path = "/fixture/ipod"
        with (
            mock.patch.dict(devices._INFO_CACHE, {path: {"MediaType": "iPod", "VolumeUUID": "uuid", "DeviceIdentifier": "disk-fixture"}}),
            mock.patch.dict(devices._IPOD_FACTS_CACHE, {path: {"hardware_serial": "serial", "firmware_version": "1.3",
                                                         "_device_identifier": "disk-fixture", "_volume_uuid": "uuid"}}),
            mock.patch.object(devices, "_classic_ipod_facts", side_effect=AssertionError("hardware probe")),
            mock.patch.object(devices, "_is_external", side_effect=AssertionError("mount probe")),
        ):
            facts = devices.device_facts(path, cached_only=True)
            self.assertEqual(facts.hardware_serial, "serial")
            self.assertEqual(facts.firmware_version, "1.3")
            self.assertEqual(facts.volume_uuid, "uuid")

    def test_ipod_info_never_opens_editable_stale_desktop_selection(self):
        owner = SimpleNamespace(
            stack=SimpleNamespace(currentIndex=lambda: 1),
            device_content_stack=SimpleNamespace(currentIndex=lambda: 1),
            _flash_status=mock.Mock(),
            _selected_track=mock.Mock(side_effect=AssertionError("stale desktop selection")))
        with mock.patch("desktop.track_info._exec_info") as show:
            TrackInfoMixin._open_track_info(owner)
            show.assert_not_called()
        owner._selected_track.assert_not_called()
        owner._flash_status.assert_called_once()

    def test_dashboard_write_access_uses_observed_format_without_model_or_recovery(self):
        from soundbox.stock_ipod_format import DATABASE_PROFILE, StockWriteFormat
        owner = host()
        owner._stored_stock_model = lambda *args: ""
        observed = StockWriteFormat(DATABASE_PROFILE)
        owner._stock_view_cache = {"/fixture/ipod": (SimpleNamespace(stock_format=observed), None)}
        owner._remember_stock_model = mock.Mock()
        with (
            mock.patch("soundbox.stock_ipod_info.confirmed_model_from_recovery",
                       side_effect=AssertionError("GUI recovery read")),
            mock.patch("desktop.app.device_facts", return_value=devices.DeviceFacts(writable=True)),
            mock.patch("desktop.app.device_fs", return_value='MS-DOS (FAT32)'),
        ):
            self.assertIs(MainWindow._stock_write_access(owner, "/fixture/ipod"), observed)
        owner._remember_stock_model.assert_not_called()

    def test_manual_favorites_offers_sync_after_added_songs_without_resolving_audio(self):
        owner=host()
        owner.lib.playlists=[Playlist('FAVORITES',[],['added','retained'])]
        owner._stock_view_cache={'/fixture/ipod':(SimpleNamespace(
            identity_key='header-identity',
            playlists=(StockIpodPlaylistSnapshot(10,'Favorites','Playlist',False,()),)),None)}
        with mock.patch.object(Library,'resolve',side_effect=AssertionError('Header scanned songs')):
            self.assertTrue(MainWindow._stock_has_sync_source(owner,'/fixture/ipod'))
        owner.lib.playlists.append(Playlist(' Favorites ',[],['other']))
        self.assertFalse(MainWindow._stock_has_sync_source(owner,'/fixture/ipod'))

    def test_adopted_custom_name_is_one_source_and_virtual_view_affordance_never_sorts(self):
        owner=host();owner.lib.playlists=[Playlist('FAVORITES',[],['one'],'native',export_key='cust:FAVORITES')]
        owner.custom={'FAVORITES':['one']}
        owner._stock_view_cache={'/fixture/ipod':(SimpleNamespace(
            identity_key='header-identity',
            playlists=(StockIpodPlaylistSnapshot(10,'Favorites','Playlist',False,()),)),None)}
        owner._sync_playlists=mock.Mock(side_effect=AssertionError('Header sorted all views'))
        self.assertTrue(MainWindow._stock_has_sync_source(owner,'/fixture/ipod'))
        owner._stock_view_cache['/fixture/ipod'][0].playlists=(
            StockIpodPlaylistSnapshot(10,'Artists','Playlist',False,()),)
        self.assertTrue(MainWindow._stock_has_sync_source(owner,'/fixture/ipod'))
        owner._sync_playlists.assert_not_called()

    def test_header_sync_dispatch_preserves_selected_device_instead_of_export_dialog(self):
        owner=host();owner._current_device_path='/fixture/ipod'
        owner._device_profiles={'/fixture/ipod':SimpleNamespace(kind='stock_ipod')}
        owner._sync_device=mock.Mock();owner._show_export_dialog=mock.Mock()
        with mock.patch('desktop.stock_catalog_sync.needs_sync',return_value=True) as pending:
            MainWindow._device_primary_action(owner)
        pending.assert_called_once_with(owner,'/fixture/ipod')
        owner._sync_device.assert_called_once_with('/fixture/ipod')
        owner._show_export_dialog.assert_not_called()

    def test_observed_device_display_history_never_synchronously_checkpoints_library(self):
        from tests.test_device_identity import snapshot
        owner=host();owner.settings={};owner._current_device_path='/other/device'
        owner._stock_view_failures={};owner._finish_ipod_loading_status=mock.Mock()
        owner._update_device_action_state=mock.Mock();owner._schedule_settings_save=mock.Mock()
        owner._save_state=mock.Mock(side_effect=AssertionError('GUI library checkpoint'))
        result=snapshot()
        MainWindow._on_stock_device_view(owner,str(result.target),(result,None,None))
        owner._schedule_settings_save.assert_called_once_with('stock_ipod_identity')
        self.assertEqual(owner.settings['stock_ipod_identity'][result.identity_key]['serial_number'],result.serial_number)
        self.assertEqual(owner._stock_view_cache[str(result.target)][0],result)
        owner._save_state.assert_not_called()

    def test_cached_device_presentation_preserves_playlist_binding_but_worker_miss_retires_it(self):
        from tests.test_device_identity import snapshot
        from soundbox.stock_ipod_catalog_state import StockCatalogWitness
        from soundbox.stock_ipod_playlist_binding import PlaylistBinding
        from soundbox.stock_ipod_info import StockIpodPlaylistSnapshot
        owner=host();owner.settings={};owner._current_device_path='/other/device'
        owner._stock_view_failures={};owner._finish_ipod_loading_status=mock.Mock()
        owner._update_device_action_state=mock.Mock();owner._schedule_settings_save=mock.Mock()
        result=replace(snapshot(),playlists=(StockIpodPlaylistSnapshot(123,'Favorites','Playlist',False,()),))
        dev=str(result.target)
        witness=StockCatalogWitness((str(owner.lib.source),owner.lib.source_kind,owner.lib.source_library_id),result.identity_key,'a'*64,(),
            bindings=(PlaylistBinding('favorites','','',123,'Favorites',()),))
        MainWindow._on_stock_device_view(owner,dev,(result,None,witness))
        self.assertIs(owner._stock_catalog_witness_cache[dev],witness)
        self.assertEqual(owner._stock_view_cache[dev][0].sync_state,'catalog_linked')
        MainWindow._on_stock_device_view(owner,dev,owner._stock_view_cache[dev])
        self.assertIs(owner._stock_catalog_witness_cache[dev],witness)
        MainWindow._on_stock_device_view(owner,dev,(result,None,None))
        self.assertIsNone(owner._stock_catalog_witness_cache[dev])
