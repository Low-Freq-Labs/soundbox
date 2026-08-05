"""Background QThread workers.

⚠ QThread lifetime rule (see soundbox-gotchas): the OWNER must hold a ref until
the built-in `finished` signal fires (MainWindow._finish_worker) — dropping the
last ref in a custom-signal slot GC's the thread mid-run → SIGABRT.
"""

from __future__ import annotations

import logging
import subprocess
import shutil
import threading
import time
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from soundbox import artfetch, engine, metadata, rekordbox_db
from soundbox.library import Playlist
from soundbox.stock_artwork import StockArtworkCache
from soundbox.stock_ipod import (
    inspect_stock_ipod_sync_status, normalize_model_number,
    supports_stock_write,
)
from soundbox.stock_ipod_info import stock_ipod_device_key

from .devices import device_facts, device_profile, list_devices

LOG = logging.getLogger("soundbox")


class ExportWorker(QThread):
    """Processes a growable queue of (device, Playlist) jobs in the background.

    One worker = one EXPORTING bar. New jobs added mid-run grow the total so the
    bar always estimates everything queued.
    """
    progress = Signal(int, int, str)
    finished_all = Signal(list)

    def __init__(self, lib, overrides=None, titles=None, art_cache=None,
                 rockbox_media_cache=None, rockbox_art_cache=None):
        super().__init__()
        self.lib = lib
        self.overrides = overrides or {}
        self.titles = titles or {}
        self.art_cache = art_cache
        self.rockbox_media_cache = rockbox_media_cache
        self.rockbox_art_cache = rockbox_art_cache
        self._lock = threading.Lock()
        self._jobs: list[tuple[str, Playlist]] = []
        self._index = 0
        self._total = 0
        self._base_done = 0
        self._stop = False
        self._reports: list[tuple[str, object]] = []

    def add(self, device: str, playlists: list[Playlist]):
        with self._lock:
            for pl in playlists:
                self._jobs.append((device, pl))
                self._total += max(1, len(self.lib.resolve(pl)))
        # No emit here: the next per-file progress tick updates the bar at the
        # real current position with the new total, so adding a playlist mid-export
        # smoothly lowers the % instead of jarringly snapping the bar to 0%.

    def cancel(self):
        with self._lock:
            self._stop = True

    def run(self):
        try:
            while True:
                with self._lock:
                    if self._stop or self._index >= len(self._jobs):
                        break
                    device, pl = self._jobs[self._index]
                    self._index += 1
                    base = self._base_done
                est = max(1, len(self.lib.resolve(pl)))

                def cb(d, _t, label, _base=base, _name=pl.name):
                    with self._lock:
                        tot = self._total
                    self.progress.emit(_base + d, tot, f"{_name} — {label}")

                try:
                    # clean=False: scrub AppleDouble once at end, not per playlist.
                    rep = engine.export_playlists(
                        self.lib, [pl], device, dry_run=False, derive_artists=True,
                        on_progress=cb, should_cancel=lambda: self._stop, clean=False,
                        artist_overrides=self.overrides, title_overrides=self.titles,
                        art_cache=self.art_cache,
                        rockbox_media_cache=self.rockbox_media_cache,
                        rockbox_art_cache=self.rockbox_art_cache)
                    self._reports.append((device, rep))
                except Exception as exc:  # noqa: BLE001 - surface failure in report
                    self._reports.append((device, str(exc) or repr(exc)))
                with self._lock:
                    self._base_done = base + est
                    total = self._total
                self.progress.emit(self._base_done, total, "")
        except Exception as exc:  # never let the thread take down the app
            self._reports.append(("(worker)", repr(exc)))
        # Scrub AppleDouble clutter ONCE per device touched (not per playlist) — but
        # only in the folders actually written this batch (fast on a full device).
        dev_dirs: dict[str, set] = {}
        for dev, rep in self._reports:
            wd = getattr(rep, "written_dirs", None)
            if wd:
                dev_dirs.setdefault(dev, set()).update(wd)
        for dev in dict.fromkeys(d for d, _ in self._reports):
            try:
                engine.clean_device(dev, dev_dirs.get(dev))
            except Exception:  # noqa: BLE001
                pass
        if self.rockbox_media_cache is not None:
            try:
                self.rockbox_media_cache.clear()
            except Exception as exc:  # noqa: BLE001
                LOG.warning("Rockbox media staging cleanup failed: %s", exc)
        self.finished_all.emit(self._reports)


class ResyncWorker(QThread):
    """Brings ONE device back in line with rekordbox (re-export changed playlists,
    move renamed ones, prune orphans) off the UI thread."""
    progress = Signal(int, int, str)
    done = Signal(str, object)  # device, SyncReport | error string

    def __init__(self, lib, device, overrides=None, titles=None, art_cache=None,
                 rockbox_media_cache=None, rockbox_art_cache=None, extra=None):
        super().__init__()
        self.lib = lib
        self.device = device
        self.overrides = overrides or {}
        self.titles = titles or {}
        self.art_cache = art_cache
        self.rockbox_media_cache = rockbox_media_cache
        self.rockbox_art_cache = rockbox_art_cache
        self.extra = extra or []  # custom SoundBox playlists (not in lib.playlists)
        self._stop = False

    def cancel(self):
        self._stop = True

    def run(self):
        def cb(d, t, label):
            self.progress.emit(d, t, label)
        try:
            rep = engine.resync_device(
                self.lib, self.device, on_progress=cb,
                should_cancel=lambda: self._stop,
                artist_overrides=self.overrides, title_overrides=self.titles,
                art_cache=self.art_cache,
                rockbox_media_cache=self.rockbox_media_cache,
                rockbox_art_cache=self.rockbox_art_cache,
                extra=self.extra)
            self.done.emit(self.device, rep)
        except Exception as exc:  # noqa: BLE001 - surface, never crash the app
            self.done.emit(self.device, str(exc) or repr(exc))
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

    def run(self):
        try:
            lib = rekordbox_db.load_live()
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.done.emit(lib)


class DeviceScanWorker(QThread):
    """Enumerate external devices + read each one's free space and sync drift OFF
    the UI thread.

    Touching a removable volume (os.statvfs, reading the manifest) can BLOCK for
    seconds on macOS fskit whenever the stick is busy — during our own export, or
    when the user is copying/clearing files in Finder. Doing it on the main-thread
    timer beachballs the whole app (rainbow wheel) and freezes the FINALIZING
    animation. Running it here keeps the UI responsive no matter what is hammering
    the disk; results are applied on the main thread via `done`.
    """
    done = Signal(list)  # list[tuple[name, path, free, drift, profile]]

    def __init__(self, lib, extra=None, *, artist_overrides=None,
                 title_overrides=None, stock_shadow_store=None,
                 stock_models=None):
        super().__init__()
        self.lib = lib
        self.extra = extra or []  # custom SoundBox playlists (not in lib.playlists)
        self.artist_overrides = dict(artist_overrides or {})
        self.title_overrides = dict(title_overrides or {})
        self.stock_shadow_store = stock_shadow_store
        self.stock_models = dict(stock_models or {})

    def run(self):
        out = []
        for name, path, free in list_devices():
            drift = 0
            profile = device_profile(path)
            if self.lib is not None:
                try:
                    dr = engine.device_drift(
                        self.lib,
                        path,
                        self.extra,
                        artist_overrides=self.artist_overrides,
                        title_overrides=self.title_overrides,
                    )
                    drift = dr.count() if dr else 0
                except Exception:  # noqa: BLE001 - a bad manifest never breaks the poll
                    drift = 0
                if (
                    not drift
                    and profile.kind == "stock_ipod"
                    and self.stock_shadow_store is not None
                ):
                    try:
                        facts = device_facts(path)
                        identity_key = stock_ipod_device_key(
                            facts.volume_uuid,
                            int(profile.library_persistent_id or 0),
                        )
                        model = normalize_model_number(
                            profile.model
                            or self.stock_models.get(identity_key, ""))
                        if supports_stock_write(model):
                            status = inspect_stock_ipod_sync_status(
                                self.lib,
                                path,
                                identity_key=identity_key,
                                model_number=model,
                                serial_number=facts.hardware_serial,
                                shadow_store=self.stock_shadow_store,
                                extra_playlists=tuple(self.extra),
                            )
                            if status.state not in {"up_to_date", "untracked"}:
                                drift = max(1, status.local_changes)
                    except Exception:  # noqa: BLE001 - keep polling read-only
                        drift = 0
            out.append((name, path, free, drift, profile))
        self.done.emit(out)


class DeleteWorker(QThread):
    """Delete files/folders from a device OFF the UI thread, reporting progress.

    Deleting a big track-folder tree over a slow USB blocks for a while; on the main
    thread that's a rainbow cursor with no feedback. Here it drives the same
    progress bar as an export instead. Stops cleanly between items on cancel."""
    progress = Signal(int, int, str)  # done, total, current name
    done = Signal(list)               # list[str] of errors

    def __init__(self, paths: list[Path]):
        super().__init__()
        self.paths = paths
        self._stop = False

    def cancel(self):
        self._stop = True

    def run(self):
        errors = []
        total = len(self.paths)
        for i, path in enumerate(self.paths, 1):
            if self._stop:
                break
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
            except OSError as exc:
                errors.append(f"{path.name}: {exc}")
            self.progress.emit(i, total, path.name)
        self.done.emit(errors)


class ArtFillWorker(QThread):
    """Background trickle: fetch missing cover art for the whole library, slowly,
    one track at a time, cached + resumable. Uses the user's curated artist/title
    so matches (and the artist-avatar fallback) keep improving over time."""
    progress = Signal(int, int)  # done, total

    DELAY = 1.6  # seconds between fetches — polite, never a burst

    def __init__(self, lib, cache, artist_overrides, title_overrides):
        super().__init__()
        self.lib = lib
        self.cache = cache
        self.artist_overrides = artist_overrides
        self.title_overrides = title_overrides
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
        import time
        while self._playback_active.is_set() and not self._stop:
            time.sleep(0.05)

    def run(self):
        import time
        todo = []
        hidden = getattr(self.lib, "hidden_ids", set())
        for t in self.lib.tracks_by_id.values():
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
                res = artfetch.fetch_best(artist, title)
            except Exception as exc:  # noqa: BLE001
                res = None
                err += 1
                if err <= 3:
                    LOG.warning("Art fetch failed for %r: %r", title[:40], exc)
            if res:
                self.cache.store_ok(
                    t.track_id, res, origin="auto", rebalance_defaults=False)
                ok += 1
            else:
                self.cache.store_none(t.track_id)
            self.progress.emit(i, total)
            time.sleep(self.DELAY)
        self.cache.rebalance_default_photos()
        LOG.info("Art fill ended: %d fetched, %d failed", ok, err)


class StockArtworkPrepWorker(QThread):
    """Pre-render disposable MA446 artwork derivatives off the UI thread.

    The curated JPEG cache remains canonical. Identical JPEG bytes converge on
    one content-addressed RGB565 entry, so this pass is resumable and cheap on
    every launch after the first.
    """
    progress = Signal(int, int)

    def __init__(
        self,
        lib,
        art_cache,
        stock_cache: StockArtworkCache,
        images: tuple[Path, ...] | None = None,
    ):
        super().__init__()
        self.lib = lib
        self.art_cache = art_cache
        self.stock_cache = stock_cache
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
        if self.images is not None:
            images = list(dict.fromkeys(self.images))
        else:
            hidden = getattr(self.lib, "hidden_ids", set())
            images = []
            seen_paths: set[Path] = set()
            for track in self.lib.tracks_by_id.values():
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

        total = len(images)
        LOG.info("Stock artwork preparation: %d curated image(s)", total)
        prepared = failed = 0
        for index, image in enumerate(images, 1):
            self._wait_for_playback()
            if self._stop:
                break
            try:
                # The low-priority whole-library pass validates manifests and
                # exact payload sizes. A stock sync rehashes/rebuilds the small
                # selected set before it can enter a device plan.
                self.stock_cache.prepare(
                    image, verify_cache=False, save_index=False)
                prepared += 1
            except Exception as exc:  # noqa: BLE001 — one bad JPEG cannot end the pass
                failed += 1
                if failed <= 3:
                    LOG.warning("Stock artwork preparation failed for %s: %s", image, exc)
            self.progress.emit(index, total)
            time.sleep(0.01)
        try:
            # One atomic index publication per batch keeps a full-library warm
            # pass O(n), instead of rewriting a growing JSON file for every song.
            self.stock_cache.flush_source_index()
        except Exception as exc:  # noqa: BLE001 — derivatives remain disposable
            LOG.warning("Stock artwork source index could not be saved: %s", exc)
        LOG.info(
            "Stock artwork preparation ended: %d ready, %d failed",
            prepared, failed,
        )


class WaveformFillWorker(QThread):
    """Prepare missing or stale canonical V5 records.

    Rekordbox analysis is imported read-only into SoundBox-owned records.
    Source audio is decoded by libsndfile or Qt's QAudioDecoder for AAC/ALAC.
    A verified V4 can be reduced losslessly to V5 one record at a time; its
    source V4 is retired only after the V5 payload passes a complete audit.

    Runs off the UI thread and honors requestInterruption().
    Preparation yields inside the current track during playback and interaction
    so even an hour-long set cannot monopolize the GIL or decoder."""
    progress = Signal(int, int)  # done, total
    tasks_ready = Signal(int)  # scan complete; actual metadata/analysis work
    detail_ready = Signal(str)  # audio path; receiver reloads canonical V5
    storage_paused = Signal(bool, object)  # paused, free bytes (64-bit)
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

    def __init__(self, lib, track_analysis_dir):
        super().__init__()
        # LLVM/Numba used by scientific dependencies can consume substantially
        # more than macOS's ~512 KiB default QThread stack during first import.
        # The analysis path no longer relies on JIT, but the larger guard keeps
        # a transitive lazy import from taking down the packaged process.
        self.setStackSize(self.STACK_BYTES)
        self.lib = lib
        self.track_analysis_dir = track_analysis_dir
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
                    self.storage_paused.emit(False, free)
                return True
            if not paused:
                paused = True
                LOG.warning(
                    "Library preparation paused: %.1f GiB free "
                    "(needs %.0f GiB)",
                    free / 1024 ** 3, threshold / 1024 ** 3)
                self.storage_paused.emit(True, free)
            time.sleep(1.0)
        return False

    def run(self):
        from soundbox.rekordbox_analysis import read as read_analysis, read_beats
        from soundbox.track_analysis import (
            analyze_soundfile as analyze_track,
            grid_needs_sync,
            imported_cues_need_sync,
            is_ready as track_is_ready,
            legacy_cache_path,
            merge_import,
            migrate_v4,
            retire_legacy_record,
            store as track_store,
            update_grid_source,
            update_imported_cues,
            with_imported_cues,
        )
        from .player import qt_detail_peaks
        todo: list[
            tuple[str, str, str, str, float, list[dict] | None, list | None]
        ] = []
        for t in self.lib.tracks_by_id.values():
            try:
                analysis_path = getattr(t, "source_analysis_path", "")
                source_cues = getattr(t, "source_cues", None)
                audio_path = str(t.path) if t.path.is_file() else ""
                try:
                    bpm_hint = float(getattr(t, "bpm", 0) or 0)
                except (TypeError, ValueError):
                    bpm_hint = 0.0
                if (self.track_analysis_dir and audio_path
                        and not track_is_ready(
                            self.track_analysis_dir, t.track_id, audio_path)):
                    legacy = legacy_cache_path(
                        self.track_analysis_dir, t.track_id)
                    todo.append((
                        "v4-migrate" if legacy.is_file() else "v5",
                        audio_path, str(analysis_path or ""),
                        str(t.track_id), bpm_hint, source_cues, None))
                    continue
                imported_beats = (
                    read_beats(analysis_path)
                    if analysis_path and Path(analysis_path).is_file()
                    else None
                )
                grid_sync = bool(
                    self.track_analysis_dir and audio_path
                    and grid_needs_sync(
                        self.track_analysis_dir, t.track_id, audio_path,
                        imported_beats))
                if grid_sync:
                    todo.append((
                        "grid-meta", audio_path, str(analysis_path or ""),
                        str(t.track_id), bpm_hint, None, imported_beats))
                if (self.track_analysis_dir and audio_path
                        and imported_cues_need_sync(
                            self.track_analysis_dir, t.track_id, audio_path,
                            source_cues)):
                    todo.append((
                        "cue-meta", audio_path, "", str(t.track_id), 0.0,
                        source_cues, None))
                    continue
                if grid_sync:
                    continue
            except OSError:
                continue
        total = len(todo)
        self._bulk_task_count = total
        task_counts = {
            kind: sum(task[0] == kind for task in todo)
            for kind in ("v5", "v4-migrate", "grid-meta", "cue-meta")
        }
        LOG.info(
            "Library preparation: %d task(s) "
            "(%d analysis, %d grid metadata, %d cue metadata)",
            total,
            task_counts["v5"] + task_counts["v4-migrate"],
            task_counts["grid-meta"],
            task_counts["cue-meta"],
        )
        self.tasks_ready.emit(total)
        done = 0
        i = 0
        while todo:
            priority = self._foreground_path()
            task_index = 0
            if priority:
                task_index = next(
                    (j for j, task in enumerate(todo)
                     if task[0] in {"v5", "v4-migrate"}
                     and task[1] == priority),
                    0,
                )
            (kind, path, fallback_path, track_id, bpm_hint, imported_cues,
             imported_beats) = todo.pop(task_index)
            self._active_task_path = (
                path if kind in {"v5", "v4-migrate"} else "")
            i += 1
            if kind not in {"cue-meta", "grid-meta"}:
                self._wait_for_playback()
            if self.isInterruptionRequested():
                break
            try:
                if kind in {"v5", "v4-migrate"}:
                    # Unit tests exercise ``run`` synchronously. Production
                    # always reaches this path through QThread.start().
                    if self.isRunning() and not self._wait_for_storage(
                            reclaiming=kind == "v4-migrate"):
                        break
                    if (
                            kind == "v4-migrate"
                            and migrate_v4(
                                self.track_analysis_dir, track_id, path)):
                        done += 1
                        self.detail_ready.emit(path)
                        self.progress.emit(i, total)
                        time.sleep(self.DELAY)
                        continue
                    native = analyze_track(
                        path, bpm_hint, self._analysis_checkpoint
                    ) or qt_detail_peaks(
                        path, checkpoint=self._analysis_checkpoint)
                    # A cooperative cancellation or decoder failure returns no
                    # native result. Never replace an existing playable record
                    # with a Rekordbox-only shell in that case: doing so loses
                    # its SoundBox detail stream and guarantees another retry.
                    if native is not None:
                        imported = (
                            read_analysis(fallback_path)
                            if fallback_path
                            and Path(fallback_path).is_file() else None)
                        prepared = merge_import(native, imported)
                        if prepared is not None and imported_cues is not None:
                            prepared = with_imported_cues(
                                prepared, imported_cues)
                        if prepared and track_store(
                                self.track_analysis_dir, track_id, path,
                                prepared
                        ) and track_is_ready(
                                self.track_analysis_dir, track_id, path):
                            retire_legacy_record(
                                self.track_analysis_dir, track_id, path)
                            done += 1
                            self.detail_ready.emit(path)
                    else:
                        LOG.debug(
                            "Library preparation kept existing record after "
                            "decode cancellation/failure: %s",
                            path,
                        )
                elif kind == "cue-meta":
                    if update_imported_cues(
                            self.track_analysis_dir, track_id, path,
                            imported_cues or []):
                        done += 1
                        self.detail_ready.emit(path)
                elif kind == "grid-meta":
                    if update_grid_source(
                            self.track_analysis_dir, track_id, path,
                            imported_beats or []):
                        done += 1
                        self.detail_ready.emit(path)
            except Exception as exc:  # noqa: BLE001 — one bad track never stops prep
                LOG.debug("Library preparation failed for %s: %r", path, exc)
            self.progress.emit(i, total)
            time.sleep(self.DELAY)
            self._active_task_path = ""
        LOG.info("Library preparation ended: %d cached", done)


class OneShotTask(QThread):
    """Run one callable off the UI thread; emits its result — or the exception
    (callers type-check). Used for backup zip/restore (193MB of art shouldn't
    beachball the app)."""
    done = Signal(object)

    def __init__(self, fn, *, log_label: str = ""):
        super().__init__()
        self.fn = fn
        self.log_label = log_label

    def run(self):
        try:
            self.done.emit(self.fn())
        except Exception as exc:  # noqa: BLE001 — surfaced to the UI
            if self.log_label:
                LOG.exception("%s failed", self.log_label)
            self.done.emit(exc)


class ProgressTask(QThread):
    """Run one callable with a thread-safe, numeric progress callback."""
    progress = Signal(int, int, str)
    done = Signal(object)

    def __init__(self, fn, *, log_label: str = ""):
        super().__init__()
        self.fn = fn
        self.log_label = log_label

    def run(self):
        try:
            self.done.emit(self.fn(self.progress.emit))
        except Exception as exc:  # noqa: BLE001 — surfaced to the UI
            if self.log_label:
                LOG.exception("%s failed", self.log_label)
            self.done.emit(exc)


class _OneShotArt(QThread):
    """A single art fetch (manual 'fetch now' / paste-URL), off the UI thread."""
    done = Signal(str, object)  # track_id, ArtResult | None | Exception

    def __init__(self, tid, fn):
        super().__init__()
        self.tid = tid
        self.fn = fn

    def run(self):
        try:
            res = self.fn()
        except Exception as exc:  # noqa: BLE001 — surfaced to the UI + log
            # NEVER collapse a failure into "no art found": a network error and
            # a genuinely artless page are different answers (a swallowed
            # exception here once made a working URL look art-less).
            res = exc
        self.done.emit(self.tid, res)


def _running(w) -> bool:
    """True if a worker QThread exists and is still running — safe even if its C++
    object was already deleted (returns False instead of raising)."""
    try:
        return w is not None and w.isRunning()
    except RuntimeError:
        return False
