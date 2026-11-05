"""iPod presentation never probes a volume before its owned worker runs."""
from dataclasses import dataclass
from types import MethodType, SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtWidgets import QApplication

from desktop.app import MainWindow
from desktop import devices
from desktop.track_info import TrackInfoMixin


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
        lib=object(), overrides={}, titles={}, custom={},
        _state_maintenance=False, _shutting_down=False,
        _stock_view_callbacks={}, _stock_view_pending={},
        _stock_view_worker=None, _dev_scan_worker=None,
        _stock_view_warming=set(), _stock_view_cache={},
        _device_coordinator=SimpleNamespace(eject_pending=False, busy=False),
        _device_profiles={}, _dual_batch_total_tracks=0,
        _stored_stock_model=lambda *args: "MA446",
        _stock_write_model=lambda *args: "MA446",
        _stock_device_key=lambda *args: "identity",
        _sync_playlists=lambda: ["custom"],
        _device_operation_owned=lambda: False,
        _defer_device_operation_until_scan_finishes=lambda *args: False,
        _release_eject_ready=mock.Mock(), _validate_export_target=mock.Mock(),
        _set_device_progress=mock.Mock(), _start_pulse=mock.Mock(),
        _start_device_worker=mock.Mock(), _set_status_count=mock.Mock(),
        _on_dual_ipod_plan_progress=mock.Mock(),
        cancel_btn=mock.Mock(), progress=mock.Mock(),
        stock_art_cache=object(), art_cache=object(),
        stock_ipod_shadow_store=mock.Mock(),
        rockbox_media_cache=object(), rockbox_art_cache=object(),
        unification_controller=SimpleNamespace(state=lambda: None),
    )
    value._deliver_device_scan_callbacks = MethodType(MainWindow._deliver_device_scan_callbacks, value)
    return value


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
            worker.deleteLater()

    def test_stock_sync_resolves_shadow_only_inside_plan_worker(self):
        owner = host()
        shadow = SimpleNamespace(playlists={"cust:one": {"name": "one"}}, sources={})
        owner.stock_ipod_shadow_store.load.return_value = shadow
        with (
            mock.patch("desktop.app.device_facts", return_value=devices.DeviceFacts()) as probe,
            mock.patch("soundbox.manifest.managed_current_playlists", return_value=["selected"]) as select,
            mock.patch("soundbox.stock_ipod.plan_stock_ipod_sync", return_value="plan") as plan,
        ):
            MainWindow._begin_stock_ipod_sync(owner, "/fixture/ipod", None, reconcile=True)
            owner.stock_ipod_shadow_store.load.assert_not_called()
            select.assert_not_called()
            probe.assert_called_once_with("/fixture/ipod", cached_only=True)
            worker = owner._stock_plan_worker
            worker.run()
            self.assertEqual(worker.result, "plan")
            self.assertEqual(plan.call_args.args[1], ["selected"])
            self.assertEqual(plan.call_args.kwargs["managed_playlist_keys_before"], ("cust:one",))
            select.assert_called_once_with({"playlists": shadow.playlists}, owner.lib, ("custom",))
            worker.deleteLater()

    def test_dual_sync_resolves_manifest_only_inside_plan_worker(self):
        owner = host()
        with (
            mock.patch("desktop.app.device_facts", return_value=devices.DeviceFacts()) as probe,
            mock.patch("soundbox.manifest.load_composite", return_value={"playlists": {}}) as read,
            mock.patch("soundbox.dual_ipod.plan_dual_ipod_sync", return_value="plan") as plan,
        ):
            MainWindow._begin_dual_ipod_sync(owner, "/fixture/ipod", None, reconcile=True, confirmed=True)
            read.assert_not_called()
            probe.assert_called_once_with("/fixture/ipod", cached_only=True)
            worker = owner._dual_plan_worker
            worker.run()
            self.assertEqual(worker.result, "plan")
            self.assertIsNone(plan.call_args.args[1])
            self.assertEqual(plan.call_args.kwargs["extra_playlists"], ("custom",))
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
            mock.patch.dict(devices._INFO_CACHE, {path: {"MediaType": "iPod", "VolumeUUID": "uuid"}}),
            mock.patch.dict(devices._IPOD_FACTS_CACHE, {path: {"hardware_serial": "serial", "firmware_version": "1.3"}}),
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

    def test_dashboard_model_confirmation_uses_captured_recovery_result(self):
        owner = host()
        owner._stored_stock_model = lambda *args: ""
        owner._stock_view_cache = {"/fixture/ipod": (Snapshot(), None)}
        owner._remember_stock_model = mock.Mock()
        with mock.patch("soundbox.stock_ipod_info.confirmed_model_from_recovery",
                        side_effect=AssertionError("GUI recovery read")):
            self.assertEqual(MainWindow._stock_write_model(owner, "/fixture/ipod"), "MA446")
        owner._remember_stock_model.assert_called_with("identity", "MA446")
