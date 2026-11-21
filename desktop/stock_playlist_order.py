"""Automatic stock-iPod drag ordering on the existing joined device lane."""
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtWidgets import QMessageBox

from soundbox.operation_control import OperationCancelled
from .devices import device_facts, device_fs
from .workers import OneShotTask


@dataclass(frozen=True)
class OrderRequest:
    device: str
    ids: tuple[int, ...]
    mount: object
    snapshot: object
    token: object

    def current(self, host):
        cached = host._stock_view_cache.get(self.device)
        return bool(not host._shutting_down and not host._state_maintenance
            and getattr(host._device_profiles.get(self.device), 'kind', '') == 'stock_ipod'
            and self.mount is not None and host._device_coordinator.mounts.get(self.device) == self.mount
            and cached is not None and cached[0].identity_key == self.snapshot.identity_key
            and cached[0].database_sha256 == self.snapshot.database_sha256
            and Path(cached[0].target) == Path(self.device))


def request_order(host, ids, *, request=None):
    pages = host.stock_ipod_pages
    if request is None:
        dev = host._current_device_path
        cached = host._stock_view_cache.get(dev)
        token = pages.playlist_order_token
        try:
            values = tuple(int(value) for value in ids)
            if (not values or len(set(values)) != len(values)
                    or any(str(value) != str(raw) or not 0 < value < 2**64 for raw, value in zip(ids, values))):
                raise ValueError('Playlist order contains invalid native identities')
            if cached is None:
                raise ValueError('Refresh the iPod before moving its playlists')
            request = OrderRequest(dev, values, host._device_coordinator.mounts.get(dev), cached[0], token)
        except (ValueError, TypeError) as exc:
            pages.restore_playlist_order(token)
            host._flash_status(str(exc), 15)
            return
    if not request.current(host) or host._device_operation_owned():
        pages.restore_playlist_order(request.token)
        host._flash_status('The iPod changed or is busy; move its playlists again when ready', 15)
        return
    def resume():
        request_order(host, (), request=request)
    # Rejected waits own only this optimistic drop, never another device's UI.
    resume._device_wait_rejected = lambda: pages.restore_playlist_order(request.token)
    if host._defer_device_operation_until_scan_finishes(resume, target=request.device):
        return
    profile = host._device_profiles.get(request.device)
    if host._stock_write_access(request.device, profile) is None or not request.current(host):
        pages.restore_playlist_order(request.token)
        return
    try:
        host._validate_export_target(request.device, device_facts(request.device, cached_only=True))
    except (OSError, ValueError, RuntimeError) as exc:
        _failed(host, request, exc)
        return
    host._release_eject_ready()
    store = host.stock_ipod_shadow_store
    witness_store = host.stock_catalog_witness_store
    witness = host._stock_catalog_witness_cache.get(request.device)
    def plan():
        from soundbox.stock_playlist_order import plan_stock_playlist_order
        return plan_stock_playlist_order(request.device, request.ids,
            snapshot=request.snapshot, model_number=str(getattr(profile, 'model', '') or ''),
            filesystem=device_fs(request.device), usb_family=getattr(request.snapshot, 'usb_family', ''),
            firewire_guid=getattr(request.snapshot, 'firewire_guid', '') or request.snapshot.usb_serial,
            shadow_store=store, catalog_witness=witness)
    def planned(result):
        if isinstance(result, Exception):
            _failed(host, request, result); return
        if not request.current(host):
            _failed(host, request, RuntimeError('The iPod changed before its order was saved')); return
        def commit():
            from soundbox.stock_playlist_order import commit_stock_playlist_order
            from .export_dialog import persist_ipod_export_witness
            return persist_ipod_export_witness(commit_stock_playlist_order(result), witness_store,
                                               request.snapshot.identity_key)
        _start(host, request, commit, '_stock_commit_worker', 'commit-order',
               lambda outcome: _completed(host, request, outcome))
    _start(host, request, plan, '_stock_plan_worker', 'plan-order', planned)


def _start(host, request, operation, attribute, intent, complete):
    worker = OneShotTask(operation)
    setattr(host, attribute, worker)
    host._exporting = True
    host._set_device_progress(0 if intent.startswith('plan') else 150)
    host._start_pulse('Saving iPod playlist order')
    delivered = False
    def completed(result):
        nonlocal delivered
        delivered = True
        if intent.startswith('plan') and worker.cancellation.is_set() and not isinstance(result, Exception):
            result = OperationCancelled('Playlist order cancelled before writing')
        complete(result)
    def retire():
        owned = getattr(host, attribute, None) is worker
        if intent.startswith('plan') and worker.cancellation.is_set() and not isinstance(worker.result, Exception):
            worker.result = OperationCancelled('Playlist order cancelled before writing')
        host._finish_device_result_worker(attribute, worker, completed)
        if owned and not delivered and host._device_coordinator.active is None:
            _failed(host, request, RuntimeError('The connected iPod changed; playlist ordering stopped'))
    worker.finished.connect(retire)
    try:
        host._start_device_worker(worker, request.device, intent)
    except Exception as exc:
        host._finish_worker(attribute, worker)
        _failed(host, request, exc)


def _failed(host, request, error):
    from .export_dialog import device_failure_text
    host.stock_ipod_pages.restore_playlist_order(request.token)
    if host._device_coordinator.active is not None:
        return
    host._exporting = False; host._cancelling = False
    if host._shutting_down:
        return
    host._stop_pulse(force=True)
    host.progress.setVisible(False); host.cancel_btn.setVisible(False)
    host._update_device_action_state()
    if isinstance(error, OperationCancelled) and not getattr(error, 'committed', False):
        host._flash_status('Playlist order cancelled — previous order kept', 15)
    else:
        QMessageBox.warning(host, 'Playlist order could not be saved', device_failure_text(error))
    if getattr(error, 'committed', False):
        host._finalize_ipod_operation(request.device, lambda: None)


def _completed(host, request, result):
    if isinstance(result, Exception):
        _failed(host, request, result); return
    host._exporting = False; host._cancelling = False
    host._stop_pulse(force=True)
    host._publish_ipod_export_witness(request.device, result)
    def finished():
        host._device_completion_message('Playlist order saved', 'iPod playlist order saved.',
                                        attention=bool(result.catalog_warning), issues=(result.catalog_warning,)
                                        if result.catalog_warning else ())
        host._flash_status('iPod playlist order saved')
    host._finalize_ipod_operation(request.device, finished)
