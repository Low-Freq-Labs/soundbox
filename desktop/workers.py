"""Background QThread workers.

⚠ QThread lifetime rule (see soundbox-gotchas): the OWNER must hold a ref until
the built-in `finished` signal fires (MainWindow._finish_worker) — dropping the
last ref in a custom-signal slot GC's the thread mid-run → SIGABRT.
"""

from __future__ import annotations

import logging
import math
import os
import subprocess
import threading
import time
import weakref
from collections import deque
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QThread, Signal

from soundbox import engine, metadata, rekordbox_db
from soundbox.library import Playlist
from soundbox.export_snapshot import ExportSnapshot, capture_export
from soundbox.providers import ArtworkProvider, provider_operation
from soundbox.operation_control import OperationCancelled, checkpoint, operation_scope
from soundbox.report import SyncReport
if TYPE_CHECKING:
    from soundbox import sync as sync_mod
    from soundbox.stock_artwork import StockArtworkCache

from .devices import (
    DeviceFacts, device_facts, device_profile, device_target_profile, list_devices, refresh_device_mount,
)
from .analysis_fence import AnalysisWriteFence

LOG = logging.getLogger("soundbox")


class ExportWorker(QThread):
    """Processes a growable queue of (device, Playlist) jobs in the background.

    One worker = one EXPORTING bar. New jobs added mid-run grow the total so the
    bar always estimates everything queued.
    """
    progress = Signal(int, int, str)
    finished_all = Signal(list)

    phase_progress = Signal(str, object, object, str)

    def __init__(self, lib, overrides=None, titles=None, art_cache=None,
                 rockbox_media_cache=None, rockbox_art_cache=None):
        super().__init__()
        # Only add(), on the GUI producer, reads this weak fallback. Each queued
        # admission owns immutable bytes; run() never reads the live library.
        self._capture_library = weakref.ref(lib)
        self.overrides = dict(overrides or {})
        self.titles = dict(titles or {})
        self.art_cache = art_cache
        self.rockbox_media_cache = rockbox_media_cache
        self.rockbox_art_cache = rockbox_art_cache
        self._lock = threading.Lock()
        self._jobs: deque[tuple[str, ExportSnapshot, int, int,
            Callable[[],None] | None,Callable[[str],None] | None,bytes | None,bool]] = deque()
        self._total = 0
        self._base_done = 0
        self._stop = False
        self.cancellation = threading.Event()
        self._cancelled_targets: dict[str, None] = {}
        self._reports: list[tuple[str, object]] = []
        self.result: list[tuple[str, object]] | None = None

    def add(self, device: str, playlists: list[Playlist], *, mount_token: int = 0,
            library=None, titles=None, overrides=None, snapshot=None, estimate=None,
            validate_inputs=None,validate_artwork=None,expected_manifest_payload=None,expected_manifest_absent=False):
        if snapshot is None:
            lib = library if library is not None else self._capture_library()
            if lib is None:
                raise RuntimeError('Export source library is no longer available')
            captured = capture_export(lib, playlists,
                titles=self.titles if titles is None else titles,
                artists=self.overrides if overrides is None else overrides)
            estimate = sum(max(1, len(lib.resolve(playlist))) for playlist in playlists)
        else:
            if not isinstance(snapshot, ExportSnapshot) or type(estimate) is not int or estimate < 0:
                raise ValueError('A captured iPod export needs its immutable snapshot and entry count')
            captured = snapshot
            estimate = max(1, estimate)
        with self._lock:
            if self._should_cancel():
                return
            if playlists:
                self._jobs.append((device, captured, mount_token, estimate,validate_inputs,validate_artwork,
                                   expected_manifest_payload,expected_manifest_absent))
                self._total += estimate
        # No emit here: the next per-file progress tick updates the bar at the
        # real current position with the new total, so adding a playlist mid-export
        # smoothly lowers the % instead of jarringly snapping the bar to 0%.

    def cancel(self):
        with self._lock:
            self._stop = True
            self.cancellation.set()
            self._cancelled_targets.update((device, None) for device, *_ in self._jobs)
            self._jobs.clear()

    @property
    def cancellation(self):
        return self._cancellation

    @cancellation.setter
    def cancellation(self, event):
        self._cancellation = event
        if self._stop:
            event.set()

    def _should_cancel(self):
        return self._stop or self.cancellation.is_set()

    def run(self):
        with operation_scope(self.cancellation, self.phase_progress.emit):
            self._run_export()

    def _run_export(self):
        try:
            while True:
                with self._lock:
                    if self._should_cancel():
                        self._cancelled_targets.update((device, None) for device, *_ in self._jobs)
                        self._jobs.clear()
                        break
                    if not self._jobs:
                        break
                    (device,captured,mount_token,est,validate_inputs,validate_artwork,
                     expected_manifest_payload,expected_manifest_absent)=self._jobs.popleft()
                    base = self._base_done
                inputs = captured.materialize()
                playlists = list(inputs.playlists)
                name = playlists[0].name if len(playlists) == 1 else f"{len(playlists)} playlists"

                def cb(d, t, label, _base=base, _name=name, _est=est):
                    with self._lock:
                        tot = self._total
                    # Export counts unique media; the growable queue counts
                    # playlist entries, including shared tracks.
                    done = int(_est * min(1.0, max(0.0, d / max(1, t))))
                    self.progress.emit(_base + done, tot, f"{_name} — {label}")

                try:
                    checkpoint()
                    if mount_token and Path(device).stat().st_dev != mount_token:
                        raise RuntimeError("Queued export target was remounted; confirmation expired")
                    # clean=False: scrub AppleDouble once at end, not per playlist.
                    options={}
                    if inputs.display_rows is not None:options['display_rows']=inputs.display_rows
                    if validate_inputs is not None:
                        validate_inputs()
                        options['validate_inputs']=validate_inputs
                    if validate_artwork is not None:options['validate_artwork']=validate_artwork
                    if expected_manifest_payload is not None:options['expected_manifest_payload']=expected_manifest_payload
                    if expected_manifest_absent:options['expected_manifest_absent']=True
                    rep = engine.export_playlists(
                        inputs.library, playlists, device, dry_run=False, derive_artists=True,
                        on_progress=cb, should_cancel=self._should_cancel, clean=False,
                        artist_overrides=inputs.artists, title_overrides=inputs.titles,
                        art_cache=self.art_cache,
                        rockbox_media_cache=self.rockbox_media_cache,
                        rockbox_art_cache=self.rockbox_art_cache,**options)
                    self._reports.append((device, rep))
                    if (getattr(rep, "cancelled", False)
                            or any(p.errors for p in getattr(rep, "playlists", []))):
                        self.cancel()
                except OperationCancelled:
                    self._reports.append((device, SyncReport(dry_run=False, cancelled=True)))
                    self.cancel()
                except Exception as exc:  # noqa: BLE001 - surface failure in report
                    self._reports.append((device, str(exc) or repr(exc)))
                    self.cancel()
                with self._lock:
                    self._base_done = base + est
                    total = self._total
                self.progress.emit(self._base_done, total, "")
        except Exception as exc:  # never let the thread take down the app
            self._reports.append(("(worker)", repr(exc)))
        with self._lock:
            cancelled_targets = tuple(self._cancelled_targets)
        for device in cancelled_targets:
            if not any(dev == device and getattr(report, 'cancelled', False)
                       for dev, report in self._reports):
                self._reports.append((device, SyncReport(dry_run=False, cancelled=True)))
        # Scrub AppleDouble clutter ONCE per device touched (not per playlist) — but
        # only in the folders actually written this batch (fast on a full device).
        dev_dirs: dict[str, set] = {}
        dev_identities: dict[str, set[tuple[int, int]]] = {}
        unproven: set[str] = set()
        for dev, rep in self._reports:
            identity = getattr(rep, 'mount_identity', None)
            if (isinstance(identity, tuple) and len(identity) == 2
                    and all(type(value) is int for value in identity)):
                dev_identities.setdefault(dev, set()).add(identity)
            else:
                unproven.add(dev)
            wd = getattr(rep, "written_dirs", None)
            if wd:
                dev_dirs.setdefault(dev, set()).update(wd)
        for dev in (() if self._should_cancel() else dict.fromkeys(d for d, _ in self._reports)):
            identities = dev_identities.get(dev, set())
            directories = dev_dirs.get(dev)
            if dev in unproven or len(identities) != 1 or not directories:
                continue
            try:
                engine.clean_device(dev, directories,
                                    expected_mount_identity=next(iter(identities)))
            except Exception:  # noqa: BLE001
                pass
        if self.rockbox_media_cache is not None:
            try:
                self.rockbox_media_cache.clear()
            except Exception as exc:  # noqa: BLE001
                LOG.warning("Rockbox media staging cleanup failed: %s", exc)
        self.result = self._reports
        self.finished_all.emit(self._reports)


class ResyncWorker(QThread):
    """Brings ONE device back in line with rekordbox (re-export changed playlists,
    move renamed ones, prune orphans) off the UI thread."""
    progress = Signal(int, int, str)
    done = Signal(str, object)  # device, SyncReport | error string

    phase_progress = Signal(str, object, object, str)

    def __init__(self, lib, device, overrides=None, titles=None, art_cache=None,
                 rockbox_media_cache=None, rockbox_art_cache=None, extra=None,*,snapshot=None,
                 validate_inputs=None,validate_artwork=None,expected_manifest_payload=None,expected_manifest_absent=False):
        super().__init__()
        if snapshot is not None and not isinstance(snapshot,ExportSnapshot):
            raise ValueError('iPod Sync needs an immutable export snapshot')
        self._snapshot = snapshot if snapshot is not None else capture_export(lib, extra=extra or (), titles=titles,
                                                                             artists=overrides, complete=True)
        self._validate_inputs=validate_inputs
        self._validate_artwork=validate_artwork
        self._expected_manifest_payload=expected_manifest_payload
        self._expected_manifest_absent=expected_manifest_absent
        self.device = device
        self.art_cache = art_cache
        self.rockbox_media_cache = rockbox_media_cache
        self.rockbox_art_cache = rockbox_art_cache
        self._stop = False
        self.cancellation = threading.Event()
        self.result = None

    def cancel(self):
        self._stop = True
        self.cancellation.set()

    @property
    def cancellation(self):
        return self._cancellation

    @cancellation.setter
    def cancellation(self, event):
        self._cancellation = event
        if self._stop:
            event.set()

    def _should_cancel(self):
        return self._stop or self.cancellation.is_set()

    def run(self):
        with operation_scope(self.cancellation, self.phase_progress.emit):
            self._run_export()

    def _run_export(self):
        def cb(d, t, label):
            self.progress.emit(d, t, label)
        try:
            checkpoint()
            inputs = self._snapshot.materialize()
            options={}
            if inputs.display_rows is not None:options['display_rows']=inputs.display_rows
            if self._validate_inputs is not None:
                self._validate_inputs()
                options['validate_inputs']=self._validate_inputs
            if self._validate_artwork is not None:options['validate_artwork']=self._validate_artwork
            if self._expected_manifest_payload is not None:options['expected_manifest_payload']=self._expected_manifest_payload
            if self._expected_manifest_absent:options['expected_manifest_absent']=True
            command = getattr(self, "device_command", None)
            if command is not None and command.target.mount_token:
                if Path(self.device).stat().st_dev != command.target.mount_token:
                    raise RuntimeError("Sync target was remounted; confirmation expired")
            rep = engine.resync_device(
                inputs.library, self.device, on_progress=cb,
                should_cancel=self._should_cancel,
                artist_overrides=inputs.artists, title_overrides=inputs.titles,
                art_cache=self.art_cache,
                rockbox_media_cache=self.rockbox_media_cache,
                rockbox_art_cache=self.rockbox_art_cache,
                extra=list(inputs.extra),**options)
            self.result = rep
            self.done.emit(self.device, rep)
        except OperationCancelled:
            self.result = SyncReport(dry_run=False, cancelled=True)
            self.done.emit(self.device, self.result)
        except Exception as exc:  # noqa: BLE001 - surface, never crash the app
            self.result = str(exc) or repr(exc)
            self.done.emit(self.device, self.result)
        finally:
            if self.rockbox_media_cache is not None:
                try:
                    self.rockbox_media_cache.clear()
                except Exception as exc:  # noqa: BLE001
                    LOG.warning("Rockbox media staging cleanup failed: %s", exc)


class RefreshWorker(QThread):
    """Reads the live rekordbox DB off the UI thread (copy + parse is slow)."""
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, database_path=None):
        super().__init__()
        self.database_path = database_path
        self.native_reader = None
        self.preflight = None
        self.selection_path = None
        self.retained_tracks = {}
        self.metadata_baseline = None
        self.metadata_changed_ids = ()
        self.result = None
        self.error = ""

    def run(self):
        try:
            if self.preflight is not None:
                self.preflight(self.database_path or rekordbox_db.REKORDBOX_DB)
            lib = (self.native_reader.read_live(self.database_path, retain_tracks=self.retained_tracks)
                   if self.native_reader is not None else
                   rekordbox_db.load_live(self.database_path,retain_tracks=self.retained_tracks)
                   if self.retained_tracks else rekordbox_db.load_live(self.database_path))
            if self.metadata_baseline is not None:
                scope, previous = self.metadata_baseline
                same_source = scope == (str(lib.source), lib.source_kind, lib.source_library_id)
                self.metadata_changed_ids = tuple(
                    track.native_id for track in lib.tracks_by_id.values()
                    if not same_source or previous.get(track.native_id) != (
                        track.source_uuid, track.source_native_path or str(track.path), track.name, track.artist))
            else:
                self.metadata_changed_ids = tuple(track.native_id for track in lib.tracks_by_id.values())
        except Exception as exc:  # noqa: BLE001
            self.error = f"{type(exc).__name__}: {exc}"
            self.failed.emit(self.error)
            return
        self.result = lib
        self.done.emit(lib)


class DeviceScanWorker(QThread):
    """Enumerate connection facts and iPod drift off the UI thread.

    CDJ USBs have no drift state; their catalogs load only on browser requests.
    Each eligible connection may check fixed setup markers once, without parsing
    or opening the native catalog payloads.

    Touching a removable volume (os.statvfs, reading the manifest) can BLOCK for
    seconds on macOS fskit whenever the stick is busy — during our own export, or
    when the user is copying/clearing files in Finder. Doing it on the main-thread
    timer beachballs the whole app (rainbow wheel) and freezes the FINALIZING
    animation. Running it here keeps the UI responsive no matter what is hammering
    the disk; results are published only from the main-thread `finished` boundary.
    """
    # `discovered` publishes named, disabled rows after bounded mount probes.
    # Rows become selectable at `finished`; dashboard preload follows separately.
    discovered = Signal(list)  # list[tuple[name, path, free, profile]]
    playlists_requested = Signal(object)  # worker awaiting a GUI-captured snapshot
    done = Signal(list)  # list[tuple[name, path, free, drift, profile]]

    def __init__(self, lib, extra=None, *, artist_overrides=None,
                 title_overrides=None, stock_shadow_store=None,
                 stock_models=None, known_paths=None,
                 known_mount_tokens=None, usb_views=None, usb_requests=None,
                 defer_playlists=False, stock_catalog_witnesses=None, stock_catalog_store=None,
                 art_cache=None, usb_setup_known=None):
        super().__init__()
        self.lib = lib
        self.extra = extra or []  # custom SoundBox playlists (not in lib.playlists)
        self.artist_overrides = dict(artist_overrides or {})
        self.title_overrides = dict(title_overrides or {})
        self.stock_shadow_store = stock_shadow_store
        self.stock_models = dict(stock_models or {})
        self.known_paths = {str(path) for path in (known_paths or ())}
        self.known_mount_tokens = {
            str(path): int(token)
            for path, token in (known_mount_tokens or {}).items()
        }
        self.usb_views = dict(usb_views or {})
        self.usb_requests = frozenset(usb_requests or ())
        self.usb_snapshots = {}
        self.usb_setup_known = frozenset(usb_setup_known or ())
        self.usb_setup_results = {}
        self.mount_tokens: dict[str, int] = {}
        self.new_paths: tuple[str, ...] = ()
        self.results: list | None = []
        self.retry_discovery = True
        self.stock_detected = False
        self.cancellation = threading.Event()
        self._defer_playlists = defer_playlists
        self._playlists_ready = threading.Event()
        self._playlist_capture_error = None
        self.drift_captures = {}
        self._drift_capture_errors = set()
        self.display_rows = None
        self.stock_catalog_witnesses = dict(stock_catalog_witnesses or {})
        self.stock_catalog_store = stock_catalog_store
        self.stock_status = {}
        self.stock_comparison_errors = {}
        self._art_observation = None
        self._art_observation_token = None
        self.art_cache = art_cache
        self.captured_source_scope = ((str(lib.source),lib.source_kind,lib.source_library_id)
                                      if lib is not None and hasattr(lib,'source') else None)

    def provide_playlists(self, extra, *, lib, artist_overrides,
                          title_overrides, error=None, stock_shadow_store=None,display_rows=None,
                          stock_catalog_witnesses=None,stock_catalog_store=None,art_cache=None):
        """Publish one GUI-captured snapshot before the worker reads drift."""
        if not self.cancellation.is_set():
            self.extra = list(extra or ())
            self.lib = lib
            self.artist_overrides = dict(artist_overrides or {})
            self.title_overrides = dict(title_overrides or {})
            self.stock_shadow_store = stock_shadow_store
            self.art_cache = art_cache
            self.stock_catalog_witnesses = dict(stock_catalog_witnesses or {})
            self.stock_catalog_store = stock_catalog_store
            self.captured_source_scope = ((str(lib.source),lib.source_kind,lib.source_library_id)
                                         if lib is not None and hasattr(lib,'source') else None)
            self.display_rows = ({key:dict(row) for key,row in display_rows.items()}
                                 if display_rows is not None else None)
            self._playlist_capture_error = error
        self._playlists_ready.set()

    def cancel(self):
        self.cancellation.set()
        self._playlists_ready.set()

    def comparison_current(self, art_cache):
        """Queued observations still belong to the captured publisher/revision."""
        if self.art_cache is not None and self.art_cache is not art_cache:return False
        return (self._art_observation_token is None or
                self._art_observation.selection_current(self._art_observation_token))

    def _capture_playlists(self):
        """Wait only after detection; cancellation never needs GUI delivery."""
        self.playlists_requested.emit(self)
        while not self._playlists_ready.wait(0.05):
            if self.cancellation.is_set() or self.isInterruptionRequested():
                return False
        return not (self.cancellation.is_set() or self.isInterruptionRequested())

    def _effective_profile(self, path: str, profile):
        """Enrich the display label; remembered models grant no format capability."""
        if not getattr(profile, "has_stock_ipod", False):
            return profile
        from soundbox.stock_ipod import normalize_model_number, supports_stock_write
        from soundbox.stock_ipod_info import stock_ipod_device_key

        model = normalize_model_number(getattr(profile, "model", ""))
        if supports_stock_write(model):
            return profile
        facts = device_facts(path)
        identity_key = stock_ipod_device_key(
            facts.volume_uuid,
            int(getattr(profile, "library_persistent_id", 0) or 0),
        )
        model = normalize_model_number(self.stock_models.get(identity_key, ""))
        if not supports_stock_write(model):
            return profile
        return replace(profile, model=model)

    def _probe_usb_setup(self, path, profile):
        """Observe fixed setup markers once per eligible mounted USB identity."""
        usb_profile = (device_target_profile(profile, 'ipod_usb')
                       if getattr(profile, 'has_stock_ipod', False) else profile)
        if (usb_profile is None or usb_profile.kind not in {'generic_usb', 'ipod_usb'}
                or not usb_profile.cdj_export):
            return
        token = self.mount_tokens.get(path, 0)
        facts = device_facts(path, cached_only=True)
        if (not token or not facts.volume_uuid or facts.writable is not True
                or (usb_profile.kind == 'generic_usb' and facts.media_type.casefold() == 'ipod')):
            return
        identity = (path, token, facts.volume_uuid, usb_profile.kind)
        if identity in self.usb_setup_known:
            return
        try:
            before = os.stat(path, follow_symlinks=False)
        except OSError:
            return
        if before.st_dev != token:
            return
        from soundbox.cdj_usb import setup_readiness
        try:
            with operation_scope(self.cancellation):
                readiness = setup_readiness(path, token=token, volume_uuid=facts.volume_uuid,
                                            target_role=usb_profile.kind)
        except OperationCancelled:
            self.cancel()
            return
        if self.cancellation.is_set() or self.isInterruptionRequested():
            return
        try:
            current = os.stat(path, follow_symlinks=False)
        except OSError:
            return
        if (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino):
            return
        if (readiness.mount_identity is not None
                and (current.st_dev, current.st_ino) != readiness.mount_identity):
            return
        self.usb_setup_results[path] = (*identity, readiness)

    def run(self):
        started = time.monotonic()
        from .devices import discovery_needs_retry
        devices = list_devices()
        self.retry_discovery = discovery_needs_retry()
        verified_devices = []
        for name, path, free in devices:
            try:
                self.mount_tokens[path] = int(os.stat(path).st_dev)
            except OSError:
                self.mount_tokens[path] = 0
            known_token = self.known_mount_tokens.get(path, 0)
            if (known_token and self.mount_tokens[path]
                    and self.mount_tokens[path] != known_token):
                if not refresh_device_mount(path):
                    self.retry_discovery = True
                    continue
            verified_devices.append((name, path, free))
        devices = verified_devices
        raw_profiles = [
            (name, path, free, device_profile(path))
            for name, path, free in devices
        ]
        self.stock_detected = any(
            getattr(profile, 'has_stock_ipod', False)
            for _name, _path, _free, profile in raw_profiles)
        self.new_paths = tuple(
            path for _name, path, _free, _profile in raw_profiles
            if (
                path not in self.known_paths
                or (
                    self.known_mount_tokens.get(path)
                    and self.mount_tokens.get(path)
                    != self.known_mount_tokens[path]
                )
            ))
        self.discovered.emit(raw_profiles)
        if self.new_paths:
            LOG.info(
                "Device discovery detected %d new mount(s) in %.2fs",
                len(self.new_paths), time.monotonic() - started)
        # CDJ USBs are destinations for explicit playlist exports. Connection
        # never compares the source library against their existing contents.
        needs_playlists = any(profile.kind != 'generic_usb' for _, _, _, profile in raw_profiles)
        if needs_playlists and self._defer_playlists:
            for _name,path,_free,profile in raw_profiles:
                if profile.kind=='generic_usb':continue
                if self.cancellation.is_set() or self.isInterruptionRequested():
                    self.results=None;return
                try:
                    with operation_scope(self.cancellation):
                        self.drift_captures[path]=engine.capture_device_drift_manifest(path)
                except OperationCancelled:
                    self.results=None;return
                except Exception:self._drift_capture_errors.add(path)
        if needs_playlists and self._defer_playlists and not self._capture_playlists():
            self.results = None
            return
        observed_art_cache=self.art_cache
        artwork_error=None
        try:
            observe=getattr(observed_art_cache,'observation_view',None)
            if needs_playlists and self.lib is not None and observe is not None:
                observed_art_cache=observe(self.lib)
                self._art_observation=observed_art_cache
                self._art_observation_token=observed_art_cache.selection_token(())
        except Exception as exc:  # noqa: BLE001 — an unavailable view leaves status unknown
            artwork_error=exc
        out = []
        for name, path, free, raw_profile in raw_profiles:
            if self.cancellation.is_set() or self.isInterruptionRequested():
                self.results = None
                return
            drift = None
            profile = self._effective_profile(path, raw_profile)
            self._probe_usb_setup(path, profile)
            if self.cancellation.is_set() or self.isInterruptionRequested():
                self.usb_setup_results.clear()
                self.results = None
                return
            # Publish the first usable row with its actual sync status. The
            # disabled discovery row is already visible; dashboard preload must
            # not delay the first status check by another full read/pass.
            if profile.kind != 'generic_usb' and self.lib is not None and self._playlist_capture_error is None:
                try:
                    if artwork_error is not None:raise artwork_error
                    if path in self._drift_capture_errors:
                        raise ValueError('The iPod ownership manifest could not be read')
                    captured=self.drift_captures.get(path)
                    dr = (None if path in self.drift_captures and captured is None else engine.device_drift(
                        self.lib,
                        path,
                        self.extra,
                        artist_overrides=self.artist_overrides,
                        title_overrides=self.title_overrides,
                        display_rows=self.display_rows,
                        captured_manifest=captured,
                        art_cache=observed_art_cache,
                    ))
                    drift = dr.count() if dr else 0
                except Exception as exc:  # noqa: BLE001 - a bad manifest never breaks the poll
                    if profile.kind=='stock_ipod':
                        self.stock_comparison_errors[path]=type(exc).__name__+': '+str(exc)[:240]
                    drift = None
                if (
                    drift == 0
                    and profile.kind == "stock_ipod"
                    and self.stock_shadow_store is not None
                ):
                    try:
                        from soundbox.stock_ipod import (
                            inspect_stock_ipod_sync_status, normalize_model_number,
                        )
                        from soundbox.stock_ipod_info import stock_ipod_device_key

                        facts = device_facts(path)
                        identity_key = stock_ipod_device_key(
                            facts.volume_uuid,
                            int(profile.library_persistent_id or 0),
                        )
                        model = normalize_model_number(
                            profile.model
                            or self.stock_models.get(identity_key, ""))
                        # This is a read-only comparison, not writer admission.
                        # A legacy Video model allowlist must not hide Classic
                        # changes or grant capabilities to an unknown format.
                        witness = self.stock_catalog_witnesses.get(path)
                        if witness is None and self.stock_catalog_store is not None:
                            witness = self.stock_catalog_store.load(identity_key)
                            if witness is not None:
                                self.stock_catalog_witnesses[path] = witness
                        status = inspect_stock_ipod_sync_status(
                            self.lib,
                            path,
                            identity_key=identity_key,
                            model_number=model,
                            serial_number=facts.hardware_serial,
                            shadow_store=self.stock_shadow_store,
                            extra_playlists=tuple(self.extra),
                            catalog_witness=witness,
                            display_rows=self.display_rows,
                            art_cache=observed_art_cache,
                        )
                        self.stock_status[path] = status
                        if status.state == 'comparison_pending':
                            drift = None
                        elif status.state not in {"up_to_date", "untracked"}:
                            drift = max(1, status.local_changes)
                    except Exception as exc:  # noqa: BLE001 - keep polling read-only
                        self.stock_comparison_errors[path]=type(exc).__name__+': '+str(exc)[:240]
                        drift = None
            requested = path in self.usb_requests
            usb_profile = (device_target_profile(profile, 'ipod_usb')
                           if getattr(profile, 'has_stock_ipod', False) else profile)
            if usb_profile is not None and (not getattr(profile, 'has_stock_ipod', False)
                                           or requested or path in self.usb_views):
                from .usb_device_view import UsbSnapshot, read_usb_snapshot, read_native_usb_snapshot
                cached = self.usb_views.get(path)
                token = self.mount_tokens.get(path, 0)
                invalid = cached is None or cached.mount_token != token or path in self.new_paths
                # Native catalogs are read only when the user opens the USB
                # browser or explicitly refreshes it, never for idle status.
                cdj_target = usb_profile.kind in {'generic_usb', 'ipod_usb'}
                if requested or (not cdj_target and invalid):
                    try:
                        reader = read_native_usb_snapshot if cdj_target else read_usb_snapshot
                        cached = reader(path, token, device_facts(path, cached_only=True))
                    except Exception as exc:
                        cached = UsbSnapshot(Path(path), token, DeviceFacts(), error=str(exc))
                elif invalid:
                    cached = None
                if cached is not None:
                    self.usb_snapshots[path] = cached
            out.append((name, path, free, drift, profile))
        if self.cancellation.is_set() or self.isInterruptionRequested():
            self.usb_setup_results.clear()
            self.results = None
            return
        log = LOG.info if out else LOG.debug
        log(
            "Device scan completed for %d mount(s) in %.2fs",
            len(out), time.monotonic() - started)
        # MainWindow publishes this only from QThread.finished, after scan
        # ownership is cleared. `done` remains available to direct worker tests.
        self.results = out
        self.done.emit(out)


class DeleteWorker(QThread):
    """Delete files/folders from a device OFF the UI thread, reporting progress.

    Deleting a big track-folder tree over a slow USB blocks for a while; on the main
    thread that's a rainbow cursor with no feedback. Here it drives the same
    progress bar as an export instead. Stops cleanly between items on cancel."""
    progress = Signal(int, int, str)  # done, total, current name
    done = Signal(object)             # DeviceDeleteReport

    def __init__(
            self, session: sync_mod.DeviceSession,
            relatives: list[Path]):
        super().__init__()
        self.session = session
        self.relatives = relatives
        self._stop = False
        self.result: sync_mod.DeviceDeleteReport | Exception | None = None

    def cancel(self):
        self._stop = True

    def run(self):
        from soundbox import sync as sync_mod

        try:
            self.result = sync_mod.delete_device_paths(
                self.session,
                self.relatives,
                on_progress=lambda done, total, label: self.progress.emit(
                    done, total, label),
                should_cancel=lambda: self._stop,
            )
        except Exception as exc:  # noqa: BLE001 - surface, never crash the app
            self.result = exc
        self.done.emit(self.result)


class ArtFillWorker(QThread):
    """Background trickle: fetch missing cover art for the whole library, slowly,
    one track at a time, cached + resumable. Uses the user's curated artist/title
    so matches (and the artist-avatar fallback) keep improving over time."""
    progress = Signal(int, int)  # done, total
    failed = Signal(str)
    profile_changed = Signal(str)
    cover_changed = Signal(str)

    DELAY = 1.6  # seconds between fetches — polite, never a burst

    def __init__(self, lib, cache, artist_overrides, title_overrides, *,
                 provider: ArtworkProvider | None):
        super().__init__()
        self.lib = lib
        self.cache = cache
        self.artist_overrides = artist_overrides
        self.title_overrides = title_overrides
        self.provider = provider
        self.changed_track_ids: set[str] = set()
        self._stop = False
        self._cancelled = threading.Event()
        self._playback_active = threading.Event()

    def stop(self):
        self._stop = True
        self._cancelled.set()

    def set_playback_active(self, active: bool):
        if active:
            self._playback_active.set()
        else:
            self._playback_active.clear()

    def _wait_for_playback(self):
        import time
        while self._playback_active.is_set() and not self._stop:
            time.sleep(0.05)

    def run(self):
        try:
            self._fill()
        except (OSError, ValueError) as exc:
            LOG.error("Artwork persistence failed: %s", exc)
            self.failed.emit(str(exc))

    def _fill(self):
        if self.provider is None:
            return
        profile_fetch = getattr(self.provider, "fetch_profile_art", None)
        profiles = {}
        hidden = getattr(self.lib, "hidden_ids", set())
        if callable(profile_fetch):
            self._wait_for_playback()
            if self._stop:
                return
            # Eligibility observes one policy snapshot for this pass, rather
            # than reopening its JSON for every existing cover. Publication's
            # cache mutation still checks fresh policy and exact cover bytes.
            excluded_ids = self.cache.source_excluded_ids(
                tid for tid in self.lib.tracks_by_id if tid not in hidden)
            replaced = attempted = 0
            for track in tuple(self.lib.tracks_by_id.values()):
                self._wait_for_playback()
                if self._stop:
                    return
                tid = track.track_id
                if tid in hidden:
                    continue
                entry = self.cache.index.get(tid) or {}
                if tid not in excluded_ids and entry.get("fallback") != "excluded-source":
                    continue
                title = (self.title_overrides or {}).get(tid) or track.name
                artist = metadata.effective_artist(
                    tid, title, track.artist, self.artist_overrides, True,
                    self.lib.artist_registry, stem=track.path.stem)
                if entry.get("fallback") == "excluded-source" and entry.get("fallback_artist") == artist:
                    continue
                snapshot = self.cache.cover_snapshot(tid)
                attempted += 1
                try:
                    if artist not in profiles:
                        with provider_operation(self.provider,lambda:self._stop):
                            profiles[artist] = profile_fetch(artist)
                    result = profiles[artist]
                except Exception as exc:
                    LOG.warning("Artist profile lookup unavailable for %r: %s", artist, exc)
                    profiles[artist] = result = None
                if self._stop:
                    return
                if result and self.cache.apply_excluded_profile(tid, snapshot, result, artist):
                    replaced += 1
                    self.changed_track_ids.add(tid)
                    self.profile_changed.emit(tid)
                self._cancelled.wait(self.DELAY)
            if attempted:
                LOG.info("Excluded artwork profiles: %d replaced / %d checked; unavailable profiles stay hidden", replaced, attempted)
        todo = []
        for t in tuple(self.lib.tracks_by_id.values()):
            if t.track_id in hidden:
                continue  # deleted from SoundBox — don't fetch for it
            if self.cache.status(t.track_id) is not None:
                continue  # already fetched or known-miss
            try:
                if not t.path.is_file():
                    continue  # only tracks we can actually export
            except OSError:
                continue
            todo.append(t)
        total = len(todo)
        LOG.info("Art fill: %d tracks need cover art", total)
        ok = err = 0
        for i, t in enumerate(todo, 1):
            self._wait_for_playback()
            if self._stop:
                break
            title = (self.title_overrides or {}).get(t.track_id) or t.name
            artist = metadata.effective_artist(
                t.track_id, title, t.artist, self.artist_overrides, True,
                self.lib.artist_registry, stem=t.path.stem)
            try:
                with provider_operation(self.provider,lambda:self._stop):
                    res = self.provider.fetch_best(artist, title)
            except Exception as exc:  # noqa: BLE001
                res = None
                err += 1
                if err <= 3:
                    LOG.warning("Art fetch failed for %r: %r", title[:40], exc)
                # Dependency initialization/network errors are retryable failures,
                # never a durable claim that this track has no artwork.
                self.progress.emit(i, total)
                self._cancelled.wait(self.DELAY)
                continue
            if self._stop:
                break
            stored = self.cache.store_auto_if_unfetched(t.track_id, res)
            if stored and res:
                ok += 1
                self.changed_track_ids.add(t.track_id)
                self.cover_changed.emit(t.track_id)
            self.progress.emit(i, total)
            self._cancelled.wait(self.DELAY)
        self.cache.rebalance_default_photos()
        LOG.info("Art fill ended: %d fetched, %d failed", ok, err)


class StockArtworkPrepWorker(QThread):
    """Pre-render attached devices' disposable artwork off the UI thread.

    The curated JPEG cache remains canonical. Identical JPEG bytes converge on
    one content-addressed RGB565 entry, so this pass is resumable and cheap on
    later connected-device preparation passes after the first.
    """
    progress = Signal(int, int)

    def __init__(
        self,
        lib,
        art_cache,
        stock_cache: StockArtworkCache,
        images: tuple[Path, ...] | None = None,
        *, additional_caches: tuple[StockArtworkCache, ...] = (), profiles: tuple[str, ...] = (),
    ):
        super().__init__()
        self.lib = lib
        self.art_cache = art_cache
        self.stock_cache = stock_cache
        self.stock_caches = (stock_cache, *additional_caches)
        self.profiles = profiles
        self.images = images
        self._stop = False
        self._playback_active = threading.Event()

    def stop(self):
        self._stop = True

    def set_playback_active(self, active: bool):
        if active:
            self._playback_active.set()
        else:
            self._playback_active.clear()

    def _wait_for_playback(self):
        while self._playback_active.is_set() and not self._stop:
            time.sleep(0.05)

    def run(self):
        # Alternate profile construction loads disposable JSON indexes; keep
        # that work on this worker, rather than the device-selection thread.
        caches = (tuple(self.stock_cache.for_profile(profile) for profile in self.profiles)
                  if self.profiles else self.stock_caches)
        if self.images is not None:
            images = list(dict.fromkeys(self.images))
        else:
            hidden = getattr(self.lib, "hidden_ids", set())
            images = []
            seen_paths: set[Path] = set()
            for track in tuple(self.lib.tracks_by_id.values()):
                if track.track_id in hidden:
                    continue
                image = self.art_cache.image_path(track.track_id)
                if image is None:
                    continue
                try:
                    canonical = image.resolve(strict=True)
                except OSError:
                    continue
                if canonical not in seen_paths:
                    seen_paths.add(canonical)
                    images.append(canonical)

        total = len(images) * len(caches)
        LOG.info("Stock artwork preparation: %d curated image(s)", total)
        prepared = failed = reused = 0
        from itertools import product
        for index, (cache, image) in enumerate(product(caches, images), 1):
            if self._stop:
                break
            try:
                if cache.is_prepared(image):
                    reused += 1
                    continue
                self._wait_for_playback()
                if self._stop:
                    break
                before = cache.preparation_signature(image)
                # The low-priority whole-library pass validates manifests and
                # exact payload sizes. A stock sync rehashes/rebuilds the small
                # selected set before it can enter a device plan.
                cache.prepare(
                    image, verify_cache=False, save_index=False)
                cache.remember_prepared(image, before)
                prepared += 1
            except Exception as exc:  # noqa: BLE001 — one bad JPEG cannot end the pass
                failed += 1
                if failed <= 3:
                    LOG.warning("Stock artwork preparation failed for %s: %s", image, exc)
            self.progress.emit(index, total)
        for cache in caches:
            try:
                # One publication per profile, independent of song count.
                cache.flush_source_index()
            except Exception as exc:  # noqa: BLE001 — derivatives remain disposable
                LOG.warning("Stock artwork source index could not be saved: %s", exc)
        LOG.info(
            "Stock artwork preparation ended: %d checked/prepared, %d reused, %d failed",
            prepared, reused, failed,
        )


class WaveformFillWorker(QThread):
    """Prepare missing, stale, and compact-upgrade canonical V5 records.

    Rekordbox analysis is imported read-only into SoundBox-owned records.
    Source audio is decoded by libsndfile or Qt's QAudioDecoder for AAC/ALAC.
    Signed-mip V5 remains playable while a complete imported compact upgrade
    prepares; atomic publication replaces it only after the new record is ready.

    Runs off the UI thread and honors requestInterruption().
    Preparation yields inside the current track during playback and interaction
    so even an hour-long set cannot monopolize the GIL or decoder."""
    progress = Signal(int, int)  # done, total
    tasks_ready = Signal(int)  # scan complete; actual metadata/analysis work
    detail_ready = Signal(str)  # audio path; receiver reloads canonical V5
    bpm_ready = Signal(object)  # source-bound display estimate, never saved analysis
    storage_paused = Signal(bool, object, object)  # paused, free/resume bytes (64-bit)
    power_policy_changed = Signal(str)  # ac, battery, low-power

    DELAY = 0.05  # tiny yield between files — gentle on CPU, never a UI beachball
    MIN_FREE_BYTES = 20 * 1024 ** 3
    RESUME_FREE_BYTES = 25 * 1024 ** 3
    MIGRATION_MIN_FREE_BYTES = 5 * 1024 ** 3
    MIGRATION_RESUME_FREE_BYTES = 7 * 1024 ** 3
    STACK_BYTES = 16 * 1024 ** 2
    LARGE_BACKLOG = 25
    BATTERY_CPU_SHARE = 0.45
    LOW_BATTERY_CPU_SHARE = 0.20

    def __init__(self, lib, track_analysis_dir, force_track_ids=()):
        super().__init__()
        # LLVM/Numba used by scientific dependencies can consume substantially
        # more than macOS's ~512 KiB default QThread stack during first import.
        # The analysis path no longer relies on JIT, but the larger guard keeps
        # a transitive lazy import from taking down the packaged process.
        self.setStackSize(self.STACK_BYTES)
        self.lib = lib
        self.track_analysis_dir = track_analysis_dir
        self.force_track_ids = {
            str(track_id) for track_id in force_track_ids if str(track_id)
        }
        self.completed_forced_track_ids: set[str] = set()
        self.cache_writes = AnalysisWriteFence()
        self._playback_active = threading.Event()
        self._priority_lock = threading.Lock()
        self._priority_path = ""
        self._interaction_lock = threading.Lock()
        self._defer_until = 0.0
        self._power_policy = "ac"
        self._last_power_check = 0.0
        self._last_low_power_check = 0.0
        self._low_power_mode = False
        self._battery_percent = 100.0
        self._bulk_task_count = 0
        self._active_task_path = ""
        self._budget_cpu = time.thread_time()
        self._budget_wall = time.monotonic()

    def prioritize(self, path: str):
        """Keep the loaded track in the foreground lane until it changes."""
        with self._priority_lock:
            self._priority_path = str(path or "")

    def _publish_bpm(self, track):
        """Project a completed fallback's tempo without reading audio/payloads.

        This is disposable presentation metadata. Native/detected/manual grids
        remain separate, and a miss explicitly clears a previously shown estimate.
        """
        if (self.isInterruptionRequested() or getattr(track, 'bpm', '')
                or getattr(track, 'source_analysis_path', '') or self._track_is_hidden(track.track_id)):
            return
        from soundbox import analysis, track_analysis
        path = track.path
        value = dict(track_id=str(track.track_id), path=str(path), track_size=track.size,
                     source=str(self.lib.source), source_kind=self.lib.source_kind,
                     library_id=self.lib.source_library_id, native_id=track.native_id,
                     uuid=track.source_uuid, size=track.size, epoch=None, bpm=None,
                     provenance='soundbox-zero-anchor')
        try:
            before = path.stat()
            value.update(size=before.st_size, epoch=analysis.identity(before))
            with track_analysis.manifest_probe():
                manifest = (track_analysis.load_manifest(self.track_analysis_dir, track.track_id, path)
                            if self.track_analysis_dir else None)
                header = (track_analysis._read_manifest_header(
                    self.track_analysis_dir, track.track_id, path) if manifest else None)
                ready = bool(manifest and track_analysis.is_ready(
                    self.track_analysis_dir, track.track_id, path))
            if (ready and (not track.size or track.size == before.st_size)
                    and not track_analysis.is_dataless_source(path)
                    and manifest['provenance'].get('grid') == 'soundbox-zero-anchor'
                    and not manifest['provenance'].get('user_data')
                    and header and header[1].get('source', {}).get('identity')
                    == f'{before.st_size}:{before.st_mtime_ns}'):
                rows = manifest.get('beats') or []
                if 1 < len(rows) <= 400000:
                    bpm = float(rows[0][2])
                    previous = -1
                    valid = math.isfinite(bpm) and 0 < bpm <= 1000
                    for row in rows:
                        if (len(row) != 3 or row[0] not in (1, 2, 3, 4)
                                or not math.isfinite(float(row[1])) or row[1] < 0
                                or row[1] <= previous or float(row[2]) != bpm):
                            valid = False
                            break
                        previous = row[1]
                    if valid and rows[0][1] == 0:
                        value['bpm'] = bpm
            if analysis.identity(path.stat()) != value['epoch']:
                value['bpm'] = None
        except (OSError, ValueError, TypeError, KeyError, IndexError, OverflowError):
            value['bpm'] = None
        if not self.isInterruptionRequested() and not self._track_is_hidden(track.track_id):
            self.bpm_ready.emit(value)

    def _foreground_path(self) -> str:
        with self._priority_lock:
            return self._priority_path

    def _active_task_is_foreground(self) -> bool:
        return bool(
            self._active_task_path
            and self._active_task_path == self._foreground_path())

    def set_playback_active(self, active: bool):
        if active:
            self._playback_active.set()
        else:
            self._playback_active.clear()

    def defer_for_interaction(self, seconds: float = 0.75):
        """Yield analysis until shortly after the latest foreground gesture."""
        deadline = time.monotonic() + max(0.0, float(seconds))
        with self._interaction_lock:
            self._defer_until = max(self._defer_until, deadline)

    @staticmethod
    def _system_power_policy(
            check_low_power: bool) -> tuple[str, bool, float]:
        """Return the macOS power source and Low Power Mode without UI work."""
        percent = 100.0
        try:
            import psutil
            battery = psutil.sensors_battery()
            on_battery = bool(battery is not None and not battery.power_plugged)
            if battery is not None:
                percent = max(
                    0.0, min(100.0, float(battery.percent or 0.0)))
        except Exception:  # noqa: BLE001 - power probing is advisory
            on_battery = False
        low_power = False
        if on_battery and check_low_power:
            try:
                result = subprocess.run(
                    ["/usr/bin/pmset", "-g", "custom"],
                    capture_output=True, text=True, timeout=2, check=False)
                section = result.stdout.split("Battery Power:", 1)[-1]
                section = section.split("AC Power:", 1)[0]
                low_power = any(
                    line.strip().startswith("lowpowermode")
                    and line.split()[-1] == "1"
                    for line in section.splitlines())
            except (OSError, subprocess.SubprocessError, IndexError):
                low_power = False
        return ("battery" if on_battery else "ac"), low_power, percent

    def _refresh_power_policy(self):
        now = time.monotonic()
        if now - self._last_power_check < 10.0:
            return
        check_low_power = now - self._last_low_power_check >= 30.0
        source, low_power, percent = self._system_power_policy(check_low_power)
        self._last_power_check = now
        self._battery_percent = percent
        if check_low_power:
            self._last_low_power_check = now
            self._low_power_mode = low_power
        policy = (
            "low-power"
            if source == "battery" and self._low_power_mode else source)
        if policy != self._power_policy:
            self._power_policy = policy
            self.power_policy_changed.emit(policy)
            LOG.info("Library preparation power policy: %s", policy)

    def _analysis_checkpoint(self) -> bool:
        """Cooperative suspension and battery CPU budgeting.

        Direct ``run()`` calls are used by hermetic unit tests; power throttling
        is intentionally active only for a genuinely running QThread.
        """
        while not self.isInterruptionRequested():
            if self.isRunning():
                self._refresh_power_policy()
            # The loaded deck is a hard UX lane. It still obeys interruption
            # and the disk-space guard, but never waits behind playback,
            # scrolling, Low Power Mode, or a bulk battery CPU budget.
            if self._active_task_is_foreground():
                return True
            with self._interaction_lock:
                interaction_active = time.monotonic() < self._defer_until
            if (not self._playback_active.is_set()
                    and not interaction_active
                    and self._power_policy != "low-power"):
                break
            time.sleep(0.05)
        if self.isInterruptionRequested():
            return False
        if self.isRunning() and self._power_policy == "battery":
            now_cpu = time.thread_time()
            now_wall = time.monotonic()
            cpu = max(0.0, now_cpu - self._budget_cpu)
            wall = max(1e-6, now_wall - self._budget_wall)
            # Battery policy changes only the bulk worker's duty cycle, never
            # its analysis result. A genuinely large backlog becomes gentler
            # below 50%; small libraries and healthier batteries stay brisk.
            share = self.BATTERY_CPU_SHARE
            if (self._battery_percent < 50.0
                    and self._bulk_task_count >= self.LARGE_BACKLOG):
                share = self.LOW_BATTERY_CPU_SHARE
            rest = max(0.0, cpu / share - wall)
            if rest:
                time.sleep(min(rest, 0.75))
            self._budget_cpu = time.thread_time()
            self._budget_wall = time.monotonic()
        return not self.isInterruptionRequested()

    def _wait_for_playback(self):
        self._analysis_checkpoint()

    def _wait_for_storage(self, *, reclaiming: bool = False) -> bool:
        """Pause cooks before AppData can consume the OS safety reserve."""
        import shutil
        import time

        if not self.track_analysis_dir:
            return True
        target = Path(self.track_analysis_dir)
        probe = target if target.exists() else target.parent
        resume_bytes = (self.MIGRATION_RESUME_FREE_BYTES if reclaiming
                        else self.RESUME_FREE_BYTES)
        paused = False
        while not self.isInterruptionRequested():
            try:
                free = shutil.disk_usage(probe).free
            except OSError:
                return True
            threshold = (
                self.MIGRATION_RESUME_FREE_BYTES
                if reclaiming and paused else
                self.MIGRATION_MIN_FREE_BYTES
                if reclaiming else
                self.RESUME_FREE_BYTES
                if paused else self.MIN_FREE_BYTES)
            if free >= threshold:
                if paused:
                    LOG.info(
                        "Library preparation resumed with %.1f GiB free",
                        free / 1024 ** 3)
                    self.storage_paused.emit(False, free, resume_bytes)
                return True
            if not paused:
                paused = True
                LOG.warning(
                    "Library preparation paused: %.1f GiB free "
                    "(needs %.0f GiB)",
                    free / 1024 ** 3, threshold / 1024 ** 3)
                self.storage_paused.emit(True, free, resume_bytes)
            time.sleep(1.0)
        return False

    def _track_is_hidden(self, track_id: str) -> bool:
        """Read the live global-Graveyard set, including mid-pass changes."""
        return str(track_id) in {
            str(value)
            for value in (getattr(self.lib, "hidden_ids", ()) or ())
        }

    def run(self):
        from soundbox.rekordbox_analysis import read as read_analysis, read_beats
        from soundbox.track_analysis import (
            analyze_soundfile as analyze_track,
            adopt_source_identity,
            discard_records,
            fallback_waveform_modes,
            grid_needs_sync,
            imported_cues_need_sync,
            imported_waveforms_need_sync,
            is_dataless_source,
            is_ready as track_is_ready,
            merge_import,
            store as track_store,
            update_grid_source,
            update_imported_cues,
            with_imported_cues,
        )
        from .player import qt_detail_peaks
        from soundbox import track_analysis as analysis
        from soundbox.waveform_detail import analyze_imported, complete_import, DETAIL_MODEL
        from soundbox.preparation_inventory import PreparationInventory, signature
        inventory = (PreparationInventory(Path(self.track_analysis_dir) / 'preparation-index.json')
                     if self.track_analysis_dir else None)
        retry_epoch = str(int(time.time() // 1800))
        policy = [3, DETAIL_MODEL, analysis.SCHEMA, analysis.COLOR_MODEL, analysis.WAVEFORM_MODEL,
                  analysis.THREE_BAND_MODEL, analysis.DISPLAY_WAVEFORM_MODEL,
                  analysis.HEIGHT_ENCODING, analysis.RANGE_ENCODING,
                  analysis.RANGE_MIP_ENCODING, analysis.RANGE_MIP_DIVISORS]
        reused = 0
        hidden = {
            str(value)
            for value in (getattr(self.lib, "hidden_ids", ()) or ())
        }
        if self.track_analysis_dir and hidden:
            removed, reclaimed = discard_records(
                self.track_analysis_dir, hidden)
            if removed:
                LOG.info(
                    "Graveyard analysis cleanup: %d record(s), %.1f MiB",
                    removed, reclaimed / 1024 ** 2)
        todo: list[
            tuple[str, str, str, str, float, list[dict] | None, list | None]
        ] = []
        adopted_identities = 0
        for t in tuple(self.lib.tracks_by_id.values()):
            if self.isInterruptionRequested():
                break
            try:
                with analysis.manifest_probe():
                    track_id = str(t.track_id)
                    if track_id in hidden:
                        continue
                    analysis_path = getattr(t, "source_analysis_path", "")
                    source_cues = getattr(t, "source_cues", None)
                    paths = [t.path]
                    if self.track_analysis_dir:
                        paths.append(analysis.cache_path(self.track_analysis_dir, track_id))
                    if analysis_path:
                        dat = Path(analysis_path)
                        paths += [dat, dat.with_suffix('.EXT'), dat.with_suffix('.2EX')]
                    inputs = [policy, source_cues, getattr(t, 'bpm', '')]
                    before = signature(paths, inputs)
                    self._publish_bpm(t)
                    retry_key = 'fallback:' + track_id
                    if (inventory is not None and track_id not in self.force_track_ids
                            and inventory.matches(track_id, before)
                            and (retry_key not in inventory.rows
                                 or inventory.matches(retry_key, retry_epoch))):
                        reused += 1
                        continue
                    audio_path = str(t.path) if t.path.is_file() else ""
                    ready = dataless = False
                    try:
                        bpm_hint = float(getattr(t, "bpm", 0) or 0)
                    except (TypeError, ValueError):
                        bpm_hint = 0.0
                    if (self.track_analysis_dir and audio_path
                            and track_id in self.force_track_ids):
                        # A user-requested recook deliberately bypasses the cheap
                        # ready-manifest check. Keep the old playable record in
                        # place until track_store atomically publishes a verified
                        # replacement; never invalidate first.
                        todo.append((
                            "v5", audio_path, str(analysis_path or ""),
                            track_id, bpm_hint, source_cues, None))
                        continue
                    if self.track_analysis_dir and audio_path:
                        ready = track_is_ready(
                            self.track_analysis_dir, track_id, audio_path)
                        dataless = is_dataless_source(audio_path)
                        if (not ready and not dataless and adopt_source_identity(
                                self.track_analysis_dir, track_id, audio_path)):
                            analysis.reset_manifest_probe()
                            ready = True
                            adopted_identities += 1
                        if not ready:
                            # A File Provider placeholder may have changed logical
                            # size while offline. Do not turn a periodic cache
                            # check into an implicit cloud download; playback,
                            # export, or an explicit forced recook is the hydration
                            # boundary.
                            if dataless:
                                continue
                            todo.append((
                                "v5",
                                audio_path, str(analysis_path or ""),
                                track_id, bpm_hint, source_cues, None))
                            continue
                    fallback_modes = (
                        fallback_waveform_modes(
                            self.track_analysis_dir, track_id, audio_path)
                        if self.track_analysis_dir and audio_path and not dataless
                        and analysis_path and Path(analysis_path).is_file()
                        else ()
                    )
                    compact_pending = bool(
                        ready and not dataless and analysis_path
                        and Path(analysis_path).is_file()
                        and (analysis.load_manifest(
                            self.track_analysis_dir, track_id, audio_path) or {}
                             ).get("waveform_model") != DETAIL_MODEL)
                    if fallback_modes or compact_pending:
                        current_import = read_analysis(analysis_path, packed_only=True)
                        if compact_pending and complete_import(current_import):
                            # A playable signed-mip V5 is not finished preparation.
                            # Upgrade local complete imports without waiting for
                            # selection, keeping the old record until publication.
                            todo.append((
                                "compact", audio_path, str(analysis_path),
                                track_id, bpm_hint, source_cues, None))
                            continue
                        if imported_waveforms_need_sync(
                                fallback_modes, current_import):
                            # Rekordbox can publish EXT/2EX after SoundBox's first
                            # cook. Only a cache still using a fallback reaches
                            # this read-only probe; a newly valid stream promotes
                            # the track through the ordinary atomic V5 recook.
                            todo.append((
                                "v5", audio_path, str(analysis_path or ""),
                                track_id, bpm_hint, source_cues, None))
                            continue
                    imported_beats = (
                        read_beats(analysis_path)
                        if analysis_path and Path(analysis_path).is_file()
                        else None
                    )
                    grid_sync = bool(
                        self.track_analysis_dir and audio_path
                        and grid_needs_sync(
                            self.track_analysis_dir, track_id, audio_path,
                            imported_beats))
                    if grid_sync:
                        todo.append((
                            "grid-meta", audio_path, str(analysis_path or ""),
                            track_id, bpm_hint, None, imported_beats))
                    if (self.track_analysis_dir and audio_path
                            and imported_cues_need_sync(
                                self.track_analysis_dir, track_id, audio_path,
                                source_cues)):
                        todo.append((
                            "cue-meta", audio_path, "", track_id, 0.0,
                            source_cues, None))
                        continue
                    if grid_sync:
                        continue
                    if inventory is not None and ready:
                        inventory.remember(track_id, before, signature(paths, inputs))
                        if fallback_modes or compact_pending:
                            inventory.remember(retry_key, retry_epoch, retry_epoch)
                        else:
                            inventory.forget(retry_key)
            except OSError:
                continue
        if inventory is not None:
            inventory.prune(key for tid in self.lib.tracks_by_id if str(tid) not in hidden
                            for key in (str(tid), 'fallback:' + str(tid)))
            try:
                inventory.flush()
            except OSError as exc:
                LOG.warning("Analysis preparation inventory could not be saved: %s", exc)
        LOG.info("Analysis discovery reused %d unchanged track(s)", reused)
        total = len(todo)
        self._bulk_task_count = total
        task_counts = {
            kind: sum(task[0] == kind for task in todo)
            for kind in ("v5", "compact", "grid-meta", "cue-meta")
        }
        if adopted_identities:
            LOG.info(
                "Library source identity reconciliation: %d unchanged "
                "track(s), 0 audio recooks",
                adopted_identities)
        LOG.info(
            "Library preparation: %d task(s) "
            "(%d analysis, %d compact upgrades, %d grid metadata, %d cue metadata)",
            total,
            task_counts["v5"],
            task_counts["compact"],
            task_counts["grid-meta"],
            task_counts["cue-meta"],
        )
        self.tasks_ready.emit(total)
        done = 0
        i = 0
        # Reclaiming replacements must not sit behind a full cook waiting for
        # the larger disk reserve. Foreground selection still has priority.
        todo.sort(key=lambda task: task[0] == "v5")
        while todo:
            priority = self._foreground_path()
            task_index = 0
            if priority:
                task_index = next(
                    (j for j, task in enumerate(todo)
                     if task[0] in {"v5", "compact"}
                     and task[1] == priority),
                    0,
                )
            (kind, path, fallback_path, track_id, bpm_hint, imported_cues,
             imported_beats) = todo.pop(task_index)
            self._active_task_path = (
                path if kind in {"v5", "compact"} else "")
            i += 1
            if self._track_is_hidden(track_id):
                if self.track_analysis_dir:
                    discard_records(self.track_analysis_dir, (track_id,))
                self.progress.emit(i, total)
                time.sleep(self.DELAY)
                self._active_task_path = ""
                continue
            if kind not in {"cue-meta", "grid-meta"}:
                self._wait_for_playback()
            if self.isInterruptionRequested():
                break
            try:
                if kind in {"v5", "compact"}:
                    # Unit tests exercise ``run`` synchronously. Production
                    # always reaches this path through QThread.start().
                    if self.isRunning() and not self._wait_for_storage(
                            reclaiming=kind == "compact"):
                        break
                    # The source may have been evicted since planning or while
                    # waiting for playback/storage. Recheck at the I/O boundary.
                    if (track_id not in self.force_track_ids
                            and is_dataless_source(path)):
                        self.progress.emit(i, total)
                        self._active_task_path = ""
                        continue
                    existing = analysis.load_manifest(
                        self.track_analysis_dir, track_id, path)
                    if (kind == "compact" and (existing or {}).get(
                            "waveform_model") == DETAIL_MODEL):
                        # Interactive loading may have completed this while the
                        # bulk worker waited. Never decode a finished upgrade.
                        self.progress.emit(i, total)
                        self._active_task_path = ""
                        continue
                    # Inspect provider streams before spending CPU on fallback
                    # colors, FFT bands or beat detection.
                    imported = (
                        read_analysis(fallback_path)
                        if fallback_path and Path(fallback_path).is_file()
                        else None)
                    prepared = (analyze_imported(
                        path, imported, self._analysis_checkpoint,
                        lambda: qt_detail_peaks(
                            path, checkpoint=self._analysis_checkpoint, supplement=True))
                                if complete_import(imported) else None)
                    if (prepared is None and kind != "compact"
                            and self._analysis_checkpoint()):
                        native = analyze_track(
                            path, bpm_hint, self._analysis_checkpoint
                        ) or qt_detail_peaks(
                            path, checkpoint=self._analysis_checkpoint)
                        prepared = merge_import(native, imported) if native is not None else None
                    # Cancellation must never publish an imported-only shell.
                    if prepared is not None and not self.isInterruptionRequested():
                        if (imported_cues is None and (existing or {}).get(
                                "cue_import", {}).get("source") == "rekordbox-db"):
                            imported_cues = existing.get("cues") or []
                        if prepared is not None and imported_cues is not None:
                            prepared = with_imported_cues(
                                prepared, imported_cues)
                        if (
                                prepared
                                and not self._track_is_hidden(track_id)
                                and self.cache_writes.publish(track_id, lambda: track_store(
                                self.track_analysis_dir, track_id, path,
                                prepared))
                                and track_is_ready(
                                    self.track_analysis_dir, track_id, path)
                        ):
                            if self._track_is_hidden(track_id):
                                discard_records(
                                    self.track_analysis_dir, (track_id,))
                            else:
                                done += 1
                                if track_id in self.force_track_ids:
                                    self.completed_forced_track_ids.add(track_id)
                                self.detail_ready.emit(path)
                    else:
                        LOG.debug(
                            "Library preparation kept existing record after "
                            "decode cancellation/failure: %s",
                            path,
                        )
                elif kind == "cue-meta":
                    if self.cache_writes.publish(track_id, lambda: update_imported_cues(
                            self.track_analysis_dir, track_id, path,
                            imported_cues or [])):
                        done += 1
                        self.detail_ready.emit(path)
                elif kind == "grid-meta":
                    if self.cache_writes.publish(track_id, lambda: update_grid_source(
                            self.track_analysis_dir, track_id, path,
                            imported_beats or [])):
                        done += 1
                        self.detail_ready.emit(path)
            except Exception as exc:  # noqa: BLE001 — one bad track never stops prep
                LOG.debug("Library preparation failed for %s: %r", path, exc)
            track = self.lib.tracks_by_id.get(track_id)
            if track is not None:
                self._publish_bpm(track)
            if self._track_is_hidden(track_id) and self.track_analysis_dir:
                # A main-thread removal may land in the narrow interval after
                # the pre-store check. Reassert the no-Graveyard-cache contract
                # after every task so that race cannot republish a record.
                discard_records(self.track_analysis_dir, (track_id,))
            self.progress.emit(i, total)
            time.sleep(self.DELAY)
            self._active_task_path = ""
        LOG.info("Library preparation ended: %d cached", done)


class OneShotTask(QThread):
    """Run one callable off the UI thread; emits its result — or the exception
    (callers type-check). Used for backup zip/restore (193MB of art shouldn't
    beachball the app)."""
    done = Signal(object)

    phase_progress = Signal(str, object, object, str)

    def __init__(self, fn, *, log_label: str = ""):
        super().__init__()
        self.fn = fn
        self.log_label = log_label
        self.result = None
        self.cancellation = threading.Event()

    def cancel(self):
        self.cancellation.set()

    def _validate_command(self):
        command = getattr(self, "device_command", None)
        if command is not None and command.target.path and command.target.mount_token:
            if Path(command.target.path).stat().st_dev != command.target.mount_token:
                raise RuntimeError("Device mount changed before operation")

    def run(self):
        try:
            with operation_scope(self.cancellation, self.phase_progress.emit):
                checkpoint()
                self._validate_command()
                self.result = self.fn()
                command = getattr(self, "device_command", None)
                if command is not None and command.intent in {"inspect", "verify", "import"}:
                    self._validate_command()
                if getattr(self, "cancel_after_result", False):
                    checkpoint()
        except Exception as exc:  # noqa: BLE001 — surfaced to the UI
            if self.log_label:
                LOG.exception("%s failed", self.log_label)
            self.result = exc
        self.done.emit(self.result)


class ProgressTask(QThread):
    """Run one callable with a thread-safe, numeric progress callback."""
    progress = Signal(int, int, str)
    done = Signal(object)

    phase_progress = Signal(str, object, object, str)

    def __init__(self, fn, *, log_label: str = ""):
        super().__init__()
        self.fn = fn
        self.log_label = log_label
        self.result = None
        self.cancellation = threading.Event()

    def cancel(self):
        self.cancellation.set()

    def _validate_command(self):
        command = getattr(self, "device_command", None)
        if command is not None and command.target.path and command.target.mount_token:
            if Path(command.target.path).stat().st_dev != command.target.mount_token:
                raise RuntimeError("Device mount changed before operation")

    def run(self):
        started = time.monotonic()
        if self.log_label:
            LOG.info("%s started", self.log_label)
        try:
            with operation_scope(self.cancellation, self.phase_progress.emit):
                checkpoint()
                self._validate_command()
                self.result = self.fn(self.progress.emit)
                command = getattr(self, "device_command", None)
                if command is not None and command.intent in {"inspect", "verify", "import"}:
                    self._validate_command()
                if getattr(self, "cancel_after_result", False):
                    checkpoint()
        except Exception as exc:  # noqa: BLE001 — surfaced to the UI
            if self.log_label:
                LOG.exception("%s failed", self.log_label)
            self.result = exc
        if self.log_label:
            LOG.info("%s %s in %.2fs", self.log_label,
                     "failed" if isinstance(self.result, Exception) else "completed",
                     time.monotonic() - started)
        self.done.emit(self.result)


class _OneShotArt(QThread):
    """Fetch/read and normalize immutable image bytes before GUI publication."""
    done = Signal(str, object)  # track_id, ArtResult | None | Exception

    def __init__(self, tid, fn, *, provider=None):
        super().__init__()
        self.tid = tid
        self.fn = fn
        self.provider = provider
        self._cancelled = threading.Event()
        self.result = None
        self.requires_save = False
        self.local = False

    def stop(self):
        self._cancelled.set()

    def run(self):
        try:
            with provider_operation(self.provider,
                    lambda:self._cancelled.is_set() or self.isInterruptionRequested()):
                res = self.fn()
                from soundbox.artwork import ArtResult
                if (isinstance(res, ArtResult) and not self._cancelled.is_set()
                        and not self.isInterruptionRequested()):
                    from soundbox.artencode import ensure_device_safe
                    res = replace(res, data=ensure_device_safe(bytes(res.data)))
            if self._cancelled.is_set() or self.isInterruptionRequested():
                raise OperationCancelled('Artwork preparation was cancelled')
        except Exception as exc:  # noqa: BLE001 — surfaced to the UI + log
            # NEVER collapse a failure into "no art found": a network error and
            # a genuinely artless page are different answers (a swallowed
            # exception here once made a working URL look art-less).
            res = exc
        self.result = res
        self.done.emit(self.tid, res)


def _running(w) -> bool:
    """True if a worker QThread exists and is still running — safe even if its C++
    object was already deleted (returns False instead of raising)."""
    try:
        return w is not None and w.isRunning()
    except RuntimeError:
        return False
